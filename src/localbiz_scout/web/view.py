"""Rendering and the in-memory scan entry point for the local web interface.

This module is deliberately free of I/O. :func:`analyze_upload` reuses the
existing loader and rule engine (no rules are re-implemented here), and the
``render_*`` helpers turn the resulting structures into escaped HTML.

All dynamic values pass through :func:`html.escape`; nothing is emitted raw.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import Any

from ..errors import InputError
from ..loader import load_records_from_text
from ..reports import SEVERITY_ORDER, build_metadata, severity_for
from ..rules import scan
from ..rules.config import DEFAULT_CONFIG, RuleConfig

__all__ = [
    "DuplicateView",
    "FindingView",
    "ScanView",
    "analyze_upload",
    "render_error_page",
    "render_results_page",
    "render_upload_page",
]

# Human-readable label used only inside error messages for in-memory input.
_UPLOAD_SOURCE = "uploaded CSV"


@dataclass(frozen=True)
class FindingView:
    """A single finding, ready for display."""

    rule_id: str
    severity: str
    business_id: str
    field: str | None
    message: str


@dataclass(frozen=True)
class DuplicateView:
    """A duplicate *candidate* pair, ready for display."""

    business_id_a: str
    business_id_b: str
    reason: str
    similarity: float
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class ScanView:
    """Structured, presentation-ready result of a deterministic scan."""

    record_count: int
    findings: tuple[FindingView, ...]
    findings_by_rule: Mapping[str, int]
    findings_by_severity: Mapping[str, int]
    duplicate_count: int
    duplicates: tuple[DuplicateView, ...]
    duplicates_by_reason: Mapping[str, int]
    thresholds: Mapping[str, float]
    columns: tuple[str, ...]
    present_fields: tuple[str, ...]
    missing_optional_fields: tuple[str, ...]
    limitations: tuple[str, ...]

    @property
    def finding_count(self) -> int:
        return len(self.findings)


def _decode_csv_bytes(csv_bytes: bytes) -> str:
    """Decode upload bytes as UTF-8 (tolerating a BOM), else raise InputError."""

    if not isinstance(csv_bytes, (bytes, bytearray)):
        raise InputError("expected raw CSV bytes")
    try:
        return bytes(csv_bytes).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InputError(
            f"uploaded data is not valid UTF-8 text ({error})"
        ) from error


def analyze_upload(
    csv_bytes: bytes,
    config: RuleConfig | None = None,
) -> ScanView:
    """Run the deterministic pipeline over in-memory CSV ``csv_bytes``.

    Uses the same loader and rules as the CLI: no schema is invented, no source
    record is repaired, and no report or temporary file is written. The optional
    AI adjudicator is never invoked. Raises :class:`InputError` for malformed
    input; the message is safe to show to the user.
    """

    config = config or DEFAULT_CONFIG
    text = _decode_csv_bytes(csv_bytes)
    loaded = load_records_from_text(text, config.id_field, source=_UPLOAD_SOURCE)
    result = scan(loaded.records, config)
    metadata = build_metadata(loaded, result, config)

    findings = tuple(
        FindingView(
            rule_id=finding.rule_id,
            severity=severity_for(finding.rule_id),
            business_id=finding.business_id,
            field=finding.field,
            message=finding.message,
        )
        for finding in result.findings
    )
    duplicates = tuple(
        DuplicateView(
            business_id_a=candidate.business_id_a,
            business_id_b=candidate.business_id_b,
            reason=candidate.reason,
            similarity=candidate.similarity,
            evidence=dict(candidate.evidence),
        )
        for candidate in result.duplicate_candidates
    )

    return ScanView(
        record_count=metadata["record_count"],
        findings=findings,
        findings_by_rule=dict(metadata["findings_by_rule"]),
        findings_by_severity=dict(metadata["findings_by_severity"]),
        duplicate_count=metadata["duplicate_candidate_count"],
        duplicates=duplicates,
        duplicates_by_reason=dict(metadata["duplicates_by_reason"]),
        thresholds=dict(metadata["thresholds"]),
        columns=tuple(metadata["columns"]),
        present_fields=tuple(metadata["present_fields"]),
        missing_optional_fields=tuple(metadata["missing_optional_fields"]),
        limitations=tuple(metadata["notes"]),
    )


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 1rem 3rem;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5; color: #1b1b1b; background: #fafafa;
}
header.site { max-width: 60rem; margin: 0 auto; padding: 1.25rem 0 .5rem; }
header.site h1 { font-size: 1.35rem; margin: 0; }
header.site p { margin: .25rem 0 0; color: #555; font-size: .9rem; }
main { max-width: 60rem; margin: 0 auto; }
section {
  background: #fff; border: 1px solid #e2e2e2; border-radius: 8px;
  padding: 1rem 1.25rem; margin: 1rem 0;
}
h2 { font-size: 1.1rem; margin: 0 0 .75rem; }
h3 { font-size: .95rem; margin: 1rem 0 .35rem; }
p.note { color: #555; font-size: .9rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
.tablewrap { overflow-x: auto; }
th, td { border: 1px solid #e2e2e2; padding: .4rem .55rem; text-align: left; vertical-align: top; }
th { background: #f3f3f3; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: .05rem .45rem; border-radius: 999px; font-size: .78rem; border: 1px solid #ccc; }
.badge.medium { background: #fff4e0; border-color: #e6b866; color: #7a5200; }
.badge.low { background: #eef0f2; border-color: #cbd2d8; color: #445; }
code { background: #f1f1f1; padding: .05rem .3rem; border-radius: 4px; font-size: .85em; }
dl.evidence { margin: .35rem 0 0; font-size: .85rem; }
dl.evidence dt { font-weight: 600; }
dl.evidence dd { margin: 0 0 .2rem; }
form.upload { display: grid; gap: .6rem; justify-items: start; }
input[type=file] { padding: .35rem; border: 1px solid #ccc; border-radius: 6px; background: #fff; max-width: 100%; }
button {
  font: inherit; padding: .45rem .9rem; border-radius: 6px; cursor: pointer;
  border: 1px solid #1f6feb; background: #1f6feb; color: #fff;
}
button.secondary { background: #fff; color: #1f6feb; }
.alert { border: 1px solid #e0a3a3; background: #fdecec; color: #7a1f1f; padding: .7rem .9rem; border-radius: 6px; }
.empty { color: #555; font-style: italic; }
footer.site { max-width: 60rem; margin: 1rem auto 0; color: #777; font-size: .8rem; }
@media (max-width: 40rem) {
  th, td { padding: .35rem; font-size: .85rem; }
  section { padding: .85rem; }
}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="referrer" content="no-referrer">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n<body>\n"
        '<header class="site"><h1>LocalBiz Scout</h1>'
        "<p>Deterministic, offline duplicate-candidate and data-quality review. "
        "Uploaded records are processed locally on this machine only.</p></header>\n"
        f'<main id="main">{body}</main>\n'
        '<footer class="site">Local-only tool. Findings are rule-based; duplicate '
        "matches are candidates for human review, never confirmed duplicates.</footer>\n"
        "</body>\n</html>\n"
    )


def _upload_form(csrf_token: str, has_sample: bool) -> str:
    sample_button = (
        '<button type="submit" class="secondary" name="action" value="sample" '
        'formnovalidate>Run bundled sample</button>'
        if has_sample
        else ""
    )
    return (
        '<form class="upload" method="post" action="/scan" enctype="multipart/form-data">\n'
        f'  <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">\n'
        '  <label for="csvfile">Business records (CSV, up to 5 MiB)</label>\n'
        '  <input type="file" id="csvfile" name="csv" accept=".csv,text/csv">\n'
        '  <div><button type="submit" name="action" value="scan">Scan CSV</button>\n'
        f"  {sample_button}</div>\n"
        "</form>\n"
    )


def render_upload_page(
    *,
    csrf_token: str,
    has_sample: bool,
    filename: str | None = None,
    error: str | None = None,
) -> str:
    """Render the landing/upload page, optionally with a validation error."""

    parts: list[str] = []
    if error is not None:
        parts.append(f'<div class="alert" role="alert">{escape(error)}</div>')
    if filename is not None:
        parts.append(f'<p class="note">Previously uploaded: <code>{escape(filename)}</code></p>')

    parts.append(
        "<section><h2>Analyze a CSV</h2>"
        "<p>Choose a CSV file with a required <code>business_id</code> column. "
        "The file is parsed in memory; it is never written to disk.</p>"
        + _upload_form(csrf_token, has_sample)
    )
    if has_sample:
        parts.append(
            '<p class="note">No file handy? Use <strong>Run bundled sample</strong> '
            'or download <a href="/sample.csv">sample_businesses.csv</a>.</p>'
        )
    parts.append(
        "</section>\n<section><h2>Expected schema</h2>"
        "<p>Only <code>business_id</code> is required. Recognized optional columns "
        "are <code>name</code>, <code>address</code>, <code>city</code>, "
        "<code>state</code>, <code>postal_code</code>, <code>phone</code>, "
        "<code>website</code>, and <code>email</code>; other columns are ignored "
        "by the rules. Rows with a different number of fields than the header are "
        "rejected as malformed.</p>"
        "<p>Findings are rule-based formatting signals; duplicate results are "
        "unreviewed candidates. The optional AI mode is not part of this web scan.</p>"
        "</section>"
    )
    return _page("LocalBiz Scout", "\n".join(parts))


def _counts_table(caption: str, counts: Mapping[str, int], key_header: str) -> str:
    if not counts:
        return f'<p class="empty">No {escape(caption)}.</p>'
    rows = "".join(
        f'<tr><td><code>{escape(str(key))}</code></td>'
        f'<td class="num">{count}</td></tr>'
        for key, count in sorted(counts.items())
    )
    return (
        '<div class="tablewrap"><table>'
        f"<caption class=\"note\">{escape(caption)}</caption>"
        f'<thead><tr><th scope="col">{escape(key_header)}</th>'
        '<th scope="col" class="num">Count</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def _render_findings(view: ScanView) -> str:
    if not view.findings:
        return '<p class="empty">No findings were produced.</p>'

    rows = []
    for finding in view.findings:
        field = escape(finding.field) if finding.field else "&ndash;"
        rows.append(
            "<tr>"
            f'<td><code>{escape(finding.rule_id)}</code></td>'
            f'<td><span class="badge {escape(finding.severity)}">'
            f"{escape(finding.severity)}</span></td>"
            f"<td>{escape(finding.business_id)}</td>"
            f"<td>{field}</td>"
            f"<td>{escape(finding.message)}</td>"
            "</tr>"
        )
    return (
        '<div class="tablewrap"><table>'
        '<thead><tr><th scope="col">Finding</th><th scope="col">Severity</th>'
        '<th scope="col">Record</th><th scope="col">Field</th>'
        '<th scope="col">Explanation</th></tr></thead>'
        "<tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_evidence(evidence: Mapping[str, Any]) -> str:
    if not evidence:
        return "&ndash;"
    items: list[str] = []
    for key in sorted(evidence):
        value = evidence[key]
        if isinstance(value, (list, tuple)):
            rendered = ", ".join(escape(str(item)) for item in value) or "&ndash;"
        elif isinstance(value, Mapping):
            rendered = ", ".join(
                f"{escape(str(k))}={escape(str(v))}" for k, v in sorted(value.items())
            ) or "&ndash;"
        else:
            rendered = escape(str(value))
        items.append(f"<dt>{escape(str(key))}</dt><dd>{rendered}</dd>")
    return '<dl class="evidence">' + "".join(items) + "</dl>"


def _render_duplicates(view: ScanView) -> str:
    if not view.duplicates:
        return '<p class="empty">No duplicate candidates were found.</p>'

    rows = []
    for candidate in view.duplicates:
        rows.append(
            "<tr>"
            f"<td>{escape(candidate.business_id_a)}</td>"
            f"<td>{escape(candidate.business_id_b)}</td>"
            f"<td><code>{escape(candidate.reason)}</code></td>"
            f'<td class="num">{candidate.similarity:g}</td>'
            f"<td>{_render_evidence(candidate.evidence)}</td>"
            "</tr>"
        )
    return (
        '<p class="note">These pairs are <strong>candidates for human review</strong>, '
        "not confirmed duplicates. LocalBiz Scout never merges or deletes records.</p>"
        '<div class="tablewrap"><table>'
        '<thead><tr><th scope="col">Record A</th><th scope="col">Record B</th>'
        '<th scope="col">Reason</th><th scope="col" class="num">Similarity</th>'
        '<th scope="col">Evidence</th></tr></thead>'
        "<tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_severity_table(view: ScanView) -> str:
    rows = "".join(
        f"<tr><td>{escape(severity)}</td>"
        f'<td class="num">{view.findings_by_severity.get(severity, 0)}</td></tr>'
        for severity in SEVERITY_ORDER
    )
    return (
        '<div class="tablewrap"><table>'
        '<thead><tr><th scope="col">Severity</th>'
        '<th scope="col" class="num">Findings</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def render_results_page(
    view: ScanView,
    *,
    filename: str,
    csrf_token: str,
    has_sample: bool = True,
) -> str:
    """Render the scan summary, findings, and duplicate candidates."""

    thresholds = ", ".join(
        f"{escape(str(key))} &ge; {value:g}"
        for key, value in sorted(view.thresholds.items())
    )
    missing = (
        ", ".join(f"<code>{escape(field)}</code>" for field in view.missing_optional_fields)
        or "<em>none</em>"
    )

    body = (
        "<section><h2>Scan summary</h2>"
        f'<p>Source: <code>{escape(filename)}</code></p>'
        '<div class="tablewrap"><table>'
        "<thead><tr><th scope=\"col\">Metric</th>"
        "<th scope=\"col\" class=\"num\">Value</th></tr></thead><tbody>"
        f'<tr><td>Records analyzed</td><td class="num">{view.record_count}</td></tr>'
        f'<tr><td>Record-level findings</td><td class="num">{view.finding_count}</td></tr>'
        f'<tr><td>Duplicate-pair candidates</td><td class="num">{view.duplicate_count}</td></tr>'
        "</tbody></table></div>"
        '<p class="note">Findings apply to individual records; duplicate-pair '
        "candidates are separate and always require human review.</p>"
        f'<p class="note">Fuzzy thresholds: {thresholds}. '
        f"Missing optional columns: {missing}.</p>"
        "</section>"
        "<section><h2>Findings by severity</h2>"
        + _render_severity_table(view)
        + "</section>"
        "<section><h2>Findings by rule</h2>"
        + _counts_table("Findings by rule", view.findings_by_rule, "Rule")
        + "</section>"
        "<section><h2>Findings</h2>"
        + _render_findings(view)
        + "</section>"
        "<section><h2>Duplicate candidates</h2>"
        + _render_duplicates(view)
        + "</section>"
        "<section><h2>Limitations</h2><ul>"
        + "".join(f"<li>{escape(note)}</li>" for note in view.limitations)
        + "</ul></section>"
        "<section><h2>Run another scan</h2>"
        + _upload_form(csrf_token, has_sample)
        + "</section>"
    )
    return _page("LocalBiz Scout - results", body)


def render_error_page(message: str, *, csrf_token: str, has_sample: bool = True) -> str:
    """Render a readable error page (no tracebacks, no server paths)."""

    body = (
        '<section><h2>Request could not be processed</h2>'
        f'<div class="alert" role="alert">{escape(message)}</div>'
        "</section>"
        "<section><h2>Try again</h2>"
        + _upload_form(csrf_token, has_sample)
        + "</section>"
    )
    return _page("LocalBiz Scout - error", body)
