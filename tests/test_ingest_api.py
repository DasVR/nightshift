from __future__ import annotations

import hashlib
import json
import time
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from server.ingest import sha256_hex
from server.main import create_app


FIXTURES = Path(__file__).parent / "fixtures"


def _client(tmp_path: Path) -> TestClient:
    from server.config import Settings

    settings = Settings(data_dir=tmp_path, _env_file=None)
    return TestClient(create_app(settings))


def _zip_bytes(pairs: list[tuple[str, str]]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in pairs:
            zf.writestr(name, content)
    return buf.getvalue()


def test_zip_ingest_happy_path(tmp_path: Path) -> None:
    """A valid zip returns 200, counts, and the session is readable."""
    client = _client(tmp_path)
    bundle = FIXTURES / "bundle.zip"
    response = client.post("/sessions/ingest", files={"bundle": ("bundle.zip", bundle.read_bytes(), "application/zip")})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "zip"
    assert body["counts"]["total"] >= 1
    assert body["errors"] == []

    first = body["sessions"][0]
    detail = client.get(f"/sessions/{first['session_id']}")
    assert detail.status_code == 200
    assert detail.json()["session_id"] == first["session_id"]


def test_idempotency_same_zip(tmp_path: Path) -> None:
    """Re-uploading the same single-session zip is idempotent."""
    client = _client(tmp_path)
    pairs = [("project-x/11111111-2222-3333-4444-555555555555.jsonl", (FIXTURES / "valid_session.jsonl").read_text())]
    data = _zip_bytes(pairs)

    r1 = client.post("/sessions/ingest", files={"bundle": ("single.zip", data, "application/zip")})
    assert r1.status_code == 200
    assert r1.json()["counts"]["created"] == 1

    sid = r1.json()["sessions"][0]["session_id"]
    created_at = client.get(f"/sessions/{sid}").json()["created_at"]

    r2 = client.post("/sessions/ingest", files={"bundle": ("single.zip", data, "application/zip")})
    assert r2.status_code == 200
    assert r2.json()["counts"]["unchanged"] == 1
    assert r2.json()["counts"]["created"] == 0

    total = client.get("/sessions").json()["total"]
    assert total == 1
    assert client.get(f"/sessions/{sid}").json()["created_at"] == created_at


def test_changed_content_updates(tmp_path: Path) -> None:
    """Same session id with new content updates the row."""
    client = _client(tmp_path)
    original = _zip_bytes([("project-x/11111111-2222-3333-4444-555555555555.jsonl", (FIXTURES / "valid_session.jsonl").read_text())])
    updated = _zip_bytes([("project-x/11111111-2222-3333-4444-555555555555.jsonl", '{"sessionId":"11111111-2222-3333-4444-555555555555","type":"user","timestamp":"2024-02-02T10:00:00Z","cwd":"/home/test/proj","message":{"content":"new"}}\n')])

    r1 = client.post("/sessions/ingest", files={"bundle": ("a.zip", original, "application/zip")})
    assert r1.json()["counts"]["created"] == 1
    sid = r1.json()["sessions"][0]["session_id"]

    r2 = client.post("/sessions/ingest", files={"bundle": ("b.zip", updated, "application/zip")})
    assert r2.status_code == 200
    assert r2.json()["counts"]["updated"] == 1

    total = client.get("/sessions").json()["total"]
    assert total == 1
    detail = client.get(f"/sessions/{sid}").json()
    assert detail["turn_count"] == 1
    assert detail["updated_at"] != detail["created_at"]


def test_malformed_lines_in_upload(tmp_path: Path) -> None:
    """Malformed lines are tolerated and reported in warnings."""
    client = _client(tmp_path)
    data = _zip_bytes([("project-m/44444444-2222-3333-4444-555555555555.jsonl", (FIXTURES / "malformed_session.jsonl").read_text())])
    response = client.post("/sessions/ingest", files={"bundle": ("m.zip", data, "application/zip")})
    assert response.status_code == 200
    body = response.json()
    assert body["sessions"][0]["skipped_lines"] > 0
    assert len(body["warnings"]) == 1


def test_zip_slip_rejected(tmp_path: Path) -> None:
    """Zip-slip entries cause 400 and no rows are written."""
    client = _client(tmp_path)
    data = _zip_bytes([
        ("../evil.jsonl", (FIXTURES / "valid_session.jsonl").read_text()),
        ("/abs/evil.jsonl", (FIXTURES / "valid_session.jsonl").read_text()),
    ])
    response = client.post("/sessions/ingest", files={"bundle": ("slip.zip", data, "application/zip")})
    assert response.status_code == 400
    assert "unsafe path" in response.json()["detail"]
    assert client.get("/sessions").json()["total"] == 0


def test_invalid_zip_and_missing_bundle(tmp_path: Path) -> None:
    """Invalid zip bytes return 400; missing bundle returns 422."""
    client = _client(tmp_path)
    bad = client.post("/sessions/ingest", files={"bundle": ("bad.zip", b"PK\x03\x04not a zip", "application/zip")})
    assert bad.status_code == 400
    assert "not a valid zip" in bad.json()["detail"]

    missing = client.post("/sessions/ingest")
    assert missing.status_code == 422
    assert "missing bundle" in missing.json()["detail"]


def test_manifest_validation(tmp_path: Path) -> None:
    """Manifest sha mismatch / missing path -> 422; matching manifest -> 200."""
    client = _client(tmp_path)
    session_text = (FIXTURES / "valid_session.jsonl").read_text()
    data = _zip_bytes([("project-a/11111111-2222-3333-4444-555555555555.jsonl", session_text)])
    real_sha = sha256_hex(session_text.encode())

    bad_sha_manifest = json.dumps({
        "sessions": [{"path": "project-a/11111111-2222-3333-4444-555555555555.jsonl", "sha256": "0" * 64}]
    })
    r1 = client.post(
        "/sessions/ingest",
        files={
            "bundle": ("a.zip", data, "application/zip"),
            "manifest": ("manifest.json", bad_sha_manifest, "application/json"),
        },
    )
    assert r1.status_code == 422
    assert "sha256 mismatch" in r1.json()["detail"]

    missing_path_manifest = json.dumps({"sessions": [{"path": "project-a/missing.jsonl"}]})
    r2 = client.post(
        "/sessions/ingest",
        files={
            "bundle": ("a.zip", data, "application/zip"),
            "manifest": ("manifest.json", missing_path_manifest, "application/json"),
        },
    )
    assert r2.status_code == 422
    assert "not present in bundle" in r2.json()["detail"]

    good_manifest = json.dumps({
        "collector_version": "0.1.0",
        "host": "desktop-test",
        "sessions": [{
            "path": "project-a/11111111-2222-3333-4444-555555555555.jsonl",
            "session_id": "11111111-2222-3333-4444-555555555555",
            "project": "project-a",
            "sha256": real_sha,
            "size": len(session_text),
        }],
    })
    r3 = client.post(
        "/sessions/ingest",
        files={
            "bundle": ("a.zip", data, "application/zip"),
            "manifest": ("manifest.json", good_manifest, "application/json"),
        },
    )
    assert r3.status_code == 200
    assert r3.json()["counts"]["created"] == 1


def test_manifest_non_jsonl_path_accepted(tmp_path: Path) -> None:
    """A manifest may list non-jsonl entries that exist; only jsonl sessions are parsed."""
    client = _client(tmp_path)
    session_text = (FIXTURES / "valid_session.jsonl").read_text()
    notes = b"project notes"
    data = _zip_bytes([
        ("project-a/11111111-2222-3333-4444-555555555555.jsonl", session_text),
        ("project-a/notes.txt", notes.decode()),
    ])
    manifest = json.dumps({
        "sessions": [
            {"path": "project-a/11111111-2222-3333-4444-555555555555.jsonl"},
            {"path": "project-a/notes.txt", "sha256": sha256_hex(notes), "size": len(notes)},
        ]
    })
    response = client.post(
        "/sessions/ingest",
        files={
            "bundle": ("mixed.zip", data, "application/zip"),
            "manifest": ("manifest.json", manifest, "application/json"),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["created"] == 1
    assert body["counts"]["total"] == 1
    assert body["sessions"][0]["session_id"] == "11111111-2222-3333-4444-555555555555"


def test_manifest_missing_path_rejected(tmp_path: Path) -> None:
    """A manifest path that is absent from the archive is rejected 422."""
    client = _client(tmp_path)
    session_text = (FIXTURES / "valid_session.jsonl").read_text()
    data = _zip_bytes([("project-a/11111111-2222-3333-4444-555555555555.jsonl", session_text)])
    manifest = json.dumps({
        "sessions": [
            {"path": "project-a/11111111-2222-3333-4444-555555555555.jsonl"},
            {"path": "project-a/missing.txt"},
        ]
    })
    response = client.post(
        "/sessions/ingest",
        files={
            "bundle": ("mixed.zip", data, "application/zip"),
            "manifest": ("manifest.json", manifest, "application/json"),
        },
    )
    assert response.status_code == 422
    assert "not present in bundle" in response.json()["detail"]


def test_bare_jsonl_upload(tmp_path: Path) -> None:
    """A bare jsonl upload gets project _unknown and id from sessionId."""
    client = _client(tmp_path)
    data = (FIXTURES / "valid_session.jsonl").read_bytes()
    response = client.post("/sessions/ingest", files={"bundle": ("valid_session.jsonl", data, "application/json")})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "jsonl"
    assert body["sessions"][0]["project"] == "_unknown"
    assert body["sessions"][0]["session_id"] == "11111111-2222-3333-4444-555555555555"


def test_list_and_detail(tmp_path: Path) -> None:
    """GET /sessions filters by project and paginates; unknown id returns 404."""
    client = _client(tmp_path)
    session_text = (FIXTURES / "valid_session.jsonl").read_text()
    data = _zip_bytes([
        ("alpha/11111111-2222-3333-4444-555555555555.jsonl", session_text),
        ("beta/22222222-2222-3333-4444-555555555555.jsonl", session_text),
    ])
    client.post("/sessions/ingest", files={"bundle": ("list.zip", data, "application/zip")})

    all_resp = client.get("/sessions?limit=1")
    assert all_resp.status_code == 200
    assert all_resp.json()["count"] == 1
    assert all_resp.json()["total"] == 2

    alpha_resp = client.get("/sessions?project=alpha")
    assert alpha_resp.json()["total"] == 1
    assert alpha_resp.json()["sessions"][0]["project"] == "alpha"

    bad = client.get("/sessions?limit=0")
    assert bad.status_code == 422

    not_found = client.get("/sessions/not-real")
    assert not_found.status_code == 404


def test_manifest_without_session_id_accepted(tmp_path: Path) -> None:
    """Manifest entry with only path is accepted; wrong declared id still rejects."""
    client = _client(tmp_path)
    session_text = (FIXTURES / "valid_session.jsonl").read_text()
    sid = "11111111-2222-3333-4444-555555555555"
    data = _zip_bytes([(f"project-a/{sid}.jsonl", session_text)])

    manifest_no_id = json.dumps({"sessions": [{"path": f"project-a/{sid}.jsonl"}]})
    r1 = client.post(
        "/sessions/ingest",
        files={
            "bundle": ("a.zip", data, "application/zip"),
            "manifest": ("manifest.json", manifest_no_id, "application/json"),
        },
    )
    assert r1.status_code == 200
    assert r1.json()["counts"]["created"] == 1

    manifest_bad_id = json.dumps(
        {"sessions": [{"path": f"project-a/{sid}.jsonl", "session_id": "22222222-2222-3333-4444-555555555555"}]}
    )
    r2 = client.post(
        "/sessions/ingest",
        files={
            "bundle": ("b.zip", data, "application/zip"),
            "manifest": ("manifest.json", manifest_bad_id, "application/json"),
        },
    )
    assert r2.status_code == 422
    assert "manifest session_id disagrees" in r2.json()["detail"]


def test_zip_named_bundle_with_bad_magic_is_400(tmp_path: Path) -> None:
    """A .zip filename without PK magic is rejected; the same bytes as .jsonl are accepted."""
    client = _client(tmp_path)
    garbage = b"this is not a zip"

    bad = client.post(
        "/sessions/ingest",
        files={"bundle": ("sessions.zip", garbage, "application/zip")},
    )
    assert bad.status_code == 400
    assert "not a valid zip" in bad.json()["detail"]
    assert client.get("/sessions").json()["total"] == 0

    ok = client.post(
        "/sessions/ingest",
        files={"bundle": ("session.jsonl", garbage, "application/json")},
    )
    assert ok.status_code == 200
    assert ok.json()["source"] == "jsonl"


def test_unsafe_non_jsonl_entry_rejected(tmp_path: Path) -> None:
    """An unsafe non-jsonl entry causes 400 before any data is written."""
    client = _client(tmp_path)
    data = _zip_bytes([
        ("../evil.sh", "#!/bin/sh\n"),
        ("project-a/11111111-2222-3333-4444-555555555555.jsonl", (FIXTURES / "valid_session.jsonl").read_text()),
    ])
    response = client.post("/sessions/ingest", files={"bundle": ("unsafe.zip", data, "application/zip")})
    assert response.status_code == 400
    assert "unsafe path" in response.json()["detail"]
    assert client.get("/sessions").json()["total"] == 0


def test_unchanged_upload_does_not_rewrite_file(tmp_path: Path) -> None:
    """Re-ingesting identical bytes leaves the stored file untouched."""
    client = _client(tmp_path)
    pairs = [("project-x/11111111-2222-3333-4444-555555555555.jsonl", (FIXTURES / "valid_session.jsonl").read_text())]
    data = _zip_bytes(pairs)

    r1 = client.post("/sessions/ingest", files={"bundle": ("single.zip", data, "application/zip")})
    assert r1.status_code == 200
    assert r1.json()["counts"]["created"] == 1
    sid = r1.json()["sessions"][0]["session_id"]

    detail = client.get(f"/sessions/{sid}").json()
    stored = tmp_path / detail["stored_path"]
    first_mtime = stored.stat().st_mtime_ns
    created_at = detail["created_at"]

    time.sleep(0.01)

    r2 = client.post("/sessions/ingest", files={"bundle": ("single.zip", data, "application/zip")})
    assert r2.status_code == 200
    assert r2.json()["counts"]["unchanged"] == 1

    second_detail = client.get(f"/sessions/{sid}").json()
    assert second_detail["created_at"] == created_at
    assert stored.stat().st_mtime_ns == first_mtime
