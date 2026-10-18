"""Tests for the generated CSV files (sample records and labeled fixture)."""

import csv
from pathlib import Path

import generate_synthetic_data as gen

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = REPO_ROOT / "data" / "sample_businesses.csv"
FIXTURE_PATH = REPO_ROOT / "data" / "fixtures" / "labeled_pairs.csv"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_sample_file_exists_with_header():
    assert SAMPLE_PATH.is_file()
    with SAMPLE_PATH.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == gen.SAMPLE_COLUMNS


def test_fixture_file_exists_with_header():
    assert FIXTURE_PATH.is_file()
    with FIXTURE_PATH.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == gen.FIXTURE_COLUMNS


def test_generated_files_match_generator_output():
    records, groups = gen.generate_dataset()
    expected_records = [
        {column: record[column] for column in gen.SAMPLE_COLUMNS} for record in records
    ]
    expected_pairs = gen.build_labeled_pairs(groups)

    assert _read_rows(SAMPLE_PATH) == expected_records
    assert _read_rows(FIXTURE_PATH) == [
        {key: str(value) for key, value in pair.items()} for pair in expected_pairs
    ]


def test_fixture_labels_are_binary():
    rows = _read_rows(FIXTURE_PATH)
    assert rows
    assert {row["label"] for row in rows} <= {"0", "1"}
    assert {row["label"] for row in rows} == {"0", "1"}


def test_fixture_ids_reference_sample_records():
    sample_ids = {row["business_id"] for row in _read_rows(SAMPLE_PATH)}
    for row in _read_rows(FIXTURE_PATH):
        assert row["business_id_a"] in sample_ids
        assert row["business_id_b"] in sample_ids


def test_fixture_pair_shape_is_valid():
    rows = _read_rows(FIXTURE_PATH)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        a, b = row["business_id_a"], row["business_id_b"]
        assert a != b
        key = tuple(sorted((a, b)))
        assert key not in seen
        seen.add(key)


def test_fixture_labels_are_justified_by_ground_truth():
    _, groups = gen.generate_dataset()
    for row in _read_rows(FIXTURE_PATH):
        expected = int(groups[row["business_id_a"]] == groups[row["business_id_b"]])
        assert int(row["label"]) == expected
