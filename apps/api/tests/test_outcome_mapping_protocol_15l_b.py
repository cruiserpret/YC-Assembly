"""Phase 15L-B — Observed Outcome Mapping Protocol tests.

Proves the protocol blocks every fabrication channel, classifies the 8 real
candidates honestly, keeps the official dataset untouched, and never writes on a
dry run. Pure/deterministic; no LLM, no network, no DB.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.validation_factory.candidate_factory import evaluate_promotion_gates
from assembly.validation_factory.candidate_schema import CandidateCase, ReviewerChecklist
from assembly.validation_factory.candidate_store import (
    load_all_candidates,
    load_candidate,
)
from assembly.validation_factory.outcome_mapping_protocol import (
    MAPPING_PROPOSAL_PURPOSE,
    ProposedOutcomeMapping,
    classify_candidate,
    mapping_readiness,
    validate_mapping,
)
from assembly.validation_ledger.loader import load_all_cases
from assembly.validation_ledger.schema import MarketDistribution

_API_ROOT = Path(__file__).resolve().parents[1]
_CASES_DIR = _API_ROOT / "validation_cases"
_PROPOSALS_DIR = _CASES_DIR / "mapping_proposals"

_ALL_IDS = (
    "automatic1111_sdwebui_oss_2022",
    "clubhouse_app_launch_2021",
    "coolest_cooler_kickstarter_2014",
    "exploding_kittens_kickstarter_2015",
    "humane_ai_pin_launch_2024",
    "pebble_original_kickstarter_2012",
    "pebble_time_kickstarter_2015",
    "vox_machina_kickstarter_2019",
)


def _cand(cid: str):
    return load_candidate(cid, None)


def _synthetic_candidate(cid: str, *, signals=None, flags=None, source_type="b2b"):
    """A clean, benign candidate (no self-selected/free/fulfillment flags) so a
    distribution mapping can be validated against a real candidate object."""
    return CandidateCase(
        candidate_id=cid,
        product_or_company_name=cid,
        source_type=source_type,
        uncertainty_flags=flags or [],
        action_signal_candidates=signals or [],
    )


def _rationales(buyer="observed", others="assumption", cite=True):
    src = "https://example.org/src" if cite else ""
    out = [{"bucket": "buyer_action_positive", "basis": buyer, "rationale": "anchor", "source_reference": src}]
    for b in ("receptive", "uncertain_proof_needed", "skeptical_resistant"):
        out.append({"bucket": b, "basis": others, "rationale": "x", "source_reference": src})
    return out


def _props(buyer=40, receptive=25, uncertain=20, skeptical=15):
    return {
        "buyer_action_positive": buyer,
        "receptive": receptive,
        "uncertain_proof_needed": uncertain,
        "skeptical_resistant": skeptical,
    }


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_classify_eight_candidates_six_anchor_two_evidence():
    types = {}
    for cid in _ALL_IDS:
        mt, _ = classify_candidate(_cand(cid))
        types[cid] = mt
    anchor = [c for c, t in types.items() if t == "action_anchor_only"]
    evidence = [c for c, t in types.items() if t == "evidence_only"]
    assert len(anchor) == 6
    assert set(evidence) == {"clubhouse_app_launch_2021", "automatic1111_sdwebui_oss_2022"}
    assert not [t for t in types.values() if t in ("direct_observed_distribution", "reject")]


def test_free_cumulative_candidates_are_evidence_only():
    for cid in ("clubhouse_app_launch_2021", "automatic1111_sdwebui_oss_2022"):
        mt, reasons = classify_candidate(_cand(cid))
        assert mt == "evidence_only", (cid, mt)
        assert reasons


# --------------------------------------------------------------------------
# G1/G2/G3/G8 — buyer numerator cannot become a direct observed distribution
# --------------------------------------------------------------------------


def test_buyer_numerator_only_cannot_be_direct_observed():
    p = ProposedOutcomeMapping(
        candidate_id="pebble_time_kickstarter_2015",
        mapping_type="direct_observed_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="assumption"),
        denominator_type="backers",
        denominator_count=78471,
        denominator_quality="self_selected_funnel_counted",
        confidence="high",
    )
    res = validate_mapping(p, _cand("pebble_time_kickstarter_2015"))
    assert not res.ok
    assert "G1_denominator_known" in res.gate_codes
    assert "G8_anchor_masquerade" in res.gate_codes


def test_sum_to_100_alone_does_not_certify_realness():
    # A perfectly summing distribution over a self-selected funnel is still blocked.
    p = ProposedOutcomeMapping(
        candidate_id="exploding_kittens_kickstarter_2015",
        mapping_type="direct_observed_distribution",
        proposed_proportions=_props(25, 25, 25, 25),
        bucket_rationales=_rationales(others="observed"),
        denominator_type="backers",
        denominator_count=219382,
        denominator_quality="self_selected_funnel_counted",
    )
    res = validate_mapping(p, _cand("exploding_kittens_kickstarter_2015"))
    assert not res.ok
    assert "G1_denominator_known" in res.gate_codes


def test_direct_observed_requires_citations_on_every_bucket():
    p = ProposedOutcomeMapping(
        candidate_id="x",
        mapping_type="direct_observed_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="observed", cite=False),
        denominator_type="independent_voices",
        denominator_count=1000,
        denominator_quality="representative_random_sample",
        estimate_quality="audited_official",
    )
    res = validate_mapping(p, _synthetic_candidate("x"))
    assert not res.ok
    assert any("source_reference" in i for i in res.issues)


def test_direct_observed_passes_with_census_and_all_observed_cited():
    p = ProposedOutcomeMapping(
        candidate_id="synthetic_census",
        mapping_type="direct_observed_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="observed", cite=True),
        denominator_type="independent_voices",
        denominator_count=1500,
        denominator_quality="fixed_external_census",
        estimate_quality="audited_official",
        confidence="high",
    )
    res = validate_mapping(p, _synthetic_candidate("synthetic_census"))
    assert res.ok, res.issues
    assert res.provenance == "measured_four_bucket"
    assert res.counts_toward_direct_observed_bar is True
    assert res.training_eligible is True
    assert "G9_sum_to_100_structural" in res.gate_codes  # structural gate pinned
    assert res.clean_holdout_eligible is False  # G7: never a clean holdout
    assert "G7_retrospective_not_holdout" in res.gate_codes


# --------------------------------------------------------------------------
# Assumption-labeled
# --------------------------------------------------------------------------


def test_assumption_labeled_requires_explicit_assumptions():
    p = ProposedOutcomeMapping(
        candidate_id="pebble_time_kickstarter_2015",
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="assumption"),
        assumptions=[],  # missing -> block
        uncertainty_flags=["assumption_based_mapping"],
        denominator_type="backers",
        denominator_count=78471,
        denominator_quality="self_selected_funnel_counted",
    )
    res = validate_mapping(p, _cand("pebble_time_kickstarter_2015"))
    assert not res.ok
    assert "G2_self_selected_sample" in res.gate_codes


def test_assumption_labeled_valid_is_low_confidence_and_not_direct():
    p = ProposedOutcomeMapping(
        candidate_id="exploding_kittens_kickstarter_2015",
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="assumption"),
        assumptions=["category base-rate prior for tabletop launches"],
        uncertainty_flags=["assumption_based_mapping"],
        denominator_type="backers",
        denominator_count=219382,
        denominator_quality="self_selected_funnel_counted",
        confidence="low",
    )
    res = validate_mapping(p, _cand("exploding_kittens_kickstarter_2015"))
    assert res.ok, res.issues
    assert res.provenance == "assumption_based_labeled"
    assert res.forced_confidence == "low"
    assert res.counts_toward_direct_observed_bar is False
    assert res.training_eligible is True


def test_assumption_labeled_nonbuyer_observed_basis_blocked():
    p = ProposedOutcomeMapping(
        candidate_id="x",
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="observed"),  # non-buyer 'observed' -> block
        assumptions=["prior"],
        uncertainty_flags=["assumption_based_mapping"],
        denominator_type="backers",
        denominator_count=100,
        denominator_quality="self_selected_funnel_counted",
    )
    res = validate_mapping(p, _synthetic_candidate("x"))
    assert not res.ok
    assert "G8_anchor_masquerade" in res.gate_codes


# --------------------------------------------------------------------------
# G4 — free action is not a buyer
# --------------------------------------------------------------------------


def test_free_action_cannot_anchor_assumption_without_weak_proxy():
    p = ProposedOutcomeMapping(
        candidate_id="clubhouse_app_launch_2021",
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="assumption"),
        assumptions=["prior"],
        uncertainty_flags=["assumption_based_mapping"],
        buyer_anchor_signal_type="download",
        denominator_type="mixed_proxy",
        denominator_count=8100000,
        denominator_quality="self_selected_funnel_estimated",
        free_action_weak_proxy=False,  # missing label -> block
    )
    res = validate_mapping(p, _cand("clubhouse_app_launch_2021"))
    assert not res.ok
    assert "G4_free_action_not_buyer" in res.gate_codes


# --------------------------------------------------------------------------
# G5 / G6 — within-buyer fulfillment / returns are not market skepticism
# --------------------------------------------------------------------------


def _assumption_props_for(cid, anchor, **over):
    base = dict(
        candidate_id=cid,
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(50, 10, 10, 30),  # skeptical mass present
        bucket_rationales=_rationales(others="assumption"),
        assumptions=["prior"],
        uncertainty_flags=["assumption_based_mapping"],
        buyer_anchor_signal_type=anchor,
        denominator_type="backers",
        denominator_count=62642,
        denominator_quality="self_selected_funnel_counted",
    )
    base.update(over)
    return ProposedOutcomeMapping(**base)


def test_fulfillment_failure_not_coded_as_skeptical():
    p = _assumption_props_for("coolest_cooler_kickstarter_2014", "kickstarter_pledge")
    res = validate_mapping(p, _cand("coolest_cooler_kickstarter_2014"))
    assert not res.ok
    assert "G5_fulfillment_not_skeptic" in res.gate_codes


def test_fulfillment_failure_with_split_note_and_count_clears_g5():
    p = _assumption_props_for(
        "coolest_cooler_kickstarter_2014",
        "kickstarter_pledge",
        within_buyer_split_note="the >20,000 non-fulfilled backers are recorded as a "
        "within-buyer fulfillment failure, NOT as non-buyer skeptics",
        within_buyer_negative_count=20000,
    )
    res = validate_mapping(p, _cand("coolest_cooler_kickstarter_2014"))
    assert res.ok, res.issues


def test_within_buyer_mass_routed_to_receptive_also_blocked():
    # The fix: G5/G6 trigger on TOTAL non-buyer mass, not just skeptical+uncertain,
    # so parking the dissatisfied cohort in 'receptive' is still blocked.
    p = ProposedOutcomeMapping(
        candidate_id="coolest_cooler_kickstarter_2014",
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(70, 30, 0, 0),
        bucket_rationales=_rationales(others="assumption"),
        assumptions=["prior"],
        uncertainty_flags=["assumption_based_mapping"],
        buyer_anchor_signal_type="kickstarter_pledge",
        denominator_type="backers",
        denominator_count=62642,
        denominator_quality="self_selected_funnel_counted",
    )
    res = validate_mapping(p, _cand("coolest_cooler_kickstarter_2014"))
    assert not res.ok
    assert "G5_fulfillment_not_skeptic" in res.gate_codes


def test_returns_not_coded_as_skeptical():
    p = _assumption_props_for("humane_ai_pin_launch_2024", "purchase", denominator_count=10000)
    res = validate_mapping(p, _cand("humane_ai_pin_launch_2024"))
    assert not res.ok
    assert "G6_returns_not_skeptic" in res.gate_codes


# --------------------------------------------------------------------------
# action_anchor_only / evidence_only must not carry a distribution
# --------------------------------------------------------------------------


def test_action_anchor_only_must_not_carry_proportions():
    p = ProposedOutcomeMapping(
        candidate_id="x",
        mapping_type="action_anchor_only",
        proposed_proportions=_props(),
        buyer_anchor_signal_type="kickstarter_pledge",
        buyer_anchor_count=100,
    )
    res = validate_mapping(p, None)
    assert not res.ok
    assert "G8_anchor_masquerade" in res.gate_codes


def test_action_anchor_only_paid_anchor_counts_tier1_2():
    p = ProposedOutcomeMapping(
        candidate_id="exploding_kittens_kickstarter_2015",
        mapping_type="action_anchor_only",
        buyer_anchor_signal_type="kickstarter_pledge",
        buyer_anchor_count=219382,
        buyer_anchor_direction="positive",
        denominator_type="backers",
        denominator_quality="self_selected_funnel_counted",
        confidence="medium",
    )
    res = validate_mapping(p, _cand("exploding_kittens_kickstarter_2015"))
    assert res.ok, res.issues
    assert res.provenance == "buyer_anchor_only"
    assert res.counts_toward_direct_observed_bar is False
    assert res.counts_toward_tier1_2_evidence is True


def test_evidence_only_does_not_count_tier1_2_and_no_proportions():
    p = ProposedOutcomeMapping(
        candidate_id="automatic1111_sdwebui_oss_2022",
        mapping_type="evidence_only",
        buyer_anchor_signal_type="github_fork",
    )
    res = validate_mapping(p, _cand("automatic1111_sdwebui_oss_2022"))
    assert res.ok, res.issues
    assert res.counts_toward_tier1_2_evidence is False
    # and a proportions-bearing evidence_only is rejected
    bad = ProposedOutcomeMapping(
        candidate_id="x", mapping_type="evidence_only", proposed_proportions=_props()
    )
    assert not validate_mapping(bad, None).ok


# --------------------------------------------------------------------------
# Structural schema gates (G9, approval, reject reason)
# --------------------------------------------------------------------------


def test_proportions_must_sum_to_100():
    with pytest.raises(ValidationError):
        ProposedOutcomeMapping(
            candidate_id="x",
            mapping_type="direct_observed_distribution",
            proposed_proportions=_props(40, 25, 20, 5),  # sums to 90
        )


def test_extra_bucket_key_rejected():
    with pytest.raises(ValidationError):
        ProposedOutcomeMapping(
            candidate_id="x",
            mapping_type="direct_observed_distribution",
            proposed_proportions={**_props(), "made_up_bucket": 0.0},
        )


def test_human_approved_cannot_be_true():
    with pytest.raises(ValidationError):
        ProposedOutcomeMapping(
            candidate_id="x", mapping_type="evidence_only", human_approved=True
        )


def test_reject_requires_reason():
    with pytest.raises(ValidationError):
        ProposedOutcomeMapping(
            candidate_id="x", mapping_type="reject", reviewer_notes=""
        )
    ok = ProposedOutcomeMapping(
        candidate_id="x", mapping_type="reject", reviewer_notes="unverifiable counts"
    )
    assert validate_mapping(ok, None).ok


# --------------------------------------------------------------------------
# G7 — retrospective known outcomes cannot be a clean holdout
# --------------------------------------------------------------------------


def test_holdout_blocked_specifically_by_anti_leakage_even_when_otherwise_complete():
    # Isolates G7: give a candidate a full mapping + a COMPLETE reviewer checklist +
    # an evidence tier, so the ONLY remaining blocker for holdout is anti-leakage
    # (no prediction locked before the outcome). A tautological "issues is non-empty"
    # would pass even if the anti-leakage gate were deleted; this asserts the gate.
    base = _cand("exploding_kittens_kickstarter_2015")
    checklist = ReviewerChecklist(
        real_product_or_market_test="yes",
        outcome_externally_observable="yes",
        sources_provided="yes",
        population_or_source_biased="yes",
        enough_evidence_to_map_buckets="yes",
        should_reject="no",
        suitable_for="holdout",
        evidence_tier=1,
    )
    complete = base.model_copy(update={
        "claimed_outcome_proportions": MarketDistribution(
            buyer_action_positive=40, receptive=25,
            uncertain_proof_needed=20, skeptical_resistant=15,
        ),
        "reviewer_checklist": checklist,
        "evidence_tier": 1,
    })
    issues = evaluate_promotion_gates(complete, "holdout", existing_cases=load_all_cases())
    assert any("anti-leakage" in i.lower() or "clean holdout" in i.lower() for i in issues), issues


def test_distribution_mapping_requires_candidate():
    # The candidate is the source of truth; a distribution mapping validated in
    # isolation (candidate=None) cannot be trusted -> blocked.
    p = ProposedOutcomeMapping(
        candidate_id="x",
        mapping_type="direct_observed_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="observed"),
        denominator_type="independent_voices",
        denominator_count=1000,
        denominator_quality="fixed_external_census",
        estimate_quality="audited_official",
    )
    res = validate_mapping(p, None)
    assert not res.ok
    assert "G_candidate_required" in res.gate_codes


def test_distribution_mapping_candidate_mismatch_blocked():
    p = ProposedOutcomeMapping(
        candidate_id="pebble_time_kickstarter_2015",
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="assumption"),
        assumptions=["prior"],
        uncertainty_flags=["assumption_based_mapping"],
        denominator_type="backers",
        denominator_count=78471,
        denominator_quality="self_selected_funnel_counted",
    )
    res = validate_mapping(p, _cand("humane_ai_pin_launch_2024"))  # wrong candidate
    assert not res.ok
    assert "G_candidate_required" in res.gate_codes


def test_candidate_none_bypass_is_closed_for_returns():
    # Red-team bypass: omit the returns flag from the proposal and validate without
    # a candidate. Now blocked by the candidate-required gate (cannot launder it).
    p = ProposedOutcomeMapping(
        candidate_id="humane_ai_pin_launch_2024",
        mapping_type="direct_observed_distribution",
        proposed_proportions=_props(35, 20, 15, 30),
        bucket_rationales=_rationales(others="observed"),
        denominator_type="independent_voices",
        denominator_count=10000,
        denominator_quality="fixed_external_census",
        estimate_quality="audited_official",
        uncertainty_flags=[],  # incriminating flags omitted
    )
    assert validate_mapping(p, None).ok is False  # candidate required
    # and WITH the real candidate, the returns flag surfaces from the source of truth
    res = validate_mapping(p, _cand("humane_ai_pin_launch_2024"))
    assert not res.ok
    assert "G6_returns_not_skeptic" in res.gate_codes


def test_estimate_based_direct_observed_is_blocked():
    p = ProposedOutcomeMapping(
        candidate_id="est_case",
        mapping_type="direct_observed_distribution",
        proposed_proportions=_props(),
        bucket_rationales=_rationales(others="observed"),
        denominator_type="independent_voices",
        denominator_count=1000,
        denominator_quality="representative_random_sample",
        estimate_quality="third_party_estimate",
        confidence="high",
    )
    res = validate_mapping(p, _synthetic_candidate("est_case"))
    assert not res.ok
    assert "G11_estimate_floor" in res.gate_codes


def test_assumption_labeled_requires_observed_buyer_anchor_and_rationales():
    p = ProposedOutcomeMapping(
        candidate_id="vibe",
        mapping_type="assumption_labeled_distribution",
        proposed_proportions=_props(),
        bucket_rationales=[],  # no anchor, all four invented "from a vibe"
        assumptions=["I made all four buckets up"],
        uncertainty_flags=["assumption_based_mapping"],
        denominator_type="backers",
        denominator_count=100,
        denominator_quality="self_selected_funnel_counted",
    )
    res = validate_mapping(p, _synthetic_candidate("vibe"))
    assert not res.ok
    assert "G8_anchor_masquerade" in res.gate_codes


def test_g3_gate_code_on_distribution_without_proportions():
    p = ProposedOutcomeMapping(
        candidate_id="noprops", mapping_type="direct_observed_distribution"
    )
    res = validate_mapping(p, _synthetic_candidate("noprops"))
    assert not res.ok
    assert "G3_buyer_numerator_only" in res.gate_codes


def test_g10_always_warns_and_forced_confidence_does_not_mutate_proposal():
    cand = _cand("clubhouse_app_launch_2021")  # free anchor -> forced low
    p = ProposedOutcomeMapping(
        candidate_id=cand.candidate_id,
        mapping_type="action_anchor_only",
        buyer_anchor_signal_type="download",
        buyer_anchor_count=8100000,
        confidence="high",
    )
    res = validate_mapping(p, cand)
    assert any(w.startswith("G10") for w in res.warnings)
    assert res.forced_confidence == "low"
    assert p.confidence == "high"  # the proposal itself is not mutated


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


def test_readiness_zero_direct_blocks_phase_15e():
    classifications = [(_cand(cid), classify_candidate(_cand(cid))[0]) for cid in _ALL_IDS]
    rep = mapping_readiness(classifications, ledger_cases=load_all_cases())
    assert rep["n_direct_observed_distribution_cases"] == 0
    assert rep["phase_15e_blocked"] is True
    assert rep["mapping_type_breakdown"]["action_anchor_only"] == 6
    assert rep["mapping_type_breakdown"]["evidence_only"] == 2


def test_readiness_flags_pebble_non_independence_and_kickstarter_concentration():
    classifications = [(_cand(cid), classify_candidate(_cand(cid))[0]) for cid in _ALL_IDS]
    rep = mapping_readiness(classifications, ledger_cases=load_all_cases())
    assert "pebble" in rep["non_independent_entity_clusters"]
    assert rep["weak_mapping_warning"] is True
    assert rep["source_concentration_over_cap"] == {"kickstarter": 5}
    assert "G12_concentration_cap" in rep["gate_codes"]


def test_readiness_assumption_cap_enforced():
    # 3 direct + 2 assumption: cap = floor(3/3)=1, so 2 assumption is over cap.
    cands = [_cand(cid) for cid in _ALL_IDS[:5]]
    classifications = (
        [(cands[0], "direct_observed_distribution")]
        + [(cands[1], "direct_observed_distribution")]
        + [(cands[2], "direct_observed_distribution")]
        + [(cands[3], "assumption_labeled_distribution")]
        + [(cands[4], "assumption_labeled_distribution")]
    )
    rep = mapping_readiness(classifications, ledger_cases=load_all_cases())
    assert rep["assumption_labeled_cap"] == 1
    assert rep["assumption_labeled_over_cap"] is True


# --------------------------------------------------------------------------
# Isolation + dataset-unchanged
# --------------------------------------------------------------------------


def test_proposal_carries_isolation_marker():
    p = ProposedOutcomeMapping(candidate_id="x", mapping_type="evidence_only")
    assert p.purpose == MAPPING_PROPOSAL_PURPOSE
    assert p.human_approved is False


def test_mapping_proposals_dir_absent_from_manifest():
    manifest = json.loads((_CASES_DIR / "manifest.json").read_text())
    paths = {f["path"] for f in manifest["files"]}
    assert not any("mapping_proposal" in p for p in paths)
    assert not any("candidates" in p for p in paths)


def test_official_dataset_unchanged():
    all_cases = load_all_cases()
    # seed training frozen at 6; candidates isolated; no factory ingestion.
    # (Phase 16A adds blind prospective PENDING locks via the SEPARATE 15I bridge.)
    assert len([c for c in all_cases if c.anti_overfit.used_for_training]) == 6
    cands = load_all_candidates(None)
    assert len(cands) == 8
    assert all(c.status == "needs_review" and c.claimed_outcome_proportions is None for c in cands)
    # factory ingestion targets stay empty; pending locks are blind (observed=None)
    for f in ("holdout_cases.json", "training_cases.json"):
        assert json.loads((_CASES_DIR / f).read_text()) == []
    for c in all_cases:
        if c.metadata.validation_status == "pending":
            assert c.observed is None and not c.anti_overfit.used_for_training


def test_template_and_example_files_present_and_valid():
    tmpl = json.loads((_PROPOSALS_DIR / "TEMPLATE.json").read_text())
    assert tmpl["purpose"] == MAPPING_PROPOSAL_PURPOSE
    assert tmpl["human_approved"] is False
    example = (_PROPOSALS_DIR / "EXAMPLE_action_anchor_only_exploding_kittens.json")
    proposed = ProposedOutcomeMapping.model_validate(json.loads(example.read_text()))
    res = validate_mapping(proposed, _cand(proposed.candidate_id))
    assert res.ok, res.issues


def test_validate_mapping_mutates_nothing():
    cand = _cand("humane_ai_pin_launch_2024")
    before_cand = cand.model_dump()
    p = ProposedOutcomeMapping(
        candidate_id=cand.candidate_id, mapping_type="action_anchor_only",
        buyer_anchor_signal_type="purchase", buyer_anchor_count=10000,
    )
    before_p = p.model_dump()
    validate_mapping(p, cand)
    assert cand.model_dump() == before_cand
    assert p.model_dump() == before_p


# --------------------------------------------------------------------------
# CLI — dry-run / read-only
# --------------------------------------------------------------------------


def _load_cli():
    path = _API_ROOT / "scripts" / "phase_15l_mapping_protocol.py"
    spec = importlib.util.spec_from_file_location("phase_15l_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_classify_and_dashboard_exit_zero(capsys):
    cli = _load_cli()
    assert cli.main(["classify"]) == 0
    assert cli.main(["dashboard"]) == 0
    out = capsys.readouterr().out
    assert "Phase 15E: BLOCKED" in out


def test_cli_mapping_template_dry_run_writes_nothing(tmp_path):
    cli = _load_cli()
    out = tmp_path / "proposal.json"
    rc = cli.main([
        "mapping-template", "--candidate-id", "humane_ai_pin_launch_2024",
        "--out", str(out), "--dry-run",
    ])
    assert rc == 0
    assert not out.exists()  # dry-run must not write


def test_cli_validate_mapping_blocks_fabrication(tmp_path):
    cli = _load_cli()
    bad = {
        "candidate_id": "pebble_time_kickstarter_2015",
        "mapping_type": "direct_observed_distribution",
        "proposed_proportions": _props(),
        "bucket_rationales": _rationales(others="assumption"),
        "denominator_type": "backers", "denominator_count": 78471,
        "denominator_quality": "self_selected_funnel_counted",
    }
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(bad))
    assert cli.main(["validate-mapping", "--from", str(f)]) == 1  # REFUSED
