# Architecture decision records

## ADR-001: Patch OOXML instead of rebuilding DOCX

**Status:** accepted

High-level Word libraries tend to reserialize styles, relationships, section settings, and runs. AutoRef opens the DOCX as a ZIP and modifies only `word/document.xml`. This is the most defensible route to layout preservation and allows package-part hash comparisons.

## ADR-002: Use native complex fields with embedded item data

**Status:** accepted for phase one

The output uses `ADDIN ZOTERO_ITEM CSL_CITATION` Word fields with a cached result identical to the source citation. Embedded CSL metadata lets Zotero treat the citations as editable orphaned fields without requiring credentials. The limitation is prominently disclosed.

## ADR-003: Export CSL-JSON

**Status:** accepted

Zotero imports CSL-JSON directly, and the same model is embedded in citation fields. One canonical shape reduces format mapping errors. RIS/BibTeX can be added through Citation.js or Zotero Translation Server if users request them.

## ADR-004: Conservative deterministic baseline, pluggable trained parser

**Status:** accepted

The application starts with an explainable APA-like parser and confidence flags. GROBID is the preferred service adapter. The converter will not depend on a parser-specific representation.

## ADR-005: Preserve the reference list as the cached result of a dynamic bibliography

**Status:** accepted

AutoRef wraps the detected reference-list paragraphs in Zotero's standard `ZOTERO_BIBL` field and keeps the original formatted text as its cached result. This makes the list refreshable without changing its initial appearance. Zotero applies the user's document style and preferences when the field is refreshed.

## ADR-006: Single application, split source trees

**Status:** accepted

React/TypeScript owns the interaction layer; FastAPI owns file processing. Production serves the compiled frontend from the backend, simplifying private/local deployment without collapsing the code boundaries.

## ADR-007: Files expire and external enrichment is opt-in

**Status:** accepted

Research papers and bibliographies can be sensitive. The default system makes no external calls, uses opaque jobs, and deletes local job folders after a configurable TTL. GROBID can be self-hosted. Crossref or Zotero calls require explicit configuration and user action.

## ADR-008: Preview-gated scoped API-key integration

**Status:** accepted for phase two

AutoRef accepts user-created Zotero keys rather than silently persisting account credentials. It discovers only writable libraries, encrypts keys in a short-lived process-memory vault, and requires a create/reuse preview before writes. OAuth remains appropriate for a hosted multi-user deployment but is not required for the local-first baseline.

## ADR-009: Exact deduplication and non-destructive reuse

**Status:** accepted

DOI matches take priority, followed by normalized exact title matches. Existing items are reused without mutation. AutoRef does not fuzzy-merge bibliographic data because a false merge is harder to detect and repair than a duplicate.

## ADR-010: Compensating rollback for Zotero writes

**Status:** accepted

Zotero multi-object writes can partially succeed. AutoRef records returned keys, uses version-guarded delete requests for newly created objects after a partial failure, and never deletes reused items. This is a compensating transaction, not an atomic guarantee: concurrent remote edits can correctly prevent rollback and require manual recovery.
