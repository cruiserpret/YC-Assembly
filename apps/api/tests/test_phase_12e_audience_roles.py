"""Phase 12E — Source-Audience Population Layer tests.

Covers:
  * Role taxonomy completeness and integrity.
  * Source-profile proportions.
  * Default profile preserves legacy behavior (no synthetic injection).
  * hn_show_hn injects non-customer voices.
  * Role-aware bucket override (locked roles → locked buckets).
  * Augmenter output shape + audit metadata.
  * 4-view distribution split.
  * Backward compatibility (missing audience_role → legacy path).
  * No new LLM calls (the module imports cleanly without provider SDK).
  * No frontend changes (verified by absence of edits under apps/web).
"""
from __future__ import annotations

import os
import re

import pytest

from assembly.calibration.market_buckets import (
    pick_market_bucket,
    pick_market_bucket_with_role,
)
from assembly.sources.audience import (
    AUDIENCE_ROLES,
    SOURCE_PROFILES,
    allocate_role_counts,
    get_profile,
    get_role_spec,
    is_hard_resistant_role,
    is_scorable_role,
    resolve_launch_source,
    role_locked_default_bucket,
)
from assembly.sources.audience.augmenter import (
    assign_audience_role,
    augment_intent_drafts_with_source_audience,
    split_view_distributions,
)


# -----------------------------------------------------------------------
# 1. Taxonomy integrity
# -----------------------------------------------------------------------


class TestTaxonomyIntegrity:
    def test_role_count_locked(self):
        # Phase 12E v1 shipped 10 roles. Phase 12E.5O added 3 PH-flavored
        # roles (shallow_positive_commenter, founder_network_supporter,
        # early_adopter) bringing the total to 13. This count is locked
        # here so any further additions get explicit architectural review.
        assert len(AUDIENCE_ROLES) == 13

    def test_every_role_has_spec(self):
        for role, spec in AUDIENCE_ROLES.items():
            assert spec.role == role
            assert spec.default_bucket in (
                "buyer", "receptive", "uncertain", "skeptical",
            )
            assert len(spec.allowed_buckets) >= 1
            assert spec.default_bucket in spec.allowed_buckets

    def test_locked_roles_are_correctly_locked(self):
        # These roles MUST be single-bucket-locked.
        locked = {
            "proof_seeker_only": "uncertain",
            "industry_observer": "uncertain",
            "technical_or_legal_explainer": "uncertain",
            "meta_commenter": "uncertain",
            "category_skeptic": "skeptical",
            "incumbent_defender": "skeptical",
            "casual_bystander": "uncertain",
            "off_topic_noise_candidate": "uncertain",
        }
        for role, bucket in locked.items():
            assert role_locked_default_bucket(role) == bucket, (
                f"role {role} should lock to {bucket}"
            )

    def test_multi_bucket_roles_unlocked(self):
        for role in ("target_customer_evaluator", "existing_competitor_user"):
            assert role_locked_default_bucket(role) is None, (
                f"{role} should be multi-bucket (not locked)"
            )

    def test_hard_resistant_roles(self):
        assert is_hard_resistant_role("category_skeptic")
        assert is_hard_resistant_role("incumbent_defender")
        assert not is_hard_resistant_role("target_customer_evaluator")
        assert not is_hard_resistant_role("proof_seeker_only")

    def test_scorable_defaults(self):
        # Customer roles + proof_seeker + category_skeptic + observer
        # + incumbent_defender are scorable by default. Meta /
        # explainer / casual / off-topic are not.
        scorable = {
            "target_customer_evaluator", "existing_competitor_user",
            "proof_seeker_only", "industry_observer",
            "category_skeptic", "incumbent_defender",
        }
        non_scorable = {
            "technical_or_legal_explainer", "meta_commenter",
            "casual_bystander", "off_topic_noise_candidate",
        }
        for role in scorable:
            assert is_scorable_role(role), f"{role} should be scorable"
        for role in non_scorable:
            assert not is_scorable_role(role), (
                f"{role} should be non-scorable"
            )

    def test_unknown_role_legacy_compat(self):
        # Roles outside the taxonomy → defaults that don't crash.
        assert get_role_spec(None) is None
        assert get_role_spec("not_a_real_role") is None
        assert role_locked_default_bucket(None) is None
        assert is_hard_resistant_role(None) is False
        assert is_scorable_role(None) is True  # legacy default
        assert is_scorable_role("not_a_real_role") is True


