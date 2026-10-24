from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath


@dataclass(frozen=True)
class ParsedSession:
    """Counters and metadata extracted from a session jsonl."""

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
    size_bytes: int


@dataclass(frozen=True)
class ZipEntry:
    """A normalized .jsonl entry extracted from a zip bundle."""

    name: str
    project: str
    session_id_hint: str  # filename stem
    data: bytes


@dataclass(frozen=True)
class BundleContents:
    """All non-directory zip entries plus the parsed jsonl session entries.

    ``all_names`` maps every non-directory entry name to its uncompressed size.
    ``read_entry`` re-opens the bundle bytes to return the entry's raw bytes,
    so sha256 checks on non-jsonl files are done lazily and only when requested
    by the manifest.
    """

    entries: list[ZipEntry]
    all_names: dict[str, int]
    _zip_bytes: bytes

    def read_entry(self, name: str) -> bytes:
        """Return the uncompressed bytes of any non-directory entry by name."""
        with zipfile.ZipFile(BytesIO(self._zip_bytes)) as zf:
            return zf.read(name)


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def is_uuid(value: str) -> bool:
    """Return True if value parses as a UUID (any variant)."""
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def safe_project_name(name: str) -> str:
    """Sanitize a project directory name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        cleaned = "_unknown"
    return cleaned


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp and return a timezone-aware UTC datetime."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # Try stripping fractional seconds as a fallback.
        base, _, frac = text.partition(".")
        if not frac:
            return None
        # Reconstruct without fractional seconds, preserving timezone offset.
        tz_part = ""
        for sign in ("+", "-"):
            if sign in frac:
                tz_part = sign + frac.split(sign, 1)[1]
                break
        try:
            dt = datetime.fromisoformat(base + tz_part)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _format_timestamp(dt: datetime) -> str:
    """Return the canonical UTC ISO string for a datetime."""
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_timestamp(raw: str) -> str | None:
    """Parse an ISO-8601 timestamp and return UTC ISO with Z suffix."""
    dt = _parse_timestamp(raw)
    if dt is None:
        return None
    return _format_timestamp(dt)


def _count_tool_use(obj: dict) -> int:
    """Count tool_use content blocks in an assistant/sidechain message."""
    content = obj.get("message", {}).get("content")
    if not isinstance(content, list):
        return 0
    count = 0
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            count += 1
    return count


def parse_session_bytes(data: bytes, project: str) -> ParsedSession:
    """Parse a jsonl byte blob and return session counters."""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()

    line_count = 0
    skipped_lines = 0
    user_turns = 0
    assistant_turns = 0
    tool_use_count = 0
    sidechain_lines = 0
    timestamps: list[datetime] = []
    cwd: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        line_count += 1
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            skipped_lines += 1
            continue
        if not isinstance(obj, dict):
            skipped_lines += 1
            continue

        ts = _parse_timestamp(obj.get("timestamp", ""))
        if ts is not None:
            timestamps.append(ts)

        line_cwd = obj.get("cwd")
        if isinstance(line_cwd, str) and line_cwd.strip():
            cwd = line_cwd

        is_sidechain = bool(obj.get("isSidechain"))
        if is_sidechain:
            sidechain_lines += 1
            tool_use_count += _count_tool_use(obj)
            continue

        msg_type = obj.get("type")
        if msg_type in ("user", "assistant"):
            if msg_type == "user":
                user_turns += 1
            else:
                assistant_turns += 1
                tool_use_count += _count_tool_use(obj)

    turn_count = user_turns + assistant_turns
    first_ts = _format_timestamp(min(timestamps)) if timestamps else None
    last_ts = _format_timestamp(max(timestamps)) if timestamps else None

    return ParsedSession(
        project=safe_project_name(project),
        cwd=cwd,
        first_ts=first_ts,
        last_ts=last_ts,
        turn_count=turn_count,
        user_turns=user_turns,
        assistant_turns=assistant_turns,
        tool_use_count=tool_use_count,
        sidechain_lines=sidechain_lines,
        line_count=line_count,
        skipped_lines=skipped_lines,
        content_sha256=sha256_hex(data),
        size_bytes=len(data),
    )


def _first_session_id_from_bytes(data: bytes) -> str | None:
    """Return the first valid UUID sessionId found in a jsonl, if any."""
    text = data.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        sid = obj.get("sessionId")
        if isinstance(sid, str) and is_uuid(sid):
            return sid
    return None


def derive_session_id(filename_stem: str, data: bytes) -> str:
    """Determine session identity from filename stem or file contents."""
    if is_uuid(filename_stem):
        return filename_stem.lower()
    from_line = _first_session_id_from_bytes(data)
    if from_line is not None:
        return from_line.lower()
    return sha256_hex(data)[:32]


def _is_unsafe_zip_path(name: str) -> bool:
    """Return True if a zip entry path is unsafe per the spec."""
    if not name:
        return True
    # Backslash absolute path (e.g., \Windows\evil.jsonl) or plain absolute path.
    if name.startswith("/") or name.startswith("\\"):
        return True
    # Drive-letter absolute path (Windows).
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        return True
    # Normalize for segment analysis; split on both slash styles.
    parts = re.split(r"[\/]", name)
    for part in parts:
        if part == "..":
            return True
    return False


def _project_and_stem_from_zip_name(name: str) -> tuple[str, str]:
    """Given a zip entry name, return (project, jsonl_stem) per spec rules."""
    # Zip tools use forward slashes internally; backslashes are unsafe and rejected earlier.
    path = PurePosixPath(name)

    # Strip a leading 'projects/' segment if present.
    parts = path.parts
    if parts and parts[0].lower() == "projects":
        parts = parts[1:]

    if len(parts) < 2:
        return "", path.stem
    # Immediate parent of the jsonl is the project; remaining leading segments are dropped.
    project = parts[-2]
    return project, path.stem


def iter_zip_entries(
    data: bytes,
    max_session_bytes: int,
    max_bundle_bytes: int,
) -> BundleContents:
    """Parse a zip bundle and return its contents, validating size and safety."""
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a valid zip") from exc

    total_uncompressed = sum(info.file_size for info in zf.infolist())
    if total_uncompressed > max_bundle_bytes:
        raise ValueError(f"bundle exceeds {max_bundle_bytes} bytes")

    seen_oversize: list[str] = []
    jsonl_entries: list[ZipEntry] = []
    all_names: dict[str, int] = {}
    for info in zf.infolist():
        name = info.filename
        if info.is_dir():
            continue
        if _is_unsafe_zip_path(name):
            raise ValueError(f"unsafe path: {name}")
        all_names[name] = info.file_size
        if not name.lower().endswith(".jsonl"):
            continue
        if info.file_size > max_session_bytes:
            seen_oversize.append(name)
            continue
        raw = zf.read(info)
        project, stem = _project_and_stem_from_zip_name(name)
        jsonl_entries.append(
            ZipEntry(name=name, project=project, session_id_hint=stem, data=raw)
        )

    if seen_oversize:
        raise ValueError(
            f"entry exceeds {max_session_bytes} bytes: {seen_oversize[0]}"
        )

    return BundleContents(entries=jsonl_entries, all_names=all_names, _zip_bytes=data)
