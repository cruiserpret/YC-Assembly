"""Phase 12A.3 — Case candidate selection framework tests.

Covers ``assembly.calibration.case_candidate_selection`` and the
preliminary shortlist in
``assembly.calibration.candidate_shortlist_examples``.

The framework is **judgment-only**: it scores hand-authored
candidate metadata and returns a recommendation. These tests verify
that the recommendation rules implement the Phase 12A.3 spec:

  - Strong candidate → ``accept``
  - Medium candidate → ``maybe``
  - Weak candidate → ``reject``
  - Unverified candidate → ``unverified``
  - Famous/contaminated candidates trigger risk flags
  - Vivago/Semble-shaped names are auto-flagged
  - Ranking is deterministic
  - No network / API / LLM / DB imports
"""
from __future__ import annotations

from datetime import date

import pytest

from assembly.calibration import (
    CaseCandidate,
    candidate_risk_flags,
    candidate_scorecard,
    evaluate_candidate_suitability,
    preliminary_unverified_shortlist,
    rank_case_candidates,
)


# ---------------------------------------------------------------------------
# Fixture builders — clearly synthetic, no real product names
# ---------------------------------------------------------------------------


def _strong_candidate() -> CaseCandidate:
    """A candidate that satisfies every dimension. Synthetic name."""
    return CaseCandidate(
        candidate_id="fake_strong_a",
        product_name="FakeStrongProduct",
        category="AI SaaS tool",
        launch_or_cutoff_date=date(2025, 6, 1),
        pre_launch_sources_available=[
            "launch_post_text",
            "founder_announcement_thread",
        ],
        outcome_sources_available=[
            "public_review_text",
            "operator_supplied_user_feedback",
        ],
        estimated_observation_count="500+",
        contamination_risk="none",
        model_prior_risk="low",
        outcome_quality="strong",
        cutoff_clarity="clear",
        category_fit="strong",
        source_access_risk="open_data",
        notes="Synthetic strong candidate for tests.",
    )


def _medium_candidate() -> CaseCandidate:
    return CaseCandidate(
        candidate_id="fake_medium_a",
        product_name="FakeMediumProduct",
        category="developer tool",
        launch_or_cutoff_date=date(2025, 6, 1),
        pre_launch_sources_available=["launch_post_text"],
        outcome_sources_available=["public_review_text"],
        estimated_observation_count="30-100",
        contamination_risk="low",
        model_prior_risk="medium",
        outcome_quality="medium",
        cutoff_clarity="approximate",
        category_fit="medium",
        source_access_risk="public_no_scrape",
    )


def _weak_candidate() -> CaseCandidate:
    """All fields filled (so it isn't 'unverified') but each dim
    scores zero or near zero."""
    return CaseCandidate(
        candidate_id="fake_weak_a",
        product_name="FakeWeakProduct",
        category="consumer product",
        launch_or_cutoff_date=date(2025, 6, 1),
        pre_launch_sources_available=["launch_post_text"],
        outcome_sources_available=["public_review_text"],
        estimated_observation_count="30-100",  # min-real value
        contamination_risk="medium",
        model_prior_risk="medium",
        outcome_quality="weak",
        cutoff_clarity="approximate",
        category_fit="weak",
        source_access_risk="operator_supply",
    )


def _unverified_candidate() -> CaseCandidate:
    """Missing the structural fields needed to score honestly."""
    return CaseCandidate(
        candidate_id="fake_unverified_a",
        product_name="FakeUnverifiedProduct",
        category="AI SaaS tool",
        # launch_or_cutoff_date intentionally None
        # estimated_observation_count default "unknown"
        # outcome_quality default "unknown"
        # cutoff_clarity default "approximate"
    )


def _contaminated_vivago_candidate() -> CaseCandidate:
    """The product_name substring 'vivago' should trigger the
    contamination flag REGARDLESS of operator-supplied
    contamination_risk."""
    return CaseCandidate(
        candidate_id="fake_vivago_overlap",
        product_name="VivagoAI clone X",
        category="AI SaaS tool",
        launch_or_cutoff_date=date(2025, 6, 1),
        pre_launch_sources_available=["launch_post_text"],
        outcome_sources_available=["public_review_text"],
        estimated_observation_count="500+",
        contamination_risk="low",   # operator says low, but name forces high
        model_prior_risk="low",
        outcome_quality="strong",
        cutoff_clarity="clear",
        category_fit="strong",
        source_access_risk="open_data",
    )


