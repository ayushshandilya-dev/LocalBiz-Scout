"""Human remediation decisions (Phase 3.0: model, parser, validation only).

This module defines the *decision-file contract*: a versioned JSON document in
which a human records what to do about the duplicate candidates a scan surfaced
(and, later, about individual cell corrections). Phase 3.0 deliberately stops at
the data model, the parser, and validation:

* no correction is generated or applied;
* no record is modified, merged, or deleted;
* no file is read or written, and nothing touches the network;
* the optional AI adjudicator is never involved.

Versioned JSON contract
-----------------------

The document is a single JSON object with these keys::

    {
      "version": 1,
      "duplicates": [ <duplicate-decision>, ... ],   # optional, default []
      "corrections": [ <correction-decision>, ... ]  # optional, default []
    }

``version`` is required and must be the integer ``1``. ``duplicates`` and
``corrections`` are optional lists; any *other* top-level key is rejected.

A duplicate decision (``<duplicate-decision>``) is::

    {
      "business_id_a": "<stable id>",   # required, non-empty string
      "business_id_b": "<stable id>",   # required, non-empty string
      "decision": "confirmed" | "rejected" | "deferred",   # required
      "rationale": "<text>"             # optional string
    }

The two IDs are canonicalized by sorting, so ``(b2, b1)`` and ``(b1, b2)`` are
the same pair. Self-pairs are rejected, and two entries for the same canonical
pair (in any order) are rejected. ``confirmed`` means only that a human believes
the two records describe the same business; it never triggers a merge, a delete,
or any change to either record. ``deferred`` (not yet decided) is distinct from
``rejected``.

A correction decision (``<correction-decision>``), whose *schema* is fixed here
for later phases, is::

    {
      "business_id": "<stable id>",     # required, non-empty string
      "field": "<column name>",         # required, non-empty string
      "rule_id": "<rule id>",           # required, non-empty string
      "proposed_value": "<text>",       # required string (may be empty)
      "decision": "accepted" | "rejected" | "deferred",   # required
      "rationale": "<text>"             # optional string
    }

Corrections are validated *structurally only*. Phase 3.0 cannot judge whether a
proposed value is safe or correct; that requires the correction allowlist in a
later phase. A structurally valid correction is therefore accepted here without
any claim that it is safe to apply.

Uniqueness rules
----------------

* Duplicate decisions: unique by canonical pair ``(min(id_a, id_b),
  max(id_a, id_b))``.
* Correction decisions: unique by the triple ``(business_id, field, rule_id)``.

Stable record identifiers are the values produced by
:func:`localbiz_scout.rules.records.get_record_id` (the configured ``id_field``,
or a positional ``row-####`` fallback). :func:`stable_record_ids` derives them
from records so callers never have to re-implement that rule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .errors import InputError
from .rules.config import RuleConfig
from .rules.records import get_record_id

__all__ = [
    "DECISIONS_SCHEMA_VERSION",
    "DUPLICATE_DECISIONS",
    "CORRECTION_DECISIONS",
    "DUPLICATE_DECISION_CONFIRMED",
    "DUPLICATE_DECISION_REJECTED",
    "DUPLICATE_DECISION_DEFERRED",
    "CORRECTION_DECISION_ACCEPTED",
    "CORRECTION_DECISION_REJECTED",
    "CORRECTION_DECISION_DEFERRED",
    "DuplicateDecision",
    "CorrectionDecision",
    "DecisionSet",
    "canonical_pair",
    "stable_record_ids",
    "duplicate_pairs",
    "parse_decisions",
    "validate_decisions",
    "load_decisions",
]

DECISIONS_SCHEMA_VERSION = 1

DUPLICATE_DECISION_CONFIRMED = "confirmed"
DUPLICATE_DECISION_REJECTED = "rejected"
DUPLICATE_DECISION_DEFERRED = "deferred"
DUPLICATE_DECISIONS: tuple[str, ...] = (
    DUPLICATE_DECISION_CONFIRMED,
    DUPLICATE_DECISION_REJECTED,
    DUPLICATE_DECISION_DEFERRED,
)

CORRECTION_DECISION_ACCEPTED = "accepted"
CORRECTION_DECISION_REJECTED = "rejected"
CORRECTION_DECISION_DEFERRED = "deferred"
CORRECTION_DECISIONS: tuple[str, ...] = (
    CORRECTION_DECISION_ACCEPTED,
    CORRECTION_DECISION_REJECTED,
    CORRECTION_DECISION_DEFERRED,
)

_TOP_LEVEL_KEYS = frozenset({"version", "duplicates", "corrections"})
_DUPLICATE_KEYS = frozenset({"business_id_a", "business_id_b", "decision", "rationale"})
_DUPLICATE_REQUIRED = ("business_id_a", "business_id_b", "decision")
_CORRECTION_KEYS = frozenset(
    {"business_id", "field", "rule_id", "proposed_value", "decision", "rationale"}
)
_CORRECTION_REQUIRED = ("business_id", "field", "rule_id", "proposed_value", "decision")


@dataclass(frozen=True)
class DuplicateDecision:
    """A human decision about one unordered pair of records.

    ``business_id_a`` and ``business_id_b`` are stored in canonical (sorted)
    order. ``decision`` is one of :data:`DUPLICATE_DECISIONS`. This object is a
    record of intent only; it never causes a merge, delete, or edit.
    """

    business_id_a: str
    business_id_b: str
    decision: str
    rationale: str | None = None


@dataclass(frozen=True)
class CorrectionDecision:
    """A human decision about one proposed cell correction (schema only).

    Phase 3.0 does not generate or apply corrections; this type fixes the
    decision-file contract. ``proposed_value`` is a string (possibly empty).
    """

    business_id: str
    field: str
    rule_id: str
    proposed_value: str
    decision: str
    rationale: str | None = None


@dataclass(frozen=True)
class DecisionSet:
    """A parsed, structurally validated decision file."""

    duplicates: tuple[DuplicateDecision, ...] = ()
    corrections: tuple[CorrectionDecision, ...] = ()
    version: int = DECISIONS_SCHEMA_VERSION


def canonical_pair(id_a: str, id_b: str) -> tuple[str, str]:
    """Return the two identifiers sorted, so ``(b, a)`` and ``(a, b)`` match."""

    first, second = sorted((id_a.strip(), id_b.strip()))
    return first, second


def stable_record_ids(
    records: Sequence[Mapping[str, Any]],
    config: RuleConfig,
) -> tuple[str, ...]:
    """Stable identifiers for ``records``, using the shared id rule.

    Reuses :func:`get_record_id` so identifiers match findings and duplicate
    candidates exactly, including the ``row-####`` fallback for blank ids.
    """

    return tuple(
        get_record_id(record, config, index) for index, record in enumerate(records)
    )


def duplicate_pairs(
    candidates: Iterable[Any],
) -> tuple[tuple[str, str], ...]:
    """Canonical ``(id_a, id_b)`` pairs for duplicate-candidate objects.

    Accepts anything exposing ``business_id_a`` / ``business_id_b`` (for example
    :class:`~localbiz_scout.rules.findings.DuplicateCandidate`). The caller's
    objects are only read, never modified.
    """

    return tuple(
        canonical_pair(candidate.business_id_a, candidate.business_id_b)
        for candidate in candidates
    )


def parse_decisions(text: str) -> DecisionSet:
    """Parse and structurally validate a decisions JSON ``text``.

    Raises :class:`~localbiz_scout.errors.InputError` for invalid JSON, a
    non-object top level, an unsupported ``version``, unknown or missing keys,
    wrong types, unsupported decision values, self-pairs, or duplicate entries.
    This performs no I/O and does not look at any business records.
    """

    if not isinstance(text, str):
        raise InputError("decisions: expected a JSON string")

    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise InputError(
            f"decisions: invalid JSON at line {error.lineno} "
            f"column {error.colno}: {error.msg}"
        ) from error

    if not isinstance(document, dict):
        raise InputError("decisions: top level must be a JSON object")

    _reject_unknown_keys(document, _TOP_LEVEL_KEYS, "decisions")

    if "version" not in document:
        raise InputError("decisions: missing required key 'version'")
    version = document["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise InputError("decisions: 'version' must be an integer")
    if version != DECISIONS_SCHEMA_VERSION:
        raise InputError(
            f"decisions: unsupported schema version {version} "
            f"(expected {DECISIONS_SCHEMA_VERSION})"
        )

    duplicates = _parse_duplicates(document.get("duplicates", []))
    corrections = _parse_corrections(document.get("corrections", []))

    return DecisionSet(duplicates=duplicates, corrections=corrections, version=version)


def validate_decisions(
    decisions: DecisionSet,
    *,
    record_ids: Iterable[str],
    candidate_pairs: Iterable[tuple[str, str]],
) -> None:
    """Check a parsed decision set against the current analysis.

    Every duplicate decision must reference two known record identifiers and a
    pair that is actually an emitted duplicate candidate. Correction decisions
    are **not** validated semantically here (that needs the future allowlist);
    only their structure was checked in :func:`parse_decisions`.

    Nothing supplied by the caller is mutated. Raises
    :class:`~localbiz_scout.errors.InputError` on the first problem found.
    """

    if not isinstance(decisions, DecisionSet):
        raise InputError("decisions: expected a DecisionSet")

    known_ids = {str(value) for value in record_ids}
    known_pairs = {
        canonical_pair(str(id_a), str(id_b)) for id_a, id_b in candidate_pairs
    }

    for decision in decisions.duplicates:
        for label, identifier in (
            ("business_id_a", decision.business_id_a),
            ("business_id_b", decision.business_id_b),
        ):
            if identifier not in known_ids:
                raise InputError(
                    f"decisions: unknown record id {identifier!r} "
                    f"in duplicate decision {label}"
                )
        pair = (decision.business_id_a, decision.business_id_b)
        if pair not in known_pairs:
            raise InputError(
                f"decisions: pair {pair!r} is not a duplicate candidate "
                f"from the current analysis"
            )


def load_decisions(
    text: str,
    *,
    record_ids: Iterable[str],
    candidate_pairs: Iterable[tuple[str, str]],
) -> DecisionSet:
    """Parse then validate a decisions document in one step."""

    decisions = parse_decisions(text)
    validate_decisions(
        decisions, record_ids=record_ids, candidate_pairs=candidate_pairs
    )
    return decisions


# --------------------------------------------------------------------------- #
# Structural validation helpers
# --------------------------------------------------------------------------- #


def _parse_duplicates(raw: Any) -> tuple[DuplicateDecision, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise InputError("decisions.duplicates: must be a list")

    seen: dict[tuple[str, str], int] = {}
    parsed: list[DuplicateDecision] = []

    for index, entry in enumerate(raw):
        context = f"decisions.duplicates[{index}]"
        _require_object(entry, context)
        _reject_unknown_keys(entry, _DUPLICATE_KEYS, context)
        _require_keys(entry, _DUPLICATE_REQUIRED, context)

        id_a = _require_non_empty_str(entry["business_id_a"], "business_id_a", context)
        id_b = _require_non_empty_str(entry["business_id_b"], "business_id_b", context)
        decision = _require_decision(entry["decision"], DUPLICATE_DECISIONS, context)
        rationale = _optional_rationale(entry.get("rationale"), context)

        if id_a == id_b:
            raise InputError(
                f"{context}: self-pair for business_id {id_a!r} is not allowed"
            )

        pair = canonical_pair(id_a, id_b)
        if pair in seen:
            raise InputError(
                f"{context}: duplicate decision for pair {pair!r}; already "
                f"defined at decisions.duplicates[{seen[pair]}]"
            )
        seen[pair] = index
        parsed.append(
            DuplicateDecision(
                business_id_a=pair[0],
                business_id_b=pair[1],
                decision=decision,
                rationale=rationale,
            )
        )

    return tuple(parsed)


def _parse_corrections(raw: Any) -> tuple[CorrectionDecision, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise InputError("decisions.corrections: must be a list")

    seen: dict[tuple[str, str, str], int] = {}
    parsed: list[CorrectionDecision] = []

    for index, entry in enumerate(raw):
        context = f"decisions.corrections[{index}]"
        _require_object(entry, context)
        _reject_unknown_keys(entry, _CORRECTION_KEYS, context)
        _require_keys(entry, _CORRECTION_REQUIRED, context)

        business_id = _require_non_empty_str(
            entry["business_id"], "business_id", context
        )
        field = _require_non_empty_str(entry["field"], "field", context)
        rule_id = _require_non_empty_str(entry["rule_id"], "rule_id", context)
        proposed_value = _require_str(entry["proposed_value"], "proposed_value", context)
        decision = _require_decision(entry["decision"], CORRECTION_DECISIONS, context)
        rationale = _optional_rationale(entry.get("rationale"), context)

        key = (business_id, field, rule_id)
        if key in seen:
            raise InputError(
                f"{context}: duplicate correction for "
                f"(business_id={business_id!r}, field={field!r}, rule_id={rule_id!r}); "
                f"already defined at decisions.corrections[{seen[key]}]"
            )
        seen[key] = index
        parsed.append(
            CorrectionDecision(
                business_id=business_id,
                field=field,
                rule_id=rule_id,
                proposed_value=proposed_value,
                decision=decision,
                rationale=rationale,
            )
        )

    return tuple(parsed)


def _require_object(value: Any, context: str) -> None:
    if not isinstance(value, dict):
        raise InputError(f"{context}: must be a JSON object")


def _reject_unknown_keys(
    mapping: Mapping[str, Any], allowed: frozenset[str], context: str
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        rendered = ", ".join(repr(key) for key in unknown)
        raise InputError(f"{context}: unknown key(s): {rendered}")


def _require_keys(
    entry: Mapping[str, Any], required: Sequence[str], context: str
) -> None:
    missing = [key for key in required if key not in entry]
    if missing:
        rendered = ", ".join(missing)
        raise InputError(f"{context}: missing required key(s): {rendered}")


def _require_str(value: Any, key: str, context: str) -> str:
    if not isinstance(value, str):
        raise InputError(f"{context}: {key!r} must be a string")
    return value


def _require_non_empty_str(value: Any, key: str, context: str) -> str:
    text = _require_str(value, key, context)
    stripped = text.strip()
    if not stripped:
        raise InputError(f"{context}: {key!r} must be a non-empty string")
    return stripped


def _require_decision(value: Any, allowed: tuple[str, ...], context: str) -> str:
    if not isinstance(value, str):
        raise InputError(f"{context}: 'decision' must be a string")
    if value not in allowed:
        raise InputError(
            f"{context}: unsupported decision {value!r}; expected one of "
            f"{', '.join(allowed)}"
        )
    return value


def _optional_rationale(value: Any, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputError(f"{context}: 'rationale' must be a string when present")
    return value
