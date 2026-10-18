"""Tests for the duplicate-candidate rule."""

from rapidfuzz import fuzz

from localbiz_scout.rules import (
    REASON_EXACT,
    REASON_FUZZY,
    REASON_NORMALIZED,
    RuleConfig,
    find_duplicate_candidates,
)
from localbiz_scout.rules.normalization import (
    AddressComponents,
    normalize_address,
    parse_address_components,
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


# --- Address-component guard -------------------------------------------------
#
# These records are defined inline and are independent of any evaluation
# fixture, so they validate the rule rather than the fixture it was tuned on.


def _address_similarity(a, b):
    return fuzz.token_sort_ratio(normalize_address(a), normalize_address(b))


def _assert_similar_enough_to_be_fuzzy(a, b, config=None):
    config = config or RuleConfig()
    assert _address_similarity(a, b) >= config.address_similarity_threshold


def test_different_suite_numbers_are_not_fuzzy():
    # Identical names and near-identical addresses (94.12); only the suite
    # differs, so the pair must not be emitted as a duplicate candidate.
    left, right = "100 Main St Ste 2", "100 Main St Ste 8"
    _assert_similar_enough_to_be_fuzzy(left, right)
    records = [
        rec("b1", name="Acme Coffee", address=left),
        rec("b2", name="Acme Coffee", address=right),
    ]
    assert find_duplicate_candidates(records, RuleConfig()) == []


def test_different_leading_house_numbers_are_not_fuzzy():
    left, right = "18 Elm Street", "81 Elm Street"
    _assert_similar_enough_to_be_fuzzy(left, right)
    records = [
        rec("b1", name="Acme Coffee", address=left),
        rec("b2", name="Acme Coffee", address=right),
    ]
    assert find_duplicate_candidates(records, RuleConfig()) == []


def test_different_house_numbers_with_letter_suffix_are_not_fuzzy():
    left, right = "12A Main Street", "12B Main Street"
    _assert_similar_enough_to_be_fuzzy(left, right)
    records = [
        rec("b1", name="Acme Coffee", address=left),
        rec("b2", name="Acme Coffee", address=right),
    ]
    assert find_duplicate_candidates(records, RuleConfig()) == []


def test_business_name_variant_at_same_address_remains_a_candidate():
    records = [
        rec("b1", name="Riverstone Dental", address="12 Oak Avenue"),
        rec("b2", name="Riverstone Dental LLC", address="12 Oak Avenue"),
    ]
    candidates = find_duplicate_candidates(records, RuleConfig())
    assert len(candidates) == 1
    assert candidates[0].reason == REASON_FUZZY


def test_exact_and_normalized_matching_are_unchanged():
    exact = [rec("b1", address="100 Main St Ste 2"), rec("b2", address="100 Main St Ste 2")]
    exact_candidates = find_duplicate_candidates(exact, RuleConfig())
    assert len(exact_candidates) == 1
    assert exact_candidates[0].reason == REASON_EXACT

    # Same suite written differently normalizes to an identical address.
    normalized = [
        rec("b1", address="100 Main St Suite 2"),
        rec("b2", address="100 MAIN ST STE 2"),
    ]
    normalized_candidates = find_duplicate_candidates(normalized, RuleConfig())
    assert len(normalized_candidates) == 1
    assert normalized_candidates[0].reason == REASON_NORMALIZED


def test_address_without_parseable_house_number_falls_back_to_similarity():
    # The second record has no leading house number, so the component is
    # unknown and must not by itself reject the candidate.
    records = [
        rec("b1", name="Riverstone Dental", address="100 Main Street"),
        rec("b2", name="Riverstone Dental LLC", address="Main Street 100"),
    ]
    candidates = find_duplicate_candidates(records, RuleConfig())
    assert len(candidates) == 1
    assert candidates[0].reason == REASON_FUZZY


def test_po_box_numbers_are_not_treated_as_house_numbers():
    records = [
        rec("b1", name="Riverstone Dental", address="PO Box 5"),
        rec("b2", name="Riverstone Dental LLC", address="PO Box 6"),
    ]
    candidates = find_duplicate_candidates(records, RuleConfig())
    assert len(candidates) == 1
    assert candidates[0].reason == REASON_FUZZY


def test_parse_address_components_handles_casing_and_abbreviations():
    assert parse_address_components("100 Main St Ste 2") == AddressComponents("100", "2")
    assert parse_address_components("Suite 4") == AddressComponents(None, "4")
    assert parse_address_components("STE 04") == AddressComponents(None, "4")
    assert parse_address_components("Apt 2B") == AddressComponents(None, "2b")
    assert parse_address_components("Unit 7") == AddressComponents(None, "7")
    assert parse_address_components("12A Main Street") == AddressComponents("12a", None)


def test_parse_address_components_leaves_ambiguous_values_unknown():
    # Ranges, fractions, box numbers and trailing numbers are not house
    # numbers, and a marker without a value is not a unit.
    assert parse_address_components("100-102 Main St") == AddressComponents(None, None)
    assert parse_address_components("12.5 Main St") == AddressComponents(None, None)
    assert parse_address_components("PO Box 5") == AddressComponents(None, None)
    assert parse_address_components("Main Street 100") == AddressComponents(None, None)
    assert parse_address_components("100 Main St Ste") == AddressComponents("100", None)
    assert parse_address_components("") == AddressComponents(None, None)
