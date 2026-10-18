"""Rule: conservative email *syntax* validation.

This only checks whether an address is well-formed. It makes no claim about
whether the address exists, whether the mailbox is reachable, or whether the
domain accepts mail. Blank addresses are intentionally skipped here; blank
values are reported by the missing-field rule so the same problem is never
reported twice.

If a dataset has no email column, this rule produces no findings. The shipped
synthetic sample has no email column, so the rule is exercised by tests using
explicit fixtures.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .config import RuleConfig
from .findings import RULE_INVALID_EMAIL, Finding
from .normalization import as_text, is_blank
from .records import get_record_id

# Local part: dot-separated runs of allowed characters. Does not allow leading,
# trailing or consecutive dots.
_LOCAL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$"
)

# Domain label: starts and ends alphanumeric, hyphens allowed inside.
_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")

_MAX_TOTAL_LENGTH = 254
_MAX_LOCAL_LENGTH = 64
_MIN_TLD_LENGTH = 2


def is_valid_email(value: object) -> bool:
    """Return True when ``value`` looks like a syntactically valid address.

    Documented, conservative checks:

    * exactly one ``@`` separating a non-empty local part and domain
    * total length <= 254 and local part <= 64 characters
    * local part uses a restricted character set with no leading/trailing/
      consecutive dots
    * domain has at least two labels, each starting/ending alphanumeric
    * the final label (TLD) is at least two alphabetic characters
    """

    text = as_text(value).strip()
    if text.count("@") != 1:
        return False

    local, _, domain = text.partition("@")
    if not local or not domain:
        return False
    if len(text) > _MAX_TOTAL_LENGTH or len(local) > _MAX_LOCAL_LENGTH:
        return False
    if not _LOCAL_RE.match(local):
        return False

    labels = domain.split(".")
    if len(labels) < 2:
        return False
    if any(not label or not _LABEL_RE.match(label) for label in labels):
        return False

    tld = labels[-1]
    return len(tld) >= _MIN_TLD_LENGTH and tld.isalpha()


def check_email(
    records: Sequence[Mapping[str, Any]],
    config: RuleConfig,
) -> list[Finding]:
    findings: list[Finding] = []

    for index, record in enumerate(records):
        if config.email_field not in record:
            continue

        value = record.get(config.email_field)
        if is_blank(value):
            # Delegated to the missing-field rule to avoid duplicate findings.
            continue

        if not is_valid_email(value):
            findings.append(
                Finding(
                    rule_id=RULE_INVALID_EMAIL,
                    business_id=get_record_id(record, config, index),
                    field=config.email_field,
                    message=(
                        f"Value for '{config.email_field}' is not a valid email "
                        "address syntax (existence is not checked)."
                    ),
                    evidence={"raw_value": as_text(value)},
                )
            )

    return findings
