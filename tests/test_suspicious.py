"""Tests for the suspicious-value checks and their documented limitations."""

from localbiz_scout.rules import (
    RULE_PLACEHOLDER_NAME,
    RULE_SUSPICIOUS_PHONE_LENGTH,
    RULE_SUSPICIOUS_POSTAL_CODE,
    RULE_SUSPICIOUS_STATE_CODE,
    RuleConfig,
    check_suspicious_fields,
)

IDENTIFIER_RULES = {
    RULE_SUSPICIOUS_PHONE_LENGTH,
    RULE_SUSPICIOUS_STATE_CODE,
    RULE_SUSPICIOUS_POSTAL_CODE,
    RULE_PLACEHOLDER_NAME,
}


def rules_for(record):
    return {f.rule_id for f in check_suspicious_fields([record], RuleConfig())}


def test_valid_values_produce_no_findings():
    record = {
        "business_id": "b1",
        "name": "Acme Coffee",
        "phone": "512-555-0100",
        "state": "TX",
        "postal_code": "78701",
    }
    assert rules_for(record) == set()


def test_phone_wrong_length_is_flagged():
    assert rules_for({"business_id": "b1", "phone": "512-555-01"}) == {
        RULE_SUSPICIOUS_PHONE_LENGTH
    }


def test_phone_with_leading_country_code_is_accepted():
    assert rules_for({"business_id": "b1", "phone": "+1 (512) 555-0100"}) == set()


def test_blank_phone_is_not_flagged_as_suspicious():
    assert rules_for({"business_id": "b1", "phone": "  "}) == set()


def test_non_two_letter_state_is_flagged():
    assert rules_for({"business_id": "b1", "state": "Texas"}) == {
        RULE_SUSPICIOUS_STATE_CODE
    }


def test_blank_state_is_not_flagged_as_suspicious():
    assert rules_for({"business_id": "b1", "state": ""}) == set()


def test_zip_and_zip_plus_four_are_accepted():
    assert rules_for({"business_id": "b1", "postal_code": "78701"}) == set()
    assert rules_for({"business_id": "b1", "postal_code": "78701-1234"}) == set()


def test_bad_postal_code_is_flagged():
    assert rules_for({"business_id": "b1", "postal_code": "ABCDE"}) == {
        RULE_SUSPICIOUS_POSTAL_CODE
    }


def test_placeholder_name_is_flagged():
    assert rules_for({"business_id": "b1", "name": "N/A"}) == {RULE_PLACEHOLDER_NAME}


def test_punctuation_only_name_is_flagged():
    assert rules_for({"business_id": "b1", "name": "---"}) == {RULE_PLACEHOLDER_NAME}


def test_blank_name_is_not_flagged_as_suspicious():
    assert rules_for({"business_id": "b1", "name": "   "}) == set()


def test_cross_field_consistency_is_not_checked_documented_limitation():
    # Every value is individually well-formed, even though Austin is in TX, not
    # CA. The schema has no reference data, so this is intentionally not
    # flagged as suspicious (it is only a formatting rule set).
    record = {
        "business_id": "b1",
        "name": "Acme Coffee",
        "city": "Austin",
        "state": "CA",
        "postal_code": "99999",
        "phone": "512-555-0100",
    }
    assert rules_for(record) == set()
