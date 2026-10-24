from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from server.config import Settings
from server.ingest import (
    BundleContents,
    ParsedSession,
    ZipEntry,
    derive_session_id,
    is_uuid,
    iter_zip_entries,
    parse_session_bytes,
    safe_project_name,
    sha256_hex,
)
from server.store import (
    SessionRow,
    ensure_schema,
    get_connection,
    get_session,
    list_sessions,
    record_bundle,
    upsert_session,
)


router = APIRouter(prefix="/sessions", tags=["sessions"])


@dataclass(frozen=True)
class _IngestedSession:
    session_id: str
    project: str
    status: str
    turn_count: int
    skipped_lines: int
    warning: str | None


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _read_upload(upload: UploadFile | None) -> bytes:
    if upload is None:
        return b""
    try:
        return upload.file.read()
    finally:
        upload.file.close()


def _stored_path(settings: Settings, project: str, session_id: str) -> Path:
    return settings.sessions_dir / safe_project_name(project) / f"{session_id}.jsonl"


def _persist_raw(data: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _derive_project_from_manifest(manifest_data: dict | None, path: str) -> str | None:
    if not isinstance(manifest_data, dict):
        return None
    sessions = manifest_data.get("sessions") or []
    for item in sessions:
        if isinstance(item, dict) and item.get("path") == path:
            project = item.get("project")
            if isinstance(project, str):
                return project
    return None


def _validate_manifest(
    manifest_bytes: bytes | None,
    contents: BundleContents,
) -> dict | None:
    if manifest_bytes is None or not manifest_bytes.strip():
        return None
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"manifest is not valid JSON: {exc}",
        ) from exc
    if not isinstance(manifest, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="manifest must be a JSON object",
        )
    sessions = manifest.get("sessions")
    if sessions is not None and not isinstance(sessions, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="manifest.sessions must be a list",
        )
    if sessions is None:
        return manifest

    jsonl_by_name = {e.name: e for e in contents.entries}
    for item in sessions:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        if path not in contents.all_names:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"manifest path not present in bundle: {path}",
            )
        entry = jsonl_by_name.get(path)
        declared_sha = item.get("sha256")
        if isinstance(declared_sha, str):
            if entry is not None:
                actual = sha256_hex(entry.data)
            else:
                actual = sha256_hex(contents.read_entry(path))
            if declared_sha.lower() != actual.lower():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"sha256 mismatch for manifest path: {path}",
                )
        declared_id = item.get("session_id")
        if isinstance(declared_id, str) and entry is not None:
            from_filename = derive_session_id(entry.session_id_hint, entry.data)
            if declared_id.strip().lower() != from_filename.lower():
                # Only enforce when the filename stem is a UUID (derive_session_id returns it).
                if is_uuid(entry.session_id_hint):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=(
                            f"manifest session_id disagrees with filename for path: {path}"
                        ),
                    )
    return manifest


def _collect_entries(
    bundle_bytes: bytes,
    filename: str,
    settings: Settings,
) -> tuple[str, BundleContents]:
    if not bundle_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="empty bundle",
        )

    is_zip_claimed = bundle_bytes.startswith(b"PK") or filename.lower().endswith(".zip")
    if is_zip_claimed:
        try:
            contents = iter_zip_entries(
                bundle_bytes,
                max_session_bytes=settings.max_session_bytes,
                max_bundle_bytes=settings.max_bundle_bytes,
            )
        except ValueError as exc:
            msg = str(exc)
            if "not a valid zip" in msg:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=msg,
                ) from exc
            if "unsafe path" in msg:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=msg,
                ) from exc
            if "bundle exceeds" in msg:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=msg,
                ) from exc
            if "entry exceeds" in msg:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=msg,
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=msg,
            ) from exc
        return "zip", contents

    # Bare jsonl.
    if len(bundle_bytes) > settings.max_session_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="jsonl exceeds max session bytes",
        )
    return "jsonl", BundleContents(
        entries=[ZipEntry(name="session.jsonl", project="", session_id_hint="session", data=bundle_bytes)],
        all_names={"session.jsonl": len(bundle_bytes)},
        _zip_bytes=b"",
    )


