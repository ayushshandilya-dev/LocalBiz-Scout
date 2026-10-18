"""Write reviewable reports from a completed scan.

Three artifacts are produced in the requested output directory:

* ``findings.json``          -- structured findings plus run metadata.
* ``report.md``              -- human-readable summary.
* ``duplicates_review.csv``  -- one row per duplicate candidate.

The output is byte-stable for a given input (no timestamps are embedded), so
repeated runs are easy to diff. Nothing here re-implements rule logic; it only
formats the structures produced by :mod:`localbiz_scout.rules`.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import __version__
from .ai import AIReport
from .ai.adjudicator import STATUS_OK
from .errors import OutputError
from .loader import LoadedCSV
from .rules import RULE_MISSING_FIELD
from .rules.config import RuleConfig
from .rules.engine import ScanResult
from .rules.findings import (
    RULE_DUPLICATE,
    RULE_INVALID_EMAIL,
    RULE_PLACEHOLDER_NAME,
    RULE_SUSPICIOUS_PHONE_LENGTH,
    RULE_SUSPICIOUS_POSTAL_CODE,
    RULE_SUSPICIOUS_STATE_CODE,
)

# Presentation-only severity labels. They describe review urgency, not a
# judgement about the business, and are not a risk or fraud score.
RULE_SEVERITY: dict[str, str] = {
    RULE_MISSING_FIELD: "medium",
    RULE_INVALID_EMAIL: "medium",
    RULE_SUSPICIOUS_PHONE_LENGTH: "low",
    RULE_SUSPICIOUS_STATE_CODE: "low",
    RULE_SUSPICIOUS_POSTAL_CODE: "low",
    RULE_PLACEHOLDER_NAME: "low",
}
DEFAULT_SEVERITY = "low"
SEVERITY_ORDER = ("medium", "low")

FINDINGS_JSON = "findings.json"
REPORT_MD = "report.md"
DUPLICATES_CSV = "duplicates_review.csv"
AI_VERDICTS_JSON = "ai_verdicts.json"

DUPLICATE_REVIEW_COLUMNS = [
    "business_id_a",
    "business_id_b",
    "reason",
    "similarity",
    "name_similarity",
    "address_similarity",
    "normalized_name_a",
    "normalized_name_b",
    "matched_identity_fields",
]

LIMITATIONS = [
    "Duplicate candidates are review suggestions, not confirmed duplicates; "
    "the tool never merges or deletes records.",
    "There is no 'confirmed duplicate' output. Everything under duplicate "
    "candidates is an unreviewed pair awaiting a human decision.",
    "Email findings describe syntax only and do not imply that an address "
    "exists or that its mailbox is reachable.",
    "Suspicious-value checks are formatting checks and do not imply that a "
    "business is fake or fraudulent.",
    "The suspicious-value checks are US-centric and may flag legitimately "
    "formatted foreign addresses and phone numbers.",
    "Cross-field consistency (for example, city-to-state) is not checked "
    "because the schema provides no reference data.",
    "Fuzzy matching uses explicit similarity thresholds and is a heuristic for "
    "human review, not proof of identity.",
]


def severity_for(rule_id: str) -> str:
    return RULE_SEVERITY.get(rule_id, DEFAULT_SEVERITY)


def expected_fields(config: RuleConfig) -> tuple[str, ...]:
    """Fields the rules can make use of, in a stable order."""

    ordered: list[str] = [config.id_field]
    for field_name in (*config.important_fields, *config.duplicate_identity_fields):
        if field_name not in ordered:
            ordered.append(field_name)
    return tuple(ordered)


def ai_summary(ai_report: AIReport) -> dict[str, Any]:
    """Summary of an AI run: model, status, and verdict counts."""

    verdict_counts: dict[str, int] = {}
    for verdict in ai_report.results:
        if verdict.ai_status == STATUS_OK and verdict.same_entity is not None:
            verdict_counts[verdict.same_entity] = (
                verdict_counts.get(verdict.same_entity, 0) + 1
            )

    return {
        "enabled": True,
        "model": ai_report.model,
        "status": ai_report.status,
        "message": ai_report.message,
        "adjudicated_count": len(ai_report.results),
        "verdict_counts": verdict_counts,
        "fallback_count": sum(
            1 for verdict in ai_report.results if verdict.ai_status != STATUS_OK
        ),
    }


def build_metadata(
    loaded: LoadedCSV,
    result: ScanResult,
    config: RuleConfig,
    ai_report: AIReport | None = None,
) -> dict[str, Any]:
    columns = loaded.columns
    expected = expected_fields(config)

    present = [field for field in expected if field in columns]
    missing_optional = [
        field
        for field in expected
        if field not in columns and field != config.id_field
    ]
    additional = [field for field in columns if field not in expected]

    findings_by_rule = _count_by(finding.rule_id for finding in result.findings)
    findings_by_severity = _count_by(
        severity_for(finding.rule_id) for finding in result.findings
    )
    duplicates_by_reason = _count_by(
        candidate.reason for candidate in result.duplicate_candidates
    )

    return {
        "tool": "localbiz-scout",
        "version": __version__,
        "input_file": str(loaded.path),
        "record_count": len(loaded.records),
        "columns": list(columns),
        "present_fields": present,
        "missing_optional_fields": missing_optional,
        "additional_fields": additional,
        "finding_count": len(result.findings),
        "findings_by_rule": findings_by_rule,
        "findings_by_severity": findings_by_severity,
        "duplicate_candidate_count": len(result.duplicate_candidates),
        "duplicates_by_reason": duplicates_by_reason,
        "thresholds": {
            "name_similarity": config.name_similarity_threshold,
            "address_similarity": config.address_similarity_threshold,
        },
        "severity_labels": dict(RULE_SEVERITY),
        "notes": list(LIMITATIONS),
    }


def build_findings_document(
    loaded: LoadedCSV,
    result: ScanResult,
    config: RuleConfig,
    ai_report: AIReport | None = None,
) -> dict[str, Any]:
    metadata = build_metadata(loaded, result, config, ai_report)
    if ai_report is not None:
        metadata["ai"] = ai_summary(ai_report)

    document: dict[str, Any] = {
        "metadata": metadata,
        "findings": [finding.to_dict() for finding in result.findings],
        "duplicate_candidates": [
            candidate.to_dict() for candidate in result.duplicate_candidates
        ],
    }
    if ai_report is not None:
        document["ai_verdicts"] = [verdict.to_dict() for verdict in ai_report.results]
    return document


def build_ai_document(ai_report: AIReport) -> dict[str, Any]:
    return {
        "model": ai_report.model,
        "status": ai_report.status,
        "message": ai_report.message,
        "verdicts": [verdict.to_dict() for verdict in ai_report.results],
    }


def duplicate_review_rows(result: ScanResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in result.duplicate_candidates:
        evidence = candidate.evidence
        normalized_values = evidence.get("normalized_values") or {}
        normalized_name = normalized_values.get("name", "") if normalized_values else ""

        rows.append(
            {
                "business_id_a": candidate.business_id_a,
                "business_id_b": candidate.business_id_b,
                "reason": candidate.reason,
                "similarity": candidate.similarity,
                "name_similarity": evidence.get("name_similarity", ""),
                "address_similarity": evidence.get("address_similarity", ""),
                "normalized_name_a": evidence.get("normalized_name_a", normalized_name),
                "normalized_name_b": evidence.get("normalized_name_b", normalized_name),
                "matched_identity_fields": ";".join(
                    evidence.get("identity_fields", [])
                ),
            }
        )
    return rows


def render_markdown(
    loaded: LoadedCSV,
    result: ScanResult,
    config: RuleConfig,
    ai_report: AIReport | None = None,
) -> str:
    metadata = build_metadata(loaded, result, config, ai_report)

    lines: list[str] = []
    lines.append("# LocalBiz Scout report")
    lines.append("")
    lines.append(f"- Input file: `{metadata['input_file']}`")
    lines.append(f"- Records analysed: {metadata['record_count']}")
    lines.append(f"- Findings: {metadata['finding_count']}")
    lines.append(f"- Duplicate candidates: {metadata['duplicate_candidate_count']}")
    lines.append("")

    lines.append("## Columns")
    lines.append("")
    lines.append(
        "- Present: "
        + (", ".join(f"`{field}`" for field in metadata["present_fields"]) or "_none_")
    )
    lines.append(
        "- Missing optional: "
        + (
            ", ".join(f"`{field}`" for field in metadata["missing_optional_fields"])
            or "_none_"
        )
    )
    if metadata["additional_fields"]:
        lines.append(
            "- Additional (not used by rules): "
            + ", ".join(f"`{field}`" for field in metadata["additional_fields"])
        )
    lines.append("")

    lines.append("## Findings by rule")
    lines.append("")
    if result.findings:
        lines.append("| Rule | Severity | Count |")
        lines.append("| --- | --- | --- |")
        for rule_id in sorted(metadata["findings_by_rule"]):
            lines.append(
                f"| `{rule_id}` | {severity_for(rule_id)} | "
                f"{metadata['findings_by_rule'][rule_id]} |"
            )
    else:
        lines.append("No findings were produced.")
    lines.append("")

    lines.append("## Findings by severity")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("| --- | --- |")
    for severity in SEVERITY_ORDER:
        lines.append(f"| {severity} | {metadata['findings_by_severity'].get(severity, 0)} |")
    lines.append("")

    lines.append("## Duplicate candidates")
    lines.append("")
    lines.append(
        "LocalBiz Scout does **not** confirm duplicates. Every pair below is an "
        "**unreviewed candidate** (a suggestion for a human reviewer); there is "
        "no 'confirmed duplicate' section. See `duplicates_review.csv` for the "
        "full list."
    )
    lines.append("")
    if result.duplicate_candidates:
        lines.append("| Reason | Count |")
        lines.append("| --- | --- |")
        for reason in sorted(metadata["duplicates_by_reason"]):
            lines.append(f"| `{reason}` | {metadata['duplicates_by_reason'][reason]} |")
    else:
        lines.append("No duplicate candidates were found.")
    lines.append("")

    if ai_report is not None:
        summary = ai_summary(ai_report)
        lines.append("## AI adjudication")
        lines.append("")
        lines.append(f"- Model: `{summary['model']}`")
        lines.append(f"- Status: `{summary['status']}`")
        lines.append(f"- {summary['message']}")
        lines.append("")
        if ai_report.results:
            lines.append("| Verdict | Count |")
            lines.append("| --- | --- |")
            for verdict in ("yes", "no", "uncertain"):
                lines.append(
                    f"| {verdict} | {summary['verdict_counts'].get(verdict, 0)} |"
                )
            lines.append(f"| (fell back) | {summary['fallback_count']} |")
            lines.append("")
        lines.append(
            "AI verdicts are advisory only. A `no` verdict drops the pair from "
            "the candidate list; it does not confirm the records are different, "
            "and nothing is merged, edited, or deleted. Any failure falls back to "
            "the deterministic result. Only non-contact fields were sent for "
            "ambiguous candidates."
        )
        lines.append("")

    lines.append("## Limitations")
    lines.append("")
    for note in LIMITATIONS:
        lines.append(f"- {note}")
    lines.append("")

    return "\n".join(lines)


def _count_by(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _write_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        raise OutputError(f"could not write {path}: {error}") from error


def write_reports(
    loaded: LoadedCSV,
    result: ScanResult,
    out_dir: str | Path,
    config: RuleConfig,
    ai_report: AIReport | None = None,
) -> dict[str, Path]:
    """Write all report artifacts and return their paths."""

    output_dir = Path(out_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OutputError(
            f"could not create output directory {output_dir}: {error}"
        ) from error

    if not output_dir.is_dir():
        raise OutputError(f"output path is not a directory: {output_dir}")

    paths = {
        "findings_json": output_dir / FINDINGS_JSON,
        "report_md": output_dir / REPORT_MD,
        "duplicates_csv": output_dir / DUPLICATES_CSV,
    }

    if ai_report is not None:
        paths["ai_verdicts_json"] = output_dir / AI_VERDICTS_JSON

    document = build_findings_document(loaded, result, config, ai_report)
    _write_text(
        paths["findings_json"],
        json.dumps(document, indent=2, sort_keys=False) + "\n",
    )
    _write_text(
        paths["report_md"], render_markdown(loaded, result, config, ai_report)
    )

    try:
        with paths["duplicates_csv"].open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=DUPLICATE_REVIEW_COLUMNS)
            writer.writeheader()
            writer.writerows(duplicate_review_rows(result))
    except OSError as error:
        raise OutputError(f"could not write {paths['duplicates_csv']}: {error}") from error

    if ai_report is not None:
        _write_text(
            paths["ai_verdicts_json"],
            json.dumps(build_ai_document(ai_report), indent=2, sort_keys=False) + "\n",
        )

    return paths
