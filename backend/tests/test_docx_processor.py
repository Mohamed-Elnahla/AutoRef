import io
import json
import zipfile

from lxml import etree

from backend.app.services.docx_processor import NS, analyze_docx, convert_docx, paragraph_text

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _fixture(body: str | None = None, relationships: bytes | None = None) -> bytes:
    body = body or """
      <w:p><w:r><w:t>Smith (2024) supports this.</w:t></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>References</w:t></w:r></w:p>
      <w:p><w:r><w:t>Smith, J. (2024). First paper. Journal, 1(1), 1-2. https://doi.org/10.1000/test</w:t></w:r></w:p>
    """
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>
      {body}
      <w:sectPr/>
    </w:body></w:document>'''.encode()
    content_types = b'''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        archive.writestr("word/document.xml", document)
        if relationships:
            archive.writestr("word/_rels/document.xml.rels", relationships)
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
        payload = instruction.split("CSL_CITATION ", 1)[1].split(" ADDIN ZOTERO_BIBL", 1)[0].strip()
        assert json.loads(payload)["citationItems"][0]["itemData"]["title"] == "First paper"
        assert (
            'ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} '
            "CSL_BIBLIOGRAPHY"
        ) in instruction
        visible = "".join(root.xpath(".//w:p[1]//w:t/text()", namespaces=NS))
        assert visible == "Smith (2024) supports this."
    assert report["converted_bibliography"] is True
    assert report["bibliography_entries"] == 1


def test_analyze_extracts_doi_from_reference_hyperlink_target():
    source = _fixture(
        """
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>References</w:t></w:r></w:p>
        <w:p><w:r><w:t>Smith, J. (2024). First paper. Journal, 1(1), 1-2. </w:t></w:r><w:hyperlink r:id="doi-link"><w:r><w:t>Read online</w:t></w:r></w:hyperlink></w:p>
        """,
        b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="doi-link" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://doi.org/10.1000%2Fhyperlinked-doi" TargetMode="External"/></Relationships>''',
    )

    analysis = analyze_docx(source, "paper.docx")

    assert analysis.references[0].doi == "10.1000/hyperlinked-doi"


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
        instruction = root.xpath(
            ".//w:instrText[contains(., 'ZOTERO_ITEM')]/text()", namespaces=NS
        )[0]
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
        instruction = root.xpath(
            ".//w:instrText[contains(., 'ZOTERO_ITEM')]/text()", namespaces=NS
        )[0]
        payload = json.loads(instruction.split("CSL_CITATION ", 1)[1].strip())
        first_paragraph = root.xpath(".//w:p[1]", namespaces=NS)[0]

    assert payload["properties"]["formattedCitation"] == "Smith (2024)"
    assert payload["citationItems"][0]["prefix"] == "Smith "
    assert payload["citationItems"][0]["suppress-author"] is True
    assert "".join(first_paragraph.xpath(".//w:t/text()", namespaces=NS)) == (
        "Smith (2024) supports this."
    )
    assert (first_paragraph.xpath("./w:r[1]/w:t/text()", namespaces=NS) or [""])[0] == ""


def test_bibliography_field_spans_references_but_stops_before_appendix():
    source = _fixture(
        """
        <w:p><w:r><w:t>Smith (2024) supports this.</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>References</w:t></w:r></w:p>
        <w:p><w:r><w:t>Smith, J. (2024). First paper. Journal, 1(1), 1-2.</w:t></w:r></w:p>
        <w:p><w:r><w:t>Doe, A. (2023). Second paper. Journal, 2(1), 3-4.</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Appendix A</w:t></w:r></w:p>
        <w:p><w:r><w:t>Supplemental content.</w:t></w:r></w:p>
        """
    )
    analysis = analyze_docx(source, "paper.docx")
    converted, report = convert_docx(source, analysis)

    with zipfile.ZipFile(io.BytesIO(converted)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        paragraphs = root.xpath(".//w:p", namespaces=NS)

    assert report["bibliography_entries"] == 2
    assert paragraphs[2].xpath(
        ".//w:instrText[contains(., 'ZOTERO_BIBL')]", namespaces=NS
    )
    assert paragraphs[3].xpath(
        ".//w:fldChar[@w:fldCharType='end']", namespaces=NS
    )
    assert not paragraphs[4].xpath(".//w:fldChar", namespaces=NS)
    assert paragraph_text(paragraphs[4]) == "Appendix A"


def test_figure_and_table_mentions_become_styled_word_cross_references():
    source = _fixture(
        """
        <w:p><w:r><w:t>See Figure </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>1</w:t></w:r><w:r><w:t> and Table 2 for details.</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr><w:r><w:t>Figure 1. System overview.</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr><w:r><w:t>Table 2: Results.</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>References</w:t></w:r></w:p>
        <w:p><w:r><w:t>Smith, J. (2024). First paper. Journal, 1(1), 1-2.</w:t></w:r></w:p>
        """
    )
    analysis = analyze_docx(source, "paper.docx")

    assert [(item.kind, item.number) for item in analysis.captions] == [
        ("figure", "1"),
        ("table", "2"),
    ]
    assert [item.text for item in analysis.cross_references] == ["1", "2"]

    converted, report = convert_docx(source, analysis)
    with zipfile.ZipFile(io.BytesIO(converted)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        paragraphs = root.xpath(".//w:p", namespaces=NS)

    assert report["detected_captions"] == 2
    assert report["converted_captions"] == 2
    assert report["converted_cross_references"] == 2
    assert report["skipped_cross_references"] == []
    assert paragraph_text(paragraphs[0]) == "See Figure 1 and Table 2 for details."
    instructions = root.xpath(".//w:instrText[starts-with(., ' REF ')]/text()", namespaces=NS)
    assert len(instructions) == 2
    assert all("\\h" in instruction for instruction in instructions)
    assert len(root.xpath(".//w:bookmarkStart[starts-with(@w:name, 'AutoRef_')]", namespaces=NS)) == 2
    seq_codes = root.xpath(".//w:instrText[starts-with(., ' SEQ ')]/text()", namespaces=NS)
    assert seq_codes == [" SEQ Figure \\* ARABIC ", " SEQ Table \\* ARABIC "]
    assert len(root.xpath(".//w:p[w:pPr/w:pStyle[@w:val='Caption']]", namespaces=NS)) == 2

    bold_cross_reference_results = paragraphs[0].xpath(
        ".//w:r[w:rPr/w:b and w:t='1']", namespaces=NS
    )
    assert len(bold_cross_reference_results) == 1


def test_split_caption_label_and_title_become_one_native_word_caption():
    source = _fixture(
        """
        <w:p><w:r><w:t>See Figure 1.</w:t></w:r></w:p>
        <w:p><w:r><w:t>Figure 1</w:t></w:r></w:p>
        <w:p><w:r><w:t>System overview</w:t></w:r></w:p>
        <w:p><w:r><w:drawing/></w:r></w:p>
        """
    )
    analysis = analyze_docx(source, "paper.docx")
    converted, report = convert_docx(source, analysis)

    with zipfile.ZipFile(io.BytesIO(converted)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        captions = root.xpath(".//w:p[w:pPr/w:pStyle[@w:val='Caption']]", namespaces=NS)

    assert report["converted_captions"] == 1
    assert report["converted_cross_references"] == 1
    assert paragraph_text(captions[0]) == "Figure 1. System overview"
    assert captions[0].xpath(".//w:instrText[text()=' SEQ Figure \\* ARABIC ']", namespaces=NS)
    assert captions[0].xpath(".//w:bookmarkStart[starts-with(@w:name, 'AutoRef_Figure_1')]", namespaces=NS)
