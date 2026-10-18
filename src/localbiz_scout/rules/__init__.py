"""Deterministic data-quality rules for LocalBiz Scout.

The rules are pure functions over sequences of record mappings. They do not
perform I/O, use the network, or mutate their inputs, and they return
structured findings suitable for a future JSON report.

This package deliberately contains no CLI, report writer, or AI integration.
"""

from __future__ import annotations

from .config import DEFAULT_CONFIG, RuleConfig
from .duplicates import (
    REASON_EXACT,
    REASON_FUZZY,
    REASON_NORMALIZED,
    find_duplicate_candidates,
)
from .email import check_email, is_valid_email
from .engine import ScanResult, scan
from .findings import (
    RULE_DUPLICATE,
    RULE_INVALID_EMAIL,
    RULE_MISSING_FIELD,
    RULE_PLACEHOLDER_NAME,
    RULE_SUSPICIOUS_PHONE_LENGTH,
    RULE_SUSPICIOUS_POSTAL_CODE,
    RULE_SUSPICIOUS_STATE_CODE,
    DuplicateCandidate,
    Finding,
)
from .missing_fields import check_missing_fields
from .suspicious import check_suspicious_fields

__all__ = [
    "DEFAULT_CONFIG",
    "RuleConfig",
    "Finding",
    "DuplicateCandidate",
    "ScanResult",
    "scan",
    "check_missing_fields",
    "check_email",
    "check_suspicious_fields",
    "find_duplicate_candidates",
    "is_valid_email",
    "REASON_EXACT",
    "REASON_NORMALIZED",
    "REASON_FUZZY",
    "RULE_MISSING_FIELD",
    "RULE_INVALID_EMAIL",
    "RULE_DUPLICATE",
    "RULE_SUSPICIOUS_PHONE_LENGTH",
    "RULE_SUSPICIOUS_STATE_CODE",
    "RULE_SUSPICIOUS_POSTAL_CODE",
    "RULE_PLACEHOLDER_NAME",
]