# -----------------------------------------------------------------------
# 2. Source profiles
# -----------------------------------------------------------------------


class TestSourceProfiles:
    def test_v1_profiles_present(self):
        # Phase 12E.5C — `hn_show_hn_v2` was added as opt-in. The two
        # v1 profiles must still be present (backwards compat). The
        # opt-in v2 is verified by tests in
        # test_phase_12e5c_hn_show_hn_v2.py.
        assert {"default", "hn_show_hn"}.issubset(set(SOURCE_PROFILES.keys()))

    def test_profile_proportions_sum_to_one(self):
        for src, profile in SOURCE_PROFILES.items():
            total = sum(profile.values())
            assert abs(total - 1.0) < 1e-9, (
                f"{src} proportions sum to {total}, expected 1.0"
            )

    def test_default_profile_is_target_customer_heavy(self):
        # Verifies legacy-compat: default profile has >= 70% target
        # customer + competitor user combined.
        prof = get_profile("default")
        customer = (
            prof["target_customer_evaluator"]
            + prof["existing_competitor_user"]
        )
        assert customer >= 0.85

    def test_hn_show_hn_includes_non_customer_roles(self):
        prof = get_profile("hn_show_hn")
        # Each of these MUST be > 0 in hn_show_hn (it's the whole point)
        for role in (
            "proof_seeker_only", "industry_observer",
            "technical_or_legal_explainer", "meta_commenter",
            "category_skeptic", "incumbent_defender",
        ):
            assert prof[role] > 0.0, (
                f"hn_show_hn must have non-zero share for {role}"
            )

    def test_hn_show_hn_customer_share_smaller_than_default(self):
        d = get_profile("default")
        h = get_profile("hn_show_hn")
        d_cust = d["target_customer_evaluator"] + d["existing_competitor_user"]
        h_cust = h["target_customer_evaluator"] + h["existing_competitor_user"]
        assert h_cust < d_cust, (
            "hn_show_hn customer share must be strictly smaller "
            "than default (it leaves room for non-customer voices)"
        )

    def test_resolve_unknown_falls_back_to_default(self):
        assert resolve_launch_source("garbage") == "default"
        assert resolve_launch_source(None) == "default"
        assert resolve_launch_source("") == "default"
        assert resolve_launch_source("hn_show_hn") == "hn_show_hn"
        assert resolve_launch_source("HN_SHOW_HN") == "hn_show_hn"  # case


# -----------------------------------------------------------------------
# 3. Role-count allocation
# -----------------------------------------------------------------------


class TestAllocateRoleCounts:
    def test_default_allocation_sums_to_n(self):
        counts = allocate_role_counts("default", 24)
        assert sum(counts.values()) == 24

    def test_hn_show_hn_allocation_sums_to_n(self):
        counts = allocate_role_counts("hn_show_hn", 24)
        assert sum(counts.values()) == 24

    def test_hn_show_hn_24_personas_creates_non_customer_voices(self):
        counts = allocate_role_counts("hn_show_hn", 24)
        non_customer = sum(
            counts[r] for r in (
                "proof_seeker_only", "industry_observer",
                "technical_or_legal_explainer", "meta_commenter",
                "category_skeptic", "incumbent_defender",
                "casual_bystander", "off_topic_noise_candidate",
            )
        )
        # ~63% of 24 = ~15 non-customer voices
        assert 12 <= non_customer <= 18

    def test_zero_personas_returns_zero_counts(self):
        counts = allocate_role_counts("hn_show_hn", 0)
        assert all(v == 0 for v in counts.values())

    def test_zero_weight_role_stays_zero(self):
        # In default profile, industry_observer weight is 0 → must be 0
        counts = allocate_role_counts("default", 24)
        assert counts["industry_observer"] == 0
        assert counts["meta_commenter"] == 0


