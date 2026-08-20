import json

import httpx
import pytest

from backend.app.models import Reference
from backend.app.services.crossref import CrossrefClient
from backend.app.services.zotero import (
    Library,
    ZoteroClient,
    ZoteroError,
    ZoteroImportReviewRequired,
)


def _reference() -> Reference:
    return Reference(
        id="ref-1",
        raw="Smith, J. (2024). First paper.",
        title="First paper",
        authors=[{"family": "Smith", "given": "J."}],
        issued_year=2024,
        doi="10.1000/test",
    )


def test_plan_reuses_exact_doi_match():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/7/items/top"
        return httpx.Response(
            200,
            json=[{"data": {"key": "ABCD2345", "DOI": "https://doi.org/10.1000/test", "title": "Other"}}],
        )

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    plan = client.plan(Library("user", 7, "Mine"), [_reference()])
    client.close()
    assert plan["entries"][0]["action"] == "reuse"
    assert plan["entries"][0]["item_key"] == "ABCD2345"


def test_plan_updates_exact_title_match_when_zotero_item_has_no_doi():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"data": {"key": "MISSINGDOI", "title": "First paper", "DOI": ""}}],
        )

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    plan = client.plan(Library("user", 7, "Mine"), [_reference()])
    client.close()

    assert plan["entries"][0]["action"] == "update"
    assert plan["entries"][0]["reason"] == "title"
    assert plan["entries"][0]["item_key"] == "MISSINGDOI"


def test_execute_adds_doi_to_existing_title_match_only_when_still_missing():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/items/MISSINGDOI"):
            return httpx.Response(
                200,
                json={"data": {"key": "MISSINGDOI", "version": 10, "DOI": "", "collections": []}},
            )
        if request.method == "PATCH" and request.url.path.endswith("/items/MISSINGDOI"):
            assert request.headers["If-Unmodified-Since-Version"] == "10"
            assert json.loads(request.content) == {"DOI": "10.1000/test"}
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    reference = _reference()
    links, audit = client.execute(
        Library("user", 7, "Mine"),
        [reference],
        {"entries": [{"reference_id": reference.id, "action": "update", "item_key": "MISSINGDOI"}]},
        None,
    )
    client.close()

    assert links[reference.id]["key"] == "MISSINGDOI"
    assert audit["updated"] == ["MISSINGDOI"]
    assert [request.method for request in requests] == ["GET", "PATCH"]


def test_plan_deduplicates_repeated_document_dois_before_writing():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[])

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    duplicate = Reference(id="ref-2", raw="Same paper", title="Another title", doi="DOI: 10.1000/TEST")
    plan = client.plan(Library("user", 7, "Mine"), [_reference(), duplicate])
    client.close()

    # The first DOI has no general-search result, so the exact DOI fallback
    # checks the library once before the second document reference reuses it.
    assert calls == 2
    assert [entry["action"] for entry in plan["entries"]] == ["create", "reuse"]
    assert plan["entries"][1]["duplicate_of"] == "ref-1"


def test_plan_reuses_doi_found_outside_zotero_text_search_results():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "q" in request.url.params:
            # Zotero's relevance-ranked text query missed the matching record.
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[{"data": {"key": "EXACTDOI", "DOI": "doi:10.1000/TEST"}}],
        )

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    plan = client.plan(Library("user", 7, "Mine"), [_reference()])
    client.close()

    assert plan["entries"][0]["action"] == "reuse"
    assert plan["entries"][0]["reason"] == "doi"
    assert plan["entries"][0]["item_key"] == "EXACTDOI"
    assert len(calls) == 2


def test_plan_retries_rate_limited_request_after_retry_after(monkeypatch):
    calls = 0
    waits: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=[])

    monkeypatch.setattr("backend.app.services.zotero.time.sleep", waits.append)
    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    plan = client.plan(Library("user", 7, "Mine"), [_reference()])
    client.close()

    assert plan["entries"][0]["action"] == "create"
    # One retry for the text lookup, then one exact DOI fallback lookup.
    assert calls == 3
    assert waits and waits[0] > 0


