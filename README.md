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

## Layout

- `server/` — FastAPI app, settings, and the module-level `app` instance.
- `collector/` — future PC-side collector that will pack and POST session jsonl (slice 4).
- `tests/` — real tests for config and the `/health` endpoint.
- `docs/PLAN.md` — the approved slice plan and ground rules.
- `docs/ingest.md` — ingest endpoint, bundle format, manifest schema, and idempotency rules.

## Status

v0.1 is a scaffold plus slice 1. Slices 2–5 (headless resume runner, done-notify, PC collector +
dashboard, and Cursor path) are tracked in `docs/PLAN.md` and are not yet implemented.

Slice 1 (CC session ingest) is live: `POST /sessions/ingest` accepts a zip or bare jsonl bundle with an
optional manifest, parses it into a sqlite session store at `data/nightshift.db`, and serves
`GET /sessions` + `GET /sessions/{id}`. See `docs/ingest.md`.
