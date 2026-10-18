"""Read a business CSV into records without modifying the source file.

The loader is intentionally strict enough to surface broken input as an
:class:`~localbiz_scout.errors.InputError`, but lenient about things that are
legitimate in the wild (a UTF-8 BOM, blank lines, padded header names). Line
endings inside quoted fields are preserved exactly, so a round-trip export
never alters a cell value the reviewer did not approve.

Two entry points share exactly the same validation logic:

* :func:`load_records` reads a file from disk.
* :func:`load_records_from_text` parses already-decoded CSV text (for example an
  in-memory upload), so callers never need to write a temporary file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .errors import InputError

# Label used in error messages when a caller supplies no source name.
_UNKNOWN_SOURCE = "<input>"


@dataclass(frozen=True)
class LoadedCSV:
    """Result of reading an input CSV."""

    path: Path
    columns: tuple[str, ...]
    records: tuple[dict[str, str], ...]


def _parse_records(
    text: str,
    id_field: str,
    source: str,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """Validate decoded CSV ``text`` and return ``(columns, records)``.

    ``source`` is only used in error messages. A single leading UTF-8 BOM is
    tolerated here as well, so in-memory callers get the same leniency as the
    file-based entry point even if they decoded without ``utf-8-sig``.
    """

    if text.startswith("\ufeff"):
        text = text[1:]

    reader = csv.reader(io.StringIO(text))

    try:
        raw_header = next(reader)
    except StopIteration:
        raise InputError(
            f"input file is empty and has no header row: {source}"
        ) from None
    except csv.Error as error:
        raise InputError(f"could not parse CSV header in {source}: {error}") from error

    columns = tuple(cell.strip() for cell in raw_header)
    if not any(columns):
        raise InputError(f"input file has an empty header row: {source}")

    if id_field not in columns:
        found = ", ".join(repr(column) for column in columns) or "<none>"
        raise InputError(
            f"input file is missing the required identifier column "
            f"{id_field!r}; columns found: {found}"
        )

    records: list[dict[str, str]] = []
    expected = len(columns)

    try:
        for line_number, row in enumerate(reader, start=2):
            if not row or all(cell == "" for cell in row):
                continue
            if len(row) != expected:
                raise InputError(
                    f"malformed CSV: row {line_number} has {len(row)} fields "
                    f"but the header declares {expected}"
                )
            records.append(dict(zip(columns, row)))
    except csv.Error as error:
        raise InputError(f"could not parse {source}: {error}") from error

    return columns, tuple(records)


def load_records(path: str | Path, id_field: str) -> LoadedCSV:
    """Load ``path`` and validate that ``id_field`` is present.

    Raises :class:`InputError` with a human-readable message when the file is
    missing, unreadable, empty, lacks the identifier column, or contains rows
    with an inconsistent number of fields.
    """

    csv_path = Path(path)

    if not csv_path.exists():
        raise InputError(f"input file not found: {csv_path}")
    if not csv_path.is_dir() and not csv_path.is_file():
        raise InputError(f"input path is not a file: {csv_path}")
    if csv_path.is_dir():
        raise InputError(f"input path is a directory, expected a CSV file: {csv_path}")

    # Read with ``newline=""`` (the documented csv convention) so that line
    # endings *inside* quoted fields are preserved verbatim. Reading with the
    # default universal-newline translation would silently rewrite an embedded
    # CRLF as LF, changing a cell value the reviewer never approved.
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError as error:
        raise InputError(
            f"input file is not valid UTF-8 text: {csv_path} ({error})"
        ) from error
    except OSError as error:
        raise InputError(f"could not read input file {csv_path}: {error}") from error

    columns, records = _parse_records(text, id_field, str(csv_path))
    return LoadedCSV(path=csv_path, columns=columns, records=records)


def load_records_from_text(
    text: str,
    id_field: str,
    source: str | Path | None = None,
) -> LoadedCSV:
    """Parse already-decoded CSV ``text`` with the same rules as
    :func:`load_records`.

    Intended for in-memory input such as an upload: no file is read or written.
    ``source`` is an optional label used only in error messages and as the
    :attr:`LoadedCSV.path` value; it is never opened.
    """

    if not isinstance(text, str):
        raise InputError("expected CSV text, not raw bytes")

    label = _UNKNOWN_SOURCE if source is None else str(source)
    columns, records = _parse_records(text, id_field, label)
    return LoadedCSV(path=Path(label), columns=columns, records=records)
