"""Deterministic, conservative correction *proposals* (Phase 3.1).

This module reads business records and suggests a small, explicitly allowlisted
set of safe formatting fixes. A proposal is **data only**:

* it never modifies, merges, or deletes a record;
* it does not read or write files and never touches the network;
* it does not call the optional AI adjudicator;
* it does not decide anything -- a proposal is a *suggestion for a human*, not
  an approval and certainly not an applied change.

Applying accepted proposals is a later phase. This module is deliberately
independent of the decision-application mechanism.

Relationship to the Phase 3.0 decision model
--------------------------------------------

:mod:`localbiz_scout.remediation` defines the versioned decision *file* that a
human writes. Its ``CorrectionDecision`` uses the keys ``business_id``,
``field``, ``rule_id``, ``proposed_value``, ``decision`` and ``rationale``.
A :class:`CorrectionProposal` here supplies exactly the first four of those
(plus ``original_value`` and ``evidence``); a human then adds a ``decision`` to
turn it into a ``CorrectionDecision``. No second decision schema is introduced.

Allowlist v1
------------

Four rules, applied to a whole-cell value. Every rule is a pure function of one
string cell and produces no proposal when it would leave the value unchanged.

.. list-table::
   :header-rows: 1

   * - Rule id
     - Field
     - Precondition and transformation
   * - ``correct_name_whitespace``
     - ``name``
     - Value is non-blank; leading/trailing whitespace is trimmed and every
       internal whitespace run collapses to a single ASCII space. Case,
       punctuation and all non-whitespace characters are preserved.
   * - ``correct_state_uppercase``
     - ``state``
     - The *trimmed* value is exactly two ASCII letters; surrounding whitespace
       is removed and the letters are uppercased. No geographic meaning is
       inferred or validated.
   * - ``correct_postal_zip4``
     - ``postal_code``
     - The value is exactly nine ASCII digits; it becomes ``#####-####``.
       Five-digit ZIPs, alphanumeric codes and every other length are left
       alone. No country is inferred.
   * - ``correct_phone_us``
     - ``phone``
     - ``normalize_phone`` establishes exactly ten digits under its documented
       rules (it strips non-digits and drops a single leading North-American
       ``1``); the value becomes ``###-###-####``. Blanks and other lengths are
       left alone. Missing digits and country codes are never invented.

Not implemented, on purpose: address, city, website and email corrections,
generic field normalization, and any AI-generated correction.

Field names
-----------

Column names are not assumed to be universal. :class:`CorrectionFields` maps
each logical field to the actual column name and defaults to the canonical
schema (``name``/``state``/``postal_code``/``phone``). A rule only fires when
its column is present in the record, so a dataset that names a column
differently simply produces no proposal unless the caller supplies the mapping.

Determinism, ordering and safety
--------------------------------

* Records are visited in the order given; within a record the four rules run in
  the fixed order above. The result is therefore identical for identical input.
* A record's ``business_id`` is the value from :func:`get_record_id`, matching
  findings and duplicate candidates (including the positional ``row-####``
  fallback when the id cell is blank).
* The identifier column is never targeted, and a column receives at most one
  proposal per record (the first rule that fires wins).
* A field that is absent, or whose value is not a string, produces no proposal.
* The caller's records -- including nested data -- are only read, never
  changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .rules.config import DEFAULT_CONFIG, RuleConfig
from .rules.normalization import collapse_whitespace, is_blank, normalize_phone
from .rules.records import get_record_id

__all__ = [
    "CORRECTION_RULE_NAME_WHITESPACE",
    "CORRECTION_RULE_STATE_UPPERCASE",
    "CORRECTION_RULE_POSTAL_ZIP4",
    "CORRECTION_RULE_PHONE_US",
    "CORRECTION_RULES",
    "CorrectionFields",
    "CorrectionProposal",
    "propose_corrections",
]

# Stable rule identifiers (part of the proposal contract; covered by tests).
CORRECTION_RULE_NAME_WHITESPACE = "correct_name_whitespace"
CORRECTION_RULE_STATE_UPPERCASE = "correct_state_uppercase"
CORRECTION_RULE_POSTAL_ZIP4 = "correct_postal_zip4"
CORRECTION_RULE_PHONE_US = "correct_phone_us"

CORRECTION_RULES: tuple[str, ...] = (
    CORRECTION_RULE_NAME_WHITESPACE,
    CORRECTION_RULE_STATE_UPPERCASE,
    CORRECTION_RULE_POSTAL_ZIP4,
    CORRECTION_RULE_PHONE_US,
)

_RATIONALE: dict[str, str] = {
    CORRECTION_RULE_NAME_WHITESPACE: (
        "Trimmed surrounding whitespace and collapsed repeated internal "
        "whitespace."
    ),
    CORRECTION_RULE_STATE_UPPERCASE: "Uppercased a two-letter ASCII state code.",
    CORRECTION_RULE_POSTAL_ZIP4: "Formatted nine ASCII digits as a US ZIP+4 code.",
    CORRECTION_RULE_PHONE_US: (
        "Formatted a ten-digit US phone number as ###-###-####."
    ),
}


@dataclass(frozen=True)
class CorrectionFields:
    """Logical-field to column-name mapping used by :func:`propose_corrections`.

    The defaults match the project's canonical schema. Supply a different
    mapping for datasets whose columns are named differently.
    """

    name: str = "name"
    state: str = "state"
    postal_code: str = "postal_code"
    phone: str = "phone"


@dataclass(frozen=True)
class CorrectionProposal:
    """A suggested, not-applied formatting fix for one record cell."""

    business_id: str
    field: str
    original_value: str
    proposed_value: str
    rule_id: str
    rationale: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "field": self.field,
            "original_value": self.original_value,
            "proposed_value": self.proposed_value,
            "rule_id": self.rule_id,
            "rationale": self.rationale,
            "evidence": dict(self.evidence),
        }


# A rule function maps a single string cell to ``(proposed_value, evidence)``
# or ``None`` when the rule does not apply.
_Proposer = Callable[[str], "tuple[str, dict[str, Any]] | None"]


def _propose_name_whitespace(value: str) -> tuple[str, dict[str, Any]] | None:
    if value.strip() == "":
        return None
    proposed = collapse_whitespace(value)
    if proposed == value:
        return None
    return proposed, {"original_length": len(value), "proposed_length": len(proposed)}


def _propose_state_uppercase(value: str) -> tuple[str, dict[str, Any]] | None:
    trimmed = value.strip()
    if len(trimmed) != 2 or not trimmed.isascii() or not trimmed.isalpha():
        return None
    proposed = trimmed.upper()
    if proposed == value:
        return None
    return proposed, {"letter_count": 2}


def _propose_postal_zip4(value: str) -> tuple[str, dict[str, Any]] | None:
    if len(value) != 9 or not value.isascii() or not value.isdigit():
        return None
    proposed = f"{value[:5]}-{value[5:]}"
    if proposed == value:
        return None
    return proposed, {"digit_count": 9}


def _propose_phone_us(value: str) -> tuple[str, dict[str, Any]] | None:
    if is_blank(value):
        return None
    digits = normalize_phone(value)
    if len(digits) != 10:
        return None
    proposed = f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    if proposed == value:
        return None
    return proposed, {"digit_count": 10, "digits": digits}


# Fixed evaluation order: (rule_id, CorrectionFields attribute, rule function).
_RULE_SPECS: tuple[tuple[str, str, _Proposer], ...] = (
    (CORRECTION_RULE_NAME_WHITESPACE, "name", _propose_name_whitespace),
    (CORRECTION_RULE_STATE_UPPERCASE, "state", _propose_state_uppercase),
    (CORRECTION_RULE_POSTAL_ZIP4, "postal_code", _propose_postal_zip4),
    (CORRECTION_RULE_PHONE_US, "phone", _propose_phone_us),
)


def propose_corrections(
    records: Sequence[Mapping[str, Any]],
    config: RuleConfig = DEFAULT_CONFIG,
    fields: CorrectionFields | None = None,
) -> tuple[CorrectionProposal, ...]:
    """Return the allowlisted correction proposals for ``records``.

    Deterministic and read-only: the same records, mapping and configuration
    always yield the same proposals in the same order. Inputs are never
    modified.
    """

    resolved = CorrectionFields() if fields is None else fields
    proposals: list[CorrectionProposal] = []

    for index, record in enumerate(records):
        business_id = get_record_id(record, config, index)
        claimed: set[str] = set()

        for rule_id, logical_field, propose in _RULE_SPECS:
            column = getattr(resolved, logical_field)
            if column == config.id_field or column in claimed:
                continue
            if column not in record:
                continue

            value = record[column]
            if not isinstance(value, str):
                continue

            result = propose(value)
            if result is None:
                continue

            proposed_value, evidence = result
            claimed.add(column)
            proposals.append(
                CorrectionProposal(
                    business_id=business_id,
                    field=column,
                    original_value=value,
                    proposed_value=proposed_value,
                    rule_id=rule_id,
                    rationale=_RATIONALE[rule_id],
                    evidence=evidence,
                )
            )

    return tuple(proposals)
