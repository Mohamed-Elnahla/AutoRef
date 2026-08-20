from backend.app.services.citation_detector import detect_citations
from backend.app.services.reference_parser import parse_references


def test_parenthetical_and_narrative_detection():
    refs = parse_references(
        [
            "Smith, J. (2024). First paper. Journal, 1(1), 1-2.",
            "Jones, A. (2023). Second paper. Journal, 2(1), 3-4.",
        ]
    )
    text = "Smith (2024) found this, as later confirmed (Jones, 2023)."
    found = detect_citations([text, "References"], refs, 1)
    assert len(found) == 2
    assert found[0].text == "(2024)"
    assert found[0].items[0].suppress_author is True
    assert found[1].text == "(Jones, 2023)"


def test_numeric_ranges_map_to_reference_order():
    refs = parse_references(
        [
            "Smith, J. (2024). First paper. Journal, 1, 1-2.",
            "Jones, A. (2023). Second paper. Journal, 2, 3-4.",
            "Lee, B. (2022). Third paper. Journal, 3, 5-6.",
        ]
    )
    found = detect_citations(["Prior work [1, 2-3].", "References"], refs, 1)
    assert len(found) == 1
    assert len(found[0].items) == 3


def test_disambiguates_same_author_year_with_second_author_hint():
    refs = parse_references(
        [
            "Gao, T., Yen, H., & Chen, D. (2023). First paper. Journal, 1, 1-2.",
            "Gao, Y., Xiong, Y., & Wang, H. (2023). Second paper. Journal, 2, 3-4.",
        ]
    )
    found = detect_citations(["Prior work (Gao, Xiong, et al., 2023).", "References"], refs, 1)
    assert len(found) == 1
    assert found[0].items[0].reference_id == refs[1].id
