from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionRow:
    """Row representation of a persisted session."""

    session_id: str
    project: str
    cwd: str | None
    first_ts: str | None
    last_ts: str | None
    turn_count: int
    user_turns: int
    assistant_turns: int
    tool_use_count: int
    sidechain_lines: int
    line_count: int
    skipped_lines: int
    content_sha256: str
    stored_path: str
    size_bytes: int
    source: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class UpsertResult:
    """Result of a session upsert."""

    status: str  # "created" | "updated" | "unchanged"
    created_at: str


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 with Z suffix (microsecond precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a sqlite connection with WAL and dict factory."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they do not exist; bump schema version."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id      TEXT PRIMARY KEY,
            project         TEXT NOT NULL,
            cwd             TEXT,
            first_ts        TEXT,
            last_ts         TEXT,
            turn_count      INTEGER NOT NULL DEFAULT 0,
            user_turns      INTEGER NOT NULL DEFAULT 0,
            assistant_turns INTEGER NOT NULL DEFAULT 0,
            tool_use_count  INTEGER NOT NULL DEFAULT 0,
            sidechain_lines INTEGER NOT NULL DEFAULT 0,
            line_count      INTEGER NOT NULL DEFAULT 0,
            skipped_lines   INTEGER NOT NULL DEFAULT 0,
            content_sha256  TEXT NOT NULL,
            stored_path     TEXT NOT NULL,
            size_bytes      INTEGER NOT NULL DEFAULT 0,
            source          TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ingest_bundles (
            bundle_id         TEXT PRIMARY KEY,
            received_at       TEXT NOT NULL,
            source            TEXT NOT NULL,
            host              TEXT,
            collector_version TEXT,
            session_count     INTEGER NOT NULL DEFAULT 0,
            manifest_present  INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
        CREATE INDEX IF NOT EXISTS idx_sessions_last_ts ON sessions(last_ts DESC);

        INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '1');
        """
    )
    conn.commit()


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        session_id=row["session_id"],
        project=row["project"],
        cwd=row["cwd"],
        first_ts=row["first_ts"],
        last_ts=row["last_ts"],
        turn_count=row["turn_count"],
        user_turns=row["user_turns"],
        assistant_turns=row["assistant_turns"],
        tool_use_count=row["tool_use_count"],
        sidechain_lines=row["sidechain_lines"],
        line_count=row["line_count"],
        skipped_lines=row["skipped_lines"],
        content_sha256=row["content_sha256"],
        stored_path=row["stored_path"],
        size_bytes=row["size_bytes"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_session(
    conn: sqlite3.Connection,
    session_id: str,
    project: str,
    cwd: str | None,
    first_ts: str | None,
    last_ts: str | None,
    turn_count: int,
    user_turns: int,
    assistant_turns: int,
    tool_use_count: int,
    sidechain_lines: int,
    line_count: int,
    skipped_lines: int,
    content_sha256: str,
    stored_path: str,
    size_bytes: int,
    source: str | None,
) -> UpsertResult:
    """Insert or update a session, returning the upsert status."""
    now = _now_iso()
    cursor = conn.execute(
        "SELECT content_sha256, created_at FROM sessions WHERE session_id = ?",
        (session_id,),
    )
    existing = cursor.fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, project, cwd, first_ts, last_ts, turn_count, user_turns,
                assistant_turns, tool_use_count, sidechain_lines, line_count,
                skipped_lines, content_sha256, stored_path, size_bytes, source,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                project,
                cwd,
                first_ts,
                last_ts,
                turn_count,
                user_turns,
                assistant_turns,
                tool_use_count,
                sidechain_lines,
                line_count,
                skipped_lines,
                content_sha256,
                stored_path,
                size_bytes,
                source,
                now,
                now,
            ),
        )
        conn.commit()
        return UpsertResult(status="created", created_at=now)

    if existing["content_sha256"] == content_sha256:
        return UpsertResult(status="unchanged", created_at=existing["created_at"])

    conn.execute(
        """
        UPDATE sessions SET
            project = ?,
            cwd = ?,
            first_ts = ?,
            last_ts = ?,
            turn_count = ?,
            user_turns = ?,
            assistant_turns = ?,
            tool_use_count = ?,
            sidechain_lines = ?,
            line_count = ?,
            skipped_lines = ?,
            content_sha256 = ?,
            stored_path = ?,
            size_bytes = ?,
            source = ?,
            updated_at = ?
        WHERE session_id = ?
        """,
        (
            project,
            cwd,
            first_ts,
            last_ts,
            turn_count,
            user_turns,
            assistant_turns,
            tool_use_count,
            sidechain_lines,
            line_count,
            skipped_lines,
            content_sha256,
            stored_path,
            size_bytes,
            source,
            now,
            session_id,
        ),
    )
    conn.commit()
    return UpsertResult(status="updated", created_at=existing["created_at"])


def get_session(conn: sqlite3.Connection, session_id: str) -> SessionRow | None:
    """Return a session row by id, or None if not found."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def list_sessions(
    conn: sqlite3.Connection,
    project: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[SessionRow]]:
    """Return (total_count, rows) sorted by last_ts DESC NULLS LAST, then session_id."""
    where_clause = "WHERE project = ?" if project is not None else ""
    params: list[Any] = [project] if project is not None else []

    total_row = conn.execute(
        f"SELECT COUNT(*) AS total FROM sessions {where_clause}", params
    ).fetchone()
    total = total_row["total"]

    rows = conn.execute(
        f"""
        SELECT * FROM sessions
        {where_clause}
        ORDER BY last_ts IS NULL, last_ts DESC, session_id
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()

    return total, [_row_to_session(row) for row in rows]


def record_bundle(
    conn: sqlite3.Connection,
    source: str,
    host: str | None,
    collector_version: str | None,
    session_count: int,
    manifest_present: bool,
) -> str:
    """Record an ingest bundle receipt and return the generated bundle id."""
    bundle_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO ingest_bundles (
            bundle_id, received_at, source, host, collector_version,
            session_count, manifest_present
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bundle_id,
            _now_iso(),
            source,
            host,
            collector_version,
            session_count,
            1 if manifest_present else 0,
        ),
    )
    conn.commit()
    return bundle_id
