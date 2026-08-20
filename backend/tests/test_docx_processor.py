import io
import json
import zipfile

from lxml import etree

from backend.app.services.docx_processor import NS, analyze_docx, convert_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _fixture() -> bytes:
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="{W}"><w:body>
      <w:p><w:r><w:t>Smith (2024) supports this.</w:t></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>References</w:t></w:r></w:p>
      <w:p><w:r><w:t>Smith, J. (2024). First paper. Journal, 1(1), 1-2. https://doi.org/10.1000/test</w:t></w:r></w:p>
      <w:sectPr/>
    </w:body></w:document>'''.encode()
    content_types = b'''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        archive.writestr("word/document.xml", document)
        archive.writestr("word/media/unchanged.bin", b"preserve-me")
    return buffer.getvalue()


def test_analyze_and_convert_preserves_unrelated_parts():
    source = _fixture()
    analysis = analyze_docx(source, "paper.docx")
    assert len(analysis.references) == 1
    assert len(analysis.citations) == 1
    converted, report = convert_docx(source, analysis)
    assert report["converted_citations"] == 1
    with zipfile.ZipFile(io.BytesIO(converted)) as archive:
        assert archive.read("word/media/unchanged.bin") == b"preserve-me"
        root = etree.fromstring(archive.read("word/document.xml"))
        instruction = "".join(root.xpath(".//w:instrText/text()", namespaces=NS))
        assert "ADDIN ZOTERO_ITEM CSL_CITATION" in instruction
        payload = instruction.split("CSL_CITATION ", 1)[1].strip()
        assert json.loads(payload)["citationItems"][0]["itemData"]["title"] == "First paper"
        visible = "".join(root.xpath(".//w:p[1]//w:t/text()", namespaces=NS))
        assert visible == "Smith (2024) supports this."


def test_linked_conversion_uses_real_zotero_key_and_uri():
    source = _fixture()
    analysis = analyze_docx(source, "paper.docx")
    reference_id = analysis.references[0].id
    converted, report = convert_docx(
        source,
        analysis,
        {reference_id: {"key": "ABCD2345", "uri": "http://zotero.org/users/7/items/ABCD2345"}},
    )
    with zipfile.ZipFile(io.BytesIO(converted)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        instruction = "".join(root.xpath(".//w:instrText/text()", namespaces=NS))
        payload = json.loads(instruction.split("CSL_CITATION ", 1)[1].strip())
    assert payload["citationItems"][0]["id"] == "ABCD2345"
    assert payload["citationItems"][0]["itemData"]["id"] == "ABCD2345"
    assert payload["citationItems"][0]["uris"] == [
        "http://zotero.org/users/7/items/ABCD2345"
    ]
    assert report["zotero_linkage"].startswith("Citations use canonical")


def test_narrative_conversion_wraps_author_and_year_in_one_field():
    source = _fixture()
    analysis = analyze_docx(source, "paper.docx")
    converted, _ = convert_docx(source, analysis)

    with zipfile.ZipFile(io.BytesIO(converted)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        instruction = "".join(root.xpath(".//w:instrText/text()", namespaces=NS))
        payload = json.loads(instruction.split("CSL_CITATION ", 1)[1].strip())
        first_paragraph = root.xpath(".//w:p[1]", namespaces=NS)[0]

    assert payload["properties"]["formattedCitation"] == "Smith (2024)"
    assert payload["citationItems"][0]["prefix"] == "Smith "
    assert payload["citationItems"][0]["suppress-author"] is True
    assert "".join(first_paragraph.xpath(".//w:t/text()", namespaces=NS)) == (
        "Smith (2024) supports this."
    )
    assert (first_paragraph.xpath("./w:r[1]/w:t/text()", namespaces=NS) or [""])[0] == ""
