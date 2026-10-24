# Cursor hooks: is there a PreToolUse/PostToolUse-equivalent surface?

Research for **Hackaboom spec v1.5 §3** (`docs/SPEC-v1.5.md` @ `b08af0b`).
Card: `t_2e657a61`. Cross-link stub: `/home/das/hackaboom/docs/notes/RESEARCH-cursor-hooks.md`.

**Verdict: YES.** Cursor exposes a first-class, documented, Claude-Code-shaped hook
surface. `preToolUse` / `postToolUse` / `postToolUseFailure` exist by name, take a
JSON payload on stdin, and return JSON on stdout — including `permission:
"allow" | "deny"` and `updated_input`. Code can POST edit/think events from these
hooks. Verified live on this box, not just from docs.

Do **not** design `plugin-cursor/` here (§3 says evidence first). This report is the
evidence.

---

## 1. What was actually run (evidence inventory)

Three independent evidence sources, all reproduced below.

| # | Source | Strength |
|---|---|---|
| A | Official docs, `https://cursor.com/docs/agent/hooks` (fetched 2026-10-23) | authoritative spec |
| B | The installed harness itself: `cursor-agent` 2026.09.10-fd3934a, `index.js` hooks module + `@cursor/sdk@1.0.31` bundle | what the code really does |
| C | Live runs on das-server with a logging hook installed into a throwaway repo | ground truth |

### C — live run 1 (capture)

Scratch repo `/home/das/.hermes/kanban/workspaces/t_2e657a61/hooktest` with a
project hook file `.cursor/hooks.json` wiring **all 12 agent events** to one logger
that appends stdin + env to a log and always exits 0.

Command:

```
CURSOR_API_KEY=$(cat ~/.cursor/api_key) \
  cursor-agent -p "Create a file named hello.txt containing exactly the word: hi. Then run the shell command: echo ok" \
  --force --output-format text
```

Result: `hello.txt` created, `echo ok` ran, agent exited 0. Events that actually
fired (counted from the log):

```
3  preToolUse        (tool_name: Read, Shell, Write)
2  postToolUse       (tool_name: Shell, Write)
1  sessionStart
1  sessionEnd
1  postToolUseFailure(tool_name: Read — "File not found")
1  beforeShellExecution
1  afterShellExecution
1  afterFileEdit
```

**Did not fire** in this headless `-p` run despite being configured:
`beforeSubmitPrompt`, `beforeReadFile`, `afterAgentResponse`, `stop`. See §8.

### C — live run 2 (control, not just observation)

Same repo, `preToolUse` with `matcher: "Write"` returning:

```json
{"permission":"deny",
 "user_message":"Hackaboom test hook: writes blocked by policy.",
 "agent_message":"A preToolUse hook denied this Write. Do not retry; tell the user it was blocked."}
```

Prompt: *"Create a file named blocked.txt containing the word nope."*

Result: `blocked.txt` **does not exist**; the agent replied *"The write to
`blocked.txt` was blocked by policy, so the file was not created."*

So the surface is not merely observational — it gate-keeps the agent loop, and
`agent_message` reaches the model.

---

## 2. Hook catalogue (source A + B agree)

Agent hooks (fire during an agent session):

```
sessionStart / sessionEnd
preToolUse / postToolUse / postToolUseFailure     <- the Claude Code equivalent
subagentStart / subagentStop
beforeShellExecution / afterShellExecution
beforeMCPExecution / afterMCPExecution
beforeReadFile / afterFileEdit
beforeSubmitPrompt
preCompact
stop
afterAgentResponse / afterAgentThought
```

Separate surfaces: Tab hooks (`beforeTabFileRead`, `afterTabFileEdit`) and the app
lifecycle hook (`workspaceOpen`).

The harness ships an explicit Claude Code mapping table (found in the CLI bundle):

```
PreToolUse -> preToolUse        UserPromptSubmit -> beforeSubmitPrompt
PostToolUse -> postToolUse      Stop             -> stop
SubagentStop -> subagentStop    SessionStart     -> sessionStart
SessionEnd -> sessionEnd        PreCompact       -> preCompact
PermissionRequest -> null       Notification     -> null
```

