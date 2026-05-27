"""Phase 10A.2 — fresh-society discussion runner.

Adapted from the 9B orchestrator's discussion logic. Takes a persona
list (just-persisted by `live_evidence_pipeline.persist_live_society`)
and runs the 7-round discussion against them, persisting
DiscussionSession + DiscussionGroup + DiscussionTurn +
DiscussionPrivateBallot + PersonaMemoryAtom rows.

All LLM calls go through `cost_guarded_chat`. Bounded retry via
`call_with_retry` from the discussion_layer package.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assembly.config import get_settings
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.models.discussion import (
    DiscussionGroup, DiscussionPrivateBallot, DiscussionSession,
    DiscussionTurn, PersonaMemoryAtom,
)
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait,
)
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.models.simulation import Simulation
from assembly.sources.discussion_layer import (
    assign_groups_stratified, build_seed_memory_atoms, call_with_retry,
    classify_public_private_delta,
)
from assembly.sources.discussion_layer.schemas import PrivateBallotDraft


logger = logging.getLogger(__name__)


_ALLOWED_STANCES = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)


_SYSTEM_PROMPT = (
    "You are a real person in the target market for this product. You "
    "are NOT an AI assistant, NOT a synthetic agent, and NOT a system "
    "evaluator. Stay in character. Speak ONLY for this single persona, "
    "in their voice, the way an actual human would react.\n\n"
    "You did NOT use the unlaunched product — you can compare it to "
    "alternatives you actually know, but you have not bought, used, "
    "owned, or reviewed it. Avoid forecasts, percentages, market "
    "verdicts, or claims about adoption.\n\n"
    "STRICT BUYER VOICE RULES (Phase 10B.1 + 10B.3):\n"
    "  • Speak like a person reacting to a product, not like an "
    "    evaluator. Do NOT mention the simulation, that you are an "
    "    AI, the synthetic society, sample size (e.g. 'n=24'), the "
    "    words 'directional', 'caveat', 'this chat', 'not a "
    "    verdict', 'not a forecast', 'as an agent', 'as a synthetic "
    "    persona', or any other system-level disclosure. Those "
    "    phrases never appear in real human speech.\n"
    "  • You MAY reference real competitors or alternatives you "
    "    plausibly use ('I use an Ember Mug', 'I usually reheat in "
    "    the microwave'). You MAY NOT claim to have used the "
    "    unlaunched product itself.\n"
    "  • Do NOT begin with the same template each round. Avoid stock "
    "    openers like 'Before I get excited', 'I need to know', "
    "    'Until I see specs', 'What would actually move me'. Open "
    "    in your own voice.\n"
    "  • Your stance + reasoning MUST be consistent. RECEPTIVE "
    "    (interested_if_proven) requires a real reason to buy / try "
    "    / seriously evaluate — a personal use case, a clear "
    "    preference over an alternative, or a willingness to "
    "    purchase. Curiosity alone is NOT receptive. If your "
    "    reasoning is mostly proof demands or major safety / "
    "    certification gates, classify as curious_but_unconvinced.\n"
    "  • The PRODUCT FACT LOCK below is the highest-authority "
    "    source of product facts. You may push back on whether a "
    "    claim is credible — phrase that as 'Since the brief says "
    "    X, I'd want proof Y' — but DO NOT ask for facts that the "
    "    lock already provides (price, bundle price, kit contents, "
    "    materials, runtime, temperature, charging, "
    "    dishwasher / microwave claims, launch state, named "
    "    competitors).\n"
    "  • Retrieved evidence describes COMPETITORS — it does not "
    "    redefine the target product. The target product remains "
    "    exactly what the fact lock says.\n\n"
    "Output ONLY the requested JSON; no preamble, no markdown."
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
  "private_reasoning": "<2-4 sentences in your voice as a real person; reference one specific evidence excerpt or competitor or your own situation; do NOT mention the simulation, AI, synthetic society, or sample size>",
  "confidence": "<one of: high, medium, low>",
  "top_objection": "<one short objection in your voice or null>",
  "top_proof_need": "<one short proof item that would change your mind, or null>"
}
""".strip()


