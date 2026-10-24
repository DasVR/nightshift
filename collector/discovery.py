from __future__ import annotations

import fnmatch
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


def is_uuid(value: str) -> bool:
    """Return True if value parses as a UUID (any variant)."""
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class SessionCandidate:
    """A discovered Claude Code session jsonl file on disk."""

    path: Path
    session_id: str
    project: str
    mtime: datetime

    @property
    def zip_entry_name(self) -> str:
        """POSIX zip entry path: ``<project>/<session_id>.jsonl``."""
        return f"{self.project}/{self.session_id}.jsonl"


def _mtime_utc(path: Path) -> datetime:
    """Return the file's modification time as UTC."""
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def discover_sessions(
    claude_dir: Path,
    *,
    since: date | None = None,
    project_globs: list[str] | None = None,
) -> list[SessionCandidate]:
    """Walk *claude_dir* and return session jsonl files whose stem is a UUID.

    *since* keeps sessions whose file mtime (UTC) is on or after that calendar date.
    *project_globs* keeps sessions whose project name matches any fnmatch pattern.
    Results are sorted by ``(project, session_id)``.
    """
    if not claude_dir.is_dir():
        return []

    since_dt: datetime | None = None
    if since is not None:
        since_dt = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)

    globs = project_globs or []
    found: list[SessionCandidate] = []

    for path in claude_dir.rglob("*.jsonl"):
        if not path.is_file():
            continue
        stem = path.stem
        if not is_uuid(stem):
            continue
        project = path.parent.name
        mtime = _mtime_utc(path)
        if since_dt is not None and mtime < since_dt:
            continue
        if globs and not any(fnmatch.fnmatch(project, pattern) for pattern in globs):
            continue
        found.append(
            SessionCandidate(
                path=path,
                session_id=stem.lower(),
                project=project,
                mtime=mtime,
            )
        )

    found.sort(key=lambda s: (s.project, s.session_id))
    return found
