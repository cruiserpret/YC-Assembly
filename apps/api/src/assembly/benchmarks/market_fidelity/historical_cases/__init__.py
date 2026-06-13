"""Phase 17D — historical case-pack builder (open-weight blind backtest data layer).

Creates RESOLVED historical case packs that strictly separate a pre-outcome
``input_bundle`` (the only thing shown to Raw/Assembly) from a post-outcome
``outcome_record`` (used only after the prediction is locked, for scoring), with a
source manifest, a leakage audit, deterministic hashes, and the 17C eligibility/
blindness verdict. Packs are stored under
``apps/api/benchmarks/market_fidelity/historical_case_packs/`` and are NEVER loaded as
validation cases. Pure data + filesystem: no model, no download, no network, no
forecast/calibration change.
"""
from __future__ import annotations

from assembly.benchmarks.market_fidelity.historical_cases.case_pack_schema import (
    CandidateMetadata,
    HistoricalCasePack,
)
from assembly.benchmarks.market_fidelity.historical_cases.case_registry import check_diversity
from assembly.benchmarks.market_fidelity.historical_cases.input_bundle import (
    EvidenceItem,
    InputBundle,
)
from assembly.benchmarks.market_fidelity.historical_cases.leakage_audit import run_leakage_audit
from assembly.benchmarks.market_fidelity.historical_cases.outcome_record import OutcomeRecord
from assembly.benchmarks.market_fidelity.historical_cases.pack_builder import (
    CasePackReport,
    ProvenanceInputs,
    build_case_pack,
)
from assembly.benchmarks.market_fidelity.historical_cases.pack_hashes import (
    full_case_pack_hash,
    hash_obj,
)
from assembly.benchmarks.market_fidelity.historical_cases.pack_validator import validate_case_pack
from assembly.benchmarks.market_fidelity.historical_cases.source_manifest import (
    build_source_manifest,
)
from assembly.benchmarks.market_fidelity.historical_cases.storage import (
    default_packs_dir,
    write_case_pack,
)

__all__ = [
    "EvidenceItem",
    "InputBundle",
    "OutcomeRecord",
    "HistoricalCasePack",
    "CandidateMetadata",
    "build_source_manifest",
    "run_leakage_audit",
    "hash_obj",
    "full_case_pack_hash",
    "ProvenanceInputs",
    "CasePackReport",
    "build_case_pack",
    "validate_case_pack",
    "check_diversity",
    "default_packs_dir",
    "write_case_pack",
]