def test_backoff_header_delays_next_request(monkeypatch):
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("q") == "10.1000/test":
            return httpx.Response(200, headers={"Backoff": "2"}, json=[])
        return httpx.Response(200, json=[])

    monkeypatch.setattr("backend.app.services.zotero.time.sleep", waits.append)
    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    client.plan(
        Library("user", 7, "Mine"),
        [_reference(), Reference(id="ref-2", raw="Second", title="Second")],
    )
    client.close()

    assert waits and waits[0] > 0


def test_execute_creates_item_and_returns_canonical_uri():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/items/top"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/items") and request.method == "POST":
            payload = json.loads(request.content)
            assert payload[0]["itemType"] == "journalArticle"
            return httpx.Response(
                200,
                headers={"Last-Modified-Version": "12"},
                json={"successful": {"0": {"key": "WXYZ6789"}}, "failed": {}},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    reference = _reference()
    plan = {
        "entries": [
            {"reference_id": reference.id, "action": "create", "item_key": None}
        ]
    }
    links, audit = client.execute(Library("user", 7, "Mine"), [reference], plan, None)
    client.close()
    assert links[reference.id] == {
        "key": "WXYZ6789",
        "uri": "http://zotero.org/users/7/items/WXYZ6789",
    }
    assert audit["created"] == ["WXYZ6789"]


def test_execute_reuses_doi_created_after_the_preview():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/items/top")
        return httpx.Response(200, json=[{"data": {"key": "JUSTADDED", "DOI": "10.1000/test"}}])

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    reference = _reference()
    links, audit = client.execute(
        Library("user", 7, "Mine"), [reference],
        {"entries": [{"reference_id": reference.id, "action": "create", "item_key": None}]}, None,
    )
    client.close()

    assert links[reference.id]["key"] == "JUSTADDED"
    assert audit["created"] == []
    assert audit["reused"] == ["JUSTADDED"]


def test_execute_creates_collection_and_adds_reused_item_to_it():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/collections"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path.endswith("/collections"):
            return httpx.Response(
                200,
                headers={"Last-Modified-Version": "9"},
                json={"successful": {"0": {"key": "COLLECT1"}}},
            )
        if request.method == "GET" and request.url.path.endswith("/items/EXISTING"):
            return httpx.Response(
                200,
                json={"data": {"key": "EXISTING", "version": 10, "collections": ["OTHER"]}},
            )
        if request.method == "PATCH" and request.url.path.endswith("/items/EXISTING"):
            assert request.headers["If-Unmodified-Since-Version"] == "10"
            assert json.loads(request.content) == {"collections": ["OTHER", "COLLECT1"]}
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    reference = _reference()
    plan = {"entries": [{"reference_id": reference.id, "action": "reuse", "item_key": "EXISTING"}]}
    links, audit = client.execute(Library("user", 7, "Mine"), [reference], plan, "  AutoRef imports  ")
    client.close()

    assert links[reference.id]["key"] == "EXISTING"
    assert audit["collection"] == {"name": "  AutoRef imports  ", "key": "COLLECT1", "created": True}
    assert [request.method for request in requests] == ["GET", "POST", "GET", "PATCH"]


def test_execute_does_not_patch_reused_item_already_in_collection():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/collections"):
            return httpx.Response(200, json=[{"data": {"name": "Imports", "key": "COLLECT1"}}])
        if request.method == "GET" and request.url.path.endswith("/items/EXISTING"):
            return httpx.Response(
                200,
                json={"data": {"key": "EXISTING", "version": 10, "collections": ["COLLECT1"]}},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    reference = _reference()
    plan = {"entries": [{"reference_id": reference.id, "action": "reuse", "item_key": "EXISTING"}]}
    client.execute(Library("user", 7, "Mine"), [reference], plan, "Imports")
    client.close()


def test_collection_reuse_searches_past_first_page_without_creating_duplicate():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        start = int(request.url.params.get("start", "0"))
        if start == 0:
            return httpx.Response(200, json=[{"data": {"name": f"Collection {i}", "key": str(i)}} for i in range(100)])
        return httpx.Response(200, json=[{"data": {"name": "AutoRef imports", "key": "COLLECT1", "version": 4}}])

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    key, created, version = client._collection(Library("user", 7, "Mine"), "autoref imports")
    client.close()

    assert (key, created, version) == ("COLLECT1", False, 4)
    assert [request.url.params["start"] for request in calls] == ["0", "100"]


def test_execute_verifies_and_downloads_crossref_metadata_before_zotero_write():
    order: list[str] = []

    def crossref_handler(_: httpx.Request) -> httpx.Response:
        order.append("crossref")
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.1000/test",
                    "type": "journal-article",
                    "title": ["Title downloaded from Crossref"],
                    "author": [{"given": "Jane", "family": "Smith"}],
                    "published": {"date-parts": [[2025]]},
                    "container-title": ["Verified Journal"],
                }
            },
        )

    def zotero_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            order.append("zotero-check")
            return httpx.Response(200, json=[])
        order.append("zotero")
        payload = json.loads(request.content)
        assert payload[0]["title"] == "Title downloaded from Crossref"
        assert payload[0]["publicationTitle"] == "Verified Journal"
        assert payload[0]["date"] == "2025"
        return httpx.Response(200, json={"successful": {"0": {"key": "NEWITEM1"}}})

    crossref = CrossrefClient(transport=httpx.MockTransport(crossref_handler))
    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(zotero_handler))
    reference = _reference()
    plan = {"entries": [{"reference_id": reference.id, "action": "create", "item_key": None}]}
    _, audit = client.execute(Library("user", 7, "Mine"), [reference], plan, None, crossref)
    client.close()
    crossref.close()

    assert order == ["zotero-check", "crossref", "zotero"]
    assert audit["crossref"]["verified_dois"] == ["10.1000/test"]


