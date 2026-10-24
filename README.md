# nightshift

A FastAPI session handoff server: receive coding-session bundles from a PC collector, then resume them headless on the server.

## Quickstart

```bash
uv venv && source .venv/bin/activate
# or: python3 -m venv .venv && source .venv/bin/activate

uv pip install -e ".[dev]"
# or: pip install -e ".[dev]"

cp .env.example .env
pytest
uvicorn server.main:app --reload
curl -s localhost:8788/health
```

See `docs/done-notify.md` for how notifications are configured and tested.

## Layout

- `server/` — FastAPI app, settings, and the module-level `app` instance.
- `collector/` — future PC-side collector that will pack and POST session jsonl (slice 4).
- `tests/` — real tests for config and the `/health` endpoint.
- `docs/PLAN.md` — the approved slice plan and ground rules.

## Resume runner

Nightshift can resume an existing Claude Code session headlessly on the server.

### Endpoints

- `POST /runs` — enqueue a resume run. Body: `{"session_id": "<uuid>", "instruction": "<prompt>", "cwd": "</optional/override>"}`. Returns `201` with the queued `RunRecord`.
- `GET /runs/{run_id}` — fetch a run record.
- `GET /runs` — list runs newest first. Optional `?status=queued|running|done|failed`.

Validation: malformed `session_id` → `400`; unknown session id → `404`; blank instruction → `400`.

### What runs under the hood

For a session id found at `~/.claude/projects/*/<session_id>.jsonl`, the worker launches:

```bash
tmux new-session -d -s nsrun-<runid> -c <cwd> \
  /bin/sh -c 'claude --resume <session_id> -p "<instruction>" --output-format json --permission-mode acceptEdits > data/runs/<run_id>.log 2>&1; echo $? > data/runs/<run_id>.exit'
```

The session's **recorded cwd** is used automatically; pass `cwd` in the request body to override it. One run executes at a time; additional runs stay queued until the worker reaches them.

### Permission modes

Set `NIGHTSHIFT_CLAUDE_PERMISSION_MODE` to one of:

- `acceptEdits` (default) — file edits allowed, Bash still denied.
- `bypassPermissions` — allows everything.
- `auto`, `manual`, `dontAsk`, `plan` — see `claude` CLI docs.

### Manual live-check recipe

```bash
# 1. start the server (do not use during pytest)
uvicorn server.main:app --reload

# 2. enqueue a run against a session you own
curl -s -X POST localhost:8788/runs \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "<uuid>", "instruction": "summarize the current task"}'

# 3. watch it in tmux
tmux ls
tmux attach -t nsrun-<runid>

# 4. inspect the log
cat data/runs/<run_id>.log
```

See `docs/RESUME-NOTES.md` for the full empirical matrix that determined these behaviours.

## Status

v0.1 scaffold is live. Slice 2 (headless resume runner) is implemented.
Slices 1, 3–5 (session ingest, done-notify, PC collector + dashboard, Cursor path) are tracked in `docs/PLAN.md`.
