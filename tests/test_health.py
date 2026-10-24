from fastapi.testclient import TestClient

from server import __version__
from server.main import app, create_app


def test_health() -> None:
    """GET /health returns ok metadata on both the factory app and module app."""
    for client in (TestClient(create_app()), TestClient(app)):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "nightshift"
        assert body["version"] == __version__


def test_unknown_route_returns_404() -> None:
    """Unregistered routes return 404."""
    client = TestClient(app)
    response = client.get("/not-a-route")
    assert response.status_code == 404
