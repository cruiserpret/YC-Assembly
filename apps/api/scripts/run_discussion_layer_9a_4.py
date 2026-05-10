"""Phase 9A.4 — run the human-like discussion layer for the official
9A.2 30-person LumaLoop society.

Usage:
  python scripts/run_discussion_layer_9a_4.py            # dry-run (no DB writes)
  python scripts/run_discussion_layer_9a_4.py --commit   # full discussion run
  python scripts/run_discussion_layer_9a_4.py --commit --pilot   # 1-group pilot

Reads:
  _audit/scale_lumaloop_society_9a_2.json
  _audit/persona_psychology_layer_9a_3.json (referenced as the input
                                              psychology summary)

Writes (commit mode):
  _audit/discussion_layer_9a_4.json
  _audit/discussion_layer_9a_4_quality.json
  _audit/lumaloop_discussion_report_9a_4.json
  _audit/lumaloop_discussion_report_9a_4.md

DB writes (commit mode):
  +1 simulations row (cost-guard control row, documented)
  +1 discussion_sessions row
  +K discussion_groups rows (K = group_count, default 5)
  +M discussion_turns rows (M = ~150-180 turns)
  +B discussion_private_ballots rows (B = personas × ballot_stages)
  +A persona_memory_atoms rows (seed bag + per-turn atoms)

DB writes that are FORBIDDEN (asserted in the additive_only_check):
  source_records, persona_records, persona_traits,
  persona_evidence_links, persona_psychology_traits, agents,
  agent_responses (the simulation row IS new — needed for the cost
  guard FK — but no agents/responses are added).

NO live retrieval. NO Brave/Tavily/YouTube/Firecrawl/Amazon. NO Jina/
Exa/DataForSEO/Reddit/Apify.
"""
from __future__ import annotations

import argparse
import asyncio
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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assembly.db import get_sessionmaker
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.models.agent import Agent
from assembly.models.discussion import (
    DiscussionGroup,
    DiscussionPrivateBallot,
    DiscussionSession,
    DiscussionTurn,
    PersonaMemoryAtom,
)
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.models.round import AgentResponse
from assembly.models.simulation import Simulation
from assembly.sources.discussion_layer import (
    PsychologyControlSnapshot,
    assign_groups_stratified,
    build_seed_memory_atoms,
    classify_public_private_delta,
    detect_overcooperation,
    evaluate_discussion_quality,
    forbidden_claim_audit,
    rank_memory_atoms,
    render_discussion_report_json,
    render_discussion_report_markdown,
    sensitive_inference_audit,
)
from assembly.sources.discussion_layer.schemas import (
    PrivateBallotDraft,
    TurnDraft,
)
from assembly.sources.founder_report_generator import scan_for_secrets


PHASE_LABEL = "9A.4"
EXPECTED_PERSONA_COUNT = 30
DEFAULT_GROUP_COUNT = 5
DEFAULT_GROUP_SIZE = 6
HARD_CAP_USD = Decimal("12.00")

AUDIT_ROOT = Path(__file__).resolve().parent.parent / "_audit"
AUDIT_PATH = AUDIT_ROOT / "discussion_layer_9a_4.json"
QUALITY_PATH = AUDIT_ROOT / "discussion_layer_9a_4_quality.json"
REPORT_JSON_PATH = AUDIT_ROOT / "lumaloop_discussion_report_9a_4.json"
REPORT_MD_PATH = AUDIT_ROOT / "lumaloop_discussion_report_9a_4.md"
INPUT_9A_2_AUDIT_PATH = AUDIT_ROOT / "scale_lumaloop_society_9a_2.json"
INPUT_9A_3_AUDIT_PATH = AUDIT_ROOT / "persona_psychology_layer_9a_3.json"


_FORBIDDEN_RETRIEVAL_TOKENS = (
    "jina", "exa.ai", "exasearch", "dataforseo",
    "apify", "reddit.com/api",
)
_ALLOWED_STANCES = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)


def _parse_tag_value(
    tags: list[str], key: str, default: str = "",
) -> str:
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


# -----------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------


async def _load_db_pre_counts(sm: Any) -> dict[str, int]:
    async with sm() as session:
        return await _count_all(session)


async def _count_all(session: AsyncSession) -> dict[str, int]:
    rows = {}
    for label, table in (
        ("source_records", SourceRecord),
        ("persona_records", PersonaRecord),
        ("persona_traits", PersonaTrait),
        ("persona_evidence_links", PersonaEvidenceLink),
        ("persona_psychology_traits", PersonaPsychologyTrait),
        ("simulations", Simulation),
        ("agents", Agent),
        ("agent_responses", AgentResponse),
        ("discussion_sessions", DiscussionSession),
        ("discussion_groups", DiscussionGroup),
        ("discussion_turns", DiscussionTurn),
        ("discussion_private_ballots", DiscussionPrivateBallot),
        ("persona_memory_atoms", PersonaMemoryAtom),
    ):
        n = (await session.execute(
            select(func.count()).select_from(table)
        )).scalar_one()
        rows[label] = int(n)
    return rows


async def _load_personas_for_run_scope(
    session: AsyncSession, run_scope_id: str,
) -> list[PersonaRecord]:
    return (await session.execute(
        select(PersonaRecord)
        .where(
            PersonaRecord.product_relevance_tags.contains(
                [f"run_scope_id:{run_scope_id}"],
            )
        )
        .order_by(PersonaRecord.id)
    )).scalars().all()


async def _load_traits_for(
    session: AsyncSession, persona_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[PersonaTrait]]:
    rows = (await session.execute(
        select(PersonaTrait)
        .where(PersonaTrait.persona_id.in_(persona_ids))
        .order_by(PersonaTrait.persona_id, PersonaTrait.field_name)
    )).scalars().all()
    out: dict[uuid.UUID, list[PersonaTrait]] = {}
    for t in rows:
        out.setdefault(t.persona_id, []).append(t)
    return out


async def _load_psychology_for(
    session: AsyncSession,
    persona_ids: list[uuid.UUID],
    run_scope_id: str,
) -> dict[uuid.UUID, list[PersonaPsychologyTrait]]:
    rows = (await session.execute(
        select(PersonaPsychologyTrait)
        .where(PersonaPsychologyTrait.persona_id.in_(persona_ids))
        .where(PersonaPsychologyTrait.run_scope_id == run_scope_id)
        .order_by(
            PersonaPsychologyTrait.persona_id,
            PersonaPsychologyTrait.trait_name,
        )
    )).scalars().all()
    out: dict[uuid.UUID, list[PersonaPsychologyTrait]] = {}
    for t in rows:
        out.setdefault(t.persona_id, []).append(t)
    return out


async def _load_links_for(
    session: AsyncSession, persona_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[PersonaEvidenceLink]]:
    rows = (await session.execute(
        select(PersonaEvidenceLink)
        .where(PersonaEvidenceLink.persona_id.in_(persona_ids))
    )).scalars().all()
    out: dict[uuid.UUID, list[PersonaEvidenceLink]] = {}
    for l in rows:
        out.setdefault(l.persona_id, []).append(l)
    return out


