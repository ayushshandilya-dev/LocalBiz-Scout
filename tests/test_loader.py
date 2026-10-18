"""Unit tests for the CSV loader."""

from pathlib import Path

import pytest

from localbiz_scout.errors import InputError
from localbiz_scout.loader import load_records, load_records_from_text

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


def test_preserves_embedded_newlines_in_quoted_fields(tmp_path):
    # A quoted cell may contain a CRLF line break (RFC 4180). The file path must
    # preserve it verbatim instead of normalizing it to LF, otherwise an export
    # would silently change a value the reviewer never approved.
    path = tmp_path / "multiline.csv"
    path.write_bytes(
        b"business_id,name\r\n"
        b'b1,"line1\r\nline2"\r\n'
    )
    loaded = load_records(path, ID_FIELD)
    assert loaded.records == ({"business_id": "b1", "name": "line1\r\nline2"},)

    from_path = loaded
    from_text = load_records_from_text(
        'business_id,name\r\nb1,"line1\r\nline2"\r\n', ID_FIELD, source="multiline.csv"
    )
    assert from_path.records == from_text.records


# --------------------------------------------------------------------------- #
# In-memory API (load_records_from_text)
# --------------------------------------------------------------------------- #


def test_from_text_parses_records():
    loaded = load_records_from_text("business_id,name\nb1,Acme Coffee\n", ID_FIELD)
    assert loaded.columns == ("business_id", "name")
    assert loaded.records == ({"business_id": "b1", "name": "Acme Coffee"},)


def test_from_text_and_path_api_agree(tmp_path):
    text = "business_id,name\nb1,Acme\nb2,Bob\n"
    from_path = load_records(_write(tmp_path, "same.csv", text), ID_FIELD)
    from_text = load_records_from_text(text, ID_FIELD, source="same.csv")
    assert from_text.columns == from_path.columns
    assert from_text.records == from_path.records


def test_from_text_tolerates_a_leading_bom():
    loaded = load_records_from_text("\ufeffbusiness_id,name\nb1,Acme\n", ID_FIELD)
    assert loaded.columns[0] == "business_id"
    assert loaded.records == ({"business_id": "b1", "name": "Acme"},)


def test_from_text_empty_raises():
    with pytest.raises(InputError, match="empty"):
        load_records_from_text("", ID_FIELD)


def test_from_text_missing_identifier_raises():
    with pytest.raises(InputError, match="business_id"):
        load_records_from_text("name,city\nAcme,Austin\n", ID_FIELD)


def test_from_text_ragged_row_raises():
    with pytest.raises(InputError, match="malformed"):
        load_records_from_text("business_id,name\nb1,Acme,extra\n", ID_FIELD)


def test_from_text_rejects_raw_bytes():
    with pytest.raises(InputError, match="text"):
        load_records_from_text(b"business_id,name\n", ID_FIELD)


def test_from_text_header_only_is_valid_and_empty():
    loaded = load_records_from_text("business_id,name\n", ID_FIELD)
    assert loaded.records == ()
    assert loaded.columns == ("business_id", "name")


def test_from_text_does_not_touch_the_filesystem():
    # A source label that looks like a path must never be opened.
    loaded = load_records_from_text(
        "business_id,name\nb1,Acme\n", ID_FIELD, source="/nonexistent/ghost.csv"
    )
    assert loaded.records == ({"business_id": "b1", "name": "Acme"},)
