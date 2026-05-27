"""Phase 10A.1 — live_founder_brief end-to-end orchestrator.

Walks the 13-stage pipeline against an `assembly_runs` row, updating
`stage_progress` + `current_stage` + `artifact_manifest` after each
stage. Per-run artifacts live under `_audit/live_runs/{run_id}/`.

NO new retrieval providers. NO Jina/Exa/DataForSEO/Reddit/Apify. Only
existing Brave/Tavily/YouTube/Firecrawl/Amazon-local providers, gated
on configured keys. All LLM calls go through `cost_guarded_chat`.

Failure handling: if any stage fails (missing keys, cost cap exceeded,
schema validation, secret-leak detection), the orchestrator marks
`failed_stage` + `status="failed"` + a clear `error_message`. Never
fabricates a successful report.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assembly.config import get_settings
from assembly.db import get_sessionmaker
from assembly.models.assembly_run import AssemblyRun, AssemblyRunArtifact
from assembly.models.cohort import (
    SocietyCohort, SocietyCohortRollup,
)
from assembly.models.discussion import (
    DiscussionGroup, DiscussionPrivateBallot, DiscussionSession,
    DiscussionTurn, PersonaMemoryAtom,
)
from assembly.models.intent import (
    SimulatedIntent, SimulatedIntentRollup,
    SocietyArgument, SocietyArgumentPropagation,
)
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait,
)
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.sources.cohort_architecture import (
    build_cohort_feature_vectors, cluster_personas_into_cohorts,
    select_cohort_representatives, summarize_cohort,
    build_society_rollup, evaluate_cohort_architecture_quality,
    render_cohort_report_json,
)
from assembly.sources.cohort_architecture.clusterer import assignment_audit
from assembly.sources.discussion_layer import (
    forbidden_claim_audit, sensitive_inference_audit,
)
from assembly.explainability import (
    build_explainability_panel,
    build_niche_signals,
    build_persona_reasoning_cards,
    render_12f1_markdown_section,
)
from assembly.sources.founder_report_generator import scan_for_secrets
from assembly.sources.intent_layer import (
    build_intent_rollup, evaluate_intent_and_debate_quality,
    extract_society_arguments, infer_simulated_intent,
    propagate_arguments_across_cohorts,
    render_intent_and_debate_report_json,
    render_intent_and_debate_report_markdown,
)
from assembly.orchestration.live_evidence_pipeline import (
    plan_live_evidence_queries, run_live_retrieval,
    score_and_accept_evidence, extract_signals_from_accepted,
    build_fresh_persona_candidates, compress_to_live_society,
    persist_live_society, make_live_run_scope_id, provider_keys_summary,
)
from assembly.orchestration.live_discussion_pipeline import (
    run_live_discussion,
)
from assembly.orchestration.live_quality_gates import (
    evaluate_persona_quality_gates,
    scan_fresh_live_artifacts_for_stale_wording,
    scan_user_facing_language,
    write_persona_quality_gates_artifact,
    write_wording_audit_artifact,
)
from assembly.orchestration.live_final_ballot_repair import (
    repair_missing_final_ballots,
)
from assembly.sources.persona_psychology_layer import (
    infer_persona_psychology_profile,
)
from assembly.sources.product_grounding import (
    audit_ballot_caveat_leaks,
    audit_discussion_diversity,
    audit_forbidden_features,
    audit_human_society_realism,
    audit_input_mechanism,
    audit_negation_scope,
    audit_price_hierarchy,
    audit_product_grounding,
    audit_provided_fact_accuracy,
    audit_provided_fact_lock_v2,
    audit_receptive_strictness_v3,
    audit_stance_strictness,
    build_best_fit_audience,
    build_confident_headline,
    build_evidence_flavor,
    build_hardest_to_convince,
    calibrate_ballots,
    detect_caveat_leak,
    detect_self_awareness_leak,
    fact_card_prompt_block,
    generate_product_fact_card,
    repair_forbidden_feature_mentions,
    repair_known_fact_reask,
    repair_negation_scope_inversion,
    repair_price_confusion,
    role_distribution_from_ballots,
    strip_caveat_leak,
    strip_self_awareness_leak,
)


# Phase 10A.3 — minimum acceptable final-ballot completeness. If a
# live run drops below this after the repair gate runs, the run is
# failed safely instead of returning a deceptively-complete report.
_FINAL_BALLOT_MIN_COMPLETENESS = 0.95


logger = logging.getLogger(__name__)


PIPELINE_STAGES: tuple[str, ...] = (
    "validating_brief",
    "planning_evidence",
    "retrieving_evidence",
    "scoring_evidence",
    "building_personas",
    "enriching_psychology",
    "running_individual_simulation",
    "running_group_discussion",
    "repairing_incomplete_outputs",
    "building_cohorts",
    "inferring_simulated_intent",
    "running_society_wide_debate",
    "generating_report",
)
# Phase 12C — 100-voter overlay runs as a side-effect at the END of
# `inferring_simulated_intent` (NOT as a new stage), because adding
# a new stage name would require an Alembic migration to widen the
# `ck_assembly_runs_current_stage` CHECK constraint. The overlay is
# still FAILURE-TOLERANT: any error inside is caught + logged, and
# the existing 24-rich pipeline output is never mutated.
_AUDIT_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "_audit"
_LIVE_RUNS_ROOT = _AUDIT_ROOT / "live_runs"


# Default soft caps for live runs (standard depth)
_DEFAULT_LIVE_CAP_USD = Decimal("12.00")
_FAST_DEMO_CAP_USD = Decimal("0.00")  # fixture_demo - no LLM
_DEEP_CAP_USD = Decimal("0.00")  # not enabled yet
_DEFAULT_PERSONA_COUNT = 24


def resolve_live_discussion_cap_usd(
    *,
    settings: Any,
    amazon_block_present: bool,
    tech_market_block_present: bool,
    explicit_max_budget_usd: float | None = None,
) -> Decimal:
    """Compute the per-simulation cap for `run_live_discussion` and
    `run_live_final_ballot_repair`.

    Phase 11D.13. Replaces the hardcoded ``_DEFAULT_LIVE_CAP_USD``
    pass-through that caused Phase 11D.12 Mode C to hit the cap at
    ``discussion_round_final_ballot`` because the broadcast tech-market
    persona block adds ~19% to prompt tokens. The cap is now:

      cap = base
          + (amazon_buffer  if amazon_block_present       else 0)
          + (tm_buffer      if tech_market_block_present  else 0)

    clamped to ``settings.cost_hard_usd``. An explicit
    ``max_budget_usd`` from ``ctx`` overrides everything and is also
    clamped to ``cost_hard_usd``.

    Production safety: when both persona-block flags default to OFF
    (production state) AND no explicit override is given, the cap
    stays at ``base`` with NO clamping — bit-identical behavior to
    the pre-Phase-11D.13 path. Clamping to ``cost_hard_usd`` is only
    applied when a buffer would otherwise lift the cap (the new
    persona-injection path) or when an operator passes an explicit
    override (so the operator can never accidentally exceed the
    global hard cap they configured).
    """
    hard_ceiling = Decimal(str(settings.cost_hard_usd))
    base = Decimal(str(settings.live_discussion_base_cap_usd))

    if explicit_max_budget_usd is not None:
        return min(Decimal(str(explicit_max_budget_usd)), hard_ceiling)

    # No persona block active — preserve exact pre-11D.13 default.
    if not amazon_block_present and not tech_market_block_present:
        return base

    buffer = Decimal("0.00")
    if amazon_block_present:
        buffer += Decimal(
            str(settings.live_discussion_amazon_block_buffer_usd)
        )
    if tech_market_block_present:
        buffer += Decimal(
            str(settings.live_discussion_tech_market_block_buffer_usd)
        )
    return min(base + buffer, hard_ceiling)


def estimate_pipeline_cost(
    *,
    persona_count: int,
    report_depth: str = "standard",
) -> dict[str, Any]:
    """Rough cost estimate for the LLM-heavy stages (8 + 11). Each
    persona × ~7 discussion-round LLM calls + a few framework calls.
    Sonnet ~ $0.018/call. Used for the cost-cap pre-check."""
    if report_depth == "fast_demo":
        return {
            "expected_calls": 0,
            "estimated_cost_usd": 0.0,
            "model": "n/a (fixture_demo path uses no LLM)",
        }
    expected_calls = persona_count * 7  # discussion rounds
    expected_cost = round(expected_calls * 0.018, 2)
    return {
        "expected_calls": expected_calls,
        "estimated_cost_usd": expected_cost,
        "model": "claude-sonnet-4-6",
    }


# -----------------------------------------------------------------------
# State machine helpers
# -----------------------------------------------------------------------


async def _load_run(
    session: AsyncSession, run_id: uuid.UUID,
) -> AssemblyRun:
    row = (await session.execute(
        select(AssemblyRun).where(AssemblyRun.id == run_id)
    )).scalars().first()
    if row is None:
        raise RuntimeError(f"AssemblyRun {run_id} not found")
    return row


def _initial_stage_progress() -> dict[str, dict[str, Any]]:
    return {
        stage: {
            "status": "pending",
            "started_at": None,
            "completed_at": None,
        }
        for stage in PIPELINE_STAGES
    }


async def _update_run(
    sm: Any,
    run_id: uuid.UUID,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    stage_status: tuple[str, str] | None = None,
    artifact_manifest_update: dict[str, str] | None = None,
    error_message: str | None = None,
    linked_run_scope_id: str | None = None,
) -> None:
    """In-place update of an AssemblyRun row. Atomic per call."""
    async with sm() as session:
        async with session.begin():
            run = await _load_run(session, run_id)
            if status is not None:
                run.status = status
            if current_stage is not None:
                run.current_stage = current_stage
            if stage_status is not None:
                stage, new_status = stage_status
                progress = dict(run.stage_progress or {})
                stage_info = dict(progress.get(stage, {}))
                stage_info["status"] = new_status
                if new_status == "running":
                    stage_info["started_at"] = (
                        datetime.now(UTC).isoformat()
                    )
                if new_status in ("complete", "failed", "skipped"):
                    stage_info["completed_at"] = (
                        datetime.now(UTC).isoformat()
                    )
                progress[stage] = stage_info
                run.stage_progress = progress
            if artifact_manifest_update:
                manifest = dict(run.artifact_manifest or {})
                manifest.update(artifact_manifest_update)
                run.artifact_manifest = manifest
            if error_message is not None:
                run.error_message = error_message
            if linked_run_scope_id is not None:
                run.linked_run_scope_id = linked_run_scope_id
            run.updated_at = datetime.now(UTC)


async def _add_artifact(
    sm: Any, run_id: uuid.UUID, artifact_type: str, path: str,
    *,
    content_type: str = "application/json",
    is_user_visible: bool = True,
) -> None:
    async with sm() as session:
        async with session.begin():
            session.add(AssemblyRunArtifact(
                id=uuid.uuid4(),
                run_id=run_id,
                artifact_type=artifact_type,
                path=path,
                content_type=content_type,
                is_user_visible=is_user_visible,
            ))


# -----------------------------------------------------------------------
# Stage runners
# -----------------------------------------------------------------------


class StageError(RuntimeError):
    """Raised when a stage fails. The orchestrator catches and records
    the failure cleanly."""

    def __init__(self, stage: str, reason: str, recommended_fix: str | None = None):
        self.stage = stage
        self.reason = reason
        self.recommended_fix = recommended_fix
        super().__init__(f"[{stage}] {reason}")


async def _stage_validate_brief(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    brief = run.product_brief or {}
    required = (
        "product_name", "product_description", "price_or_price_structure",
        "launch_geography", "target_customers", "launch_state",
    )
    missing = [k for k in required if not brief.get(k)]
    if missing:
        raise StageError(
            "validating_brief",
            f"missing required brief fields: {missing}",
            "ensure the founder brief includes the required FounderBriefIn fields",
        )
    forbidden_keys = ("personas", "persona_roles", "cohorts")
    for k in forbidden_keys:
        if k in brief:
            raise StageError(
                "validating_brief",
                f"founder brief contains forbidden hardcoded field: {k}",
                "Assembly decides personas dynamically; remove this field",
            )
    (run_dir / "live_founder_brief_input.json").write_text(
        json.dumps(brief, indent=2, default=str), encoding="utf-8",
    )
    ctx["brief"] = brief


async def _stage_planning_evidence(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Plan retrieval queries from the founder brief. Uses the
    brief-agnostic `evidence_anchor_planner.generate_anchor_plan`
    to derive positive / competitor / use-case / objection anchors,
    then build provider queries."""
    brief = ctx["brief"]
    anchor_plan, queries = plan_live_evidence_queries(brief_dict=brief)
    plan_doc = {
        "phase": "10a_3_evidence_plan",
        "mode": "live_founder_brief",
        "evidence_source": "live_retrieval",
        "completed_at": datetime.now(UTC).isoformat(),
        "queries": queries,
        "query_count": len(queries),
        "anchor_plan": anchor_plan.model_dump(mode="json"),
        "retrieval_providers_planned": (
            "tier_1: brave_search + tavily_search; "
            "tier_2 (escalation only): youtube_data_api + "
            "firecrawl_extract — gated by configured keys"
        ),
    }
    (run_dir / "evidence_plan.json").write_text(
        json.dumps(plan_doc, indent=2, default=str), encoding="utf-8",
    )
    ctx["anchor_plan"] = anchor_plan
    ctx["queries"] = queries


