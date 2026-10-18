"""Command-line interface for LocalBiz Scout.

Currently exposes a single ``analyze`` subcommand that loads a business CSV,
runs the deterministic rule engine, and writes review reports. The CLI contains
no rule logic of its own; it orchestrates :mod:`localbiz_scout.loader`,
:mod:`localbiz_scout.rules`, and :mod:`localbiz_scout.reports`.
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
from .errors import LocalBizScoutError
from .loader import load_records
from .reports import write_reports
from .rules import scan
from .rules.config import DEFAULT_CONFIG


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
