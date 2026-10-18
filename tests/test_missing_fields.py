"""Tests for the missing-field rule."""

from localbiz_scout.rules import RULE_MISSING_FIELD, RuleConfig, check_missing_fields


def test_present_value_is_not_reported():
    records = [{"business_id": "b1", "name": "Acme Coffee"}]
    assert check_missing_fields(records, RuleConfig()) == []


def test_empty_string_is_reported():
    records = [{"business_id": "b1", "name": ""}]
    findings = check_missing_fields(records, RuleConfig())
    assert len(findings) == 1
    assert findings[0].rule_id == RULE_MISSING_FIELD
    assert findings[0].business_id == "b1"
    assert findings[0].field == "name"


def test_whitespace_only_is_reported():
    records = [{"business_id": "b1", "name": "   \t  "}]
    findings = check_missing_fields(records, RuleConfig())
    assert [f.field for f in findings] == ["name"]


def test_none_is_reported():
    records = [{"business_id": "b1", "name": None}]
    findings = check_missing_fields(records, RuleConfig())
    assert [f.field for f in findings] == ["name"]


def test_field_absent_from_schema_is_not_reported():
    records = [{"business_id": "b1"}]  # no 'name'/'email' keys at all
    assert check_missing_fields(records, RuleConfig()) == []


def test_multiple_missing_fields_each_reported_once():
    records = [{"business_id": "b1", "name": "", "city": "  ", "state": "TX"}]
    findings = check_missing_fields(records, RuleConfig())
    assert sorted(f.field for f in findings) == ["city", "name"]


def test_finding_carries_identifier_field_and_evidence():
    records = [{"business_id": "b1", "name": "   "}]
    finding = check_missing_fields(records, RuleConfig())[0]
    assert finding.rule_id == RULE_MISSING_FIELD
    assert finding.business_id == "b1"
    assert finding.field == "name"
    assert finding.evidence["raw_value"] == "   "


def test_missing_identifier_falls_back_to_row_id():
    records = [{"name": ""}]
    finding = check_missing_fields(records, RuleConfig())[0]
    assert finding.business_id == "row-0000"
