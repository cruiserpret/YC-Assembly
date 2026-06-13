"""Phase 17D — historical case-pack validator (read-only).

Re-validates a pack's three artifacts for consistency + leakage-freedom: case-id match,
hash reproduction, input/outcome SEPARATION (the outcome must not appear in the input
bundle), bundle cleanliness for accepted packs, and the public-claim tier rule. Returns
a list of issues (empty == valid). Pure; no model, no network.
"""
from __future__ import annotations

from assembly.benchmarks.market_fidelity.historical_cases.case_pack_schema import HistoricalCasePack
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


def validate_case_pack(
    *, input_bundle: InputBundle, outcome_record: OutcomeRecord, pack: HistoricalCasePack,
    flagged_outcome_values: list[str] | None = None,
) -> list[str]:
    issues: list[str] = []

    # case-id consistency
    if not (input_bundle.case_id == outcome_record.case_id == pack.case_id):
        issues.append("case_id mismatch across input_bundle / outcome_record / pack")

    # purpose marker
    if pack.purpose != "historical_case_pack_not_validation_data":
        issues.append("pack.purpose marker is wrong")

    # hash reproduction
    ib_hash, or_hash = hash_obj(input_bundle), hash_obj(outcome_record)
    sm_hash = hash_obj(build_source_manifest(input_bundle.evidence_items))
    if pack.input_bundle_hash not in (None, ib_hash):
        issues.append("input_bundle_hash does not reproduce")
    if pack.outcome_record_hash not in (None, or_hash):
        issues.append("outcome_record_hash does not reproduce")
    if pack.source_manifest_hash not in (None, sm_hash):
        issues.append("source_manifest_hash does not reproduce")
    if pack.full_case_pack_hash is not None:
        expect = full_case_pack_hash(
            input_bundle_hash=ib_hash, outcome_record_hash=or_hash,
            source_manifest_hash=sm_hash, case_id=pack.case_id,
        )
        if pack.full_case_pack_hash != expect:
            issues.append("full_case_pack_hash does not reproduce")

    # SEPARATION: the realized outcome must NOT leak into the input bundle. Re-run the
    # leakage audit; an accepted pack MUST be clean.
    audit = run_leakage_audit(
        input_bundle, outcome_record.outcome_timestamp, flagged_outcome_values=flagged_outcome_values
    )
    if pack.case_status == "accepted" and not audit["input_bundle_clean"]:
        issues.append("an 'accepted' pack must have a CLEAN input bundle (leakage audit failed)")
    if audit["outcome_leakage_flags"] and pack.case_status == "accepted":
        issues.append("accepted pack has outcome-leakage flags (evidence or structured fields)")
    # the leakage audit scans BOTH evidence text and the structured model-facing fields
    # against the outcome-reveal patterns + any flagged values, so a separation breach in
    # any model-facing field surfaces here (no brittle float-substring guessing).
    if audit["structured_field_leak_flags"] and pack.case_status == "accepted":
        issues.append(
            "accepted pack leaks the outcome via structured fields: "
            + ", ".join(audit["structured_field_leak_flags"])
        )

    # public-claim tier rule
    if pack.eligible_for_public_claim and pack.blindness_tier not in (0, 1):
        issues.append("eligible_for_public_claim requires blindness_tier 0 or 1")

    return issues
