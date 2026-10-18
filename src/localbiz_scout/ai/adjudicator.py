"""Optional AI adjudication of *ambiguous* duplicate candidates.

The adjudicator runs **only** on candidate pairs the deterministic engine marks
as ambiguous (currently ``reason == "fuzzy"``). It never sees whole files, never
edits/merges/deletes records, and never overrides high-confidence reasons.

Design rules:

* Strict JSON verdict schema; anything malformed is rejected.
* Any AI failure (unavailable, timeout, model error, bad output) degrades to
  the deterministic result for that pair, and is reported as such.
* Only non-contact business fields are sent to the model; phone and email are
  deliberately excluded.
* Prompts are never stored or logged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..rules.duplicates import REASON_FUZZY
from ..rules.findings import DuplicateCandidate
from ..rules.normalization import as_text
from .client import AIClient, AIError, AIServiceError, AITimeoutError, AIUnavailableError

DEFAULT_MODEL = "gemma3:1b"
DEFAULT_TIMEOUT = 30.0

# Non-contact fields only. Phone/email are intentionally excluded.
AI_INPUT_FIELDS: tuple[str, ...] = (
    "name",
    "address",
    "city",
    "state",
    "postal_code",
    "category",
    "website",
)

VALID_SAME_ENTITY = ("yes", "no", "uncertain")

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_INVALID = "invalid_response"

VERDICT_OK = "ok"

SYSTEM_PROMPT = (
    "You are a conservative data-quality assistant. You compare two business "
    "records and decide whether they describe the SAME real-world business.\n"
    "Rules:\n"
    "- Reply with ONLY a JSON object and no other text.\n"
    '- "same_entity" must be exactly "yes", "no", or "uncertain".\n'
    '- "reason" must be one short sentence grounded only in the supplied fields.\n'
    '- "signals" must be a list of the supplied field names you relied on.\n'
    "- Use only these field names in signals: "
    + ", ".join(AI_INPUT_FIELDS)
    + ".\n"
    "- If the evidence is insufficient to be confident, answer \"uncertain\".\n"
    "- Treat differing suite/unit numbers or house numbers as evidence of "
    "DIFFERENT businesses.\n"
    "- Never merge or edit records; only classify the pair.\n"
    'JSON shape: {"same_entity": "yes|no|uncertain", "reason": "...", '
    '"signals": ["name", "address"]}'
)


@dataclass(frozen=True)
class AIVerdict:
    same_entity: str
    reason: str
    signals: tuple[str, ...]


@dataclass(frozen=True)
class AICandidateResult:
    business_id_a: str
    business_id_b: str
    deterministic_reason: str
    ai_status: str
    same_entity: str | None
    ai_reason: str | None
    signals: tuple[str, ...]
    effective_positive: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_id_a": self.business_id_a,
            "business_id_b": self.business_id_b,
            "deterministic_reason": self.deterministic_reason,
            "ai_status": self.ai_status,
            "same_entity": self.same_entity,
            "ai_reason": self.ai_reason,
            "signals": list(self.signals),
            "effective_positive": self.effective_positive,
            "error": self.error,
        }


@dataclass(frozen=True)
class AIReport:
    model: str
    status: str
    message: str
    results: tuple[AICandidateResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "status": self.status,
            "message": self.message,
            "adjudicated_count": len(self.results),
            "results": [result.to_dict() for result in self.results],
        }


class InvalidResponseError(Exception):
    """The model response did not match the required JSON schema."""


def is_ambiguous(candidate: DuplicateCandidate) -> bool:
    """Ambiguous = the deterministic reason is threshold-based (fuzzy)."""

    return candidate.reason == REASON_FUZZY


def build_record_view(record: Mapping[str, Any]) -> dict[str, str]:
    return {field: as_text(record.get(field)) for field in AI_INPUT_FIELDS}


def build_prompt(
    candidate: DuplicateCandidate,
    record_a: Mapping[str, Any],
    record_b: Mapping[str, Any],
) -> tuple[str, str]:
    payload = {
        "record_a": build_record_view(record_a),
        "record_b": build_record_view(record_b),
    }
    user = (
        "Compare these two business records and decide whether they refer to "
        "the same real-world business.\n"
        + json.dumps(payload, ensure_ascii=True, sort_keys=True)
        + "\nReply with the JSON object only."
    )
    return SYSTEM_PROMPT, user


def parse_verdict(content: str, allowed_signals: Sequence[str]) -> AIVerdict:
    """Parse and strictly validate a model response."""

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as error:
        raise InvalidResponseError(f"response is not valid JSON: {error}") from error

    if not isinstance(parsed, dict):
        raise InvalidResponseError("response JSON is not an object")

    same_entity = parsed.get("same_entity")
    if same_entity not in VALID_SAME_ENTITY:
        raise InvalidResponseError(
            f"same_entity must be one of {VALID_SAME_ENTITY}, got {same_entity!r}"
        )

    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidResponseError("reason must be a non-empty string")

    signals = parsed.get("signals")
    if not isinstance(signals, list) or not all(isinstance(s, str) for s in signals):
        raise InvalidResponseError("signals must be a list of strings")

    allowed = set(allowed_signals)
    unsupported = [signal for signal in signals if signal not in allowed]
    if unsupported:
        raise InvalidResponseError(f"unsupported signals: {unsupported}")

    return AIVerdict(
        same_entity=same_entity,
        reason=reason.strip(),
        signals=tuple(dict.fromkeys(signals)),
    )


def adjudicate_candidates(
    candidates: Sequence[DuplicateCandidate],
    records_by_id: Mapping[str, Mapping[str, Any]],
    client: AIClient,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
) -> AIReport:
    """Adjudicate the ambiguous candidates, degrading gracefully on failure."""

    ambiguous = [c for c in candidates if is_ambiguous(c)]

    results: list[AICandidateResult] = []
    service_down = False

    for candidate in ambiguous:
        if service_down:
            results.append(
                _fallback(candidate, STATUS_UNAVAILABLE, "service unavailable (short-circuited)")
            )
            continue

        record_a = records_by_id.get(candidate.business_id_a)
        record_b = records_by_id.get(candidate.business_id_b)
        if record_a is None or record_b is None:
            results.append(
                _fallback(candidate, STATUS_UNAVAILABLE, "record not found for pair")
            )
            continue

        system, user = build_prompt(candidate, record_a, record_b)

        try:
            content = client.chat(model=model, system=system, user=user, timeout=timeout)
        except AIUnavailableError as error:
            # Connection failure means the service is down: stop trying.
            results.append(
                _fallback(candidate, STATUS_UNAVAILABLE, f"service unavailable: {error}")
            )
            service_down = True
        except AITimeoutError as error:
            # A timeout is per-request; other pairs may still succeed.
            results.append(_fallback(candidate, STATUS_UNAVAILABLE, f"timeout: {error}"))
        except AIServiceError as error:
            results.append(_fallback(candidate, STATUS_UNAVAILABLE, f"service error: {error}"))
        except AIError as error:
            results.append(_fallback(candidate, STATUS_UNAVAILABLE, f"ai error: {error}"))
        except Exception as error:  # noqa: BLE001 - never let the CLI crash
            results.append(
                _fallback(candidate, STATUS_UNAVAILABLE, f"unexpected error: {type(error).__name__}")
            )
        else:
            try:
                verdict = parse_verdict(content, AI_INPUT_FIELDS)
            except InvalidResponseError as error:
                results.append(
                    _fallback(candidate, STATUS_INVALID, f"invalid response: {error}")
                )
            else:
                results.append(
                    AICandidateResult(
                        business_id_a=candidate.business_id_a,
                        business_id_b=candidate.business_id_b,
                        deterministic_reason=candidate.reason,
                        ai_status=STATUS_OK,
                        same_entity=verdict.same_entity,
                        ai_reason=verdict.reason,
                        signals=verdict.signals,
                        # "no" demotes a candidate; everything else keeps it.
                        effective_positive=verdict.same_entity != "no",
                    )
                )

    return _build_report(model, ambiguous, results)


def _fallback(
    candidate: DuplicateCandidate, status: str, error: str
) -> AICandidateResult:
    return AICandidateResult(
        business_id_a=candidate.business_id_a,
        business_id_b=candidate.business_id_b,
        deterministic_reason=candidate.reason,
        ai_status=status,
        same_entity=None,
        ai_reason=None,
        signals=(),
        # Fall back to the deterministic decision (candidate stays positive).
        effective_positive=True,
        error=error,
    )


def _build_report(
    model: str,
    ambiguous: Sequence[DuplicateCandidate],
    results: Sequence[AICandidateResult],
) -> AIReport:
    if not ambiguous:
        return AIReport(
            model=model,
            status=STATUS_OK,
            message="no ambiguous candidates to adjudicate",
            results=(),
        )

    ok = sum(1 for r in results if r.ai_status == STATUS_OK)
    if ok == len(results):
        status = STATUS_OK
    elif ok > 0:
        status = "partial"
    else:
        status = STATUS_UNAVAILABLE

    message = (
        f"{ok}/{len(results)} ambiguous candidate(s) adjudicated by {model}; "
        f"{len(results) - ok} fell back to the deterministic result."
    )
    return AIReport(model=model, status=status, message=message, results=tuple(results))
