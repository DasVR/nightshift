from __future__ import annotations

from pathlib import Path

import pytest

from server.ingest import derive_session_id, parse_session_bytes


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_valid_session() -> None:
    """Counters, timestamps, cwd and project are extracted correctly."""
    data = (FIXTURES / "valid_session.jsonl").read_bytes()
    parsed = parse_session_bytes(data, "project-a")

    assert parsed.project == "project-a"
    assert parsed.cwd == "/home/test/proj"
    assert parsed.first_ts == "2024-01-01T10:00:00Z"
    assert parsed.last_ts == "2024-01-01T10:02:00Z"
    assert parsed.turn_count == 3
    assert parsed.user_turns == 1
    assert parsed.assistant_turns == 2
    assert parsed.tool_use_count == 1
    assert parsed.sidechain_lines == 0
    assert parsed.line_count == 3
    assert parsed.skipped_lines == 0
    assert parsed.size_bytes == len(data)


def test_parse_malformed_lines_tolerated() -> None:
    """Non-JSON and non-object lines are counted as skipped but do not raise."""
    data = (FIXTURES / "malformed_session.jsonl").read_bytes()
    parsed = parse_session_bytes(data, "project-x")

    assert parsed.turn_count == 2  # one user + one assistant
    assert parsed.user_turns == 1
    assert parsed.assistant_turns == 1
    assert parsed.skipped_lines == 2
    assert parsed.line_count == 4  # excluding trailing blank line


def test_parse_non_utf8_bytes_tolerated() -> None:
    """Invalid UTF-8 is replaced and parsing continues without raising."""
    bad = b'{"type":"user","timestamp":"2024-01-01T10:00:00Z"}\n\xff\xfe\n'
    parsed = parse_session_bytes(bad, "project-x")
    assert parsed.user_turns == 1
    assert parsed.skipped_lines == 1


def test_parse_empty_session() -> None:
    """A file with no user/assistant turns produces turn_count == 0."""
    data = (FIXTURES / "empty_session.jsonl").read_bytes()
    parsed = parse_session_bytes(data, "project-y")

    assert parsed.turn_count == 0
    assert parsed.line_count == 1
    assert parsed.skipped_lines == 0


def test_derive_session_id_from_uuid_stem() -> None:
    """Filename UUID stem becomes the session id."""
    data = b'{"sessionId":"not-a-uuid"}'
    assert derive_session_id("11111111-2222-3333-4444-555555555555", data) == "11111111-2222-3333-4444-555555555555"


def test_derive_session_id_from_line_fallback() -> None:
    """Non-UUID stem falls back to the first valid UUID sessionId line."""
    data = b'{"sessionId":"22222222-2222-3333-4444-555555555555"}\n'
    assert derive_session_id("not-uuid", data) == "22222222-2222-3333-4444-555555555555"


def test_derive_session_id_sha256_fallback() -> None:
    """No UUID anywhere falls back to sha256 prefix."""
    data = b'{"type":"user"}'
    sid = derive_session_id("not-uuid", data)
    assert len(sid) == 32
    assert all(c in "0123456789abcdef" for c in sid)


def test_mixed_precision_timestamps_ordered() -> None:
    """Datetime-aware min/max avoids lexicographic mis-ordering of mixed precision."""
    data = (
        b'{"type":"user","timestamp":"2024-01-01T10:00:00Z"}\n'
        b'{"type":"assistant","timestamp":"2024-01-01T10:00:00.500000Z"}\n'
        b'{"type":"user","timestamp":"2024-01-01T09:59:59.900000Z"}\n'
    )
    parsed = parse_session_bytes(data, "project-z")
    assert parsed.first_ts == "2024-01-01T09:59:59.900000Z"
    assert parsed.last_ts == "2024-01-01T10:00:00.500000Z"


def test_offset_timestamp_normalized_to_utc() -> None:
    """An offset-bearing timestamp is converted to the canonical UTC Z string."""
    data = b'{"type":"user","timestamp":"2024-01-01T12:00:00+02:00"}\n'
    parsed = parse_session_bytes(data, "project-z")
    assert parsed.first_ts == "2024-01-01T10:00:00Z"
    assert parsed.last_ts == "2024-01-01T10:00:00Z"
