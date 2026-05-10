"""Phase 10A.2 — resume an existing live_founder_brief run from
the cohort stage. Used to validate the cohort-stage TypeError fix
without re-spending on retrieval/discussion.

Loads everything cohorts→report need from DB + existing artifacts,
calls the four remaining stage runners, and finalizes the run row.

Usage:
    uv run python scripts/resume_live_demo_from_cohorts.py <run_uuid>
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from assembly.config import get_settings
from assembly.db import get_sessionmaker
from assembly.models.assembly_run import AssemblyRun, AssemblyRunArtifact
from assembly.models.discussion import (
    DiscussionGroup, DiscussionPrivateBallot, DiscussionSession,
    DiscussionTurn, PersonaMemoryAtom,
)
from assembly.models.persona import PersonaRecord
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.orchestration.live_founder_brief import (
    _LIVE_RUNS_ROOT, _STAGE_RUNNERS, _add_artifact, _update_run,
)


async def main() -> None:
    if len(sys.argv) != 2:
        print("usage: resume_live_demo_from_cohorts.py <run_uuid>")
        sys.exit(2)
    run_id = uuid.UUID(sys.argv[1])
    sm = get_sessionmaker()
    run_dir = _LIVE_RUNS_ROOT / str(run_id)
    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    # Load the run row + brief
    async with sm() as session:
        run = (await session.execute(
            select(AssemblyRun).where(AssemblyRun.id == run_id)
        )).scalars().first()
        if run is None:
            raise SystemExit(f"AssemblyRun {run_id} not found")
    brief = run.product_brief or {}
    print(f"[resume] product={brief.get('product_name')} run_id={run_id}")

    # Identify the live_run_scope_id from persistence.json
    persistence_doc = json.loads(
        (run_dir / "persistence.json").read_text(encoding="utf-8")
    )
    run_scope_id = persistence_doc["run_scope_id"]
    print(f"[resume] run_scope_id={run_scope_id}")

    # Load all DB state needed for cohorts onward
    async with sm() as session:
        personas = (await session.execute(
            select(PersonaRecord).where(
                PersonaRecord.product_relevance_tags.contains(
                    [f"run_scope_id:{run_scope_id}"]
                )
            )
        )).scalars().all()
        if not personas:
            raise SystemExit(
                f"no PersonaRecord rows found for run_scope_id={run_scope_id}"
            )
        persona_ids = [p.id for p in personas]
        psy = (await session.execute(
            select(PersonaPsychologyTrait).where(
                PersonaPsychologyTrait.persona_id.in_(persona_ids)
            ).where(
                PersonaPsychologyTrait.run_scope_id == run_scope_id
            )
        )).scalars().all()
        sess_row = (await session.execute(
            select(DiscussionSession).where(
                DiscussionSession.run_scope_id == run_scope_id
            ).order_by(DiscussionSession.created_at.desc()).limit(1)
        )).scalars().first()
        if sess_row is None:
            raise SystemExit(
                f"no DiscussionSession found for run_scope_id={run_scope_id}"
            )
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id == sess_row.id
            )
        )).scalars().all()
        gids = [g.id for g in groups]
        turns = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(gids)
            )
        )).scalars().all()
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id == sess_row.id
            )
        )).scalars().all()
        atoms = (await session.execute(
            select(PersonaMemoryAtom).where(
                PersonaMemoryAtom.run_scope_id == run_scope_id
            )
        )).scalars().all()
    by_pid: dict[uuid.UUID, dict[str, float]] = {}
    for t in psy:
        by_pid.setdefault(t.persona_id, {})[t.trait_name] = float(
            t.value_numeric
        )
    print(
        f"[resume] loaded personas={len(personas)} psy_traits={len(psy)} "
        f"turns={len(turns)} ballots={len(ballots)} atoms={len(atoms)}"
    )

    # Build ctx exactly as the orchestrator would have
    ctx: dict[str, object] = {
        "_dev_reuse_existing_society": False,
        "preferred_persona_count": len(personas),
        "max_budget_usd": 12.0,
        "brief": brief,
        "live_run_scope_id": run_scope_id,
        "live_persona_uuids": persona_ids,
        "personas": personas,
        "psychology_by_pid": by_pid,
        "existing_session_id": sess_row.id,
        "turns": turns,
        "ballots": ballots,
        "memory_atoms": atoms,
    }

    # Run remaining stages — Phase 10A.3 also re-runs the
    # individual_simulation stub (to overwrite the stale wording
    # written by 10A.2) and the final-ballot repair gate (so missing
    # final ballots are repaired and the new audit JSON is emitted).
    remaining_stages = (
        "running_individual_simulation",
        "repairing_incomplete_outputs",
        "building_cohorts",
        "inferring_simulated_intent",
        "running_society_wide_debate",
        "generating_report",
    )
    for stage in remaining_stages:
        print(f"[resume] running stage={stage}")
        await _update_run(
            sm, run_id,
            current_stage=stage,
            stage_status=(stage, "running"),
        )
        try:
            async with sm() as session:
                run_row = (await session.execute(
                    select(AssemblyRun).where(AssemblyRun.id == run_id)
                )).scalars().first()
            await _STAGE_RUNNERS[stage](
                sm=sm, run=run_row, run_dir=run_dir, ctx=ctx,
            )
            await _update_run(
                sm, run_id,
                stage_status=(stage, "complete"),
            )
            print(f"[resume]   ✓ {stage} complete")
        except Exception as exc:
            print(f"[resume]   ✗ {stage} FAILED: {type(exc).__name__}: {exc}")
            await _update_run(
                sm, run_id,
                status="failed",
                current_stage=stage,
                stage_status=(stage, "failed"),
                error_message=(
                    f"[{stage}] resume error: {type(exc).__name__}: "
                    f"{str(exc)[:240]}"
                ),
            )
            raise

    manifest = ctx.get("report_files") or {}
    await _update_run(
        sm, run_id,
        status="complete",
        current_stage="complete",
        artifact_manifest_update=manifest,
    )
    for artifact_type, path in manifest.items():
        try:
            await _add_artifact(
                sm, run_id, artifact_type, path,
                content_type=(
                    "text/markdown" if artifact_type == "report_markdown"
                    else "application/json"
                ),
            )
        except Exception:
            pass
    print(f"[resume] DONE — status=complete artifact_manifest={manifest}")


if __name__ == "__main__":
    asyncio.run(main())
