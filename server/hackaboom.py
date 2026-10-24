"""Probe and prepare the local Hackaboom editor mirror.

Resume runs still type at human pace and can emit heartbeats, but only if the
editor bridge is up and the Claude plugin is loaded. This module is best-effort:
a down bridge never fails the run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen

from server.config import Settings

UrlOpen = Callable[..., object]
Which = Callable[[str], str | None]
RunCmd = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class MirrorStatus:
    """Live state of the editor bridge on this box."""

    status: str
    detail: str
    port: int
    version: str | None = None
    panel_url: str | None = None
    telemetry_enabled: bool | None = None
    plugin_dir: str | None = None
    collision: bool = False


def credentials_path(home: Path) -> Path:
    """Path the editor writes bridge credentials into."""
    return home / ".hackaboom.json"


def read_credentials(home: Path) -> dict:
    """Load ~/.hackaboom.json; missing or junk files yield an empty dict."""
    path = credentials_path(home)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def telemetry_enabled(home: Path) -> bool | None:
    """Whether the optional heartbeat sink is on. None if we cannot tell."""
    creds = read_credentials(home)
    telemetry = creds.get("telemetry")
    if not isinstance(telemetry, dict):
        return None
    enabled = telemetry.get("enabled")
    if isinstance(enabled, bool):
        return enabled
    return None


def panel_url(port: int, home: Path) -> str:
    """Control-panel URL, with token when the editor has written one."""
    creds = read_credentials(home)
    url = f"http://127.0.0.1:{port}/"
    token = creds.get("token")
    if isinstance(token, str) and token.strip():
        return f"{url}?token={token.strip()}"
    return url


def resolve_plugin_dir(settings: Settings, home: Path) -> Path | None:
    """Configured plugin dir, or ~/hackaboom/plugin when that tree exists."""
    configured = settings.hackaboom_plugin_dir
    if configured is not None:
        return configured if configured.is_dir() else None
    candidate = home / "hackaboom" / "plugin"
    marker = candidate / ".claude-plugin" / "plugin.json"
    return candidate if marker.is_file() else None


def probe_bridge(
    port: int,
    timeout: float = 1.5,
    opener: UrlOpen = urlopen,
) -> dict:
    """GET the editor health endpoint. Distinguishes a Nightshift collision."""
    url = f"http://127.0.0.1:{port}/health"
    try:
        with opener(url, timeout=timeout) as resp:
            raw = resp.read()
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "collision": False}

    if not isinstance(body, dict):
        return {"ok": False, "error": "health response was not an object", "collision": False}

    service = body.get("service")
    if service == "nightshift":
        return {
            "ok": False,
            "error": f"port {port} is Nightshift, not the editor bridge",
            "collision": True,
        }
    if body.get("ok") is True or service == "hackaboom":
        version = body.get("version")
        return {
            "ok": True,
            "version": version if isinstance(version, str) else None,
            "collision": False,
        }
    return {"ok": False, "error": f"unexpected health body from port {port}", "collision": False}


def start_headless(
    settings: Settings,
    cwd: str,
    run: RunCmd = subprocess.run,
    which: Which = shutil.which,
) -> tuple[bool, str]:
    """Start `hackaboom headless` for this workspace. Returns (started, detail)."""
    binary = which(settings.hackaboom_bin)
    if not binary:
        return False, f"{settings.hackaboom_bin} is not on PATH"
    argv = [
        binary,
        "headless",
        "start",
        "--workspace",
        cwd,
        "--port",
        str(settings.hackaboom_port),
        "--wait",
        "25",
    ]
    try:
        completed = run(
            argv,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    text = (completed.stdout or "") + (completed.stderr or "")
    text = text.strip() or f"exit {completed.returncode}"
    if completed.returncode == 0:
        return True, text
    if completed.returncode == 1 and "already running" in text.lower():
        return True, text
    return False, text


def prepare_mirror(
    settings: Settings,
    cwd: str,
    home: Path | None = None,
    opener: UrlOpen = urlopen,
    run: RunCmd = subprocess.run,
    which: Which = shutil.which,
) -> MirrorStatus:
    """Ensure the editor bridge is up for this cwd. Never raises."""
    home = home or Path.home()
    port = settings.hackaboom_port
    plugin = resolve_plugin_dir(settings, home)
    plugin_s = str(plugin) if plugin else None
    telemetry = telemetry_enabled(home)
    url = panel_url(port, home)

    if not settings.hackaboom_prepare:
        return MirrorStatus(
            status="skipped",
            detail="mirror prepare disabled",
            port=port,
            panel_url=url,
            telemetry_enabled=telemetry,
            plugin_dir=plugin_s,
        )

    probed = probe_bridge(port, opener=opener)
    if probed.get("ok"):
        return MirrorStatus(
            status="ready",
            detail="editor bridge is listening",
            port=port,
            version=probed.get("version"),
            panel_url=url,
            telemetry_enabled=telemetry,
            plugin_dir=plugin_s,
        )
    if probed.get("collision"):
        return MirrorStatus(
            status="collision",
            detail=str(probed.get("error") or "port collision"),
            port=port,
            panel_url=None,
            telemetry_enabled=telemetry,
            plugin_dir=plugin_s,
            collision=True,
        )

    started, detail = start_headless(settings, cwd, run=run, which=which)
    probed_again = probe_bridge(port, opener=opener)
    if probed_again.get("ok"):
        return MirrorStatus(
            status="started" if started else "ready",
            detail=detail if started else "editor bridge is listening",
            port=port,
            version=probed_again.get("version"),
            panel_url=url,
            telemetry_enabled=telemetry,
            plugin_dir=plugin_s,
        )
    return MirrorStatus(
        status="down",
        detail=detail if not started else str(probed_again.get("error") or detail),
        port=port,
        panel_url=url,
        telemetry_enabled=telemetry,
        plugin_dir=plugin_s,
    )


def live_mirror_status(
    settings: Settings,
    home: Path | None = None,
    opener: UrlOpen = urlopen,
) -> MirrorStatus:
    """Read-only probe for the control panel. Does not start the editor."""
    home = home or Path.home()
    port = settings.hackaboom_port
    plugin = resolve_plugin_dir(settings, home)
    plugin_s = str(plugin) if plugin else None
    telemetry = telemetry_enabled(home)
    probed = probe_bridge(port, opener=opener)
    if probed.get("ok"):
        return MirrorStatus(
            status="ready",
            detail="editor bridge is listening",
            port=port,
            version=probed.get("version"),
            panel_url=panel_url(port, home),
            telemetry_enabled=telemetry,
            plugin_dir=plugin_s,
        )
    if probed.get("collision"):
        return MirrorStatus(
            status="collision",
            detail=str(probed.get("error") or "port collision"),
            port=port,
            panel_url=None,
            telemetry_enabled=telemetry,
            plugin_dir=plugin_s,
            collision=True,
        )
    return MirrorStatus(
        status="down",
        detail=str(probed.get("error") or "bridge not listening"),
        port=port,
        panel_url=panel_url(port, home),
        telemetry_enabled=telemetry,
        plugin_dir=plugin_s,
    )
