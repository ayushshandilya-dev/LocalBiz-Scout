"""User-facing error types for the CLI.

These exist so the command-line layer can turn expected failure modes (bad
input, unwritable output) into short, actionable messages instead of
tracebacks.
"""

from __future__ import annotations


class LocalBizScoutError(Exception):
    """Base class for expected, user-actionable errors."""


class InputError(LocalBizScoutError):
    """The input CSV is missing, unreadable, empty, or malformed."""


class OutputError(LocalBizScoutError):
    """The requested output directory or file could not be written."""
