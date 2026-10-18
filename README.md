# LocalBiz Scout

LocalBiz Scout is an early-stage project for collecting and de-duplicating
local business records that are typically spread across many inconsistent
sources. The intended end goal is to normalize messy business listings and
identify which records refer to the same real-world business before any
downstream analysis.

> **Status: Phase 5 — release/demo readiness.** The deterministic workflow is
> the default and fully offline. An opt-in `--ai` flag asks a local Ollama model
> to adjudicate *ambiguous* duplicate candidates; on the synthetic fixtures the
> model matched, but did not beat, the deterministic rules (see "Evaluation").
> There is still no scraping or automatic merging.

## Quick start

Prerequisites: Python 3.10+ and the single runtime dependency `rapidfuzz`
(declared in `pyproject.toml`). This repository is run **from source**; the
commands below need no installation and no network access. `reports/` is
git-ignored, so generated output never enters version control.

```
# 1) check the environment (prints the Python version and rapidfuzz version)
python3 -c "import sys, rapidfuzz; print(sys.version.split()[0], rapidfuzz.__version__)"

# 2) deterministic scan of the bundled sample (offline, no model)
PYTHONPATH=src python3 -m localbiz_scout analyze \
  --input data/sample_businesses.csv \
  --out reports/sample-run

# 3) read the human-readable report
python3 -c "print(open('reports/sample-run/report.md').read())"
```

That produces `findings.json`, `report.md`, and `duplicates_review.csv`. For the
optional local-model path, see "Optional local AI adjudication"; for the
scoring command, see "Evaluation". None of these commands have been verified as
a packaged install — only run-from-source is exercised here.

## Repository layout

```
.
├── pyproject.toml                    # project metadata and pytest config
├── README.md
├── data/
│   ├── sample_businesses.csv         # generated synthetic records
│   └── fixtures/
│       ├── labeled_pairs.csv         # generated duplicate-pair labels
│       ├── ambiguous_businesses.csv  # additive ambiguous-fixture records
│       └── ambiguous_pairs.csv       # additive ambiguous-pair labels
├── scripts/
│   ├── generate_synthetic_data.py    # deterministic data generator
│   ├── generate_ambiguous_fixture.py # additive ambiguous fixture generator
│   └── evaluate_pairs.py             # deterministic vs AI-assisted metrics
├── src/
│   └── localbiz_scout/
│       ├── __init__.py
│       ├── __main__.py               # enables `python3 -m localbiz_scout`
│       ├── cli.py                    # argparse CLI (analyze command)
│       ├── loader.py                 # read-only CSV loading + validation
│       ├── reports.py                # findings.json / report.md / CSVs
│       ├── errors.py                 # InputError / OutputError
│       ├── ai/
│       │   ├── __init__.py           # public AI adjudication surface
│       │   ├── client.py             # local Ollama HTTP client (stdlib)
│       │   └── adjudicator.py        # prompt + strict verdict schema
│       └── rules/
│           ├── config.py             # RuleConfig (fields, thresholds)
│           ├── records.py            # record helpers used by the rules
│           ├── normalization.py      # safe text/phone/address normalization
│           ├── findings.py           # Finding / DuplicateCandidate types
│           ├── missing_fields.py     # rule: missing important fields
│           ├── email.py              # rule: email syntax
│           ├── duplicates.py         # rule: duplicate candidates
│           ├── suspicious.py         # rule: suspicious-value checks
│           └── engine.py             # scan() + ScanResult
└── tests/
    ├── conftest.py
    ├── test_generator.py
    ├── test_fixture.py
    ├── test_missing_fields.py
    ├── test_email_rule.py
    ├── test_duplicates.py
    ├── test_suspicious.py
    ├── test_normalization.py
    ├── test_engine.py
    ├── test_loader.py
    ├── test_cli.py
    ├── test_ai_client.py
    └── test_ai_adjudicator.py
```

## Synthetic data

All data is synthetic. It is generated locally by
`scripts/generate_synthetic_data.py` using only the Python standard library.
No network requests are made and no data from other projects is used.

The generator produces ten canonical businesses and injects intentionally
seeded data-quality defects into some records, including:

