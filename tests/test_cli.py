"""End-to-end tests for the command-line interface.

The CLI is invoked as a subprocess so that argument parsing, exit codes, and
the ``python3 -m localbiz_scout`` entry point are all exercised exactly as a
user would run them. All outputs go to pytest's temporary directories.
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

import localbiz_scout.cli as cli
from localbiz_scout.ai.client import AIUnavailableError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SAMPLE = REPO_ROOT / "data" / "sample_businesses.csv"

EXPECTED_OUTPUTS = ("findings.json", "report.md", "duplicates_review.csv")
DUPLICATE_COLUMNS = [
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


def write_csv(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Help / argument parsing
# --------------------------------------------------------------------------- #


def test_top_level_help():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "analyze" in result.stdout
    assert "usage" in result.stdout.lower()


def test_analyze_help():
    result = run_cli("analyze", "--help")
    assert result.returncode == 0
    assert "--input" in result.stdout
    assert "--out" in result.stdout
    assert "--ai" in result.stdout


def test_version():
    result = run_cli("--version")
    assert result.returncode == 0
    assert "localbiz_scout" in result.stdout


def test_no_command_shows_help_and_fails():
    result = run_cli()
    assert result.returncode == 2
    assert "analyze" in result.stderr


def test_analyze_requires_input_and_out():
    result = run_cli("analyze")
    assert result.returncode == 2
    assert "--input" in result.stderr or "--out" in result.stderr


# --------------------------------------------------------------------------- #
# Successful end-to-end run
# --------------------------------------------------------------------------- #


def test_end_to_end_against_sample(tmp_path):
    out = tmp_path / "run"
    result = run_cli("analyze", "--input", str(SAMPLE), "--out", str(out))

    assert result.returncode == 0, result.stderr
    for name in EXPECTED_OUTPUTS:
        assert (out / name).is_file(), f"missing {name}"

    document = read_json(out / "findings.json")
    assert set(document) == {"metadata", "findings", "duplicate_candidates"}
    assert document["metadata"]["record_count"] == 17
    assert isinstance(document["findings"], list)
    assert isinstance(document["duplicate_candidates"], list)
    assert document["metadata"]["finding_count"] == len(document["findings"])

    with (out / "duplicates_review.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == DUPLICATE_COLUMNS
        rows = list(reader)
    assert len(rows) == document["metadata"]["duplicate_candidate_count"]

    report = (out / "report.md").read_text(encoding="utf-8")
    assert "# LocalBiz Scout report" in report
    assert "Findings by rule" in report
    assert "Findings by severity" in report
    assert "Duplicate candidates" in report
    assert "## Limitations" in report


def test_output_directory_is_created_when_nested(tmp_path):
    out = tmp_path / "a" / "b" / "c"
    result = run_cli("analyze", "--input", str(SAMPLE), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert out.is_dir()
    for name in EXPECTED_OUTPUTS:
        assert (out / name).is_file()


def test_input_file_is_not_modified(tmp_path):
    before = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    result = run_cli("analyze", "--input", str(SAMPLE), "--out", str(tmp_path / "o"))
    assert result.returncode == 0, result.stderr
    assert hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == before


# --------------------------------------------------------------------------- #
# Empty / minimal inputs
# --------------------------------------------------------------------------- #


def test_header_only_input_produces_zero_findings(tmp_path):
    source = write_csv(tmp_path, "header.csv", "business_id,name\n")
    out = tmp_path / "out"
    result = run_cli("analyze", "--input", str(source), "--out", str(out))

    assert result.returncode == 0, result.stderr
    document = read_json(out / "findings.json")
    assert document["metadata"]["record_count"] == 0
    assert document["findings"] == []
    assert document["duplicate_candidates"] == []

    report = (out / "report.md").read_text(encoding="utf-8")
    assert "No findings were produced." in report
    assert "No duplicate candidates were found." in report


def test_clean_input_produces_zero_findings(tmp_path):
    source = write_csv(
        tmp_path,
        "clean.csv",
        "business_id,name,address,city,state,postal_code,phone\n"
        "b1,Acme Coffee,1 Main Street,Austin,TX,78701,512-555-0100\n",
    )
    out = tmp_path / "out"
    result = run_cli("analyze", "--input", str(source), "--out", str(out))
    assert result.returncode == 0, result.stderr

    document = read_json(out / "findings.json")
    assert document["metadata"]["finding_count"] == 0
    assert document["duplicate_candidates"] == []


def test_missing_optional_columns_are_reported(tmp_path):
    source = write_csv(tmp_path, "minimal.csv", "business_id,name\nb1,Acme Coffee\n")
    out = tmp_path / "out"
    result = run_cli("analyze", "--input", str(source), "--out", str(out))
    assert result.returncode == 0, result.stderr

    document = read_json(out / "findings.json")
    missing = document["metadata"]["missing_optional_fields"]
    assert "address" in missing
    assert "business_id" not in missing

    report = (out / "report.md").read_text(encoding="utf-8")
    assert "Missing optional" in report


# --------------------------------------------------------------------------- #
# Invalid input
# --------------------------------------------------------------------------- #


def test_empty_file_is_an_actionable_error(tmp_path):
    source = write_csv(tmp_path, "empty.csv", "")
    result = run_cli("analyze", "--input", str(source), "--out", str(tmp_path / "out"))
    assert result.returncode == 2
    assert "empty" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_missing_required_column_is_an_actionable_error(tmp_path):
    source = write_csv(tmp_path, "no_id.csv", "name,city\nAcme,Austin\n")
    result = run_cli("analyze", "--input", str(source), "--out", str(tmp_path / "out"))
    assert result.returncode == 2
    assert "business_id" in result.stderr


def test_malformed_row_is_an_actionable_error(tmp_path):
    source = write_csv(tmp_path, "ragged.csv", "business_id,name\nb1,Acme,extra\n")
    result = run_cli("analyze", "--input", str(source), "--out", str(tmp_path / "out"))
    assert result.returncode == 2
    assert "malformed" in result.stderr.lower()


def test_missing_input_file_is_an_actionable_error(tmp_path):
    result = run_cli(
        "analyze",
        "--input",
        str(tmp_path / "ghost.csv"),
        "--out",
        str(tmp_path / "out"),
    )
    assert result.returncode == 2
    assert "not found" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# Unwritable output
# --------------------------------------------------------------------------- #


def test_output_path_under_a_file_fails_cleanly(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    result = run_cli(
        "analyze",
        "--input",
        str(SAMPLE),
        "--out",
        str(blocker / "sub"),
    )
    assert result.returncode == 2
    assert "output" in result.stderr.lower()
    assert "Traceback" not in result.stderr


# --------------------------------------------------------------------------- #
# Repeatability
# --------------------------------------------------------------------------- #


class StubAIClient:
    """Deterministic stand-in for the local model; counts calls."""

    def __init__(self, same_entity="yes"):
        self.calls = 0
        self.same_entity = same_entity

    def chat(self, *, model, system, user, timeout, format_json=True):
        self.calls += 1
        return json.dumps(
            {
                "same_entity": self.same_entity,
                "reason": "stub verdict",
                "signals": ["name", "address"],
            }
        )


def test_ai_flag_writes_verdicts(tmp_path, monkeypatch):
    stub = StubAIClient()
    monkeypatch.setattr(cli, "_make_client", lambda args: stub)

    out = tmp_path / "ai"
    rc = cli.main(["analyze", "--input", str(SAMPLE), "--out", str(out), "--ai"])
    assert rc == 0

    assert (out / "ai_verdicts.json").is_file()
    document = read_json(out / "findings.json")
    assert document["metadata"]["ai"]["enabled"] is True
    assert document["metadata"]["ai"]["model"] == "gemma3:1b"
    assert "ai_verdicts" in document
    assert stub.calls > 0

    report = (out / "report.md").read_text(encoding="utf-8")
    assert "## AI adjudication" in report


def test_default_run_has_no_ai_artifacts(tmp_path):
    out = tmp_path / "plain"
    result = run_cli("analyze", "--input", str(SAMPLE), "--out", str(out))
    assert result.returncode == 0
    assert not (out / "ai_verdicts.json").exists()
    document = read_json(out / "findings.json")
    assert "ai" not in document["metadata"]
    assert "ai_verdicts" not in document


def test_ai_unavailable_preserves_deterministic_results(tmp_path, monkeypatch):
    class DownClient:
        def chat(self, **kwargs):
            raise AIUnavailableError("service down")

    monkeypatch.setattr(cli, "_make_client", lambda args: DownClient())

    out = tmp_path / "down"
    rc = cli.main(["analyze", "--input", str(SAMPLE), "--out", str(out), "--ai"])
    assert rc == 0  # AI failure never fails the run

    document = read_json(out / "findings.json")
    assert document["metadata"]["ai"]["status"] == "unavailable"
    assert document["metadata"]["duplicate_candidate_count"] == len(
        document["duplicate_candidates"]
    )
    assert document["metadata"]["duplicate_candidate_count"] == 9


def test_repeated_runs_are_equivalent(tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    first = run_cli("analyze", "--input", str(SAMPLE), "--out", str(out_a))
    second = run_cli("analyze", "--input", str(SAMPLE), "--out", str(out_b))
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr

    assert (out_a / "findings.json").read_text(encoding="utf-8") == (
        out_b / "findings.json"
    ).read_text(encoding="utf-8")
    assert (out_a / "duplicates_review.csv").read_text(encoding="utf-8") == (
        out_b / "duplicates_review.csv"
    ).read_text(encoding="utf-8")
    assert (out_a / "report.md").read_text(encoding="utf-8") == (
        out_b / "report.md"
    ).read_text(encoding="utf-8")
