import base64
import io
import json
import zipfile

import pytest
from lxml import etree
from mcp import Client

from backend.app import mcp_server
from backend.app.services.docx_processor import NS
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


@pytest.mark.asyncio
async def test_mcp_reports_and_converts_figure_cross_references_without_bibliography(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(mcp_server, "store", JobStore(tmp_path / "jobs"))
    source = _fixture(
        """
        <w:p><w:r><w:t>See Fig. 3 for the workflow.</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr><w:r><w:t>Figure 3. Workflow.</w:t></w:r></w:p>
        """
    )

    async with Client(mcp_server.mcp) as client:
        health_result = _payload(await client.call_tool("health", {}))
        assert "figure/table caption detection" in health_result["features"]

        result = await client.call_tool(
            "convert_docx_to_zotero",
            {
                "document_base64": base64.b64encode(source).decode("ascii"),
                "filename": "figures.docx",
            },
        )
        assert not result.is_error
        converted = _payload(result)
        assert converted["analysis"]["summary"]["captions"] == 1
        assert converted["analysis"]["summary"]["cross_references"] == 1
        assert converted["report"]["converted_cross_references"] == 1

        document = _payload(
            await client.call_tool(
                "read_artifact",
                {"job_id": converted["job_id"], "name": "document"},
            )
        )

    with zipfile.ZipFile(io.BytesIO(base64.b64decode(document["data_base64"]))) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    instructions = root.xpath(".//w:instrText[starts-with(., ' REF ')]/text()", namespaces=NS)
    assert len(instructions) == 1
    assert "\\h" in instructions[0]
