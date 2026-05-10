"""Phase 9D — build the cohort/cluster architecture over the 9B/9B.1
official 66-person LumaLoop society.

Loads the existing 9B run-scope (no new retrieval), builds a feature
vector per persona, clusters into 8-14 cohorts via deterministic
agglomerative clustering, summarizes each cohort, builds a weighted
rollup, evaluates 10 quality scores, persists into 3 new cohort
tables (additive only), and emits the founder-facing report.

NO LLM calls. NO mutation of 9A/9B rows. NO new retrieval.

Usage:
  python scripts/build_cohort_architecture_9d.py             # dry-run
  python scripts/build_cohort_architecture_9d.py --commit    # full
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
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.models.round import AgentResponse
from assembly.models.simulation import Simulation
from assembly.sources.cohort_architecture import (
    build_cohort_feature_vectors,
    build_society_rollup,
    cluster_personas_into_cohorts,
    evaluate_cohort_architecture_quality,
    render_cohort_report_json,
    render_cohort_report_markdown,
    select_cohort_representatives,
    summarize_cohort,
)
from assembly.sources.cohort_architecture.clusterer import assignment_audit
from assembly.sources.discussion_layer import (
    forbidden_claim_audit,
    sensitive_inference_audit,
)
from assembly.sources.founder_report_generator import scan_for_secrets


PHASE_LABEL = "9D"
EXPECTED_PERSONA_COUNT = 66
TARGET_MIN_COHORTS = 8
TARGET_MAX_COHORTS = 14
MIN_CLUSTER_SIZE = 3
MAX_CLUSTER_SIZE = 10

AUDIT_ROOT = Path(__file__).resolve().parent.parent / "_audit"
AUDIT_PATH = AUDIT_ROOT / "cohort_architecture_9d.json"
QUALITY_PATH = AUDIT_ROOT / "cohort_architecture_9d_quality.json"
REPORT_JSON_PATH = AUDIT_ROOT / "lumaloop_cohort_architecture_report_9d.json"
REPORT_MD_PATH = AUDIT_ROOT / "lumaloop_cohort_architecture_report_9d.md"
INPUT_9B_1_AUDIT_PATH = (
    AUDIT_ROOT / "repair_9b_reflections_9b_1.json"
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
    ):
        n = (await session.execute(
            select(func.count()).select_from(table)
        )).scalar_one()
        out[label] = int(n)
    return out


async def _load_9b_society(
    session: AsyncSession,
) -> dict[str, Any]:
    """Returns a packed dict with everything 9D needs."""
    sess = (await session.execute(
        select(DiscussionSession)
        .where(DiscussionSession.phase == "9B")
        .order_by(DiscussionSession.created_at.desc())
        .limit(1)
    )).scalars().first()
    if not sess:
        return {"blocker": "no 9B discussion session found"}
    run_scope_id = sess.run_scope_id
    groups = (await session.execute(
        select(DiscussionGroup)
        .where(DiscussionGroup.discussion_session_id == sess.id)
        .order_by(DiscussionGroup.group_index)
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
    pids: set = set()
    for g in groups:
        for pid in g.persona_ids:
            pids.add(pid)
    personas = (await session.execute(
        select(PersonaRecord).where(PersonaRecord.id.in_(list(pids)))
    )).scalars().all()
    psy = (await session.execute(
        select(PersonaPsychologyTrait)
        .where(PersonaPsychologyTrait.run_scope_id == run_scope_id)
        .where(PersonaPsychologyTrait.persona_id.in_(list(pids)))
    )).scalars().all()
    atoms = (await session.execute(
        select(PersonaMemoryAtom)
        .where(PersonaMemoryAtom.run_scope_id == run_scope_id)
    )).scalars().all()
    return {
        "session": sess,
        "groups": groups,
        "turns": turns,
        "ballots": ballots,
        "personas": personas,
        "psychology": psy,
        "memory_atoms": atoms,
        "run_scope_id": run_scope_id,
    }


def _build_persona_meta(
    personas: list[PersonaRecord],
    psy: list[PersonaPsychologyTrait],
    ballots: list[DiscussionPrivateBallot],
    turns: list[DiscussionTurn],
    atoms: list[PersonaMemoryAtom],
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, float]],
]:
    """Return:
      - persona_dicts (list[dict]) — input shape for feature builder
      - persona_meta (dict[pid_str → meta dict])
      - persona_psychology (dict[pid_str → trait_name → float])"""
    psy_by_pid: dict[uuid.UUID, dict[str, float]] = {}
    for t in psy:
        psy_by_pid.setdefault(t.persona_id, {})[t.trait_name] = float(
            t.value_numeric
        )
    pre_by_pid: dict[uuid.UUID, DiscussionPrivateBallot] = {
        b.persona_id: b for b in ballots if b.ballot_stage == "pre"
    }
    final_by_pid: dict[uuid.UUID, DiscussionPrivateBallot] = {
        b.persona_id: b for b in ballots if b.ballot_stage == "final"
    }
    refl_by_pid: dict[uuid.UUID, DiscussionPrivateBallot] = {
        b.persona_id: b for b in ballots if b.ballot_stage == "reflection"
    }
    peer_refs_by_pid: dict[uuid.UUID, int] = {}
    for t in turns:
        if t.referenced_turn_ids:
            peer_refs_by_pid[t.speaker_persona_id] = (
                peer_refs_by_pid.get(t.speaker_persona_id, 0)
                + len(t.referenced_turn_ids)
            )
    atom_count_by_pid: dict[uuid.UUID, dict[str, int]] = {}
    for a in atoms:
        m = atom_count_by_pid.setdefault(a.persona_id, {})
        m[a.memory_type] = m.get(a.memory_type, 0) + 1

    persona_dicts: list[dict[str, Any]] = []
    persona_meta: dict[str, dict[str, Any]] = {}
    persona_psychology: dict[str, dict[str, float]] = {}
    for p in personas:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        provider = _parse_tag_value(
            tags, "source_provider_family",
        ) or "unknown"
        psy_v = psy_by_pid.get(p.id, {})
        pre = pre_by_pid.get(p.id)
        final = final_by_pid.get(p.id)
        refl = refl_by_pid.get(p.id)
        persona_dicts.append({
            "persona_id": str(p.id),
            "normalized_primary_role": normalized_role,
            "source_provider_family": provider,
            "psychology_value_map": psy_v,
            "pre_stance": pre.private_stance if pre else None,
            "final_stance": final.private_stance if final else None,
            "public_private_delta": (
                final.public_private_delta if final else None
            ),
            "peer_reference_count": peer_refs_by_pid.get(p.id, 0),
            "has_top_objection": bool(pre and pre.top_objection),
            "has_top_proof_need": bool(pre and pre.top_proof_need),
            "memory_atom_count_by_type": atom_count_by_pid.get(p.id, {}),
            "reflection_present": refl is not None,
        })
        persona_meta[str(p.id)] = {
            "persona_record_id": p.id,
            "display_name": p.display_name,
            "normalized_primary_role": normalized_role,
            "final_stance": final.private_stance if final else None,
            "psychology_value_map": psy_v,
        }
        persona_psychology[str(p.id)] = psy_v
    return persona_dicts, persona_meta, persona_psychology


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — cohort architecture.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Persist into society_cohorts/* tables. Default is dry-run.",
    )
    args = parser.parse_args()
    AUDIT_ROOT.mkdir(exist_ok=True)
    audit: dict[str, Any] = {
        "phase": "9d_cohort_cluster_architecture",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "dry_run",
    }
    sm = get_sessionmaker()
    async with sm() as session:
        db_pre = await _count_all(session)
        loaded = await _load_9b_society(session)
    audit["db_pre_counts"] = db_pre

    if "blocker" in loaded:
        print(f"REFUSED: {loaded['blocker']}")
        audit["blocker"] = loaded["blocker"]
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2

    sess = loaded["session"]
    personas = loaded["personas"]
    psy = loaded["psychology"]
    ballots = loaded["ballots"]
    turns = loaded["turns"]
    atoms = loaded["memory_atoms"]
    run_scope_id = loaded["run_scope_id"]
    audit["existing_9b_session_id"] = str(sess.id)
    audit["existing_9b_run_scope_id"] = run_scope_id

    if len(personas) != EXPECTED_PERSONA_COUNT:
        msg = (
            f"persona count mismatch: expected {EXPECTED_PERSONA_COUNT}, "
            f"got {len(personas)}"
        )
        print(f"REFUSED: {msg}")
        audit["blocker"] = msg
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2
    pre_count = sum(1 for b in ballots if b.ballot_stage == "pre")
    refl_count = sum(1 for b in ballots if b.ballot_stage == "reflection")
    final_count = sum(1 for b in ballots if b.ballot_stage == "final")
    if (
        pre_count != EXPECTED_PERSONA_COUNT
        or final_count != EXPECTED_PERSONA_COUNT
        or refl_count < EXPECTED_PERSONA_COUNT
    ):
        msg = (
            f"ballot completeness mismatch: pre={pre_count} "
            f"refl={refl_count} final={final_count}"
        )
        print(f"REFUSED: {msg}")
        audit["blocker"] = msg
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2
    if len(turns) != 264:
        msg = (
            f"turn count mismatch: expected 264, got {len(turns)}"
        )
        print(f"REFUSED: {msg}")
        audit["blocker"] = msg
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2

    audit["input_persona_count"] = len(personas)
    audit["input_pre_count"] = pre_count
    audit["input_reflection_count"] = refl_count
    audit["input_final_count"] = final_count
    audit["input_turn_count"] = len(turns)
    audit["input_memory_atom_count"] = len(atoms)
    audit["input_psychology_trait_count"] = len(psy)

    # Build persona meta + features
    persona_dicts, persona_meta, persona_psychology = _build_persona_meta(
        personas, psy, ballots, turns, atoms,
    )
    feature_vectors, feature_meta = build_cohort_feature_vectors(
        personas=persona_dicts,
    )
    audit["feature_metadata"] = feature_meta

    # Cluster
    persona_ids = [p["persona_id"] for p in persona_dicts]
    cohort_persona_lists, cluster_audit = cluster_personas_into_cohorts(
        persona_ids=persona_ids,
        feature_vectors=feature_vectors,
        target_min_cohorts=TARGET_MIN_COHORTS,
        target_max_cohorts=TARGET_MAX_COHORTS,
        min_cluster_size=MIN_CLUSTER_SIZE,
        max_cluster_size=MAX_CLUSTER_SIZE,
    )
    assign_audit = assignment_audit(persona_ids, cohort_persona_lists)
    audit["clustering_audit"] = cluster_audit
    audit["assignment_audit"] = assign_audit
    if not assign_audit["every_persona_assigned_exactly_once"]:
        msg = (
            "clustering assignment failed: "
            f"{assign_audit}"
        )
        print(f"REFUSED: {msg}")
        audit["blocker"] = msg
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2

    # Build cohort summaries
    pre_by_pid_str = {
        str(b.persona_id): {
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "confidence": b.confidence,
            "top_objection": b.top_objection,
            "top_proof_need": b.top_proof_need,
        }
        for b in ballots if b.ballot_stage == "pre"
    }
    final_by_pid_str = {
        str(b.persona_id): {
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "confidence": b.confidence,
            "top_objection": b.top_objection,
            "top_proof_need": b.top_proof_need,
            "public_private_delta": b.public_private_delta,
        }
        for b in ballots if b.ballot_stage == "final"
    }
    refl_by_pid_str = {
        str(b.persona_id): {
            "private_stance": b.private_stance,
            "private_reasoning": b.private_reasoning,
            "confidence": b.confidence,
        }
        for b in ballots if b.ballot_stage == "reflection"
    }
    turn_dicts = [
        {
            "turn_id": str(t.id),
            "speaker_persona_id": str(t.speaker_persona_id),
            "turn_type": t.turn_type,
            "public_text": t.public_text,
            "stance": t.stance,
            "referenced_turn_ids": [
                str(r) for r in (t.referenced_turn_ids or [])
            ],
        }
        for t in turns
    ]
    atom_dicts = [
        {
            "memory_atom_id": str(a.id),
            "id": str(a.id),
            "persona_id": str(a.persona_id),
            "memory_type": a.memory_type,
            "origin_excerpt": a.origin_excerpt,
            "memory_text": a.memory_text,
            "importance_score": a.importance_score,
        }
        for a in atoms
    ]

    persona_features_dict = dict(zip(persona_ids, feature_vectors))
    cohort_summaries: list[dict[str, Any]] = []
    cohort_weights: list[float] = []
    cohort_repr: list[dict[str, str | None]] = []
    cohort_labels: list[str] = []
    for c in cohort_persona_lists:
        s = summarize_cohort(
            cohort_persona_ids=c,
            persona_meta=persona_meta,
            persona_psychology=persona_psychology,
            pre_ballots=pre_by_pid_str,
            final_ballots=final_by_pid_str,
            reflection_ballots=refl_by_pid_str,
            discussion_turns=turn_dicts,
            memory_atoms=atom_dicts,
        )
        s["cohort_size"] = len(c)
        cohort_summaries.append(s)
        cohort_weights.append(len(c) / max(len(persona_ids), 1))
        reps = select_cohort_representatives(
            cohort_persona_ids=c,
            persona_features=persona_features_dict,
            persona_meta=persona_meta,
        )
        cohort_repr.append(reps)
        # Cohort label = top role + top final stance
        roles = s.get("role_distribution") or {}
        stances = s.get("stance_distribution") or {}
        top_role = next(iter(sorted(
            roles.items(), key=lambda kv: -kv[1],
        )), ("unknown", 0))[0]
        top_stance = next(iter(sorted(
            stances.items(), key=lambda kv: -kv[1],
        )), ("none", 0))[0]
        label = f"{top_role}::{top_stance}"
        cohort_labels.append(label[:128])

    # Society rollup
    rollup = build_society_rollup(
        cohort_summaries=[
            {
                **s,
                "cohort_size": len(c),
            }
            for s, c in zip(cohort_summaries, cohort_persona_lists)
        ],
        cohort_weights=cohort_weights,
        persona_count=len(personas),
    )

    # Forbidden / sensitive scans across:
    #   - cohort labels + caveats + summaries (text fields only)
    #   - rollup summaries (already structured, not text)
    audit_texts: list[tuple[str, str]] = []
    for i, (label, s) in enumerate(zip(cohort_labels, cohort_summaries)):
        audit_texts.append((f"cohort_label[{i}]", label))
        for ev in s.get("_evidence_links") or []:
            if ev.get("excerpt"):
                audit_texts.append((
                    f"cohort_evidence[{i}]:{ev.get('evidence_role')}",
                    ev["excerpt"],
                ))
    fb_audit = forbidden_claim_audit(
        texts=audit_texts, product_name=sess.product_name,
    )
    sens_audit = sensitive_inference_audit(audit_texts)

    # Build the rich cohort_map for the report (centroids + reps)
    cohort_report_rows: list[dict[str, Any]] = []
    for i, (cohort_pids, summary, reps, weight, label) in enumerate(zip(
        cohort_persona_lists, cohort_summaries, cohort_repr,
        cohort_weights, cohort_labels,
    )):
        rep_meta_primary = persona_meta.get(reps.get("primary") or "")
        rep_meta_dissent = persona_meta.get(reps.get("dissent") or "") if reps.get("dissent") else {}
        rep_meta_proof = persona_meta.get(reps.get("proof_threshold") or "") if reps.get("proof_threshold") else {}
        cohort_report_rows.append({
            "cohort_index": i,
            "cohort_label": label,
            "cohort_size": len(cohort_pids),
            "cohort_weight": round(weight, 4),
            "member_persona_ids": list(cohort_pids),
            "member_display_names": [
                persona_meta.get(pid, {}).get("display_name", pid[:8])
                for pid in cohort_pids
            ],
            "representatives": {
                "primary": reps.get("primary"),
                "dissent": reps.get("dissent"),
                "proof_threshold": reps.get("proof_threshold"),
                "primary_display_name": (
                    rep_meta_primary.get("display_name") if rep_meta_primary else None
                ),
                "dissent_display_name": (
                    rep_meta_dissent.get("display_name") if rep_meta_dissent else None
                ),
                "proof_threshold_display_name": (
                    rep_meta_proof.get("display_name") if rep_meta_proof else None
                ),
            },
            "representative_persona_id": reps.get("primary"),
            "role_distribution": summary.get("role_distribution") or {},
            "stance_distribution": summary.get("stance_distribution") or {},
            "psychology_summary": summary.get("psychology_summary") or {},
            "objection_summary": summary.get("objection_summary") or {},
            "proof_need_summary": summary.get("proof_need_summary") or {},
            "discussion_behavior_summary": (
                summary.get("discussion_behavior_summary") or {}
            ),
            "caveats": [
                "Run-scoped + brief-scoped cohort. Not a global market segment.",
                "Synthetic n=66 simulation. Not a forecast.",
            ],
        })

    # Quality evaluator
    evidence_link_total = sum(
        len(s.get("_evidence_links") or []) for s in cohort_summaries
    )
    quality = evaluate_cohort_architecture_quality(
        cohorts=cohort_persona_lists,
        persona_features=persona_features_dict,
        cohort_summaries=cohort_summaries,
        society_rollup=rollup,
        pre_ballots=pre_by_pid_str,
        final_ballots=final_by_pid_str,
        expected_persona_count=len(personas),
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
        evidence_link_count=evidence_link_total,
        target_min_cohorts=TARGET_MIN_COHORTS,
        target_max_cohorts=TARGET_MAX_COHORTS,
    )

    audit["cohort_count"] = len(cohort_persona_lists)
    audit["cohort_size_distribution"] = [
        len(c) for c in cohort_persona_lists
    ]
    audit["cohorts"] = cohort_report_rows
    audit["weighted_society_rollup"] = rollup
    audit["forbidden_claim_audit"] = fb_audit
    audit["sensitive_inference_audit"] = sens_audit
    audit["evidence_link_count"] = evidence_link_total
    audit["quality_scores"] = quality

    # Render report
    report = render_cohort_report_json(
        run_scope_id=run_scope_id,
        phase=PHASE_LABEL,
        product_name=sess.product_name,
        cohorts=cohort_report_rows,
        rollup=rollup,
        quality_scores=quality,
        persona_count=len(personas),
        forbidden_audit=fb_audit,
        sensitive_audit=sens_audit,
    )
    md = render_cohort_report_markdown(report)

    if not args.commit:
        print(
            f"\nDRY-RUN — {len(personas)} personas → "
            f"{len(cohort_persona_lists)} cohorts. "
            f"Aggregate quality {quality['aggregate_score']} "
            f"({quality['ready_state']}). "
            "No DB writes."
        )
        audit["recommendation"] = (
            "DRY-RUN — re-run with --commit to persist + emit report."
        )
        # Security scan even on dry-run audit
        json_text = json.dumps(audit, indent=2, default=str)
        scan = scan_for_secrets(json_text)
        audit["security_redaction_audit"] = {
            "secrets_clean": scan.is_clean,
            "finding_count": len(scan.findings),
            "scanner_version": "9D.universal",
        }
        AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        return 0

    # =================================================================
    # COMMIT
    # =================================================================
    cohort_id_by_index: dict[int, uuid.UUID] = {}
    inserted_cohorts = 0
    inserted_links = 0
    inserted_rollups = 0
    async with sm() as session:
        async with session.begin():
            # 1) cohorts (additive — no upsert; refuse double-write)
            existing = (await session.execute(
                select(func.count()).select_from(SocietyCohort)
                .where(SocietyCohort.run_scope_id == run_scope_id)
                .where(SocietyCohort.phase == PHASE_LABEL)
            )).scalar_one()
            if existing > 0:
                raise RuntimeError(
                    f"refusing to commit: {existing} society_cohorts row(s) "
                    f"already exist for run_scope_id={run_scope_id}, "
                    f"phase={PHASE_LABEL}"
                )
            for i, (cohort_pids, summary, reps, weight, label) in enumerate(zip(
                cohort_persona_lists, cohort_summaries, cohort_repr,
                cohort_weights, cohort_labels,
            )):
                cid = uuid.uuid4()
                cohort_id_by_index[i] = cid
                rep_uuid = (
                    uuid.UUID(reps.get("primary"))
                    if reps.get("primary") else None
                )
                session.add(SocietyCohort(
                    id=cid,
                    run_scope_id=run_scope_id,
                    phase=PHASE_LABEL,
                    cohort_label=label[:128],
                    cohort_size=len(cohort_pids),
                    cohort_weight=Decimal(str(round(weight, 4))),
                    representative_persona_id=rep_uuid,
                    member_persona_ids=[
                        uuid.UUID(pid) for pid in cohort_pids
                    ],
                    clustering_method="deterministic_agglomerative_v1",
                    role_distribution=summary.get("role_distribution") or {},
                    stance_distribution=(
                        summary.get("stance_distribution") or {}
                    ),
                    psychology_summary=(
                        summary.get("psychology_summary") or {}
                    ),
                    objection_summary=(
                        summary.get("objection_summary") or {}
                    ),
                    proof_need_summary=(
                        summary.get("proof_need_summary") or {}
                    ),
                    discussion_behavior_summary=(
                        summary.get("discussion_behavior_summary") or {}
                    ),
                    caveats=[
                        "Run-scoped + brief-scoped cohort. Not a "
                        "global market segment.",
                        "Synthetic n=66 simulation. Not a forecast.",
                    ],
                ))
                inserted_cohorts += 1
            await session.flush()

            # 2) evidence links
            for i, summary in enumerate(cohort_summaries):
                cid = cohort_id_by_index[i]
                for ev in summary.get("_evidence_links") or []:
                    excerpt = (ev.get("excerpt") or "").strip()
                    if not excerpt:
                        continue
                    role = ev.get("evidence_role") or "evidence_anchor"
                    if role not in (
                        "objection", "proof_need", "stance_anchor",
                        "psychology_anchor", "discussion_anchor",
                        "evidence_anchor",
                    ):
                        role = "evidence_anchor"
                    persona_uuid = None
                    if ev.get("persona_id"):
                        try:
                            persona_uuid = uuid.UUID(ev["persona_id"])
                        except (ValueError, TypeError):
                            persona_uuid = None
                    turn_uuid = None
                    if ev.get("discussion_turn_id"):
                        try:
                            turn_uuid = uuid.UUID(ev["discussion_turn_id"])
                        except (ValueError, TypeError):
                            turn_uuid = None
                    atom_uuid = None
                    if ev.get("memory_atom_id"):
                        try:
                            atom_uuid = uuid.UUID(ev["memory_atom_id"])
                        except (ValueError, TypeError):
                            atom_uuid = None
                    if (
                        persona_uuid is None
                        and turn_uuid is None
                        and atom_uuid is None
                    ):
                        # DB CHECK requires at least one origin — skip
                        continue
                    session.add(SocietyCohortEvidenceLink(
                        id=uuid.uuid4(),
                        cohort_id=cid,
                        source_record_id=None,
                        discussion_turn_id=turn_uuid,
                        memory_atom_id=atom_uuid,
                        persona_id=persona_uuid,
                        evidence_role=role,
                        excerpt=excerpt[:1500],
                        confidence=Decimal("0.80"),
                    ))
                    inserted_links += 1

            # 3) rollup row
            session.add(SocietyCohortRollup(
                id=uuid.uuid4(),
                run_scope_id=run_scope_id,
                phase=PHASE_LABEL,
                cohort_count=len(cohort_persona_lists),
                persona_count=len(personas),
                weighted_stance_distribution=(
                    rollup.get("weighted_stance_distribution") or {}
                ),
                weighted_objection_summary=(
                    rollup.get("weighted_objection_summary") or {}
                ),
                weighted_proof_need_summary=(
                    rollup.get("weighted_proof_need_summary") or {}
                ),
                social_influence_summary=(
                    rollup.get("social_influence_summary") or {}
                ),
                resistance_summary=(
                    rollup.get("resistance_summary") or {}
                ),
                caveats=rollup.get("caveats") or [],
                quality_scores=quality,
            ))
            inserted_rollups += 1

    audit["cohorts_inserted"] = inserted_cohorts
    audit["evidence_links_inserted"] = inserted_links
    audit["rollups_inserted"] = inserted_rollups

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
        "simulations",
    )
    audit["additive_only_check"] = {
        "non_cohort_deltas_zero": all(
            delta.get(k, 0) == 0 for k in forbidden_table_keys
        ),
        "delta_society_cohorts": delta.get("society_cohorts", 0),
        "delta_society_cohort_evidence_links": delta.get(
            "society_cohort_evidence_links", 0,
        ),
        "delta_society_cohort_rollups": delta.get(
            "society_cohort_rollups", 0,
        ),
    }

    # Render to disk
    REPORT_JSON_PATH.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8",
    )
    REPORT_MD_PATH.write_text(md, encoding="utf-8")

    # Security scan
    json_text = json.dumps(audit, indent=2, default=str)
    audit_scan = scan_for_secrets(json_text)
    md_scan = scan_for_secrets(md)
    audit["security_redaction_audit"] = {
        "secrets_clean": audit_scan.is_clean and md_scan.is_clean,
        "finding_count": (
            len(audit_scan.findings) + len(md_scan.findings)
        ),
        "scanner_version": "9D.universal",
    }

    pass_required = (
        TARGET_MIN_COHORTS <= len(cohort_persona_lists) <= TARGET_MAX_COHORTS
        and assign_audit["every_persona_assigned_exactly_once"]
        and not fb_audit["any_fake_target_product_use"]
        and not fb_audit["any_forecast_or_verdict"]
        and not sens_audit["any_sensitive_inference"]
        and audit["additive_only_check"]["non_cohort_deltas_zero"]
        and audit["security_redaction_audit"]["secrets_clean"]
        and quality["ready_state"] == "READY_FOR_HUGE_SOCIETY_ARCHITECTURE"
    )
    audit["ready_for_huge_society_architecture"] = bool(pass_required)
    audit["recommendation"] = (
        "PASS — Phase 9D complete. Cohort/cluster architecture working "
        "at 66-person scale. Recommended next phase: Phase 10A — "
        "API / demo packaging for founder-input → society-report flow."
        if pass_required else
        "PARTIAL — cohort architecture ran but one or more pass "
        "conditions did not hold; see quality_scores."
    )
    audit["report_files"] = {
        "report_json": str(REPORT_JSON_PATH),
        "report_md": str(REPORT_MD_PATH),
    }

    AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    QUALITY_PATH.write_text(json.dumps({
        "phase": "9d_cohort_quality",
        "completed_at": datetime.now(UTC).isoformat(),
        "run_scope_id": run_scope_id,
        "cohort_count": len(cohort_persona_lists),
        "quality_scores": quality,
        "forbidden_claim_audit": fb_audit,
        "sensitive_inference_audit": sens_audit,
        "ready_for_huge_society_architecture": (
            audit["ready_for_huge_society_architecture"]
        ),
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nPhase {PHASE_LABEL} — committed.")
    print(
        f"  personas={len(personas)} cohorts={len(cohort_persona_lists)} "
        f"sizes={audit['cohort_size_distribution']}"
    )
    print(
        f"  inserted: cohorts={inserted_cohorts} "
        f"evidence_links={inserted_links} rollups={inserted_rollups}"
    )
    print(
        f"  quality.aggregate={quality['aggregate_score']} "
        f"ready_state={quality['ready_state']}"
    )
    print(
        f"  ready_for_huge_society_architecture="
        f"{audit['ready_for_huge_society_architecture']}"
    )
    print(f"\n→ orchestrator audit: {AUDIT_PATH}")
    print(f"→ quality artifact:   {QUALITY_PATH}")
    print(f"→ report (md):        {REPORT_MD_PATH}")
    print(f"→ report (json):      {REPORT_JSON_PATH}")
    return 0 if pass_required else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
