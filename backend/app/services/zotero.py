from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from backend.app.config import settings
from backend.app.models import Reference
from backend.app.services.crossref import (
    CrossrefClient,
    CrossrefError,
)


class ZoteroError(RuntimeError):
    pass


class ZoteroImportReviewRequired(ZoteroError):
    """Raised before any write when the user must choose how to handle DOI failures."""

    def __init__(self, unresolved: list[dict[str, str]]) -> None:
        self.unresolved = unresolved
        super().__init__(f"{len(unresolved)} DOI(s) could not be verified. Review the import choices.")


@dataclass(frozen=True)
class Library:
    type: Literal["user", "group"]
    id: int
    name: str

    @property
    def prefix(self) -> str:
        return f"/{self.type}s/{self.id}"

    def item_uri(self, key: str) -> str:
        return f"http://zotero.org/{self.type}s/{self.id}/items/{key}"


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalized_doi(value: str) -> str:
    return re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.IGNORECASE
    ).casefold()


def _extract_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("key") or value.get("data", {}).get("key") or "")
    return ""


class ZoteroClient:
    _MAX_RATE_LIMIT_RETRIES = 3

    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=settings.zotero_api_url,
            headers={
                "Zotero-API-Key": api_key,
                "Zotero-API-Version": "3",
                "User-Agent": "AutoRef/0.2",
            },
            timeout=30,
            transport=transport,
        )
        self._backoff_until = 0.0

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(self._MAX_RATE_LIMIT_RETRIES + 1):
            remaining_backoff = self._backoff_until - time.monotonic()
            if remaining_backoff > 0:
                time.sleep(remaining_backoff)
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise ZoteroError("Zotero could not be reached. No library changes were made.") from exc

            backoff = self._header_seconds(response, "Backoff")
            if backoff:
                self._backoff_until = max(self._backoff_until, time.monotonic() + backoff)
            if response.status_code == 429 and attempt < self._MAX_RATE_LIMIT_RETRIES:
                retry_after = self._header_seconds(response, "Retry-After")
                delay = retry_after if retry_after is not None else 2**attempt
                self._backoff_until = max(self._backoff_until, time.monotonic() + delay)
                continue
            if response.status_code >= 400:
                message = "Zotero rejected the request"
                if response.status_code in {401, 403}:
                    message = "The Zotero key is invalid or lacks the required library permission"
                elif response.status_code == 429:
                    message = "Zotero rate-limited the request after retrying; try again later"
                raise ZoteroError(f"{message} ({response.status_code}).")
            return response
        raise AssertionError("Unreachable rate-limit retry loop")

    @staticmethod
    def _header_seconds(response: httpx.Response, name: str) -> float | None:
        try:
            return max(0.0, float(response.headers[name]))
        except (KeyError, ValueError):
            return None

    def libraries(self) -> tuple[dict[str, Any], list[Library]]:
        access = self._request("GET", "/keys/current").json()
        user_id = int(access["userID"])
        libraries: list[Library] = []
        user_access = access.get("access", {}).get("user", {})
        if user_access.get("library") and user_access.get("write"):
            libraries.append(Library("user", user_id, access.get("username", "My Library")))
        groups_access = access.get("access", {}).get("groups", {})
        if groups_access:
            groups = self._request("GET", f"/users/{user_id}/groups").json()
            for group in groups:
                group_id = int(group.get("id") or group.get("data", {}).get("id"))
                permission = groups_access.get(str(group_id), groups_access.get("all", {}))
                if permission.get("library") and permission.get("write"):
                    data = group.get("data", group)
                    libraries.append(Library("group", group_id, data.get("name", f"Group {group_id}")))
        return access, libraries

    def _candidates(self, library: Library, reference: Reference) -> list[dict[str, Any]]:
        query = reference.doi or reference.title
        if not query:
            return []
        response = self._request(
            "GET",
            f"{library.prefix}/items/top",
            params={"q": query, "qmode": "everything", "limit": 25, "format": "json"},
        )
        return [item.get("data", item) for item in response.json()]

    def plan(self, library: Library, references: list[Reference]) -> dict[str, Any]:
        entries = []
        for reference in references:
            match = None
            reason = None
            for item in self._candidates(library, reference):
                if reference.doi and _normalized_doi(item.get("DOI", "")) == _normalized_doi(
                    reference.doi
                ):
                    match, reason = item, "doi"
                    break
                if reference.title and _normalized(item.get("title", "")) == _normalized(reference.title):
                    match, reason = item, "title"
                    break
            entries.append(
                {
                    "reference_id": reference.id,
                    "title": reference.title or reference.raw,
                    "action": "reuse" if match else "create",
                    "reason": reason,
                    "item_key": match.get("key") if match else None,
                }
            )
        digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        return {"plan_id": digest, "entries": entries}

    def _item_payload(
        self, reference: Reference, collection_key: str | None, review_note: str = ""
    ) -> dict[str, Any]:
        type_map = {
            "article-journal": "journalArticle",
            "article": "journalArticle",
            "book": "book",
            "chapter": "bookSection",
            "paper-conference": "conferencePaper",
            "thesis": "thesis",
            "report": "report",
            "webpage": "webpage",
        }
        item_type = type_map.get(reference.type, "document")
        payload: dict[str, Any] = {
            "itemType": item_type,
            "title": reference.title,
            "creators": [
                (
                    {"creatorType": "author", "name": author["literal"]}
                    if author.get("literal")
                    else {
                        "creatorType": "author",
                        "firstName": author.get("given", ""),
                        "lastName": author.get("family", ""),
                    }
                )
                for author in reference.authors
            ],
            "date": str(reference.issued_year or ""),
            "url": reference.url,
            "extra": "\n".join(
                part for part in (f"AutoRef source: {reference.raw}", review_note) if part
            ),
            "collections": [collection_key] if collection_key else [],
        }
        if item_type == "journalArticle":
            payload.update(
                {
                    "publicationTitle": reference.container_title,
                    "volume": reference.volume,
                    "issue": reference.issue,
                    "pages": reference.page,
                    "DOI": reference.doi,
                }
            )
        elif item_type == "bookSection":
            payload.update({"bookTitle": reference.container_title, "pages": reference.page})
        elif item_type == "conferencePaper":
            payload.update({"proceedingsTitle": reference.container_title, "pages": reference.page})
        elif item_type == "webpage":
            payload["websiteTitle"] = reference.container_title
        return {key: value for key, value in payload.items() if value not in ("", [], None)}

    def _collection(self, library: Library, name: str | None) -> tuple[str | None, bool, int | None]:
        if not name:
            return None, False, None
        collections = self._request(
            "GET", f"{library.prefix}/collections", params={"limit": 100, "format": "json"}
        ).json()
        for wrapped in collections:
            data = wrapped.get("data", wrapped)
            if data.get("name", "").casefold() == name.casefold():
                return data.get("key"), False, data.get("version")
        response = self._request(
            "POST",
            f"{library.prefix}/collections",
            headers={"Zotero-Write-Token": uuid.uuid4().hex},
            json=[{"name": name}],
        )
        body = response.json()
        successful = body.get("successful", body.get("success", {}))
        value = successful.get("0")
        key = _extract_key(value)
        if not key:
            raise ZoteroError("Zotero did not return a key for the new collection.")
        version = int(response.headers.get("Last-Modified-Version", "0")) or None
        return key, True, version

    def execute(
        self,
        library: Library,
        references: list[Reference],
        plan: dict[str, Any],
        collection_name: str | None,
        crossref: CrossrefClient | None = None,
        unverified_doi_action: Literal["review", "use_parsed", "mark_for_review", "exclude"] = "review",
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        by_id = {reference.id: reference for reference in references}
        creates = [entry for entry in plan["entries"] if entry["action"] == "create"]
        verified_dois: list[str] = []
        resolver_verified_dois: list[str] = []
        unresolved: list[dict[str, str]] = []
        review_notes: dict[str, str] = {}
        if crossref:
            # Resolve every DOI before the first Zotero write, so failed verification cannot
            # leave behind a collection or a partially imported library.
            for entry in creates:
                reference = by_id[entry["reference_id"]]
                if reference.doi:
                    try:
                        reference = crossref.verify_and_download(reference)
                    except CrossrefError as exc:
                        # Some valid DOI agencies (including DataCite/arXiv) are
                        # not indexed by Crossref. If doi.org resolves it, retain
                        # the document's parsed metadata and import normally.
                        if crossref.doi_resolves(reference.doi):
                            resolver_verified_dois.append(reference.doi)
                            continue
                        unresolved.append(
                            {
                                "reference_id": reference.id,
                                "title": reference.title or reference.raw,
                                "doi": reference.doi,
                                "reason": str(exc),
                            }
                        )
                        continue
                    by_id[entry["reference_id"]] = reference
                    verified_dois.append(reference.doi)
        if unresolved and unverified_doi_action == "review":
            raise ZoteroImportReviewRequired(unresolved)
        unresolved_ids = {item["reference_id"] for item in unresolved}
        if unverified_doi_action == "mark_for_review":
            review_notes = {
                item["reference_id"]: (
                    "AutoRef review required: DOI could not be verified by Crossref. "
                    f"Reason: {item['reason']}"
                )
                for item in unresolved
            }
        if unverified_doi_action == "exclude":
            creates = [entry for entry in creates if entry["reference_id"] not in unresolved_ids]
        if creates:
            collection_key, collection_created, collection_version = self._collection(
                library, collection_name
            )
        else:
            collection_key, collection_created, collection_version = None, False, None
        links: dict[str, dict[str, str]] = {}
        created_keys: list[str] = []
        created_version: int | None = None
        for entry in plan["entries"]:
            if entry["action"] == "reuse":
                key = entry["item_key"]
                links[entry["reference_id"]] = {"key": key, "uri": library.item_uri(key)}
        try:
            for offset in range(0, len(creates), 50):
                batch = creates[offset : offset + 50]
                response = self._request(
                    "POST",
                    f"{library.prefix}/items",
                    headers={"Zotero-Write-Token": uuid.uuid4().hex},
                    json=[
                        self._item_payload(
                            by_id[entry["reference_id"]],
                            collection_key,
                            review_notes.get(entry["reference_id"], ""),
                        )
                        for entry in batch
                    ],
                )
                body = response.json()
                successful = body.get("successful", body.get("success", {}))
                failed = body.get("failed", {})
                for index, entry in enumerate(batch):
                    key = _extract_key(successful.get(str(index)))
                    if key:
                        created_keys.append(key)
                        links[entry["reference_id"]] = {"key": key, "uri": library.item_uri(key)}
                created_version = int(response.headers.get("Last-Modified-Version", "0")) or created_version
                if failed or len(successful) != len(batch):
                    raise ZoteroError(f"Zotero failed to create {len(failed) or 1} item(s).")
        except Exception as exc:
            rollback = self._rollback(
                library,
                created_keys,
                created_version,
                collection_key if collection_created else None,
                collection_version,
            )
            if isinstance(exc, ZoteroError):
                raise ZoteroError(f"{exc} Compensating rollback: {rollback}.") from exc
            raise
        audit = {
            "library": {"type": library.type, "id": library.id, "name": library.name},
            "collection": {"name": collection_name, "key": collection_key, "created": collection_created},
            "created": created_keys,
            "reused": [entry["item_key"] for entry in plan["entries"] if entry["action"] == "reuse"],
            "crossref": {
                "verified_dois": verified_dois,
                "doi_resolved_dois": resolver_verified_dois,
                "unresolved": unresolved,
                "unverified_doi_action": unverified_doi_action,
                "metadata_source": "Crossref",
            },
            "rollback": "not_needed",
        }
        return links, audit

    def _rollback(self, library, keys, version, collection_key, collection_version) -> str:
        try:
            if keys and version:
                self._request(
                    "DELETE",
                    f"{library.prefix}/items",
                    params={"itemKey": ",".join(keys)},
                    headers={"If-Unmodified-Since-Version": str(version)},
                )
            if collection_key and collection_version:
                self._request(
                    "DELETE",
                    f"{library.prefix}/collections/{collection_key}",
                    headers={"If-Unmodified-Since-Version": str(collection_version)},
                )
            return "completed"
        except ZoteroError:
            return "incomplete; manual review may be required"
