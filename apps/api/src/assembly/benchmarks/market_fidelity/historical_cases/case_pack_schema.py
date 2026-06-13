"""Phase 17D — HistoricalCasePack metadata + candidate diversity metadata.

The case pack is the top-level record tying together the (separate) input bundle and
outcome record, their hashes, the leakage-audit + eligibility verdicts, and the
anti-cherry-pick candidate metadata. Pure data. These packs live under
``apps/api/benchmarks/market_fidelity/historical_case_packs/`` and are NEVER loaded as
validation cases.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

CasePackPurpose = Literal["historical_case_pack_not_validation_data"]
CasePackStatus = Literal["candidate", "accepted", "rejected", "case_study_only"]
ContaminationRisk = Literal["none", "low", "medium", "high", "unknown"]

ExpectedOutcomeClass = Literal["success", "failure", "middling", "uncertain"]
FameLevel = Literal["obscure", "niche", "notable", "famous", "unknown"]
MemorizationRiskPrior = Literal["low", "medium", "high", "unknown"]


class CandidateMetadata(BaseModel):
    """Anti-cherry-pick metadata, so a balanced pack list isn't only famous winners."""

    model_config = ConfigDict(extra="forbid")

    expected_outcome_class: ExpectedOutcomeClass
    category: str
    platform: str
    source_availability: Literal["rich", "adequate", "thin", "unknown"] = "unknown"
    fame_level: FameLevel = "unknown"
    memorization_risk_prior: MemorizationRiskPrior = "unknown"
    selection_reason: str = ""


class HistoricalCasePack(BaseModel):
    """Top-level historical case pack. The input bundle and outcome record live in
    separate files/paths; only their HASHES + verdicts are recorded here."""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["historical_case_pack_not_validation_data"] = "historical_case_pack_not_validation_data"
    case_id: str
    product_name: str
    company_or_creator: str = ""
    category: str = ""
    platform: str = ""
    geography: str = ""
    prediction_timestamp: str
    outcome_timestamp: str
    case_status: CasePackStatus = "candidate"
    input_bundle_path: str | None = None
    outcome_record_path: str | None = None
    source_manifest_path: str | None = None
    leakage_audit_path: str | None = None
    eligibility_report_path: str | None = None
    input_bundle_hash: str | None = None
    outcome_record_hash: str | None = None
    source_manifest_hash: str | None = None
    full_case_pack_hash: str | None = None
    contamination_risk: ContaminationRisk = "unknown"
    blindness_tier: int | None = None
    eligible_for_public_claim: bool = False
    candidate_metadata: CandidateMetadata | None = None
    notes: str = ""
    # Outcomes are never embedded in the pack metadata (they live in the outcome record).
    observed: None = None
