from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.config import settings
from backend.app.services.credential_vault import CredentialVault
from backend.app.services.crossref import CrossrefClient
from backend.app.services.docx_processor import (
    DocxError,
    analyze_docx,
    convert_docx,
    write_csl_json,
)
from backend.app.services.job_store import JobStore
from backend.app.services.zotero import (
    Library,
    ZoteroClient,
    ZoteroError,
    ZoteroImportReviewRequired,
)

store = JobStore()
vault = CredentialVault()


class ZoteroConnectionRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)


class ZoteroLibraryRequest(BaseModel):
    connection_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    library_type: Literal["user", "group"]
    library_id: int = Field(gt=0)
    collection_name: str | None = Field(default=None, max_length=255)


class ZoteroImportRequest(ZoteroLibraryRequest):
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    unverified_doi_action: Literal["review", "use_parsed", "mark_for_review", "exclude"] = "review"


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.cleanup()
    vault.cleanup()
    yield


app = FastAPI(
    title="AutoRef API",
    version="0.3.0",
    description="Layout-preserving DOCX citation conversion for Zotero",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")
    return cleaned[:100] or "paper"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


def _zotero_client(connection_id: str) -> ZoteroClient:
    try:
        return ZoteroClient(vault.get(connection_id))
    except KeyError as exc:
        raise HTTPException(status_code=401, detail="The Zotero connection expired; reconnect it.") from exc


def _selected_library(client: ZoteroClient, kind: str, library_id: int) -> Library:
    _, libraries = client.libraries()
    for library in libraries:
        if library.type == kind and library.id == library_id:
            return library
    raise HTTPException(status_code=403, detail="The Zotero key does not have write access to that library.")


@app.post("/api/v1/zotero/connections", status_code=201)
def connect_zotero(payload: ZoteroConnectionRequest) -> dict:
    client = ZoteroClient(payload.api_key)
    try:
        _, libraries = client.libraries()
        if not libraries:
            raise HTTPException(status_code=403, detail="The Zotero key has no writable libraries.")
        connection_id = vault.put(payload.api_key)
        return {
            "connection_id": connection_id,
            "expires_in_minutes": settings.credential_ttl_minutes,
            "libraries": [
                {"type": library.type, "id": library.id, "name": library.name}
                for library in libraries
            ],
        }
    except ZoteroError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()


@app.delete("/api/v1/zotero/connections/{connection_id}", status_code=204)
def disconnect_zotero(connection_id: str) -> None:
    vault.remove(connection_id)


@app.post("/api/v1/documents/analyze", status_code=201)
async def analyze(file: Annotated[UploadFile, File()]) -> dict:
    filename = file.filename or "paper.docx"
    if not filename.lower().endswith(".docx"):
        raise HTTPException(status_code=415, detail="Upload a .docx Word document.")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="The document exceeds the upload limit.")
    try:
        analysis = analyze_docx(data, filename)
    except DocxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.cleanup()
    job_id = store.create(filename, data)
    store.write_json(job_id, "analysis.json", analysis.to_dict())
    return {"job_id": job_id, **analysis.to_dict()}


@app.post("/api/v1/documents/{job_id}/convert")
def convert(job_id: str) -> dict:
    try:
        source = store.source(job_id)
        metadata = store.read_json(job_id, "metadata.json")
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="This job does not exist or has expired.")
    analysis = analyze_docx(source, metadata["source_name"])
    if not analysis.references:
        raise HTTPException(status_code=409, detail="No reference list was detected; conversion was not run.")
    converted, report = convert_docx(source, analysis)
    stem = _safe_stem(metadata["source_name"])
    directory = store.directory(job_id)
    document_name = f"{stem}-zotero.docx"
    library_name = f"{stem}-library.csl.json"
    report_name = f"{stem}-conversion-report.json"
    (directory / document_name).write_bytes(converted)
    write_csl_json(analysis.references, directory / library_name)
    store.write_json(job_id, report_name, {**report, "analysis": analysis.to_dict()})
    store.write_json(
        job_id,
        "artifacts.json",
        {"document": document_name, "library": library_name, "report": report_name},
    )
    return {
        "job_id": job_id,
        "report": report,
        "artifacts": {
            name: f"/api/v1/documents/{job_id}/artifacts/{name}"
            for name in ("document", "library", "report")
        },
    }


