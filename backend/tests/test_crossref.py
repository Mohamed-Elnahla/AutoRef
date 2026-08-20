import httpx
import pytest

from backend.app.models import Reference
from backend.app.services.crossref import (
    CrossrefClient,
    CrossrefVerificationError,
)


def test_verify_and_download_returns_crossref_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(b"/works/10.1000%2Ftest")
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.1000/TEST",
                    "type": "journal-article",
                    "title": ["Canonical title"],
                    "author": [{"given": "Jane", "family": "Smith"}],
                    "published-print": {"date-parts": [[2025, 3, 1]]},
                    "container-title": ["Journal of Tests"],
                    "volume": "8",
                    "issue": "2",
                    "page": "10-19",
                    "URL": "https://doi.org/10.1000/test",
                }
            },
        )

    client = CrossrefClient(transport=httpx.MockTransport(handler))
    result = client.verify_and_download(
        Reference(id="ref-1", raw="Unstructured", title="Parsed title", doi="10.1000/test")
    )
    client.close()

    assert result.title == "Canonical title"
    assert result.authors == [{"family": "Smith", "given": "Jane"}]
    assert result.issued_year == 2025
    assert result.container_title == "Journal of Tests"
    assert result.doi == "10.1000/test"
    assert result.parser == "crossref"
    assert result.confidence == 1.0


def test_missing_doi_fails_verification():
    client = CrossrefClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"status": "not-found"}))
    )
    with pytest.raises(CrossrefVerificationError, match="could not verify DOI"):
        client.verify_and_download(Reference(id="ref-1", raw="Bad", doi="10.1000/missing"))
    client.close()


def test_crossref_returning_a_different_doi_fails_verification():
    client = CrossrefClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"message": {"DOI": "10.1000/other"}})
        )
    )
    with pytest.raises(CrossrefVerificationError, match="different DOI"):
        client.verify_and_download(Reference(id="ref-1", raw="Wrong", doi="10.1000/requested"))
    client.close()
