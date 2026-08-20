from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import settings
from backend.app.services.docx_processor import (
    DocxError,
    analyze_docx,
    convert_docx,
    write_csl_json,
)
from backend.app.services.job_store import JobStore

store = JobStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.cleanup()
    yield


app = FastAPI(
    title="AutoRef API",
    version="0.1.0",
    description="Layout-preserving DOCX citation conversion for Zotero",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")
    return cleaned[:100] or "paper"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


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
