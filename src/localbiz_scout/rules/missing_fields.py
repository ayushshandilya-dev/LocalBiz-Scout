"""Rule: detect missing values in configured important fields.

An empty string, a whitespace-only string, or an explicit ``None`` all count
as missing. A field that is absent from the schema entirely is *not* reported
(see :class:`~localbiz_scout.rules.config.RuleConfig`).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .config import RuleConfig
from .findings import RULE_MISSING_FIELD, Finding
from .normalization import as_text, is_blank
from .records import get_record_id


def check_missing_fields(
    records: Sequence[Mapping[str, Any]],
    config: RuleConfig,
) -> list[Finding]:
    findings: list[Finding] = []

    for index, record in enumerate(records):
        business_id = get_record_id(record, config, index)
        for field_name in config.important_fields:
            if field_name not in record:
                continue
            value = record.get(field_name)
            if is_blank(value):
                findings.append(
                    Finding(
                        rule_id=RULE_MISSING_FIELD,
                        business_id=business_id,
                        field=field_name,
                        message=f"Missing or blank value for important field '{field_name}'.",
                        evidence={"raw_value": as_text(value)},
                    )
                )

    return findings
