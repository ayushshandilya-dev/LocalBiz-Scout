"""Tests for the shared normalization helpers."""

from localbiz_scout.rules.normalization import (
    as_text,
    is_blank,
    normalize_address,
    normalize_phone,
    normalize_text,
)


def test_case_and_whitespace_are_collapsed():
    assert normalize_text("  Blue   Bottle  Coffee ") == "blue bottle coffee"


def test_is_blank_handles_none_empty_and_whitespace():
    assert is_blank(None) is True
    assert is_blank("") is True
    assert is_blank("   \t ") is True
    assert is_blank("x") is False


def test_as_text_handles_none():
    assert as_text(None) == ""
    assert as_text(42) == "42"


def test_phone_punctuation_is_removed():
    assert normalize_phone("(512) 555-0100") == "5125550100"
    assert normalize_phone("512.555.0100") == "5125550100"


def test_phone_leading_country_code_is_dropped():
    assert normalize_phone("+1 512-555-0100") == "5125550100"
    assert normalize_phone("15125550100") == "5125550100"


def test_street_abbreviations_are_canonicalized():
    assert normalize_address("1 Main Street") == "1 main st"
    assert normalize_address("1 Main St") == "1 main st"
    assert normalize_address("700 W 5th Avenue,") == "700 w 5th ave"
