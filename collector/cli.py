from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from collector.bundle import (
    build_bundle,
    manifest_json_bytes,
    write_bundle_artifacts,
)
from collector.client import format_ingest_response, post_ingest
from collector.discovery import discover_sessions

DEFAULT_SERVER = "http://127.0.0.1:8791"
ENV_SERVER_URL = "NIGHTSHIFT_SERVER_URL"


def _parse_since(value: str) -> date:
    """Parse YYYY-MM-DD for the --since filter."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; use YYYY-MM-DD"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    """Configure the collector CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="collector",
        description="Discover Claude Code session jsonl files and POST them to nightshift.",
    )
    parser.add_argument(
        "--claude-dir",
        type=Path,
        default=Path("~/.claude/projects"),
        help="Root directory to scan (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        help="Only sessions with file mtime on/after this UTC date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        default=None,
        metavar="GLOB",
        help="Fnmatch filter on project name; repeatable",
    )
    parser.add_argument(
        "--server",
        default=None,
        help=f"Ingest server base URL (default: {DEFAULT_SERVER}, env: {ENV_SERVER_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="DIR",
        help="Also write bundle zip and manifest.json under DIR",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build bundle and print manifest; do not POST",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-file progress on stderr",
    )
    return parser


def _resolve_server(explicit: str | None) -> str:
    """CLI --server wins over NIGHTSHIFT_SERVER_URL env."""
    if explicit is not None:
        return explicit
    return os.environ.get(ENV_SERVER_URL, DEFAULT_SERVER)


def _print_dry_run(manifest: dict[str, object], server_url: str) -> None:
    """Print manifest JSON and a per-session summary table."""
    print(json.dumps(manifest, indent=2, sort_keys=True))
    sessions = manifest.get("sessions")
    if not isinstance(sessions, list):
        return
    print()
    print(f"Target: {server_url.rstrip('/')}/sessions/ingest")
    print(f"{'project':<30} {'session_id':<38} {'size':>10}  sha256 (prefix)")
    print("-" * 100)
    for row in sessions:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project", ""))
        session_id = str(row.get("session_id", ""))
        size = row.get("size", 0)
        digest = str(row.get("sha256", ""))
        prefix = digest[:16] if digest else ""
        print(f"{project:<30} {session_id:<38} {size:>10}  {prefix}")


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m collector``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    claude_dir = args.claude_dir.expanduser()
    server_url = _resolve_server(args.server)

    candidates = discover_sessions(
        claude_dir,
        since=args.since,
        project_globs=args.projects,
    )

    if not candidates:
        print("No matching sessions found.", file=sys.stderr)
        return 0

    if args.verbose:
        for candidate in candidates:
            print(f"pack {candidate.path} -> {candidate.zip_entry_name}", file=sys.stderr)

    result = build_bundle(candidates)
    manifest_bytes = manifest_json_bytes(result.manifest)

    if args.out is not None:
        host = str(result.manifest.get("host", "bundle"))
        zip_path, manifest_path = write_bundle_artifacts(
            args.out,
            result.zip_bytes,
            result.manifest,
            host=host,
        )
        if args.verbose:
            print(f"wrote {zip_path}", file=sys.stderr)
            print(f"wrote {manifest_path}", file=sys.stderr)

    if args.dry_run:
        _print_dry_run(result.manifest, server_url)
        return 0

    try:
        status, body = post_ingest(
            server_url,
            result.zip_bytes,
            manifest_bytes,
            timeout=args.timeout,
        )
    except ConnectionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except TimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if status < 200 or status >= 300:
        print(body, file=sys.stderr)
        return 1

    print(format_ingest_response(body))
    return 0
