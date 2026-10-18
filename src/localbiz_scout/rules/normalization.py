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
from dataclasses import dataclass

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


# Unit markers are only recognized after they have been canonicalized (see
# :data:`STREET_ABBREVIATIONS`): ``suite`` becomes ``ste`` and ``apartment``
# becomes ``apt``, while ``unit`` maps to itself.
_UNIT_MARKERS = frozenset({"ste", "unit", "apt"})

# A leading house number is digits with at most one trailing letter (``12``,
# ``18``, ``100``, ``12a``). Ranges (``100-102``), fractions (``12.5``) and
# anything non-numeric are deliberately left unrecognized.
_HOUSE_NUMBER_RE = re.compile(r"(\d{1,6})([a-z]?)")

# A unit value is a short alphanumeric token, optionally with a single hyphen
# (``2``, ``2b``, ``12-14``).
_UNIT_VALUE_RE = re.compile(r"[a-z0-9]{1,6}(?:-[a-z0-9]{1,4})?")

# Tokens that describe the street itself rather than a unit; seeing one after
# a marker means the "unit" was not actually present.
_NON_UNIT_TOKENS = frozenset(STREET_ABBREVIATIONS) | frozenset(STREET_ABBREVIATIONS.values())


@dataclass(frozen=True)
class AddressComponents:
    """Comparable address parts, or ``None`` when they cannot be parsed.

    A ``None`` component means "unknown", never "absent": callers must treat it
    as unparsed and must not compare it against a known value.
    """

    house_number: str | None = None
    unit: str | None = None


def _canonical_number(token: str) -> str:
    """Drop leading zeros from a numeric token, keeping any letter suffix."""

    match = re.fullmatch(r"(\d+)([a-z]?)", token)
    if match is None:
        return token
    return str(int(match.group(1))) + match.group(2)


def parse_address_components(value: object) -> AddressComponents:
    """Best-effort extraction of a leading house number and a unit.

    Parsing is intentionally conservative and US-leaning: only components that
    are syntactically unambiguous are returned; everything else stays ``None``
    so that callers never compare a manufactured value. Specifically:

    * the house number must be the *first* token and purely digits with an
      optional single trailing letter;
    * a unit must follow a recognized marker (``ste``/``unit``/``apt``) as a
      short alphanumeric token that is not itself a street/direction word.

    The helper is pure: it reads no global state and returns only a value
    object.
    """

    tokens = [token for token in _ADDRESS_SPLIT_RE.split(normalize_address(value)) if token]

    house_number: str | None = None
    if tokens:
        full = _HOUSE_NUMBER_RE.fullmatch(tokens[0])
        if full is not None:
            house_number = str(int(full.group(1))) + full.group(2)

    unit: str | None = None
    for index, token in enumerate(tokens):
        if token not in _UNIT_MARKERS:
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if (
            following is not None
            and following not in _NON_UNIT_TOKENS
            and _UNIT_VALUE_RE.fullmatch(following) is not None
        ):
            unit = _canonical_number(following)
        break

    return AddressComponents(house_number=house_number, unit=unit)
