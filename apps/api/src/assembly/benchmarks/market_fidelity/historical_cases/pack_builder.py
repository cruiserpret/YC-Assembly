"""Phase 17D — historical case-pack builder.

Assembles a pack from a (separate) input bundle + outcome record + candidate metadata,
then runs the source manifest, the leakage audit, the hashes, and the 17C eligibility
gate, and conservatively CLASSIFIES the pack (accepted / case_study_only / rejected /
candidate). It NEVER mixes the outcome into the input bundle and NEVER calls a model.
Pure/deterministic.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from assembly.benchmarks.market_fidelity.eligibility import (
    RetrospectiveCaseEligibilityInput,
    evaluate_eligibility,
)
from assembly.benchmarks.market_fidelity.historical_cases.case_pack_schema import (
    CandidateMetadata,
    HistoricalCasePack,
)
from assembly.benchmarks.market_fidelity.historical_cases.input_bundle import InputBundle
from assembly.benchmarks.market_fidelity.historical_cases.leakage_audit import run_leakage_audit
from assembly.benchmarks.market_fidelity.historical_cases.outcome_record import OutcomeRecord
from assembly.benchmarks.market_fidelity.historical_cases.pack_hashes import (
    full_case_pack_hash,
    hash_obj,
)
from assembly.benchmarks.market_fidelity.historical_cases.source_manifest import (
    build_source_manifest,
)

CaseClassification = Literal[
    "prospective_clean", "retrospective_time_frozen", "retrospective_open_weight_uncertain",
    "case_study_only", "reject",
]


class ProvenanceInputs(BaseModel):
    """Operator-supplied provenance for the eligibility gate (defaults are conservative
    for an offline retrospective open-weight run)."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    model_checkpoint: str = "unspecified"
    is_prospective: bool = False
    is_open_weight: bool = True
    offline: bool = True
    live_web_after_outcome: bool = False
    model_release_date: str | None = None
    training_cutoff: str | None = None
    has_temporal_proof: bool = False
    knowledge_probe_blocks_claim: bool = False
    tier1_provenance_justified: bool = False


class CasePackReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: HistoricalCasePack
    source_manifest: dict
    leakage_audit: dict
    eligibility: dict
    case_classification: CaseClassification
    reasons: list[str]


def _scoreable(outcome: OutcomeRecord) -> bool:
    return any([
        outcome.full_distribution_scoreable, outcome.buyer_anchor_scoreable,
        outcome.qualitative_scoreable,
    ]) and not outcome.not_scoreable


def build_case_pack(
    *,
    input_bundle: InputBundle,
    outcome_record: OutcomeRecord,
    candidate_metadata: CandidateMetadata,
    product_name: str,
    provenance: ProvenanceInputs,
    company_or_creator: str = "",
    geography: str = "",
    flagged_outcome_values: list[str] | None = None,
    notes: str = "",
) -> CasePackReport:
    if input_bundle.case_id != outcome_record.case_id:
        raise ValueError("input_bundle.case_id must match outcome_record.case_id")

    manifest = build_source_manifest(input_bundle.evidence_items)
    audit = run_leakage_audit(
        input_bundle, outcome_record.outcome_timestamp, flagged_outcome_values=flagged_outcome_values
    )

    elig = evaluate_eligibility(RetrospectiveCaseEligibilityInput(
        case_id=input_bundle.case_id,
        subject=provenance.subject,
        prediction_timestamp=input_bundle.prediction_timestamp,
        outcome_timestamp=outcome_record.outcome_timestamp,
        model_checkpoint=provenance.model_checkpoint,
        model_release_date=provenance.model_release_date,
        training_cutoff=provenance.training_cutoff,
        is_prospective=provenance.is_prospective,
        is_open_weight=provenance.is_open_weight,
        offline=provenance.offline,
        live_web_after_outcome=provenance.live_web_after_outcome,
        has_pre_outcome_source_timestamps=manifest["all_timestamps_high_confidence"],
        has_temporal_proof=provenance.has_temporal_proof,
        knowledge_probe_blocks_claim=provenance.knowledge_probe_blocks_claim,
        tier1_provenance_justified=provenance.tier1_provenance_justified,
    ))

    reasons: list[str] = list(audit["eligibility_downgrades"]) + list(elig.reasons)
    scoreable = _scoreable(outcome_record)
    # 'accepted' additionally requires that EVERY evidence item is human-attested
    # verified_pre_outcome (regex-clean text alone is not enough — paraphrased reveals
    # can dodge it), so an unverified-but-clean source can only reach 'candidate'.
    all_verified = bool(input_bundle.evidence_items) and all(
        e.pre_outcome_status == "verified_pre_outcome" for e in input_bundle.evidence_items
    )

    # --- conservative classification ---
    if audit["outcome_leakage_flags"] or audit["post_outcome_flags"] or elig.blindness_tier == 4:
        status, classification = "rejected", "reject"
        reasons.append("contamination: outcome leakage in the bundle and/or Tier-4 eligibility")
    elif not audit["input_bundle_clean"]:
        status, classification = "rejected", "reject"
        reasons.append("input bundle is not clean (post-prediction / untimestamped / structured-field leak)")
    elif not scoreable:
        status, classification = "case_study_only", "case_study_only"
        reasons.append("no defensible scoreable outcome mapping — case study only")
    elif elig.blindness_tier == 0:
        status, classification = ("accepted" if all_verified else "candidate"), "prospective_clean"
        if not all_verified:
            reasons.append("not all evidence is attested verified_pre_outcome — candidate, not accepted")
    elif elig.blindness_tier == 1:
        claimable = elig.eligible_for_public_claim and all_verified
        status = "accepted" if claimable else "candidate"
        classification = "retrospective_time_frozen"
        if not claimable:
            reasons.append(
                "Tier 1 requires justified provenance AND all-evidence verified_pre_outcome — candidate, not accepted"
            )
    elif elig.blindness_tier == 2:
        status, classification = "case_study_only", "retrospective_open_weight_uncertain"
    else:  # tier 3
        status, classification = "case_study_only", "case_study_only"

    ib_hash = hash_obj(input_bundle)
    or_hash = hash_obj(outcome_record)
    sm_hash = hash_obj(manifest)
    pack = HistoricalCasePack(
        case_id=input_bundle.case_id,
        product_name=product_name,
        company_or_creator=company_or_creator,
        category=candidate_metadata.category,
        platform=candidate_metadata.platform,
        geography=geography,
        prediction_timestamp=input_bundle.prediction_timestamp,
        outcome_timestamp=outcome_record.outcome_timestamp,
        case_status=status,
        input_bundle_hash=ib_hash,
        outcome_record_hash=or_hash,
        source_manifest_hash=sm_hash,
        full_case_pack_hash=full_case_pack_hash(
            input_bundle_hash=ib_hash, outcome_record_hash=or_hash,
            source_manifest_hash=sm_hash, case_id=input_bundle.case_id,
        ),
        contamination_risk=("high" if classification == "reject" else
                            "medium" if elig.blindness_tier and elig.blindness_tier >= 2 else "low"),
        blindness_tier=elig.blindness_tier,
        eligible_for_public_claim=elig.eligible_for_public_claim and status == "accepted",
        candidate_metadata=candidate_metadata,
        notes=notes,
    )
    return CasePackReport(
        pack=pack, source_manifest=manifest, leakage_audit=audit,
        eligibility=elig.model_dump(mode="json"), case_classification=classification, reasons=reasons,
    )