`PermissionRequest` and `Notification` are the only two Claude events with no Cursor
counterpart. Cursor also accepts **Claude-style hook output** (`permissionDecision`,
`permissionDecisionReason`, `updatedInput`) and translates it internally into
`permission` / `user_message` / `updated_input`. There is a third-party-hooks
compatibility doc at `https://cursor.com/docs/reference/third-party-hooks`.

---

## 3. Configuration surface

Lives in `hooks.json`, `{ "version": 1, "hooks": { <event>: [ <def>, ... ] } }`.

Locations and precedence (highest first): **Enterprise** (`/etc/cursor/hooks.json`
on Linux) → **Team** (dashboard, Enterprise only) → **Project**
(`<project>/.cursor/hooks.json`) → **User** (`~/.cursor/hooks.json`), then plugin
hooks last.

Working directory is per-source and is a real footgun: project hooks run from the
**project root** (so use `.cursor/hooks/x.sh`), user hooks run from **`~/.cursor/`**
(so use `./hooks/x.sh`).

Per-definition options:

| Option | Type | Default | Notes |
|---|---|---|---|
| `command` | string | required | shell string / absolute / relative |
| `type` | `"command"` \| `"prompt"` | `"command"` | `prompt` = LLM-evaluated policy hook |
| `timeout` | number (seconds) | platform default | harness validates `>0`, warns `>3600` |
| `loop_limit` | number \| null | `5` | for `stop` / `subagentStop` follow-ups |
| `failClosed` | boolean | `false` | `true` = crash/timeout/non-zero/no-output **blocks** |
| `matcher` | regex string | — | `""` or `"*"` = everything |

Matchers run against: tool name for `preToolUse`/`postToolUse`/
`postToolUseFailure` (`Shell`, `Read`, `Write`, `Grep`, `Delete`, `Task`,
`MCP:<tool_name>`); subagent type for `subagentStart`/`Stop`; the command string
for `beforeShellExecution`/`afterShellExecution`; and fixed sentinels elsewhere
(`beforeReadFile`→`Read`, `afterFileEdit`→`Write`, `beforeSubmitPrompt`→
`UserPromptSubmit`, `stop`→`Stop`).

Merging: all matching hooks from all sources run; `deny` beats `ask` beats `allow`;
`user_message`/`agent_message` concatenate; last response wins for
`followup_message`.

---

## 4. Payloads — verbatim from the live run

Common envelope on every agent event:

```json
{"conversation_id":"…","generation_id":"…","model":"default",
 "session_id":"…","hook_event_name":"preToolUse",
 "cursor_version":"2026.09.10-fd3934a",
 "workspace_roots":["/…/hooktest"],"user_email":"…","transcript_path":null}
```

Env vars present in the hook process (observed): `CURSOR_PROJECT_DIR`,
`CLAUDE_PROJECT_DIR` (alias, always set), `CURSOR_VERSION`, `CURSOR_USER_EMAIL`.
Docs add `CURSOR_TRANSCRIPT_PATH`, `CURSOR_CODE_REMOTE`, `CURSOR_PLUGIN_ROOT`.
`transcript_path` was `null` on `sessionStart` and populated on later events.

`preToolUse` (observed, Write):

```json
{"tool_name":"Write",
 "tool_input":{"file_path":"/…/hello.txt","content":"hi\n"},
 "tool_use_id":"call-…","session_id":"…","hook_event_name":"preToolUse", …}
```

`preToolUse` (observed, Shell):

```json
{"tool_name":"Shell","tool_input":{"command":"echo ok","cwd":"","timeout":30000},
 "tool_use_id":"…","cwd":"","hook_event_name":"preToolUse", …}
```

`postToolUse` (observed) adds `tool_output` (a JSON **string**) and `duration` (ms):

```json
{"tool_name":"Shell","tool_input":{"command":"echo ok","cwd":"","timeout":30000},
 "tool_output":"{\"output\":\"ok\\n\",\"exitCode\":0}","duration":348.135, …}
```

