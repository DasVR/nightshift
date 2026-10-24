from __future__ import annotations

import hashlib
import json
import socket
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath

from collector.discovery import SessionCandidate, is_uuid

COLLECTOR_VERSION = "0.1.0"


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def _looks_like_windows_path(path_str: str) -> bool:
    """Return True if *path_str* appears to use Windows path syntax."""
    if "\\" in path_str:
        return True
    return len(path_str) >= 2 and path_str[1] == ":" and path_str[0].isalpha()


def zip_entry_name_from_path_string(path_str: str) -> str | None:
    """Derive a POSIX zip entry name from a POSIX or Windows path string.

    Returns ``<project>/<session_id>.jsonl`` where *project* is the immediate
    parent directory of the ``.jsonl`` file. Works on Linux for Windows-shaped
    input. Returns ``None`` if the path is not a UUID-stem jsonl file.
    """
    if _looks_like_windows_path(path_str):
        pure = PureWindowsPath(path_str)
    else:
        pure = PurePosixPath(path_str)

    if pure.suffix.lower() != ".jsonl":
        return None
    stem = pure.stem
    if not is_uuid(stem):
        return None
    project = pure.parent.name
    if not project:
        return None
    return f"{project}/{stem.lower()}.jsonl"


@dataclass(frozen=True)
class ManifestSession:
    """One row in the ingest manifest."""

    path: str
    session_id: str
    project: str
    sha256: str
    size: int


@dataclass(frozen=True)
class BundleResult:
    """Built zip bytes plus manifest dict and session rows."""

    zip_bytes: bytes
    manifest: dict[str, object]
    sessions: list[ManifestSession]


def _read_session_bytes(candidate: SessionCandidate) -> bytes:
    """Read raw session file bytes."""
    return candidate.path.read_bytes()


def build_bundle(candidates: list[SessionCandidate]) -> BundleResult:
    """Build an in-memory zip and manifest for the given sessions."""
    host = socket.gethostname()
    manifest_rows: list[ManifestSession] = []

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for candidate in candidates:
            data = _read_session_bytes(candidate)
            entry_name = candidate.zip_entry_name
            zf.writestr(entry_name, data)
            manifest_rows.append(
                ManifestSession(
                    path=entry_name,
                    session_id=candidate.session_id,
                    project=candidate.project,
                    sha256=sha256_hex(data),
                    size=len(data),
                )
            )

    manifest_rows.sort(key=lambda row: row.path)
    sessions_json = [
        {
            "path": row.path,
            "session_id": row.session_id,
            "project": row.project,
            "sha256": row.sha256,
            "size": row.size,
        }
        for row in manifest_rows
    ]
    manifest: dict[str, object] = {
        "collector_version": COLLECTOR_VERSION,
        "host": host,
        "sessions": sessions_json,
    }
    return BundleResult(
        zip_bytes=buf.getvalue(),
        manifest=manifest,
        sessions=manifest_rows,
    )


def manifest_json_bytes(manifest: dict[str, object]) -> bytes:
    """Serialize manifest to UTF-8 JSON with stable key order."""
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


def bundle_output_basename(host: str | None = None) -> str:
    """Return ``<host>-<UTC timestamp>`` for bundle/manifest filenames."""
    label = host or "bundle"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{label}-{ts}"


def write_bundle_artifacts(
    out_dir: Path,
    zip_bytes: bytes,
    manifest: dict[str, object],
    *,
    host: str | None = None,
) -> tuple[Path, Path]:
    """Write zip and manifest.json under *out_dir*; return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = bundle_output_basename(host)
    zip_path = out_dir / f"{base}.zip"
    manifest_path = out_dir / "manifest.json"
    zip_path.write_bytes(zip_bytes)
    manifest_path.write_bytes(manifest_json_bytes(manifest))
    return zip_path, manifest_path
