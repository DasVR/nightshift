# Authorship audit (history snapshot)

**Report only — no history rewrite.** Existing commits stay as-is. New work must follow
`CONTRIBUTING.md`: owner authorship (`DasVR <arriqaalraee@gmail.com>`), conventional
commit subjects, and no AI co-author or generated-by lines (enforced by `commit-msg` hook).

- **Date:** 2026-10-23
- **Command:** `node scripts/check_authorship.mjs`

## Summary

| Metric | Count |
| --- | ---: |
| Commits scanned | 11 |
| Files scanned | 39 |
| Violations | 8 |
| Advisories | 113 |

File-level **violations:** none. Advisories are expected (product docs and code refer to Claude Code, Cursor research notes, and `--claude-dir` paths).

## History violations

| Short | Author | Subject | Kind |
| --- | --- | --- | --- |
| `7a99655` | Arriq `<157200408+DasVR@users.noreply.github.com>` | Merge pull request #5 from DasVR/cursor/collector-cli-452b | `trailer` (branch name contains `cursor`) |
| `7297563` | Cursor Agent `<cursoragent@cursor.com>` | feat(collector): PC-side session collector CLI (slice 4) | `trailer` (`Co-authored-by:` in body) |
| `7297563` | Cursor Agent `<cursoragent@cursor.com>` | feat(collector): PC-side session collector CLI (slice 4) | `author-identity` |
| `7297563` | Cursor Agent `<cursoragent@cursor.com>` | feat(collector): PC-side session collector CLI (slice 4) | `non-owner-author` |
| `0ebfce9` | worker `<worker@das-server>` | ingest: CC session bundle ingestion, sqlite store, list/detail API | `trailer` (`Claude` in message body) |
| `0ebfce9` | worker `<worker@das-server>` | ingest: CC session bundle ingestion, sqlite store, list/detail API | `non-owner-author` |
| `472d834` | DasVR `<arriqaalraee@gmail.com>` | resume runner: headless Claude session resume via tmux + /runs job table | `trailer` (`Claude` in subject) |
| `1a4433a` | worker `<worker@das-server>` | done-notify: hermes send on terminal run state + POST /notify/test | `non-owner-author` |

## Pre-PR gate

```bash
node scripts/check_authorship.mjs --strict --range=origin/main..HEAD
```