# -----------------------------------------------------------------------
# 4. Role-aware bucket override (LOCKED roles cannot escape)
# -----------------------------------------------------------------------


class TestRoleAwareBucketOverride:
    def test_proof_seeker_only_locked_to_uncertain(self):
        # Even if the intent label / signal says "buy now", the
        # bucket-locked role overrides.
        bucket, basis = pick_market_bucket_with_role(
            audience_role="proof_seeker_only",
            intent_signal="explicit_buy_or_use_now",  # buyer-bucket signal
            intent_label="would_buy_now",
            intent_signal_routing_enabled=True,
        )
        assert bucket == "uncertain"
        assert "role_locked:proof_seeker_only" in (basis or "")

    def test_category_skeptic_locked_to_skeptical(self):
        bucket, basis = pick_market_bucket_with_role(
            audience_role="category_skeptic",
            intent_signal="positive_interest_if_proven",
            intent_label="would_consider_if_proven",
            intent_signal_routing_enabled=True,
        )
        assert bucket == "skeptical"
        assert "role_locked:category_skeptic" in (basis or "")

    def test_incumbent_defender_locked_to_skeptical(self):
        bucket, _ = pick_market_bucket_with_role(
            audience_role="incumbent_defender",
            intent_signal="explicit_buy_or_use_now",
            intent_label="would_buy_now",
        )
        assert bucket == "skeptical"

    def test_industry_observer_cannot_become_buyer(self):
        bucket, basis = pick_market_bucket_with_role(
            audience_role="industry_observer",
            intent_signal="explicit_buy_or_use_now",
            intent_label="would_buy_now",
        )
        assert bucket == "uncertain"

    def test_meta_commenter_locked_to_uncertain(self):
        bucket, basis = pick_market_bucket_with_role(
            audience_role="meta_commenter",
            intent_signal="positive_interest_if_proven",
            intent_label="would_consider_if_proven",
        )
        assert bucket == "uncertain"

    def test_target_customer_evaluator_can_reach_any_bucket(self):
        # Multi-bucket role — routes via intent_label normally.
        b_buyer, _ = pick_market_bucket_with_role(
            audience_role="target_customer_evaluator",
            intent_signal=None,
            intent_label="would_buy_now",
            intent_signal_routing_enabled=False,
        )
        assert b_buyer == "buyer"
        b_skep, _ = pick_market_bucket_with_role(
            audience_role="target_customer_evaluator",
            intent_signal=None,
            intent_label="loyal_to_current_alternative",
            intent_signal_routing_enabled=False,
        )
        assert b_skep == "skeptical"

    def test_existing_competitor_user_cannot_jump_to_buyer(self):
        # `existing_competitor_user` allowed_buckets is
        # {receptive, uncertain, skeptical} — buyer is forbidden.
        # If intent_label routes to buyer, role-clamp falls back to
        # the role's default_bucket (skeptical).
        bucket, basis = pick_market_bucket_with_role(
            audience_role="existing_competitor_user",
            intent_signal=None,
            intent_label="would_buy_now",
            intent_signal_routing_enabled=False,
        )
        assert bucket == "skeptical"
        assert (basis or "").startswith("role_clamp:existing_competitor_user")

    def test_missing_role_falls_back_to_legacy(self):
        # No audience_role → behaves like pick_market_bucket()
        b1, _ = pick_market_bucket_with_role(
            audience_role=None,
            intent_signal=None,
            intent_label="would_consider_if_proven",
            intent_signal_routing_enabled=False,
        )
        b2, _ = pick_market_bucket(
            intent_signal=None,
            intent_label="would_consider_if_proven",
            intent_signal_routing_enabled=False,
        )
        assert b1 == b2 == "receptive"

    def test_unknown_role_falls_back_to_legacy(self):
        b1, _ = pick_market_bucket_with_role(
            audience_role="not_a_real_role",
            intent_signal=None,
            intent_label="would_buy_now",
            intent_signal_routing_enabled=False,
        )
        b2, _ = pick_market_bucket(
            intent_signal=None,
            intent_label="would_buy_now",
            intent_signal_routing_enabled=False,
        )
        assert b1 == b2 == "buyer"


