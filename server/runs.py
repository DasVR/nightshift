from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from server.config import Settings
from server.hackaboom import MirrorStatus, prepare_mirror, resolve_plugin_dir


class RunStatus(str, Enum):
    """Lifecycle states for a resume run."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class RunRecord(BaseModel):
    """Persistent state of a single headless resume run."""

    id: str
    session_id: str
    instruction: str
    cwd: str
    project_dir: str
    status: str
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    log_path: str
    tmux_session: str | None = None
    result_summary: str | None = None
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    mirror_status: str | None = None
    mirror_detail: str | None = None
    mirror_panel_url: str | None = None


class Runner(Protocol):
    """Pluggable backend that starts and polls a run."""

    def start(self, record: RunRecord) -> None:
        """Launch the run; may raise if the backend fails to spawn it."""
        ...

    def poll(self, record: RunRecord) -> tuple[bool, int | None]:
        """Return (still_running, exit_code_when_finished)."""
        ...

    def kill(self, record: RunRecord) -> None:
        """Best-effort termination of a running run."""
        ...


class RunStore:
    """Thread-safe, write-through store for run records."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self._lock = threading.Lock()
        self._runs: dict[str, RunRecord] = {}
        self._load_existing()

    def _path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def _load_existing(self) -> None:
        if not self.runs_dir.exists():
            return
        for path in self.runs_dir.glob("*.json"):
            try:
                record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if record.status == RunStatus.RUNNING.value:
                record.status = RunStatus.FAILED.value
                record.error = "server restarted mid-run"
                record.ended_at = datetime.now(timezone.utc).isoformat()
                self._persist(record)
            self._runs[record.id] = record

    def _persist(self, record: RunRecord) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._path(record.id).write_text(
            record.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def create(
        self,
        session_id: str,
        instruction: str,
        cwd: str,
        project_dir: str,
    ) -> RunRecord:
        run_id = str(uuid.uuid4())
        log_path = str(self.runs_dir / f"{run_id}.log")
        record = RunRecord(
            id=run_id,
            session_id=session_id,
            instruction=instruction,
            cwd=cwd,
            project_dir=project_dir,
            status=RunStatus.QUEUED.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            log_path=log_path,
        )
        with self._lock:
            self._runs[record.id] = record
            self._persist(record)
        return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self, status: str | None = None) -> list[RunRecord]:
        with self._lock:
            records = list(self._runs.values())
        records.sort(key=lambda r: r.created_at, reverse=True)
        if status:
            records = [r for r in records if r.status == status]
        return records

    def update(self, record: RunRecord) -> RunRecord:
        with self._lock:
            self._runs[record.id] = record
            self._persist(record)
        return record


