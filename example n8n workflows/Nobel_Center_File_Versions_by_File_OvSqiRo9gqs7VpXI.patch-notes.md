# Nobel Center File Versions by File (`OvSqiRo9gqs7VpXI`)

## Weird filename support (2026-05-29)

SharePoint names like `M1-570-MM-Complete model.ifc` must keep spaces in `name`
while MinIO keys use `safe_upload_basename` (spaces → underscores).

### API (`/download-from-url`)

- Accepts original `output_filename` with spaces / Unicode
- Stores under `uploads/<storage_basename>` via `shared.object_storage.safe_upload_basename`
- Audit metadata keeps `original_filename`
- Cache lookup falls back to `source_etag` when object key sanitization changed

### Workflow code node `Pick Latest Two SP Versions`

Replace the `sanitisedName = name.split(' ').join('-')` block with the
`safeUploadBasename()` helper (mirrors Python) and emit `storage_basename`.

Ensure Prev/New nodes keep:

```text
outputFilename = {{ $('Pick Latest Two SP Versions').item.json.name }}
```

The gateway sanitizes; do **not** pass raw names as S3 keys in expressions.

### Emit Result

Prefer paths from the download node:

```text
file_path = {{ $('Ensure New in MinIO').item.json.file_path || $('Pick Latest Two SP Versions').item.json.file_path }}
file_key  = {{ $('Ensure New in MinIO').item.json.object_key || $('Pick Latest Two SP Versions').item.json.file_key }}
```
