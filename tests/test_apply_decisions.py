"""Tests for Phase 3.2 pure decision application and audit trail.

Only correction decisions change records. Duplicate decisions are carried
through for reporting and never alter a record.
"""

from __future__ import annotations

import copy
import json

import pytest

from localbiz_scout.application import (
    DECISION_NOT_DECIDED,
    apply_decisions,
)
from localbiz_scout.corrections import (
    CORRECTION_RULE_NAME_WHITESPACE,
    CORRECTION_RULE_PHONE_US,
    CORRECTION_RULE_POSTAL_ZIP4,
    CORRECTION_RULE_STATE_UPPERCASE,
    CorrectionProposal,
    propose_corrections,
)
from localbiz_scout.errors import InputError
from localbiz_scout.remediation import (
    CORRECTION_DECISION_ACCEPTED,
    CORRECTION_DECISION_DEFERRED,
    CORRECTION_DECISION_REJECTED,
    DUPLICATE_DECISION_CONFIRMED,
    DUPLICATE_DECISION_DEFERRED,
    DUPLICATE_DECISION_REJECTED,
    CorrectionDecision,
    DecisionSet,
    DuplicateDecision,
    parse_decisions,
)

RECORD = {
    "business_id": "b1",
    "name": "  Acme   Coffee  ",
    "state": "tx",
    "postal_code": "787011234",
    "phone": "(512) 555-0101",
}
NOISY = {
    "business_id": "b2",
    "name": "Beta Bakery",
    "state": "TX",
    "postal_code": "78701",
    "phone": "512-555-0102",
}


def _decide(proposal, decision, rationale=None):
    return CorrectionDecision(
        business_id=proposal.business_id,
        field=proposal.field,
        rule_id=proposal.rule_id,
        proposed_value=proposal.proposed_value,
        decision=decision,
        rationale=rationale,
    )


def _by_field(proposals):
    return {proposal.field: proposal for proposal in proposals}


# --------------------------------------------------------------------------- #
# 1-4. Apply / skip semantics
# --------------------------------------------------------------------------- #


def test_accepted_proposal_changes_only_its_field():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    name = _by_field(proposals)["name"]

    result = apply_decisions(records, DecisionSet(corrections=(_decide(name, CORRECTION_DECISION_ACCEPTED),)), proposals)

    output = result.records[0]
    assert output["name"] == "Acme Coffee"
    assert output["state"] == "tx"
    assert output["postal_code"] == "787011234"
    assert output["phone"] == "(512) 555-0101"
    # The caller's record is untouched.
    assert records[0]["name"] == "  Acme   Coffee  "


def test_rejected_proposal_leaves_field_unchanged():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    name = _by_field(proposals)["name"]

    result = apply_decisions(records, DecisionSet(corrections=(_decide(name, CORRECTION_DECISION_REJECTED),)), proposals)

    assert result.records[0]["name"] == "  Acme   Coffee  "
    entry = next(a for a in result.audit if a.field == "name")
    assert entry.decision == CORRECTION_DECISION_REJECTED
    assert entry.applied is False


def test_deferred_proposal_leaves_field_unchanged():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    name = _by_field(proposals)["name"]

    result = apply_decisions(records, DecisionSet(corrections=(_decide(name, CORRECTION_DECISION_DEFERRED),)), proposals)

    assert result.records[0]["name"] == "  Acme   Coffee  "
    assert result.summary.deferred == 1
    assert result.summary.applied == 0


def test_missing_decision_is_undecided_and_unchanged():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)

    result = apply_decisions(records, DecisionSet(), proposals)

    assert result.records[0]["name"] == "  Acme   Coffee  "
    assert all(entry.decision == DECISION_NOT_DECIDED for entry in result.audit)
    assert result.summary.not_decided == len(proposals)
    assert result.summary.applied == 0


# --------------------------------------------------------------------------- #
# 5-6. Fail closed on mismatch / stale approvals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "stale",
    [
        # Original value changed since the proposal was generated.
        CorrectionDecision(
            "b1", "name", CORRECTION_RULE_NAME_WHITESPACE, "Something Else",
            CORRECTION_DECISION_ACCEPTED,
        ),
        # Unknown record.
        CorrectionDecision(
            "ghost", "state", CORRECTION_RULE_STATE_UPPERCASE, "TX",
            CORRECTION_DECISION_ACCEPTED,
        ),
        # Allowlisted field, but no such proposal exists.
        CorrectionDecision(
            "b1", "phone", CORRECTION_RULE_PHONE_US, "999-999-9999",
            CORRECTION_DECISION_ACCEPTED,
        ),
    ],
)
def test_accepted_decision_without_matching_proposal_fails_closed(stale):
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    with pytest.raises(InputError, match="does not match any generated proposal"):
        apply_decisions(records, DecisionSet(corrections=(stale,)), proposals)


