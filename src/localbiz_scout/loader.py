"""Read a business CSV into records without modifying the source file.

The loader is intentionally strict enough to surface broken input as an
:class:`~localbiz_scout.errors.InputError`, but lenient about things that are
legitimate in the wild (a UTF-8 BOM, blank lines, padded header names).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .errors import InputError


@dataclass(frozen=True)
class LoadedCSV:
    """Result of reading an input CSV."""

    path: Path
    columns: tuple[str, ...]
    records: tuple[dict[str, str], ...]


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

    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise InputError(
            f"input file is not valid UTF-8 text: {csv_path} ({error})"
        ) from error
    except OSError as error:
        raise InputError(f"could not read input file {csv_path}: {error}") from error

    reader = csv.reader(io.StringIO(text))

    try:
        raw_header = next(reader)
    except StopIteration:
        raise InputError(
            f"input file is empty and has no header row: {csv_path}"
        ) from None
    except csv.Error as error:
        raise InputError(f"could not parse CSV header in {csv_path}: {error}") from error

    columns = tuple(cell.strip() for cell in raw_header)
    if not any(columns):
        raise InputError(f"input file has an empty header row: {csv_path}")

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
        raise InputError(f"could not parse {csv_path}: {error}") from error

    return LoadedCSV(path=csv_path, columns=columns, records=tuple(records))
