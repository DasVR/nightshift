from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server.config import Settings
from server.main import STATIC_DIR, create_app


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        claude_projects_dir=tmp_path / "projects",
        hackaboom_prepare=False,
        _env_file=None,
    )
    return TestClient(create_app(settings=settings))


def test_desk_page_has_resume_button(tmp_path: Path) -> None:
    """GET / is the control panel, not an empty 404."""
    assert (STATIC_DIR / "index.html").is_file()
    client = _client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Resume" in response.text
    assert "Continue the current task." in response.text
    assert "Editor" in response.text


def test_desk_assets_are_served(tmp_path: Path) -> None:
    client = _client(tmp_path)
    css = client.get("/static/desk.css")
    js = client.get("/static/desk.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "btn-resume" in js.text


def test_mirror_endpoint_reports_typing_flags(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/mirror")
    assert response.status_code == 200
    body = response.json()
    assert "typing" in body
    assert "heartbeats" in body
    assert "panel_url" in body
    assert body["port"] == 8788
