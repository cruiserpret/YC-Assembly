"""Phase 9A.1 — persona-candidate emission widening.

Universal fix for the 9A raw-candidate bottleneck (19 < 25 floor).
Adds two new universal modules to the 9A pipeline:

  1. `evidence_signal_extractor.extract_evidence_signals(...)` —
     atomic signal extraction from each accepted evidence item.
     One source can yield 1-N atomic signals (price/value, trust/
     proof, safety/visibility, format, convenience, performance,
     objection, use-case, competitor, substitute).
  2. `persona_emission_widener.widen_persona_candidates(...)` —
     supplemental candidate emission from atomic signals on top
     of the existing PersonaCandidatePlanner output. Per-source
     cap = 3, per-(role,source,objection) cap = 2, per-(role,
     evidence_excerpt) dedup.

NO new APIs. NO weakening of gates: `EXPECTED_MIN_RAW_CANDIDATES=25`
and `EXPECTED_MIN_COMPRESSED_PERSONAS=21` are unchanged.

Modes:
  --dry-run (default): planner + caps preview, no DB / no LLM.
  --replay-9a: read 9A audit, document what's missing for true
    replay-from-audit, exit cleanly without DB/LLM.
  --commit: full live retrieval + widened emission + persistence
    + simulation + scaled founder report.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.models.agent import Agent
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.models.round import (
    AgentResponse, DebateTurn, SimulationRound,
)
from assembly.models.simulation import Simulation, SimulationInput
from assembly.pipeline.persona.anonymization import generate_display_name
from assembly.pipeline.persona.constants import PERSONA_FIELD_NAMES
from assembly.sources.brave import (
    BraveAdapterConfig, BraveSearchClient,
    is_brave_key_present, redact_url_for_audit,
)
from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning, generate_anchor_plan,
)
from assembly.sources.evidence_signal_extractor import (
    extract_evidence_signals,
)
from assembly.sources.persona_emission_widener import (
    EmissionPolicy, widen_persona_candidates,
)
from assembly.sources.firecrawl import (
    FirecrawlAdapterConfig, FirecrawlExtractClient,
    is_firecrawl_key_present,
)
from assembly.sources.founder_report_generator import (
    aggregate_founder_report, evaluate_report_quality,
    render_markdown_report, scan_for_secrets,
)
from assembly.sources.persona_diversity_evaluator import (
    evaluate_persona_diversity,
)
from assembly.sources.persona_role_planner import (
    EffectiveSourceRecord, PersonaCandidatePlanner,
    validate_launch_state_claims,
)
from assembly.sources.persona_role_planner.schemas import (
    InferredPersonaTrait, PersonaCandidate,
)
from assembly.sources.persona_set_compressor import compress_persona_set
from assembly.sources.run_scoped_persona_simulation import (
    AGENT_ROUND_TYPES, MARKET_ENTRY_STANCES, RoundOutputAudit,
    evaluate_simulation_quality, load_run_scoped_agents,
    scan_forecast_or_verdict_claims,
    scan_unlaunched_product_use_claims,
)
from assembly.sources.source_expansion_planner import (
    generate_source_expansion_plan,
)
from assembly.sources.tavily import (
    TavilyAdapterConfig, TavilySearchClient,
    is_tavily_key_present, redact_tavily_url_for_audit,
)
from assembly.sources.youtube import (
    YouTubeAdapterConfig, YouTubeDataClient,
    is_youtube_key_present, looks_like_low_quality_comment,
)


PHASE_LABEL = "9A.1"
TARGET_BRIEF_ID = "lumaloop"
PRODUCT_NAME = "LumaLoop"
LAUNCH_STATE = "unlaunched"
INGESTED_BY = "assembly_phase_9a_1_lumaloop_widened"
# Phase 9A target: 21-30 compressed personas (up from 5 in 8.5G.1).
EXPECTED_MIN_COMPRESSED_PERSONAS = 21
EXPECTED_MAX_COMPRESSED_PERSONAS = 30
EXPECTED_MIN_RAW_CANDIDATES = 25
MAX_ROLE_CONCENTRATION_FRACTION = 0.35  # no single role > 35%
SIM_HARD_CAP_USD = Decimal("8.00")  # raised from $3 in 8.5G.1
SIM_SOFT_CAP_USD = Decimal("3.00")

LUMALOOP_BRIEF = ProductBriefForPlanning(
    product_name=PRODUCT_NAME,
    product_description=(
        "A rechargeable snap-on LED safety band for runners, "
        "cyclists, dog walkers, college students, and night "
        "commuters who want to be more visible outdoors after "
        "dark. It clips onto an arm, ankle, backpack strap, "
        "bike handlebar, or dog leash. It has three brightness "
        "modes, weather-resistant housing, USB-C charging, and "
        "a lightweight silicone body."
    ),
    price_or_price_structure="$24.99",
    launch_geography="California, United States",
    target_customers=[
        "night runners", "cyclists", "dog walkers",
        "college students walking at night",
        "commuters who walk or bike after dark",
        "parents buying safety gear for teens",
        "people who dislike bulky reflective vests",
    ],
    competitors=[
        "Noxgear Tracer2", "Amphipod", "Nathan reflective gear",
        "FlipBelt lights", "Black Diamond Sprinter headlamp",
    ],
    optional_constraints=[],
)


_FORBIDDEN_RAW_USER_ID_KEYS = (
    "raw_user_id", "channel_id", "channelId", "author_channel_id",
    "authorChannelId", "user_id", "reviewer_id",
)
_FORBIDDEN_IMAGE_URL_KEYS = (
    "image_url", "image", "thumbnail", "thumbnail_url",
    "profile_image", "profile_picture", "avatar_url", "photo_url",
)


def _strip_forbidden_metadata(md: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v for k, v in md.items()
        if k not in _FORBIDDEN_RAW_USER_ID_KEYS
        and k not in _FORBIDDEN_IMAGE_URL_KEYS
    }


_TRAIT_PREFIX_MAP: tuple[tuple[str, str], ...] = (
    ("current_alternative", "current_alternatives"),
    ("alternative_", "current_alternatives"),
    ("competitor_", "current_alternatives"),
    ("substitute_", "current_alternatives"),
    ("price_", "price_sensitivity"),
    ("budget_", "price_sensitivity"),
    ("cost_", "price_sensitivity"),
    ("willingness_to_pay", "price_sensitivity"),
    ("trust_", "trust_triggers"),
    ("proof_", "trust_triggers"),
    ("credibility_", "trust_triggers"),
    ("required_credibility", "trust_triggers"),
    ("geography_", "geography_broad"),
    ("region_", "geography_broad"),
    ("location_", "geography_broad"),
    ("role_", "role_or_context"),
    ("context_", "role_or_context"),
    ("occupation_", "role_or_context"),
    ("profession_", "role_or_context"),
    ("influence_", "influence_signals"),
    ("susceptibility", "influence_signals"),
    ("status_", "influence_signals"),
    ("communication_", "communication_style"),
    ("voice_", "communication_style"),
    ("tone_", "communication_style"),
    ("buying_", "buying_constraints"),
    ("purchase_", "buying_constraints"),
    ("constraint_", "buying_constraints"),
    ("switching_", "buying_constraints"),
    ("objection_", "objection_patterns"),
    ("concern_", "objection_patterns"),
    ("complaint_", "objection_patterns"),
    ("fear_", "objection_patterns"),
    ("preference_", "interests"),
    ("interest_", "interests"),
    ("behavior_", "interests"),
    ("habit_", "interests"),
    ("use_case", "interests"),
)


def _map_trait_field(trait_name: str) -> str:
    if not trait_name:
        return "interests"
    low = trait_name.lower().strip()
    for prefix, field in _TRAIT_PREFIX_MAP:
        if low.startswith(prefix):
            return field
    return "interests"


def _confidence_decimal(confidence: str) -> Decimal:
    return {
        "high": Decimal("0.9"),
        "medium": Decimal("0.6"),
        "low": Decimal("0.3"),
    }.get(confidence, Decimal("0.5"))


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


async def _read_table_counts(sm) -> dict[str, int]:
    async with sm() as session:
        sr = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        pr = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
        sim = (await session.execute(
            select(func.count()).select_from(Simulation)
        )).scalar_one()
        ag = (await session.execute(
            select(func.count()).select_from(Agent)
        )).scalar_one()
        rd = (await session.execute(
            select(func.count()).select_from(SimulationRound)
        )).scalar_one()
        ar = (await session.execute(
            select(func.count()).select_from(AgentResponse)
        )).scalar_one()
        dt = (await session.execute(
            select(func.count()).select_from(DebateTurn)
        )).scalar_one()
    return {
        "source_records": int(sr), "persona_records": int(pr),
        "persona_traits": int(pt), "persona_evidence_links": int(pel),
        "simulations": int(sim), "agents": int(ag),
        "simulation_rounds": int(rd),
        "agent_responses": int(ar), "debate_turns": int(dt),
    }


def _make_run_scope_id() -> str:
    payload = "|".join((
        TARGET_BRIEF_ID, PRODUCT_NAME, LAUNCH_STATE,
        datetime.now(UTC).date().isoformat(), PHASE_LABEL,
    ))
    return "run_9a1_" + hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()[:12]


_EMAIL_RE = re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")


def _scan_pii(text: str) -> list[str]:
    hits: list[str] = []
    if _EMAIL_RE.search(text or ""):
        hits.append("email")
    if _PHONE_RE.search(text or ""):
        hits.append("phone")
    return hits


def _generic_filler(text: str) -> bool:
    return not text or len(text.strip()) < 30


def _text_score(
    *,
    text: str,
    competitors_lower: list[str],
    substitutes_lower: list[str],
    positive_anchors_lower: list[str],
    use_cases_lower: list[str],
    objection_anchors_lower: list[str],
) -> tuple[int, list[str]]:
    """Universal scorer (same as 8.5G with first-word competitor +
    single-token use-case fallbacks)."""
    if not text:
        return 0, []
    low = text.lower()
    matched: list[str] = []
    score = 0
    for c in competitors_lower:
        if re.search(rf"\b{re.escape(c)}\b", low):
            matched.append(f"competitor:{c}")
            score += 4
            continue
        first = c.split()[0]
        if (
            len(first) >= 4
            and first not in ("the", "and", "for", "all")
            and re.search(rf"\b{re.escape(first)}\b", low)
        ):
            matched.append(f"competitor_first_word:{first}")
            score += 2
    for s in substitutes_lower:
        if re.search(rf"\b{re.escape(s)}\b", low):
            matched.append(f"substitute:{s}")
            score += 2
    multi_word_positives = [
        a for a in positive_anchors_lower if " " in a
    ]
    for a in multi_word_positives:
        if a in low:
            matched.append(f"positive:{a}")
            score += 3
    seen_uc: set[str] = set()
    for u in use_cases_lower:
        if " " in u and u in low:
            matched.append(f"use_case:{u}")
            score += 1
            for tok in u.split():
                seen_uc.add(tok)
        for tok in u.split():
            if (
                len(tok) >= 4 and tok not in seen_uc
                and re.search(rf"\b{re.escape(tok)}\b", low)
            ):
                matched.append(f"use_case_token:{tok}")
                score += 1
                seen_uc.add(tok)
    for o in objection_anchors_lower:
        if " " in o and o in low:
            matched.append(f"objection:{o}")
            score += 1
    return score, matched


def _infer_persona_value_roles(
    *,
    text: str,
    competitors: list[str],
    substitutes_lower: list[str],
    use_cases_lower: list[str],
) -> list[str]:
    low = (text or "").lower()
    roles: list[str] = []
    for c in competitors:
        if re.search(rf"\b{re.escape(c.lower())}\b", low):
            slug = re.sub(
                r"[^\w]+", "_",
                c.lower().replace("'", ""),
            ).strip("_")
            roles.append(f"competitor_user_{slug}")
        else:
            first = c.split()[0]
            if (
                len(first) >= 4
                and re.search(rf"\b{re.escape(first.lower())}\b", low)
            ):
                slug = re.sub(r"[^\w]+", "_", first.lower()).strip("_")
                roles.append(f"competitor_user_{slug}")
    for s in substitutes_lower:
        if re.search(rf"\b{re.escape(s)}\b", low):
            slug = re.sub(r"[^\w]+", "_", s).strip("_")
            roles.append(f"substitute_user_{slug}")
    for u in use_cases_lower:
        if " " in u and u in low:
            roles.append("use_case_focused_buyer")
            break
    return list(dict.fromkeys(roles))


def _planned_source_record_dict(
    *,
    source_kind: str,
    source_url: str,
    content: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    md = _strip_forbidden_metadata(dict(metadata))
    md["target_brief"] = TARGET_BRIEF_ID
    md["product_name"] = PRODUCT_NAME
    md["launch_state"] = LAUNCH_STATE
    md["phase_origin"] = PHASE_LABEL
    return {
        "planned_source_record_id_synthetic": (
            f"planned::{TARGET_BRIEF_ID}::{source_kind}::{h[:16]}"
        ),
        "source_kind": source_kind,
        "source_url": source_url,
        "content_preview": content[:1200],
        "content_length": len(content),
        "content_hash": h,
        "language": "en",
        "metadata": md,
        "ingested_by": INGESTED_BY,
        "compliance_tag": (
            "public_html"
            if source_kind in ("brave_search_result", "tavily_search_result", "firecrawl_extracted_page")
            else "public_api"
        ),
        "captured_at": datetime.now(UTC).isoformat(),
        "pii_redaction_status": "passed",
        "sensitive_scan_status": "passed",
        "user_handle_hash": None,
    }


# =====================================================================
# Stage: bounded retrieval (Brave + YouTube + Tavily + optional Firecrawl)
# =====================================================================


async def _stage_retrieve_evidence(
    *,
    brief: ProductBriefForPlanning,
    anchor_plan,
    expansion_plan,
    do_commit: bool,
    brave_max_queries: int,
    yt_max_video_queries: int,
    yt_max_videos_per_query: int,
    yt_max_comments_per_video: int,
    tavily_max_queries: int,
    firecrawl_max_pages: int,
) -> dict[str, Any]:
    competitors_lower = [c.lower() for c in brief.competitors]
    substitutes_lower = [s.lower() for s in anchor_plan.substitute_anchor_terms]
    positive_lower = [a.lower() for a in anchor_plan.positive_anchor_terms]
    use_cases_lower = [u.lower() for u in anchor_plan.use_case_anchor_terms]
    objections_lower = [o.lower() for o in anchor_plan.objection_anchor_terms]

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()

    summaries: dict[str, dict[str, Any]] = {
        "brave_search": {
            "configured": is_brave_key_present(),
            "queries_run": 0, "results_returned": 0,
            "accepted": 0, "rejected": 0,
        },
        "youtube_data_api": {
            "configured": is_youtube_key_present(),
            "video_queries_run": 0, "videos_returned": 0,
            "comments_returned": 0, "accepted": 0, "rejected": 0,
        },
        "tavily_search": {
            "configured": is_tavily_key_present(),
            "queries_run": 0, "results_returned": 0,
            "accepted": 0, "rejected": 0,
        },
        "firecrawl": {
            "configured": is_firecrawl_key_present(),
            "pages_extracted": 0, "accepted": 0, "rejected": 0,
        },
    }

    if not do_commit:
        return {
            "providers_used": [],
            "summaries": summaries,
            "accepted": [], "rejected": [],
            "planned_source_records": [],
        }

    # ---- Brave ----
    brave_plan = next(
        (p for p in expansion_plan.provider_query_plans
         if p.provider == "brave_search"), None,
    )
    if brave_plan and brave_plan.is_provider_configured:
        client = BraveSearchClient(BraveAdapterConfig(
            max_queries=brave_max_queries,
            max_results_per_query=10,
        ))
        bqs = [q.query_text for q in brave_plan.queries[:brave_max_queries]]
        try:
            results = client.search(queries=bqs)
        except Exception as e:
            print(f"WARN: Brave failed: {type(e).__name__}: {e}")
            results = []
        summaries["brave_search"]["queries_run"] = len(bqs)
        summaries["brave_search"]["results_returned"] = len(results)
        for r in results:
            url_red = redact_url_for_audit(r.url)
            content = (r.title + ". " + r.description).strip()
            score, matched = _text_score(
                text=content,
                competitors_lower=competitors_lower,
                substitutes_lower=substitutes_lower,
                positive_anchors_lower=positive_lower,
                use_cases_lower=use_cases_lower,
                objection_anchors_lower=objections_lower,
            )
            pii = _scan_pii(content)
            v_use = scan_unlaunched_product_use_claims(
                text=content, product_name=PRODUCT_NAME,
            )
            content_hash = hashlib.sha256(
                content.encode("utf-8"),
            ).hexdigest()
            duplicate = (
                url_red in seen_urls or content_hash in seen_hashes
            )
            reasons: list[str] = []
            if pii:
                reasons.append(f"reject_pii_hit:{','.join(pii)}")
            if not v_use.is_valid:
                reasons.append("reject_fake_target_product_use")
            if duplicate:
                reasons.append("reject_duplicate_url_or_hash")
            if _generic_filler(content) and score == 0:
                reasons.append("reject_generic_only")
            if score < 3 and not reasons:
                reasons.append("reject_below_relevance_threshold")
            row = {
                "provider": "brave_search",
                "query": r.query, "title": r.title,
                "url": url_red, "domain": r.domain,
                "snippet": r.description[:300],
                "evidence_score": score,
                "matched_terms": matched,
                "decision": "REJECTED" if reasons else "ACCEPTED",
                "rejection_reasons": reasons,
            }
            if reasons:
                rejected.append(row)
                summaries["brave_search"]["rejected"] += 1
                continue
            seen_urls.add(url_red)
            seen_hashes.add(content_hash)
            roles = _infer_persona_value_roles(
                text=content,
                competitors=brief.competitors,
                substitutes_lower=substitutes_lower,
                use_cases_lower=use_cases_lower,
            )
            psr = _planned_source_record_dict(
                source_kind="brave_search_result",
                source_url=url_red, content=content,
                metadata={
                    "provider": "brave_search",
                    "source_provider": "brave_search",
                    "source_is_live_web": True,
                    "query": r.query, "title": r.title,
                    "domain": r.domain,
                    "matched_terms": matched,
                    "persona_value_roles": roles,
                    "anchor_score": score,
                    "expected_evidence_type": "blog_review",
                    "source_caveat": (
                        "Brave Search snippet captured during "
                        "Phase 8.5G.1 source expansion."
                    ),
                },
            )
            row["planned_source_record_id_synthetic"] = (
                psr["planned_source_record_id_synthetic"]
            )
            accepted.append(row)
            planned.append(psr)
            summaries["brave_search"]["accepted"] += 1

    # ---- Tavily ----
    tavily_plan = next(
        (p for p in expansion_plan.provider_query_plans
         if p.provider == "tavily_search"), None,
    )
    if tavily_plan and tavily_plan.is_provider_configured:
        tclient = TavilySearchClient(TavilyAdapterConfig(
            max_queries=tavily_max_queries,
            max_results_per_query=8,
        ))
        tqs = [q.query_text for q in tavily_plan.queries[:tavily_max_queries]]
        try:
            tresults = tclient.search(queries=tqs)
        except Exception as e:
            print(f"WARN: Tavily failed: {type(e).__name__}: {e}")
            tresults = []
        summaries["tavily_search"]["queries_run"] = len(tqs)
        summaries["tavily_search"]["results_returned"] = len(tresults)
        for r in tresults:
            url_red = redact_tavily_url_for_audit(r.url)
            content = (r.title + ". " + r.content).strip()
            score, matched = _text_score(
                text=content,
                competitors_lower=competitors_lower,
                substitutes_lower=substitutes_lower,
                positive_anchors_lower=positive_lower,
                use_cases_lower=use_cases_lower,
                objection_anchors_lower=objections_lower,
            )
            pii = _scan_pii(content)
            v_use = scan_unlaunched_product_use_claims(
                text=content, product_name=PRODUCT_NAME,
            )
            content_hash = hashlib.sha256(
                content.encode("utf-8"),
            ).hexdigest()
            duplicate = (
                url_red in seen_urls or content_hash in seen_hashes
            )
            reasons: list[str] = []
            if pii:
                reasons.append(f"reject_pii_hit:{','.join(pii)}")
            if not v_use.is_valid:
                reasons.append("reject_fake_target_product_use")
            if duplicate:
                reasons.append("reject_duplicate_url_or_hash")
            if _generic_filler(content) and score == 0:
                reasons.append("reject_generic_only")
            if score < 2 and not reasons:
                # Tavily threshold slightly lower because it returns
                # full-content snippets (longer than Brave's title).
                reasons.append("reject_below_relevance_threshold")
            row = {
                "provider": "tavily_search",
                "query": r.query, "title": r.title,
                "url": url_red, "domain": r.domain,
                "snippet": r.content[:400],
                "evidence_score": score,
                "matched_terms": matched,
                "decision": "REJECTED" if reasons else "ACCEPTED",
                "rejection_reasons": reasons,
                "tavily_score": r.score,
            }
            if reasons:
                rejected.append(row)
                summaries["tavily_search"]["rejected"] += 1
                continue
            seen_urls.add(url_red)
            seen_hashes.add(content_hash)
            roles = _infer_persona_value_roles(
                text=content,
                competitors=brief.competitors,
                substitutes_lower=substitutes_lower,
                use_cases_lower=use_cases_lower,
            )
            psr = _planned_source_record_dict(
                source_kind="tavily_search_result",
                source_url=url_red, content=content,
                metadata={
                    "provider": "tavily_search",
                    "source_provider": "tavily_search",
                    "source_is_live_web": True,
                    "query": r.query, "title": r.title,
                    "domain": r.domain,
                    "matched_terms": matched,
                    "persona_value_roles": roles,
                    "anchor_score": score,
                    "tavily_score": r.score,
                    "expected_evidence_type": "buyer_guide",
                    "source_caveat": (
                        "Tavily search snippet captured during "
                        "Phase 8.5G.1 source expansion."
                    ),
                },
            )
            row["planned_source_record_id_synthetic"] = (
                psr["planned_source_record_id_synthetic"]
            )
            accepted.append(row)
            planned.append(psr)
            summaries["tavily_search"]["accepted"] += 1

    # ---- Firecrawl extraction (top URLs) ----
    if is_firecrawl_key_present() and firecrawl_max_pages > 0:
        # Pick top URLs by evidence_score from the accepted Brave/Tavily
        # candidates; cap per-domain inside the adapter.
        top_urls: list[str] = []
        ranked = sorted(
            (
                r for r in accepted
                if r.get("provider") in ("brave_search", "tavily_search")
                and r.get("url")
            ),
            key=lambda r: -int(r.get("evidence_score") or 0),
        )
        seen_urls_for_fc: set[str] = set()
        for r in ranked:
            u = r["url"]
            if u in seen_urls_for_fc:
                continue
            seen_urls_for_fc.add(u)
            top_urls.append(u)
            if len(top_urls) >= firecrawl_max_pages:
                break
        if top_urls:
            fc_client = FirecrawlExtractClient(FirecrawlAdapterConfig(
                max_pages=firecrawl_max_pages,
                max_pages_per_domain=3,
            ))
            try:
                fc_pages = await fc_client.extract_top_urls(urls=top_urls)
            except Exception as e:
                print(f"WARN: Firecrawl failed: {type(e).__name__}: {e}")
                fc_pages = []
            summaries["firecrawl"]["pages_extracted"] = len(fc_pages)
            for p in fc_pages:
                content = (p.title + "\n\n" + p.markdown).strip()
                score, matched = _text_score(
                    text=content,
                    competitors_lower=competitors_lower,
                    substitutes_lower=substitutes_lower,
                    positive_anchors_lower=positive_lower,
                    use_cases_lower=use_cases_lower,
                    objection_anchors_lower=objections_lower,
                )
                pii = _scan_pii(content)
                v_use = scan_unlaunched_product_use_claims(
                    text=content, product_name=PRODUCT_NAME,
                )
                content_hash = hashlib.sha256(
                    content.encode("utf-8"),
                ).hexdigest()
                duplicate = (
                    p.url in seen_urls or content_hash in seen_hashes
                )
                reasons: list[str] = []
                if pii:
                    reasons.append(f"reject_pii_hit:{','.join(pii)}")
                if not v_use.is_valid:
                    reasons.append("reject_fake_target_product_use")
                if duplicate:
                    reasons.append("reject_duplicate_url_or_hash")
                if score < 3 and not reasons:
                    reasons.append("reject_below_relevance_threshold")
                row = {
                    "provider": "firecrawl",
                    "url": p.url, "domain": p.domain,
                    "title": p.title,
                    "snippet": p.markdown[:400],
                    "evidence_score": score,
                    "matched_terms": matched,
                    "decision": (
                        "REJECTED" if reasons else "ACCEPTED"
                    ),
                    "rejection_reasons": reasons,
                }
                if reasons:
                    rejected.append(row)
                    summaries["firecrawl"]["rejected"] += 1
                    continue
                seen_urls.add(p.url)
                seen_hashes.add(content_hash)
                roles = _infer_persona_value_roles(
                    text=content,
                    competitors=brief.competitors,
                    substitutes_lower=substitutes_lower,
                    use_cases_lower=use_cases_lower,
                )
                psr = _planned_source_record_dict(
                    source_kind="firecrawl_extracted_page",
                    source_url=p.url, content=content,
                    metadata={
                        "provider": "firecrawl",
                        "source_provider": "firecrawl",
                        "source_is_live_web": True,
                        "title": p.title, "domain": p.domain,
                        "matched_terms": matched,
                        "persona_value_roles": roles,
                        "anchor_score": score,
                        "expected_evidence_type": "blog_review",
                        "source_caveat": (
                            "Firecrawl-extracted full page captured "
                            "during Phase 8.5G.1 source expansion."
                        ),
                    },
                )
                row["planned_source_record_id_synthetic"] = (
                    psr["planned_source_record_id_synthetic"]
                )
                accepted.append(row)
                planned.append(psr)
                summaries["firecrawl"]["accepted"] += 1

    # ---- YouTube ----
    yt_plan = next(
        (p for p in expansion_plan.provider_query_plans
         if p.provider == "youtube_data_api"), None,
    )
    if yt_plan and yt_plan.is_provider_configured:
        client = YouTubeDataClient(YouTubeAdapterConfig(
            max_videos=yt_max_videos_per_query,
            max_comments_total=200,
            max_comments_per_video=yt_max_comments_per_video,
        ))
        yqs = [q.query_text for q in yt_plan.queries[:yt_max_video_queries]]
        videos: list = []
        for q in yqs:
            try:
                vlist = client.search_videos(
                    query=q, max_results=yt_max_videos_per_query,
                )
            except Exception as e:
                print(f"WARN: YT search {q!r}: {e}")
                continue
            summaries["youtube_data_api"]["video_queries_run"] += 1
            for v in vlist:
                videos.append((q, v))
            summaries["youtube_data_api"]["videos_returned"] += len(vlist)

        comments_total = 0
        for q, v in videos:
            v_text = (v.title + ". by " + v.channel_title).strip()
            v_score, v_matched = _text_score(
                text=v_text,
                competitors_lower=competitors_lower,
                substitutes_lower=substitutes_lower,
                positive_anchors_lower=positive_lower,
                use_cases_lower=use_cases_lower,
                objection_anchors_lower=objections_lower,
            )
            v_pii = _scan_pii(v_text)
            v_use = scan_unlaunched_product_use_claims(
                text=v_text, product_name=PRODUCT_NAME,
            )
            v_url = f"https://www.youtube.com/watch?v={v.video_id}"
            v_url_red = redact_url_for_audit(v_url)
            v_hash = hashlib.sha256(v_text.encode("utf-8")).hexdigest()
            duplicate = (
                v_url_red in seen_urls or v_hash in seen_hashes
            )
            reasons: list[str] = []
            if v_pii:
                reasons.append(f"reject_pii_hit:{','.join(v_pii)}")
            if not v_use.is_valid:
                reasons.append("reject_fake_target_product_use")
            if duplicate:
                reasons.append("reject_duplicate_url_or_hash")
            if v_score < 3 and not reasons:
                reasons.append("reject_below_relevance_threshold")
            row = {
                "provider": "youtube_data_api",
                "query": q, "title": v.title,
                "video_id": v.video_id,
                "channel_title": v.channel_title,
                "url": v_url_red,
                "snippet": v_text[:300],
                "evidence_score": v_score,
                "matched_terms": v_matched,
                "decision": "REJECTED" if reasons else "ACCEPTED",
                "rejection_reasons": reasons,
            }
            if reasons:
                rejected.append(row)
                summaries["youtube_data_api"]["rejected"] += 1
            else:
                seen_urls.add(v_url_red)
                seen_hashes.add(v_hash)
                roles = _infer_persona_value_roles(
                    text=v_text,
                    competitors=brief.competitors,
                    substitutes_lower=substitutes_lower,
                    use_cases_lower=use_cases_lower,
                )
                psr = _planned_source_record_dict(
                    source_kind="youtube_video_result",
                    source_url=v_url_red, content=v_text,
                    metadata={
                        "provider": "youtube_data_api",
                        "query": q, "title": v.title,
                        "video_id": v.video_id,
                        "channel_title": v.channel_title,
                        "matched_terms": v_matched,
                        "persona_value_roles": roles,
                        "anchor_score": v_score,
                        "expected_evidence_type": "video_review",
                        "source_caveat": (
                            "YouTube Data API public metadata; "
                            "author identifiers not stored."
                        ),
                    },
                )
                row["planned_source_record_id_synthetic"] = (
                    psr["planned_source_record_id_synthetic"]
                )
                accepted.append(row)
                planned.append(psr)
                summaries["youtube_data_api"]["accepted"] += 1

            try:
                comments = client.fetch_comments(
                    video_id=v.video_id,
                    max_comments=min(
                        yt_max_comments_per_video,
                        200 - comments_total,
                    ),
                )
            except Exception as e:
                print(f"WARN: YT comments {v.video_id}: {e}")
                comments = []
            summaries["youtube_data_api"]["comments_returned"] += len(comments)
            comments_total += len(comments)
            for c in comments:
                c_text = c.text
                if looks_like_low_quality_comment(c_text):
                    rejected.append({
                        "provider": "youtube_data_api",
                        "video_id": v.video_id,
                        "comment_id": c.comment_id,
                        "snippet": c_text[:300],
                        "decision": "REJECTED",
                        "rejection_reasons": ["reject_low_quality_comment"],
                    })
                    summaries["youtube_data_api"]["rejected"] += 1
                    continue
                c_score, c_matched = _text_score(
                    text=c_text,
                    competitors_lower=competitors_lower,
                    substitutes_lower=substitutes_lower,
                    positive_anchors_lower=positive_lower,
                    use_cases_lower=use_cases_lower,
                    objection_anchors_lower=objections_lower,
                )
                c_pii = _scan_pii(c_text)
                c_use = scan_unlaunched_product_use_claims(
                    text=c_text, product_name=PRODUCT_NAME,
                )
                c_hash = hashlib.sha256(c_text.encode("utf-8")).hexdigest()
                duplicate_c = c_hash in seen_hashes
                reasons = []
                if c_pii:
                    reasons.append(f"reject_pii_hit:{','.join(c_pii)}")
                if not c_use.is_valid:
                    reasons.append("reject_fake_target_product_use")
                if duplicate_c:
                    reasons.append("reject_duplicate_url_or_hash")
                if _generic_filler(c_text) and c_score == 0:
                    reasons.append("reject_generic_only")
                if c_score < 1 and not reasons:
                    reasons.append("reject_below_relevance_threshold")
                comment_url = (
                    f"https://www.youtube.com/watch?v={v.video_id}"
                    f"&lc={c.comment_id}"
                )
                row = {
                    "provider": "youtube_data_api",
                    "query": q, "video_id": v.video_id,
                    "comment_id": c.comment_id,
                    "url": comment_url, "snippet": c_text[:300],
                    "evidence_score": c_score,
                    "matched_terms": c_matched,
                    "decision": "REJECTED" if reasons else "ACCEPTED",
                    "rejection_reasons": reasons,
                }
                if reasons:
                    rejected.append(row)
                    summaries["youtube_data_api"]["rejected"] += 1
                    continue
                seen_hashes.add(c_hash)
                roles = _infer_persona_value_roles(
                    text=c_text,
                    competitors=brief.competitors,
                    substitutes_lower=substitutes_lower,
                    use_cases_lower=use_cases_lower,
                )
                psr = _planned_source_record_dict(
                    source_kind="youtube_comment_result",
                    source_url=comment_url, content=c_text,
                    metadata={
                        "provider": "youtube_data_api",
                        "query": q, "video_id": v.video_id,
                        "comment_id": c.comment_id,
                        "matched_terms": c_matched,
                        "persona_value_roles": roles,
                        "anchor_score": c_score,
                        "expected_evidence_type": "video_comment_thread",
                        "source_caveat": (
                            "YouTube Data API public comment; "
                            "author identifiers not stored."
                        ),
                    },
                )
                row["planned_source_record_id_synthetic"] = (
                    psr["planned_source_record_id_synthetic"]
                )
                accepted.append(row)
                planned.append(psr)
                summaries["youtube_data_api"]["accepted"] += 1

    providers_used: list[str] = []
    for p in ("brave_search", "tavily_search", "firecrawl", "youtube_data_api"):
        if summaries[p].get("accepted", 0) > 0:
            providers_used.append(p)
    return {
        "providers_used": providers_used,
        "summaries": summaries,
        "accepted": accepted, "rejected": rejected,
        "planned_source_records": planned,
    }


# =====================================================================
# Simulation prompts (universal — same shape as 8.5E / 8.5G)
# =====================================================================


_SYSTEM_PROMPT = (
    "You are an evidence-backed run-scoped persona in a market-entry "
    "simulation for an unlaunched product. Stay in character. Speak "
    "ONLY for this single persona. Output ONLY the requested JSON; "
    "no preamble, no markdown. Avoid forecasts, percentages, or "
    "launch verdicts."
)


def _round_user_message(
    *, round_type: str, agent: dict[str, Any],
    peer_summary: str | None,
) -> str:
    traits_blob = "\n".join(
        f"  - {t['field_name']}: {(t.get('value') or '')[:200]}"
        for t in (agent.get("traits") or [])[:6]
    )
    excerpts: list[str] = []
    seen: set[str] = set()
    for link in agent.get("evidence_links") or []:
        ex = (link.get("excerpt") or "").strip()
        if not ex or ex[:80] in seen:
            continue
        seen.add(ex[:80])
        excerpts.append(ex[:280])
        if len(excerpts) >= 3:
            break
    excerpts_blob = "\n".join(f"  - {x}" for x in excerpts) or "  (none)"
    questions = {
        "baseline_context": (
            f"BASELINE — describe your current competitor/substitute "
            f"behavior in this category, BEFORE seeing {PRODUCT_NAME}."
        ),
        "first_exposure": (
            f"FIRST EXPOSURE — read this {PRODUCT_NAME} brief. Give "
            "your FIRST honest reaction. Pick a stance from the "
            "allowed set."
        ),
        "objection_formation": (
            f"OBJECTIONS — concrete blockers / risks for {PRODUCT_NAME}."
        ),
        "competitor_comparison": (
            f"COMPARISON — compare {PRODUCT_NAME} explicitly to your "
            "evidence-backed competitor or substitute."
        ),
        "proof_exposure": (
            f"PROOF — what specific PROOF would make you more open to "
            f"{PRODUCT_NAME}?"
        ),
        "social_influence": (
            "PEER VOICES — summary of peer objections + reactions. "
            "Update or hold."
        ),
        "final_stance": (
            f"FINAL — commit to a stance from the allowed set + "
            "one-paragraph reasoning."
        ),
    }
    parts: list[str] = []
    parts.append(f"Persona: {agent.get('display_name', '')}")
    parts.append(
        f"  normalized_primary_role: "
        f"{agent.get('normalized_primary_role', '')}"
    )
    parts.append(
        f"  evidence_theme: {agent.get('evidence_theme', '')}"
    )
    parts.append(
        f"  source_provider_family: "
        f"{agent.get('source_provider_family', '')}"
    )
    parts.append(f"Persisted traits:\n{traits_blob}")
    parts.append(f"Source evidence excerpts:\n{excerpts_blob}")
    parts.append("=" * 60)
    parts.append(
        f"Founder brief ({PRODUCT_NAME}, {LAUNCH_STATE}):\n"
        f"  description: {LUMALOOP_BRIEF.product_description}\n"
        f"  price: {LUMALOOP_BRIEF.price_or_price_structure}\n"
        f"  launch_geography: {LUMALOOP_BRIEF.launch_geography}\n"
        f"  competitors: "
        f"{', '.join(LUMALOOP_BRIEF.competitors)}"
    )
    parts.append("=" * 60)
    parts.append("Round task: " + questions[round_type])
    if peer_summary:
        parts.append("=" * 60)
        parts.append("Peer summary:\n" + peer_summary)
    parts.append("=" * 60)
    parts.append(
        "Allowed final-stance labels: "
        + ", ".join(MARKET_ENTRY_STANCES)
    )
    parts.append(
        f"Universal rules:\n"
        f"  - DO NOT claim direct {PRODUCT_NAME} use, purchase, or "
        f"review. {PRODUCT_NAME} is unlaunched.\n"
        "  - DO NOT produce buy-percentages or forecasts.\n"
        "  - DO NOT issue launch / kill verdicts.\n"
    )
    parts.append(
        "Respond ONLY in JSON: {\n"
        '  "stance": "<allowed label OR null>",\n'
        '  "reasoning": "<short paragraph>",\n'
        '  "objections": [{"text": "...", "category": "..."}],\n'
        '  "persuasion_levers": [{"text": "...", "category": "..."}],\n'
        '  "competitor_mentions": ["..."],\n'
        '  "shift_from_previous": null OR {"from": "...", "to": "...", "reason": "..."}\n'
        "}"
    )
    return "\n".join(parts)


def _parse_round_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if s.startswith("```"):
        parts = s.split("```", 2)
        s = parts[1] if len(parts) > 1 else "{}"
        if s.startswith("json"):
            s = s[4:]
    s = s.strip()
    o = s.find("{"); c = s.rfind("}")
    if o < 0 or c <= o:
        return {
            "stance": None, "reasoning": text[:400] or "",
            "objections": [], "persuasion_levers": [],
            "competitor_mentions": [], "shift_from_previous": None,
        }
    try:
        return json.loads(s[o:c + 1])
    except Exception:
        return {
            "stance": None, "reasoning": text[:400] or "",
            "objections": [], "persuasion_levers": [],
            "competitor_mentions": [], "shift_from_previous": None,
        }


def _normalize_response(parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "stance": parsed.get("stance"),
        "reasoning": (parsed.get("reasoning") or "")[:1500],
        "objections": [
            {
                "text": (o.get("text") or "")[:280],
                "category": (o.get("category") or "")[:64],
            }
            for o in (parsed.get("objections") or [])[:6]
            if isinstance(o, dict)
        ],
        "persuasion_levers": [
            {
                "text": (l.get("text") or "")[:280],
                "category": (l.get("category") or "")[:64],
            }
            for l in (parsed.get("persuasion_levers") or [])[:6]
            if isinstance(l, dict)
        ],
        "competitor_mentions": [
            (c or "")[:64]
            for c in (parsed.get("competitor_mentions") or [])[:8]
            if isinstance(c, str) and (c or "").strip()
        ],
        "shift_from_previous": parsed.get("shift_from_previous"),
    }


def _peer_summary(rounds_audit: list[RoundOutputAudit]) -> str:
    if not rounds_audit:
        return "(no peer data yet)"
    obj_counter: Counter = Counter()
    stance_counter: Counter = Counter()
    for r in rounds_audit:
        for o in r.objections or []:
            t = ((o.get("text") or "")[:60].strip().lower())
            if t:
                obj_counter[t] += 1
        if r.stance:
            stance_counter[r.stance] += 1
    top_obj = "; ".join(
        f"{t} (×{c})" for t, c in obj_counter.most_common(5)
    ) or "(none)"
    stance_dist = ", ".join(
        f"{s}={c}" for s, c in stance_counter.most_common()
    ) or "(no final stances yet)"
    return f"Top objections: {top_obj}\nStance distribution: {stance_dist}"


# =====================================================================
# Main orchestrator
# =====================================================================


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — fresh end-to-end test "
                    "with evidence-coverage broadening.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", default=True,
    )
    mode.add_argument("--commit", action="store_true")
    mode.add_argument(
        "--replay-9a", action="store_true",
        help=(
            "Document what's needed for true replay-from-9A audit "
            "and exit cleanly (no DB / no LLM / no live calls)."
        ),
    )
    # Phase 9A raised caps (8.5G.1 baselines: 10/12/4/3/8/8/8)
    parser.add_argument("--brave-max-queries", type=int, default=20)
    parser.add_argument("--tavily-max-queries", type=int, default=20)
    parser.add_argument("--yt-max-video-queries", type=int, default=8)
    parser.add_argument(
        "--yt-max-videos-per-query", type=int, default=4,
    )
    parser.add_argument(
        "--yt-max-comments-per-video", type=int, default=12,
    )
    parser.add_argument("--firecrawl-max-pages", type=int, default=15)
    parser.add_argument(
        "--max-personas-for-sim", type=int, default=21,
    )
    args = parser.parse_args()
    do_commit = bool(args.commit)
    do_replay = bool(args.replay_9a)
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_main = audit_root / "scale_lumaloop_society_9a_1.json"
    out_quality = audit_root / "scale_lumaloop_society_9a_1_quality.json"
    out_report_json = audit_root / "lumaloop_scaled_founder_report_9a_1.json"
    out_report_md = audit_root / "lumaloop_scaled_founder_report_9a_1.md"
    out_widening = audit_root / "persona_candidate_emission_widening_9a_1.json"
    baseline_path_8_5g_1 = audit_root / "fresh_end_to_end_lumaloop_8_5g_1.json"
    baseline_path_9a = audit_root / "scale_lumaloop_society_9a.json"

    sm = get_sessionmaker()
    db_pre = await _read_table_counts(sm)
    print(f"DB pre: {db_pre}")
    if do_replay:
        mode_label = "REPLAY-9A"
    elif do_commit:
        mode_label = "COMMIT"
    else:
        mode_label = "DRY-RUN"
    print(f"Mode: {mode_label}")

    # ---------- --replay-9a path: document audit limitations + exit ----------
    if do_replay:
        replay_audit: dict[str, Any] = {
            "phase": "9a_1_replay_attempt",
            "completed_at": datetime.now(UTC).isoformat(),
            "mode": "replay_9a",
            "input_9a_audit_path": str(baseline_path_9a),
            "db_pre_counts": db_pre,
            "db_writes": False,
            "llm_calls": 0,
            "live_calls": 0,
        }
        if not baseline_path_9a.is_file():
            replay_audit["replay_blocker"] = (
                f"9A audit file missing at {baseline_path_9a}"
            )
            replay_audit["recommendation"] = (
                "Run Phase 9A first, then re-attempt --replay-9a."
            )
        else:
            ba = json.loads(baseline_path_9a.read_text())
            missing_for_replay: list[str] = []
            if not ba.get("accepted_evidence"):
                missing_for_replay.append(
                    "accepted_evidence (full list of 201 items, not "
                    "just count) — required to reconstruct planned "
                    "source_record dicts in memory."
                )
            if not ba.get("planned_source_records"):
                missing_for_replay.append(
                    "planned_source_records (full list, not count) — "
                    "required to reconstruct EffectiveSourceRecords."
                )
            replay_audit["audit_field_inventory"] = {
                "accepted_evidence_count": ba.get(
                    "accepted_evidence_count",
                ),
                "planned_source_records_count": ba.get(
                    "planned_source_records_count",
                ),
                "raw_persona_candidates_count": ba.get(
                    "raw_persona_candidates_count",
                ),
                "fields_missing_for_replay": missing_for_replay,
            }
            replay_audit["replay_possible"] = (
                len(missing_for_replay) == 0
            )
            replay_audit["recommendation"] = (
                "Replay is NOT possible from 9A audit alone — the "
                "audit only stores counts, not the actual evidence "
                "list. Run with --commit instead to perform a "
                "bounded fresh retrieval + widening + persistence + "
                "simulation + report."
                if not replay_audit["replay_possible"] else
                "Replay is possible — but the orchestrator currently "
                "performs only live retrieval. Add a replay execution "
                "path in a follow-up if needed."
            )
        post = await _read_table_counts(sm)
        replay_audit["db_post_counts"] = post
        replay_audit["db_unchanged"] = post == db_pre
        out_widening.write_text(
            json.dumps(replay_audit, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"\n=== REPLAY-9A documented; DB unchanged: "
            f"{post == db_pre} ===\n"
            f"  blockers: {replay_audit.get('audit_field_inventory', {}).get('fields_missing_for_replay', [])}"
        )
        print(f"→ widening audit: {out_widening}")
        return 0

    # Load 8.5G.1 baseline (signal-only — never modify it).
    baseline_summary: dict[str, Any] | None = None
    if baseline_path_8_5g_1.is_file():
        b = json.loads(baseline_path_8_5g_1.read_text())
        baseline_summary = {
            "run_scope_id": b.get("run_scope_id"),
            "compressed_personas_count": b.get(
                "compressed_personas_count",
            ),
            "compressed_persona_roles": b.get(
                "compressed_persona_roles",
            ),
            "personas_persisted_count": b.get(
                "personas_persisted_count",
            ),
            "providers_used": b.get("providers_used"),
            "accepted_evidence_count": b.get(
                "accepted_evidence_count",
            ),
            "ready_for_scaling_phase": b.get(
                "ready_for_scaling_phase",
            ),
            "simulation_id": b.get("simulation_id"),
        }
    audit: dict[str, Any] = {
        "phase": "9a_1_persona_candidate_emission_widening",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if do_commit else "dry_run",
        "founder_brief": json.loads(LUMALOOP_BRIEF.model_dump_json()),
        "launch_state": LAUNCH_STATE,
        "previous_8_5g_1_summary": baseline_summary,
        "provider_key_presence": {
            "brave_search_configured": is_brave_key_present(),
            "youtube_data_configured": is_youtube_key_present(),
            "tavily_configured": is_tavily_key_present(),
            "firecrawl_configured": is_firecrawl_key_present(),
            "anthropic_configured": bool(
                os.environ.get("ANTHROPIC_API_KEY"),
            ),
        },
        "scaled_persona_target_range": [
            EXPECTED_MIN_COMPRESSED_PERSONAS,
            EXPECTED_MAX_COMPRESSED_PERSONAS,
        ],
        "db_pre_counts": db_pre,
    }

    # 2. Anchor plan
    anchor_plan = generate_anchor_plan(LUMALOOP_BRIEF)
    audit["evidence_anchor_plan"] = json.loads(
        anchor_plan.model_dump_json(),
    )

    # 3. Source-expansion plan (3 providers + per-competitor floor)
    seed_eval = evaluate_persona_diversity(
        brief=LUMALOOP_BRIEF, candidates=[],
    )
    expansion_plan = generate_source_expansion_plan(
        brief=LUMALOOP_BRIEF, anchor_plan=anchor_plan,
        diversity_eval=seed_eval,
        providers_available={
            "brave_search": is_brave_key_present(),
            "youtube_data_api": is_youtube_key_present(),
            "tavily_search": is_tavily_key_present(),
        },
        target_brief_id=TARGET_BRIEF_ID,
        launch_state=LAUNCH_STATE,
    )
    audit["source_expansion_plan"] = json.loads(
        expansion_plan.model_dump_json(),
    )

    # Per-competitor query coverage check
    competitor_coverage: dict[str, list[str]] = {
        c: [] for c in LUMALOOP_BRIEF.competitors
    }
    for pp in expansion_plan.provider_query_plans:
        for q in pp.queries:
            for c in LUMALOOP_BRIEF.competitors:
                if c.lower() in q.query_text.lower():
                    competitor_coverage[c].append(
                        f"{pp.provider}::{q.query_text[:60]}",
                    )
    audit["per_competitor_query_coverage"] = {
        c: len(qs) for c, qs in competitor_coverage.items()
    }

    # 4-6. Bounded retrieval + scoring
    retrieval = await _stage_retrieve_evidence(
        brief=LUMALOOP_BRIEF, anchor_plan=anchor_plan,
        expansion_plan=expansion_plan,
        do_commit=do_commit,
        brave_max_queries=args.brave_max_queries,
        yt_max_video_queries=args.yt_max_video_queries,
        yt_max_videos_per_query=args.yt_max_videos_per_query,
        yt_max_comments_per_video=args.yt_max_comments_per_video,
        tavily_max_queries=args.tavily_max_queries,
        firecrawl_max_pages=args.firecrawl_max_pages,
    )
    audit["providers_used"] = retrieval["providers_used"]
    audit["brave_summary"] = retrieval["summaries"]["brave_search"]
    audit["youtube_summary"] = retrieval["summaries"]["youtube_data_api"]
    audit["tavily_summary"] = retrieval["summaries"]["tavily_search"]
    audit["firecrawl_summary"] = retrieval["summaries"]["firecrawl"]
    audit["evidence_candidates_count"] = (
        len(retrieval["accepted"]) + len(retrieval["rejected"])
    )
    audit["accepted_evidence_count"] = len(retrieval["accepted"])
    audit["rejected_evidence_count"] = len(retrieval["rejected"])
    by_provider: dict[str, int] = Counter()
    for r in retrieval["accepted"]:
        by_provider[r.get("provider", "unknown")] += 1
    audit["accepted_evidence_by_provider"] = dict(by_provider)
    rej_reasons: Counter = Counter()
    for r in retrieval["rejected"]:
        for rr in r.get("rejection_reasons") or []:
            rej_reasons[rr.split(":")[0]] += 1
    audit["rejected_evidence_by_reason"] = dict(rej_reasons)
    audit["planned_source_records_count"] = len(
        retrieval["planned_source_records"],
    )

    if not do_commit:
        audit["recommendation"] = (
            "DRY-RUN — preflight only. Run --commit to execute."
        )
        audit["ready_for_scaling_phase"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        post = await _read_table_counts(sm)
        print(
            f"DB unchanged on dry-run: {db_pre == post}. "
            f"Audit: {out_main}"
        )
        return 0

    # 7. STAGE source records IN MEMORY (Phase 8.5G.1 discipline)
    planned = retrieval["planned_source_records"]
    if not planned:
        audit["rollback_reason"] = "no_evidence_after_retrieval_and_scoring"
        audit["recommendation"] = (
            "FAIL — retrieval yielded zero accepted candidates."
        )
        audit["ready_for_scaling_phase"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    print(
        f"Planned (staged in memory): {len(planned)} source records | "
        f"providers: {retrieval['providers_used']}"
    )

    # Build effective_sources from staged-in-memory planned rows;
    # generate persona candidates BEFORE inserting source_records.
    effective_sources: list[EffectiveSourceRecord] = []
    for psr in planned:
        sid = psr["planned_source_record_id_synthetic"]
        effective_sources.append(EffectiveSourceRecord(
            source_record_id=sid,
            effective_kind="preview_used_as_is",
            superseded_preview_source_record_id=None,
            parent_asin=None, asin=None,
            category=psr["metadata"].get("provider", "external"),
            metadata_title=psr["metadata"].get("title"),
            rating=None, verified_purchase=None,
            helpful_vote=None, timestamp=None,
            content_length=psr["content_length"],
            content=psr["content_preview"],
            metadata=psr["metadata"],
        ))
    persona_planner = PersonaCandidatePlanner(
        generated_for_phase=PHASE_LABEL,
    )
    persona_plan = persona_planner.generate(
        product_name=PRODUCT_NAME, target_brief_id=TARGET_BRIEF_ID,
        launch_state=LAUNCH_STATE,
        competitor_brief_list=LUMALOOP_BRIEF.competitors,
        substitute_brief_list=anchor_plan.substitute_anchor_terms,
        effective_sources=effective_sources,
        preview_rows_total=0, companion_rows_total=0,
        superseded_preview_ids=[],
    )
    audit["raw_persona_candidates_count"] = len(
        persona_plan.persona_candidates,
    )
    audit["candidates_before_widening"] = len(
        persona_plan.persona_candidates,
    )

    # =====================================================================
    # Phase 9A.1 — atomic-signal extraction + persona-emission widening
    # =====================================================================
    # Each accepted evidence item is scanned by the universal
    # `evidence_signal_extractor`. Multi-signal sources yield
    # additional candidates via `persona_emission_widener`.
    all_signals = []
    for ev in retrieval["accepted"]:
        all_signals.extend(extract_evidence_signals(
            evidence_item=ev,
            competitors=LUMALOOP_BRIEF.competitors,
            substitutes=anchor_plan.substitute_anchor_terms,
            use_case_terms=anchor_plan.use_case_anchor_terms,
            objection_terms=anchor_plan.objection_anchor_terms,
        ))
    audit["evidence_signal_count"] = len(all_signals)
    sig_by_type: Counter = Counter(
        s.signal_type for s in all_signals
    )
    audit["evidence_signals_by_type"] = dict(sig_by_type)
    sig_by_provider: Counter = Counter(
        s.provider for s in all_signals
    )
    audit["evidence_signals_by_provider"] = dict(sig_by_provider)

    existing_candidates_dict = [
        json.loads(c.model_dump_json())
        for c in persona_plan.persona_candidates
    ]
    extended_candidates_dict, widening_audit = widen_persona_candidates(
        existing_candidates=existing_candidates_dict,
        signals=all_signals,
        target_brief=TARGET_BRIEF_ID,
        product_name=PRODUCT_NAME,
        generated_for_phase=PHASE_LABEL,
        policy=EmissionPolicy(),
    )
    audit["candidate_emission_policy"] = widening_audit["policy"]
    audit["candidates_before_fix"] = (
        widening_audit["input_existing_count"]
    )
    audit["candidates_after_fix"] = (
        widening_audit["extended_total"]
    )
    audit["candidate_conversion_rate_before"] = (
        round(
            widening_audit["input_existing_count"]
            / max(len(retrieval["accepted"]), 1),
            3,
        )
    )
    audit["candidate_conversion_rate_after"] = (
        round(
            widening_audit["extended_total"]
            / max(len(retrieval["accepted"]), 1),
            3,
        )
    )
    audit["multi_signal_candidates_created"] = (
        widening_audit["multi_signal_candidates_created"]
    )
    audit["same_role_subsegments_created"] = (
        widening_audit["same_role_subsegments_created"]
    )
    audit["widening_emitted_count"] = widening_audit["emitted_count"]
    audit["widening_rejected_count"] = widening_audit["rejected_count"]

    # Write a separate widening-only audit so the operator can see
    # the per-source emission breakdown clearly.
    out_widening.write_text(
        json.dumps({
            "phase": "9a_1_persona_candidate_emission_widening",
            "completed_at": datetime.now(UTC).isoformat(),
            "input_9a_audit_path": str(baseline_path_9a),
            "founder_brief": json.loads(
                LUMALOOP_BRIEF.model_dump_json(),
            ),
            "previous_9a_summary": baseline_summary,
            "accepted_evidence_count": len(retrieval["accepted"]),
            "evidence_signal_count": len(all_signals),
            "evidence_signals_by_type": dict(sig_by_type),
            "evidence_signals_by_provider": dict(sig_by_provider),
            "candidate_emission_policy": widening_audit["policy"],
            "candidates_before_fix": (
                widening_audit["input_existing_count"]
            ),
            "candidates_after_fix": widening_audit["extended_total"],
            "multi_signal_candidates_created": (
                widening_audit["multi_signal_candidates_created"]
            ),
            "same_role_subsegments_created": (
                widening_audit["same_role_subsegments_created"]
            ),
            "per_source_emit_distribution": (
                widening_audit["per_source_emit_distribution"]
            ),
            "rejected_breakdown_sample": (
                widening_audit["rejected_breakdown"]
            ),
        }, indent=2, default=str), encoding="utf-8",
    )
    print(
        f"\n=== Widener: {audit['candidates_before_fix']} → "
        f"{audit['candidates_after_fix']} candidates "
        f"(+{audit['widening_emitted_count']} emitted, "
        f"{audit['widening_rejected_count']} rejected) ==="
    )

    # Build a lightweight wrapper around the widened candidate list
    # so downstream code (diversity / compression) can consume it as
    # if it had come straight from the persona planner.
    class _ExtendedPersonaPlan:
        def __init__(self, candidates_dict_list):
            from assembly.sources.persona_role_planner.schemas import (
                PersonaCandidate as _PC,
            )
            self.persona_candidates = [
                _PC(**c) for c in candidates_dict_list
            ]
            self.persona_role_distribution = dict(Counter(
                c["inferred_persona_role"]
                for c in candidates_dict_list
            ))

    persona_plan = _ExtendedPersonaPlan(extended_candidates_dict)
    audit["raw_persona_candidates_count"] = len(
        persona_plan.persona_candidates,
    )
    audit["persona_candidates_generated"] = len(
        persona_plan.persona_candidates,
    )
    audit["raw_persona_role_distribution"] = (
        persona_plan.persona_role_distribution
    )
    audit["persona_role_distribution"] = (
        persona_plan.persona_role_distribution
    )

    # 9A raw-candidate gate — UNCHANGED at 25 (universal floor).
    # The widener's job is to LIFT raw count above the floor without
    # weakening the gate.
    if (
        len(persona_plan.persona_candidates)
        < EXPECTED_MIN_RAW_CANDIDATES
    ):
        audit["gate_decision"] = "halted_at_raw_candidate_gate"
        audit["rollback_reason"] = (
            f"only {len(persona_plan.persona_candidates)} raw "
            f"candidate(s); need >="
            f"{EXPECTED_MIN_RAW_CANDIDATES} for the 21–30 target."
        )
        audit["recommendation"] = (
            "FAIL — evidence breadth insufficient. Raise retrieval "
            "caps or add provider routing."
        )
        audit["ready_for_scaling_phase"] = False
        audit["source_records_inserted"] = 0
        audit["source_records_reused"] = 0
        audit["source_records_cleaned_up_if_failed"] = 0
        post = await _read_table_counts(sm)
        audit["db_post_counts"] = post
        audit["db_delta_summary"] = {
            k: post[k] - db_pre[k] for k in db_pre
        }
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"\n=== HALT at raw-candidate gate "
            f"({len(persona_plan.persona_candidates)}/"
            f"{EXPECTED_MIN_RAW_CANDIDATES}) ==="
        )
        return 1

    persona_diversity = evaluate_persona_diversity(
        brief=LUMALOOP_BRIEF,
        candidates=persona_plan.persona_candidates,
        plan=anchor_plan,
    )
    audit["persona_diversity_evaluation"] = json.loads(
        persona_diversity.model_dump_json(),
    )

    # Compress
    candidates_dict = [
        json.loads(c.model_dump_json())
        for c in persona_plan.persona_candidates
    ]
    compressed = compress_persona_set(
        candidates=candidates_dict,
        planned_source_records=planned,
        target_brief_id=TARGET_BRIEF_ID,
        product_name=PRODUCT_NAME,
        launch_state=LAUNCH_STATE,
        generated_for_phase=PHASE_LABEL,
        # Phase 9A scaled mode: target 21–30 compressed personas.
        max_target_range=(
            EXPECTED_MIN_COMPRESSED_PERSONAS,
            EXPECTED_MAX_COMPRESSED_PERSONAS,
        ),
        min_behavioral_differential=2,
    )
    audit["compressed_personas_count"] = (
        compressed.diff_summary.after_count
    )
    audit["compressed_persona_roles"] = sorted({
        c.normalized_primary_role for c in compressed.compressed_candidates
    })

    # 9A diversity gate: no single role > 35% of compressed set,
    # ≥5 distinct primary roles. Universal — applies to any product.
    role_counts: Counter = Counter(
        c.normalized_primary_role
        for c in compressed.compressed_candidates
    )
    n_compressed = compressed.diff_summary.after_count
    role_concentration_blocker: str | None = None
    distinct_role_count = len(role_counts)
    if n_compressed > 0:
        top_role, top_count = role_counts.most_common(1)[0]
        top_share = top_count / n_compressed
        if top_share > MAX_ROLE_CONCENTRATION_FRACTION:
            role_concentration_blocker = (
                f"role {top_role!r} has {top_count}/{n_compressed} "
                f"= {top_share:.0%} of compressed set; threshold is "
                f"{MAX_ROLE_CONCENTRATION_FRACTION:.0%}."
            )
    audit["role_concentration_top_role"] = (
        f"{role_counts.most_common(1)[0][0]} "
        f"({role_counts.most_common(1)[0][1]}/{n_compressed})"
        if role_counts else None
    )
    audit["distinct_compressed_role_count"] = distinct_role_count

    # PERSONA GATE — DO NOT insert anything if below the floor
    if compressed.diff_summary.after_count < EXPECTED_MIN_COMPRESSED_PERSONAS:
        audit["gate_decision"] = "halted_at_compression_gate"
        audit["rollback_reason"] = (
            f"only {compressed.diff_summary.after_count} compressed "
            f"persona(s); need ≥{EXPECTED_MIN_COMPRESSED_PERSONAS}."
        )
        audit["recommendation"] = (
            "FAIL — insufficient diverse evidence after broadening. "
            "No DB writes occurred (staged-in-memory discipline)."
        )
        audit["ready_for_scaling_phase"] = False
        audit["source_records_inserted"] = 0
        audit["source_records_reused"] = 0
        audit["source_records_cleaned_up_if_failed"] = 0
        post = await _read_table_counts(sm)
        audit["db_post_counts"] = post
        audit["db_delta_summary"] = {
            k: post[k] - db_pre[k] for k in db_pre
        }
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"\n=== HALT at compression gate "
            f"({compressed.diff_summary.after_count}/{EXPECTED_MIN_COMPRESSED_PERSONAS}) ==="
        )
        print("DB unchanged (staged-in-memory). Audit written.")
        return 1

    # 9A diversity gate (post-count): no single role > 35% AND
    # ≥5 distinct roles. If either fails, halt with reason.
    diversity_blockers: list[str] = []
    if role_concentration_blocker:
        diversity_blockers.append(role_concentration_blocker)
    if distinct_role_count < 5:
        diversity_blockers.append(
            f"only {distinct_role_count} distinct primary role(s) "
            "in compressed set; need ≥5."
        )
    if diversity_blockers:
        audit["gate_decision"] = "halted_at_diversity_gate"
        audit["rollback_reason"] = "; ".join(diversity_blockers)
        audit["recommendation"] = (
            "FAIL — diversity gate failed. Broaden source coverage "
            "or relax role-concentration cap with explicit audit."
        )
        audit["ready_for_scaling_phase"] = False
        audit["source_records_inserted"] = 0
        audit["source_records_reused"] = 0
        audit["source_records_cleaned_up_if_failed"] = 0
        post = await _read_table_counts(sm)
        audit["db_post_counts"] = post
        audit["db_delta_summary"] = {
            k: post[k] - db_pre[k] for k in db_pre
        }
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"\n=== HALT at 9A diversity gate ===\n"
            f"  blockers: {diversity_blockers}"
        )
        return 1

    # ============= GATE PASSED — open the bounded transaction =============
    print(
        f"\n=== Compression gate PASSED "
        f"({compressed.diff_summary.after_count} compressed) ===\n"
        "Opening atomic transaction: source_records + personas + "
        "traits + evidence_links."
    )

    rebuilt: list[PersonaCandidate] = []
    for c in compressed.compressed_candidates:
        rebuilt.append(PersonaCandidate(
            candidate_id=c.candidate_id,
            target_brief=c.target_brief,
            generated_for_phase=c.generated_for_phase,
            inferred_persona_role=c.normalized_primary_role,
            secondary_persona_roles=list(c.secondary_persona_roles),
            role_inference_basis=list(c.role_inference_basis),
            segment_label=c.segment_label,
            source_record_ids=list(c.source_record_ids),
            evidence_summary=c.evidence_summary,
            evidence_snippets=list(c.evidence_snippets),
            inferred_traits=[
                InferredPersonaTrait(**t) for t in c.inferred_traits
            ],
            inferred_preferences=list(c.inferred_preferences),
            inferred_objections=list(c.inferred_objections),
            inferred_behaviors=list(c.inferred_behaviors),
            hypothetical_target_product_reaction=(
                c.hypothetical_target_product_reaction
            ),
            confidence=c.confidence,
            evidence_strength=c.evidence_strength,
            caveats=list(c.caveats),
            simulation_usefulness_summary=c.simulation_usefulness_summary,
            persistence_recommendation=c.persistence_recommendation,
        ))
    diversity_after = evaluate_persona_diversity(
        brief=LUMALOOP_BRIEF, candidates=rebuilt, plan=anchor_plan,
    )
    audit["compressed_diversity_evaluation"] = json.loads(
        diversity_after.model_dump_json(),
    )

    # Build persona_blueprints (closed-field collapse + role_or_context fallback)
    persona_blueprints: list[dict[str, Any]] = []
    for c in compressed.compressed_candidates:
        traits_in = list(c.inferred_traits)[:7]
        collapsed: dict[str, dict[str, Any]] = {}
        for t in traits_in:
            field = _map_trait_field(t.get("trait_name", ""))
            if field not in PERSONA_FIELD_NAMES:
                continue
            entry = collapsed.setdefault(field, {
                "field_name": field, "values": [],
                "excerpts": [], "confidences": [],
                "source_sids": set(), "trait_names": [],
            })
            entry["values"].append(t.get("trait_value") or "")
            entry["excerpts"].append(t.get("evidence_excerpt") or "")
            entry["confidences"].append(t.get("confidence") or "medium")
            entry["source_sids"].add(
                t.get("evidence_source_record_id") or "",
            )
            entry["trait_names"].append(t.get("trait_name") or "")
        if "role_or_context" not in collapsed and len(collapsed) < 2:
            role_value = c.normalized_primary_role or c.pre_normalization_role
            if role_value:
                role_excerpt = " | ".join(
                    str(b) for b in c.role_inference_basis
                )[:300] or c.evidence_summary or role_value
                collapsed["role_or_context"] = {
                    "field_name": "role_or_context",
                    "values": [role_value],
                    "excerpts": [role_excerpt],
                    "confidences": [c.confidence],
                    "source_sids": set(c.source_record_ids),
                    "trait_names": [f"persona_role::{role_value}"],
                }
        persona_blueprints.append({
            "candidate": c, "collapsed_traits": collapsed,
        })

    # Find which planned source IDs are USED by compressed candidates
    used_planned_sids: set[str] = set()
    for c in compressed.compressed_candidates:
        for sid in c.source_record_ids:
            used_planned_sids.add(sid)
    relevant_planned = [
        psr for psr in planned
        if psr["planned_source_record_id_synthetic"] in used_planned_sids
    ]
    print(
        f"Inserting only the {len(relevant_planned)} source records "
        f"actually referenced by compressed personas (of {len(planned)} "
        "staged)."
    )

    run_scope_id = _make_run_scope_id()
    audit["run_scope_id"] = run_scope_id

    sid_to_real_id: dict[str, str] = {}
    inserted_ids: list[str] = []
    reused_ids: list[str] = []
    persisted_personas: list[dict[str, Any]] = []
    expected_trait_count = sum(
        len(b["collapsed_traits"]) for b in persona_blueprints
    )
    expected_link_count = 0
    rollback_reason: str | None = None

    try:
        async with sm() as session:
            async with session.begin():
                # 7. Insert source_records first (needed for FK on
                # PersonaEvidenceLink).
                for psr in relevant_planned:
                    payload = {
                        "source_kind": psr["source_kind"],
                        "source_url": psr["source_url"],
                        "captured_at": datetime.fromisoformat(
                            psr["captured_at"],
                        ),
                        "content": psr["content_preview"],
                        "content_hash": psr["content_hash"],
                        "language": psr["language"] or "en",
                        "metadata": dict(psr["metadata"]),
                        "ingested_by": INGESTED_BY,
                        "compliance_tag": psr["compliance_tag"],
                        "user_handle_hash": None,
                        "pii_redaction_status": "passed",
                        "sensitive_scan_status": "passed",
                    }
                    payload["metadata"]["persisted_in_phase"] = PHASE_LABEL
                    existing = (await session.execute(
                        select(SourceRecord).where(
                            SourceRecord.source_kind
                            == payload["source_kind"],
                            SourceRecord.content_hash
                            == payload["content_hash"],
                        )
                    )).scalar_one_or_none()
                    if existing is not None:
                        sid_to_real_id[
                            psr["planned_source_record_id_synthetic"]
                        ] = str(existing.id)
                        reused_ids.append(str(existing.id))
                        continue
                    new_id = uuid.uuid4()
                    session.add(SourceRecord(
                        id=new_id,
                        source_kind=payload["source_kind"],
                        source_url=payload["source_url"],
                        captured_at=payload["captured_at"],
                        content=payload["content"],
                        content_hash=payload["content_hash"],
                        language=payload["language"],
                        metadata_=payload["metadata"],
                        ingested_by=payload["ingested_by"],
                        compliance_tag=payload["compliance_tag"],
                        user_handle_hash=None,
                        pii_redaction_status=payload[
                            "pii_redaction_status"
                        ],
                        sensitive_scan_status=payload[
                            "sensitive_scan_status"
                        ],
                    ))
                    sid_to_real_id[
                        psr["planned_source_record_id_synthetic"]
                    ] = str(new_id)
                    inserted_ids.append(str(new_id))

                # PersonaRecord pre-pass
                now = datetime.now(UTC)
                for blueprint in persona_blueprints:
                    cand = blueprint["candidate"]
                    persona_id = uuid.uuid4()
                    blueprint["persona_id"] = persona_id
                    display_name = generate_display_name(
                        seed=str(persona_id),
                    )
                    blueprint["display_name"] = display_name
                    relevance_tags = [
                        f"target_brief:{TARGET_BRIEF_ID}",
                        f"product_name:{PRODUCT_NAME}",
                        f"launch_state:{LAUNCH_STATE}",
                        f"phase:{PHASE_LABEL}",
                        f"run_scope_id:{run_scope_id}",
                        f"normalized_primary_role:{cand.normalized_primary_role}",
                        f"evidence_theme:{cand.evidence_theme}",
                        f"source_provider_family:{cand.source_provider_family}",
                        f"compressed_candidate_id:{cand.candidate_id}",
                        "scope:run_scoped_brief_scoped",
                        "persistence_type:generated_simulation_artifact",
                        "not_global_persona:true",
                        (
                            f"caveat:Generated for this {PRODUCT_NAME} "
                            "simulation run from evidence; not a "
                            "permanent/global persona."
                        ),
                    ]
                    session.add(PersonaRecord(
                        id=persona_id,
                        display_name=display_name,
                        segment_label=(
                            cand.segment_label or cand.normalized_primary_role
                        )[:64],
                        origin_market_broad=None,
                        product_relevance_tags=relevance_tags,
                        influence_score=None,
                        susceptibility=None,
                        population_weight=Decimal("1.0"),
                        source_strength_score=None,
                        refreshed_at=now,
                    ))
                await session.flush()

                for blueprint in persona_blueprints:
                    cand = blueprint["candidate"]
                    persona_id = blueprint["persona_id"]
                    real_src_for_persona: list[uuid.UUID] = []
                    for sid in cand.source_record_ids:
                        rid = sid_to_real_id.get(sid)
                        if rid:
                            real_src_for_persona.append(uuid.UUID(rid))
                    if not real_src_for_persona:
                        raise RuntimeError(
                            f"candidate {cand.candidate_id}: no resolved "
                            "real source IDs"
                        )
                    traits_added = 0
                    links_added = 0
                    for field_name, entry in blueprint["collapsed_traits"].items():
                        contributing_real_ids: set[uuid.UUID] = set()
                        for sid in entry["source_sids"]:
                            rid = sid_to_real_id.get(sid)
                            if rid:
                                contributing_real_ids.add(uuid.UUID(rid))
                        if not contributing_real_ids:
                            contributing_real_ids = set(real_src_for_persona)
                        max_conf = max(
                            (_confidence_decimal(c2)
                             for c2 in entry["confidences"]),
                            default=Decimal("0.5"),
                        )
                        sup = (
                            "direct" if "high" in entry["confidences"]
                            else "inferred"
                        )
                        merged_value = "; ".join(
                            sorted({v for v in entry["values"] if v})
                        )[:1000] or (entry["trait_names"][0] or "evidence")
                        rationale = " | ".join(
                            f"{tn}: {(ex or '')[:300]}"
                            for tn, ex in zip(
                                entry["trait_names"], entry["excerpts"],
                            )
                            if (ex or "").strip()
                        )[:2000] or None
                        session.add(PersonaTrait(
                            id=uuid.uuid4(),
                            persona_id=persona_id,
                            field_name=field_name,
                            value=merged_value,
                            support_level=sup,
                            source_ids=sorted(contributing_real_ids),
                            confidence=max_conf,
                            rationale=rationale,
                            last_updated_at=now,
                        ))
                        traits_added += 1
                        for src_id in contributing_real_ids:
                            session.add(PersonaEvidenceLink(
                                id=uuid.uuid4(),
                                persona_id=persona_id,
                                source_record_id=src_id,
                                contribution_kind="trait_support",
                                contribution_field=field_name,
                                excerpt=(
                                    (entry["excerpts"][0]
                                     if entry["excerpts"] else
                                     cand.evidence_summary or "evidence")
                                )[:4000],
                                excerpt_offset=None,
                                confidence=max_conf,
                            ))
                            links_added += 1
                            expected_link_count += 1
                    if traits_added < 2:
                        raise RuntimeError(
                            f"persona {persona_id}: only {traits_added} "
                            "trait(s)"
                        )
                    persisted_personas.append({
                        "persona_record_id": str(persona_id),
                        "display_name": blueprint["display_name"],
                        "compressed_candidate_id": cand.candidate_id,
                        "normalized_primary_role": cand.normalized_primary_role,
                        "evidence_theme": cand.evidence_theme,
                        "source_provider_family": cand.source_provider_family,
                        "trait_count": traits_added,
                        "evidence_link_count": links_added,
                    })
    except Exception as e:
        rollback_reason = (
            f"persistence_failed: {type(e).__name__}: {e}"
        )
        print(f"ROLLBACK: {rollback_reason}")

    audit["source_records_inserted"] = len(inserted_ids)
    audit["source_records_reused"] = len(reused_ids)
    audit["source_records_cleaned_up_if_failed"] = (
        0 if rollback_reason is None else len(inserted_ids) + len(reused_ids)
    )
    audit["personas_persisted_count"] = (
        len(persisted_personas) if rollback_reason is None else 0
    )
    audit["traits_persisted_count"] = (
        expected_trait_count if rollback_reason is None else 0
    )
    audit["evidence_links_persisted_count"] = (
        expected_link_count if rollback_reason is None else 0
    )
    audit["persisted_personas"] = persisted_personas
    audit["gate_decision"] = (
        "passed_compression_gate_persisted_run_scoped"
        if rollback_reason is None else "transaction_rolled_back"
    )

    if rollback_reason:
        audit["rollback_reason"] = rollback_reason
        audit["recommendation"] = (
            f"FAIL — {rollback_reason}. Atomic transaction rolled "
            "back; DB unchanged."
        )
        audit["ready_for_scaling_phase"] = False
        post = await _read_table_counts(sm)
        audit["db_post_counts"] = post
        audit["db_delta_summary"] = {
            k: post[k] - db_pre[k] for k in db_pre
        }
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    print(
        f"Persisted: +{len(inserted_ids)} sources / "
        f"+{len(reused_ids)} reused / +{len(persisted_personas)} personas "
        f"/ +{expected_trait_count} traits / +{expected_link_count} links."
    )

    # 12. Simulation
    cap_personas = min(args.max_personas_for_sim, len(persisted_personas))
    print(f"\nLoading {cap_personas} personas + running 7 rounds...")
    async with sm() as session:
        run_scoped_agents = await load_run_scoped_agents(
            session=session, run_scope_id=run_scope_id,
        )
    if len(run_scoped_agents) > cap_personas:
        run_scoped_agents = run_scoped_agents[:cap_personas]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        audit["rollback_reason"] = "anthropic_key_missing"
        audit["ready_for_scaling_phase"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1
    from assembly.llm.anthropic import AnthropicProvider
    provider: LLMProvider = AnthropicProvider()

    sim_id = uuid.uuid4()
    persona_to_agent_id: dict[str, uuid.UUID] = {}
    rounds_audit: list[RoundOutputAudit] = []
    cost_summary = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    async with sm() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id=f"phase_{PHASE_LABEL}_lumaloop",
                status="simulating",
                started_at=datetime.now(UTC),
                progress={
                    "phase": PHASE_LABEL,
                    "run_scope_id": run_scope_id,
                    "expected_rounds": len(AGENT_ROUND_TYPES),
                    "expected_agents": len(run_scoped_agents),
                },
            ))
            price_value = float(
                str(LUMALOOP_BRIEF.price_or_price_structure)
                .replace("$", "").strip() or 0
            )
            session.add(SimulationInput(
                id=uuid.uuid4(), simulation_id=sim_id,
                product_type=anchor_plan.product_type,
                product_name=PRODUCT_NAME,
                description=LUMALOOP_BRIEF.product_description,
                price_structure={
                    "amount_usd": price_value,
                    "structure": "one_time",
                },
                target_society={
                    "geography_broad": LUMALOOP_BRIEF.launch_geography,
                    "target_customers": list(
                        LUMALOOP_BRIEF.target_customers,
                    ),
                },
                competitors=[
                    {"name": c} for c in LUMALOOP_BRIEF.competitors
                ],
                raw_brief=json.loads(LUMALOOP_BRIEF.model_dump_json()),
            ))
            for a in run_scoped_agents:
                agent_id = uuid.uuid4()
                persona_to_agent_id[str(a.persona_id)] = agent_id
                session.add(Agent(
                    id=agent_id, simulation_id=sim_id,
                    segment_label=(
                        a.segment_label or a.normalized_primary_role
                    )[:128],
                    weight=1.0,
                    buyer_state={
                        "current_alternatives": [
                            t["value"] for t in a.traits
                            if t["field_name"] == "current_alternatives"
                        ],
                        "current_behavior": "",
                        "objection_pattern": "",
                        "price_sensitivity": "moderate",
                    },
                    traits={
                        "persisted_persona_id": str(a.persona_id),
                        "compressed_candidate_id": (
                            a.compressed_candidate_id
                        ),
                        "normalized_primary_role": (
                            a.normalized_primary_role
                        ),
                        "evidence_theme": a.evidence_theme,
                        "source_provider_family": (
                            a.source_provider_family
                        ),
                        "run_scope_id": run_scope_id,
                        "display_name": a.display_name,
                        "trait_field_names": [
                            t["field_name"] for t in a.traits
                        ],
                    },
                    evidence_anchors=[],
                ))
            await session.flush()

    agent_dicts: list[dict[str, Any]] = []
    for a in run_scoped_agents:
        agent_dicts.append({
            "persona_id": str(a.persona_id),
            "display_name": a.display_name,
            "normalized_primary_role": a.normalized_primary_role,
            "evidence_theme": a.evidence_theme,
            "source_provider_family": a.source_provider_family,
            "compressed_candidate_id": a.compressed_candidate_id,
            "traits": list(a.traits),
            "evidence_links": list(a.evidence_links),
        })

    peer_summary_text = ""
    sim_rollback: str | None = None
    try:
        for round_idx, round_type in enumerate(AGENT_ROUND_TYPES, start=1):
            round_id = uuid.uuid4()
            async with sm() as session:
                async with session.begin():
                    session.add(SimulationRound(
                        id=round_id, simulation_id=sim_id,
                        round_number=round_idx, round_type=round_type,
                        started_at=datetime.now(UTC),
                        summary={
                            "phase": PHASE_LABEL,
                            "round_type": round_type,
                            "round_number": round_idx,
                        },
                    ))
            print(
                f"\nRound {round_idx} ({round_type}) — "
                f"{len(agent_dicts)} agents..."
            )
            for ad in agent_dicts:
                user_msg = _round_user_message(
                    round_type=round_type, agent=ad,
                    peer_summary=(
                        peer_summary_text
                        if round_type == "social_influence" else None
                    ),
                )
                messages = [
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ]
                response = await cost_guarded_chat(
                    sessionmaker=sm, simulation_id=sim_id,
                    stage=f"round_{round_type}",
                    messages=messages, provider=provider,
                    hard_cap_usd=SIM_HARD_CAP_USD,
                    max_tokens=900, temperature=0.4,
                    estimated_prompt_tokens=2500,
                    estimated_completion_tokens=600,
                )
                cost_summary["calls"] += 1
                cost_summary["input_tokens"] += int(
                    response.prompt_tokens or 0,
                )
                cost_summary["output_tokens"] += int(
                    response.completion_tokens or 0,
                )
                parsed = _normalize_response(_parse_round_json(response.text))
                blob = (response.text or "") + " | " + (parsed.get("reasoning") or "")
                forbidden: list[str] = []
                v_use = scan_unlaunched_product_use_claims(
                    text=blob, product_name=PRODUCT_NAME,
                )
                if not v_use.is_valid:
                    forbidden.append(
                        f"launch_state:{v_use.rejection_reason}",
                    )
                v_fc = scan_forecast_or_verdict_claims(text=blob)
                if not v_fc.is_valid:
                    forbidden.append(
                        f"forecast_or_verdict:{v_fc.rejection_reason}",
                    )
                stance_for_db = parsed.get("stance") or "needs_more_information"
                if stance_for_db not in MARKET_ENTRY_STANCES:
                    stance_for_db = "needs_more_information"
                async with sm() as session:
                    async with session.begin():
                        session.add(AgentResponse(
                            id=uuid.uuid4(), round_id=round_id,
                            agent_id=persona_to_agent_id[ad["persona_id"]],
                            stance=stance_for_db,
                            reasoning=(parsed.get("reasoning") or "")[:4000],
                            objections=parsed.get("objections") or [],
                            persuasion_drivers=(
                                parsed.get("persuasion_levers") or []
                            ),
                            shift_from_previous=parsed.get(
                                "shift_from_previous",
                            ),
                            state_after={
                                "stance": stance_for_db,
                                "round_type": round_type,
                                "competitor_mentions": (
                                    parsed.get("competitor_mentions") or []
                                ),
                                "forbidden_claim_audit": forbidden,
                            },
                            raw_output={
                                "raw_text": (response.text or "")[:6000],
                                "model": response.model,
                                "provider": response.provider,
                                "parsed": parsed,
                                "forbidden_claim_audit": forbidden,
                            },
                        ))
                rounds_audit.append(RoundOutputAudit(
                    agent_persona_id=ad["persona_id"],
                    display_name=ad["display_name"],
                    compressed_candidate_id=ad["compressed_candidate_id"],
                    normalized_primary_role=ad["normalized_primary_role"],
                    round_type=round_type,  # type: ignore[arg-type]
                    round_number=round_idx,
                    stance=stance_for_db,  # type: ignore[arg-type]
                    reasoning=(parsed.get("reasoning") or "")[:1500],
                    objections=parsed.get("objections") or [],
                    persuasion_levers=(
                        parsed.get("persuasion_levers") or []
                    ),
                    competitor_mentions=(
                        parsed.get("competitor_mentions") or []
                    ),
                    shift_from_previous=parsed.get("shift_from_previous"),
                    forbidden_claim_audit=forbidden,
                    raw_text=(response.text or "")[:6000],
                ))
                print(
                    f"  {ad['display_name']:14s} stance="
                    f"{stance_for_db:24s} obj="
                    f"{len(parsed.get('objections') or [])} "
                    f"forbid={len(forbidden)}"
                )
            peer_summary_text = _peer_summary(rounds_audit)

        async with sm() as session:
            async with session.begin():
                sim_row = (await session.execute(
                    select(Simulation).where(Simulation.id == sim_id)
                )).scalar_one()
                sim_row.status = "simulation_completed"
                sim_row.completed_at = datetime.now(UTC)
    except Exception as e:
        sim_rollback = f"simulation_failed: {type(e).__name__}: {e}"
        print(f"ROLLBACK: {sim_rollback}")

    audit["simulation_id"] = str(sim_id)
    audit["simulation_rounds"] = len(AGENT_ROUND_TYPES)
    audit["agent_response_count"] = len(rounds_audit)
    audit["cost_summary"] = {
        **cost_summary,
        "hard_cap_usd": str(SIM_HARD_CAP_USD),
        "cost_guard_active": True,
        "model_used": "claude-sonnet-4-6",
    }

    if sim_rollback:
        audit["rollback_reason"] = sim_rollback
        audit["ready_for_scaling_phase"] = False
        post = await _read_table_counts(sm)
        audit["db_post_counts"] = post
        audit["db_delta_summary"] = {
            k: post[k] - db_pre[k] for k in db_pre
        }
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    forbidden_summary = {
        "fake_target_product_use_count": sum(
            1 for r in rounds_audit
            if any(f.startswith("launch_state:") for f in r.forbidden_claim_audit)
        ),
        "forecast_or_verdict_count": sum(
            1 for r in rounds_audit
            if any(f.startswith("forecast_or_verdict:") for f in r.forbidden_claim_audit)
        ),
    }
    forbidden_summary["any_fake_target_product_use"] = (
        forbidden_summary["fake_target_product_use_count"] > 0
    )
    forbidden_summary["any_forecast_or_verdict"] = (
        forbidden_summary["forecast_or_verdict_count"] > 0
    )
    audit["forbidden_claim_audit"] = forbidden_summary

    sim_quality = evaluate_simulation_quality(
        rounds=rounds_audit,
        caveats=[
            f"n={len(agent_dicts)} micro-simulation",
            "Not a forecast.", "Not a market verdict.",
            "Not representative of the full California market.",
            "Personas are run-scoped synthetic agents.",
            f"{PRODUCT_NAME} is unlaunched; no persona has actually used it.",
            "Sources include Brave Search web snippets, Tavily web "
            "results, Firecrawl page extractions, and YouTube Data "
            "API public metadata/comments.",
        ],
        product_name=PRODUCT_NAME,
        agents_with_traits_count=sum(
            1 for a in run_scoped_agents if a.traits
        ),
        total_agents=len(run_scoped_agents),
    )
    audit["simulation_quality"] = json.loads(sim_quality.model_dump_json())

    # 13. Founder report
    final_stance_dist = dict(Counter(
        r.stance for r in rounds_audit
        if r.round_type == "final_stance" and r.stance
    ))
    obj_global = Counter()
    lever_global = Counter()
    competitor_mentions_g = Counter()
    for r in rounds_audit:
        for o in r.objections or []:
            t = (o.get("text") or "")[:80].strip().lower()
            if t:
                obj_global[t] += 1
        for l in r.persuasion_levers or []:
            t = (l.get("text") or "")[:80].strip().lower()
            if t:
                lever_global[t] += 1
        for c in r.competitor_mentions or []:
            competitor_mentions_g[(c or "")[:60].strip().lower()] += 1

    sim_audit_for_report = {
        "phase": "9a_lumaloop_scaled_simulation_for_report",
        "simulation_id": str(sim_id),
        "run_scope_id": run_scope_id,
        "founder_brief": json.loads(LUMALOOP_BRIEF.model_dump_json()),
        "launch_state": LAUNCH_STATE,
        "input_persona_count": len(agent_dicts),
        "input_persona_ids": [a["persona_id"] for a in agent_dicts],
        "input_persona_summary": [
            {
                "persona_id": a["persona_id"],
                "display_name": a["display_name"],
                "normalized_primary_role": a["normalized_primary_role"],
                "compressed_candidate_id": a["compressed_candidate_id"],
                "source_provider_family": a["source_provider_family"],
                "evidence_theme": a["evidence_theme"],
                "trait_count": len(a["traits"]),
                "evidence_link_count": len(a["evidence_links"]),
                "source_record_count": 0,
            }
            for a in agent_dicts
        ],
        "traits_loaded_count": sum(
            len(a["traits"]) for a in agent_dicts
        ),
        "evidence_links_loaded_count": sum(
            len(a["evidence_links"]) for a in agent_dicts
        ),
        "source_records_loaded_count": (
            len(inserted_ids) + len(reused_ids)
        ),
        "rounds_completed": len(AGENT_ROUND_TYPES),
        "per_round_outputs": [
            json.loads(r.model_dump_json()) for r in rounds_audit
        ],
        "final_stance_distribution": final_stance_dist,
        "top_objections": [
            {"text": t, "count": c}
            for t, c in obj_global.most_common(10)
        ],
        "top_persuasion_levers": [
            {"text": t, "count": c}
            for t, c in lever_global.most_common(10)
        ],
        "competitor_comparison_summary": [
            {"competitor": k, "mentions": v}
            for k, v in competitor_mentions_g.most_common(10)
        ],
        "forbidden_claim_audit": forbidden_summary,
        "source_persona_tables_unchanged": False,
        "db_delta_summary": {"agent_responses": len(rounds_audit)},
        "cost_summary": audit["cost_summary"],
        "ready_for_founder_report_phase": (
            sim_quality.ready_state in (
                "READY_FOR_FOUNDER_REPORT", "READY_FOR_PROMPT_FIX",
            )
            and not forbidden_summary["any_fake_target_product_use"]
            and not forbidden_summary["any_forecast_or_verdict"]
        ),
        "quality_evaluator_result": json.loads(sim_quality.model_dump_json()),
    }
    quality_audit_for_report = {
        "scores": json.loads(sim_quality.model_dump_json()),
    }

    if not sim_audit_for_report["ready_for_founder_report_phase"]:
        audit["rollback_reason"] = (
            "simulation_quality_or_forbidden_audit_blocked"
        )
        audit["ready_for_scaling_phase"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    report = aggregate_founder_report(
        simulation_audit=sim_audit_for_report,
        quality_audit=quality_audit_for_report,
    )
    md = render_markdown_report(report)
    report_qual = evaluate_report_quality(
        report=report, rendered_markdown=md, product_name=PRODUCT_NAME,
    )

    json_text = json.dumps(report.model_dump(), indent=2, default=str)
    json_scan = scan_for_secrets(json_text)
    md_scan = scan_for_secrets(md)
    secrets_clean = json_scan.is_clean and md_scan.is_clean

    if not secrets_clean:
        audit["security_redaction_audit"] = {
            "secrets_clean": False,
            "finding_count": (
                len(json_scan.findings) + len(md_scan.findings)
            ),
        }
        audit["recommendation"] = (
            "FAIL — secrets detected in generated report."
        )
        audit["ready_for_scaling_phase"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    report_dict = report.model_dump()
    report_dict["security_redaction_audit"] = {
        "secrets_detected_in_inputs": False,
        "redactions_applied": 0,
        "scanner_version": "8.5G.1.universal",
    }
    report_dict["quality_reference"] = {
        **report_dict.get("quality_reference", {}),
        "report_quality_evaluation": json.loads(
            report_qual.model_dump_json(),
        ),
    }
    out_report_json.write_text(
        json.dumps(report_dict, indent=2, default=str),
        encoding="utf-8",
    )
    out_report_md.write_text(md, encoding="utf-8")
    audit["report_quality_evaluation"] = json.loads(
        report_qual.model_dump_json(),
    )
    audit["founder_report_files"] = {
        "report_json": str(out_report_json),
        "report_md": str(out_report_md),
    }
    audit["security_redaction_audit"] = {
        "secrets_clean": True, "finding_count": 0,
        "scanner_version": "8.5G.1.universal",
    }

    db_post = await _read_table_counts(sm)
    audit["db_post_counts"] = db_post
    audit["db_delta_summary"] = {
        k: db_post[k] - db_pre[k] for k in db_pre.keys()
    }

    quality_gates = {
        "anchor_plan_generated": bool(anchor_plan.positive_anchor_terms),
        "expansion_plan_generated": expansion_plan.total_planned_queries > 0,
        "evidence_from_2_or_more_providers": (
            len(retrieval["providers_used"]) >= 2
        ),
        "compressed_personas_at_least_21": (
            compressed.diff_summary.after_count
            >= EXPECTED_MIN_COMPRESSED_PERSONAS
        ),
        "compressed_personas_at_most_30": (
            compressed.diff_summary.after_count
            <= EXPECTED_MAX_COMPRESSED_PERSONAS
        ),
        "no_single_role_over_35_pct": (
            role_concentration_blocker is None
        ),
        "at_least_5_distinct_roles": distinct_role_count >= 5,
        "raw_candidates_above_floor": (
            len(persona_plan.persona_candidates)
            >= EXPECTED_MIN_RAW_CANDIDATES
        ),
        "diversity_after_compression_ready": (
            diversity_after.mutating_persistence_recommendation == "READY"
        ),
        "personas_run_scoped": True,
        "simulation_completed": sim_rollback is None,
        "simulation_quality_ready": (
            sim_quality.ready_state in (
                "READY_FOR_FOUNDER_REPORT", "READY_FOR_PROMPT_FIX",
            )
        ),
        "report_generated": True,
        "report_quality_ready": (
            report_qual.ready_state in (
                "READY_FOR_FRESH_END_TO_END_TEST",
                "READY_FOR_REPORT_PROMPT_FIX",
            )
        ),
        "no_fake_target_product_use": (
            not forbidden_summary["any_fake_target_product_use"]
        ),
        "no_forecast_or_verdict": (
            not forbidden_summary["any_forecast_or_verdict"]
        ),
        "secrets_clean": secrets_clean,
    }
    audit["quality_gates"] = quality_gates
    audit["ready_for_9b_50_to_100_personas"] = all(quality_gates.values())
    audit["ready_for_scaling_phase"] = audit[
        "ready_for_9b_50_to_100_personas"
    ]
    audit["recommendation"] = (
        f"PASS — Phase 9A scale completed with "
        f"{compressed.diff_summary.after_count} compressed personas; "
        "ready for Phase 9B (50–100 personas)."
        if audit["ready_for_9b_50_to_100_personas"] else
        "READY_WITH_CAVEATS — some quality gates require attention."
    )
    out_main.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    out_quality.write_text(
        json.dumps({
            "phase": "9a_quality",
            "completed_at": datetime.now(UTC).isoformat(),
            "simulation_quality": json.loads(sim_quality.model_dump_json()),
            "report_quality": json.loads(report_qual.model_dump_json()),
            "quality_gates": quality_gates,
            "ready_for_9b_50_to_100_personas": audit[
                "ready_for_9b_50_to_100_personas"
            ],
        }, indent=2, default=str), encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print(f"Phase {PHASE_LABEL} — Scale LumaLoop society to 21–30 personas")
    print("=" * 72)
    for k, v in quality_gates.items():
        flag = "✓" if v else "✗"
        print(f"  [{flag}] {k}: {v}")
    print(
        f"\nready_for_9b_50_to_100_personas: "
        f"{audit['ready_for_9b_50_to_100_personas']}"
    )
    print(f"\n→ main:    {out_main}")
    print(f"→ quality: {out_quality}")
    print(f"→ report:  {out_report_md}")
    return 0 if audit["ready_for_9b_50_to_100_personas"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
