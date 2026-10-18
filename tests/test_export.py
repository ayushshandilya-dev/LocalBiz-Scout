"""Tests for Phase 3.3 safe export and the opt-in ``remediate`` CLI command.

Synthetic data only. All filesystem work happens under pytest's ``tmp_path``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from localbiz_scout import export
from localbiz_scout.errors import InputError, OutputError
from localbiz_scout.export import OUTPUT_AUDIT_JSON, OUTPUT_CSV, remediate

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

CSV_TEXT = (
    "business_id,name,address,city,state,postal_code,phone,website\n"
    "b1,  Acme   Coffee  ,1 Main St,Austin,tx,787011234,(512) 555-0101,"
    "https://acme.example\n"
    "b2,Beta Bakery,2 Oak Ave,Austin,TX,78702,512-555-0102,"
    "https://beta.example\n"
)

# Canonical rule id + proposed value for b1's fixable cells.
FIELDS: dict[str, tuple[str, str]] = {
    "name": ("correct_name_whitespace", "Acme Coffee"),
    "state": ("correct_state_uppercase", "TX"),
    "postal_code": ("correct_postal_zip4", "78701-1234"),
    "phone": ("correct_phone_us", "512-555-0101"),
}


def corr(field: str, decision: str, rationale: str | None = None) -> dict:
    rule_id, value = FIELDS[field]
    entry = {
        "business_id": "b1",
        "field": field,
        "rule_id": rule_id,
        "proposed_value": value,
        "decision": decision,
    }
    if rationale is not None:
        entry["rationale"] = rationale
    return entry


def decisions_text(corrections=(), duplicates=()) -> str:
    document: dict = {"version": 1}
    if corrections:
        document["corrections"] = list(corrections)
    if duplicates:
        document["duplicates"] = list(duplicates)
    return json.dumps(document)


def setup_inputs(tmp_path: Path, text: str = CSV_TEXT, decisions: str | None = None):
    source = tmp_path / "businesses.csv"
    source.write_text(text, encoding="utf-8")
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        decisions if decisions is not None else decisions_text(), encoding="utf-8"
    )
    out = tmp_path / "out"
    return source, decisions_path, out


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def leftover_temp_files(out_dir: Path) -> list[str]:
    return sorted(
        entry.name
        for entry in out_dir.iterdir()
        if entry.name.startswith(".")
        or entry.name.endswith(".bak")
        or ".tmp" in entry.name
    )


# --------------------------------------------------------------------------- #
# 1-2. Apply / preserve
# --------------------------------------------------------------------------- #


def test_export_applies_accepted_correction(tmp_path):
    source, decisions_path, out = setup_inputs(
        tmp_path, decisions=decisions_text([corr("name", "accepted")])
    )

    result = remediate(source, decisions_path, out)

    assert result.csv_path == out / OUTPUT_CSV
    assert (out / OUTPUT_CSV).is_file()
    assert (out / OUTPUT_AUDIT_JSON).is_file()

    rows = {row["business_id"]: row for row in read_rows(out / OUTPUT_CSV)}
    assert rows["b1"]["name"] == "Acme Coffee"
    assert rows["b1"]["state"] == "tx"  # not decided
    assert rows["b2"]["name"] == "Beta Bakery"

    audit = json.loads((out / OUTPUT_AUDIT_JSON).read_text(encoding="utf-8"))
    assert audit["summary"]["total_proposals"] == 4
    assert audit["summary"]["accepted"] == 1
    assert audit["summary"]["applied"] == 1
    applied = [e for e in audit["entries"] if e["applied"]]
    assert applied == [
        {
            "business_id": "b1",
            "field": "name",
            "rule_id": "correct_name_whitespace",
            "original_value": "  Acme   Coffee  ",
            "proposed_value": "Acme Coffee",
            "decision": "accepted",
            "applied": True,
            "rationale": applied[0]["rationale"],
            "decision_rationale": None,
        }
    ]


def test_rejected_deferred_and_undecided_preserve_original(tmp_path):
    source, decisions_path, out = setup_inputs(
        tmp_path,
        decisions=decisions_text(
            [
                corr("name", "rejected"),
                corr("state", "deferred"),
                # postal_code and phone: no decision -> undecided
            ]
        ),
    )

    remediate(source, decisions_path, out)

    row = next(r for r in read_rows(out / OUTPUT_CSV) if r["business_id"] == "b1")
    assert row["name"] == "  Acme   Coffee  "
    assert row["state"] == "tx"
    assert row["postal_code"] == "787011234"
    assert row["phone"] == "(512) 555-0101"

    audit = json.loads((out / OUTPUT_AUDIT_JSON).read_text(encoding="utf-8"))
    assert audit["summary"] == {
        "total_proposals": 4,
        "accepted": 0,
        "rejected": 1,
        "deferred": 1,
        "not_decided": 2,
        "applied": 0,
        "unapplied": 4,
    }


# --------------------------------------------------------------------------- #
# 3. Input protection
# --------------------------------------------------------------------------- #


def test_input_file_remains_byte_identical(tmp_path):
    source, decisions_path, out = setup_inputs(
        tmp_path, decisions=decisions_text([corr("name", "accepted")])
    )
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    remediate(source, decisions_path, out)

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_output_artifact_resolving_to_input_is_rejected(tmp_path):
    io_dir = tmp_path / "io"
    io_dir.mkdir()
    source = io_dir / OUTPUT_CSV  # the export target would overwrite the input
    source.write_text(CSV_TEXT, encoding="utf-8")
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(decisions_text(), encoding="utf-8")

    with pytest.raises(OutputError, match="resolves to the input file"):
        remediate(source, decisions_path, io_dir)


def test_symlinked_output_artifact_to_input_is_rejected(tmp_path):
    source, decisions_path, out = setup_inputs(tmp_path)
    out.mkdir()
    # A symlink to the input sits exactly where the CSV would be published.
    os.symlink(source, out / OUTPUT_CSV)

    with pytest.raises(OutputError, match="resolves to the input file"):
        remediate(source, decisions_path, out)


def test_same_file_falls_back_to_resolved_paths(tmp_path):
    # Neither path exists, so os.path.samefile raises and the resolved-path
    # comparison decides.
    first = tmp_path / "missing" / ".." / "thing.csv"
    second = tmp_path / "thing.csv"
    assert export._same_file(first, second) is True


# --------------------------------------------------------------------------- #
# 6-7. Overwrite behavior
# --------------------------------------------------------------------------- #


def test_existing_output_refused_without_force(tmp_path):
    source, decisions_path, out = setup_inputs(tmp_path)
    remediate(source, decisions_path, out)

    with pytest.raises(OutputError, match="already exists"):
        remediate(source, decisions_path, out)


def test_force_replaces_artifacts_but_not_input(tmp_path):
    source, decisions_path, out = setup_inputs(tmp_path)
    remediate(source, decisions_path, out)

    # Diverge the inputs, then force a second export.
    decisions_path.write_text(
        decisions_text([corr("name", "accepted"), corr("state", "accepted")]),
        encoding="utf-8",
    )
    input_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    remediate(source, decisions_path, out, force=True)

    row = next(r for r in read_rows(out / OUTPUT_CSV) if r["business_id"] == "b1")
    assert row["name"] == "Acme Coffee"
    assert row["state"] == "TX"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == input_hash


# --------------------------------------------------------------------------- #
# 8-13. Failure handling
# --------------------------------------------------------------------------- #


def test_invalid_input_causes_no_output(tmp_path):
    out = tmp_path / "out"
    source = tmp_path / "bad.csv"
    source.write_text("name,city\nAcme,Austin\n", encoding="utf-8")  # no business_id
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(decisions_text(), encoding="utf-8")

    with pytest.raises(InputError):
        remediate(source, decisions_path, out)
    assert not (out / OUTPUT_CSV).exists()


def test_invalid_decisions_causes_no_output(tmp_path):
    source, _decisions_path, out = setup_inputs(tmp_path)
    bad = tmp_path / "bad_decisions.json"
    bad.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(InputError):
        remediate(source, bad, out)
    assert not (out / OUTPUT_CSV).exists()


def test_reload_validation_failure_prevents_publication(tmp_path, monkeypatch):
    source, decisions_path, out = setup_inputs(tmp_path)

    def truncated_csv(output_dir, columns, records):
        path = export._mktemp(output_dir, ".remediated-", ".csv")
        with open(path, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(list(columns))  # header only: a row is gone
        return path

    monkeypatch.setattr(export, "_write_temp_csv", truncated_csv)

    with pytest.raises(OutputError, match="reload validation failed"):
        remediate(source, decisions_path, out)

    assert not (out / OUTPUT_CSV).exists()
    assert not (out / OUTPUT_AUDIT_JSON).exists()
    assert leftover_temp_files(out) == []


def test_simulated_csv_write_failure_leaves_no_output(tmp_path, monkeypatch):
    source, decisions_path, out = setup_inputs(tmp_path)

    def boom(*args, **kwargs):
        raise OutputError("simulated CSV write failure")

    monkeypatch.setattr(export, "_write_temp_csv", boom)

    with pytest.raises(OutputError, match="simulated CSV write failure"):
        remediate(source, decisions_path, out)
    assert not (out / OUTPUT_CSV).exists()


def test_simulated_audit_write_failure_cleans_temp_and_leaves_no_output(
    tmp_path, monkeypatch
):
    source, decisions_path, out = setup_inputs(tmp_path)

    def boom(*args, **kwargs):
        raise OutputError("simulated audit write failure")

    monkeypatch.setattr(export, "_write_temp_audit", boom)

    with pytest.raises(OutputError, match="simulated audit write failure"):
        remediate(source, decisions_path, out)
    assert not (out / OUTPUT_CSV).exists()
    assert leftover_temp_files(out) == []


def test_publish_failure_rolls_back_and_preserves_existing(tmp_path, monkeypatch):
    source, decisions_path, out = setup_inputs(
        tmp_path, decisions=decisions_text([corr("name", "accepted")])
    )
    out.mkdir()
    csv_final = out / OUTPUT_CSV
    audit_final = out / OUTPUT_AUDIT_JSON
    csv_final.write_text("OLD-CSV", encoding="utf-8")
    audit_final.write_text("OLD-AUDIT", encoding="utf-8")

    real_replace = os.replace

    def failing_replace(src, dst):
        if Path(dst) == audit_final:
            raise OSError("simulated publish failure")
        real_replace(src, dst)

    monkeypatch.setattr(export, "_atomic_replace", failing_replace)

    with pytest.raises(OutputError, match="could not publish"):
        remediate(source, decisions_path, out, force=True)

    # Both pre-existing files are restored; no partial second artifact remains.
    assert csv_final.read_text(encoding="utf-8") == "OLD-CSV"
    assert audit_final.read_text(encoding="utf-8") == "OLD-AUDIT"
    assert leftover_temp_files(out) == []


def test_output_path_that_is_a_file_is_rejected(tmp_path):
    source, decisions_path, _out = setup_inputs(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")

    with pytest.raises(OutputError, match="not a directory"):
        remediate(source, decisions_path, blocker)


def test_output_path_under_a_file_is_rejected(tmp_path):
    source, decisions_path, _out = setup_inputs(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")

    with pytest.raises(OutputError):
        remediate(source, decisions_path, blocker / "sub")


# --------------------------------------------------------------------------- #
# 14-15. Structure preservation
# --------------------------------------------------------------------------- #


def test_column_and_record_order_are_preserved(tmp_path):
    source, decisions_path, out = setup_inputs(
        tmp_path, decisions=decisions_text([corr("name", "accepted")])
    )
    original_header = read_header(source)
    original_ids = [row["business_id"] for row in read_rows(source)]

    remediate(source, decisions_path, out)

    assert read_header(out / OUTPUT_CSV) == original_header
    assert [row["business_id"] for row in read_rows(out / OUTPUT_CSV)] == original_ids
    assert len(read_rows(out / OUTPUT_CSV)) == len(original_ids)


def test_non_allowlisted_fields_are_unchanged(tmp_path):
    source, decisions_path, out = setup_inputs(
        tmp_path, decisions=decisions_text([corr("name", "accepted")])
    )
    before = {r["business_id"]: r for r in read_rows(source)}

    remediate(source, decisions_path, out)

    after = {r["business_id"]: r for r in read_rows(out / OUTPUT_CSV)}
    for business_id in ("b1", "b2"):
        for column in ("business_id", "address", "city", "website"):
            assert after[business_id][column] == before[business_id][column]


def test_unedited_export_preserves_embedded_crlf_byte_identical(tmp_path):
    # A quoted cell with an embedded CRLF must survive an unedited export
    # byte-for-byte: no approved correction may change any value, and an
    # unapproved cell must not be silently rewritten to LF.
    source = tmp_path / "businesses.csv"
    source.write_bytes(b'business_id,name\r\nb1,"line1\r\nline2"\r\n')
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(decisions_text(), encoding="utf-8")
    out = tmp_path / "out"
    before = source.read_bytes()

    remediate(source, decisions_path, out)

    assert (out / OUTPUT_CSV).read_bytes() == before
    with (out / OUTPUT_CSV).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"business_id": "b1", "name": "line1\r\nline2"}]


# --------------------------------------------------------------------------- #
# 17. Determinism
# --------------------------------------------------------------------------- #


def test_repeated_runs_produce_identical_audit(tmp_path):
    source, decisions_path, out_a = setup_inputs(
        tmp_path, decisions=decisions_text([corr("name", "accepted")])
    )
    out_b = tmp_path / "out_b"

    remediate(source, decisions_path, out_a)
    remediate(source, decisions_path, out_b)

    assert (out_a / OUTPUT_AUDIT_JSON).read_bytes() == (
        out_b / OUTPUT_AUDIT_JSON
    ).read_bytes()
    assert (out_a / OUTPUT_CSV).read_bytes() == (out_b / OUTPUT_CSV).read_bytes()


# --------------------------------------------------------------------------- #
# CLI integration
# --------------------------------------------------------------------------- #


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
    return subprocess.run(
        [sys.executable, "-m", "localbiz_scout", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_remediate_success(tmp_path):
    source, decisions_path, out = setup_inputs(
        tmp_path, decisions=decisions_text([corr("name", "accepted")])
    )

    result = run_cli(
        "remediate",
        "--input",
        str(source),
        "--decisions",
        str(decisions_path),
        "--out",
        str(out),
    )

    assert result.returncode == 0, result.stderr
    assert (out / OUTPUT_CSV).is_file()
    assert (out / OUTPUT_AUDIT_JSON).is_file()
    assert "accepted 1" in result.stdout
    assert "Wrote" in result.stdout
    # Never prints business-record contents.
    assert "Acme Coffee" not in result.stdout


def test_cli_remediate_invalid_decisions_fails(tmp_path):
    source, _decisions_path, out = setup_inputs(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 2}', encoding="utf-8")

    result = run_cli(
        "remediate", "--input", str(source), "--decisions", str(bad), "--out", str(out)
    )

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert not (out / OUTPUT_CSV).exists()
    assert not (out / OUTPUT_AUDIT_JSON).exists()


def test_cli_analyze_behavior_unchanged(tmp_path):
    out = tmp_path / "analyze-out"
    result = run_cli(
        "analyze", "--input", str(REPO_ROOT / "data" / "sample_businesses.csv"),
        "--out", str(out),
    )
    assert result.returncode == 0, result.stderr
    for name in ("findings.json", "report.md", "duplicates_review.csv"):
        assert (out / name).is_file()


def test_cli_web_help_unchanged():
    result = run_cli("web", "--help")
    assert result.returncode == 0
    assert "--host" in result.stdout
    assert "--port" in result.stdout


def test_cli_duplicate_decisions_are_reported_not_merged(tmp_path):
    duplicate_csv = (
        "business_id,name,address,city,state,postal_code,phone\n"
        "b1,Acme Coffee,1 Main St,Austin,TX,78701,512-555-0101\n"
        "b2,Acme Coffee,1 Main St,Austin,TX,78701,512-555-0101\n"
    )
    source, decisions_path, out = setup_inputs(
        tmp_path,
        text=duplicate_csv,
        decisions=decisions_text(
            duplicates=[
                {"business_id_a": "b1", "business_id_b": "b2", "decision": "confirmed"}
            ]
        ),
    )

    result = run_cli(
        "remediate",
        "--input",
        str(source),
        "--decisions",
        str(decisions_path),
        "--out",
        str(out),
    )

    assert result.returncode == 0, result.stderr
    assert "confirmed 1" in result.stdout
    # A confirmed duplicate never merges: both rows are still present.
    rows = read_rows(out / OUTPUT_CSV)
    assert [r["business_id"] for r in rows] == ["b1", "b2"]
    audit = json.loads((out / OUTPUT_AUDIT_JSON).read_text(encoding="utf-8"))
    assert audit["duplicate_decisions"]["confirmed"] == 1
    assert audit["duplicate_decisions"]["total"] == 1


def test_cli_force_cannot_bypass_input_protection(tmp_path):
    io_dir = tmp_path / "io"
    io_dir.mkdir()
    source = io_dir / OUTPUT_CSV
    source.write_text(CSV_TEXT, encoding="utf-8")
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(decisions_text(), encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    result = run_cli(
        "remediate",
        "--input",
        str(source),
        "--decisions",
        str(decisions_path),
        "--out",
        str(io_dir),
        "--force",
    )

    assert result.returncode == 2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
