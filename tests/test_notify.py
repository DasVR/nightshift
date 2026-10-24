from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app
from server.notify import (
    DEFAULT_TIMEOUT,
    MAX_SUMMARY_CHARS,
    NotifyResult,
    NotifyTestRequest,
    build_command,
    format_run_message,
    hermes_bin,
    notify_run,
    one_line,
    resolve_targets,
)


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def stub_run_factory(records: list, returns: list | None = None, exception: Exception | None = None):
    """Return a stand-in for ``subprocess.run`` that records argv and yields canned results."""
    returns = list(returns) if returns is not None else []

    def _run(cmd, **kwargs):
        records.append(cmd)
        if exception is not None:
            raise exception
        if returns:
            return returns.pop(0)
        return FakeCompletedProcess()

    return _run


def test_build_command_exact_argv():
    """build_command emits the exact argv list expected by Hermes."""
    assert build_command("discord:home", "subject here", "body here") == [
        "hermes",
        "send",
        "--to",
        "discord:home",
        "--subject",
        "subject here",
        "body here",
    ]
    assert build_command("photon:x", "s", "b", binary="/opt/hermes") == [
        "/opt/hermes",
        "send",
        "--to",
        "photon:x",
        "--subject",
        "s",
        "b",
    ]


def test_resolve_targets_defaults(tmp_path: Path):
    """Default settings resolve to the configured default target list."""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert resolve_targets(settings) == ["discord:home"]


def test_resolve_targets_appends_photon_and_dedupes(tmp_path: Path):
    """photon_target is appended once and duplicates are removed."""
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        notify_targets=["discord:home", "photon:foo"],
        photon_target="photon:foo",
    )
    assert resolve_targets(settings) == ["discord:home", "photon:foo"]

    settings2 = Settings(
        _env_file=None,
        data_dir=tmp_path,
        notify_targets=["discord:home"],
        photon_target="photon:bar",
    )
    assert resolve_targets(settings2) == ["discord:home", "photon:bar"]


def test_resolve_targets_drops_empty_strings(tmp_path: Path):
    """Empty target strings are dropped before deduplication."""
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        notify_targets=["", "discord:home", "", "photon:foo", ""],
        photon_target="",
    )
    assert resolve_targets(settings) == ["discord:home", "photon:foo"]


def test_resolve_targets_may_return_empty(tmp_path: Path):
    """When no targets are configured the list may legitimately be empty."""
    settings = Settings(_env_file=None, data_dir=tmp_path, notify_targets=[])
    assert resolve_targets(settings) == []


def test_one_line_collapse_and_truncate():
    """one_line normalizes whitespace and truncates with an ellipsis."""
    assert one_line(None) == ""
    assert one_line("") == ""
    assert one_line("  a\n\n  b\t c  ") == "a b c"
    long_text = "x" * (MAX_SUMMARY_CHARS + 10)
    result = one_line(long_text)
    assert len(result) <= MAX_SUMMARY_CHARS
    assert result.endswith("…")
    assert "x" * 10 in result


