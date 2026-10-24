from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app
from server.runs import RunStatus
from server.sessions import InvalidSessionId, SessionNotFound, find_session


_ENV_NAMES = (
    "NIGHTSHIFT_DATA_DIR",
    "NIGHTSHIFT_HOST",
    "NIGHTSHIFT_PORT",
    "NIGHTSHIFT_LOG_LEVEL",
    "NIGHTSHIFT_NOTIFY_TARGETS",
    "NIGHTSHIFT_PHOTON_TARGET",
    "NIGHTSHIFT_CLAUDE_BIN",
    "NIGHTSHIFT_CLAUDE_PROJECTS_DIR",
    "NIGHTSHIFT_CLAUDE_PERMISSION_MODE",
    "NIGHTSHIFT_RUN_TIMEOUT_SECONDS",
    "NIGHTSHIFT_TMUX_BIN",
    "NIGHTSHIFT_WORKER_POLL_SECONDS",
)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Fresh settings pointing data/projects dirs at tmp_path."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return Settings(
        data_dir=tmp_path / "data",
        claude_projects_dir=tmp_path / "projects",
        worker_poll_seconds=0.05,
        _env_file=None,
    )


@pytest.fixture
def session_id(settings: Settings) -> str:
    """Create one resumable session jsonl and return its id."""
    sid = str(uuid.uuid4())
    project_dir = settings.claude_projects_dir / "fake-proj"
    project_dir.mkdir(parents=True)
    jsonl_path = project_dir / f"{sid}.jsonl"
    jsonl_path.write_text(
        json.dumps({"type": "summary", "cwd": "/tmp/fake-proj", "sessionId": sid}) + "\n",
        encoding="utf-8",
    )
    return sid


class FakeRunner:
    """Test double for the Runner protocol. Never invokes tmux or claude."""

    def __init__(
        self,
        settings: Settings,
        log_body: str = "",
        exit_code: int = 0,
        hold_event: threading.Event | None = None,
    ) -> None:
        self.settings = settings
        self.log_body = log_body
        self.exit_code = exit_code
        self.hold_event = hold_event
        self.started: list[object] = []
        self.killed: list[object] = []

    def start(self, record: object) -> None:
        self.started.append(record)
        log_path = self.settings.runs_dir / f"{getattr(record, 'id')}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(self.log_body, encoding="utf-8")

    def poll(self, record: object) -> tuple[bool, int | None]:
        if self.hold_event is not None and not self.hold_event.is_set():
            return (True, None)
        return (False, self.exit_code)

    def kill(self, record: object) -> None:
        self.killed.append(record)


