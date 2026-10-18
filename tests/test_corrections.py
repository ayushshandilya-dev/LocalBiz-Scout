"""Tests for the Phase 3.1 conservative correction-proposal allowlist.

Table-driven where practical. Proposals are pure data: these tests assert they
are never applied, never mutate input, are deterministic, and cover the exact
allowlisted transformations plus everything that must be left alone.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from localbiz_scout import corrections
from localbiz_scout.corrections import (
    CORRECTION_RULES,
    CORRECTION_RULE_NAME_WHITESPACE,
    CORRECTION_RULE_PHONE_US,
    CORRECTION_RULE_POSTAL_ZIP4,
    CORRECTION_RULE_STATE_UPPERCASE,
    CorrectionFields,
    propose_corrections,
)

# --------------------------------------------------------------------------- #
# 1. Valid transformation for every allowlisted rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "record, rule_id, field, expected",
    [
        (
            {"business_id": "b1", "name": "  Acme   Coffee  "},
            CORRECTION_RULE_NAME_WHITESPACE,
            "name",
            "Acme Coffee",
        ),
        (
            {"business_id": "b1", "state": "tx"},
            CORRECTION_RULE_STATE_UPPERCASE,
            "state",
            "TX",
        ),
        (
            {"business_id": "b1", "state": " tx "},
            CORRECTION_RULE_STATE_UPPERCASE,
            "state",
            "TX",
        ),
        (
            {"business_id": "b1", "postal_code": "787011234"},
            CORRECTION_RULE_POSTAL_ZIP4,
            "postal_code",
            "78701-1234",
        ),
        (
            {"business_id": "b1", "phone": "(512) 555-0101"},
            CORRECTION_RULE_PHONE_US,
            "phone",
            "512-555-0101",
        ),
        (
            {"business_id": "b1", "phone": "15125550101"},
            CORRECTION_RULE_PHONE_US,
            "phone",
            "512-555-0101",
        ),
    ],
)
def test_valid_transformations(record, rule_id, field, expected):
    proposals = propose_corrections([record])

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.rule_id == rule_id
    assert proposal.field == field
    assert proposal.business_id == "b1"
    assert proposal.original_value == record[field]
    assert proposal.proposed_value == expected
    assert proposal.proposed_value != proposal.original_value
    assert proposal.rationale.strip()
    assert proposal.evidence


# --------------------------------------------------------------------------- #
# 2. Values that must remain unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "record",
    [
        {"business_id": "b1", "name": ""},
        {"business_id": "b1", "name": "   "},
        {"business_id": "b1", "name": "Acme Coffee"},  # already collapsed
        {"business_id": "b1", "state": ""},
        {"business_id": "b1", "state": "   "},
        {"business_id": "b1", "state": "TX"},  # already upper
        {"business_id": "b1", "state": "T"},  # too short
        {"business_id": "b1", "state": "TXX"},  # too long
        {"business_id": "b1", "state": "T1"},  # not letters
        {"business_id": "b1", "postal_code": "78701"},  # 5-digit ZIP
        {"business_id": "b1", "postal_code": "78701-1234"},  # already ZIP+4
        {"business_id": "b1", "postal_code": "1234"},  # 4 digits
        {"business_id": "b1", "postal_code": "1234567890"},  # 10 digits
        {"business_id": "b1", "postal_code": "A1A1A1"},  # alphanumeric
        {"business_id": "b1", "phone": ""},
        {"business_id": "b1", "phone": "   "},
        {"business_id": "b1", "phone": "555-0101"},  # 7 digits
        {"business_id": "b1", "phone": "123456789012"},  # 12 digits
        {"business_id": "b1", "phone": "512-555-0101"},  # already formatted
    ],
)
def test_values_left_unchanged(record):
    assert propose_corrections([record]) == ()


def test_no_proposal_when_transformation_is_a_no_op():
    record = {"business_id": "b1", "name": "Acme Coffee"}
    assert propose_corrections([record]) == ()


# --------------------------------------------------------------------------- #
# 3. Name preserves case and punctuation
# --------------------------------------------------------------------------- #


def test_name_preserves_case_and_punctuation():
    record = {"business_id": "b1", "name": "  ACME, Inc. \t & Co.  "}
    (proposal,) = propose_corrections([record])
    assert proposal.proposed_value == "ACME, Inc. & Co."


# --------------------------------------------------------------------------- #
# 4. ASCII-only validation where required
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "record",
    [
        {"business_id": "b1", "state": "\u00f1\u00e9"},  # two non-ASCII letters
        {"business_id": "b1", "postal_code": "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19"},
    ],
)
def test_non_ascii_values_are_not_transformed(record):
    assert propose_corrections([record]) == ()


# --------------------------------------------------------------------------- #
# 5. Idempotence
# --------------------------------------------------------------------------- #


def test_proposing_on_applied_values_yields_nothing():
    records = [
        {
            "business_id": "b1",
            "name": "  Acme   Coffee  ",
            "state": " tx ",
            "postal_code": "787011234",
            "phone": "(512) 555-0101",
        }
    ]
    first = propose_corrections(records)
    assert len(first) == 4

    applied = [dict(records[0])]
    for proposal in first:
        applied[0][proposal.field] = proposal.proposed_value

    assert propose_corrections(applied) == ()


# --------------------------------------------------------------------------- #
# 6. Missing columns and unsupported field-value types
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "record",
    [
        {"business_id": "b1"},  # no allowlisted columns at all
        {"business_id": "b1", "phone": 5550101},  # int
        {"business_id": "b1", "postal_code": 787011234},  # int
        {"business_id": "b1", "state": None},  # None
        {"business_id": "b1", "name": ["Acme"]},  # list
        {"business_id": "b1", "phone": True},  # bool
    ],
)
def test_missing_or_unsupported_values_are_skipped(record):
    assert propose_corrections([record]) == ()


def test_default_mapping_ignores_differently_named_columns():
    record = {"business_id": "b1", "zip": "787011234", "telephone": "(512) 555-0101"}
    assert propose_corrections([record]) == ()


# --------------------------------------------------------------------------- #
# 7. Stable record IDs and the id column
# --------------------------------------------------------------------------- #


def test_missing_id_uses_positional_fallback():
    (proposal,) = propose_corrections([{"name": "  Acme  "}])
    assert proposal.business_id == "row-0000"


def test_positional_fallback_uses_row_index():
    records = [
        {"business_id": "b1", "name": "Acme"},  # no proposal
        {"name": "  Beta  "},
    ]
    (proposal,) = propose_corrections(records)
    assert proposal.business_id == "row-0001"


def test_id_column_is_never_targeted():
    fields = CorrectionFields(name="business_id")
    record = {"business_id": "  b1  "}
    assert propose_corrections([record], fields=fields) == ()


def test_a_column_receives_at_most_one_proposal():
    fields = CorrectionFields(name="value", state="value")
    record = {"business_id": "b1", "value": "  tx  "}
    proposals = propose_corrections([record], fields=fields)
    assert len(proposals) == 1
    assert proposals[0].rule_id == CORRECTION_RULE_NAME_WHITESPACE


# --------------------------------------------------------------------------- #
# 8. Explicit field mapping
# --------------------------------------------------------------------------- #


def test_custom_field_mapping_is_honoured():
    fields = CorrectionFields(
        name="business_name",
        state="state_code",
        postal_code="zip",
        phone="telephone",
    )
    record = {
        "business_id": "b1",
        "business_name": "  A  ",
        "state_code": "tx",
        "zip": "787011234",
        "telephone": "(512) 555-0101",
    }
    proposals = propose_corrections([record], fields=fields)
    assert [(p.rule_id, p.field, p.proposed_value) for p in proposals] == [
        (CORRECTION_RULE_NAME_WHITESPACE, "business_name", "A"),
        (CORRECTION_RULE_STATE_UPPERCASE, "state_code", "TX"),
        (CORRECTION_RULE_POSTAL_ZIP4, "zip", "78701-1234"),
        (CORRECTION_RULE_PHONE_US, "telephone", "512-555-0101"),
    ]


# --------------------------------------------------------------------------- #
# 9. Non-allowlisted fields are never touched
# --------------------------------------------------------------------------- #


def test_non_allowlisted_fields_are_untouched():
    record = {
        "business_id": "b1",
        "address": "  1 Main St  ",
        "city": "  Austin  ",
        "website": "  example.com  ",
        "email": "  a@b.com  ",
    }
    assert propose_corrections([record]) == ()


# --------------------------------------------------------------------------- #
# 10. Deterministic ordering
# --------------------------------------------------------------------------- #


def test_ordering_is_record_then_fixed_rule_order():
    records = [
        {
            "business_id": "b1",
            # Deliberately scrambled mapping order: output must ignore it.
            "phone": "(512) 555-0101",
            "name": "  A  ",
            "postal_code": "787011234",
            "state": "tx",
        },
        {"business_id": "b2", "name": "  B  "},
    ]
    proposals = propose_corrections(records)
    assert [(p.business_id, p.rule_id) for p in proposals] == [
        ("b1", CORRECTION_RULE_NAME_WHITESPACE),
        ("b1", CORRECTION_RULE_STATE_UPPERCASE),
        ("b1", CORRECTION_RULE_POSTAL_ZIP4),
        ("b1", CORRECTION_RULE_PHONE_US),
        ("b2", CORRECTION_RULE_NAME_WHITESPACE),
    ]


def test_repeated_calls_are_identical():
    records = [
        {"business_id": "b1", "name": "  Acme  ", "phone": "(512) 555-0101"},
        {"business_id": "b2", "state": "tx"},
    ]
    assert propose_corrections(records) == propose_corrections(records)


# --------------------------------------------------------------------------- #
# 11. Inputs are never mutated
# --------------------------------------------------------------------------- #


def test_inputs_and_nested_data_are_not_mutated():
    records = [
        {
            "business_id": "b1",
            "name": "  Acme  ",
            "meta": {"tags": ["x", "y"]},
        }
    ]
    snapshot = copy.deepcopy(records)

    propose_corrections(records)

    assert records == snapshot


# --------------------------------------------------------------------------- #
# 12. Stable rule ids, rationale and evidence
# --------------------------------------------------------------------------- #


def test_rule_ids_are_stable_and_ordered():
    assert CORRECTION_RULES == (
        CORRECTION_RULE_NAME_WHITESPACE,
        CORRECTION_RULE_STATE_UPPERCASE,
        CORRECTION_RULE_POSTAL_ZIP4,
        CORRECTION_RULE_PHONE_US,
    )


def test_rationale_and_evidence_present_for_every_rule():
    records = [
        {
            "business_id": "b1",
            "name": "  A  ",
            "state": "tx",
            "postal_code": "787011234",
            "phone": "(512) 555-0101",
        }
    ]
    proposals = propose_corrections(records)
    by_rule = {proposal.rule_id: proposal for proposal in proposals}

    assert set(by_rule) == set(CORRECTION_RULES)
    for proposal in proposals:
        assert proposal.rationale.strip()
        assert proposal.evidence

    assert by_rule[CORRECTION_RULE_STATE_UPPERCASE].evidence == {"letter_count": 2}
    assert by_rule[CORRECTION_RULE_POSTAL_ZIP4].evidence == {"digit_count": 9}
    assert by_rule[CORRECTION_RULE_PHONE_US].evidence == {
        "digit_count": 10,
        "digits": "5125550101",
    }


def test_proposal_to_dict_contains_all_keys():
    (proposal,) = propose_corrections([{"business_id": "b1", "phone": "(512) 555-0101"}])
    rendered = proposal.to_dict()
    assert rendered == {
        "business_id": "b1",
        "field": "phone",
        "original_value": "(512) 555-0101",
        "proposed_value": "512-555-0101",
        "rule_id": CORRECTION_RULE_PHONE_US,
        "rationale": proposal.rationale,
        "evidence": {"digit_count": 10, "digits": "5125550101"},
    }


# --------------------------------------------------------------------------- #
# 13. No AI or network dependency
# --------------------------------------------------------------------------- #


def test_module_imports_no_ai_or_network_code():
    source = Path(corrections.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    forbidden = ("localbiz_scout.ai", "socket", "urllib", "http", "requests", "ollama")
    for module in imported:
        assert not module.startswith(forbidden), f"unexpected import: {module}"


# --------------------------------------------------------------------------- #
# 14. Bridge to the Phase 3.0 decision model (no conflicting schema)
# --------------------------------------------------------------------------- #


def test_proposal_maps_onto_correction_decision_fields():
    from localbiz_scout.remediation import CorrectionDecision

    (proposal,) = propose_corrections([{"business_id": "b1", "name": "  Acme  "}])
    decision = CorrectionDecision(
        business_id=proposal.business_id,
        field=proposal.field,
        rule_id=proposal.rule_id,
        proposed_value=proposal.proposed_value,
        decision="deferred",
    )
    assert decision.proposed_value == "Acme"
    assert decision.rule_id == CORRECTION_RULE_NAME_WHITESPACE
