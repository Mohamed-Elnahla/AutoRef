from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from backend.app.models import CitationCandidate, CitationItem, Reference

PAREN_CITATION_RE = re.compile(
    r"\((?=[^()]{0,320}\b(?:18|19|20)\d{2}[a-z]?\b)[^()]{1,340}\)"
)
NARRATIVE_RE = re.compile(
    r"\b(?P<authors>[A-Z][\w'’.-]+(?:\s+(?:et al\.|and|&)\s+[A-Z][\w'’.-]+|\s+et al\.)?)"
    r"\s*\((?P<year>(?:18|19|20)\d{2}[a-z]?)\)"
)
NUMERIC_RE = re.compile(r"\[(?=\s*\d)[\d\s,;–-]+\]")
YEAR_RE = re.compile(r"\b((?:18|19|20)\d{2})[a-z]?\b")
SURNAME_RE = re.compile(r"([A-Z][\w'’.-]+)")
LOCATOR_RE = re.compile(r",\s*(?:p{1,2}\.?\s*)?(\d+(?:[-–]\d+)?)\s*$", re.IGNORECASE)


def _surname(value: str) -> str:
    match = SURNAME_RE.search(value.strip())
    return match.group(1).casefold() if match else ""


def _reference_index(references: Sequence[Reference]):
    by_author_year: dict[tuple[str, int], list[Reference]] = defaultdict(list)
    for ref in references:
        if ref.issued_year and ref.first_author_family:
            by_author_year[(ref.first_author_family.casefold(), ref.issued_year)].append(ref)
        elif ref.issued_year and ref.authors and "literal" in ref.authors[0]:
            literal = ref.authors[0]["literal"].split()[0].casefold()
            by_author_year[(literal, ref.issued_year)].append(ref)
    return by_author_year


def _match_author_year(segment: str, index) -> tuple[list[CitationItem], str]:
    years = list(YEAR_RE.finditer(segment))
    items: list[CitationItem] = []
    warnings: list[str] = []
    for year_match in years:
        prefix = segment[: year_match.start()].split(";")[-1]
        author_match = list(SURNAME_RE.finditer(prefix))
        surname = author_match[0].group(1).casefold() if author_match else ""
        refs = index.get((surname, int(year_match.group(1))), [])
        if len(refs) > 1 and len(author_match) > 1:
            second_hint = author_match[1].group(1).casefold()
            narrowed = [
                ref
                for ref in refs
                if len(ref.authors) > 1
                and ref.authors[1].get("family", "").casefold() == second_hint
            ]
            if len(narrowed) == 1:
                refs = narrowed
        if len(refs) == 1:
            locator_match = LOCATOR_RE.search(segment[year_match.end() :].split(";", 1)[0])
            items.append(
                CitationItem(
                    reference_id=refs[0].id,
                    locator=locator_match.group(1).replace("–", "-") if locator_match else "",
                )
            )
        elif len(refs) > 1:
            warnings.append(f"ambiguous {surname.title()} {year_match.group(1)}")
        else:
            warnings.append(f"unmatched {surname.title() or 'author'} {year_match.group(1)}")
    return items, "; ".join(warnings)


def detect_citations(
    paragraphs: Sequence[str], references: Sequence[Reference], reference_start: int | None
) -> list[CitationCandidate]:
    index = _reference_index(references)
    candidates: list[CitationCandidate] = []
    body_end = reference_start if reference_start is not None else len(paragraphs)
    for paragraph_index, text in enumerate(paragraphs[:body_end]):
        occupied: list[tuple[int, int]] = []
        for match in PAREN_CITATION_RE.finditer(text):
            # A bare year in parentheses is handled as the field portion of a narrative citation.
            if re.fullmatch(r"\((?:18|19|20)\d{2}[a-z]?\)", match.group(0)):
                continue
            items, warning = _match_author_year(match.group(0)[1:-1], index)
            if items or warning:
                candidates.append(
                    CitationCandidate(
                        paragraph_index=paragraph_index,
                        start=match.start(),
                        end=match.end(),
                        text=match.group(0),
                        kind="author-date",
                        items=items,
                        confidence=0.98 if items and not warning else 0.55,
                        warning=warning,
                    )
                )
                occupied.append(match.span())
        for match in NARRATIVE_RE.finditer(text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            authors = match.group("authors")
            key = (_surname(authors), int(match.group("year")[:4]))
            refs = index.get(key, [])
            warning = "" if len(refs) == 1 else f"{'ambiguous' if refs else 'unmatched'} narrative citation"
            candidates.append(
                CitationCandidate(
                    paragraph_index=paragraph_index,
                    start=match.start(),
                    end=match.end(),
                    text=match.group(0),
                    kind="author-date",
                    items=[
                        CitationItem(
                            reference_id=refs[0].id,
                            prefix=f"{authors} ",
                            suppress_author=True,
                        )
                    ]
                    if len(refs) == 1
                    else [],
                    confidence=0.96 if len(refs) == 1 else 0.5,
                    warning=warning,
                )
            )
        for match in NUMERIC_RE.finditer(text):
            numbers: list[int] = []
            for part in re.split(r"[,;]", match.group(0).strip("[]")):
                part = part.strip()
                if re.fullmatch(r"\d+[-–]\d+", part):
                    lo, hi = map(int, re.split(r"[-–]", part))
                    numbers.extend(range(lo, hi + 1))
                elif part.isdigit():
                    numbers.append(int(part))
            refs = [references[number - 1] for number in numbers if 0 < number <= len(references)]
            if refs:
                candidates.append(
                    CitationCandidate(
                        paragraph_index=paragraph_index,
                        start=match.start(),
                        end=match.end(),
                        text=match.group(0),
                        kind="numeric",
                        items=[CitationItem(reference_id=ref.id) for ref in refs],
                        confidence=0.94,
                    )
                )
    return sorted(candidates, key=lambda item: (item.paragraph_index, item.start))


def detect_style(citations: Sequence[CitationCandidate]) -> str:
    author_date = sum(item.kind == "author-date" for item in citations)
    numeric = sum(item.kind == "numeric" for item in citations)
    if author_date == numeric == 0:
        return "unknown"
    return "author-date" if author_date >= numeric else "numeric"
