"""Read-only review scaffolding: proposals in, editable decisions out (Phase 4).

``analyze`` reports findings and duplicate *candidates*; ``remediate`` applies a
human-written decisions file. Nothing in between told a human *which*
corrections exist or what values they proposed. This module writes the missing
link: a version-1 decisions **template** (safe to run unchanged) plus a
human-readable listing of the correction proposals.

Everything here is read-only with respect to the source records:

* nothing is applied, merged, or deleted;
* the only file output is two review artifacts in ``--out``;
* there is no network access and no AI adjudication;
* the input CSV is never written and an output target that resolves to the input
  is refused;
* existing artifacts are refused unless ``force`` is requested, so an edited
  template is never silently replaced, and the pair is published atomically
  after every target has been checked.

The decisions contract itself is unchanged: this module only *emits* a document
that :func:`localbiz_scout.remediation.parse_decisions` and
:func:`localbiz_scout.remediation.validate_decisions` accept for the same input.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .corrections import CorrectionProposal
from .errors import InputError, OutputError
from .export import _guard_input_alias, _mktemp, _publish, _quiet_unlink
from .loader import LoadedCSV
from .remediation import (
    CORRECTION_DECISION_DEFERRED,
    DECISIONS_SCHEMA_VERSION,
    DUPLICATE_DECISION_DEFERRED,
    duplicate_pairs,
)
from .rules.engine import ScanResult

__all__ = [
    "OUTPUT_DECISIONS_TEMPLATE",
    "OUTPUT_CORRECTIONS_REVIEW",
    "REVIEW_COLUMNS",
    "build_decisions_template",
    "proposal_review_rows",
    "write_review",
]

OUTPUT_DECISIONS_TEMPLATE = "decisions_template.json"
OUTPUT_CORRECTIONS_REVIEW = "corrections_review.csv"

# Stable column order for the human-readable proposal listing.
REVIEW_COLUMNS = (
    "business_id",
    "field",
    "rule_id",
    "original_value",
    "proposed_value",
    "rationale",
)


def build_decisions_template(
    candidates: Sequence[Any],
    proposals: Sequence[CorrectionProposal],
) -> dict[str, Any]:
    """Build a version-1 decisions document with everything set to ``deferred``.

    Every correction proposal and every duplicate candidate appears exactly
    once. ``deferred`` means "not yet decided", so an unedited template is a
    safe no-op if it is handed straight to ``remediate``. Ordering follows the
    proposal order and the candidate order (both already deterministic), and no
    timestamps are embedded.

    The decisions schema identifies a correction by ``(business_id, field,
    rule_id)`` and forbids duplicates. :func:`propose_corrections` guarantees at
    most one proposal per record-and-field, so two proposals can only collide
    when two source records share the same identifier. In that case no unique
    decision exists, so this raises
    :class:`~localbiz_scout.errors.InputError` rather than emit a template its
    own validator would reject.
    """

    seen: set[tuple[str, str, str]] = set()
    corrections: list[dict[str, str]] = []
    for proposal in proposals:
        key = (proposal.business_id, proposal.field, proposal.rule_id)
        if key in seen:
            raise InputError(
                "review: cannot form a unique decisions template: duplicate "
                f"correction identity {key!r}; the input likely contains "
                "duplicate record identifiers"
            )
        seen.add(key)
        corrections.append(
            {
                "business_id": proposal.business_id,
                "field": proposal.field,
                "rule_id": proposal.rule_id,
                "proposed_value": proposal.proposed_value,
                "decision": CORRECTION_DECISION_DEFERRED,
            }
        )

    duplicates = [
        {
            "business_id_a": id_a,
            "business_id_b": id_b,
            "decision": DUPLICATE_DECISION_DEFERRED,
        }
        for id_a, id_b in duplicate_pairs(candidates)
    ]

    return {
        "version": DECISIONS_SCHEMA_VERSION,
        "corrections": corrections,
        "duplicates": duplicates,
    }


def proposal_review_rows(
    proposals: Sequence[CorrectionProposal],
) -> list[dict[str, str]]:
    """Return one human-readable row per correction proposal."""

    return [
        {
            "business_id": proposal.business_id,
            "field": proposal.field,
            "rule_id": proposal.rule_id,
            "original_value": proposal.original_value,
            "proposed_value": proposal.proposed_value,
            "rationale": proposal.rationale,
        }
        for proposal in proposals
    ]


def write_review(
    loaded: LoadedCSV,
    scan_result: ScanResult,
    proposals: Sequence[CorrectionProposal],
    out_dir: str | Path,
    *,
    force: bool = False,
) -> dict[str, Path]:
    """Write the decisions template and proposal listing to ``out_dir``.

    Refuses to overwrite an existing artifact unless ``force`` is true, so a
    previously edited ``decisions_template.json`` is never silently clobbered.
    Every precondition (template construction, input-alias guard, and the
    existence check across *both* targets) is evaluated before any file is
    written; the artifacts are then written to temporary files and published
    with atomic ``os.replace`` calls, so a failure cannot leave a partial pair
    or overwrite the input or any unrelated file.

    Raises :class:`~localbiz_scout.errors.OutputError` if the destination cannot
    be used, if an output target resolves to the input CSV (including via a
    symlink), or -- without ``force`` -- if any target already exists.
    """

    # Build everything first: a validation failure must not create the output
    # directory or leave any partial artifact behind.
    document = build_decisions_template(
        scan_result.duplicate_candidates, proposals
    )
    rows = proposal_review_rows(proposals)

    output_dir = _ensure_output_dir(out_dir)
    input_resolved = loaded.path.resolve()

    template_path = output_dir / OUTPUT_DECISIONS_TEMPLATE
    review_path = output_dir / OUTPUT_CORRECTIONS_REVIEW

    # Reuse the remediate guard so review refuses the same path aliases. This
    # runs before the existence check so an aliased input is always reported as
    # such, even when it collides with an output name.
    _guard_input_alias(input_resolved, (template_path, review_path))

    # Check every target before writing anything, so one pre-existing file can
    # never result in a half-written pair.
    if not force:
        existing = [path for path in (template_path, review_path) if path.exists()]
        if existing:
            rendered = ", ".join(str(path) for path in existing)
            raise OutputError(
                f"output already exists: {rendered} (pass --force to replace it)"
            )

    template_temp: Path | None = None
    review_temp: Path | None = None
    try:
        template_temp = _write_temp_text(
            output_dir,
            ".decisions-template-",
            ".json",
            json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        )
        review_temp = _write_temp_csv(output_dir, rows)
        _publish(((template_temp, template_path), (review_temp, review_path)))
        template_temp = None
        review_temp = None
    finally:
        for temp_path in (template_temp, review_temp):
            if temp_path is not None:
                _quiet_unlink(temp_path)

    return {
        "decisions_template": template_path,
        "corrections_review": review_path,
    }


def _ensure_output_dir(path: str | Path) -> Path:
    output_dir = Path(path)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OutputError(
            f"could not create output directory {output_dir}: {error}"
        ) from error
    if not output_dir.is_dir():
        raise OutputError(f"output path is not a directory: {output_dir}")
    return output_dir


def _write_temp_text(
    output_dir: Path, prefix: str, suffix: str, text: str
) -> Path:
    path = _mktemp(output_dir, prefix, suffix)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        _quiet_unlink(path)
        raise OutputError(f"could not write {path}: {error}") from error
    return path


def _write_temp_csv(output_dir: Path, rows: Sequence[Mapping[str, str]]) -> Path:
    path = _mktemp(output_dir, ".corrections-review-", ".csv")
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(REVIEW_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
    except OSError as error:
        _quiet_unlink(path)
        raise OutputError(f"could not write {path}: {error}") from error
    return path
