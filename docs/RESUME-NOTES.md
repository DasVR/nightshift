# Headless resume runner — empirical notes

These are the behaviours observed on the box (claude 2.1.308) that the
resume runner is built around.

## What actually works for headless resume

| Command | Result |
| --- | --- |
| `claude --resume <uuid> -p '<instruction>' --output-format json` | Works headless. OAuth creds in `~/.claude` are used; no API key is required. stdout is a **single JSON object** with keys including `session_id`, `result`, `is_error`, `num_turns`, `total_cost_usd`, `permission_denials`. |
| Resume keeps the same session id | The original `jsonl` is appended to, not forked. The session id in the resumed JSON matches the one passed to `--resume`. |
| `--resume <uuid>` finds the session globally under `~/.claude/projects` | It locates the right project directory automatically. |
| **Critical cwd behaviour** | Resumed turns are recorded with the **cwd of the invoking process**. Verified by resuming from `/tmp/ns-resume-probe2`: the session jsonl was stamped with `cwd=/tmp/ns-resume-probe2`. The runner therefore `chdir`s into the session's own recorded cwd before launching `claude`, otherwise the agent edits files in the wrong directory. |
| `claude -p --continue '<instruction>'` | Continues "the most recent conversation in the current directory". Ambiguous when a project dir holds several sessions, so Nightshift uses `--resume <uuid>` exclusively. |
| Unknown session id | Exit code `1`, stderr plain text: `No conversation found with session ID: <uuid>`. stdout has no JSON. The runner treats any non-zero exit as failed and stores the log tail in `error`. |
| Default permission mode | Headless default **denies Write**. `--permission-mode acceptEdits` allows file edits (Bash is still denied). `--permission-mode bypassPermissions` allows everything. Nightshift defaults to `acceptEdits` and makes it overridable via `NIGHTSHIFT_CLAUDE_PERMISSION_MODE`. |

## Why tmux is used

Not because `claude -p` needs a tty (it does not — plain headless `claude -p` ran fine
without one). tmux is used because it makes a run detachable and inspectable: the run
keeps going if the API server restarts, `tmux attach -t nsrun-<id8>` lets a human watch a
long grind live, and the shell wrapper can capture the exit code into
`data/runs/<id>.exit` without the server process holding a child it must reap.

## Log layout

Each run writes:

- `data/runs/<run_id>.json` — run record (status, result summary, exit code, error).
- `data/runs/<run_id>.log` — merged stdout/stderr from `claude`.
- `data/runs/<run_id>.exit` — numeric exit code written by the tmux shell wrapper.

## Live verification (2026-10-23, das-server, claude 2.1.308)

Two real runs were driven through `POST /runs` + the tmux worker against a session whose
recorded cwd was `/tmp/ns-live-proj`:

1. Instruction "reply with the secret token you were told to remember" → run
   `a461bd2c-5130-4bef-94e3-be339b88bd89` went queued → running → done in ~8s,
   `result_summary="WALRUS42"` (the token seeded earlier in that session),
   `exit_code=0`, `num_turns=1`, `cost_usd=0.4687764`, log at
   `data/runs/<id>.log` held the single JSON result object, tmux session gone afterwards,
   exit file contained `0`.
2. Instruction "write your secret token into token.txt in the current working directory"
   → run `6f73a008-2229-441a-a45a-f755d2b90cdc` finished `done`, `num_turns=2`, and
   `/tmp/ns-live-proj/token.txt` contained `WALRUS42` — file edits land in the session's
   recorded cwd under the default `acceptEdits` mode (Bash was not needed).