- inconsistent name casing, extra whitespace, legal suffixes and articles
- missing fields (website, state, postal code)
- inconsistent phone formatting (`512-555-0101`, `(512) 555-0101`, `5125550101`)
- abbreviated street names (`Street` -> `St`, etc.)

Some businesses appear more than once under the same real-world identity, so
de-duplication matters. A handful of cross-business pairs are deliberately
confusable (for example, *Bright Smile Orthodontics* vs *Bright Smile
Dentistry*) and are used as labeled negatives.

`data/fixtures/labeled_pairs.csv` contains pairs of `business_id` values with
a binary `label` (`1` = same business, `0` = different businesses). Labels are
derived from the generator's internal ground-truth grouping, so they are
justified by construction.

### Regenerating the data

```
python3 scripts/generate_synthetic_data.py
```

The output is deterministic for a fixed seed (default `20260101`); pass
`--seed` to change it.

## Deterministic usage (default, offline)

This is the default mode: no model, no network. The package is not installed,
so run it from the repository root with `src` on `PYTHONPATH` (an installed
console script `localbiz-scout` is also declared in `pyproject.toml`):

```
PYTHONPATH=src python3 -m localbiz_scout analyze \
  --input data/sample_businesses.csv \
  --out reports/sample-run
```

The input CSV is opened read-only and is never modified. `--out` is created if
it does not exist. The command writes three files:

| File | Contents |
| --- | --- |
| `findings.json` | Structured findings, duplicate candidates, and run metadata (counts, columns, thresholds, notes). No timestamps, so output is byte-stable. |
| `report.md` | Human-readable summary with counts by rule and severity, plus limitations. |
| `duplicates_review.csv` | One row per duplicate candidate: both IDs, reason, similarity, and available evidence fields. |

Exit codes: `0` on success, `2` for an invalid input or unwritable output (with
a short message on stderr rather than a traceback).
`PYTHONPATH=src python3 -m localbiz_scout --help` and
`PYTHONPATH=src python3 -m localbiz_scout analyze --help` describe the options.

### CLI limitations

- The CLI only reads CSV, not other formats.
- Rows with a different number of fields than the header are rejected as
  malformed rather than coerced.
- Duplicate candidates are suggestions for review; the tool never merges or
  deletes records and never writes back to the input.

## Optional local AI adjudication

Adding `--ai` asks a local Ollama model to adjudicate **only** the ambiguous
(`fuzzy`) candidate pairs. The default workflow is unchanged when `--ai` is
absent. This is the only mode that talks to anything, and only to a local
Ollama server (`127.0.0.1`); without `--ai` the tool makes no network calls.

```
PYTHONPATH=src python3 -m localbiz_scout analyze \
  --input data/fixtures/ambiguous_businesses.csv \
  --out reports/ai-run --ai
```

- Endpoint: the local Ollama server only (`--ai-base-url`, default
  `http://127.0.0.1:11434`); model `gemma3:1b` by default (`--ai-model`).
  Per-request timeout `--ai-timeout` (default 30s).
- Only non-contact fields (`name`, `address`, `city`, `state`, `postal_code`,
  `category`, `website`) for the two records are sent. **Phone and email are
  never sent**, and prompts are never stored or logged.
- The model must return strict JSON
  (`same_entity`: `yes`/`no`/`uncertain`, `reason`, `signals`); malformed or
  unsupported responses are rejected.
- The AI never edits, merges, or rewrites records and never changes the source
  CSV. A `no` verdict demotes a candidate; every failure (service down,
  timeout, bad output) falls back to the deterministic result and is reported
  as such.
- When enabled, an extra `ai_verdicts.json` is written and `findings.json` /
  `report.md` gain an AI section (model name, status, verdicts).

## Evaluation

`scripts/evaluate_pairs.py` compares deterministic-only and AI-assisted
predictions on a labeled fixture (positive class = `label 1`; closed-world on
the listed pairs). It prints the fixture sizes, the class distribution, the
per-pair detail, the metrics, and an explicit limitations list.

**Offline (reproducible, no model).** This is the command to reproduce the
summary numbers below and needs no network:

```
PYTHONPATH=src python3 scripts/evaluate_pairs.py \
  --businesses data/fixtures/ambiguous_businesses.csv \
  --labels data/fixtures/ambiguous_pairs.csv
```

