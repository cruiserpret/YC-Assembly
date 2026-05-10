"""Phase 8.2I — schema tests."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from assembly.pipeline.audience_retrieval.schemas import (
    NextStepRecommendation,
)
from assembly.pipeline.run_scoped_topup.schemas import (
    CategoryBeforeAfter,
    RunScopedReauditResult,
    RunScopedTopUpLoopResult,
    RunScopedTopUpPlan,
    TopUpExecutionResult,
    TopUpPersonaWriteResult,
)


def _good_plan() -> RunScopedTopUpPlan:
    return RunScopedTopUpPlan(
        brief_label="amboras",
        target_categories=["a"],
        queries_by_category={"a": ["q1"]},
        total_queries=1,
        max_queries_per_category=3,
        max_total_queries=15,
        max_results_per_query=10,
        max_accepted_records=100,
        max_content_chars=4000,
        persona_write_cap=50,
        cost_cap_usd=Decimal("2.00"),
        sensitive_caveats=[],
        requires_compliance_approval=False,
    )


def test_run_scoped_topup_plan_validates_minimum_payload() -> None:
    p = _good_plan()
    assert p.brief_label == "amboras"


def test_plan_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RunScopedTopUpPlan(
            brief_label="x",
            target_categories=["a"],
            queries_by_category={"a": ["q"]},
            total_queries=1,
            max_queries_per_category=3,
            max_total_queries=15,
            max_results_per_query=10,
            max_accepted_records=100,
            max_content_chars=4000,
            persona_write_cap=50,
            cost_cap_usd=Decimal("2.00"),
            sensitive_caveats=[],
            requires_compliance_approval=False,
            extra_field="oops",  # type: ignore[call-arg]
        )


def test_plan_caps_bounded() -> None:
    with pytest.raises(ValidationError):
        RunScopedTopUpPlan(
            brief_label="x",
            target_categories=["a"],
            queries_by_category={"a": ["q"]},
            total_queries=31,  # > 30 (8.2I.1 raised ceiling from 15)
            max_queries_per_category=3,
            max_total_queries=15,
            max_results_per_query=10,
            max_accepted_records=100,
            max_content_chars=4000,
            persona_write_cap=50,
            cost_cap_usd=Decimal("2.00"),
            requires_compliance_approval=False,
        )


def test_plan_min_categories_target() -> None:
    """target_categories must have at least 1 entry."""
    with pytest.raises(ValidationError):
        RunScopedTopUpPlan(
            brief_label="x",
            target_categories=[],  # empty
            queries_by_category={},
            total_queries=1,
            max_queries_per_category=3,
            max_total_queries=15,
            max_results_per_query=10,
            max_accepted_records=100,
            max_content_chars=4000,
            persona_write_cap=50,
            cost_cap_usd=Decimal("2.00"),
            requires_compliance_approval=False,
        )


def test_topup_execution_result_validates() -> None:
    TopUpExecutionResult(
        fetched_count=10, accepted_count=5,
        rejected_count=2, deduped_count=1,
        accepted_by_category={"a": 5},
        new_source_record_ids=["abc"],
        rejected_reason_codes={"x": 1},
        accepted_source_domains={"reddit.com": 5},
        runtime_seconds=1.5,
        live_network_used=True,
    )


def test_topup_execution_result_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        TopUpExecutionResult(
            fetched_count=-1, accepted_count=0,
            rejected_count=0, deduped_count=0,
            runtime_seconds=0.1, live_network_used=True,
        )


def test_topup_persona_write_result_validates() -> None:
    TopUpPersonaWriteResult(
        candidate_shells=10, strong_signal_shells=4,
        weak_signal_shells=3, context_only_shells=3,
        personas_created=4, personas_skipped=0,
        traits_created=40, traits_rejected=0,
        evidence_links_created=20,
        skipped_reasons={},
        new_persona_ids=[],
        cost_estimate_usd=None, cost_actual_usd=0.5,
    )


def test_category_before_after_validates() -> None:
    CategoryBeforeAfter(
        category_key="x", display_name="X",
        before_matched=0, after_matched=2, delta=2,
        coverage_label_before="missing",
        coverage_label_after="acceptable_for_tiny",
    )


def test_reaudit_validates() -> None:
    RunScopedReauditResult(
        before_matched_count=1, after_matched_count=5, matched_delta=4,
        before_tiny_ready=False, after_tiny_ready=True,
        before_small_ready=False, after_small_ready=False,
        before_serious_ready=False, after_serious_ready=False,
        per_category=[],
        new_caveats=[],
        remaining_missing_categories=[],
        next_step_recommendation_before=(
            NextStepRecommendation.RUN_TOPUP_INGESTION_FIRST
        ),
        next_step_recommendation_after=(
            NextStepRecommendation.PROCEED_TO_TINY_SIMULATION
        ),
    )


def test_loop_result_dry_run_omits_optional_fields() -> None:
    plan = _good_plan()
    RunScopedTopUpLoopResult(
        brief_label="x",
        plan=plan, dry_run=True,
        ingestion=None, persona_write=None, reaudit=None,
        summary_text="ok",
        safety_assertions=["ok"],
    )