def test_crossref_failure_happens_before_any_zotero_write():
    zotero_requests: list[httpx.Request] = []
    crossref = CrossrefClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404, json={}))
    )
    client = ZoteroClient(
        "not-a-real-key",
        transport=httpx.MockTransport(
            lambda request: zotero_requests.append(request) or httpx.Response(200, json=[])
        ),
    )
    reference = _reference()
    plan = {"entries": [{"reference_id": reference.id, "action": "create", "item_key": None}]}

    with pytest.raises(ZoteroImportReviewRequired, match="could not be verified"):
        client.execute(Library("user", 7, "Mine"), [reference], plan, "New collection", crossref)
    client.close()
    crossref.close()
    assert [request.method for request in zotero_requests] == ["GET"]


def test_doi_org_resolution_keeps_parsed_metadata_when_crossref_has_no_record():
    requests: list[httpx.Request] = []

    def crossref_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(200)
        return httpx.Response(404)

    def zotero_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        payload = json.loads(request.content)
        assert payload[0]["title"] == "First paper"
        assert payload[0]["DOI"] == "10.1000/test"
        return httpx.Response(200, json={"successful": {"0": {"key": "NEWITEM1"}}})

    crossref = CrossrefClient(transport=httpx.MockTransport(crossref_handler))
    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(zotero_handler))
    reference = _reference()
    plan = {"entries": [{"reference_id": reference.id, "action": "create", "item_key": None}]}
    _, audit = client.execute(Library("user", 7, "Mine"), [reference], plan, None, crossref)
    client.close()
    crossref.close()

    assert [request.method for request in requests] == ["GET", "POST"]
    assert audit["crossref"]["verified_dois"] == []
    assert audit["crossref"]["doi_resolved_dois"] == ["10.1000/test"]


def test_partial_failure_attempts_compensating_delete():
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"Last-Modified-Version": "12"},
                json={
                    "successful": {"0": {"key": "WXYZ6789"}},
                    "failed": {"1": {"code": 400, "message": "bad item"}},
                },
            )
        if request.method == "DELETE":
            assert request.url.params["itemKey"] == "WXYZ6789"
            assert request.headers["If-Unmodified-Since-Version"] == "12"
            return httpx.Response(204)
        raise AssertionError(request.method)

    client = ZoteroClient("not-a-real-key", transport=httpx.MockTransport(handler))
    references = [_reference(), Reference(id="ref-2", raw="Second", title="Second")]
    plan = {
        "entries": [
            {"reference_id": reference.id, "action": "create", "item_key": None}
            for reference in references
        ]
    }
    with pytest.raises(ZoteroError, match="rollback: completed"):
        client.execute(Library("user", 7, "Mine"), references, plan, None)
    client.close()
    assert methods == ["GET", "POST", "DELETE"]
