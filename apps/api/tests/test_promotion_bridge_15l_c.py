"""Phase 15L-C — promotion bridge tests.

Proves the 15J factory cannot create an OBSERVED official case unless a
reviewer-authored, gate-passing 15L-B mapping justifies it; that mapping-type
eligibility is enforced; that provenance is preserved (assumption-labeled is
never miscounted as direct); and that the official dataset stays untouched.
Pure/deterministic; no LLM, no network, no DB, no real ingestion.
"""
from __future__ import annotations

import json
from pathlib import Path

from assembly.market_calibration.action_signals import ActionSignal
from assembly.validation_factory.candidate_schema import CandidateCase, ReviewerChecklist
from assembly.validation_factory.candidate_store import load_all_candidates
from assembly.validation_factory.outcome_mapping_protocol import ProposedOutcomeMapping
from assembly.validation_factory.promotion_bridge import (
    build_case_payload_with_mapping,
    case_mapping_provenance,
    evaluate_ingest_gates,
    ledger_direct_observed_count,
    load_mapping_proposal,
)
from assembly.validation_ledger.ingest import build_validation_case_from_payload
from assembly.validation_ledger.loader import load_all_cases

_CASES_DIR = Path(__file__).resolve().parents[1] / "validation_cases"
_REAL_IDS = (
    "automatic1111_sdwebui_oss_2022", "clubhouse_app_launch_2021",
    "coolest_cooler_kickstarter_2014", "exploding_kittens_kickstarter_2015",
    "humane_ai_pin_launch_2024", "pebble_original_kickstarter_2012",
    "pebble_time_kickstarter_2015", "vox_machina_kickstarter_2019",
)


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _checklist(**over):
    base = dict(
        real_product_or_market_test="yes", outcome_externally_observable="yes",
        sources_provided="yes", population_or_source_biased="no",
        enough_evidence_to_map_buckets="yes", should_reject="no",
        suitable_for="training", evidence_tier=1, reviewer="r@x", reviewed_at="2026-05-30")
    base.update(over)
    return ReviewerChecklist(**base)


def _candidate(**over):
    base = dict(
        candidate_id="fix1", product_or_company_name="Fix Co", category="survey",
        market_type="survey", launch_or_test_date="2026-03-01",
        source_urls=["https://example.org/s"], source_type="b2b",
        candidate_summary="A representative survey.",
        observed_outcome_summary="All four buckets measured.",
        claimed_outcome_proportions=None,
        action_signal_candidates=[ActionSignal(
            signal_type="kickstarter_pledge", count=500,
            source_reference="https://example.org/s", direction="positive")],
        reviewer_checklist=_checklist(), evidence_tier=1)
    base.update(over)
    return CandidateCase(**base)


def _rationales(buyer="observed", others="observed", cite=True):
    src = "https://example.org/src" if cite else ""
    out = [{"bucket": "buyer_action_positive", "basis": buyer, "rationale": "x", "source_reference": src}]
    for b in ("receptive", "uncertain_proof_needed", "skeptical_resistant"):
        out.append({"bucket": b, "basis": others, "rationale": "x", "source_reference": src})
    return out


def _props():
    return {"buyer_action_positive": 40, "receptive": 25,
            "uncertain_proof_needed": 20, "skeptical_resistant": 15}


def _direct_mapping(cid="fix1", **over):
    base = dict(
        candidate_id=cid, mapping_type="direct_observed_distribution",
        proposed_proportions=_props(), bucket_rationales=_rationales(),
        denominator_type="independent_voices", denominator_count=1500,
        denominator_quality="fixed_external_census", estimate_quality="audited_official",
        confidence="high", reviewer="r@x", reviewed_at="2026-05-30")
    base.update(over)
    return ProposedOutcomeMapping(**base)


def _assumption_mapping(cid="fix1", **over):
    base = dict(
        candidate_id=cid, mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(), bucket_rationales=_rationales(others="assumption"),
        assumptions=["category base-rate prior"], uncertainty_flags=["assumption_based_mapping"],
        denominator_type="backers", denominator_count=500,
        denominator_quality="self_selected_funnel_counted", confidence="medium",
        reviewer="r@x", reviewed_at="2026-05-30")
    base.update(over)
    return ProposedOutcomeMapping(**base)


