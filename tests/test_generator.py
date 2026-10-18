"""Tests for the synthetic data generator."""

from collections import Counter

import generate_synthetic_data as gen


def test_generation_is_deterministic():
    records_a, groups_a = gen.generate_dataset(seed=gen.SEED)
    records_b, groups_b = gen.generate_dataset(seed=gen.SEED)
    assert records_a == records_b
    assert groups_a == groups_b


def test_records_have_expected_columns():
    records, _ = gen.generate_dataset()
    for record in records:
        assert list(record.keys()) == gen.SAMPLE_COLUMNS
        assert record["business_id"]


def test_business_ids_are_unique():
    records, _ = gen.generate_dataset()
    ids = [record["business_id"] for record in records]
    assert len(ids) == len(set(ids))


def test_duplicate_groups_exist():
    records, groups = gen.generate_dataset()
    assert len(groups) == len(records)

    group_sizes = Counter(groups.values())
    assert any(size >= 2 for size in group_sizes.values())


def test_confusable_groups_are_distinct():
    groups = set()
    _, mapping = gen.generate_dataset()
    groups.update(mapping.values())
    for group_a, group_b in gen.CONFUSABLE_PAIRS:
        assert group_a in groups
        assert group_b in groups
        assert group_a != group_b


def test_seeded_defects_are_present():
    records, _ = gen.generate_dataset()

    assert any(record["postal_code"] == "" for record in records)
    assert any(record["state"] == "" for record in records)
    assert any(record["website"] == "" for record in records)
    assert any(
        "  " in record["name"] or record["name"] != record["name"].strip()
        for record in records
    )
    assert any(
        sum(ch.isdigit() for ch in record["phone"]) == 10
        and "-" not in record["phone"]
        and "(" not in record["phone"]
        for record in records
    )
    assert any("LLC" in record["name"] or "Inc." in record["name"] for record in records)


def test_labeled_pairs_labels_match_groups():
    _, groups = gen.generate_dataset()
    pairs = gen.build_labeled_pairs(groups)

    for pair in pairs:
        expected = int(groups[pair["business_id_a"]] == groups[pair["business_id_b"]])
        assert pair["label"] == expected


def test_labeled_pairs_have_both_classes():
    _, groups = gen.generate_dataset()
    pairs = gen.build_labeled_pairs(groups)
    labels = {pair["label"] for pair in pairs}
    assert labels == {0, 1}
