"""Generate synthetic business records and a labeled duplicate-pair fixture.

This script is fully self-contained and uses only the Python standard library.
It does not read data from any other project and makes no network requests.

Running it writes two files relative to the repository root:

* ``data/sample_businesses.csv``     -- synthetic business records, some of
  which are duplicates of the same real-world business with intentionally
  seeded data-quality defects (case, whitespace, legal suffixes, missing
  fields, phone formatting, address abbreviations).
* ``data/fixtures/labeled_pairs.csv`` -- pairs of ``business_id`` values with a
  binary ``label`` (1 = the pair refers to the same business, 0 = different
  businesses). Labels are derived from the generator's internal ground-truth
  grouping, so they are justified by construction.

The output is deterministic for a given ``SEED``.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SEED = 20260101
REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_PATH = REPO_ROOT / "data" / "sample_businesses.csv"
FIXTURE_PATH = REPO_ROOT / "data" / "fixtures" / "labeled_pairs.csv"

SAMPLE_COLUMNS = [
    "business_id",
    "name",
    "address",
    "city",
    "state",
    "postal_code",
    "phone",
    "website",
    "category",
]

FIXTURE_COLUMNS = ["business_id_a", "business_id_b", "label"]


@dataclass(frozen=True)
class Business:
    """A canonical (de-duplicated) synthetic business."""

    group_id: str
    name: str
    address: str
    city: str
    state: str
    postal_code: str
    phone: str
    website: str
    category: str


# Ten canonical businesses. Names are intentionally chosen so that four
# cross-group pairs are confusable but genuinely distinct businesses; those
# pairs are used as labeled negatives in the fixture.
CANONICAL_BUSINESSES: list[Business] = [
    Business(
        "grp-01", "Blue Bottle Coffee", "1201 S Congress Ave", "Austin", "TX",
        "78704", "512-555-0101", "https://bluebottle.example.com", "cafe",
    ),
    Business(
        "grp-02", "Riverstone Dental", "88 Rio Grande St", "Austin", "TX",
        "78701", "512-555-0142", "https://riverstonedental.example.com", "dentist",
    ),
    Business(
        "grp-03", "AutoFix Garage", "410 N Lamar Blvd", "Austin", "TX",
        "78703", "512-555-0177", "https://autofixgarage.example.com", "auto_repair",
    ),
    Business(
        "grp-04", "Bright Smile Orthodontics", "700 W 5th Street", "Austin", "TX",
        "78703", "512-555-0190", "https://brightsmileortho.example.com", "orthodontist",
    ),
    Business(
        "grp-05", "Summit Yoga Studio", "1500 E 6th Street", "Austin", "TX",
        "78702", "512-555-0123", "https://summityoga.example.com", "gym",
    ),
    Business(
        "grp-06", "Pioneer Plumbing", "33 Burnet Road", "Austin", "TX",
        "78756", "512-555-0166", "https://pioneerplumbing.example.com", "plumber",
    ),
    Business(
        "grp-07", "Bright Smile Dentistry", "910 E 41st Street", "Austin", "TX",
        "78751", "512-555-0199", "https://brightsmiledentistry.example.com", "dentist",
    ),
    Business(
        "grp-08", "Summit Yoga & Wellness", "2200 S Lamar Blvd", "Austin", "TX",
        "78704", "512-555-0134", "https://summityogawellness.example.com", "gym",
    ),
    Business(
        "grp-09", "Pioneer Plumbing & Heating", "512 Airport Blvd", "Austin", "TX",
        "78702", "512-555-0155", "https://pioneerplumbingheating.example.com", "plumber",
    ),
    Business(
        "grp-10", "Bluebird Cafe", "300 W 6th Street", "Austin", "TX",
        "78701", "512-555-0112", "https://bluebirdcafe.example.com", "cafe",
    ),
]

# How many *additional* (duplicate) records to create for a given group. The
# canonical record plus these duplicates all share the same group_id.
DUPLICATE_COUNTS: dict[str, int] = {
    "grp-01": 2,
    "grp-02": 1,
    "grp-03": 2,
    "grp-05": 1,
    "grp-06": 1,
}

# Explicitly confusable cross-group pairs used as labeled negatives. Each pair
# lists two group ids; representative records are chosen when building rows.
CONFUSABLE_PAIRS: list[tuple[str, str]] = [
    ("grp-04", "grp-07"),  # orthodontics vs dentistry
    ("grp-05", "grp-08"),  # yoga studio vs wellness studio
    ("grp-06", "grp-09"),  # plumbing vs plumbing & heating
    ("grp-01", "grp-10"),  # coffee vs cafe
]

STREET_ABBREVIATIONS = {
    "Street": "St",
    "Avenue": "Ave",
    "Boulevard": "Blvd",
    "Drive": "Dr",
    "Road": "Rd",
}


def _phone_digits(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def _defect_lower_case(record: dict[str, str], rng: random.Random) -> None:
    record["name"] = record["name"].lower()


def _defect_extra_whitespace(record: dict[str, str], rng: random.Random) -> None:
    record["name"] = "  " + record["name"].replace(" ", "  ") + " "


def _defect_legal_suffix(record: dict[str, str], rng: random.Random) -> None:
    record["name"] = record["name"] + rng.choice([" LLC", " Inc.", " Co."])


def _defect_article_prefix(record: dict[str, str], rng: random.Random) -> None:
    record["name"] = "The " + record["name"]


def _defect_missing_website(record: dict[str, str], rng: random.Random) -> None:
    record["website"] = ""


def _defect_missing_postal_code(record: dict[str, str], rng: random.Random) -> None:
    record["postal_code"] = ""


def _defect_missing_state(record: dict[str, str], rng: random.Random) -> None:
    record["state"] = ""


def _defect_phone_parens(record: dict[str, str], rng: random.Random) -> None:
    digits = _phone_digits(record["phone"])
    record["phone"] = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _defect_phone_plain(record: dict[str, str], rng: random.Random) -> None:
    record["phone"] = _phone_digits(record["phone"])


def _defect_street_abbrev(record: dict[str, str], rng: random.Random) -> None:
    for long_form, short_form in STREET_ABBREVIATIONS.items():
        if long_form in record["address"]:
            record["address"] = record["address"].replace(long_form, short_form)
            return


# Each defect mutates a copy of a record in place. They are applied
# deterministically (seeded shuffle) when generating duplicate records.
DEFECTS: list[Callable[[dict[str, str], random.Random], None]] = [
    _defect_lower_case,
    _defect_extra_whitespace,
    _defect_legal_suffix,
    _defect_article_prefix,
    _defect_missing_website,
    _defect_missing_postal_code,
    _defect_missing_state,
    _defect_phone_parens,
    _defect_phone_plain,
    _defect_street_abbrev,
]

# A small, fixed set of defects applied to the single-record groups so the
# sample also contains quality problems outside the duplicate groups.
SINGLETON_DEFECTS: dict[str, list[str]] = {
    "grp-04": ["street_abbrev"],
    "grp-07": ["missing_website"],
    "grp-10": ["phone_parens"],
}

_DEFECT_BY_NAME: dict[str, Callable[[dict[str, str], random.Random], None]] = {
    "_defect_lower_case": _defect_lower_case,
    "_defect_extra_whitespace": _defect_extra_whitespace,
    "_defect_legal_suffix": _defect_legal_suffix,
    "_defect_article_prefix": _defect_article_prefix,
    "_defect_missing_website": _defect_missing_website,
    "_defect_missing_postal_code": _defect_missing_postal_code,
    "_defect_missing_state": _defect_missing_state,
    "_defect_phone_parens": _defect_phone_parens,
    "_defect_phone_plain": _defect_phone_plain,
    "_defect_street_abbrev": _defect_street_abbrev,
}

_SINGLETON_ALIASES = {
    "lower_case": "_defect_lower_case",
    "extra_whitespace": "_defect_extra_whitespace",
    "legal_suffix": "_defect_legal_suffix",
    "article_prefix": "_defect_article_prefix",
    "missing_website": "_defect_missing_website",
    "missing_postal_code": "_defect_missing_postal_code",
    "missing_state": "_defect_missing_state",
    "phone_parens": "_defect_phone_parens",
    "phone_plain": "_defect_phone_plain",
    "street_abbrev": "_defect_street_abbrev",
}


def _record_from_business(business: Business) -> dict[str, str]:
    return {
        "name": business.name,
        "address": business.address,
        "city": business.city,
        "state": business.state,
        "postal_code": business.postal_code,
        "phone": business.phone,
        "website": business.website,
        "category": business.category,
    }


def generate_dataset(seed: int = SEED) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Return ``(records, groups)``.

    ``records`` is a list of row dicts (including ``business_id``) in a
    deterministic order. ``groups`` maps each ``business_id`` to the
    ground-truth ``group_id`` of the real-world business it describes.
    """

    rng = random.Random(seed)

    # Shuffle the defect catalog once so the chosen defects vary by seed while
    # remaining reproducible.
    defect_order = list(DEFECTS)
    rng.shuffle(defect_order)

    records: list[dict[str, str]] = []
    groups: dict[str, str] = {}
    counter = 1
    defect_cursor = 0

    for business in CANONICAL_BUSINESSES:
        # Canonical record.
        canonical_id = f"biz-{counter:04d}"
        counter += 1
        canonical_record = _record_from_business(business)
        groups[canonical_id] = business.group_id
        record_entry = {"business_id": canonical_id, **canonical_record}
        records.append(record_entry)

        # Apply any fixed singleton defects to the canonical record.
        for alias in SINGLETON_DEFECTS.get(business.group_id, []):
            _DEFECT_BY_NAME[_SINGLETON_ALIASES[alias]](canonical_record, rng)
            record_entry.update(canonical_record)

        # Duplicate records for groups that have them.
        extra_count = DUPLICATE_COUNTS.get(business.group_id, 0)
        for _ in range(extra_count):
            duplicate_id = f"biz-{counter:04d}"
            counter += 1
            duplicate_record = dict(canonical_record)
            # Apply one defect per duplicate, cycling through the pre-shuffled
            # catalog so the sample exercises a range of defect types.
            defect = defect_order[defect_cursor % len(defect_order)]
            defect_cursor += 1
            defect(duplicate_record, rng)
            groups[duplicate_id] = business.group_id
            records.append({"business_id": duplicate_id, **duplicate_record})

    return records, groups


