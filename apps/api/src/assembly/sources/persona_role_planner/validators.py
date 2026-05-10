"""Phase 8.5D.1 — universal launch-state-aware claim validator.

For unlaunched products, no persona candidate may claim direct
usage / customer / loyalty / review status of the target product.
This is the same anti-pretending discipline used in micro-simulation
forbidden-language scanning, applied at persona-candidate stage.

The validator is universal — works for any product name + any
launch state. The forbidden-pattern templates below are
parameterized by the target product's first-word and full name.
"""
from __future__ import annotations

import re

from assembly.sources.persona_role_planner.schemas import (
    LaunchStateClaimValidationResult, PersonaCandidate, ProductLaunchState,
)


# Universal forbidden-pattern templates. `{n}` is substituted with
# the product name (and the first word, for short-name fallback).
UNLAUNCHED_DIRECT_USAGE_PATTERNS: tuple[str, ...] = (
    r"\b{n} buyer\b", r"\b{n} buyers\b",
    r"\b{n} customer\b", r"\b{n} customers\b",
    r"\b{n} loyalist\b", r"\b{n} loyalists\b",
    r"\b{n} reviewer\b", r"\b{n} reviewers\b",
    r"\b{n} fan\b", r"\b{n} fans\b",
    r"\btried {n}\b", r"\bbought {n}\b", r"\bpurchased {n}\b",
    r"\buses {n}\b", r"\busing {n}\b", r"\bused {n}\b",
    r"\brepeat {n}\b", r"\b{n} repeat purchase\b",
    r"\b{n} habit\b",
    r"\bstarted with {n}\b",
    r"\bI drink {n}\b", r"\bdrinking {n}\b",
    r"\bI buy {n}\b", r"\bbuy {n}\b",
    r"\bI consume {n}\b",
)


def _all_text_fields_blob(candidate: PersonaCandidate) -> str:
    """Concatenate every text leaf from a candidate for scanning."""
    parts: list[str] = [
        candidate.evidence_summary,
        candidate.simulation_usefulness_summary,
        candidate.hypothetical_target_product_reaction,
    ]
    parts.extend(candidate.evidence_snippets)
    parts.extend(candidate.role_inference_basis)
    parts.extend(candidate.inferred_preferences)
    parts.extend(candidate.inferred_objections)
    parts.extend(candidate.inferred_behaviors)
    parts.extend(t.evidence_excerpt for t in candidate.inferred_traits)
    parts.extend(t.trait_value for t in candidate.inferred_traits)
    return " | ".join(p for p in parts if p)


def validate_launch_state_claims(
    *,
    candidate: PersonaCandidate,
    launch_state: ProductLaunchState,
    product_name: str,
) -> LaunchStateClaimValidationResult:
    """Apply the universal launch-state validator to one candidate.

    Returns a validation result. For `launched` and `in_market`
    products, this is currently a no-op (always valid) — direct
    target-product claims are allowed when the source supports them.

    For `unlaunched`: any direct-usage phrase about the target
    product (`tried <Product>`, `<Product> buyer`, etc.) is
    forbidden. The validator scans every text leaf in the candidate.
    """
    if launch_state in ("launched", "in_market"):
        return LaunchStateClaimValidationResult(
            candidate_id=candidate.candidate_id,
            launch_state=launch_state,
            forbidden_phrases_matched=[],
            is_valid=True,
        )

    name = product_name.strip()
    name_first_word = name.split()[0] if name else ""
    name_candidates = [name]
    if name_first_word and name_first_word.lower() != name.lower():
        name_candidates.append(name_first_word)

    blob = _all_text_fields_blob(candidate).lower()
    matches: list[str] = []
    for n in name_candidates:
        n_low = n.lower()
        for tmpl in UNLAUNCHED_DIRECT_USAGE_PATTERNS:
            pat = tmpl.format(n=re.escape(n_low))
            if re.search(pat, blob):
                matches.append(tmpl.format(n=n))
    matches = sorted(set(matches))
    return LaunchStateClaimValidationResult(
        candidate_id=candidate.candidate_id,
        launch_state=launch_state,
        forbidden_phrases_matched=matches,
        is_valid=not matches,
        rejection_reason=(
            "fabricated_unlaunched_target_product_use" if matches else None
        ),
    )
