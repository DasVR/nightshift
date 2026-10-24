from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 200
DEFAULT_TIMEOUT = 15.0


class NotifyTestRequest(BaseModel):
    session_id: str = "test-session"
    project: str = "nightshift-test"
    outcome: Literal["done", "failed"] = "done"
    summary: str = "fake notification from POST /notify/test"


def resolve_targets(settings) -> list[str]:
    """Return ordered, deduplicated notify targets from settings.

    Includes ``settings.notify_targets`` and appends ``settings.photon_target``
    when it is set and not already present. Empty strings are dropped.
    """
    targets: list[str] = []
    seen: set[str] = set()

    for target in getattr(settings, "notify_targets", []) or []:
        if target and target not in seen:
            targets.append(target)
            seen.add(target)

    photon = getattr(settings, "photon_target", None)
    if photon and photon not in seen:
        targets.append(photon)
        seen.add(photon)

    return targets


def hermes_bin() -> str:
    """Resolve the Hermes CLI binary path.

    Override with ``NIGHTSHIFT_HERMES_BIN``; otherwise fall back to the first
    ``hermes`` on ``$PATH`` or the bare command name.
    """
    return os.environ.get("NIGHTSHIFT_HERMES_BIN") or shutil.which("hermes") or "hermes"


def one_line(text: object, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Coerce ``text`` to a single trimmed line, collapsing whitespace.

    Returns ``""`` for ``None``. Truncated results end with an ellipsis so the
    final string never exceeds ``limit`` characters.
    """
    if text is None:
        return ""
    value = str(text)
    value = " ".join(value.split())
    value = value.strip()
    if len(value) > limit:
        truncate = max(limit - 1, 0)
        value = value[:truncate] + "…"
    return value


def build_command(target: str, subject: str, body: str, binary: str = "hermes") -> list[str]:
    """Build the Hermes CLI argv for sending a notification."""
    return [binary, "send", "--to", target, "--subject", subject, body]


def _field(run, name: str, default=None):
    """Read ``name`` from a dict-like mapping or an arbitrary object."""
    if isinstance(run, Mapping):
        return run.get(name, default)
    return getattr(run, name, default)


def format_run_message(run) -> tuple[str, str]:
    """Build a (subject, body) notification for a finished run.

    Accepts either a ``Mapping`` (dict) or a dataclass/object run record.
    """
    session_id = _field(run, "session_id") or _field(run, "session") or "unknown"
    project = _field(run, "project") or "unknown"
    outcome = (_field(run, "outcome") or _field(run, "status") or "unknown").lower()
    summary = _field(run, "summary") or _field(run, "result") or ""

    subject = f"nightshift {outcome}: {session_id}"
    body_lines = [
        f"session: {session_id}",
        f"project: {project}",
        f"outcome: {outcome}",
        f"summary: {one_line(summary)}",
    ]

    run_id = _field(run, "id")
    if run_id is not None:
        body_lines.append(f"run: {run_id}")

    return subject, "\n".join(body_lines)


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    targets: list[str]
    failed: list[str]
    detail: str
    commands: list[list[str]]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "targets": self.targets,
            "failed": self.failed,
            "detail": self.detail,
            "commands": self.commands,
        }


def notify_run(run, settings, *, binary: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> NotifyResult:
    """Notify every configured target that a run reached a terminal state.

    This function never raises. Exceptions for individual targets are caught,
    logged, and reported in the returned ``NotifyResult``.
    """
    targets = resolve_targets(settings)
    if not targets:
        return NotifyResult(
            ok=False,
            targets=[],
            failed=[],
            detail="no notify targets configured",
            commands=[],
        )

    subject, body = format_run_message(run)
    binary = binary if binary is not None else hermes_bin()

    commands: list[list[str]] = []
    failed: list[str] = []
    detail_parts: list[str] = []

    for target in targets:
        cmd = build_command(target, subject, body, binary=binary)
        commands.append(cmd)
        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("notify failed for %s: %s", target, exc)
            failed.append(target)
            detail_parts.append(f"{target}: {exc}")
            continue
        except Exception as exc:  # pragma: no cover - defensive catch
            logger.warning("notify failed for %s: %s", target, exc)
            failed.append(target)
            detail_parts.append(f"{target}: {exc}")
            continue

        if proc.returncode != 0:
            stderr_last = one_line(proc.stderr.strip().splitlines()[-1] if proc.stderr else "", limit=300)
            detail_parts.append(f"{target}: exit {proc.returncode} ({stderr_last})")
            failed.append(target)

    ok = not failed
    sent_count = len(targets) - len(failed)

    if ok:
        detail = f"sent to {sent_count} target(s)"
    elif detail_parts:
        detail = f"sent to {sent_count} target(s), {len(failed)} failed; " + "; ".join(detail_parts)
    else:
        detail = f"sent to {sent_count} target(s), {len(failed)} failed"

    return NotifyResult(
        ok=ok,
        targets=targets,
        failed=failed,
        detail=detail,
        commands=commands,
    )


router = APIRouter(prefix="/notify", tags=["notify"])


@router.post("/test")
def notify_test(request: Request, payload: NotifyTestRequest | None = None) -> dict:
    """Manually fire a notification using the configured targets."""
    payload = payload or NotifyTestRequest()
    fake_run = {
        "id": "test",
        "session_id": payload.session_id,
        "project": payload.project,
        "outcome": payload.outcome,
        "summary": payload.summary,
    }
    subject, body = format_run_message(fake_run)
    result = notify_run(fake_run, request.app.state.settings)
    return {
        "ok": result.ok,
        "targets": result.targets,
        "failed": result.failed,
        "detail": result.detail,
        "subject": subject,
        "body": body,
    }
