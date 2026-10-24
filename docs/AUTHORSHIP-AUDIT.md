# Authorship audit — history snapshot

**Report only. No history rewrite.** Existing commits stay exactly as they are. This file
records what `scripts/check_authorship.mjs` found in the already-pushed history so the
findings are documented rather than silently scrubbed. New work follows `CONTRIBUTING.md`
and is gated by the `commit-msg` hook plus `--strict` before a PR.

- **Date:** 2026-10-23
- **Scope:** pushed history of `main` (`node scripts/check_authorship.mjs --range=main`)
- **Result:** 11 commits, 40 tracked files → 6 violations, 116 advisories

## Violations (attribution / identity)

| Short | Author | Subject | Kind |
| --- | --- | --- | --- |
| `7a99655` | Arriq `<157200408+DasVR@users.noreply.github.com>` | Merge pull request #5 from DasVR/cursor/collector-cli-452b | `branch-name` — agent-branded branch (`cursor/…`) in the merge subject |
| `7297563` | Cursor Agent `<cursoragent@cursor.com>` | feat(collector): PC-side session collector CLI (slice 4) | `trailer` — `Co-authored-by:` in the body |
| `7297563` | Cursor Agent `<cursoragent@cursor.com>` | feat(collector): PC-side session collector CLI (slice 4) | `author-identity` — AI identity as commit author + committer |
| `7297563` | Cursor Agent `<cursoragent@cursor.com>` | feat(collector): PC-side session collector CLI (slice 4) | `non-owner-author` — not the owner allowlist email |
| `0ebfce9` | worker `<worker@das-server>` | ingest: CC session bundle ingestion, sqlite store, list/detail API | `non-owner-author` |
| `1a4433a` | worker `<worker@das-server>` | done-notify: hermes send on terminal run state + POST /notify/test | `non-owner-author` |

## File-level violations

None. No tracked file carries a `generated-by` / `co-authored-by` trailer, an agent
e-mail, or a robot emoji.

## Advisories (subject matter, not credit)

116 advisory hits: the product legitimately ingests and resumes external agent session
bundles, so `README.md`, `.env.example`, `collector/*`, `docs/PLAN.md` and
`docs/collector.md` name the session format and its CLI by name (e.g. `--claude-dir`,
`~/.claude/projects`). Commit subjects/bodies that describe that subject matter
(`0ebfce9`, `472d834`) are reported the same way. Advisories are never fatal: they exist
so a genuine credit line is not confused with a description of what the tool does.

## Pre-PR gate

```bash
node scripts/check_authorship.mjs --strict --range=origin/main..HEAD
```

Exit `0` = clean, `1` = at least one attribution/identity violation in the new commits.
