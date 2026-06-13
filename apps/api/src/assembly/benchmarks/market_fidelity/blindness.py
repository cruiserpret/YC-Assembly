"""Phase 17C — blindness tiers for benchmark cases.

A case's blindness tier bounds what kind of claim its result can support. Only Tier 0
(prospective, locked-before-outcome) and a carefully-justified Tier 1 (time-frozen
model, offline, pre-outcome bundle) may back a PUBLIC accuracy claim. Pure data.
"""
from __future__ import annotations

from typing import Literal

BlindnessTierId = Literal[0, 1, 2, 3, 4]

TIER_DEFINITIONS: dict[int, dict] = {
    0: {
        "id": "prospective_clean",
        "summary": "Outcome has not happened yet; prediction locked before outcome.",
        "public_claim_grade": True,
        "notes": "Strongest evidence. The only tier that is blind by construction.",
    },
    1: {
        "id": "time_frozen_model_clean",
        "summary": "Retrospective, but the model checkpoint/release/cutoff predates the "
                   "outcome; offline; no web/RAG/tools; input bundle has only pre-outcome sources.",
        "public_claim_grade": True,  # only with strong, justified provenance
        "notes": "Public claim-grade ONLY with strong temporal + model provenance (release/cutoff "
                 "demonstrably before the outcome) AND a clean knowledge probe.",
    },
    2: {
        "id": "open_weight_cutoff_uncertain",
        "summary": "Open-weight/local model, offline, no tools, but training cutoff / data "
                   "exposure is uncertain.",
        "public_claim_grade": False,
        "notes": "Internal comparison only.",
    },
    3: {
        "id": "closed_frontier_after_outcome",
        "summary": "Closed frontier model used after the outcome exists.",
        "public_claim_grade": False,
        "notes": "Useful for UX/report comparison only; not blind accuracy.",
    },
    4: {
        "id": "contaminated_or_post_outcome",
        "summary": "Known post-outcome exposure, live-web contamination, or outcome hints.",
        "public_claim_grade": False,
        "notes": "Case-study only; excluded from benchmark accuracy.",
    },
}

# Public benchmark claims may use ONLY Tier 0 and a carefully-justified Tier 1.
PUBLIC_CLAIM_TIERS = (0, 1)


def tier_definition(tier: int) -> dict:
    if tier not in TIER_DEFINITIONS:
        raise ValueError(f"unknown blindness_tier {tier!r} (valid: 0..4)")
    return TIER_DEFINITIONS[tier]


def is_public_claim_grade(tier: int, *, tier1_provenance_justified: bool = False) -> bool:
    """Tier 0 is always claim-grade; Tier 1 only when its temporal/model provenance is
    explicitly justified; Tiers 2-4 never."""
    if tier == 0:
        return True
    if tier == 1:
        return bool(tier1_provenance_justified)
    return False
