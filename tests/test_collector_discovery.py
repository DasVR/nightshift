from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

from collector.discovery import SessionCandidate, discover_sessions

SESSION_A = "11111111-2222-3333-4444-555555555555"
SESSION_B = "22222222-3333-4444-5555-666666666666"


def _minimal_jsonl(session_id: str) -> str:
    return (
        f'{{"sessionId":"{session_id}","type":"user","timestamp":"2024-01-01T10:00:00Z",'
        f'"message":{{"content":"hi"}}}}\n'
        f'{{"sessionId":"{session_id}","type":"assistant","timestamp":"2024-01-01T10:01:00Z",'
        f'"message":{{"content":"ok"}}}}\n'
    )


def _write_tree(root: Path) -> dict[str, Path]:
    """Create a small Claude projects tree; return session paths by id."""
    proj_a = root / "proj-alpha"
    proj_b = root / "proj-beta"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)
    path_a = proj_a / f"{SESSION_A}.jsonl"
    path_b = proj_b / f"{SESSION_B}.jsonl"
    path_a.write_text(_minimal_jsonl(SESSION_A), encoding="utf-8")
    path_b.write_text(_minimal_jsonl(SESSION_B), encoding="utf-8")
    (proj_a / "notes.txt").write_text("ignore", encoding="utf-8")
    (proj_a / "not-a-uuid.jsonl").write_text("{}", encoding="utf-8")
    return {SESSION_A: path_a, SESSION_B: path_b}


def test_discover_default_root_structure(tmp_path: Path) -> None:
    """Discovery finds UUID jsonl files and derives project from parent dir."""
    paths = _write_tree(tmp_path)
    found = discover_sessions(tmp_path)
    assert len(found) == 2
    by_id = {s.session_id: s for s in found}
    assert by_id[SESSION_A].project == "proj-alpha"
    assert by_id[SESSION_A].path == paths[SESSION_A]
    assert by_id[SESSION_B].project == "proj-beta"
    assert by_id[SESSION_A].zip_entry_name == f"proj-alpha/{SESSION_A}.jsonl"


def test_discover_nested_project_dir(tmp_path: Path) -> None:
    """Immediate parent of the jsonl is the project name, even when nested."""
    nested = tmp_path / "outer" / "C--Users-das"
    nested.mkdir(parents=True)
    sid = SESSION_A
    path = nested / f"{sid}.jsonl"
    path.write_text(_minimal_jsonl(sid), encoding="utf-8")
    found = discover_sessions(tmp_path)
    assert len(found) == 1
    assert found[0].project == "C--Users-das"


def test_discover_since_filters_by_mtime(tmp_path: Path) -> None:
    """--since keeps sessions whose mtime is on/after the UTC date."""
    _write_tree(tmp_path)
    path_a = tmp_path / "proj-alpha" / f"{SESSION_A}.jsonl"
    path_b = tmp_path / "proj-beta" / f"{SESSION_B}.jsonl"
    old = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    new = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path_a, (old, old))
    os.utime(path_b, (new, new))

    found = discover_sessions(tmp_path, since=date(2026, 10, 1))
    assert len(found) == 1
    assert found[0].session_id == SESSION_B


def test_discover_project_glob(tmp_path: Path) -> None:
    """Project fnmatch globs filter sessions."""
    _write_tree(tmp_path)
    found = discover_sessions(tmp_path, project_globs=["proj-al*"])
    assert len(found) == 1
    assert found[0].project == "proj-alpha"


def test_discover_empty_when_no_matches(tmp_path: Path) -> None:
    """No error when directory exists but nothing matches."""
    _write_tree(tmp_path)
    found = discover_sessions(tmp_path, project_globs=["nonexistent-*"])
    assert found == []


def test_discover_missing_dir_returns_empty(tmp_path: Path) -> None:
    """Missing claude dir yields empty list."""
    assert discover_sessions(tmp_path / "nope") == []


def test_discover_sorted_deterministically(tmp_path: Path) -> None:
    """Results are sorted by (project, session_id)."""
    _write_tree(tmp_path)
    found = discover_sessions(tmp_path)
    keys = [(s.project, s.session_id) for s in found]
    assert keys == sorted(keys)


def test_session_candidate_types() -> None:
    """SessionCandidate exposes expected zip entry naming."""
    c = SessionCandidate(
        path=Path("/tmp/x/y/u.jsonl"),
        session_id="a",
        project="y",
        mtime=datetime.now(timezone.utc),
    )
    assert c.zip_entry_name == "y/a.jsonl"
