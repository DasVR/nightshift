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

## Status

v0.1 is a scaffold only. Slices 1–5 (session ingest, headless resume runner, done-notify, PC collector + dashboard, and Cursor path) are tracked in `docs/PLAN.md` and are not yet implemented.