@router.post("/ingest")
async def ingest(
    request: Request,
    bundle: UploadFile | None = File(None),
    manifest: UploadFile | None = File(None),
) -> dict:
    """Ingest a session bundle (zip or bare jsonl) with optional manifest."""
    settings: Settings = _get_settings(request)
    settings.ensure_dirs()

    bundle_bytes = _read_upload(bundle)
    if not bundle_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="missing bundle",
        )

    manifest_bytes = _read_upload(manifest)
    source, contents = _collect_entries(bundle_bytes, bundle.filename or "", settings)

    manifest_data = _validate_manifest(manifest_bytes, contents)

    conn = get_connection(settings.db_path)
    try:
        ensure_schema(conn)

        ingested: list[_IngestedSession] = []
        counts: dict[str, int] = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "empty": 0,
            "total": 0,
        }

        for entry in contents.entries:
            project = entry.project or _derive_project_from_manifest(manifest_data, entry.name) or "_unknown"
            session_id = derive_session_id(entry.session_id_hint, entry.data)
            parsed = parse_session_bytes(entry.data, project)

            stored = _stored_path(settings, parsed.project, session_id)

            result = upsert_session(
                conn,
                session_id=session_id,
                project=parsed.project,
                cwd=parsed.cwd,
                first_ts=parsed.first_ts,
                last_ts=parsed.last_ts,
                turn_count=parsed.turn_count,
                user_turns=parsed.user_turns,
                assistant_turns=parsed.assistant_turns,
                tool_use_count=parsed.tool_use_count,
                sidechain_lines=parsed.sidechain_lines,
                line_count=parsed.line_count,
                skipped_lines=parsed.skipped_lines,
                content_sha256=parsed.content_sha256,
                stored_path=str(stored.relative_to(settings.data_dir)),
                size_bytes=parsed.size_bytes,
                source=source,
            )

            if result.status != "unchanged":
                _persist_raw(entry.data, stored)

            if parsed.turn_count == 0:
                status_label = "empty"
                counts["empty"] += 1
            else:
                status_label = result.status
                counts[status_label] += 1
            counts["total"] += 1

            warning: str | None = None
            if parsed.skipped_lines > 0:
                warning = f"{parsed.project}/{entry.session_id_hint}.jsonl: {parsed.skipped_lines} malformed line(s) skipped"

            ingested.append(
                _IngestedSession(
                    session_id=session_id,
                    project=parsed.project,
                    status=status_label,
                    turn_count=parsed.turn_count,
                    skipped_lines=parsed.skipped_lines,
                    warning=warning,
                )
            )

        host = manifest_data.get("host") if isinstance(manifest_data, dict) else None
        collector_version = (
            manifest_data.get("collector_version")
            if isinstance(manifest_data, dict)
            else None
        )
        bundle_id = record_bundle(
            conn,
            source=source,
            host=host if isinstance(host, str) else None,
            collector_version=(
                collector_version if isinstance(collector_version, str) else None
            ),
            session_count=len(ingested),
            manifest_present=manifest_bytes is not None and bool(manifest_bytes.strip()),
        )

        return {
            "bundle_id": bundle_id,
            "source": source,
            "counts": counts,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "project": s.project,
                    "status": s.status,
                    "turn_count": s.turn_count,
                    "skipped_lines": s.skipped_lines,
                }
                for s in ingested
            ],
            "warnings": [s.warning for s in ingested if s.warning],
            "errors": [],
        }
    finally:
        conn.close()


@router.get("")
async def list_sessions_route(
    request: Request,
    project: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List persisted sessions."""
    settings: Settings = _get_settings(request)
    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="limit must be between 1 and 500",
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="offset must be >= 0",
        )

    conn = get_connection(settings.db_path)
    try:
        ensure_schema(conn)
        total, rows = list_sessions(conn, project=project, limit=limit, offset=offset)
        return {
            "count": len(rows),
            "total": total,
            "sessions": [_row_to_api(row) for row in rows],
        }
    finally:
        conn.close()


@router.get("/{session_id}")
async def get_session_route(request: Request, session_id: str) -> dict:
    """Return a single session by id."""
    settings: Settings = _get_settings(request)
    conn = get_connection(settings.db_path)
    try:
        ensure_schema(conn)
        row = get_session(conn, session_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"session not found: {session_id}",
            )
        return _row_to_api(row)
    finally:
        conn.close()


def _row_to_api(row: SessionRow) -> dict:
    return {
        "session_id": row.session_id,
        "project": row.project,
        "cwd": row.cwd,
        "first_ts": row.first_ts,
        "last_ts": row.last_ts,
        "turn_count": row.turn_count,
        "user_turns": row.user_turns,
        "assistant_turns": row.assistant_turns,
        "tool_use_count": row.tool_use_count,
        "sidechain_lines": row.sidechain_lines,
        "line_count": row.line_count,
        "skipped_lines": row.skipped_lines,
        "content_sha256": row.content_sha256,
        "stored_path": row.stored_path,
        "size_bytes": row.size_bytes,
        "source": row.source,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
