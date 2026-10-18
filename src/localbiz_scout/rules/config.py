"""Configuration for deterministic data-quality rules.

All knobs are explicit so that rule behaviour is documented and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class RuleConfig:
    """Settings shared by the rule modules.

    ``important_fields`` are checked for missing values. A field is only
    reported as missing when it is *present in the record* but blank; a field
    that the schema does not include at all is skipped, because absence of a
    column is different from a blank cell.

    ``duplicate_identity_fields`` are the fields used to decide whether two
    records are duplicates or near-duplicates.
    """

    id_field: str = "business_id"

    important_fields: tuple[str, ...] = (
        "name",
        "address",
        "city",
        "state",
        "postal_code",
        "phone",
        "website",
        "email",
    )

    email_field: str = "email"

    duplicate_identity_fields: tuple[str, ...] = (
        "name",
        "address",
        "city",
        "state",
        "postal_code",
        "phone",
    )

    # Thresholds are on rapidfuzz's 0-100 token-sort ratio against normalized
    # text. They are deliberately conservative: a fuzzy candidate requires
    # both a similar name *and* a similar address.
    name_similarity_threshold: float = 85.0
    address_similarity_threshold: float = 85.0

    fuzzy_matching_enabled: bool = True

    def replace(self, **changes: object) -> "RuleConfig":
        """Return a copy with the given fields replaced."""

        valid = {f.name for f in fields(self)}
        unknown = set(changes) - valid
        if unknown:
            raise TypeError(f"Unknown config fields: {sorted(unknown)}")
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update(changes)
        return RuleConfig(**data)


DEFAULT_CONFIG = RuleConfig()