def test_hermes_bin_defaults(monkeypatch: pytest.MonkeyPatch):
    """hermes_bin prefers the env override, then which, then the bare name."""
    monkeypatch.delenv("NIGHTSHIFT_HERMES_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/hermes" if name == "hermes" else None)
    assert hermes_bin() == "/usr/bin/hermes"

    monkeypatch.setenv("NIGHTSHIFT_HERMES_BIN", "/custom/hermes")
    assert hermes_bin() == "/custom/hermes"

    monkeypatch.delenv("NIGHTSHIFT_HERMES_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert hermes_bin() == "hermes"


def test_format_run_message_from_dict():
    """format_run_message builds subject and body from a dict run record."""
    run = {
        "id": "run-42",
        "session_id": "sess-1",
        "project": "acme",
        "outcome": "DONE",
        "summary": "first line\n\nsecond   line",
    }
    subject, body = format_run_message(run)
    assert subject == "nightshift done: sess-1"
    assert "session: sess-1" in body
    assert "project: acme" in body
    assert "outcome: done" in body
    assert "summary: first line second line" in body
    assert "run: run-42" in body


def test_format_run_message_missing_fields():
    """Missing fields fall back to sensible defaults."""
    subject, body = format_run_message({})
    assert subject == "nightshift unknown: unknown"
    assert "session: unknown" in body
    assert "project: unknown" in body
    assert "outcome: unknown" in body
    assert "summary: " in body
    assert "run:" not in body


def test_format_run_message_from_dataclass():
    """format_run_message works with dataclass/object runs, not only dicts."""

    @dataclass
    class Run:
        id: str
        session: str
        project: str
        status: str
        result: str

    run = Run(id="r1", session="s1", project="p1", status="FAILED", result="boom")
    subject, body = format_run_message(run)
    assert subject == "nightshift failed: s1"
    assert "project: p1" in body
    assert "outcome: failed" in body
    assert "summary: boom" in body
    assert "run: r1" in body


def test_notify_run_success_records_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """notify_run runs the expected Hermes argv for each target on success."""
    records: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", stub_run_factory(records))

    settings = Settings(_env_file=None, data_dir=tmp_path)
    run = {"id": "r1", "session_id": "s1", "project": "p1", "outcome": "done", "summary": "ok"}
    result = notify_run(run, settings, binary="hermes")

    assert result.ok is True
    assert result.targets == ["discord:home"]
    assert result.failed == []
    assert len(result.commands) == 1
    expected = build_command("discord:home", *format_run_message(run)[:2])
    assert result.commands[0] == expected
    assert records == [expected]
    assert "sent to 1 target(s)" in result.detail


def test_notify_run_nonzero_returncode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A nonzero Hermes exit marks the target failed and keeps notify_run safe."""
    records: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        stub_run_factory(records, returns=[FakeCompletedProcess(returncode=1, stderr="err\nlast line")]),
    )

    settings = Settings(_env_file=None, data_dir=tmp_path)
    run = {"session_id": "s1", "project": "p1", "outcome": "failed", "summary": "bad"}
    result = notify_run(run, settings)

    assert result.ok is False
    assert result.targets == ["discord:home"]
    assert result.failed == ["discord:home"]
    assert "exit 1" in result.detail
    assert "last line" in result.detail


def test_notify_run_file_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Hermes not installed is caught as an OSError and reported, not re-raised."""
    records: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        stub_run_factory(records, exception=FileNotFoundError("hermes not found")),
    )

    settings = Settings(_env_file=None, data_dir=tmp_path)
    run = {"session_id": "s1", "project": "p1", "outcome": "done"}
    result = notify_run(run, settings)

    assert result.ok is False
    assert result.failed == ["discord:home"]
    assert "hermes not found" in result.detail


def test_notify_run_no_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """With no targets configured notify_run returns early without calling subprocess."""
    records: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", stub_run_factory(records))

    settings = Settings(_env_file=None, data_dir=tmp_path, notify_targets=[])
    run = {"session_id": "s1", "project": "p1", "outcome": "done"}
    result = notify_run(run, settings)

    assert result.ok is False
    assert result.targets == []
    assert result.failed == []
    assert result.commands == []
    assert "no notify targets configured" in result.detail
    assert records == []


def test_notify_test_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """POST /notify/test returns 200 and reports success when Hermes succeeds."""
    records: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", stub_run_factory(records))

    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    response = TestClient(app).post("/notify/test")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "discord:home" in body["targets"]
    assert body["failed"] == []
    assert "subject" in body
    assert "body" in body
    assert body["subject"] == "nightshift done: test-session"
    assert "test" in body["body"]


