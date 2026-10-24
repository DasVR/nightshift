# Collector (PC side)

The `collector` package discovers Claude Code session `.jsonl` files on a PC,
builds a zip bundle plus manifest, and POSTs them to the nightshift ingest API.

## Install

From the repo root (same venv as the server):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

No extra runtime dependencies beyond the server package.

## CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--claude-dir` | `~/.claude/projects` | Root to scan recursively for session jsonl |
| `--since` | (none) | Keep sessions whose **file mtime** (UTC) is on/after `YYYY-MM-DD` |
| `--project` | (none, repeatable) | `fnmatch` glob against the project directory name |
| `--server` | `http://127.0.0.1:8791` | Ingest server base URL; overridden by `NIGHTSHIFT_SERVER_URL` unless this flag is set |
| `--timeout` | `60` | HTTP timeout in seconds |
| `--out` | (none) | Also write `<host>-<UTC timestamp>.zip` and `manifest.json` under this directory |
| `--dry-run` | off | Build bundle, print manifest and table; do not POST |
| `--verbose` | off | Per-file progress on stderr |

## Discovery rules

- Walk `--claude-dir` recursively with `pathlib`.
- Every `*.jsonl` file whose **stem is a UUID** is a session candidate.
- **Project name** = the immediate parent directory name (Claude’s encoded cwd dir, e.g. `C--Users-das-projects-foo`).
- Non-UUID `.jsonl` files and non-jsonl files are ignored.
- Zip entry names are always POSIX: `<project>/<session_id>.jsonl` (no `projects/` prefix, no backslashes, no absolute paths).

On Windows, point `--claude-dir` at `%USERPROFILE%\.claude\projects`.

## Manifest

Uploaded as the multipart field `manifest` (not inside the zip). Example:

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

Sessions in the manifest are sorted by `path`. See `docs/ingest.md` for server validation rules.

## Dry-run output

`--dry-run` prints pretty-printed manifest JSON, then a table:

```
Target: http://127.0.0.1:8791/sessions/ingest
project                        session_id                                  size  sha256 (prefix)
----------------------------------------------------------------------------------------------------
proj-a                         11111111-2222-3333-4444-555555555555        1234  abcd1234…
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (including zero sessions selected, or dry-run) |
| `1` | Connection error, timeout, or non-2xx HTTP response |

## Manual live check

Run on a machine with real Claude Code sessions and a running nightshift server:

```bash
# Terminal 1 — start the server
uvicorn server.main:app --reload --port 8791

# Terminal 2 — dry-run first
python -m collector --claude-dir ~/.claude/projects --since 2026-10-01 --dry-run

# POST for real
python -m collector --claude-dir ~/.claude/projects --since 2026-10-01 --server http://127.0.0.1:8791

# Verify indexed sessions
curl -s localhost:8791/sessions | jq .
```

Windows example:

```bat
python -m collector --claude-dir %USERPROFILE%\.claude\projects --dry-run
```