# -----------------------------------------------------------------------
# 5. Assign-audience-role heuristic
# -----------------------------------------------------------------------


class TestAssignAudienceRole:
    def test_competitor_user_segment_maps_to_competitor(self):
        assert assign_audience_role(
            segment_label="competitor_user_docusign",
        ) == "existing_competitor_user"

    def test_role_field_competitor_user_maps_too(self):
        assert assign_audience_role(
            segment_label=None,
            role="competitor_user_pandadoc",
        ) == "existing_competitor_user"

    def test_known_buyer_segments(self):
        for seg in (
            "trust_seeker", "price_skeptic",
            "performance_focused_buyer", "convenience_focused_buyer",
            "format_focused_buyer", "objection_focused_buyer",
            "use_case_focused_buyer",
        ):
            assert assign_audience_role(
                segment_label=seg,
            ) == "target_customer_evaluator"

    def test_unknown_segment_defaults_target_customer(self):
        assert assign_audience_role(
            segment_label="some_novel_segment",
        ) == "target_customer_evaluator"

    def test_none_segment_defaults_target_customer(self):
        assert assign_audience_role(
            segment_label=None, role=None,
        ) == "target_customer_evaluator"


# -----------------------------------------------------------------------
# 6. Augmenter (synthetic non-customer injection)
# -----------------------------------------------------------------------


def _fake_legacy_draft(pid: str, segment: str, intent: str):
    """Return a dict that mimics SimulatedIntentDraft.model_dump()."""
    return {
        "persona_id": pid,
        "cohort_id": "live_cohort_0",
        "stance_label": "curious_but_unconvinced",
        "simulated_intent": intent,
        "intent_strength": "medium",
        "switching_status": "weakly_attached_to_alternative",
        "evidence_basis": "test",
        "discussion_turn_ids": [],
        "ballot_ids": [],
        "memory_atom_ids": [],
        "confidence": "medium",
        "caveat": "test",
        "intent_signal": None,
        "intent_signal_basis": None,
        "_test_segment": segment,
    }