class ClaudeTmuxRunner:
    """Production runner: resumes a Claude session inside a detached tmux pane."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _log_path(self, record: RunRecord) -> Path:
        return self.settings.runs_dir / f"{record.id}.log"

    def _exit_path(self, record: RunRecord) -> Path:
        return self.settings.runs_dir / f"{record.id}.exit"

    def _tmux_name(self, record: RunRecord) -> str:
        return f"nsrun-{record.id[:8]}"

    def claude_argv(self, record: RunRecord) -> list[str]:
        """Argv for a headless resume, including the editor plugin when present."""
        argv = [
            self.settings.claude_bin,
            "--resume",
            record.session_id,
            "-p",
            record.instruction,
            "--output-format",
            "json",
            "--permission-mode",
            self.settings.claude_permission_mode,
        ]
        plugin = resolve_plugin_dir(self.settings, Path.home())
        if plugin is not None:
            argv.extend(["--plugin-dir", str(plugin)])
        return argv

    def start(self, record: RunRecord) -> None:
        log_path = self._log_path(record)
        exit_path = self._exit_path(record)
        tmux_name = self._tmux_name(record)
        argv = self.claude_argv(record)
        shell_cmd = (
            f"{shlex.join(argv)} > {shlex.quote(str(log_path))} 2>\u00261; "
            f"echo $? > {shlex.quote(str(exit_path))}"
        )
        env = os.environ.copy()
        if "--plugin-dir" in argv:
            env["CLAUDE_CODE_PLUGIN_DIRS"] = argv[argv.index("--plugin-dir") + 1]
        subprocess.run(
            [
                self.settings.tmux_bin,
                "new-session",
                "-d",
                "-s",
                tmux_name,
                "-c",
                record.cwd,
                "/bin/sh",
                "-c",
                shell_cmd,
            ],
            check=True,
            env=env,
        )
        record.tmux_session = tmux_name

    def poll(self, record: RunRecord) -> tuple[bool, int | None]:
        exit_path = self._exit_path(record)
        if exit_path.exists() and exit_path.stat().st_size > 0:
            try:
                code = int(exit_path.read_text(encoding="utf-8").strip())
            except ValueError:
                return (False, None)
            return (False, code)

        tmux_name = self._tmux_name(record)
        result = subprocess.run(
            [self.settings.tmux_bin, "has-session", "-t", tmux_name],
            capture_output=True,
        )
        if result.returncode != 0:
            return (False, None)

        return (True, None)

    def kill(self, record: RunRecord) -> None:
        tmux_name = self._tmux_name(record)
        subprocess.run(
            [self.settings.tmux_bin, "kill-session", "-t", tmux_name],
            capture_output=True,
        )


class RunWorker:
    """Daemon worker that drives queued runs to completion via a Runner."""

    def __init__(
        self,
        store: RunStore,
        runner: Runner,
        settings: Settings,
    ) -> None:
        self.store = store
        self.runner = runner
        self.settings = settings
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _oldest_queued(self) -> RunRecord | None:
        queued = [r for r in self.store.list() if r.status == RunStatus.QUEUED.value]
        if not queued:
            return None
        queued.sort(key=lambda r: r.created_at)
        return queued[0]

    def _log_tail(self, record: RunRecord, max_chars: int = 4000) -> str:
        log_path = Path(record.log_path)
        if not log_path.exists():
            return ""
        text = log_path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text

    def _parse_success(self, record: RunRecord) -> tuple[str | None, float | None, int | None]:
        log_path = Path(record.log_path)
        if not log_path.exists():
            return None, None, None
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return (
                    payload.get("result"),
                    payload.get("total_cost_usd"),
                    payload.get("num_turns"),
                )
        return None, None, None

    def _apply_mirror(self, record: RunRecord, mirror: MirrorStatus) -> None:
        """Copy editor-bridge status onto the run without failing it."""
        record.mirror_status = mirror.status
        record.mirror_detail = mirror.detail
        record.mirror_panel_url = mirror.panel_url

    def _process_run(self, record: RunRecord) -> None:
        record.status = RunStatus.RUNNING.value
        record.started_at = datetime.now(timezone.utc).isoformat()
        self.store.update(record)

        if self.settings.hackaboom_prepare:
            mirror = prepare_mirror(self.settings, record.cwd)
            self._apply_mirror(record, mirror)

        self.runner.start(record)
        self.store.update(record)

        deadline = None
        if self.settings.run_timeout_seconds > 0:
            deadline = datetime.now(timezone.utc).timestamp() + self.settings.run_timeout_seconds

        still_running = True
        exit_code: int | None = None
        while still_running:
            if self._stop_event.wait(timeout=self.settings.worker_poll_seconds):
                return
            still_running, exit_code = self.runner.poll(record)
            if deadline is not None and datetime.now(timezone.utc).timestamp() > deadline:
                self.runner.kill(record)
                record.status = RunStatus.FAILED.value
                record.ended_at = datetime.now(timezone.utc).isoformat()
                record.error = f"timeout after {self.settings.run_timeout_seconds}s"
                self.store.update(record)
                return

        if exit_code is None:
            record.status = RunStatus.FAILED.value
            record.error = (
                f"tmux session {record.tmux_session} disappeared without writing an exit file; "
                f"log tail: {self._log_tail(record)}"
            )
        elif exit_code == 0:
            result, cost_usd, num_turns = self._parse_success(record)
            if result is None:
                record.status = RunStatus.FAILED.value
                record.error = self._log_tail(record)
            else:
                record.status = RunStatus.DONE.value
                record.result_summary = result[:2000]
                record.cost_usd = cost_usd
                record.num_turns = num_turns
        else:
            record.status = RunStatus.FAILED.value
            record.error = self._log_tail(record)

        record.exit_code = exit_code
        record.ended_at = datetime.now(timezone.utc).isoformat()
        self.store.update(record)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            record = self._oldest_queued()
            if record is None:
                if self._stop_event.wait(timeout=self.settings.worker_poll_seconds):
                    break
                continue
            try:
                self._process_run(record)
            except Exception as exc:
                try:
                    record.status = RunStatus.FAILED.value
                    record.ended_at = datetime.now(timezone.utc).isoformat()
                    record.error = f"worker error: {exc}"
                    self.store.update(record)
                except Exception:
                    pass

    def start(self) -> None:
        """Idempotent start of the daemon worker thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Idempotent stop; does not kill in-flight runs."""
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        self._thread = None
