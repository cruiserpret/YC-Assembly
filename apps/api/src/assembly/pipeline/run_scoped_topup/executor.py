"""Phase 8.2I — top-level run-scoped top-up loop orchestrator.

Two paths:

  * `execute_topup_loop_dry_run`  —  pure planning. Builds the
        target-society plan, loads existing personas, runs Phase
        8.2H audience retrieval, builds the run-scoped top-up plan,
        and returns a `RunScopedTopUpLoopResult` with `dry_run=True`,
        `ingestion=None`, `persona_write=None`, `reaudit=None`. NO
        Tavily call. NO persona writes.

  * `execute_topup_loop_live`     —  full live loop:
        1. dry-run steps to build the plan
        2. flip Tavily compliance status to `approved` for the run
        3. run targeted Tavily ingest with the plan's queries
        4. flip Tavily status back to `review`
        5. classify new source_records → strong/weak/context
        6. run persona construction (write_personas=True) on
           strong-signal shells only, capped at the plan's
           persona_write_cap and cost_cap_usd
        7. re-run Phase 8.2H audience retrieval
        8. build the before/after re-audit
        9. return a fully-populated `RunScopedTopUpLoopResult`
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.persona import (
    PersonaEvidenceLink,
    PersonaRecord,
    PersonaTrait,
    SourceRecord,
)
from assembly.pipeline.audience_retrieval import (
    retrieve_personas_for_target_society,
)
from assembly.pipeline.audience_retrieval.schemas import (
    RunScopedAudienceRetrievalResult,
)
from assembly.pipeline.ingestion import (
    ComplianceError,
    TavilySearchExtractAdapter,
    register_or_update_adapter_status,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.run_scoped_topup.ingestion_plan import (
    build_topup_plan_from_audience_retrieval,
    flatten_plan_to_query_to_category_map,
)
from assembly.pipeline.run_scoped_topup.persona_write import (
    execute_persona_write_for_topup,
)
from assembly.pipeline.run_scoped_topup.reaudit import compare_before_after
from assembly.pipeline.run_scoped_topup.schemas import (
    RunScopedTopUpLoopResult,
    RunScopedTopUpPlan,
    TopUpExecutionResult,
)
from assembly.pipeline.run_scoped_topup.summary import (
    render_run_scoped_topup_summary,
)
from assembly.pipeline.target_society import (
    ProductBriefInput,
    build_target_society_plan,
)


RUN_PURPOSE = "phase_8_2i_run_scoped_topup"


# ---------------------------------------------------------------------------
# Helpers — DB I/O for loading personas + their domain context.
# ---------------------------------------------------------------------------


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


async def _load_audience_inputs(
    sessionmaker: async_sessionmaker,
) -> tuple[list[PersonaAuditInput], dict[UUID, str]]:
    """Load every PersonaRecord + traits + evidence_links + source
    domain into in-memory views the audience-retrieval scorer needs."""
    async with sessionmaker() as session:
        all_personas = (await session.execute(
            select(PersonaRecord)
        )).scalars().all()
        all_traits = (await session.execute(
            select(PersonaTrait)
        )).scalars().all()
        all_links = (await session.execute(
            select(PersonaEvidenceLink)
        )).scalars().all()
        sources = (await session.execute(
            select(SourceRecord.id, SourceRecord.source_url, SourceRecord.metadata_)
        )).all()

    domain_map = {sid: _domain_of(url) for sid, url, _ in sources}
    likely_signal_by_id = {
        sid: (
            None if (md or {}).get("likely_human_signal_candidate") is None
            else bool((md or {})["likely_human_signal_candidate"])
        )
        for sid, _, md in sources
    }

    traits_by_persona: dict = {}
    for t in all_traits:
        traits_by_persona.setdefault(t.persona_id, []).append(t)
    links_by_persona: dict = {}
    for l in all_links:
        links_by_persona.setdefault(l.persona_id, []).append(l)

    audit_inputs: list[PersonaAuditInput] = []
    for p in all_personas:
        ts = traits_by_persona.get(p.id, [])
        ls = links_by_persona.get(p.id, [])
        audit_inputs.append(PersonaAuditInput(
            persona_id=p.id,
            display_name=p.display_name,
            traits=tuple(
                TraitView(
                    field_name=t.field_name,
                    support_level=t.support_level,
                    value=t.value,
                    confidence=float(t.confidence),
                    source_ids=tuple(t.source_ids or ()),
                    rationale=t.rationale,
                )
                for t in ts
            ),
            evidence_links=tuple(
                EvidenceLinkView(
                    persona_id=l.persona_id,
                    source_record_id=l.source_record_id,
                    contribution_kind=l.contribution_kind,
                    contribution_field=l.contribution_field,
                    excerpt=l.excerpt or "",
                    source_likely_human_signal=likely_signal_by_id.get(
                        l.source_record_id
                    ),
                )
                for l in ls
            ),
        ))
    return audit_inputs, domain_map


# ---------------------------------------------------------------------------
# Dry-run path
# ---------------------------------------------------------------------------


async def execute_topup_loop_dry_run(
    *,
    sessionmaker: async_sessionmaker,
    brief: ProductBriefInput,
    brief_label: str,
    approve_sensitive_topup: bool = False,
    plan_overrides: dict | None = None,
    topup_plan_override: RunScopedTopUpPlan | None = None,
) -> RunScopedTopUpLoopResult:
    """Build the plan; do NOT touch Tavily; do NOT write personas.

    Phase 8.2I.1 — `topup_plan_override` lets an operator skip the
    audience-retrieval-driven query picker and supply a refined
    catalog (e.g. `build_amboras_refined_topup_plan()` output).
    Audience retrieval still runs so the live re-audit captures the
    actual coverage delta later.
    """
    target_plan = build_target_society_plan(brief)
    audience_inputs, domain_map = await _load_audience_inputs(sessionmaker)
    audience_before = retrieve_personas_for_target_society(
        brief=brief,
        plan=target_plan,
        personas=audience_inputs,
        domain_by_record_id=domain_map,
    )
    if topup_plan_override is not None:
        topup_plan = topup_plan_override
    else:
        overrides = plan_overrides or {}
        topup_plan = build_topup_plan_from_audience_retrieval(
            brief_label=brief_label,
            audience_result=audience_before,
            approve_sensitive_topup=approve_sensitive_topup,
            **overrides,
        )

    safety = [
        "dry_run wrote 0 source_records",
        "dry_run wrote 0 personas / traits / evidence_links",
        "no Tavily live call issued",
        "no graph / cluster / simulation / UI write",
    ]

    result = RunScopedTopUpLoopResult(
        brief_label=brief_label,
        plan=topup_plan,
        dry_run=True,
        ingestion=None,
        persona_write=None,
        reaudit=None,
        summary_text="",
        safety_assertions=safety,
    )
    result = result.model_copy(
        update={"summary_text": render_run_scoped_topup_summary(result)}
    )
    return result


# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------


class TopUpReadinessAlreadySufficient(Exception):
    """Raised when the executor refuses live mode because the audience
    is already simulation-ready and a top-up would be a noop."""


class TopUpComplianceCaveatUnresolved(Exception):
    """Raised when live mode is requested but the plan flags
    `requires_compliance_approval=True` and the operator did not pass
    `approve_sensitive_topup=True`."""


async def execute_topup_loop_live(
    *,
    sessionmaker: async_sessionmaker,
    brief: ProductBriefInput,
    brief_label: str,
    approver_label: str,
    approve_sensitive_topup: bool = False,
    plan_overrides: dict | None = None,
    refuse_if_already_ready: bool = True,
    topup_plan_override: RunScopedTopUpPlan | None = None,
) -> RunScopedTopUpLoopResult:
    """Full live loop. Operator-only.

    Phase 8.2I.1 — `topup_plan_override` lets an operator supply a
    refined catalog (e.g. `build_amboras_refined_topup_plan()`)
    directly, bypassing the audience-retrieval-driven query picker.
    The refinement label propagates into every accepted Tavily row's
    metadata so post-run audits can attribute coverage to the
    refinement pass.

    * Refuses if `audience_before.readiness_by_mode.tiny_ready` is
      already True AND `refuse_if_already_ready` is True (no-op
      protection).
    * Refuses if the plan needs compliance approval that wasn't
      explicitly provided.
    * Flips Tavily compliance to approved → ingests → flips back to
      review (mirrors Phase 8.2E / 8.2F.5 / 8.2F.6 hygiene).
    """
    target_plan = build_target_society_plan(brief)
    audience_inputs_before, domain_map_before = await _load_audience_inputs(
        sessionmaker,
    )
    audience_before = retrieve_personas_for_target_society(
        brief=brief,
        plan=target_plan,
        personas=audience_inputs_before,
        domain_by_record_id=domain_map_before,
    )

    if refuse_if_already_ready and audience_before.readiness_by_mode.tiny_ready:
        raise TopUpReadinessAlreadySufficient(
            f"audience for brief={brief_label} is already tiny-ready; "
            "top-up would be a no-op. Override with "
            "refuse_if_already_ready=False."
        )

    if topup_plan_override is not None:
        topup_plan = topup_plan_override
    else:
        overrides = plan_overrides or {}
        topup_plan = build_topup_plan_from_audience_retrieval(
            brief_label=brief_label,
            audience_result=audience_before,
            approve_sensitive_topup=approve_sensitive_topup,
            **overrides,
        )
    if topup_plan.requires_compliance_approval and not approve_sensitive_topup:
        raise TopUpComplianceCaveatUnresolved(
            f"plan for brief={brief_label} requires compliance approval; "
            "pass approve_sensitive_topup=True to proceed."
        )

    # ---- Stage 1: Tavily ingest -------------------------------------
    adapter = TavilySearchExtractAdapter(
        queries=list(_flatten_queries(topup_plan)),
        query_to_category=flatten_plan_to_query_to_category_map(topup_plan),
        run_purpose=RUN_PURPOSE,
        operator_run=True,
        test_fixture=False,
        target_brief=brief_label,
        query_refinement_version=topup_plan.query_refinement_version,
        max_queries=topup_plan.max_total_queries,
        max_results_per_query=topup_plan.max_results_per_query,
        max_accepted=topup_plan.max_accepted_records,
    )
    # Flip status to approved.
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="approved",
        memo_path=adapter.MEMO_PATH,
        approver=approver_label,
        approved_at=datetime.now(UTC),
        notes=f"Phase 8.2I run-scoped top-up for brief={brief_label}.",
    )
    started = time.monotonic()
    pre_existing_ids = await _existing_tavily_ids(sessionmaker)
    try:
        ingest_summary = await adapter.ingest_live(
            sessionmaker=sessionmaker,
            salt=f"phase_8_2i_topup_{brief_label}",
            accepted_cap=topup_plan.max_accepted_records,
        )
    finally:
        # Always re-flip back to review even on failure.
        await register_or_update_adapter_status(
            sessionmaker,
            adapter_name=adapter.NAME,
            status="review",
            memo_path=adapter.MEMO_PATH,
            approver=None,
            approved_at=None,
            notes=(
                f"Phase 8.2I run-scoped top-up for brief={brief_label} "
                "completed; status reverted to review."
            ),
        )
    runtime = time.monotonic() - started

    # Look up newly-inserted source_record IDs by computing the delta.
    post_ids = await _existing_tavily_ids(sessionmaker)
    new_ids = sorted(post_ids - pre_existing_ids)

    # Per-category accepted counts + domain breakdown.
    async with sessionmaker() as session:
        new_rows = (await session.execute(
            select(SourceRecord).where(SourceRecord.id.in_(new_ids))
        )).scalars().all()
    accepted_by_category: Counter[str] = Counter()
    accepted_domains: Counter[str] = Counter()
    for r in new_rows:
        cat = (r.metadata_ or {}).get("target_missing_category") or "<unknown>"
        accepted_by_category[cat] += 1
        accepted_domains[_domain_of(r.source_url)] += 1
    rejected_codes: Counter[str] = Counter()
    for rej in ingest_summary.rejection_reasons:
        rejected_codes[rej.reason_code] += 1

    ingest_result = TopUpExecutionResult(
        fetched_count=ingest_summary.fetched_count,
        accepted_count=ingest_summary.accepted_count,
        rejected_count=ingest_summary.rejected_count,
        deduped_count=ingest_summary.deduped_count,
        accepted_by_category=dict(accepted_by_category),
        new_source_record_ids=[str(rid) for rid in new_ids],
        rejected_reason_codes=dict(rejected_codes),
        accepted_source_domains=dict(accepted_domains),
        runtime_seconds=runtime,
        live_network_used=ingest_summary.live_network_used,
    )

    # ---- Stage 2: persona-write on strong shells --------------------
    persona_write_result = await execute_persona_write_for_topup(
        sessionmaker=sessionmaker,
        plan=topup_plan,
        new_source_record_ids=new_ids,
    )

    # ---- Stage 3: re-audit ------------------------------------------
    audience_inputs_after, domain_map_after = await _load_audience_inputs(
        sessionmaker,
    )
    audience_after = retrieve_personas_for_target_society(
        brief=brief,
        plan=target_plan,
        personas=audience_inputs_after,
        domain_by_record_id=domain_map_after,
    )
    reaudit = compare_before_after(before=audience_before, after=audience_after)

    safety = [
        f"Tavily compliance status reverted to 'review' "
        f"(approver=None, approved_at=None)",
        f"new source_records inserted: {ingest_result.accepted_count}",
        f"new personas written: {persona_write_result.personas_created}",
        "no graph / cluster / simulation / UI write performed",
        "no non-Tavily live API was called",
    ]

    result = RunScopedTopUpLoopResult(
        brief_label=brief_label,
        plan=topup_plan,
        dry_run=False,
        ingestion=ingest_result,
        persona_write=persona_write_result,
        reaudit=reaudit,
        summary_text="",
        safety_assertions=safety,
    )
    return result.model_copy(
        update={"summary_text": render_run_scoped_topup_summary(result)},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _existing_tavily_ids(
    sessionmaker: async_sessionmaker,
) -> set[UUID]:
    async with sessionmaker() as session:
        return set(
            (await session.execute(
                select(SourceRecord.id).where(
                    SourceRecord.source_kind == "tavily_search_extract"
                )
            )).scalars().all()
        )


def _flatten_queries(plan: RunScopedTopUpPlan) -> list[str]:
    out: list[str] = []
    for cat, qs in plan.queries_by_category.items():
        out.extend(qs)
    return out
