from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from uuid import uuid4


def _build_multipart_body(
    boundary: str,
    bundle_bytes: bytes,
    manifest_bytes: bytes,
) -> bytes:
    """Return multipart/form-data body with bundle and manifest file fields."""
    lines: list[bytes] = []
    crlf = b"\r\n"

    def add_file(name: str, filename: str, content: bytes, content_type: str) -> None:
        lines.append(f"--{boundary}".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
        )
        lines.append(f"Content-Type: {content_type}".encode())
        lines.append(b"")
        lines.append(content)

    add_file("bundle", "session-bundle.zip", bundle_bytes, "application/zip")
    add_file("manifest", "manifest.json", manifest_bytes, "application/json")
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")

    return crlf.join(lines)


def post_ingest(
    server_url: str,
    bundle_bytes: bytes,
    manifest_bytes: bytes,
    *,
    timeout: float = 60.0,
) -> tuple[int, str]:
    """POST bundle + manifest to ``{server_url}/sessions/ingest``.

    Returns ``(status_code, response_body_text)``. Raises ``ConnectionError`` on
    network failures and ``TimeoutError`` on timeout.
    """
    base = server_url.rstrip("/")
    url = f"{base}/sessions/ingest"
    boundary = f"nightshift-{uuid4().hex}"
    body = _build_multipart_body(boundary, bundle_bytes, manifest_bytes)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = response.getcode() or 200
            text = response.read().decode("utf-8", errors="replace")
            return status, text
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        return exc.code, text
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise TimeoutError(f"request timed out after {timeout}s") from exc
        raise ConnectionError(f"could not connect to {url}: {reason}") from exc


def format_ingest_response(body: str) -> str:
    """Pretty-print JSON response when possible."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body
    return json.dumps(parsed, indent=2)
