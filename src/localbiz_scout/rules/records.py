"""Small helpers for reading records without mutating them."""

from __future__ import annotations

from typing import Any, Mapping

from .config import RuleConfig


def get_record_id(record: Mapping[str, Any], config: RuleConfig, index: int) -> str:
    """Return the identifier used by findings for a record.

    Falls back to a positional identifier when the configured id field is
    absent, so rules never crash on a partially-formed row.
    """

    value = record.get(config.id_field)
    if value is None or str(value).strip() == "":
        return f"row-{index:04d}"
    return str(value).strip()
