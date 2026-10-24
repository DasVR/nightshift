from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import pytest

from collector.cli import main

SESSION = "11111111-2222-3333-4444-555555555555"


def _minimal_jsonl(session_id: str) -> str:
    return (
        f'{{"sessionId":"{session_id}","type":"user","timestamp":"2024-01-01T10:00:00Z",'
        f'"message":{{"content":"hi"}}}}\n'
        f'{{"sessionId":"{session_id}","type":"assistant","timestamp":"2024-01-01T10:01:00Z",'
        f'"message":{{"content":"ok"}}}}\n'
    )


def _fixture_dir(tmp_path: Path) -> Path:
    proj = tmp_path / "proj-one"
    proj.mkdir()
    (proj / f"{SESSION}.jsonl").write_text(_minimal_jsonl(SESSION), encoding="utf-8")
    return tmp_path


def test_cli_dry_run_no_http(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Dry-run prints manifest and never opens a URL."""
    root = _fixture_dir(tmp_path)

    def fail_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("urlopen must not be called in dry-run")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(urllib.request, "urlopen", fail_open)
    try:
        code = main(["--claude-dir", str(root), "--dry-run"])
    finally:
        monkeypatch.undo()

    assert code == 0
    out = capsys.readouterr().out
    parsed = json.loads(out.split("\n\n")[0] if "\n\n" in out else out.split("Target:")[0].strip())
    assert parsed["collector_version"] == "0.1.0"
    assert parsed["sessions"][0]["session_id"] == SESSION
    assert "Target:" in out


def test_cli_empty_selection_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Zero sessions prints a message and exits 0 without HTTP."""
    root = tmp_path / "empty"
    root.mkdir()

    def fail_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("urlopen must not be called when empty")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(urllib.request, "urlopen", fail_open)
    try:
        code = main(["--claude-dir", str(root), "--dry-run"])
    finally:
        monkeypatch.undo()

    assert code == 0
    assert "No matching sessions" in capsys.readouterr().err


def test_cli_post_captures_multipart(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """POST uses /sessions/ingest with bundle and manifest fields."""
    root = _fixture_dir(tmp_path)
    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self) -> None:
            self._body = b'{"ok": true, "counts": {"created": 1}}'

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getcode(self) -> int:
            return 200

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(request: urllib.request.Request, **_kwargs: object) -> FakeResponse:
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["body"] = request.data
        return FakeResponse()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    try:
        code = main(
            [
                "--claude-dir",
                str(root),
                "--server",
                "http://127.0.0.1:8788",
            ]
        )
    finally:
        monkeypatch.undo()

    assert code == 0
    assert captured["url"] == "http://127.0.0.1:8788/sessions/ingest"
    assert captured["method"] == "POST"
    body = captured["body"]
    assert isinstance(body, bytes)
    text = body.decode("utf-8", errors="replace")
    assert 'name="bundle"' in text
    assert 'name="manifest"' in text
    assert "session-bundle.zip" in text
    assert "manifest.json" in text
    out = capsys.readouterr().out
    assert '"ok": true' in out or '"ok":true' in out.replace(" ", "")


def test_cli_non_2xx_exit_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Non-2xx response body goes to stderr and exit code is 1."""
    root = _fixture_dir(tmp_path)

    def fake_urlopen(_request: urllib.request.Request, **_kwargs: object) -> object:
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:8788/sessions/ingest",
            code=422,
            msg="Unprocessable",
            hdrs=None,
            fp=StringIO('{"detail":"bad manifest"}'),
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    try:
        code = main(["--claude-dir", str(root)])
    finally:
        monkeypatch.undo()

    assert code == 1
    assert "bad manifest" in capsys.readouterr().err


def test_cli_server_env_override(tmp_path: Path) -> None:
    """NIGHTSHIFT_SERVER_URL is used when --server is omitted."""
    root = _fixture_dir(tmp_path)
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getcode(self) -> int:
            return 200

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: urllib.request.Request, **_kwargs: object) -> FakeResponse:
        captured["url"] = request.full_url
        return FakeResponse()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("NIGHTSHIFT_SERVER_URL", "http://custom:9999")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    try:
        main(["--claude-dir", str(root)])
    finally:
        monkeypatch.undo()

    assert captured["url"] == "http://custom:9999/sessions/ingest"
