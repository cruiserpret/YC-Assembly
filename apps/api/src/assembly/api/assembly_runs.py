"""Phase 10A.1 — POST /assembly/runs + GET endpoints for the founder
demo flow.

Two modes:
  - fixture_demo: returns the existing 9B.1/9D/9E artifacts; no LLM,
                  no DB writes beyond the run-tracking row, no new
                  retrieval. Frontend can build against this immediately.
  - live_founder_brief (Phase 10A.1): persists a run row, schedules a
                  BackgroundTask that walks the 13-stage live pipeline,
                  and returns 202 + status="running" — NO LONGER
                  skeletal. Per-run artifacts land under
                  `_audit/live_runs/{run_id}/`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assembly.api.deps import db_session
from assembly.api.fixture_demo_loader import (
    fixture_artifact_manifest,
    fixture_audit_dev_only,
    fixture_cohorts,
    fixture_discussion,
    fixture_intent,
    fixture_main_report,
    fixture_main_report_md,
    fixture_personas,
    is_fixture_available,
)
from assembly.db import get_sessionmaker
from assembly.models.assembly_run import (
    ARTIFACT_TYPES,
    RUN_STAGES,
    AssemblyRun,
    AssemblyRunArtifact,
)
from assembly.orchestration.live_founder_brief import (
    estimate_pipeline_cost,
    run_live_founder_brief_pipeline,
)
from assembly.schemas.founder_brief import (
    CreateAssemblyRunRequest,
    CreateAssemblyRunResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(db_session)]


_ESTIMATED_STEP_COUNT = 13


def _stage_template(mode: str) -> dict[str, dict[str, str | None]]:
    """Initial stage_progress dict for a run."""
    if mode == "fixture_demo":
        # All stages are pre-completed because the fixture demo reads
        # existing artifacts.
        return {
            stage: {
                "status": "complete",
                "started_at": None,
                "completed_at": None,
                "description": _stage_description(stage),
            }
            for stage in RUN_STAGES if stage not in ("complete", "failed")
        }
    return {
        stage: {
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "description": _stage_description(stage),
        }
        for stage in RUN_STAGES if stage not in ("complete", "failed")
    }


def _stage_description(stage: str) -> str:
    return {
        "validating_brief": "Validate founder input and reject hardcoded personas.",
        "planning_evidence": "Plan evidence retrieval queries from the brief.",
        "retrieving_evidence": "Pull evidence from configured retrieval providers.",
        "scoring_evidence": "Score evidence relevance + filter low-quality items.",
        "building_personas": "Compress evidence into run-scoped persona records.",
        "enriching_psychology": "Infer OCEAN + 6 additional psychology traits.",
        "running_individual_simulation": (
            "Run baseline individual-persona stance simulation."
        ),
        "running_group_discussion": (
            "Run grouped discussion: opening, challenge, peer-response, "
            "proof-discussion."
        ),
        "repairing_incomplete_outputs": (
            "Repair any missing reflection ballots from the discussion."
        ),
        "building_cohorts": (
            "Cluster personas into traceable cohorts via deterministic "
            "agglomerative clustering."
        ),
        "inferring_simulated_intent": (
            "Infer one simulated-intent label per persona from a closed set."
        ),
        "running_society_wide_debate": (
            "Cross-cohort argument propagation (deterministic)."
        ),
        "generating_report": (
            "Render the founder-facing report (JSON + markdown)."
        ),
    }.get(stage, stage)


# -----------------------------------------------------------------------
# POST /assembly/runs
# -----------------------------------------------------------------------


async def _spawn_live_pipeline(run_id: uuid.UUID) -> None:
    """BackgroundTasks-friendly entry point that spawns the live
    pipeline. Decoupled from the request lifecycle so the HTTP
    response returns 202 immediately.

    Phase 10A.2: defaults to fresh evidence-driven persona generation.
    The internal-only `_dev_reuse_existing_society` flag is NEVER set
    here — the API contract for live_founder_brief is fresh-mode only.
    """
    try:
        await run_live_founder_brief_pipeline(run_id)
    except Exception:  # noqa: BLE001
        logger.exception("live_founder_brief.background_task_failed run_id=%s", run_id)


@router.post(
    "/runs",
    status_code=http_status.HTTP_202_ACCEPTED,
    response_model=CreateAssemblyRunResponse,
)
async def create_run(
    payload: CreateAssemblyRunRequest,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> CreateAssemblyRunResponse:
    """Validate the founder brief and create a new run.

    For `fixture_demo` mode: status is set to `complete` immediately
    and the artifact manifest points at the existing 9B.1/9D/9E files.

    For `live_founder_brief` mode (Phase 10A.1): status is set to
    `running`, the run row is persisted, a BackgroundTask schedules the
    13-stage pipeline, and the response returns 202 with the run_id
    plus a cost estimate so the caller can poll for completion."""
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    if payload.mode == "fixture_demo":
        if not is_fixture_available():
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "fixture_demo unavailable: 9B.1/9D/9E audit "
                    "artifacts not found on disk."
                ),
            )
        manifest = fixture_artifact_manifest()
        run = AssemblyRun(
            id=run_id,
            user_id=None,
            mode=payload.mode,
            product_brief=payload.brief.model_dump(),
            status="complete",
            current_stage="complete",
            stage_progress=_stage_template(payload.mode),
            artifact_manifest=manifest,
            error_message=None,
            linked_run_scope_id="run_9b_lumaloop_ea818fbeeb21",
            updated_at=now,
        )
        session.add(run)
        await session.commit()
        return CreateAssemblyRunResponse(
            run_id=str(run.id),
            status=run.status,
            mode=payload.mode,
            current_stage=run.current_stage,
            estimated_steps=_ESTIMATED_STEP_COUNT,
            artifact_manifest={k: v for k, v in (manifest or {}).items()},
        )

    # live_founder_brief mode: schedule the live pipeline
    cost_est = estimate_pipeline_cost(
        persona_count=24, report_depth=payload.brief.report_depth,
    )
    cap_usd = float(payload.brief.max_budget_usd or 12.0)
    if cost_est["estimated_cost_usd"] > cap_usd:
        raise HTTPException(
            status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"cost estimate ${cost_est['estimated_cost_usd']:.2f} "
                f"exceeds max_budget_usd ${cap_usd:.2f}; raise the "
                "budget or pick a smaller society size."
            ),
        )
    run = AssemblyRun(
        id=run_id,
        user_id=None,
        mode=payload.mode,
        product_brief=payload.brief.model_dump(),
        status="running",
        current_stage="validating_brief",
        stage_progress={
            stage: {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "description": _stage_description(stage),
            }
            for stage in RUN_STAGES if stage not in ("complete", "failed")
        },
        artifact_manifest={
            "_cost_estimate": json.dumps(cost_est),
        },
        error_message=None,
        linked_run_scope_id=None,
        updated_at=now,
    )
    session.add(run)
    await session.commit()
    # schedule the pipeline as a FastAPI BackgroundTask
    background_tasks.add_task(_spawn_live_pipeline, run_id)
    return CreateAssemblyRunResponse(
        run_id=str(run.id),
        status="running",
        mode=payload.mode,
        current_stage="validating_brief",
        estimated_steps=_ESTIMATED_STEP_COUNT,
        artifact_manifest={"_cost_estimate": json.dumps(cost_est)},
    )


async def _load_run(
    session: AsyncSession, run_id: str,
) -> AssemblyRun:
    try:
        rid = uuid.UUID(run_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="run_id must be a valid UUID",
        )
    row = (await session.execute(
        select(AssemblyRun).where(AssemblyRun.id == rid)
    )).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    return row


# -----------------------------------------------------------------------
# GET /assembly/runs/{run_id}
# -----------------------------------------------------------------------


@router.get("/runs/{run_id}")
async def get_run_status(run_id: str, session: SessionDep) -> dict:
    run = await _load_run(session, run_id)
    completed_stages = [
        s for s, info in (run.stage_progress or {}).items()
        if isinstance(info, dict) and info.get("status") == "complete"
    ]
    failed = [
        s for s, info in (run.stage_progress or {}).items()
        if isinstance(info, dict) and info.get("status") == "failed"
    ]
    progress_pct = round(
        100.0 * len(completed_stages)
        / max(_ESTIMATED_STEP_COUNT, 1),
        1,
    )
    return {
        "run_id": str(run.id),
        "mode": run.mode,
        "status": run.status,
        "current_stage": run.current_stage,
        "completed_stages": completed_stages,
        "failed_stage": failed[0] if failed else None,
        "progress_pct": progress_pct,
        "stage_progress": run.stage_progress,
        "artifact_links": {
            k: f"/assembly/runs/{run.id}/{ep}"
            for k, ep in (
                ("report_json", "report"),
                ("report_markdown", "report.md"),
                ("personas_json", "personas"),
                ("cohorts_json", "cohorts"),
                ("discussion_json", "discussion"),
                ("intent_json", "intent"),
                ("audit_json", "audit"),
            )
        },
        "error_message": run.error_message,
        "caveat": (
            "Assembly produces synthetic-society simulations and "
            "simulated intent — never real-world purchase forecasts "
            "or launch verdicts."
        ),
    }


# -----------------------------------------------------------------------
# Report endpoints
# -----------------------------------------------------------------------


def _load_live_artifact_json(
    run: AssemblyRun, artifact_key: str,
) -> dict | None:
    """Load a live-run artifact JSON file from
    `_audit/live_runs/{run_id}/<filename>`."""
    manifest = run.artifact_manifest or {}
    path_str = manifest.get(artifact_key)
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _live_run_status_check(run: AssemblyRun) -> None:
    """Raise 409/425 when a live run is not yet complete."""
    if run.mode == "fixture_demo":
        return
    if run.status == "running":
        raise HTTPException(
            status_code=http_status.HTTP_425_TOO_EARLY,
            detail=(
                f"live run is still running (current_stage="
                f"{run.current_stage}); poll GET /assembly/runs/"
                f"{run.id} until status='complete'."
            ),
        )
    if run.status == "failed":
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"live run failed at stage='{run.current_stage}': "
                f"{run.error_message or 'no error message'}"
            ),
        )
    if run.status == "skeletal":
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "live run is in 'skeletal' state — this should not "
                "happen in Phase 10A.1; create a new run."
            ),
        )


@router.get("/runs/{run_id}/report")
async def get_report_json(
    run_id: str, session: SessionDep,
) -> dict:
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        payload = fixture_main_report()
        if payload is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="fixture report could not be loaded.",
            )
        payload["run_id"] = str(run.id)
        return payload
    _live_run_status_check(run)
    payload = _load_live_artifact_json(run, "report_json")
    if payload is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live report not on disk for this run.",
        )
    payload["run_id"] = str(run.id)
    return payload


@router.get(
    "/runs/{run_id}/report.md",
    response_class=Response,
)
async def get_report_markdown(
    run_id: str, session: SessionDep,
) -> Response:
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        md = fixture_main_report_md()
        if md is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="fixture markdown report not on disk.",
            )
        return Response(content=md, media_type="text/markdown")
    _live_run_status_check(run)
    md_path_str = (run.artifact_manifest or {}).get("report_markdown")
    if not md_path_str:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live markdown report not on disk for this run.",
        )
    p = Path(md_path_str)
    if not p.exists():
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live markdown report path missing.",
        )
    return Response(
        content=p.read_text(encoding="utf-8"), media_type="text/markdown",
    )


@router.get("/runs/{run_id}/personas")
async def get_personas(run_id: str, session: SessionDep) -> dict:
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        payload = fixture_personas()
        if payload is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="fixture personas could not be loaded.",
            )
        return {"run_id": str(run.id), **payload}
    _live_run_status_check(run)
    payload = _load_live_artifact_json(run, "personas_json")
    if payload is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live personas artifact not on disk for this run.",
        )
    return {"run_id": str(run.id), **payload}


@router.get("/runs/{run_id}/cohorts")
async def get_cohorts(run_id: str, session: SessionDep) -> dict:
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        payload = fixture_cohorts()
        if payload is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="fixture cohorts could not be loaded.",
            )
        return {"run_id": str(run.id), **payload}
    _live_run_status_check(run)
    payload = _load_live_artifact_json(run, "cohorts_json")
    if payload is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live cohorts artifact not on disk for this run.",
        )
    return {"run_id": str(run.id), **payload}


@router.get("/runs/{run_id}/discussion")
async def get_discussion(run_id: str, session: SessionDep) -> dict:
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        payload = fixture_discussion()
        if payload is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="fixture discussion could not be loaded.",
            )
        return {"run_id": str(run.id), **payload}
    _live_run_status_check(run)
    payload = _load_live_artifact_json(run, "discussion_json")
    if payload is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live discussion artifact not on disk for this run.",
        )
    return {"run_id": str(run.id), **payload}


@router.get("/runs/{run_id}/discussion/turns")
async def get_discussion_turns(
    run_id: str, session: SessionDep,
) -> dict:
    """Phase 10B addition — return the actual round-by-round transcript
    so founders can see WHAT each synthetic agent said and WHY, not
    just aggregate counts. Shape:

      {
        "run_id": "...",
        "discussion_session_id": "...",
        "groups": [
          {
            "group_index": 0,
            "personas": [{ "persona_id", "display_name", "role" }, ...],
            "rounds": [
              {
                "round_number": 1,
                "round_label": "public_opening",
                "turns": [
                  {
                    "turn_id", "speaker_persona_id", "speaker_name",
                    "speaker_role", "stance", "public_text",
                    "referenced_turn_ids"
                  }, ...
                ]
              }, ...
            ]
          }, ...
        ],
        "private_ballots": {
          "<persona_id>": {
            "pre":   { "stance", "reasoning", "top_objection",
                       "top_proof_need", "is_repaired" },
            "reflection": {...},
            "final": {...}
          }
        }
      }

    Live mode only — fixture_demo returns the existing summary at
    /assembly/runs/{id}/discussion.
    """
    from sqlalchemy import select
    from assembly.models.discussion import (
        DiscussionGroup, DiscussionPrivateBallot,
        DiscussionSession, DiscussionTurn,
    )
    from assembly.models.persona import PersonaRecord
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        # The fixture transcript isn't part of the prebuilt artifacts;
        # surface a clean empty-state instead of leaking the live SQL
        # path for a fixture run.
        return {
            "run_id": str(run.id),
            "discussion_session_id": None,
            "groups": [],
            "private_ballots": {},
            "note": (
                "Per-turn transcript is only emitted for live "
                "founder-brief runs. Switch the form to "
                "live_founder_brief to get a full transcript."
            ),
        }
    _live_run_status_check(run)
    payload = _load_live_artifact_json(run, "discussion_json")
    if not payload:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live discussion artifact not on disk for this run.",
        )
    sess_id_str = payload.get("discussion_session_id")
    if not sess_id_str:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run has no discussion_session_id recorded.",
        )
    try:
        sess_id = uuid.UUID(sess_id_str)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="discussion_session_id is not a valid UUID.",
        )
    groups = (await session.execute(
        select(DiscussionGroup).where(
            DiscussionGroup.discussion_session_id == sess_id,
        ).order_by(DiscussionGroup.group_index.asc())
    )).scalars().all()
    if not groups:
        return {
            "run_id": str(run.id),
            "discussion_session_id": str(sess_id),
            "groups": [],
            "private_ballots": {},
        }
    group_ids = [g.id for g in groups]
    persona_ids: list[uuid.UUID] = []
    for g in groups:
        for pid in (g.persona_ids or []):
            if pid not in persona_ids:
                persona_ids.append(pid)
    personas_rows = (await session.execute(
        select(PersonaRecord).where(PersonaRecord.id.in_(persona_ids))
    )).scalars().all()
    persona_by_id = {p.id: p for p in personas_rows}
    turns = (await session.execute(
        select(DiscussionTurn).where(
            DiscussionTurn.discussion_group_id.in_(group_ids),
        ).order_by(
            DiscussionTurn.round_number.asc(),
            DiscussionTurn.turn_number.asc(),
        )
    )).scalars().all()
    ballots = (await session.execute(
        select(DiscussionPrivateBallot).where(
            DiscussionPrivateBallot.discussion_session_id == sess_id,
        )
    )).scalars().all()

    def _persona_role(p: PersonaRecord) -> str:
        for tag in (p.product_relevance_tags or []):
            if tag.startswith("normalized_primary_role:"):
                return tag.split(":", 1)[1]
        return p.segment_label or "unknown"

    # Group turns by group_index → round_number
    out_groups: list[dict] = []
    for g in groups:
        g_persona_ids = list(g.persona_ids or [])
        g_personas = [
            {
                "persona_id": str(pid),
                "display_name": (
                    persona_by_id[pid].display_name
                    if pid in persona_by_id else "anonymous"
                ),
                "role": (
                    _persona_role(persona_by_id[pid])
                    if pid in persona_by_id else "unknown"
                ),
            }
            for pid in g_persona_ids
        ]
        rounds_map: dict[int, list[dict]] = {}
        for t in turns:
            if t.discussion_group_id != g.id:
                continue
            speaker = persona_by_id.get(t.speaker_persona_id)
            rounds_map.setdefault(t.round_number, []).append({
                "turn_id": str(t.id),
                "turn_number": t.turn_number,
                "speaker_persona_id": str(t.speaker_persona_id),
                "speaker_name": (
                    speaker.display_name if speaker else "anonymous"
                ),
                "speaker_role": (
                    _persona_role(speaker) if speaker else "unknown"
                ),
                "turn_type": t.turn_type,
                "stance": t.stance,
                "public_text": t.public_text or "",
                "referenced_turn_ids": [
                    str(r) for r in (t.referenced_turn_ids or [])
                ],
            })
        rounds_out = []
        round_labels = {
            1: "public_opening",
            2: "challenge",
            3: "peer_response",
            4: "proof_discussion",
            5: "reflection_round",
            6: "final_ballot_round",
        }
        for rn in sorted(rounds_map.keys()):
            rounds_out.append({
                "round_number": rn,
                "round_label": round_labels.get(rn, f"round_{rn}"),
                "turns": rounds_map[rn],
            })
        out_groups.append({
            "group_index": g.group_index,
            "personas": g_personas,
            "rounds": rounds_out,
        })

    # Private ballots keyed by persona_id
    ballots_by_pid: dict[str, dict[str, dict]] = {}
    for b in ballots:
        pid_str = str(b.persona_id)
        reasoning = b.private_reasoning or ""
        is_repaired = (
            "[repair_marker:" in reasoning
            or "[deterministic_fallback_marker]" in reasoning
        )
        # Strip internal markers from displayed text
        cleaned = reasoning
        for marker in (
            "[repair_marker:llm_strict]",
            "[repair_marker:llm_stricter]",
            "[deterministic_fallback_marker]",
        ):
            cleaned = cleaned.replace(marker, "").strip()
        ballots_by_pid.setdefault(pid_str, {})[b.ballot_stage] = {
            "stance": b.private_stance,
            "reasoning": cleaned,
            "confidence": b.confidence,
            "top_objection": b.top_objection,
            "top_proof_need": b.top_proof_need,
            "public_private_delta": b.public_private_delta,
            "is_repaired": is_repaired,
        }
    return {
        "run_id": str(run.id),
        "discussion_session_id": str(sess_id),
        "groups": out_groups,
        "private_ballots": ballots_by_pid,
    }


@router.get("/runs/{run_id}/intent")
async def get_intent(run_id: str, session: SessionDep) -> dict:
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        payload = fixture_intent()
        if payload is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="fixture intent could not be loaded.",
            )
        return {"run_id": str(run.id), **payload}
    _live_run_status_check(run)
    payload = _load_live_artifact_json(run, "intent_json")
    if payload is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live intent artifact not on disk for this run.",
        )
    return {"run_id": str(run.id), **payload}


# -----------------------------------------------------------------------
# Lightweight voters — Phase 14A surface (the 100-voter influence layer)
#
# Reads the on-disk artifacts that `run_lightweight_voter_overlay` writes
# during the live pipeline:
#   - lightweight_voters.json          (n_voters, voters[], sampling_warnings)
#   - final_100_voter_distribution.json (4-bucket distribution + calibrated)
#   - influence_rounds.json            (4-round influence loop + cluster args)
#   - diversity_health.json            (per-run diagnostics)
#   - representative_debates.json      (paraphrased samples, optional)
#
# Surfaces them as ONE consolidated founder-facing payload. Old runs that
# pre-date the Phase 12C overlay simply lack these files; the endpoint
# returns a `voter_overlay_available: false` shape so the frontend can
# gracefully omit the panel without breaking the rest of the report.
#
# NEVER regenerates voters, NEVER mutates artifacts, NEVER calls LLMs.
# -----------------------------------------------------------------------


def _resolve_live_run_dir(run: AssemblyRun) -> Path:
    """Resolve the on-disk run_dir for a live run.

    Phase 14C — prefer the configured durable artifact root
    (``ASSEMBLY_ARTIFACT_ROOT``, default ``apps/api/_audit``) via
    ``run_artifact_dir``, which is where the orchestrator now writes.
    Falls back to the legacy cwd-relative ``_audit/live_runs/{run_id}``
    path for old/test layouts. Returns the durable path when neither
    exists, so a missing artifact yields the graceful unavailable
    contract (this function never raises for a missing dir)."""
    from assembly.artifact_paths import run_artifact_dir
    durable = run_artifact_dir(str(run.id))
    if durable.exists():
        return durable
    legacy = Path(f"_audit/live_runs/{run.id}")
    if legacy.exists():
        return legacy
    return durable


def _read_run_artifact(
    run_dir: Path, filename: str,
) -> dict | None:
    p = run_dir / filename
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


@router.get("/runs/{run_id}/lightweight_voters")
async def get_lightweight_voters(
    run_id: str, session: SessionDep,
) -> dict:
    """Founder-facing 100-voter overlay payload.

    Returns:
        {
          run_id: str,
          voter_overlay_available: bool,
          voters_count: int,
          final_distribution: { buyer, receptive, uncertain, skeptical, n_voters, ... } | None,
          calibrated_distribution: { distribution_percent, confidence_band_pp, ... } | None,
          influence_rounds: [ { round_idx, voters_affected, intent_changes, bucket_changes, bucket_distribution, skeptic_transitions, ... } ],
          cluster_arguments: { ... } | None,
          diversity_health: { ... } | None,
          samples: [ paraphrased voter narratives ] | [],
          source: "lightweight_voters.json + final_100_voter_distribution.json + influence_rounds.json + diversity_health.json"
        }

    `voter_overlay_available: false` is returned (HTTP 200, NOT 404)
    when a run pre-dates the Phase 12C overlay or the artifacts are
    otherwise missing. This lets the frontend show the report without
    the voter panel without surfacing a hard error.
    """
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        # Fixture mode never ran the voter pipeline. Return the empty-
        # state shape; frontend will hide the panel.
        return {
            "run_id": str(run.id),
            "voter_overlay_available": False,
            "reason": (
                "fixture_demo runs do not exercise the 100-voter overlay"
            ),
        }
    _live_run_status_check(run)

    run_dir = _resolve_live_run_dir(run)
    voters_artifact = _read_run_artifact(run_dir, "lightweight_voters.json")
    final_dist_artifact = _read_run_artifact(
        run_dir, "final_100_voter_distribution.json",
    )
    influence_artifact = _read_run_artifact(run_dir, "influence_rounds.json")
    diversity_artifact = _read_run_artifact(run_dir, "diversity_health.json")
    rep_debates_artifact = _read_run_artifact(
        run_dir, "representative_debates.json",
    )

    if voters_artifact is None and final_dist_artifact is None:
        # Run pre-dates Phase 12C OR overlay failed at runtime. Either
        # way, frontend gets a clear empty-state signal.
        return {
            "run_id": str(run.id),
            "voter_overlay_available": False,
            "reason": (
                "lightweight_voters.json / final_100_voter_distribution.json "
                "not on disk for this run (may pre-date the Phase 12C "
                "voter overlay)"
            ),
        }

    final_distribution = None
    calibrated_distribution = None
    raw_24_distribution = None
    if isinstance(final_dist_artifact, dict):
        lvd = final_dist_artifact.get("lightweight_voter_distribution")
        if isinstance(lvd, dict):
            final_distribution = lvd
        cal = final_dist_artifact.get("calibrated_distribution")
        if isinstance(cal, dict):
            calibrated_distribution = cal
        raw_24_distribution = final_dist_artifact.get(
            "raw_24_distribution_percent"
        )

    influence_rounds: list[dict] = []
    cluster_arguments = None
    if isinstance(influence_artifact, dict):
        rounds_blob = influence_artifact.get("rounds") or []
        if isinstance(rounds_blob, list):
            # Strip the per_voter_log payloads from each round — they
            # blow up response size and the panel only needs the
            # aggregate counts + bucket_distribution snapshot.
            slim_rounds: list[dict] = []
            for r in rounds_blob:
                if not isinstance(r, dict):
                    continue
                slim_rounds.append({
                    k: v for k, v in r.items()
                    if k != "per_voter_log"
                })
            influence_rounds = slim_rounds
        cluster_arguments = influence_artifact.get("cluster_arguments")

    samples: list[dict] = []
    if isinstance(rep_debates_artifact, dict):
        raw_samples = rep_debates_artifact.get("samples") or []
        if isinstance(raw_samples, list):
            samples = raw_samples[:6]

    n_voters = 0
    if isinstance(voters_artifact, dict):
        n_voters = int(voters_artifact.get("n_voters") or 0)
    if n_voters == 0 and isinstance(final_distribution, dict):
        n_voters = int(final_distribution.get("n_voters") or 0)

    return {
        "run_id": str(run.id),
        "voter_overlay_available": True,
        "voters_count": n_voters,
        "final_distribution": final_distribution,
        "calibrated_distribution": calibrated_distribution,
        "raw_24_distribution_percent": raw_24_distribution,
        "influence_rounds": influence_rounds,
        "cluster_arguments": cluster_arguments,
        "diversity_health": diversity_artifact,
        "samples": samples,
        "source_notes": {
            "phase": "12c_lightweight_voters",
            "files_loaded": {
                "lightweight_voters_json": voters_artifact is not None,
                "final_100_voter_distribution_json": (
                    final_dist_artifact is not None
                ),
                "influence_rounds_json": influence_artifact is not None,
                "diversity_health_json": diversity_artifact is not None,
                "representative_debates_json": (
                    rep_debates_artifact is not None
                ),
            },
            "note": (
                "Voters react to the deep-agent debate arguments through "
                "a 4-round bounded-confidence influence loop. No LLM "
                "calls per voter; no free-text generation."
            ),
        },
    }


# -----------------------------------------------------------------------
# Audit endpoint — internal/dev-only
# -----------------------------------------------------------------------


@router.get("/runs/{run_id}/audit")
async def get_audit(
    run_id: str,
    session: SessionDep,
    x_dev_key: str | None = None,
) -> dict:
    """Internal/dev-only. Returns quality + safety + DB-delta summaries
    for this run. For fixture_demo runs returns the static fixture
    audit. For live runs returns the per-run audit aggregated from
    the live ``run_quality.json`` + ``persona_quality_gates.json`` +
    ``final_ballot_repair.json`` + retrieval audit + wording audit.

    Requires a non-empty ``x-dev-key`` header in production.
    Frontend should NOT rely on this; it's for operator self-audit."""
    from assembly.config import get_settings
    settings = get_settings()
    if settings.env == "production" and not x_dev_key:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="audit endpoint requires x-dev-key header in production",
        )
    run = await _load_run(session, run_id)
    if run.mode == "fixture_demo":
        payload = fixture_audit_dev_only()
        return {
            "run_id": str(run.id),
            "mode": run.mode,
            "linked_run_scope_id": run.linked_run_scope_id,
            **payload,
        }
    # Live run — gather live-only audits from disk
    _live_run_status_check(run)
    # Phase 14C — resolve via the durable artifact root (same resolver
    # the voter overlay endpoint uses).
    run_dir = _resolve_live_run_dir(run)

    def _load(name: str) -> dict | None:
        p = run_dir / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    return {
        "run_id": str(run.id),
        "mode": run.mode,
        "linked_run_scope_id": run.linked_run_scope_id,
        "current_stage": run.current_stage,
        "status": run.status,
        "audit_kind": "live_founder_brief",
        "run_quality": _load("run_quality.json"),
        "persona_quality_gates": _load("persona_quality_gates.json"),
        "final_ballot_repair": _load("final_ballot_repair.json"),
        "evidence_retrieval": _load("evidence_retrieval.json"),
        "evidence_quality": _load("evidence_quality.json"),
        "evidence_signals": _load("evidence_signals.json"),
        "discussion_quality": _load("discussion_quality.json"),
        "fresh_live_artifact_wording_audit": _load(
            "fresh_live_artifact_wording_audit.json"
        ),
        "user_facing_language_audit": _load(
            "user_facing_language_audit.json"
        ),
    }
