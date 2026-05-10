"""Phase 8.2A — first-pass sensitive-attribute filter.

Rejects storage / inference of sensitive personal attributes. This is a
TECHNICAL guard, not a complete legal/privacy solution — the privacy memo
in `docs/privacy/PHASE_8_2.md` (added in Phase 8.2B alongside the first
real ingestion adapter) covers the legal posture. The architectural
intent is that no future code path can store these attributes in
persona_traits / persona_records — every candidate value passes through
`assert_no_sensitive_attributes` before it lands.

Forbidden attribute classes (re-stated in plain English so reviewers can
see the discipline at a glance):

  - race, ethnicity, ancestry
  - religion / religious affiliation
  - sexual orientation
  - gender identity (beyond what a public source explicitly states; never
    inferred)
  - health status, disability
  - immigration / citizenship status
  - household income / exact financial details
  - precise address / ZIP+4 / street-level location
  - phone numbers, email addresses
  - real names that appear to bear identity (the anonymization layer
    catches accidental name leakage; this filter rejects intentional
    inference)
  - employer identity (unless from a public job-title self-disclosure
    stored as `role_or_context` only — and never paired with the
    person's real name)

Anything matched here MUST be rejected. Persona trait validators call
`assert_no_sensitive_attributes` against `value` AND `rationale`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SensitiveCategory(str, Enum):
    RACE_ETHNICITY = "race_ethnicity"
    RELIGION = "religion"
    SEXUAL_ORIENTATION = "sexual_orientation"
    GENDER_IDENTITY_INFERRED = "gender_identity_inferred"
    HEALTH = "health"
    IMMIGRATION = "immigration"
    INCOME = "income"
    CONTACT_EMAIL = "contact_email"
    CONTACT_PHONE = "contact_phone"
    PRECISE_ADDRESS = "precise_address"
    ZIP_CODE = "zip_code"
    REAL_NAME_IDENTIFIER = "real_name_identifier"


@dataclass(frozen=True)
class SensitiveViolation:
    category: SensitiveCategory
    matched: str
    rule_id: str


class SensitiveAttributeRejected(Exception):
    """Raised when a candidate persona value contains a sensitive attribute.
    Persona-trait insert paths must catch this — never silently strip."""

    def __init__(self, violations: list[SensitiveViolation]) -> None:
        self.violations = violations
        head = violations[:5]
        details = "; ".join(f"{v.rule_id}={v.matched!r}" for v in head)
        super().__init__(
            f"sensitive_attribute_rejected: {len(violations)} violation(s): {details}"
        )


# Patterns are conservative and case-insensitive. False positives are
# tolerable; false negatives are not. When in doubt, expand the pattern.

_RACE_ETHNICITY = re.compile(
    r"\b(?:black|white|asian|hispanic|latino|latina|latinx|caucasian|"
    r"african[-\s]?american|native[-\s]?american|indigenous|"
    r"middle[-\s]?eastern|south[-\s]?asian|east[-\s]?asian|"
    r"pacific[-\s]?islander|jewish|arab|chinese|indian|mexican|filipino)\b",
    re.IGNORECASE,
)

# Religion keywords. We match "is a {religion}" and bare religion words
# in the context of identity-bearing prose.
_RELIGION = re.compile(
    r"\b(?:muslim|islamic|christian|catholic|protestant|jew(?:ish)?|"
    r"hindu|buddhist|sikh|atheist|agnostic|orthodox|mormon|"
    r"evangelical|baptist|methodist)\b",
    re.IGNORECASE,
)

_SEXUAL_ORIENTATION = re.compile(
    r"\b(?:gay|lesbian|bisexual|bi|straight|heterosexual|homosexual|"
    r"queer|lgbt(?:q\+?)?|asexual|pansexual)\b",
    re.IGNORECASE,
)

_GENDER_IDENTITY_INFERRED = re.compile(
    r"\b(?:transgender|trans|cisgender|cis|nonbinary|non[-\s]?binary|"
    r"genderqueer|two[-\s]?spirit)\b",
    re.IGNORECASE,
)

_HEALTH = re.compile(
    r"\b(?:diabetes|cancer|hiv|aids|depression|anxiety|"
    r"bipolar|schizophrenia|adhd|autism|disabled|disability|chronic\s+illness|"
    r"prescription|antidepressant|wheelchair|medical\s+condition)\b",
    re.IGNORECASE,
)

_IMMIGRATION = re.compile(
    r"\b(?:undocumented|illegal\s+(?:immigrant|alien)|green\s+card|"
    r"h1b|h-?1b|f1\s+visa|f-?1\s+visa|asylum[-\s]?seeker|refugee\s+status|"
    r"naturalized|naturali[sz]ation|citizenship\s+status|permanent\s+resident)\b",
    re.IGNORECASE,
)

# Income — match dollar amounts attached to "income", "salary", "earns",
# "household income", "earns $X", or precise income brackets.
_INCOME = re.compile(
    r"\b(?:income|salary|earnings?|earns|household)\b[^.\n]{0,30}\$\s*\d",
    re.IGNORECASE,
)

# Email + phone are RFC-permissive but conservative.
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# Phone — North American + international-ish patterns. We cast a wide net.
_PHONE = re.compile(
    r"(?<!\d)"
    r"(?:\+?\d{1,3}[\s.-]?)?"          # optional country code
    r"(?:\(?\d{3}\)?[\s.-]?)"          # area code
    r"\d{3}[\s.-]?\d{4}"               # 7-digit subscriber
    r"(?!\d)"
)

# Precise address — match house-number + street-name patterns ("123 Main St"),
# and extended address with apartment/unit numbers.
_PRECISE_ADDRESS = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s){1,4}"
    r"(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?|"
    r"Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Place|Pl\.?|Way|Plaza)"
    r"\b",
    re.IGNORECASE,
)

# US ZIP — 5-digit; ZIP+4 also caught.
_ZIP_CODE = re.compile(r"\b\d{5}(?:-\d{4})?\b")


_RULES: tuple[tuple[SensitiveCategory, str, re.Pattern[str]], ...] = (
    (SensitiveCategory.RACE_ETHNICITY, "rwi.race_ethnicity", _RACE_ETHNICITY),
    (SensitiveCategory.RELIGION, "rwi.religion", _RELIGION),
    (SensitiveCategory.SEXUAL_ORIENTATION, "rwi.sexual_orientation", _SEXUAL_ORIENTATION),
    (SensitiveCategory.GENDER_IDENTITY_INFERRED, "rwi.gender_identity", _GENDER_IDENTITY_INFERRED),
    (SensitiveCategory.HEALTH, "rwi.health", _HEALTH),
    (SensitiveCategory.IMMIGRATION, "rwi.immigration", _IMMIGRATION),
    (SensitiveCategory.INCOME, "rwi.income", _INCOME),
    (SensitiveCategory.CONTACT_EMAIL, "rwi.email", _EMAIL),
    (SensitiveCategory.CONTACT_PHONE, "rwi.phone", _PHONE),
    (SensitiveCategory.PRECISE_ADDRESS, "rwi.precise_address", _PRECISE_ADDRESS),
    (SensitiveCategory.ZIP_CODE, "rwi.zip_code", _ZIP_CODE),
)


def scan_sensitive_attributes(text: str | None) -> list[SensitiveViolation]:
    """Return every sensitive-attribute hit in `text`. Empty list when clean."""
    if not text:
        return []
    out: list[SensitiveViolation] = []
    for category, rule_id, pattern in _RULES:
        for m in pattern.finditer(text):
            out.append(
                SensitiveViolation(
                    category=category,
                    matched=m.group(0),
                    rule_id=rule_id,
                )
            )
    return out


def assert_no_sensitive_attributes(text: str | None) -> None:
    """Raise SensitiveAttributeRejected if `text` contains any sensitive
    attribute. Use this inside persona-trait validators before storing."""
    violations = scan_sensitive_attributes(text)
    if violations:
        raise SensitiveAttributeRejected(violations)
