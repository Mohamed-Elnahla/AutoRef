from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence

from backend.app.models import Caption, CrossReferenceCandidate

NUMBER_PATTERN = r"(?:[A-Za-z]?\d+(?:[.\-\u2013]\d+)*[A-Za-z]?|[IVXLCDM]+)"
CAPTION_RE = re.compile(
    rf"^\s*(?P<label>fig(?:ure)?\.?|table)\s*(?P<number>{NUMBER_PATTERN})"
    r"(?=\s|[.:\-\u2013\u2014]|$)",
    re.IGNORECASE,
)
REFERENCE_RE = re.compile(
    rf"\b(?P<label>fig(?:ure)?s?\.?|tables?)\s+(?P<number>{NUMBER_PATTERN})\b",
    re.IGNORECASE,
)


def _kind(label: str) -> str:
    return "table" if label.casefold().startswith("table") else "figure"


def _normalized_number(number: str) -> str:
    return number.casefold().replace("\u2013", "-")


def _caption_id(kind: str, number: str, paragraph_index: int) -> str:
    digest = hashlib.sha1(f"{kind}:{number}:{paragraph_index}".encode()).hexdigest()[:12]
    return f"caption-{digest}"


def detect_cross_references(
    paragraphs: Sequence[str],
    reference_start: int | None,
    paragraph_styles: Sequence[str] | None = None,
    standalone_caption_contexts: Sequence[bool] | None = None,
) -> tuple[list[Caption], list[CrossReferenceCandidate], list[str]]:
    """Find figure/table captions and body references to their numbers.

    Only the number is selected for conversion. This lets Word update the REF field while
    leaving the author's exact label spelling (for example, ``Fig.``, ``Figure``, or a
    plural form) untouched.
    """
    body_end = reference_start if reference_start is not None else len(paragraphs)
    captions: list[Caption] = []
    by_key: dict[tuple[str, str], list[Caption]] = defaultdict(list)

    for paragraph_index, text in enumerate(paragraphs[:body_end]):
        match = CAPTION_RE.match(text)
        if not match:
            continue
        trailing = text[match.end() : match.end() + 1]
        style = paragraph_styles[paragraph_index].casefold() if paragraph_styles else ""
        # Some authoring workflows split a caption into a bold label paragraph
        # ("Figure 1") followed by a title paragraph. A label-only paragraph is
        # unambiguous enough to accept, while ordinary prose mentions remain
        # excluded because they have text after the number.
        is_standalone_label = not text[match.end() :].strip() and (
            standalone_caption_contexts is None or standalone_caption_contexts[paragraph_index]
        )
        if (
            "caption" not in style
            and trailing not in {".", ":", "-", "\u2013", "\u2014"}
            and not is_standalone_label
        ):
            continue
        kind = _kind(match.group("label"))
        number = match.group("number")
        caption = Caption(
            id=_caption_id(kind, _normalized_number(number), paragraph_index),
            paragraph_index=paragraph_index,
            start=match.start("number"),
            end=match.end("number"),
            text=match.group(0).strip(),
            kind=kind,  # type: ignore[arg-type]
            number=number,
        )
        captions.append(caption)
        by_key[(kind, _normalized_number(number))].append(caption)

    caption_paragraphs = {caption.paragraph_index for caption in captions}
    candidates: list[CrossReferenceCandidate] = []
    warnings: list[str] = []
    ambiguous_keys: set[tuple[str, str]] = set()
    for paragraph_index, text in enumerate(paragraphs[:body_end]):
        if paragraph_index in caption_paragraphs:
            continue
        for match in REFERENCE_RE.finditer(text):
            kind = _kind(match.group("label"))
            number = match.group("number")
            key = (kind, _normalized_number(number))
            matches = by_key.get(key, [])
            if len(matches) == 1:
                candidates.append(
                    CrossReferenceCandidate(
                        paragraph_index=paragraph_index,
                        start=match.start("number"),
                        end=match.end("number"),
                        text=number,
                        caption_id=matches[0].id,
                    )
                )
            elif len(matches) > 1:
                ambiguous_keys.add(key)

    for kind, number in sorted(ambiguous_keys):
        warnings.append(
            f"Duplicate {kind} caption number {number!r}; its in-text references were left unchanged."
        )
    return captions, candidates, warnings