def _contaminated_semble_candidate() -> CaseCandidate:
    return CaseCandidate(
        candidate_id="fake_semble_overlap",
        product_name="Semble developer search",
        category="developer tool",
        launch_or_cutoff_date=date(2025, 6, 1),
        pre_launch_sources_available=["launch_post_text"],
        outcome_sources_available=["public_review_text"],
        estimated_observation_count="500+",
        contamination_risk="low",
        model_prior_risk="low",
        outcome_quality="strong",
        cutoff_clarity="clear",
        category_fit="strong",
        source_access_risk="open_data",
    )


def _famous_product_candidate() -> CaseCandidate:
    """model_prior_risk='high' is an auto-reject."""
    return CaseCandidate(
        candidate_id="fake_famous_a",
        product_name="FakeFamousProduct",
        category="AI SaaS tool",
        launch_or_cutoff_date=date(2025, 6, 1),
        pre_launch_sources_available=["launch_post_text"],
        outcome_sources_available=["public_review_text"],
        estimated_observation_count="500+",
        contamination_risk="low",
        model_prior_risk="high",     # auto-reject trigger
        outcome_quality="strong",
        cutoff_clarity="clear",
        category_fit="strong",
        source_access_risk="open_data",
    )


# ---------------------------------------------------------------------------
# Recommendation rules
# ---------------------------------------------------------------------------


class TestRecommendation:
    def test_strong_candidate_accepts(self) -> None:
        rec = evaluate_candidate_suitability(_strong_candidate())
        assert rec == "accept"

    def test_medium_candidate_is_maybe(self) -> None:
        rec = evaluate_candidate_suitability(_medium_candidate())
        assert rec == "maybe"

    def test_weak_candidate_rejects(self) -> None:
        rec = evaluate_candidate_suitability(_weak_candidate())
        assert rec == "reject"

    def test_unverified_candidate_remains_unverified(self) -> None:
        rec = evaluate_candidate_suitability(_unverified_candidate())
        assert rec == "unverified"

    def test_famous_candidate_auto_rejects(self) -> None:
        """model_prior_risk='high' is an auto-reject regardless of
        scorecard total."""
        rec = evaluate_candidate_suitability(_famous_product_candidate())
        assert rec == "reject"

    def test_contaminated_vivago_auto_rejects(self) -> None:
        rec = evaluate_candidate_suitability(
            _contaminated_vivago_candidate()
        )
        assert rec == "reject"

    def test_contaminated_semble_auto_rejects(self) -> None:
        rec = evaluate_candidate_suitability(
            _contaminated_semble_candidate()
        )
        assert rec == "reject"

    def test_operator_recommendation_overrides_computed(self) -> None:
        c = _strong_candidate()
        c.operator_recommendation = "reject"
        assert evaluate_candidate_suitability(c) == "reject"

    def test_scraping_required_auto_rejects(self) -> None:
        c = _strong_candidate()
        c.source_access_risk = "scraping_required"
        assert evaluate_candidate_suitability(c) == "reject"

    def test_forbidden_source_auto_rejects(self) -> None:
        c = _strong_candidate()
        c.source_access_risk = "forbidden"
        assert evaluate_candidate_suitability(c) == "reject"


# ---------------------------------------------------------------------------
# Risk flags
# ---------------------------------------------------------------------------


class TestRiskFlags:
    def test_vivago_name_triggers_contamination_flag(self) -> None:
        flags = candidate_risk_flags(_contaminated_vivago_candidate())
        assert "contaminated_in_signal_layer" in flags

    def test_semble_name_triggers_contamination_flag(self) -> None:
        flags = candidate_risk_flags(_contaminated_semble_candidate())
        assert "contaminated_in_signal_layer" in flags

    def test_case_insensitive_contamination_name_match(self) -> None:
        c = _contaminated_vivago_candidate()
        c.product_name = "VIVAGO_PRODUCT"
        flags = candidate_risk_flags(c)
        assert "contaminated_in_signal_layer" in flags

    def test_model_prior_high_triggers_flag(self) -> None:
        flags = candidate_risk_flags(_famous_product_candidate())
        assert "model_prior_too_strong" in flags

    def test_weak_outcome_data_triggers_flag(self) -> None:
        c = _strong_candidate()
        c.outcome_quality = "weak"
        flags = candidate_risk_flags(c)
        assert "weak_outcome_data" in flags

    def test_low_observation_count_triggers_flag(self) -> None:
        c = _strong_candidate()
        c.estimated_observation_count = "<30"
        flags = candidate_risk_flags(c)
        assert "insufficient_observations" in flags

    def test_missing_cutoff_triggers_flag(self) -> None:
        c = _strong_candidate()
        c.launch_or_cutoff_date = None
        c.cutoff_clarity = "approximate"
        flags = candidate_risk_flags(c)
        assert "vague_cutoff_date" in flags

    def test_unclear_cutoff_triggers_flag(self) -> None:
        c = _strong_candidate()
        c.cutoff_clarity = "unclear"
        flags = candidate_risk_flags(c)
        assert "vague_cutoff_date" in flags

    def test_scraping_required_triggers_flag(self) -> None:
        c = _strong_candidate()
        c.source_access_risk = "scraping_required"
        flags = candidate_risk_flags(c)
        assert "requires_unauthorized_scraping" in flags

    def test_forbidden_source_triggers_flag(self) -> None:
        c = _strong_candidate()
        c.source_access_risk = "forbidden"
        flags = candidate_risk_flags(c)
        assert "source_access_forbidden" in flags

    def test_category_mismatch_triggers_flag(self) -> None:
        c = _strong_candidate()
        c.category_fit = "none"
        flags = candidate_risk_flags(c)
        assert "category_mismatch" in flags

    def test_unverified_candidate_carries_umbrella_flag(self) -> None:
        flags = candidate_risk_flags(_unverified_candidate())
        assert "unverified_metadata" in flags

    def test_clean_strong_candidate_has_no_flags(self) -> None:
        flags = candidate_risk_flags(_strong_candidate())
        assert flags == []

    def test_risk_flags_are_deduped_and_ordered(self) -> None:
        """A candidate triggering both contamination and prior must
        not list ``contaminated_in_signal_layer`` twice."""
        c = _contaminated_vivago_candidate()
        c.contamination_risk = "high"  # also explicit high
        flags = candidate_risk_flags(c)
        assert flags.count("contaminated_in_signal_layer") == 1