def test_mismatched_non_accepted_decision_does_not_apply():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)

    mismatched = CorrectionDecision(
        "b1", "name", CORRECTION_RULE_NAME_WHITESPACE, "Different Value",
        CORRECTION_DECISION_REJECTED,
    )
    result = apply_decisions(records, DecisionSet(corrections=(mismatched,)), proposals)

    assert result.records[0]["name"] == "  Acme   Coffee  "
    name_entry = next(a for a in result.audit if a.field == "name")
    assert name_entry.decision == DECISION_NOT_DECIDED


# --------------------------------------------------------------------------- #
# 7. Conflicting proposals fail closed
# --------------------------------------------------------------------------- #


def test_conflicting_proposals_for_one_field_fail_closed():
    records = [{"business_id": "b1", "name": "  X  "}]
    first = CorrectionProposal("b1", "name", "  X  ", "X", CORRECTION_RULE_NAME_WHITESPACE, "r1")
    second = CorrectionProposal("b1", "name", "  X  ", "x", "some_other_rule", "r2")

    with pytest.raises(InputError, match="conflicting proposals"):
        apply_decisions(records, DecisionSet(), (first, second))


# --------------------------------------------------------------------------- #
# 8. Duplicate decisions never change records
# --------------------------------------------------------------------------- #


def test_duplicate_decisions_do_not_modify_records():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    name = _by_field(proposals)["name"]

    duplicates = (
        DuplicateDecision("b1", "b9", DUPLICATE_DECISION_CONFIRMED),
        DuplicateDecision("b1", "b8", DUPLICATE_DECISION_REJECTED),
        DuplicateDecision("b1", "b7", DUPLICATE_DECISION_DEFERRED),
    )
    decisions = DecisionSet(
        duplicates=duplicates,
        corrections=(_decide(name, CORRECTION_DECISION_ACCEPTED),),
    )

    result = apply_decisions(records, decisions, proposals)

    # Only the accepted correction changed anything; duplicates did not.
    assert result.records[0]["name"] == "Acme Coffee"
    assert result.records[0]["business_id"] == "b1"
    assert result.duplicate_decisions == duplicates
    assert result.duplicate_counts.confirmed == 1
    assert result.duplicate_counts.rejected == 1
    assert result.duplicate_counts.deferred == 1
    assert result.duplicate_counts.total == 3
    # Duplicate counts are excluded from correction counts.
    assert result.summary.total_proposals == len(proposals)


# --------------------------------------------------------------------------- #
# 9-10. Audit trail
# --------------------------------------------------------------------------- #


def test_every_proposal_has_exactly_one_audit_entry():
    records = [dict(RECORD), dict(NOISY)]
    proposals = propose_corrections(records)

    result = apply_decisions(records, DecisionSet(), proposals)

    assert len(result.audit) == len(proposals)
    assert {(a.business_id, a.field, a.rule_id) for a in result.audit} == {
        (p.business_id, p.field, p.rule_id) for p in proposals
    }


def test_audit_entry_contents_for_accepted_proposal():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    state = _by_field(proposals)["state"]

    result = apply_decisions(
        records,
        DecisionSet(corrections=(_decide(state, CORRECTION_DECISION_ACCEPTED, "confirmed by hand"),)),
        proposals,
    )

    entry = next(a for a in result.audit if a.field == "state")
    assert entry.business_id == "b1"
    assert entry.field == "state"
    assert entry.rule_id == CORRECTION_RULE_STATE_UPPERCASE
    assert entry.original_value == "tx"
    assert entry.proposed_value == "TX"
    assert entry.decision == CORRECTION_DECISION_ACCEPTED
    assert entry.applied is True
    assert entry.rationale == state.rationale
    assert entry.decision_rationale == "confirmed by hand"


def test_audit_entry_decision_rationale_is_none_when_undecided():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    result = apply_decisions(records, DecisionSet(), proposals)
    assert all(entry.decision_rationale is None for entry in result.audit)


def test_audit_entry_to_dict_round_trips():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    postal = _by_field(proposals)["postal_code"]
    result = apply_decisions(
        records,
        DecisionSet(corrections=(_decide(postal, CORRECTION_DECISION_ACCEPTED),)),
        proposals,
    )
    entry = next(a for a in result.audit if a.field == "postal_code")
    assert entry.to_dict() == {
        "business_id": "b1",
        "field": "postal_code",
        "rule_id": CORRECTION_RULE_POSTAL_ZIP4,
        "original_value": "787011234",
        "proposed_value": "78701-1234",
        "decision": CORRECTION_DECISION_ACCEPTED,
        "applied": True,
        "rationale": postal.rationale,
        "decision_rationale": None,
    }


# --------------------------------------------------------------------------- #
# 11. Summary reconciles
# --------------------------------------------------------------------------- #


