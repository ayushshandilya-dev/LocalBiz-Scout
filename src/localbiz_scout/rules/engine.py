"""Run all data-quality rules over a sequence of records.

Execution is deterministic, offline, and read-only: the input records are
never modified. The result is a structured :class:`ScanResult` that can be
serialized for a future JSON report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .config import DEFAULT_CONFIG, RuleConfig
from .duplicates import find_duplicate_candidates
from .email import check_email
from .findings import DuplicateCandidate, Finding
from .missing_fields import check_missing_fields
from .suspicious import check_suspicious_fields


@dataclass(frozen=True)
class ScanResult:
    findings: tuple[Finding, ...]
    duplicate_candidates: tuple[DuplicateCandidate, ...]

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "duplicate_candidates": [
                candidate.to_dict() for candidate in self.duplicate_candidates
            ],
        }


def scan(
    records: Sequence[Mapping[str, Any]],
    config: RuleConfig = DEFAULT_CONFIG,
) -> ScanResult:
    """Run every rule and return a deterministic, structured result."""

    findings: list[Finding] = []
    findings.extend(check_missing_fields(records, config))
    findings.extend(check_email(records, config))
    findings.extend(check_suspicious_fields(records, config))

    findings.sort(key=lambda finding: (finding.business_id, finding.rule_id, finding.field or ""))

    candidates = find_duplicate_candidates(records, config)

    return ScanResult(findings=tuple(findings), duplicate_candidates=tuple(candidates))