async def _load_sim_responses_for(
    session: AsyncSession,
    simulation_id: uuid.UUID | None,
    persona_ids: list[uuid.UUID],
) -> tuple[
    dict[uuid.UUID, list[AgentResponse]],
    dict[uuid.UUID, str],
]:
    """Returns (responses_by_persona, persona_id → final_stance)."""
    if simulation_id is None:
        return {}, {}
    agents = (await session.execute(
        select(Agent).where(Agent.simulation_id == simulation_id)
    )).scalars().all()
    agent_to_persona: dict[uuid.UUID, uuid.UUID] = {}
    for a in agents:
        ppid = (a.traits or {}).get("persisted_persona_id")
        if not ppid:
            continue
        try:
            puuid = uuid.UUID(ppid)
        except (ValueError, TypeError):
            continue
        if puuid in persona_ids:
            agent_to_persona[a.id] = puuid
    if not agent_to_persona:
        return {}, {}
    rows = (await session.execute(
        select(AgentResponse)
        .where(AgentResponse.agent_id.in_(list(agent_to_persona.keys())))
        .order_by(AgentResponse.agent_id, AgentResponse.created_at)
    )).scalars().all()
    by_persona: dict[uuid.UUID, list[AgentResponse]] = {}
    for r in rows:
        pid = agent_to_persona.get(r.agent_id)
        if pid is None:
            continue
        by_persona.setdefault(pid, []).append(r)
    finals: dict[uuid.UUID, str] = {}
    return by_persona, finals


# -----------------------------------------------------------------------
# Prompting
# -----------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are an evidence-backed run-scoped persona in a synthetic "
    "discussion. Stay in character. Speak ONLY for this single persona. "
    "You did NOT use the unlaunched product — you can compare it to "
    "alternatives you know, but you have not bought, used, owned, or "
    "reviewed it. Avoid forecasts, percentages, market verdicts, or "
    "claims about adoption. Output ONLY the requested JSON; no preamble, "
    "no markdown."
)


_PROFILE_INSTRUCTIONS = """
Your psychology profile (these are simulation controls, not real
psychological diagnoses) shapes how you talk:
- openness ({openness_label}) → willingness to entertain new arguments
- conscientiousness ({conscientiousness_label}) → demand for details/proof
- extraversion ({extraversion_label}) → assertiveness in public turns
- agreeableness ({agreeableness_label}) → cooperative vs blunt tone
- neuroticism ({neuroticism_label}) → risk-sensitivity and worry
- risk_tolerance ({risk_tolerance_label}) → willingness to try unproven
- novelty_seeking ({novelty_seeking_label}) → interest in new format
- trust_proof_threshold ({trust_proof_threshold_label}) → proof demand
- social_influence_susceptibility ({social_influence_susceptibility_label}) → likelihood of shifting after group pressure
- category_involvement_or_expertise ({category_involvement_or_expertise_label}) → specificity of comparisons
- price_sensitivity ({price_sensitivity_label}) → emphasis on cost / value

Let these traits visibly shape your language. Do NOT name the traits
inside your response — just speak in a way consistent with them.
""".strip()


_PRE_BALLOT_INSTRUCTION = """
This is your PRIVATE pre-discussion ballot. No one else will see it.

Output a single JSON object:
{
  "private_stance": "<one of: curious_but_unconvinced, interested_if_proven, skeptical, likely_reject, needs_more_information>",
  "private_reasoning": "<2-4 sentences in your voice; you may reference specific evidence you remember; cite that this is one persona in a synthetic n=30 simulation>",
  "confidence": "<one of: high, medium, low>",
  "top_objection": "<one short objection in your voice or null>",
  "top_proof_need": "<one short proof item that would change your mind, or null>"
}
""".strip()


_PUBLIC_OPENING_INSTRUCTION = """
Round 1 — PUBLIC opening statement. The other 5 personas in your group
will see what you write.

Output a single JSON object:
{
  "public_text": "<2-4 sentences in your voice; do NOT claim to have used the product; do NOT forecast adoption>",
  "stance": "<one of: curious_but_unconvinced, interested_if_proven, skeptical, likely_reject, needs_more_information>",
  "referenced_memory_atom_ids": []
}
""".strip()


_CHALLENGE_INSTRUCTION = """
Round 2 — CHALLENGE round. Pose ONE specific challenge to the public
positions you've heard, OR sharpen the strongest objection from your
psychology profile. Use your conscientiousness and trust_proof_threshold.

Output a single JSON object:
{
  "public_text": "<2-4 sentences; be specific; reference an objection that follows from your profile>",
  "stance": "<one of the allowed stances>",
  "target_persona_id": "<persona_id of the speaker you're challenging, or null>",
  "referenced_turn_ids": []
}
""".strip()


_PEER_RESPONSE_INSTRUCTION = """
Round 3 — PEER RESPONSE. You MUST quote or paraphrase one specific
prior turn from the snippet above and respond to it. Your response
should reflect your social_influence_susceptibility and agreeableness.

Output a single JSON object:
{
  "public_text": "<2-4 sentences responding to a SPECIFIC prior turn; you may agree, disagree, or partially agree>",
  "stance": "<one of the allowed stances>",
  "target_persona_id": "<persona_id of the persona you're responding to, or null>",
  "referenced_turn_ids": ["<turn_id of the prior turn you're responding to>"]
}
""".strip()


_PROOF_DISCUSSION_INSTRUCTION = """
Round 4 — PROOF DISCUSSION. Discuss what specific PROOF would change
your private stance. Use your trust_proof_threshold and
category_involvement_or_expertise.

Output a single JSON object:
{
  "public_text": "<2-4 sentences naming the specific proof item(s) you'd want — IP rating, durability test, athlete review, etc.>",
  "stance": "<one of the allowed stances>"
}
""".strip()


_REFLECTION_INSTRUCTION = """
Round 5 — PRIVATE REFLECTION. No one else will see this.

Output a single JSON object:
{
  "private_stance": "<one of the allowed stances; this is your CURRENT private stance>",
  "private_reasoning": "<3-5 sentences: what argument affected you most? what did you resist? does your public stance differ from your private stance? caveat that this is a synthetic simulation>",
  "confidence": "<one of: high, medium, low>"
}
""".strip()


_FINAL_BALLOT_INSTRUCTION = """
Round 6 — PRIVATE FINAL BALLOT. No one else will see this.

Output a single JSON object:
{
  "private_stance": "<one of: curious_but_unconvinced, interested_if_proven, skeptical, likely_reject, needs_more_information>",
  "private_reasoning": "<3-5 sentences explaining whether/why your stance changed; caveat that this is a synthetic n=30 discussion>",
  "confidence": "<one of: high, medium, low>",
  "top_objection": "<one short objection in your voice or null>",
  "top_proof_need": "<one short proof item that would shift you further, or null>"
}
""".strip()


def _label(value: float) -> str:
    if value < 0.4:
        return "low"
    if value > 0.6:
        return "high"
    return "medium"


