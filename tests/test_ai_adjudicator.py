"""Tests for AI adjudication: routing, parsing, fallback, immutability."""

import copy
import json

import pytest

from localbiz_scout.ai.adjudicator import (
    STATUS_INVALID,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    InvalidResponseError,
    adjudicate_candidates,
    build_prompt,
    build_record_view,
    is_ambiguous,
    parse_verdict,
)
from localbiz_scout.ai.client import AIServiceError, AITimeoutError, AIUnavailableError
from localbiz_scout.rules import scan
from localbiz_scout.rules.config import DEFAULT_CONFIG


def rec(bid, name, address="1 Main Street", phone="512-555-0100", **over):
    record = {
        "business_id": bid,
        "name": name,
        "address": address,
        "city": "Austin",
        "state": "TX",
        "postal_code": "78701",
        "phone": phone,
        "website": "https://x.example.com",
        "category": "cafe",
    }
    record.update(over)
    return record


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, *, model, system, user, timeout, format_json=True):
        self.calls.append({"model": model, "system": system, "user": user, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def verdict(same_entity, reason="looks similar", signals=None):
    return json.dumps(
        {
            "same_entity": same_entity,
            "reason": reason,
            "signals": signals if signals is not None else ["name", "address"],
        }
    )


def build_candidates(records):
    return list(scan(records, DEFAULT_CONFIG).duplicate_candidates)


# --------------------------------------------------------------------------- #
# Prompt construction / privacy
# --------------------------------------------------------------------------- #


def test_build_record_view_excludes_contact_fields():
    view = build_record_view(rec("b1", "Acme", "1 Main St", phone="512-555-9999"))
    assert "phone" not in view
    assert "email" not in view
    assert set(view) == {"name", "address", "city", "state", "postal_code", "category", "website"}


def test_build_prompt_omits_phone_and_email():
    a = rec("b1", "Riverstone Dental", "1 Main St", phone="512-555-9999", email="secret@example.com")
    b = rec("b2", "Riverstone Dental LLC", "1 Main St", phone="512-555-9999", email="secret@example.com")
    candidate = build_candidates([a, b])[0]
    system, user = build_prompt(candidate, a, b)
    assert "512-555-9999" not in user
    assert "secret@example.com" not in user
    assert "Riverstone" in user
    assert system.startswith("You are a conservative")


# --------------------------------------------------------------------------- #
# Strict parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["yes", "no", "uncertain"])
def test_parse_valid_verdict(value):
    parsed = parse_verdict(verdict(value), ["name", "address"])
    assert parsed.same_entity == value
    assert parsed.reason == "looks similar"
    assert parsed.signals == ("name", "address")


def test_parse_deduplicates_signals():
    parsed = parse_verdict(verdict("yes", signals=["name", "name"]), ["name"])
    assert parsed.signals == ("name",)


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "[1, 2, 3]",
        json.dumps({"reason": "x", "signals": []}),
        json.dumps({"same_entity": "maybe", "reason": "x", "signals": []}),
        json.dumps({"same_entity": "yes", "reason": "", "signals": []}),
        json.dumps({"same_entity": "yes", "reason": "x", "signals": "name"}),
        json.dumps({"same_entity": "yes", "reason": "x", "signals": [1]}),
        json.dumps({"same_entity": "yes", "reason": "x", "signals": ["phone"]}),
    ],
)
def test_parse_rejects_bad_responses(content):
    with pytest.raises(InvalidResponseError):
        parse_verdict(content, ["name", "address"])


# --------------------------------------------------------------------------- #
# Routing / fallback behaviour
# --------------------------------------------------------------------------- #


def test_only_ambiguous_candidates_are_sent():
    records = [
        rec("b1", "Riverstone Dental", "1 Main Street"),
        rec("b2", "riverstone dental", "1 Main St"),           # normalized (not ambiguous)
        rec("b3", "Riverstone Dental LLC", "1 Main Street"),   # fuzzy (ambiguous)
    ]
    candidates = build_candidates(records)
    reasons = {c.reason for c in candidates}
    assert {"normalized", "fuzzy"} <= reasons

    fuzzy_count = sum(1 for c in candidates if c.reason == "fuzzy")
    by_id = {r["business_id"]: r for r in records}
    client = FakeClient([verdict("yes")] * fuzzy_count)
    report = adjudicate_candidates(candidates, by_id, client)

    # Only fuzzy candidates are sent; the normalized pair is never adjudicated.
    assert len(client.calls) == fuzzy_count
    assert all(r.deterministic_reason == "fuzzy" for r in report.results)
    assert all(r.ai_status == STATUS_OK for r in report.results)


