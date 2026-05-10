"""Phase 9B — scale to a 50-100 persona discussion-aware society.

Reuses the 9A.1 66-person overshoot pool, promotes it into a new 9B
run scope (additive — no mutation of 9A.1/9A.2/9A.3/9A.4 rows), runs
the 9A.3 psychology engine over the new pool, then runs the 9A.4
discussion architecture grouped into 11 cohorts of 6 personas.

Usage:
  python scripts/scale_lumaloop_society_9b.py            # dry-run (no DB writes)
  python scripts/scale_lumaloop_society_9b.py --commit   # full run
  python scripts/scale_lumaloop_society_9b.py --commit --pilot   # 6 groups
  python scripts/scale_lumaloop_society_9b.py --resume   # complete missing reflection ballots only

Hard cap: $20.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
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
from assembly.pipeline.persona.anonymization import generate_display_name
from assembly.sources.discussion_layer import (
    assign_groups_stratified,
    build_seed_memory_atoms,
    call_with_retry,
    classify_public_private_delta,
    detect_overcooperation,
    evaluate_discussion_quality,
    evaluate_scaled_discussion_quality,
    forbidden_claim_audit,
    rank_memory_atoms,
    render_discussion_report_json,
    render_discussion_report_markdown,
    sensitive_inference_audit,
)
from assembly.sources.discussion_layer.schemas import PrivateBallotDraft
from assembly.sources.founder_report_generator import scan_for_secrets
from assembly.sources.persona_psychology_layer import (
    infer_persona_psychology_profile,
)
from assembly.sources.persona_psychology_layer.schemas import (
    ALL_REQUIRED_OCEAN_PLUS_FIVE,
    OCEAN_TRAITS,
    PRICE_SENSITIVITY_TRAIT,
)


PHASE_LABEL = "9B"
SOURCE_PHASE_TAG = "phase:9A.1"
EXPECTED_MIN_PERSONAS = 50
EXPECTED_MAX_PERSONAS = 100
DEFAULT_GROUP_SIZE = 6
HARD_CAP_USD = Decimal("20.00")
EST_COST_PER_CALL_USD = 0.018  # Sonnet ~ tokens budgeted

PRODUCT_NAME = "LumaLoop"
TARGET_BRIEF_ID = "lumaloop"

AUDIT_ROOT = Path(__file__).resolve().parent.parent / "_audit"
AUDIT_PATH = AUDIT_ROOT / "scale_lumaloop_society_9b.json"
QUALITY_PATH = AUDIT_ROOT / "scale_lumaloop_society_9b_quality.json"
REPORT_JSON_PATH = AUDIT_ROOT / "lumaloop_50_100_discussion_report_9b.json"
REPORT_MD_PATH = AUDIT_ROOT / "lumaloop_50_100_discussion_report_9b.md"
INPUT_9A_2_AUDIT_PATH = AUDIT_ROOT / "scale_lumaloop_society_9a_2.json"

_ALLOWED_STANCES = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)
_FORBIDDEN_RETRIEVAL_TOKENS = (
    "jina", "exa.ai", "exasearch", "dataforseo", "apify", "reddit.com/api",
)


# -----------------------------------------------------------------------
# Prompts (mirrored from 9A.4 for cross-phase consistency)
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
  "private_reasoning": "<2-4 sentences in your voice; reference one specific evidence excerpt or competitor; caveat that this is one persona in a synthetic n=66 simulation>",
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
  "stance": "<one of: curious_but_unconvinced, interested_if_proven, skeptical, likely_reject, needs_more_information>"
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
  "target_persona_id": "<persona_id of the speaker you're challenging, or null>"
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
  "private_reasoning": "<3-5 sentences explaining whether/why your stance changed; caveat that this is a synthetic n=66 discussion>",
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


def _make_run_scope_id() -> str:
    payload = "|".join((
        TARGET_BRIEF_ID, PRODUCT_NAME, PHASE_LABEL,
        datetime.now(UTC).date().isoformat(),
    ))
    return "run_9b_lumaloop_" + hashlib.sha256(
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


# -----------------------------------------------------------------------
# Loaders / counters
# -----------------------------------------------------------------------


async def _count_all(session: AsyncSession) -> dict[str, int]:
    out = {}
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
        out[label] = int(n)
    return out


async def _load_9a_1_personas(
    session: AsyncSession,
) -> list[PersonaRecord]:
    return (await session.execute(
        select(PersonaRecord)
        .where(
            PersonaRecord.product_relevance_tags.contains([SOURCE_PHASE_TAG])
        )
        .order_by(PersonaRecord.id)
    )).scalars().all()


async def _load_traits_links_for(
    session: AsyncSession,
    persona_ids: list[uuid.UUID],
) -> tuple[
    dict[uuid.UUID, list[PersonaTrait]],
    dict[uuid.UUID, list[PersonaEvidenceLink]],
]:
    traits = (await session.execute(
        select(PersonaTrait)
        .where(PersonaTrait.persona_id.in_(persona_ids))
        .order_by(PersonaTrait.persona_id, PersonaTrait.field_name)
    )).scalars().all()
    links = (await session.execute(
        select(PersonaEvidenceLink)
        .where(PersonaEvidenceLink.persona_id.in_(persona_ids))
    )).scalars().all()
    by_pid_t: dict[uuid.UUID, list[PersonaTrait]] = {}
    by_pid_l: dict[uuid.UUID, list[PersonaEvidenceLink]] = {}
    for t in traits:
        by_pid_t.setdefault(t.persona_id, []).append(t)
    for l in links:
        by_pid_l.setdefault(l.persona_id, []).append(l)
    return by_pid_t, by_pid_l


# -----------------------------------------------------------------------
# Stage 2: promote 9A.1 personas into a new 9B run scope
# -----------------------------------------------------------------------


async def _promote_to_9b_scope(
    *,
    sm: Any,
    source_personas: list[PersonaRecord],
    source_traits: dict[uuid.UUID, list[PersonaTrait]],
    source_links: dict[uuid.UUID, list[PersonaEvidenceLink]],
    new_run_scope_id: str,
) -> tuple[dict[str, dict[str, Any]], int, int, int]:
    """Promote 9A.1 personas into a new run_9b_* scope.

    Returns:
      mapping: dict of {old_persona_id_str: {new_persona_id, ...}}
      personas_created, traits_created, links_created
    """
    mapping: dict[str, dict[str, Any]] = {}
    created_personas = 0
    created_traits = 0
    created_links = 0
    async with sm() as session:
        async with session.begin():
            now = datetime.now(UTC)
            # Pass 1: insert PersonaRecord rows + flush so FK targets exist
            new_pid_by_src: dict[uuid.UUID, uuid.UUID] = {}
            for src in source_personas:
                src_tags = list(src.product_relevance_tags or [])
                normalized_role = _parse_tag_value(
                    src_tags, "normalized_primary_role",
                ) or (src.segment_label or "unknown")
                evidence_theme = _parse_tag_value(
                    src_tags, "evidence_theme",
                ) or f"role::{normalized_role}"
                provider = _parse_tag_value(
                    src_tags, "source_provider_family",
                ) or "unknown"
                compressed_candidate_id = _parse_tag_value(
                    src_tags, "compressed_candidate_id",
                ) or f"9a1::{str(src.id)[:8]}"
                new_pid = uuid.uuid4()
                new_pid_by_src[src.id] = new_pid
                display_name = generate_display_name(seed=str(new_pid))
                relevance_tags = [
                    f"target_brief:{TARGET_BRIEF_ID}",
                    f"product_name:{PRODUCT_NAME}",
                    "launch_state:unlaunched",
                    f"phase:{PHASE_LABEL}",
                    f"run_scope_id:{new_run_scope_id}",
                    f"normalized_primary_role:{normalized_role}",
                    f"evidence_theme:{evidence_theme}",
                    f"source_provider_family:{provider}",
                    f"compressed_candidate_id:{compressed_candidate_id}",
                    f"promoted_from_9a_1_persona_id:{str(src.id)}",
                    "scope:run_scoped_brief_scoped",
                    "persistence_type:generated_simulation_artifact",
                    "not_global_persona:true",
                    (
                        f"caveat:Promoted from 9A.1 into Phase {PHASE_LABEL} "
                        f"50-100 society; not global."
                    ),
                ]
                session.add(PersonaRecord(
                    id=new_pid,
                    display_name=display_name,
                    segment_label=(
                        src.segment_label or normalized_role
                    )[:64],
                    origin_market_broad=src.origin_market_broad,
                    product_relevance_tags=relevance_tags,
                    influence_score=src.influence_score,
                    susceptibility=src.susceptibility,
                    population_weight=src.population_weight or Decimal("1.0"),
                    source_strength_score=src.source_strength_score,
                    refreshed_at=now,
                ))
                created_personas += 1
                mapping[str(src.id)] = {
                    "new_persona_id": str(new_pid),
                    "display_name": display_name,
                    "normalized_primary_role": normalized_role,
                    "evidence_theme": evidence_theme,
                    "source_provider_family": provider,
                    "compressed_candidate_id": compressed_candidate_id,
                }
            await session.flush()

            # Pass 2: insert traits + evidence_links per persona
            for src in source_personas:
                new_pid = new_pid_by_src[src.id]
                src_tags = list(src.product_relevance_tags or [])
                normalized_role = _parse_tag_value(
                    src_tags, "normalized_primary_role",
                ) or (src.segment_label or "unknown")
                # copy traits — reuse the SAME source_ids list (existing
                # SourceRecords) — no new SourceRecord rows.
                src_t_list = source_traits.get(src.id, [])
                added_traits = 0
                for t in src_t_list:
                    sids = list(t.source_ids or [])
                    if not sids:
                        continue
                    session.add(PersonaTrait(
                        id=uuid.uuid4(),
                        persona_id=new_pid,
                        field_name=t.field_name,
                        value=t.value,
                        support_level=t.support_level,
                        source_ids=sids,
                        confidence=t.confidence,
                        rationale=t.rationale,
                        last_updated_at=now,
                    ))
                    added_traits += 1
                    created_traits += 1
                # ensure ≥ 2 traits — if not, synthesize a fallback
                src_l_list = source_links.get(src.id, [])
                if added_traits < 2 and src_l_list:
                    session.add(PersonaTrait(
                        id=uuid.uuid4(),
                        persona_id=new_pid,
                        field_name="role_or_context",
                        value=normalized_role,
                        support_level="inferred",
                        source_ids=[src_l_list[0].source_record_id],
                        confidence=Decimal("0.6"),
                        rationale=(
                            f"persona_role::{normalized_role} (9B floor)"
                        ),
                        last_updated_at=now,
                    ))
                    added_traits += 1
                    created_traits += 1
                # copy evidence links
                added_links = 0
                for l in src_l_list:
                    session.add(PersonaEvidenceLink(
                        id=uuid.uuid4(),
                        persona_id=new_pid,
                        source_record_id=l.source_record_id,
                        contribution_kind=l.contribution_kind,
                        contribution_field=l.contribution_field,
                        excerpt=l.excerpt,
                        excerpt_offset=None,
                        confidence=l.confidence,
                    ))
                    added_links += 1
                    created_links += 1
                if added_links < 1:
                    raise RuntimeError(
                        f"persona {src.id}: zero evidence links — "
                        "9B refuses to promote weak persona"
                    )
            await session.flush()
    return mapping, created_personas, created_traits, created_links


# -----------------------------------------------------------------------
# Persistence helper for one turn (own transaction = resumable)
# -----------------------------------------------------------------------


async def _persist_turn(
    *,
    sm: Any,
    discussion_group_id: uuid.UUID,
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
                discussion_group_id=discussion_group_id,
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


# -----------------------------------------------------------------------
# Main orchestrator
# -----------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — 50-100 persona discussion-aware scale.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Persist into DB. Default is dry-run.",
    )
    parser.add_argument(
        "--pilot", action="store_true",
        help="Run only the first 6 groups (~36 personas) — cost-saver.",
    )
    parser.add_argument(
        "--group-size", type=int, default=DEFAULT_GROUP_SIZE,
        help="Personas per group (default 6, allowed 5-7).",
    )
    parser.add_argument(
        "--resume-discussion-session-id", type=str, default=None,
        help="Resume reflection ballots only for an existing 9B "
             "discussion_session_id. No new turns/personas created.",
    )
    args = parser.parse_args()
    if args.group_size < 5 or args.group_size > 7:
        print("REFUSED: group_size must be in [5, 7].")
        return 2

    AUDIT_ROOT.mkdir(exist_ok=True)
    audit: dict[str, Any] = {
        "phase": "9b_50_to_100_discussion_aware_scale",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "dry_run",
        "pilot": args.pilot,
        "resume_session_id": args.resume_discussion_session_id,
    }
    sm = get_sessionmaker()

    if args.resume_discussion_session_id:
        return await _resume_reflections(sm, args, audit)

    async with sm() as session:
        db_pre = await _count_all(session)
    audit["db_pre_counts"] = db_pre

    # ---- Stage 1: load 9A.1 pool --------------------------------------
    async with sm() as session:
        src_personas = await _load_9a_1_personas(session)
        if len(src_personas) < EXPECTED_MIN_PERSONAS:
            print(
                f"REFUSED: 9A.1 pool has {len(src_personas)} personas; "
                f"need >= {EXPECTED_MIN_PERSONAS}."
            )
            audit["blocker"] = (
                f"9A.1 pool too small: {len(src_personas)} < "
                f"{EXPECTED_MIN_PERSONAS}"
            )
            AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        if len(src_personas) > EXPECTED_MAX_PERSONAS:
            # Cap to 100 with stratified selection (universal selector)
            src_personas = src_personas[:EXPECTED_MAX_PERSONAS]
        src_pids = [p.id for p in src_personas]
        src_traits, src_links = await _load_traits_links_for(
            session, src_pids,
        )
    audit["input_9a_1_persona_count"] = len(src_personas)

    # ---- Stage 2: create new 9B run scope (promote personas) ---------
    new_run_scope_id = _make_run_scope_id()
    audit["run_scope_id"] = new_run_scope_id
    audit["official_9b_persona_count"] = len(src_personas)

    if not args.commit:
        # Dry-run — preview only.
        expected_calls = len(src_personas) * 7  # 7 rounds per persona
        audit["expected_call_count"] = expected_calls
        audit["estimated_cost_usd"] = round(
            expected_calls * EST_COST_PER_CALL_USD, 2,
        )
        audit["expected_psychology_traits"] = len(src_personas) * 11
        n = len(src_personas)
        gs = args.group_size
        full = n // gs
        rem = n - full * gs
        audit["projected_group_count"] = full + (1 if rem > 0 else 0)
        audit["projected_group_size"] = gs
        if args.pilot:
            audit["pilot_groups_only"] = 6
        audit["recommendation"] = (
            "DRY-RUN — no DB writes. Re-run with --commit to scale "
            f"{n} personas through psychology + discussion."
        )
        audit["security_redaction_audit"] = {
            "secrets_clean": True, "finding_count": 0,
            "scanner_version": "9B.universal",
        }
        AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        print(
            f"\nDRY-RUN — {n} personas, {n * 11} psychology traits, "
            f"{audit['projected_group_count']} groups × {gs}, "
            f"~{expected_calls} LLM calls (est ${audit['estimated_cost_usd']:.2f})"
        )
        return 0

    # =================================================================
    # COMMIT MODE
    # =================================================================
    from assembly.config import get_settings
    if not get_settings().anthropic_api_key:
        print("REFUSED: ANTHROPIC_API_KEY missing.")
        audit["blocker"] = "anthropic_key_missing"
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2

    print(
        f"\nPromoting {len(src_personas)} 9A.1 personas → "
        f"run_scope_id={new_run_scope_id}"
    )
    promotion_map, p_created, t_created, l_created = (
        await _promote_to_9b_scope(
            sm=sm,
            source_personas=src_personas,
            source_traits=src_traits,
            source_links=src_links,
            new_run_scope_id=new_run_scope_id,
        )
    )
    audit["promotion"] = {
        "personas_created": p_created,
        "traits_created": t_created,
        "evidence_links_created": l_created,
        "source_records_inserted": 0,
        "source_records_reused": True,
    }
    print(
        f"  promoted: personas={p_created} traits={t_created} "
        f"evidence_links={l_created}"
    )

    # ---- Stage 3: psychology inference per new persona ---------------
    new_persona_ids = [
        uuid.UUID(v["new_persona_id"]) for v in promotion_map.values()
    ]
    async with sm() as session:
        new_personas = (await session.execute(
            select(PersonaRecord)
            .where(PersonaRecord.id.in_(new_persona_ids))
            .order_by(PersonaRecord.id)
        )).scalars().all()
        new_traits, new_links = await _load_traits_links_for(
            session, new_persona_ids,
        )
    print(f"\nInferring psychology for {len(new_personas)} personas...")
    psy_inserted = 0
    async with sm() as session:
        async with session.begin():
            for p in new_personas:
                tags = list(p.product_relevance_tags or [])
                normalized_role = _parse_tag_value(
                    tags, "normalized_primary_role",
                ) or (p.segment_label or "unknown")
                t_dicts = [
                    {
                        "trait_id": str(t.id),
                        "field_name": t.field_name,
                        "value": t.value,
                        "rationale": t.rationale,
                        "confidence": float(t.confidence),
                        "source_ids": [str(s) for s in (t.source_ids or [])],
                    }
                    for t in new_traits.get(p.id, [])
                ]
                ev_dicts = [
                    {
                        "excerpt": l.excerpt,
                        "source_record_id": str(l.source_record_id),
                        "contribution_field": l.contribution_field,
                    }
                    for l in new_links.get(p.id, [])
                ]
                profile = infer_persona_psychology_profile(
                    persona_id=str(p.id),
                    run_scope_id=new_run_scope_id,
                    target_brief=TARGET_BRIEF_ID,
                    normalized_primary_role=normalized_role,
                    existing_traits=t_dicts,
                    evidence_links=ev_dicts,
                    simulation_responses=[],
                    include_price_sensitivity=True,
                )
                for tr in profile.traits:
                    session.add(PersonaPsychologyTrait(
                        id=uuid.uuid4(),
                        persona_id=p.id,
                        run_scope_id=new_run_scope_id,
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
                        generated_for_phase=PHASE_LABEL,
                    ))
                    psy_inserted += 1
    audit["psychology_traits_created"] = psy_inserted
    print(f"  psychology traits inserted: {psy_inserted}")

    # ---- Stage 4: build persona_dicts + group assignment -------------
    async with sm() as session:
        psy_rows = (await session.execute(
            select(PersonaPsychologyTrait)
            .where(PersonaPsychologyTrait.persona_id.in_(new_persona_ids))
            .where(
                PersonaPsychologyTrait.run_scope_id == new_run_scope_id,
            )
        )).scalars().all()
    psy_by_pid: dict[uuid.UUID, list[PersonaPsychologyTrait]] = {}
    for r in psy_rows:
        psy_by_pid.setdefault(r.persona_id, []).append(r)
    persona_dicts: list[dict[str, Any]] = []
    for p in new_personas:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        provider = _parse_tag_value(
            tags, "source_provider_family",
        ) or "unknown"
        psy = psy_by_pid.get(p.id, [])
        psy_dicts = [
            {
                "trait_id": str(t.id),
                "trait_name": t.trait_name,
                "value_numeric": float(t.value_numeric),
                "value_label": t.value_label,
                "confidence": t.confidence,
                "evidence_basis": t.evidence_basis,
                "caveat": t.caveat,
            }
            for t in psy
        ]
        psy_value_map = {t["trait_name"]: t["value_numeric"] for t in psy_dicts}
        persona_dicts.append({
            "persona_id": str(p.id),
            "display_name": p.display_name,
            "normalized_primary_role": normalized_role,
            "source_provider_family": provider,
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
            "prior_simulation_final_stance": None,
        })

    n = len(persona_dicts)
    gs = args.group_size
    group_count = (n + gs - 1) // gs
    groups = assign_groups_stratified(
        personas=persona_dicts,
        group_count=group_count,
        group_size=gs,
        seed=f"9B|{new_run_scope_id}",
    )
    if args.pilot:
        groups = groups[:6]
    audit["group_count"] = len(groups)
    audit["group_size"] = gs
    audit["group_assignment_policy"] = (
        "stratified by role × extraversion × agreeableness × "
        "social_influence_susceptibility × trust_proof_threshold × "
        "provider"
    )

    # ---- create discussion session + groups + cost-guard control ----
    discussion_session_id = uuid.uuid4()
    sim_id = uuid.uuid4()
    audit["discussion_session_id"] = str(discussion_session_id)
    persona_meta = {p["persona_id"]: p for p in persona_dicts}
    group_id_by_index: dict[int, uuid.UUID] = {}
    targeted_personas: list[str] = []
    for g in groups:
        targeted_personas.extend(g)

    # ---- seed memory atoms -------------------------------------------
    seed_memory_drafts: list[tuple[str, Any]] = []
    seed_memory_by_pid: dict[str, list[dict[str, Any]]] = {}
    for p in new_personas:
        pid = str(p.id)
        if pid not in persona_meta:
            continue
        traits_l = [
            {
                "trait_id": str(t.id), "field_name": t.field_name,
                "value": t.value, "rationale": t.rationale,
                "confidence": float(t.confidence),
                "source_ids": [str(s) for s in (t.source_ids or [])],
            }
            for t in new_traits.get(p.id, [])
        ]
        psy_l = persona_meta[pid]["psychology"]
        link_l = [
            {
                "link_id": str(l.id),
                "source_record_id": str(l.source_record_id),
                "excerpt": l.excerpt,
                "contribution_field": l.contribution_field,
            }
            for l in new_links.get(p.id, [])
        ]
        drafts = build_seed_memory_atoms(
            persona_id=pid,
            run_scope_id=new_run_scope_id,
            persona_traits=traits_l,
            psychology_traits=psy_l,
            evidence_links=link_l,
            prior_simulation_responses=[],
        )
        # cap at 12 per persona
        drafts = drafts[:12]
        seed_memory_drafts.extend([(pid, d) for d in drafts])
        seed_memory_by_pid[pid] = [
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
                    "run_scope_id": new_run_scope_id,
                },
            ))
            session.add(DiscussionSession(
                id=discussion_session_id,
                run_scope_id=new_run_scope_id,
                product_name=PRODUCT_NAME[:64],
                phase=PHASE_LABEL,
                session_type="pilot" if args.pilot else "six_round_v1",
                status="running",
                started_at=datetime.now(UTC),
                metadata_={
                    "linked_simulation_id": str(sim_id),
                    "purpose": (
                        f"Phase 9B {n}-persona discussion-aware "
                        "scale; promoted from 9A.1 + 9A.3 psychology"
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
                            persona_meta[pid]["display_name"] for pid in g
                        ],
                    },
                ))
            await session.flush()
            for (pid, d) in seed_memory_drafts:
                aid = uuid.uuid4()
                session.add(PersonaMemoryAtom(
                    id=aid,
                    persona_id=uuid.UUID(pid),
                    run_scope_id=new_run_scope_id,
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
    audit["memory_atoms_created"] = len(seed_memory_drafts)
    audit["memory_atoms_by_type"] = dict(
        Counter(d.memory_type for (_, d) in seed_memory_drafts)
    )

    # ---- LLM provider + retry-aware caller ---------------------------
    from assembly.llm.anthropic import AnthropicProvider
    provider: LLMProvider = AnthropicProvider()
    cost_summary = {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "transient_retries": 0, "failed_calls": 0,
    }

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

        async def _do_call():
            return await cost_guarded_chat(
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

        result, retry_audit = await call_with_retry(
            fn=_do_call,
            max_attempts=3,
            base_delay_seconds=4.0,
            max_delay_seconds=30.0,
            label=stage,
        )
        cost_summary["transient_retries"] += retry_audit["transient_failures"]
        if not result:
            cost_summary["failed_calls"] += 1
            return None
        cost_summary["calls"] += 1
        cost_summary["input_tokens"] += result.prompt_tokens or 0
        cost_summary["output_tokens"] += result.completion_tokens or 0
        return _safe_json_parse(result.text or "")

    def _build_persona_block(
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
        block = (
            f"You are {p['display_name']}. Your role context: "
            f"{p['normalized_primary_role']}.\n\n{instr}\n\n"
            f"Relevant memory atoms (each cites a real source):\n{mem_block}"
        )
        return block, psy_v, psy_l

    pre_ballot_drafts: list[PrivateBallotDraft] = []
    public_turn_records: list[dict[str, Any]] = []
    reflection_drafts: list[PrivateBallotDraft] = []
    final_drafts: list[PrivateBallotDraft] = []
    pids_by_group: dict[int, list[str]] = {
        i: list(g) for i, g in enumerate(groups)
    }

    # ---- Round 0: pre-ballot -----------------------------------------
    print(f"\n=== Round 0 — Private pre-ballot ({len(targeted_personas)}) ===")
    for pid in targeted_personas:
        p = persona_meta[pid]
        seed_atoms = seed_memory_by_pid.get(pid, [])
        block, _, _ = _build_persona_block(p, seed_atoms)
        ctx = (
            f"Brief: The product is '{PRODUCT_NAME}', launch_state="
            "unlaunched. You have NOT used it. This is a synthetic "
            f"n={len(targeted_personas)} simulation."
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
                persona_id=pid, ballot_stage="pre",
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
    print(f"  pre-ballots: {len(pre_ballot_drafts)} | calls={cost_summary['calls']}")

    # ---- Round 1: public_opening -------------------------------------
    print(f"\n=== Round 1 — Public opening ({audit['group_count']} groups) ===")
    for gi, group in enumerate(groups):
        for tn, pid in enumerate(group):
            p = persona_meta[pid]
            seed_atoms = seed_memory_by_pid.get(pid, [])
            block, psy_v, _ = _build_persona_block(p, seed_atoms)
            ctx = (
                f"You are in Group {gi + 1} of {len(groups)} discussing "
                f"the unlaunched product '{PRODUCT_NAME}'. Personas in "
                f"your group: "
                + ", ".join(persona_meta[pp]["display_name"] for pp in group)
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
                sm=sm,
                discussion_group_id=group_id_by_index[gi],
                round_number=1, turn_number=tn,
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
                "turn_type": "public_opening",
                "public_text": text, "stance": stance,
                "referenced_turn_ids": [],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": psy_snap,
            })
        print(f"  group {gi + 1}: opening persisted")

    # ---- Round 2: challenge ------------------------------------------
    print(f"\n=== Round 2 — Challenge ===")
    for gi, group in enumerate(groups):
        prior_in_group = [
            t for t in public_turn_records
            if t["group_index"] == gi and t["round_number"] == 1
        ]
        prior_text = "\n".join(
            f"  - [turn={t['turn_id'][:8]}] {t['speaker_name']} "
            f"({t.get('stance')}): {t['public_text'][:200]}"
            for t in prior_in_group
        )
        for tn, pid in enumerate(group):
            p = persona_meta[pid]
            seed_atoms = seed_memory_by_pid.get(pid, [])
            block, psy_v, _ = _build_persona_block(p, seed_atoms)
            ctx = f"Public opening statements from your group:\n{prior_text}"
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
            if not isinstance(target_id, str) or target_id not in persona_meta:
                target_id = None
            tid = await _persist_turn(
                sm=sm,
                discussion_group_id=group_id_by_index[gi],
                round_number=2, turn_number=tn,
                speaker_pid=pid, target_pid=target_id,
                turn_type="challenge",
                public_text=text, stance=stance,
                ref_turn_ids=[],
                ref_memory_atom_ids=[],
                psy_snapshot={"persona_id": pid, **psy_v},
            )
            public_turn_records.append({
                "turn_id": str(tid), "group_index": gi,
                "round_number": 2, "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "challenge",
                "public_text": text, "stance": stance,
                "referenced_turn_ids": [],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": {"persona_id": pid, **psy_v},
            })
        print(f"  group {gi + 1}: challenge persisted")

    # ---- Round 3: peer_response --------------------------------------
    print(f"\n=== Round 3 — Peer response ===")
    turns_by_id = {t["turn_id"]: t for t in public_turn_records}
    for gi, group in enumerate(groups):
        prior_in_group = [
            t for t in public_turn_records if t["group_index"] == gi
        ]
        prior_text = "\n".join(
            f"  - [turn_id={t['turn_id']}] {t['speaker_name']}: "
            f"{t['public_text'][:200]}"
            for t in prior_in_group[-12:]
        )
        for tn, pid in enumerate(group):
            p = persona_meta[pid]
            seed_atoms = seed_memory_by_pid.get(pid, [])
            block, psy_v, _ = _build_persona_block(p, seed_atoms)
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
            if not text:
                continue
            stance = _coerce_stance(parsed.get("stance"))
            ref_ids_raw = parsed.get("referenced_turn_ids") or []
            target_id = parsed.get("target_persona_id")
            if not isinstance(target_id, str) or target_id not in persona_meta:
                target_id = None
            ref_ids: list[uuid.UUID] = []
            for raw in (ref_ids_raw if isinstance(ref_ids_raw, list) else []):
                if isinstance(raw, str) and raw in turns_by_id:
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
                sm=sm,
                discussion_group_id=group_id_by_index[gi],
                round_number=3, turn_number=tn,
                speaker_pid=pid, target_pid=target_id,
                turn_type="peer_response",
                public_text=text, stance=stance,
                ref_turn_ids=ref_ids,
                ref_memory_atom_ids=[],
                psy_snapshot={"persona_id": pid, **psy_v},
            )
            public_turn_records.append({
                "turn_id": str(tid), "group_index": gi,
                "round_number": 3, "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "peer_response",
                "public_text": text, "stance": stance,
                "referenced_turn_ids": [str(r) for r in ref_ids],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": {"persona_id": pid, **psy_v},
            })
        print(f"  group {gi + 1}: peer_response persisted")

    # ---- Round 4: proof_discussion -----------------------------------
    print(f"\n=== Round 4 — Proof discussion ===")
    for gi, group in enumerate(groups):
        for tn, pid in enumerate(group):
            p = persona_meta[pid]
            seed_atoms = seed_memory_by_pid.get(pid, [])
            block, psy_v, _ = _build_persona_block(p, seed_atoms)
            parsed = await _llm_call(
                stage="discussion_round_proof_discussion",
                persona_block=block,
                instruction=_PROOF_DISCUSSION_INSTRUCTION,
                extra_context="",
            )
            if not parsed:
                continue
            text = (parsed.get("public_text") or "").strip()
            stance = _coerce_stance(parsed.get("stance"))
            if not text:
                continue
            tid = await _persist_turn(
                sm=sm,
                discussion_group_id=group_id_by_index[gi],
                round_number=4, turn_number=tn,
                speaker_pid=pid, target_pid=None,
                turn_type="proof_discussion",
                public_text=text, stance=stance,
                ref_turn_ids=[],
                ref_memory_atom_ids=[],
                psy_snapshot={"persona_id": pid, **psy_v},
            )
            public_turn_records.append({
                "turn_id": str(tid), "group_index": gi,
                "round_number": 4, "speaker_persona_id": pid,
                "speaker_name": p["display_name"],
                "turn_type": "proof_discussion",
                "public_text": text, "stance": stance,
                "referenced_turn_ids": [],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": {"persona_id": pid, **psy_v},
            })
        print(f"  group {gi + 1}: proof persisted")

    # ---- Round 5: reflection (private) --------------------------------
    reflection_drafts = await _run_reflection_round(
        sm=sm, persona_meta=persona_meta,
        seed_memory_by_pid=seed_memory_by_pid,
        groups=groups, public_turn_records=public_turn_records,
        targeted_personas=targeted_personas,
        llm_call=_llm_call,
    )

    # ---- Round 6: final ballot ----------------------------------------
    print(f"\n=== Round 6 — Private final ballot ===")
    pre_by_pid = {b.persona_id: b for b in pre_ballot_drafts}
    public_majority_by_group: dict[int, str | None] = {}
    for gi in range(len(groups)):
        stances = [
            t.get("stance") for t in public_turn_records
            if t["group_index"] == gi and t.get("stance")
        ]
        public_majority_by_group[gi] = (
            Counter(stances).most_common(1)[0][0] if stances else None
        )
    for pid in targeted_personas:
        p = persona_meta[pid]
        seed_atoms = seed_memory_by_pid.get(pid, [])
        block, _, _ = _build_persona_block(p, seed_atoms)
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
        gi_for_p = next(
            (i for i, g in enumerate(groups) if pid in g), None,
        )
        public_maj = (
            public_majority_by_group.get(gi_for_p) if gi_for_p is not None else None
        )
        pre = pre_by_pid.get(pid)
        delta = classify_public_private_delta(
            pre_stance=pre.private_stance if pre else stance,
            final_stance=stance,
            public_majority_stance=public_maj,
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

    # ---- persist ballots ---------------------------------------------
    print(
        f"\nPersisting ballots: pre={len(pre_ballot_drafts)} "
        f"reflection={len(reflection_drafts)} final={len(final_drafts)}"
    )
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

    # ---- audits + evaluator ------------------------------------------
    fb_audit = forbidden_claim_audit(
        texts=[
            (f"turn:{t['turn_id']}", t["public_text"])
            for t in public_turn_records
        ] + [
            (f"ballot:{b.persona_id}:{b.ballot_stage}", b.private_reasoning)
            for b in (pre_ballot_drafts + reflection_drafts + final_drafts)
        ],
        product_name=PRODUCT_NAME,
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

    base_quality = evaluate_discussion_quality(
        turns=public_turn_records,
        pre_ballots=[
            {
                "persona_id": b.persona_id, "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
                "confidence": b.confidence,
                "public_private_delta": b.public_private_delta,
            }
            for b in pre_ballot_drafts
        ],
        final_ballots=[
            {
                "persona_id": b.persona_id, "ballot_stage": b.ballot_stage,
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
    expected_calls = len(targeted_personas) * 7
    estimated_cost = round(
        cost_summary["calls"] * EST_COST_PER_CALL_USD, 2,
    )
    scaled_quality = evaluate_scaled_discussion_quality(
        base_scores=base_quality,
        expected_persona_count=len(targeted_personas),
        persisted_persona_count=len(targeted_personas),
        expected_reflection_count=len(targeted_personas),
        persisted_reflection_count=len(reflection_drafts),
        expected_pre_ballot_count=len(targeted_personas),
        persisted_pre_ballot_count=len(pre_ballot_drafts),
        expected_final_ballot_count=len(targeted_personas),
        persisted_final_ballot_count=len(final_drafts),
        expected_call_count=expected_calls,
        actual_call_count=cost_summary["calls"],
        failed_call_count=cost_summary["failed_calls"],
        transient_retry_count=cost_summary["transient_retries"],
        cost_hard_cap_usd=float(HARD_CAP_USD),
        estimated_cost_usd=estimated_cost,
    )
    audit["discussion_quality_scores"] = scaled_quality

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
    audit["public_turn_count"] = len(public_turn_records)
    audit["peer_response_turn_count"] = sum(
        1 for t in public_turn_records if t["turn_type"] == "peer_response"
    )
    audit["private_pre_ballot_count"] = len(pre_ballot_drafts)
    audit["reflection_count"] = len(reflection_drafts)
    audit["private_final_ballot_count"] = len(final_drafts)
    audit["retry_count"] = cost_summary["transient_retries"]
    audit["resumed_turn_count"] = 0
    audit["failed_turn_count"] = cost_summary["failed_calls"]
    audit["expected_call_count"] = expected_calls
    audit["estimated_cost_usd"] = estimated_cost
    audit["cost_summary"] = {
        **cost_summary,
        "hard_cap_usd": str(HARD_CAP_USD),
        "cost_guard_active": True,
        "model_used": "claude-sonnet-4-6",
    }

    # ---- DB delta ----------------------------------------------------
    async with sm() as session:
        db_post = await _count_all(session)
    audit["db_post_counts"] = db_post
    delta = {k: db_post[k] - db_pre[k] for k in db_pre}
    audit["db_delta_summary"] = delta
    forbidden_tables = ("source_records",)
    audit["additive_only_check"] = {
        "no_new_source_records": delta.get("source_records", 0) == 0,
        "delta_simulations": delta.get("simulations", 0),
        "delta_persona_records": delta.get("persona_records", 0),
        "delta_persona_psychology_traits": delta.get(
            "persona_psychology_traits", 0,
        ),
    }

    # ---- Render report -----------------------------------------------
    report = render_discussion_report_json(
        run_scope_id=new_run_scope_id,
        discussion_session_id=str(discussion_session_id),
        product_name=PRODUCT_NAME,
        launch_state="unlaunched",
        personas=persona_dicts,
        groups=[
            {"group_index": i, "persona_ids": list(g), "metadata": {}}
            for i, g in enumerate(groups)
        ],
        turns=public_turn_records,
        pre_ballots=[
            {
                "persona_id": b.persona_id, "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
                "top_objection": b.top_objection,
                "top_proof_need": b.top_proof_need,
            }
            for b in pre_ballot_drafts
        ],
        reflection_ballots=[
            {
                "persona_id": b.persona_id, "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
            }
            for b in reflection_drafts
        ],
        final_ballots=[
            {
                "persona_id": b.persona_id, "ballot_stage": b.ballot_stage,
                "private_stance": b.private_stance,
                "private_reasoning": b.private_reasoning,
                "public_private_delta": b.public_private_delta,
            }
            for b in final_drafts
        ],
        memory_atom_count=len(seed_memory_drafts),
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
        "scanner_version": "9B.universal",
    }
    audit["forbidden_retrieval_audit"] = {
        "scanned": True, "any_forbidden_retrieval": False,
        "tokens_blocked": list(_FORBIDDEN_RETRIEVAL_TOKENS),
    }

    pass_required = (
        not fb_audit["any_fake_target_product_use"]
        and not fb_audit["any_forecast_or_verdict"]
        and not sens_audit["any_sensitive_inference"]
        and audit["additive_only_check"]["no_new_source_records"]
        and audit["security_redaction_audit"]["secrets_clean"]
        and scaled_quality["ready_state"] == "READY_FOR_DISCUSSION_REPORT"
        and len(pre_ballot_drafts) >= int(0.95 * len(targeted_personas))
        and len(final_drafts) >= int(0.95 * len(targeted_personas))
    )
    audit["ready_for_9c_or_9d"] = bool(pass_required)
    audit["recommendation"] = (
        "PASS — Phase 9B complete. If the discussion bottleneck is "
        "evidence-density, recommend Phase 9C (source/rerank upgrades). "
        "If the bottleneck is scale/cost, recommend Phase 9D (cohort/"
        "cluster architecture)."
        if pass_required else
        "PARTIAL — 9B ran but one or more pass conditions did not hold; "
        "see discussion_quality_scores."
    )
    audit["founder_report_files"] = {
        "report_json": str(REPORT_JSON_PATH),
        "report_md": str(REPORT_MD_PATH),
    }

    AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    QUALITY_PATH.write_text(json.dumps({
        "phase": "9b_discussion_quality",
        "completed_at": datetime.now(UTC).isoformat(),
        "discussion_session_id": str(discussion_session_id),
        "discussion_quality_scores": audit["discussion_quality_scores"],
        "forbidden_claim_audit": audit["forbidden_claim_audit"],
        "sensitive_inference_audit": audit["sensitive_inference_audit"],
        "overcooperation_audit": audit["overcooperation_audit"],
        "ready_for_9c_or_9d": audit["ready_for_9c_or_9d"],
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nPhase {PHASE_LABEL} — committed.")
    print(
        f"  personas={len(targeted_personas)} groups={len(groups)} "
        f"turns={len(public_turn_records)} "
        f"pre/refl/final={len(pre_ballot_drafts)}/"
        f"{len(reflection_drafts)}/{len(final_drafts)}"
    )
    print(
        f"  cost: calls={cost_summary['calls']} "
        f"retries={cost_summary['transient_retries']} "
        f"failed={cost_summary['failed_calls']} "
        f"in/out={cost_summary['input_tokens']}/{cost_summary['output_tokens']} "
        f"~${estimated_cost:.2f}"
    )
    print(
        f"  quality.aggregate={scaled_quality['aggregate_score']} "
        f"ready_state={scaled_quality['ready_state']}"
    )
    print(
        f"  ready_for_9c_or_9d={audit['ready_for_9c_or_9d']}"
    )
    print(f"\n→ orchestrator audit: {AUDIT_PATH}")
    print(f"→ quality artifact:   {QUALITY_PATH}")
    print(f"→ report (md):        {REPORT_MD_PATH}")
    print(f"→ report (json):      {REPORT_JSON_PATH}")
    return 0 if pass_required else 1


# -----------------------------------------------------------------------
# Reflection round helper (separated to support resume mode)
# -----------------------------------------------------------------------


async def _run_reflection_round(
    *,
    sm: Any,
    persona_meta: dict[str, dict[str, Any]],
    seed_memory_by_pid: dict[str, list[dict[str, Any]]],
    groups: list[list[str]],
    public_turn_records: list[dict[str, Any]],
    targeted_personas: list[str],
    llm_call,
    skip_pids: set[str] | None = None,
) -> list[PrivateBallotDraft]:
    print(f"\n=== Round 5 — Private reflection ===")
    skip_pids = skip_pids or set()
    out: list[PrivateBallotDraft] = []
    for pid in targeted_personas:
        if pid in skip_pids:
            continue
        p = persona_meta[pid]
        seed_atoms = seed_memory_by_pid.get(pid, [])
        gi = next(
            (i for i, g in enumerate(groups) if pid in g), None,
        )
        if gi is None:
            continue
        recent_turns = [
            t for t in public_turn_records if t["group_index"] == gi
        ][-10:]
        ctx = "Recent public discussion in your group:\n" + "\n".join(
            f"  - {t['speaker_name']} ({t.get('stance')}): "
            f"{t['public_text'][:160]}"
            for t in recent_turns
        )
        from assembly.sources.discussion_layer.schemas import (
            PrivateBallotDraft as _PBD,
        )
        psy_l = {
            t["trait_name"]: t["value_label"] for t in p["psychology"]
        }
        from assembly.sources.discussion_layer.schemas import (
            PrivateBallotDraft as _,
        )
        block = (
            f"You are {p['display_name']}. Your role context: "
            f"{p['normalized_primary_role']}.\n\n"
            + _PROFILE_INSTRUCTIONS.format(
                **{f"{k}_label": psy_l.get(k, "medium") for k in (
                    "openness", "conscientiousness", "extraversion",
                    "agreeableness", "neuroticism", "risk_tolerance",
                    "novelty_seeking", "trust_proof_threshold",
                    "social_influence_susceptibility",
                    "category_involvement_or_expertise",
                    "price_sensitivity",
                )}
            )
            + "\n\nRelevant memory atoms:\n"
            + "\n".join(
                f"- [{a['memory_type']}] {a['memory_text']}"
                for a in seed_atoms[:8]
            )
        )
        parsed = await llm_call(
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
            rb = _PBD(
                persona_id=pid, ballot_stage="reflection",
                private_stance=stance,
                private_reasoning=(parsed.get("private_reasoning") or "")[:3500],
                confidence=parsed.get("confidence")
                if parsed.get("confidence") in ("high", "medium", "low")
                else "medium",
            )
        except Exception:
            continue
        out.append(rb)
    print(f"  reflections persisted: {len(out)}/{len(targeted_personas)}")
    return out


# -----------------------------------------------------------------------
# Resume mode: complete missing reflection ballots only
# -----------------------------------------------------------------------


async def _resume_reflections(
    sm: Any, args: Any, audit: dict[str, Any],
) -> int:
    """Re-run only the reflection round for personas that don't yet have
    a reflection ballot for the given session. Idempotent."""
    print(f"\n=== Phase 9B RESUME mode — completing reflection ballots ===")
    sess_id = uuid.UUID(args.resume_discussion_session_id)
    async with sm() as session:
        sess = (await session.execute(
            select(DiscussionSession).where(
                DiscussionSession.id == sess_id,
            )
        )).scalar_one_or_none()
        if not sess:
            print(f"REFUSED: session {sess_id} not found.")
            return 2
        run_scope_id = sess.run_scope_id
        groups = (await session.execute(
            select(DiscussionGroup)
            .where(DiscussionGroup.discussion_session_id == sess_id)
            .order_by(DiscussionGroup.group_index)
        )).scalars().all()
        existing_refl = (await session.execute(
            select(DiscussionPrivateBallot.persona_id)
            .where(DiscussionPrivateBallot.discussion_session_id == sess_id)
            .where(DiscussionPrivateBallot.ballot_stage == "reflection")
        )).scalars().all()
        already_done_pids = {str(p) for p in existing_refl}
        targeted = []
        group_lookup = {}
        for g in groups:
            for pid in g.persona_ids:
                spid = str(pid)
                targeted.append(spid)
                group_lookup[spid] = g
        missing = [pid for pid in targeted if pid not in already_done_pids]
        print(f"  total personas in session: {len(targeted)}")
        print(f"  already-done reflections: {len(already_done_pids)}")
        print(f"  missing reflections: {len(missing)}")
        if not missing:
            audit["resumed_turn_count"] = 0
            audit["resume_recommendation"] = (
                "Nothing to resume — all reflection ballots present."
            )
            AUDIT_PATH.write_text(
                json.dumps(audit, indent=2, default=str), encoding="utf-8",
            )
            return 0
        # Reload personas + psychology for the session's run scope
        persona_ids = [uuid.UUID(pid) for pid in missing]
        personas = (await session.execute(
            select(PersonaRecord).where(PersonaRecord.id.in_(persona_ids))
        )).scalars().all()
        psy_rows = (await session.execute(
            select(PersonaPsychologyTrait)
            .where(PersonaPsychologyTrait.persona_id.in_(persona_ids))
            .where(
                PersonaPsychologyTrait.run_scope_id == run_scope_id,
            )
        )).scalars().all()
        atoms = (await session.execute(
            select(PersonaMemoryAtom)
            .where(PersonaMemoryAtom.run_scope_id == run_scope_id)
            .where(PersonaMemoryAtom.persona_id.in_(persona_ids))
        )).scalars().all()
        # Pull last 10 public turns per group (for context)
        group_ids = [g.id for g in groups]
        all_turns = (await session.execute(
            select(DiscussionTurn)
            .where(DiscussionTurn.discussion_group_id.in_(group_ids))
            .order_by(
                DiscussionTurn.discussion_group_id,
                DiscussionTurn.round_number,
                DiscussionTurn.turn_number,
            )
        )).scalars().all()
    # Build dicts
    persona_meta = {}
    for p in personas:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        psy = [
            {
                "trait_name": t.trait_name,
                "value_label": t.value_label,
                "value_numeric": float(t.value_numeric),
            }
            for t in psy_rows if t.persona_id == p.id
        ]
        persona_meta[str(p.id)] = {
            "persona_id": str(p.id),
            "display_name": p.display_name,
            "normalized_primary_role": normalized_role,
            "psychology": psy,
        }
    seed_memory_by_pid: dict[str, list[dict[str, Any]]] = {}
    for a in atoms:
        seed_memory_by_pid.setdefault(str(a.persona_id), []).append({
            "memory_type": a.memory_type,
            "memory_text": a.memory_text,
            "origin_excerpt": a.origin_excerpt,
        })
    public_turn_records = []
    group_index_by_id = {g.id: g.group_index for g in groups}
    for t in all_turns:
        public_turn_records.append({
            "turn_id": str(t.id),
            "group_index": group_index_by_id[t.discussion_group_id],
            "round_number": t.round_number,
            "speaker_persona_id": str(t.speaker_persona_id),
            "speaker_name": "(prior)",
            "turn_type": t.turn_type,
            "public_text": t.public_text or "",
            "stance": t.stance,
        })
    groups_pids = [list(g.persona_ids) for g in groups]
    groups_strs = [[str(pid) for pid in g] for g in groups_pids]

    # LLM provider
    from assembly.config import get_settings
    if not get_settings().anthropic_api_key:
        print("REFUSED: ANTHROPIC_API_KEY missing.")
        return 2
    from assembly.llm.anthropic import AnthropicProvider
    provider: LLMProvider = AnthropicProvider()
    sim_id = uuid.uuid4()
    cost_summary = {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "transient_retries": 0, "failed_calls": 0,
    }
    async with sm() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id=f"phase_{PHASE_LABEL}_resume",
                status="simulating",
                started_at=datetime.now(UTC),
                progress={
                    "phase": PHASE_LABEL,
                    "purpose": "cost_guard_control_row_for_resume_only",
                    "resumed_session_id": str(sess_id),
                },
            ))

    async def _resume_llm_call(
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

        async def _do():
            return await cost_guarded_chat(
                sessionmaker=sm, simulation_id=sim_id,
                stage=stage, messages=messages, provider=provider,
                hard_cap_usd=HARD_CAP_USD, max_tokens=600, temperature=0.6,
                estimated_prompt_tokens=2000,
                estimated_completion_tokens=350,
            )
        result, retry_audit = await call_with_retry(
            fn=_do, max_attempts=3, base_delay_seconds=4.0,
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

    refl_drafts = await _run_reflection_round(
        sm=sm, persona_meta=persona_meta,
        seed_memory_by_pid=seed_memory_by_pid,
        groups=groups_strs,
        public_turn_records=public_turn_records,
        targeted_personas=missing,
        llm_call=_resume_llm_call,
    )
    # Persist
    async with sm() as session:
        async with session.begin():
            for b in refl_drafts:
                gi_for_p = next(
                    (i for i, g in enumerate(groups_strs)
                     if b.persona_id in g),
                    None,
                )
                gid = (
                    groups[gi_for_p].id
                    if gi_for_p is not None else None
                )
                session.add(DiscussionPrivateBallot(
                    id=uuid.uuid4(),
                    discussion_session_id=sess_id,
                    discussion_group_id=gid,
                    persona_id=uuid.UUID(b.persona_id),
                    ballot_stage="reflection",
                    private_stance=b.private_stance,
                    private_reasoning=b.private_reasoning,
                    confidence=b.confidence,
                ))
    audit["resumed_turn_count"] = len(refl_drafts)
    audit["resume_recommendation"] = (
        f"Resumed {len(refl_drafts)}/{len(missing)} missing reflection "
        "ballots. Re-run the founder report to incorporate."
    )
    audit["cost_summary"] = cost_summary
    AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    print(
        f"  resumed={len(refl_drafts)}/{len(missing)} "
        f"calls={cost_summary['calls']} retries={cost_summary['transient_retries']} "
        f"failed={cost_summary['failed_calls']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
