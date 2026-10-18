"""Tests for the rule engine: determinism, immutability, and serialization."""

import copy
import csv
import json
from pathlib import Path

from localbiz_scout.rules import ScanResult, scan

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = REPO_ROOT / "data" / "sample_businesses.csv"


def sample_records():
    return [
        {
            "business_id": "b1",
            "name": "Acme Coffee",
            "address": "1 Main Street",
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "phone": "512-555-0100",
            "email": "owner@example.com",
        },
        {
            "business_id": "b2",
            "name": "  acme coffee ",
            "address": "1 Main St",
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "phone": "(512) 555-0100",
            "email": "   ",
        },
        {
            "business_id": "b3",
            "name": "Bob Plumbing",
            "address": "900 Oak Ave",
            "city": "Austin",
            "state": "TX",
            "postal_code": "ABCDE",
            "phone": "51255501001",
            "email": "bad@@example.com",
        },
    ]


def test_scan_returns_structured_result():
    result = scan(sample_records())
    assert isinstance(result, ScanResult)
    assert isinstance(result.findings, tuple)
    assert isinstance(result.duplicate_candidates, tuple)
    assert result.findings
    assert result.duplicate_candidates


def test_scan_is_deterministic():
    assert scan(sample_records()).to_dict() == scan(sample_records()).to_dict()


def test_scan_does_not_mutate_records():
    records = sample_records()
    before = copy.deepcopy(records)
    scan(records)
    assert records == before


def test_findings_sorted_by_identifier_for_stable_output():
    result = scan(sample_records())
    keys = [(f.business_id, f.rule_id, f.field or "") for f in result.findings]
    assert keys == sorted(keys)


def test_result_is_json_serializable():
    payload = scan(sample_records()).to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    assert isinstance(encoded, str)
    assert set(payload) == {"findings", "duplicate_candidates"}


def test_scan_detects_expected_issues_in_fixture():
    result = scan(sample_records())
    reasons = {c.reason for c in result.duplicate_candidates}
    assert "normalized" in reasons

    finding_pairs = {(f.business_id, f.rule_id) for f in result.findings}
    assert ("b2", "missing_field") in finding_pairs
    assert ("b3", "invalid_email") in finding_pairs
    assert ("b3", "suspicious_postal_code") in finding_pairs
    assert ("b3", "suspicious_phone_length") in finding_pairs


def _load_sample_csv():
    with SAMPLE_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_scan_sample_csv_runs_and_is_deterministic():
    # Integration smoke test over the shipped synthetic schema. Edge cases are
    # covered by the explicit fixtures above, not by this file alone.
    records = _load_sample_csv()
    first = scan(records).to_dict()
    second = scan(copy.deepcopy(records)).to_dict()
    assert first == second
    assert first["duplicate_candidates"]
