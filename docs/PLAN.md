# Nightshift — plan of record

One line: hit a button on the PC dashboard at night → it collects your coding sessions
(Claude Code first, Cursor later) → ships them over Tailscale to das-server → agents keep
grinding headless → Finn pings Discord/iMessage when the work's done.
Reverse direction later: feed plans from a phone dashboard over Tailscale.

Related: Hackaboom v1.5 spec (`~/hackaboom` branch `feat/headless-launchpad` @ b08af0b —
headless linux, emulated windows, cursor, dashboard). Nightshift is the session-handoff
half of that direction; agent-lane dashboard ideas from that spec §4 apply here later.

## Slice plan (owner-approved order)

1. **CC sessions ingest** — Claude Code sessions are jsonl files (Windows: `~/.claude/projects/`,
   server-side same layout). Collector packs + POSTs them; server indexes them (id, project,
   cwd, timestamps, turns) idempotently.
2. **Headless resume runner** — restart an ingested session headless on the server
   (`claude` CLI resume/-p semantics against the matching project dir, tmux/pty, logs, job
   status table). This is the actual "grind headless" engine.
3. **Done-notify** — run lifecycle (done/failed) → `hermes send --to discord:home`
   (+ photon option), manual test endpoint.
4. **PC collector + dashboard** — collector CLI is on main; the resume control
   panel lives at `GET /` on the server (`docs/desk.md`). Extra Windows dashboard
   chrome can still land later.
5. **Cursor path** — two separate things, do not conflate:
   - Cursor cloud agents (api.cursor.com/v1/agents, @cursor/sdk): orchestration lane; bridge
     exists at `/home/das/scripts/cursor-bridge.js` (+ `/home/das/scripts/cursor-orchestrator.py`).
     Needs the Cursor API key dropped on the wall (see RESEARCH below).
   - Cursor-as-editor adapter (Hackaboom plugin-cursor): evidence-first research per spec §3.
   Resume runs prepare the local editor bridge (typing + optional heartbeats)
   when `hackaboom` is on PATH; see `docs/desk.md`.

## Ground rules

- Branch per feature → push → PR; owner merges. (hackaboom direct-to-master pattern does
  NOT carry over to this fresh repo until owner says so.)
- Tests per slice are part of "done"; no fake greens. Honesty rails from Hackaboom carry over.
- Secrets never enter the repo; Tailscale is the assumed transport, never expose publicly.
- Ship-it contract in force (2026-10-23): assigned work gets committed and pushed.