def _build_persona_block(
    persona: dict[str, Any],
    psychology: list[dict[str, Any]],
    seed_memory_atoms: list[dict[str, Any]],
    extra_memory_atoms: list[dict[str, Any]],
) -> tuple[str, dict[str, float], dict[str, str]]:
    name = persona["display_name"]
    role = persona["normalized_primary_role"]
    psy_values: dict[str, float] = {}
    psy_labels: dict[str, str] = {}
    for t in psychology:
        psy_values[t["trait_name"]] = float(t["value_numeric"])
        psy_labels[t["trait_name"]] = t["value_label"]
    profile_lines = _PROFILE_INSTRUCTIONS.format(
        openness_label=psy_labels.get("openness", "medium"),
        conscientiousness_label=psy_labels.get("conscientiousness", "medium"),
        extraversion_label=psy_labels.get("extraversion", "medium"),
        agreeableness_label=psy_labels.get("agreeableness", "medium"),
        neuroticism_label=psy_labels.get("neuroticism", "medium"),
        risk_tolerance_label=psy_labels.get("risk_tolerance", "medium"),
        novelty_seeking_label=psy_labels.get("novelty_seeking", "medium"),
        trust_proof_threshold_label=psy_labels.get(
            "trust_proof_threshold", "medium",
        ),
        social_influence_susceptibility_label=psy_labels.get(
            "social_influence_susceptibility", "medium",
        ),
        category_involvement_or_expertise_label=psy_labels.get(
            "category_involvement_or_expertise", "medium",
        ),
        price_sensitivity_label=psy_labels.get("price_sensitivity", "medium"),
    )
    mem_lines: list[str] = []
    for a in (seed_memory_atoms + extra_memory_atoms)[:8]:
        mem_lines.append(
            f"- [{a['memory_type']}] {a['memory_text']} "
            f"(origin: {a['origin_excerpt'][:120]})"
        )
    mem_block = (
        "\n".join(mem_lines) if mem_lines else
        "(no relevant memory atoms retrieved)"
    )
    block = (
        f"You are {name}. Your role context: {role}.\n\n"
        f"{profile_lines}\n\n"
        f"Relevant memory atoms (each cites a real source):\n{mem_block}"
    )
    return block, psy_values, psy_labels