_PUBLIC_OPENING_INSTRUCTION = """
Round 1 — PUBLIC opening statement. The other personas in your group
will see what you write.

Output a single JSON object:
{
  "public_text": "<2-4 sentences in your voice; do NOT claim to have used the product; do NOT forecast adoption>",
  "stance": "<one of: curious_but_unconvinced, interested_if_proven, skeptical, likely_reject, needs_more_information>"
}
""".strip()


_CHALLENGE_INSTRUCTION = """
Round 2 — CHALLENGE round. Pose ONE specific challenge to the public
positions you've heard, OR sharpen the strongest objection from your
psychology profile.

Output a single JSON object:
{
  "public_text": "<2-4 sentences; be specific>",
  "stance": "<one of the allowed stances>"
}
""".strip()


_PEER_RESPONSE_INSTRUCTION = """
Round 3 — PEER RESPONSE. You MUST quote or paraphrase one specific
prior turn from the snippet above and respond to it.

Output a single JSON object:
{
  "public_text": "<2-4 sentences responding to a SPECIFIC prior turn>",
  "stance": "<one of the allowed stances>",
  "referenced_turn_ids": ["<turn_id of the prior turn>"]
}
""".strip()


_PROOF_DISCUSSION_INSTRUCTION = """
Round 4 — PROOF DISCUSSION. Discuss what specific PROOF would change
your private stance.

Output a single JSON object:
{
  "public_text": "<2-4 sentences naming specific proof item(s) you'd want>",
  "stance": "<one of the allowed stances>"
}
""".strip()


_REFLECTION_INSTRUCTION = """
Round 5 — PRIVATE REFLECTION. No one else will see this.

Output a single JSON object:
{
  "private_stance": "<one of the allowed stances>",
  "private_reasoning": "<3-5 sentences in your voice as a real person: what argument affected you most? do NOT mention the simulation, AI, synthetic society, or sample size>",
  "confidence": "<one of: high, medium, low>"
}
""".strip()


_FINAL_BALLOT_INSTRUCTION = """
Round 6 — PRIVATE FINAL BALLOT. No one else will see this.

Output a single JSON object:
{
  "private_stance": "<one of: curious_but_unconvinced, interested_if_proven, skeptical, likely_reject, needs_more_information>",
  "private_reasoning": "<3-5 sentences in your voice as a real person explaining whether/why your stance changed; do NOT mention the simulation, AI, synthetic society, or sample size>",
  "confidence": "<one of: high, medium, low>",
  "top_objection": "<one short objection in your voice or null>",
  "top_proof_need": "<one short proof item that would shift you further, or null>"
}
""".strip()