class TestAugmenter:
    def _build_drafts(self):
        # 19 target_customer + 5 competitor_user = 24 legacy drafts
        drafts = []
        for i in range(19):
            drafts.append(_fake_legacy_draft(
                f"p_target_{i}", "trust_seeker", "would_consider_if_proven",
            ))
        for i in range(5):
            drafts.append(_fake_legacy_draft(
                f"p_competitor_{i}", "competitor_user_docusign",
                "loyal_to_current_alternative",
            ))
        meta = {
            d["persona_id"]: {"segment_label": d["_test_segment"]}
            for d in drafts
        }
        return drafts, meta

    def test_default_profile_is_identity(self):
        drafts, meta = self._build_drafts()
        augmented, audit = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="default",
            run_scope_id="test",
        )
        assert len(augmented) == 24  # No synthetic added
        assert audit["n_synthetic_added_by_role"] == {}
        # All entries have audience_role assigned
        for d in augmented:
            assert d["audience_role"] in (
                "target_customer_evaluator", "existing_competitor_user",
            )
            assert d["is_synthetic_non_customer_voice"] is False

    def test_hn_show_hn_injects_non_customer_voices(self):
        drafts, meta = self._build_drafts()
        augmented, audit = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="hn_show_hn",
            run_scope_id="test",
        )
        assert len(augmented) > 24
        # Synthetic voices include the expected roles
        synth_roles = {
            d["audience_role"] for d in augmented
            if d["is_synthetic_non_customer_voice"]
        }
        assert "proof_seeker_only" in synth_roles
        assert "industry_observer" in synth_roles
        assert "category_skeptic" in synth_roles
        # Legacy customer voices preserved
        n_legacy = sum(
            1 for d in augmented
            if not d["is_synthetic_non_customer_voice"]
        )
        assert n_legacy == 24

    def test_synthetic_voice_audience_role_in_taxonomy(self):
        drafts, meta = self._build_drafts()
        augmented, _ = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="hn_show_hn",
            run_scope_id="test",
        )
        for d in augmented:
            assert d["audience_role"] in AUDIENCE_ROLES, (
                f"audience_role {d['audience_role']!r} not in taxonomy"
            )

    def test_synthetic_voice_is_deterministic(self):
        drafts, meta = self._build_drafts()
        a1, _ = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="hn_show_hn",
            run_scope_id="same_scope",
        )
        a2, _ = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="hn_show_hn",
            run_scope_id="same_scope",
        )
        ids_1 = sorted([d["persona_id"] for d in a1])
        ids_2 = sorted([d["persona_id"] for d in a2])
        assert ids_1 == ids_2  # deterministic UUID5 ids

    def test_synthetic_voice_run_scope_changes_ids(self):
        drafts, meta = self._build_drafts()
        a1, _ = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="hn_show_hn",
            run_scope_id="scope_a",
        )
        a2, _ = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="hn_show_hn",
            run_scope_id="scope_b",
        )
        # The legacy customer ids are the same; synthetic voice ids
        # differ between scopes (this is a load-bearing determinism
        # property — same scope → same synthetic personas).
        synth_a = [d["persona_id"] for d in a1 if d["is_synthetic_non_customer_voice"]]
        synth_b = [d["persona_id"] for d in a2 if d["is_synthetic_non_customer_voice"]]
        assert set(synth_a).isdisjoint(set(synth_b))

    def test_no_zero_collapse_under_hn_show_hn(self):
        drafts, meta = self._build_drafts()
        augmented, _ = augment_intent_drafts_with_source_audience(
            intent_drafts=drafts,
            persona_metadata_by_pid=meta,
            launch_source="hn_show_hn",
            run_scope_id="test",
        )
        # At least 4 of the 8 non-customer roles MUST have ≥1 voice
        # at n=24 source audience (resistance against role collapse).
        from collections import Counter
        c = Counter(
            d["audience_role"] for d in augmented
            if d["is_synthetic_non_customer_voice"]
        )
        present = {r for r, v in c.items() if v > 0}
        assert len(present) >= 4


# -----------------------------------------------------------------------
# 7. 4-view distribution split
# -----------------------------------------------------------------------


class TestSplitViewDistributions:
    def test_four_views_present(self):
        out = split_view_distributions([])
        assert set(out.keys()) == {
            "target_market_reaction", "source_audience_reaction",
            "scorable_market_reaction", "noise_meta_estimate",
        }

    def test_default_views_collapse(self):
        """Under default launch_source (legacy 24 target-customer
        voices, no synthetic), source_audience and target_market
        should be identical."""
        # Build 24 target_customer drafts → default profile is identity
        drafts = []
        for i in range(19):
            drafts.append({
                "persona_id": f"p{i}",
                "simulated_intent": "would_consider_if_proven",
                "intent_signal": None,
                "audience_role": "target_customer_evaluator",
                "is_synthetic_non_customer_voice": False,
                "is_scorable": True,
            })
        for i in range(5):
            drafts.append({
                "persona_id": f"c{i}",
                "simulated_intent": "loyal_to_current_alternative",
                "intent_signal": None,
                "audience_role": "existing_competitor_user",
                "is_synthetic_non_customer_voice": False,
                "is_scorable": True,
            })
        out = split_view_distributions(drafts)
        assert (
            out["target_market_reaction"]
            == out["source_audience_reaction"]
            == out["scorable_market_reaction"]
        )
        assert out["noise_meta_estimate"]["count"] == 0

    def test_meta_commenter_in_noise(self):
        drafts = [
            {
                "persona_id": "m1",
                "simulated_intent": "wait_and_see",
                "intent_signal": None,
                "audience_role": "meta_commenter",
                "is_synthetic_non_customer_voice": True,
                "is_scorable": False,
            },
        ]
        out = split_view_distributions(drafts)
        assert out["noise_meta_estimate"]["count"] == 1
        assert out["source_audience_reaction"] == {
            "buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 0,
        }

    def test_category_skeptic_routed_to_skeptical(self):
        drafts = [
            {
                "persona_id": "s1",
                "simulated_intent": "would_reject",
                "intent_signal": None,
                "audience_role": "category_skeptic",
                "is_synthetic_non_customer_voice": True,
                "is_scorable": True,
            },
        ]
        out = split_view_distributions(drafts)
        assert out["source_audience_reaction"]["skeptical"] == 1
        # NOT in target_market_reaction (non-customer)
        assert out["target_market_reaction"]["skeptical"] == 0

    def test_proof_seeker_routed_to_uncertain(self):
        drafts = [
            {
                "persona_id": "ps1",
                "simulated_intent": "wait_and_see",
                "intent_signal": None,
                "audience_role": "proof_seeker_only",
                "is_synthetic_non_customer_voice": True,
                "is_scorable": True,
            },
        ]
        out = split_view_distributions(drafts)
        assert out["source_audience_reaction"]["uncertain"] == 1