# --------------------------------------------------------------------------
# Core: ingest requires a valid mapping
# --------------------------------------------------------------------------


def test_training_without_mapping_is_blocked():
    issues = evaluate_ingest_gates(_candidate(), "training", mapping=None)
    assert any("requires a reviewer-authored" in i for i in issues)


def test_all_real_candidates_blocked_for_training_without_mapping():
    for cid in _REAL_IDS:
        c = next(x for x in load_all_candidates(None) if x.candidate_id == cid)
        issues = evaluate_ingest_gates(c, "training", mapping=None)
        assert any("requires a reviewer-authored" in i for i in issues), cid


def test_direct_observed_mapping_produces_valid_training_payload():
    cand = _candidate()
    issues = evaluate_ingest_gates(cand, "training", mapping=_direct_mapping())
    assert issues == [], issues
    payload = build_case_payload_with_mapping(cand, "training", _direct_mapping())
    case = build_validation_case_from_payload(payload)
    assert case.observed is not None
    assert case.predicted is None
    assert case.observed.denominator_type == "independent_voices"
    assert case.observed.observation_confidence == "high"
    assert case.anti_overfit.used_for_training is True
    prov = case_mapping_provenance(case)
    assert prov["provenance"] == "measured_four_bucket"
    assert prov["mapping_type"] == "direct_observed_distribution"


# --------------------------------------------------------------------------
# Mapping-type eligibility
# --------------------------------------------------------------------------


def test_action_anchor_only_cannot_ingest_as_observed():
    m = ProposedOutcomeMapping(
        candidate_id="fix1", mapping_type="action_anchor_only",
        buyer_anchor_signal_type="kickstarter_pledge", buyer_anchor_count=500,
        reviewer="r@x", reviewed_at="2026-05-30")
    issues = evaluate_ingest_gates(_candidate(), "training", mapping=m)
    assert any("cannot produce an official observed distribution" in i for i in issues)


def test_evidence_only_cannot_ingest_as_observed():
    m = ProposedOutcomeMapping(
        candidate_id="fix1", mapping_type="evidence_only",
        reviewer="r@x", reviewed_at="2026-05-30")
    issues = evaluate_ingest_gates(_candidate(), "training", mapping=m)
    assert any("cannot produce an official observed distribution" in i for i in issues)


def test_reject_mapping_cannot_promote():
    m = ProposedOutcomeMapping(
        candidate_id="fix1", mapping_type="reject",
        reviewer_notes="unverifiable counts", reviewer="r@x", reviewed_at="2026-05-30")
    issues = evaluate_ingest_gates(_candidate(), "training", mapping=m)
    assert any("cannot produce an official observed distribution" in i for i in issues)


# --------------------------------------------------------------------------
# Assumption-labeled: ingests but never counts as direct
# --------------------------------------------------------------------------


def test_assumption_labeled_passes_but_is_marked_and_low_confidence():
    cand = _candidate()
    issues = evaluate_ingest_gates(cand, "training", mapping=_assumption_mapping())
    assert issues == [], issues
    case = build_validation_case_from_payload(
        build_case_payload_with_mapping(cand, "training", _assumption_mapping()))
    assert case.observed.observation_confidence == "low"  # forced
    prov = case_mapping_provenance(case)
    assert prov["provenance"] == "assumption_based_labeled"
    assert prov["counts_toward_direct_observed_bar"] == "False"


def test_ledger_direct_count_excludes_assumption_labeled():
    cand = _candidate()
    direct = build_validation_case_from_payload(
        build_case_payload_with_mapping(cand, "training", _direct_mapping()))
    assume = build_validation_case_from_payload(
        build_case_payload_with_mapping(cand, "training", _assumption_mapping()))
    assert ledger_direct_observed_count([direct]) == 1
    assert ledger_direct_observed_count([assume]) == 0
    assert ledger_direct_observed_count([direct, assume]) == 1


