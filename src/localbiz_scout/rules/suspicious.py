"""Rule: a small set of explainable deterministic "suspicious value" checks.

Every check here is a *format* sanity check supported by the synthetic schema.
None of them infer that a business is fake or fraudulent; they only flag values
that do not match an expected shape so a human can review them.

Checks implemented:

* ``phone`` has an unexpected number of digits (North-American 10-digit
  numbers; a single leading country code ``1`` is accepted).
* ``state`` is not a two-letter code.
* ``postal_code`` is not a 5-digit or 5+4 US ZIP code.
* ``name`` is a known placeholder token or contains no letters/digits.

Documented limitations
----------------------
* These checks are US-centric and will flag legitimately-formatted foreign
  addresses/phone numbers.
* Cross-field consistency (for example, "does this city belong to this
  state?") is **not** checked, because the schema has no reference data to
  validate against.
* Spelling/casing of names is not judged as suspicious; only the placeholder
  check applies.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .config import RuleConfig
from .findings import (
    RULE_PLACEHOLDER_NAME,
    RULE_SUSPICIOUS_PHONE_LENGTH,
    RULE_SUSPICIOUS_POSTAL_CODE,
    RULE_SUSPICIOUS_STATE_CODE,
    Finding,
)
from .normalization import as_text, is_blank, normalize_phone, normalize_text
from .records import get_record_id

_EXPECTED_PHONE_DIGITS = 10
_STATE_RE = re.compile(r"^[A-Za-z]{2}$")
_POSTAL_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")

PLACEHOLDER_NAMES: frozenset[str] = frozenset(
    {
        "n/a",
        "na",
        "none",
        "null",
        "nil",
        "test",
        "testing",
        "unknown",
        "tbd",
        "placeholder",
        "xxx",
        "example",
        "foobar",
    }
)


def check_suspicious_fields(
    records: Sequence[Mapping[str, Any]],
    config: RuleConfig,
) -> list[Finding]:
    findings: list[Finding] = []

    for index, record in enumerate(records):
        business_id = get_record_id(record, config, index)
        findings.extend(_check_phone(record, business_id))
        findings.extend(_check_state(record, business_id))
        findings.extend(_check_postal_code(record, business_id))
        findings.extend(_check_name(record, business_id))

    return findings


def _check_phone(record: Mapping[str, Any], business_id: str) -> list[Finding]:
    if "phone" not in record:
        return []
    value = record.get("phone")
    if is_blank(value):
        return []

    digits = normalize_phone(value)
    if len(digits) != _EXPECTED_PHONE_DIGITS:
        return [
            Finding(
                rule_id=RULE_SUSPICIOUS_PHONE_LENGTH,
                business_id=business_id,
                field="phone",
                message=(
                    f"Phone number has {len(digits)} digits; expected "
                    f"{_EXPECTED_PHONE_DIGITS} for a North-American number."
                ),
                evidence={"raw_value": as_text(value), "digits": digits},
            )
        ]
    return []


def _check_state(record: Mapping[str, Any], business_id: str) -> list[Finding]:
    if "state" not in record:
        return []
    value = record.get("state")
    if is_blank(value):
        return []

    if not _STATE_RE.match(as_text(value).strip()):
        return [
            Finding(
                rule_id=RULE_SUSPICIOUS_STATE_CODE,
                business_id=business_id,
                field="state",
                message="State is not a two-letter code.",
                evidence={"raw_value": as_text(value)},
            )
        ]
    return []


def _check_postal_code(record: Mapping[str, Any], business_id: str) -> list[Finding]:
    if "postal_code" not in record:
        return []
    value = record.get("postal_code")
    if is_blank(value):
        return []

    if not _POSTAL_RE.match(as_text(value).strip()):
        return [
            Finding(
                rule_id=RULE_SUSPICIOUS_POSTAL_CODE,
                business_id=business_id,
                field="postal_code",
                message="Postal code is not a 5-digit or 5+4 US ZIP code.",
                evidence={"raw_value": as_text(value)},
            )
        ]
    return []


def _check_name(record: Mapping[str, Any], business_id: str) -> list[Finding]:
    if "name" not in record:
        return []
    value = record.get("name")
    if is_blank(value):
        return []

    normalized = normalize_text(value)
    has_alnum = _ALNUM_RE.search(normalized) is not None
    if normalized in PLACEHOLDER_NAMES or not has_alnum:
        return [
            Finding(
                rule_id=RULE_PLACEHOLDER_NAME,
                business_id=business_id,
                field="name",
                message="Name looks like a placeholder rather than a real business name.",
                evidence={"raw_value": as_text(value)},
            )
        ]
    return []