def test_no_ambiguous_candidates_skips_client():
    records = [
        rec("b1", "Riverstone Dental", "1 Main Street"),
        rec("b2", "riverstone dental", "1 Main St"),
    ]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    client = FakeClient([])
    report = adjudicate_candidates(candidates, by_id, client)
    assert client.calls == []
    assert report.results == ()
    assert report.status == STATUS_OK


def test_yes_keeps_candidate_positive():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    report = adjudicate_candidates(candidates, by_id, FakeClient([verdict("yes")]))
    assert report.results[0].same_entity == "yes"
    assert report.results[0].effective_positive is True
    assert report.status == STATUS_OK


def test_no_demotes_candidate():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    report = adjudicate_candidates(candidates, by_id, FakeClient([verdict("no")]))
    assert report.results[0].effective_positive is False


def test_uncertain_keeps_candidate_positive():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    report = adjudicate_candidates(candidates, by_id, FakeClient([verdict("uncertain")]))
    assert report.results[0].effective_positive is True


def test_timeout_falls_back_to_deterministic():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    report = adjudicate_candidates(
        candidates, by_id, FakeClient([AITimeoutError("slow")]), timeout=1
    )
    result = report.results[0]
    assert result.ai_status == STATUS_UNAVAILABLE
    assert result.same_entity is None
    assert result.effective_positive is True
    assert report.status == STATUS_UNAVAILABLE


def test_timeout_does_not_short_circuit():
    records = [
        rec("b1", "Riverstone Dental", "1 Main Street"),
        rec("b2", "Riverstone Dental LLC", "1 Main Street"),
        rec("b3", "Riverstone Dental Inc", "1 Main Street"),
    ]
    candidates = build_candidates(records)
    assert len(candidates) >= 2
    by_id = {r["business_id"]: r for r in records}
    client = FakeClient([AITimeoutError("slow")] * len(candidates))
    report = adjudicate_candidates(candidates, by_id, client, timeout=1)
    # Every ambiguous pair was attempted (no short-circuit on timeout).
    assert len(client.calls) == len(candidates)
    assert all(r.ai_status == STATUS_UNAVAILABLE for r in report.results)


def test_service_unavailable_short_circuits():
    records = [
        rec("b1", "Riverstone Dental", "1 Main Street"),
        rec("b2", "Riverstone Dental LLC", "1 Main Street"),
        rec("b3", "Riverstone Dental Inc", "1 Main Street"),
    ]
    candidates = build_candidates(records)
    assert len(candidates) >= 2
    by_id = {r["business_id"]: r for r in records}
    client = FakeClient([AIUnavailableError("down")])
    report = adjudicate_candidates(candidates, by_id, client)
    assert len(client.calls) == 1  # short-circuited after first failure
    assert all(r.ai_status == STATUS_UNAVAILABLE for r in report.results)


def test_service_error_falls_back():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    report = adjudicate_candidates(candidates, by_id, FakeClient([AIServiceError("boom")]))
    assert report.results[0].ai_status == STATUS_UNAVAILABLE
    assert report.results[0].effective_positive is True


def test_invalid_response_falls_back():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    report = adjudicate_candidates(candidates, by_id, FakeClient(["definitely not json"]))
    assert report.results[0].ai_status == STATUS_INVALID
    assert report.results[0].effective_positive is True


def test_unsupported_signal_is_rejected():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    bad = json.dumps({"same_entity": "no", "reason": "x", "signals": ["phone"]})
    report = adjudicate_candidates(candidates, by_id, FakeClient([bad]))
    assert report.results[0].ai_status == STATUS_INVALID


def test_missing_record_falls_back():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    report = adjudicate_candidates(candidates, {}, FakeClient([]))
    assert report.results[0].ai_status == STATUS_UNAVAILABLE
    assert report.results[0].effective_positive is True


def test_model_name_is_recorded():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    report = adjudicate_candidates(
        candidates, by_id, FakeClient([verdict("yes")]), model="gemma3:1b"
    )
    assert report.model == "gemma3:1b"
    assert report.to_dict()["model"] == "gemma3:1b"


def test_records_are_not_mutated():
    records = [rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")]
    before = copy.deepcopy(records)
    candidates = build_candidates(records)
    by_id = {r["business_id"]: r for r in records}
    adjudicate_candidates(candidates, by_id, FakeClient([verdict("no")]))
    assert records == before


def test_is_ambiguous_only_fuzzy():
    fuzzy = build_candidates([rec("b1", "Riverstone Dental"), rec("b2", "Riverstone Dental LLC")])[0]
    assert is_ambiguous(fuzzy) is True
    normalized = build_candidates(
        [rec("b1", "Riverstone Dental", "1 Main Street"), rec("b2", "riverstone dental", "1 Main St")]
    )[0]
    assert is_ambiguous(normalized) is False
