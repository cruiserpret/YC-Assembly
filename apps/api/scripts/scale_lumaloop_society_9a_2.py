"""Phase 9A.2 — official 21–30 persona LumaLoop scale pass.

Loads the 66 personas persisted by Phase 9A.1, applies the new
universal `_apply_hard_cap_stratified` selector with `hard_max=30`,
persists the kept 30 under a new run_scope_id (reusing the existing
9A.1 SourceRecord rows via content_hash), runs a 7-round simulation
on the 30, and generates the scaled founder report.

Key Phase 9A.2 changes vs 9A.1:
  * Hard cap = 30 enforced via stratified selector (universal,
    drift-tested).
  * NO new retrieval — reuses 9A.1's evidence pool.
  * NO new candidate widening — uses the 66 9A.1 personas directly.
  * NEW run_scope_id `run_9a2_lumaloop_<hash>` so the 9A.1 society
    stays intact.
  * Adds `next_psychology_layer_needed=true` and
    `next_discussion_layer_needed=true` to the audit roadmap hooks.
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

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning,
)
from assembly.sources.founder_report_generator import (
    aggregate_founder_report, evaluate_report_quality,
    render_markdown_report, scan_for_secrets,
)
from assembly.sources.persona_set_compressor.compressor import (
    _apply_hard_cap_stratified,
)
from assembly.sources.persona_set_compressor.schemas import (
    CompressedPersonaCandidate,
)
from assembly.sources.run_scoped_persona_simulation import (
    AGENT_ROUND_TYPES, MARKET_ENTRY_STANCES, RoundOutputAudit,
    evaluate_simulation_quality, load_run_scoped_agents,
    scan_forecast_or_verdict_claims,
    scan_unlaunched_product_use_claims,
)


PHASE_LABEL = "9A.2"
TARGET_BRIEF_ID = "lumaloop"
PRODUCT_NAME = "LumaLoop"
LAUNCH_STATE = "unlaunched"
EXPECTED_MIN_COMPRESSED_PERSONAS = 21
EXPECTED_MAX_COMPRESSED_PERSONAS = 30
HARD_MAX_COMPRESSED = 30
SIM_HARD_CAP_USD = Decimal("8.00")

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
    return "run_9a2_" + hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()[:12]


def _parse_tag_value(
    tags: list[str], key: str, default: str = "",
) -> str:
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


async def _load_9a_1_as_candidates(
    session: AsyncSession,
) -> tuple[
    list[CompressedPersonaCandidate],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    """Return (candidates, persona_meta_by_candidate_id,
    candidates_metadata).

    `persona_meta_by_candidate_id` keys candidate_id → {persona_id,
    trait_rows, link_rows} so the caller can re-emit traits +
    evidence_links pointing at the existing SourceRecords.
    """
    persona_rows = (await session.execute(
        select(PersonaRecord).where(
            PersonaRecord.product_relevance_tags.contains(
                ["phase:9A.1"],
            )
        ).order_by(PersonaRecord.id)
    )).scalars().all()
    if not persona_rows:
        return [], {}, {"input_count": 0}
    persona_ids = [p.id for p in persona_rows]
    trait_rows = (await session.execute(
        select(PersonaTrait)
        .where(PersonaTrait.persona_id.in_(persona_ids))
        .order_by(PersonaTrait.persona_id, PersonaTrait.field_name)
    )).scalars().all()
    link_rows = (await session.execute(
        select(PersonaEvidenceLink)
        .where(PersonaEvidenceLink.persona_id.in_(persona_ids))
        .order_by(
            PersonaEvidenceLink.persona_id,
            PersonaEvidenceLink.contribution_field,
        )
    )).scalars().all()
    traits_by_persona: dict[Any, list[Any]] = {}
    for t in trait_rows:
        traits_by_persona.setdefault(t.persona_id, []).append(t)
    links_by_persona: dict[Any, list[Any]] = {}
    for l in link_rows:
        links_by_persona.setdefault(l.persona_id, []).append(l)

    candidates: list[CompressedPersonaCandidate] = []
    meta: dict[str, dict[str, Any]] = {}
    for p in persona_rows:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        evidence_theme = _parse_tag_value(
            tags, "evidence_theme",
        ) or f"role::{normalized_role}"
        provider = _parse_tag_value(
            tags, "source_provider_family",
        ) or "unknown"
        compressed_candidate_id = _parse_tag_value(
            tags, "compressed_candidate_id",
        ) or f"9a1::{str(p.id)[:8]}"
        traits = traits_by_persona.get(p.id, [])
        links = links_by_persona.get(p.id, [])
        trait_dicts: list[dict[str, Any]] = []
        for t in traits[:7]:
            trait_dicts.append({
                "trait_name": t.field_name,
                "trait_value": t.value or normalized_role,
                "evidence_source_record_id": (
                    str(t.source_ids[0]) if t.source_ids else "unknown"
                ),
                "evidence_excerpt": (t.rationale or "")[:240] or "evidence",
                "confidence": (
                    "high" if float(t.confidence) >= 0.8
                    else "medium" if float(t.confidence) >= 0.5
                    else "low"
                ),
                "caveat": None,
            })
        if len(trait_dicts) < 2:
            trait_dicts.extend([{
                "trait_name": "role_or_context",
                "trait_value": normalized_role,
                "evidence_source_record_id": "synthetic",
                "evidence_excerpt": (
                    f"persona_role::{normalized_role} "
                    "(rebuilt from 9A.1)"
                ),
                "confidence": "medium",
                "caveat": None,
            }] * max(0, 2 - len(trait_dicts)))
        snippets: list[str] = []
        for l in links[:5]:
            ex = (l.excerpt or "")[:300]
            if ex:
                snippets.append(ex)
        if not snippets:
            snippets = [
                f"persona_role::{normalized_role} (rebuilt from 9A.1)"
            ]
        src_ids = sorted({
            str(l.source_record_id) for l in links
        }) or ["unknown"]
        avg_conf = sum(
            float(t.confidence) for t in traits
        ) / max(len(traits), 1)
        qs = round(7.0 + 3.0 * avg_conf, 3)
        cand = CompressedPersonaCandidate(
            candidate_id=compressed_candidate_id,
            target_brief=TARGET_BRIEF_ID,
            generated_for_phase="9A.1",
            pre_normalization_role=normalized_role,
            normalized_primary_role=normalized_role,
            secondary_persona_roles=[],
            role_inference_basis=[
                "rebuilt from 9A.1 PersonaRecord product_relevance_tags",
            ],
            segment_label=p.segment_label or normalized_role,
            source_record_ids=src_ids,
            evidence_summary=(
                f"Rebuilt from 9A.1 persona {str(p.id)[:8]} "
                f"({len(traits)} traits, {len(links)} links)."
            ),
            evidence_snippets=snippets,
            evidence_theme=evidence_theme,
            source_provider_family=provider,
            inferred_traits=trait_dicts,
            inferred_preferences=[],
            inferred_objections=[],
            inferred_behaviors=[],
            hypothetical_target_product_reaction=(
                f"This persona would compare {PRODUCT_NAME} to its "
                f"{normalized_role.replace('_', ' ')} context."
            ),
            confidence="high",
            evidence_strength="strong",
            quality_score=qs,
            caveats=[
                "rebuilt from 9A.1 PersonaRecord; 9A.1 quality_score "
                "not preserved in DB",
            ],
            simulation_usefulness_summary=(
                f"9A.1 → 9A.2 hard-cap input "
                f"(role: {normalized_role})."
            ),
            persistence_recommendation="DEFER",
            kept_reason="rebuilt from 9A.1 for 9A.2 hard-cap",
        )
        candidates.append(cand)
        meta[cand.candidate_id] = {
            "persona_id_9a1": p.id,
            "trait_rows": traits[:7],
            "link_rows": links,
        }
    return candidates, meta, {"input_count": len(candidates)}


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


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Phase {PHASE_LABEL} — official 21–30 LumaLoop scale."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--max-personas-for-sim", type=int, default=21,
    )
    args = parser.parse_args()
    do_commit = bool(args.commit)
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_main = audit_root / "scale_lumaloop_society_9a_2.json"
    out_quality = audit_root / "scale_lumaloop_society_9a_2_quality.json"
    out_report_json = audit_root / "lumaloop_scaled_founder_report_9a_2.json"
    out_report_md = audit_root / "lumaloop_scaled_founder_report_9a_2.md"

    sm = get_sessionmaker()
    db_pre = await _read_table_counts(sm)
    print(f"DB pre: {db_pre}")
    print(f"Mode: {'COMMIT' if do_commit else 'DRY-RUN'}")

    audit: dict[str, Any] = {
        "phase": "9a_2_compressor_hard_cap",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if do_commit else "dry_run",
        "founder_brief": json.loads(LUMALOOP_BRIEF.model_dump_json()),
        "launch_state": LAUNCH_STATE,
        "hard_max_compressed": HARD_MAX_COMPRESSED,
        "provider_key_presence": {
            "anthropic_configured": bool(
                os.environ.get("ANTHROPIC_API_KEY"),
            ),
        },
        "db_pre_counts": db_pre,
    }

    # 1. Load 9A.1 personas as candidates
    async with sm() as session:
        candidates, persona_meta, load_audit = (
            await _load_9a_1_as_candidates(session)
        )
    if not candidates:
        audit["rollback_reason"] = "no_9A_1_personas_in_db"
        audit["recommendation"] = (
            "FAIL — Phase 9A.1 personas not found in DB. Run 9A.1 first."
        )
        audit["ready_for_9b_50_to_100_personas"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1
    audit["candidates_before_cap"] = len(candidates)
    audit["compressed_before_cap"] = len(candidates)
    audit["previous_9a_1_summary"] = {
        "personas_loaded_from_db": len(candidates),
        "distinct_roles_input": len({
            c.normalized_primary_role for c in candidates
        }),
    }

    # 2. Apply hard cap
    role_dist_before = Counter(
        c.normalized_primary_role for c in candidates
    )
    provider_dist_before = Counter(
        c.source_provider_family for c in candidates
    )
    kept, dropped, hard_cap_audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=HARD_MAX_COMPRESSED,
    )
    audit["compressed_after_cap"] = len(kept)
    audit["rejected_due_to_hard_cap"] = [
        {
            "candidate_id": d.candidate_id,
            "normalized_primary_role": d.normalized_primary_role,
            "evidence_theme": d.evidence_theme,
            "source_provider_family": d.source_provider_family,
            "quality_score": d.quality_score,
        }
        for d in dropped
    ]
    audit["stratified_selection_policy"] = hard_cap_audit
    role_dist_after = Counter(
        c.normalized_primary_role for c in kept
    )
    provider_dist_after = Counter(
        c.source_provider_family for c in kept
    )
    use_case_dist_after = Counter(
        c.evidence_theme for c in kept
    )
    audit["role_distribution_before_cap"] = dict(role_dist_before)
    audit["role_distribution_after_cap"] = dict(role_dist_after)
    audit["provider_distribution_before_cap"] = dict(provider_dist_before)
    audit["provider_distribution_after_cap"] = dict(provider_dist_after)
    audit["use_case_distribution_after_cap"] = dict(use_case_dist_after)
    audit["objection_distribution_after_cap"] = {}
    audit["proof_requirement_distribution_after_cap"] = {}
    if role_dist_after:
        top_r, top_n = role_dist_after.most_common(1)[0]
        audit["role_concentration_top_role"] = (
            f"{top_r} ({top_n}/{len(kept)} = "
            f"{top_n / max(len(kept), 1):.0%})"
        )
    audit["distinct_role_count"] = len(role_dist_after)

    # Diversity gates
    if len(kept) < EXPECTED_MIN_COMPRESSED_PERSONAS:
        audit["persona_gate_decision"] = "halted_at_min_compressed"
        audit["rollback_reason"] = (
            f"only {len(kept)} compressed; need ≥"
            f"{EXPECTED_MIN_COMPRESSED_PERSONAS}."
        )
        audit["ready_for_9b_50_to_100_personas"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1
    if len(kept) > EXPECTED_MAX_COMPRESSED_PERSONAS:
        audit["persona_gate_decision"] = "halted_at_max_compressed"
        audit["rollback_reason"] = (
            f"{len(kept)} compressed exceeds 9A max "
            f"{EXPECTED_MAX_COMPRESSED_PERSONAS}; hard cap selector "
            "did not enforce."
        )
        audit["ready_for_9b_50_to_100_personas"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1
    if len(role_dist_after) < 5:
        audit["persona_gate_decision"] = "halted_at_distinct_roles"
        audit["rollback_reason"] = (
            f"only {len(role_dist_after)} distinct roles; need ≥5."
        )
        audit["ready_for_9b_50_to_100_personas"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    audit["persona_gate_decision"] = (
        "passed_hard_cap_diversity_gate"
    )
    print(
        f"\n=== Hard-cap PASSED: {len(candidates)} → {len(kept)} "
        f"(dropped {len(dropped)}). distinct_roles={len(role_dist_after)} ==="
    )

    if not do_commit:
        audit["recommendation"] = (
            "DRY-RUN — preflight only. Run --commit to persist + simulate."
        )
        audit["ready_for_9b_50_to_100_personas"] = False
        audit["next_psychology_layer_needed"] = True
        audit["next_discussion_layer_needed"] = True
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
            f"DB unchanged on dry-run: "
            f"{post == db_pre}. Audit: {out_main}"
        )
        return 0

    # 3. Persist 30 under new run_scope_id (reuses 9A.1 SourceRecords)
    run_scope_id = _make_run_scope_id()
    audit["run_scope_id"] = run_scope_id
    print(
        f"\nPersisting {len(kept)} new PersonaRecords under "
        f"run_scope_id={run_scope_id}; reusing 9A.1 SourceRecords."
    )
    persisted_personas: list[dict[str, Any]] = []
    expected_traits = 0
    expected_links = 0
    rollback_reason: str | None = None
    persona_id_map: dict[str, uuid.UUID] = {}  # candidate_id → 9A.2 persona_id

    async with sm() as session:
        try:
            async with session.begin():
                now = datetime.now(UTC)
                # Pre-pass: insert all PersonaRecords
                for c in kept:
                    persona_id = uuid.uuid4()
                    persona_id_map[c.candidate_id] = persona_id
                    display_name = generate_display_name(
                        seed=str(persona_id),
                    )
                    relevance_tags = [
                        f"target_brief:{TARGET_BRIEF_ID}",
                        f"product_name:{PRODUCT_NAME}",
                        f"launch_state:{LAUNCH_STATE}",
                        f"phase:{PHASE_LABEL}",
                        f"run_scope_id:{run_scope_id}",
                        f"normalized_primary_role:{c.normalized_primary_role}",
                        f"evidence_theme:{c.evidence_theme}",
                        f"source_provider_family:{c.source_provider_family}",
                        f"compressed_candidate_id:{c.candidate_id}",
                        "scope:run_scoped_brief_scoped",
                        "persistence_type:generated_simulation_artifact",
                        "not_global_persona:true",
                        (
                            f"caveat:Generated for this {PRODUCT_NAME} "
                            "9A.2 official 30-persona run; not global."
                        ),
                    ]
                    session.add(PersonaRecord(
                        id=persona_id,
                        display_name=display_name,
                        segment_label=(
                            c.segment_label or c.normalized_primary_role
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

                # Insert traits + evidence_links
                for c in kept:
                    persona_id = persona_id_map[c.candidate_id]
                    meta = persona_meta[c.candidate_id]
                    src_traits = meta["trait_rows"]
                    src_links = meta["link_rows"]
                    if not src_traits or not src_links:
                        raise RuntimeError(
                            f"candidate {c.candidate_id}: missing 9A.1 "
                            "trait or link rows"
                        )
                    traits_added = 0
                    for t in src_traits:
                        # Reuse the SAME source_ids list (existing
                        # SourceRecords) — ARRAY of UUIDs.
                        source_ids_list = list(t.source_ids or [])
                        if not source_ids_list:
                            continue
                        session.add(PersonaTrait(
                            id=uuid.uuid4(),
                            persona_id=persona_id,
                            field_name=t.field_name,
                            value=t.value,
                            support_level=t.support_level,
                            source_ids=source_ids_list,
                            confidence=t.confidence,
                            rationale=t.rationale,
                            last_updated_at=now,
                        ))
                        traits_added += 1
                        expected_traits += 1
                    if traits_added < 2:
                        # Synthesize a fallback trait with role context
                        fallback_src = list(
                            src_links[0].source_record_id
                            for _ in range(1)
                        )
                        session.add(PersonaTrait(
                            id=uuid.uuid4(),
                            persona_id=persona_id,
                            field_name="role_or_context",
                            value=c.normalized_primary_role,
                            support_level="inferred",
                            source_ids=[
                                src_links[0].source_record_id,
                            ],
                            confidence=Decimal("0.6"),
                            rationale=(
                                f"persona_role::{c.normalized_primary_role} "
                                "(9A.2 fallback for trait floor)"
                            ),
                            last_updated_at=now,
                        ))
                        traits_added += 1
                        expected_traits += 1
                    links_added = 0
                    for l in src_links:
                        session.add(PersonaEvidenceLink(
                            id=uuid.uuid4(),
                            persona_id=persona_id,
                            source_record_id=l.source_record_id,
                            contribution_kind=l.contribution_kind,
                            contribution_field=l.contribution_field,
                            excerpt=l.excerpt,
                            excerpt_offset=None,
                            confidence=l.confidence,
                        ))
                        links_added += 1
                        expected_links += 1
                    if traits_added < 2:
                        raise RuntimeError(
                            f"persona {persona_id}: only {traits_added} "
                            "trait(s)"
                        )
                    persisted_personas.append({
                        "persona_record_id": str(persona_id),
                        "display_name": generate_display_name(
                            seed=str(persona_id),
                        ),
                        "compressed_candidate_id": c.candidate_id,
                        "normalized_primary_role": c.normalized_primary_role,
                        "evidence_theme": c.evidence_theme,
                        "source_provider_family": c.source_provider_family,
                        "trait_count": traits_added,
                        "evidence_link_count": links_added,
                    })
        except Exception as e:
            rollback_reason = (
                f"persistence_failed: {type(e).__name__}: {e}"
            )
            print(f"ROLLBACK: {rollback_reason}")

    audit["personas_persisted_count"] = (
        len(persisted_personas) if rollback_reason is None else 0
    )
    audit["traits_persisted_count"] = (
        expected_traits if rollback_reason is None else 0
    )
    audit["evidence_links_persisted_count"] = (
        expected_links if rollback_reason is None else 0
    )
    audit["source_records_inserted"] = 0  # reused 9A.1 sources
    audit["source_records_reused"] = sum(
        len(persona_meta[c.candidate_id]["link_rows"]) for c in kept
    )
    audit["persisted_personas"] = persisted_personas
    audit["persisted_society_size"] = len(persisted_personas)

    if rollback_reason:
        audit["rollback_reason"] = rollback_reason
        audit["ready_for_9b_50_to_100_personas"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    print(
        f"Persisted: +{len(persisted_personas)} personas / "
        f"+{expected_traits} traits / +{expected_links} links."
    )

    # 4. Simulation
    cap_personas = min(
        args.max_personas_for_sim, len(persisted_personas),
    )
    audit["simulated_sample_size"] = cap_personas
    print(
        f"\nLoading {cap_personas} personas + running 7 rounds..."
    )
    async with sm() as session:
        run_scoped_agents = await load_run_scoped_agents(
            session=session, run_scope_id=run_scope_id,
        )
    if len(run_scoped_agents) > cap_personas:
        run_scoped_agents = run_scoped_agents[:cap_personas]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        audit["rollback_reason"] = "anthropic_key_missing"
        audit["ready_for_9b_50_to_100_personas"] = False
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
                    "persisted_society_size": len(persisted_personas),
                    "simulated_sample_size": cap_personas,
                },
            ))
            price_value = float(
                str(LUMALOOP_BRIEF.price_or_price_structure)
                .replace("$", "").strip() or 0
            )
            session.add(SimulationInput(
                id=uuid.uuid4(), simulation_id=sim_id,
                product_type="LED safety band",
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
                        "current_alternatives": [],
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
        for round_idx, round_type in enumerate(
            AGENT_ROUND_TYPES, start=1,
        ):
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
        audit["ready_for_9b_50_to_100_personas"] = False
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
            "Sources include Brave/Tavily web snippets and YouTube "
            "Data API public metadata + comments (rebuilt from "
            "Phase 9A.1 evidence pool).",
        ],
        product_name=PRODUCT_NAME,
        agents_with_traits_count=sum(
            1 for a in run_scoped_agents if a.traits
        ),
        total_agents=len(run_scoped_agents),
    )
    audit["simulation_quality"] = json.loads(sim_quality.model_dump_json())

    # Founder report
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
        "phase": "9a_2_lumaloop_scaled_simulation_for_report",
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
            audit.get("source_records_reused") or 0
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
        audit["ready_for_9b_50_to_100_personas"] = False
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
        audit["rollback_reason"] = "secrets_in_report"
        audit["ready_for_9b_50_to_100_personas"] = False
        out_main.write_text(
            json.dumps(audit, indent=2, default=str),
            encoding="utf-8",
        )
        return 1

    report_dict = report.model_dump()
    report_dict["security_redaction_audit"] = {
        "secrets_detected_in_inputs": False,
        "redactions_applied": 0,
        "scanner_version": "9A.2.universal",
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
    audit["report_quality"] = json.loads(
        report_qual.model_dump_json(),
    )
    audit["founder_report_files"] = {
        "report_json": str(out_report_json),
        "report_md": str(out_report_md),
    }
    audit["security_redaction_audit"] = {
        "secrets_clean": True, "finding_count": 0,
        "scanner_version": "9A.2.universal",
    }

    db_post = await _read_table_counts(sm)
    audit["db_post_counts"] = db_post
    audit["db_delta_summary"] = {
        k: db_post[k] - db_pre[k] for k in db_pre.keys()
    }

    quality_gates = {
        "hard_cap_applied": hard_cap_audit.get("applied", False),
        "compressed_personas_at_least_21": (
            len(kept) >= EXPECTED_MIN_COMPRESSED_PERSONAS
        ),
        "compressed_personas_at_most_30": (
            len(kept) <= EXPECTED_MAX_COMPRESSED_PERSONAS
        ),
        "no_single_role_over_35_pct": (
            (
                role_dist_after.most_common(1)[0][1]
                / max(len(kept), 1)
            ) <= 0.35 if role_dist_after else False
        ),
        "at_least_5_distinct_roles": (
            len(role_dist_after) >= 5
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
    audit["ready_for_9b_50_to_100_personas"] = all(
        quality_gates.values()
    )
    audit["next_psychology_layer_needed"] = True
    audit["next_discussion_layer_needed"] = True
    audit["recommendation"] = (
        f"PASS — Phase 9A.2 official 30-persona scale completed; "
        "ready for Phase 9A.3 (OCEAN + psychology traits) → "
        "Phase 9A.4 (discussion layer) → Phase 9B "
        "(50–100 personas)."
        if audit["ready_for_9b_50_to_100_personas"] else
        "READY_WITH_CAVEATS — some quality gates require attention."
    )

    out_main.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    out_quality.write_text(
        json.dumps({
            "phase": "9a_2_quality",
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
    print(f"Phase {PHASE_LABEL} — Official 30-persona LumaLoop scale")
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
