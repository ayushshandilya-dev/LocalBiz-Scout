"""Tests for the email syntax rule."""

import pytest

from localbiz_scout.rules import (
    RULE_INVALID_EMAIL,
    RULE_MISSING_FIELD,
    RuleConfig,
    check_email,
    check_missing_fields,
    is_valid_email,
)


@pytest.mark.parametrize(
    "email",
    [
        "owner@example.com",
        "first.last@sub.example.co",
        "tag+filter@example.org",
        "a_b-c@my-shop.example.com",
    ],
)
def test_valid_emails(email):
    assert is_valid_email(email) is True


@pytest.mark.parametrize(
    "email",
    [
        "no-at-sign",
        "@example.com",
        "user@",
        "user@@example.com",
        "user name@example.com",
        ".leading@example.com",
        "trailing.@example.com",
        "double..dot@example.com",
        "user@-bad.example.com",
        "user@bad-.example.com",
        "user@nodot",
        "user@example.c",
        "user@exa mple.com",
    ],
)
def test_malformed_emails(email):
    assert is_valid_email(email) is False


def test_invalid_email_produces_finding():
    records = [{"business_id": "b1", "email": "not-an-email"}]
    findings = check_email(records, RuleConfig())
    assert len(findings) == 1
    assert findings[0].rule_id == RULE_INVALID_EMAIL
    assert findings[0].business_id == "b1"
    assert findings[0].field == "email"


def test_valid_email_produces_no_finding():
    records = [{"business_id": "b1", "email": "owner@example.com"}]
    assert check_email(records, RuleConfig()) == []


def test_blank_email_is_delegated_to_missing_field_rule():
    records = [{"business_id": "b1", "email": "   "}]

    email_findings = check_email(records, RuleConfig())
    missing_findings = check_missing_fields(records, RuleConfig())

    assert email_findings == []
    assert [(f.rule_id, f.field) for f in missing_findings] == [
        (RULE_MISSING_FIELD, "email")
    ]


def test_absent_email_column_produces_no_findings():
    records = [{"business_id": "b1", "name": "Acme Coffee"}]
    assert check_email(records, RuleConfig()) == []
    assert check_missing_fields(records, RuleConfig()) == []
