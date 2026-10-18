"""Safe, opt-in export of human-reviewed corrections (Phase 3.3).

This is the only remediation module that touches the filesystem. All business
logic stays in the earlier modules:

* :mod:`localbiz_scout.remediation` -- the decision-file contract;
* :mod:`localbiz_scout.corrections` -- proposal generation;
* :mod:`localbiz_scout.application` -- applying decisions and the audit model.

The workflow is deliberately conservative:

1. Read and validate the input with the existing loader.
2. Read, parse and validate the decisions file.
3. Generate proposals and apply decisions **in memory**.
4. Write the corrected CSV and the audit JSON to temporary files in the
   destination directory.
5. Reload the temporary CSV with the loader and prove it round-trips.
6. Publish the artifacts, rolling back if the second publish fails.
7. Report success only after both artifacts are in place.

Guarantees and limits
---------------------

* The input file is never opened for writing and is never replaced, even with
  ``--force``. An output target that resolves to the input file is refused.
* Existing output artifacts are refused unless ``--force`` is given.
* Publishing uses ``os.replace`` (atomic per file on POSIX). A *multi-file*
  transaction is **not** truly atomic: the two ``os.replace`` calls cannot be
  one operation. If the second publish fails, newly published files are removed
  and any pre-existing files are restored from backups; if a rollback step
  itself fails, the operation reports failure and leaves what it could not undo.
* Encoding is UTF-8 and the CSV uses the standard ``csv`` line terminator
  (``\\r\\n``); column order and record order are preserved exactly.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .application import (
    ApplicationSummary,
    DecisionApplicationResult,
    DuplicateDecisionCounts,
    apply_decisions,
)
from .corrections import propose_corrections
from .errors import InputError, OutputError
from .loader import LoadedCSV, load_records
from .remediation import (
    duplicate_pairs,
    parse_decisions,
    stable_record_ids,
    validate_decisions,
)
from .rules import scan
from .rules.config import DEFAULT_CONFIG, RuleConfig

__all__ = [
    "OUTPUT_CSV",
    "OUTPUT_AUDIT_JSON",
    "AUDIT_SCHEMA_VERSION",
    "RemediationResult",
    "build_audit_document",
    "remediate",
]

OUTPUT_CSV = "remediated.csv"
OUTPUT_AUDIT_JSON = "remediation_audit.json"
AUDIT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RemediationResult:
    """Where the artifacts landed and what the application counted."""

    csv_path: Path
    audit_path: Path
    input_path: Path
    summary: ApplicationSummary
    duplicate_counts: DuplicateDecisionCounts


def build_audit_document(
    loaded: LoadedCSV,
    application: DecisionApplicationResult,
) -> dict[str, Any]:
    """Build the stable, deterministic audit JSON document.

    No source records are embedded: only per-proposal audit entries and the
    separate duplicate-decision summary.
    """

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "input_file": str(loaded.path),
        "columns": list(loaded.columns),
        "record_count": len(loaded.records),
        "summary": application.summary.to_dict(),
        "entries": [entry.to_dict() for entry in application.audit],
        "duplicate_decisions": {
            **application.duplicate_counts.to_dict(),
            "entries": [
                {
                    "business_id_a": decision.business_id_a,
                    "business_id_b": decision.business_id_b,
                    "decision": decision.decision,
                    "rationale": decision.rationale,
                }
                for decision in application.duplicate_decisions
            ],
        },
    }


def remediate(
    input_path: str | Path,
    decisions_path: str | Path,
    out_dir: str | Path,
    *,
    force: bool = False,
    config: RuleConfig = DEFAULT_CONFIG,
) -> RemediationResult:
    """Run the full reviewed-remediation workflow and publish the artifacts.

    Raises :class:`~localbiz_scout.errors.InputError` or
    :class:`~localbiz_scout.errors.OutputError` on any failure; nothing is
    published in that case.
    """

    loaded = load_records(input_path, config.id_field)
    input_resolved = loaded.path.resolve()

    decisions = parse_decisions(_read_decisions_text(decisions_path))

    scan_result = scan(loaded.records, config)
    validate_decisions(
        decisions,
        record_ids=stable_record_ids(loaded.records, config),
        candidate_pairs=duplicate_pairs(scan_result.duplicate_candidates),
    )

    proposals = propose_corrections(loaded.records, config)
    application = apply_decisions(loaded.records, decisions, proposals, config)

    output_dir = _prepare_output_dir(out_dir)
    csv_final = output_dir / OUTPUT_CSV
    audit_final = output_dir / OUTPUT_AUDIT_JSON

    _guard_input_alias(input_resolved, (csv_final, audit_final))

    if not force:
        existing = [path for path in (csv_final, audit_final) if path.exists()]
        if existing:
            rendered = ", ".join(str(path) for path in existing)
            raise OutputError(
                f"output already exists: {rendered} (pass --force to replace it)"
            )

    document = build_audit_document(loaded, application)

    csv_temp: Path | None = None
    audit_temp: Path | None = None
    try:
        csv_temp = _write_temp_csv(output_dir, loaded.columns, application.records)
        audit_temp = _write_temp_audit(output_dir, document)

        _validate_reloaded(csv_temp, loaded, application, config)

        _publish(((csv_temp, csv_final), (audit_temp, audit_final)))
        csv_temp = None
        audit_temp = None
    finally:
        for temp_path in (csv_temp, audit_temp):
            if temp_path is not None:
                _quiet_unlink(temp_path)

    return RemediationResult(
        csv_path=csv_final,
        audit_path=audit_final,
        input_path=loaded.path,
        summary=application.summary,
        duplicate_counts=application.duplicate_counts,
    )


# --------------------------------------------------------------------------- #
# Input handling
# --------------------------------------------------------------------------- #


def _read_decisions_text(path: str | Path) -> str:
    decisions_path = Path(path)
    if not decisions_path.exists():
        raise InputError(f"decisions file not found: {decisions_path}")
    if decisions_path.is_dir():
        raise InputError(
            f"decisions path is a directory, expected a JSON file: {decisions_path}"
        )
    try:
        return decisions_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise InputError(
            f"decisions file is not valid UTF-8 text: {decisions_path} ({error})"
        ) from error
    except OSError as error:
        raise InputError(f"could not read decisions file {decisions_path}: {error}") from error


# --------------------------------------------------------------------------- #
# Destination handling
# --------------------------------------------------------------------------- #


def _prepare_output_dir(path: str | Path) -> Path:
    output_dir = Path(path)
    try:
        if output_dir.exists() and not output_dir.is_dir():
            raise OutputError(f"output path is not a directory: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
    except OutputError:
        raise
    except OSError as error:
        raise OutputError(
            f"could not create output directory {output_dir}: {error}"
        ) from error
    return output_dir


def _guard_input_alias(input_resolved: Path, targets: Sequence[Path]) -> None:
    for target in targets:
        if _same_file(input_resolved, target):
            raise OutputError(
                f"refusing to write {target}: it resolves to the input file "
                f"{input_resolved}"
            )


def _same_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return first.resolve() == second.resolve()


# --------------------------------------------------------------------------- #
# Temporary artifact writing
# --------------------------------------------------------------------------- #


def _write_temp_csv(
    output_dir: Path,
    columns: Sequence[str],
    records: Sequence[Mapping[str, Any]],
) -> Path:
    path = _mktemp(output_dir, ".remediated-", ".csv")
    try:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(columns))
            for record in records:
                writer.writerow([record.get(column, "") for column in columns])
    except OSError as error:
        _quiet_unlink(path)
        raise OutputError(f"could not write corrected CSV: {error}") from error
    return path


def _write_temp_audit(output_dir: Path, document: Mapping[str, Any]) -> Path:
    path = _mktemp(output_dir, ".remediation-audit-", ".json")
    try:
        text = json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except (OSError, TypeError, ValueError) as error:
        _quiet_unlink(path)
        raise OutputError(f"could not write audit JSON: {error}") from error
    return path


def _mktemp(output_dir: Path, prefix: str, suffix: str) -> Path:
    try:
        descriptor, name = tempfile.mkstemp(dir=output_dir, prefix=prefix, suffix=suffix)
    except OSError as error:
        raise OutputError(
            f"could not create a temporary file in {output_dir}: {error}"
        ) from error
    os.close(descriptor)
    return Path(name)


# --------------------------------------------------------------------------- #
# Reload validation
# --------------------------------------------------------------------------- #


def _validate_reloaded(
    csv_temp: Path,
    loaded: LoadedCSV,
    application: DecisionApplicationResult,
    config: RuleConfig,
) -> None:
    reloaded = load_records(csv_temp, config.id_field)

    if reloaded.columns != loaded.columns:
        raise OutputError(
            "reload validation failed: column names or order changed in the export"
        )
    if len(reloaded.records) != len(loaded.records):
        raise OutputError(
            "reload validation failed: record count changed in the export"
        )

    expected_ids = stable_record_ids(loaded.records, config)
    actual_ids = stable_record_ids(reloaded.records, config)
    if actual_ids != expected_ids:
        raise OutputError(
            "reload validation failed: record id sequence changed in the export"
        )

    applied: dict[tuple[str, str], str] = {
        (entry.business_id, entry.field): entry.proposed_value
        for entry in application.audit
        if entry.applied
    }

    for index, (before, after) in enumerate(zip(loaded.records, reloaded.records)):
        business_id = expected_ids[index]
        for column in loaded.columns:
            expected = applied.get((business_id, column), before[column])
            if after.get(column) != expected:
                raise OutputError(
                    f"reload validation failed: record {business_id!r} column "
                    f"{column!r} did not round-trip"
                )


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #


def _atomic_replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _publish(artifacts: Sequence[tuple[Path, Path]]) -> None:
    """Atomically replace each final artifact, rolling back on failure.

    A per-file ``os.replace`` is atomic, but replacing *two* files is not a
    single transaction. If publishing the second file fails, the first is
    undone and any pre-existing files are restored from backups.
    """

    published: list[tuple[Path, Path | None]] = []
    try:
        for temp_path, final_path in artifacts:
            backup: Path | None = None
            if final_path.exists():
                backup = _make_backup(final_path)
                _atomic_replace(final_path, backup)
            published.append((final_path, backup))
            _atomic_replace(temp_path, final_path)
    except OSError as error:
        _rollback(published)
        raise OutputError(
            f"could not publish export artifacts; nothing was left partially "
            f"written: {error}"
        ) from error

    for _final_path, backup in published:
        if backup is not None:
            _quiet_unlink(backup)


def _make_backup(final_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        dir=final_path.parent, prefix=f".{final_path.name}.", suffix=".bak"
    )
    os.close(descriptor)
    return Path(name)


def _rollback(published: Sequence[tuple[Path, Path | None]]) -> None:
    for final_path, backup in reversed(published):
        try:
            if final_path.exists():
                final_path.unlink()
            if backup is not None and backup.exists():
                os.replace(backup, final_path)
        except OSError:
            # Best effort: a rollback step that cannot complete leaves the
            # operation failed and the user informed.
            pass


def _quiet_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
