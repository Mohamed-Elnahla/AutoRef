from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class Reference:
    id: str
    raw: str
    type: str = "article-journal"
    title: str = ""
    authors: list[dict[str, str]] = field(default_factory=list)
    issued_year: int | None = None
    container_title: str = ""
    volume: str = ""
    issue: str = ""
    page: str = ""
    doi: str = ""
    url: str = ""
    parser: str = "heuristic"
    confidence: float = 0.0

    @property
    def first_author_family(self) -> str:
        return self.authors[0].get("family", "") if self.authors else ""

    def to_csl(self) -> dict[str, Any]:
        item: dict[str, Any] = {"id": self.id, "type": self.type or "document"}
        optional = {
            "title": self.title,
            "author": self.authors,
            "container-title": self.container_title,
            "volume": self.volume,
            "issue": self.issue,
            "page": self.page,
            "DOI": self.doi,
            "URL": self.url,
        }
        item.update({key: value for key, value in optional.items() if value})
        if self.issued_year:
            item["issued"] = {"date-parts": [[self.issued_year]]}
        item["note"] = f"AutoRef source: {self.raw}"
        return item

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CitationItem:
    reference_id: str
    locator: str = ""
    label: str = "page"
    prefix: str = ""
    suffix: str = ""
    suppress_author: bool = False


@dataclass
class CitationCandidate:
    paragraph_index: int
    start: int
    end: int
    text: str
    kind: Literal["author-date", "numeric"]
    items: list[CitationItem]
    confidence: float
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["matched_reference_ids"] = [item.reference_id for item in self.items]
        return payload


@dataclass
class Caption:
    id: str
    paragraph_index: int
    start: int
    end: int
    text: str
    kind: Literal["figure", "table"]
    number: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CrossReferenceCandidate:
    paragraph_index: int
    start: int
    end: int
    text: str
    caption_id: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Analysis:
    source_name: str
    detected_style: Literal["author-date", "numeric", "unknown"]
    reference_heading_index: int | None
    references: list[Reference]
    citations: list[CitationCandidate]
    captions: list[Caption] = field(default_factory=list)
    cross_references: list[CrossReferenceCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        total = len(self.citations)
        return 1.0 if total == 0 else sum(bool(c.items) for c in self.citations) / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "detected_style": self.detected_style,
            "reference_heading_index": self.reference_heading_index,
            "references": [item.to_dict() for item in self.references],
            "citations": [item.to_dict() for item in self.citations],
            "captions": [item.to_dict() for item in self.captions],
            "cross_references": [item.to_dict() for item in self.cross_references],
            "warnings": self.warnings,
            "summary": {
                "references": len(self.references),
                "citations": len(self.citations),
                "matched_citations": sum(bool(item.items) for item in self.citations),
                "match_rate": round(self.match_rate, 4),
                "captions": len(self.captions),
                "cross_references": len(self.cross_references),
            },
        }
