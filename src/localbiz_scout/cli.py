"""Command-line interface for LocalBiz Scout.

Exposes four subcommands:

* ``analyze`` loads a business CSV, runs the deterministic rule engine, and
  writes review reports.
* ``review`` writes an editable version-1 decisions template and a
  human-readable listing of the correction proposals for the same CSV.
* ``web`` serves a loopback-only web interface that runs the same deterministic
  scan in memory.
* ``remediate`` applies human-reviewed correction decisions and writes a
  corrected CSV plus an audit trail (opt-in; nothing is merged or deleted).

The CLI contains no rule logic of its own; it orchestrates
:mod:`localbiz_scout.loader`, :mod:`localbiz_scout.rules`,
:mod:`localbiz_scout.reports`, :mod:`localbiz_scout.review`,
:mod:`localbiz_scout.web`, and :mod:`localbiz_scout.export`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .ai import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    AIClient,
    HttpOllamaClient,
    adjudicate_candidates,
)
from .ai.client import DEFAULT_BASE_URL
from .corrections import propose_corrections
from .errors import LocalBizScoutError
from .export import OUTPUT_AUDIT_JSON, OUTPUT_CSV, remediate
from .loader import load_records
from .reports import write_reports
from .review import OUTPUT_CORRECTIONS_REVIEW, OUTPUT_DECISIONS_TEMPLATE, write_review
from .rules import scan
from .rules.config import DEFAULT_CONFIG
from .web import DEFAULT_HOST, DEFAULT_PORT, create_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localbiz_scout",
        description=(
            "Deterministic, offline data-quality scanning for business CSVs."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    analyze = subparsers.add_parser(
        "analyze",
        help="scan a CSV and write findings.json, report.md and duplicates_review.csv",
        description=(
            "Load a business CSV, run the deterministic rules, and write "
            "findings.json, report.md and duplicates_review.csv to --out."
        ),
    )
    analyze.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="path to the input business CSV (read-only; the file is not modified)",
    )
    analyze.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="output directory for the reports (created if it does not exist)",
    )

    ai_group = analyze.add_argument_group(
        "optional AI adjudication",
        "Runs only for ambiguous (fuzzy) candidates, against a local Ollama "
        "service. Disabled unless --ai is passed.",
    )
    ai_group.add_argument(
        "--ai",
        action="store_true",
        help="adjudicate ambiguous duplicate candidates with a local model",
    )
    ai_group.add_argument(
        "--ai-model",
        default=DEFAULT_MODEL,
        metavar="NAME",
        help=f"local model name (default: {DEFAULT_MODEL})",
    )
    ai_group.add_argument(
        "--ai-timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    ai_group.add_argument(
        "--ai-base-url",
        default=DEFAULT_BASE_URL,
        metavar="URL",
        help=f"local Ollama base URL (default: {DEFAULT_BASE_URL})",
    )
    analyze.set_defaults(handler=_run_analyze)

    review = subparsers.add_parser(
        "review",
        help="write an editable decisions template and correction proposals",
        description=(
            "Load a business CSV and write a version-1 decisions template "
            f"({OUTPUT_DECISIONS_TEMPLATE}) plus a human-readable listing of the "
            f"correction proposals ({OUTPUT_CORRECTIONS_REVIEW}) to --out. Every "
            "decision defaults to 'deferred', so the template applies nothing "
            "until a human edits it. The input is never modified."
        ),
    )
    review.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="path to the input business CSV (read-only; the file is not modified)",
    )
    review.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help=(
            f"output directory for {OUTPUT_DECISIONS_TEMPLATE} and "
            f"{OUTPUT_CORRECTIONS_REVIEW} (created if it does not exist)"
        ),
    )
    review.add_argument(
        "--force",
        action="store_true",
        help=(
            "replace existing review artifacts (never the input file); without "
            "this flag an existing target is left untouched and the command fails"
        ),
    )
    review.set_defaults(handler=_run_review)

    web = subparsers.add_parser(
        "web",
        help="serve a loopback-only web interface for interactive scans",
        description=(
            "Start a loopback-only web interface. An uploaded CSV is scanned in "
            "memory with the same deterministic rules as 'analyze'; nothing is "
            "written to disk and nothing is sent to an external service."
        ),
    )
    web.add_argument(
        "--host",
        default=DEFAULT_HOST,
        metavar="HOST",
        help=f"bind address; must be a loopback address (default: {DEFAULT_HOST})",
    )
    web.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"port to listen on (default: {DEFAULT_PORT})",
    )
    web.set_defaults(handler=_run_web)

    remediate_parser = subparsers.add_parser(
        "remediate",
        help="apply reviewed correction decisions and write a corrected CSV + audit",
        description=(
            "Apply a versioned decisions file to a business CSV and write a "
            f"corrected copy ({OUTPUT_CSV}) and an audit trail "
            f"({OUTPUT_AUDIT_JSON}) to --out. The input is never modified, and "
            "records are never merged or deleted."
        ),
    )
    remediate_parser.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="path to the input business CSV (read-only; the file is not modified)",
    )
    remediate_parser.add_argument(
        "--decisions",
        required=True,
        metavar="PATH",
        help="path to the versioned decisions JSON file",
    )
    remediate_parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help=(
            f"output directory for {OUTPUT_CSV} and {OUTPUT_AUDIT_JSON} "
            "(created if it does not exist)"
        ),
    )
    remediate_parser.add_argument(
        "--force",
        action="store_true",
        help="replace existing output artifacts (never the input file)",
    )
    remediate_parser.set_defaults(handler=_run_remediate)

    return parser


def _make_client(args: argparse.Namespace) -> AIClient:
    """Build the AI client. Overridable in tests to avoid a live service."""

    return HttpOllamaClient(base_url=args.ai_base_url)


def _run_analyze(args: argparse.Namespace) -> int:
    config = DEFAULT_CONFIG
    loaded = load_records(args.input, config.id_field)
    result = scan(loaded.records, config)

    ai_report = None
    if args.ai:
        records_by_id = {
            str(record.get(config.id_field, "")).strip(): record
            for record in loaded.records
        }
        ai_report = adjudicate_candidates(
            result.duplicate_candidates,
            records_by_id,
            _make_client(args),
            model=args.ai_model,
            timeout=args.ai_timeout,
        )

    paths = write_reports(loaded, result, args.out, config, ai_report)

    print(f"Analyzed {len(loaded.records)} records from {loaded.path}")
    print(
        f"Findings: {len(result.findings)} | "
        f"Duplicate candidates: {len(result.duplicate_candidates)}"
    )
    print(f"Wrote {paths['findings_json']}")
    print(f"Wrote {paths['report_md']}")
    print(f"Wrote {paths['duplicates_csv']}")
    if ai_report is not None:
        print(
            f"AI ({ai_report.model}) status: {ai_report.status} "
            f"- {ai_report.message}"
        )
        print(f"Wrote {paths['ai_verdicts_json']}")
    return 0


def _run_review(args: argparse.Namespace) -> int:
    config = DEFAULT_CONFIG
    loaded = load_records(args.input, config.id_field)
    result = scan(loaded.records, config)
    proposals = propose_corrections(loaded.records, config)

    paths = write_review(loaded, result, proposals, args.out, force=args.force)

    print(f"Reviewed {len(loaded.records)} records from {loaded.path}")
    print(
        f"Correction proposals: {len(proposals)} | "
        f"Duplicate candidates: {len(result.duplicate_candidates)}"
    )
    print(
        "All decisions default to 'deferred'; edit the template, then run "
        "'remediate'."
    )
    print(f"Wrote {paths['decisions_template']}")
    print(f"Wrote {paths['corrections_review']}")
    return 0


def _run_remediate(args: argparse.Namespace) -> int:
    result = remediate(
        args.input,
        args.decisions,
        args.out,
        force=args.force,
    )

    summary = result.summary
    print(f"Remediated {summary.total_proposals} proposals from {result.input_path}")
    print(
        f"Decisions: accepted {summary.accepted} | rejected {summary.rejected} | "
        f"deferred {summary.deferred} | undecided {summary.not_decided}"
    )
    print(
        f"Applied: {summary.applied} | unapplied: {summary.unapplied} "
        "(proposals only; records are never merged or deleted)"
    )

    duplicates = result.duplicate_counts
    print(
        f"Duplicate decisions (reported only): confirmed {duplicates.confirmed} | "
        f"rejected {duplicates.rejected} | deferred {duplicates.deferred}"
    )

    print(f"Wrote {result.csv_path}")
    print(f"Wrote {result.audit_path}")
    return 0


def _run_web(args: argparse.Namespace) -> int:
    server = create_server(args.host, args.port)
    host, port = server.server_address[0], server.server_address[1]
    display_host = f"[{host}]" if ":" in host else host
    print(f"LocalBiz Scout web interface: http://{display_host}:{port}/")
    print("Bound to loopback only; uploads are processed locally and never persisted.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        return handler(args)
    except LocalBizScoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
