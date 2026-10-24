"""PC-side collector: discover Claude Code sessions and POST them to nightshift."""

from __future__ import annotations

from collector.bundle import (
    BundleResult,
    ManifestSession,
    build_bundle,
    manifest_json_bytes,
    sha256_hex,
    write_bundle_artifacts,
    zip_entry_name_from_path_string,
)
from collector.cli import main
from collector.client import format_ingest_response, post_ingest
from collector.discovery import SessionCandidate, discover_sessions

__version__ = "0.1.0"

__all__ = [
    "BundleResult",
    "ManifestSession",
    "SessionCandidate",
    "__version__",
    "build_bundle",
    "discover_sessions",
    "format_ingest_response",
    "main",
    "manifest_json_bytes",
    "post_ingest",
    "sha256_hex",
    "write_bundle_artifacts",
    "zip_entry_name_from_path_string",
]
