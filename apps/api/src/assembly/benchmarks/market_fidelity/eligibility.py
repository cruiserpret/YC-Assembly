"""Phase 17C — retrospective case eligibility gate.

Combines temporal facts (prediction / outcome timestamps, source timestamps), model
provenance (release date / training cutoff vs the outcome), the offline/contamination
status, and the knowledge-probe verdict into a blindness_tier + an
``eligible_for_public_claim`` decision with explicit reasons. Pure/deterministic. The
conservative default is to DOWNGRADE: a case is public claim-grade only when it
clearly earns Tier 0 or a justified Tier 1.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from assembly.benchmarks.market_fidelity.blindness import is_public_claim_grade

Classification = Literal[
    "prospective_clean", "time_frozen_model_clean", "internal_only",
    "case_study_only", "contaminated_excluded",
]


def _date(s: str | None) -> str | None:
    return s[:10] if isinstance(s, str) and len(s) >= 10 else s


class RetrospectiveCaseEligibilityInput(BaseModel):
    """Everything the gate needs about one (case, model, run)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    subject: str  # product / company / campaign
    prediction_timestamp: str
    outcome_timestamp: str
    input_bundle_created_at: str | None = None
    model_checkpoint: str
    model_release_date: str | None = None
    training_cutoff: str | None = None
    is_prospective: bool = False  # prediction locked before the outcome existed (real-time)
    is_open_weight: bool = False
    offline: bool = True  # no live web/tools; frozen pre-outcome bundle
    live_web_after_outcome: bool = False
    has_pre_outcome_source_timestamps: bool = False
    has_temporal_proof: bool = False  # archived sources / strong provenance
    outcome_data_source: str = ""
    knowledge_probe_blocks_claim: bool = False  # from knowledge_probe.probe_blocks_public_claim
    tier1_provenance_justified: bool = False


class EligibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    blindness_tier: int
    classification: Classification
    eligible_for_public_claim: bool
    reasons: list[str]


def evaluate_eligibility(case: RetrospectiveCaseEligibilityInput) -> EligibilityResult:
    reasons: list[str] = []
    out_d = _date(case.outcome_timestamp)
    rel_d = _date(case.model_release_date)
    cut_d = _date(case.training_cutoff)

    model_after_outcome = bool(
        (rel_d and out_d and rel_d >= out_d) or (cut_d and out_d and cut_d >= out_d)
    )
    if rel_d and out_d and rel_d >= out_d:
        reasons.append(f"model release {rel_d} is at/after outcome {out_d}")
    if cut_d and out_d and cut_d >= out_d:
        reasons.append(f"training cutoff {cut_d} is at/after outcome {out_d}")
    cutoff_known = bool(rel_d or cut_d)

    # 1) Contamination dominates.
    if case.live_web_after_outcome:
        reasons.append("live web used after the outcome — contaminated")
        tier, classification = 4, "contaminated_excluded"
    elif not case.offline:
        reasons.append("run is not offline (web/tools/live retrieval enabled)")
        tier, classification = (4 if model_after_outcome else 3), (
            "contaminated_excluded" if model_after_outcome else "case_study_only"
        )
    # 2) The knowledge probe + a post-outcome model are DISQUALIFIERS that apply even to
    # a self-attested 'prospective' claim (a genuine prospective outcome cannot be known
    # by the model nor post-dated by it) — they are checked BEFORE the is_prospective
    # short-circuit so an inconsistent 'prospective' flag cannot launder a contaminated case.
    elif case.knowledge_probe_blocks_claim:
        reasons.append("knowledge probe indicates the model may already know the outcome")
        tier, classification = 4, "contaminated_excluded"
    elif model_after_outcome:
        reasons.append("model release/cutoff is at/after the outcome (inconsistent with a blind run)")
        tier, classification = 3, "case_study_only"
    # 3) Prospective = Tier 0 (blind by construction), once the disqualifiers above are clear.
    elif case.is_prospective:
        tier, classification = 0, "prospective_clean"
    # 5) Open-weight with uncertain cutoff -> internal only.
    elif case.is_open_weight and not cutoff_known:
        reasons.append("open-weight model with uncertain training cutoff — internal comparison only")
        tier, classification = 2, "internal_only"
    # 6) Time-frozen clean retrospective -> Tier 1 (claim-grade only if justified).
    elif (
        cutoff_known
        and not model_after_outcome
        and case.has_pre_outcome_source_timestamps
        and case.has_temporal_proof
    ):
        tier, classification = 1, "time_frozen_model_clean"
    # 7) Everything else: insufficient temporal proof -> downgrade.
    else:
        if not case.has_pre_outcome_source_timestamps:
            reasons.append("no pre-outcome source timestamps")
        if not case.has_temporal_proof:
            reasons.append("no temporal/model provenance proof")
        tier, classification = 2, "case_study_only"

    eligible = is_public_claim_grade(
        tier, tier1_provenance_justified=case.tier1_provenance_justified
    )
    if tier == 1 and not eligible:
        reasons.append("Tier 1 requires explicitly justified temporal/model provenance to be claim-grade")

    return EligibilityResult(
        case_id=case.case_id,
        blindness_tier=tier,
        classification=classification,
        eligible_for_public_claim=eligible,
        reasons=reasons,
    )
