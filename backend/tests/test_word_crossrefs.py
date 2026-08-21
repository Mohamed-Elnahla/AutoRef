from backend.app.services.word_crossrefs import detect_cross_references


def test_detects_caption_references_without_changing_label_variants():
    paragraphs = [
        "Figures 1 and 2 summarize the result; see Fig. 1 and table 2.",
        "Figure 1. First result.",
        "Table 2: Measurements.",
        "References",
    ]

    captions, candidates, warnings = detect_cross_references(paragraphs, 3)

    assert [(item.kind, item.number) for item in captions] == [
        ("figure", "1"),
        ("table", "2"),
    ]
    assert [item.text for item in candidates] == ["1", "1", "2"]
    assert all(paragraphs[item.paragraph_index][item.start : item.end] == item.text for item in candidates)
    assert warnings == []


def test_duplicate_caption_numbers_are_not_linked_ambiguously():
    captions, candidates, warnings = detect_cross_references(
        ["See Figure 1.", "Figure 1. First.", "Fig. 1. Duplicate."], None
    )

    assert len(captions) == 2
    assert candidates == []
    assert "Duplicate figure caption number '1'" in warnings[0]


def test_paragraph_starting_with_figure_mention_is_not_mistaken_for_caption():
    captions, candidates, _ = detect_cross_references(
        ["Figure 1 shows the workflow.", "Figure 1. Workflow."], None
    )

    assert len(captions) == 1
    assert len(candidates) == 1
    assert candidates[0].paragraph_index == 0


def test_detects_standalone_caption_labels_followed_by_titles():
    paragraphs = [
        "Figure 1",
        "System overview",
        "The topology is shown in Figure 1.",
        "Table 1",
        "Case corpus",
        "References",
    ]

    captions, candidates, warnings = detect_cross_references(paragraphs, 5)

    assert [(item.kind, item.number) for item in captions] == [
        ("figure", "1"),
        ("table", "1"),
    ]
    assert [item.text for item in candidates] == ["1"]
    assert candidates[0].paragraph_index == 2
    assert warnings == []