def test_summary_counts_reconcile():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)  # name, state, postal_code, phone
    by_field = _by_field(proposals)

    decisions = DecisionSet(
        corrections=(
            _decide(by_field["name"], CORRECTION_DECISION_ACCEPTED),
            _decide(by_field["state"], CORRECTION_DECISION_REJECTED),
            _decide(by_field["postal_code"], CORRECTION_DECISION_DEFERRED),
            # phone: no decision -> not_decided
        )
    )
    summary = apply_decisions(records, decisions, proposals).summary

    assert summary.total_proposals == 4
    assert summary.accepted == 1
    assert summary.rejected == 1
    assert summary.deferred == 1
    assert summary.not_decided == 1
    assert (
        summary.accepted + summary.rejected + summary.deferred + summary.not_decided
        == summary.total_proposals
    )
    assert summary.applied == 1
    assert summary.unapplied == 3
    assert summary.applied + summary.unapplied == summary.total_proposals


# --------------------------------------------------------------------------- #
# 12. Inputs are never mutated
# --------------------------------------------------------------------------- #


def test_inputs_are_not_mutated():
    records = [
        {
            "business_id": "b1",
            "name": "  Acme  ",
            "phone": "(512) 555-0101",
            "meta": {"tags": ["x", "y"]},
        }
    ]
    proposals = propose_corrections(records)
    decisions = DecisionSet(
        duplicates=(DuplicateDecision("b1", "b2", DUPLICATE_DECISION_CONFIRMED),),
        corrections=(
            _decide(_by_field(proposals)["name"], CORRECTION_DECISION_ACCEPTED),
            _decide(_by_field(proposals)["phone"], CORRECTION_DECISION_REJECTED),
        ),
    )

    records_snapshot = copy.deepcopy(records)
    proposals_snapshot = copy.deepcopy(proposals)
    decisions_snapshot = copy.deepcopy(decisions)

    result = apply_decisions(records, decisions, proposals)

    assert records == records_snapshot
    assert proposals == proposals_snapshot
    assert decisions == decisions_snapshot
    # New output records are independent top-level dicts.
    assert result.records[0] is not records[0]


# --------------------------------------------------------------------------- #
# 13. IDs and non-allowlisted fields cannot change
# --------------------------------------------------------------------------- #


def test_record_id_field_is_never_changed():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    result = apply_decisions(records, DecisionSet(), proposals)
    assert all(record["business_id"] == "b1" for record in result.records)


def test_proposal_targeting_id_column_fails_closed():
    records = [{"business_id": "b1", "name": "  X  ", "state": "tx", "postal_code": "787011234", "phone": "5125550101"}]
    bad = CorrectionProposal(
        "b1", "business_id", "b1", "b2", CORRECTION_RULE_NAME_WHITESPACE, "r"
    )
    with pytest.raises(InputError, match="identifier column"):
        apply_decisions(records, DecisionSet(), (bad,))


def test_proposal_outside_allowlist_field_fails_closed():
    records = [{"business_id": "b1", "name": "  X  ", "address": "  1 Main St  "}]
    bad = CorrectionProposal(
        "b1", "address", "  1 Main St  ", "1 Main St", CORRECTION_RULE_NAME_WHITESPACE, "r"
    )
    with pytest.raises(InputError, match="must target"):
        apply_decisions(records, DecisionSet(), (bad,))


def test_proposal_with_unknown_rule_fails_closed():
    records = [{"business_id": "b1", "name": "  X  "}]
    bad = CorrectionProposal("b1", "name", "  X  ", "X", "totally_made_up_rule", "r")
    with pytest.raises(InputError, match="allowlist"):
        apply_decisions(records, DecisionSet(), (bad,))


# --------------------------------------------------------------------------- #
# 14. Determinism
# --------------------------------------------------------------------------- #


def test_repeated_calls_are_equivalent():
    records = [dict(RECORD), dict(NOISY)]
    proposals = propose_corrections(records)
    decisions = DecisionSet(
        corrections=(_decide(_by_field(proposals)["name"], CORRECTION_DECISION_ACCEPTED),)
    )
    assert apply_decisions(records, decisions, proposals) == apply_decisions(
        records, decisions, proposals
    )


# --------------------------------------------------------------------------- #
# Integration with the Phase 3.0 decision-file parser
# --------------------------------------------------------------------------- #


def test_integration_with_parsed_decision_file():
    records = [dict(RECORD)]
    proposals = propose_corrections(records)
    name = _by_field(proposals)["name"]

    text = json.dumps(
        {
            "version": 1,
            "duplicates": [
                {
                    "business_id_a": "b1",
                    "business_id_b": "b2",
                    "decision": "confirmed",
                }
            ],
            "corrections": [
                {
                    "business_id": "b1",
                    "field": "name",
                    "rule_id": name.rule_id,
                    "proposed_value": name.proposed_value,
                    "decision": "accepted",
                    "rationale": "looks right",
                }
            ],
        }
    )
    decisions = parse_decisions(text)

    result = apply_decisions(records, decisions, proposals)

    assert result.records[0]["name"] == "Acme Coffee"
    entry = next(a for a in result.audit if a.field == "name")
    assert entry.decision_rationale == "looks right"
    assert result.duplicate_counts.confirmed == 1
