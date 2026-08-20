from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit

from backend.app.models import Reference

# DOI suffixes are intentionally permissive: publishers use several valid punctuation
# characters and references often put the identifier behind a URL or "doi:" label.
DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[^\s\"<>]+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
YEAR_RE = re.compile(
    r"\((?P<year>(?:18|19|20)\d{2}|n\.d\.)[a-z]?\)\.?", re.IGNORECASE
)
PAGES_RE = re.compile(r"\b(?P<pages>\d{1,6}(?:[-–]\d{1,6})?)\b")
VOLUME_ISSUE_RE = re.compile(r"\b(?P<volume>\d{1,4})(?:\((?P<issue>[^)]+)\))?\s*,\s*(?P<page>\d+[-–]\d+)")


def stable_reference_id(raw: str) -> str:
    return "autoref-" + hashlib.sha1(raw.strip().encode("utf-8")).hexdigest()[:12]


def _clean_token(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,")


def extract_doi(value: str) -> str:
    """Extract and clean a DOI embedded in a reference or URL.

    This is also used just before network verification, so a DOI missed or
    partially captured during initial parsing gets one last recovery attempt.
    """
    # Resolver links often carry a percent-encoded slash (particularly when a
    # DOI is passed through a redirect or query parameter).  Decode URL tokens
    # before applying the normal DOI matcher.  Looking at doi.org paths first
    # also avoids accidentally retaining tracking query parameters as DOI text.
    for url in URL_RE.findall(value):
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower()
        if host in {"doi.org", "www.doi.org", "dx.doi.org"}:
            candidate = _percent_decode(parsed.path.lstrip("/"))
            match = DOI_RE.search(candidate)
            if match:
                return _clean_doi(match.group(1))

    match = DOI_RE.search(_percent_decode(value))
    if not match:
        return ""
    return _clean_doi(match.group(1))


def _percent_decode(value: str) -> str:
    """Decode nested URL encoding without letting malformed escapes fail parsing."""
    for _ in range(2):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def _clean_doi(doi: str) -> str:
    """Remove reference punctuation that is not part of a DOI suffix."""
    doi = doi.rstrip(".,;:!?”’]}>")
    # A closing parenthesis is valid in a DOI only when it closes an opening one.
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1]
    return doi


def _parse_authors(author_block: str) -> list[dict[str, str]]:
    block = re.sub(r"\s+", " ", author_block).strip().rstrip(".")
    if not block:
        return []
    # APA names are comma-sensitive, so split at author separators rather than every comma.
    chunks = re.split(r"\s*,?\s*&\s*|\s*;\s*|\s*,\s*(?=[A-Z][\w'’-]+,)", block)
    authors: list[dict[str, str]] = []
    for chunk in chunks:
        chunk = chunk.strip(" ,")
        if not chunk:
            continue
        if "," in chunk:
            family, given = chunk.split(",", 1)
        else:
            pieces = chunk.split()
            # Corporate authors are kept as literals when no initials are visible.
            if len(pieces) > 2 and not any("." in piece for piece in pieces):
                authors.append({"literal": chunk})
                continue
            family, given = pieces[-1], " ".join(pieces[:-1])
        authors.append({"family": _clean_token(family), "given": _clean_token(given)})
    return authors


def parse_reference(raw: str) -> Reference:
    normalized = re.sub(r"\s+", " ", raw).strip()
    ref = Reference(id=stable_reference_id(normalized), raw=normalized)
    year_match = YEAR_RE.search(normalized)
    if year_match:
        year_text = year_match.group("year")
        ref.issued_year = int(year_text[:4]) if year_text[:4].isdigit() else None
        ref.authors = _parse_authors(normalized[: year_match.start()])
        remainder = normalized[year_match.end() :].lstrip(" .")
    else:
        remainder = normalized

    ref.doi = extract_doi(normalized)
    url_match = URL_RE.search(normalized)
    if url_match:
        ref.url = url_match.group(0).rstrip(".,)")

    # The first sentence after the year is a useful conservative title boundary for APA-like lists.
    sentence_parts = re.split(r"\.\s+(?=[A-Z])", remainder, maxsplit=1)
    ref.title = _clean_token(sentence_parts[0])
    publication = sentence_parts[1] if len(sentence_parts) > 1 else ""
    volume_match = VOLUME_ISSUE_RE.search(publication)
    if volume_match:
        ref.container_title = _clean_token(publication[: volume_match.start()])
        ref.volume = volume_match.group("volume") or ""
        ref.issue = volume_match.group("issue") or ""
        ref.page = (volume_match.group("page") or "").replace("–", "-")
    else:
        ref.container_title = _clean_token(publication.split(".", 1)[0])

    evidence = [bool(ref.authors), bool(ref.issued_year), bool(ref.title), bool(ref.doi or ref.container_title)]
    ref.confidence = round(sum(evidence) / len(evidence), 2)
    if "arxiv" in normalized.lower():
        ref.type = "article"
    elif "proceedings" in normalized.lower() or "conference" in normalized.lower():
        ref.type = "paper-conference"
    elif ref.url and not ref.container_title:
        ref.type = "webpage"
    return ref


def parse_references(raw_references: Iterable[str]) -> list[Reference]:
    return [parse_reference(raw) for raw in raw_references if raw.strip()]
