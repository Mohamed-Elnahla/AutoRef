from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from backend.app.config import settings
from backend.app.services.credential_vault import CredentialVault
from backend.app.services.crossref import CrossrefClient
from backend.app.services.docx_processor import DocxError, write_csl_json
from backend.app.services.docx_processor import analyze_docx as analyze_docx_bytes
from backend.app.services.docx_processor import convert_docx as convert_docx_bytes
from backend.app.services.job_store import JobStore
from backend.app.services.zotero import (
    Library,
    ZoteroClient,
    ZoteroError,
    ZoteroImportReviewRequired,
)

store = JobStore()
vault = CredentialVault()

mcp = MCPServer(
    "AutoRef",
    version="0.3.0",
    instructions=(
        "Convert ordinary DOCX citations and reference lists into native Zotero Word fields, "
        "and convert matching figure/table mentions into native Word cross-reference fields. "
        "Analyze before conversion, surface warnings, and never call import_to_zotero until "
        "the user has reviewed a preview and explicitly confirmed the write."
    ),
)

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PLAN_ID_RE = re.compile(r"^[0-9a-f]{64}$")
CONNECTION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ArtifactName = Literal["document", "library", "report"]
LibraryType = Literal["user", "group"]


def _safe_stem(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip("-.")
    return cleaned[:100] or "paper"


def _require_token(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"Invalid {label}.")
    return value


def _document_input(
    source_path: str | None, document_base64: str | None, filename: str | None
) -> tuple[bytes, str]:
    if bool(source_path) == bool(document_base64):
        raise ValueError("Provide exactly one of source_path or document_base64.")
    if source_path:
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"DOCX file not found: {path}")
        source_name = filename or path.name
        data = path.read_bytes()
    else:
        source_name = filename or "paper.docx"
        try:
            data = base64.b64decode(document_base64 or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("document_base64 is not valid base64.") from exc
    if not source_name.lower().endswith(".docx"):
        raise ValueError("The document filename must end in .docx.")
    if len(data) > settings.max_upload_bytes:
        raise ValueError(f"The document exceeds the {settings.max_upload_bytes}-byte upload limit.")
    return data, source_name


def _analyze_and_store(data: bytes, source_name: str) -> dict:
    try:
        analysis = analyze_docx_bytes(data, source_name)
    except DocxError as exc:
        raise ValueError(str(exc)) from exc
    store.cleanup()
    job_id = store.create(source_name, data)
    payload = analysis.to_dict()
    store.write_json(job_id, "analysis.json", payload)
    return {"job_id": job_id, **payload}


def _job_source(job_id: str) -> tuple[bytes, str]:
    _require_token(job_id, JOB_ID_RE, "job_id")
    try:
        metadata = store.read_json(job_id, "metadata.json")
        return store.source(job_id), metadata["source_name"]
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise ValueError("This job does not exist or has expired.") from exc


def _artifact_metadata(job_id: str, names: tuple[ArtifactName, ...]) -> dict:
    artifacts: dict[str, dict] = {}
    for name in names:
        path = store.artifact(job_id, name)
        artifacts[name] = {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "local_path": str(path.resolve()),
            "read_with": {"tool": "read_artifact", "job_id": job_id, "name": name},
        }
    return artifacts


def _convert_job(job_id: str) -> dict:
    source, source_name = _job_source(job_id)
    analysis = analyze_docx_bytes(source, source_name)
    if not analysis.references and not analysis.cross_references:
        raise ValueError(
            "No reference list or convertible figure/table cross-references were detected."
        )
    converted, report = convert_docx_bytes(source, analysis)
    stem = _safe_stem(source_name)
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
        "artifacts": _artifact_metadata(job_id, ("document", "library", "report")),
    }


def _zotero_client(connection_id: str) -> ZoteroClient:
    _require_token(connection_id, CONNECTION_ID_RE, "connection_id")
    try:
        return ZoteroClient(vault.get(connection_id))
    except KeyError as exc:
        raise ValueError("The Zotero connection expired; reconnect it.") from exc


def _selected_library(client: ZoteroClient, library_type: str, library_id: int) -> Library:
    _, libraries = client.libraries()
    for library in libraries:
        if library.type == library_type and library.id == library_id:
            return library
    raise ValueError("The Zotero key does not have write access to that library.")


@mcp.tool(
    structured_output=True,
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
)
def health() -> dict[str, Any]:
    """Check the AutoRef MCP server and report its supported artifact formats."""
    store.cleanup()
    vault.cleanup()
    return {
        "status": "ok",
        "version": "0.3.0",
        "outputs": [
            "Zotero and Word cross-reference field DOCX",
            "CSL-JSON Zotero import",
            "JSON audit report",
        ],
        "features": [
            "bibliographic citation conversion",
            "figure/table caption detection",
            "figure/table in-text Word cross-references",
        ],
    }


