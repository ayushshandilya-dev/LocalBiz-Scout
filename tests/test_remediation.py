"""Tests for the Phase 3.0 remediation decision model.

These cover the pure decision-file contract only: parsing, structural
validation, canonicalization, uniqueness, and referential validation against
caller-supplied record ids and duplicate-candidate pairs. No correction is
applied and no file is written.

The small synthetic :data:`RECORDS` set is analyzed with the real
``find_duplicate_candidates`` so the candidate pairs under test come from the
actual engine rather than a hand-written stub.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from localbiz_scout.errors import InputError
from localbiz_scout.loader import load_records
from localbiz_scout.remediation import (
    DECISIONS_SCHEMA_VERSION,
    CorrectionDecision,
    DecisionSet,
    DuplicateDecision,
    canonical_pair,
    duplicate_pairs,
    load_decisions,
    parse_decisions,
    stable_record_ids,
    validate_decisions,
)
from localbiz_scout.rules import scan
from localbiz_scout.rules.config import DEFAULT_CONFIG
from localbiz_scout.rules.duplicates import find_duplicate_candidates

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "data" / "sample_businesses.csv"

# b1 and b2 are identical -> an "exact" candidate; b3 is unrelated.
RECORDS = (
    {"business_id": "b1", "name": "Acme Coffee", "address": "1 Main St"},
    {"business_id": "b2", "name": "Acme Coffee", "address": "1 Main St"},
    {"business_id": "b3", "name": "Beta Bakery", "address": "9 Oak Ave"},
)


@pytest.fixture
def analysis():
    """(record_ids, candidate_pairs) derived from the real engine."""

    record_ids = stable_record_ids(RECORDS, DEFAULT_CONFIG)
    pairs = duplicate_pairs(find_duplicate_candidates(RECORDS, DEFAULT_CONFIG))
    return record_ids, pairs


def _doc(**overrides):
    document = {"version": DECISIONS_SCHEMA_VERSION}
    document.update(overrides)
    return json.dumps(document)


# --------------------------------------------------------------------------- #
# Duplicate decisions: happy path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("decision", ("confirmed", "rejected", "deferred"))
def test_valid_duplicate_decisions(analysis, decision):
    record_ids, pairs = analysis
    text = _doc(
        duplicates=[
            {
                "business_id_a": "b1",
                "business_id_b": "b2",
                "decision": decision,
                "rationale": "checked by hand",
            }
        ]
    )

    decisions = parse_decisions(text)

    assert decisions.version == DECISIONS_SCHEMA_VERSION
    assert decisions.corrections == ()
    assert decisions.duplicates == (
        DuplicateDecision("b1", "b2", decision, "checked by hand"),
    )
    validate_decisions(decisions, record_ids=record_ids, candidate_pairs=pairs)


def test_minimal_document_is_valid():
    assert parse_decisions(_doc()) == DecisionSet()


def test_load_decisions_parses_and_validates(analysis):
    record_ids, pairs = analysis
    text = _doc(
        duplicates=[
            {"business_id_a": "b2", "business_id_b": "b1", "decision": "deferred"}
        ]
    )
    decisions = load_decisions(text, record_ids=record_ids, candidate_pairs=pairs)
    assert decisions.duplicates[0].decision == "deferred"


# --------------------------------------------------------------------------- #
# Canonicalization and uniqueness
# --------------------------------------------------------------------------- #


def test_pair_is_canonicalized():
    text = _doc(
        duplicates=[
            {"business_id_a": "b2", "business_id_b": "b1", "decision": "confirmed"}
        ]
    )
    decisions = parse_decisions(text)
    assert (
        decisions.duplicates[0].business_id_a,
        decisions.duplicates[0].business_id_b,
    ) == ("b1", "b2")


def test_reversed_duplicate_entries_are_rejected():
    text = _doc(
        duplicates=[
            {"business_id_a": "b1", "business_id_b": "b2", "decision": "confirmed"},
            {"business_id_a": "b2", "business_id_b": "b1", "decision": "rejected"},
        ]
    )
    with pytest.raises(InputError, match="duplicate decision"):
        parse_decisions(text)


def test_self_pair_is_rejected():
    text = _doc(
        duplicates=[
            {"business_id_a": "b1", "business_id_b": "b1", "decision": "confirmed"}
        ]
    )
    with pytest.raises(InputError, match="self-pair"):
        parse_decisions(text)


def test_canonical_pair_sorts():
    assert canonical_pair("b2", "b1") == ("b1", "b2")
    assert canonical_pair(" b1 ", "b2") == ("b1", "b2")
    assert canonical_pair("b1", "b1") == ("b1", "b1")


# --------------------------------------------------------------------------- #
# Referential validation against the current analysis
# --------------------------------------------------------------------------- #


def test_unknown_record_id_is_rejected(analysis):
    record_ids, pairs = analysis
    text = _doc(
        duplicates=[
            {"business_id_a": "b1", "business_id_b": "ghost", "decision": "confirmed"}
        ]
    )
    decisions = parse_decisions(text)
    with pytest.raises(InputError, match="unknown record id"):
        validate_decisions(decisions, record_ids=record_ids, candidate_pairs=pairs)


def test_pair_not_a_candidate_is_rejected(analysis):
    record_ids, pairs = analysis
    # b3 is a real record id but not part of any emitted candidate pair.
    text = _doc(
        duplicates=[
            {"business_id_a": "b1", "business_id_b": "b3", "decision": "confirmed"}
        ]
    )
    decisions = parse_decisions(text)
    with pytest.raises(InputError, match="not a duplicate candidate"):
        validate_decisions(decisions, record_ids=record_ids, candidate_pairs=pairs)


# --------------------------------------------------------------------------- #
# Structural validation: JSON, version, keys, types, values
# --------------------------------------------------------------------------- #


def test_invalid_json_is_rejected():
    with pytest.raises(InputError, match="invalid JSON"):
        parse_decisions("{not json")


def test_non_object_top_level_is_rejected():
    with pytest.raises(InputError, match="top level must be a JSON object"):
        parse_decisions("[1, 2, 3]")


def test_missing_version_is_rejected():
    with pytest.raises(InputError, match="missing required key 'version'"):
        parse_decisions(json.dumps({"duplicates": []}))


def test_unsupported_version_is_rejected():
    with pytest.raises(InputError, match="unsupported schema version"):
        parse_decisions(json.dumps({"version": 2, "duplicates": []}))


def test_boolean_version_is_rejected():
    with pytest.raises(InputError, match="must be an integer"):
        parse_decisions(json.dumps({"version": True}))


def test_unknown_top_level_key_is_rejected():
    with pytest.raises(InputError, match="unknown key"):
        parse_decisions(json.dumps({"version": 1, "duplicates": [], "extra": 1}))


def test_non_list_collection_is_rejected():
    with pytest.raises(InputError, match="must be a list"):
        parse_decisions(json.dumps({"version": 1, "duplicates": {"a": 1}}))


def test_non_object_entry_is_rejected():
    with pytest.raises(InputError, match="must be a JSON object"):
        parse_decisions(json.dumps({"version": 1, "duplicates": ["nope"]}))


def test_missing_entry_key_is_rejected():
    text = _doc(duplicates=[{"business_id_a": "b1", "business_id_b": "b2"}])
    with pytest.raises(InputError, match="missing required key"):
        parse_decisions(text)


def test_unknown_entry_key_is_rejected():
    text = _doc(
        duplicates=[
            {
                "business_id_a": "b1",
                "business_id_b": "b2",
                "decision": "confirmed",
                "note": "x",
            }
        ]
    )
    with pytest.raises(InputError, match="unknown key"):
        parse_decisions(text)


def test_wrong_identifier_type_is_rejected():
    text = _doc(
        duplicates=[
            {"business_id_a": 1, "business_id_b": "b2", "decision": "confirmed"}
        ]
    )
    with pytest.raises(InputError, match="must be a string"):
        parse_decisions(text)


def test_blank_identifier_is_rejected():
    text = _doc(
        duplicates=[
            {"business_id_a": "  ", "business_id_b": "b2", "decision": "confirmed"}
        ]
    )
    with pytest.raises(InputError, match="non-empty"):
        parse_decisions(text)


def test_unsupported_duplicate_decision_is_rejected():
    text = _doc(
        duplicates=[
            {"business_id_a": "b1", "business_id_b": "b2", "decision": "maybe"}
        ]
    )
    with pytest.raises(InputError, match="unsupported decision"):
        parse_decisions(text)


def test_non_string_decision_is_rejected():
    text = _doc(
        duplicates=[{"business_id_a": "b1", "business_id_b": "b2", "decision": 1}]
    )
    with pytest.raises(InputError, match="'decision' must be a string"):
        parse_decisions(text)


def test_wrong_rationale_type_is_rejected():
    text = _doc(
        duplicates=[
            {
                "business_id_a": "b1",
                "business_id_b": "b2",
                "decision": "confirmed",
                "rationale": 5,
            }
        ]
    )
    with pytest.raises(InputError, match="'rationale' must be a string"):
        parse_decisions(text)


# --------------------------------------------------------------------------- #
# Correction decisions: schema only
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("decision", ("accepted", "rejected", "deferred"))
def test_valid_correction_decisions_parse(decision):
    entry = {
        "business_id": "b1",
        "field": "phone",
        "rule_id": "suspicious_phone_length",
        "proposed_value": "512-555-0101",
        "decision": decision,
        "rationale": "canonical formatting",
    }
    decisions = parse_decisions(_doc(corrections=[entry]))
    assert decisions.corrections == (
        CorrectionDecision(
            "b1",
            "phone",
            "suspicious_phone_length",
            "512-555-0101",
            decision,
            "canonical formatting",
        ),
    )


def test_correction_allows_empty_proposed_value():
    entry = {
        "business_id": "b1",
        "field": "state",
        "rule_id": "suspicious_state_code",
        "proposed_value": "",
        "decision": "rejected",
    }
    decisions = parse_decisions(_doc(corrections=[entry]))
    assert decisions.corrections[0].proposed_value == ""


def test_duplicate_correction_entries_are_rejected():
    entry = {
        "business_id": "b1",
        "field": "state",
        "rule_id": "suspicious_state_code",
        "proposed_value": "TX",
        "decision": "accepted",
    }
    with pytest.raises(InputError, match="duplicate correction"):
        parse_decisions(_doc(corrections=[entry, dict(entry)]))


def test_corrections_with_different_rule_ids_are_allowed():
    base = {
        "business_id": "b1",
        "field": "state",
        "proposed_value": "TX",
        "decision": "accepted",
    }
    first = dict(base, rule_id="rule_one")
    second = dict(base, rule_id="rule_two")
    decisions = parse_decisions(_doc(corrections=[first, second]))
    assert len(decisions.corrections) == 2


def test_missing_correction_key_is_rejected():
    entry = {
        "business_id": "b1",
        "field": "state",
        "rule_id": "r",
        "decision": "accepted",
    }
    with pytest.raises(InputError, match="missing required key"):
        parse_decisions(_doc(corrections=[entry]))


def test_unsupported_correction_decision_is_rejected():
    entry = {
        "business_id": "b1",
        "field": "state",
        "rule_id": "r",
        "proposed_value": "TX",
        "decision": "approved",
    }
    with pytest.raises(InputError, match="unsupported decision"):
        parse_decisions(_doc(corrections=[entry]))


def test_unknown_correction_key_is_rejected():
    entry = {
        "business_id": "b1",
        "field": "state",
        "rule_id": "r",
        "proposed_value": "TX",
        "decision": "accepted",
        "reason": "typo of rationale",
    }
    with pytest.raises(InputError, match="unknown key"):
        parse_decisions(_doc(corrections=[entry]))


def test_corrections_are_not_semantically_validated(analysis):
    # Phase 3.0 checks structure only: an unknown id/field/rule and an
    # unverified proposed value still parse and validate, because judging a
    # correction is the job of the future allowlist -- not this phase.
    record_ids, pairs = analysis
    entry = {
        "business_id": "ghost",
        "field": "not_a_column",
        "rule_id": "made_up",
        "proposed_value": "anything",
        "decision": "accepted",
    }
    decisions = parse_decisions(_doc(corrections=[entry]))
    validate_decisions(decisions, record_ids=record_ids, candidate_pairs=pairs)


# --------------------------------------------------------------------------- #
# Purity, helpers, and non-regression
# --------------------------------------------------------------------------- #


def test_parse_decisions_requires_a_string():
    with pytest.raises(InputError, match="expected a JSON string"):
        parse_decisions(b'{"version": 1}')


def test_stable_record_ids_use_the_shared_id_rule():
    records = ({"business_id": "b1"}, {"name": "no id"}, {})
    assert stable_record_ids(records, DEFAULT_CONFIG) == (
        "b1",
        "row-0001",
        "row-0002",
    )


def test_duplicate_pairs_matches_the_engine():
    candidates = find_duplicate_candidates(RECORDS, DEFAULT_CONFIG)
    assert duplicate_pairs(candidates) == (("b1", "b2"),)


def test_inputs_are_not_mutated():
    records = (
        {"business_id": "b1", "name": "Acme"},
        {"business_id": "b2", "name": "Acme"},
    )
    records_snapshot = copy.deepcopy(records)

    record_ids = list(stable_record_ids(records, DEFAULT_CONFIG))
    ids_snapshot = list(record_ids)
    candidates = find_duplicate_candidates(RECORDS, DEFAULT_CONFIG)
    pairs = [tuple(pair) for pair in duplicate_pairs(candidates)]
    pairs_snapshot = [tuple(pair) for pair in pairs]

    text = _doc(
        duplicates=[
            {"business_id_a": "b1", "business_id_b": "b2", "decision": "confirmed"}
        ]
    )
    decisions = parse_decisions(text)
    validate_decisions(decisions, record_ids=record_ids, candidate_pairs=pairs)

    assert records == records_snapshot
    assert record_ids == ids_snapshot
    assert pairs == pairs_snapshot


def test_error_messages_do_not_include_record_contents():
    records = (
        {"business_id": "b1", "name": "Super Secret Business Name"},
    )
    record_ids = stable_record_ids(records, DEFAULT_CONFIG)
    text = _doc(
        duplicates=[
            {"business_id_a": "b1", "business_id_b": "ghost", "decision": "confirmed"}
        ]
    )
    decisions = parse_decisions(text)

    with pytest.raises(InputError) as excinfo:
        validate_decisions(decisions, record_ids=record_ids, candidate_pairs=[])

    message = str(excinfo.value)
    assert "Super Secret Business Name" not in message
    assert "ghost" in message


def test_existing_analysis_is_unaffected_by_remediation():
    loaded = load_records(SAMPLE, DEFAULT_CONFIG.id_field)
    before = scan(loaded.records, DEFAULT_CONFIG)

    decisions = parse_decisions(_doc())
    assert decisions.duplicates == ()

    after = scan(loaded.records, DEFAULT_CONFIG)
    assert before == after