`postToolUseFailure` (observed) adds `error_message`, `failure_type`
(`"error"|"timeout"|"permission_denied"`), `duration`, `is_interrupt`.

`afterFileEdit` (observed) — **the one Hackaboom's `/edit` wants**:

```json
{"file_path":"/…/hello.txt",
 "edits":[{"old_string":"","new_string":"hi\n"}], …}
```

`beforeShellExecution` / `afterShellExecution` (observed):
`{"command","cwd","sandbox"}` and `{"command","output","duration","sandbox"}`.

`sessionEnd` (observed):
`{"reason":"completed","duration_ms":33940,"is_background_agent":false,
  "final_status":"completed"}`.

---

## 5. Response contract — the part that matters for not hanging the agent

Exit code:

- **0** — succeeded, stdout JSON is parsed and used. For permission hooks
  (`beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`,
  `beforeTabFileRead`, `subagentStart`, `preToolUse`) **invalid JSON or a
  schema-violating response blocks the action** even with `failClosed: false`.
- **2** — block (equivalent to `permission: "deny"`). Claude Code compatible.
- **other** — treated as hook failure, action proceeds (fail-open) unless
  `failClosed: true`.

`preToolUse` output schema:

```json
{"permission":"allow"|"deny",
 "user_message":"…shown in client when denied…",
 "agent_message":"…sent to the agent when denied…",
 "updated_input":{…}}
```

The harness validator accepts `permission ∈ {"allow","deny","ask"}` for
`beforeShellExecution`/`beforeMCPExecution`, and `{"allow","deny"}` for
`beforeReadFile`/`beforeTabFileRead`. Docs document `preToolUse` as
`allow | deny` only — the in-binary validator for `preToolUse` was not pinned down
(see §8).

---

## 6. Cloud agents (§3's second question)

Docs answer it directly: **cloud agents run project hooks** (`.cursor/hooks.json`
committed to the repo) inside the cloud VM, for `preToolUse`, `postToolUse`,
`postToolUseFailure`, `afterFileEdit`, `beforeShellExecution`, etc.

What cloud agents do **not** get:

- **User hooks (`~/.cursor/hooks.json`) — explicitly unavailable.** The cloud VM has
  no access to your local home directory. This means the wall's current
  `~/.cursor/hooks.json` → `wall-status.mjs` wiring **does not fire for cloud
  agents**.
- `sessionStart`, `sessionEnd`, `beforeMCPExecution`/`afterMCPExecution`, Tab hooks,
  `workspaceOpen`.
- **Prompt-based hooks** — command hooks only.

So a cloud agent cannot reach a local editor session through hooks; its hooks run
5000 miles away in its own VM. Two viable shapes: (a) project hooks in the repo that
POST to the wall's public URL from inside the VM, or (b) a **Self-Hosted Machine
worker** (`cursor-agent worker`, "My Machines", no Enterprise needed) which executes
tool calls on this machine — there `sessionStart`/`sessionEnd` fire locally on claim
and release.

### Cloud agent run, as the card asked

`/home/das/scripts/cursor-bridge.js` (needs
`NODE_PATH=/home/das/.npm-global/lib/node_modules` — it throws
`Cannot find module '@cursor/sdk'` without it, because the SDK is only installed
globally). With that fixed, cloud mode worked on this key:

```
{"id":"run-fe009179-…","status":"finished","model":{"id":"composer-2.5"},
 "durationMs":83151}
```

Asked to report its own harness hooks, the agent said its workspace was empty
(`/agent`, `repoUrl: null`) and it **could not see Cursor IDE `.cursor/hooks.json`**
at all — it found only an unrelated `@grok/agent-sdk` npx cache describing a
different thing (`defineHook` over session stream events), whose own docs say it is
*"unrelated to Cursor Agent hooks, the `.cursor/hooks.json` scripts"*.
**Treat that VM's self-report as unverified** — I cannot inspect that VM. The
authoritative evidence for hooks is §1–§5 above, gathered on this machine.

