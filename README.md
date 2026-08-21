# AutoRef

Developed by [Eng. Mohamed Elnahla](https://www.linkedin.com/in/mohamed-el-nahla). Source code and releases are available at [github.com/Mohamed-Elnahla/AutoRef](https://github.com/Mohamed-Elnahla/AutoRef).

AutoRef is a local-first web application that turns plain-text citations and the detected reference list in a Word research paper into native Zotero Word fields while preserving the surrounding DOCX package. Phase 2 can create or reuse items in a personal or group Zotero library, then write the returned item keys and canonical URIs into the document. A credential-free CSL-JSON workflow remains available.

This repository is a conservative working foundation, not a claim that arbitrary academic documents can be converted without review. AutoRef converts only unambiguous matches and leaves uncertain text untouched. Zotero writes require a separate, explicit preview and confirmation.

## What works

- DOCX upload, validation, analysis, and expiring job storage
- reference-section detection for common headings
- APA-like author-date citation matching, including narrative citations
- bracketed numeric citation matching, including ranges
- figure/table conversion to native Word `Caption` paragraphs using `SEQ Figure` / `SEQ Table` fields, with native `REF` fields for matching in-text mentions
- self-contained `ADDIN ZOTERO_ITEM CSL_CITATION` complex Word fields
- minimal OOXML patching: non-document package parts are copied byte-for-byte
- Zotero-importable CSL-JSON and a machine-readable conversion report
- short-lived, server-side encrypted Zotero API-key connections
- personal/group library selection and optional collection creation/reuse
- exact DOI/title deduplication with a review screen before any write; exact title matches missing a DOI are updated with the source DOI
- Crossref DOI verification and canonical metadata download before new Zotero items are written
- create/reuse audit data, canonical Zotero item linkage, and compensating rollback
- responsive React UI with light/dark themes
- MCP server for stdio or Streamable HTTP AI clients, covering the full backend workflow
- downloadable Codex plugin and `$autoref-docx` skill in `plugins/autoref`

## Figures and tables

AutoRef recognizes a figure or table caption when it is styled as a caption, punctuated as one (for example, `Figure 2. Title`), or split into a standalone label and following title (`Figure 2` followed by `Title`). Conversion applies Word's `Caption` style, replaces the typed number with a live `SEQ Figure` or `SEQ Table` field, and bookmarks that field result. Existing in-text mentions become clickable `REF` fields to the caption number.

Word owns future numbering. After adding, deleting, or moving a caption, use `Ctrl+A`, then `F9` in Word. Use **References → Insert Table of Figures** with the `Figure` or `Table` label to generate or update the List of Figures or List of Tables.

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

## Zotero-linked workflow

Create a dedicated Zotero API key with library write access at [Zotero’s key settings](https://www.zotero.org/settings/keys/new). After document analysis:

1. Paste the key and connect. The browser sends it once over the application connection; AutoRef encrypts it in server memory and never writes or returns it.
2. Select a writable personal or group library and optionally name a collection.
3. Review which references will be created, reused, or updated when an exact title match in Zotero is missing the source DOI.
4. Confirm the import. Before any Zotero write, AutoRef checks DOIs for new items with Crossref and uses returned canonical metadata. When Crossref has no record, it checks `doi.org` and keeps the document’s parsed metadata if the DOI resolves. Any remaining unresolved DOI is shown together for a choice to keep parsed data, add a manual-review note, or remove it from the linked output and import. It then writes in batches, attempts compensating deletion if a batch partially fails, and generates a linked DOCX using Zotero’s returned keys.

Connections expire after 30 minutes by default. Set `AUTOREF_CREDENTIAL_TTL_MINUTES` to change this. For stable encrypted credentials across application workers, set `AUTOREF_CREDENTIAL_KEY` to a Fernet key; otherwise a process-local key is generated on startup. `AUTOREF_ZOTERO_API_URL` defaults to `https://api.zotero.org`, and `AUTOREF_CROSSREF_API_URL` defaults to `https://api.crossref.org`. Set `AUTOREF_CROSSREF_MAILTO` to a contact email to use Crossref's polite pool.

Use HTTPS outside localhost. AutoRef never puts API keys in URLs or application logs. Confirming a linked import sends each DOI for a new item to Crossref and, on a Crossref miss, to `doi.org` for resolution; it then sends reference metadata to the selected Zotero library. Plain local conversion makes no third-party request.

## Outputs

Each successful conversion provides:

1. `*-zotero.docx` — the source document with matched citation spans represented as Zotero Word fields and figure/table mentions represented as Word cross-reference fields.
2. `*-library.csl.json` — parsed references importable with Zotero's File → Import → A file flow.
3. `*-conversion-report.json` — counts, warnings, metadata, and skipped candidates.

A confirmed Zotero import instead returns `*-zotero-linked.docx` and `*-zotero-import-report.json`. CSL-JSON is unnecessary in that path because the items already exist in the chosen library.

## Local conversion limitation

CSL-JSON import creates new item keys, so credential-free output cannot link its Word fields to the separately imported records. Those fields retain embedded metadata and remain editable, but initially appear as orphaned citations. Use the Zotero-linked workflow when stable library linkage matters.

Read [the architecture](docs/ARCHITECTURE.md), [research notes](docs/OPEN_SOURCE_RESEARCH.md), [decisions](docs/DECISIONS.md), and [roadmap](docs/ROADMAP.md) before extending the converter.

## License and acknowledgments

AutoRef is source-available for non-commercial use under the [AutoRef Community License 1.0](LICENSE). Commercial use is reserved for Eng. Mohamed Elnahla or requires his prior written approval. See [NOTICE.md](NOTICE.md) for direct open-source dependency acknowledgments.

## AI clients and Codex

Installing the package also installs `autoref-mcp`. Run it with no arguments for stdio, or use `autoref-mcp --transport streamable-http --port 8010` for a loopback HTTP endpoint. The one-call `convert_docx_to_zotero` tool produces the converted DOCX, CSL-JSON Zotero import file, and audit report; the remaining tools expose analysis, artifact reads, and the reviewed linked-Zotero workflow.

The repository-local Codex plugin lives at `plugins/autoref` and includes the `$autoref-docx` skill. See [the MCP and plugin guide](docs/MCP.md) for client configuration, installation, tool behavior, secret handling, and the explicit Zotero write boundary.
