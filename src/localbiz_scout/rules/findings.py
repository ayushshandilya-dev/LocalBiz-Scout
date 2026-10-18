"""Structured result types produced by the data-quality rules.

Findings are plain, immutable, JSON-friendly records. They never reference or
mutate the source business records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# Stable rule identifiers. These are part of the (future) JSON report contract.
RULE_MISSING_FIELD = "missing_field"
RULE_INVALID_EMAIL = "invalid_email"
RULE_DUPLICATE = "duplicate_candidate"
RULE_SUSPICIOUS_PHONE_LENGTH = "suspicious_phone_length"
RULE_SUSPICIOUS_STATE_CODE = "suspicious_state_code"
RULE_SUSPICIOUS_POSTAL_CODE = "suspicious_postal_code"
RULE_PLACEHOLDER_NAME = "placeholder_name"


@dataclass(frozen=True)
class Finding:
    """A single per-record data-quality observation."""

    rule_id: str
    business_id: str
    field: str | None
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "business_id": self.business_id,
            "field": self.field,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class DuplicateCandidate:
    """A pair of records that may refer to the same business.

    A candidate is a prompt for human review, never an automatic merge. The
    ``reason`` is one of the documented match strengths ("exact", "normalized",
    "fuzzy"); ``similarity`` is a 0-100 score, not a probability.
    """

    business_id_a: str
    business_id_b: str
    reason: str
    similarity: float
    evidence: Mapping[str, Any] = field(default_factory=dict)
    rule_id: str = RULE_DUPLICATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "business_id_a": self.business_id_a,
            "business_id_b": self.business_id_b,
            "reason": self.reason,
            "similarity": self.similarity,
            "evidence": dict(self.evidence),
        }
