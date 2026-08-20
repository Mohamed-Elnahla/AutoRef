# Architecture

## Goals

AutoRef accepts a DOCX paper, discovers its bibliography and citation callouts, produces structured reference data, inserts native Zotero fields into safely matched callouts, and returns downloadable artifacts without reflowing or reconstructing the paper.

The design optimizes for fidelity, explainability, and conservative failure. A false negative stays visible as static text. A false positive silently corrupts scholarly attribution, so ambiguous candidates are not converted.

## System context

```text
Browser (React + TypeScript)
        |
        | multipart upload / JSON / artifact download
        v
FastAPI application
  +-- upload validation and expiring job store
  +-- DOCX OOXML reader and patcher
  +-- citation detector and reference matcher
  +-- reference parser adapter
  +-- CSL-JSON exporter and audit report
        |
        +-- Phase 1: built-in deterministic parser
        +-- Optional next adapter: self-hosted GROBID
        +-- Phase 2: Zotero Web API + OAuth/API key
```

## Backend modules

- `main.py`: HTTP boundary, size checks, job lifecycle, and artifact delivery.
- `job_store.py`: opaque IDs, path confinement, 24-hour default retention, and cleanup.
- `docx_processor.py`: DOCX validation, paragraph inventory, reference boundaries, and complex Word-field insertion.
- `reference_parser.py`: deterministic low-dependency baseline that converts APA-like reference strings to CSL-shaped records.
- `citation_detector.py`: author-year and numeric recognition plus bibliography matching.
- `models.py`: internal evidence and output contracts.

## Conversion pipeline

1. Reject non-DOCX, oversized, malformed, or incomplete ZIP packages.
2. Read `word/document.xml` without loading macros, relationships, or external content.
3. Inventory paragraph text and locate a reference-list heading.
4. Stop the reference list at a later appendix/annex/declaration heading.
5. Parse each reference into an internal record with the original string retained.
6. Build author-year and ordinal indexes.
7. detect citation spans before the reference section and classify each as matched, ambiguous, or unmatched.
8. Patch only matched character spans, in reverse order inside each paragraph.
9. Copy every other DOCX ZIP part using its original `ZipInfo` metadata and payload.
10. Emit the converted DOCX, CSL-JSON library, and audit report.

## Word field representation

Each citation uses a complex OOXML field:

```xml
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN ZOTERO_ITEM CSL_CITATION {...} </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>(Author, 2024)</w:t></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

The JSON stores the original visible citation in `properties`, each matched item in `citationItems`, embedded CSL `itemData`, and the CSL citation schema URL. The visible result is intentionally the exact source text. Zotero can later regenerate it when the user refreshes or edits the citation.

Narrative citations keep the author text static and convert only the year parentheses with `suppress-author: true`. This follows the behavior Zotero can reproduce across author-date styles.

## Fidelity contract

“Same exact format” is implemented as a constrained invariant, not by re-creating the document with a high-level Word library:

- every ZIP part except `word/document.xml` is copied byte-for-byte;
- paragraph and run properties outside matched spans are unchanged;
- the cached field result equals the original citation text;
- the display run inherits the first matched run's properties;
- unresolved, ambiguous, nested, or structurally complex spans are skipped;
- visual regression compares source and output page renders.

OOXML serialization can change namespace declaration ordering inside `document.xml`, so the output is semantically and visually equivalent rather than byte-identical as a whole file.

## Known phase-one boundaries

- The baseline reference parser is strongest on APA-like references; MLA, Chicago notes, legal citations, and multilingual/no-space scripts need trained adapters and fixtures.
- Citations nested in hyperlinks, content controls, equations, text boxes, tracked changes, or existing fields are not blindly rewritten.
- Footnote/endnote citation conversion is a separate document-part traversal and is not enabled yet.
- Static bibliographies remain static. Replacing them with a `ZOTERO_BIBL` field is deferred until citation style and document preferences can be reproduced and verified.
- CSL-JSON import does not relink word-processor citations to newly created Zotero items.

## Security and privacy

- Files remain on the configured server and are not sent to third-party services by default.
- Jobs use server-generated opaque IDs and fixed artifact names resolved through allowlists.
- ZIP structure and required parts are validated before processing.
- Upload size defaults to 30 MB; jobs expire after 24 hours.
- External metadata enrichment must be explicit because DOIs, titles, and authors can disclose research interests.
- Production should add malware scanning, ZIP expansion limits, authentication, rate limits, encrypted storage, and a durable cleanup worker.

## Deployment shape

Phase one is intentionally a single deployable web application: Vite produces static assets that FastAPI serves alongside `/api`. The job store is local disk, suitable for one instance. Multi-instance deployment requires object storage, a shared job database, idempotent workers, and a queue.