---

## 7. What this means for `plugin-cursor/` (input for the design card, not the design)

- The bridge contract maps cleanly: `/think` ← `afterAgentThought` /
  `beforeSubmitPrompt`; `/edit` ← `afterFileEdit` (has `file_path` + `edits`);
  `/read` ← `postToolUse` with `matcher: "Read"`.
- **Do not let the wall block the agent.** Hooks are synchronous gates: a slow hook
  stalls the loop, and a permission hook that returns junk *blocks*. `wall-status.mjs`
  already gets this right (fire-and-forget, always `exit 0`). Any `plugin-cursor/`
  hook must keep that shape — the §3 "2s AbortSignal, nothing rethrown" semantics are
  the same requirement seen from the other side.
- Prefer `~/.cursor/hooks.json` (user level) for a personal install and
  `.cursor/hooks.json` (project level) only where the repo should carry it — and note
  the different relative-path roots.
- Watch the payload size: `afterFileEdit.edits` carries full `old_string`/
  `new_string` bodies, and `postToolUse.tool_output` carries raw tool output. The wall
  should slim these exactly as `slim()` in `wall-status.mjs` already does — never
  forward file contents.

---

## 8. Not verified / open items

Stated as open, per the honesty rule:

- `beforeSubmitPrompt`, `beforeReadFile`, `afterAgentResponse`, `stop` were configured
  but **did not fire** in headless `-p` mode across the runs. Docs describe them as
  Agent Chat hooks. Whether they fire under `cursor-agent` interactive/TUI or only in
  the desktop app is **not verified**. Hackaboom's headless path should therefore not
  depend on `stop` for "run finished" — `sessionEnd` did fire and is the safer signal.
- `preToolUse` accepting `permission: "ask"`: docs document `allow|deny` only; the
  binary validator for `beforeShellExecution`/`beforeMCPExecution` accepts `ask`. The
  `preToolUse` validator's exact enum was not extracted — **not verified**.
- `updated_input` was not exercised. Docs show it rewriting a Shell command
  (`npm install` → `npm ci`) — **not verified** on this box.
- `failClosed`, `loop_limit`, prompt-based (`type: "prompt"`) hooks, plugin hooks
  (`CURSOR_PLUGIN_ROOT`), and the `cursor-agent plugin marketplace` path were **not
  exercised**. `cursor-agent plugin --help` exposes only `marketplace
  {add,list,remove,update}` on this CLI build.
- Cloud-agent hook execution was **not observed** — the cloud run had no repo, so no
  project hooks existed to fire. Docs claim support; that claim is **docs-only** here.
- Windows/PowerShell hook execution (relevant to the emulated-Windows v1.5 work) was
  **not tested**.
- Version drift: installed CLI is 2026.09.10-fd3934a; upstream is
  2026.10.22-27dcf5f. Nothing here was re-checked against the newer build.

## 9. Reproduce

```bash
cd /home/das/.hermes/kanban/workspaces/t_2e657a61
ls hooktest/.cursor/hooks.json hooktest/.cursor/hooks/log.sh   # capture harness
ls hookdeny/.cursor/hooks.json hookdeny/.cursor/hooks/deny-write.sh  # deny harness
wc -l events.log deny-events.log                                # captured payloads
```

## Sources

- Official: https://cursor.com/docs/agent/hooks (full text cached at
  `/home/das/.hermes/profiles/worker/cache/web/cursor.com-ea71ddf504.md`)
- Third-party/Claude compatibility: https://cursor.com/docs/reference/third-party-hooks
- Local harness: `cursor-agent` 2026.09.10-fd3934a,
  `/home/das/.local/share/cursor-agent/versions/2026.09.10-fd3934a/index.js`
  (hooks module, internal path `../hooks/dist/index.js`)
- Local SDK: `/home/das/.npm-global/lib/node_modules/@cursor/sdk@1.0.31/dist/bundled/index.js`
- Existing wall wiring: `/home/das/.cursor/hooks.json`,
  `/home/das/.config/smart-display/wall-status.mjs`
