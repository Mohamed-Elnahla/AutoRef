# AutoRef MCP tool reference

## Document input

`analyze_document` and `convert_docx_to_zotero` accept exactly one of:

- `source_path`: an absolute or resolvable local `.docx` path;
- `document_base64`: base64-encoded DOCX bytes, with `filename` ending in `.docx`.

Uploads use the backend's `AUTOREF_MAX_UPLOAD_BYTES` limit. Jobs and artifacts expire according to `AUTOREF_JOB_TTL_HOURS`.

## Tools

- `health`: server status and supported output formats.
- `analyze_document`: validates and inventories the paper, returning `job_id`, references, citation candidates, warnings, and summary.
- `convert_document`: converts an existing analyzed job without external calls.
- `convert_docx_to_zotero`: convenience tool combining analysis and local conversion.
- `read_artifact`: returns metadata and, by default, base64 bytes for `document`, `library`, or `report`.
- `connect_zotero`: validates a key and returns an opaque expiring connection and writable libraries.
- `disconnect_zotero`: removes the encrypted in-memory connection.
- `preview_zotero_import`: exact DOI/title create-or-reuse plan; it performs no write.
- `import_to_zotero`: verifies new-item DOIs with Crossref, performs confirmed Zotero writes, and generates a linked DOCX. It requires the preview's exact options and `confirm=true`.

## Artifacts

Credential-free conversion returns:

- `document`: `*-zotero.docx`;
- `library`: `*-library.csl.json`, importable through Zotero's File -> Import flow;
- `report`: `*-conversion-report.json`.

Confirmed library import returns:

- `document`: `*-zotero-linked.docx` using canonical Zotero item keys and URIs;
- `report`: `*-zotero-import-report.json` with create/reuse and Crossref audit data.

## Boundaries

The converter currently supports DOCX body paragraphs, common reference-list headings, APA-like author-date citations including narrative forms, and bracketed numeric citations including ranges. It does not blindly rewrite ambiguous citations or citations nested in unsupported complex Word markup. Local CSL-JSON import cannot retroactively relink embedded citation fields to the newly created Zotero items.