# -----------------------------------------------------------------------
# 8. No-LLM invariant — module has zero provider/SDK imports
# -----------------------------------------------------------------------


class TestNoLLMSurface:
    def test_audience_module_has_no_llm_imports(self):
        import importlib
        for modname in (
            "assembly.sources.audience.role_taxonomy",
            "assembly.sources.audience.augmenter",
        ):
            m = importlib.import_module(modname)
            forbidden = ("anthropic", "openai", "AsyncAnthropic", "AsyncOpenAI")
            for name in dir(m):
                obj = getattr(m, name)
                mod = getattr(obj, "__module__", "") or ""
                for f in forbidden:
                    assert f not in mod.lower(), (
                        f"forbidden import {f!r} found via {modname}.{name}"
                    )

    def test_market_buckets_role_override_no_llm(self):
        import importlib
        m = importlib.import_module(
            "assembly.calibration.market_buckets",
        )
        forbidden = ("anthropic", "openai", "AsyncAnthropic")
        for name in dir(m):
            obj = getattr(m, name)
            mod = getattr(obj, "__module__", "") or ""
            for f in forbidden:
                assert f not in mod.lower(), (
                    f"forbidden import {f!r} found via market_buckets.{name}"
                )


# -----------------------------------------------------------------------
# 9. No apps/web changes — verified by grep
# -----------------------------------------------------------------------


class TestNoFrontendOrDBChanges:
    def test_no_audience_role_in_alembic_versions(self):
        import pathlib
        alembic_dir = pathlib.Path(
            "/Users/hamza40/Desktop/Aseembly/assembly-v0/apps/api/alembic/versions"
        )
        if not alembic_dir.exists():
            return  # skip if not in repo
        for f in alembic_dir.glob("*.py"):
            content = f.read_text(encoding="utf-8", errors="ignore")
            assert "audience_role" not in content, (
                f"Phase 12E should not require a DB migration; "
                f"{f.name} mentions audience_role"
            )

    def test_no_audience_role_in_apps_web(self):
        import pathlib
        web_dir = pathlib.Path(
            "/Users/hamza40/Desktop/Aseembly/assembly-v0/apps/web"
        )
        if not web_dir.exists():
            return
        bad = []
        for f in web_dir.rglob("*.ts"):
            try:
                if "audience_role" in f.read_text(
                    encoding="utf-8", errors="ignore",
                ):
                    bad.append(str(f))
            except Exception:
                pass
        for f in web_dir.rglob("*.tsx"):
            try:
                if "audience_role" in f.read_text(
                    encoding="utf-8", errors="ignore",
                ):
                    bad.append(str(f))
            except Exception:
                pass
        assert not bad, f"Phase 12E should not touch apps/web; offenders: {bad}"
