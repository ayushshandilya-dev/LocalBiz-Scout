"""Local-only web interface for LocalBiz Scout.

The public surface is the in-memory analysis entry point
(:func:`analyze_upload`) plus the loopback-only server factory
(:func:`create_server`). Uploaded records are processed in memory and are never
written to disk or sent anywhere.
"""

from __future__ import annotations

from .server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_UPLOAD_BYTES,
    ScanHTTPServer,
    create_server,
    is_oversized_content_length,
    resolve_bind,
)
from .view import DuplicateView, FindingView, ScanView, analyze_upload

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_UPLOAD_BYTES",
    "ScanHTTPServer",
    "ScanView",
    "FindingView",
    "DuplicateView",
    "analyze_upload",
    "create_server",
    "is_oversized_content_length",
    "resolve_bind",
]