**Live (optional, local model).** Adds the `--ai` column and requires a running
local Ollama server with the model; AI results are recomputed each run and are
**not** cached, so these numbers are not reproducible without the model:

```
PYTHONPATH=src python3 scripts/evaluate_pairs.py \
  --businesses data/fixtures/ambiguous_businesses.csv \
  --labels data/fixtures/ambiguous_pairs.csv --ai --ai-timeout 120
```

Findings on the synthetic fixtures with `gemma3:1b`:

| Fixture | Approach | P | R | F1 |
| --- | --- | --- | --- | --- |
| `labeled_pairs.csv` (13 pairs) | deterministic | 1.000 | 1.000 | 1.000 |
| `labeled_pairs.csv` | + AI | 1.000 | 1.000 | 1.000 |
| `ambiguous_pairs.csv` (8 pairs) | deterministic | 0.667 | 1.000 | 0.800 |
| `ambiguous_pairs.csv` | + AI | 0.667 | 1.000 | 0.800 |

**The model did not improve on the deterministic rules.** The original fixture
is saturated (deterministic already perfect), so it cannot show improvement.
The additive `ambiguous_*` fixture was built specifically to create ambiguous
hard cases; on it, `gemma3:1b` answered `yes` for all five ambiguous pairs —
including the two **hard negatives** `amb-01`/`amb-02` (same building, suites 2
vs 8) and `amb-05`/`amb-06` (same street, house numbers 18 vs 81). It never
demoted a single false positive, so precision stayed at 0.667. The AI can
therefore only match or lower precision here, and is reported as no better.
Small samples make these numbers indicative only. See the evaluator's printed
limitations for the exact scope of these metrics.

## Data-quality rules

`localbiz_scout.rules.scan(records, config)` runs every rule over a sequence of
record mappings and returns a `ScanResult` (`to_dict()` is JSON-friendly). It is
deterministic, offline, and never mutates the input records.

| Rule id | What it detects |
| --- | --- |
| `missing_field` | Empty, whitespace-only, or `None` values in configured important fields. |
| `invalid_email` | Email values that are not well-formed *syntax*. |
| `duplicate_candidate` | `exact`, `normalized`, or `fuzzy` record pairs. |
| `suspicious_phone_length` | Phones that are not 10-digit North-American numbers. |
| `suspicious_state_code` | States that are not a two-letter code. |
| `suspicious_postal_code` | Values that are not a 5-digit or 5+4 US ZIP code. |
| `placeholder_name` | Names that are placeholder tokens or contain no letters/digits. |

Normalization collapses only safe formatting differences: case, surrounding and
repeated whitespace, phone punctuation, and a documented set of common street
abbreviations (`Street` -> `St`, ...). Legal suffixes such as "LLC"/"Inc." are
**not** normalized, so those near-matches appear as fuzzy candidates for review.

Duplicate matching thresholds (`name` and `address` token-sort ratios, default
85.0 each) are explicit in `RuleConfig`. A pair is a fuzzy candidate only when
*both* the normalized name and address clear their thresholds. **Fuzzy
similarity is a heuristic for human review, not proof that two records are the
same business**, and the rule never merges or deletes records.

### Documented limitations

- The shipped synthetic schema has **no `email` column**, so the
  `invalid_email` rule produces no findings on `data/sample_businesses.csv`. It
  is exercised by tests using explicit fixtures. Blank emails (when the column
  exists) are reported by `missing_field`, not by `invalid_email`.
- The suspicious-value checks are **US-centric** and will flag
  legitimately-formatted foreign addresses and phone numbers.
- Cross-field consistency (for example, whether a city belongs to a state) is
  **not** checked, because the schema has no reference data to validate against.
- The rules flag formatting problems only; they do not infer that a business is
  fraudulent.
- The optional AI adjudicator requires a locally running Ollama server and the
  configured model; it is unavailable otherwise and simply falls back. Its
  verdicts are **advisory and unvalidated beyond the small synthetic fixtures**,
  and a small local model can be confidently wrong (it accepted every ambiguous
  pair, including the hard negatives).

## Development

Requires Python 3.10+. The test suite uses `pytest`.

```
python3 -m pytest
```