# ---------------------------------------------------------------------------
# Scorecard determinism + structure
# ---------------------------------------------------------------------------


class TestScorecard:
    def test_scorecard_is_deterministic(self) -> None:
        c = _strong_candidate()
        a = candidate_scorecard(c)
        b = candidate_scorecard(c)
        assert a == b

    def test_strong_candidate_calibration_value_is_high(self) -> None:
        sc = candidate_scorecard(_strong_candidate())
        assert sc["calibration_value"] >= 0.90

    def test_weak_candidate_calibration_value_is_low(self) -> None:
        sc = candidate_scorecard(_weak_candidate())
        assert sc["calibration_value"] < 0.45

    def test_scorecard_contains_required_keys(self) -> None:
        sc = candidate_scorecard(_strong_candidate())
        for k in (
            "dimensions", "raw_total", "max_total",
            "calibration_value", "risk_flags",
        ):
            assert k in sc, f"missing key {k}"
        for dim in (
            "cutoff_clarity", "observation_count",
            "outcome_label_mappability",
            "pre_post_separation_quality",
            "contamination_risk_inverse",
            "model_prior_risk_inverse",
            "source_accessibility", "category_fit",
        ):
            assert dim in sc["dimensions"], f"missing dim {dim}"

    def test_contaminated_name_forces_contam_inverse_to_zero(self) -> None:
        """Even if operator marked contamination_risk='none', a
        product_name like 'Vivago' forces the inverse score to 0."""
        c = _contaminated_vivago_candidate()
        c.contamination_risk = "none"
        sc = candidate_scorecard(c)
        assert sc["dimensions"]["contamination_risk_inverse"] == 0


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


class TestRanking:
    def test_strong_ranks_above_weak(self) -> None:
        rows = rank_case_candidates([
            _weak_candidate(), _strong_candidate(),
        ])
        assert rows[0]["candidate_id"] == "fake_strong_a"
        assert rows[1]["candidate_id"] == "fake_weak_a"

    def test_ranking_is_deterministic(self) -> None:
        candidates = [
            _strong_candidate(), _medium_candidate(), _weak_candidate(),
        ]
        a = rank_case_candidates(candidates)
        b = rank_case_candidates(list(reversed(candidates)))
        # Same set of candidates → same final order
        assert [r["candidate_id"] for r in a] == [
            r["candidate_id"] for r in b
        ]

    def test_ranking_tiebreaks_on_candidate_id(self) -> None:
        a = _strong_candidate()
        a.candidate_id = "z_strong"
        b = _strong_candidate()
        b.candidate_id = "a_strong"
        rows = rank_case_candidates([a, b])
        # Same calibration_value → alphabetical candidate_id wins
        assert rows[0]["candidate_id"] == "a_strong"
        assert rows[1]["candidate_id"] == "z_strong"

    def test_ranking_does_not_mutate_input(self) -> None:
        c1, c2 = _strong_candidate(), _weak_candidate()
        original_ids = [c1.candidate_id, c2.candidate_id]
        rank_case_candidates([c1, c2])
        assert [c1.candidate_id, c2.candidate_id] == original_ids


# ---------------------------------------------------------------------------
# Preliminary shortlist behavior
# ---------------------------------------------------------------------------


