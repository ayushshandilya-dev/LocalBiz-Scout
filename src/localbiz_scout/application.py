"""Pure application of validated correction decisions (Phase 3.2).

This module takes three in-memory inputs -- the source records, a validated
:class:`~localbiz_scout.remediation.DecisionSet`, and the
:class:`~localbiz_scout.corrections.CorrectionProposal` objects produced by
Phase 3.1 -- and returns a *new* result. It performs no filesystem I/O, no
network access, no AI calls, and never mutates its inputs.

It is the inverse of Phase 3.1: proposals *suggest*, decisions *choose*, and
this module *reports* the chosen outcome. It does not export anything.

Decision-to-proposal matching
-----------------------------

A proposal is uniquely identified within a record by the canonical triple
``(business_id, field, rule_id)`` (Phase 3.1 emits at most one proposal per
record-and-field, and Phase 3.0 rejects duplicate decision triples). A decision
is considered to govern a proposal only when its *full* key matches::

    business_id == proposal.business_id
    field       == proposal.field
    rule_id     == proposal.rule_id
    proposed_value == proposal.proposed_value

The proposed value is part of the identity on purpose: if a proposal is
regenerated with a different value, an earlier approval of the old value is
stale and must not be applied.

Rules
-----

* Only a decision whose value is ``accepted`` applies a change; ``rejected``
  and ``deferred`` leave the field untouched.
* A proposal with no governing decision is ``not_decided`` -- absence is never
  treated as approval.
* A structurally valid ``accepted`` decision that governs no generated proposal
  makes the whole operation fail closed with
  :class:`~localbiz_scout.errors.InputError`. Stale or mismatched approvals are
  never silently ignored.
* Two proposals for the same record and field make the operation fail closed.
* The record identifier column, and any column outside the Phase 3.1 allowlist,
  can never be changed.
* Input records (including nested objects), proposals and the decision set are
  never mutated. Output records are new top-level dicts.
* Duplicate decisions are carried through for reporting only. A ``confirmed``
  duplicate is a human note, never a merge or delete, and never changes a
  record.

Referential and duplicate-pair validation (does this pair actually exist?) stays
in :mod:`localbiz_scout.remediation`; this module only consumes a
:class:`DecisionSet`.

Summary counting
----------------

Every generated proposal lands in exactly one decision category, so
``accepted + rejected + deferred + not_decided == total_proposals``. ``applied``
and ``unapplied`` are a separate status dimension: ``applied`` counts audit
entries whose output record actually holds the proposed value (only ever the
accepted ones here), and ``unapplied == total_proposals - applied``. Duplicate
decisions are counted separately and never contribute to the correction counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .corrections import (
    CORRECTION_RULE_NAME_WHITESPACE,
    CORRECTION_RULE_PHONE_US,
    CORRECTION_RULE_POSTAL_ZIP4,
    CORRECTION_RULE_STATE_UPPERCASE,
    CorrectionFields,
    CorrectionProposal,
)
from .errors import InputError
from .remediation import (
    CORRECTION_DECISION_ACCEPTED,
    CORRECTION_DECISION_DEFERRED,
    CORRECTION_DECISION_REJECTED,
    DUPLICATE_DECISION_CONFIRMED,
    DUPLICATE_DECISION_DEFERRED,
    DUPLICATE_DECISION_REJECTED,
    CorrectionDecision,
    DecisionSet,
    DuplicateDecision,
)
from .rules.config import DEFAULT_CONFIG, RuleConfig
from .rules.records import get_record_id

__all__ = [
    "DECISION_NOT_DECIDED",
    "CorrectionAuditEntry",
    "ApplicationSummary",
    "DuplicateDecisionCounts",
    "DecisionApplicationResult",
    "apply_decisions",
]

# Audit `decision` value for a proposal that no decision governs. This is not a
# decision-file value (those live in remediation.CORRECTION_DECISIONS); it only
# ever appears in an audit entry.
DECISION_NOT_DECIDED = "not_decided"

# Each allowlisted rule targets exactly one logical field, resolved through
# CorrectionFields. This is how the allowlist is enforced on the way in.
_RULE_TO_FIELD_ATTR: dict[str, str] = {
    CORRECTION_RULE_NAME_WHITESPACE: "name",
    CORRECTION_RULE_STATE_UPPERCASE: "state",
    CORRECTION_RULE_POSTAL_ZIP4: "postal_code",
    CORRECTION_RULE_PHONE_US: "phone",
}


@dataclass(frozen=True)
class CorrectionAuditEntry:
    """The outcome recorded for exactly one generated proposal."""

    business_id: str
    field: str
    rule_id: str
    original_value: str
    proposed_value: str
    decision: str
    applied: bool
    rationale: str
    decision_rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "field": self.field,
            "rule_id": self.rule_id,
            "original_value": self.original_value,
            "proposed_value": self.proposed_value,
            "decision": self.decision,
            "applied": self.applied,
            "rationale": self.rationale,
            "decision_rationale": self.decision_rationale,
        }


@dataclass(frozen=True)
class ApplicationSummary:
    """Counts over generated proposals (corrections only)."""

    total_proposals: int
    accepted: int
    rejected: int
    deferred: int
    not_decided: int
    applied: int
    unapplied: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total_proposals": self.total_proposals,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "deferred": self.deferred,
            "not_decided": self.not_decided,
            "applied": self.applied,
            "unapplied": self.unapplied,
        }


@dataclass(frozen=True)
class DuplicateDecisionCounts:
    """Counts over duplicate decisions, kept separate from corrections."""

    confirmed: int
    rejected: int
    deferred: int
    total: int

    def to_dict(self) -> dict[str, int]:
        return {
            "confirmed": self.confirmed,
            "rejected": self.rejected,
            "deferred": self.deferred,
            "total": self.total,
        }


@dataclass(frozen=True)
class DecisionApplicationResult:
    """The applied outcome: new records, a full audit trail, and summaries."""

    records: tuple[dict[str, Any], ...]
    audit: tuple[CorrectionAuditEntry, ...]
    summary: ApplicationSummary
    duplicate_decisions: tuple[DuplicateDecision, ...] = ()
    duplicate_counts: DuplicateDecisionCounts = DuplicateDecisionCounts(0, 0, 0, 0)


def apply_decisions(
    records: Sequence[Mapping[str, Any]],
    decisions: DecisionSet,
    proposals: Sequence[CorrectionProposal],
    config: RuleConfig = DEFAULT_CONFIG,
    fields: CorrectionFields | None = None,
) -> DecisionApplicationResult:
    """Apply accepted correction decisions and return a new result.

    ``proposals`` must be the proposals generated from ``records`` (Phase 3.1)
    using the same ``fields`` mapping. Anything inconsistent fails closed with
    :class:`~localbiz_scout.errors.InputError` rather than guessing.
    """

    if not isinstance(decisions, DecisionSet):
        raise InputError("apply: expected a DecisionSet")

    resolved = CorrectionFields() if fields is None else fields
    allowed_column = {
        rule_id: getattr(resolved, attr)
        for rule_id, attr in _RULE_TO_FIELD_ATTR.items()
    }

    decisions_by_key: dict[tuple[str, str, str], CorrectionDecision] = {}
    for decision in decisions.corrections:
        key = (decision.business_id, decision.field, decision.rule_id)
        if key in decisions_by_key:
            raise InputError(f"apply: duplicate correction decision for {key!r}")
        decisions_by_key[key] = decision

    # Fail closed on stale or mismatched approvals before touching anything.
    proposal_full_keys = {
        (p.business_id, p.field, p.rule_id, p.proposed_value) for p in proposals
    }
    for decision in decisions.corrections:
        full_key = (
            decision.business_id,
            decision.field,
            decision.rule_id,
            decision.proposed_value,
        )
        if (
            full_key not in proposal_full_keys
            and decision.decision == CORRECTION_DECISION_ACCEPTED
        ):
            raise InputError(
                "apply: accepted correction decision for "
                f"(business_id={decision.business_id!r}, field={decision.field!r}, "
                f"rule_id={decision.rule_id!r}) does not match any generated proposal"
            )

    # Resolve each business_id to exactly one record; duplicates are ambiguous.
    index_by_id: dict[str, int] = {}
    ambiguous_ids: set[str] = set()
    for index, record in enumerate(records):
        business_id = get_record_id(record, config, index)
        if business_id in index_by_id:
            ambiguous_ids.add(business_id)
        else:
            index_by_id[business_id] = index

    # Two proposals for one record-and-field conflict; refuse to pick one.
    seen_record_fields: set[tuple[str, str]] = set()
    for proposal in proposals:
        key = (proposal.business_id, proposal.field)
        if key in seen_record_fields:
            raise InputError(
                "apply: conflicting proposals for "
                f"business_id={proposal.business_id!r}, field={proposal.field!r}"
            )
        seen_record_fields.add(key)

    output_records: list[dict[str, Any]] = [dict(record) for record in records]
    audit: list[CorrectionAuditEntry] = []

    for proposal in proposals:
        if proposal.rule_id not in allowed_column:
            raise InputError(
                f"apply: proposal uses rule {proposal.rule_id!r}, which is not in "
                f"the correction allowlist"
            )
        expected_column = allowed_column[proposal.rule_id]
        if proposal.field == config.id_field:
            raise InputError(
                "apply: proposals may not target the record identifier column"
            )
        if proposal.field != expected_column:
            raise InputError(
                f"apply: proposal rule {proposal.rule_id!r} must target "
                f"{expected_column!r}, not {proposal.field!r}"
            )
        if proposal.business_id in ambiguous_ids:
            raise InputError(
                f"apply: business_id {proposal.business_id!r} matches multiple records"
            )
        if proposal.business_id not in index_by_id:
            raise InputError(
                f"apply: business_id {proposal.business_id!r} is not present in the records"
            )

        index = index_by_id[proposal.business_id]
        record = records[index]
        if proposal.field not in record:
            raise InputError(
                f"apply: field {proposal.field!r} is absent from record "
                f"{proposal.business_id!r}"
            )
        if record[proposal.field] != proposal.original_value:
            raise InputError(
                f"apply: record {proposal.business_id!r} field {proposal.field!r} "
                f"no longer holds the proposal's original value"
            )

        decision = decisions_by_key.get(
            (proposal.business_id, proposal.field, proposal.rule_id)
        )
        if decision is not None and decision.proposed_value == proposal.proposed_value:
            category = decision.decision
            decision_rationale = decision.rationale
        else:
            category = DECISION_NOT_DECIDED
            decision_rationale = None

        if category == CORRECTION_DECISION_ACCEPTED:
            output_records[index][proposal.field] = proposal.proposed_value

        applied = (
            category == CORRECTION_DECISION_ACCEPTED
            and output_records[index][proposal.field] == proposal.proposed_value
        )
        audit.append(
            CorrectionAuditEntry(
                business_id=proposal.business_id,
                field=proposal.field,
                rule_id=proposal.rule_id,
                original_value=proposal.original_value,
                proposed_value=proposal.proposed_value,
                decision=category,
                applied=applied,
                rationale=proposal.rationale,
                decision_rationale=decision_rationale,
            )
        )

    audit_tuple = tuple(audit)
    return DecisionApplicationResult(
        records=tuple(output_records),
        audit=audit_tuple,
        summary=_summarize(audit_tuple),
        duplicate_decisions=tuple(decisions.duplicates),
        duplicate_counts=_count_duplicates(decisions.duplicates),
    )


def _summarize(audit: tuple[CorrectionAuditEntry, ...]) -> ApplicationSummary:
    accepted = rejected = deferred = not_decided = 0
    applied = 0
    for entry in audit:
        if entry.decision == CORRECTION_DECISION_ACCEPTED:
            accepted += 1
        elif entry.decision == CORRECTION_DECISION_REJECTED:
            rejected += 1
        elif entry.decision == CORRECTION_DECISION_DEFERRED:
            deferred += 1
        else:
            not_decided += 1
        if entry.applied:
            applied += 1

    total = len(audit)
    return ApplicationSummary(
        total_proposals=total,
        accepted=accepted,
        rejected=rejected,
        deferred=deferred,
        not_decided=not_decided,
        applied=applied,
        unapplied=total - applied,
    )


def _count_duplicates(
    duplicates: Sequence[DuplicateDecision],
) -> DuplicateDecisionCounts:
    confirmed = sum(
        1 for d in duplicates if d.decision == DUPLICATE_DECISION_CONFIRMED
    )
    rejected = sum(
        1 for d in duplicates if d.decision == DUPLICATE_DECISION_REJECTED
    )
    deferred = sum(
        1 for d in duplicates if d.decision == DUPLICATE_DECISION_DEFERRED
    )
    return DuplicateDecisionCounts(
        confirmed=confirmed,
        rejected=rejected,
        deferred=deferred,
        total=len(duplicates),
    )
