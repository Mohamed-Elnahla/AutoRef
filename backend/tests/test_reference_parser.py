from backend.app.services.reference_parser import extract_doi, parse_reference


def test_parse_apa_reference():
    ref = parse_reference(
        "Bragadin, M. A., & Kähkönen, K. (2016). Schedule health assessment of "
        "construction projects. Construction Management and Economics, 34(12), 875-897. "
        "https://doi.org/10.1080/01446193.2016.1205751"
    )
    assert ref.issued_year == 2016
    assert ref.authors[0]["family"] == "Bragadin"
    assert ref.title == "Schedule health assessment of construction projects"
    assert ref.volume == "34"
    assert ref.issue == "12"
    assert ref.doi == "10.1080/01446193.2016.1205751"


def test_reference_id_is_stable():
    raw = "Example, A. (2024). A title. A Journal, 1, 1-2."
    assert parse_reference(raw).id == parse_reference(raw).id


def test_parse_reference_recovers_doi_label_and_trailing_bibliography_punctuation():
    ref = parse_reference(
        "Author, A. (2024). Preprint. doi: https://doi.org/10.48550/arXiv.2312.10997)."
    )

    assert ref.doi == "10.48550/arXiv.2312.10997"


def test_extract_doi_recovers_percent_encoded_doi_org_url_without_tracking_query():
    assert extract_doi("https://doi.org/10.1016%2Fj.buildenv.2024.111111?utm_source=reference") == (
        "10.1016/j.buildenv.2024.111111"
    )


def test_extract_doi_recovers_percent_encoded_doi_in_a_publisher_url_query():
    assert extract_doi("https://example.test/article?doi=10.1000%252Fencoded-suffix") == "10.1000/encoded-suffix"
