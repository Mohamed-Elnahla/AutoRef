# HTTP API

## `GET /api/health`

Returns service status and version.

## `POST /api/v1/documents/analyze`

Multipart field: `file`, a DOCX up to `AUTOREF_MAX_UPLOAD_BYTES` (30 MB by default).

Returns `201` with a job ID, detected style, parsed references, citation candidates, warnings, and summary counts. Analysis does not alter the source.

## `POST /api/v1/documents/{job_id}/convert`

Re-analyzes the stored source and converts only candidates with an unambiguous reference match. Returns artifact URLs plus converted/skipped counts.

## `GET /api/v1/documents/{job_id}/artifacts/{name}`

`name` is one of `document`, `library`, or `report`. Paths are server-resolved through an allowlist; user-controlled filesystem paths are never accepted.

## Errors

- `413`: file exceeds configured upload size
- `415`: filename is not `.docx`
- `422`: invalid or incomplete DOCX package
- `404`: missing/expired job or artifact
- `409`: conversion cannot run because no reference list was detected