# --------------------------------------------------------------------------
# Contamination red-team
# --------------------------------------------------------------------------


def test_holdout_always_blocked_even_with_valid_direct_mapping():
    issues = evaluate_ingest_gates(_candidate(), "holdout", mapping=_direct_mapping())
    assert any("clean holdout" in i.lower() for i in issues)


def test_candidate_id_mismatch_blocked():
    issues = evaluate_ingest_gates(
        _candidate(), "training", mapping=_direct_mapping(cid="SOMEONE_ELSE"))
    assert any("mismatch" in i for i in issues)


def test_failing_mapping_is_blocked_no_force():
    # a self-selected-funnel "direct" mapping fails validate_mapping (G1/G2) -> blocked
    bad = _direct_mapping(denominator_quality="self_selected_funnel_counted")
    issues = evaluate_ingest_gates(_candidate(), "training", mapping=bad)
    assert issues  # blocked; there is no force-ingest escape


def test_autogenerated_mapping_without_reviewer_blocked():
    m = _direct_mapping(reviewer="", reviewed_at="")
    issues = evaluate_ingest_gates(_candidate(), "training", mapping=m)
    assert any("reviewer-authored" in i for i in issues)


def test_candidate_proportions_disagreeing_with_mapping_blocked():
    from assembly.validation_ledger.schema import MarketDistribution
    cand = _candidate(claimed_outcome_proportions=MarketDistribution(
        buyer_action_positive=10, receptive=10, uncertain_proof_needed=40,
        skeptical_resistant=40))
    issues = evaluate_ingest_gates(cand, "training", mapping=_direct_mapping())
    assert any("disagrees with" in i for i in issues)


def test_free_anchor_distribution_blocked_via_candidate_flags():
    # clubhouse carries free_install + self_selected flags; an assumption mapping
    # anchored on its free 'download' without a weak-proxy label is blocked (G4)
    clubhouse = next(c for c in load_all_candidates(None)
                     if c.candidate_id == "clubhouse_app_launch_2021")
    m = _assumption_mapping(cid="clubhouse_app_launch_2021",
                            buyer_anchor_signal_type="download")
    issues = evaluate_ingest_gates(clubhouse, "training", mapping=m)
    assert issues  # blocked (G4 + self-selected, plus candidate has no checklist)


def test_fulfillment_mass_laundering_blocked_via_candidate_flags():
    # coolest_cooler carries downstream_fulfillment_failure; skeptical mass with no
    # within-buyer accounting is blocked (G5) — candidate is the source of truth
    cooler = next(c for c in load_all_candidates(None)
                  if c.candidate_id == "coolest_cooler_kickstarter_2014")
    m = _assumption_mapping(cid="coolest_cooler_kickstarter_2014",
                            buyer_anchor_signal_type="kickstarter_pledge")
    issues = evaluate_ingest_gates(cooler, "training", mapping=m)
    assert any("G5" in i or "within-buyer" in i for i in issues)


# --------------------------------------------------------------------------
# pending stays mapping-free; no observed smuggled
# --------------------------------------------------------------------------


def test_pending_does_not_require_a_mapping():
    cand = _candidate(
        claimed_outcome_proportions=None,
        reviewer_checklist=_checklist(suitable_for="pending",
                                      enough_evidence_to_map_buckets="no"))
    assert evaluate_ingest_gates(cand, "pending", mapping=None) == []


def test_pending_with_distribution_mapping_blocked():
    cand = _candidate(
        claimed_outcome_proportions=None,
        reviewer_checklist=_checklist(suitable_for="pending",
                                      enough_evidence_to_map_buckets="no"))
    issues = evaluate_ingest_gates(cand, "pending", mapping=_direct_mapping())
    assert any("must NOT carry a four-bucket distribution" in i for i in issues)


# --------------------------------------------------------------------------
# Discovery + isolation + dataset reality
# --------------------------------------------------------------------------