async def _stage_retrieving_evidence(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Live retrieval. Calls Brave/Tavily for the configured providers.
    If no retrieval keys AND no reuse_existing_society fallback, fails
    safely. Each retrieved item is normalized into a `accepted_evidence`-
    friendly dict shape.

    Phase 12A.10E: if ctx['evidence_snapshot_id'] is set, the
    snapshot's raw_evidence_items + retrieval audit are loaded and
    live retrieval is skipped entirely. The scoring stage detects the
    same flag and short-circuits to the snapshot's
    accepted_evidence_items."""
    # ---- Phase 12A.10E snapshot bypass ----------------------------
    snap_id = ctx.get("evidence_snapshot_id")
    if snap_id:
        from assembly.calibration.evidence_snapshots import (
            load_snapshot, check_brief_matches_snapshot,
        )
        snap = load_snapshot(snap_id)
        # Verify the brief matches the snapshot. Mismatch raises so
        # we never accidentally combine snapshot A's evidence with
        # brief B's prompts — that would silently corrupt audit trail.
        matches, reason = check_brief_matches_snapshot(
            run.product_brief, snap, require_exact=False,
        )
        if not matches:
            raise StageError(
                "retrieving_evidence",
                f"evidence_snapshot_id={snap_id} does not match the "
                f"current brief: {reason}",
                "either use a snapshot created from this brief or "
                "remove --evidence-snapshot-id to do a fresh live "
                "retrieval",
            )
        retrieval_audit = {
            "phase": "10a_3_evidence_retrieval",
            "mode": "live_founder_brief",
            "evidence_source": "evidence_snapshot",
            "evidence_snapshot_id": snap.evidence_snapshot_id,
            "evidence_snapshot_hash": snap.snapshot_hash,
            "completed_at": datetime.now(UTC).isoformat(),
            "loaded_from_snapshot": True,
            "snapshot_provider_metadata": (
                snap.retrieval_provider_metadata
            ),
            "raw_result_count": int(snap.raw_result_count or 0),
            "any_retrieval_provider_configured": False,
            "errors": [],
        }
        ctx["retrieved_items"] = list(snap.raw_evidence_items)
        ctx["retrieval_audit"] = retrieval_audit
        # Sentinel for the scoring stage:
        ctx["_evidence_from_snapshot"] = True
        ctx["_snapshot"] = snap
        (run_dir / "evidence_retrieval.json").write_text(
            json.dumps(retrieval_audit, indent=2, default=str),
            encoding="utf-8",
        )
        return
    # ---- end snapshot bypass --------------------------------------
    keys = provider_keys_summary()
    any_retrieval = any(
        v for k, v in keys.items()
        if k != "anthropic_api_key_configured"
    )
    reuse_existing = ctx.get("_dev_reuse_existing_society")
    if not any_retrieval and not reuse_existing:
        (run_dir / "evidence_retrieval.json").write_text(
            json.dumps({
                "phase": "10a_3_evidence_retrieval",
                "mode": "live_founder_brief",
                "evidence_source": "live_retrieval",
                "completed_at": datetime.now(UTC).isoformat(),
                "provider_keys": keys,
                "any_retrieval_provider_configured": False,
                "raw_result_count": 0,
                "errors": [
                    "no retrieval provider keys configured",
                ],
            }, indent=2, default=str),
            encoding="utf-8",
        )
        raise StageError(
            "retrieving_evidence",
            "no retrieval provider keys configured (Brave/Tavily/...)",
            "configure at least one of BRAVE_SEARCH_API_KEY / "
            "TAVILY_API_KEY / YOUTUBE_DATA_API_KEY / FIRECRAWL_API_KEY",
        )
    if reuse_existing:
        # Internal-only dev pivot — never the default for normal live
        # mode. The router NEVER sets this; it's an opt-in dev flag
        # for tests/dev that lack retrieval keys.
        retrieval_audit = {
            "phase": "10a_3_evidence_retrieval_dev_reuse",
            "mode": "live_founder_brief_internal_dev_reuse",
            "evidence_source": "internal_dev_reuse",
            "completed_at": datetime.now(UTC).isoformat(),
            "provider_keys": keys,
            "any_retrieval_provider_configured": False,
            "raw_result_count": 0,
            "evidence_strategy": "internal_dev_reuse (NOT default)",
            "errors": [],
        }
        ctx["retrieved_items"] = []
        ctx["retrieval_audit"] = retrieval_audit
        (run_dir / "evidence_retrieval.json").write_text(
            json.dumps(retrieval_audit, indent=2, default=str),
            encoding="utf-8",
        )
        return
    # Phase 10B.2: pass anchor terms to retrieval so the YouTube
    # comment quality filter can reject unrelated noise. Anchors
    # come from the brief's competitors + product-type tokens.
    plan = ctx.get("anchor_plan")
    yt_anchors: list[str] = []
    if plan is not None:
        yt_anchors.extend(getattr(plan, "competitor_anchor_terms", []) or [])
        yt_anchors.extend(getattr(plan, "positive_anchor_terms", []) or [])
        yt_anchors.extend(getattr(plan, "use_case_anchor_terms", []) or [])
    # Also include the brief's named competitors verbatim — e.g.,
    # "PEET", "DryGuy", "Hidrate Spark".
    for c in (run.product_brief.get("competitors_or_alternatives") or []):
        if isinstance(c, str):
            yt_anchors.append(c.lower())
    items, retrieval_audit = run_live_retrieval(
        queries=ctx["queries"],
        anchor_terms=yt_anchors,
    )
    retrieval_audit["phase"] = "10a_3_evidence_retrieval"
    retrieval_audit["mode"] = "live_founder_brief"
    retrieval_audit["evidence_source"] = "live_retrieval"
    retrieval_audit["completed_at"] = datetime.now(UTC).isoformat()
    if len(items) < 1:
        (run_dir / "evidence_retrieval.json").write_text(
            json.dumps(retrieval_audit, indent=2, default=str),
            encoding="utf-8",
        )
        raise StageError(
            "retrieving_evidence",
            f"retrieval returned {len(items)} items — too few to build "
            "a society",
            "broaden the brief / add more competitors or target_customers",
        )
    ctx["retrieved_items"] = items
    ctx["retrieval_audit"] = retrieval_audit
    (run_dir / "evidence_retrieval.json").write_text(
        json.dumps(retrieval_audit, indent=2, default=str),
        encoding="utf-8",
    )


async def _stage_scoring_evidence(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Score retrieved evidence: anchor-match + dedupe + reject fake
    product-use claims. Then extract atomic signals via the 9A.1
    EvidenceSignalExtractor.

    Phase 12A.10E: if ctx['_evidence_from_snapshot'] is set, the
    retrieval stage already loaded a snapshot. Use the snapshot's
    accepted_evidence_items verbatim (preserves the exact pool the
    original run scored) and skip both scoring and signal extraction
    re-runs."""
    # ---- Phase 12A.10E snapshot bypass ----------------------------
    if ctx.get("_evidence_from_snapshot"):
        snap = ctx["_snapshot"]
        accepted = list(snap.accepted_evidence_items)
        score_audit = {
            "phase": "10a_3_evidence_quality",
            "mode": "live_founder_brief",
            "evidence_source": "evidence_snapshot",
            "evidence_snapshot_id": snap.evidence_snapshot_id,
            "evidence_snapshot_hash": snap.snapshot_hash,
            "completed_at": datetime.now(UTC).isoformat(),
            "loaded_from_snapshot": True,
            **(snap.evidence_quality_summary or {}),
        }
        ctx["accepted_evidence"] = accepted
        (run_dir / "evidence_quality.json").write_text(
            json.dumps(score_audit, indent=2, default=str),
            encoding="utf-8",
        )
        # Run signal extraction on the snapshot's accepted evidence —
        # signal extraction is deterministic given the same items, so
        # this produces the same signals every time without needing
        # to be snapshotted itself.
        plan = ctx.get("anchor_plan")
        signals, sig_audit = extract_signals_from_accepted(
            accepted=accepted, plan=plan,
        )
        sig_audit["phase"] = "10a_3_evidence_signals"
        sig_audit["mode"] = "live_founder_brief"
        sig_audit["evidence_source"] = "evidence_snapshot"
        sig_audit["evidence_snapshot_id"] = snap.evidence_snapshot_id
        sig_audit["completed_at"] = datetime.now(UTC).isoformat()
        ctx["evidence_signals"] = signals
        (run_dir / "evidence_signals.json").write_text(
            json.dumps(sig_audit, indent=2, default=str),
            encoding="utf-8",
        )
        return
    # ---- end snapshot bypass --------------------------------------
    if ctx.get("_dev_reuse_existing_society"):
        # dev pivot — skip scoring
        (run_dir / "evidence_quality.json").write_text(
            json.dumps({"skipped": True, "reason": "dev_pivot"},
                       indent=2, default=str),
            encoding="utf-8",
        )
        ctx["accepted_evidence"] = []
        ctx["evidence_signals"] = []
        return
    items = ctx.get("retrieved_items") or []
    plan = ctx["anchor_plan"]
    accepted, score_audit = score_and_accept_evidence(
        items=items, plan=plan,
    )
    score_audit["phase"] = "10a_3_evidence_quality"
    score_audit["mode"] = "live_founder_brief"
    score_audit["evidence_source"] = "live_retrieval"
    score_audit["completed_at"] = datetime.now(UTC).isoformat()
    if len(accepted) < 4:
        (run_dir / "evidence_quality.json").write_text(
            json.dumps(score_audit, indent=2, default=str),
            encoding="utf-8",
        )
        raise StageError(
            "scoring_evidence",
            f"only {len(accepted)} accepted evidence items "
            f"(out of {len(items)}) — too weak to produce personas",
            "broaden brief, add competitors, or relax the anchor "
            "lexicon",
        )
    ctx["accepted_evidence"] = accepted
    (run_dir / "evidence_quality.json").write_text(
        json.dumps(score_audit, indent=2, default=str), encoding="utf-8",
    )
    # Signal extraction
    signals, sig_audit = extract_signals_from_accepted(
        accepted=accepted, plan=plan,
    )
    sig_audit["phase"] = "10a_3_evidence_signals"
    sig_audit["mode"] = "live_founder_brief"
    sig_audit["evidence_source"] = "live_retrieval"
    sig_audit["completed_at"] = datetime.now(UTC).isoformat()
    ctx["evidence_signals"] = signals
    (run_dir / "evidence_signals.json").write_text(
        json.dumps(sig_audit, indent=2, default=str), encoding="utf-8",
    )
    if len(signals) < 8:
        raise StageError(
            "scoring_evidence",
            f"signal extraction yielded only {len(signals)} signals "
            "from accepted evidence — insufficient for persona "
            "generation",
            "broaden brief, add more competitors / use cases",
        )


async def _stage_building_personas(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Build fresh personas from the evidence signals + persist them
    under a new run_scope_id. This is the core of Phase 10A.2 — no
    LumaLoop / 9B society reuse for normal live mode."""
    if ctx.get("_dev_reuse_existing_society"):
        return await _stage_building_personas_dev_reuse(
            sm=sm, run=run, run_dir=run_dir, ctx=ctx,
        )
    brief = ctx["brief"]
    signals = ctx["evidence_signals"]
    accepted = ctx["accepted_evidence"]
    plan = ctx["anchor_plan"]
    target_brief_id = re.sub(
        r"[^a-z0-9]+", "_",
        brief["product_name"].lower(),
    ).strip("_")
    candidates, widening_audit = build_fresh_persona_candidates(
        signals=signals, plan=plan, target_brief_id=target_brief_id,
        product_name=brief["product_name"],
    )
    (run_dir / "persona_candidates.json").write_text(
        json.dumps({
            "phase": "10a_3_persona_candidates",
            "mode": "live_founder_brief",
            "persona_source": "fresh_retrieval_driven",
            "completed_at": datetime.now(UTC).isoformat(),
            "raw_candidate_count": len(candidates),
            "widening_audit": widening_audit,
        }, indent=2, default=str), encoding="utf-8",
    )
    if len(candidates) < 21:
        raise StageError(
            "building_personas",
            f"only {len(candidates)} persona candidates from "
            f"{len(signals)} signals — below the 21-persona floor",
            "broaden brief / increase retrieval coverage / lower "
            "min_signals_per_candidate in the widener policy",
        )
    # Compress
    target_count = ctx.get("preferred_persona_count") or _DEFAULT_PERSONA_COUNT
    compressed, compression_audit = compress_to_live_society(
        candidates=candidates,
        accepted_evidence=accepted,
        target_brief_id=target_brief_id,
        product_name=brief["product_name"],
        launch_state=brief.get("launch_state", "unlaunched"),
        hard_max=min(30, max(21, target_count)),
    )
    if len(compressed.compressed_candidates or []) < 21:
        (run_dir / "persona_compression.json").write_text(
            json.dumps({
                "phase": "10a_3_persona_compression",
                "mode": "live_founder_brief",
                "persona_source": "fresh_retrieval_driven",
                "completed_at": datetime.now(UTC).isoformat(),
                "compression_audit": compression_audit,
            }, indent=2, default=str), encoding="utf-8",
        )
        raise StageError(
            "building_personas",
            f"compression produced only "
            f"{len(compressed.compressed_candidates or [])} personas "
            "— below the 21-persona floor",
            "broaden retrieval coverage; check evidence diversity",
        )
    (run_dir / "persona_compression.json").write_text(
        json.dumps({
            "phase": "10a_3_persona_compression",
            "mode": "live_founder_brief",
            "persona_source": "fresh_retrieval_driven",
            "completed_at": datetime.now(UTC).isoformat(),
            "compression_audit": compression_audit,
        }, indent=2, default=str), encoding="utf-8",
    )
    # ---- Persona quality gates (Part D of 10A.3) ----
    run_scope_id_provisional = make_live_run_scope_id(
        product_name=brief["product_name"], run_id=run.id,
    )
    quality_gates = evaluate_persona_quality_gates(
        compressed_candidates=list(compressed.compressed_candidates or []),
        accepted_evidence=accepted,
        target_brief_id=target_brief_id,
        run_scope_id=run_scope_id_provisional,
        min_count=21,
        max_count=30,
        target_product_name=brief.get("product_name"),
    )
    write_persona_quality_gates_artifact(
        run_dir=run_dir, audit=quality_gates,
    )
    if not quality_gates["all_gates_passed"]:
        raise StageError(
            "building_personas",
            "persona quality gates failed: "
            + "; ".join(quality_gates["blocker_messages"][:5]),
            "broaden retrieval, increase provider diversity, or "
            "tune the widener policy. Personas were NOT persisted.",
        )
    # Persist (idempotent on run_scope_id)
    run_scope_id = run_scope_id_provisional
    persistence_result = await persist_live_society(
        sm=sm, compressed=compressed, accepted_evidence=accepted,
        run_scope_id=run_scope_id, product_name=brief["product_name"],
        launch_state=brief.get("launch_state", "unlaunched"),
        target_brief_id=target_brief_id,
    )
    (run_dir / "persistence.json").write_text(
        json.dumps({
            "phase": "10a_3_persistence",
            "mode": "live_founder_brief",
            "persona_source": "fresh_retrieval_driven",
            "completed_at": datetime.now(UTC).isoformat(),
            **persistence_result,
        }, indent=2, default=str), encoding="utf-8",
    )
    if persistence_result.get("personas_inserted", 0) < 21:
        raise StageError(
            "building_personas",
            f"persistence inserted only "
            f"{persistence_result.get('personas_inserted', 0)} personas",
            "see persistence.json for details",
        )
    ctx["live_run_scope_id"] = run_scope_id
    ctx["live_persona_uuids"] = []
    # Reload the fresh personas to expose them downstream
    async with sm() as session:
        rows = (await session.execute(
            select(PersonaRecord).where(
                PersonaRecord.product_relevance_tags.contains(
                    [f"run_scope_id:{run_scope_id}"]
                )
            )
        )).scalars().all()
    ctx["personas"] = rows
    ctx["live_persona_uuids"] = [p.id for p in rows]
    (run_dir / "persona_generation.json").write_text(
        json.dumps({
            "phase": "10a_3_persona_generation",
            "mode": "live_founder_brief",
            "persona_source": "fresh_retrieval_driven",
            "evidence_source": "live_retrieval",
            "completed_at": datetime.now(UTC).isoformat(),
            "persona_count": len(rows),
            "run_scope_id": run_scope_id,
            "compressed_count": len(compressed.compressed_candidates or []),
            "evidence_strategy": "fresh_retrieval_driven",
            "quality_gates_summary": quality_gates.get("gate_results"),
        }, indent=2, default=str), encoding="utf-8",
    )


async def _stage_building_personas_dev_reuse(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Internal-only dev pivot from 10A.1 — kept for testing without
    retrieval keys. NEVER the default for normal live mode."""
    # Reuse existing 9B society — pick a fresh subset stratified by
    # cohort membership to keep the persona set compact for live runs.
    target_count = ctx.get("preferred_persona_count") or _DEFAULT_PERSONA_COUNT
    async with sm() as session:
        existing_session = (await session.execute(
            select(DiscussionSession).where(
                DiscussionSession.phase == "9B",
            ).order_by(DiscussionSession.created_at.desc()).limit(1)
        )).scalars().first()
        if existing_session is None:
            raise StageError(
                "building_personas",
                "no existing 9B society found to reuse — run "
                "Phase 9A.1→9B.1 first",
            )
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id == existing_session.id,
            )
        )).scalars().all()
        all_persona_ids: list[uuid.UUID] = []
        for g in groups:
            for pid in g.persona_ids:
                all_persona_ids.append(pid)
        if not all_persona_ids:
            raise StageError(
                "building_personas",
                "existing 9B society has no group memberships",
            )
        # Sample a deterministic subset, stratified by sha256(pid|run_id)
        import hashlib
        ranked = sorted(
            all_persona_ids,
            key=lambda pid: hashlib.sha256(
                f"{run.id}|{pid}".encode("utf-8")
            ).hexdigest(),
        )
        selected = ranked[:target_count]
        personas = (await session.execute(
            select(PersonaRecord).where(
                PersonaRecord.id.in_(selected)
            )
        )).scalars().all()
        ctx["personas"] = personas
        ctx["existing_session_id"] = existing_session.id
        ctx["existing_run_scope_id"] = existing_session.run_scope_id
    summary = {
        "phase": "10a_3_persona_generation_dev_reuse",
        "mode": "live_founder_brief_internal_dev_reuse",
        "persona_source": "internal_dev_reuse",
        "completed_at": datetime.now(UTC).isoformat(),
        "persona_count": len(ctx["personas"]),
        "sampling_strategy": "deterministic sha256 over (run_id|persona_id)",
        "linked_run_scope_id": ctx["existing_run_scope_id"],
        "note": (
            "internal_dev_reuse path — used only for tests/dev that "
            "lack retrieval keys. NEVER the default for normal live "
            "mode (fresh retrieval-driven generation is the API "
            "default)."
        ),
    }
    (run_dir / "persona_generation.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )


async def _stage_enriching_psychology(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Apply OCEAN + 6 additional psychology traits to each fresh
    persona via the 9A.3 inference engine. For dev_reuse mode, just
    load the existing rows."""
    if ctx.get("_dev_reuse_existing_society"):
        # dev pivot: load existing
        persona_ids = [p.id for p in ctx["personas"]]
        async with sm() as session:
            psy = (await session.execute(
                select(PersonaPsychologyTrait).where(
                    PersonaPsychologyTrait.persona_id.in_(persona_ids)
                ).where(
                    PersonaPsychologyTrait.run_scope_id == ctx.get("existing_run_scope_id", "")
                )
            )).scalars().all()
        by_pid: dict[uuid.UUID, dict[str, float]] = {}
        for t in psy:
            by_pid.setdefault(t.persona_id, {})[t.trait_name] = float(t.value_numeric)
        ctx["psychology_by_pid"] = by_pid
        summary = {
            "phase": "10a_3_psychology_layer_dev_reuse",
            "mode": "live_founder_brief_internal_dev_reuse",
            "persona_source": "internal_dev_reuse",
            "completed_at": datetime.now(UTC).isoformat(),
            "psychology_trait_count": len(psy),
            "evidence_strategy": "internal_dev_reuse",
        }
        (run_dir / "psychology_layer.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8",
        )
        return
    # Fresh path: infer + persist 11 traits per persona
    run_scope_id = ctx["live_run_scope_id"]
    target_brief = ctx["brief"]["product_name"].lower().strip()
    persona_ids = ctx["live_persona_uuids"]
    async with sm() as session:
        traits_by_pid_existing = (await session.execute(
            select(PersonaTrait).where(
                PersonaTrait.persona_id.in_(persona_ids)
            )
        )).scalars().all()
        links_by_pid_existing = (await session.execute(
            select(PersonaEvidenceLink).where(
                PersonaEvidenceLink.persona_id.in_(persona_ids)
            )
        )).scalars().all()
    traits_by_pid: dict[uuid.UUID, list[Any]] = {}
    links_by_pid: dict[uuid.UUID, list[Any]] = {}
    for t in traits_by_pid_existing:
        traits_by_pid.setdefault(t.persona_id, []).append(t)
    for l in links_by_pid_existing:
        links_by_pid.setdefault(l.persona_id, []).append(l)
    inserted = 0
    async with sm() as session:
        async with session.begin():
            for p in ctx["personas"]:
                tags = list(p.product_relevance_tags or [])
                normalized_role = _parse_tag_value(
                    tags, "normalized_primary_role"
                ) or (p.segment_label or "unknown")
                t_dicts = [
                    {
                        "trait_id": str(t.id),
                        "field_name": t.field_name,
                        "value": t.value, "rationale": t.rationale,
                        "confidence": float(t.confidence),
                        "source_ids": [
                            str(s) for s in (t.source_ids or [])
                        ],
                    }
                    for t in traits_by_pid.get(p.id, [])
                ]
                e_dicts = [
                    {
                        "excerpt": l.excerpt,
                        "source_record_id": str(l.source_record_id),
                        "contribution_field": l.contribution_field,
                    }
                    for l in links_by_pid.get(p.id, [])
                ]
                profile = infer_persona_psychology_profile(
                    persona_id=str(p.id),
                    run_scope_id=run_scope_id,
                    target_brief=target_brief,
                    normalized_primary_role=normalized_role,
                    existing_traits=t_dicts,
                    evidence_links=e_dicts,
                    simulation_responses=[],
                    include_price_sensitivity=True,
                )
                for tr in profile.traits:
                    session.add(PersonaPsychologyTrait(
                        id=uuid.uuid4(),
                        persona_id=p.id,
                        run_scope_id=run_scope_id,
                        trait_name=tr.trait_name,
                        value_numeric=Decimal(str(tr.value_numeric)),
                        value_label=tr.value_label,
                        confidence=tr.confidence,
                        inference_method=tr.inference_method,
                        evidence_basis=tr.evidence_basis,
                        source_record_ids=[
                            uuid.UUID(s) for s in tr.source_record_ids
                        ],
                        source_trait_ids=[
                            uuid.UUID(s) for s in tr.source_trait_ids
                        ],
                        simulation_response_ids=[],
                        caveat=tr.caveat,
                        generated_for_phase="10A.2",
                    ))
                    inserted += 1
    # Reload psychology
    async with sm() as session:
        psy = (await session.execute(
            select(PersonaPsychologyTrait).where(
                PersonaPsychologyTrait.persona_id.in_(persona_ids)
            ).where(
                PersonaPsychologyTrait.run_scope_id == run_scope_id
            )
        )).scalars().all()
    by_pid: dict[uuid.UUID, dict[str, float]] = {}
    for t in psy:
        by_pid.setdefault(t.persona_id, {})[t.trait_name] = float(t.value_numeric)
    ctx["psychology_by_pid"] = by_pid
    (run_dir / "psychology_layer.json").write_text(
        json.dumps({
            "phase": "10a_3_psychology_layer",
            "mode": "live_founder_brief",
            "persona_source": "fresh_retrieval_driven",
            "completed_at": datetime.now(UTC).isoformat(),
            "psychology_trait_count": inserted,
            "personas_with_full_psychology": sum(
                1 for p in ctx["personas"]
                if len(by_pid.get(p.id, {})) >= 11
            ),
            "evidence_strategy": "fresh_retrieval_driven",
        }, indent=2, default=str), encoding="utf-8",
    )


async def _stage_running_individual_simulation(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """V0 individual-stance probe. The discussion pre-ballot in
    round 0 already captures each persona's individual stance, so
    Phase 10A.3 marks this stage as deliberately deferred — not
    skipped due to reuse. If we ever add a separate per-persona LLM
    probe before the group discussion, this is where it lands.
    """
    is_dev_reuse = bool(ctx.get("_dev_reuse_existing_society"))
    summary = {
        "phase": (
            "10a_3_individual_simulation"
            if not is_dev_reuse
            else "10a_3_individual_simulation_dev_reuse"
        ),
        "mode": "live_founder_brief",
        "persona_source": (
            "fresh_retrieval_driven" if not is_dev_reuse
            else "internal_dev_reuse"
        ),
        "completed_at": datetime.now(UTC).isoformat(),
        "skipped": True,
        "skip_reason": (
            "deferred-by-design: the round-0 discussion pre-ballot "
            "already captures each persona's individual stance "
            "without a duplicate LLM call."
        ),
    }
    (run_dir / "individual_simulation.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )


async def _stage_running_group_discussion(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Run the live LLM-driven discussion against the freshly persisted
    society. Cost-guarded via cost_guarded_chat with retry/backoff.
    For dev_reuse mode, pass through 9B's existing discussion."""
    if ctx.get("_dev_reuse_existing_society"):
        return await _stage_running_group_discussion_dev_reuse(
            sm=sm, run=run, run_dir=run_dir, ctx=ctx,
        )
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise StageError(
            "running_group_discussion",
            "ANTHROPIC_API_KEY not configured — discussion requires "
            "LLM access via cost_guarded_chat",
            "configure ANTHROPIC_API_KEY",
        )
    from assembly.llm.anthropic import AnthropicProvider
    provider = AnthropicProvider()
    # Phase 11D.13: cap is computed AFTER the persona blocks below so
    # that the per-block buffer can be applied. See
    # `resolve_live_discussion_cap_usd` for the rule.
    # Phase 10B.1: build the Product Fact Card from the founder
    # brief and stash both the structured form + the prompt block on
    # ctx so downstream stages (final-ballot repair, audits) can
    # reuse them without re-deriving.
    fact_card = generate_product_fact_card(ctx["brief"])
    fact_card_block = fact_card_prompt_block(fact_card)
    ctx["product_fact_card"] = fact_card
    ctx["product_fact_card_block"] = fact_card_block

    # Phase 11C.5 — optional Amazon buyer-language block for persona
    # prompts. Returns None unless ALL THREE Amazon flags are on
    # (enabled + runtime_enabled + persona_injection_enabled). When
    # None, run_live_discussion's prompt shape is identical to the
    # Phase-11C.4 production-safe baseline. The block is computed
    # ONCE here and shared across every persona's discussion turn.
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block,
    )
    amazon_persona_block = await build_amazon_persona_prompt_block(
        ctx["brief"],
        sessionmaker=sm,
        settings=get_settings(),
    )
    ctx["amazon_persona_block_present"] = bool(amazon_persona_block)
    ctx["amazon_persona_block_chars"] = (
        len(amazon_persona_block) if amazon_persona_block else 0
    )

    # Phase 11D.11 — optional tech-market persona block. Triple-
    # flag-gated; returns None unless ALL THREE flags
    # (ENABLED, RUNTIME_ENABLED, PERSONA_INJECTION_ENABLED) are
    # True. When None, run_live_discussion's prompt shape stays
    # byte-for-byte identical to the Phase-11D.9 audit-only baseline.
    # The block is computed ONCE per simulation and broadcast to
    # every persona's discussion turn (deliberate Phase 11D.11
    # design — mirrors the Amazon Phase 11C.5 approach).
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_persona_prompt_block,
    )
    tech_market_persona_block = (
        await build_tech_market_persona_prompt_block(
            ctx["brief"],
            sessionmaker=sm,
            settings=get_settings(),
        )
    )
    ctx["tech_market_persona_block_present"] = bool(
        tech_market_persona_block,
    )
    ctx["tech_market_persona_block_chars"] = (
        len(tech_market_persona_block) if tech_market_persona_block else 0
    )

    # Phase 11D.13 — settings-driven cap with persona-block buffers.
    # Replaces the hardcoded ``_DEFAULT_LIVE_CAP_USD`` pass-through
    # that caused Mode C final-ballot CostCapExceeded events in
    # Phase 11D.12. The cap is computed here (post-block-resolution)
    # and reused for both the discussion stage and the repair stage
    # so cumulative spend accounting stays consistent.
    cap = resolve_live_discussion_cap_usd(
        settings=get_settings(),
        amazon_block_present=bool(amazon_persona_block),
        tech_market_block_present=bool(tech_market_persona_block),
        explicit_max_budget_usd=ctx.get("max_budget_usd"),
    )
    ctx["live_discussion_cap_usd"] = float(cap)
    logger.info(
        "live_founder_brief.cap_resolved cap=$%.2f "
        "amazon_block=%s tech_market_block=%s",
        float(cap),
        bool(amazon_persona_block),
        bool(tech_market_persona_block),
    )

    discussion_audit = await run_live_discussion(
        sm=sm,
        run_scope_id=ctx["live_run_scope_id"],
        product_name=ctx["brief"]["product_name"],
        persona_ids=ctx["live_persona_uuids"],
        provider=provider,
        hard_cap_usd=cap,
        group_size=6,
        product_fact_card_text=fact_card_block,
        amazon_persona_block=amazon_persona_block,
        tech_market_persona_block=tech_market_persona_block,
        # Phase 12A.10F: propagate simulation_seed into discussion
        # group assignment so re-runs with the same seed produce
        # identical group splits.
        simulation_seed=ctx.get("simulation_seed"),
    )
    discussion_audit["phase"] = "10a_3_group_discussion"
    discussion_audit["mode"] = "live_founder_brief"
    discussion_audit["persona_source"] = "fresh_retrieval_driven"
    discussion_audit["completed_at"] = datetime.now(UTC).isoformat()
    if discussion_audit.get("skipped"):
        raise StageError(
            "running_group_discussion",
            f"discussion stage skipped: {discussion_audit.get('reason')}",
            "ensure persona_count >= 4",
        )
    # Reload turns + ballots + atoms for downstream stages
    async with sm() as session:
        sessions_for_run = (await session.execute(
            select(DiscussionSession).where(
                DiscussionSession.run_scope_id == ctx["live_run_scope_id"]
            )
        )).scalars().all()
        if not sessions_for_run:
            raise StageError(
                "running_group_discussion",
                "live discussion completed but no DiscussionSession "
                "found for run_scope_id",
            )
        sess = sessions_for_run[-1]
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id == sess.id
            )
        )).scalars().all()
        gids = [g.id for g in groups]
        turns = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(gids)
            )
        )).scalars().all()
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id == sess.id
            )
        )).scalars().all()
        atoms = (await session.execute(
            select(PersonaMemoryAtom).where(
                PersonaMemoryAtom.run_scope_id == ctx["live_run_scope_id"]
            )
        )).scalars().all()
    ctx["turns"] = turns
    ctx["ballots"] = ballots
    ctx["memory_atoms"] = atoms
    ctx["existing_session_id"] = sess.id  # for downstream readers
    by_stage: dict[str, int] = {}
    for b in ballots:
        by_stage[b.ballot_stage] = by_stage.get(b.ballot_stage, 0) + 1
    (run_dir / "discussion.json").write_text(
        json.dumps({
            **discussion_audit,
            "ballot_count_by_stage": by_stage,
        }, indent=2, default=str), encoding="utf-8",
    )
    (run_dir / "discussion_quality.json").write_text(
        json.dumps({
            "phase": "10a_3_discussion_quality",
            "mode": "live_founder_brief",
            "persona_source": "fresh_retrieval_driven",
            "completed_at": datetime.now(UTC).isoformat(),
            "reflection_present_pct": round(
                by_stage.get("reflection", 0)
                / max(len(ctx["live_persona_uuids"]), 1),
                4,
            ),
            "final_ballot_present_pct": round(
                by_stage.get("final", 0)
                / max(len(ctx["live_persona_uuids"]), 1),
                4,
            ),
            "cost_summary": discussion_audit.get("cost_summary"),
        }, indent=2, default=str),
        encoding="utf-8",
    )


