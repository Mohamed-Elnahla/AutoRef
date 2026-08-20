from __future__ import annotations

import copy
import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from lxml import etree

from backend.app.models import Analysis, CitationCandidate, Reference
from backend.app.services.citation_detector import detect_citations, detect_style
from backend.app.services.reference_parser import parse_references

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W_NS}


def qn(name: str) -> str:
    return f"{{{W_NS}}}{name}"


REFERENCE_HEADINGS = {
    "references",
    "reference list",
    "bibliography",
    "works cited",
    "literature cited",
    "références",
    "referencias",
}
STOP_HEADING_RE = re.compile(
    r"^(appendix|annex|supplementary|acknowledg|declaration)", re.IGNORECASE
)


class DocxError(ValueError):
    pass


def validate_docx(data: bytes) -> None:
    if not data.startswith(b"PK"):
        raise DocxError("The uploaded file is not a DOCX package.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise DocxError("The package is missing required Word document parts.")
            bad = archive.testzip()
            if bad:
                raise DocxError(f"The DOCX archive is damaged at {bad}.")
    except zipfile.BadZipFile as exc:
        raise DocxError("The uploaded file is not a readable DOCX package.") from exc


def paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))


def _paragraph_style(paragraph: etree._Element) -> str:
    values = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
    return values[0] if values else ""


def _find_reference_range(paragraphs: list[etree._Element]) -> tuple[int | None, list[str]]:
    heading_index: int | None = None
    for index, paragraph in enumerate(paragraphs):
        text = re.sub(r"\s+", " ", paragraph_text(paragraph)).strip().rstrip(":").casefold()
        if text in REFERENCE_HEADINGS:
            heading_index = index
            break
    if heading_index is None:
        return None, []

    references: list[str] = []
    for paragraph in paragraphs[heading_index + 1 :]:
        text = re.sub(r"\s+", " ", paragraph_text(paragraph)).strip()
        if not text:
            continue
        style = _paragraph_style(paragraph).casefold()
        if STOP_HEADING_RE.match(text) and ("heading" in style or len(text) < 100):
            break
        references.append(text)
    return heading_index, references


