# AutoRef MCP tool reference

## Document input

`analyze_document` and `convert_docx_to_zotero` accept exactly one of:

- `source_path`: an absolute or resolvable local `.docx` path;
- `document_base64`: base64-encoded DOCX bytes, with `filename` ending in `.docx`.

Uploads use the backend's `AUTOREF_MAX_UPLOAD_BYTES` limit. Jobs and artifacts expire according to `AUTOREF_JOB_TTL_HOURS`.

## Tools

- `health`: server status, output formats, and supported citation/cross-reference features.
- `analyze_document`: validates and inventories the paper, returning `job_id`, references, citation candidates, figure/table captions, matching in-text cross-references, warnings, and summary.
- `convert_document`: converts an existing analyzed job without external calls, including native Word `REF` fields for unambiguous figure/table matches.
- `convert_docx_to_zotero`: convenience tool combining analysis and local conversion, including figure/table cross-references.
- `read_artifact`: returns metadata and, by default, base64 bytes for `document`, `library`, or `report`.
- `connect_zotero`: validates a key and returns an opaque expiring connection and writable libraries.
- `disconnect_zotero`: removes the encrypted in-memory connection.
- `preview_zotero_import`: exact DOI/title create-or-reuse plan; it performs no write.
- `import_to_zotero`: verifies new-item DOIs with Crossref, performs confirmed Zotero writes, and generates a linked DOCX. It requires the preview's exact options and `confirm=true`.

## Artifacts

Credential-free conversion returns:

- `document`: `*-zotero.docx`, containing Zotero fields plus figure/table Word `REF` fields;
- `library`: `*-library.csl.json`, importable through Zotero's File -> Import flow;
- `report`: `*-conversion-report.json`.

The conversion report includes `detected_captions`, `converted_cross_references`, and `skipped_cross_references`. Analysis summary includes `captions` and `cross_references`.

Confirmed library import returns:

- `document`: `*-zotero-linked.docx` using canonical Zotero item keys and URIs;
- `report`: `*-zotero-import-report.json` with create/reuse and Crossref audit data.

## Boundaries

The converter currently supports DOCX body paragraphs, common reference-list headings, APA-like author-date citations including narrative forms, bracketed numeric citations including ranges, and figure/table captions using labels such as `Figure`, `Fig.`, and `Table`. Caption paragraphs are recognized conservatively by caption style or caption punctuation. Matching singular/plural in-text labels are supported, and only their number spans become Word `REF` fields so authored wording and character formatting remain intact.

Duplicate caption numbers of the same type are ambiguous and remain unchanged with a warning. Cross-references nested in unsupported complex Word markup are also left unchanged. Local CSL-JSON import cannot retroactively relink embedded bibliographic citation fields to newly created Zotero items; figure/table links are local Word fields and do not use Zotero.
