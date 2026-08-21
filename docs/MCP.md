# MCP server and Codex plugin

AutoRef includes an MCP server for AI clients and a repository-local Codex plugin. The MCP surface mirrors the backend's analysis, local conversion—including figure/table caption detection and native Word cross-reference creation—artifact retrieval, Zotero connection, import preview, confirmed import, and disconnect operations.

## Install and run

Install the Python package from the repository so the `autoref-mcp` executable is available:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
autoref-mcp
```

The default transport is stdio. A generic MCP client configuration is:

```json
{
  "mcpServers": {
    "autoref": {
      "command": "autoref-mcp",
      "args": []
    }
  }
}
```

For a local Streamable HTTP endpoint:

```bash
autoref-mcp --transport streamable-http --host 127.0.0.1 --port 8010
```

Connect clients to `http://127.0.0.1:8010/mcp`. The command intentionally defaults to loopback. A public HTTP deployment needs authentication, TLS, an explicit host allowlist, rate limiting, and shared job/credential storage before it is safe to expose.

The Docker equivalent is `docker compose --profile mcp up autoref-mcp`, which publishes the same endpoint on port 8010 with a dedicated expiring-job volume.

## Tools

- `health`
- `analyze_document`
- `convert_document`
- `convert_docx_to_zotero`
- `read_artifact`
- `connect_zotero`
- `disconnect_zotero`
- `preview_zotero_import`
- `import_to_zotero`

Local clients can pass `source_path`; remote clients can send `document_base64` and a filename. Generated artifacts include safe local paths, and `read_artifact` can return base64 for clients without shared filesystem access.

`analyze_document` returns detected `captions` and `cross_references` alongside bibliographic results. `convert_document` and `convert_docx_to_zotero` turn detected captions into Word `Caption` paragraphs with live `SEQ Figure` or `SEQ Table` fields, bookmark each caption number, and replace unambiguous in-text number spans with native clickable `REF` fields. Word's **Insert Table of Figures** then recognizes the converted captions for independent lists of figures and tables. The report exposes detected captions, converted captions, converted cross-references, and skipped candidates. Figure/table links are fully local; credential-free Zotero fields embed metadata but are not linked to keys created by importing the CSL-JSON later.

For stable Zotero library linkage, connect, preview, show the exact create/reuse plan to the user, and call `import_to_zotero` only after explicit confirmation. The tool requires `confirm=true`, the preview's `plan_id`, and identical library and collection options. New items with DOIs are verified with Crossref before the first Zotero write.

Prefer setting `AUTOREF_ZOTERO_API_KEY` in the MCP process environment instead of passing a key through an AI conversation. The key is encrypted in process memory and represented to tools by an expiring opaque connection ID.

## Codex plugin

The distributable plugin is in `plugins/autoref`, and the repository marketplace is `.agents/plugins/marketplace.json`. After installing AutoRef's Python package, run these commands with the downloaded repository's absolute path:

```bash
codex plugin marketplace add /absolute/path/to/AutoRef
codex plugin add autoref@personal
```

Start a new Codex task after installation so it discovers the MCP tools and `$autoref-docx` skill. If `personal` is already the name of another configured marketplace, rename the top-level marketplace `name` before adding the repository and use that new name after `@`.

The plugin's MCP launcher expects `autoref-mcp` on `PATH`. Its environment allowlist passes the documented `AUTOREF_*` settings, including `AUTOREF_ZOTERO_API_KEY` when configured outside Codex.
