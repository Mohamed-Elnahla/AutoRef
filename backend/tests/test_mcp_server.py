import base64
import json

import pytest
from mcp import Client

from backend.app import mcp_server
from backend.app.services.job_store import JobStore
from backend.tests.test_docx_processor import _fixture


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_exposes_backend_workflow_and_converts_docx(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "store", JobStore(tmp_path / "jobs"))

    async with Client(mcp_server.mcp) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert {
            "analyze_document",
            "convert_document",
            "convert_docx_to_zotero",
            "read_artifact",
            "connect_zotero",
            "disconnect_zotero",
            "preview_zotero_import",
            "import_to_zotero",
        } <= names

        result = await client.call_tool(
            "convert_docx_to_zotero",
            {
                "document_base64": base64.b64encode(_fixture()).decode("ascii"),
                "filename": "paper.docx",
            },
        )
        assert not result.is_error
        converted = _payload(result)
        assert converted["report"]["converted_citations"] == 1
        assert set(converted["artifacts"]) == {"document", "library", "report"}

        library = await client.call_tool(
            "read_artifact",
            {"job_id": converted["job_id"], "name": "library"},
        )
        library_payload = _payload(library)
        references = json.loads(base64.b64decode(library_payload["data_base64"]))
        assert references[0]["title"] == "First paper"


@pytest.mark.asyncio
async def test_mcp_rejects_unconfirmed_zotero_write():
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool(
            "import_to_zotero",
            {
                "job_id": "0" * 32,
                "connection_id": "1" * 32,
                "library_type": "user",
                "library_id": 7,
                "plan_id": "2" * 64,
                "confirm": False,
            },
        )
    assert result.is_error
    assert "Explicit confirmation is required" in result.content[0].text
