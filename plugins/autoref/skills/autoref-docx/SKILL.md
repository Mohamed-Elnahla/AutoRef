---
name: autoref-docx
description: Analyze research DOCX files, convert plain-text citations into native Zotero Word fields, export Zotero-importable CSL-JSON, or create fully linked Zotero citations after a reviewed import. Use for requests to make an ordinary Word paper Zotero-aware, audit citation matching, or prepare a Zotero library import. Do not use for non-DOCX files or general Word editing.
---

# AutoRef DOCX

Use the AutoRef MCP tools to preserve the source document while converting only unambiguous citation matches. Keep unresolved or structurally complex text unchanged and report the warnings.

## Local conversion

For a local file, pass its absolute path to `analyze_document`. For a remote client, pass base64 and a `.docx` filename instead. Show the user the detected style, reference/citation counts, match rate, and warnings.

Use `convert_document` with the returned job ID, or use `convert_docx_to_zotero` for a one-call workflow. Deliver all three artifacts:

- the converted Zotero-field DOCX;
- the CSL-JSON Zotero import file;
- the JSON conversion report.

Prefer each artifact's `local_path` when the client can access it. Otherwise call `read_artifact` and decode `data_base64`. Explain that local conversion embeds metadata but cannot bind fields to item keys created by a later CSL-JSON import.

## Fully linked Zotero conversion

Use this mode only when the user asks to write references into a Zotero library.

1. Analyze the document.
2. Call `connect_zotero`. Prefer `AUTOREF_ZOTERO_API_KEY`; do not ask the user to paste a key into chat when an environment-backed connection is possible.
3. Call `preview_zotero_import` for the chosen writable library and optional collection.
4. Present every create/reuse decision and the summary. Stop for explicit confirmation.
5. Only after confirmation, call `import_to_zotero` with the unchanged options, returned `plan_id`, and `confirm=true`.
6. Deliver the linked DOCX and import report, then disconnect unless the user wants to continue within the short connection TTL.

Never treat a request to analyze, preview, or locally convert as authorization to write to Zotero. Crossref verification and Zotero writes happen only during the confirmed import.

For tool parameters, outputs, and limitations, read [references/mcp-tools.md](references/mcp-tools.md).
