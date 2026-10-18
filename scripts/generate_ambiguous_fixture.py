"""Generate an additive synthetic fixture of *ambiguous* duplicate pairs.

This is a **separate, additive** fixture. It does not modify the Phase 1
generator, ``data/sample_businesses.csv``, or ``data/fixtures/labeled_pairs.csv``.

Why it exists
-------------
The original labeled fixture is saturated for the deterministic engine: every
positive pair is already a candidate and none of the negative pairs are, so the
deterministic rules already score precision = recall = F1 = 1.0. There is no
room to measure whether an AI adjudicator helps. This fixture adds pairs that
straddle the deterministic fuzzy thresholds, including *hard negatives* (similar
name/address but genuinely different businesses) and *hard positives*.

Leakage caveat
--------------
The pairs below were chosen after inspecting the deterministic thresholds, so
this fixture is **not** independent validation of the deterministic rules. It is
a controlled test bed for whether an AI adjudicator adds value on the ambiguous
subset. It is synthetic and uses only ``example.com``/reserved 555 numbers.

Running it writes two files relative to the repository root:

* ``data/fixtures/ambiguous_businesses.csv`` -- synthetic records.
* ``data/fixtures/ambiguous_pairs.csv``      -- labeled pairs over those records.

Output is deterministic.
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUSINESSES_PATH = REPO_ROOT / "data" / "fixtures" / "ambiguous_businesses.csv"
PAIRS_PATH = REPO_ROOT / "data" / "fixtures" / "ambiguous_pairs.csv"

COLUMNS = [
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

# Each record is (business_id, name, address, city, state, postal_code,
# phone, website, category). Ground truth for the pairs is stated inline below.
BUSINESSES: list[tuple[str, ...]] = [
    # Hard negative: same name, same building, different suite -> two practices.
    ("amb-01", "Lakeview Dental", "100 Main St Ste 2", "Austin", "TX", "78701",
     "512-555-2001", "https://lakeview.example.com", "dentist"),
    ("amb-02", "Lakeview Dental", "100 Main St Ste 8", "Austin", "TX", "78701",
     "512-555-2002", "https://lakeview.example.com", "dentist"),
    # Control negative: similar names, adjacent address, different shops.
    ("amb-03", "Cedar Nail Salon", "22 Oak Avenue", "Round Rock", "TX", "78664",
     "512-555-2003", "https://cedarnails.example.com", "nails"),
    ("amb-04", "Cedar Nail Salon North", "24 Oak Avenue", "Round Rock", "TX",
     "78664", "512-555-2004", "https://cedarnailsnorth.example.com", "nails"),
    # Hard negative: same name/street, different house number.
    ("amb-05", "Sunrise Bakery", "18 Elm Street", "Austin", "TX", "78702",
     "512-555-2005", "https://sunrisebakery.example.com", "bakery"),
    ("amb-06", "Sunrise Bakery", "81 Elm Street", "Austin", "TX", "78702",
     "512-555-2006", "https://sunrisebakery.example.com", "bakery"),
    # Control negative: same name, different city/address.
    ("amb-07", "Sunrise Bakery", "900 Commerce St", "Dallas", "TX", "75202",
     "214-555-2007", "https://sunrisebakerydallas.example.com", "bakery"),
    # Hard positive: name variant, identical address + phone.
    ("amb-08", "Joe's Pizza", "5 Elm Street", "Austin", "TX", "78702",
     "512-555-2008", "https://joespizza.example.com", "pizza"),
    ("amb-09", "Joe's Pizzeria", "5 Elm St", "Austin", "TX", "78702",
     "512-555-2008", "https://joespizza.example.com", "pizza"),
    # Hard positive: article prefix, identical address + phone.
    ("amb-10", "Bluebird Diner", "40 River Road", "Austin", "TX", "78703",
     "512-555-2009", "https://bluebirddiner.example.com", "diner"),
    ("amb-11", "The Bluebird Diner", "40 River Rd", "Austin", "TX", "78703",
     "512-555-2009", "https://bluebirddiner.example.com", "diner"),
    # Hard positive: legal suffix, identical address + phone.
    ("amb-12", "Green Leaf Grocers", "77 Cedar Blvd", "Austin", "TX", "78705",
     "512-555-2010", "https://greenleaf.example.com", "grocery"),
    ("amb-13", "Green Leaf Grocers LLC", "77 Cedar Blvd", "Austin", "TX", "78705",
     "512-555-2010", "https://greenleaf.example.com", "grocery"),
    # Control positive (not a fuzzy candidate): exact duplicate.
    ("amb-14", "Harbor Books", "12 Bay Street", "Austin", "TX", "78706",
     "512-555-2011", "https://harborbooks.example.com", "books"),
    ("amb-15", "Harbor Books", "12 Bay Street", "Austin", "TX", "78706",
     "512-555-2011", "https://harborbooks.example.com", "books"),
]

# (business_id_a, business_id_b, label). label 1 = same business, 0 = different.
PAIRS: list[tuple[str, str, int]] = [
    ("amb-01", "amb-02", 0),   # ambiguous hard negative (suites)
    ("amb-03", "amb-04", 0),   # control negative (not a candidate)
    ("amb-05", "amb-06", 0),   # ambiguous hard negative (house number)
    ("amb-05", "amb-07", 0),   # control negative (different city)
    ("amb-08", "amb-09", 1),   # ambiguous hard positive (name variant)
    ("amb-10", "amb-11", 1),   # ambiguous hard positive (article)
    ("amb-12", "amb-13", 1),   # ambiguous hard positive (legal suffix)
    ("amb-14", "amb-15", 1),   # control positive (exact)
]


def write_outputs() -> tuple[Path, Path]:
    BUSINESSES_PATH.parent.mkdir(parents=True, exist_ok=True)

    with BUSINESSES_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(BUSINESSES)

    with PAIRS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["business_id_a", "business_id_b", "label"])
        writer.writerows(PAIRS)

    return BUSINESSES_PATH, PAIRS_PATH


def main() -> int:
    businesses, pairs = write_outputs()
    print(f"Wrote {businesses.relative_to(REPO_ROOT)}")
    print(f"Wrote {pairs.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
