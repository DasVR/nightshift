from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path


class InvalidSessionId(Exception):
    """Session id is not a valid UUID."""


class SessionNotFound(Exception):
    """Session jsonl could not be located or is unusable."""


@dataclass(frozen=True)
class SessionRef:
    """Pointer to a resumable Claude session on disk."""

    session_id: str
    project_dir: Path
    cwd: str
    jsonl_path: Path


def find_session(session_id: str, projects_dir: Path) -> SessionRef:
    """Locate a session jsonl under projects_dir and extract its recorded cwd.

    Raises:
        InvalidSessionId: if session_id is not a valid UUID.
        SessionNotFound: if no jsonl exists or no cwd was recorded.
    """
    try:
        uuid.UUID(session_id)
    except ValueError as exc:
        raise InvalidSessionId(f"not a valid uuid: {session_id}") from exc

    matches = list(projects_dir.glob(f"*/{session_id}.jsonl"))
    if not matches:
        raise SessionNotFound(f"no session jsonl for {session_id}")

    jsonl_path = matches[0]
    project_dir = jsonl_path.parent

    cwd: str | None = None
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh):
            if idx >= 200:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            candidate = record.get("cwd")
            if candidate:
                cwd = candidate
                break

    if not cwd:
        raise SessionNotFound("session has no recorded cwd")

    return SessionRef(
        session_id=session_id,
        project_dir=project_dir,
        cwd=cwd,
        jsonl_path=jsonl_path,
    )
