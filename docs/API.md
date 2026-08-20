# HTTP API

## `GET /api/health`

Returns service status and version.

## `POST /api/v1/documents/analyze`

Multipart field: `file`, a DOCX up to `AUTOREF_MAX_UPLOAD_BYTES` (30 MB by default).

Returns `201` with a job ID, detected style, parsed references, citation candidates, warnings, and summary counts. Analysis does not alter the source.

## `POST /api/v1/documents/{job_id}/convert`

Re-analyzes the stored source, converts only candidates with an unambiguous reference match, and wraps the detected reference-list body in a refreshable Zotero bibliography field. Returns artifact URLs plus converted/skipped counts and bibliography conversion status.

This local endpoint does not call Zotero and its fields are not linked to library objects.

## `POST /api/v1/zotero/connections`

JSON body: `{ "api_key": "..." }`.

Validates the key through Zotero Web API v3 and returns an opaque, expiring `connection_id` plus writable personal/group libraries. The key is encrypted in server memory, never stored with a job, and never returned.

## `DELETE /api/v1/zotero/connections/{connection_id}`

Immediately removes an encrypted connection from the process-local vault. It is also removed automatically after the configured idle TTL.

## `POST /api/v1/documents/{job_id}/zotero/preview`

JSON body:

```json
{
  "connection_id": "opaque-id",
  "library_type": "user",
  "library_id": 123,
  "collection_name": "AutoRef imports"
}
```

Returns a `plan_id`, a create/reuse decision for every reference, and summary counts. Reuse requires an exact normalized DOI or title. No Zotero write occurs.

## `POST /api/v1/documents/{job_id}/zotero/import`

Accepts the preview body plus its `plan_id`. Options must exactly match the saved preview. After explicit confirmation, every new item with a DOI is verified through `GET /works/{doi}` at Crossref and enriched from the returned work metadata. If Crossref has no record, AutoRef checks `doi.org`; a resolvable DOI is imported automatically with the parsed document metadata. All checks finish before the first Zotero write. If a DOI remains unresolved, the first request returns `422` with every unresolved record and no writes. Resubmit with `unverified_doi_action` set to `use_parsed`, `mark_for_review`, or `exclude` to keep parsed data, add an AutoRef review note, or omit those records from the linked document and import.

On success, the endpoint creates/reuses Zotero items, generates a linked DOCX, and returns its import report. The `zotero_import.crossref.verified_dois` and `doi_resolved_dois` audit fields distinguish Crossref-enriched records from resolver-confirmed records. Partial Zotero batch failure triggers best-effort compensating rollback.

## `GET /api/v1/documents/{job_id}/artifacts/{name}`

`name` is one of `document`, `library`, or `report`. Paths are server-resolved through an allowlist; user-controlled filesystem paths are never accepted.

## Errors

- `413`: file exceeds configured upload size
- `415`: filename is not `.docx`
- `422`: invalid/incomplete DOCX package, or an unresolved-DOI review response (before any Zotero write)
- `404`: missing/expired job or artifact
- `409`: conversion cannot run because no reference list was detected
- `409`: Zotero import options no longer match the reviewed preview
- `401`: encrypted Zotero connection expired
- `403`: key lacks write access to the selected library
- `502`: Crossref service or a Zotero request could not be completed
