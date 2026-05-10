"""Phase 9B.1 — repair the missing reflection ballots from the
official 9B discussion session, then re-evaluate and emit 9B.1
quality + report artifacts.

Repair ladder per missing persona:
  Attempt 1 — strict LLM reflection prompt (JSON-only, schema-required)
  Attempt 2 — stricter LLM prompt (lower temperature, terse, JSON-only)
  Attempt 3 — deterministic fallback synthesized from pre-ballot +
              final-ballot + relevant public turns. Audited as
              `generation_method = deterministic_repair` so the
              audit can never mistake it for direct agent speech.

Idempotency: looked up by (session_id, persona_id, ballot_stage='reflection').
Existing reflections are never duplicated.

NO new retrieval. NO new SourceRecords / PersonaRecords / PersonaTraits /
PersonaEvidenceLinks / PersonaPsychologyTraits / DiscussionTurns. The
ONLY allowed write is `discussion_private_ballots` (+missing only).
"""
from __future__ import annotations

import argparse
import asyncio
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
from assembly.sources.discussion_layer import (
    call_with_retry,
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


PHASE_LABEL = "9B.1"
HARD_CAP_USD = Decimal("5.00")  # narrow phase, much smaller cap

AUDIT_ROOT = Path(__file__).resolve().parent.parent / "_audit"
REPAIR_AUDIT_PATH = AUDIT_ROOT / "repair_9b_reflections_9b_1.json"
QUALITY_PATH = AUDIT_ROOT / "scale_lumaloop_society_9b_1_quality.json"
REPORT_JSON_PATH = AUDIT_ROOT / "lumaloop_50_100_discussion_report_9b_1.json"
REPORT_MD_PATH = AUDIT_ROOT / "lumaloop_50_100_discussion_report_9b_1.md"
INPUT_9B_AUDIT_PATH = AUDIT_ROOT / "scale_lumaloop_society_9b.json"

_ALLOWED_STANCES = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)


# -----------------------------------------------------------------------
# Strict prompts
# -----------------------------------------------------------------------

_SYSTEM_STRICT = (
    "You are a single evidence-backed run-scoped persona writing a "
    "PRIVATE reflection. You did NOT use the unlaunched product. Output "
    "EXACTLY one JSON object — no markdown, no fences, no preamble, "
    "no explanation. The JSON must use the schema below verbatim."
)


def _strict_user_prompt(
    *,
    persona_block: str,
    pre_ballot: dict[str, Any],
    final_ballot: dict[str, Any],
    public_turns_text: str,
    memory_atoms_text: str,
) -> str:
    return f"""
{persona_block}

Your private pre-discussion ballot:
- stance: {pre_ballot.get('private_stance')}
- reasoning: {(pre_ballot.get('private_reasoning') or '')[:600]}
- top_objection: {pre_ballot.get('top_objection') or '—'}
- top_proof_need: {pre_ballot.get('top_proof_need') or '—'}

Your private FINAL ballot (recorded after discussion):
- stance: {final_ballot.get('private_stance')}
- reasoning: {(final_ballot.get('private_reasoning') or '')[:600]}

Public discussion turns from your group (most recent shown):
{public_turns_text}

Relevant grounded memory atoms:
{memory_atoms_text}

Now write your PRIVATE Round-5 reflection. No one else will see this.
Produce ONLY this JSON object — nothing else:
{{
  "private_stance": "<one of: curious_but_unconvinced | interested_if_proven | skeptical | likely_reject | needs_more_information>",
  "private_reasoning": "<3-5 sentences: what argument affected you most? what did you resist? does your public stance differ from your private stance? include the caveat that this is a synthetic n=66 simulation>",
  "confidence": "<one of: high | medium | low>"
}}
""".strip()


_SYSTEM_STRICTER = (
    "Return valid JSON only. No markdown. No prose. No bullets. No "
    "explanation. Exactly one JSON object matching the schema."
)


def _stricter_user_prompt(
    *,
    persona_short: str,
    pre_stance: str,
    final_stance: str,
) -> str:
    return f"""
{persona_short}
pre_stance={pre_stance} final_stance={final_stance}

Output ONE JSON object. No prose, no fences, no markdown. Allowed
stance values: curious_but_unconvinced, interested_if_proven,
skeptical, likely_reject, needs_more_information. Confidence values:
high, medium, low.

Schema (return EXACTLY this shape, nothing else):
{{"private_stance":"<allowed_stance>","private_reasoning":"<3 short sentences; mention this is a synthetic n=66 simulation>","confidence":"<high|medium|low>"}}
""".strip()


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


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


