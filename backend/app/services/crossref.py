from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx

from backend.app.config import settings
from backend.app.models import Reference
from backend.app.services.reference_parser import extract_doi


class CrossrefError(RuntimeError):
    pass


class CrossrefVerificationError(CrossrefError):
    pass


def normalize_doi(value: str) -> str:
    return re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    ).casefold()


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value and value[0] else ""
    return str(value) if value else ""


def _year(message: dict[str, Any]) -> int | None:
    for field in ("published-print", "published-online", "published", "issued", "created"):
        value = message.get(field) or {}
        parts = value.get("date-parts", []) if isinstance(value, dict) else []
        if parts and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _authors(message: dict[str, Any]) -> list[dict[str, str]]:
    authors: list[dict[str, str]] = []
    for author in message.get("author") or []:
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        name = str(author.get("name") or "").strip()
        if family or given:
            authors.append({"family": family, "given": given})
        elif name:
            authors.append({"literal": name})
    return authors


def _url(message: dict[str, Any]) -> str:
    resource_data = message.get("resource") or {}
    primary = resource_data.get("primary") or {}
    resource = primary.get("URL")
    return str(message.get("URL") or resource or "")


class CrossrefClient:
    """Verify a DOI and convert the returned Crossref work into an AutoRef reference."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        user_agent = "AutoRef/0.2"
        if settings.crossref_mailto:
            user_agent += f" (mailto:{settings.crossref_mailto})"
        self._client = httpx.Client(
            base_url=settings.crossref_api_url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            params={"mailto": settings.crossref_mailto} if settings.crossref_mailto else None,
            timeout=30,
            transport=transport,
        )
        self._resolver = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=20,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()
        self._resolver.close()

    def doi_resolves(self, doi: str) -> bool:
        """Return whether doi.org can resolve a DOI outside Crossref's registry.

        Resolution intentionally does not replace parsed metadata: a successful
        resolver lookup only establishes that the identifier is live, while
        Crossref remains the source of canonical metadata when it has a record.
        """
        normalized = normalize_doi(doi)
        if not normalized:
            return False
        try:
            response = self._resolver.get(f"https://doi.org/{quote(normalized, safe='/')}")
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 400

    def verify_and_download(self, reference: Reference) -> Reference:
        # Retry extraction from the original reference before declaring a DOI
        # unresolved. This handles DOI labels/URLs and trailing bibliography
        # punctuation that can survive an earlier parse.
        requested_doi = normalize_doi(reference.doi or extract_doi(reference.raw))
        if not requested_doi:
            return reference
        try:
            response = self._client.get(f"/works/{quote(requested_doi, safe='')}")
        except httpx.HTTPError as exc:
            raise CrossrefError(
                f"Crossref could not be reached while verifying DOI {reference.doi}."
            ) from exc
        if response.status_code == 404:
            raise CrossrefVerificationError(f"Crossref could not verify DOI {reference.doi}.")
        if response.status_code == 429:
            raise CrossrefError("Crossref rate-limited DOI verification; try again later.")
        if response.status_code >= 400:
            raise CrossrefError(
                f"Crossref rejected DOI verification for {reference.doi} ({response.status_code})."
            )
        try:
            message = response.json()["message"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CrossrefError(
                f"Crossref returned invalid metadata for DOI {reference.doi}."
            ) from exc
        if not isinstance(message, dict):
            raise CrossrefError(f"Crossref returned invalid metadata for DOI {reference.doi}.")

        canonical_doi = normalize_doi(str(message.get("DOI") or ""))
        if not canonical_doi or canonical_doi != requested_doi:
            raise CrossrefVerificationError(
                f"Crossref returned a different DOI while verifying {reference.doi}."
            )

        type_map = {
            "journal-article": "article-journal",
            "book": "book",
            "book-chapter": "chapter",
            "proceedings-article": "paper-conference",
            "dissertation": "thesis",
            "report": "report",
            "posted-content": "webpage",
        }
        return replace(
            reference,
            type=type_map.get(str(message.get("type") or ""), reference.type),
            title=_first(message.get("title")) or reference.title,
            authors=_authors(message) or reference.authors,
            issued_year=_year(message) or reference.issued_year,
            container_title=_first(message.get("container-title")) or reference.container_title,
            volume=str(message.get("volume") or reference.volume),
            issue=str(message.get("issue") or reference.issue),
            page=str(message.get("page") or reference.page),
            doi=canonical_doi,
            url=_url(message) or reference.url,
            parser="crossref",
            confidence=1.0,
        )