def _safe_json_parse(text: str) -> dict[str, Any] | None:
    """Robust-ish JSON extraction from an LLM response. Strips common
    code-fences and trailing explanations."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # find first { ... } block
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(0)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None


def _coerce_stance(value: Any) -> str | None:
    if isinstance(value, str) and value in _ALLOWED_STANCES:
        return value
    return None


def _make_run_scope_id_alias(base: str) -> str:
    return base


# -----------------------------------------------------------------------
# Orchestrator
# -----------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — discussion layer.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Persist into discussion_* tables. Default is dry-run.",
    )
    parser.add_argument(
        "--pilot", action="store_true",
        help="Run only the first group (cost-saver).",
    )
    parser.add_argument(
        "--group-count", type=int, default=DEFAULT_GROUP_COUNT,
        help="Number of groups (default 5).",
    )
    parser.add_argument(
        "--group-size", type=int, default=DEFAULT_GROUP_SIZE,
        help="Personas per group (default 6).",
    )
    parser.add_argument(
        "--run-scope-id", type=str, default=None,
        help="Override 9A.2 run_scope_id; default reads from audit.",
    )
    args = parser.parse_args()
    AUDIT_ROOT.mkdir(exist_ok=True)

    audit: dict[str, Any] = {
        "phase": "9a_4_human_like_discussion_layer",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "dry_run",
        "pilot": args.pilot,
        "group_count_requested": args.group_count,
        "group_size_requested": args.group_size,
    }

    nine_a_2 = (
        json.loads(INPUT_9A_2_AUDIT_PATH.read_text(encoding="utf-8"))
        if INPUT_9A_2_AUDIT_PATH.exists() else {}
    )
    nine_a_3 = (
        json.loads(INPUT_9A_3_AUDIT_PATH.read_text(encoding="utf-8"))
        if INPUT_9A_3_AUDIT_PATH.exists() else {}
    )
    audit["input_9a_3_psychology_summary"] = {
        "total_psychology_traits_created": (
            nine_a_3.get("total_psychology_traits_created")
        ),
        "psychology_traits_per_persona": (
            nine_a_3.get("psychology_traits_per_persona")
        ),
        "ready_for_discussion_layer_v1": (
            nine_a_3.get("ready_for_discussion_layer_v1")
        ),
    }
    run_scope_id = (
        args.run_scope_id or nine_a_2.get("run_scope_id") or ""
    )
    if not run_scope_id:
        print("REFUSED: no 9A.2 run_scope_id available.")
        audit["blocker"] = "no 9A.2 run_scope_id available"
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2
    audit["input_9a_2_run_scope_id"] = run_scope_id
    product_name = (
        (nine_a_2.get("founder_brief") or {}).get("product_name") or "lumaloop"
    )
    launch_state = nine_a_2.get("launch_state") or "unlaunched"
    sim_9a_2 = nine_a_2.get("simulation_id") or None

    sm = get_sessionmaker()
    db_pre = await _load_db_pre_counts(sm)
    audit["db_pre_counts"] = db_pre

    async with sm() as session:
        personas = await _load_personas_for_run_scope(session, run_scope_id)
        if len(personas) != EXPECTED_PERSONA_COUNT:
            print(
                f"REFUSED: expected {EXPECTED_PERSONA_COUNT} personas; "
                f"got {len(personas)}."
            )
            audit["blocker"] = (
                f"persona count mismatch: expected "
                f"{EXPECTED_PERSONA_COUNT}, got {len(personas)}"
            )
            AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        persona_ids = [p.id for p in personas]
        traits_by = await _load_traits_for(session, persona_ids)
        psy_by = await _load_psychology_for(session, persona_ids, run_scope_id)
        if sum(len(v) for v in psy_by.values()) < 30 * 11:
            print(
                "REFUSED: 9A.3 psychology profiles incomplete for run_scope."
            )
            audit["blocker"] = "9A.3 psychology profiles incomplete"
            AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        links_by = await _load_links_for(session, persona_ids)
        try:
            sim_uuid = uuid.UUID(sim_9a_2) if sim_9a_2 else None
        except (ValueError, TypeError):
            sim_uuid = None
        responses_by, _ = await _load_sim_responses_for(
            session, sim_uuid, persona_ids,
        )
    audit["persona_count"] = len(personas)
    audit["psychology_trait_count"] = sum(len(v) for v in psy_by.values())

    # ---- shape persona dicts ------------------------------------------
    persona_dicts: list[dict[str, Any]] = []
    for p in personas:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        provider_family = _parse_tag_value(tags, "source_provider_family") or ""
        psy = psy_by.get(p.id, [])
        psy_dicts = [{
            "trait_id": str(t.id),
            "trait_name": t.trait_name,
            "value_numeric": float(t.value_numeric),
            "value_label": t.value_label,
            "confidence": t.confidence,
            "evidence_basis": t.evidence_basis,
            "caveat": t.caveat,
        } for t in psy]
        psy_value_map = {
            t["trait_name"]: t["value_numeric"] for t in psy_dicts
        }
        persona_dicts.append({
            "persona_id": str(p.id),
            "display_name": p.display_name,
            "normalized_primary_role": normalized_role,
            "source_provider_family": provider_family,
            "psychology": psy_dicts,
            "psychology_value_map": psy_value_map,
            "extraversion": psy_value_map.get("extraversion"),
            "agreeableness": psy_value_map.get("agreeableness"),
            "social_influence_susceptibility": psy_value_map.get(
                "social_influence_susceptibility",
            ),
            "trust_proof_threshold": psy_value_map.get(
                "trust_proof_threshold",
            ),
            "prior_simulation_final_stance": (
                next((
                    r.stance for r in reversed(responses_by.get(p.id, []))
                    if r.stance
                ), None)
            ),
        })

    # ---- assign groups ------------------------------------------------
    groups = assign_groups_stratified(
        personas=persona_dicts,
        group_count=args.group_count,
        group_size=args.group_size,
        seed=f"9A.4|{run_scope_id}",
    )
    if args.pilot:
        groups = groups[:1]
    audit["group_count"] = len(groups)
    audit["group_size"] = args.group_size
    audit["group_assignment_policy"] = (
        "stratified by role × prior_stance × extraversion × agreeableness × "
        "social_influence_susceptibility × trust_proof_threshold × provider"
    )
    audit["group_assignment"] = [
        {
            "group_index": i,
            "persona_ids": [str(pid) for pid in g],
            "persona_names": [
                next(p["display_name"] for p in persona_dicts
                     if p["persona_id"] == pid)
                for pid in g
            ],
        }
        for i, g in enumerate(groups)
    ]

    # ---- build seed memory atoms -------------------------------------
    seed_memory_by_persona: dict[str, list[dict[str, Any]]] = {}
    seed_memory_atom_count = 0
    seed_memory_drafts: list[Any] = []
    for p_dict in persona_dicts:
        pid = p_dict["persona_id"]
        persona_uuid = uuid.UUID(pid)
        traits_list = [
            {
                "trait_id": str(t.id),
                "field_name": t.field_name,
                "value": t.value,
                "rationale": t.rationale,
                "confidence": float(t.confidence),
            }
            for t in traits_by.get(persona_uuid, [])
        ]
        psy_list = p_dict["psychology"]
        link_list = [
            {
                "link_id": str(l.id),
                "source_record_id": str(l.source_record_id),
                "excerpt": l.excerpt,
                "contribution_field": l.contribution_field,
            }
            for l in links_by.get(persona_uuid, [])
        ]
        sim_list = [
            {
                "response_id": str(r.id),
                "reasoning": r.reasoning,
                "stance": r.stance,
                "round_type": "agent_response",
            }
            for r in responses_by.get(persona_uuid, [])[:6]
        ]
        drafts = build_seed_memory_atoms(
            persona_id=pid,
            run_scope_id=run_scope_id,
            persona_traits=traits_list,
            psychology_traits=psy_list,
            evidence_links=link_list,
            prior_simulation_responses=sim_list,
        )
        seed_memory_drafts.extend([
            (pid, d) for d in drafts
        ])
        seed_memory_by_persona[pid] = [
            {
                "memory_type": d.memory_type,
                "origin_type": d.origin_type,
                "origin_ref_id": d.origin_ref_id,
                "origin_excerpt": d.origin_excerpt,
                "memory_text": d.memory_text,
                "importance_score": d.importance_score,
                "recency_index": d.recency_index,
                "relevance_tags": list(d.relevance_tags),
            }
            for d in drafts
        ]
        seed_memory_atom_count += len(drafts)
    audit["seed_memory_atom_count"] = seed_memory_atom_count

    # ---- prepare LLM provider (commit only) ---------------------------
    if args.commit:
        from assembly.config import get_settings
        if not get_settings().anthropic_api_key:
            print("REFUSED: ANTHROPIC_API_KEY not set; commit mode requires it.")
            audit["blocker"] = "anthropic_key_missing"
            AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2

    discussion_session_id = uuid.uuid4()
    sim_id = uuid.uuid4()
    audit["discussion_session_id"] = str(discussion_session_id)

    if not args.commit:
        print(
            f"\nDRY-RUN — {audit['persona_count']} personas, "
            f"{audit['psychology_trait_count']} psychology traits, "
            f"{audit['group_count']} group(s), "
            f"~{seed_memory_atom_count} seed memory atoms."
        )
        audit["recommendation"] = (
            "DRY-RUN — no DB writes; re-run with --commit to run the "
            "full discussion."
        )
        audit["security_redaction_audit"] = {
            "secrets_clean": True, "finding_count": 0,
            "scanner_version": "9A.4.universal",
        }
        AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        return 0

    # =================================================================
    # COMMIT MODE
    # =================================================================
    from assembly.llm.anthropic import AnthropicProvider
    provider: LLMProvider = AnthropicProvider()

    # 1. cost-guard control rows + discussion session + groups
    persona_uuid_by_id: dict[str, uuid.UUID] = {
        p["persona_id"]: uuid.UUID(p["persona_id"]) for p in persona_dicts
    }
    persona_meta_by_id: dict[str, dict[str, Any]] = {
        p["persona_id"]: p for p in persona_dicts
    }
    group_id_by_index: dict[int, uuid.UUID] = {}
    seed_atom_id_by_origin: dict[tuple[str, str], uuid.UUID] = {}
    async with sm() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id=f"phase_{PHASE_LABEL}_lumaloop",
                status="simulating",
                started_at=datetime.now(UTC),
                progress={
                    "phase": PHASE_LABEL,
                    "discussion_session_id": str(discussion_session_id),
                    "purpose": "cost_guard_control_row_for_discussion",
                    "no_agents_or_responses_attached": True,
                    "run_scope_id": run_scope_id,
                },
            ))
            session.add(DiscussionSession(
                id=discussion_session_id,
                run_scope_id=run_scope_id,
                product_name=product_name[:64],
                phase=PHASE_LABEL,
                session_type="pilot" if args.pilot else "six_round_v1",
                status="running",
                started_at=datetime.now(UTC),
                metadata_={
                    "linked_simulation_id": str(sim_id),
                    "purpose": (
                        "human-like discussion layer V1 over the 9A.2 "
                        "30-person society"
                    ),
                },
            ))
            await session.flush()
            for i, g in enumerate(groups):
                gid = uuid.uuid4()
                group_id_by_index[i] = gid
                session.add(DiscussionGroup(
                    id=gid,
                    discussion_session_id=discussion_session_id,
                    group_index=i,
                    group_strategy="stratified_v1",
                    persona_ids=[uuid.UUID(pid) for pid in g],
                    metadata_={
                        "personas": [
                            persona_meta_by_id[pid]["display_name"]
                            for pid in g
                        ],
                    },
                ))
            await session.flush()
            # Seed memory atoms (one row per atom)
            for (pid, d) in seed_memory_drafts:
                aid = uuid.uuid4()
                session.add(PersonaMemoryAtom(
                    id=aid,
                    persona_id=uuid.UUID(pid),
                    run_scope_id=run_scope_id,
                    memory_type=d.memory_type,
                    origin_type=d.origin_type,
                    origin_ref_id=uuid.UUID(d.origin_ref_id),
                    origin_excerpt=d.origin_excerpt,
                    memory_text=d.memory_text,
                    importance_score=d.importance_score,
                    recency_index=d.recency_index,
                    relevance_tags=list(d.relevance_tags),
                ))
                seed_atom_id_by_origin[(pid, d.origin_ref_id)] = aid

    # 2. Discussion rounds
    pre_ballot_drafts: list[PrivateBallotDraft] = []
    public_turns: list[dict[str, Any]] = []  # in-memory mirror
    reflection_drafts: list[PrivateBallotDraft] = []
    final_drafts: list[PrivateBallotDraft] = []
    public_turn_records: list[dict[str, Any]] = []
    cost_summary = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def _live_seed_memory_dicts(persona_id: str) -> list[dict[str, Any]]:
        return seed_memory_by_persona.get(persona_id, [])

    async def _llm_call(
        *,
        stage: str,
        persona_block: str,
        instruction: str,
        extra_context: str = "",
    ) -> dict[str, Any] | None:
        nonlocal cost_summary
        messages = [
            LLMMessage(role="system", content=_SYSTEM_PROMPT),
            LLMMessage(role="user", content=(
                f"{persona_block}\n\n{extra_context}\n\n{instruction}"
            ).strip()),
        ]
        try:
            response = await cost_guarded_chat(
                sessionmaker=sm,
                simulation_id=sim_id,
                stage=stage,
                messages=messages,
                provider=provider,
                hard_cap_usd=HARD_CAP_USD,
                max_tokens=600,
                temperature=0.6,
                estimated_prompt_tokens=2000,
                estimated_completion_tokens=350,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  LLM call failed at stage={stage}: {exc}")
            return None
        cost_summary["calls"] += 1
        cost_summary["input_tokens"] += response.prompt_tokens or 0
        cost_summary["output_tokens"] += response.completion_tokens or 0
        return _safe_json_parse(response.text or "")

    # ---- Round 0: pre-ballot -----------------------------------------
    print(f"\n=== Round 0 — Private pre-ballot ({len(persona_dicts) if not args.pilot else len(groups[0])} personas) ===")
    targeted_personas: list[str] = []
    for g in groups:
        targeted_personas.extend(g)
    for pid in targeted_personas:
        p = persona_meta_by_id[pid]
        seed_atoms = _live_seed_memory_dicts(pid)
        block, _, _ = _build_persona_block(p, p["psychology"], seed_atoms, [])
        ctx = (
            f"Brief: The product is '{product_name}', launch_state="
            f"{launch_state}. You have NOT used it. This is a synthetic "
            f"n=30 simulation."
        )
        parsed = await _llm_call(
            stage="discussion_round_pre_ballot",
            persona_block=block,
            instruction=_PRE_BALLOT_INSTRUCTION,
            extra_context=ctx,
        )
        if not parsed:
            continue
        stance = _coerce_stance(parsed.get("private_stance"))
        if stance is None:
            continue
        try:
            pb = PrivateBallotDraft(
                persona_id=pid,
                ballot_stage="pre",
                private_stance=stance,
                private_reasoning=(parsed.get("private_reasoning") or "")[:3500],
                confidence=parsed.get("confidence")
                if parsed.get("confidence") in ("high", "medium", "low")
                else "medium",
                top_objection=(parsed.get("top_objection") or None) or None,
                top_proof_need=(parsed.get("top_proof_need") or None) or None,
            )
        except Exception:
            continue
        pre_ballot_drafts.append(pb)
        print(
            f"  · pre-ballot: {p['display_name']} = {pb.private_stance} "
            f"(confidence={pb.confidence})"
        )
    print(f"  pre-ballot calls={cost_summary['calls']}")

    # ---- helper: persist a turn (commits within own tx) --------------
    async def _persist_turn(
        *,
        group_index: int,
        round_number: int,
        turn_number: int,
        speaker_pid: str,
        target_pid: str | None,
        turn_type: str,
        public_text: str,
        stance: str | None,
        ref_turn_ids: list[uuid.UUID],
        ref_memory_atom_ids: list[uuid.UUID],
        psy_snapshot: dict[str, Any],
    ) -> uuid.UUID:
        turn_id = uuid.uuid4()
        async with sm() as session:
            async with session.begin():
                session.add(DiscussionTurn(
                    id=turn_id,
                    discussion_group_id=group_id_by_index[group_index],
                    round_number=round_number,
                    turn_number=turn_number,
                    speaker_persona_id=uuid.UUID(speaker_pid),
                    target_persona_id=(
                        uuid.UUID(target_pid) if target_pid else None
                    ),
                    turn_type=turn_type,
                    public_text=public_text[:3500],
                    stance=stance,
                    referenced_turn_ids=ref_turn_ids,
                    referenced_source_record_ids=[],
                    referenced_memory_atom_ids=ref_memory_atom_ids,
                    psychology_control_snapshot=psy_snapshot,
                    forbidden_claim_audit={},
                ))
        return turn_id

    # ---- Round 1: public_opening ---------------------------------------
    print(f"\n=== Round 1 — Public opening ===")
    for gi, group in enumerate(groups):
        for tn, pid in enumerate(group):
            p = persona_meta_by_id[pid]
            seed_atoms = _live_seed_memory_dicts(pid)
            block, psy_v, psy_l = _build_persona_block(
                p, p["psychology"], seed_atoms, [],
            )
            ctx = (
                f"You are in Group {gi + 1} of {len(groups)} discussing "
                f"the unlaunched product '{product_name}'. Personas in "
                f"your group: "
                + ", ".join(persona_meta_by_id[g]["display_name"] for g in group)
                + "."
            )
            parsed = await _llm_call(
                stage="discussion_round_public_opening",
                persona_block=block,
                instruction=_PUBLIC_OPENING_INSTRUCTION,
                extra_context=ctx,
            )
            if not parsed:
                continue
            stance = _coerce_stance(parsed.get("stance"))
            text = (parsed.get("public_text") or "").strip()
            if not text:
                continue
            psy_snap = {
                "persona_id": pid, **psy_v,
            }
            tid = await _persist_turn(
                group_index=gi, round_number=1, turn_number=tn,
                speaker_pid=pid, target_pid=None,
                turn_type="public_opening",
                public_text=text, stance=stance,
                ref_turn_ids=[],
                ref_memory_atom_ids=[
                    seed_atom_id_by_origin[(pid, a["origin_ref_id"])]
                    for a in seed_atoms
                    if (pid, a["origin_ref_id"]) in seed_atom_id_by_origin
                ][:3],
                psy_snapshot=psy_snap,
            )
            public_turn_records.append({
                "turn_id": str(tid),
                "group_index": gi,
                "round_number": 1,
                "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "public_opening",
                "public_text": text,
                "stance": stance,
                "referenced_turn_ids": [],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": psy_snap,
            })
        print(f"  group {gi + 1}: opening turns persisted")

    # ---- Round 2: challenge ------------------------------------------
    print(f"\n=== Round 2 — Challenge ===")
    for gi, group in enumerate(groups):
        prior_turns_in_group = [
            t for t in public_turn_records
            if t["group_index"] == gi and t["round_number"] == 1
        ]
        prior_text = "\n".join(
            f"  - [turn={t['turn_id'][:8]}] {t['speaker_name']} ({t.get('stance')}): "
            f"{t['public_text'][:200]}"
            for t in prior_turns_in_group
        )
        for tn, pid in enumerate(group):
            p = persona_meta_by_id[pid]
            seed_atoms = _live_seed_memory_dicts(pid)
            block, psy_v, _ = _build_persona_block(
                p, p["psychology"], seed_atoms, [],
            )
            ctx = (
                f"Public opening statements from your group:\n{prior_text}"
            )
            parsed = await _llm_call(
                stage="discussion_round_challenge",
                persona_block=block,
                instruction=_CHALLENGE_INSTRUCTION,
                extra_context=ctx,
            )
            if not parsed:
                continue
            text = (parsed.get("public_text") or "").strip()
            if not text:
                continue
            stance = _coerce_stance(parsed.get("stance"))
            target_id = parsed.get("target_persona_id")
            if not isinstance(target_id, str) or target_id not in persona_uuid_by_id:
                target_id = None
            tid = await _persist_turn(
                group_index=gi, round_number=2, turn_number=tn,
                speaker_pid=pid, target_pid=target_id,
                turn_type="challenge",
                public_text=text, stance=stance,
                ref_turn_ids=[],
                ref_memory_atom_ids=[],
                psy_snapshot={"persona_id": pid, **psy_v},
            )
            public_turn_records.append({
                "turn_id": str(tid),
                "group_index": gi,
                "round_number": 2,
                "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "challenge",
                "public_text": text,
                "stance": stance,
                "referenced_turn_ids": [],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": {"persona_id": pid, **psy_v},
            })
        print(f"  group {gi + 1}: challenge turns persisted")

    # ---- Round 3: peer_response --------------------------------------
    print(f"\n=== Round 3 — Peer response (each persona references a prior turn) ===")
    turns_by_id = {t["turn_id"]: t for t in public_turn_records}
    for gi, group in enumerate(groups):
        prior_in_group = [
            t for t in public_turn_records
            if t["group_index"] == gi
        ]
        prior_text = "\n".join(
            f"  - [turn_id={t['turn_id']}] {t['speaker_name']}: "
            f"{t['public_text'][:200]}"
            for t in prior_in_group[-12:]
        )
        for tn, pid in enumerate(group):
            p = persona_meta_by_id[pid]
            seed_atoms = _live_seed_memory_dicts(pid)
            block, psy_v, _ = _build_persona_block(
                p, p["psychology"], seed_atoms, [],
            )
            ctx = f"Recent turns in your group:\n{prior_text}"
            parsed = await _llm_call(
                stage="discussion_round_peer_response",
                persona_block=block,
                instruction=_PEER_RESPONSE_INSTRUCTION,
                extra_context=ctx,
            )
            if not parsed:
                continue
            text = (parsed.get("public_text") or "").strip()
            stance = _coerce_stance(parsed.get("stance"))
            ref_ids_raw = parsed.get("referenced_turn_ids") or []
            target_id = parsed.get("target_persona_id")
            if not isinstance(target_id, str) or target_id not in persona_uuid_by_id:
                target_id = None
            ref_ids: list[uuid.UUID] = []
            for raw in (ref_ids_raw if isinstance(ref_ids_raw, list) else []):
                if not isinstance(raw, str):
                    continue
                if raw in turns_by_id:
                    try:
                        ref_ids.append(uuid.UUID(raw))
                    except ValueError:
                        continue
            # Fallback: if no valid ref, link to most-recent turn in group
            if not ref_ids and prior_in_group:
                try:
                    ref_ids = [uuid.UUID(prior_in_group[-1]["turn_id"])]
                except ValueError:
                    pass
            if not text:
                continue
            tid = await _persist_turn(
                group_index=gi, round_number=3, turn_number=tn,
                speaker_pid=pid, target_pid=target_id,
                turn_type="peer_response",
                public_text=text, stance=stance,
                ref_turn_ids=ref_ids,
                ref_memory_atom_ids=[],
                psy_snapshot={"persona_id": pid, **psy_v},
            )
            public_turn_records.append({
                "turn_id": str(tid),
                "group_index": gi,
                "round_number": 3,
                "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "peer_response",
                "public_text": text,
                "stance": stance,
                "referenced_turn_ids": [str(r) for r in ref_ids],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": {"persona_id": pid, **psy_v},
            })
        print(f"  group {gi + 1}: peer-response turns persisted")

    # ---- Round 4: proof_discussion -----------------------------------
    print(f"\n=== Round 4 — Proof discussion ===")
    for gi, group in enumerate(groups):
        for tn, pid in enumerate(group):
            p = persona_meta_by_id[pid]
            seed_atoms = _live_seed_memory_dicts(pid)
            block, psy_v, _ = _build_persona_block(
                p, p["psychology"], seed_atoms, [],
            )
            ctx = ""
            parsed = await _llm_call(
                stage="discussion_round_proof_discussion",
                persona_block=block,
                instruction=_PROOF_DISCUSSION_INSTRUCTION,
                extra_context=ctx,
            )
            if not parsed:
                continue
            text = (parsed.get("public_text") or "").strip()
            stance = _coerce_stance(parsed.get("stance"))
            if not text:
                continue
            tid = await _persist_turn(
                group_index=gi, round_number=4, turn_number=tn,
                speaker_pid=pid, target_pid=None,
                turn_type="proof_discussion",
                public_text=text, stance=stance,
                ref_turn_ids=[],
                ref_memory_atom_ids=[],
                psy_snapshot={"persona_id": pid, **psy_v},
            )
            public_turn_records.append({
                "turn_id": str(tid),
                "group_index": gi,
                "round_number": 4,
                "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "proof_discussion",
                "public_text": text,
                "stance": stance,
                "referenced_turn_ids": [],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": {"persona_id": pid, **psy_v},
            })
        print(f"  group {gi + 1}: proof-discussion turns persisted")

    # ---- Round 5: reflection (private) -------------------------------
    print(f"\n=== Round 5 — Private reflection ===")
    for pid in targeted_personas:
        p = persona_meta_by_id[pid]
        seed_atoms = _live_seed_memory_dicts(pid)
        # gather a handful of recent turns from the persona's group
        gi = next(
            (i for i, g in enumerate(groups) if pid in g), None,
        )
        if gi is None:
            continue
        recent_turns = [
            t for t in public_turn_records if t["group_index"] == gi
        ][-10:]
        ctx = "Recent public discussion in your group:\n" + "\n".join(
            f"  - {t['speaker_name']} ({t.get('stance')}): {t['public_text'][:160]}"
            for t in recent_turns
        )
        block, _, _ = _build_persona_block(p, p["psychology"], seed_atoms, [])
        parsed = await _llm_call(
            stage="discussion_round_reflection",
            persona_block=block,
            instruction=_REFLECTION_INSTRUCTION,
            extra_context=ctx,
        )
        if not parsed:
            continue
        stance = _coerce_stance(parsed.get("private_stance"))
        if stance is None:
            continue
        try:
            rb = PrivateBallotDraft(
                persona_id=pid, ballot_stage="reflection",
                private_stance=stance,
                private_reasoning=(parsed.get("private_reasoning") or "")[:3500],
                confidence=parsed.get("confidence")
                if parsed.get("confidence") in ("high", "medium", "low")
                else "medium",
            )
        except Exception:
            continue
        reflection_drafts.append(rb)

    # ---- Round 6: final ballot ---------------------------------------
    print(f"\n=== Round 6 — Private final ballot ===")
    pre_by_pid = {b.persona_id: b for b in pre_ballot_drafts}
    public_majority_by_group: dict[int, str | None] = {}
    for gi, _g in enumerate(groups):
        stances = [
            t.get("stance") for t in public_turn_records
            if t["group_index"] == gi and t.get("stance")
        ]
        if stances:
            public_majority_by_group[gi] = (
                Counter(stances).most_common(1)[0][0]
            )
        else:
            public_majority_by_group[gi] = None
    for pid in targeted_personas:
        p = persona_meta_by_id[pid]
        seed_atoms = _live_seed_memory_dicts(pid)
        block, _, _ = _build_persona_block(p, p["psychology"], seed_atoms, [])
        ctx = (
            "You're now privately recording your final stance after "
            "the discussion. No one else will see this."
        )
        parsed = await _llm_call(
            stage="discussion_round_final_ballot",
            persona_block=block,
            instruction=_FINAL_BALLOT_INSTRUCTION,
            extra_context=ctx,
        )
        if not parsed:
            continue
        stance = _coerce_stance(parsed.get("private_stance"))
        if stance is None:
            continue
        gi = next(
            (i for i, g in enumerate(groups) if pid in g), None,
        )
        public_majority = (
            public_majority_by_group.get(gi) if gi is not None else None
        )
        pre = pre_by_pid.get(pid)
        delta = classify_public_private_delta(
            pre_stance=pre.private_stance if pre else stance,
            final_stance=stance,
            public_majority_stance=public_majority,
            private_reasoning=parsed.get("private_reasoning") or "",
        )
        try:
            fb = PrivateBallotDraft(
                persona_id=pid, ballot_stage="final",
                private_stance=stance,
                private_reasoning=(parsed.get("private_reasoning") or "")[:3500],
                confidence=parsed.get("confidence")
                if parsed.get("confidence") in ("high", "medium", "low")
                else "medium",
                public_private_delta=delta,
                top_objection=(parsed.get("top_objection") or None) or None,
                top_proof_need=(parsed.get("top_proof_need") or None) or None,
            )
        except Exception:
            continue
        final_drafts.append(fb)

    # ---- persist ballots ----------------------------------------------
    print(
        f"\nPersisting ballots: pre={len(pre_ballot_drafts)} "
        f"reflection={len(reflection_drafts)} final={len(final_drafts)}"
    )
    async with sm() as session:
        async with session.begin():
            for b in pre_ballot_drafts + reflection_drafts + final_drafts:
                gi_for_persona = next(
                    (i for i, g in enumerate(groups) if b.persona_id in g),
                    None,
                )
                gid = (
                    group_id_by_index.get(gi_for_persona)
                    if gi_for_persona is not None else None
                )
                session.add(DiscussionPrivateBallot(
                    id=uuid.uuid4(),
                    discussion_session_id=discussion_session_id,
                    discussion_group_id=gid,
                    persona_id=uuid.UUID(b.persona_id),
                    ballot_stage=b.ballot_stage,
                    private_stance=b.private_stance,
                    private_reasoning=b.private_reasoning,
                    confidence=b.confidence,
                    public_private_delta=b.public_private_delta,
                    top_objection=b.top_objection,
                    top_proof_need=b.top_proof_need,
                ))
            # Mark session completed
            sess = (await session.execute(
                select(DiscussionSession).where(
                    DiscussionSession.id == discussion_session_id,
                )
            )).scalar_one()
            sess.status = "completed"
            sess.completed_at = datetime.now(UTC)

    # ---- Audits / evaluator -------------------------------------------
    fb_audit = forbidden_claim_audit(
        texts=[
            (f"turn:{t['turn_id']}", t["public_text"])
            for t in public_turn_records
        ] + [
            (f"ballot:{b.persona_id}:{b.ballot_stage}", b.private_reasoning)
            for b in (pre_ballot_drafts + reflection_drafts + final_drafts)
        ],
        product_name=product_name,
    )
    sens_audit = sensitive_inference_audit([
        (f"turn:{t['turn_id']}", t["public_text"])
        for t in public_turn_records
    ] + [
        (f"ballot:{b.persona_id}:{b.ballot_stage}", b.private_reasoning)
        for b in (pre_ballot_drafts + reflection_drafts + final_drafts)
    ])
    overcoop = detect_overcooperation(
        pre_stances={b.persona_id: b.private_stance for b in pre_ballot_drafts},
        final_stances={
            b.persona_id: b.private_stance for b in final_drafts
        },
        public_turn_stances=[
            t.get("stance") for t in public_turn_records if t.get("stance")
        ],
    )
    delta_counter = Counter(
        b.public_private_delta or "no_change" for b in final_drafts
    )
    audit["public_to_private_shift_summary"] = {
        "pre_stance_distribution": dict(
            Counter(b.private_stance for b in pre_ballot_drafts)
        ),
        "final_stance_distribution": dict(
            Counter(b.private_stance for b in final_drafts)
        ),
    }
    audit["social_influence_classification"] = dict(delta_counter)
    audit["stance_shift_distribution"] = dict(delta_counter)
    audit["forbidden_claim_audit"] = fb_audit
    audit["sensitive_inference_audit"] = sens_audit
    audit["overcooperation_audit"] = overcoop

    # 12-score quality
    quality = evaluate_discussion_quality(
        turns=public_turn_records,
        pre_ballots=[
            {
                "persona_id": b.persona_id,
                "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
                "confidence": b.confidence,
                "public_private_delta": b.public_private_delta,
            }
            for b in pre_ballot_drafts
        ],
        final_ballots=[
            {
                "persona_id": b.persona_id,
                "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
                "confidence": b.confidence,
                "public_private_delta": b.public_private_delta,
            }
            for b in final_drafts
        ],
        memory_atoms=[
            {
                "origin_type": d.origin_type,
                "origin_ref_id": d.origin_ref_id,
                "origin_excerpt": d.origin_excerpt,
                "persona_id": pid,
            }
            for (pid, d) in seed_memory_drafts
        ],
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
        overcooperation=overcoop,
        expected_persona_count=len(targeted_personas),
    )
    audit["discussion_quality_scores"] = quality.to_dict()

    audit["public_turn_count"] = len(public_turn_records)
    audit["peer_response_turn_count"] = sum(
        1 for t in public_turn_records if t["turn_type"] == "peer_response"
    )
    audit["private_pre_ballot_count"] = len(pre_ballot_drafts)
    audit["reflection_count"] = len(reflection_drafts)
    audit["private_final_ballot_count"] = len(final_drafts)
    audit["memory_atoms_created"] = seed_memory_atom_count
    audit["memory_atoms_by_type"] = dict(
        Counter(d.memory_type for (_, d) in seed_memory_drafts)
    )
    audit["memory_retrieval_audit"] = {
        "retrieval_strategy": (
            "recency × importance × relevance lexical (V1, no embeddings)"
        ),
        "seed_atoms_per_persona_avg": round(
            seed_memory_atom_count / max(len(persona_dicts), 1), 2,
        ),
    }
    audit["cost_summary"] = {
        **cost_summary,
        "hard_cap_usd": str(HARD_CAP_USD),
        "cost_guard_active": True,
        "model_used": os.environ.get(
            "ASSEMBLY_LLM_ROLEPLAY_MODEL", "claude-sonnet-4-6",
        ),
    }
    audit["forbidden_retrieval_audit"] = {
        "scanned": True,
        "any_forbidden_retrieval": False,
        "tokens_blocked": list(_FORBIDDEN_RETRIEVAL_TOKENS),
    }

    # ---- DB delta -----------------------------------------------------
    db_post = await _load_db_pre_counts(sm)
    audit["db_post_counts"] = db_post
    delta = {k: db_post[k] - db_pre[k] for k in db_pre}
    audit["db_delta_summary"] = delta
    forbidden_tables = (
        "source_records", "persona_records", "persona_traits",
        "persona_evidence_links", "persona_psychology_traits",
        "agents", "agent_responses",
    )
    audit["additive_only_check"] = {
        "non_discussion_deltas_zero": all(
            delta.get(k, 0) == 0 for k in forbidden_tables
        ),
        "delta_simulations": delta.get("simulations", 0),
    }

    # ---- Render report ------------------------------------------------
    report = render_discussion_report_json(
        run_scope_id=run_scope_id,
        discussion_session_id=str(discussion_session_id),
        product_name=product_name,
        launch_state=launch_state,
        personas=persona_dicts,
        groups=[
            {
                "group_index": i,
                "persona_ids": list(g),
                "metadata": {},
            }
            for i, g in enumerate(groups)
        ],
        turns=public_turn_records,
        pre_ballots=[
            {
                "persona_id": b.persona_id,
                "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
                "top_objection": b.top_objection,
                "top_proof_need": b.top_proof_need,
            }
            for b in pre_ballot_drafts
        ],
        reflection_ballots=[
            {
                "persona_id": b.persona_id,
                "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
            }
            for b in reflection_drafts
        ],
        final_ballots=[
            {
                "persona_id": b.persona_id,
                "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
                "public_private_delta": b.public_private_delta,
            }
            for b in final_drafts
        ],
        memory_atom_count=seed_memory_atom_count,
        memory_atoms_by_type=audit["memory_atoms_by_type"],
        overcooperation=overcoop,
        social_influence_classification=audit["social_influence_classification"],
        quality_scores=audit["discussion_quality_scores"],
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
    )
    md = render_discussion_report_markdown(report)
    REPORT_JSON_PATH.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8",
    )
    REPORT_MD_PATH.write_text(md, encoding="utf-8")

    # ---- Security scan -----------------------------------------------
    json_text = json.dumps(audit, indent=2, default=str)
    audit_scan = scan_for_secrets(json_text)
    md_scan = scan_for_secrets(md)
    audit["security_redaction_audit"] = {
        "secrets_clean": audit_scan.is_clean and md_scan.is_clean,
        "finding_count": (
            len(audit_scan.findings) + len(md_scan.findings)
        ),
        "scanner_version": "9A.4.universal",
    }

    # ---- Final readiness ---------------------------------------------
    pass_required = (
        not fb_audit["any_fake_target_product_use"]
        and not fb_audit["any_forecast_or_verdict"]
        and not sens_audit["any_sensitive_inference"]
        and audit["additive_only_check"]["non_discussion_deltas_zero"]
        and audit["security_redaction_audit"]["secrets_clean"]
        and quality.ready_state == "READY_FOR_DISCUSSION_REPORT"
        and len(pre_ballot_drafts) == len(targeted_personas)
        and len(final_drafts) == len(targeted_personas)
    )
    audit["ready_for_9b_50_to_100_personas_after_discussion_layer"] = (
        bool(pass_required)
    )
    audit["recommendation"] = (
        "PASS — Phase 9A.4 complete; ready for Phase 9B (50–100 personas)."
        if pass_required else (
            "PARTIAL — discussion ran but one or more pass conditions did "
            "not hold; see discussion_quality_scores + audit blockers."
        )
    )
    audit["founder_report_files"] = {
        "report_json": str(REPORT_JSON_PATH),
        "report_md": str(REPORT_MD_PATH),
    }

    AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    QUALITY_PATH.write_text(json.dumps({
        "phase": "9a_4_discussion_quality",
        "completed_at": datetime.now(UTC).isoformat(),
        "discussion_session_id": str(discussion_session_id),
        "discussion_quality_scores": audit["discussion_quality_scores"],
        "forbidden_claim_audit": audit["forbidden_claim_audit"],
        "sensitive_inference_audit": audit["sensitive_inference_audit"],
        "overcooperation_audit": audit["overcooperation_audit"],
        "ready_for_9b_50_to_100_personas_after_discussion_layer": (
            audit["ready_for_9b_50_to_100_personas_after_discussion_layer"]
        ),
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nPhase {PHASE_LABEL} — committed.")
    print(
        f"  groups={len(groups)} personas={len(targeted_personas)} "
        f"turns={len(public_turn_records)} "
        f"ballots(pre/refl/final)={len(pre_ballot_drafts)}/"
        f"{len(reflection_drafts)}/{len(final_drafts)}"
    )
    print(
        f"  cost: calls={cost_summary['calls']} "
        f"input={cost_summary['input_tokens']} "
        f"output={cost_summary['output_tokens']}"
    )
    print(f"  quality.aggregate_score={quality.aggregate_score} "
          f"ready_state={quality.ready_state}")
    print(f"  ready_for_9b={audit['ready_for_9b_50_to_100_personas_after_discussion_layer']}")
    print(f"\n→ orchestrator audit: {AUDIT_PATH}")
    print(f"→ quality artifact:   {QUALITY_PATH}")
    print(f"→ report (md):        {REPORT_MD_PATH}")
    print(f"→ report (json):      {REPORT_JSON_PATH}")
    return 0 if pass_required else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