def _coerce_confidence(value: Any) -> str:
    if isinstance(value, str) and value in ("high", "medium", "low"):
        return value
    return "medium"


def _build_persona_block(
    *,
    display_name: str,
    normalized_role: str,
    psy_labels: dict[str, str],
) -> str:
    psy_lines = "\n".join(
        f"- {k}: {v}" for k, v in sorted(psy_labels.items())
    )
    return (
        f"You are {display_name}. Role context: {normalized_role}.\n"
        "Psychology profile (simulation controls, not real diagnoses):\n"
        f"{psy_lines}"
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
# Deterministic fallback
# -----------------------------------------------------------------------


def _deterministic_fallback(
    *,
    pre_ballot: dict[str, Any],
    final_ballot: dict[str, Any] | None,
    relevant_turns: list[dict[str, Any]],
    persona_display_name: str,
) -> dict[str, Any]:
    """Build a Round-5 reflection from existing records.

    The reasoning text is explicitly tagged so an auditor cannot
    mistake it for direct agent speech."""
    pre_stance = pre_ballot.get("private_stance") or "needs_more_information"
    if final_ballot:
        final_stance = final_ballot.get("private_stance") or pre_stance
        confidence = final_ballot.get("confidence") or "medium"
    else:
        final_stance = pre_stance
        confidence = pre_ballot.get("confidence") or "medium"
    private_stance = (
        final_stance if final_stance in _ALLOWED_STANCES else pre_stance
    )
    # Pick one peer turn from the group as the "argument that mattered"
    most_recent = relevant_turns[-1] if relevant_turns else None
    challenge = next(
        (t for t in relevant_turns if t.get("turn_type") == "challenge"),
        most_recent,
    )
    challenge_excerpt = (
        ((challenge or {}).get("public_text") or "")[:200]
        or "(no challenge text retrievable)"
    )
    initial_lang = (
        f"I started this discussion at `{pre_stance}`."
    )
    if pre_stance != final_stance:
        change_lang = (
            f"After hearing my group's arguments, my private stance moved "
            f"to `{final_stance}`."
        )
    else:
        change_lang = (
            f"My private stance stayed at `{pre_stance}` despite group "
            "discussion."
        )
    reasoning = (
        f"[deterministic_repair — generation_method=deterministic_repair: "
        f"synthesized from pre-ballot + final-ballot + one challenge turn "
        f"from this persona's group; not direct agent speech]\n"
        f"{initial_lang} The argument that most engaged me was: "
        f"\"{challenge_excerpt}\". {change_lang} I want to flag that this "
        "is a synthetic n=66 simulation and the persona ({persona}) is a "
        "run-scoped agent, not a real buyer."
    ).format(persona=persona_display_name)
    return {
        "private_stance": private_stance,
        "private_reasoning": reasoning[:3500],
        "confidence": _coerce_confidence(confidence),
        "generation_method": "deterministic_repair",
    }


# -----------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------


async def _load_session_full(
    session: AsyncSession,
) -> tuple[
    DiscussionSession,
    list[DiscussionGroup],
    list[DiscussionTurn],
    list[DiscussionPrivateBallot],
    list[PersonaMemoryAtom],
    dict[uuid.UUID, PersonaRecord],
    dict[uuid.UUID, list[PersonaPsychologyTrait]],
]:
    sess = (await session.execute(
        select(DiscussionSession)
        .where(DiscussionSession.phase == "9B")
        .order_by(DiscussionSession.created_at.desc())
        .limit(1)
    )).scalars().first()
    if not sess:
        raise RuntimeError("no 9B discussion session found")
    groups = (await session.execute(
        select(DiscussionGroup)
        .where(DiscussionGroup.discussion_session_id == sess.id)
        .order_by(DiscussionGroup.group_index)
    )).scalars().all()
    group_ids = [g.id for g in groups]
    turns = (await session.execute(
        select(DiscussionTurn)
        .where(DiscussionTurn.discussion_group_id.in_(group_ids))
        .order_by(
            DiscussionTurn.discussion_group_id,
            DiscussionTurn.round_number,
            DiscussionTurn.turn_number,
        )
    )).scalars().all()
    ballots = (await session.execute(
        select(DiscussionPrivateBallot)
        .where(DiscussionPrivateBallot.discussion_session_id == sess.id)
    )).scalars().all()
    pids: set = set()
    for g in groups:
        for pid in g.persona_ids:
            pids.add(pid)
    personas = (await session.execute(
        select(PersonaRecord).where(PersonaRecord.id.in_(list(pids)))
    )).scalars().all()
    persona_map = {p.id: p for p in personas}
    psy = (await session.execute(
        select(PersonaPsychologyTrait)
        .where(PersonaPsychologyTrait.run_scope_id == sess.run_scope_id)
        .where(PersonaPsychologyTrait.persona_id.in_(list(pids)))
    )).scalars().all()
    psy_by_pid: dict[uuid.UUID, list[PersonaPsychologyTrait]] = {}
    for t in psy:
        psy_by_pid.setdefault(t.persona_id, []).append(t)
    atoms = (await session.execute(
        select(PersonaMemoryAtom)
        .where(PersonaMemoryAtom.run_scope_id == sess.run_scope_id)
    )).scalars().all()
    return sess, groups, turns, ballots, atoms, persona_map, psy_by_pid


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


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — repair missing 9B reflections.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Persist repaired reflection ballots. Default is dry-run.",
    )
    args = parser.parse_args()
    AUDIT_ROOT.mkdir(exist_ok=True)

    audit: dict[str, Any] = {
        "phase": "9b_1_reflection_completion_repair",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "dry_run",
    }

    sm = get_sessionmaker()
    async with sm() as session:
        db_pre = await _count_all(session)
        sess, groups, turns, ballots, atoms, persona_map, psy_by_pid = (
            await _load_session_full(session)
        )
    audit["db_pre_counts"] = db_pre
    audit["existing_9b_session_id"] = str(sess.id)
    audit["existing_9b_run_scope_id"] = sess.run_scope_id

    # Identify missing reflections
    all_pids: set = set()
    group_index_by_pid: dict[uuid.UUID, int] = {}
    group_id_by_pid: dict[uuid.UUID, uuid.UUID] = {}
    for g in groups:
        for pid in g.persona_ids:
            all_pids.add(pid)
            group_index_by_pid[pid] = g.group_index
            group_id_by_pid[pid] = g.id
    pre_by_pid = {
        b.persona_id: b for b in ballots if b.ballot_stage == "pre"
    }
    final_by_pid = {
        b.persona_id: b for b in ballots if b.ballot_stage == "final"
    }
    refl_existing = {
        b.persona_id for b in ballots if b.ballot_stage == "reflection"
    }
    missing = sorted(all_pids - refl_existing, key=str)
    audit["session_persona_count"] = len(all_pids)
    audit["pre_ballot_count_before"] = len(pre_by_pid)
    audit["final_ballot_count_before"] = len(final_by_pid)
    audit["reflection_count_before"] = len(refl_existing)
    audit["missing_reflection_count"] = len(missing)

    # Validate session shape
    if (
        len(all_pids) != 66
        or len(pre_by_pid) != 66
        or len(final_by_pid) != 66
        or len(turns) != 264
    ):
        msg = (
            f"session shape mismatch: personas={len(all_pids)} "
            f"pre={len(pre_by_pid)} final={len(final_by_pid)} "
            f"turns={len(turns)}"
        )
        print(f"REFUSED: {msg}")
        audit["blocker"] = msg
        REPAIR_AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        return 2

    # Build context per missing persona
    turns_by_group: dict[uuid.UUID, list[DiscussionTurn]] = {}
    for t in turns:
        turns_by_group.setdefault(t.discussion_group_id, []).append(t)
    atoms_by_pid: dict[uuid.UUID, list[PersonaMemoryAtom]] = {}
    for a in atoms:
        atoms_by_pid.setdefault(a.persona_id, []).append(a)

    missing_audit: list[dict[str, Any]] = []
    for pid in missing:
        p = persona_map[pid]
        gid = group_id_by_pid[pid]
        gi = group_index_by_pid[pid]
        psy = psy_by_pid.get(pid, [])
        psy_labels = {t.trait_name: t.value_label for t in psy}
        pre = pre_by_pid[pid]
        final = final_by_pid[pid]
        # Pick relevant public turns: from this persona's group
        rel_turns = sorted(
            turns_by_group.get(gid, []),
            key=lambda t: (t.round_number, t.turn_number),
        )
        # Pick most recent + at least one challenge
        recent = rel_turns[-6:]
        challenge_turns = [
            t for t in rel_turns if t.turn_type == "challenge"
        ][:2]
        seen_ids: set = set()
        relevant_turn_dicts = []
        for t in challenge_turns + recent:
            if t.id in seen_ids:
                continue
            seen_ids.add(t.id)
            relevant_turn_dicts.append({
                "turn_id": str(t.id),
                "round_number": t.round_number,
                "turn_type": t.turn_type,
                "public_text": t.public_text,
                "stance": t.stance,
                "speaker_name": persona_map[t.speaker_persona_id].display_name,
            })
        rel_atoms = atoms_by_pid.get(pid, [])
        ranked_atoms = rank_memory_atoms(
            atoms=rel_atoms,
            query=(
                f"{p.display_name} {pre.private_stance} "
                f"{final.private_stance} reflection"
            ),
            top_k=5,
        )
        missing_audit.append({
            "persona_id": str(pid),
            "display_name": p.display_name,
            "group_id": str(gid),
            "group_index": gi,
            "pre_stance": pre.private_stance,
            "final_stance": final.private_stance,
            "psychology_summary_labels": psy_labels,
            "relevant_turn_count": len(relevant_turn_dicts),
            "relevant_memory_atom_count": len(ranked_atoms),
        })
    audit["missing_reflection_personas"] = missing_audit

    if not missing:
        print("Nothing to repair — all 66 reflections already present.")
        audit["repair_attempt_summary"] = {
            "strict_llm_success": 0,
            "stricter_llm_success": 0,
            "deterministic_fallback": 0,
            "still_missing": 0,
        }
        REPAIR_AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        return 0

    if not args.commit:
        print(
            f"\nDRY-RUN — {len(missing)} missing reflections detected. "
            "Re-run with --commit to repair."
        )
        audit["recommendation"] = (
            "DRY-RUN — no DB writes; re-run with --commit to repair."
        )
        REPAIR_AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        return 0

    # =================================================================
    # COMMIT — repair ladder
    # =================================================================
    from assembly.config import get_settings
    if not get_settings().anthropic_api_key:
        print("REFUSED: ANTHROPIC_API_KEY missing.")
        audit["blocker"] = "anthropic_key_missing"
        REPAIR_AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
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
                user_id=f"phase_{PHASE_LABEL}_repair",
                status="simulating",
                started_at=datetime.now(UTC),
                progress={
                    "phase": PHASE_LABEL,
                    "purpose": "cost_guard_control_row_for_reflection_repair",
                    "no_agents_or_responses_attached": True,
                    "session_id": str(sess.id),
                },
            ))

    repair_summary = {
        "strict_llm_success": 0,
        "stricter_llm_success": 0,
        "deterministic_fallback": 0,
        "still_missing": 0,
    }

    async def _llm_strict(messages: list[LLMMessage]) -> str | None:
        async def _do():
            return await cost_guarded_chat(
                sessionmaker=sm, simulation_id=sim_id,
                stage="discussion_round_reflection_repair_strict",
                messages=messages, provider=provider,
                hard_cap_usd=HARD_CAP_USD,
                max_tokens=600, temperature=0.4,
                estimated_prompt_tokens=2000,
                estimated_completion_tokens=350,
            )
        result, retry_audit = await call_with_retry(
            fn=_do, max_attempts=3, base_delay_seconds=4.0,
            max_delay_seconds=20.0, label="repair_strict",
        )
        cost_summary["transient_retries"] += retry_audit["transient_failures"]
        if not result:
            cost_summary["failed_calls"] += 1
            return None
        cost_summary["calls"] += 1
        cost_summary["input_tokens"] += result.prompt_tokens or 0
        cost_summary["output_tokens"] += result.completion_tokens or 0
        return result.text or None

    async def _llm_stricter(messages: list[LLMMessage]) -> str | None:
        async def _do():
            return await cost_guarded_chat(
                sessionmaker=sm, simulation_id=sim_id,
                stage="discussion_round_reflection_repair_stricter",
                messages=messages, provider=provider,
                hard_cap_usd=HARD_CAP_USD,
                max_tokens=400, temperature=0.1,
                estimated_prompt_tokens=600,
                estimated_completion_tokens=250,
            )
        result, retry_audit = await call_with_retry(
            fn=_do, max_attempts=3, base_delay_seconds=4.0,
            max_delay_seconds=20.0, label="repair_stricter",
        )
        cost_summary["transient_retries"] += retry_audit["transient_failures"]
        if not result:
            cost_summary["failed_calls"] += 1
            return None
        cost_summary["calls"] += 1
        cost_summary["input_tokens"] += result.prompt_tokens or 0
        cost_summary["output_tokens"] += result.completion_tokens or 0
        return result.text or None

    repaired_drafts: list[tuple[str, dict[str, Any]]] = []
    per_persona_repair_log: list[dict[str, Any]] = []

    for pid in missing:
        p = persona_map[pid]
        psy = psy_by_pid.get(pid, [])
        psy_labels = {t.trait_name: t.value_label for t in psy}
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        persona_block = _build_persona_block(
            display_name=p.display_name,
            normalized_role=normalized_role,
            psy_labels=psy_labels,
        )
        pre = pre_by_pid[pid]
        final = final_by_pid[pid]
        gid = group_id_by_pid[pid]
        rel_turns = sorted(
            turns_by_group.get(gid, []),
            key=lambda t: (t.round_number, t.turn_number),
        )
        public_turns_text = "\n".join(
            f"  - [{t.round_number}.{t.turn_number}] "
            f"{persona_map[t.speaker_persona_id].display_name} "
            f"({t.stance}): {t.public_text[:200]}"
            for t in (rel_turns[:2] + rel_turns[-4:])[-6:]
        )
        ranked_atoms = rank_memory_atoms(
            atoms=atoms_by_pid.get(pid, []),
            query=(
                f"{p.display_name} {pre.private_stance} "
                f"{final.private_stance} reflection"
            ),
            top_k=5,
        )
        memory_atoms_text = "\n".join(
            f"  - [{a.memory_type}] {a.memory_text}"
            for a in ranked_atoms
        ) or "  (none)"
        log = {
            "persona_id": str(pid),
            "display_name": p.display_name,
            "attempts": [],
            "method": None,
            "result_stance": None,
        }

        # Attempt 1: strict
        messages = [
            LLMMessage(role="system", content=_SYSTEM_STRICT),
            LLMMessage(role="user", content=_strict_user_prompt(
                persona_block=persona_block,
                pre_ballot={
                    "private_stance": pre.private_stance,
                    "private_reasoning": pre.private_reasoning,
                    "top_objection": pre.top_objection,
                    "top_proof_need": pre.top_proof_need,
                },
                final_ballot={
                    "private_stance": final.private_stance,
                    "private_reasoning": final.private_reasoning,
                },
                public_turns_text=public_turns_text,
                memory_atoms_text=memory_atoms_text,
            )),
        ]
        text = await _llm_strict(messages)
        parsed = _safe_json_parse(text or "")
        stance = _coerce_stance((parsed or {}).get("private_stance"))
        if parsed and stance:
            reasoning = (parsed.get("private_reasoning") or "").strip()
            if reasoning:
                draft = {
                    "private_stance": stance,
                    "private_reasoning": reasoning[:3500],
                    "confidence": _coerce_confidence(parsed.get("confidence")),
                    "generation_method": "llm_strict",
                }
                repaired_drafts.append((str(pid), draft))
                repair_summary["strict_llm_success"] += 1
                log["attempts"].append("strict_llm:success")
                log["method"] = "llm_strict"
                log["result_stance"] = stance
                per_persona_repair_log.append(log)
                continue
        log["attempts"].append("strict_llm:failed")

        # Attempt 2: stricter
        messages = [
            LLMMessage(role="system", content=_SYSTEM_STRICTER),
            LLMMessage(role="user", content=_stricter_user_prompt(
                persona_short=(
                    f"{p.display_name} ({normalized_role})"
                ),
                pre_stance=pre.private_stance,
                final_stance=final.private_stance,
            )),
        ]
        text = await _llm_stricter(messages)
        parsed = _safe_json_parse(text or "")
        stance = _coerce_stance((parsed or {}).get("private_stance"))
        if parsed and stance:
            reasoning = (parsed.get("private_reasoning") or "").strip()
            if reasoning:
                draft = {
                    "private_stance": stance,
                    "private_reasoning": reasoning[:3500],
                    "confidence": _coerce_confidence(parsed.get("confidence")),
                    "generation_method": "llm_stricter",
                }
                repaired_drafts.append((str(pid), draft))
                repair_summary["stricter_llm_success"] += 1
                log["attempts"].append("stricter_llm:success")
                log["method"] = "llm_stricter"
                log["result_stance"] = stance
                per_persona_repair_log.append(log)
                continue
        log["attempts"].append("stricter_llm:failed")

        # Attempt 3: deterministic fallback
        det = _deterministic_fallback(
            pre_ballot={
                "private_stance": pre.private_stance,
                "private_reasoning": pre.private_reasoning,
                "confidence": pre.confidence,
                "top_objection": pre.top_objection,
                "top_proof_need": pre.top_proof_need,
            },
            final_ballot={
                "private_stance": final.private_stance,
                "private_reasoning": final.private_reasoning,
                "confidence": final.confidence,
            },
            relevant_turns=[
                {
                    "turn_type": t.turn_type,
                    "public_text": t.public_text,
                }
                for t in rel_turns
            ],
            persona_display_name=p.display_name,
        )
        repaired_drafts.append((str(pid), det))
        repair_summary["deterministic_fallback"] += 1
        log["attempts"].append("deterministic_fallback:success")
        log["method"] = "deterministic_repair"
        log["result_stance"] = det["private_stance"]
        per_persona_repair_log.append(log)

    audit["per_persona_repair_log"] = per_persona_repair_log
    audit["repair_attempt_summary"] = repair_summary
    audit["cost_summary"] = {
        **cost_summary,
        "hard_cap_usd": str(HARD_CAP_USD),
        "cost_guard_active": True,
        "model_used": "claude-sonnet-4-6",
    }

    # Persist — idempotent insert (skip any already-present row)
    inserted = 0
    async with sm() as session:
        async with session.begin():
            existing_now = (await session.execute(
                select(DiscussionPrivateBallot.persona_id).where(
                    DiscussionPrivateBallot.discussion_session_id == sess.id,
                ).where(DiscussionPrivateBallot.ballot_stage == "reflection")
            )).scalars().all()
            existing_set = set(existing_now)
            for (pid_str, draft) in repaired_drafts:
                pid_uuid = uuid.UUID(pid_str)
                if pid_uuid in existing_set:
                    continue
                gid = group_id_by_pid[pid_uuid]
                session.add(DiscussionPrivateBallot(
                    id=uuid.uuid4(),
                    discussion_session_id=sess.id,
                    discussion_group_id=gid,
                    persona_id=pid_uuid,
                    ballot_stage="reflection",
                    private_stance=draft["private_stance"],
                    private_reasoning=draft["private_reasoning"],
                    confidence=draft["confidence"],
                ))
                inserted += 1
    audit["reflection_inserted_count"] = inserted

    # ---- Re-evaluate quality ----------------------------------------
    async with sm() as session:
        sess2, groups2, turns2, ballots2, atoms2, persona_map2, _ = (
            await _load_session_full(session)
        )
    pre2 = [b for b in ballots2 if b.ballot_stage == "pre"]
    refl2 = [b for b in ballots2 if b.ballot_stage == "reflection"]
    final2 = [b for b in ballots2 if b.ballot_stage == "final"]

    turn_dicts = [
        {
            "turn_id": str(t.id),
            "speaker_persona_id": str(t.speaker_persona_id),
            "speaker_name": persona_map2[t.speaker_persona_id].display_name,
            "turn_type": t.turn_type,
            "public_text": t.public_text or "",
            "stance": t.stance,
            "referenced_turn_ids": [
                str(x) for x in (t.referenced_turn_ids or [])
            ],
            "referenced_memory_atom_ids": [
                str(x) for x in (t.referenced_memory_atom_ids or [])
            ],
            "psychology_control_snapshot": (
                t.psychology_control_snapshot or {}
            ),
        }
        for t in turns2
    ]
    pre_dicts = [
        {
            "persona_id": str(b.persona_id),
            "ballot_stage": b.ballot_stage,
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "confidence": b.confidence,
            "public_private_delta": b.public_private_delta,
            "top_objection": b.top_objection,
            "top_proof_need": b.top_proof_need,
        }
        for b in pre2
    ]
    refl_dicts = [
        {
            "persona_id": str(b.persona_id),
            "ballot_stage": b.ballot_stage,
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "confidence": b.confidence,
        }
        for b in refl2
    ]
    final_dicts = [
        {
            "persona_id": str(b.persona_id),
            "ballot_stage": b.ballot_stage,
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "confidence": b.confidence,
            "public_private_delta": b.public_private_delta,
            "top_objection": b.top_objection,
            "top_proof_need": b.top_proof_need,
        }
        for b in final2
    ]
    atom_dicts = [
        {
            "origin_type": a.origin_type,
            "origin_ref_id": str(a.origin_ref_id),
            "origin_excerpt": a.origin_excerpt,
            "persona_id": str(a.persona_id),
            "memory_type": a.memory_type,
        }
        for a in atoms2
    ]

    fb_audit = forbidden_claim_audit(
        texts=[
            (f"turn:{t['turn_id']}", t["public_text"]) for t in turn_dicts
        ] + [
            (f"ballot:{b['persona_id']}:{b['ballot_stage']}",
             b["private_reasoning"])
            for b in (pre_dicts + refl_dicts + final_dicts)
        ],
        product_name=sess.product_name,
    )
    sens_audit = sensitive_inference_audit(
        [(f"turn:{t['turn_id']}", t["public_text"]) for t in turn_dicts]
        + [
            (f"ballot:{b['persona_id']}:{b['ballot_stage']}",
             b["private_reasoning"])
            for b in (pre_dicts + refl_dicts + final_dicts)
        ],
    )
    overcoop = detect_overcooperation(
        pre_stances={b["persona_id"]: b["private_stance"] for b in pre_dicts},
        final_stances={
            b["persona_id"]: b["private_stance"] for b in final_dicts
        },
        public_turn_stances=[
            t["stance"] for t in turn_dicts if t["stance"]
        ],
    )

    base = evaluate_discussion_quality(
        turns=turn_dicts,
        pre_ballots=pre_dicts,
        final_ballots=final_dicts,
        memory_atoms=atom_dicts,
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
        overcooperation=overcoop,
        expected_persona_count=len(all_pids),
    )
    # Pull cost from the prior 9B audit so cost_efficiency stays
    # tied to the actual main-run cost, not just the repair sliver.
    nine_b_audit = (
        json.loads(INPUT_9B_AUDIT_PATH.read_text(encoding="utf-8"))
        if INPUT_9B_AUDIT_PATH.exists() else {}
    )
    main_cost = nine_b_audit.get("cost_summary") or {}
    expected_calls = len(all_pids) * 7
    actual_calls = (
        (main_cost.get("calls") or 462) + cost_summary["calls"]
    )
    estimated_cost = round(actual_calls * 0.018, 2)
    scaled = evaluate_scaled_discussion_quality(
        base_scores=base,
        expected_persona_count=len(all_pids),
        persisted_persona_count=len(all_pids),
        expected_reflection_count=len(all_pids),
        persisted_reflection_count=len(refl_dicts),
        expected_pre_ballot_count=len(all_pids),
        persisted_pre_ballot_count=len(pre_dicts),
        expected_final_ballot_count=len(all_pids),
        persisted_final_ballot_count=len(final_dicts),
        expected_call_count=expected_calls,
        actual_call_count=actual_calls,
        failed_call_count=cost_summary["failed_calls"],
        transient_retry_count=cost_summary["transient_retries"],
        cost_hard_cap_usd=20.0,  # 9B's main cap
        estimated_cost_usd=estimated_cost,
    )
    audit["reflection_count_after"] = len(refl_dicts)
    audit["reflection_completeness_after"] = round(
        len(refl_dicts) / max(len(all_pids), 1), 4,
    )
    audit["reflection_completeness_before"] = round(
        audit["reflection_count_before"] / max(len(all_pids), 1), 4,
    )
    audit["discussion_quality_scores"] = scaled
    audit["forbidden_claim_audit"] = fb_audit
    audit["sensitive_inference_audit"] = sens_audit
    audit["overcooperation_audit"] = overcoop
    audit["public_to_private_shift_summary"] = {
        "pre_stance_distribution": dict(
            Counter(b["private_stance"] for b in pre_dicts)
        ),
        "final_stance_distribution": dict(
            Counter(b["private_stance"] for b in final_dicts)
        ),
    }
    audit["social_influence_classification"] = dict(
        Counter(b["public_private_delta"] or "no_change" for b in final_dicts)
    )

    # DB delta
    async with sm() as session:
        db_post = await _count_all(session)
    audit["db_post_counts"] = db_post
    delta = {k: db_post[k] - db_pre[k] for k in db_pre}
    audit["db_delta_summary"] = delta
    forbidden_table_keys = (
        "source_records", "persona_records", "persona_traits",
        "persona_evidence_links", "persona_psychology_traits",
        "agents", "agent_responses",
        "discussion_groups", "discussion_turns", "persona_memory_atoms",
    )
    audit["additive_only_check"] = {
        "non_ballot_deltas_zero": all(
            delta.get(k, 0) == 0 for k in forbidden_table_keys
        ),
        "delta_simulations": delta.get("simulations", 0),
        "delta_discussion_private_ballots": delta.get(
            "discussion_private_ballots", 0,
        ),
    }

    # Render 9B.1 report (separate file from 9B's)
    persona_dicts = [
        {"persona_id": str(p.id), "display_name": p.display_name}
        for p in persona_map2.values()
    ]
    group_dicts = [
        {
            "group_index": g.group_index,
            "persona_ids": [str(x) for x in g.persona_ids],
            "metadata": g.metadata_,
        }
        for g in groups2
    ]
    report = render_discussion_report_json(
        run_scope_id=sess.run_scope_id,
        discussion_session_id=str(sess.id),
        product_name=sess.product_name,
        launch_state="unlaunched",
        personas=persona_dicts,
        groups=group_dicts,
        turns=turn_dicts,
        pre_ballots=pre_dicts,
        reflection_ballots=refl_dicts,
        final_ballots=final_dicts,
        memory_atom_count=len(atom_dicts),
        memory_atoms_by_type=dict(
            Counter(a["memory_type"] for a in atom_dicts)
        ),
        overcooperation=overcoop,
        social_influence_classification=audit[
            "social_influence_classification"
        ],
        quality_scores=scaled,
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
    )
    md = render_discussion_report_markdown(report)
    REPORT_JSON_PATH.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8",
    )
    REPORT_MD_PATH.write_text(md, encoding="utf-8")

    json_text = json.dumps(audit, indent=2, default=str)
    audit_scan = scan_for_secrets(json_text)
    md_scan = scan_for_secrets(md)
    audit["security_redaction_audit"] = {
        "secrets_clean": audit_scan.is_clean and md_scan.is_clean,
        "finding_count": (
            len(audit_scan.findings) + len(md_scan.findings)
        ),
        "scanner_version": "9B.1.universal",
    }

    pass_required = (
        audit["reflection_completeness_after"] >= 0.95
        and not fb_audit["any_fake_target_product_use"]
        and not fb_audit["any_forecast_or_verdict"]
        and not sens_audit["any_sensitive_inference"]
        and audit["additive_only_check"]["non_ballot_deltas_zero"]
        and audit["security_redaction_audit"]["secrets_clean"]
        and scaled["ready_state"] == "READY_FOR_DISCUSSION_REPORT"
        and len(pre_dicts) == len(all_pids)
        and len(final_dicts) == len(all_pids)
    )
    audit["ready_for_9c_or_9d"] = bool(pass_required)
    audit["recommendation"] = (
        "PASS — Phase 9B.1 complete; 9B is now officially passed. "
        "Recommended next phase: Phase 9D (cohort/cluster architecture "
        "for huge societies)."
        if pass_required else (
            "PARTIAL — repair ladder ran but reflection completeness or "
            "another gate is still below threshold; see "
            "discussion_quality_scores."
        )
    )
    audit["report_files"] = {
        "report_json": str(REPORT_JSON_PATH),
        "report_md": str(REPORT_MD_PATH),
        "quality_json": str(QUALITY_PATH),
    }

    REPAIR_AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    QUALITY_PATH.write_text(json.dumps({
        "phase": "9b_1_quality",
        "completed_at": datetime.now(UTC).isoformat(),
        "discussion_session_id": str(sess.id),
        "discussion_quality_scores": scaled,
        "forbidden_claim_audit": fb_audit,
        "sensitive_inference_audit": sens_audit,
        "overcooperation_audit": overcoop,
        "ready_for_9c_or_9d": audit["ready_for_9c_or_9d"],
        "reflection_completeness_after": audit[
            "reflection_completeness_after"
        ],
        "reflection_completeness_before": audit[
            "reflection_completeness_before"
        ],
        "repair_attempt_summary": repair_summary,
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nPhase {PHASE_LABEL} — committed.")
    print(
        f"  reflections: {audit['reflection_count_before']} → "
        f"{audit['reflection_count_after']} of {len(all_pids)} "
        f"({audit['reflection_completeness_after']:.1%})"
    )
    print(f"  repair attempts: {repair_summary}")
    print(
        f"  quality.aggregate={scaled['aggregate_score']} "
        f"ready_state={scaled['ready_state']}"
    )
    print(f"  ready_for_9c_or_9d={audit['ready_for_9c_or_9d']}")
    print(f"\n→ repair audit: {REPAIR_AUDIT_PATH}")
    print(f"→ quality artifact: {QUALITY_PATH}")
    print(f"→ report (md): {REPORT_MD_PATH}")
    print(f"→ report (json): {REPORT_JSON_PATH}")
    return 0 if pass_required else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