@app.post("/api/v1/documents/{job_id}/zotero/preview")
def preview_zotero_import(job_id: str, payload: ZoteroLibraryRequest) -> dict:
    try:
        source = store.source(job_id)
        metadata = store.read_json(job_id, "metadata.json")
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="This job does not exist or has expired.")
    analysis = analyze_docx(source, metadata["source_name"])
    if not analysis.references:
        raise HTTPException(status_code=409, detail="No references are available to import.")
    client = _zotero_client(payload.connection_id)
    try:
        library = _selected_library(client, payload.library_type, payload.library_id)
        plan = client.plan(library, analysis.references)
    except ZoteroError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()
    stored = {
        **plan,
        "library": {"type": library.type, "id": library.id, "name": library.name},
        "collection_name": payload.collection_name.strip() if payload.collection_name else None,
    }
    store.write_json(job_id, "zotero-plan.json", stored)
    return {
        **stored,
        "summary": {
            "create": sum(item["action"] == "create" for item in plan["entries"]),
            "reuse": sum(item["action"] == "reuse" for item in plan["entries"]),
        },
    }


@app.post("/api/v1/documents/{job_id}/zotero/import")
def import_to_zotero(job_id: str, payload: ZoteroImportRequest) -> dict:
    try:
        source = store.source(job_id)
        metadata = store.read_json(job_id, "metadata.json")
        plan = store.read_json(job_id, "zotero-plan.json")
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="The job or import preview has expired.")
    expected_library = plan["library"]
    if (
        payload.plan_id != plan["plan_id"]
        or payload.library_type != expected_library["type"]
        or payload.library_id != expected_library["id"]
        or (payload.collection_name.strip() if payload.collection_name else None)
        != plan.get("collection_name")
    ):
        raise HTTPException(status_code=409, detail="The import options changed; preview them again.")
    analysis = analyze_docx(source, metadata["source_name"])
    client = _zotero_client(payload.connection_id)
    crossref = CrossrefClient()
    try:
        library = _selected_library(client, payload.library_type, payload.library_id)
        links, import_audit = client.execute(
            library,
            analysis.references,
            plan,
            plan.get("collection_name"),
            crossref,
            payload.unverified_doi_action,
        )
    except ZoteroImportReviewRequired as exc:
        failure = {
            "status": "needs_doi_review",
            "unresolved": exc.unresolved,
            "message": str(exc),
            "write_performed": False,
        }
        store.write_json(job_id, "zotero-import-failure.json", failure)
        raise HTTPException(status_code=422, detail=failure) from exc
    except ZoteroError as exc:
        store.write_json(
            job_id,
            "zotero-import-failure.json",
            {"status": "failed", "message": str(exc), "rollback": "attempted"},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        crossref.close()
        client.close()

    excluded = {
        item["reference_id"]
        for item in import_audit["crossref"]["unresolved"]
    } if payload.unverified_doi_action == "exclude" else set()
    converted, report = convert_docx(source, analysis, links, excluded)
    stem = _safe_stem(metadata["source_name"])
    directory = store.directory(job_id)
    document_name = f"{stem}-zotero-linked.docx"
    report_name = f"{stem}-zotero-import-report.json"
    (directory / document_name).write_bytes(converted)
    full_report = {**report, "zotero_import": import_audit, "analysis": analysis.to_dict()}
    store.write_json(job_id, report_name, full_report)
    store.write_json(job_id, "artifacts.json", {"document": document_name, "report": report_name})
    return {
        "job_id": job_id,
        "report": full_report,
        "artifacts": {
            name: f"/api/v1/documents/{job_id}/artifacts/{name}"
            for name in ("document", "report")
        },
    }


@app.get("/api/v1/documents/{job_id}/artifacts/{name}")
def artifact(job_id: str, name: str):
    try:
        path = store.artifact(job_id, name)
    except (FileNotFoundError, KeyError, ValueError):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    media = {
        "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "library": "application/json",
        "report": "application/json",
    }[name]
    return FileResponse(path, media_type=media, filename=path.name)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