def _safe_json_parse(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
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


def _label(value: float) -> str:
    if value < 0.4:
        return "low"
    if value > 0.6:
        return "high"
    return "medium"


def _parse_tag_value(tags: list[str], key: str, default: str = "") -> str:
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


# -----------------------------------------------------------------------
# Main runner
# -----------------------------------------------------------------------


async def run_live_discussion(
    *,
    sm: Any,
    run_scope_id: str,
    product_name: str,
    persona_ids: list[uuid.UUID],
    provider: LLMProvider,
    hard_cap_usd: Decimal = Decimal("12.00"),
    group_size: int = 6,
    product_fact_card_text: str | None = None,
    amazon_persona_block: str | None = None,
    tech_market_persona_block: str | None = None,
    simulation_seed: int | None = None,
) -> dict[str, Any]:
    """Run the full 7-round discussion against a freshly persisted
    society. Returns an artifact-summary dict for the orchestrator.

    Phase 10B.1: when ``product_fact_card_text`` is provided, it is
    prepended to every persona prompt as a high-authority block.
    Agents may push back on whether a claim is credible, but they
    must not contradict the card or pretend its facts are missing.
    """
    n_total = len(persona_ids)
    if n_total < 4:
        return {
            "skipped": True,
            "reason": f"only {n_total} personas — too few to discuss",
        }

    # Load all the per-persona context the discussion engine needs
    async with sm() as session:
        personas = (await session.execute(
            select(PersonaRecord).where(
                PersonaRecord.id.in_(persona_ids)
            )
        )).scalars().all()
        traits = (await session.execute(
            select(PersonaTrait).where(
                PersonaTrait.persona_id.in_(persona_ids)
            )
        )).scalars().all()
        psych = (await session.execute(
            select(PersonaPsychologyTrait).where(
                PersonaPsychologyTrait.run_scope_id == run_scope_id
            )
        )).scalars().all()
        links = (await session.execute(
            select(PersonaEvidenceLink).where(
                PersonaEvidenceLink.persona_id.in_(persona_ids)
            )
        )).scalars().all()
    psy_by_pid: dict[uuid.UUID, dict[str, float]] = {}
    for t in psych:
        psy_by_pid.setdefault(t.persona_id, {})[t.trait_name] = float(
            t.value_numeric
        )
    traits_by_pid: dict[uuid.UUID, list[Any]] = {}
    for t in traits:
        traits_by_pid.setdefault(t.persona_id, []).append(t)
    links_by_pid: dict[uuid.UUID, list[Any]] = {}
    for l in links:
        links_by_pid.setdefault(l.persona_id, []).append(l)

    persona_dicts: list[dict[str, Any]] = []
    persona_meta_by_id: dict[str, dict[str, Any]] = {}
    persona_uuid_by_id: dict[str, uuid.UUID] = {}
    seed_memory_drafts: list[tuple[str, Any]] = []
    seed_memory_by_pid: dict[str, list[dict[str, Any]]] = {}
    for p in personas:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role"
        ) or (p.segment_label or "unknown")
        provider_family = _parse_tag_value(
            tags, "source_provider_family"
        ) or "unknown"
        psy_value_map = psy_by_pid.get(p.id, {})
        psy_dicts = [
            {
                "trait_id": str(uuid.uuid4()),  # not used downstream
                "trait_name": k, "value_numeric": v,
                "value_label": _label(v), "confidence": "medium",
                "evidence_basis": None, "caveat": None,
            }
            for k, v in psy_value_map.items()
        ]
        persona_dicts.append({
            "persona_id": str(p.id),
            "display_name": p.display_name,
            "normalized_primary_role": normalized_role,
            "source_provider_family": provider_family,
            "psychology_value_map": psy_value_map,
            "psychology": psy_dicts,
            "extraversion": psy_value_map.get("extraversion"),
            "agreeableness": psy_value_map.get("agreeableness"),
            "social_influence_susceptibility": psy_value_map.get(
                "social_influence_susceptibility"
            ),
            "trust_proof_threshold": psy_value_map.get(
                "trust_proof_threshold"
            ),
            "prior_simulation_final_stance": None,
        })
        persona_meta_by_id[str(p.id)] = persona_dicts[-1]
        persona_uuid_by_id[str(p.id)] = p.id
        # Seed memory atoms — built from persona traits + links
        traits_l = [
            {
                "trait_id": str(t.id),
                "field_name": t.field_name,
                "value": t.value,
                "rationale": t.rationale,
                "confidence": float(t.confidence),
                "source_ids": [str(s) for s in (t.source_ids or [])],
            }
            for t in traits_by_pid.get(p.id, [])
        ]
        link_l = [
            {
                "link_id": str(l.id),
                "source_record_id": str(l.source_record_id),
                "excerpt": l.excerpt,
                "contribution_field": l.contribution_field,
            }
            for l in links_by_pid.get(p.id, [])
        ]
        drafts = build_seed_memory_atoms(
            persona_id=str(p.id),
            run_scope_id=run_scope_id,
            persona_traits=traits_l,
            psychology_traits=psy_dicts,
            evidence_links=link_l,
            prior_simulation_responses=[],
        )[:8]
        seed_memory_drafts.extend([(str(p.id), d) for d in drafts])
        seed_memory_by_pid[str(p.id)] = [
            {
                "memory_type": d.memory_type,
                "origin_type": d.origin_type,
                "origin_ref_id": d.origin_ref_id,
                "origin_excerpt": d.origin_excerpt,
                "memory_text": d.memory_text,
                "importance_score": d.importance_score,
            }
            for d in drafts
        ]

    # Stratified group assignment.
    # Phase 12A.10F: when simulation_seed is provided, mix it into
    # the seed string so re-runs with the same seed (even with
    # different run_scope_id) produce identical group assignments.
    # Default behavior unchanged when simulation_seed is None.
    group_count = max(1, n_total // group_size)
    if simulation_seed is not None:
        _group_seed = f"10A.2|{run_scope_id}|simseed:{simulation_seed}"
    else:
        _group_seed = f"10A.2|{run_scope_id}"
    groups = assign_groups_stratified(
        personas=persona_dicts,
        group_count=group_count,
        group_size=group_size,
        seed=_group_seed,
    )
    targeted_personas = [pid for g in groups for pid in g]
    discussion_session_id = uuid.uuid4()
    sim_id = uuid.uuid4()
    group_id_by_index: dict[int, uuid.UUID] = {}
    seed_atom_id_by_origin: dict[tuple[str, str], uuid.UUID] = {}
    async with sm() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id=f"phase_10A_2_live_{run_scope_id[:24]}",
                status="simulating",
                started_at=datetime.now(UTC),
                progress={
                    "phase": "10A.2",
                    "discussion_session_id": str(discussion_session_id),
                    "purpose": "cost_guard_control_row_for_live_discussion",
                    "run_scope_id": run_scope_id,
                },
            ))
            session.add(DiscussionSession(
                id=discussion_session_id,
                run_scope_id=run_scope_id,
                product_name=product_name[:64],
                phase="10A.2",
                session_type="six_round_v1",
                status="running",
                started_at=datetime.now(UTC),
                metadata_={
                    "linked_simulation_id": str(sim_id),
                    "purpose": "live founder brief discussion",
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

    cost_summary = {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "transient_retries": 0, "failed_calls": 0,
    }

    def _build_block(
        p: dict[str, Any],
        seed_atoms: list[dict[str, Any]],
    ) -> tuple[str, dict[str, float], dict[str, str]]:
        psy_l = {
            t["trait_name"]: t["value_label"] for t in p["psychology"]
        }
        psy_v = {
            t["trait_name"]: t["value_numeric"] for t in p["psychology"]
        }
        instr = _PROFILE_INSTRUCTIONS.format(
            **{f"{k}_label": psy_l.get(k, "medium") for k in (
                "openness", "conscientiousness", "extraversion",
                "agreeableness", "neuroticism", "risk_tolerance",
                "novelty_seeking", "trust_proof_threshold",
                "social_influence_susceptibility",
                "category_involvement_or_expertise", "price_sensitivity",
            )}
        )
        mem_block = (
            "\n".join(
                f"- [{a['memory_type']}] {a['memory_text']} "
                f"(origin: {a['origin_excerpt'][:120]})"
                for a in seed_atoms[:8]
            )
            if seed_atoms else "(no relevant memory atoms retrieved)"
        )
        # Phase 10B.1: prepend the Product Fact Card so every prompt
        # carries the highest-authority product facts. The card also
        # lays out the strict buyer-voice rules (no system caveats,
        # no fact re-asks, no wrong-category drift).
        fact_block = (
            f"{product_fact_card_text}\n\n"
            if product_fact_card_text
            else ""
        )
        # Phase 11C.5 — optional Amazon buyer-language block,
        # appended ONLY when all three feature flags are on at the
        # caller's side. `amazon_persona_block` is None otherwise,
        # so production prompts stay byte-for-byte identical to
        # the Phase-11C.4 shape.
        amazon_block = (
            f"\n\n{amazon_persona_block}\n"
            if amazon_persona_block
            else ""
        )
        # Phase 11D.11 — optional tech-market buyer-language block,
        # appended ONLY when all three tech-market flags are on at
        # the caller's side. `tech_market_persona_block` is None
        # otherwise, so production prompts stay byte-for-byte
        # identical to the Phase-11D.9 shape.
        tech_market_block = (
            f"\n\n{tech_market_persona_block}\n"
            if tech_market_persona_block
            else ""
        )
        return (
            f"{fact_block}"
            f"You are {p['display_name']}. Your role context: "
            f"{p['normalized_primary_role']}.\n\n{instr}\n\n"
            f"Relevant memory atoms (each cites a real source):\n{mem_block}"
            f"{amazon_block}"
            f"{tech_market_block}",
            psy_v, psy_l,
        )

    async def _llm_call(
        *,
        stage: str,
        persona_block: str,
        instruction: str,
        extra_context: str = "",
    ) -> dict[str, Any] | None:
        nonlocal cost_summary
        # Phase 12A.10G: `_SYSTEM_PROMPT` is the ~2400-token static
        # roleplay/realism instruction block that's identical across
        # every one of the 168 calls per simulation. Mark it as the
        # cache breakpoint so all subsequent calls within the same
        # cache TTL window reuse it. The user message contains the
        # persona-specific block + per-round dynamic context (NOT
        # cached). When the prompt-cache flag is off, this is a no-op.
        messages = [
            LLMMessage(
                role="system", content=_SYSTEM_PROMPT,
                cache_breakpoint=True,
            ),
            LLMMessage(role="user", content=(
                f"{persona_block}\n\n{extra_context}\n\n{instruction}"
            ).strip()),
        ]

        async def _do_call():
            # Phase 12A.10F: temperature is settings-driven (default
            # 0.6 preserves pre-12A.10F behavior; lower values reduce
            # discussion-ballot variance for repeatability tests at
            # some cost to persona diversity).
            return await cost_guarded_chat(
                sessionmaker=sm,
                simulation_id=sim_id,
                stage=stage,
                messages=messages,
                provider=provider,
                hard_cap_usd=hard_cap_usd,
                max_tokens=600,
                temperature=get_settings().live_discussion_temperature,
                estimated_prompt_tokens=2000,
                estimated_completion_tokens=350,
            )
        result, retry_audit = await call_with_retry(
            fn=_do_call, max_attempts=3, base_delay_seconds=4.0,
            max_delay_seconds=30.0, label=stage,
        )
        cost_summary["transient_retries"] += retry_audit["transient_failures"]
        if not result:
            cost_summary["failed_calls"] += 1
            return None
        cost_summary["calls"] += 1
        cost_summary["input_tokens"] += result.prompt_tokens or 0
        cost_summary["output_tokens"] += result.completion_tokens or 0
        return _safe_json_parse(result.text or "")

    pre_ballot_drafts: list[PrivateBallotDraft] = []
    public_turn_records: list[dict[str, Any]] = []
    reflection_drafts: list[PrivateBallotDraft] = []
    final_drafts: list[PrivateBallotDraft] = []

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

    n = len(targeted_personas)
    pre_instr = _PRE_BALLOT_INSTRUCTION.replace("{n}", str(n))
    final_instr = _FINAL_BALLOT_INSTRUCTION.replace("{n}", str(n))

    # ---- Round 0: pre-ballot
    logger.info("live_discussion: round 0 pre-ballot start (n=%d)", n)
    for pid in targeted_personas:
        p = persona_meta_by_id[pid]
        seed_atoms = seed_memory_by_pid.get(pid, [])
        block, _, _ = _build_block(p, seed_atoms)
        ctx = (
            f"Brief: The product is '{product_name}', launch_state="
            "unlaunched. You have NOT used it. React the way you "
            "would react in real life if you saw this product."
        )
        parsed = await _llm_call(
            stage="discussion_round_pre_ballot",
            persona_block=block,
            instruction=pre_instr,
            extra_context=ctx,
        )
        if not parsed:
            continue
        stance = _coerce_stance(parsed.get("private_stance"))
        if stance is None:
            continue
        try:
            pre_ballot_drafts.append(PrivateBallotDraft(
                persona_id=pid, ballot_stage="pre",
                private_stance=stance,
                private_reasoning=(parsed.get("private_reasoning") or "")[:3500],
                confidence=parsed.get("confidence")
                if parsed.get("confidence") in ("high", "medium", "low")
                else "medium",
                top_objection=(parsed.get("top_objection") or None) or None,
                top_proof_need=(parsed.get("top_proof_need") or None) or None,
            ))
        except Exception:  # noqa: BLE001
            continue

    # ---- Round 1: public_opening
    logger.info("live_discussion: round 1 public_opening start")
    for gi, group in enumerate(groups):
        for tn, pid in enumerate(group):
            p = persona_meta_by_id[pid]
            seed_atoms = seed_memory_by_pid.get(pid, [])
            block, psy_v, _ = _build_block(p, seed_atoms)
            ctx = (
                f"You are in Group {gi + 1} of {len(groups)} discussing "
                f"the unlaunched product '{product_name}'. Personas in "
                "your group: "
                + ", ".join(persona_meta_by_id[pp]["display_name"] for pp in group)
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
            psy_snap = {"persona_id": pid, **psy_v}
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
                "turn_id": str(tid), "group_index": gi,
                "round_number": 1, "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "public_opening", "public_text": text,
                "stance": stance, "referenced_turn_ids": [],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": psy_snap,
            })

    # ---- Rounds 2-4 (challenge, peer_response, proof_discussion)
    for round_num, instr_label, instr in (
        (2, "challenge", _CHALLENGE_INSTRUCTION),
        (3, "peer_response", _PEER_RESPONSE_INSTRUCTION),
        (4, "proof_discussion", _PROOF_DISCUSSION_INSTRUCTION),
    ):
        logger.info("live_discussion: round %d %s start", round_num, instr_label)
        turns_by_id_now = {t["turn_id"]: t for t in public_turn_records}
        for gi, group in enumerate(groups):
            prior_in_group = [
                t for t in public_turn_records if t["group_index"] == gi
            ]
            prior_text = "\n".join(
                f"  - [turn={t['turn_id'][:8]}] {t['speaker_name']} "
                f"({t.get('stance')}): {t['public_text'][:200]}"
                for t in prior_in_group[-12:]
            )
            for tn, pid in enumerate(group):
                p = persona_meta_by_id[pid]
                seed_atoms = seed_memory_by_pid.get(pid, [])
                block, psy_v, _ = _build_block(p, seed_atoms)
                ctx = (
                    f"Recent turns in your group:\n{prior_text}"
                    if prior_text else ""
                )
                parsed = await _llm_call(
                    stage=f"discussion_round_{instr_label}",
                    persona_block=block,
                    instruction=instr,
                    extra_context=ctx,
                )
                if not parsed:
                    continue
                text = (parsed.get("public_text") or "").strip()
                if not text:
                    continue
                stance = _coerce_stance(parsed.get("stance"))
                ref_ids: list[uuid.UUID] = []
                if instr_label == "peer_response":
                    raw_refs = parsed.get("referenced_turn_ids") or []
                    if isinstance(raw_refs, list):
                        for raw in raw_refs:
                            if isinstance(raw, str) and raw in turns_by_id_now:
                                try:
                                    ref_ids.append(uuid.UUID(raw))
                                except ValueError:
                                    continue
                    if not ref_ids and prior_in_group:
                        try:
                            ref_ids = [uuid.UUID(prior_in_group[-1]["turn_id"])]
                        except ValueError:
                            pass
                tid = await _persist_turn(
                    group_index=gi, round_number=round_num, turn_number=tn,
                    speaker_pid=pid, target_pid=None,
                    turn_type=instr_label,
                    public_text=text, stance=stance,
                    ref_turn_ids=ref_ids,
                    ref_memory_atom_ids=[],
                    psy_snapshot={"persona_id": pid, **psy_v},
                )
                public_turn_records.append({
                    "turn_id": str(tid), "group_index": gi,
                    "round_number": round_num, "speaker_persona_id": pid,
                    "speaker_name": p["display_name"],
                    "turn_type": instr_label, "public_text": text,
                    "stance": stance,
                    "referenced_turn_ids": [str(r) for r in ref_ids],
                    "referenced_memory_atom_ids": [],
                    "psychology_control_snapshot": {"persona_id": pid, **psy_v},
                })

    # ---- Round 5: reflection (private)
    logger.info("live_discussion: round 5 reflection start")
    for pid in targeted_personas:
        p = persona_meta_by_id[pid]
        seed_atoms = seed_memory_by_pid.get(pid, [])
        gi = next((i for i, g in enumerate(groups) if pid in g), None)
        if gi is None:
            continue
        recent = [
            t for t in public_turn_records if t["group_index"] == gi
        ][-10:]
        ctx = "Recent public discussion in your group:\n" + "\n".join(
            f"  - {t['speaker_name']} ({t.get('stance')}): "
            f"{t['public_text'][:160]}"
            for t in recent
        )
        block, _, _ = _build_block(p, seed_atoms)
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
            reflection_drafts.append(PrivateBallotDraft(
                persona_id=pid, ballot_stage="reflection",
                private_stance=stance,
                private_reasoning=(parsed.get("private_reasoning") or "")[:3500],
                confidence=parsed.get("confidence")
                if parsed.get("confidence") in ("high", "medium", "low")
                else "medium",
            ))
        except Exception:  # noqa: BLE001
            continue

    # ---- Round 6: final ballot
    logger.info("live_discussion: round 6 final_ballot start")
    pre_by_pid = {b.persona_id: b for b in pre_ballot_drafts}
    for pid in targeted_personas:
        p = persona_meta_by_id[pid]
        seed_atoms = seed_memory_by_pid.get(pid, [])
        block, _, _ = _build_block(p, seed_atoms)
        ctx = (
            "You're now privately recording your final stance after "
            "the discussion. No one else will see this."
        )
        parsed = await _llm_call(
            stage="discussion_round_final_ballot",
            persona_block=block,
            instruction=final_instr,
            extra_context=ctx,
        )
        if not parsed:
            continue
        stance = _coerce_stance(parsed.get("private_stance"))
        if stance is None:
            continue
        gi = next((i for i, g in enumerate(groups) if pid in g), None)
        public_majority = None
        if gi is not None:
            stances = [
                t.get("stance") for t in public_turn_records
                if t["group_index"] == gi and t.get("stance")
            ]
            if stances:
                public_majority = (
                    Counter(stances).most_common(1)[0][0]
                )
        pre = pre_by_pid.get(pid)
        delta = classify_public_private_delta(
            pre_stance=pre.private_stance if pre else stance,
            final_stance=stance,
            public_majority_stance=public_majority,
            private_reasoning=parsed.get("private_reasoning") or "",
        )
        try:
            final_drafts.append(PrivateBallotDraft(
                persona_id=pid, ballot_stage="final",
                private_stance=stance,
                private_reasoning=(parsed.get("private_reasoning") or "")[:3500],
                confidence=parsed.get("confidence")
                if parsed.get("confidence") in ("high", "medium", "low")
                else "medium",
                public_private_delta=delta,
                top_objection=(parsed.get("top_objection") or None) or None,
                top_proof_need=(parsed.get("top_proof_need") or None) or None,
            ))
        except Exception:  # noqa: BLE001
            continue

    # ---- Persist ballots + close session
    async with sm() as session:
        async with session.begin():
            for b in pre_ballot_drafts + reflection_drafts + final_drafts:
                gi_for_p = next(
                    (i for i, g in enumerate(groups) if b.persona_id in g),
                    None,
                )
                gid = (
                    group_id_by_index.get(gi_for_p)
                    if gi_for_p is not None else None
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
            sess = (await session.execute(
                select(DiscussionSession).where(
                    DiscussionSession.id == discussion_session_id,
                )
            )).scalar_one()
            sess.status = "completed"
            sess.completed_at = datetime.now(UTC)
    return {
        "discussion_session_id": str(discussion_session_id),
        "persona_count": n,
        "group_count": len(groups),
        "public_turn_count": len(public_turn_records),
        "peer_response_turn_count": sum(
            1 for t in public_turn_records
            if t["turn_type"] == "peer_response"
        ),
        "pre_ballot_count": len(pre_ballot_drafts),
        "reflection_count": len(reflection_drafts),
        "final_ballot_count": len(final_drafts),
        "memory_atom_count": len(seed_memory_drafts),
        "cost_summary": cost_summary,
    }
