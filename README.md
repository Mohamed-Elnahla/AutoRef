# AutoRef

AutoRef is a local-first web application that turns plain-text citations in a Word research paper into native Zotero Word fields while preserving the surrounding DOCX package. It also exports the parsed bibliography as CSL-JSON for Zotero import and produces a conversion audit report.

This repository is a working phase-one foundation, not a claim that arbitrary academic documents can be converted without review. AutoRef converts only unambiguous matches and leaves uncertain text untouched.

## What works

- DOCX upload, validation, analysis, and expiring job storage
- reference-section detection for common headings
- APA-like author-date citation matching, including narrative citations
- bracketed numeric citation matching, including ranges
- self-contained `ADDIN ZOTERO_ITEM CSL_CITATION` complex Word fields
- minimal OOXML patching: non-document package parts are copied byte-for-byte
- Zotero-importable CSL-JSON and a machine-readable conversion report
- responsive React UI with light/dark themes

## Quick start

Requirements: Python 3.11+, Node 20+, and pnpm.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cd frontend && pnpm install && pnpm build && cd ..
uvicorn backend.app.main:app --reload
```

Open `http://localhost:8000`. For split frontend development, run `pnpm dev` in `frontend`; Vite proxies `/api` to port 8000.

## Test

```bash
pytest
ruff check backend
cd frontend && pnpm build
```

## Outputs

Each successful conversion provides:

1. `*-zotero.docx` — the source document with matched citation spans represented as Zotero Word fields.
2. `*-library.csl.json` — parsed references importable with Zotero's File → Import → A file flow.
3. `*-conversion-report.json` — counts, warnings, metadata, and skipped candidates.

## Phase-one limitation

Zotero import creates new item keys. It does not preserve links from an existing word-processor document, so the generated fields contain embedded item metadata and initially appear to Zotero as orphaned citations. They are still native, refreshable fields, but the separately imported CSL-JSON records are not the same linked library objects. Phase two solves this by creating items through the Zotero API first, then writing their actual library URIs into the fields.

Read [the architecture](docs/ARCHITECTURE.md), [research notes](docs/OPEN_SOURCE_RESEARCH.md), [decisions](docs/DECISIONS.md), and [roadmap](docs/ROADMAP.md) before extending the converter.