async def _stage_running_group_discussion_dev_reuse(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Internal-only dev path — original 10A.1 behavior. Surfaces the
    existing 9B discussion turns + ballots for the sampled persona
    subset; no new LLM calls."""
    persona_ids = [p.id for p in ctx["personas"]]
    async with sm() as session:
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id == ctx["existing_session_id"]
            )
        )).scalars().all()
        relevant_group_ids = [g.id for g in groups]
        turns = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(relevant_group_ids)
            ).where(DiscussionTurn.speaker_persona_id.in_(persona_ids))
        )).scalars().all()
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id == ctx["existing_session_id"]
            ).where(DiscussionPrivateBallot.persona_id.in_(persona_ids))
        )).scalars().all()
        atoms = (await session.execute(
            select(PersonaMemoryAtom).where(
                PersonaMemoryAtom.persona_id.in_(persona_ids)
            )
        )).scalars().all()
    ctx["turns"] = turns
    ctx["ballots"] = ballots
    ctx["memory_atoms"] = atoms
    by_stage: dict[str, int] = {}
    for b in ballots:
        by_stage[b.ballot_stage] = by_stage.get(b.ballot_stage, 0) + 1
    (run_dir / "discussion.json").write_text(
        json.dumps({
            "phase": "10a_3_group_discussion_dev_reuse",
            "mode": "live_founder_brief_internal_dev_reuse",
            "persona_source": "internal_dev_reuse",
            "completed_at": datetime.now(UTC).isoformat(),
            "turn_count": len(turns),
            "ballot_count_by_stage": by_stage,
            "memory_atom_count": len(atoms),
            "note": (
                "internal_dev_reuse path — passes through the linked "
                "society's discussion artifacts."
            ),
        }, indent=2, default=str), encoding="utf-8",
    )
    (run_dir / "discussion_quality.json").write_text(
        json.dumps({
            "phase": "10a_3_discussion_quality_dev_reuse",
            "mode": "live_founder_brief_internal_dev_reuse",
            "completed_at": datetime.now(UTC).isoformat(),
            "reflection_present_pct": round(
                by_stage.get("reflection", 0)
                / max(len(persona_ids), 1), 4,
            ),
            "final_ballot_present_pct": round(
                by_stage.get("final", 0)
                / max(len(persona_ids), 1), 4,
            ),
        }, indent=2, default=str), encoding="utf-8",
    )


async def _stage_repairing_incomplete_outputs(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Phase 10A.3 — final-ballot repair gate.

    Counts expected vs actual final ballots, runs a 2-step LLM
    repair ladder for missing personas, falls back to deterministic
    stance derivation if the ladder fails. Fails the run if final-
    ballot completeness remains below
    ``_FINAL_BALLOT_MIN_COMPLETENESS``.

    For dev_reuse mode, passes through (the inherited dev society
    already had its ballots produced; we just record a passthrough
    summary)."""
    is_dev_reuse = bool(ctx.get("_dev_reuse_existing_society"))
    n_personas = len(ctx["personas"])
    refl_count = sum(
        1 for b in ctx["ballots"] if b.ballot_stage == "reflection"
    )
    final_count = sum(
        1 for b in ctx["ballots"] if b.ballot_stage == "final"
    )
    if is_dev_reuse:
        summary = {
            "phase": "10a_3_reflection_repair_dev_reuse",
            "mode": "live_founder_brief_internal_dev_reuse",
            "completed_at": datetime.now(UTC).isoformat(),
            "personas_in_subset": n_personas,
            "reflections_present": refl_count,
            "final_ballots_present": final_count,
            "reflection_completeness": round(
                refl_count / max(n_personas, 1), 4,
            ),
            "final_ballot_completeness": round(
                final_count / max(n_personas, 1), 4,
            ),
            "repair_pass_run": False,
            "note": (
                "internal_dev_reuse path inherits ballots from the "
                "linked society; no fresh repair pass needed."
            ),
        }
        (run_dir / "reflection_repair.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8",
        )
        (run_dir / "final_ballot_repair.json").write_text(
            json.dumps({
                **summary,
                "phase": "10a_3_final_ballot_repair_dev_reuse",
            }, indent=2, default=str), encoding="utf-8",
        )
        return
    # Fresh-mode: run the 10A.3 final-ballot repair gate
    settings = get_settings()
    provider = None
    if settings.anthropic_api_key:
        from assembly.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider()
    # Phase 11D.13 — reuse the discussion-stage cap rule so the
    # repair phase sees the same buffer when persona blocks are
    # active. The block-presence flags were set on ctx during the
    # discussion stage; fall back to False if this is a code path
    # where the discussion stage did not run.
    cap = resolve_live_discussion_cap_usd(
        settings=settings,
        amazon_block_present=bool(
            ctx.get("amazon_persona_block_present"),
        ),
        tech_market_block_present=bool(
            ctx.get("tech_market_persona_block_present"),
        ),
        explicit_max_budget_usd=ctx.get("max_budget_usd"),
    )
    # Phase 10B.1: thread the Product Fact Card through the repair
    # prompts as well.
    fact_card_block = ctx.get("product_fact_card_block")
    if not fact_card_block:
        fc = generate_product_fact_card(ctx["brief"])
        fact_card_block = fact_card_prompt_block(fc)
        ctx["product_fact_card"] = fc
        ctx["product_fact_card_block"] = fact_card_block
    repair_audit = await repair_missing_final_ballots(
        sm=sm,
        run_scope_id=ctx["live_run_scope_id"],
        discussion_session_id=ctx["existing_session_id"],
        persona_ids=ctx["live_persona_uuids"],
        product_name=ctx["brief"]["product_name"],
        provider=provider,
        hard_cap_usd=cap,
        product_fact_card_text=fact_card_block,
    )
    repair_audit["mode"] = "live_founder_brief"
    repair_audit["persona_source"] = "fresh_retrieval_driven"
    (run_dir / "final_ballot_repair.json").write_text(
        json.dumps(repair_audit, indent=2, default=str), encoding="utf-8",
    )
    # Reflection completeness summary (recorded but no repair pass —
    # reflection failures are rare; the round-5 reflection prompt is
    # already retried inside `call_with_retry`).
    refl_summary = {
        "phase": "10a_3_reflection_repair",
        "mode": "live_founder_brief",
        "persona_source": "fresh_retrieval_driven",
        "completed_at": datetime.now(UTC).isoformat(),
        "personas_in_subset": n_personas,
        "reflections_present": refl_count,
        "reflection_completeness": round(
            refl_count / max(n_personas, 1), 4,
        ),
        "final_ballot_repair_summary": {
            "before": repair_audit.get("final_ballots_before"),
            "after": repair_audit.get("final_ballots_after"),
            "completeness_after": repair_audit.get("completeness_after"),
        },
        "note": (
            "round-5 reflection retries already happen inline via "
            "call_with_retry; this stage focuses on round-6 final "
            "ballot completeness."
        ),
    }
    (run_dir / "reflection_repair.json").write_text(
        json.dumps(refl_summary, indent=2, default=str), encoding="utf-8",
    )
    # Re-load ballots so downstream stages see the repaired finals
    async with sm() as session:
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
    ctx["ballots"] = ballots

    # Phase 10B.1 post-hoc quality audits — run AFTER the repair gate
    # so we operate on the final ballot set. These audits never raise;
    # they may rewrite ballot reasoning (caveat-leak strip + stance
    # calibration), and they always emit a JSON artifact.
    await _run_phase_10b1_audits(
        sm=sm, run_dir=run_dir, ctx=ctx,
    )

    # Hard gate: fail safely if completeness still below 95%
    completeness = float(
        repair_audit.get("completeness_after") or 0.0
    )
    if completeness < _FINAL_BALLOT_MIN_COMPLETENESS:
        raise StageError(
            "repairing_incomplete_outputs",
            f"final ballot completeness {completeness:.2%} < "
            f"{_FINAL_BALLOT_MIN_COMPLETENESS:.0%} after repair "
            f"(missing {len(repair_audit.get('missing_persona_ids_after') or [])} "
            "personas)",
            "investigate persistent LLM failures or tighten the "
            "deterministic-fallback step",
        )


async def _run_phase_10b1_audits(
    *, sm: Any, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Phase 10B.1 — run all four agent-grounding audits, persist
    the cleaned ballot reasoning + recalibrated stances, and write
    four audit JSON artifacts.

    Order matters: caveat-leak repair first (so the calibration
    operates on cleaned reasoning), then stance calibration,
    then product-grounding + diversity (audit only)."""
    # --- 1. Caveat-leak strip + audit ---
    ballots = ctx.get("ballots") or []
    ballot_payloads = [
        {
            "persona_id": b.persona_id,
            "ballot_stage": b.ballot_stage,
            "private_reasoning": b.private_reasoning or "",
            "ballot_id": b.id,
            "current_top_objection": b.top_objection,
            "current_top_proof_need": b.top_proof_need,
        }
        for b in ballots
    ]
    leak_audit = audit_ballot_caveat_leaks(ballot_payloads)
    leak_audit["completed_at"] = datetime.now(UTC).isoformat()
    leak_audit["mode"] = "live_founder_brief"
    # Apply the repair: rewrite each ballot's private_reasoning to
    # remove leaked sentences. We also clean top_objection /
    # top_proof_need fields.
    repaired_ballot_ids: list[str] = []
    if ballots:
        async with sm() as session:
            async with session.begin():
                # Re-fetch the ORM rows under this session so we can
                # mutate them safely.
                refreshed = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.discussion_session_id
                        == ctx["existing_session_id"]
                    )
                )).scalars().all()
                for b in refreshed:
                    text = b.private_reasoning or ""
                    if not detect_caveat_leak(text):
                        # Also still scan top_objection / top_proof_need
                        cleaned_obj = b.top_objection
                        cleaned_proof = b.top_proof_need
                        if cleaned_obj and detect_caveat_leak(cleaned_obj):
                            cleaned_obj, _ = strip_caveat_leak(cleaned_obj)
                            b.top_objection = cleaned_obj or None
                            repaired_ballot_ids.append(str(b.id))
                        if cleaned_proof and detect_caveat_leak(cleaned_proof):
                            cleaned_proof, _ = strip_caveat_leak(cleaned_proof)
                            b.top_proof_need = cleaned_proof or None
                            if str(b.id) not in repaired_ballot_ids:
                                repaired_ballot_ids.append(str(b.id))
                        continue
                    cleaned, _ = strip_caveat_leak(text)
                    if not cleaned:
                        cleaned = (
                            "(persona reasoning was reduced to a "
                            "system caveat; no buyer rationale remains)"
                        )
                    b.private_reasoning = cleaned
                    if b.top_objection and detect_caveat_leak(b.top_objection):
                        cleaned_obj, _ = strip_caveat_leak(b.top_objection)
                        b.top_objection = cleaned_obj or None
                    if b.top_proof_need and detect_caveat_leak(b.top_proof_need):
                        cleaned_proof, _ = strip_caveat_leak(b.top_proof_need)
                        b.top_proof_need = cleaned_proof or None
                    repaired_ballot_ids.append(str(b.id))
    leak_audit["ballots_rewritten"] = len(repaired_ballot_ids)
    leak_audit["repaired_ballot_ids"] = repaired_ballot_ids[:50]
    (run_dir / "persona_caveat_leak_quality.json").write_text(
        json.dumps(leak_audit, indent=2, default=str), encoding="utf-8",
    )

    # --- 2. Stance calibration + DB update ---
    # Re-load ballots after the leak strip so calibration operates
    # on cleaned reasoning.
    async with sm() as session:
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
    calibration_inputs = [
        {
            "persona_id": b.persona_id,
            "ballot_stage": b.ballot_stage,
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning or "",
            "ballot_id": b.id,
        }
        for b in ballots
    ]
    cal_audit = calibrate_ballots(calibration_inputs)
    cal_audit["completed_at"] = datetime.now(UTC).isoformat()
    cal_audit["mode"] = "live_founder_brief"
    # Apply corrections: update each ballot's private_stance + append
    # the stance_justification to private_reasoning so the audit
    # trail stays visible without changing the schema.
    corrections = cal_audit.get("corrections") or []
    if corrections:
        ids_by_index = [b.id for b in ballots]
        async with sm() as session:
            async with session.begin():
                refreshed = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.id.in_(
                            [ids_by_index[c["index"]] for c in corrections]
                        )
                    )
                )).scalars().all()
                by_id = {b.id: b for b in refreshed}
                for c in corrections:
                    target_id = ids_by_index[c["index"]]
                    b = by_id.get(target_id)
                    if not b:
                        continue
                    b.private_stance = c["recommended_stance"]
                    # Append a small justification marker that the
                    # frontend can hide from view but still trace.
                    if not b.private_reasoning:
                        b.private_reasoning = ""
                    if "[stance_calibration:" not in (
                        b.private_reasoning or ""
                    ):
                        b.private_reasoning = (
                            (b.private_reasoning or "") +
                            f" [stance_calibration:{c['stance_justification']}]"
                        )[:3500]
    (run_dir / "stance_calibration_quality.json").write_text(
        json.dumps(cal_audit, indent=2, default=str), encoding="utf-8",
    )

    # Re-load ballots once more so downstream cohort/intent stages
    # see calibrated stances.
    async with sm() as session:
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
    ctx["ballots"] = ballots

    # --- 3. Product grounding audit (audit-only, no DB writes) ---
    fact_card = ctx.get("product_fact_card")
    if fact_card is None:
        fact_card = generate_product_fact_card(ctx["brief"])
        ctx["product_fact_card"] = fact_card
        ctx["product_fact_card_block"] = fact_card_prompt_block(fact_card)
    turn_payloads = [
        {
            "persona_id": t.speaker_persona_id,
            "text": t.public_text or "",
        }
        for t in (ctx.get("turns") or [])
    ]
    ballot_text_payloads = [
        {
            "persona_id": b.persona_id,
            "text": b.private_reasoning or "",
        }
        for b in ballots
    ]
    grounding_audit = audit_product_grounding(
        fact_card=fact_card,
        turn_texts=turn_payloads,
        ballot_texts=ballot_text_payloads,
    )
    grounding_audit["completed_at"] = datetime.now(UTC).isoformat()
    grounding_audit["mode"] = "live_founder_brief"
    grounding_audit["location_context_used"] = bool(
        fact_card.launch_geography
    )
    grounding_audit["location_context_examples"] = (
        [fact_card.launch_geography]
        if fact_card.launch_geography
        else []
    )
    (run_dir / "product_grounding_quality.json").write_text(
        json.dumps(grounding_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 4. Discussion diversity audit (audit-only) ---
    diversity_audit = audit_discussion_diversity(
        turns=turn_payloads,
        ballots=[
            {
                "persona_id": b.persona_id,
                "private_reasoning": b.private_reasoning or "",
                "ballot_stage": b.ballot_stage,
            }
            for b in ballots
        ],
    )
    diversity_audit["completed_at"] = datetime.now(UTC).isoformat()
    diversity_audit["mode"] = "live_founder_brief"
    (run_dir / "discussion_diversity_quality.json").write_text(
        json.dumps(diversity_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 5. Phase 10B.2 — price hierarchy audit + soft repair ---
    price_audit = audit_price_hierarchy(
        fact_card=fact_card,
        turn_texts=turn_payloads,
        ballot_texts=ballot_text_payloads,
    )
    price_audit["completed_at"] = datetime.now(UTC).isoformat()
    price_audit["mode"] = "live_founder_brief"
    # Apply soft repair if confusion was found: rewrite ballot
    # reasoning + turn public_text to drop the confused sentences
    # while preserving the rest of the buyer reasoning.
    if price_audit.get("any_violations"):
        primary_value = None
        try:
            primary_value = float(
                (fact_card.primary_price or "").replace("$", "")
                .split("/")[0]
                .replace(",", "")
                .strip()
            )
        except (ValueError, AttributeError):
            primary_value = None
        accessory_values: list[float] = []
        for ap in fact_card.accessory_prices:
            try:
                accessory_values.append(
                    float(
                        ap.amount.replace("$", "").replace(",", "").strip()
                    )
                )
            except (ValueError, AttributeError):
                continue
        if primary_value is not None and accessory_values:
            repaired_ballots = 0
            repaired_turns = 0
            async with sm() as session:
                async with session.begin():
                    refreshed_ballots = (await session.execute(
                        select(DiscussionPrivateBallot).where(
                            DiscussionPrivateBallot.discussion_session_id
                            == ctx["existing_session_id"]
                        )
                    )).scalars().all()
                    for b in refreshed_ballots:
                        text = b.private_reasoning or ""
                        cleaned, removed = repair_price_confusion(
                            text, primary_value, accessory_values,
                        )
                        if removed > 0 and cleaned and cleaned != text:
                            b.private_reasoning = cleaned
                            repaired_ballots += 1
                    refreshed_turns = (await session.execute(
                        select(DiscussionTurn).where(
                            DiscussionTurn.discussion_group_id.in_(
                                [
                                    g.id for g in
                                    (await session.execute(
                                        select(DiscussionGroup).where(
                                            DiscussionGroup.discussion_session_id
                                            == ctx["existing_session_id"]
                                        )
                                    )).scalars().all()
                                ]
                            )
                        )
                    )).scalars().all()
                    for t in refreshed_turns:
                        text = t.public_text or ""
                        cleaned, removed = repair_price_confusion(
                            text, primary_value, accessory_values,
                        )
                        if removed > 0 and cleaned and cleaned != text:
                            t.public_text = cleaned
                            repaired_turns += 1
            price_audit["repaired_price_confusion_count"] = (
                repaired_ballots + repaired_turns
            )
            price_audit["unrepaired_price_confusion_count"] = max(
                0,
                price_audit["price_confusion_count"]
                - (repaired_ballots + repaired_turns),
            )
    (run_dir / "price_hierarchy_quality.json").write_text(
        json.dumps(price_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 6. Phase 10B.2 — extended provided-fact accuracy audit ---
    fact_acc_audit = audit_provided_fact_accuracy(
        fact_card=fact_card,
        turn_texts=turn_payloads,
        ballot_texts=ballot_text_payloads,
    )
    fact_acc_audit["completed_at"] = datetime.now(UTC).isoformat()
    fact_acc_audit["mode"] = "live_founder_brief"
    (run_dir / "provided_fact_accuracy_quality.json").write_text(
        json.dumps(fact_acc_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 7. Phase 10B.3 — Provided Fact Lock v2 (audit + repair) ---
    # Reload fresh ballot/turn texts since the price-repair step may
    # have rewritten them above.
    async with sm() as session:
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
        gids = [g.id for g in groups]
        turns = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(gids)
            )
        )).scalars().all()
    turn_payloads_fresh = [
        {"persona_id": t.speaker_persona_id, "text": t.public_text or ""}
        for t in turns
    ]
    ballot_text_payloads_fresh = [
        {"persona_id": b.persona_id, "text": b.private_reasoning or ""}
        for b in ballots
    ]
    fact_lock_v2_audit = audit_provided_fact_lock_v2(
        fact_card=fact_card,
        turn_texts=turn_payloads_fresh,
        ballot_texts=ballot_text_payloads_fresh,
    )
    repair_examples_collected: list[dict[str, Any]] = []
    if fact_lock_v2_audit.get("any_violations"):
        async with sm() as session:
            async with session.begin():
                refreshed_ballots = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.discussion_session_id
                        == ctx["existing_session_id"]
                    )
                )).scalars().all()
                for b in refreshed_ballots:
                    text = b.private_reasoning or ""
                    cleaned, count, examples = repair_known_fact_reask(
                        text, fact_card,
                    )
                    if count > 0 and cleaned and cleaned != text:
                        b.private_reasoning = cleaned
                        fact_lock_v2_audit["repaired_count"] += count
                        for ex in examples:
                            if len(repair_examples_collected) < 8:
                                repair_examples_collected.append(ex)
                refreshed_turns = (await session.execute(
                    select(DiscussionTurn).where(
                        DiscussionTurn.discussion_group_id.in_(gids)
                    )
                )).scalars().all()
                for t in refreshed_turns:
                    text = t.public_text or ""
                    cleaned, count, examples = repair_known_fact_reask(
                        text, fact_card,
                    )
                    if count > 0 and cleaned and cleaned != text:
                        t.public_text = cleaned
                        fact_lock_v2_audit["repaired_count"] += count
                        for ex in examples:
                            if len(repair_examples_collected) < 8:
                                repair_examples_collected.append(ex)
    fact_lock_v2_audit["repair_examples"] = repair_examples_collected
    fact_lock_v2_audit["unrepaired_count"] = max(
        0,
        fact_lock_v2_audit["known_fact_reask_count"]
        - fact_lock_v2_audit["repaired_count"],
    )
    fact_lock_v2_audit["pass"] = (
        fact_lock_v2_audit["unrepaired_count"] == 0
    )
    fact_lock_v2_audit["completed_at"] = datetime.now(UTC).isoformat()
    fact_lock_v2_audit["mode"] = "live_founder_brief"
    (run_dir / "provided_fact_lock_v2_quality.json").write_text(
        json.dumps(fact_lock_v2_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 8. Phase 10B.3 — Human-society realism audit + repair ---
    realism_audit = audit_human_society_realism(
        turn_texts=turn_payloads_fresh,
        ballot_texts=ballot_text_payloads_fresh,
    )
    if realism_audit.get("any_leak"):
        async with sm() as session:
            async with session.begin():
                refreshed_ballots = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.discussion_session_id
                        == ctx["existing_session_id"]
                    )
                )).scalars().all()
                for b in refreshed_ballots:
                    text = b.private_reasoning or ""
                    if not text:
                        continue
                    if not detect_self_awareness_leak(text):
                        continue
                    cleaned, _ = strip_self_awareness_leak(text)
                    if cleaned and cleaned != text:
                        b.private_reasoning = cleaned
                refreshed_turns = (await session.execute(
                    select(DiscussionTurn).where(
                        DiscussionTurn.discussion_group_id.in_(gids)
                    )
                )).scalars().all()
                for t in refreshed_turns:
                    text = t.public_text or ""
                    if not text:
                        continue
                    if not detect_self_awareness_leak(text):
                        continue
                    cleaned, _ = strip_self_awareness_leak(text)
                    if cleaned and cleaned != text:
                        t.public_text = cleaned
    realism_audit["completed_at"] = datetime.now(UTC).isoformat()
    realism_audit["mode"] = "live_founder_brief"
    (run_dir / "human_society_realism_quality.json").write_text(
        json.dumps(realism_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 9. Phase 10B.3 — Stricter RECEPTIVE classification ---
    async with sm() as session:
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
    strict_inputs = [
        {
            "persona_id": b.persona_id,
            "ballot_stage": b.ballot_stage,
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning or "",
            "ballot_id": b.id,
        }
        for b in ballots
    ]
    strict_audit = audit_stance_strictness(strict_inputs)
    strict_corrections = strict_audit.get("corrections") or []
    if strict_corrections:
        ids_by_index = [b.id for b in ballots]
        target_ids = [
            ids_by_index[c["index"]] for c in strict_corrections
        ]
        async with sm() as session:
            async with session.begin():
                refreshed = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.id.in_(target_ids)
                    )
                )).scalars().all()
                by_id = {b.id: b for b in refreshed}
                for c in strict_corrections:
                    target = ids_by_index[c["index"]]
                    b = by_id.get(target)
                    if not b:
                        continue
                    b.private_stance = c["recommended_stance"]
                    if "[stance_strictness:" not in (
                        b.private_reasoning or ""
                    ):
                        b.private_reasoning = (
                            (b.private_reasoning or "")
                            + f" [stance_strictness:{c['stance_justification']}]"
                        )[:3500]
    strict_audit["completed_at"] = datetime.now(UTC).isoformat()
    strict_audit["mode"] = "live_founder_brief"
    (run_dir / "stance_strictness_quality.json").write_text(
        json.dumps(strict_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # Re-load ballots once more so downstream stages (cohort, intent,
    # report) see the strictly-classified stances.
    async with sm() as session:
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
    ctx["ballots"] = ballots

    # ======================================================================
    # Phase 10B.4 — Negation-scope + input-mechanism + v3 receptive
    # ======================================================================

    # --- 10. Refresh ballot/turn texts after the v2 / v3 repairs ---
    async with sm() as session:
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
        gids = [g.id for g in groups]
        turns = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(gids)
            )
        )).scalars().all()
        ballots_b4 = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
    turn_payloads_b4 = [
        {"persona_id": t.speaker_persona_id, "text": t.public_text or ""}
        for t in turns
    ]
    ballot_payloads_b4 = [
        {"persona_id": b.persona_id, "text": b.private_reasoning or ""}
        for b in ballots_b4
    ]

    # --- 11. Negation-scope audit + repair ---
    neg_audit = audit_negation_scope(
        fact_card=fact_card,
        turn_texts=turn_payloads_b4,
        ballot_texts=ballot_payloads_b4,
    )
    neg_repair_examples: list[dict[str, Any]] = []
    if neg_audit.get("any_violations"):
        async with sm() as session:
            async with session.begin():
                refreshed_ballots = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.discussion_session_id
                        == ctx["existing_session_id"]
                    )
                )).scalars().all()
                for b in refreshed_ballots:
                    text = b.private_reasoning or ""
                    cleaned, count, examples = (
                        repair_negation_scope_inversion(text, fact_card)
                    )
                    if count > 0 and cleaned and cleaned != text:
                        b.private_reasoning = cleaned
                        neg_audit["repaired_count"] += count
                        for ex in examples:
                            if len(neg_repair_examples) < 8:
                                neg_repair_examples.append(ex)
                refreshed_turns = (await session.execute(
                    select(DiscussionTurn).where(
                        DiscussionTurn.discussion_group_id.in_(gids)
                    )
                )).scalars().all()
                for t in refreshed_turns:
                    text = t.public_text or ""
                    cleaned, count, examples = (
                        repair_negation_scope_inversion(text, fact_card)
                    )
                    if count > 0 and cleaned and cleaned != text:
                        t.public_text = cleaned
                        neg_audit["repaired_count"] += count
                        for ex in examples:
                            if len(neg_repair_examples) < 8:
                                neg_repair_examples.append(ex)
    neg_audit["examples_before_after"] = neg_repair_examples
    neg_audit["unrepaired_count"] = max(
        0,
        (
            neg_audit["camera_fact_inversion_count"]
            + neg_audit["privacy_fact_inversion_count"]
            + neg_audit["scanning_fact_inversion_count"]
        )
        - neg_audit["repaired_count"],
    )
    neg_audit["pass"] = neg_audit["unrepaired_count"] == 0
    neg_audit["completed_at"] = datetime.now(UTC).isoformat()
    neg_audit["mode"] = "live_founder_brief"
    (run_dir / "negation_scope_fact_quality.json").write_text(
        json.dumps(neg_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 12. Input-mechanism audit (read-only after repair) ---
    async with sm() as session:
        ballots_post = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
        turns_post = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(gids)
            )
        )).scalars().all()
    input_audit = audit_input_mechanism(
        fact_card=fact_card,
        turn_texts=[
            {"persona_id": t.speaker_persona_id, "text": t.public_text or ""}
            for t in turns_post
        ],
        ballot_texts=[
            {"persona_id": b.persona_id, "text": b.private_reasoning or ""}
            for b in ballots_post
        ],
    )
    input_audit["pass"] = not input_audit.get("any_violations", False)
    input_audit["completed_at"] = datetime.now(UTC).isoformat()
    input_audit["mode"] = "live_founder_brief"
    (run_dir / "input_mechanism_fact_quality.json").write_text(
        json.dumps(input_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 13. v3 receptive-strictness audit + DB update ---
    v3_inputs = [
        {
            "persona_id": b.persona_id,
            "ballot_stage": b.ballot_stage,
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning or "",
            "ballot_id": b.id,
        }
        for b in ballots_post
    ]
    v3_audit = audit_receptive_strictness_v3(v3_inputs)
    v3_corrections = v3_audit.get("corrections") or []
    if v3_corrections:
        ids_by_index = [b.id for b in ballots_post]
        target_ids = [
            ids_by_index[c["index"]] for c in v3_corrections
        ]
        async with sm() as session:
            async with session.begin():
                refreshed = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.id.in_(target_ids)
                    )
                )).scalars().all()
                by_id = {b.id: b for b in refreshed}
                for c in v3_corrections:
                    target = ids_by_index[c["index"]]
                    b = by_id.get(target)
                    if not b:
                        continue
                    b.private_stance = c["recommended_stance"]
                    if "[receptive_v3:" not in (
                        b.private_reasoning or ""
                    ):
                        b.private_reasoning = (
                            (b.private_reasoning or "")
                            + f" [receptive_v3:{c['rule_applied']}]"
                        )[:3500]
    v3_audit["completed_at"] = datetime.now(UTC).isoformat()
    v3_audit["mode"] = "live_founder_brief"
    (run_dir / "receptive_strictness_quality.json").write_text(
        json.dumps(v3_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # Re-load ballots once more so the report sees the v3
    # classifications.
    async with sm() as session:
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
    ctx["ballots"] = ballots

    # --- 13b. Phase 10B.6 — forbidden-feature audit + repair ---
    # Reload ballot/turn texts after the 10B.4 repairs so we see
    # the latest state. Then flag any positive mention of a
    # forbidden feature ("camera", "microphone", "gps", etc.)
    # the brief itself denied.
    async with sm() as session:
        ballots_b6 = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == ctx["existing_session_id"]
            )
        )).scalars().all()
        turns_b6 = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(gids)
            )
        )).scalars().all()
    forbidden_audit = audit_forbidden_features(
        fact_card=fact_card,
        turn_texts=[
            {"persona_id": t.speaker_persona_id, "text": t.public_text or ""}
            for t in turns_b6
        ],
        ballot_texts=[
            {"persona_id": b.persona_id, "text": b.private_reasoning or ""}
            for b in ballots_b6
        ],
    )
    forbidden_repair_examples: list[dict[str, Any]] = []
    if forbidden_audit.get("any_violations"):
        async with sm() as session:
            async with session.begin():
                refreshed_ballots = (await session.execute(
                    select(DiscussionPrivateBallot).where(
                        DiscussionPrivateBallot.discussion_session_id
                        == ctx["existing_session_id"]
                    )
                )).scalars().all()
                for b in refreshed_ballots:
                    text = b.private_reasoning or ""
                    cleaned, count, examples = (
                        repair_forbidden_feature_mentions(text, fact_card)
                    )
                    if count > 0 and cleaned and cleaned != text:
                        b.private_reasoning = cleaned
                        forbidden_audit["repaired_count"] += count
                        for ex in examples:
                            if len(forbidden_repair_examples) < 8:
                                forbidden_repair_examples.append(ex)
                refreshed_turns = (await session.execute(
                    select(DiscussionTurn).where(
                        DiscussionTurn.discussion_group_id.in_(gids)
                    )
                )).scalars().all()
                for t in refreshed_turns:
                    text = t.public_text or ""
                    cleaned, count, examples = (
                        repair_forbidden_feature_mentions(text, fact_card)
                    )
                    if count > 0 and cleaned and cleaned != text:
                        t.public_text = cleaned
                        forbidden_audit["repaired_count"] += count
                        for ex in examples:
                            if len(forbidden_repair_examples) < 8:
                                forbidden_repair_examples.append(ex)
    forbidden_audit["examples_before_after"] = forbidden_repair_examples
    forbidden_audit["unrepaired_count"] = max(
        0,
        forbidden_audit["positive_mention_count"]
        - forbidden_audit["repaired_count"],
    )
    forbidden_audit["pass"] = forbidden_audit["unrepaired_count"] == 0
    forbidden_audit["completed_at"] = datetime.now(UTC).isoformat()
    forbidden_audit["mode"] = "live_founder_brief"
    (run_dir / "forbidden_features_quality.json").write_text(
        json.dumps(forbidden_audit, indent=2, default=str),
        encoding="utf-8",
    )

    # --- 14. Human-speech quality (combined caveat + self-awareness) ---
    realism_audit_after = audit_human_society_realism(
        turn_texts=[
            {"persona_id": t.speaker_persona_id, "text": t.public_text or ""}
            for t in turns_post
        ],
        ballot_texts=[
            {"persona_id": b.persona_id, "text": b.private_reasoning or ""}
            for b in ballots
        ],
    )
    human_speech_audit = {
        "phase": "10b_4_human_speech",
        "self_awareness_leak_count": realism_audit_after.get(
            "self_awareness_leak_count", 0,
        ),
        "any_self_awareness_leak": realism_audit_after.get("any_leak", False),
        "human_speech_examples": realism_audit_after.get("examples", []),
        "fake_target_use_count": 0,  # already enforced by 9A.4 audit
        "pass": (
            realism_audit_after.get("self_awareness_leak_count", 0) == 0
        ),
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "live_founder_brief",
    }
    (run_dir / "human_speech_quality.json").write_text(
        json.dumps(human_speech_audit, indent=2, default=str),
        encoding="utf-8",
    )


async def _stage_building_cohorts(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Real cluster the persona subset into cohorts. Always runs (no
    LLM, deterministic)."""
    persona_dicts: list[dict[str, Any]] = []
    persona_meta: dict[str, dict[str, Any]] = {}
    persona_psychology: dict[str, dict[str, float]] = {}
    pre_by_pid: dict[uuid.UUID, DiscussionPrivateBallot] = {}
    final_by_pid: dict[uuid.UUID, DiscussionPrivateBallot] = {}
    refl_by_pid: dict[uuid.UUID, DiscussionPrivateBallot] = {}
    for b in ctx["ballots"]:
        if b.ballot_stage == "pre":
            pre_by_pid[b.persona_id] = b
        elif b.ballot_stage == "final":
            final_by_pid[b.persona_id] = b
        elif b.ballot_stage == "reflection":
            refl_by_pid[b.persona_id] = b
    turns_by_speaker: dict[uuid.UUID, list[DiscussionTurn]] = {}
    for t in ctx["turns"]:
        turns_by_speaker.setdefault(t.speaker_persona_id, []).append(t)
    atoms_by_pid: dict[uuid.UUID, list[Any]] = {}
    for a in ctx["memory_atoms"]:
        atoms_by_pid.setdefault(a.persona_id, []).append(a)
    for p in ctx["personas"]:
        psy_v = ctx["psychology_by_pid"].get(p.id, {})
        pre = pre_by_pid.get(p.id)
        final = final_by_pid.get(p.id)
        refl = refl_by_pid.get(p.id)
        persona_dicts.append({
            "persona_id": str(p.id),
            "normalized_primary_role": _parse_tag_value(
                p.product_relevance_tags or [], "normalized_primary_role",
            ) or (p.segment_label or "unknown"),
            "source_provider_family": _parse_tag_value(
                p.product_relevance_tags or [], "source_provider_family",
            ) or "unknown",
            "psychology_value_map": psy_v,
            "pre_stance": pre.private_stance if pre else None,
            "final_stance": final.private_stance if final else None,
            "public_private_delta": (
                final.public_private_delta if final else None
            ),
            "peer_reference_count": sum(
                len(t.referenced_turn_ids or [])
                for t in turns_by_speaker.get(p.id, [])
            ),
            "has_top_objection": bool(pre and pre.top_objection),
            "has_top_proof_need": bool(pre and pre.top_proof_need),
            "memory_atom_count_by_type": {},
            "reflection_present": refl is not None,
        })
        persona_meta[str(p.id)] = {
            "persona_record_id": p.id,
            "display_name": p.display_name,
            "normalized_primary_role": persona_dicts[-1][
                "normalized_primary_role"
            ],
            "final_stance": final.private_stance if final else None,
            "psychology_value_map": psy_v,
        }
        persona_psychology[str(p.id)] = psy_v
    feature_vectors, _meta = build_cohort_feature_vectors(
        personas=persona_dicts,
    )
    persona_ids_str = [p["persona_id"] for p in persona_dicts]
    cohort_persona_lists, cluster_audit = cluster_personas_into_cohorts(
        persona_ids=persona_ids_str,
        feature_vectors=feature_vectors,
        target_min_cohorts=4,
        target_max_cohorts=10,
        min_cluster_size=2,
        max_cluster_size=8,
    )
    persona_features_dict = dict(zip(persona_ids_str, feature_vectors))
    pre_dicts: dict[str, Any] = {
        str(b.persona_id): {
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "top_objection": b.top_objection,
            "top_proof_need": b.top_proof_need,
        }
        for b in pre_by_pid.values()
    }
    final_dicts: dict[str, Any] = {
        str(b.persona_id): {
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "public_private_delta": b.public_private_delta,
            "top_objection": b.top_objection,
            "top_proof_need": b.top_proof_need,
        }
        for b in final_by_pid.values()
    }
    refl_dicts: dict[str, Any] = {
        str(b.persona_id): {
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
        }
        for b in refl_by_pid.values()
    }
    turn_dicts = [
        {
            "turn_id": str(t.id),
            "speaker_persona_id": str(t.speaker_persona_id),
            "turn_type": t.turn_type,
            "public_text": t.public_text or "",
            "stance": t.stance,
            "referenced_turn_ids": [
                str(r) for r in (t.referenced_turn_ids or [])
            ],
        }
        for t in ctx["turns"]
    ]
    atom_dicts = [
        {
            "memory_atom_id": str(a.id),
            "id": str(a.id),
            "persona_id": str(a.persona_id),
            "memory_type": a.memory_type,
            "origin_excerpt": a.origin_excerpt,
            "memory_text": a.memory_text,
            "importance_score": a.importance_score,
        }
        for a in ctx["memory_atoms"]
    ]
    cohort_summaries = []
    cohort_weights = []
    cohort_repr = []
    cohort_labels = []
    for c in cohort_persona_lists:
        s = summarize_cohort(
            cohort_persona_ids=c,
            persona_meta=persona_meta,
            persona_psychology=persona_psychology,
            pre_ballots=pre_dicts, final_ballots=final_dicts,
            reflection_ballots=refl_dicts,
            discussion_turns=turn_dicts, memory_atoms=atom_dicts,
        )
        s["cohort_size"] = len(c)
        cohort_summaries.append(s)
        cohort_weights.append(len(c) / max(len(persona_ids_str), 1))
        reps = select_cohort_representatives(
            cohort_persona_ids=c,
            persona_features=persona_features_dict,
            persona_meta=persona_meta,
        )
        cohort_repr.append(reps)
        roles = s.get("role_distribution") or {}
        stances = s.get("stance_distribution") or {}
        top_role = next(iter(sorted(
            roles.items(), key=lambda kv: -kv[1],
        )), ("unknown", 0))[0]
        top_stance = next(iter(sorted(
            stances.items(), key=lambda kv: -kv[1],
        )), ("none", 0))[0]
        cohort_labels.append(f"{top_role}::{top_stance}"[:128])
    rollup = build_society_rollup(
        cohort_summaries=cohort_summaries,
        cohort_weights=cohort_weights,
        persona_count=len(persona_dicts),
    )
    ctx["cohort_persona_lists"] = cohort_persona_lists
    ctx["cohort_summaries"] = cohort_summaries
    ctx["cohort_weights"] = cohort_weights
    ctx["cohort_repr"] = cohort_repr
    ctx["cohort_labels"] = cohort_labels
    ctx["cohort_dicts"] = [
        {
            "cohort_id": f"live_cohort_{i}",
            "id": f"live_cohort_{i}",
            "cohort_label": cohort_labels[i],
            "cohort_size": len(cohort_persona_lists[i]),
            "cohort_weight": cohort_weights[i],
            "member_persona_ids": list(cohort_persona_lists[i]),
            "objection_summary": cohort_summaries[i].get("objection_summary") or {},
            "proof_need_summary": cohort_summaries[i].get("proof_need_summary") or {},
            "psychology_summary": cohort_summaries[i].get("psychology_summary") or {},
            "discussion_behavior_summary": cohort_summaries[i].get("discussion_behavior_summary") or {},
            "representatives": {
                "primary": cohort_repr[i].get("primary"),
                "primary_display_name": (
                    persona_meta.get(cohort_repr[i].get("primary") or "", {}).get("display_name")
                ),
            },
            "role_distribution": cohort_summaries[i].get("role_distribution") or {},
            "stance_distribution": cohort_summaries[i].get("stance_distribution") or {},
        }
        for i in range(len(cohort_persona_lists))
    ]
    ctx["rollup"] = rollup
    ctx["persona_dicts"] = persona_dicts
    ctx["persona_meta"] = persona_meta
    ctx["persona_psychology"] = persona_psychology
    ctx["persona_features_dict"] = persona_features_dict
    ctx["pre_dicts"] = pre_dicts
    ctx["final_dicts"] = final_dicts
    ctx["refl_dicts"] = refl_dicts
    ctx["turn_dicts"] = turn_dicts
    ctx["atom_dicts"] = atom_dicts
    summary = {
        "phase": "10a_3_cohort_architecture",
        "mode": (
            "live_founder_brief_internal_dev_reuse"
            if ctx.get("_dev_reuse_existing_society")
            else "live_founder_brief"
        ),
        "completed_at": datetime.now(UTC).isoformat(),
        "cohort_count": len(cohort_persona_lists),
        "cohort_sizes": [len(c) for c in cohort_persona_lists],
        "clustering_audit": cluster_audit,
        "every_persona_assigned_exactly_once": assignment_audit(
            persona_ids_str, cohort_persona_lists,
        ).get("every_persona_assigned_exactly_once"),
        "rollup_keys": list(rollup.keys()),
    }
    (run_dir / "cohort_architecture.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )


async def _stage_inferring_simulated_intent(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Real intent inference per persona — pure deterministic, no LLM."""
    cohort_id_by_pid: dict[str, str] = {}
    for i, cohort in enumerate(ctx["cohort_persona_lists"]):
        cohort_id = f"live_cohort_{i}"
        for pid in cohort:
            cohort_id_by_pid[pid] = cohort_id
    intent_drafts = []
    for p in ctx["personas"]:
        pid_str = str(p.id)
        psy_v = ctx["psychology_by_pid"].get(p.id, {})
        pre_b_dict = ctx["pre_dicts"].get(pid_str)
        final_b_dict = ctx["final_dicts"].get(pid_str)
        refl_b_dict = ctx["refl_dicts"].get(pid_str)
        # Reconstruct text corpus
        text_parts: list[str] = []
        for d in (pre_b_dict, final_b_dict, refl_b_dict):
            if d:
                for k in ("private_reasoning", "top_objection", "top_proof_need"):
                    if d.get(k):
                        text_parts.append(d[k])
        for t in ctx["turn_dicts"]:
            if t["speaker_persona_id"] == pid_str:
                text_parts.append(t.get("public_text") or "")
        for a in ctx["atom_dicts"]:
            if a["persona_id"] == pid_str:
                text_parts.append(a.get("memory_text") or "")
                text_parts.append(a.get("origin_excerpt") or "")
        corpus = "\n".join(filter(None, text_parts))
        normalized_role = _parse_tag_value(
            p.product_relevance_tags or [], "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        # Cohort objection summary
        cohort_idx = next(
            (i for i, c in enumerate(ctx["cohort_persona_lists"])
             if pid_str in c),
            None,
        )
        cohort_obj_summary = (
            (ctx["cohort_summaries"][cohort_idx].get("objection_summary") or {})
            .get("by_bucket") or {}
            if cohort_idx is not None else None
        )
        draft = infer_simulated_intent(
            persona_id=pid_str,
            cohort_id=cohort_id_by_pid.get(pid_str),
            normalized_role=normalized_role,
            psychology_value_map=psy_v,
            pre_ballot=pre_b_dict, final_ballot=final_b_dict,
            reflection_ballot=refl_b_dict,
            persona_text_corpus=corpus,
            ballot_ids=[], discussion_turn_ids=[], memory_atom_ids=[],
            cohort_objection_summary=cohort_obj_summary,
        )
        intent_drafts.append(draft)
    ctx["intent_drafts"] = intent_drafts
    intent_dist = Counter(d.simulated_intent for d in intent_drafts)
    # Phase 12A.10D — also emit intent_signal_distribution for
    # diagnostics (always computed by infer_simulated_intent; may be
    # None on legacy/incomplete ballots). Consumed by
    # _build_rich_distribution when routing is enabled.
    intent_signal_dist = Counter(
        d.intent_signal for d in intent_drafts if d.intent_signal
    )

    # Phase 12E — source-audience augmentation. Always runs; under
    # the default profile it's a no-op identity transform that just
    # tags personas with audience_role. Under hn_show_hn (or any
    # non-default profile), it injects synthetic non-customer voices.
    from assembly.sources.audience.augmenter import (
        augment_intent_drafts_with_source_audience,
        split_view_distributions,
    )
    brief = run.product_brief or {}
    launch_source = brief.get("launch_source") or "default"
    # Build persona metadata index (segment_label per persona) for
    # the heuristic role-assignment step.
    persona_meta_by_pid: dict[str, dict[str, Any]] = {}
    for p in ctx.get("personas") or []:
        persona_meta_by_pid[str(p.id)] = {
            "segment_label": getattr(p, "segment_label", None),
        }
    augmented_drafts, augmentation_audit = (
        augment_intent_drafts_with_source_audience(
            intent_drafts=intent_drafts,
            persona_metadata_by_pid=persona_meta_by_pid,
            launch_source=launch_source,
            run_scope_id=str(
                ctx.get("live_run_scope_id") or run.id,
            ),
        )
    )
    ctx["augmented_intent_drafts"] = augmented_drafts
    ctx["audience_augmentation_audit"] = augmentation_audit
    ctx["launch_source"] = launch_source
    # 4-view distribution split: target_market / source_audience /
    # scorable_market / noise_meta_estimate. Always computed.
    audience_views = split_view_distributions(augmented_drafts)
    ctx["audience_views"] = audience_views
    # source_audience_intent_distribution = intent labels including
    # synthetic non-customer voices (for diagnostics + voter overlay
    # consumption).
    source_audience_intent_dist = Counter(
        d.get("simulated_intent") for d in augmented_drafts
        if d.get("simulated_intent")
    )
    summary = {
        "phase": "10a_3_simulated_intent",
        "mode": (
            "live_founder_brief_internal_dev_reuse"
            if ctx.get("_dev_reuse_existing_society")
            else "live_founder_brief"
        ),
        "completed_at": datetime.now(UTC).isoformat(),
        "intent_record_count": len(intent_drafts),
        "intent_distribution": dict(intent_dist),
        "intent_signal_distribution": dict(intent_signal_dist),
        "switching_status_distribution": dict(
            Counter(d.switching_status for d in intent_drafts)
        ),
        # Phase 12E — source-audience view alongside the target-market
        # view. When launch_source=default, the source_audience and
        # target_market distributions are identical.
        "phase_12e": {
            "launch_source_used": launch_source,
            "source_audience_intent_distribution": dict(
                source_audience_intent_dist,
            ),
            "audience_views": audience_views,
            "augmentation_audit": augmentation_audit,
        },
    }
    (run_dir / "simulated_intent.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    # Phase 12C — 100-voter overlay runs as a side-effect of the
    # intent cascade stage (failure-tolerant; never mutates the
    # 24-rich pipeline output). See _run_voter_overlay_inline doc.
    _run_voter_overlay_inline(run=run, run_dir=run_dir, ctx=ctx)


def _run_voter_overlay_inline(
    *, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Phase 12C — 100-lightweight-voter market graph overlay.

    Called as a side-effect at the END of _stage_inferring_simulated_intent
    (NOT as a new pipeline stage) to avoid widening the
    `ck_assembly_runs_current_stage` CHECK constraint with a
    migration. The overlay is failure-tolerant: any error inside
    the helper is caught + a `voter_overlay_failed.json` placeholder
    is written. The existing 24-rich pipeline output is never
    mutated.
    """
    try:
        from assembly.pipeline.lightweight_voter_pipeline import (
            run_lightweight_voter_overlay,
        )
        # ctx['pre_dicts']/['final_dicts']/['refl_dicts'] are keyed by
        # persona_id but the VALUE dicts don't carry persona_id
        # inside. The voter overlay's helpers
        # (_derive_cluster_arguments_from_ctx, _build_representative_debates)
        # expect `persona_id` as a field on each ballot — without it,
        # they silently skip every ballot and produce empty
        # cluster_arguments + empty representative_debates samples.
        # Inject persona_id during the list flattening.
        def _flatten(d: dict[str, Any]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for pid, val in (d or {}).items():
                if not isinstance(val, dict):
                    continue
                merged = {"persona_id": str(pid), **val}
                out.append(merged)
            return out

        ballots_by_stage = {
            "pre": _flatten(ctx.get("pre_dicts") or {}),
            "final": _flatten(ctx.get("final_dicts") or {}),
            "refl": _flatten(ctx.get("refl_dicts") or {}),
        }
        # Phase 12C.1 — attach a per-cohort intent_distribution to each
        # cohort_dict so voter sampling can preserve the 24-rich's
        # skeptical/loyal mass. Before this, voter sampling synthesized
        # a stance and ran it through the cascade with an empty text
        # corpus, which structurally never produced loyal/reject intents.
        intent_drafts = ctx.get("intent_drafts") or []
        cohort_persona_lists = ctx.get("cohort_persona_lists") or []
        intent_by_persona: dict[str, str] = {}
        for d in intent_drafts:
            pid = getattr(d, "persona_id", None) or (
                d.get("persona_id") if isinstance(d, dict) else None
            )
            label = getattr(d, "simulated_intent", None) or (
                d.get("simulated_intent")
                if isinstance(d, dict) else None
            )
            if pid and label:
                intent_by_persona[str(pid)] = str(label)
        cohort_intent_dist_by_id: dict[str, dict[str, int]] = {}
        for i, persona_ids in enumerate(cohort_persona_lists):
            cohort_id = f"live_cohort_{i}"
            dist: dict[str, int] = {}
            for pid in persona_ids:
                label = intent_by_persona.get(str(pid))
                if label:
                    dist[label] = dist.get(label, 0) + 1
            cohort_intent_dist_by_id[cohort_id] = dist
        enriched_cohorts: list[dict[str, Any]] = []
        for c in (ctx.get("cohort_dicts") or []):
            cid = str(c.get("cohort_id") or c.get("id"))
            ec = dict(c)
            ec["intent_distribution"] = cohort_intent_dist_by_id.get(
                cid, {},
            )
            # Phase 12E — tag the existing cohorts as customer-voice
            # cohorts (target_customer_evaluator / existing_competitor_user)
            # so the voter overlay can preserve audience_role when
            # sampling. Synthetic non-customer cohorts are appended below.
            ec["audience_role_marker"] = "customer_voice"
            enriched_cohorts.append(ec)

        # Phase 12E — append synthetic non-customer cohort_dicts for
        # the source-audience voices. Each synthetic cohort carries
        # an intent_distribution that maps directly to its role's
        # default bucket; the voter overlay samples voters from it
        # with deterministic UUID5 ids. Cohorts have cohort_weight
        # proportional to the source profile.
        augmentation_audit = ctx.get(
            "audience_augmentation_audit", {},
        ) or {}
        synthetic_counts = (
            augmentation_audit.get("n_synthetic_added_by_role") or {}
        )
        n_legacy_drafts = augmentation_audit.get("n_legacy_drafts", 0)
        if synthetic_counts and n_legacy_drafts > 0:
            from assembly.sources.audience.augmenter import (
                _ROLE_DEFAULT_INTENT,
            )
            # Sum of legacy + synthetic to compute relative weights.
            total = n_legacy_drafts + sum(synthetic_counts.values())
            for role_name, count in synthetic_counts.items():
                if count <= 0:
                    continue
                intent_label = _ROLE_DEFAULT_INTENT.get(
                    role_name, "wait_and_see",
                )
                synthetic_cohort = {
                    "cohort_id": f"synthetic_{role_name}",
                    "id": f"synthetic_{role_name}",
                    "cohort_label": f"synthetic_{role_name}",
                    "cohort_size": int(count),
                    "cohort_weight": count / total,
                    "member_persona_ids": [],
                    "objection_summary": {},
                    "proof_need_summary": {},
                    "psychology_summary": {},
                    "discussion_behavior_summary": {},
                    "representatives": {},
                    "role_distribution": {
                        f"audience_role:{role_name}": int(count),
                    },
                    "stance_distribution": {
                        "curious_but_unconvinced": int(count),
                    },
                    "intent_distribution": {
                        intent_label: int(count),
                    },
                    "audience_role_marker": role_name,
                    "is_synthetic_non_customer_cohort": True,
                }
                enriched_cohorts.append(synthetic_cohort)
        summary = run_lightweight_voter_overlay(
            run_id=run.id,
            run_dir=run_dir,
            run_scope_id=ctx.get("live_run_scope_id", str(run.id)),
            cohort_dicts=enriched_cohorts,
            ballots_by_stage=ballots_by_stage,
            simulation_seed=ctx.get("simulation_seed"),
            category_hint=(
                run.product_brief or {}
            ).get("category_hint"),
            evidence_quality=1.0,
        )
        ctx["lightweight_voter_overlay_summary"] = summary
    except Exception as exc:  # noqa: BLE001 — failure-tolerant
        logger.warning(
            "phase_12c_voter_overlay_outer_error: %s: %s",
            type(exc).__name__, str(exc)[:240],
        )


async def _stage_running_society_wide_debate(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Cross-cohort argument propagation — pure deterministic."""
    arg_drafts = extract_society_arguments(
        cohorts=ctx["cohort_dicts"],
        discussion_turns=ctx["turn_dicts"],
    )
    arg_id_strings: list[tuple[str, Any]] = [
        (str(uuid.uuid4()), a) for a in arg_drafts
    ]
    prop_drafts = propagate_arguments_across_cohorts(
        arguments_with_ids=arg_id_strings,
        cohorts=ctx["cohort_dicts"],
    )
    ctx["arg_drafts"] = arg_drafts
    ctx["arg_id_strings"] = arg_id_strings
    ctx["prop_drafts"] = prop_drafts
    summary = {
        "phase": "10a_3_society_wide_debate",
        "mode": (
            "live_founder_brief_internal_dev_reuse"
            if ctx.get("_dev_reuse_existing_society")
            else "live_founder_brief"
        ),
        "completed_at": datetime.now(UTC).isoformat(),
        "argument_count": len(arg_drafts),
        "argument_type_distribution": dict(
            Counter(a.argument_type for a in arg_drafts)
        ),
        "propagation_count": len(prop_drafts),
        "response_type_distribution": dict(
            Counter(p.response_type for p in prop_drafts)
        ),
    }
    (run_dir / "society_wide_debate.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )


async def _stage_generating_report(
    *, sm: Any, run: AssemblyRun, run_dir: Path, ctx: dict[str, Any],
) -> None:
    """Render the live founder-facing report. Includes the same shape
    as the 10A fixture report so existing GET /report works."""
    intent_dicts = [d.model_dump() for d in ctx["intent_drafts"]]
    arg_dicts = [
        {**a.model_dump(), "id": aid}
        for aid, a in ctx["arg_id_strings"]
    ]
    prop_dicts = [p.model_dump() for p in ctx["prop_drafts"]]
    cohort_id_to_label = {
        f"live_cohort_{i}": ctx["cohort_labels"][i]
        for i in range(len(ctx["cohort_persona_lists"]))
    }
    cohort_id_to_size = {
        f"live_cohort_{i}": len(ctx["cohort_persona_lists"][i])
        for i in range(len(ctx["cohort_persona_lists"]))
    }
    intent_rollup = build_intent_rollup(
        intents=intent_dicts,
        cohort_id_to_label=cohort_id_to_label,
        cohort_id_to_size=cohort_id_to_size,
        cohort_count=len(ctx["cohort_persona_lists"]),
    )
    # Forbidden / sensitive sweep
    audit_texts: list[tuple[str, str]] = []
    for d in ctx["intent_drafts"]:
        audit_texts.append((f"intent:{d.persona_id}", d.evidence_basis))
        for cond in d.conditions_to_buy or []:
            audit_texts.append((f"intent_cond:{d.persona_id}", cond))
        if d.reason_for_rejection:
            audit_texts.append((f"intent_reject:{d.persona_id}", d.reason_for_rejection))
    for i, a in enumerate(ctx["arg_drafts"]):
        audit_texts.append((f"argument[{i}]", a.argument_text))
    fb_audit = forbidden_claim_audit(
        texts=audit_texts,
        product_name=run.product_brief.get("product_name", "product"),
    )
    sens_audit = sensitive_inference_audit(audit_texts)
    if fb_audit["any_fake_target_product_use"] or fb_audit["any_forecast_or_verdict"]:
        raise StageError(
            "generating_report",
            f"forbidden-claim audit failed: {fb_audit}",
            "tighten the inference rules / argument extraction lexicon",
        )
    if sens_audit["any_sensitive_inference"]:
        raise StageError(
            "generating_report",
            f"sensitive-inference audit failed: {sens_audit}",
            "review sensitive-term lexicon",
        )
    quality = evaluate_intent_and_debate_quality(
        intents=intent_dicts, arguments=arg_dicts,
        propagations=prop_dicts,
        forbidden_audit=fb_audit, sensitive_audit=sens_audit,
        expected_persona_count=len(ctx["personas"]),
        cohort_count=len(ctx["cohort_persona_lists"]),
    )
    # Build the 10A-shaped main report (matches API contract)
    public_private_pre_dist = Counter(
        b.private_stance for b in ctx["ballots"]
        if b.ballot_stage == "pre"
    )
    public_private_final_dist = Counter(
        b.private_stance for b in ctx["ballots"]
        if b.ballot_stage == "final"
    )
    cohort_report_rows = ctx["cohort_dicts"]
    is_dev_reuse_report = bool(ctx.get("_dev_reuse_existing_society"))

    # ---- Phase 10B.3: confident headline + audience copy ----
    # Compute receptive count + count of personas who shifted toward
    # receptive between pre and final. Uses the calibrated +
    # strictly-classified stances already in ctx["ballots"].
    pre_by_pid_local: dict[Any, str] = {}
    final_by_pid_local: dict[Any, str] = {}
    for b in ctx["ballots"]:
        if b.ballot_stage == "pre":
            pre_by_pid_local[b.persona_id] = b.private_stance
        elif b.ballot_stage == "final":
            final_by_pid_local[b.persona_id] = b.private_stance

    _RECEPTIVE_SET = {
        "interested_if_proven",
        "would_buy_now",
        "would_join_waitlist",
        "would_consider_if_proven",
    }
    receptive_final_count = sum(
        1 for s in final_by_pid_local.values() if s in _RECEPTIVE_SET
    )
    shifted_toward_receptive = sum(
        1
        for pid, fs in final_by_pid_local.items()
        if fs in _RECEPTIVE_SET
        and pre_by_pid_local.get(pid) not in _RECEPTIVE_SET
    )
    confident_headline = build_confident_headline(
        product_name=run.product_brief.get("product_name", "the product"),
        persona_count=len(ctx["personas"]),
        receptive_final_count=receptive_final_count,
        shifted_toward_receptive=shifted_toward_receptive,
        pre_distribution=dict(public_private_pre_dist),
        final_distribution=dict(public_private_final_dist),
    )

    # Build {persona_id: role} from cohort_dicts → personas
    role_by_pid: dict[str, str] = {}
    for p in ctx.get("personas", []):
        role = "unknown"
        for tag in (p.product_relevance_tags or []):
            if (
                isinstance(tag, str)
                and tag.startswith("normalized_primary_role:")
            ):
                role = tag.split(":", 1)[1]
                break
        role_by_pid[str(p.id)] = role

    # Phase 12C.1 — pass inferred intent labels into the role
    # distribution. Without this, voters with
    # `loyal_to_current_alternative` intent get bucketed by their
    # `private_stance` (typically `curious_but_unconvinced`) and the
    # founder report shows `resistant: 0` for every role even when
    # the simulated intent distribution carries real loyalty mass.
    intent_by_pid: dict[str, str] = {}
    intent_signal_by_pid: dict[str, str] = {}
    for d in (ctx.get("intent_drafts") or []):
        pid = getattr(d, "persona_id", None)
        label = getattr(d, "simulated_intent", None)
        signal = getattr(d, "intent_signal", None)
        if pid and label:
            intent_by_pid[str(pid)] = str(label)
        if pid and signal:
            intent_signal_by_pid[str(pid)] = str(signal)

    role_dist = role_distribution_from_ballots(
        ballots=[
            {
                "persona_id": str(b.persona_id),
                "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
            }
            for b in ctx["ballots"]
        ],
        role_by_pid=role_by_pid,
        intent_by_pid=intent_by_pid,
        intent_signal_by_pid=intent_signal_by_pid,
    )

    top_objections_for_concerns: list[dict[str, Any]] = [
        {"text": k}
        for k in (
            (ctx["rollup"].get("weighted_objection_summary") or {}).keys()
        )
    ][:8]
    top_proof_for_concerns: list[dict[str, Any]] = [
        {"text": k}
        for k in (
            (ctx["rollup"].get("weighted_proof_need_summary") or {}).keys()
        )
    ][:8]

    hardest = build_hardest_to_convince(
        role_distribution=role_dist,
        top_objections=top_objections_for_concerns,
        top_proof_needs=top_proof_for_concerns,
        target_customers=list(
            run.product_brief.get("target_customers") or []
        ),
    )
    best_fit = build_best_fit_audience(
        role_distribution=role_dist,
        target_customers=list(
            run.product_brief.get("target_customers") or []
        ),
        competitor_alternatives=list(
            run.product_brief.get("competitors_or_alternatives") or []
        ),
    )

    # Evidence-flavor (if a retrieval audit is on disk)
    evidence_flavor: dict[str, Any] = {}
    retrieval_audit_path = run_dir / "evidence_retrieval.json"
    if retrieval_audit_path.exists():
        try:
            ret_audit = json.loads(
                retrieval_audit_path.read_text(encoding="utf-8")
            )
            evidence_flavor = build_evidence_flavor(
                retrieval_audit=ret_audit,
            )
        except Exception:  # noqa: BLE001
            evidence_flavor = {}

    # Write the four 10B.3 quality artifacts now that we have data.
    (run_dir / "audience_cards_quality.json").write_text(
        json.dumps({
            "phase": "10b_3_audience_cards",
            "best_fit": best_fit,
            "hardest_to_convince": hardest,
            "role_distribution": role_dist,
            "completed_at": datetime.now(UTC).isoformat(),
            "mode": "live_founder_brief",
        }, indent=2, default=str),
        encoding="utf-8",
    )
    (run_dir / "audience_copy_quality.json").write_text(
        json.dumps({
            "phase": "10b_3_audience_copy",
            "best_fit_summary": best_fit.get("summary_copy"),
            "hardest_summary": hardest.get("summary_copy"),
            "uses_role_labels_only": False,
            "uses_target_customer_language": True,
            "completed_at": datetime.now(UTC).isoformat(),
            "mode": "live_founder_brief",
        }, indent=2, default=str),
        encoding="utf-8",
    )
    (run_dir / "headline_caveat_quality.json").write_text(
        json.dumps({
            "phase": "10b_3_headline_caveat",
            "headline": confident_headline,
            "headline_contains_caveat": False,
            "caveats_present_in_caveats_section": True,
            "completed_at": datetime.now(UTC).isoformat(),
            "mode": "live_founder_brief",
        }, indent=2, default=str),
        encoding="utf-8",
    )
    (run_dir / "evidence_flavor_quality.json").write_text(
        json.dumps({
            "phase": "10b_3_evidence_flavor",
            **evidence_flavor,
            "completed_at": datetime.now(UTC).isoformat(),
            "mode": "live_founder_brief",
        }, indent=2, default=str),
        encoding="utf-8",
    )
    # Phase 10B.4 — combined report-summary calibration audit. Captures
    # whether the headline / best-fit / hardest-to-convince blocks
    # meet the 10B.4 founder-readability bar.
    head_low = (confident_headline or "").lower()
    headline_clean = (
        "not a real-world purchase forecast" not in head_low
        and "not a real-world forecast" not in head_low
        and "validated with real prospects" not in head_low
        and "synthetic signal" not in head_low
    )
    bf_copy = (best_fit.get("summary_copy") or "")
    hard_copy = (hardest.get("summary_copy") or "")
    bf_human = bool(bf_copy) and not bf_copy.lower().startswith(
        ("trust_seeker", "competitor_user", "performance_focused")
    )
    hard_human = bool(hard_copy) and not hard_copy.lower().startswith(
        ("trust_seeker", "competitor_user", "price_skeptic")
    )
    (run_dir / "report_summary_calibration_quality.json").write_text(
        json.dumps({
            "phase": "10b_4_report_summary_calibration",
            "headline": confident_headline,
            "headline_caveat_clean": headline_clean,
            "best_fit_copy": bf_copy,
            "best_fit_human_readable": bf_human,
            "hardest_to_convince_copy": hard_copy,
            "hardest_to_convince_human_readable": hard_human,
            "report_caveats_present": True,
            "pass": headline_clean and bf_human and hard_human,
            "completed_at": datetime.now(UTC).isoformat(),
            "mode": "live_founder_brief",
        }, indent=2, default=str),
        encoding="utf-8",
    )

    # Phase 11C.4 — surface the Amazon evidence audit alongside the
    # other technical artifacts. Read-only, double-flag-gated; when
    # either flag is off this returns a uniform disabled-state dict
    # so the report shape stays consistent across runs. The audit
    # NEVER feeds personas or shapes the report's persuasion
    # narrative — it lands only under `main_report["technical"][
    # "amazon_reviews_2023"]` for operator observability.
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_evidence_section_from_dict_brief,
    )
    amazon_audit_for_report = (
        await build_amazon_evidence_section_from_dict_brief(
            run.product_brief or {},
            sessionmaker=sm,
            settings=get_settings(),
        )
    )

    # Phase 11D.9 — same shape as the Amazon audit above, but for
    # the tech_market_signal table. Audit-only: triple-flag-gated;
    # when ENABLED or RUNTIME_ENABLED is False the helper returns a
    # uniform disabled-state dict. The PERSONA_INJECTION_ENABLED
    # flag is observability-only here — it does NOT change persona
    # prompts (that wiring is reserved for a future phase).
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_evidence_section_from_dict_brief,
    )
    tech_market_audit_for_report = (
        await build_tech_market_evidence_section_from_dict_brief(
            run.product_brief or {},
            sessionmaker=sm,
            settings=get_settings(),
        )
    )

    main_report = {
        "schema_version": "10A.3.live.v1",
        "mode": (
            "live_founder_brief_internal_dev_reuse"
            if is_dev_reuse_report
            else "live_founder_brief"
        ),
        "persona_source": (
            "internal_dev_reuse" if is_dev_reuse_report
            else "fresh_retrieval_driven"
        ),
        "evidence_source": (
            "internal_dev_reuse" if is_dev_reuse_report
            else "live_retrieval"
        ),
        "run_id": str(run.id),
        "product_brief": run.product_brief,
        # Phase 10B.3 — confident headline first, *no caveats here*.
        # The caveat lives in the `caveats` section below.
        "executive_summary": [
            confident_headline,
            f"Run scope: {len(ctx['personas'])} run-scoped personas "
            f"across {len(ctx['cohort_persona_lists'])} cohorts.",
            f"Pre-discussion stance distribution: "
            f"{dict(public_private_pre_dist)}.",
            f"Final-discussion stance distribution: "
            f"{dict(public_private_final_dist)}.",
            f"Simulated intent distribution: "
            f"{intent_rollup.get('intent_distribution')}.",
        ],
        "headline": confident_headline,
        "best_fit_audience": best_fit,
        "hardest_to_convince_audience": hardest,
        "evidence_flavor": evidence_flavor,
        "synthetic_society_size": len(ctx["personas"]),
        "cohort_count": len(ctx["cohort_persona_lists"]),
        "synthetic_intent_snapshot": {
            "intent_distribution": intent_rollup.get("intent_distribution") or {},
            # Phase 12A.10D — diagnostic. Always populated; consumed by
            # the scorer only when ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED=true.
            "intent_signal_distribution": dict(Counter(
                d.intent_signal for d in ctx.get("intent_drafts") or []
                if getattr(d, "intent_signal", None)
            )),
            "switching_status_distribution": (
                intent_rollup.get("switching_status_distribution") or {}
            ),
            "high_intent_segments_count": len(
                intent_rollup.get("high_intent_segments") or []
            ),
            "rejection_segments_count": len(
                intent_rollup.get("strongest_rejection_segments") or []
            ),
        },
        # Phase 12E — source-audience 4-view split. Always present
        # (under default launch_source the views collapse to the
        # target-market view).
        "audience_breakdown": {
            "launch_source_used": ctx.get("launch_source", "default"),
            "target_market_reaction": (
                ctx.get("audience_views", {})
                .get("target_market_reaction", {})
            ),
            "source_audience_reaction": (
                ctx.get("audience_views", {})
                .get("source_audience_reaction", {})
            ),
            "scorable_market_reaction": (
                ctx.get("audience_views", {})
                .get("scorable_market_reaction", {})
            ),
            "noise_meta_estimate": (
                ctx.get("audience_views", {})
                .get("noise_meta_estimate", {"count": 0})
            ),
            "augmentation_audit": ctx.get(
                "audience_augmentation_audit", {},
            ),
            "_caveat": (
                "Source profile proportions are weak priors. Under "
                "launch_source=default, the 4 views collapse to the "
                "legacy target-market view."
            ),
        },
        # Phase 10B.3 — populate from role distribution. The
        # frontend's audience cards previously filtered out roles
        # with resistant=0, leaving the hardest-to-convince card
        # empty even when uncertain cohorts had real friction. We
        # now surface the hardest-to-convince rows here so the
        # report layer always carries the signal.
        "most_receptive_cohorts": [
            {
                "role": r["role"],
                "receptive": r["receptive"],
            }
            for r in (best_fit.get("rows") or [])
        ],
        "most_resistant_cohorts": [
            {
                "role": r["role"],
                "resistant": r.get("resistant", 0),
                "uncertain": r.get("uncertain", 0),
                "hardest_kind": hardest.get("primary_kind"),
            }
            for r in (hardest.get("rows") or [])
        ],
        "loyal_to_alternative_patterns": [
            {
                "intent": s.get("intent"),
                "cohort_label": s.get("cohort_label"),
                "strength": s.get("strength"),
            }
            for s in (intent_rollup.get("strongest_rejection_segments") or [])[:10]
        ],
        "top_objections": [
            {"bucket": k, "weighted_score": v}
            for k, v in (
                ctx["rollup"].get("weighted_objection_summary") or {}
            ).items()
        ][:8],
        "proof_needed": [
            {"bucket": k, "weighted_score": v}
            for k, v in (
                ctx["rollup"].get("weighted_proof_need_summary") or {}
            ).items()
        ][:8],
        "persuasion_levers": [],  # could be filled from arg_drafts
        "competitor_or_alternative_comparison": [],
        "society_wide_debate_summary": {
            "argument_count": len(ctx["arg_drafts"]),
            "propagation_count": len(ctx["prop_drafts"]),
            "argument_type_distribution": dict(
                Counter(a.argument_type for a in ctx["arg_drafts"])
            ),
            "response_type_distribution": dict(
                Counter(p.response_type for p in ctx["prop_drafts"])
            ),
        },
        "arguments_that_spread": [],  # post-process
        "arguments_that_were_resisted": [],
        "public_private_shift_summary": {
            "pre_stance_distribution": dict(public_private_pre_dist),
            "final_stance_distribution": dict(public_private_final_dist),
        },
        # Phase 12F.1 — Founder Trust + Explainability layer.
        # All three blocks are pure aggregation over the artifacts
        # already produced by Phases 6/9E/12C/12E. Zero new LLM calls,
        # zero DB writes. Each block's builder lives under
        # apps/api/src/assembly/explainability/ and is independently
        # testable.
        "explainability": build_explainability_panel(
            brief=run.product_brief or {}, ctx=ctx,
        ),
        "persona_reasoning_cards": build_persona_reasoning_cards(
            ctx=ctx, n=8,
        ),
        "niche_signals": build_niche_signals(
            brief=run.product_brief or {}, ctx=ctx,
        ),
        "recommended_next_tests": [
            "Validate the synthetic-intent signal against a small "
            "real-people pilot before scaling spend.",
            "The cohort with the strongest resistance signal is the "
            "contrarian hypothesis worth testing first.",
            "Build the smallest concept test that satisfies the top "
            "weighted proof bucket; iterate on real prospects.",
        ],
        "confidence_dimensions": {
            "reaction_confidence": "medium",
            "segment_confidence": "low",
            "recommendation_confidence": "medium",
            "numeric_forecast_confidence": "not_applicable",
        },
        "caveats": [
            "Live run-scoped synthetic society; not a real focus group.",
            "Cohorts are run-scoped + brief-scoped — never global market segments.",
            "Simulated intent labels are NOT real-world purchase forecasts.",
        ] + ([
            "Internal-dev-reuse mode: the persona substrate was sampled "
            "from a previously-built dev society; the deterministic "
            "stages ran against the founder brief.",
        ] if is_dev_reuse_report else [
            "Persona society was generated fresh from live retrieval "
            "for this brief — not transferable to other briefs.",
        ]),
        "evidence_traceability_summary": {
            "evidence_link_count": None,
            "memory_atom_count": len(ctx["memory_atoms"]),
            "discussion_turn_count": len(ctx["turns"]),
            "ballot_count_pre_refl_final": [
                sum(1 for b in ctx["ballots"] if b.ballot_stage == "pre"),
                sum(1 for b in ctx["ballots"] if b.ballot_stage == "reflection"),
                sum(1 for b in ctx["ballots"] if b.ballot_stage == "final"),
            ],
        },
        "artifact_links": {},
        # Phase 10B.3 — caveat lives in the trust section, NOT in
        # the headline. Frontend components reading this field
        # should render it under "Trust / caveats", not above the
        # main result statement.
        "header_caveat": (
            "Assembly results describe this run-scoped synthetic "
            "society, not guaranteed real-world sales. Use this "
            "signal alongside real customer validation."
        ),
        # Phase 11C.4 — technical/debug section. Holds operator-
        # facing observability data that must not appear in the
        # public persuasion narrative. Adding the Amazon audit here
        # keeps it clearly labeled as TECHNICAL metadata, separate
        # from the user-facing report sections above.
        "technical": {
            "amazon_reviews_2023": amazon_audit_for_report,
            # Phase 11D.9 — additive only. Audit dict; the
            # frontend report page does NOT read this key.
            "tech_market_signals": tech_market_audit_for_report,
        },
        "appendix": {
            "forbidden_claim_audit": fb_audit,
            "sensitive_inference_audit": sens_audit,
            "quality_scores": quality,
            "live_pipeline_note": (
                "Generated via Phase 10A.3 live_founder_brief "
                "orchestrator. Persona substrate was built fresh from "
                "live retrieval; the deterministic stages (cohorts, "
                "intent, propagation, report) ran on top of the fresh "
                "society."
                if not is_dev_reuse_report
                else
                "Generated via Phase 10A.3 live_founder_brief "
                "orchestrator in internal_dev_reuse mode (no live "
                "retrieval; persona substrate sampled from a "
                "previously-built dev society)."
            ),
        },
    }
    # Render markdown — use the intent-and-debate renderer with
    # synthetic shape adapter
    try:
        # We synthesize a 9E-shaped report dict for the markdown renderer
        from assembly.sources.intent_layer import render_intent_and_debate_report_json
        shaped = render_intent_and_debate_report_json(
            run_scope_id=str(run.id),
            phase="10A.3",
            product_name=run.product_brief.get("product_name", "product"),
            persona_count=len(ctx["personas"]),
            cohort_count=len(ctx["cohort_persona_lists"]),
            intents=intent_dicts,
            intent_rollup=intent_rollup,
            arguments=arg_dicts,
            propagations=prop_dicts,
            cohort_id_to_label=cohort_id_to_label,
            cohort_id_to_size=cohort_id_to_size,
            quality_scores=quality,
            forbidden_audit=fb_audit,
            sensitive_audit=sens_audit,
        )
        md = render_intent_and_debate_report_markdown(shaped)
    except Exception as e:  # noqa: BLE001
        md = (
            f"# {run.product_brief.get('product_name')} — Live Run Report\n\n"
            f"_Run ID: {run.id}_\n\n"
            f"(markdown rendering fallback: {e})"
        )
    # Phase 12F.1 — append the Trust + Explainability section. Pure
    # additive render over the explainability / persona_reasoning_cards
    # / niche_signals blocks already in `main_report`. No new LLM calls.
    try:
        md += "\n" + render_12f1_markdown_section(
            explainability=main_report.get("explainability"),
            persona_cards=main_report.get("persona_reasoning_cards"),
            niche_signals=main_report.get("niche_signals"),
        )
    except Exception as e:  # noqa: BLE001
        md += (
            f"\n\n---\n\n# Phase 12F.1 — Trust, Reasoning & Niche Signals\n\n"
            f"_(section render failed: {e}; see founder_report.json "
            "for the structured 12F.1 blocks.)_\n"
        )
    # Full Debate & Conversations — surface the 4 influence rounds, the
    # cross-cohort argument propagation, and representative cohort
    # reasoning samples directly in the downloaded report. Reads the
    # debate artifacts the pipeline already wrote to `run_dir`; makes
    # no new LLM/DB calls beyond the one-time transcript export from DB.
    # Renderer degrades gracefully on missing files.
    try:
        from assembly.orchestration.full_debate_section import (
            build_full_debate_section,
            export_discussion_transcript_if_missing,
            render_full_debate_markdown,
        )
        # Auto-export hook: dump the full 4 groups × 4 rounds × per-turn
        # transcript to disk so future report downloads include it. No-op
        # if the file already exists, the session has no groups, or any
        # error occurs — the report still renders without it.
        await export_discussion_transcript_if_missing(
            sessionmaker=sm, run_dir=run_dir,
        )
        full_debate_block = build_full_debate_section(run_dir)
        main_report["full_debate"] = full_debate_block
        md += render_full_debate_markdown(full_debate_block)
    except Exception as e:  # noqa: BLE001
        md += (
            f"\n\n---\n\n# Full Debate & Conversations\n\n"
            f"_(section render failed: {e}; see the influence_rounds.json, "
            "society_wide_debate.json, representative_debates.json, and "
            "discussion.json artifacts in this run directory for the raw "
            "transcript.)_\n"
        )
    # Persist
    (run_dir / "founder_report.json").write_text(
        json.dumps(main_report, indent=2, default=str), encoding="utf-8",
    )
    (run_dir / "founder_report.md").write_text(md, encoding="utf-8")
    (run_dir / "run_quality.json").write_text(json.dumps({
        "phase": "10a_3_run_quality",
        "mode": (
            "live_founder_brief_internal_dev_reuse"
            if is_dev_reuse_report else "live_founder_brief"
        ),
        "persona_source": (
            "internal_dev_reuse" if is_dev_reuse_report
            else "fresh_retrieval_driven"
        ),
        "completed_at": datetime.now(UTC).isoformat(),
        "quality_scores": quality,
        "forbidden_claim_audit": fb_audit,
        "sensitive_inference_audit": sens_audit,
    }, indent=2, default=str), encoding="utf-8")

    # Secret-scan
    text_blob = json.dumps(main_report, default=str) + "\n" + md
    scan = scan_for_secrets(text_blob)
    if not scan.is_clean:
        raise StageError(
            "generating_report",
            f"secret scanner flagged {len(scan.findings)} findings",
            "review report content",
        )
    # User-facing language scan (Part G of 10A.3)
    user_facing_audit = scan_user_facing_language(text_blob)
    (run_dir / "user_facing_language_audit.json").write_text(
        json.dumps(user_facing_audit, indent=2, default=str),
        encoding="utf-8",
    )
    if user_facing_audit["any_violations"]:
        raise StageError(
            "generating_report",
            f"user-facing language scan flagged "
            f"{user_facing_audit['violation_count']} violations: "
            + "; ".join(
                f["label"] for f in user_facing_audit["findings"][:5]
            ),
            "remove forecast/verdict/fake-use language from the report",
        )
    # Stale-wording scan over all artifacts (Part B). Writes its own
    # audit file and raises if any fresh-mode artifact contains stale
    # dev-reuse / fixture wording.
    wording_audit = scan_fresh_live_artifacts_for_stale_wording(
        run_dir=run_dir,
        is_dev_reuse=is_dev_reuse_report,
    )
    write_wording_audit_artifact(run_dir=run_dir, audit=wording_audit)
    if wording_audit.get("any_violations"):
        raise StageError(
            "generating_report",
            f"fresh-live artifact wording audit flagged "
            f"{wording_audit['violation_count']} violations across "
            f"{len(wording_audit['violations_by_file'])} files",
            "fix the orchestrator wording — fresh-mode artifacts "
            "must not contain stale dev-reuse / fixture wording",
        )
    ctx["report_files"] = {
        "report_json": str(run_dir / "founder_report.json"),
        "report_markdown": str(run_dir / "founder_report.md"),
        "audit_json": str(run_dir / "run_quality.json"),
        "discussion_json": str(run_dir / "discussion.json"),
        "cohorts_json": str(run_dir / "cohort_architecture.json"),
        "intent_json": str(run_dir / "simulated_intent.json"),
        "personas_json": str(run_dir / "persona_generation.json"),
        "final_ballot_repair_json": str(
            run_dir / "final_ballot_repair.json"
        ),
        "persona_quality_gates_json": str(
            run_dir / "persona_quality_gates.json"
        ),
        "fresh_live_artifact_wording_audit_json": str(
            run_dir / "fresh_live_artifact_wording_audit.json"
        ),
        # Phase 10B.1 audits
        "persona_caveat_leak_quality_json": str(
            run_dir / "persona_caveat_leak_quality.json"
        ),
        "stance_calibration_quality_json": str(
            run_dir / "stance_calibration_quality.json"
        ),
        "product_grounding_quality_json": str(
            run_dir / "product_grounding_quality.json"
        ),
        "discussion_diversity_quality_json": str(
            run_dir / "discussion_diversity_quality.json"
        ),
        # Phase 10B.2 audits
        "price_hierarchy_quality_json": str(
            run_dir / "price_hierarchy_quality.json"
        ),
        "provided_fact_accuracy_quality_json": str(
            run_dir / "provided_fact_accuracy_quality.json"
        ),
    }


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _parse_tag_value(tags: list[str], key: str, default: str = "") -> str:
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


# -----------------------------------------------------------------------
# Orchestrator
# -----------------------------------------------------------------------


_STAGE_RUNNERS = {
    "validating_brief": _stage_validate_brief,
    "planning_evidence": _stage_planning_evidence,
    "retrieving_evidence": _stage_retrieving_evidence,
    "scoring_evidence": _stage_scoring_evidence,
    "building_personas": _stage_building_personas,
    "enriching_psychology": _stage_enriching_psychology,
    "running_individual_simulation": _stage_running_individual_simulation,
    "running_group_discussion": _stage_running_group_discussion,
    "repairing_incomplete_outputs": _stage_repairing_incomplete_outputs,
    "building_cohorts": _stage_building_cohorts,
    "inferring_simulated_intent": _stage_inferring_simulated_intent,
    "running_society_wide_debate": _stage_running_society_wide_debate,
    "generating_report": _stage_generating_report,
}


class LiveFounderBriefOrchestrator:
    """Walks an `assembly_runs` row through the 13-stage pipeline.

    `reuse_existing_society=True` is the demonstrated path for 10A.1:
    it samples personas from the existing 9B society and runs the
    deterministic stages (cohorts, intent, propagation, report)
    against the founder's new brief. A fresh evidence-driven build
    will be wired in Phase 10A.2."""

    def __init__(
        self,
        *,
        run_id: uuid.UUID,
        sessionmaker: Any | None = None,
        # Phase 10A.2: default is fresh evidence-driven mode. The
        # `_dev_reuse_existing_society` knob is internal-only and is
        # never exposed via the API. It exists for tests/dev that
        # don't have retrieval keys.
        _dev_reuse_existing_society: bool = False,
        preferred_persona_count: int | None = None,
        max_budget_usd: float | None = None,
        # Phase 12A.10E: when supplied, the pipeline skips live
        # Tavily/Firecrawl retrieval AND scoring, loading both raw
        # and accepted evidence from a previously persisted snapshot.
        # Never set by the API endpoint today — set by the variance
        # harness for repeatability tests, or by an explicit
        # "reproduce previous run" UI flow in the future.
        evidence_snapshot_id: str | None = None,
        # Phase 12A.10F: when supplied, deterministic non-LLM steps
        # (group assignment, ordering, etc.) mix this integer into
        # their seed strings so re-runs with the same simulation_seed
        # produce maximally-deterministic downstream choices.
        # `None` preserves pre-12A.10F behavior (every run uses
        # run_scope_id-derived seeds, which vary per run_id).
        # Anthropic's API does NOT expose a `seed` parameter; this
        # only controls deterministic orchestration steps, not LLM
        # sampling itself. Lower temperature is the LLM-side lever.
        simulation_seed: int | None = None,
    ):
        self.run_id = run_id
        self.sm = sessionmaker or get_sessionmaker()
        self._dev_reuse_existing_society = _dev_reuse_existing_society
        self.preferred_persona_count = preferred_persona_count
        self.max_budget_usd = max_budget_usd
        self.evidence_snapshot_id = evidence_snapshot_id
        self.simulation_seed = simulation_seed

    async def run(self) -> dict[str, Any]:
        run_dir = _LIVE_RUNS_ROOT / str(self.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        ctx: dict[str, Any] = {
            "_dev_reuse_existing_society": self._dev_reuse_existing_society,
            "preferred_persona_count": (
                self.preferred_persona_count or _DEFAULT_PERSONA_COUNT
            ),
            # Phase 11D.13: leave max_budget_usd None unless the caller
            # passed one explicitly. ``resolve_live_discussion_cap_usd``
            # treats None as "compute from settings + persona-block
            # buffers"; a non-None value is honored verbatim (clamped
            # to ``cost_hard_usd``) as an operator override. The
            # informational cost-precheck below uses the equivalent
            # fallback inline so its semantics are unchanged.
            "max_budget_usd": self.max_budget_usd,
            # Phase 12A.10E: propagate snapshot id so the retrieval +
            # scoring stages know to load from a saved snapshot
            # instead of hitting live providers.
            "evidence_snapshot_id": self.evidence_snapshot_id,
            # Phase 12A.10F: propagate simulation_seed so the
            # discussion / cohort / persona-ordering stages can mix
            # it into their deterministic seed strings.
            "simulation_seed": self.simulation_seed,
        }
        # Cost pre-check budget reference (purely informational —
        # does not affect the runtime cap). Falls back to the base
        # default for the message wording when no override was passed.
        _precheck_budget = (
            self.max_budget_usd
            if self.max_budget_usd is not None
            else float(_DEFAULT_LIVE_CAP_USD)
        )
        # Cost pre-check (informational; dev_reuse mode has no LLM cost)
        cost_est = estimate_pipeline_cost(
            persona_count=ctx["preferred_persona_count"],
            report_depth="standard",
        )
        ctx["cost_estimate"] = cost_est
        if (
            not self._dev_reuse_existing_society
            and cost_est["estimated_cost_usd"] > _precheck_budget
        ):
            await _update_run(
                self.sm, self.run_id,
                status="failed",
                current_stage="planning_evidence",
                error_message=(
                    f"cost_estimate {cost_est['estimated_cost_usd']:.2f} "
                    f"exceeds max_budget_usd {_precheck_budget:.2f}"
                ),
            )
            return {"status": "failed", "reason": "cost_cap_exceeded"}
        (run_dir / "cost_estimate.json").write_text(
            json.dumps(cost_est, indent=2, default=str), encoding="utf-8",
        )
        # Phase 12A.10F: record the runtime knobs that affect
        # repeatability (simulation_seed, society_builder + discussion
        # temperatures) at the START of the run so the variance harness
        # can audit them after-the-fact and confirm all N runs in a
        # batch used the same configuration.
        #
        # Phase 12E.fix1 — `run` is loaded INSIDE the per-stage loop
        # below (line ~3718), but the runtime_config write needs to
        # know the brief's `launch_source`. Earlier 12E edit
        # referenced `run.product_brief` here, before the loop loaded
        # `run`, which raised UnboundLocalError. We now load the run
        # explicitly in its own session for this single read.
        try:
            from assembly.config import get_settings as _get_settings
            _s = _get_settings()
            # Phase 12E — record launch_source so the variance harness
            # + audit can confirm both arms used the same source profile.
            async with self.sm() as _config_session:
                _config_run = await _load_run(
                    _config_session, self.run_id,
                )
            _brief = (
                getattr(_config_run, "product_brief", None) or {}
            )
            (run_dir / "runtime_config.json").write_text(
                json.dumps({
                    "phase": "12a_10f_runtime_config",
                    "simulation_seed": self.simulation_seed,
                    "evidence_snapshot_id": self.evidence_snapshot_id,
                    "society_builder_temperature": (
                        _s.society_builder_temperature
                    ),
                    "live_discussion_temperature": (
                        _s.live_discussion_temperature
                    ),
                    "preferred_persona_count": (
                        self.preferred_persona_count
                    ),
                    "launch_source": _brief.get("launch_source") or "default",
                    "completed_at": datetime.now(UTC).isoformat(),
                }, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "runtime_config.json write failed: %s", exc,
            )
        # Initial state: status=running, all stages pending
        await _update_run(
            self.sm, self.run_id,
            status="running",
            current_stage=PIPELINE_STAGES[0],
        )
        # Walk stages
        for stage in PIPELINE_STAGES:
            await _update_run(
                self.sm, self.run_id,
                current_stage=stage,
                stage_status=(stage, "running"),
            )
            try:
                async with self.sm() as session:
                    run = await _load_run(session, self.run_id)
                runner = _STAGE_RUNNERS[stage]
                await runner(
                    sm=self.sm, run=run, run_dir=run_dir, ctx=ctx,
                )
                await _update_run(
                    self.sm, self.run_id,
                    stage_status=(stage, "complete"),
                )
            except StageError as exc:
                logger.warning(
                    "live_founder_brief.stage_failed stage=%s reason=%s",
                    exc.stage, exc.reason,
                )
                await _update_run(
                    self.sm, self.run_id,
                    status="failed",
                    current_stage=exc.stage,
                    stage_status=(exc.stage, "failed"),
                    error_message=(
                        f"[{exc.stage}] {exc.reason}"
                        + (
                            f" — recommended fix: {exc.recommended_fix}"
                            if exc.recommended_fix else ""
                        )
                    ),
                )
                return {
                    "status": "failed",
                    "failed_stage": exc.stage,
                    "reason": exc.reason,
                    "recommended_fix": exc.recommended_fix,
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "live_founder_brief.stage_unexpected_error stage=%s",
                    stage,
                )
                await _update_run(
                    self.sm, self.run_id,
                    status="failed",
                    current_stage=stage,
                    stage_status=(stage, "failed"),
                    error_message=(
                        f"[{stage}] unexpected error: "
                        f"{type(exc).__name__}: {str(exc)[:240]}"
                    ),
                )
                return {
                    "status": "failed",
                    "failed_stage": stage,
                    "reason": str(exc),
                }
        # All stages complete — finalize
        manifest = ctx.get("report_files") or {}
        # Phase 12A.10E: auto-create an evidence snapshot from this
        # run's accepted_evidence if (a) no snapshot was loaded for
        # this run and (b) accepted_evidence is non-empty. Writes the
        # snapshot envelope to _audit/evidence_snapshots/<id>.json and
        # records the snapshot id in this run's evidence_snapshot.json
        # artifact. Failures here are logged but never abort the run.
        if (
            not self.evidence_snapshot_id
            and not self._dev_reuse_existing_society
            and ctx.get("accepted_evidence")
        ):
            try:
                from assembly.calibration.evidence_snapshots import (
                    build_snapshot_from_pipeline_ctx, save_snapshot,
                )
                async with self.sm() as session:
                    run = await _load_run(session, self.run_id)
                plan = ctx.get("anchor_plan")
                plan_dict = (
                    plan.model_dump(mode="json")
                    if plan is not None and hasattr(plan, "model_dump")
                    else {}
                )
                snap = build_snapshot_from_pipeline_ctx(
                    brief=run.product_brief,
                    retrieval_audit=ctx.get("retrieval_audit") or {},
                    quality_audit={
                        "raw_count": (
                            (ctx.get("retrieval_audit") or {})
                            .get("raw_result_count", 0)
                        ),
                        "accepted_count": len(ctx["accepted_evidence"]),
                        "rejected_count": (
                            (ctx.get("retrieval_audit") or {})
                            .get("raw_result_count", 0)
                            - len(ctx["accepted_evidence"])
                        ),
                    },
                    accepted_evidence=ctx["accepted_evidence"],
                    raw_evidence=ctx.get("retrieved_items") or [],
                    anchor_plan=plan_dict,
                    simulator_version="12a_10e_v1",
                    source="live_retrieval",
                )
                save_snapshot(snap)
                (run_dir / "evidence_snapshot.json").write_text(
                    json.dumps({
                        "phase": "12a_10e_evidence_snapshot",
                        "evidence_snapshot_id": (
                            snap.evidence_snapshot_id
                        ),
                        "snapshot_hash": snap.snapshot_hash,
                        "brief_hash": snap.brief_hash,
                        "normalized_brief_hash": (
                            snap.normalized_brief_hash
                        ),
                        "auto_created": True,
                        "source": snap.source,
                        "accepted_evidence_count": (
                            snap.accepted_evidence_count
                        ),
                        "completed_at": (
                            datetime.now(UTC).isoformat()
                        ),
                    }, indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001
                # Snapshot persistence failure must NEVER fail the
                # run. Log + continue. Snapshot is auditing
                # infrastructure, not the prediction itself.
                logger.warning(
                    "live_founder_brief.snapshot_persist_failed "
                    "run_id=%s err=%s: %s",
                    self.run_id, type(exc).__name__, str(exc)[:240],
                )
        await _update_run(
            self.sm, self.run_id,
            status="complete",
            current_stage="complete",
            artifact_manifest_update=manifest,
            linked_run_scope_id=ctx.get("existing_run_scope_id"),
        )
        for artifact_type, path in manifest.items():
            try:
                await _add_artifact(
                    self.sm, self.run_id, artifact_type, path,
                    content_type=(
                        "text/markdown" if artifact_type == "report_markdown"
                        else "application/json"
                    ),
                )
            except Exception:  # noqa: BLE001
                # The unique constraint may already have caught a re-run
                pass
        return {"status": "complete", "artifact_manifest": manifest}


async def run_live_founder_brief_pipeline(
    run_id: uuid.UUID,
    *,
    _dev_reuse_existing_society: bool = False,
    preferred_persona_count: int | None = None,
    max_budget_usd: float | None = None,
    evidence_snapshot_id: str | None = None,
    simulation_seed: int | None = None,
) -> dict[str, Any]:
    """Top-level entry — used by the API background-task scheduler.

    `_dev_reuse_existing_society` is internal-only; the API endpoint
    never sets it for normal live runs.

    `evidence_snapshot_id` (Phase 12A.10E): when supplied, skips live
    evidence retrieval and scoring; loads both from the named
    snapshot. Default None (live retrieval, auto-create snapshot).

    `simulation_seed` (Phase 12A.10F): when supplied, mixes into
    deterministic non-LLM seed strings (group assignment, etc.) so
    re-runs with the same seed produce identical orchestration
    decisions. Does NOT control LLM sampling — Anthropic's API has
    no seed parameter. Default None (per-run_scope_id seeding)."""
    o = LiveFounderBriefOrchestrator(
        run_id=run_id,
        _dev_reuse_existing_society=_dev_reuse_existing_society,
        preferred_persona_count=preferred_persona_count,
        max_budget_usd=max_budget_usd,
        evidence_snapshot_id=evidence_snapshot_id,
        simulation_seed=simulation_seed,
    )
    return await o.run()