@mcp.tool(structured_output=True, annotations=ToolAnnotations(open_world_hint=False))
def analyze_document(
    source_path: str | None = None,
    document_base64: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Analyze a DOCX without changing it and create an expiring job.

    Local clients should pass source_path. Remote clients can pass base64 plus filename.
    Returns parsed references, citation matches, figure/table captions and cross-reference
    matches, warnings, summary counts, and job_id.
    """
    data, source_name = _document_input(source_path, document_base64, filename)
    return _analyze_and_store(data, source_name)


@mcp.tool(structured_output=True, annotations=ToolAnnotations(open_world_hint=False))
def convert_document(job_id: str) -> dict[str, Any]:
    """Convert citations, bibliography, and figure/table mentions to native Word fields.

    Figure/table mentions become clickable Word REF fields linked to bookmarked caption numbers.
    Bibliographic fields embed metadata but cannot link to Zotero library item keys in this
    credential-free mode. Use the preview/import tools for fully linked Zotero fields.
    """
    return _convert_job(job_id)


@mcp.tool(structured_output=True, annotations=ToolAnnotations(open_world_hint=False))
def convert_docx_to_zotero(
    source_path: str | None = None,
    document_base64: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Convert Zotero citations and figure/table Word cross-references in one local call."""
    data, source_name = _document_input(source_path, document_base64, filename)
    analysis = _analyze_and_store(data, source_name)
    result = _convert_job(analysis["job_id"])
    result["analysis"] = analysis
    return result


@mcp.tool(
    structured_output=True,
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
)
def read_artifact(
    job_id: str, name: ArtifactName, include_base64: bool = True
) -> dict[str, Any]:
    """Read a generated document, Zotero import library, or report.

    Set include_base64 false when the client can use the returned local_path directly.
    """
    _require_token(job_id, JOB_ID_RE, "job_id")
    try:
        path = store.artifact(job_id, name)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise ValueError("Artifact not found.") from exc
    media_type = {
        "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "library": "application/json",
        "report": "application/json",
    }[name]
    result = {
        "job_id": job_id,
        "artifact": name,
        "filename": path.name,
        "media_type": media_type,
        "size_bytes": path.stat().st_size,
        "local_path": str(path.resolve()),
    }
    if include_base64:
        result["data_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
    return result


@mcp.tool(structured_output=True, annotations=ToolAnnotations(open_world_hint=True))
def connect_zotero(api_key: str | None = None) -> dict[str, Any]:
    """Validate a Zotero API key and return an expiring connection plus writable libraries.

    When api_key is omitted, AUTOREF_ZOTERO_API_KEY is used. Prefer the environment variable so
    a secret does not need to be placed in an AI conversation.
    """
    secret = api_key or os.getenv("AUTOREF_ZOTERO_API_KEY")
    if not secret:
        raise ValueError("Provide api_key or set AUTOREF_ZOTERO_API_KEY for the MCP process.")
    if not 8 <= len(secret) <= 200:
        raise ValueError("The Zotero API key length is invalid.")
    client = ZoteroClient(secret)
    try:
        _, libraries = client.libraries()
        if not libraries:
            raise ValueError("The Zotero key has no writable libraries.")
        connection_id = vault.put(secret)
        return {
            "connection_id": connection_id,
            "expires_in_minutes": settings.credential_ttl_minutes,
            "libraries": [
                {"type": library.type, "id": library.id, "name": library.name}
                for library in libraries
            ],
        }
    except ZoteroError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        client.close()


@mcp.tool(
    structured_output=True,
    annotations=ToolAnnotations(destructive_hint=True, open_world_hint=False),
)
def disconnect_zotero(connection_id: str) -> dict[str, Any]:
    """Immediately remove an encrypted Zotero connection from memory."""
    _require_token(connection_id, CONNECTION_ID_RE, "connection_id")
    vault.remove(connection_id)
    return {"disconnected": True, "connection_id": connection_id}


@mcp.tool(structured_output=True, annotations=ToolAnnotations(open_world_hint=True))
def preview_zotero_import(
    job_id: str,
    connection_id: str,
    library_type: LibraryType,
    library_id: int,
    collection_name: str | None = None,
) -> dict[str, Any]:
    """Preview exact create/reuse/DOI-update decisions without writing anything."""
    source, source_name = _job_source(job_id)
    if library_id <= 0:
        raise ValueError("library_id must be positive.")
    if collection_name and len(collection_name) > 255:
        raise ValueError("collection_name cannot exceed 255 characters.")
    analysis = analyze_docx_bytes(source, source_name)
    if not analysis.references:
        raise ValueError("No references are available to import.")
    client = _zotero_client(connection_id)
    try:
        library = _selected_library(client, library_type, library_id)
        plan = client.plan(library, analysis.references)
    except ZoteroError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        client.close()
    stored = {
        **plan,
        "library": {"type": library.type, "id": library.id, "name": library.name},
        "collection_name": collection_name.strip() if collection_name else None,
    }
    store.write_json(job_id, "zotero-plan.json", stored)
    return {
        **stored,
        "summary": {
            "create": sum(item["action"] == "create" for item in plan["entries"]),
            "reuse": sum(item["action"] == "reuse" for item in plan["entries"]),
            "update": sum(item["action"] == "update" for item in plan["entries"]),
        },
        "write_performed": False,
    }


@mcp.tool(
    structured_output=True,
    annotations=ToolAnnotations(destructive_hint=True, open_world_hint=True),
)
def import_to_zotero(
    job_id: str,
    connection_id: str,
    library_type: LibraryType,
    library_id: int,
    plan_id: str,
    confirm: bool,
    collection_name: str | None = None,
    unverified_doi_action: Literal["review", "use_parsed", "mark_for_review", "exclude"] = "review",
) -> dict[str, Any]:
    """Execute a reviewed Zotero import and produce a DOCX linked to canonical item keys.

    This writes to Zotero. Call preview_zotero_import first, show its decisions to the user, and
    call this tool only after explicit user confirmation with confirm=true.
    """
    if not confirm:
        raise ValueError("Explicit confirmation is required before writing to Zotero.")
    _require_token(plan_id, PLAN_ID_RE, "plan_id")
    source, source_name = _job_source(job_id)
    try:
        plan = store.read_json(job_id, "zotero-plan.json")
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("The import preview has expired; preview it again.") from exc
    normalized_collection = collection_name.strip() if collection_name else None
    expected_library = plan["library"]
    if (
        plan_id != plan["plan_id"]
        or library_type != expected_library["type"]
        or library_id != expected_library["id"]
        or normalized_collection != plan.get("collection_name")
    ):
        raise ValueError("The import options changed; preview them again.")
    analysis = analyze_docx_bytes(source, source_name)
    client = _zotero_client(connection_id)
    crossref = CrossrefClient()
    try:
        library = _selected_library(client, library_type, library_id)
        links, import_audit = client.execute(
            library,
            analysis.references,
            plan,
            plan.get("collection_name"),
            crossref,
            unverified_doi_action,
        )
    except ZoteroImportReviewRequired as exc:
        failure = {
            "status": "needs_doi_review",
            "unresolved": exc.unresolved,
            "message": str(exc),
            "write_performed": False,
        }
        store.write_json(job_id, "zotero-import-failure.json", failure)
        raise ValueError(json.dumps(failure)) from exc
    except ZoteroError as exc:
        store.write_json(
            job_id,
            "zotero-import-failure.json",
            {"status": "failed", "message": str(exc), "rollback": "attempted"},
        )
        raise ValueError(str(exc)) from exc
    finally:
        crossref.close()
        client.close()
    excluded = {
        item["reference_id"]
        for item in import_audit["crossref"]["unresolved"]
    } if unverified_doi_action == "exclude" else set()
    converted, report = convert_docx_bytes(source, analysis, links, excluded)
    stem = _safe_stem(source_name)
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
        "artifacts": _artifact_metadata(job_id, ("document", "report")),
        "write_performed": True,
    }


@mcp.resource("autoref://capabilities")
def capabilities() -> str:
    """Describe AutoRef's supported conversion workflow and safety boundary."""
    return (
        "AutoRef analyzes DOCX files; converts unambiguous citation spans and the detected "
        "reference list to native Zotero Word fields; bookmarks detected figure/table caption "
        "numbers; and converts matching in-text numbers to clickable Word REF fields while "
        "preserving label wording and styling. It exports CSL-JSON and audit reports, and can "
        "create/reuse Zotero records after a separate preview and explicit confirmation. "
        "Ambiguous citations and duplicate caption-number references remain unchanged."
    )


@mcp.prompt()
def convert_research_paper(source_path: str) -> str:
    """Create a careful conversion workflow for a local research paper."""
    return (
        f"Analyze the DOCX at {source_path!r}. Summarize its warnings, citation match rate, "
        "caption count, and figure/table cross-reference count. Then perform a local conversion "
        "and return the converted DOCX, CSL-JSON Zotero import file, and audit report. Confirm "
        "that matched figure/table mentions became native Word REF fields. Do not write to a "
        "Zotero library unless I separately request and confirm it."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AutoRef MCP server.")
    parser.add_argument(
        "--transport", choices=("stdio", "streamable-http"), default="stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            stateless_http=True,
            json_response=True,
        )


if __name__ == "__main__":
    main()
