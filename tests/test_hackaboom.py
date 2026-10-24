from __future__ import annotations

import json
from pathlib import Path

from server.config import Settings
from server.hackaboom import (
    live_mirror_status,
    prepare_mirror,
    probe_bridge,
    resolve_plugin_dir,
    telemetry_enabled,
)
from server.runs import ClaudeTmuxRunner, RunRecord


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        claude_projects_dir=tmp_path / "projects",
        hackaboom_prepare=True,
        _env_file=None,
        **kwargs,
    )


def test_probe_detects_nightshift_collision() -> None:
    """A health body from this server is not the editor bridge."""

    def opener(url: str, timeout: float = 1.5) -> _Resp:
        assert "8788" in url
        return _Resp({"status": "ok", "service": "nightshift", "version": "0.1.0"})

    result = probe_bridge(8788, opener=opener)
    assert result["ok"] is False
    assert result["collision"] is True


def test_probe_accepts_editor_health() -> None:
    def opener(url: str, timeout: float = 1.5) -> _Resp:
        return _Resp({"ok": True, "service": "hackaboom", "version": "1.4.0"})

    result = probe_bridge(8788, opener=opener)
    assert result["ok"] is True
    assert result["version"] == "1.4.0"


def test_resolve_plugin_dir_home_tree(tmp_path: Path) -> None:
    plugin = tmp_path / "hackaboom" / "plugin"
    marker = plugin / ".claude-plugin"
    marker.mkdir(parents=True)
    (marker / "plugin.json").write_text("{}", encoding="utf-8")
    settings = _settings(tmp_path)
    assert resolve_plugin_dir(settings, tmp_path) == plugin


def test_telemetry_flag_from_credentials(tmp_path: Path) -> None:
    (tmp_path / ".hackaboom.json").write_text(
        json.dumps({"token": "abc", "port": 8788, "telemetry": {"enabled": True}}),
        encoding="utf-8",
    )
    assert telemetry_enabled(tmp_path) is True


def test_prepare_ready_does_not_spawn(tmp_path: Path) -> None:
    started = []

    def opener(url: str, timeout: float = 1.5) -> _Resp:
        return _Resp({"ok": True, "service": "hackaboom", "version": "1.4.0"})

    def run(*args: object, **kwargs: object) -> None:
        started.append(args)
        raise AssertionError("should not start headless when the bridge is up")

    status = prepare_mirror(
        _settings(tmp_path),
        cwd="/tmp/proj",
        home=tmp_path,
        opener=opener,
        run=run,
        which=lambda _: None,
    )
    assert status.status == "ready"
    assert started == []
    assert status.panel_url == "http://127.0.0.1:8788/"


def test_prepare_starts_when_down(tmp_path: Path) -> None:
    calls = {"n": 0}

    def opener(url: str, timeout: float = 1.5) -> _Resp:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("down")
        return _Resp({"ok": True, "service": "hackaboom", "version": "1.4.0"})

    class Done:
        returncode = 0
        stdout = "started"
        stderr = ""

    status = prepare_mirror(
        _settings(tmp_path),
        cwd="/tmp/proj",
        home=tmp_path,
        opener=opener,
        run=lambda *a, **k: Done(),
        which=lambda _: "/usr/bin/hackaboom",
    )
    assert status.status == "started"
    assert status.version == "1.4.0"


def test_live_mirror_does_not_start(tmp_path: Path) -> None:
    def opener(url: str, timeout: float = 1.5) -> _Resp:
        raise TimeoutError("down")

    status = live_mirror_status(_settings(tmp_path), home=tmp_path, opener=opener)
    assert status.status == "down"


def test_claude_argv_includes_plugin_dir(tmp_path: Path) -> None:
    plugin = tmp_path / "plug"
    plugin.mkdir()
    settings = _settings(tmp_path, hackaboom_plugin_dir=plugin)
    runner = ClaudeTmuxRunner(settings)
    record = RunRecord(
        id="run-1",
        session_id="11111111-2222-3333-4444-555555555555",
        instruction="continue",
        cwd="/tmp/proj",
        project_dir="/tmp/proj",
        status="queued",
        created_at="t",
        log_path=str(tmp_path / "r.log"),
    )
    argv = runner.claude_argv(record)
    assert "--plugin-dir" in argv
    assert str(plugin) in argv
