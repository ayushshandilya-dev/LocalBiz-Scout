"""Unit tests for the CSV loader."""

from pathlib import Path

import pytest

from localbiz_scout.errors import InputError
from localbiz_scout.loader import load_records

ID_FIELD = "business_id"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_records(tmp_path):
    path = _write(
        tmp_path,
        "ok.csv",
        "business_id,name\nb1,Acme Coffee\nb2,Bob Plumbing\n",
    )
    loaded = load_records(path, ID_FIELD)
    assert loaded.columns == ("business_id", "name")
    assert loaded.records == (
        {"business_id": "b1", "name": "Acme Coffee"},
        {"business_id": "b2", "name": "Bob Plumbing"},
    )


def test_missing_file_raises(tmp_path):
    with pytest.raises(InputError, match="not found"):
        load_records(tmp_path / "nope.csv", ID_FIELD)


def test_directory_input_raises(tmp_path):
    with pytest.raises(InputError, match="directory"):
        load_records(tmp_path, ID_FIELD)


def test_empty_file_raises(tmp_path):
    path = _write(tmp_path, "empty.csv", "")
    with pytest.raises(InputError, match="empty"):
        load_records(path, ID_FIELD)


def test_missing_identifier_column_raises(tmp_path):
    path = _write(tmp_path, "bad.csv", "name,city\nAcme,Austin\n")
    with pytest.raises(InputError, match="business_id"):
        load_records(path, ID_FIELD)


def test_ragged_row_raises(tmp_path):
    path = _write(tmp_path, "ragged.csv", "business_id,name\nb1,Acme,extra\n")
    with pytest.raises(InputError, match="malformed"):
        load_records(path, ID_FIELD)


def test_header_only_is_valid_and_empty(tmp_path):
    path = _write(tmp_path, "header.csv", "business_id,name\n")
    loaded = load_records(path, ID_FIELD)
    assert loaded.records == ()
    assert loaded.columns == ("business_id", "name")


def test_bom_and_blank_lines_are_tolerated(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbfbusiness_id,name\n\nb1,Acme\n")
    loaded = load_records(path, ID_FIELD)
    assert loaded.columns[0] == "business_id"
    assert loaded.records == ({"business_id": "b1", "name": "Acme"},)


def test_padded_header_names_are_trimmed(tmp_path):
    path = _write(tmp_path, "padded.csv", " business_id , name \nb1,Acme\n")
    loaded = load_records(path, ID_FIELD)
    assert loaded.columns == ("business_id", "name")
    assert loaded.records[0]["business_id"] == "b1"