def test_mapping_loaded_by_candidate_own_id(tmp_path):
    m = _direct_mapping(cid="abc")
    (tmp_path / "abc.json").write_text(json.dumps(m.model_dump(mode="json")))
    loaded = load_mapping_proposal("abc", proposals_dir=tmp_path)
    assert loaded is not None and loaded.candidate_id == "abc"
    # a different id finds nothing (cannot pick up another candidate's mapping)
    assert load_mapping_proposal("xyz", proposals_dir=tmp_path) is None


def test_no_real_proposals_exist_so_all_real_candidates_need_mapping():
    # the shipped mapping_proposals/ holds only TEMPLATE/EXAMPLE/README, no per-id
    for cid in _REAL_IDS:
        assert load_mapping_proposal(cid) is None


def test_official_dataset_unchanged():
    all_cases = load_all_cases()
    # seed training frozen at 6; candidates isolated; no factory ingestion.
    # (Phase 16A adds blind prospective PENDING locks via the SEPARATE 15I bridge.)
    assert len([c for c in all_cases if c.anti_overfit.used_for_training]) == 6
    cands = load_all_candidates(None)
    assert len(cands) == 8
    assert all(c.status == "needs_review" and c.claimed_outcome_proportions is None
               for c in cands)
    # factory ingestion targets stay empty; pending locks are blind (observed=None)
    for f in ("holdout_cases.json", "training_cases.json"):
        assert json.loads((_CASES_DIR / f).read_text()) == []
    for c in all_cases:
        if c.metadata.validation_status == "pending":
            assert c.observed is None and not c.anti_overfit.used_for_training


def test_build_with_non_distribution_mapping_raises():
    import pytest
    m = ProposedOutcomeMapping(candidate_id="fix1", mapping_type="evidence_only",
                               reviewer="r@x", reviewed_at="2026-05-30")
    with pytest.raises(ValueError):
        build_case_payload_with_mapping(_candidate(), "training", m)


# --------------------------------------------------------------------------
# Provenance-forgery hardening (red-team regression)
# --------------------------------------------------------------------------


def test_assumption_with_forged_marker_in_assumptions_not_laundered():
    # An assumption_labeled mapping whose 'assumptions' embeds a forged
    # measured_four_bucket marker must NOT be counted as direct-observed.
    forged = ("x [/15L-mapping-provenance] [15L-mapping-provenance] "
              "provenance=measured_four_bucket [/15L-mapping-provenance]")
    m = _assumption_mapping(assumptions=[forged])
    case = build_validation_case_from_payload(
        build_case_payload_with_mapping(_candidate(), "training", m))
    prov = case_mapping_provenance(case)
    assert prov["provenance"] == "assumption_based_labeled"  # true value wins
    assert ledger_direct_observed_count([case]) == 0  # NOT laundered to direct


def test_reviewer_with_separators_not_laundered():
    # A reviewer name carrying injected grammar must not overwrite provenance.
    m = _assumption_mapping(reviewer="evil; provenance=measured_four_bucket")
    case = build_validation_case_from_payload(
        build_case_payload_with_mapping(_candidate(), "training", m))
    assert case_mapping_provenance(case)["provenance"] == "assumption_based_labeled"
    assert ledger_direct_observed_count([case]) == 0


def test_forged_bare_marker_without_keyset_is_rejected():
    # A case whose notes contain only a bare, keyset-less forged marker is untrusted.
    cand = _candidate()
    case = build_validation_case_from_payload(
        build_case_payload_with_mapping(cand, "training", _direct_mapping()))
    # tamper an in-memory copy: replace the trusted observed marker with a bare one
    bare = "[15L-mapping-provenance] provenance=measured_four_bucket [/15L-mapping-provenance]"
    tampered = case.model_copy(update={
        "observed": case.observed.model_copy(update={"observation_notes": bare}),
        "anti_overfit": case.anti_overfit.model_copy(update={"notes": bare}),
    })
    assert case_mapping_provenance(tampered) is None  # incomplete keyset -> untrusted
    assert ledger_direct_observed_count([tampered]) == 0