def test_notify_test_delivery_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """POST /notify/test still returns HTTP 200 with ok=false on delivery failure."""
    monkeypatch.setattr(
        "subprocess.run",
        stub_run_factory([], returns=[FakeCompletedProcess(returncode=1, stderr="fail")]),
    )

    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    response = TestClient(app).post("/notify/test")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "discord:home" in body["failed"]


def test_notify_result_to_dict():
    """NotifyResult.to_dict exposes plain JSON-ready values."""
    result = NotifyResult(
        ok=True,
        targets=["a"],
        failed=[],
        detail="sent to 1 target(s)",
        commands=[["hermes", "send", "--to", "a", "--subject", "s", "b"]],
    )
    assert result.to_dict() == {
        "ok": True,
        "targets": ["a"],
        "failed": [],
        "detail": "sent to 1 target(s)",
        "commands": [["hermes", "send", "--to", "a", "--subject", "s", "b"]],
    }


def test_notify_test_request_defaults():
    """NotifyTestRequest provides the documented default payload."""
    req = NotifyTestRequest()
    assert req.session_id == "test-session"
    assert req.project == "nightshift-test"
    assert req.outcome == "done"
    assert req.summary == "fake notification from POST /notify/test"


def test_notify_run_uses_binary_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The binary= override changes the emitted command."""
    records: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", stub_run_factory(records))

    settings = Settings(_env_file=None, data_dir=tmp_path)
    run = {"session_id": "s1", "project": "p1", "outcome": "done"}
    notify_run(run, settings, binary="/opt/hermes")

    assert records[0][0] == "/opt/hermes"


def test_notify_run_timeout_passed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """notify_run forwards the timeout and DEVNULL stdin to subprocess.run."""
    import subprocess

    calls: list[dict] = []

    def _run(cmd, **kwargs):
        calls.append(kwargs)
        return FakeCompletedProcess()

    monkeypatch.setattr("subprocess.run", _run)

    settings = Settings(_env_file=None, data_dir=tmp_path)
    run = {"session_id": "s1", "project": "p1", "outcome": "done"}
    notify_run(run, settings, timeout=3.5)

    assert calls[0]["timeout"] == 3.5
    assert calls[0]["stdin"] == subprocess.DEVNULL


def test_notify_failure_does_not_block_run_row_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A Hermes failure must not prevent the already-persisted run row from updating."""
    stored = {
        "id": "r1",
        "session_id": "s1",
        "project": "p1",
        "status": "done",
        "summary": "ok",
    }
    runs = {"r1": stored}

    records: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        stub_run_factory(records, exception=FileNotFoundError("hermes not found")),
    )

    settings = Settings(_env_file=None, data_dir=tmp_path)

    try:
        result = notify_run(runs["r1"], settings)
    except Exception as exc:
        pytest.fail(f"notify_run raised {type(exc).__name__}: {exc}")

    assert result.ok is False
    assert result.failed == ["discord:home"]
    assert runs["r1"] == {
        "id": "r1",
        "session_id": "s1",
        "project": "p1",
        "status": "done",
        "summary": "ok",
    }


def test_notify_run_two_targets_one_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """With two targets, one success and one failure yields ok=False and records both commands."""
    records: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        stub_run_factory(
            records,
            returns=[
                FakeCompletedProcess(returncode=0),
                FakeCompletedProcess(returncode=1, stderr="photon failed"),
            ],
        ),
    )

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        notify_targets=["discord:home"],
        photon_target="photon:dev",
    )
    run = {"session_id": "s1", "project": "p1", "outcome": "done"}
    result = notify_run(run, settings, binary="hermes")

    assert result.ok is False
    assert result.failed == ["photon:dev"]
    assert result.targets == ["discord:home", "photon:dev"]
    assert len(result.commands) == 2
    assert result.commands[0] == build_command(
        "discord:home", *format_run_message(run)[:2]
    )
    assert result.commands[1] == build_command(
        "photon:dev", *format_run_message(run)[:2]
    )
    assert records == result.commands
    assert "photon:dev" in result.detail
