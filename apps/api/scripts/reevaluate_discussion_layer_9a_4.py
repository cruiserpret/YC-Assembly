"""Phase 9A.4 — re-evaluate persisted discussion data.

Reads `discussion_sessions / discussion_groups / discussion_turns /
discussion_private_ballots / persona_memory_atoms` for the latest 9A.4
session, re-runs the quality evaluator + audits, and rewrites the audit
+ quality + report files.

NO LLM calls. NO DB writes. Pure read + recompute + emit-files.
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from assembly.db import get_sessionmaker
from assembly.models.discussion import (
    DiscussionGroup,
    DiscussionPrivateBallot,
    DiscussionSession,
    DiscussionTurn,
    PersonaMemoryAtom,
)
from assembly.models.persona import PersonaRecord
from assembly.sources.discussion_layer import (
    classify_public_private_delta,
    detect_overcooperation,
    evaluate_discussion_quality,
    forbidden_claim_audit,
    render_discussion_report_json,
    render_discussion_report_markdown,
    sensitive_inference_audit,
)


AUDIT_ROOT = Path(__file__).resolve().parent.parent / "_audit"
AUDIT_PATH = AUDIT_ROOT / "discussion_layer_9a_4.json"
QUALITY_PATH = AUDIT_ROOT / "discussion_layer_9a_4_quality.json"
REPORT_JSON_PATH = AUDIT_ROOT / "lumaloop_discussion_report_9a_4.json"
REPORT_MD_PATH = AUDIT_ROOT / "lumaloop_discussion_report_9a_4.md"


async def main() -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        sess = (await session.execute(
            select(DiscussionSession)
            .where(DiscussionSession.phase == "9A.4")
            .order_by(DiscussionSession.created_at.desc())
            .limit(1)
        )).scalars().first()
        if not sess:
            print("REFUSED: no 9A.4 discussion_sessions row found.")
            return 2
        run_scope_id = sess.run_scope_id
        product_name = sess.product_name
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
            .where(
                DiscussionPrivateBallot.discussion_session_id == sess.id,
            )
        )).scalars().all()
        all_persona_ids: set = set()
        for g in groups:
            for pid in g.persona_ids:
                all_persona_ids.add(pid)
        personas = (await session.execute(
            select(PersonaRecord)
            .where(PersonaRecord.id.in_(list(all_persona_ids)))
        )).scalars().all()
        atoms = (await session.execute(
            select(PersonaMemoryAtom)
            .where(PersonaMemoryAtom.run_scope_id == run_scope_id)
        )).scalars().all()

    persona_by_id = {p.id: p for p in personas}

    turn_dicts: list[dict[str, Any]] = []
    group_index_by_id = {g.id: g.group_index for g in groups}
    for t in turns:
        turn_dicts.append({
            "turn_id": str(t.id),
            "group_index": group_index_by_id[t.discussion_group_id],
            "round_number": t.round_number,
            "speaker_persona_id": str(t.speaker_persona_id),
            "speaker_name": persona_by_id[t.speaker_persona_id].display_name,
            "turn_type": t.turn_type,
            "public_text": t.public_text or "",
            "stance": t.stance,
            "referenced_turn_ids": [str(x) for x in (t.referenced_turn_ids or [])],
            "referenced_memory_atom_ids": [
                str(x) for x in (t.referenced_memory_atom_ids or [])
            ],
            "psychology_control_snapshot": (
                t.psychology_control_snapshot or {}
            ),
        })

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
        for b in ballots if b.ballot_stage == "pre"
    ]
    refl_dicts = [
        {
            "persona_id": str(b.persona_id),
            "ballot_stage": b.ballot_stage,
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "confidence": b.confidence,
        }
        for b in ballots if b.ballot_stage == "reflection"
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
        for b in ballots if b.ballot_stage == "final"
    ]
    atom_dicts = [
        {
            "origin_type": a.origin_type,
            "origin_ref_id": str(a.origin_ref_id),
            "origin_excerpt": a.origin_excerpt,
            "persona_id": str(a.persona_id),
            "memory_type": a.memory_type,
        }
        for a in atoms
    ]

    fb_audit = forbidden_claim_audit(
        texts=[(f"turn:{t['turn_id']}", t["public_text"]) for t in turn_dicts]
        + [
            (f"ballot:{b['persona_id']}:{b['ballot_stage']}",
             b["private_reasoning"])
            for b in (pre_dicts + refl_dicts + final_dicts)
        ],
        product_name=product_name,
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

    quality = evaluate_discussion_quality(
        turns=turn_dicts,
        pre_ballots=pre_dicts,
        final_ballots=final_dicts,
        memory_atoms=atom_dicts,
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
        overcooperation=overcoop,
        expected_persona_count=len(all_persona_ids),
    )

    delta_counter = Counter(
        b["public_private_delta"] or "no_change" for b in final_dicts
    )

    # Load existing audit (preserve everything we don't recompute)
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    audit["reevaluated_at"] = datetime.now(UTC).isoformat()
    audit["evaluator_note"] = (
        "interaction_score now uses peer_response turns only (the round "
        "explicitly designed for cross-persona interaction); the prior "
        "definition included proof_discussion which is by design self-"
        "stated and dragged the score artificially low."
    )
    audit["forbidden_claim_audit"] = fb_audit
    audit["sensitive_inference_audit"] = sens_audit
    audit["overcooperation_audit"] = overcoop
    audit["discussion_quality_scores"] = quality.to_dict()
    audit["public_to_private_shift_summary"] = {
        "pre_stance_distribution": dict(
            Counter(b["private_stance"] for b in pre_dicts)
        ),
        "final_stance_distribution": dict(
            Counter(b["private_stance"] for b in final_dicts)
        ),
    }
    audit["social_influence_classification"] = dict(delta_counter)
    audit["stance_shift_distribution"] = dict(delta_counter)

    pass_required = (
        not fb_audit["any_fake_target_product_use"]
        and not fb_audit["any_forecast_or_verdict"]
        and not sens_audit["any_sensitive_inference"]
        and audit["additive_only_check"]["non_discussion_deltas_zero"]
        and audit["security_redaction_audit"]["secrets_clean"]
        and quality.ready_state == "READY_FOR_DISCUSSION_REPORT"
        and len(pre_dicts) == len(all_persona_ids)
        and len(final_dicts) == len(all_persona_ids)
    )
    audit["ready_for_9b_50_to_100_personas_after_discussion_layer"] = (
        bool(pass_required)
    )
    audit["recommendation"] = (
        "PASS — Phase 9A.4 complete; ready for Phase 9B (50–100 personas)."
        if pass_required else (
            "PARTIAL — discussion ran but one or more pass conditions did "
            "not hold; see discussion_quality_scores."
        )
    )

    # Persona dicts for renderer
    persona_dicts = [
        {
            "persona_id": str(p.id),
            "display_name": p.display_name,
        }
        for p in personas
    ]
    group_dicts = [
        {
            "group_index": g.group_index,
            "persona_ids": [str(x) for x in g.persona_ids],
            "metadata": g.metadata_,
        }
        for g in groups
    ]

    report = render_discussion_report_json(
        run_scope_id=run_scope_id,
        discussion_session_id=str(sess.id),
        product_name=product_name,
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
        quality_scores=audit["discussion_quality_scores"],
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
    )
    md = render_discussion_report_markdown(report)
    REPORT_JSON_PATH.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8",
    )
    REPORT_MD_PATH.write_text(md, encoding="utf-8")

    AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    QUALITY_PATH.write_text(json.dumps({
        "phase": "9a_4_discussion_quality",
        "completed_at": datetime.now(UTC).isoformat(),
        "discussion_session_id": str(sess.id),
        "discussion_quality_scores": audit["discussion_quality_scores"],
        "forbidden_claim_audit": audit["forbidden_claim_audit"],
        "sensitive_inference_audit": audit["sensitive_inference_audit"],
        "overcooperation_audit": audit["overcooperation_audit"],
        "ready_for_9b_50_to_100_personas_after_discussion_layer": (
            audit["ready_for_9b_50_to_100_personas_after_discussion_layer"]
        ),
    }, indent=2, default=str), encoding="utf-8")

    print(f"Re-evaluated quality.aggregate={quality.aggregate_score} "
          f"ready_state={quality.ready_state}")
    print(
        f"  ready_for_9b="
        f"{audit['ready_for_9b_50_to_100_personas_after_discussion_layer']}"
    )
    return 0 if pass_required else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
