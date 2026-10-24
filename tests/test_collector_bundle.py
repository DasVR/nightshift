from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from collector.bundle import (
    build_bundle,
    sha256_hex,
    zip_entry_name_from_path_string,
)
from collector.discovery import SessionCandidate

SESSION = "11111111-2222-3333-4444-555555555555"


def _minimal_jsonl(session_id: str) -> bytes:
    text = (
        f'{{"sessionId":"{session_id}","type":"user","timestamp":"2024-01-01T10:00:00Z",'
        f'"message":{{"content":"hi"}}}}\n'
        f'{{"sessionId":"{session_id}","type":"assistant","timestamp":"2024-01-01T10:01:00Z",'
        f'"message":{{"content":"ok"}}}}\n'
    )
    return text.encode("utf-8")


def _candidate(tmp_path: Path, project: str, session_id: str) -> SessionCandidate:
    proj_dir = tmp_path / project
    proj_dir.mkdir(parents=True)
    path = proj_dir / f"{session_id}.jsonl"
    path.write_bytes(_minimal_jsonl(session_id))
    return SessionCandidate(
        path=path,
        session_id=session_id.lower(),
        project=project,
        mtime=datetime.now(timezone.utc),
    )


def test_zip_entry_name_windows_and_unc() -> None:
    """Windows and UNC path strings normalize to POSIX zip entries on Linux."""
    win = r"C:\Users\das\.claude\projects\C--Users-das\11111111-2222-3333-4444-555555555555.jsonl"
    assert zip_entry_name_from_path_string(win) == (
        "C--Users-das/11111111-2222-3333-4444-555555555555.jsonl"
    )
    alt = r"D:\claude\projects\proj-two\22222222-3333-4444-5555-666666666666.jsonl"
    assert zip_entry_name_from_path_string(alt) == (
        "proj-two/22222222-3333-4444-5555-666666666666.jsonl"
    )
    unc = r"\\server\share\projects\encoded-proj\11111111-2222-3333-4444-555555555555.jsonl"
    assert zip_entry_name_from_path_string(unc) == (
        "encoded-proj/11111111-2222-3333-4444-555555555555.jsonl"
    )


def test_zip_entry_name_posix() -> None:
    """POSIX paths normalize the same way."""
    posix = "/home/das/.claude/projects/my-proj/11111111-2222-3333-4444-555555555555.jsonl"
    assert zip_entry_name_from_path_string(posix) == (
        "my-proj/11111111-2222-3333-4444-555555555555.jsonl"
    )


def test_zip_entry_name_rejects_non_uuid() -> None:
    """Non-UUID jsonl stems return None."""
    assert zip_entry_name_from_path_string("/a/not-uuid.jsonl") is None


def test_build_bundle_zip_and_manifest(tmp_path: Path) -> None:
    """Zip entries, sizes, and manifest sha256 match file bytes."""
    c1 = _candidate(tmp_path, "proj-a", SESSION)
    c2 = _candidate(
        tmp_path,
        "proj-b",
        "22222222-3333-4444-5555-666666666666",
    )
    result = build_bundle([c1, c2])
    assert result.zip_bytes
    assert result.manifest["collector_version"] == "0.1.0"
    assert isinstance(result.manifest["host"], str)

    with zipfile.ZipFile(BytesIO(result.zip_bytes)) as zf:
        names = zf.namelist()
        assert f"proj-a/{SESSION}.jsonl" in names
        assert "proj-b/22222222-3333-4444-5555-666666666666.jsonl" in names
        for name in names:
            assert "\\" not in name
            assert not name.startswith("/")
            assert ".." not in name.split("/")

    sessions = result.manifest["sessions"]
    assert isinstance(sessions, list)
    paths = [row["path"] for row in sessions]
    assert paths == sorted(paths)

    for row in sessions:
        data = c1.path.read_bytes() if row["project"] == "proj-a" else (
            tmp_path / "proj-b" / "22222222-3333-4444-5555-666666666666.jsonl"
        ).read_bytes()
        assert row["sha256"] == sha256_hex(data)
        assert row["size"] == len(data)


def test_bundle_from_windows_shaped_names_only(tmp_path: Path) -> None:
    """Building from on-disk encoded project dirs yields safe zip paths."""
    project = "C--Users-das"
    proj_dir = tmp_path / project
    proj_dir.mkdir()
    path = proj_dir / f"{SESSION}.jsonl"
    path.write_bytes(_minimal_jsonl(SESSION))
    candidate = SessionCandidate(
        path=path,
        session_id=SESSION,
        project=project,
        mtime=datetime.now(timezone.utc),
    )
    result = build_bundle([candidate])
    with zipfile.ZipFile(BytesIO(result.zip_bytes)) as zf:
        assert zf.namelist() == [f"{project}/{SESSION}.jsonl"]


def test_manifest_json_roundtrip(tmp_path: Path) -> None:
    """Manifest serializes to valid JSON."""
    c = _candidate(tmp_path, "p", SESSION)
    result = build_bundle([c])
    parsed = json.loads(json.dumps(result.manifest))
    assert parsed["sessions"][0]["session_id"] == SESSION
