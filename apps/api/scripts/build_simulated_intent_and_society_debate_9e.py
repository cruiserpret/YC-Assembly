"""Phase 9E — build the simulated-intent layer + cross-cohort
argument-propagation pass over the 9B/9D society.

Loads the existing 9B run-scope (no new retrieval), reuses the 9D
cohort summaries, infers one simulated-intent record per persona via
deterministic rules, extracts society-wide arguments, propagates them
deterministically across cohorts, evaluates, persists, and emits the
founder-facing report.

NO LLM calls. NO mutation of 9A/9B/9D rows. NO new retrieval.

Usage:
  python scripts/build_simulated_intent_and_society_debate_9e.py            # dry-run
  python scripts/build_simulated_intent_and_society_debate_9e.py --commit   # full
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
from assembly.models.agent import Agent
from assembly.models.cohort import (
    SocietyCohort,
    SocietyCohortEvidenceLink,
    SocietyCohortRollup,
)
from assembly.models.discussion import (
    DiscussionGroup,
    DiscussionPrivateBallot,
    DiscussionSession,
    DiscussionTurn,
    PersonaMemoryAtom,
)
from assembly.models.intent import (
    SimulatedIntent,
    SimulatedIntentRollup,
    SocietyArgument,
    SocietyArgumentPropagation,
)
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.models.round import AgentResponse
from assembly.models.simulation import Simulation
from assembly.sources.discussion_layer import (
    forbidden_claim_audit,
    sensitive_inference_audit,
)
from assembly.sources.founder_report_generator import scan_for_secrets
from assembly.sources.intent_layer import (
    build_intent_rollup,
    evaluate_intent_and_debate_quality,
    extract_society_arguments,
    infer_simulated_intent,
    propagate_arguments_across_cohorts,
    render_intent_and_debate_report_json,
    render_intent_and_debate_report_markdown,
)


PHASE_LABEL = "9E"
EXPECTED_PERSONA_COUNT = 66

AUDIT_ROOT = Path(__file__).resolve().parent.parent / "_audit"
INTENT_AUDIT_PATH = AUDIT_ROOT / "simulated_intent_layer_9e.json"
DEBATE_AUDIT_PATH = AUDIT_ROOT / "society_wide_debate_9e.json"
DEBATE_QUALITY_PATH = AUDIT_ROOT / "society_wide_debate_9e_quality.json"
REPORT_JSON_PATH = (
    AUDIT_ROOT / "lumaloop_intent_and_society_debate_report_9e.json"
)
REPORT_MD_PATH = (
    AUDIT_ROOT / "lumaloop_intent_and_society_debate_report_9e.md"
)


def _parse_tag_value(
    tags: list[str], key: str, default: str = "",
) -> str:
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


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
        ("society_cohorts", SocietyCohort),
        ("society_cohort_evidence_links", SocietyCohortEvidenceLink),
        ("society_cohort_rollups", SocietyCohortRollup),
        ("simulated_intents", SimulatedIntent),
        ("simulated_intent_rollups", SimulatedIntentRollup),
        ("society_arguments", SocietyArgument),
        ("society_argument_propagation", SocietyArgumentPropagation),
    ):
        n = (await session.execute(
            select(func.count()).select_from(table)
        )).scalar_one()
        out[label] = int(n)
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — intent + society-wide debate.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Persist into 4 new intent tables. Default is dry-run.",
    )
    args = parser.parse_args()
    AUDIT_ROOT.mkdir(exist_ok=True)
    audit: dict[str, Any] = {
        "phase": "9e_simulated_intent_and_society_debate",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "dry_run",
    }
    sm = get_sessionmaker()
    async with sm() as session:
        db_pre = await _count_all(session)
    audit["db_pre_counts"] = db_pre

    # ---- Load 9B + 9D ----------------------------------------------
    async with sm() as session:
        sess = (await session.execute(
            select(DiscussionSession)
            .where(DiscussionSession.phase == "9B")
            .order_by(DiscussionSession.created_at.desc())
            .limit(1)
        )).scalars().first()
        if not sess:
            print("REFUSED: no 9B discussion session found.")
            audit["blocker"] = "no 9B discussion session found"
            INTENT_AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        run_scope_id = sess.run_scope_id
        cohorts_orm = (await session.execute(
            select(SocietyCohort)
            .where(SocietyCohort.run_scope_id == run_scope_id)
            .where(SocietyCohort.phase == "9D")
            .order_by(SocietyCohort.cohort_size.desc())
        )).scalars().all()
        if not cohorts_orm:
            print("REFUSED: no 9D cohorts found for the run scope.")
            audit["blocker"] = "no 9D cohorts found"
            INTENT_AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        groups = (await session.execute(
            select(DiscussionGroup)
            .where(DiscussionGroup.discussion_session_id == sess.id)
        )).scalars().all()
        group_ids = [g.id for g in groups]
        turns = (await session.execute(
            select(DiscussionTurn)
            .where(DiscussionTurn.discussion_group_id.in_(group_ids))
        )).scalars().all()
        ballots = (await session.execute(
            select(DiscussionPrivateBallot)
            .where(DiscussionPrivateBallot.discussion_session_id == sess.id)
        )).scalars().all()
        all_pids: set = set()
        for c in cohorts_orm:
            for pid in c.member_persona_ids:
                all_pids.add(pid)
        personas = (await session.execute(
            select(PersonaRecord).where(PersonaRecord.id.in_(list(all_pids)))
        )).scalars().all()
        psy = (await session.execute(
            select(PersonaPsychologyTrait)
            .where(PersonaPsychologyTrait.run_scope_id == run_scope_id)
            .where(PersonaPsychologyTrait.persona_id.in_(list(all_pids)))
        )).scalars().all()
        atoms = (await session.execute(
            select(PersonaMemoryAtom)
            .where(PersonaMemoryAtom.run_scope_id == run_scope_id)
        )).scalars().all()

    audit["existing_9b_session_id"] = str(sess.id)
    audit["existing_9b_run_scope_id"] = run_scope_id
    audit["input_persona_count"] = len(all_pids)
    audit["input_cohort_count"] = len(cohorts_orm)
    audit["input_turn_count"] = len(turns)
    audit["input_ballot_count"] = len(ballots)
    audit["input_psychology_trait_count"] = len(psy)
    audit["input_memory_atom_count"] = len(atoms)

    if len(all_pids) != EXPECTED_PERSONA_COUNT:
        msg = (
            f"persona count mismatch: expected {EXPECTED_PERSONA_COUNT}, "
            f"got {len(all_pids)}"
        )
        print(f"REFUSED: {msg}")
        audit["blocker"] = msg
        INTENT_AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2

    # ---- Build per-persona context ---------------------------------
    psy_by_pid: dict[uuid.UUID, dict[str, float]] = {}
    for t in psy:
        psy_by_pid.setdefault(t.persona_id, {})[t.trait_name] = float(
            t.value_numeric,
        )
    pre_by_pid = {
        b.persona_id: b for b in ballots if b.ballot_stage == "pre"
    }
    final_by_pid = {
        b.persona_id: b for b in ballots if b.ballot_stage == "final"
    }
    refl_by_pid = {
        b.persona_id: b for b in ballots if b.ballot_stage == "reflection"
    }
    turns_by_speaker: dict[uuid.UUID, list[DiscussionTurn]] = {}
    for t in turns:
        turns_by_speaker.setdefault(t.speaker_persona_id, []).append(t)
    atoms_by_pid: dict[uuid.UUID, list[PersonaMemoryAtom]] = {}
    for a in atoms:
        atoms_by_pid.setdefault(a.persona_id, []).append(a)
    cohort_id_by_pid: dict[uuid.UUID, uuid.UUID] = {}
    cohort_obj_summary_by_id: dict[uuid.UUID, dict[str, int]] = {}
    cohort_id_to_label: dict[str, str] = {}
    cohort_id_to_size: dict[str, int] = {}
    cohorts_dict: list[dict[str, Any]] = []
    for c in cohorts_orm:
        cohort_id_to_label[str(c.id)] = c.cohort_label
        cohort_id_to_size[str(c.id)] = c.cohort_size
        for pid in c.member_persona_ids:
            cohort_id_by_pid[pid] = c.id
        cohort_obj_summary_by_id[c.id] = (
            (c.objection_summary or {}).get("by_bucket") or {}
        )
        cohorts_dict.append({
            "cohort_id": str(c.id),
            "id": str(c.id),
            "cohort_label": c.cohort_label,
            "member_persona_ids": [str(p) for p in c.member_persona_ids],
            "objection_summary": c.objection_summary or {},
            "proof_need_summary": c.proof_need_summary or {},
            "psychology_summary": c.psychology_summary or {},
            "discussion_behavior_summary": (
                c.discussion_behavior_summary or {}
            ),
            "representatives": {
                "primary": str(c.representative_persona_id)
                if c.representative_persona_id else None,
            },
        })

    # ---- Infer per-persona intent ----------------------------------
    intent_drafts: list[Any] = []
    for p in personas:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        psy_v = psy_by_pid.get(p.id, {})
        pre = pre_by_pid.get(p.id)
        final = final_by_pid.get(p.id)
        refl = refl_by_pid.get(p.id)
        cid = cohort_id_by_pid.get(p.id)

        text_corpus_parts: list[str] = []
        for ballot in (pre, refl, final):
            if not ballot:
                continue
            text_corpus_parts.append(ballot.private_reasoning or "")
            text_corpus_parts.append(ballot.top_objection or "")
            text_corpus_parts.append(ballot.top_proof_need or "")
        for t in turns_by_speaker.get(p.id, []):
            text_corpus_parts.append(t.public_text or "")
        for a in atoms_by_pid.get(p.id, []):
            text_corpus_parts.append(a.memory_text or "")
            text_corpus_parts.append(a.origin_excerpt or "")
        corpus = "\n".join(filter(None, text_corpus_parts))

        ballot_ids = [
            str(b.id) for b in (pre, refl, final) if b
        ]
        turn_ids = [str(t.id) for t in turns_by_speaker.get(p.id, [])]
        atom_ids = [str(a.id) for a in atoms_by_pid.get(p.id, [])]

        draft = infer_simulated_intent(
            persona_id=str(p.id),
            cohort_id=str(cid) if cid else None,
            normalized_role=normalized_role,
            psychology_value_map=psy_v,
            pre_ballot=({
                "private_stance": pre.private_stance,
                "private_reasoning": pre.private_reasoning,
                "top_objection": pre.top_objection,
                "top_proof_need": pre.top_proof_need,
                "confidence": pre.confidence,
            } if pre else None),
            final_ballot=({
                "private_stance": final.private_stance,
                "private_reasoning": final.private_reasoning,
                "top_objection": final.top_objection,
                "top_proof_need": final.top_proof_need,
                "public_private_delta": final.public_private_delta,
                "confidence": final.confidence,
            } if final else None),
            reflection_ballot=({
                "private_stance": refl.private_stance,
                "private_reasoning": refl.private_reasoning,
                "confidence": refl.confidence,
            } if refl else None),
            persona_text_corpus=corpus,
            ballot_ids=ballot_ids,
            discussion_turn_ids=turn_ids,
            memory_atom_ids=atom_ids,
            cohort_objection_summary=(
                cohort_obj_summary_by_id.get(cid) if cid else None
            ),
        )
        intent_drafts.append(draft)
    audit["intent_record_count"] = len(intent_drafts)
    intent_dist = Counter(d.simulated_intent for d in intent_drafts)
    audit["intent_distribution"] = dict(intent_dist)
    audit["switching_status_distribution"] = dict(
        Counter(d.switching_status for d in intent_drafts)
    )

    # ---- Extract society-wide arguments ----------------------------
    turn_dicts = [
        {
            "turn_id": str(t.id),
            "speaker_persona_id": str(t.speaker_persona_id),
            "turn_type": t.turn_type,
            "public_text": t.public_text or "",
            "stance": t.stance,
        }
        for t in turns
    ]
    arg_drafts = extract_society_arguments(
        cohorts=cohorts_dict, discussion_turns=turn_dicts,
    )
    audit["argument_count"] = len(arg_drafts)
    audit["argument_type_distribution"] = dict(
        Counter(a.argument_type for a in arg_drafts)
    )

    # ---- Forbidden / sensitive scans across all generated text -----
    audit_texts: list[tuple[str, str]] = []
    for d in intent_drafts:
        audit_texts.append(
            (f"intent:{d.persona_id}", d.evidence_basis),
        )
        if d.reason_for_rejection:
            audit_texts.append(
                (f"intent_reject:{d.persona_id}", d.reason_for_rejection),
            )
        for cond in d.conditions_to_buy or []:
            audit_texts.append(
                (f"intent_cond:{d.persona_id}", cond),
            )
    for i, a in enumerate(arg_drafts):
        audit_texts.append((f"argument[{i}]", a.argument_text))
    fb_audit = forbidden_claim_audit(
        texts=audit_texts, product_name=sess.product_name,
    )
    sens_audit = sensitive_inference_audit(audit_texts)

    # ---- Persist (commit only) -------------------------------------
    intent_id_by_persona: dict[str, uuid.UUID] = {}
    arg_id_by_index: dict[int, uuid.UUID] = {}
    inserted_intents = 0
    inserted_arguments = 0
    inserted_propagations = 0
    inserted_rollup = 0

    if not args.commit:
        # dry-run preview only
        rollup = build_intent_rollup(
            intents=[d.model_dump() for d in intent_drafts],
            cohort_id_to_label=cohort_id_to_label,
            cohort_id_to_size=cohort_id_to_size,
            cohort_count=len(cohorts_orm),
        )
        # We can still simulate propagation in-memory
        # (assign synthetic ids so the rollup-side can reason about it)
        synthetic_arg_ids = [
            (str(uuid.uuid4()), d) for d in arg_drafts
        ]
        prop_drafts = propagate_arguments_across_cohorts(
            arguments_with_ids=synthetic_arg_ids,
            cohorts=cohorts_dict,
        )
        audit["argument_count"] = len(arg_drafts)
        audit["propagation_count"] = len(prop_drafts)
        audit["intent_rollup"] = rollup
        audit["forbidden_claim_audit"] = fb_audit
        audit["sensitive_inference_audit"] = sens_audit
        # quality on dry-run
        # (we can't persist arg ids, so use synthetic ones for evaluator)
        quality = evaluate_intent_and_debate_quality(
            intents=[d.model_dump() for d in intent_drafts],
            arguments=[
                {**a.model_dump(), "id": aid}
                for aid, a in synthetic_arg_ids
            ],
            propagations=[p.model_dump() for p in prop_drafts],
            forbidden_audit=fb_audit,
            sensitive_audit=sens_audit,
            expected_persona_count=len(all_pids),
            cohort_count=len(cohorts_orm),
        )
        audit["quality_scores"] = quality
        audit["recommendation"] = (
            "DRY-RUN — no DB writes; re-run with --commit to persist."
        )
        # security scan
        json_text = json.dumps(audit, indent=2, default=str)
        scan = scan_for_secrets(json_text)
        audit["security_redaction_audit"] = {
            "secrets_clean": scan.is_clean,
            "finding_count": len(scan.findings),
            "scanner_version": "9E.universal",
        }
        INTENT_AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        print(
            f"\nDRY-RUN — {len(intent_drafts)} intents, "
            f"{len(arg_drafts)} arguments, {len(prop_drafts)} "
            f"propagations. quality.aggregate="
            f"{quality['aggregate_score']} ({quality['ready_state']}). "
            "No DB writes."
        )
        return 0

    # =================================================================
    # COMMIT
    # =================================================================
    async with sm() as session:
        async with session.begin():
            # Refuse double-write
            existing = (await session.execute(
                select(func.count()).select_from(SimulatedIntent)
                .where(SimulatedIntent.run_scope_id == run_scope_id)
            )).scalar_one()
            if existing > 0:
                raise RuntimeError(
                    f"refusing to commit: {existing} simulated_intents "
                    f"row(s) already exist for run_scope_id={run_scope_id}"
                )
            for d in intent_drafts:
                iid = uuid.uuid4()
                intent_id_by_persona[d.persona_id] = iid
                session.add(SimulatedIntent(
                    id=iid,
                    run_scope_id=run_scope_id,
                    persona_id=uuid.UUID(d.persona_id),
                    cohort_id=(
                        uuid.UUID(d.cohort_id) if d.cohort_id else None
                    ),
                    stance_label=d.stance_label,
                    simulated_intent=d.simulated_intent,
                    intent_strength=d.intent_strength,
                    switching_status=d.switching_status,
                    current_alternative=d.current_alternative,
                    conditions_to_buy=d.conditions_to_buy,
                    reason_for_rejection=d.reason_for_rejection,
                    proof_needed=d.proof_needed,
                    evidence_basis=d.evidence_basis,
                    discussion_turn_ids=[
                        uuid.UUID(s) for s in d.discussion_turn_ids
                    ],
                    ballot_ids=[
                        uuid.UUID(s) for s in d.ballot_ids
                    ],
                    memory_atom_ids=[
                        uuid.UUID(s) for s in d.memory_atom_ids
                    ],
                    confidence=d.confidence,
                    caveat=d.caveat,
                    generated_for_phase=PHASE_LABEL,
                ))
                inserted_intents += 1
            await session.flush()

            for i, a in enumerate(arg_drafts):
                aid = uuid.uuid4()
                arg_id_by_index[i] = aid
                session.add(SocietyArgument(
                    id=aid,
                    run_scope_id=run_scope_id,
                    phase=PHASE_LABEL,
                    origin_type=a.origin_type,
                    origin_ref_id=uuid.UUID(a.origin_ref_id),
                    argument_text=a.argument_text,
                    argument_type=a.argument_type,
                    source_cohort_id=(
                        uuid.UUID(a.source_cohort_id)
                        if a.source_cohort_id else None
                    ),
                    supporting_turn_ids=[
                        uuid.UUID(s) for s in a.supporting_turn_ids
                    ],
                    supporting_memory_atom_ids=[
                        uuid.UUID(s) for s in a.supporting_memory_atom_ids
                    ],
                ))
                inserted_arguments += 1
            await session.flush()

            arg_id_strings = [
                (str(arg_id_by_index[i]), a)
                for i, a in enumerate(arg_drafts)
            ]
            prop_drafts = propagate_arguments_across_cohorts(
                arguments_with_ids=arg_id_strings,
                cohorts=cohorts_dict,
            )
            for p in prop_drafts:
                session.add(SocietyArgumentPropagation(
                    id=uuid.uuid4(),
                    argument_id=uuid.UUID(p.argument_id),
                    target_cohort_id=uuid.UUID(p.target_cohort_id),
                    representative_persona_id=(
                        uuid.UUID(p.representative_persona_id)
                        if p.representative_persona_id else None
                    ),
                    response_type=p.response_type,
                    response_text=p.response_text,
                    effect_on_intent=p.effect_on_intent,
                    evidence_basis=p.evidence_basis,
                ))
                inserted_propagations += 1
            await session.flush()

            # Rollup row
            rollup = build_intent_rollup(
                intents=[d.model_dump() for d in intent_drafts],
                cohort_id_to_label=cohort_id_to_label,
                cohort_id_to_size=cohort_id_to_size,
                cohort_count=len(cohorts_orm),
            )
            quality_for_rollup = evaluate_intent_and_debate_quality(
                intents=[d.model_dump() for d in intent_drafts],
                arguments=[
                    {**a.model_dump(), "id": str(arg_id_by_index[i])}
                    for i, a in enumerate(arg_drafts)
                ],
                propagations=[p.model_dump() for p in prop_drafts],
                forbidden_audit=fb_audit,
                sensitive_audit=sens_audit,
                expected_persona_count=len(all_pids),
                cohort_count=len(cohorts_orm),
            )
            session.add(SimulatedIntentRollup(
                id=uuid.uuid4(),
                run_scope_id=run_scope_id,
                phase=PHASE_LABEL,
                persona_count=len(intent_drafts),
                cohort_count=len(cohorts_orm),
                intent_distribution=rollup["intent_distribution"],
                intent_by_cohort=rollup["intent_by_cohort"],
                switching_status_distribution=rollup[
                    "switching_status_distribution"
                ],
                high_intent_segments=rollup["high_intent_segments"],
                strongest_rejection_segments=rollup[
                    "strongest_rejection_segments"
                ],
                caveats=rollup["caveats"],
                quality_scores=quality_for_rollup,
            ))
            inserted_rollup += 1

    audit["inserted_intents"] = inserted_intents
    audit["inserted_arguments"] = inserted_arguments
    audit["inserted_propagations"] = inserted_propagations
    audit["inserted_rollup_rows"] = inserted_rollup
    audit["propagation_count"] = inserted_propagations

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
        "discussion_sessions", "discussion_groups", "discussion_turns",
        "discussion_private_ballots", "persona_memory_atoms",
        "society_cohorts", "society_cohort_evidence_links",
        "society_cohort_rollups",
        "simulations",
    )
    audit["additive_only_check"] = {
        "non_intent_deltas_zero": all(
            delta.get(k, 0) == 0 for k in forbidden_table_keys
        ),
        "delta_simulated_intents": delta.get("simulated_intents", 0),
        "delta_simulated_intent_rollups": delta.get(
            "simulated_intent_rollups", 0,
        ),
        "delta_society_arguments": delta.get("society_arguments", 0),
        "delta_society_argument_propagation": delta.get(
            "society_argument_propagation", 0,
        ),
    }

    # Render
    intent_dicts = [d.model_dump() for d in intent_drafts]
    arg_dicts = [
        {**a.model_dump(), "id": str(arg_id_by_index[i])}
        for i, a in enumerate(arg_drafts)
    ]
    prop_dicts = [p.model_dump() for p in prop_drafts]
    rollup = build_intent_rollup(
        intents=intent_dicts,
        cohort_id_to_label=cohort_id_to_label,
        cohort_id_to_size=cohort_id_to_size,
        cohort_count=len(cohorts_orm),
    )
    quality = evaluate_intent_and_debate_quality(
        intents=intent_dicts,
        arguments=arg_dicts,
        propagations=prop_dicts,
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
        expected_persona_count=len(all_pids),
        cohort_count=len(cohorts_orm),
    )
    report = render_intent_and_debate_report_json(
        run_scope_id=run_scope_id,
        phase=PHASE_LABEL,
        product_name=sess.product_name,
        persona_count=len(all_pids),
        cohort_count=len(cohorts_orm),
        intents=intent_dicts,
        intent_rollup=rollup,
        arguments=arg_dicts,
        propagations=prop_dicts,
        cohort_id_to_label=cohort_id_to_label,
        cohort_id_to_size=cohort_id_to_size,
        quality_scores=quality,
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
    )
    md = render_intent_and_debate_report_markdown(report)
    REPORT_JSON_PATH.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8",
    )
    REPORT_MD_PATH.write_text(md, encoding="utf-8")

    # Final audits
    audit["intent_rollup"] = rollup
    audit["quality_scores"] = quality
    audit["forbidden_claim_audit"] = fb_audit
    audit["sensitive_inference_audit"] = sens_audit
    json_text = json.dumps(audit, indent=2, default=str)
    audit_scan = scan_for_secrets(json_text)
    md_scan = scan_for_secrets(md)
    audit["security_redaction_audit"] = {
        "secrets_clean": audit_scan.is_clean and md_scan.is_clean,
        "finding_count": (
            len(audit_scan.findings) + len(md_scan.findings)
        ),
        "scanner_version": "9E.universal",
    }

    pass_required = (
        not fb_audit["any_fake_target_product_use"]
        and not fb_audit["any_forecast_or_verdict"]
        and not sens_audit["any_sensitive_inference"]
        and audit["additive_only_check"]["non_intent_deltas_zero"]
        and audit["security_redaction_audit"]["secrets_clean"]
        and quality["ready_state"] == "READY_FOR_PHASE_10A"
        and inserted_intents == EXPECTED_PERSONA_COUNT
    )
    audit["ready_for_phase_10a_api_demo_packaging"] = bool(pass_required)
    audit["recommended_next_phase"] = (
        "Phase 10A — API / Demo Packaging for Founder-Input → Society "
        "Report Flow"
    )
    audit["recommendation"] = (
        "PASS — Phase 9E complete. Simulated intent layer + cross-cohort "
        "argument propagation working over the 9B/9D society. "
        "Recommended next phase: Phase 10A — API / Demo Packaging."
        if pass_required else
        "PARTIAL — intent + propagation ran but one or more pass "
        "conditions did not hold; see quality_scores."
    )
    audit["report_files"] = {
        "report_json": str(REPORT_JSON_PATH),
        "report_md": str(REPORT_MD_PATH),
    }

    INTENT_AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    DEBATE_AUDIT_PATH.write_text(json.dumps({
        "phase": "9e_society_wide_debate",
        "completed_at": datetime.now(UTC).isoformat(),
        "run_scope_id": run_scope_id,
        "argument_count": len(arg_drafts),
        "argument_type_distribution": audit["argument_type_distribution"],
        "propagation_count": inserted_propagations,
        "response_type_distribution": dict(
            Counter(p.response_type for p in prop_drafts)
        ),
        "effect_on_intent_distribution": dict(
            Counter(p.effect_on_intent for p in prop_drafts)
        ),
    }, indent=2, default=str), encoding="utf-8")
    DEBATE_QUALITY_PATH.write_text(json.dumps({
        "phase": "9e_quality",
        "completed_at": datetime.now(UTC).isoformat(),
        "run_scope_id": run_scope_id,
        "quality_scores": quality,
        "forbidden_claim_audit": fb_audit,
        "sensitive_inference_audit": sens_audit,
        "ready_for_phase_10a_api_demo_packaging": (
            audit["ready_for_phase_10a_api_demo_packaging"]
        ),
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nPhase {PHASE_LABEL} — committed.")
    print(
        f"  intents={inserted_intents} arguments={inserted_arguments} "
        f"propagations={inserted_propagations} rollups={inserted_rollup}"
    )
    print(f"  intent_distribution: {dict(Counter(d.simulated_intent for d in intent_drafts))}")
    print(
        f"  quality.aggregate={quality['aggregate_score']} "
        f"ready_state={quality['ready_state']}"
    )
    print(
        f"  ready_for_phase_10a_api_demo_packaging="
        f"{audit['ready_for_phase_10a_api_demo_packaging']}"
    )
    print(f"\n→ intent audit:    {INTENT_AUDIT_PATH}")
    print(f"→ debate audit:    {DEBATE_AUDIT_PATH}")
    print(f"→ quality artifact: {DEBATE_QUALITY_PATH}")
    print(f"→ report (md):     {REPORT_MD_PATH}")
    print(f"→ report (json):   {REPORT_JSON_PATH}")
    return 0 if pass_required else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
