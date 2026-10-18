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
> An opt-in `review` command writes an editable decisions template plus a
> proposal listing, and an opt-in `remediate` command (see "Human-reviewed
> remediation") applies only human-approved, allowlisted formatting fixes to a
> *copy* of the input. There is still no scraping, and no automatic merging or
> deletion.

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
scoring command, see "Evaluation".

Running from source with `PYTHONPATH=src` is the primary, network-free path and
is what the examples use. The package can also be installed normally
(`pip install .`), which pulls in the single declared dependency (`rapidfuzz`)
and provides the `localbiz-scout` console script and the
`python -m localbiz_scout` module entry point; both were exercised in a clean
virtual environment during release checks. See "Installation" for details.

## Installation

The project is plain Python with a single runtime dependency and is usually run
in place (no install, no network):

```
PYTHONPATH=src python3 -m localbiz_scout analyze \
  --input data/sample_businesses.csv --out reports/sample-run
```

To install it — for example into a virtual environment — use the standard
setuptools build declared in `pyproject.toml`:

```
python3 -m venv .venv
. .venv/bin/activate
pip install .
localbiz-scout --version          # console script (pyproject [project.scripts])
python3 -m localbiz_scout --help  # module entry point (__main__.py)
```

Both the `localbiz-scout` console script and `python3 -m localbiz_scout` expose
the same four subcommands. The project requires Python 3.10+ (declared in
`pyproject.toml`); its only runtime dependency is `rapidfuzz`, supplied as a
pre-built wheel.

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
│       ├── cli.py                    # argparse CLI (analyze, review, web, remediate)
│       ├── loader.py                 # read-only CSV loading + validation
│       ├── reports.py                # findings.json / report.md / CSVs
│       ├── errors.py                 # InputError / OutputError
│       ├── remediation.py            # versioned decisions file (parse/validate)
│       ├── corrections.py            # conservative correction proposals
│       ├── review.py                 # decisions template + proposal listing
│       ├── application.py            # apply decisions + audit model
│       ├── export.py                 # safe remediated.csv + audit JSON export
│       ├── ai/
│       │   ├── __init__.py           # public AI adjudication surface
│       │   ├── client.py             # local Ollama HTTP client (stdlib)
│       │   └── adjudicator.py        # prompt + strict verdict schema
│       ├── web/                      # local-only web UI (stdlib http.server)
│       │   ├── __init__.py
│       │   ├── server.py             # loopback-only server, routes, limits
│       │   └── view.py               # in-memory scan entry point + HTML
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
    ├── test_web.py
    ├── test_remediation.py
    ├── test_corrections.py
    ├── test_review.py
    ├── test_apply_decisions.py
    ├── test_export.py
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

## Reviewing proposals (review)

Before applying anything, use `review` to see exactly which corrections exist
and to get an editable starting point for the decisions file. `review` is
read-only: it never applies a correction and never changes the input.

```
PYTHONPATH=src python3 -m localbiz_scout review \
  --input data/sample_businesses.csv \
  --out reports/review
```

It writes two files into `--out` (created if needed):

| File | Contents |
| --- | --- |
| `decisions_template.json` | A version-1 decisions document listing **every** current correction proposal and **every** duplicate candidate, each set to `"deferred"`. An unedited template applies nothing. |
| `corrections_review.csv` | One human-readable row per proposal: `business_id, field, rule_id, original_value, proposed_value, rationale`. |

The intended loop is **`analyze` → `review` → edit the template → `remediate`**:
open `decisions_template.json`, change the decision for the changes you approve
to `"accepted"` (leaving the rest `"deferred"` or marking them `"rejected"`),
then pass the edited file to `remediate`. Because the template already contains
the exact `rule_id` and `proposed_value` values, `remediate` accepts it without
hand-copying or guessing.

`review` is deterministic (no timestamps), prints only counts and output paths
(never record contents), and refuses to write over the input CSV.

`review` will **not overwrite existing artifacts**: if `decisions_template.json`
or `corrections_review.csv` already exists in `--out`, the command refuses to
write anything and exits `2`, so an edited template is never silently replaced.
All targets are checked *before* any file is written, and the pair is published
atomically (written to temporary files, then swapped in), so a conflict or
failure cannot leave a half-written pair. To intentionally regenerate the
artifacts, pass `--force`:

```
PYTHONPATH=src python3 -m localbiz_scout review \
  --input data/sample_businesses.csv \
  --out reports/review --force
```

`--force` only replaces the two `review` artifacts; it can never overwrite the
input file or any unrelated file. Exit codes match the rest of the CLI: `0` on
success, `2` for an invalid input or an existing output that was not forced.

## Human-reviewed remediation (opt-in)

`remediate` is a separate, opt-in command. `analyze` never writes back to any
record; `remediate` only ever applies corrections that a **human explicitly
accepted** in a decisions file, and it writes a *new* CSV — the input is never
modified. An accepted correction must match a generated proposal exactly, so the
simplest way to author a decisions file is to start from the `review` template
above.

```
PYTHONPATH=src python3 -m localbiz_scout remediate \
  --input data/sample_businesses.csv \
  --decisions decisions.json \
  --out reports/remediated
```

It writes two files into `--out` (created if needed):

| File | Contents |
| --- | --- |
| `remediated.csv` | A copy of the input with only accepted corrections applied. Column order and record order are preserved. UTF-8, standard `csv` line endings. |
| `remediation_audit.json` | One audit entry per generated proposal (decision, applied flag, original/proposed values, rationales) plus a separate duplicate-decision summary. No source records and no timestamps, so output is byte-stable. |

The command prints a one-line-per-dimension summary (proposed, accepted,
rejected, deferred, undecided, applied, unapplied, plus duplicate decisions); it
never prints record contents. Exit codes match the rest of the CLI: `0` on
success, `2` for invalid decisions, unsafe paths, or a failed export.

### Decisions file (version 1)

The decisions file is a single JSON object. `version` is required and must be
`1`; `corrections` and `duplicates` are optional lists. Unknown keys, wrong
types, unsupported decision values, self-pairs, and duplicate entries are
rejected.

```json
{
  "version": 1,
  "corrections": [
    {
      "business_id": "biz-0002",
      "field": "name",
      "rule_id": "correct_name_whitespace",
      "proposed_value": "Blue Bottle Coffee",
      "decision": "accepted",
      "rationale": "collapse repeated spaces"
    }
  ],
  "duplicates": [
    { "business_id_a": "biz-0001", "business_id_b": "biz-0009", "decision": "confirmed" }
  ]
}
```

- Correction `decision` is one of `accepted`, `rejected`, `deferred`. Only
  `accepted` applies a change; a proposal with no decision stays unchanged
  (`not_decided` in the audit).
- A `confirmed` duplicate is a **human note only**. It is never a merge or
  delete instruction and never changes a record; `rejected` and `deferred`
  duplicates change nothing either.

### Correction allowlist

Remediation applies a deliberately small allowlist of pure formatting fixes.
Anything else (address, city, website, email, generic normalization, AI
corrections) is out of scope.

| Rule id | Field | Transformation |
| --- | --- | --- |
| `correct_name_whitespace` | `name` | Trim and collapse whitespace; case/punctuation preserved. |
| `correct_state_uppercase` | `state` | Uppercase exactly two ASCII letters (no geographic validation). |
| `correct_postal_zip4` | `postal_code` | Exactly nine ASCII digits → `#####-####`. |
| `correct_phone_us` | `phone` | Exactly ten digits (after the documented leading-`1` rule) → `###-###-####`. |

### Safety guarantees and limitations

- **The input file is never modified or overwritten**, even with `--force`.
  An output target that resolves to (or is a symlink to) the input is refused.
- Existing output artifacts are refused unless `--force` is given; `--force`
  replaces only the two output artifacts.
- The corrected CSV is written to a temporary file in the destination
  directory, **reloaded with the same loader**, and checked before anything is
  published: column names/order, record count, record-id sequence, that accepted
  corrections landed in the right cells, and that rejected/deferred/undecided
  and non-allowlisted cells are unchanged.
- Publishing replaces each artifact with `os.replace` (atomic per file) and
  rolls back on failure: a newly published file is removed and any pre-existing
  file is restored from a backup. **Replacing two files is not a single
  filesystem transaction**, so true all-or-nothing atomicity across both
  artifacts is not guaranteed; if a rollback step itself fails, the command
  reports failure and leaves what it could not undo.
- This is a local, best-effort safety model for a small tool. It is not a
  claim of complete security or that every possible filesystem failure is
  impossible.

## Web interface (local only)

The same deterministic scan is available through a small web UI built on the
Python standard library only (`http.server`), so it adds no dependencies and
needs no network access.

```
PYTHONPATH=src python3 -m localbiz_scout web
# open http://127.0.0.1:8000/ in a browser
```

- The server binds to `127.0.0.1` (loopback) by default. Non-loopback hosts are
  **refused**; use `--host`/`--port` to choose a different loopback address or
  port, e.g. `PYTHONPATH=src python3 -m localbiz_scout web --port 8080`.
- Upload a CSV of up to **5 MiB** with a required `business_id` column, or click
  **Run bundled sample** to scan `data/sample_businesses.csv`. The page lists the
  recognized optional columns.
- The results page shows the record count, findings by severity and rule, and
  duplicate candidates with their evidence and thresholds. All values derived
  from the CSV are HTML-escaped before rendering.
- The optional `--ai` adjudicator is **not** part of the web scan; the web UI is
  deterministic only.

Unlike `analyze`, the web UI writes no files: the upload is parsed in memory,
scanned, and discarded. Uploaded records are **not** sent to any external
service and are **not** persisted.

### Web limitations and trade-offs

- Findings are rule-based formatting signals, and duplicate results are
  unreviewed **candidates**, not confirmed duplicates. The web UI performs no
  merging or correction.
- The server is intentionally **unauthenticated and loopback-only**; it must not
  be exposed on a network. It refuses non-loopback binds for that reason.
- CSRF protection is a per-process double-submit token embedded in the form plus
  a loopback-only origin check. This is proportionate for a stateless local tool
  but is **not** a substitute for authentication or for running behind a trusted
  reverse proxy.
- The `Host` header is validated against loopback names and addresses
  (`127.0.0.0/8`, `::1`, `localhost`, case-insensitively) using a purely
  syntactic check (no DNS resolution). This blocks the common DNS-rebinding
  case where a hostile name resolves to loopback; it is not a claim of complete
  rebinding immunity, and a request that repeats the `Host` header is rejected.
- Only plain `Content-Length` framing is accepted: `Transfer-Encoding` (e.g.
  `chunked`) is refused, the media type must be exactly `multipart/form-data`,
  and a form that supplies a security-relevant field (`csrf_token`, `action`)
  more than once, or more than one file, is rejected rather than silently
  taking one value.
- Sockets carry an idle read timeout (30s default), and request bodies are read
  against a total wall-clock deadline as well, so a client that stalls — or
  dribbles small chunks just under the idle timeout — cannot hold a worker
  thread open indefinitely. This bounds the common slow-read case; it is not a
  general defence against every local resource-exhaustion scenario.
- IPv6 loopback (`::1`) binding is supported when the host platform allows it;
  the automated test suite reports it as unverified where the environment
  cannot bind `::1`.
- Uploaded content is validated structurally (CSV header, required
  `business_id`, consistent row width); the file extension is only a coarse
  filter.
- Only a fixed, bundled sample file is ever served from disk; request paths are
  never mapped to filesystem paths.

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
- Duplicate detection compares every unordered pair of records, so its cost grows
  roughly with the square of the record count. This is fine for the small
  datasets the tool targets, but a very large input (thousands of rows) will be
  noticeably slower; `analyze`, `review` and `remediate` all share this cost.
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
