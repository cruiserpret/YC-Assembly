"""Phase 10A.3 — final-ballot repair gate.

Mirrors the 9B.1 reflection-repair pattern but for the round-6 final
ballot. Repair ladder:
  1. strict-JSON LLM retry
  2. stricter-JSON LLM retry
  3. deterministic fallback derived from reflection ballot (or pre
     ballot if reflection is also missing)

Idempotency: only inserts ballots for personas that don't already have
a ``final`` ballot under ``discussion_session_id``. Re-running the
function is safe.

Pass thresholds:
  - target = 100% completeness
  - minimum acceptable = 95% (orchestrator fails the run below this)
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.models.discussion import (
    DiscussionGroup, DiscussionPrivateBallot, DiscussionSession,
    DiscussionTurn,
)
from assembly.models.persona import PersonaRecord
from assembly.models.simulation import Simulation


logger = logging.getLogger(__name__)


_ALLOWED_STANCES = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)

_REPAIR_SYSTEM = (
    "You are repairing a single private final ballot for a "
    "synthetic-society persona. Output ONLY valid JSON — no markdown, "
    "no preamble, no commentary. The persona did NOT use the unlaunched "
    "product."
)

_REPAIR_INSTRUCTION_STRICT = """
Output ONLY this JSON object:
{
  "private_stance": "<one of: curious_but_unconvinced, interested_if_proven, skeptical, likely_reject, needs_more_information>",
  "private_reasoning": "<2-3 sentences in this persona's voice as a real person; do NOT mention the simulation, AI, synthetic society, or sample size>",
  "confidence": "<one of: high, medium, low>",
  "top_objection": "<one short objection or null>",
  "top_proof_need": "<one short proof item or null>"
}
""".strip()


_REPAIR_INSTRUCTION_STRICTER = (
    "STRICT MODE — your previous output was unparseable. Return ONLY "
    "this JSON object with NO additional text:\n"
    + _REPAIR_INSTRUCTION_STRICT
)


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


async def repair_missing_final_ballots(
    *,
    sm: Any,
    run_scope_id: str,
    discussion_session_id: uuid.UUID,
    persona_ids: list[uuid.UUID],
    product_name: str,
    provider: LLMProvider | None,
    hard_cap_usd: Decimal = Decimal("12.00"),
    product_fact_card_text: str | None = None,
) -> dict[str, Any]:
    """Inspect the final-ballot table for missing personas and repair
    them with a 2-step LLM ladder + deterministic fallback. Returns
    an audit dict suitable for ``final_ballot_repair.json``.

    Idempotent — re-running on a complete society is a no-op.
    """
    n_total = len(persona_ids)
    if n_total == 0:
        return {
            "phase": "10a_3_final_ballot_repair",
            "completed_at": datetime.now(UTC).isoformat(),
            "expected_final_ballots": 0,
            "final_ballots_before": 0,
            "final_ballots_after": 0,
            "completeness_before": 1.0,
            "completeness_after": 1.0,
            "missing_persona_ids_before": [],
            "missing_persona_ids_after": [],
            "repair_attempts": 0,
            "llm_strict_repaired_count": 0,
            "llm_stricter_repaired_count": 0,
            "deterministic_fallback_count": 0,
            "deterministic_fallback_persona_ids": [],
            "skipped": True,
            "skipped_reason": "no personas",
        }
    # Fetch existing final ballots + reflection + pre ballots + persona rows
    async with sm() as session:
        existing_finals = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == discussion_session_id
            ).where(
                DiscussionPrivateBallot.ballot_stage == "final"
            )
        )).scalars().all()
        reflections = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == discussion_session_id
            ).where(
                DiscussionPrivateBallot.ballot_stage == "reflection"
            )
        )).scalars().all()
        pres = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id
                == discussion_session_id
            ).where(
                DiscussionPrivateBallot.ballot_stage == "pre"
            )
        )).scalars().all()
        personas = (await session.execute(
            select(PersonaRecord).where(
                PersonaRecord.id.in_(persona_ids)
            )
        )).scalars().all()
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id
                == discussion_session_id,
            )
        )).scalars().all()
    persona_by_id = {p.id: p for p in personas}
    final_pids = {b.persona_id for b in existing_finals}
    reflection_by_pid = {b.persona_id: b for b in reflections}
    pre_by_pid = {b.persona_id: b for b in pres}
    group_id_by_persona: dict[uuid.UUID, uuid.UUID | None] = {}
    for g in groups:
        for pid in (g.persona_ids or []):
            group_id_by_persona[pid] = g.id
    # Determine missing
    missing = [pid for pid in persona_ids if pid not in final_pids]
    final_ballots_before = len(final_pids & set(persona_ids))
    completeness_before = round(
        final_ballots_before / max(n_total, 1), 4,
    )

    audit: dict[str, Any] = {
        "phase": "10a_3_final_ballot_repair",
        "completed_at": None,
        "discussion_session_id": str(discussion_session_id),
        "run_scope_id": run_scope_id,
        "expected_final_ballots": n_total,
        "final_ballots_before": final_ballots_before,
        "completeness_before": completeness_before,
        "missing_persona_ids_before": [str(p) for p in missing],
        "repair_attempts": 0,
        "llm_strict_repaired_count": 0,
        "llm_stricter_repaired_count": 0,
        "deterministic_fallback_count": 0,
        "deterministic_fallback_persona_ids": [],
        "errors": [],
    }
    if not missing:
        audit["completed_at"] = datetime.now(UTC).isoformat()
        audit["final_ballots_after"] = final_ballots_before
        audit["completeness_after"] = completeness_before
        audit["missing_persona_ids_after"] = []
        audit["repair_pass_run"] = False
        return audit

    # Need a Simulation row to act as the cost-guard control row
    sim_id = uuid.uuid4()
    if provider is not None:
        async with sm() as session:
            async with session.begin():
                session.add(Simulation(
                    id=sim_id,
                    user_id=f"phase_10A_3_final_repair_{run_scope_id[:24]}",
                    status="simulating",
                    started_at=datetime.now(UTC),
                    progress={
                        "phase": "10A.3",
                        "discussion_session_id": str(discussion_session_id),
                        "purpose": "cost_guard_control_for_final_repair",
                    },
                ))

    # Build per-persona context for repair LLM calls
    async def _llm_repair(
        *, pid: uuid.UUID, instruction: str,
    ) -> dict[str, Any] | None:
        if provider is None:
            return None
        p = persona_by_id.get(pid)
        if p is None:
            return None
        refl = reflection_by_pid.get(pid)
        pre = pre_by_pid.get(pid)
        snippets = []
        if refl:
            snippets.append(
                f"Your private reflection (round 5): "
                f"stance={refl.private_stance}, reasoning="
                f"{(refl.private_reasoning or '')[:300]}"
            )
        if pre:
            snippets.append(
                f"Your private pre-discussion ballot: "
                f"stance={pre.private_stance}, "
                f"top_objection={pre.top_objection or 'none'}, "
                f"top_proof_need={pre.top_proof_need or 'none'}"
            )
        ctx = "\n".join(snippets) if snippets else (
            "(no prior private ballots available — emit a coherent "
            "final stance based on your role and the unlaunched product)"
        )
        fact_block = (
            f"{product_fact_card_text}\n\n"
            if product_fact_card_text
            else ""
        )
        msg = (
            f"{fact_block}"
            f"You are persona '{p.display_name}' (role: "
            f"{p.segment_label or 'unknown'}).\n\n"
            f"{ctx}\n\n"
            f"{instruction.replace('{n}', str(n_total))}"
        )
        async def _do_call():
            return await cost_guarded_chat(
                sessionmaker=sm,
                simulation_id=sim_id,
                stage="final_ballot_repair",
                messages=[
                    LLMMessage(role="system", content=_REPAIR_SYSTEM),
                    LLMMessage(role="user", content=msg),
                ],
                provider=provider,
                hard_cap_usd=hard_cap_usd,
                max_tokens=400,
                temperature=0.4,
                estimated_prompt_tokens=1200,
                estimated_completion_tokens=250,
            )
        try:
            result = await _do_call()
        except Exception as exc:  # noqa: BLE001
            audit["errors"].append(
                f"{pid}: {type(exc).__name__}: {str(exc)[:120]}"
            )
            return None
        if result is None:
            return None
        return _safe_json_parse(result.text or "")

    repaired_drafts: list[tuple[uuid.UUID, dict[str, Any], str]] = []
    still_missing: list[uuid.UUID] = []
    for pid in missing:
        audit["repair_attempts"] += 1
        # Pass 1: strict
        parsed = await _llm_repair(
            pid=pid, instruction=_REPAIR_INSTRUCTION_STRICT,
        )
        stance = _coerce_stance(parsed.get("private_stance")) if parsed else None
        if stance:
            repaired_drafts.append((pid, parsed, "llm_strict"))
            audit["llm_strict_repaired_count"] += 1
            continue
        # Pass 2: stricter
        parsed = await _llm_repair(
            pid=pid, instruction=_REPAIR_INSTRUCTION_STRICTER,
        )
        stance = _coerce_stance(parsed.get("private_stance")) if parsed else None
        if stance:
            repaired_drafts.append((pid, parsed, "llm_stricter"))
            audit["llm_stricter_repaired_count"] += 1
            continue
        still_missing.append(pid)

    # Pass 3: deterministic fallback
    fallback_drafts: list[tuple[uuid.UUID, dict[str, Any], str]] = []
    for pid in still_missing:
        refl = reflection_by_pid.get(pid)
        pre = pre_by_pid.get(pid)
        # prefer reflection stance, else pre stance, else neutral
        det_stance = (
            refl.private_stance if refl
            else (
                pre.private_stance if pre
                else "needs_more_information"
            )
        )
        if det_stance not in _ALLOWED_STANCES:
            det_stance = "needs_more_information"
        det_reasoning = (
            "Deterministic fallback final ballot — the LLM repair "
            "ladder failed to emit valid JSON for this persona, so "
            "the system carried forward the last valid private "
            "stance from round 5/0."
        )
        fallback_drafts.append((pid, {
            "private_stance": det_stance,
            "private_reasoning": det_reasoning,
            "confidence": "low",
            "top_objection": (pre.top_objection if pre else None),
            "top_proof_need": (pre.top_proof_need if pre else None),
        }, "deterministic_fallback"))
        audit["deterministic_fallback_count"] += 1
        audit["deterministic_fallback_persona_ids"].append(str(pid))

    all_drafts = repaired_drafts + fallback_drafts
    if not all_drafts:
        audit["completed_at"] = datetime.now(UTC).isoformat()
        audit["final_ballots_after"] = final_ballots_before
        audit["completeness_after"] = completeness_before
        audit["missing_persona_ids_after"] = [
            str(p) for p in missing
        ]
        audit["repair_pass_run"] = True
        return audit

    # Persist with idempotency: re-check for existing final ballots
    # under this session before inserting (prevents duplicates if the
    # repair function is called twice).
    async with sm() as session:
        async with session.begin():
            re_existing = (await session.execute(
                select(DiscussionPrivateBallot.persona_id).where(
                    DiscussionPrivateBallot.discussion_session_id
                    == discussion_session_id
                ).where(
                    DiscussionPrivateBallot.ballot_stage == "final"
                )
            )).scalars().all()
            re_existing_set = set(re_existing)
            inserted = 0
            for pid, parsed, mark in all_drafts:
                if pid in re_existing_set:
                    continue
                gid = group_id_by_persona.get(pid)
                stance = _coerce_stance(
                    parsed.get("private_stance")
                ) or "needs_more_information"
                reasoning = (
                    parsed.get("private_reasoning") or ""
                )[:3500]
                if mark.startswith("llm_") and not (
                    "synthetic" in reasoning.lower()
                    or "simulation" in reasoning.lower()
                ):
                    reasoning = (
                        reasoning + " (Synthetic n=" + str(n_total)
                        + " simulation; not a real-world forecast.)"
                    )[:3500]
                conf = parsed.get("confidence")
                if conf not in ("high", "medium", "low"):
                    conf = "low" if mark == "deterministic_fallback" else "medium"
                top_obj = parsed.get("top_objection") or None
                top_proof = parsed.get("top_proof_need") or None
                if isinstance(top_obj, str) and not top_obj.strip():
                    top_obj = None
                if isinstance(top_proof, str) and not top_proof.strip():
                    top_proof = None
                metadata_tag = (
                    f"final_ballot_repair::{mark}::10A.3"
                )
                # Mark deterministic-fallback ballots with a clear suffix
                # in the reasoning so downstream renderers can show
                # the fallback flag.
                if mark == "deterministic_fallback":
                    reasoning = (
                        reasoning
                        + " [deterministic_fallback_marker]"
                    )[:3500]
                else:
                    reasoning = (
                        reasoning
                        + " [repair_marker:" + mark + "]"
                    )[:3500]
                session.add(DiscussionPrivateBallot(
                    id=uuid.uuid4(),
                    discussion_session_id=discussion_session_id,
                    discussion_group_id=gid,
                    persona_id=pid,
                    ballot_stage="final",
                    private_stance=stance,
                    private_reasoning=reasoning,
                    confidence=conf,
                    public_private_delta=None,
                    top_objection=(top_obj[:240] if isinstance(top_obj, str) else None),
                    top_proof_need=(top_proof[:240] if isinstance(top_proof, str) else None),
                ))
                re_existing_set.add(pid)
                inserted += 1
    # Recount
    async with sm() as session:
        finals_after = (await session.execute(
            select(DiscussionPrivateBallot.persona_id).where(
                DiscussionPrivateBallot.discussion_session_id
                == discussion_session_id
            ).where(
                DiscussionPrivateBallot.ballot_stage == "final"
            )
        )).scalars().all()
    finals_after_set = set(finals_after) & set(persona_ids)
    audit["final_ballots_after"] = len(finals_after_set)
    audit["completeness_after"] = round(
        len(finals_after_set) / max(n_total, 1), 4,
    )
    audit["missing_persona_ids_after"] = [
        str(p) for p in persona_ids if p not in finals_after_set
    ]
    audit["repair_pass_run"] = True
    audit["completed_at"] = datetime.now(UTC).isoformat()
    return audit
