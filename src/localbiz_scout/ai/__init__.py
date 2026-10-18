"""Optional local AI adjudication of ambiguous duplicate candidates.

This subpackage is entirely opt-in (``--ai``). It talks only to a local Ollama
endpoint, sends only non-contact fields for ambiguous candidate pairs, and
degrades to the deterministic result on any failure. It never edits, merges, or
rewrites source records.
"""

from __future__ import annotations

from .adjudicator import (
    AI_INPUT_FIELDS,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    AIReport,
    AICandidateResult,
    AIVerdict,
    adjudicate_candidates,
    build_prompt,
    build_record_view,
    is_ambiguous,
    parse_verdict,
)
from .client import (
    AIClient,
    AIError,
    AIServiceError,
    AITimeoutError,
    AIUnavailableError,
    HttpOllamaClient,
)

__all__ = [
    "AI_INPUT_FIELDS",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "AIReport",
    "AICandidateResult",
    "AIVerdict",
    "adjudicate_candidates",
    "build_prompt",
    "build_record_view",
    "is_ambiguous",
    "parse_verdict",
    "AIClient",
    "AIError",
    "AIServiceError",
    "AITimeoutError",
    "AIUnavailableError",
    "HttpOllamaClient",
]
