"""Phase 12A.4 — Operator candidate metadata intake tests.

Covers ``assembly.calibration.candidate_metadata_intake``:

  parse → validate → convert → score → batch-summarize

All fixtures are synthetic (``fake_*`` ids and product names). No
real product names are committed in this file. Three synthetic
operator batches are defined:

  - fake_ai_saas_candidate
  - fake_hn_devtool_candidate
  - fake_b2b_review_candidate

These exercise the typical operator-metadata shapes the intake
layer expects to handle in the upcoming real-candidate phase.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from assembly.calibration import (
    CaseCandidate,
    IntakeRecord,
    IntakeValidationResult,
    ScoredOperatorCandidate,
    convert_metadata_to_case_candidate,
    parse_operator_candidate_metadata,
    score_operator_candidates,
    summarize_operator_candidate_batch,
    validate_operator_candidate_metadata,
)


# ---------------------------------------------------------------------------
# Synthetic operator-payload builders
# ---------------------------------------------------------------------------


def _fake_ai_saas_candidate() -> dict[str, Any]:
    """Strong, well-formed operator payload — should score 'accept'."""
    return {
        "candidate_id": "fake_ai_saas_candidate",
        "product_name": "FakeAISaaSProduct",
        "category": "AI SaaS tool",
        "launch_or_cutoff_date": "2024-09-15",
        "pre_launch_sources_available": [
            "launch_post_text",
            "founder_announcement_thread",
        ],
        "outcome_sources_available": [
            "public_review_text",
            "operator_supplied_user_feedback",
        ],
        "estimated_observation_count": "500+",
        "contamination_risk": "none",
        "model_prior_risk": "low",
        "outcome_quality": "strong",
        "cutoff_clarity": "clear",
        "category_fit": "strong",
        "source_access_risk": "open_data",
        "notes": "Synthetic strong candidate, unit tests only.",
    }


def _fake_hn_devtool_candidate() -> dict[str, Any]:
    """Medium candidate — clear launch and outcomes but moderate
    dimensions across the board."""
    return {
        "candidate_id": "fake_hn_devtool_candidate",
        "product_name": "FakeHNDevtoolProduct",
        "category": "developer tool",
        "launch_or_cutoff_date": "2024-06-01",
        "pre_launch_sources_available": ["show_hn_thread_text"],
        "outcome_sources_available": ["show_hn_comment_thread_after_top"],
        "estimated_observation_count": "100-500",
        "contamination_risk": "low",
        "model_prior_risk": "medium",
        "outcome_quality": "medium",
        "cutoff_clarity": "approximate",
        "category_fit": "strong",
        "source_access_risk": "public_no_scrape",
    }


def _fake_b2b_review_candidate() -> dict[str, Any]:
    """Operator deliberately leaves several fields blank to verify
    follow-up-question generation."""
    return {
        "candidate_id": "fake_b2b_review_candidate",
        "product_name": "FakeB2BReviewProduct",
        "category": "B2B SaaS",
        # launch_or_cutoff_date omitted
        "pre_launch_sources_available": ["launch_post_or_pricing_page_at_launch"],
        # outcome_sources_available omitted
        # estimated_observation_count omitted
        "contamination_risk": "low",
        # model_prior_risk omitted
        "outcome_quality": "medium",
        # cutoff_clarity omitted
        # category_fit omitted
        # source_access_risk omitted
        "notes": "Operator hasn't supplied launch date or outcome sources yet.",
    }


def _three_fake_batch() -> list[dict[str, Any]]:
    return [
        _fake_ai_saas_candidate(),
        _fake_hn_devtool_candidate(),
        _fake_b2b_review_candidate(),
    ]


# ---------------------------------------------------------------------------
# 1. parse_operator_candidate_metadata
# ---------------------------------------------------------------------------


class TestParse:
    def test_valid_payload_produces_complete_record(self) -> None:
        rec = parse_operator_candidate_metadata(_fake_ai_saas_candidate())
        assert isinstance(rec, IntakeRecord)
        assert rec.candidate_id == "fake_ai_saas_candidate"
        assert rec.product_name == "FakeAISaaSProduct"
        assert rec.launch_or_cutoff_date == date(2024, 9, 15)
        assert rec.estimated_observation_count == "500+"
        assert rec.contamination_risk == "none"
        assert rec.parse_warnings == []

    def test_missing_optional_fields_produce_none_not_warnings(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x",
            "product_name": "X",
            "category": "AI SaaS tool",
        })
        assert rec.launch_or_cutoff_date is None
        assert rec.estimated_observation_count is None
        assert rec.contamination_risk is None
        assert rec.parse_warnings == []

    def test_invalid_enum_value_falls_back_to_none_with_warning(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x",
            "product_name": "X",
            "category": "AI SaaS tool",
            "contamination_risk": "extremely_high_omg",
        })
        assert rec.contamination_risk is None
        assert any(
            "invalid_value_for_contamination_risk" in w
            for w in rec.parse_warnings
        )

    def test_unknown_top_level_key_warned_not_errored(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x",
            "product_name": "X",
            "category": "AI SaaS tool",
            "random_extra_field": "ignored",
        })
        assert any(
            "unknown_top_level_key" in w for w in rec.parse_warnings
        )

    def test_iso_date_string_parsed(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x",
            "product_name": "X",
            "category": "AI SaaS tool",
            "launch_or_cutoff_date": "2024-09-15",
        })
        assert rec.launch_or_cutoff_date == date(2024, 9, 15)

    def test_alt_date_separators_parsed(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
            "launch_or_cutoff_date": "2024/09/15",
        })
        assert rec.launch_or_cutoff_date == date(2024, 9, 15)

    def test_unparseable_date_becomes_none_with_warning(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
            "launch_or_cutoff_date": "sometime in 2024",
        })
        assert rec.launch_or_cutoff_date is None
        assert any("unparseable_date" in w for w in rec.parse_warnings)

    def test_string_passed_as_source_list_accepted_as_one_item(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
            "pre_launch_sources_available": "launch_post_text",
        })
        assert rec.pre_launch_sources_available == ["launch_post_text"]

    def test_non_string_in_source_list_warned(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
            "outcome_sources_available": ["good_source", 42, "another"],
        })
        assert rec.outcome_sources_available == ["good_source", "another"]
        assert any(
            "non_string_in_outcome_sources_available" in w
            for w in rec.parse_warnings
        )

    def test_non_dict_payload_handled_gracefully(self) -> None:
        rec = parse_operator_candidate_metadata("not a dict")  # type: ignore[arg-type]
        assert any(
            "payload_not_a_dict" in w for w in rec.parse_warnings
        )

    def test_case_insensitive_enum_normalization(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
            "contamination_risk": "HIGH",
            "source_access_risk": "Open-Data",
            "outcome_quality": " strong ",
        })
        assert rec.contamination_risk == "high"
        assert rec.source_access_risk == "open_data"
        assert rec.outcome_quality == "strong"


# ---------------------------------------------------------------------------
# 2. validate_operator_candidate_metadata
# ---------------------------------------------------------------------------


class TestValidate:
    def test_complete_payload_is_valid(self) -> None:
        rec = parse_operator_candidate_metadata(_fake_ai_saas_candidate())
        val = validate_operator_candidate_metadata(rec)
        assert val.is_valid
        assert val.errors == []
        assert val.missing_required == []
        assert val.missing_optional == []
        assert val.operator_followup_questions == []

    def test_missing_required_field_makes_invalid(self) -> None:
        rec = parse_operator_candidate_metadata({
            "product_name": "X", "category": "AI SaaS tool",
            # candidate_id missing
        })
        val = validate_operator_candidate_metadata(rec)
        assert not val.is_valid
        assert "candidate_id" in val.missing_required
        assert any("candidate_id" in q for q in val.operator_followup_questions)

    def test_missing_all_optional_produces_follow_up_questions(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
        })
        val = validate_operator_candidate_metadata(rec)
        assert val.is_valid  # required present, even if optional all missing
        assert len(val.missing_optional) == 10
        assert len(val.operator_followup_questions) == 10
        joined = " ".join(val.operator_followup_questions)
        for snippet in (
            "ISO date", "pre-launch sources", "outcome sources",
            "reactions", "evidence/signal layers", "pretrained LLM",
            "buyer / receptive", "cutoff", "category", "outcome data",
        ):
            assert snippet in joined, (
                f"missing follow-up question containing {snippet!r}"
            )

    def test_validation_propagates_parse_warnings(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
            "contamination_risk": "very_high",
        })
        val = validate_operator_candidate_metadata(rec)
        assert any(
            "invalid_value_for_contamination_risk" in w
            for w in val.warnings
        )


# ---------------------------------------------------------------------------
# 3. convert_metadata_to_case_candidate
# ---------------------------------------------------------------------------


class TestConvert:
    def test_complete_payload_round_trips(self) -> None:
        rec = parse_operator_candidate_metadata(_fake_ai_saas_candidate())
        c = convert_metadata_to_case_candidate(rec)
        assert isinstance(c, CaseCandidate)
        assert c.candidate_id == "fake_ai_saas_candidate"
        assert c.launch_or_cutoff_date == date(2024, 9, 15)
        assert c.contamination_risk == "none"
        assert c.outcome_quality == "strong"

    def test_partial_payload_defaults_safely(self) -> None:
        rec = parse_operator_candidate_metadata({
            "candidate_id": "x", "product_name": "X",
            "category": "AI SaaS tool",
        })
        c = convert_metadata_to_case_candidate(rec)
        assert c.estimated_observation_count == "unknown"
        assert c.outcome_quality == "unknown"
        assert c.cutoff_clarity == "unclear"
        assert c.contamination_risk == "low"
        assert c.model_prior_risk == "medium"


# ---------------------------------------------------------------------------
# 4. score_operator_candidates — end-to-end
# ---------------------------------------------------------------------------


class TestScoreOperatorCandidates:
    def test_strong_payload_scores_accept(self) -> None:
        scored = score_operator_candidates([_fake_ai_saas_candidate()])
        assert scored[0].recommendation == "accept"
        assert scored[0].calibration_value >= 0.90
        assert scored[0].risk_flags == []
        assert scored[0].operator_followup_questions == []

    def test_medium_payload_scores_below_accept_threshold(self) -> None:
        scored = score_operator_candidates([_fake_hn_devtool_candidate()])
        # cv >= 0.45 but < 0.70 expected for medium
        assert scored[0].recommendation in ("maybe", "accept")
        if scored[0].recommendation == "maybe":
            assert 0.45 <= scored[0].calibration_value < 0.70

    def test_unverified_payload_lists_followup_questions(self) -> None:
        scored = score_operator_candidates([_fake_b2b_review_candidate()])
        s = scored[0]
        assert s.recommendation == "unverified"
        assert "launch_or_cutoff_date" in s.missing_optional_fields
        assert "outcome_sources_available" in s.missing_optional_fields
        # questions are present and specifically reference what's missing
        joined = " ".join(s.operator_followup_questions)
        assert "ISO date" in joined
        assert "outcome sources" in joined

    def test_vivago_product_name_auto_rejects(self) -> None:
        payload = _fake_ai_saas_candidate()
        payload["product_name"] = "VivagoCloneX"
        scored = score_operator_candidates([payload])
        assert scored[0].recommendation == "reject"
        assert "contaminated_in_signal_layer" in scored[0].risk_flags

    def test_semble_product_name_auto_rejects(self) -> None:
        payload = _fake_hn_devtool_candidate()
        payload["product_name"] = "SembleVariantY"
        scored = score_operator_candidates([payload])
        assert scored[0].recommendation == "reject"
        assert "contaminated_in_signal_layer" in scored[0].risk_flags

    def test_famous_high_prior_auto_rejects(self) -> None:
        payload = _fake_ai_saas_candidate()
        payload["model_prior_risk"] = "high"
        scored = score_operator_candidates([payload])
        assert scored[0].recommendation == "reject"
        assert "model_prior_too_strong" in scored[0].risk_flags

    def test_scraping_required_auto_rejects(self) -> None:
        payload = _fake_ai_saas_candidate()
        payload["source_access_risk"] = "scraping_required"
        scored = score_operator_candidates([payload])
        assert scored[0].recommendation == "reject"
        assert "requires_unauthorized_scraping" in scored[0].risk_flags

    def test_low_observation_count_carries_warning_flag(self) -> None:
        payload = _fake_ai_saas_candidate()
        payload["estimated_observation_count"] = "<30"
        scored = score_operator_candidates([payload])
        assert "insufficient_observations" in scored[0].risk_flags

    def test_unknown_launch_date_carries_warning_flag(self) -> None:
        payload = _fake_ai_saas_candidate()
        payload.pop("launch_or_cutoff_date")
        scored = score_operator_candidates([payload])
        # Either unverified (if other fields missing) or has the flag
        assert "vague_cutoff_date" in scored[0].risk_flags

    def test_validation_errors_propagate_through_scoring(self) -> None:
        payload = _fake_ai_saas_candidate()
        del payload["candidate_id"]  # required
        scored = score_operator_candidates([payload])
        s = scored[0]
        assert "missing_required=candidate_id" in s.validation_errors
        # Fallback candidate_id is "unspecified_candidate"
        assert s.candidate_id == "unspecified_candidate"


# ---------------------------------------------------------------------------
# 5. summarize_operator_candidate_batch
# ---------------------------------------------------------------------------


class TestBatchSummary:
    def test_three_fake_batch_summarizes(self) -> None:
        scored = score_operator_candidates(_three_fake_batch())
        summary = summarize_operator_candidate_batch(scored)
        assert summary["batch_size"] == 3
        assert summary["by_recommendation"]["accept"] >= 1
        assert summary["by_recommendation"]["unverified"] >= 1
        assert summary["with_followup_questions"] >= 1
        # ranked output present and complete
        ranked_ids = [r["candidate_id"] for r in summary["ranked"]]
        assert set(ranked_ids) == {
            "fake_ai_saas_candidate",
            "fake_hn_devtool_candidate",
            "fake_b2b_review_candidate",
        }
        # Strong candidate ranks first
        assert ranked_ids[0] == "fake_ai_saas_candidate"

    def test_ranking_is_deterministic(self) -> None:
        scored_a = score_operator_candidates(_three_fake_batch())
        scored_b = score_operator_candidates(list(reversed(_three_fake_batch())))
        sa = summarize_operator_candidate_batch(scored_a)
        sb = summarize_operator_candidate_batch(scored_b)
        assert [r["candidate_id"] for r in sa["ranked"]] == [
            r["candidate_id"] for r in sb["ranked"]
        ]

    def test_summary_counts_validation_errors(self) -> None:
        bad = _fake_ai_saas_candidate()
        del bad["candidate_id"]
        scored = score_operator_candidates([
            _fake_ai_saas_candidate(), bad,
        ])
        s = summarize_operator_candidate_batch(scored)
        assert s["with_validation_errors"] == 1
        assert s["with_missing_required"] == 1

    def test_empty_batch_returns_zero_size(self) -> None:
        s = summarize_operator_candidate_batch([])
        assert s["batch_size"] == 0
        assert s["ranked"] == []


# ---------------------------------------------------------------------------
# 6. Three synthetic fake candidates round-trip
# ---------------------------------------------------------------------------


class TestSyntheticBatch:
    def test_three_synthetic_candidates_round_trip_independently(self) -> None:
        scored = score_operator_candidates(_three_fake_batch())
        recs = {s.candidate_id: s.recommendation for s in scored}
        assert recs["fake_ai_saas_candidate"] == "accept"
        assert recs["fake_hn_devtool_candidate"] in ("accept", "maybe")
        assert recs["fake_b2b_review_candidate"] == "unverified"

    def test_no_synthetic_candidate_carries_real_outcome_data(self) -> None:
        """A synthetic operator payload must not include any field
        whose name looks like a hidden-outcome field. The intake
        layer must reject (warn) unknown top-level keys, so any
        accidental ``observed_distribution`` etc. would surface."""
        for payload in _three_fake_batch():
            for k in payload:
                assert not any(
                    s in k.lower() for s in (
                        "observed_", "post_launch", "real_world_",
                        "outcome_distribution", "ground_truth",
                    )
                ), f"synthetic payload contains outcome-shaped key: {k}"


# ---------------------------------------------------------------------------
# 7. Safety / structural guards
# ---------------------------------------------------------------------------


class TestPackageSafety:
    def test_no_network_or_llm_imports_in_phase_12a_4_module(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        py = Path(pkg.__file__).resolve().parent / "candidate_metadata_intake.py"
        content = py.read_text(encoding="utf-8")
        forbidden_substrings = (
            "import httpx", "import requests", "import aiohttp",
            "import scrapy", "import selenium", "import playwright",
            "import bs4", "from bs4",
            "from anthropic", "from openai",
            "with_cost_guard",
            "AnthropicProvider", "OpenAIProvider",
            "from assembly.llm",
        )
        for bad in forbidden_substrings:
            assert bad not in content, (
                f"forbidden substring {bad!r} found in "
                "candidate_metadata_intake.py"
            )

    def test_no_schema_or_db_imports_in_phase_12a_4_module(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        py = Path(pkg.__file__).resolve().parent / "candidate_metadata_intake.py"
        content = py.read_text(encoding="utf-8")
        assert "from sqlalchemy" not in content
        assert "import sqlalchemy" not in content
        assert "alembic" not in content.lower()
        assert "apps/web" not in content
        assert "apps.web" not in content

    def test_no_real_outcome_data_in_phase_12a_4_module(self) -> None:
        """The intake module must not carry any real outcome
        proportions or URLs."""
        from pathlib import Path
        import re
        import assembly.calibration as pkg
        py = Path(pkg.__file__).resolve().parent / "candidate_metadata_intake.py"
        content = py.read_text(encoding="utf-8")
        assert "http://" not in content
        assert "https://" not in content
        for bucket in ("buyer", "receptive", "uncertain", "skeptical"):
            assert not re.search(
                rf"{bucket}\s*[:=]\s*[0-9]", content,
            )

    def test_calibration_package_still_imports_cleanly(self) -> None:
        import assembly.calibration  # noqa: F401
