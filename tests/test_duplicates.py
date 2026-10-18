"""Tests for the duplicate-candidate rule."""

from localbiz_scout.rules import (
    REASON_EXACT,
    REASON_FUZZY,
    REASON_NORMALIZED,
    RuleConfig,
    find_duplicate_candidates,
)

BASE = {
    "name": "Acme Coffee",
    "address": "1 Main Street",
    "city": "Austin",
    "state": "TX",
    "postal_code": "78701",
    "phone": "512-555-0100",
}


def rec(business_id, **overrides):
    record = {"business_id": business_id, **BASE}
    record.update(overrides)
    return record


def test_exact_duplicate_pair():
    records = [rec("b1"), rec("b2")]
    candidates = find_duplicate_candidates(records, RuleConfig())
    assert len(candidates) == 1
    assert candidates[0].reason == REASON_EXACT
    assert candidates[0].similarity == 100.0


def test_normalized_match_pair():
    records = [
        rec("b1"),
        rec(
            "b2",
            name="  ACME   COFFEE ",
            address="1 Main St",
            phone="(512) 555-0100",
        ),
    ]
    candidates = find_duplicate_candidates(records, RuleConfig())
    assert len(candidates) == 1
    assert candidates[0].reason == REASON_NORMALIZED
    assert candidates[0].similarity == 100.0


def test_fuzzy_candidate_pair():
    records = [
        rec("b1", name="Riverstone Dental"),
        rec("b2", name="Riverstone Dental LLC"),
    ]
    candidates = find_duplicate_candidates(records, RuleConfig())
    assert len(candidates) == 1
    assert candidates[0].reason == REASON_FUZZY
    assert candidates[0].similarity >= 85.0
    assert candidates[0].evidence["name_similarity"] >= 85.0
    assert candidates[0].evidence["address_similarity"] == 100.0


def test_non_matching_records_produce_no_candidate():
    records = [
        rec("b1"),
        rec("b2", name="Bob Plumbing", address="900 Oak Avenue", postal_code="78702"),
    ]
    assert find_duplicate_candidates(records, RuleConfig()) == []


def test_similar_names_with_different_address_are_not_fuzzy():
    # High name similarity, but the address condition keeps this out.
    records = [
        rec("b1", name="Summit Yoga Studio", address="1500 E 6th Street"),
        rec("b2", name="Summit Yoga & Wellness", address="2200 S Lamar Blvd"),
    ]
    assert find_duplicate_candidates(records, RuleConfig()) == []


def test_fuzzy_threshold_is_respected():
    records = [
        rec("b1", name="Riverstone Dental"),
        rec("b2", name="Riverstone Dental LLC"),
    ]
    strict = RuleConfig().replace(
        name_similarity_threshold=99.0, address_similarity_threshold=99.0
    )
    assert find_duplicate_candidates(records, strict) == []


def test_fuzzy_matching_can_be_disabled():
    records = [
        rec("b1", name="Riverstone Dental"),
        rec("b2", name="Riverstone Dental LLC"),
    ]
    disabled = RuleConfig().replace(fuzzy_matching_enabled=False)
    assert find_duplicate_candidates(records, disabled) == []


def test_record_is_not_compared_with_itself():
    assert find_duplicate_candidates([rec("b1")], RuleConfig()) == []


def test_same_id_is_not_paired():
    records = [rec("dup"), rec("dup")]
    assert find_duplicate_candidates(records, RuleConfig()) == []


def test_each_unordered_pair_emitted_once_for_three_identical_records():
    records = [rec("b1"), rec("b2"), rec("b3")]
    candidates = find_duplicate_candidates(records, RuleConfig())
    keys = [(c.business_id_a, c.business_id_b) for c in candidates]
    assert keys == [("b1", "b2"), ("b1", "b3"), ("b2", "b3")]
    assert len(keys) == len(set(keys))


def test_pair_ids_are_ordered_regardless_of_input_order():
    candidates = find_duplicate_candidates([rec("b2"), rec("b1")], RuleConfig())
    assert (candidates[0].business_id_a, candidates[0].business_id_b) == ("b1", "b2")


def test_strongest_reason_wins_and_no_duplicate_rows():
    # b2 normalizes to b1 but is not byte-identical; only one row should exist.
    records = [rec("b1"), rec("b2", name="acme coffee"), rec("b1")]
    candidates = find_duplicate_candidates(records, RuleConfig())
    pair_keys = {(c.business_id_a, c.business_id_b) for c in candidates}
    assert ("b1", "b2") in pair_keys
    assert len(candidates) == len(pair_keys)
