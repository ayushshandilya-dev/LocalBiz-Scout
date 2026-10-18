"""Tests for the Phase 4 read-only review scaffolding.

Synthetic data only. Every filesystem artifact is written under pytest's
``tmp_path``; the checked-in sample data and reference outputs are never
touched.
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

from localbiz_scout import review
from localbiz_scout.corrections import propose_corrections
from localbiz_scout.errors import InputError, OutputError
from localbiz_scout.export import OUTPUT_AUDIT_JSON, OUTPUT_CSV, remediate
from localbiz_scout.loader import load_records
from localbiz_scout.remediation import (
    duplicate_pairs,
    load_decisions,
    parse_decisions,
    stable_record_ids,
    validate_decisions,
)
from localbiz_scout.review import (
    OUTPUT_CORRECTIONS_REVIEW,
    OUTPUT_DECISIONS_TEMPLATE,
    REVIEW_COLUMNS,
    build_decisions_template,
    proposal_review_rows,
    write_review,
)
from localbiz_scout.rules import scan
from localbiz_scout.rules.config import DEFAULT_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

# b1 has one fixable cell per allowlisted field; b2 is a normalized duplicate of
# b1 (its state and postal_code are also fixable).
CSV_TEXT = (
    "business_id,name,address,city,state,postal_code,phone\n"
    "b1,  Acme   Coffee  ,1 Main St,Austin,tx,787011234,(512) 555-0101\n"
    "b2,Acme Coffee,1 Main St,Austin,tx,787011234,512-555-0101\n"
)

NO_ISSUES_CSV = "business_id,name\nb1,Acme\nb2,Beta\n"


def setup(tmp_path: Path, text: str = CSV_TEXT) -> tuple[Path, Path]:
    # The exporter writes canonical CRLF line endings, so write sources that way
    # too; byte-identity checks then hold for an unapproved run.
    normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
    source = tmp_path / "businesses.csv"
    source.write_bytes(normalized.encode("utf-8"))
    return source, tmp_path / "review-out"


def prepared(source: Path):
    config = DEFAULT_CONFIG
    loaded = load_records(source, config.id_field)
    result = scan(loaded.records, config)
    proposals = propose_corrections(loaded.records, config)
    return config, loaded, result, proposals


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


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


# --------------------------------------------------------------------------- #
# 1. Template correctness and usability
# --------------------------------------------------------------------------- #


def test_template_parses_and_validates(tmp_path):
    source, out = setup(tmp_path)
    config, loaded, result, proposals = prepared(source)

    write_review(loaded, result, proposals, out)
    text = (out / OUTPUT_DECISIONS_TEMPLATE).read_text(encoding="utf-8")

    decisions = load_decisions(
        text,
        record_ids=stable_record_ids(loaded.records, config),
        candidate_pairs=duplicate_pairs(result.duplicate_candidates),
    )
    # Also exercise parse/validate separately.
    parsed = parse_decisions(text)
    validate_decisions(
        parsed,
        record_ids=stable_record_ids(loaded.records, config),
        candidate_pairs=duplicate_pairs(result.duplicate_candidates),
    )
    assert decisions.version == 1


def test_every_proposal_and_candidate_represented_once(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)

    write_review(loaded, result, proposals, out)
    document = json.loads((out / OUTPUT_DECISIONS_TEMPLATE).read_text(encoding="utf-8"))

    proposal_keys = {
        (p.business_id, p.field, p.rule_id, p.proposed_value) for p in proposals
    }
    template_keys = {
        (c["business_id"], c["field"], c["rule_id"], c["proposed_value"])
        for c in document["corrections"]
    }
    assert template_keys == proposal_keys
    assert len(document["corrections"]) == len(proposals)

    expected_pairs = set(duplicate_pairs(result.duplicate_candidates))
    template_pairs = {
        (d["business_id_a"], d["business_id_b"]) for d in document["duplicates"]
    }
    assert template_pairs == expected_pairs
    assert len(document["duplicates"]) == len(result.duplicate_candidates)


def test_all_decisions_default_to_deferred(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)

    write_review(loaded, result, proposals, out)
    document = json.loads((out / OUTPUT_DECISIONS_TEMPLATE).read_text(encoding="utf-8"))

    assert document["corrections"], "fixture should produce proposals"
    assert document["duplicates"], "fixture should produce candidates"
    assert all(c["decision"] == "deferred" for c in document["corrections"])
    assert all(d["decision"] == "deferred" for d in document["duplicates"])


def test_builders_are_pure_and_ordered(tmp_path):
    source, _out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)

    document = build_decisions_template(result.duplicate_candidates, proposals)
    assert [c["business_id"] for c in document["corrections"]] == [
        p.business_id for p in proposals
    ]
    rows = proposal_review_rows(proposals)
    assert [r["proposed_value"] for r in rows] == [
        p.proposed_value for p in proposals
    ]


def test_empty_proposals_and_candidates_still_write_valid_template(tmp_path):
    source, out = setup(tmp_path, text=NO_ISSUES_CSV)
    config, loaded, result, proposals = prepared(source)
    assert proposals == ()
    assert result.duplicate_candidates == ()

    write_review(loaded, result, proposals, out)
    document = json.loads((out / OUTPUT_DECISIONS_TEMPLATE).read_text(encoding="utf-8"))
    assert document == {"version": 1, "corrections": [], "duplicates": []}
    parsed = parse_decisions((out / OUTPUT_DECISIONS_TEMPLATE).read_text(encoding="utf-8"))
    assert parsed.corrections == () and parsed.duplicates == ()
    assert read_header(out / OUTPUT_CORRECTIONS_REVIEW) == list(REVIEW_COLUMNS)
    assert read_rows(out / OUTPUT_CORRECTIONS_REVIEW) == []


# --------------------------------------------------------------------------- #
# 2. Safe no-op and edited-approval behaviour
# --------------------------------------------------------------------------- #


def test_unedited_template_applies_nothing_and_output_is_byte_identical(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    write_review(loaded, result, proposals, out)

    before = hashlib.sha256(source.read_bytes()).hexdigest()
    out2 = tmp_path / "remediated"
    result_obj = remediate(source, out / OUTPUT_DECISIONS_TEMPLATE, out2)

    assert result_obj.summary.applied == 0
    assert result_obj.summary.accepted == 0
    assert result_obj.summary.deferred == len(proposals)  # deferred, never applied
    assert (out2 / OUTPUT_CSV).read_bytes() == source.read_bytes()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before

    audit = json.loads((out2 / OUTPUT_AUDIT_JSON).read_text(encoding="utf-8"))
    assert audit["summary"]["total_proposals"] == len(proposals)
    assert audit["summary"]["applied"] == 0


def test_edited_template_applies_only_accepted_cell(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    write_review(loaded, result, proposals, out)

    document = json.loads((out / OUTPUT_DECISIONS_TEMPLATE).read_text(encoding="utf-8"))
    name_proposal = next(
        c for c in document["corrections"] if c["business_id"] == "b1" and c["field"] == "name"
    )
    name_proposal["decision"] = "accepted"
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(document), encoding="utf-8")

    out2 = tmp_path / "remediated"
    remediate(source, edited, out2)

    rows = {r["business_id"]: r for r in read_rows(out2 / OUTPUT_CSV)}
    assert rows["b1"]["name"] == "Acme Coffee"      # approved cell changed
    assert rows["b1"]["state"] == "tx"              # still deferred
    assert rows["b1"]["postal_code"] == "787011234"  # still deferred
    assert rows["b2"]["name"] == "Acme Coffee"      # other records untouched
    # Only one cell differs from the source when parsed as rows.
    original = {r["business_id"]: r for r in read_rows(source)}
    for bid, row in rows.items():
        for column, value in row.items():
            if bid == "b1" and column == "name":
                assert value != original[bid][column]
            else:
                assert value == original[bid][column]


# --------------------------------------------------------------------------- #
# 3. Review CSV fidelity, determinism, input safety
# --------------------------------------------------------------------------- #


def test_review_csv_matches_proposals(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    write_review(loaded, result, proposals, out)

    assert read_header(out / OUTPUT_CORRECTIONS_REVIEW) == list(REVIEW_COLUMNS)
    rows = read_rows(out / OUTPUT_CORRECTIONS_REVIEW)
    assert len(rows) == len(proposals)
    for row, proposal in zip(rows, proposals):
        assert row == {
            "business_id": proposal.business_id,
            "field": proposal.field,
            "rule_id": proposal.rule_id,
            "original_value": proposal.original_value,
            "proposed_value": proposal.proposed_value,
            "rationale": proposal.rationale,
        }


def test_csv_quoting_is_correct(tmp_path):
    text = (
        "business_id,name\n"
        'b1,"Acme,  Coffee  "\n'
    )
    source, out = setup(tmp_path, text=text)
    _, loaded, result, proposals = prepared(source)
    assert proposals, "whitespace fix should be proposed"
    write_review(loaded, result, proposals, out)

    # The embedded comma must round-trip through a real CSV reader (the loader
    # does not trim values, only header cells).
    rows = read_rows(out / OUTPUT_CORRECTIONS_REVIEW)
    assert rows[0]["original_value"] == "Acme,  Coffee  "


def test_two_runs_are_byte_identical(tmp_path):
    source, out_a = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    out_b = tmp_path / "review-out-b"

    write_review(loaded, result, proposals, out_a)
    write_review(loaded, result, proposals, out_b)

    assert (out_a / OUTPUT_DECISIONS_TEMPLATE).read_bytes() == (
        out_b / OUTPUT_DECISIONS_TEMPLATE
    ).read_bytes()
    assert (out_a / OUTPUT_CORRECTIONS_REVIEW).read_bytes() == (
        out_b / OUTPUT_CORRECTIONS_REVIEW
    ).read_bytes()


def test_input_csv_remains_byte_identical(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    write_review(loaded, result, proposals, out)

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


# --------------------------------------------------------------------------- #
# 3b. Duplicate record identifiers cannot yield a valid unique template
# --------------------------------------------------------------------------- #


DUPLICATE_ID_CSV = (
    "business_id,name,state\n"
    "D1,  Alpha  ,tx\n"
    "D1,  Alpha  ,TX\n"
    "D1,Beta,ca\n"
)


def test_duplicate_record_ids_fail_closed(tmp_path):
    source, out = setup(tmp_path, text=DUPLICATE_ID_CSV)
    _, loaded, result, proposals = prepared(source)
    assert len(proposals) >= 2, "fixture should produce colliding proposals"

    with pytest.raises(InputError):
        build_decisions_template(result.duplicate_candidates, proposals)

    # No output directory is created when building the template fails.
    with pytest.raises(InputError):
        write_review(loaded, result, proposals, out)
    assert not out.exists()


def test_cli_review_duplicate_ids_errors_without_output(tmp_path):
    source, out = setup(tmp_path, text=DUPLICATE_ID_CSV)
    result = run_cli("review", "--input", str(source), "--out", str(out))

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert not out.exists()


# --------------------------------------------------------------------------- #
# 4. Input-alias guard (existing name, symlinked file, resolved directory)
# --------------------------------------------------------------------------- #


def test_guard_rejects_input_named_like_output(tmp_path):
    io_dir = tmp_path / "io"
    io_dir.mkdir()
    source = io_dir / OUTPUT_DECISIONS_TEMPLATE
    source.write_text(CSV_TEXT, encoding="utf-8")
    config, loaded, result, proposals = prepared(source)

    with pytest.raises(OutputError):
        write_review(loaded, result, proposals, io_dir)


def test_guard_rejects_symlinked_output_to_input(tmp_path):
    source = tmp_path / "businesses.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    config, loaded, result, proposals = prepared(source)

    out = tmp_path / "out"
    out.mkdir()
    os.symlink(source, out / OUTPUT_DECISIONS_TEMPLATE)

    with pytest.raises(OutputError):
        write_review(loaded, result, proposals, out)


def test_guard_rejects_resolved_directory_alias(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    source = real / OUTPUT_CORRECTIONS_REVIEW
    source.write_text(CSV_TEXT, encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(real, link)
    config, loaded, result, proposals = prepared(source)

    with pytest.raises(OutputError):
        write_review(loaded, result, proposals, link)


# --------------------------------------------------------------------------- #
# 4b. Safe overwrite protection (review --force)
# --------------------------------------------------------------------------- #


def test_review_refuses_existing_artifacts_without_force(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    write_review(loaded, result, proposals, out)
    before = (out / OUTPUT_DECISIONS_TEMPLATE).read_bytes()

    with pytest.raises(OutputError, match="already exists"):
        write_review(loaded, result, proposals, out)
    assert (out / OUTPUT_DECISIONS_TEMPLATE).read_bytes() == before


def test_review_partial_conflict_writes_nothing(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    out.mkdir()
    sentinel = out / OUTPUT_DECISIONS_TEMPLATE
    sentinel.write_text("SENTINEL", encoding="utf-8")

    with pytest.raises(OutputError, match="already exists"):
        write_review(loaded, result, proposals, out)

    assert sentinel.read_text(encoding="utf-8") == "SENTINEL"
    assert not (out / OUTPUT_CORRECTIONS_REVIEW).exists()


def test_review_force_replaces_existing_artifacts(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    out.mkdir()
    (out / OUTPUT_DECISIONS_TEMPLATE).write_text("OLD", encoding="utf-8")
    (out / OUTPUT_CORRECTIONS_REVIEW).write_text("OLD", encoding="utf-8")

    write_review(loaded, result, proposals, out, force=True)

    document = json.loads(
        (out / OUTPUT_DECISIONS_TEMPLATE).read_text(encoding="utf-8")
    )
    assert document["version"] == 1
    assert read_header(out / OUTPUT_CORRECTIONS_REVIEW) == list(REVIEW_COLUMNS)
    # No temporary or backup files are left behind after a forced publish.
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [OUTPUT_DECISIONS_TEMPLATE, OUTPUT_CORRECTIONS_REVIEW]
    )


def test_review_force_cannot_bypass_input_alias(tmp_path):
    io_dir = tmp_path / "io"
    io_dir.mkdir()
    source = io_dir / OUTPUT_DECISIONS_TEMPLATE
    source.write_text(CSV_TEXT, encoding="utf-8")
    config, loaded, result, proposals = prepared(source)

    with pytest.raises(OutputError):
        write_review(loaded, result, proposals, io_dir, force=True)


def test_review_refusal_preserves_edited_template(tmp_path):
    source, out = setup(tmp_path)
    _, loaded, result, proposals = prepared(source)
    write_review(loaded, result, proposals, out)

    template = out / OUTPUT_DECISIONS_TEMPLATE
    document = json.loads(template.read_text(encoding="utf-8"))
    document["corrections"][0]["decision"] = "accepted"
    edited_text = json.dumps(document)
    template.write_text(edited_text, encoding="utf-8")

    with pytest.raises(OutputError, match="already exists"):
        write_review(loaded, result, proposals, out)
    assert template.read_text(encoding="utf-8") == edited_text


# --------------------------------------------------------------------------- #
# 5. CLI behaviour and error handling
# --------------------------------------------------------------------------- #


def test_cli_review_success(tmp_path):
    source, out = setup(tmp_path)
    result = run_cli("review", "--input", str(source), "--out", str(out))

    assert result.returncode == 0, result.stderr
    assert (out / OUTPUT_DECISIONS_TEMPLATE).is_file()
    assert (out / OUTPUT_CORRECTIONS_REVIEW).is_file()
    assert "Correction proposals:" in result.stdout
    assert "Wrote" in result.stdout


def test_cli_review_does_not_leak_record_contents(tmp_path):
    text = "business_id,name\nb1,ZzyzxSecretBusinessName\n"
    source, out = setup(tmp_path, text=text)
    result = run_cli("review", "--input", str(source), "--out", str(out))

    assert result.returncode == 0, result.stderr
    assert "ZzyzxSecretBusinessName" not in result.stdout
    assert "ZzyzxSecretBusinessName" not in result.stderr


def test_cli_review_existing_fails_then_force_succeeds(tmp_path):
    source, out = setup(tmp_path)
    first = run_cli("review", "--input", str(source), "--out", str(out))
    assert first.returncode == 0, first.stderr

    second = run_cli("review", "--input", str(source), "--out", str(out))
    assert second.returncode == 2
    assert "already exists" in second.stderr
    assert "Traceback" not in second.stderr

    third = run_cli("review", "--input", str(source), "--out", str(out), "--force")
    assert third.returncode == 0, third.stderr


def test_cli_review_missing_args_errors(tmp_path):
    source, out = setup(tmp_path)
    for args in (
        ("review", "--out", str(out)),
        ("review", "--input", str(source)),
    ):
        result = run_cli(*args)
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert not (out / OUTPUT_DECISIONS_TEMPLATE).exists()


def test_cli_review_malformed_csv_errors_without_output(tmp_path):
    source, out = setup(tmp_path, text="business_id,name\nb1,Acme,extra\n")
    result = run_cli("review", "--input", str(source), "--out", str(out))

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert not out.exists()


def test_cli_review_missing_input_file_errors(tmp_path):
    out = tmp_path / "out"
    result = run_cli(
        "review", "--input", str(tmp_path / "nope.csv"), "--out", str(out)
    )
    assert result.returncode == 2
    assert not out.exists()


# --------------------------------------------------------------------------- #
# 6. Additive: existing commands and contracts unchanged
# --------------------------------------------------------------------------- #


def test_analyze_output_contract_unchanged(tmp_path):
    out = tmp_path / "analyze-out"
    result = run_cli(
        "analyze", "--input", str(REPO_ROOT / "data" / "sample_businesses.csv"),
        "--out", str(out),
    )
    assert result.returncode == 0, result.stderr
    produced = sorted(p.name for p in out.iterdir())
    assert produced == ["duplicates_review.csv", "findings.json", "report.md"]


def test_review_module_exports_are_stable():
    assert review.OUTPUT_DECISIONS_TEMPLATE == "decisions_template.json"
    assert review.OUTPUT_CORRECTIONS_REVIEW == "corrections_review.csv"
    assert review.REVIEW_COLUMNS == (
        "business_id",
        "field",
        "rule_id",
        "original_value",
        "proposed_value",
        "rationale",
    )
