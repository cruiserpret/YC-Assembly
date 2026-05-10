"""Phase 8.4A — Triton Drinks unlaunched-product society-build test.

Operator-only multi-stage live run. Builds a fresh Triton Drinks
target society plan, runs Tavily discovery + Firecrawl extraction on
energy-drink / college-caffeine / gym / competitor evidence, runs
persona construction on the new source_records, and runs the existing
relevance audit (audience retrieval) against the Triton plan.

CRITICAL discipline (per Phase 8.4A spec):

  * NO fake Triton customers. NO Triton loyalists. NO direct-Triton
    evidence invented. Triton is unlaunched.
  * Relevance = evidence-backed buyers / rejectors / influencers in
    the energy / sports / caffeine market that Triton would enter.
  * Personas are built from STRONG human-signal evidence only
    (Phase 8.2F classifier). Weak / context-only signals are not
    promoted into personas.
  * Every persona must have ≥3 source-bound traits + a
    `persona_evidence_link` for each one (Phase 8.2A invariant).
  * Source coverage: Tavily (discovery) + Firecrawl (extraction).
    No Brave / Reddit-API / YouTube / Product Hunt / SerpAPI.
  * No graph / cluster / simulation / UI write — drift-tested.

Caps (mirror Phase 8.4A spec):
  MAX_TAVILY_QUERIES         30
  MAX_TAVILY_RESULTS_PER_Q   5
  MAX_TAVILY_ACCEPTED        100
  MAX_FIRECRAWL_URLS         20
  MAX_PERSONAS               50
  LLM_COST_HARD_USD         5.00
  SOURCE_API_COST_HARD_USD  2.00 (advisory; live counters log to
                                  llm_call_log + Tavily/Firecrawl
                                  request count)

In `finally:` (always, even on crash):
  * Tavily compliance flipped back to `'review'`.
  * Firecrawl compliance flipped back to `'review'`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

MAX_TAVILY_QUERIES = 30
MAX_TAVILY_RESULTS_PER_Q = 5
MAX_TAVILY_ACCEPTED = 100
MAX_FIRECRAWL_URLS = 20
MAX_PERSONAS = 50
LLM_COST_HARD_USD = Decimal("5.00")
TARGET_BRIEF_TAG = "triton_drinks"
APPROVER_LABEL = "phase_8_4a_triton_society_build"
HANDLE_SALT = "phase_8_4a_triton_smoke_salt"


# ---------------------------------------------------------------------------
# Triton Tavily query catalog (30 queries, energy-drink + college +
# gym + competitor + California-specific). Every query is scoped to
# energy-drink-category evidence — not Triton-specific (Triton is
# unlaunched so no Triton-specific evidence exists).
# ---------------------------------------------------------------------------


TRITON_QUERIES: dict[str, str] = {
    # Red Bull buyer / loyalist / rejector evidence (4)
    "Red Bull energy drink heavy user college Reddit": "current_alternative_red_bull",
    "Red Bull addiction college student forum": "current_alternative_red_bull",
    "Red Bull too expensive switching alternative review": "current_alternative_red_bull",
    "Red Bull gym workout pre training Reddit": "current_alternative_red_bull",
    # Monster buyer / heavy-user evidence (3)
    "Monster energy drink college dorm Reddit": "current_alternative_monster",
    "Monster energy drink heavy user complaints": "current_alternative_monster",
    "Monster energy drink ingredients health concerns": "current_alternative_monster",
    # Celsius / Prime / Gatorade buyer evidence (4)
    "Celsius energy drink review honest Reddit": "current_alternative_celsius",
    "Celsius vs Red Bull better which one": "current_alternative_celsius",
    "Prime energy drink hype overrated review": "current_alternative_celsius",
    "Gatorade pre workout energy drink comparison": "fitness_lifestyle_buyer",
    # College caffeine / studying evidence (3)
    "college student caffeine for studying late night forum": "mass_market_grocery_buyer",
    "college student energy drink consumption forum": "mass_market_grocery_buyer",
    "best caffeine for finals week college Reddit": "mass_market_grocery_buyer",
    # Gym / pre-workout / athlete evidence (4)
    "pre workout vs energy drink gym goers Reddit": "fitness_lifestyle_buyer",
    "athlete energy drink pre training caffeine": "fitness_lifestyle_buyer",
    "gym goer energy drink before workout review": "fitness_lifestyle_buyer",
    "pre workout drink reviews athletic performance": "fitness_lifestyle_buyer",
    # Health / sugar / crash skepticism (3)
    "energy drink crash sugar complaint forum": "skeptical_rejector",
    "energy drink ingredients health concern Reddit": "skeptical_rejector",
    "low sugar energy drink alternative review": "sustainability_conscious_buyer",
    # Price / value (3)
    "energy drink price too expensive convenience store": "price_sensitive_buyer",
    "$3.99 energy drink worth it review": "price_sensitive_buyer",
    "cheaper alternative to Red Bull Monster energy drink": "price_sensitive_buyer",
    # Premium / brand-story (2)
    "premium energy drink brand worth paying more": "premium_buyer",
    "energy drink brand loyalty review review": "premium_buyer",
    # Substitute occasions (2)
    "cold brew vs energy drink for studying caffeine": "skeptical_rejector",
    "electrolyte drink athlete vs energy drink": "fitness_lifestyle_buyer",
    # California-specific (2)
    "California energy drink convenience store impulse": "geography_california_united_states",
    "California college student energy drink choice": "geography_california_united_states",
}


async def _amain() -> int:
    _load_env()
    if not os.environ.get("TAVILY_API_KEY"):
        print("ERROR: TAVILY_API_KEY not set after .env load. Aborting.")
        return 2
    if not os.environ.get("FIRECRAWL_API_KEY"):
        print("ERROR: FIRECRAWL_API_KEY not set after .env load. Aborting.")
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set after .env load. Aborting.")
        return 2

    from sqlalchemy import func, select

    from assembly.db import get_sessionmaker
    from assembly.models import (
        Agent,
        AgentEdge,
        AgentResponse,
        DebateTurn,
        PersonaCluster,
        PersonaClusterMembership,
        PersonaEvidenceLink,
        PersonaGraphEdge,
        PersonaOpinion,
        PersonaRecord,
        PersonaTrait,
        SimulationOutput,
        SimulationRound,
        SourceRecord,
    )
    from assembly.pipeline.audience_retrieval import (
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.ingestion.compliance import (
        register_or_update_adapter_status,
    )
    from assembly.pipeline.ingestion.firecrawl import (
        FIRECRAWL_ADAPTER_NAME,
        FIRECRAWL_MEMO_PATH,
        FirecrawlBlockedPage,
        FirecrawlBodyRedactionFailed,
        FirecrawlBodyTooShort,
        FirecrawlBoilerplateDominated,
        FirecrawlBotProtectionPlaceholder,
        FirecrawlClient,
        FirecrawlError,
    )
    from assembly.pipeline.ingestion.redaction import (
        prepare_source_record_insert,
    )
    from assembly.pipeline.ingestion.run_summary import NormalizedSourcePayload
    from assembly.pipeline.ingestion.tavily_adapter import (
        TavilySearchExtractAdapter,
    )
    from assembly.pipeline.persona_construction import (
        LLMTraitExtractor,
        run_persona_construction,
    )
    from assembly.pipeline.persona_relevance.auditor import (
        EvidenceLinkView,
        PersonaAuditInput,
        TraitView,
    )
    from assembly.pipeline.persona_relevance.rubric import (
        RelevanceClassification,
    )
    from assembly.pipeline.target_society import (
        build_target_society_plan,
    )
    from assembly.pipeline.target_society.constants import SimulationGoal
    from assembly.pipeline.target_society.schemas import ProductBriefInput

    sm = get_sessionmaker()
    print("=" * 70)
    print("Phase 8.4A — Triton Drinks SOCIETY-BUILD TEST (operator-only)")
    print("=" * 70)
    print("policy: Tavily + Firecrawl only. No Brave/Reddit/YouTube/PH/SerpAPI.")
    print("API keys: detected (values not printed).")

    # ---- Stage 0: build brief + plan ----------------------------------
    triton_brief = ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is a new caffeinated sports/energy drink launching "
            "in California at $3.99 per can. Targeted at college students, "
            "athletes, gym-goers, and busy young adults who use energy drinks "
            "or caffeine for studying, workouts, alertness, or performance. "
            "Competes with Red Bull and Monster on the energy drink shelf, and "
            "overlaps with Celsius, Prime, Gatorade, pre-workout drinks, cold "
            "brew, and electrolyte drinks for share-of-occasion. Triton is "
            "unlaunched; relevance means evidence-backed buyers / rejectors / "
            "influencers in the category, not Triton-specific buyers."
        ),
        price_or_price_structure="$3.99 per can (single-serve)",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        target_market_or_society=(
            "California consumers in the energy / sports / functional-beverage "
            "occasion: college students, athletes, gym-goers, busy young "
            "adults; caffeine-for-study and pre-workout users; convenience-"
            "store impulse buyers; price-sensitive shoppers and premium-buyers "
            "alike."
        ),
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        extra_context=(
            "Substitutes considered in scope: cold brew, coffee, pre-workout "
            "powders, electrolyte drinks. The brief explicitly does NOT define "
            "relevance as Triton-specific buyers; relevance means evidence-"
            "backed energy / sports / caffeine buyers in the California market."
        ),
        simulation_goal=SimulationGoal.TEST_PRICE,
    )
    plan = build_target_society_plan(triton_brief)
    print(
        f"\nplan: family={plan.interpreted_brief.detected_product_family.value}, "
        f"{len(plan.stakeholder_categories)} categories, "
        f"{len(plan.warnings_and_limitations)} warnings"
    )
    for c in plan.stakeholder_categories:
        print(f"  [{c.priority}] {c.category_key}")

    # ---- Snapshot forbidden tables BEFORE -----------------------------
    forbidden_models = [
        SimulationOutput, SimulationRound,
        PersonaGraphEdge, PersonaCluster, PersonaClusterMembership,
        PersonaOpinion,
        Agent, AgentResponse, DebateTurn, AgentEdge,
    ]
    async with sm() as session:
        before = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }
        before["SourceRecord"] = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        before["PersonaRecord"] = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        before["PersonaTrait"] = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        before["PersonaEvidenceLink"] = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
    print(
        f"\nrow counts BEFORE: SR={before['SourceRecord']}, "
        f"PR={before['PersonaRecord']}, PT={before['PersonaTrait']}, "
        f"PEL={before['PersonaEvidenceLink']}"
    )

    accepted_tavily_count = 0
    rejected_tavily_count = 0
    deduped_tavily_count = 0
    firecrawl_accepted: list[dict] = []
    firecrawl_rejections: list[dict] = []
    persona_construction_summary = None
    audience_result = None
    error_in_run: str | None = None

    started = time.monotonic()
    try:
        # ============================================================
        # Stage 1: Tavily discovery
        # ============================================================
        print("\n" + "=" * 70)
        print("Stage 1: Tavily live discovery (Triton-tagged queries)")
        print("=" * 70)
        await register_or_update_adapter_status(
            sm,
            adapter_name="tavily_search_extract",
            status="approved",
            memo_path="apps/api/docs/compliance/tavily_search_extract.md",
            approver=APPROVER_LABEL,
            approved_at=datetime.now(UTC),
            notes=(
                "phase 8.4A Triton society build — Tavily discovery for "
                "energy-drink / college / gym / competitor evidence; "
                "not production approval"
            ),
        )

        # Build a Tavily adapter that uses Triton query catalog,
        # tags every accepted row with target_brief='triton_drinks'.
        adapter = TavilySearchExtractAdapter(
            queries=list(TRITON_QUERIES.keys()),
            run_purpose=APPROVER_LABEL,
            operator_run=True,
            test_fixture=False,
            max_queries=MAX_TAVILY_QUERIES,
            max_results_per_query=MAX_TAVILY_RESULTS_PER_Q,
            max_accepted=MAX_TAVILY_ACCEPTED,
            query_to_category=dict(TRITON_QUERIES),
            target_brief=TARGET_BRIEF_TAG,
            query_refinement_version="8.4A",
        )
        tavily_summary = await adapter.ingest_live(
            sessionmaker=sm,
            salt=HANDLE_SALT,
            accepted_cap=MAX_TAVILY_ACCEPTED,
        )
        accepted_tavily_count = tavily_summary.accepted_count
        rejected_tavily_count = tavily_summary.rejected_count
        deduped_tavily_count = tavily_summary.deduped_count
        print(
            f"  fetched={tavily_summary.fetched_count}, "
            f"accepted={accepted_tavily_count}, "
            f"rejected={rejected_tavily_count}, "
            f"deduped={deduped_tavily_count}"
        )

        # Re-flip Tavily back to review NOW (don't wait for finally).
        await register_or_update_adapter_status(
            sm,
            adapter_name="tavily_search_extract",
            status="review",
            memo_path="apps/api/docs/compliance/tavily_search_extract.md",
            approver=None, approved_at=None,
            notes="post-8.4A Tavily ingestion re-flip; live use closed",
        )

        # ============================================================
        # Stage 2: Firecrawl extraction on top promising URLs
        # ============================================================
        print("\n" + "=" * 70)
        print("Stage 2: Firecrawl extraction on top human-signal URLs")
        print("=" * 70)
        # Curate up to MAX_FIRECRAWL_URLS Triton-tagged URLs that look
        # like human-signal candidates.
        async with sm() as session:
            triton_rows = (await session.execute(
                select(SourceRecord)
                .where(SourceRecord.source_kind == "tavily_search_extract")
                .where(SourceRecord.source_url.is_not(None))
            )).scalars().all()
        triton_only = [
            r for r in triton_rows
            if (r.metadata_ or {}).get("target_brief") == TARGET_BRIEF_TAG
        ]
        human_signal = [
            r for r in triton_only
            if (r.metadata_ or {}).get("likely_human_signal_candidate")
            is True
        ]
        # Diversify by domain: at most 2 URLs per domain.
        by_domain: dict[str, list] = {}
        for r in human_signal:
            d = (r.metadata_ or {}).get("domain") or "unknown"
            by_domain.setdefault(d, []).append(r)
        target_urls: list[tuple[str, str, str]] = []
        for d, rows in by_domain.items():
            for r in rows[:2]:
                target_urls.append((
                    r.source_url, d,
                    (r.metadata_ or {}).get("title", "") or "",
                ))
                if len(target_urls) >= MAX_FIRECRAWL_URLS:
                    break
            if len(target_urls) >= MAX_FIRECRAWL_URLS:
                break
        print(
            f"  curated {len(target_urls)} URLs from "
            f"{len(human_signal)} human-signal Triton Tavily rows."
        )
        if target_urls:
            await register_or_update_adapter_status(
                sm,
                adapter_name=FIRECRAWL_ADAPTER_NAME,
                status="approved",
                memo_path=FIRECRAWL_MEMO_PATH,
                approver=APPROVER_LABEL,
                approved_at=datetime.now(UTC),
                notes=(
                    "phase 8.4A Triton society build — Firecrawl on "
                    "Tavily-discovered Triton URLs only"
                ),
            )
            try:
                client = FirecrawlClient(
                    max_chars=8000, min_chars=80, timeout_s=30.0,
                )
                for idx, (url, domain, title) in enumerate(target_urls, 1):
                    print(f"  [{idx}/{len(target_urls)}] [{domain}] {url[:80]}")
                    try:
                        page = await client.extract(url)
                    except (
                        FirecrawlBlockedPage,
                        FirecrawlBotProtectionPlaceholder,
                        FirecrawlBoilerplateDominated,
                        FirecrawlBodyTooShort,
                        FirecrawlBodyRedactionFailed,
                    ) as e:
                        rc = getattr(e, "reason_code", type(e).__name__)
                        print(f"      REJECTED [{rc}]")
                        firecrawl_rejections.append({
                            "url": url, "domain": domain,
                            "reason_code": rc, "message": str(e)[:240],
                        })
                        continue
                    except FirecrawlError as e:
                        print(f"      REJECTED [{type(e).__name__}]")
                        firecrawl_rejections.append({
                            "url": url, "domain": domain,
                            "reason_code": type(e).__name__,
                            "message": str(e)[:240],
                        })
                        continue
                    payload = NormalizedSourcePayload(
                        source_url=page.requested_url,
                        captured_at=page.captured_at,
                        content=page.body_markdown,
                        raw_handle=None,
                        metadata={
                            "scraped_via": page.metadata.scraped_via,
                            "requested_url": page.metadata.requested_url,
                            "final_url": page.metadata.final_url,
                            "title": page.metadata.title,
                            "source_status_code":
                                page.metadata.source_status_code,
                            "domain": domain,
                            "target_brief": TARGET_BRIEF_TAG,
                            "phase": "8_4a_triton_society_build",
                            "operator_run": True,
                            "test_fixture": False,
                            "truncated": page.truncated,
                            "body_chars": page.body_chars,
                        },
                        language=page.metadata.page_lang or "en",
                    )
                    insert_dict, rejection = prepare_source_record_insert(
                        payload,
                        source_kind="firecrawl_v1_scrape",
                        compliance_tag="public_api",
                        ingested_by=APPROVER_LABEL,
                        salt=HANDLE_SALT,
                        max_content_chars=8000,
                    )
                    if insert_dict is None:
                        rc = (
                            rejection.reason_code
                            if rejection else "UNKNOWN"
                        )
                        print(f"      REJECTED [{rc}] post-pipeline")
                        firecrawl_rejections.append({
                            "url": url, "domain": domain,
                            "reason_code": rc,
                            "message": (
                                rejection.message if rejection else ""
                            )[:240],
                        })
                        continue
                    try:
                        async with sm() as session:
                            async with session.begin():
                                session.add(SourceRecord(
                                    id=uuid4(), **insert_dict,
                                ))
                        print(
                            f"      ACCEPTED body_chars={page.body_chars}, "
                            f"truncated={page.truncated}"
                        )
                        firecrawl_accepted.append({
                            "url": url, "domain": domain,
                            "title": page.metadata.title,
                            "body_chars": page.body_chars,
                            "truncated": page.truncated,
                        })
                    except Exception as e:
                        if "uq_source_records_kind_hash" in str(e):
                            print("      ACCEPTED but DEDUPED")
                        else:
                            raise
            finally:
                await register_or_update_adapter_status(
                    sm,
                    adapter_name=FIRECRAWL_ADAPTER_NAME,
                    status="review",
                    memo_path=FIRECRAWL_MEMO_PATH,
                    approver=None, approved_at=None,
                    notes="post-8.4A Firecrawl extraction re-flip",
                )
        print(
            f"  firecrawl accepted={len(firecrawl_accepted)}, "
            f"rejected={len(firecrawl_rejections)}"
        )

        # ============================================================
        # Stage 3: persona construction (live LLM trait extractor)
        # ============================================================
        print("\n" + "=" * 70)
        print("Stage 3: persona construction on Triton-tagged source_records")
        print("=" * 70)
        # Pull Triton-tagged source_records (Tavily + Firecrawl).
        async with sm() as session:
            triton_sr = (await session.execute(
                select(SourceRecord)
                .where(
                    (SourceRecord.metadata_["target_brief"].astext
                     == TARGET_BRIEF_TAG)
                )
            )).scalars().all()
        print(f"  Triton-tagged source_records: {len(triton_sr)}")
        # Cap input scope to keep cost in check; the persona builder
        # also classifies per-record so weak / context-only signals
        # don't promote into shells.
        if len(triton_sr) > 0:
            extractor = LLMTraitExtractor()  # cost_guarded_chat-routed
            persona_construction_summary = await run_persona_construction(
                sessionmaker=sm,
                source_records=triton_sr,
                extractor=extractor,
                write_personas=True,
            )
            print(
                f"  candidate_shells={persona_construction_summary.candidate_shells}, "
                f"strong={persona_construction_summary.strong_persona_signal_records}, "
                f"weak={persona_construction_summary.weak_persona_signal_records}, "
                f"context_only={persona_construction_summary.context_only_records}"
            )
            print(
                f"  personas_created="
                f"{persona_construction_summary.personas_created}, "
                f"traits={persona_construction_summary.traits_created}, "
                f"links={persona_construction_summary.evidence_links_created}"
            )

        # ============================================================
        # Stage 4: relevance audit (Triton plan vs all Triton personas)
        # ============================================================
        print("\n" + "=" * 70)
        print("Stage 4: relevance audit against Triton plan")
        print("=" * 70)
        from assembly.pipeline.run_scoped_topup.executor import (
            _load_audience_inputs,
        )
        audience_inputs, domain_map = await _load_audience_inputs(sm)
        # Filter to Triton-only personas: any persona whose
        # evidence_links point to Triton-tagged source_records.
        async with sm() as session:
            # Map source_record_id → target_brief tag
            triton_sr_ids = {r.id for r in triton_sr}
            triton_pids: set = set()
            links = (await session.execute(
                select(PersonaEvidenceLink)
                .where(PersonaEvidenceLink.source_record_id.in_(triton_sr_ids))
            )).scalars().all() if triton_sr_ids else []
            for el in links:
                triton_pids.add(el.persona_id)
        triton_only_inputs = [
            ai for ai in audience_inputs if ai.persona_id in triton_pids
        ]
        print(
            f"  total persona pool: {len(audience_inputs)}, "
            f"Triton-only personas: {len(triton_only_inputs)}"
        )
        if triton_only_inputs:
            audience_result = retrieve_personas_for_target_society(
                brief=triton_brief,
                plan=plan,
                personas=triton_only_inputs,
                domain_by_record_id=domain_map,
            )
            print(
                f"  matched={len(audience_result.matched_personas)}, "
                f"excluded={len(audience_result.excluded_personas)}"
            )

    except Exception as e:
        error_in_run = f"{type(e).__name__}: {e}"
        print(f"\nUNEXPECTED ERROR: {error_in_run}")
    finally:
        # Belt-and-suspenders re-flip in case the inner re-flips were
        # bypassed by an exception.
        try:
            await register_or_update_adapter_status(
                sm, adapter_name="tavily_search_extract",
                status="review",
                memo_path="apps/api/docs/compliance/tavily_search_extract.md",
                approver=None, approved_at=None,
                notes="post-8.4A finally re-flip",
            )
        except Exception:
            pass
        try:
            await register_or_update_adapter_status(
                sm, adapter_name=FIRECRAWL_ADAPTER_NAME,
                status="review",
                memo_path=FIRECRAWL_MEMO_PATH,
                approver=None, approved_at=None,
                notes="post-8.4A finally re-flip",
            )
        except Exception:
            pass

    elapsed = time.monotonic() - started

    # ---- Snapshot forbidden tables AFTER -----------------------------
    async with sm() as session:
        after = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }
        after["SourceRecord"] = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        after["PersonaRecord"] = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        after["PersonaTrait"] = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        after["PersonaEvidenceLink"] = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
    deltas = {
        k: (before[k], after[k]) for k in before if before[k] != after[k]
    }
    forbidden_deltas = {
        k: v for k, v in deltas.items()
        if k not in (
            "SourceRecord", "PersonaRecord", "PersonaTrait",
            "PersonaEvidenceLink",
        )
    }

    # ---- Build report dict ------------------------------------------
    relevant: list[dict] = []
    weakly: list[dict] = []
    excluded: list[dict] = []
    if audience_result is not None:
        for m in audience_result.matched_personas:
            entry = {
                "persona_id": m.persona_id,
                "display_name": m.display_name,
                "score": m.relevance_score,
                "classification": m.classification.value,
                "category": m.matched_category_key,
                "category_display": m.matched_category_display_name,
                "matched_signals": list(m.matched_signals)[:5],
                "evidence_link_count": m.evidence_link_count,
                "why_included": m.why_included[:300],
            }
            if m.classification in (
                RelevanceClassification.RELEVANT,
                RelevanceClassification.HIGHLY_RELEVANT,
            ):
                relevant.append(entry)
            elif m.classification == RelevanceClassification.WEAKLY_RELEVANT:
                weakly.append(entry)
        for ex in audience_result.excluded_personas:
            excluded.append({
                "persona_id": ex.persona_id,
                "display_name": ex.display_name,
                "exclusion_reason": ex.exclusion_reason[:300],
                "best_possible_category": ex.best_possible_category,
                "score": ex.score,
            })
    relevant.sort(key=lambda x: -x["score"])
    weakly.sort(key=lambda x: -x["score"])

    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = {
        "phase": "8_4a_triton_society_build",
        "completed_at": datetime.now(UTC).isoformat(),
        "runtime_s": round(elapsed, 1),
        "brief": {
            "product_name": triton_brief.product_name,
            "category": triton_brief.product_type,
            "price": triton_brief.price_or_price_structure,
            "geography": triton_brief.geography,
            "competitors": list(triton_brief.competitors),
            "simulation_goal": triton_brief.simulation_goal.value,
        },
        "plan_summary": {
            "family": plan.interpreted_brief.detected_product_family.value,
            "category_count": len(plan.stakeholder_categories),
            "categories": [
                {
                    "key": c.category_key,
                    "display": c.display_name,
                    "priority": c.priority,
                    "tiny_min": c.minimum_persona_target_tiny,
                    "small_min": c.minimum_persona_target_small,
                    "serious_min": c.minimum_persona_target_serious,
                } for c in plan.stakeholder_categories
            ],
            "warnings": [
                {"code": w.code, "severity": w.severity.value,
                 "message": w.message[:200]}
                for w in plan.warnings_and_limitations
            ],
        },
        "queries_used_count": len(TRITON_QUERIES),
        "queries": list(TRITON_QUERIES.keys()),
        "tavily": {
            "fetched": (
                tavily_summary.fetched_count
                if 'tavily_summary' in locals() else 0
            ),
            "accepted": accepted_tavily_count,
            "rejected": rejected_tavily_count,
            "deduped": deduped_tavily_count,
            "rejection_reasons": [
                {"reason_code": r.reason_code, "message": r.message[:200]}
                for r in (
                    tavily_summary.rejection_reasons
                    if 'tavily_summary' in locals() else []
                )[:25]
            ],
        },
        "firecrawl": {
            "urls_attempted": (
                len(firecrawl_accepted) + len(firecrawl_rejections)
            ),
            "accepted": len(firecrawl_accepted),
            "rejected": len(firecrawl_rejections),
            "accepted_records": firecrawl_accepted,
            "rejection_reasons": firecrawl_rejections,
        },
        "persona_construction": (
            {
                "source_records_seen":
                    persona_construction_summary.source_records_seen,
                "candidate_shells":
                    persona_construction_summary.candidate_shells,
                "strong_persona_signal_records":
                    persona_construction_summary
                    .strong_persona_signal_records,
                "weak_persona_signal_records":
                    persona_construction_summary
                    .weak_persona_signal_records,
                "context_only_records":
                    persona_construction_summary.context_only_records,
                "rejected_records":
                    persona_construction_summary.rejected_records,
                "personas_created":
                    persona_construction_summary.personas_created,
                "personas_skipped":
                    persona_construction_summary.personas_skipped,
                "skipped_reasons": [
                    s.value for s in
                    persona_construction_summary.skipped_reasons
                ][:25],
                "traits_created":
                    persona_construction_summary.traits_created,
                "traits_rejected":
                    persona_construction_summary.traits_rejected,
                "evidence_links_created":
                    persona_construction_summary.evidence_links_created,
            }
            if persona_construction_summary is not None else None
        ),
        "audience_audit": (
            {
                "matched_count": len(audience_result.matched_personas),
                "relevant_or_better_count": len(relevant),
                "weakly_relevant_count": len(weakly),
                "excluded_count": len(excluded),
                "next_step":
                    audience_result.next_step_recommendation.value,
                "category_coverage": [
                    {
                        "key": cc.category_key,
                        "matched_total": cc.matched_total,
                        "label": cc.coverage_label.value,
                    }
                    for cc in audience_result.category_coverage
                ],
                "warnings": list(audience_result.warnings_and_caveats),
            }
            if audience_result is not None else None
        ),
        "relevant_personas_top10": relevant[:10],
        "weakly_relevant_top10": weakly[:10],
        "excluded_top10": excluded[:10],
        "row_deltas": {
            "SourceRecord": deltas.get("SourceRecord"),
            "PersonaRecord": deltas.get("PersonaRecord"),
            "PersonaTrait": deltas.get("PersonaTrait"),
            "PersonaEvidenceLink": deltas.get("PersonaEvidenceLink"),
        },
        "forbidden_table_deltas_must_be_empty": forbidden_deltas,
        "error_in_run": error_in_run,
    }
    out_path = out_dir / "triton_society_build_8_4a.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print("=" * 70)
    print("Phase 8.4A — TRITON SOCIETY BUILD SUMMARY")
    print("=" * 70)
    print(f"runtime: {elapsed:.1f}s")
    print(
        f"row deltas: SR+{(deltas.get('SourceRecord') or (0,0))[1] - (deltas.get('SourceRecord') or (0,0))[0]}, "
        f"PR+{(deltas.get('PersonaRecord') or (0,0))[1] - (deltas.get('PersonaRecord') or (0,0))[0]}, "
        f"PT+{(deltas.get('PersonaTrait') or (0,0))[1] - (deltas.get('PersonaTrait') or (0,0))[0]}, "
        f"PEL+{(deltas.get('PersonaEvidenceLink') or (0,0))[1] - (deltas.get('PersonaEvidenceLink') or (0,0))[0]}"
    )
    print(f"forbidden-table deltas (must be empty): {forbidden_deltas}")
    print(
        f"audience: relevant={len(relevant)}, "
        f"weakly={len(weakly)}, excluded={len(excluded)}"
    )
    print(f"\n→ audit JSON: {out_path}")
    return 0 if error_in_run is None else 1


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