def _first_id_by_group(groups: dict[str, str]) -> dict[str, str]:
    first: dict[str, str] = {}
    for business_id, group_id in groups.items():
        first.setdefault(group_id, business_id)
    return first


def build_labeled_pairs(
    groups: dict[str, str],
) -> list[dict[str, str | int]]:
    """Build the labeled duplicate-pair fixture from ground-truth groups."""

    ids_by_group: dict[str, list[str]] = {}
    for business_id, group_id in groups.items():
        ids_by_group.setdefault(group_id, []).append(business_id)

    rows: list[dict[str, str | int]] = []

    # Positive pairs: every unordered pair within the same group.
    for group_id in sorted(ids_by_group):
        ids = sorted(ids_by_group[group_id])
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                rows.append(
                    {"business_id_a": ids[i], "business_id_b": ids[j], "label": 1}
                )

    # Negative pairs: explicitly confusable cross-group businesses.
    first_id = _first_id_by_group(groups)
    for group_a, group_b in CONFUSABLE_PAIRS:
        id_a, id_b = first_id[group_a], first_id[group_b]
        if id_a > id_b:
            id_a, id_b = id_b, id_a
        rows.append({"business_id_a": id_a, "business_id_b": id_b, "label": 0})

    rows.sort(key=lambda row: (row["business_id_a"], row["business_id_b"]))
    return rows


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(seed: int = SEED) -> tuple[Path, Path]:
    """Generate the dataset and write both CSV files. Returns their paths."""

    records, groups = generate_dataset(seed=seed)
    pairs = build_labeled_pairs(groups)

    sample_rows = [
        {column: record[column] for column in SAMPLE_COLUMNS} for record in records
    ]
    _write_csv(SAMPLE_PATH, SAMPLE_COLUMNS, sample_rows)
    _write_csv(FIXTURE_PATH, FIXTURE_COLUMNS, pairs)
    return SAMPLE_PATH, FIXTURE_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        type=int,
        default=SEED,
        help=f"random seed for deterministic generation (default: {SEED})",
    )
    args = parser.parse_args(argv)

    sample_path, fixture_path = write_outputs(seed=args.seed)
    print(f"Wrote {sample_path.relative_to(REPO_ROOT)}")
    print(f"Wrote {fixture_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
