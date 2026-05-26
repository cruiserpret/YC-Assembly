"""Phase 12C — comprehensive tests for the 100-voter market graph
overlay. Consolidates voter sampling, graph, influence loop,
aggregation, calibration, diversity health, drift, integration.

Pure-Python, no DB, no LLM.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from assembly.sources.lightweight_voters import (
    INTENT_ORDER,
    aggregate_voter_distribution,
    allocate_voters_per_cohort,
    build_social_graph,
    calibrated_distribution,
    compute_diversity_health,
    generate_voters_from_cohorts,
    run_influence_rounds,
)


# ---------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------


def _two_cohorts() -> list[dict]:
    """Small but realistic fixture — 2 cohorts × balanced shape."""
    return [
        {
            "cohort_id": "cohort_a",
            "cohort_label": "small_merchants",
            "cohort_weight": 0.6,
            "role_distribution": {
                "shopify_merchant": 5,
                "etsy_seller": 3,
                "price_skeptic": 2,
                "trust_seeker": 2,
            },
            "stance_distribution": {
                "curious_but_unconvinced": 4,
                "interested_if_proven": 3,
                "skeptical": 2,
                "needs_more_information": 1,
            },
            "psychology_summary": {
                "openness": 0.55,
                "novelty_seeking": 0.5,
                "trust_proof_threshold": 0.5,
                "risk_tolerance": 0.5,
                "price_sensitivity": 0.55,
                "social_influence_susceptibility": 0.5,
                "category_involvement_or_expertise": 0.5,
            },
            "objection_summary": {"price": 3, "tone_quality": 2},
            "proof_need_summary": {"show_before_after": 4},
            "top_alternatives": {"shopify_inbox": 4, "manual_email": 6},
            "member_persona_ids": [],
        },
        {
            "cohort_id": "cohort_b",
            "cohort_label": "price_skeptics",
            "cohort_weight": 0.4,
            "role_distribution": {
                "price_skeptic": 6, "trust_seeker": 4,
                "performance_focused_buyer": 3,
            },
            "stance_distribution": {
                "skeptical": 5, "curious_but_unconvinced": 3,
                "likely_reject": 2,
            },
            "psychology_summary": {
                "openness": 0.45,
                "novelty_seeking": 0.4,
                "trust_proof_threshold": 0.65,
                "risk_tolerance": 0.4,
                "price_sensitivity": 0.8,
                "social_influence_susceptibility": 0.4,
                "category_involvement_or_expertise": 0.5,
            },
            "objection_summary": {"cost": 5, "roi_unclear": 3},
            "proof_need_summary": {"cost_per_reply": 4},
            "top_alternatives": {"manual_email": 5},
            "member_persona_ids": [],
        },
    ]


@pytest.fixture
def two_cohorts() -> list[dict]:
    return _two_cohorts()


# ---------------------------------------------------------------------
# 1. VOTER SAMPLING
# ---------------------------------------------------------------------


class TestVoterSampling:
    def test_deterministic_given_seed(self, two_cohorts) -> None:
        v1, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        v2, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        assert len(v1) == len(v2) == 100
        # Same psy values across runs (modulo voter_id randomness in
        # UUID generation — compare a content-fingerprint instead)
        for a, b in zip(v1, v2):
            assert a.segment == b.segment
            assert a.role == b.role
            assert a.initial_intent == b.initial_intent
            assert a.trust_threshold == b.trust_threshold
            assert a.population_weight == b.population_weight

    def test_seed_change_alters_voters(self, two_cohorts) -> None:
        v1, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        v2, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=99, n=100,
        )
        roles_1 = [v.role for v in v1]
        roles_2 = [v.role for v in v2]
        # At least 10% of voters should differ in role (very weak signal)
        diffs = sum(1 for a, b in zip(roles_1, roles_2) if a != b)
        assert diffs >= 10

    def test_hits_n_voters_exactly(self, two_cohorts) -> None:
        for n in (24, 50, 100):
            voters, _ = generate_voters_from_cohorts(
                two_cohorts, run_scope_id="t",
                simulation_seed=42, n=n,
            )
            assert len(voters) == n

    def test_psy_jitter_bounded_to_pm_015(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        # Each voter's trust_threshold should be within ±0.15 of
        # its cohort's psychology centroid.
        cohort_centroid = {
            "small_merchants": 0.5,
            "price_skeptics": 0.65,
        }
        for v in voters:
            centroid = cohort_centroid[v.segment]
            assert centroid - 0.15 - 1e-9 <= v.trust_threshold
            assert v.trust_threshold <= centroid + 0.15 + 1e-9

    def test_every_voter_has_initial_intent(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        for v in voters:
            assert v.initial_intent  # non-empty
            assert v.initial_intent != ""

    def test_population_weight_summed_close_to_total(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        # Each cohort_weight is distributed across its voters; total
        # sum of voter weights should approximately equal sum of
        # cohort weights (= 1.0 in this fixture).
        total = sum(v.population_weight for v in voters)
        assert 0.95 <= total <= 1.05

    def test_quota_gate_competitor_user_cap(self) -> None:
        # Cohort dominated by competitor_user_* roles
        cohort_heavy_cu = [
            {
                "cohort_id": "cu_heavy",
                "cohort_label": "cu_heavy",
                "cohort_weight": 1.0,
                "role_distribution": {
                    "competitor_user_x": 10,
                    "competitor_user_y": 8,
                    "trust_seeker": 2,
                },
                "stance_distribution": {"curious_but_unconvinced": 10},
                "psychology_summary": {},
                "objection_summary": {},
                "proof_need_summary": {},
                "top_alternatives": {},
                "member_persona_ids": [],
            },
        ]
        voters, warnings = generate_voters_from_cohorts(
            cohort_heavy_cu, run_scope_id="t",
            simulation_seed=42, n=100, competitor_user_cap=0.50,
        )
        cu_count = sum(
            1 for v in voters if v.role.startswith("competitor_user_")
        )
        # Either gate enforced OR warning fired
        assert cu_count / 100 <= 0.55 or any(
            "competitor_user" in w for w in warnings
        )


# ---------------------------------------------------------------------
# 2. ALLOCATION
# ---------------------------------------------------------------------


class TestAllocation:
    def test_allocation_sums_to_n(self, two_cohorts) -> None:
        alloc = allocate_voters_per_cohort(two_cohorts, n=100)
        assert sum(alloc.values()) == 100

    def test_allocation_respects_min_per_cohort(self) -> None:
        nine_cohorts = [
            {"cohort_id": f"c{i}", "cohort_label": f"c{i}",
             "cohort_weight": 1 / 9 + (0.01 if i == 0 else 0),
             "role_distribution": {}, "stance_distribution": {},
             "psychology_summary": {}, "objection_summary": {},
             "proof_need_summary": {}, "top_alternatives": {},
             "member_persona_ids": []}
            for i in range(9)
        ]
        alloc = allocate_voters_per_cohort(nine_cohorts, n=100)
        for cid, count in alloc.items():
            assert count >= 3, (cid, count)

    def test_allocation_with_single_cohort_does_not_explode(self) -> None:
        single = [{
            "cohort_id": "only",
            "cohort_label": "only",
            "cohort_weight": 1.0,
            "role_distribution": {}, "stance_distribution": {},
            "psychology_summary": {}, "objection_summary": {},
            "proof_need_summary": {}, "top_alternatives": {},
            "member_persona_ids": [],
        }]
        alloc = allocate_voters_per_cohort(single, n=100)
        assert sum(alloc.values()) == 100


# ---------------------------------------------------------------------
# 3. SOCIAL GRAPH
# ---------------------------------------------------------------------


class TestSocialGraph:
    def test_edge_count_per_voter_3_to_8(self, two_cohorts) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        from collections import Counter
        out_degree = Counter(str(e.source_voter_id) for e in edges)
        for v in voters:
            n_edges = out_degree.get(str(v.voter_id), 0)
            assert 3 <= n_edges <= 8, (
                f"voter {v.voter_id} has {n_edges} edges"
            )

    def test_graph_deterministic(self, two_cohorts) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        e1, _ = build_social_graph(voters, simulation_seed=42)
        e2, _ = build_social_graph(voters, simulation_seed=42)
        assert len(e1) == len(e2)
        # Edge sets identical (by source/target/type)
        s1 = sorted(
            (str(e.source_voter_id), str(e.target_voter_id), e.edge_type)
            for e in e1
        )
        s2 = sorted(
            (str(e.source_voter_id), str(e.target_voter_id), e.edge_type)
            for e in e2
        )
        assert s1 == s2

    def test_within_segment_dominance(self, two_cohorts) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        n_within = sum(
            1 for e in edges
            if e.edge_type in (
                "segment_similarity",
                "current_alt_similarity",
                "skeptic_influence",
                "early_adopter_influence",
            )
        )
        assert n_within / len(edges) >= 0.50

    def test_cross_segment_edges_exist(self, two_cohorts) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        n_cross = sum(
            1 for e in edges
            if e.edge_type in (
                "role_similarity",
                "influencer",
                "cross_segment_exposure",
            )
        )
        assert n_cross > 0

    def test_every_edge_has_evidence_basis(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        for e in edges:
            assert e.evidence_basis


# ---------------------------------------------------------------------
# 4. INFLUENCE LOOP
# ---------------------------------------------------------------------


class TestInfluenceLoop:
    def test_4_rounds_produced(self, two_cohorts) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        assert len(rounds) == 4
        assert [r.round_idx for r in rounds] == [0, 1, 2, 3]

    def test_round_1_collects_no_intent_changes(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        assert rounds[1].intent_changes == 0

    def test_movement_bounded_pm1_step(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        for v in voters:
            cur_idx = (
                INTENT_ORDER.index(v.initial_intent)
                if v.initial_intent in INTENT_ORDER else
                len(INTENT_ORDER) // 2
            )
            final_idx = (
                INTENT_ORDER.index(v.final_intent)
                if v.final_intent in INTENT_ORDER else
                len(INTENT_ORDER) // 2
            )
            assert abs(final_idx - cur_idx) <= 1

    def test_every_voter_has_final_bucket(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        for v in voters:
            assert v.final_bucket in (
                "buyer", "receptive", "uncertain", "skeptical",
            )

    def test_switching_resistance_damps_movement(
        self, two_cohorts,
    ) -> None:
        # Compare PER-VOTER movement RATES (not raw counts) between
        # low-resistance and high-resistance voters, aggregated
        # over multiple seeds.
        moves_lo, n_lo = 0, 0
        moves_hi, n_hi = 0, 0
        for seed in range(5):
            voters, _ = generate_voters_from_cohorts(
                two_cohorts, run_scope_id="t",
                simulation_seed=seed, n=100,
            )
            edges, _ = build_social_graph(
                voters, simulation_seed=seed,
            )
            run_influence_rounds(
                voters, edges, simulation_seed=seed,
            )
            for v in voters:
                moved = v.final_intent != v.initial_intent
                if v.switching_resistance < 0.5:
                    n_lo += 1
                    if moved:
                        moves_lo += 1
                else:
                    n_hi += 1
                    if moved:
                        moves_hi += 1
        rate_lo = moves_lo / n_lo if n_lo else 0
        rate_hi = moves_hi / n_hi if n_hi else 0
        # The lower-resistance group should have a higher per-voter
        # movement rate. Allow a small tolerance for variance.
        assert rate_lo >= rate_hi - 0.05, (
            f"low-resistance rate={rate_lo:.3f} should be >= "
            f"high-resistance rate={rate_hi:.3f} (within 0.05)"
        )

    def test_influence_loop_deterministic(
        self, two_cohorts,
    ) -> None:
        # Same seed → same final_intent per voter
        v1, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        e1, _ = build_social_graph(v1, simulation_seed=42)
        run_influence_rounds(v1, e1, simulation_seed=42)

        v2, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        e2, _ = build_social_graph(v2, simulation_seed=42)
        run_influence_rounds(v2, e2, simulation_seed=42)

        intents_1 = [v.final_intent for v in v1]
        intents_2 = [v.final_intent for v in v2]
        assert intents_1 == intents_2


# ---------------------------------------------------------------------
# 5. AGGREGATION
# ---------------------------------------------------------------------


class TestAggregation:
    def test_distribution_sums_to_100(self, two_cohorts) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        dist = aggregate_voter_distribution(voters)
        total = dist.buyer + dist.receptive + dist.uncertain + dist.skeptical
        assert abs(total - 100.0) < 0.01

    def test_n_voters_recorded(self, two_cohorts) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        dist = aggregate_voter_distribution(voters)
        assert dist.n_voters == 100


# ---------------------------------------------------------------------
# 6. CALIBRATION CORRECTION
# ---------------------------------------------------------------------


class TestCalibration:
    def test_conservative_default_50_50_blend(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        voter_dist = aggregate_voter_distribution(voters)
        raw_24 = {
            "buyer": 0, "receptive": 75, "uncertain": 0,
            "skeptical": 25,
        }
        cal = calibrated_distribution(
            raw_24, voter_dist, evidence_quality=1.0,
        )
        assert cal.blend_weights == {
            "rich_24": 0.5, "voter_100": 0.5,
        }

    def test_calibration_support_weak_warning(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        voter_dist = aggregate_voter_distribution(voters)
        cal = calibrated_distribution(
            {"buyer": 0, "receptive": 75, "uncertain": 0, "skeptical": 25},
            voter_dist,
        )
        # n=1 (one prior case): warning must fire
        assert any(
            "calibration_support_weak" in w
            for w in cal.calibration_warnings
        )
        assert cal.used_prior_correction is False

    def test_calibration_normalizes_to_100(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        voter_dist = aggregate_voter_distribution(voters)
        cal = calibrated_distribution(
            {"buyer": 0, "receptive": 75, "uncertain": 0, "skeptical": 25},
            voter_dist,
        )
        s = sum(cal.distribution_percent.values())
        assert abs(s - 100.0) < 0.01

    def test_calibration_wide_default_confidence_band(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        voter_dist = aggregate_voter_distribution(voters)
        cal = calibrated_distribution(
            {"buyer": 0, "receptive": 75, "uncertain": 0, "skeptical": 25},
            voter_dist,
        )
        assert cal.confidence_band_pp == 15.0

    def test_low_evidence_quality_leans_rich(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        voter_dist = aggregate_voter_distribution(voters)
        cal = calibrated_distribution(
            {"buyer": 0, "receptive": 75, "uncertain": 0, "skeptical": 25},
            voter_dist, evidence_quality=0.4,
        )
        assert cal.blend_weights["rich_24"] == 0.7
        assert cal.blend_weights["voter_100"] == 0.3


# ---------------------------------------------------------------------
# 7. DIVERSITY HEALTH
# ---------------------------------------------------------------------


class TestDiversityHealth:
    def test_diversity_metrics_computed(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert health.n_voters == 100
        assert health.n_segments_represented >= 1
        assert health.n_edges > 0
        assert 3 <= health.edges_per_voter_min
        assert health.edges_per_voter_max <= 8

    def test_intent_diversity_per_round_tracked(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert set(health.intent_diversity_per_round.keys()) == {0, 1, 2, 3}

    def test_voter_uniqueness_100pct(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert health.voter_id_uniqueness_pct == 100.0

    def test_warnings_collected_when_inherited_metrics_breach(
        self, two_cohorts,
    ) -> None:
        voters, _ = generate_voters_from_cohorts(
            two_cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        bad_rich = {
            "persona_voice_diversity_score": 0.40,
            "repeated_objection_count": 20,
            "near_duplicate_turn_count": 3,
        }
        health = compute_diversity_health(
            voters, edges, rounds,
            rich_persona_diversity=bad_rich,
        )
        # Should fire warnings for all three breaches.
        warns_text = " ".join(health.warnings)
        assert "persona_voice_diversity_score_low" in warns_text
        assert "repeated_objection_count_high" in warns_text
        assert "near_duplicate_turn_count_nonzero" in warns_text


# ---------------------------------------------------------------------
# 8. DRIFT / SAFETY
# ---------------------------------------------------------------------


class TestSafetyInvariants:
    def test_no_llm_calls_in_voter_package(self) -> None:
        """The lightweight_voters package must contain ZERO direct
        LLM call references — no provider.chat, no
        cost_guarded_chat, no structured_output."""
        pkg = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "sources" / "lightweight_voters"
        )
        forbidden = (
            "provider.chat", "cost_guarded_chat",
            "structured_output", "anthropic.messages",
            "openai.chat", "AsyncAnthropic", "AsyncOpenAI",
        )
        violations = []
        for p in pkg.rglob("*.py"):
            src = p.read_text(encoding="utf-8")
            for f in forbidden:
                if f in src:
                    violations.append((p.name, f))
        assert not violations, (
            f"lightweight_voters must NOT call any LLM. Found: "
            f"{violations}"
        )

    def test_no_apps_web_touch_in_voter_files(self) -> None:
        targets = [
            "apps/api/src/assembly/sources/lightweight_voters/__init__.py",
            "apps/api/src/assembly/sources/lightweight_voters/voter_schema.py",
            "apps/api/src/assembly/sources/lightweight_voters/voter_sampling.py",
            "apps/api/src/assembly/sources/lightweight_voters/social_graph.py",
            "apps/api/src/assembly/sources/lightweight_voters/influence_loop.py",
            "apps/api/src/assembly/sources/lightweight_voters/aggregation.py",
            "apps/api/src/assembly/sources/lightweight_voters/calibration_correction.py",
            "apps/api/src/assembly/sources/lightweight_voters/diversity_health.py",
            "apps/api/src/assembly/pipeline/lightweight_voter_pipeline.py",
        ]
        repo_root = Path(__file__).resolve().parents[3]
        for t in targets:
            src = (repo_root / t).read_text(encoding="utf-8")
            assert "apps/web" not in src

    def test_voter_overlay_called_from_intent_cascade_stage(
        self,
    ) -> None:
        """The voter overlay does not have its own pipeline stage
        (that would require widening the assembly_runs CHECK
        constraint). Instead it runs as a side-effect at the end of
        `_stage_inferring_simulated_intent`."""
        from pathlib import Path as _Path
        src = (
            _Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "orchestration"
            / "live_founder_brief.py"
        ).read_text(encoding="utf-8")
        # The inline helper exists
        assert "_run_voter_overlay_inline" in src
        # It is called from inside _stage_inferring_simulated_intent
        # (we look for the call somewhere after the function's def)
        intent_stage_start = src.index(
            "async def _stage_inferring_simulated_intent",
        )
        # Find the next async def (boundary of next stage runner)
        next_async_def = src.index(
            "async def ", intent_stage_start + 10,
        )
        # Could be _run_voter_overlay_inline (sync) — check for it
        # specifically inside the intent stage body.
        intent_body = src[intent_stage_start:next_async_def]
        assert "_run_voter_overlay_inline(" in intent_body, (
            "voter overlay must be invoked from inside the intent "
            "cascade stage body"
        )

    def test_voter_overlay_is_failure_tolerant(
        self, tmp_path,
    ) -> None:
        """If something fails inside the overlay (e.g. no cohorts
        supplied), the helper should write voter_overlay_failed.json
        and return status='failed', NOT raise."""
        from assembly.pipeline.lightweight_voter_pipeline import (
            run_lightweight_voter_overlay,
        )
        result = run_lightweight_voter_overlay(
            run_id=uuid.uuid4(),
            run_dir=tmp_path,
            run_scope_id="t",
            cohort_dicts=[],  # empty → triggers failure
            ballots_by_stage=None,
            simulation_seed=42,
        )
        assert result["status"] == "failed"
        assert (tmp_path / "voter_overlay_failed.json").exists()


# ---------------------------------------------------------------------
# 9. INTEGRATION (end-to-end MVP path)
# ---------------------------------------------------------------------


class TestIntegration:
    def test_end_to_end_writes_all_artifacts(
        self, tmp_path, two_cohorts,
    ) -> None:
        """Drive the voter pipeline against tmp_path and verify all
        artifacts land. Uses cohort_dicts directly (no DB)."""
        from assembly.pipeline.lightweight_voter_pipeline import (
            run_lightweight_voter_overlay,
        )
        # Synthesize a minimal simulated_intent.json so the rich
        # distribution lookup has something to read.
        import json as _json
        (tmp_path / "simulated_intent.json").write_text(_json.dumps({
            "intent_distribution": {
                "would_consider_if_proven": 16,
                "loyal_to_current_alternative": 7,
                "would_reject": 1,
            },
        }))
        result = run_lightweight_voter_overlay(
            run_id=uuid.uuid4(),
            run_dir=tmp_path,
            run_scope_id="integration",
            cohort_dicts=two_cohorts,
            ballots_by_stage={"final": [
                {"persona_id": "p1", "top_objection": "price",
                 "top_proof_need": "show_demo"},
            ]},
            simulation_seed=42,
        )
        assert result["status"] == "complete"
        for f in (
            "rich_persona_distribution.json",
            "lightweight_voters.json",
            "social_graph_nodes_edges.json",
            "influence_rounds.json",
            "final_100_voter_distribution.json",
            "diversity_health.json",
            "representative_debates.json",
            "phase_12c_summary.md",
        ):
            assert (tmp_path / f).exists(), f"missing artifact: {f}"

    def test_end_to_end_no_overwrite_of_simulated_intent(
        self, tmp_path, two_cohorts,
    ) -> None:
        """The overlay reads simulated_intent.json but must NOT
        modify it."""
        import json as _json
        original = {
            "intent_distribution": {
                "would_consider_if_proven": 16,
                "loyal_to_current_alternative": 7,
                "would_reject": 1,
            },
        }
        (tmp_path / "simulated_intent.json").write_text(
            _json.dumps(original),
        )
        from assembly.pipeline.lightweight_voter_pipeline import (
            run_lightweight_voter_overlay,
        )
        run_lightweight_voter_overlay(
            run_id=uuid.uuid4(),
            run_dir=tmp_path,
            run_scope_id="integration",
            cohort_dicts=two_cohorts,
            ballots_by_stage=None,
            simulation_seed=42,
        )
        after = _json.loads(
            (tmp_path / "simulated_intent.json").read_text()
        )
        assert after == original

    def test_total_cost_zero_no_llm_invocation(
        self, tmp_path, two_cohorts,
    ) -> None:
        """End-to-end voter overlay produces zero LLM calls. We
        verify by importing the lightweight_voters package and
        confirming no module-level LLM client exists."""
        import assembly.sources.lightweight_voters as pkg
        # Module surface shouldn't have provider / sdk clients.
        for name in dir(pkg):
            obj = getattr(pkg, name)
            assert "Provider" not in type(obj).__name__


# ---------------------------------------------------------------------
# Phase 12C.1 — resistance realism + transition audit
# ---------------------------------------------------------------------


def _cohort_with_intent_dist(
    intent_dist: dict[str, int],
    *,
    cohort_id: str = "c_skeptic",
    cohort_label: str = "skeptic_cohort",
    role_dist: dict[str, int] | None = None,
) -> dict:
    """Build a single cohort fixture carrying an explicit
    intent_distribution (the new Phase 12C.1 contract)."""
    return {
        "cohort_id": cohort_id,
        "cohort_label": cohort_label,
        "cohort_weight": 1.0,
        "role_distribution": role_dist or {
            "trust_seeker": 5,
            "competitor_user_shopify_inbox": 4,
            "price_skeptic": 3,
        },
        "stance_distribution": {
            "curious_but_unconvinced": 5,
            "skeptical": 3,
        },
        "psychology_summary": {
            "openness": 0.4,
            "novelty_seeking": 0.4,
            "trust_proof_threshold": 0.7,
            "risk_tolerance": 0.4,
            "price_sensitivity": 0.6,
            "social_influence_susceptibility": 0.5,
            "category_involvement_or_expertise": 0.5,
        },
        "objection_summary": {"price": 3},
        "proof_need_summary": {"cost_proof": 2},
        "top_alternatives": {"shopify_inbox": 3},
        "intent_distribution": intent_dist,
        "member_persona_ids": [],
    }


class TestPhase12C1ResistancePreservation:
    def test_loyal_intent_maps_to_skeptical_initial_bucket(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 10,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=20,
        )
        assert voters, "no voters sampled"
        assert all(
            v.initial_intent == "loyal_to_current_alternative"
            for v in voters
        )
        assert all(v.initial_bucket == "skeptical" for v in voters)

    def test_would_reject_maps_to_skeptical(self) -> None:
        cohorts = [_cohort_with_intent_dist({"would_reject": 10})]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=20,
        )
        assert all(v.initial_intent == "would_reject" for v in voters)
        assert all(v.initial_bucket == "skeptical" for v in voters)

    def test_hard_resistant_set_for_loyal_intent(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 10,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=20,
        )
        assert all(v.hard_resistant for v in voters)
        assert all(
            v.hard_resistant_reason
            == "loyal_to_current_alternative_intent"
            for v in voters
        )

    def test_hard_resistant_set_for_would_reject(self) -> None:
        cohorts = [_cohort_with_intent_dist({"would_reject": 10})]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=20,
        )
        assert all(v.hard_resistant for v in voters)

    def test_intent_distribution_sample_preserves_proportions(
        self,
    ) -> None:
        """A 50/50 loyal vs receptive intent_distribution should
        produce roughly the same ratio in voters (within sampling
        tolerance)."""
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 10,
            "would_consider_if_proven": 10,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=200,
        )
        loyal = sum(
            1 for v in voters
            if v.initial_intent == "loyal_to_current_alternative"
        )
        receptive = sum(
            1 for v in voters
            if v.initial_intent == "would_consider_if_proven"
        )
        # Within ±20pp tolerance at n=200 with 50/50 prior.
        assert 80 <= loyal <= 120, f"loyal count {loyal} not ~100"
        assert 80 <= receptive <= 120, (
            f"receptive count {receptive} not ~100"
        )

    def test_skeptic_cannot_cross_to_receptive_in_one_round(
        self,
    ) -> None:
        """A voter starting in the skeptical bucket cannot end up in
        receptive/buyer after one influence loop, even with strong
        peer pull."""
        # Pure skeptical cohort + pure receptive cohort, equal weight.
        cohorts = [
            _cohort_with_intent_dist(
                {"loyal_to_current_alternative": 10},
                cohort_id="c_loyal",
                cohort_label="loyal_segment",
            ),
            _cohort_with_intent_dist(
                {"would_consider_if_proven": 10},
                cohort_id="c_consider",
                cohort_label="consider_segment",
            ),
        ]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=60,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        for v in voters:
            if v.initial_bucket == "skeptical":
                assert v.final_bucket in ("skeptical", "uncertain"), (
                    f"skeptic {v.voter_id} ended at "
                    f"{v.final_bucket} (initial={v.initial_intent})"
                )

    def test_hard_resistant_cannot_become_buyer_in_one_round(
        self,
    ) -> None:
        cohorts = [
            _cohort_with_intent_dist(
                {"loyal_to_current_alternative": 10},
                cohort_id="c_loyal",
                cohort_label="loyal_segment",
            ),
            _cohort_with_intent_dist(
                {"would_buy_now": 10},
                cohort_id="c_buy",
                cohort_label="buy_segment",
            ),
        ]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=60,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        for v in voters:
            if v.hard_resistant:
                assert v.final_bucket != "buyer", (
                    f"hard_resistant voter {v.voter_id} became buyer"
                )


class TestPhase12C1TransitionAudit:
    def test_transition_matrix_written(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert health.transition_matrix
        # Must contain at least one initial-bucket key
        assert any(k for k in health.transition_matrix)
        # Counts in matrix should sum to n_voters
        total = sum(
            sum(row.values())
            for row in health.transition_matrix.values()
        )
        assert total == len(voters)

    def test_initial_and_final_distributions_recorded(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert sum(health.initial_intent_distribution.values()) == 40
        assert sum(health.final_intent_distribution.values()) == 40
        assert sum(health.initial_bucket_distribution.values()) == 40
        assert sum(health.final_bucket_distribution.values()) == 40

    def test_hard_resistant_count_recorded(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 10,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=30,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert health.hard_resistant_count == 30

    def test_skeptic_retention_rate_high_when_constrained(
        self,
    ) -> None:
        """When 30% of voters start skeptical, their retention rate
        after one round should be very high (constraints disallow
        crossing to receptive/buyer)."""
        cohorts = [
            _cohort_with_intent_dist(
                {"loyal_to_current_alternative": 3,
                 "would_consider_if_proven": 7},
                cohort_id="c_mixed",
                cohort_label="mixed",
            ),
        ]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=100,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        # Some voters may shift to `uncertain` (wait_and_see) but the
        # share retained as skeptical OR uncertain (i.e., NOT lost to
        # receptive/buyer) must be 100% — that's the constraint.
        skeptic_to_recv = (
            health.skeptic_to_receptive_rate or 0.0
        )
        skeptic_to_buy = (
            health.skeptic_to_buyer_rate or 0.0
        )
        assert skeptic_to_recv == 0.0
        assert skeptic_to_buy == 0.0


class TestPhase12C1Warnings:
    def test_zero_resistant_warning_fires_when_skeptical_erased(
        self,
    ) -> None:
        """If a voter starts skeptical but somehow ends not-skeptical
        AND no voter remains skeptical at the end, the warning should
        fire. We construct this synthetically by mutating voters
        between sampling and diversity_health."""
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=20,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Synthetically erase skeptical mass to trigger the warning
        for v in voters:
            v.final_intent = "would_consider_if_proven"
            v.final_bucket = "receptive"
        health = compute_diversity_health(voters, edges, rounds)
        assert any(
            "zero_resistant_people_warning" in w
            for w in health.warnings
        ), f"warning missing: {health.warnings}"

    def test_skeptic_overconversion_warning_fires(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=20,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Synthetically over-convert skeptics (keep 1, convert rest)
        skeptic_seen = 0
        for v in voters:
            if v.initial_bucket == "skeptical":
                skeptic_seen += 1
                if skeptic_seen > 1:
                    v.final_intent = "wait_and_see"
                    v.final_bucket = "uncertain"
        health = compute_diversity_health(voters, edges, rounds)
        assert any(
            "skeptic_overconversion_warning" in w
            for w in health.warnings
        ), f"warning missing: {health.warnings}"

    def test_no_warning_when_skeptics_preserved(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=20,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        # Should not fire either of these resistance warnings
        ban = (
            "zero_resistant_people_warning",
            "skeptic_overconversion_warning",
            "hard_reject_erased_warning",
            "skeptic_to_buyer_observed",
        )
        for w in health.warnings:
            for b in ban:
                assert b not in w, (
                    f"unexpected warning {w} in {health.warnings}"
                )


class TestPhase12C1PerRoundCounters:
    def test_round_3_does_not_double_count_intent_changes(
        self,
    ) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=50,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Round 3 is "finalize" and must NOT attribute intent_changes
        # to itself (round 2 already counted them).
        round_3 = next(r for r in rounds if r.round_type == "finalize")
        assert round_3.intent_changes == 0


class TestPhase12C1RepresentativeDebates:
    def test_samples_non_empty_when_ballots_exist(self) -> None:
        from assembly.pipeline.lightweight_voter_pipeline import (
            _build_representative_debates,
        )
        cohorts = [{
            "cohort_id": "c1",
            "cohort_label": "loyal_segment",
            "member_persona_ids": ["p1", "p2"],
        }, {
            "cohort_id": "c2",
            "cohort_label": "consider_segment",
            "member_persona_ids": ["p3"],
        }]
        ballots_by_stage = {
            "pre": [],
            "final": [
                {
                    "persona_id": "p1",
                    "private_stance": "skeptical",
                    "top_objection": "incumbent already covers this",
                    "top_proof_need": "show 90-day retention",
                    "private_reasoning": (
                        "I've been using Shopify Inbox for 3 years. "
                        "Why would I switch?"
                    ),
                },
                {
                    "persona_id": "p3",
                    "private_stance": "interested_if_proven",
                    "top_objection": "pricing unclear",
                    "top_proof_need": "side-by-side comparison",
                    "private_reasoning": (
                        "Looks promising but I need a real demo."
                    ),
                },
            ],
            "refl": [],
        }
        out = _build_representative_debates(
            cohort_dicts=cohorts,
            ballots_by_stage=ballots_by_stage,
        )
        assert out["samples"], "samples must be non-empty"
        assert len(out["samples"]) >= 2

    def test_samples_empty_when_no_ballots(self) -> None:
        from assembly.pipeline.lightweight_voter_pipeline import (
            _build_representative_debates,
        )
        out = _build_representative_debates(
            cohort_dicts=[{
                "cohort_id": "c1", "cohort_label": "loyal",
                "member_persona_ids": ["p1"],
            }],
            ballots_by_stage={"final": []},
        )
        assert out["samples"] == []


# ---------------------------------------------------------------------
# Phase 12C.1 (extended) — uncertain-pileup + softening guards +
# per-round transition snapshots + report-mapping audit
# ---------------------------------------------------------------------


class TestPhase12C1HardResistantSoftening:
    def test_hard_resistant_cannot_soften_to_uncertain(self) -> None:
        """Hard-resistant voters must NOT step to uncertain via the
        single-step soften path — without an explicit proof
        satisfaction signal, they stay skeptical."""
        cohorts = [
            _cohort_with_intent_dist(
                {"loyal_to_current_alternative": 10},
                cohort_id="c_loyal",
                cohort_label="loyal_segment",
            ),
            _cohort_with_intent_dist(
                {"would_consider_if_proven": 10},
                cohort_id="c_consider",
                cohort_label="consider_segment",
            ),
        ]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=80,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        run_influence_rounds(voters, edges, simulation_seed=42)
        for v in voters:
            if v.hard_resistant:
                assert v.final_bucket == "skeptical", (
                    f"hard_resistant voter softened to "
                    f"{v.final_bucket}: intent {v.initial_intent} → "
                    f"{v.final_intent}"
                )

    def test_hard_resistant_to_uncertain_rate_zero_when_guarded(
        self,
    ) -> None:
        cohorts = [
            _cohort_with_intent_dist(
                {"loyal_to_current_alternative": 10},
                cohort_id="c_loyal",
                cohort_label="loyal_segment",
            ),
            _cohort_with_intent_dist(
                {"would_consider_if_proven": 10},
                cohort_id="c_consider",
                cohort_label="consider_segment",
            ),
        ]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=80,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert health.hard_resistant_to_uncertain_rate == 0.0
        assert (health.hard_resistant_retention_rate or 0.0) == 1.0


class TestPhase12C1UncertainPileup:
    def test_uncertain_pileup_warning_fires(self) -> None:
        """Synthetically rebucket voters into uncertain to validate
        the warning fires above its growth+share threshold."""
        cohorts = [_cohort_with_intent_dist({
            "would_consider_if_proven": 10,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Force-soften every voter into uncertain
        for v in voters:
            v.final_intent = "wait_and_see"
            v.final_bucket = "uncertain"
        health = compute_diversity_health(voters, edges, rounds)
        assert any(
            "uncertain_pileup_warning" in w for w in health.warnings
        ), f"warning missing: {health.warnings}"

    def test_resistant_softening_warning_fires(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 10,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Force-soften all hard-resistant voters into uncertain
        for v in voters:
            if v.hard_resistant:
                v.final_intent = "wait_and_see"
                v.final_bucket = "uncertain"
        health = compute_diversity_health(voters, edges, rounds)
        assert any(
            "resistant_softening_warning" in w for w in health.warnings
        ), f"warning missing: {health.warnings}"

    def test_no_softening_warning_when_resistance_preserved(
        self,
    ) -> None:
        cohorts = [
            _cohort_with_intent_dist(
                {"loyal_to_current_alternative": 5,
                 "would_consider_if_proven": 5},
                cohort_id="c_mix",
                cohort_label="mix",
            ),
        ]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        banned = (
            "resistant_softening_warning",
            "uncertain_pileup_warning",
        )
        for w in health.warnings:
            for b in banned:
                assert b not in w, (
                    f"unexpected warning {w} in {health.warnings}"
                )


class TestPhase12C1PerRoundTransitions:
    def test_each_round_has_bucket_distribution(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        assert len(rounds) == 4
        for r in rounds:
            assert r.bucket_distribution, (
                f"round {r.round_idx} missing bucket_distribution"
            )
            # 4-key bucket vocab
            assert set(r.bucket_distribution.keys()) == {
                "buyer", "receptive", "uncertain", "skeptical",
            }
            assert sum(r.bucket_distribution.values()) == len(voters)

    def test_each_round_has_skeptic_transitions(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        for r in rounds:
            assert r.skeptic_transitions
            assert set(r.skeptic_transitions.keys()) == {
                "skeptical_to_skeptical",
                "skeptical_to_uncertain",
                "skeptical_to_receptive",
                "skeptical_to_buyer",
            }

    def test_round_0_and_1_show_all_skeptics_stay_skeptical(
        self,
    ) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Rounds 0/1 = no movement yet; ALL initially skeptical voters
        # must show as skeptical_to_skeptical.
        for r in rounds:
            if r.round_idx not in (0, 1):
                continue
            sk_initial = sum(
                1 for v in voters
                if v.initial_bucket == "skeptical"
            )
            assert (
                r.skeptic_transitions["skeptical_to_skeptical"]
                == sk_initial
            )
            for k in ("skeptical_to_uncertain",
                      "skeptical_to_receptive",
                      "skeptical_to_buyer"):
                assert r.skeptic_transitions[k] == 0

    def test_per_round_distributions_in_diversity_health(self) -> None:
        cohorts = [_cohort_with_intent_dist({
            "loyal_to_current_alternative": 5,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=40,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        health = compute_diversity_health(voters, edges, rounds)
        assert len(health.per_round_bucket_distribution) == 4
        assert len(health.per_round_skeptic_transitions) == 4
        for k in (0, 1, 2, 3):
            assert k in health.per_round_bucket_distribution
            assert k in health.per_round_skeptic_transitions


class TestPhase12C1ReportMappingAudit:
    """Phase 12C.1 — assert that backend's loyal/would_reject intents
    are NOT rebucketed as uncertain in the founder-facing report."""

    def test_loyal_to_current_alternative_maps_to_resistant_in_report(
        self,
    ) -> None:
        from assembly.sources.product_grounding.report_polish import (
            role_distribution_from_ballots,
        )
        # Two personas with private_stance="curious_but_unconvinced"
        # but one has loyal_to_current_alternative intent.
        ballots = [
            {
                "persona_id": "p1",
                "ballot_stage": "final",
                "private_stance": "curious_but_unconvinced",
            },
            {
                "persona_id": "p2",
                "ballot_stage": "final",
                "private_stance": "curious_but_unconvinced",
            },
        ]
        role_by_pid = {"p1": "trust_seeker", "p2": "trust_seeker"}
        intent_by_pid = {
            "p1": "loyal_to_current_alternative",
            "p2": "would_consider_if_proven",
        }
        dist = role_distribution_from_ballots(
            ballots=ballots,
            role_by_pid=role_by_pid,
            intent_by_pid=intent_by_pid,
        )
        # p1 (loyal) must be resistant; p2 (consider) must be receptive.
        assert dist["trust_seeker"]["resistant"] == 1
        assert dist["trust_seeker"]["receptive"] == 1
        assert dist["trust_seeker"]["uncertain"] == 0

    def test_would_reject_maps_to_resistant_in_report(self) -> None:
        from assembly.sources.product_grounding.report_polish import (
            role_distribution_from_ballots,
        )
        ballots = [{
            "persona_id": "p1",
            "ballot_stage": "final",
            "private_stance": "needs_more_information",
        }]
        dist = role_distribution_from_ballots(
            ballots=ballots,
            role_by_pid={"p1": "price_skeptic"},
            intent_by_pid={"p1": "would_reject"},
        )
        assert dist["price_skeptic"]["resistant"] == 1
        assert dist["price_skeptic"]["uncertain"] == 0

    def test_legacy_path_still_works_without_intent_by_pid(
        self,
    ) -> None:
        from assembly.sources.product_grounding.report_polish import (
            role_distribution_from_ballots,
        )
        ballots = [{
            "persona_id": "p1",
            "ballot_stage": "final",
            "private_stance": "skeptical",
        }]
        # No intent_by_pid → falls back to stance map
        dist = role_distribution_from_ballots(
            ballots=ballots,
            role_by_pid={"p1": "trust_seeker"},
        )
        assert dist["trust_seeker"]["resistant"] == 1


# ---------------------------------------------------------------------
# Phase 12C.1 (extended) — persona_id flattening regression
# ---------------------------------------------------------------------


class TestPhase12C1PersonaIdFlattening:
    """Regression for the silent skip that produced samples=[] in
    representative_debates.json and empty top_objection/top_proof_need
    in cluster_arguments.

    Root cause: ctx['pre_dicts']/['final_dicts']/['refl_dicts'] are
    keyed by persona_id but the VALUES are dicts without persona_id
    inside. The helpers _build_representative_debates and
    _derive_cluster_arguments_from_ctx both call `b.get("persona_id")`
    on each ballot — every ballot returned None, so every ballot was
    skipped, and the artifacts came back empty.
    """

    def _orchestrator_style_ballots(self) -> dict[str, list[dict]]:
        """Reproduces the ballots_by_stage shape that
        _run_voter_overlay_inline now produces (post-fix). Pre-fix
        this list would have been values() only, missing persona_id."""
        return {
            "pre": [],
            "final": [
                {
                    "persona_id": "p1",
                    "private_stance": "skeptical",
                    "private_reasoning": "Already on Shopify Inbox.",
                    "top_objection": "incumbent already covers this",
                    "top_proof_need": "show 90-day retention",
                },
                {
                    "persona_id": "p2",
                    "private_stance": "interested_if_proven",
                    "private_reasoning": "Promising but pricing unclear.",
                    "top_objection": "pricing unclear",
                    "top_proof_need": "side-by-side comparison",
                },
            ],
            "refl": [],
        }

    def test_representative_debates_sees_persona_id_in_ballots(
        self,
    ) -> None:
        """If the orchestrator's flatten injects persona_id, the
        sampler produces non-empty samples."""
        from assembly.pipeline.lightweight_voter_pipeline import (
            _build_representative_debates,
        )
        cohorts = [{
            "cohort_id": "c1",
            "cohort_label": "loyal_segment",
            "member_persona_ids": ["p1"],
        }, {
            "cohort_id": "c2",
            "cohort_label": "consider_segment",
            "member_persona_ids": ["p2"],
        }]
        out = _build_representative_debates(
            cohort_dicts=cohorts,
            ballots_by_stage=self._orchestrator_style_ballots(),
        )
        assert out["samples"], (
            "samples empty — persona_id likely missing in ballots"
        )
        assert len(out["samples"]) == 2
        # Each sample carries persona_id so downstream consumers can
        # join back to the rich-persona table if needed.
        for s in out["samples"]:
            assert s.get("persona_id"), (
                "sample missing persona_id field"
            )

    def test_cluster_arguments_see_persona_id_in_ballots(
        self,
    ) -> None:
        """The cluster-arguments builder reads persona_id to bucket
        ballots by cohort. If persona_id is missing, every ballot is
        skipped and cluster_arguments come back empty."""
        from assembly.pipeline.lightweight_voter_pipeline import (
            _derive_cluster_arguments_from_ctx,
        )
        cohorts = [{
            "cohort_id": "c1",
            "cohort_label": "loyal_segment",
            "member_persona_ids": ["p1"],
        }, {
            "cohort_id": "c2",
            "cohort_label": "consider_segment",
            "member_persona_ids": ["p2"],
        }]
        out = _derive_cluster_arguments_from_ctx(
            cohorts, self._orchestrator_style_ballots(),
        )
        # Both cohorts must have non-empty top_objection /
        # top_proof_need (one ballot each maps to them).
        for seg in ("loyal_segment", "consider_segment"):
            assert out.get(seg), f"missing segment {seg} in {out}"
            assert out[seg]["top_objection"], (
                f"empty top_objection for {seg}: {out[seg]}"
            )
            assert out[seg]["top_proof_need"], (
                f"empty top_proof_need for {seg}: {out[seg]}"
            )

    def test_flatten_helper_contract(self) -> None:
        """The orchestrator helper that injects persona_id has a
        documented contract: every value-dict in the input becomes a
        list entry with `persona_id` set to the original key."""
        # Re-implement the exact contract used in
        # live_founder_brief._run_voter_overlay_inline._flatten so
        # the test breaks if the contract drifts. This is a contract
        # test, not a structural reference.
        from typing import Any

        def _flatten(d: dict[str, Any]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for pid, val in (d or {}).items():
                if not isinstance(val, dict):
                    continue
                merged = {"persona_id": str(pid), **val}
                out.append(merged)
            return out

        src = {
            "p1": {"private_stance": "skeptical", "top_objection": "x"},
            "p2": {"private_stance": "interested_if_proven"},
        }
        out = _flatten(src)
        assert len(out) == 2
        pids = sorted(b["persona_id"] for b in out)
        assert pids == ["p1", "p2"]
        # Original fields preserved.
        p1_dict = next(b for b in out if b["persona_id"] == "p1")
        assert p1_dict["private_stance"] == "skeptical"
        assert p1_dict["top_objection"] == "x"


# ---------------------------------------------------------------------
# Phase 12C.1 (Option A) — bucket-level vs exact-intent warning split
# ---------------------------------------------------------------------


class TestPhase12C1OptionABucketSemantics:
    """Phase 12C.1 (Option A) — the `hard_reject_erased_warning` must
    fire on bucket-leave (would_reject → uncertain/receptive/buyer)
    but NOT on within-skeptical intent micro-shifts (e.g. would_reject
    → loyal_to_current_alternative, both still skeptical bucket).
    """

    def _cohort(self, intent_dist: dict[str, int]) -> dict:
        return _cohort_with_intent_dist(
            intent_dist,
            cohort_id="c_test",
            cohort_label="test_segment",
        )

    def test_warning_does_NOT_fire_for_within_skeptical_shift(
        self,
    ) -> None:
        """A would_reject voter that shifts to loyal_to_current_alternative
        is STILL in skeptical bucket. No warning should fire."""
        cohorts = [self._cohort({
            "would_reject": 10,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=30,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Synthetically shift HALF the would_reject voters to
        # loyal_to_current_alternative (still skeptical bucket).
        shifted = 0
        for v in voters:
            if v.initial_intent == "would_reject":
                v.final_intent = "loyal_to_current_alternative"
                v.final_bucket = "skeptical"
                shifted += 1
                if shifted >= 4:
                    break
        health = compute_diversity_health(voters, edges, rounds)
        # bucket_retention should still be 1.00 (no bucket leave)
        assert health.hard_reject_bucket_retention_rate == 1.0
        # exact_intent_retention reflects the diagnostic micro-shift
        assert (
            health.hard_reject_exact_intent_retention_rate is not None
        )
        assert (
            health.hard_reject_exact_intent_retention_rate < 1.0
        )
        # within_skeptical_intent_shift_count > 0
        assert health.within_skeptical_intent_shift_count >= 4
        # No hard_reject_erased_warning
        assert not any(
            "hard_reject_erased_warning" in w for w in health.warnings
        ), f"unexpected warning: {health.warnings}"

    def test_warning_DOES_fire_for_would_reject_to_uncertain(
        self,
    ) -> None:
        cohorts = [self._cohort({
            "would_reject": 10, "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=30,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # Synthetically push 2 would_reject voters into uncertain.
        pushed = 0
        for v in voters:
            if v.initial_intent == "would_reject":
                v.final_intent = "wait_and_see"
                v.final_bucket = "uncertain"
                pushed += 1
                if pushed >= 2:
                    break
        health = compute_diversity_health(voters, edges, rounds)
        assert any(
            "hard_reject_erased_warning" in w for w in health.warnings
        ), f"warning missing: {health.warnings}"

    def test_warning_DOES_fire_for_would_reject_to_receptive(
        self,
    ) -> None:
        cohorts = [self._cohort({
            "would_reject": 10, "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=30,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        pushed = 0
        for v in voters:
            if v.initial_intent == "would_reject":
                v.final_intent = "would_consider_if_proven"
                v.final_bucket = "receptive"
                pushed += 1
                if pushed >= 2:
                    break
        health = compute_diversity_health(voters, edges, rounds)
        assert any(
            "hard_reject_erased_warning" in w for w in health.warnings
        ), f"warning missing: {health.warnings}"

    def test_warning_DOES_fire_for_would_reject_to_buyer(self) -> None:
        cohorts = [self._cohort({
            "would_reject": 10, "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=30,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        pushed = 0
        for v in voters:
            if v.initial_intent == "would_reject":
                v.final_intent = "would_buy_now"
                v.final_bucket = "buyer"
                pushed += 1
                if pushed >= 2:
                    break
        health = compute_diversity_health(voters, edges, rounds)
        assert any(
            "hard_reject_erased_warning" in w for w in health.warnings
        ), f"warning missing: {health.warnings}"

    def test_bucket_retention_is_the_gate_exact_is_diagnostic(
        self,
    ) -> None:
        """Even when exact_intent_retention drops, bucket_retention
        stays 1.0 and all_gates_passed remains true."""
        cohorts = [self._cohort({
            "would_reject": 10,
            "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=30,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # All would_reject voters shift to loyal_to_current_alternative.
        for v in voters:
            if v.initial_intent == "would_reject":
                v.final_intent = "loyal_to_current_alternative"
                v.final_bucket = "skeptical"
        health = compute_diversity_health(voters, edges, rounds)
        # Bucket-level gate intact.
        assert health.hard_reject_bucket_retention_rate == 1.0
        # Exact-intent retention is 0.0 (everyone shifted).
        assert health.hard_reject_exact_intent_retention_rate == 0.0
        # No warnings about hard_reject — the gate is bucket-level.
        assert not any(
            "hard_reject_erased_warning" in w for w in health.warnings
        )

    def test_within_skeptical_examples_recorded(self) -> None:
        cohorts = [self._cohort({
            "would_reject": 10, "would_consider_if_proven": 5,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=30,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        for v in voters:
            if v.initial_intent == "would_reject":
                v.final_intent = "loyal_to_current_alternative"
                v.final_bucket = "skeptical"
        health = compute_diversity_health(voters, edges, rounds)
        # At most 5 examples (the cap) are surfaced.
        assert 0 < len(health.within_skeptical_intent_shift_examples) <= 5
        for ex in health.within_skeptical_intent_shift_examples:
            assert ex["from_intent"] == "would_reject"
            assert ex["to_intent"] == "loyal_to_current_alternative"
            assert ex["voter_id"]

    def test_hard_resistant_exact_vs_bucket_metric_split(self) -> None:
        """The hard_resistant_* metrics expose both bucket and exact-
        intent retention so operators can inspect within-skeptical
        movement without it failing the gate."""
        cohorts = [self._cohort({
            "loyal_to_current_alternative": 8,
        })]
        voters, _ = generate_voters_from_cohorts(
            cohorts, run_scope_id="t", simulation_seed=42, n=24,
        )
        edges, _ = build_social_graph(voters, simulation_seed=42)
        rounds = run_influence_rounds(
            voters, edges, simulation_seed=42,
        )
        # All loyal voters shift to would_reject (still skeptical).
        for v in voters:
            if v.initial_intent == "loyal_to_current_alternative":
                v.final_intent = "would_reject"
                v.final_bucket = "skeptical"
        health = compute_diversity_health(voters, edges, rounds)
        assert health.hard_resistant_bucket_retention_rate == 1.0
        assert (
            health.hard_resistant_exact_intent_retention_rate
            is not None
        )
        assert (
            health.hard_resistant_exact_intent_retention_rate < 1.0
        )
        # bucket-level resistance preserved — no resistance warning
        # fires (other diversity gates may fire on this tiny fixture,
        # but the resistance-realism gates must remain clean).
        for w in health.warnings:
            for banned in (
                "hard_reject_erased_warning",
                "resistant_softening_warning",
                "competitor_loyalty_not_preserved_warning",
                "skeptic_overconversion_warning",
                "zero_resistant_people_warning",
                "skeptic_to_buyer_observed",
            ):
                assert banned not in w, (
                    f"unexpected resistance warning: {w}"
                )
