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

from backend.app.models import (
    Analysis,
    Caption,
    CitationCandidate,
    CrossReferenceCandidate,
    Reference,
)
from backend.app.services.citation_detector import detect_citations, detect_style
from backend.app.services.reference_parser import parse_references
from backend.app.services.word_crossrefs import detect_cross_references

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W_NS, "r": R_NS}


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
ZOTERO_BIBLIOGRAPHY_CODE = (
    ' ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY '
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


def _is_near_captioned_object(paragraph: etree._Element) -> bool:
    """Accept label-only captions only when a table or graphic is adjacent.

    A title paragraph and blank spacer are allowed between the label and its
    object, covering both the common ``caption → title → object`` and
    ``object → title → caption`` layouts without treating a standalone prose
    reference as a caption.
    """
    parent = paragraph.getparent()
    if parent is None:
        return False
    siblings = list(parent)
    try:
        index = siblings.index(paragraph)
    except ValueError:
        return False
    for direction in (-1, 1):
        prose_between = 0
        for offset in range(1, 4):
            candidate_index = index + direction * offset
            if not 0 <= candidate_index < len(siblings):
                break
            candidate = siblings[candidate_index]
            if candidate.tag == qn("tbl") or candidate.xpath(
                ".//w:drawing|.//w:pict|.//w:object", namespaces=NS
            ):
                return True
            if candidate.tag == qn("p") and paragraph_text(candidate).strip():
                prose_between += 1
                if prose_between > 1:
                    break
    return False


def _hyperlink_targets(archive: zipfile.ZipFile) -> dict[str, str]:
    """Return external hyperlink destinations from the main document part."""
    try:
        root = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    return {
        relationship.get("Id", ""): relationship.get("Target", "")
        for relationship in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        if relationship.get("Type", "").endswith("/hyperlink")
        and relationship.get("TargetMode") == "External"
        and relationship.get("Target")
    }


def _reference_paragraph_text(paragraph: etree._Element, hyperlink_targets: dict[str, str]) -> str:
    """Include hyperlink destinations, whose URL is often absent from visible text."""
    targets = [
        hyperlink_targets[relationship_id]
        for relationship_id in paragraph.xpath(".//w:hyperlink/@r:id", namespaces=NS)
        if relationship_id in hyperlink_targets
    ]
    return " ".join([paragraph_text(paragraph), *targets])


def _find_reference_range(
    paragraphs: list[etree._Element], hyperlink_targets: dict[str, str] | None = None
) -> tuple[int | None, list[str]]:
    hyperlink_targets = hyperlink_targets or {}
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
        text = re.sub(r"\s+", " ", _reference_paragraph_text(paragraph, hyperlink_targets)).strip()
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
        hyperlink_targets = _hyperlink_targets(archive)
    paragraphs = root.xpath(".//w:p", namespaces=NS)
    texts = [paragraph_text(item) for item in paragraphs]
    paragraph_styles = [_paragraph_style(item) for item in paragraphs]
    standalone_caption_contexts = [_is_near_captioned_object(item) for item in paragraphs]
    reference_start, raw_references = _find_reference_range(paragraphs, hyperlink_targets)
    references = parse_references(raw_references)
    citations = detect_citations(texts, references, reference_start)
    captions, cross_references, crossref_warnings = detect_cross_references(
        texts, reference_start, paragraph_styles, standalone_caption_contexts
    )
    warnings: list[str] = []
    if reference_start is None:
        warnings.append(
            "No reference-list heading was found. Bibliographic citations cannot be converted, "
            "but unambiguous figure/table cross-references can still be converted."
        )
    if references and any(ref.confidence < 0.5 for ref in references):
        warnings.append("Some references have sparse metadata and should be reviewed before import.")
    unmatched = sum(not citation.items for citation in citations)
    if unmatched:
        warnings.append(f"{unmatched} citation candidate(s) could not be matched unambiguously.")
    warnings.extend(crossref_warnings)
    warnings.append(
        "Local conversion embeds self-contained Zotero citations but cannot link them to library "
        "items. Connect Zotero and confirm an import preview to generate fully linked fields."
    )
    return Analysis(
        source_name=source_name,
        detected_style=detect_style(citations),
        reference_heading_index=reference_start,
        references=references,
        citations=citations,
        captions=captions,
        cross_references=cross_references,
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


def _citation_code(
    candidate: CitationCandidate,
    references: dict[str, Reference],
    zotero_items: dict[str, dict[str, str]] | None = None,
) -> str:
    citation_items: list[dict[str, Any]] = []
    for linked in candidate.items:
        ref = references[linked.reference_id]
        linked_item = (zotero_items or {}).get(ref.id)
        item_data = ref.to_csl()
        if linked_item:
            item_data["id"] = linked_item["key"]
        item: dict[str, Any] = {
            "id": linked_item["key"] if linked_item else ref.id,
            "uris": [
                linked_item["uri"]
                if linked_item
                else f"http://zotero.org/users/local/AUTOREF/items/{ref.id[-8:].upper()}"
            ],
            "itemData": item_data,
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


def _field_runs(candidate: CitationCandidate, refs, rpr, zotero_items=None):
    begin = etree.Element(qn("fldChar"))
    begin.set(qn("fldCharType"), "begin")
    begin.set(qn("dirty"), "true")
    instruction = etree.Element(qn("instrText"))
    instruction.set(f"{{{XML_NS}}}space", "preserve")
    instruction.text = _citation_code(candidate, refs, zotero_items)
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


def _cross_reference_field_runs(
    candidate: CrossReferenceCandidate, bookmark_name: str, rpr: etree._Element | None
) -> list[etree._Element]:
    begin = etree.Element(qn("fldChar"))
    begin.set(qn("fldCharType"), "begin")
    begin.set(qn("dirty"), "true")
    instruction = etree.Element(qn("instrText"))
    instruction.set(f"{{{XML_NS}}}space", "preserve")
    instruction.text = f" REF {bookmark_name} \\h "
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


def _caption_sequence_field_runs(
    caption: Caption, rpr: etree._Element | None
) -> list[etree._Element]:
    """Create the SEQ field Word uses for Insert Caption labels.

    The identifier deliberately matches Word's built-in caption label (Figure
    or Table), which lets Word's Table of Figures/Table of Tables fields collect
    these captions by using their respective ``\\c`` switches.
    """
    label = caption.kind.title()
    begin = etree.Element(qn("fldChar"))
    begin.set(qn("fldCharType"), "begin")
    begin.set(qn("dirty"), "true")
    instruction = etree.Element(qn("instrText"))
    instruction.set(f"{{{XML_NS}}}space", "preserve")
    instruction.text = f" SEQ {label} \\* ARABIC "
    separate = etree.Element(qn("fldChar"))
    separate.set(qn("fldCharType"), "separate")
    end = etree.Element(qn("fldChar"))
    end.set(qn("fldCharType"), "end")
    return [
        _new_run(begin),
        _new_run(instruction),
        _new_run(separate),
        _new_run(_text_element(caption.number), rpr),
        _new_run(end),
    ]


def _apply_caption_style(paragraph: etree._Element) -> None:
    ppr = paragraph.find(qn("pPr"))
    if ppr is None:
        ppr = etree.Element(qn("pPr"))
        paragraph.insert(0, ppr)
    style = ppr.find(qn("pStyle"))
    if style is None:
        style = etree.Element(qn("pStyle"))
        ppr.insert(0, style)
    style.set(qn("val"), "Caption")


def _replace_caption_number_with_sequence(paragraph: etree._Element, caption: Caption) -> None:
    """Replace a typed caption number with Word's live ``SEQ`` field."""
    text_nodes = paragraph.xpath(".//w:t", namespaces=NS)
    positions: list[tuple[etree._Element, int, int]] = []
    cursor = 0
    for node in text_nodes:
        value = node.text or ""
        positions.append((node, cursor, cursor + len(value)))
        cursor += len(value)
    touched = [item for item in positions if item[1] < caption.end and item[2] > caption.start]
    if not touched:
        raise DocxError(f"Could not locate caption number {caption.number!r} in paragraph XML.")
    start_node, start_global, _ = touched[0]
    end_node, end_global_start, _ = touched[-1]
    start_run = _run_for_text(start_node)
    if start_run is None or start_run.getparent() is not paragraph:
        raise DocxError("A detected caption is nested in unsupported complex Word markup.")
    rpr = _clone_run_properties(start_run)
    start_local = caption.start - start_global
    end_local = caption.end - end_global_start
    before = (start_node.text or "")[:start_local]
    suffix = (end_node.text or "")[end_local:]
    start_node.text = before
    for node, _, _ in touched[1:]:
        node.text = ""
    if end_node is not start_node:
        end_node.text = suffix
    insertion_index = paragraph.index(start_run) + 1
    for field_run in _caption_sequence_field_runs(caption, rpr):
        paragraph.insert(insertion_index, field_run)
        insertion_index += 1
    if end_node is start_node and suffix:
        paragraph.insert(insertion_index, _new_run(_text_element(suffix), rpr))


def _merge_split_caption_title(paragraphs: list[etree._Element], caption: Caption) -> None:
    """Fold ``Figure 1`` + following title into one Caption paragraph when safe."""
    paragraph = paragraphs[caption.paragraph_index]
    if re.sub(r"\s+", " ", paragraph_text(paragraph)).strip() != caption.text:
        return
    try:
        title_paragraph = paragraphs[caption.paragraph_index + 1]
    except IndexError:
        return
    title = paragraph_text(title_paragraph).strip()
    if not title or title_paragraph.xpath(".//w:drawing|.//w:pict|.//w:object", namespaces=NS):
        return
    paragraph.append(_new_run(_text_element(". ")))
    for child in list(title_paragraph):
        if child.tag != qn("pPr"):
            paragraph.append(copy.deepcopy(child))
    title_paragraph.getparent().remove(title_paragraph)


def _text_boundary_index(
    paragraph: etree._Element, offset: int, *, include_zero_width_after: bool = False
) -> int | None:
    """Return a paragraph child boundary for a visible-text offset, splitting a run if needed."""
    cursor = 0
    children = list(paragraph)
    for index, child in enumerate(children):
        value = paragraph_text(child)
        child_end = cursor + len(value)
        if offset == cursor:
            if include_zero_width_after:
                boundary = index
                while boundary < len(children):
                    boundary_child = children[boundary]
                    if paragraph_text(boundary_child):
                        break
                    boundary += 1
                return boundary
            return index
        if cursor < offset < child_end:
            if child.tag != qn("r"):
                return None
            text_nodes = child.xpath(".//w:t", namespaces=NS)
            if len(text_nodes) != 1:
                return None
            local = offset - cursor
            original = text_nodes[0].text or ""
            left = copy.deepcopy(child)
            right = copy.deepcopy(child)
            left.xpath(".//w:t", namespaces=NS)[0].text = original[:local]
            right_text = right.xpath(".//w:t", namespaces=NS)[0]
            right_text.text = original[local:]
            if right_text.text[:1].isspace() or right_text.text[-1:].isspace():
                right_text.set(f"{{{XML_NS}}}space", "preserve")
            paragraph.remove(child)
            paragraph.insert(index, left)
            paragraph.insert(index + 1, right)
            return index + 1
        cursor = child_end
    return len(children) if offset == cursor else None


def _bookmark_caption_number(
    paragraph: etree._Element, caption: Caption, bookmark_name: str, bookmark_id: int
) -> None:
    end_index = _text_boundary_index(paragraph, caption.end, include_zero_width_after=True)
    if end_index is None:
        raise DocxError(f"Could not bookmark caption {caption.text!r} in paragraph XML.")
    bookmark_end = etree.Element(qn("bookmarkEnd"))
    bookmark_end.set(qn("id"), str(bookmark_id))
    paragraph.insert(end_index, bookmark_end)

    start_index = _text_boundary_index(paragraph, caption.start)
    if start_index is None:
        paragraph.remove(bookmark_end)
        raise DocxError(f"Could not bookmark caption {caption.text!r} in paragraph XML.")
    bookmark_start = etree.Element(qn("bookmarkStart"))
    bookmark_start.set(qn("id"), str(bookmark_id))
    bookmark_start.set(qn("name"), bookmark_name)
    paragraph.insert(start_index, bookmark_start)


def _bookmark_name(caption: Caption, used_names: set[str]) -> str:
    number = re.sub(r"[^A-Za-z0-9]", "_", caption.number)
    base = f"AutoRef_{caption.kind.title()}_{number}"[:40]
    name = base
    suffix = 2
    while name in used_names:
        marker = f"_{suffix}"
        name = f"{base[:40 - len(marker)]}{marker}"
        suffix += 1
    used_names.add(name)
    return name


def _reference_result_paragraphs(
    paragraphs: list[etree._Element], heading_index: int | None
) -> list[etree._Element]:
    if heading_index is None:
        return []
    result: list[etree._Element] = []
    for paragraph in paragraphs[heading_index + 1 :]:
        text = re.sub(r"\s+", " ", paragraph_text(paragraph)).strip()
        if not text:
            continue
        style = _paragraph_style(paragraph).casefold()
        if STOP_HEADING_RE.match(text) and ("heading" in style or len(text) < 100):
            break
        result.append(paragraph)
    return result


def _wrap_reference_list_as_bibliography(
    paragraphs: list[etree._Element], heading_index: int | None
) -> int:
    result_paragraphs = _reference_result_paragraphs(paragraphs, heading_index)
    if not result_paragraphs:
        return 0
    if any(
        "ZOTERO_BIBL" in instruction
        for paragraph in result_paragraphs
        for instruction in paragraph.xpath(".//w:instrText/text()", namespaces=NS)
    ):
        return 0

    begin = etree.Element(qn("fldChar"))
    begin.set(qn("fldCharType"), "begin")
    begin.set(qn("dirty"), "true")
    instruction = etree.Element(qn("instrText"))
    instruction.set(f"{{{XML_NS}}}space", "preserve")
    instruction.text = ZOTERO_BIBLIOGRAPHY_CODE
    separate = etree.Element(qn("fldChar"))
    separate.set(qn("fldCharType"), "separate")
    end = etree.Element(qn("fldChar"))
    end.set(qn("fldCharType"), "end")

    first = result_paragraphs[0]
    insertion_index = 1 if len(first) and first[0].tag == qn("pPr") else 0
    for run in (_new_run(begin), _new_run(instruction), _new_run(separate)):
        first.insert(insertion_index, run)
        insertion_index += 1
    result_paragraphs[-1].append(_new_run(end))
    return len(result_paragraphs)


def _replace_span(paragraph, candidate: CitationCandidate, refs, zotero_items=None) -> None:
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
    for field_run in _field_runs(candidate, refs, rpr, zotero_items):
        paragraph.insert(insertion_index, field_run)
        insertion_index += 1
    if end_node is start_node and suffix:
        paragraph.insert(insertion_index, _new_run(_text_element(suffix), rpr))


def _replace_cross_reference_span(
    paragraph: etree._Element,
    candidate: CrossReferenceCandidate,
    bookmark_name: str,
) -> None:
    text_nodes = paragraph.xpath(".//w:t", namespaces=NS)
    positions: list[tuple[etree._Element, int, int]] = []
    cursor = 0
    for node in text_nodes:
        value = node.text or ""
        positions.append((node, cursor, cursor + len(value)))
        cursor += len(value)
    touched = [item for item in positions if item[1] < candidate.end and item[2] > candidate.start]
    if not touched:
        raise DocxError(f"Could not locate cross-reference text {candidate.text!r} in paragraph XML.")
    start_node, start_global, _ = touched[0]
    end_node, end_global_start, _ = touched[-1]
    start_run = _run_for_text(start_node)
    if start_run is None or start_run.getparent() is not paragraph:
        raise DocxError("A matched cross-reference is nested in unsupported complex Word markup.")
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
    for field_run in _cross_reference_field_runs(candidate, bookmark_name, rpr):
        paragraph.insert(insertion_index, field_run)
        insertion_index += 1
    if end_node is start_node and suffix:
        paragraph.insert(insertion_index, _new_run(_text_element(suffix), rpr))


def convert_docx(
    data: bytes,
    analysis: Analysis,
    zotero_items: dict[str, dict[str, str]] | None = None,
    excluded_reference_ids: set[str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    refs = {ref.id: ref for ref in analysis.references}
    excluded_reference_ids = excluded_reference_ids or set()
    selected = [
        candidate
        for candidate in analysis.citations
        if candidate.items
        and not any(item.reference_id in excluded_reference_ids for item in candidate.items)
    ]
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        root = etree.fromstring(source.read("word/document.xml"))
        paragraphs = root.xpath(".//w:p", namespaces=NS)
        excluded_raw = {
            re.sub(r"\s+", " ", refs[reference_id].raw).strip()
            for reference_id in excluded_reference_ids
            if reference_id in refs
        }
        removed_references = 0
        if excluded_raw:
            for paragraph in list(_reference_result_paragraphs(paragraphs, analysis.reference_heading_index)):
                text = re.sub(r"\s+", " ", paragraph_text(paragraph)).strip()
                if text in excluded_raw:
                    paragraph.getparent().remove(paragraph)
                    removed_references += 1
            paragraphs = root.xpath(".//w:p", namespaces=NS)

        used_bookmark_names = set(root.xpath(".//w:bookmarkStart/@w:name", namespaces=NS))
        existing_bookmark_ids = []
        for raw_id in root.xpath(".//w:bookmarkStart/@w:id", namespaces=NS):
            try:
                existing_bookmark_ids.append(int(raw_id))
            except ValueError:
                continue
        next_bookmark_id = max(existing_bookmark_ids, default=0) + 1
        bookmark_names: dict[str, str] = {}
        skipped_cross_references: list[str] = []
        converted_captions = 0
        for caption in analysis.captions:
            bookmark_name = _bookmark_name(caption, used_bookmark_names)
            try:
                caption_paragraph = paragraphs[caption.paragraph_index]
                _merge_split_caption_title(paragraphs, caption)
                _apply_caption_style(caption_paragraph)
                _replace_caption_number_with_sequence(caption_paragraph, caption)
                _bookmark_caption_number(caption_paragraph, caption, bookmark_name, next_bookmark_id)
            except (DocxError, IndexError) as exc:
                skipped_cross_references.append(str(exc))
                continue
            bookmark_names[caption.id] = bookmark_name
            next_bookmark_id += 1
            converted_captions += 1

        grouped: dict[int, list[CitationCandidate]] = defaultdict(list)
        for candidate in selected:
            grouped[candidate.paragraph_index].append(candidate)
        converted = 0
        skipped: list[str] = []
        for paragraph_index, candidates in grouped.items():
            for candidate in sorted(candidates, key=lambda item: item.start, reverse=True):
                try:
                    _replace_span(paragraphs[paragraph_index], candidate, refs, zotero_items)
                    converted += 1
                except DocxError as exc:
                    skipped.append(str(exc))

        grouped_cross_references: dict[int, list[CrossReferenceCandidate]] = defaultdict(list)
        for candidate in analysis.cross_references:
            if candidate.caption_id in bookmark_names:
                grouped_cross_references[candidate.paragraph_index].append(candidate)
        converted_cross_references = 0
        for paragraph_index, candidates in grouped_cross_references.items():
            for candidate in sorted(candidates, key=lambda item: item.start, reverse=True):
                try:
                    _replace_cross_reference_span(
                        paragraphs[paragraph_index],
                        candidate,
                        bookmark_names[candidate.caption_id],
                    )
                    converted_cross_references += 1
                except (DocxError, IndexError) as exc:
                    skipped_cross_references.append(str(exc))
        bibliography_entries = _wrap_reference_list_as_bibliography(
            paragraphs, analysis.reference_heading_index
        )
        document_xml = etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        settings_xml: bytes | None = None
        if "word/settings.xml" in source.namelist():
            settings_root = etree.fromstring(source.read("word/settings.xml"))
            update_fields = settings_root.find("w:updateFields", namespaces=NS)
            if update_fields is None:
                update_fields = etree.SubElement(settings_root, qn("updateFields"))
            update_fields.set(qn("val"), "true")
            settings_xml = etree.tostring(
                settings_root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
        output_buffer = io.BytesIO()
        with zipfile.ZipFile(output_buffer, "w") as target:
            for info in source.infolist():
                payload = document_xml if info.filename == "word/document.xml" else source.read(info.filename)
                if info.filename == "word/settings.xml" and settings_xml is not None:
                    payload = settings_xml
                target.writestr(info, payload)
    report = {
        "converted_citations": converted,
        "detected_captions": len(analysis.captions),
        "converted_captions": converted_captions,
        "converted_cross_references": converted_cross_references,
        "converted_bibliography": bool(bibliography_entries),
        "bibliography_entries": bibliography_entries,
        "skipped_citations": skipped,
        "skipped_cross_references": skipped_cross_references,
        "excluded_references": removed_references,
        "unlinked_excluded_citations": sum(
            1
            for candidate in analysis.citations
            if candidate.items
            and any(item.reference_id in excluded_reference_ids for item in candidate.items)
        ),
        "unchanged_parts": "All DOCX package parts except word/document.xml are copied byte-for-byte.",
        "zotero_linkage": (
            "Citations use canonical Zotero library item keys and URIs."
            if zotero_items
            else "Citations contain embedded metadata but are not linked to library items."
        ),
    }
    return output_buffer.getvalue(), report


def write_csl_json(references: list[Reference], destination: Path) -> None:
    destination.write_text(
        json.dumps([ref.to_csl() for ref in references], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
