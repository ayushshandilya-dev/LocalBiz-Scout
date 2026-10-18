"""Text normalization helpers shared by the rules.

Only *safe formatting* differences are collapsed here:

* surrounding and repeated whitespace
* letter case
* phone punctuation and an optional leading North-American country code ``1``
* a documented set of common street/unit abbreviations

Deliberately **not** normalized: legal suffixes such as "LLC"/"Inc." and
articles such as a leading "The". Those are left intact so that near-matches
surface as fuzzy candidates for human review rather than being silently
treated as identical.
"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")
_NON_DIGIT_RE = re.compile(r"\D")
_ADDRESS_SPLIT_RE = re.compile(r"[\s,]+")

# Canonical (short) form for common street types, directions and unit words.
# Both the long and the short spelling map to the same short token.
STREET_ABBREVIATIONS: dict[str, str] = {
    "street": "st",
    "st": "st",
    "avenue": "ave",
    "ave": "ave",
    "boulevard": "blvd",
    "blvd": "blvd",
    "drive": "dr",
    "dr": "dr",
    "road": "rd",
    "rd": "rd",
    "lane": "ln",
    "ln": "ln",
    "court": "ct",
    "ct": "ct",
    "highway": "hwy",
    "hwy": "hwy",
    "suite": "ste",
    "ste": "ste",
    "apartment": "apt",
    "apt": "apt",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}


def as_text(value: object) -> str:
    """Coerce a cell value to text, treating ``None`` as an empty string."""

    if value is None:
        return ""
    return str(value)


def is_blank(value: object) -> bool:
    """Return True for empty or whitespace-only values (and ``None``)."""

    return as_text(value).strip() == ""


def collapse_whitespace(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", as_text(value)).strip()


def normalize_text(value: object) -> str:
    """Lower-case and collapse whitespace."""

    return collapse_whitespace(value).casefold()


def normalize_name(value: object) -> str:
    return normalize_text(value)


def normalize_phone(value: object) -> str:
    """Return only the digits of a phone number.

    A single leading ``1`` (North-American country code) is dropped so that
    ``+1 512-555-0101`` and ``5125550101`` compare equal.
    """

    digits = _NON_DIGIT_RE.sub("", as_text(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_address(value: object) -> str:
    """Lower-case, collapse whitespace/punctuation and canonicalize street
    type words via :data:`STREET_ABBREVIATIONS`."""

    tokens = [token for token in _ADDRESS_SPLIT_RE.split(normalize_text(value)) if token]
    return " ".join(STREET_ABBREVIATIONS.get(token, token) for token in tokens)
