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

## ADR-005: Do not generate a dynamic bibliography in phase one

**Status:** accepted

Replacing a formatted reference list with `ZOTERO_BIBL` requires reliable style identification, Zotero document preferences, disambiguation state, and layout verification. Keeping the list untouched satisfies content preservation and avoids duplicate/changed bibliography output.

## ADR-006: Single application, split source trees

**Status:** accepted

React/TypeScript owns the interaction layer; FastAPI owns file processing. Production serves the compiled frontend from the backend, simplifying private/local deployment without collapsing the code boundaries.

## ADR-007: Files expire and external enrichment is opt-in

**Status:** accepted

Research papers and bibliographies can be sensitive. The default system makes no external calls, uses opaque jobs, and deletes local job folders after a configurable TTL. GROBID can be self-hosted. Crossref or Zotero calls require explicit configuration and user action.

