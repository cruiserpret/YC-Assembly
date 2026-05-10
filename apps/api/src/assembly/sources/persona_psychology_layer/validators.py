"""Phase 9A.3 — sensitive-inference guard for the psychology layer.

Forbids the inference layer from producing trait names or evidence_basis
language that names protected categories. Universal — these forbidden
fields are never the right answer regardless of product.
"""
from __future__ import annotations

import re
from typing import Iterable

from assembly.sources.persona_psychology_layer.schemas import (
    PsychologyProfile,
)


SENSITIVE_INFERENCE_FORBIDDEN_FIELDS: tuple[str, ...] = (
    # Note: "race" is intentionally NOT in this list — it is ambiguous
    # (running-race vs. racial category). "Ethnicity" / "racial" cover
    # the protected-category meaning unambiguously.
    "racial",
    "ethnicity",
    "religion",
    "religious",
    "party_affiliation",
    "voted_for",
    "sexual_orientation",
    "lgbt",
    "trans_",
    "mental_health",
    "depression_diagnosis",
    "anxiety_disorder",
    "ptsd",
    "schizophrenia",
    "bipolar",
    "diagnosed_with",
    "medical_condition",
    "disability",
    "disabled",
    "income_bracket",
    "household_income",
    "net_worth",
    "credit_score",
    "immigration",
    "citizenship",
    "ssn",
)


_SENSITIVE_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(t) for t in SENSITIVE_INFERENCE_FORBIDDEN_FIELDS)
    + r")\b",
    re.IGNORECASE,
)


def _has_sensitive_term(text: str | None) -> str | None:
    if not text:
        return None
    m = _SENSITIVE_RE.search(text)
    if m:
        return m.group(0)
    return None


def validate_no_sensitive_inferences(
    profiles: Iterable[PsychologyProfile],
) -> dict[str, object]:
    """Scan every profile's evidence_basis + caveat for forbidden
    protected-category terms. Returns a structured audit dict — empty
    findings list = clean.
    """
    findings: list[dict[str, str]] = []
    for prof in profiles:
        for trait in prof.traits:
            for field, text in (
                ("evidence_basis", trait.evidence_basis),
                ("caveat", trait.caveat),
            ):
                hit = _has_sensitive_term(text)
                if hit:
                    findings.append({
                        "persona_id": prof.persona_id,
                        "trait_name": trait.trait_name,
                        "field": field,
                        "matched_term": hit,
                    })
    return {
        "scanner_version": "9A.3.universal",
        "finding_count": len(findings),
        "any_sensitive_inference": bool(findings),
        "findings": findings[:50],
    }