class TestPreliminaryShortlist:
    def test_shortlist_has_five_slots(self) -> None:
        sl = preliminary_unverified_shortlist()
        assert len(sl) == 5

    def test_every_slot_is_unverified(self) -> None:
        """Phase 12A.3 honesty rule: no slot may be auto-accepted."""
        for c in preliminary_unverified_shortlist():
            assert evaluate_candidate_suitability(c) == "unverified", (
                f"{c.candidate_id} unexpectedly classified non-unverified"
            )

    def test_no_slot_contains_real_product_name(self) -> None:
        """The placeholder convention is
        '[OPERATOR_TO_SUPPLY: …]' — assert every product_name uses it."""
        for c in preliminary_unverified_shortlist():
            assert "OPERATOR_TO_SUPPLY" in c.product_name, (
                f"{c.candidate_id} appears to name a real product: "
                f"{c.product_name!r}"
            )

    def test_no_slot_names_contaminated_products(self) -> None:
        """Vivago and Semble must not appear in the shortlist."""
        for c in preliminary_unverified_shortlist():
            name_lc = c.product_name.lower()
            assert "vivago" not in name_lc
            assert "semble" not in name_lc

    def test_no_slot_has_outcome_data_or_hidden_fields(self) -> None:
        """Honesty rule: no real outcome distribution may be carried
        on a Phase 12A.3 candidate."""
        for c in preliminary_unverified_shortlist():
            # CaseCandidate has no outcome_distribution field at all;
            # confirm structurally.
            assert not hasattr(c, "observed_distribution")
            assert not hasattr(c, "hidden_real_world_outcome")

    def test_shortlist_ranks_all_unverified(self) -> None:
        rows = rank_case_candidates(preliminary_unverified_shortlist())
        assert {r["recommendation"] for r in rows} == {"unverified"}

    def test_shortlist_covers_five_category_slots(self) -> None:
        """One slot for each category in the phase spec."""
        sl = preliminary_unverified_shortlist()
        ids = {c.candidate_id for c in sl}
        assert ids == {
            "slot_product_hunt_ai_saas_a",
            "slot_hacker_news_devtool_a",
            "slot_chrome_extension_or_app_store_a",
            "slot_b2b_prosumer_tool_a",
            "slot_consumer_product_with_reviews_a",
        }


# ---------------------------------------------------------------------------
# Safety / structural guards
# ---------------------------------------------------------------------------


class TestPackageSafety:
    def test_no_network_or_llm_imports_in_phase_12a_3_modules(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        pkg_root = Path(pkg.__file__).resolve().parent
        forbidden_substrings = (
            "import httpx", "import requests", "import aiohttp",
            "import scrapy", "import selenium", "import playwright",
            "import bs4", "from bs4",
            "from anthropic", "from openai",
            "with_cost_guard",
            "AnthropicProvider", "OpenAIProvider",
            "from assembly.llm",
        )
        for name in (
            "case_candidate_selection.py",
            "candidate_shortlist_examples.py",
        ):
            content = (pkg_root / name).read_text(encoding="utf-8")
            for bad in forbidden_substrings:
                assert bad not in content, (
                    f"forbidden substring {bad!r} found in {name}"
                )

    def test_no_schema_or_db_imports_in_phase_12a_3_modules(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        pkg_root = Path(pkg.__file__).resolve().parent
        for name in (
            "case_candidate_selection.py",
            "candidate_shortlist_examples.py",
        ):
            content = (pkg_root / name).read_text(encoding="utf-8")
            assert "from sqlalchemy" not in content
            assert "import sqlalchemy" not in content
            assert "alembic" not in content.lower()
            assert "apps/web" not in content
            assert "apps.web" not in content

    def test_no_real_outcome_data_committed_in_phase_12a_3(self) -> None:
        """Last-line guard: scan the shortlist module for any
        substring that looks like real outcome data (percentages,
        counts associated with bucket names, real URLs)."""
        from pathlib import Path
        import re
        import assembly.calibration as pkg
        pkg_root = Path(pkg.__file__).resolve().parent
        content = (
            pkg_root / "candidate_shortlist_examples.py"
        ).read_text(encoding="utf-8")
        # No URLs.
        assert "http://" not in content
        assert "https://" not in content
        # No bucket counts beside a number.
        for bucket in ("buyer", "receptive", "uncertain", "skeptical"):
            # The bucket word may appear in a comment but never with
            # a numeric value like "buyer: 8" or "buyer=8".
            assert not re.search(
                rf"{bucket}\s*[:=]\s*[0-9]", content,
            ), f"shortlist appears to carry a {bucket} count"

    def test_calibration_package_still_imports_cleanly(self) -> None:
        import assembly.calibration  # noqa: F401
