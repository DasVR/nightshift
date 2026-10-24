# Ingest reference

## Endpoints

| Method | Path | Params / Body |
|--------|------|---------------|
| `POST` | `/sessions/ingest` | `bundle` (file, required): zip or bare `.jsonl`; `manifest` (file, optional): JSON manifest |
| `GET` | `/sessions` | `project`, `limit` (1–500), `offset` |
| `GET` | `/sessions/{session_id}` | — |
| `GET` | `/health` | — |

## Bundle formats

- **zip**: entries are `<project-dir>/<session-uuid>.jsonl`. A leading `projects/` segment is stripped; deeper nesting is allowed and the immediate parent of the `.jsonl` becomes the project. Non-`.jsonl` entries are ignored as sessions but may be listed in the manifest.
- **bare jsonl**: one session, project becomes `_unknown`, session id is taken from the file's first `sessionId` line.
- Zip-slip / traversal entries (absolute paths, `..`, backslash-absolute) are rejected with 400 before anything is written.

## Manifest schema

```json
{
  "collector_version": "0.1.0",
  "host": "desktop-abc",
  "sessions": [
    {
      "path": "proj-a/11111111-2222-3333-4444-555555555555.jsonl",
      "session_id": "11111111-2222-3333-4444-555555555555",
      "project": "proj-a",
      "sha256": "…",
      "size": 1234
    }
  ]
}
```

All fields are optional. `sessions[]` may also list non-session paths (e.g. `proj/notes.txt`); those are existence-checked but not parsed.

422 rules:
- manifest is not valid JSON, or `sessions` is not a list.
- a listed `path` is not present in the bundle.
- a listed `sha256` does not match the entry's bytes.
- a listed `session_id` disagrees with a UUID filename stem.

## Parse rules

Each line is inspected independently. Blank, non-JSON, or non-object lines increment `skipped_lines` and are reported in warnings.

- `type` in `{"user", "assistant"}` and not `isSidechain` counts as a turn.
- `isSidechain` lines count toward `sidechain_lines` and their `message.content` tool_use blocks count toward `tool_use_count`.
- Timestamps are parsed with `datetime.fromisoformat`, normalized to UTC, and emitted as `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`.
- `cwd`: the last non-empty value seen wins.
- Files with zero valid turns are ingested with `status: "empty"`.

## Storage

- SQLite: `data_dir/nightshift.db` (WAL mode).
- Raw jsonl: `data_dir/sessions/<safe_project>/<session_id>.jsonl`.
- `stored_path` in API responses is relative to `data_dir`.

Why sqlite instead of a json index? Atomic upserts on the primary key give idempotency, indexed reads are cheap, the stdlib dependency keeps the project light, and later slices need transactional job tables. A JSON index would require whole-file rewrites and manual locking.

## Idempotency

| Existing row? | Same sha? | Result |
|---------------|-----------|--------|
| no | — | `created` |
| yes | yes | `unchanged` (no DB/file write, `created_at` preserved) |
| yes | no | `updated` (row + file overwritten, `created_at` kept) |

## Curl examples

```bash
# Ingest a zip with its manifest
curl -s -F bundle=@session-bundle.zip -F manifest=@manifest.json \
  localhost:8788/sessions/ingest | jq .

# List sessions for a project
curl -s 'localhost:8788/sessions?project=proj-a&limit=10' | jq .
```

## Slice 2 input

The headless resume runner uses `stored_path` + `project` + `cwd` to restart the matching session.