def analyze_docx(data: bytes, source_name: str) -> Analysis:
    validate_docx(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    paragraphs = root.xpath(".//w:p", namespaces=NS)
    texts = [paragraph_text(item) for item in paragraphs]
    reference_start, raw_references = _find_reference_range(paragraphs)
    references = parse_references(raw_references)
    citations = detect_citations(texts, references, reference_start)
    warnings: list[str] = []
    if reference_start is None:
        warnings.append("No reference-list heading was found. No citation conversion is safe yet.")
    if references and any(ref.confidence < 0.5 for ref in references):
        warnings.append("Some references have sparse metadata and should be reviewed before import.")
    unmatched = sum(not citation.items for citation in citations)
    if unmatched:
        warnings.append(f"{unmatched} citation candidate(s) could not be matched unambiguously.")
    warnings.append(
        "Phase 1 embeds self-contained orphaned Zotero citations. Importing the CSL-JSON library "
        "does not relink those fields to the newly imported library items; API-backed relinking is phase 2."
    )
    return Analysis(
        source_name=source_name,
        detected_style=detect_style(citations),
        reference_heading_index=reference_start,
        references=references,
        citations=citations,
        warnings=warnings,
    )


def _run_for_text(text_node: etree._Element) -> etree._Element | None:
    current = text_node.getparent()
    while current is not None and current.tag != qn("p"):
        if current.tag == qn("r"):
            return current
        current = current.getparent()
    return None


def _clone_run_properties(run: etree._Element | None) -> etree._Element | None:
    if run is None:
        return None
    rpr = run.find(qn("rPr"))
    return copy.deepcopy(rpr) if rpr is not None else None


def _new_run(child: etree._Element, rpr: etree._Element | None = None) -> etree._Element:
    run = etree.Element(qn("r"))
    if rpr is not None:
        run.append(copy.deepcopy(rpr))
    run.append(child)
    return run


def _text_element(value: str) -> etree._Element:
    node = etree.Element(qn("t"))
    if value[:1].isspace() or value[-1:].isspace():
        node.set(f"{{{XML_NS}}}space", "preserve")
    node.text = value
    return node


def _citation_code(candidate: CitationCandidate, references: dict[str, Reference]) -> str:
    citation_items: list[dict[str, Any]] = []
    for linked in candidate.items:
        ref = references[linked.reference_id]
        item: dict[str, Any] = {
            "id": ref.id,
            "uris": [f"http://zotero.org/users/local/AUTOREF/items/{ref.id[-8:].upper()}"],
            "itemData": ref.to_csl(),
        }
        if linked.locator:
            item.update({"locator": linked.locator, "label": linked.label})
        if linked.prefix:
            item["prefix"] = linked.prefix
        if linked.suffix:
            item["suffix"] = linked.suffix
        if linked.suppress_author:
            item["suppress-author"] = True
        citation_items.append(item)
    citation = {
        "citationID": f"autoref-{candidate.paragraph_index}-{candidate.start}",
        "properties": {
            "formattedCitation": candidate.text,
            "plainCitation": candidate.text,
            "noteIndex": 0,
        },
        "citationItems": citation_items,
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
    }
    compact = json.dumps(citation, ensure_ascii=False, separators=(",", ":"))
    return f" ADDIN ZOTERO_ITEM CSL_CITATION {compact} "


def _field_runs(candidate: CitationCandidate, refs: dict[str, Reference], rpr):
    begin = etree.Element(qn("fldChar"))
    begin.set(qn("fldCharType"), "begin")
    begin.set(qn("dirty"), "true")
    instruction = etree.Element(qn("instrText"))
    instruction.set(f"{{{XML_NS}}}space", "preserve")
    instruction.text = _citation_code(candidate, refs)
    separate = etree.Element(qn("fldChar"))
    separate.set(qn("fldCharType"), "separate")
    end = etree.Element(qn("fldChar"))
    end.set(qn("fldCharType"), "end")
    return [
        _new_run(begin),
        _new_run(instruction),
        _new_run(separate),
        _new_run(_text_element(candidate.text), rpr),
        _new_run(end),
    ]


def _replace_span(paragraph: etree._Element, candidate: CitationCandidate, refs) -> None:
    text_nodes = paragraph.xpath(".//w:t", namespaces=NS)
    positions: list[tuple[etree._Element, int, int]] = []
    cursor = 0
    for node in text_nodes:
        value = node.text or ""
        positions.append((node, cursor, cursor + len(value)))
        cursor += len(value)
    touched = [item for item in positions if item[1] < candidate.end and item[2] > candidate.start]
    if not touched:
        raise DocxError(f"Could not locate citation text {candidate.text!r} in paragraph XML.")
    start_node, start_global, _ = touched[0]
    end_node, end_global_start, _ = touched[-1]
    start_run = _run_for_text(start_node)
    if start_run is None or start_run.getparent() is not paragraph:
        raise DocxError("A matched citation is nested in unsupported complex Word markup.")
    rpr = _clone_run_properties(start_run)
    start_local = candidate.start - start_global
    end_local = candidate.end - end_global_start
    start_value = start_node.text or ""
    end_value = end_node.text or ""
    before = start_value[:start_local]
    suffix = end_value[end_local:]
    start_node.text = before
    if before[:1].isspace() or before[-1:].isspace():
        start_node.set(f"{{{XML_NS}}}space", "preserve")

    for node, _, _ in touched[1:]:
        node.text = ""
    if end_node is not start_node:
        end_node.text = suffix
        if suffix[:1].isspace() or suffix[-1:].isspace():
            end_node.set(f"{{{XML_NS}}}space", "preserve")

    insertion_index = paragraph.index(start_run) + 1
    for field_run in _field_runs(candidate, refs, rpr):
        paragraph.insert(insertion_index, field_run)
        insertion_index += 1
    if end_node is start_node and suffix:
        paragraph.insert(insertion_index, _new_run(_text_element(suffix), rpr))


def convert_docx(data: bytes, analysis: Analysis) -> tuple[bytes, dict[str, Any]]:
    refs = {ref.id: ref for ref in analysis.references}
    selected = [candidate for candidate in analysis.citations if candidate.items]
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        root = etree.fromstring(source.read("word/document.xml"))
        paragraphs = root.xpath(".//w:p", namespaces=NS)
        grouped: dict[int, list[CitationCandidate]] = defaultdict(list)
        for candidate in selected:
            grouped[candidate.paragraph_index].append(candidate)
        converted = 0
        skipped: list[str] = []
        for paragraph_index, candidates in grouped.items():
            for candidate in sorted(candidates, key=lambda item: item.start, reverse=True):
                try:
                    _replace_span(paragraphs[paragraph_index], candidate, refs)
                    converted += 1
                except DocxError as exc:
                    skipped.append(str(exc))
        document_xml = etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        output_buffer = io.BytesIO()
        with zipfile.ZipFile(output_buffer, "w") as target:
            for info in source.infolist():
                payload = document_xml if info.filename == "word/document.xml" else source.read(info.filename)
                target.writestr(info, payload)
    report = {
        "converted_citations": converted,
        "skipped_citations": skipped,
        "unchanged_parts": "All DOCX package parts except word/document.xml are copied byte-for-byte.",
        "zotero_linkage": "Embedded citations are dynamic but orphaned until phase-two API relinking.",
    }
    return output_buffer.getvalue(), report


def write_csl_json(references: list[Reference], destination: Path) -> None:
    destination.write_text(
        json.dumps([ref.to_csl() for ref in references], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
