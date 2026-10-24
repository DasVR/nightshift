# Control panel (resume desk)

Open the Nightshift server in a browser. `GET /` is a control panel: pick an
ingested session, press **Resume**, and send follow-up instructions on the same
session after the first run finishes.

```bash
# keep this process off 8788 so the editor bridge can bind there
NIGHTSHIFT_PORT=8791 uvicorn server.main:app --reload --port 8791
# then: http://127.0.0.1:8791/
```

## What the Resume button does

1. `POST /runs` with the selected `session_id` and the instruction box.
2. Before `claude --resume … -p` starts, Nightshift probes the editor bridge
   (`GET http://127.0.0.1:8788/health`). If it is down, it runs
   `hackaboom headless start --workspace <session cwd>`.
3. If `~/hackaboom/plugin` (or `NIGHTSHIFT_HACKABOOM_PLUGIN_DIR`) exists, the
   resume argv includes `--plugin-dir`, so file hooks fire.
4. The worker still runs one job at a time. A second Resume on the same session
   queues a follow-up turn.

A failed editor start does **not** fail the run. The panel shows `down` and the
session still executes.

## Typing and heartbeats

They are the editor's job, not Nightshift's.

| Piece | When it happens |
| --- | --- |
| Human-paced typing | Editor extension is listening; plugin hooks POST `/edit` for each write |
| Heartbeats | Same keystrokes, plus the opt-in sink (`hackaboom --track-telemetry`) |

The desk `/mirror` payload reports `typing` (bridge up) and `heartbeats`
(bridge up **and** `telemetry.enabled` in `~/.hackaboom.json`).

If `/mirror` says `collision`, Nightshift and the editor are fighting over
port 8788. Move Nightshift to 8791.

## Keep talking after handoff

The instruction box stays. After a run is queued, the button becomes **Send
follow-up**. Each submit is another `POST /runs` against the same session id
(`claude --resume` again). Open the editor control panel link to watch the
buffer; that URL includes the live bridge token from `~/.hackaboom.json`.

Interactive TUI attach (`tmux attach -t nsrun-<id8>`) still works for a run in
flight, but `-p` exits when that instruction finishes. Follow-ups from the desk
are the supported way to keep going.
