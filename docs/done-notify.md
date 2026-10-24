# done-notify

Notify configured channels via the local Hermes CLI when a nightshift run reaches a terminal state (`done` or `failed`).

## How it works

The `server/notify.py` module is intentionally thin and self-contained:

- `format_run_message(run)` builds a concise subject/body from a run record.
- `notify_run(run, settings)` sends the message to every resolved target.
- The endpoint `POST /notify/test` lets operators fire a manual check.

No run storage, job table, or runner logic lives here. The run-lifecycle module in the parallel branch owns persistence.

## Hermes argv

For each target the notifier runs:

```
$NIGHTSHIFT_HERMES_BIN send --to <target> --subject <subject> <body>
```

Each argument is passed as a separate list element to `subprocess.run`, so no shell quoting is involved. The `body` is four separate lines (`session:`, `project:`, `outcome:`, `summary:`) plus an optional `run:` line when the record has an `id`; only the `summary` value is collapsed onto one line and truncated at 200 characters. Hermes itself prepends the `--subject` value plus a blank line, so the delivered message is `subject\n\n<body>`.

## Settings

| Variable | Purpose |
| --- | --- |
| `NIGHTSHIFT_NOTIFY_TARGETS` | Comma-separated list of `hermes send` targets (default: `discord:home`). |
| `NIGHTSHIFT_PHOTON_TARGET` | Optional extra target appended when set and not already in the list. |
| `NIGHTSHIFT_HERMES_BIN` | Override path to the `hermes` binary; otherwise resolved from `$PATH`. |

### Which Hermes profile

`hermes send` reads credentials and the channel directory from **one** Hermes profile home. On this host the default profile home is `/home/das/.hermes` and it holds both `.env` (Discord token) and `channel_directory.json`; a non-default profile home such as `/home/das/.hermes/profiles/worker` has no channel directory, and `hermes send --list` prints `No messaging platforms configured`. The nightshift service must therefore be launched with `HERMES_HOME=/home/das/.hermes` (or run under that profile) or notifications will fail. Verified on this host:

```bash
HERMES_HOME=/home/das/.hermes hermes send --list      # lists discord:#home etc.
# versus
HERMES_HOME=/home/das/.hermes/profiles/worker hermes send --list  # "No messaging platforms configured"
```

Failures are non-fatal by design (`notify_run` never raises), so you must still check the exit code / `NotifyResult.ok`.

### Target resolution

`discord:home` is resolved by Hermes as a channel **name** through the channel directory, not as a hardcoded id. Verified on this host: the directory holds a Discord channel named `home` (id `1505304425420554342`, guild "Agent Community"). If the channel is renamed or the directory is empty, the send fails with a resolution error that lands in `NotifyResult.detail`.

## Integration contract for run-lifecycle

When a run transitions to `done` or `failed`, after the run row has been persisted call:

```python
from server.notify import notify_run

notify_run(run_dict, settings)
```

Call it exactly once per terminal transition. `notify_run` never raises, never blocks, and must never roll back the run row update. Notification failures are reported in the returned `NotifyResult` and logged at warning level.

## Manual checks

Fire the test endpoint:

```bash
curl -sX POST localhost:8788/notify/test
```

For a live send check, install Hermes locally, set `NIGHTSHIFT_NOTIFY_TARGETS`, and post to `/notify/test` with a custom summary:

```bash
curl -sX POST localhost:8788/notify/test \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"manual-check","project":"nightshift-test","outcome":"done","summary":"live notification test"}'
```

To send directly through Hermes (this messages a real Discord channel and is **not** run automatically):

```bash
HERMES_HOME=/home/das/.hermes hermes send --to discord:home --subject "nightshift done: live-check" "session: live-check
project: nightshift
outcome: done
summary: live send check"
```

The owner should run the above once to confirm the profile and channel are correct.
