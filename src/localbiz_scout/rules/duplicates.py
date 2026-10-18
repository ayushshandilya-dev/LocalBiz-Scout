"""Rule: detect duplicate and near-duplicate records.

This rule never merges or deletes anything. It emits *candidate pairs* for
human review, each with the reason it was flagged, a similarity score where
relevant, and supporting evidence.

Match strengths, strongest first:

``exact``
    Every configured identity field is byte-for-byte identical.
``normalized``
    Every configured identity field is identical after safe normalization
    (case, whitespace, phone punctuation, common street abbreviations).
``fuzzy``
    Not identical after normalization, but the normalized name *and* the
    normalized address both meet their configured similarity thresholds.

Fuzzy similarity is a heuristic, not proof that two records are the same
business; thresholds are explicit in
:class:`~localbiz_scout.rules.config.RuleConfig`.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from rapidfuzz import fuzz

from .config import RuleConfig
from .findings import DuplicateCandidate
from .normalization import (
    as_text,
    normalize_address,
    normalize_name,
    normalize_phone,
    normalize_text,
)
from .records import get_record_id

REASON_EXACT = "exact"
REASON_NORMALIZED = "normalized"
REASON_FUZZY = "fuzzy"

# Sort key so the strongest reason wins when a pair qualifies several ways.
_REASON_STRENGTH = {REASON_FUZZY: 1, REASON_NORMALIZED: 2, REASON_EXACT: 3}

_FIELD_NORMALIZERS: dict[str, Callable[[object], str]] = {
    "name": normalize_name,
    "address": normalize_address,
    "phone": normalize_phone,
}


def _normalize_field(field_name: str, value: object) -> str:
    normalizer = _FIELD_NORMALIZERS.get(field_name, normalize_text)
    return normalizer(value)


def find_duplicate_candidates(
    records: Sequence[Mapping[str, Any]],
    config: RuleConfig,
) -> list[DuplicateCandidate]:
    identity_fields = config.duplicate_identity_fields

    ids: list[str] = []
    raw_values: list[tuple[str, ...]] = []
    norm_values: list[tuple[str, ...]] = []
    named_norm: list[dict[str, str]] = []

    for index, record in enumerate(records):
        ids.append(get_record_id(record, config, index))
        raw_values.append(tuple(as_text(record.get(f)) for f in identity_fields))
        norm_tuple = tuple(_normalize_field(f, record.get(f)) for f in identity_fields)
        norm_values.append(norm_tuple)
        named_norm.append(dict(zip(identity_fields, norm_tuple)))

    # Keyed by the ordered pair of ids so each unordered pair is emitted once
    # and only the strongest reason survives.
    candidates: dict[tuple[str, str], DuplicateCandidate] = {}

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            id_a, id_b = ids[i], ids[j]
            if id_a == id_b:
                # Same identifier means the same record; never pair with self.
                continue

            raw_a, raw_b = raw_values[i], raw_values[j]
            norm_a, norm_b = norm_values[i], norm_values[j]
            named_a, named_b = named_norm[i], named_norm[j]

            # Keep a stable ordering so the emitted pair is always (smaller,
            # larger) regardless of the input order.
            if id_a > id_b:
                id_a, id_b = id_b, id_a
                raw_a, raw_b = raw_b, raw_a
                norm_a, norm_b = norm_b, norm_a
                named_a, named_b = named_b, named_a

            candidate = _evaluate_pair(
                id_a, id_b, raw_a, raw_b, norm_a, norm_b, named_a, named_b,
                identity_fields, config,
            )
            if candidate is None:
                continue

            key = (id_a, id_b)
            existing = candidates.get(key)
            if existing is None or _REASON_STRENGTH[candidate.reason] > _REASON_STRENGTH[existing.reason]:
                candidates[key] = candidate

    return sorted(candidates.values(), key=lambda c: (c.business_id_a, c.business_id_b))


def _evaluate_pair(
    id_a: str,
    id_b: str,
    raw_a: tuple[str, ...],
    raw_b: tuple[str, ...],
    norm_a: tuple[str, ...],
    norm_b: tuple[str, ...],
    named_a: Mapping[str, str],
    named_b: Mapping[str, str],
    identity_fields: tuple[str, ...],
    config: RuleConfig,
) -> DuplicateCandidate | None:
    if raw_a == raw_b:
        return DuplicateCandidate(
            business_id_a=id_a,
            business_id_b=id_b,
            reason=REASON_EXACT,
            similarity=100.0,
            evidence={"identity_fields": list(identity_fields)},
        )

    if norm_a == norm_b:
        return DuplicateCandidate(
            business_id_a=id_a,
            business_id_b=id_b,
            reason=REASON_NORMALIZED,
            similarity=100.0,
            evidence={
                "identity_fields": list(identity_fields),
                "normalized_values": dict(zip(identity_fields, norm_a)),
            },
        )

    if not config.fuzzy_matching_enabled:
        return None

    name_similarity = float(
        fuzz.token_sort_ratio(named_a["name"], named_b["name"])
        if "name" in identity_fields
        else 0.0
    )
    address_similarity = float(
        fuzz.token_sort_ratio(named_a["address"], named_b["address"])
        if "address" in identity_fields
        else 0.0
    )

    if (
        name_similarity >= config.name_similarity_threshold
        and address_similarity >= config.address_similarity_threshold
    ):
        return DuplicateCandidate(
            business_id_a=id_a,
            business_id_b=id_b,
            reason=REASON_FUZZY,
            similarity=round(min(name_similarity, address_similarity), 2),
            evidence={
                "name_similarity": round(name_similarity, 2),
                "address_similarity": round(address_similarity, 2),
                "name_threshold": config.name_similarity_threshold,
                "address_threshold": config.address_similarity_threshold,
                "normalized_name_a": named_a.get("name", ""),
                "normalized_name_b": named_b.get("name", ""),
            },
        )

    return None