def wait_for_status(
    client: TestClient,
    run_id: str,
    target_status: str,
    timeout: float = 5,
) -> dict:
    """Poll GET /runs/{run_id} until the record reaches target_status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] == target_status:
            return body
        time.sleep(0.02)
    raise AssertionError(
        f"run {run_id} did not reach {target_status} within {timeout}s"
    )


def test_create_run_persists_record(settings: Settings, session_id: str) -> None:
    """POST /runs enqueues a run and writes its JSON state to disk."""
    runner = FakeRunner(settings)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"session_id": session_id, "instruction": "say hello"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == session_id
    assert body["instruction"] == "say hello"
    assert body["status"] == RunStatus.QUEUED.value
    assert body["cwd"] == "/tmp/fake-proj"
    assert Path(body["log_path"]).parent == settings.runs_dir
    assert (settings.runs_dir / f"{body['id']}.json").exists()


def test_happy_path_done(settings: Settings, session_id: str) -> None:
    """A zero-exit JSON log is parsed into result_summary/cost/turns."""
    log_body = json.dumps(
        {
            "session_id": session_id,
            "result": "completed summary",
            "is_error": False,
            "num_turns": 7,
            "total_cost_usd": 0.042,
        }
    )
    runner = FakeRunner(settings, log_body=log_body, exit_code=0)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"session_id": session_id, "instruction": "resume"},
        )
        run_id = response.json()["id"]
        body = wait_for_status(client, run_id, RunStatus.DONE.value)

    assert body["exit_code"] == 0
    assert body["result_summary"] == "completed summary"
    assert body["num_turns"] == 7
    assert body["cost_usd"] == 0.042
    assert Path(body["log_path"]).exists()


def test_failure_path_unknown_session(settings: Settings, session_id: str) -> None:
    """Non-zero exit surfaces the log tail as the run error."""
    log_tail = f"No conversation found with session ID: {session_id}"
    runner = FakeRunner(settings, log_body=log_tail, exit_code=1)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"session_id": session_id, "instruction": "resume"},
        )
        run_id = response.json()["id"]
        body = wait_for_status(client, run_id, RunStatus.FAILED.value)

    assert body["exit_code"] == 1
    assert log_tail in body["error"]


def test_one_at_a_time(settings: Settings, session_id: str) -> None:
    """Only one run executes at a time; the rest stay queued."""
    hold = threading.Event()
    log_body = json.dumps(
        {"session_id": session_id, "result": "ok", "is_error": False, "num_turns": 1}
    )
    runner = FakeRunner(settings, log_body=log_body, exit_code=0, hold_event=hold)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        first = client.post(
            "/runs",
            json={"session_id": session_id, "instruction": "first"},
        ).json()
        wait_for_status(client, first["id"], RunStatus.RUNNING.value)

        second = client.post(
            "/runs",
            json={"session_id": session_id, "instruction": "second"},
        ).json()
        assert second["status"] == RunStatus.QUEUED.value

        hold.set()
        wait_for_status(client, first["id"], RunStatus.DONE.value)
        wait_for_status(client, second["id"], RunStatus.DONE.value)

    assert len(runner.started) == 2


def test_bad_session_id_returns_400(settings: Settings) -> None:
    """Malformed session id is rejected before hitting the filesystem."""
    runner = FakeRunner(settings)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"session_id": "not-a-uuid", "instruction": "x"},
        )
    assert response.status_code == 400


def test_unknown_session_returns_404(settings: Settings) -> None:
    """A valid but missing session id returns 404."""
    runner = FakeRunner(settings)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"session_id": str(uuid.uuid4()), "instruction": "x"},
        )
    assert response.status_code == 404


def test_blank_instruction_returns_400(settings: Settings, session_id: str) -> None:
    """Empty instruction is rejected with 400."""
    runner = FakeRunner(settings)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"session_id": session_id, "instruction": "   "},
        )
    assert response.status_code == 400


def test_get_run_unknown_returns_404(settings: Settings) -> None:
    """GET /runs/{id} for a missing id is a 404."""
    runner = FakeRunner(settings)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.get(f"/runs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "run not found"


def test_list_runs_with_status_filter(settings: Settings, session_id: str) -> None:
    """GET /runs supports ?status=queued."""
    runner = FakeRunner(settings)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        created = client.post(
            "/runs",
            json={"session_id": session_id, "instruction": "list me"},
        ).json()
        queued = client.get("/runs", params={"status": RunStatus.QUEUED.value}).json()["runs"]
        assert any(r["id"] == created["id"] for r in queued)
        invalid = client.get("/runs", params={"status": "nope"})
    assert invalid.status_code == 400


def test_find_session_locates_jsonl(settings: Settings) -> None:
    """find_session parses the recorded cwd from a session jsonl."""
    sid = str(uuid.uuid4())
    project_dir = settings.claude_projects_dir / "-fake-proj"
    project_dir.mkdir(parents=True)
    jsonl_path = project_dir / f"{sid}.jsonl"
    jsonl_path.write_text(
        json.dumps({"type": "summary", "cwd": "/tmp/fake-proj", "sessionId": sid}) + "\n",
        encoding="utf-8",
    )
    ref = find_session(sid, settings.claude_projects_dir)
    assert ref.session_id == sid
    assert ref.project_dir == project_dir
    assert ref.cwd == "/tmp/fake-proj"
    assert ref.jsonl_path == jsonl_path


def test_find_session_rejects_bad_uuid(settings: Settings) -> None:
    with pytest.raises(InvalidSessionId):
        find_session("not-a-uuid", settings.claude_projects_dir)


def test_find_session_missing_jsonl(settings: Settings) -> None:
    with pytest.raises(SessionNotFound):
        find_session(str(uuid.uuid4()), settings.claude_projects_dir)


def test_cwd_override_wins(settings: Settings, session_id: str) -> None:
    """The optional cwd field in RunCreate overrides the session cwd."""
    runner = FakeRunner(settings)
    app = create_app(settings=settings, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={
                "session_id": session_id,
                "instruction": "use other dir",
                "cwd": "/overridden",
            },
        )
    assert response.status_code == 201
    assert response.json()["cwd"] == "/overridden"
