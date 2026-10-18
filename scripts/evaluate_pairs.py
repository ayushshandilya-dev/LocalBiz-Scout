"""Compare deterministic-only vs AI-assisted duplicate predictions.

Metrics are computed on a labeled pair fixture. The positive class is
``label == 1`` (the pair is the same business). Evaluation is *closed-world*:
only pairs listed in the fixture are scored, so pairs the engine flags that are
absent from the fixture are ignored, and pairs the engine never flags are
counted as negative predictions.

Usage (deterministic only):

    PYTHONPATH=src python3 scripts/evaluate_pairs.py \
        --businesses data/fixtures/ambiguous_businesses.csv \
        --labels data/fixtures/ambiguous_pairs.csv

With local AI adjudication of ambiguous (fuzzy) candidates:

    PYTHONPATH=src python3 scripts/evaluate_pairs.py \
        --businesses data/fixtures/ambiguous_businesses.csv \
        --labels data/fixtures/ambiguous_pairs.csv --ai

Nothing here is tuned automatically; thresholds come from the rule config.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from localbiz_scout.ai import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    HttpOllamaClient,
    adjudicate_candidates,
)
from localbiz_scout.ai.client import DEFAULT_BASE_URL  # noqa: E402
from localbiz_scout.loader import load_records  # noqa: E402
from localbiz_scout.rules import scan  # noqa: E402
from localbiz_scout.rules.config import DEFAULT_CONFIG  # noqa: E402

# Always printed at the end so a result is never read without its caveats.
LIMITATIONS = [
    "Closed-world: only pairs listed in the labels file are scored. A candidate "
    "pair absent from that file would be ignored; engine non-candidates are "
    "counted as negative predictions.",
    "Positive class is label 1. The AI keeps a candidate unless it answers "
    '"no", so an "uncertain" verdict or any fallback is scored as a positive '
    "prediction (a conservative choice, not a separate abstain bucket).",
    "Deterministic metrics are reproducible offline. AI-assisted metrics "
    "require a live local Ollama model and are recomputed each run (results are "
    "not cached or stored by this script), so AI numbers are not reproducible "
    "without that model.",
    "The ambiguous fixture was authored after inspecting the thresholds (see "
    "scripts/generate_ambiguous_fixture.py), so it is a controlled test bed, "
    "not independent validation of the deterministic rules.",
    "Fixtures are small, so precision/recall/F1 are indicative only.",
]


def _key(a: str, b: str) -> frozenset[str]:
    return frozenset((a.strip(), b.strip()))


def _reason_counts(by_reason: dict[str, int]) -> str:
    if not by_reason:
        return "none"
    return ", ".join(f"{reason}={by_reason[reason]}" for reason in sorted(by_reason))


def _prf(labels: list[tuple[frozenset[str], bool]], positives: set[frozenset[str]]):
    tp = fp = fn = tn = 0
    for key, is_same in labels:
        predicted = key in positives
        if is_same and predicted:
            tp += 1
        elif not is_same and predicted:
            fp += 1
        elif is_same and not predicted:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--businesses", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--ai", action="store_true")
    parser.add_argument("--ai-model", default=DEFAULT_MODEL)
    parser.add_argument("--ai-timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--ai-base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)

    config = DEFAULT_CONFIG
    loaded = load_records(args.businesses, config.id_field)
    result = scan(loaded.records, config)
    candidates = {
        _key(c.business_id_a, c.business_id_b): c for c in result.duplicate_candidates
    }

    with open(args.labels, newline="", encoding="utf-8") as handle:
        raw_labels = list(csv.DictReader(handle))
    labels = [
        (_key(row["business_id_a"], row["business_id_b"]), row["label"] == "1")
        for row in raw_labels
    ]

    positives = sum(1 for _, is_same in labels if is_same)
    negatives = len(labels) - positives
    by_reason: dict[str, int] = {}
    for candidate in candidates.values():
        by_reason[candidate.reason] = by_reason.get(candidate.reason, 0) + 1

    print(f"Businesses: {args.businesses} ({len(loaded.records)} records)")
    print(f"Labels:     {args.labels}")
    print("Fixture summary:")
    print(f"  records:              {len(loaded.records)}")
    print(f"  engine candidates:    {len(candidates)} ({_reason_counts(by_reason)})")
    print(f"  labeled pairs:        {len(labels)}")
    print(f"  positive labels (1):  {positives}")
    print(f"  negative labels (0):  {negatives}")
    print()
    print(
        "Note: closed-world eval on the fixture; not independent validation of "
        "the thresholds."
    )
    print()

    deterministic_positives = set(candidates.keys())

    ai_positives = None
    ai_report = None
    if args.ai:
        records_by_id = {
            str(record.get(config.id_field, "")).strip(): record
            for record in loaded.records
        }
        ai_report = adjudicate_candidates(
            result.duplicate_candidates,
            records_by_id,
            HttpOllamaClient(base_url=args.ai_base_url),
            model=args.ai_model,
            timeout=args.ai_timeout,
        )
        demoted = {
            _key(r.business_id_a, r.business_id_b)
            for r in ai_report.results
            if not r.effective_positive
        }
        ai_positives = deterministic_positives - demoted

    det = _prf(labels, deterministic_positives)
    print("Per-pair detail:")
    print(f"  {'pair':28s} {'label':5s} {'det':10s} {'ai':6s} ai_status")
    verdicts = {}
    if ai_report is not None:
        verdicts = {
            _key(r.business_id_a, r.business_id_b): r for r in ai_report.results
        }
    for key, is_same in labels:
        a, b = sorted(key)
        reason = candidates[key].reason if key in candidates else "-"
        verdict = verdicts.get(key)
        if args.ai:
            ai_cell = verdict.same_entity if verdict and verdict.ai_status == "ok" else (
                "fellback" if verdict else "-"
            )
            status = verdict.ai_status if verdict else "not-ambiguous"
        else:
            ai_cell = "-"
            status = "-"
        print(
            f"  {a + '/' + b:28s} {int(is_same):<5d} {reason:10s} {ai_cell:6s} {status}"
        )
    print()

    print("Metrics (positive class = label 1):")
    print("  approach        TP  FP  FN  TN   P      R      F1")
    print(
        "  deterministic   "
        f"{det['tp']:>3} {det['fp']:>3} {det['fn']:>3} {det['tn']:>3}  "
        f"{det['precision']:<6} {det['recall']:<6} {det['f1']}"
    )
    if ai_positives is not None:
        ai = _prf(labels, ai_positives)
        print(
            "  ai-assisted     "
            f"{ai['tp']:>3} {ai['fp']:>3} {ai['fn']:>3} {ai['tn']:>3}  "
            f"{ai['precision']:<6} {ai['recall']:<6} {ai['f1']}"
        )
        print()
        print(f"AI model: {ai_report.model}  status: {ai_report.status}")
        print(f"AI message: {ai_report.message}")

    print()
    print("Limitations:")
    for note in LIMITATIONS:
        print(f"  - {note}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
