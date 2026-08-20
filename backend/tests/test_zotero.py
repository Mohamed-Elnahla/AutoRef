import json

import httpx
import pytest

from backend.app.models import Reference
from backend.app.services.zotero import Library, ZoteroClient, ZoteroError


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


def test_execute_creates_item_and_returns_canonical_uri():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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


def test_partial_failure_attempts_compensating_delete():
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
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
    assert methods == ["POST", "DELETE"]
