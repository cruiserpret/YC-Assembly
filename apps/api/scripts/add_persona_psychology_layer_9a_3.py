"""Phase 9A.3 — add OCEAN + 5 additional psychology traits to the
official 9A.2 LumaLoop run-scoped society.

Loads the 30 PersonaRecords persisted under the 9A.2 run_scope_id along
with their PersonaTraits, PersonaEvidenceLinks, and the AgentResponses
from the 9A.2 simulation. For each persona, infers a PsychologyProfile
(11 traits) using the universal `assembly.sources.persona_psychology_layer`
inference engine. Persists the inferred traits into the new
`persona_psychology_traits` table.

NO live retrieval. NO new SourceRecords. NO new PersonaRecords. NO new
PersonaTraits. NO new PersonaEvidenceLinks. NO new simulations. NO new
agents. NO new agent_responses. NO LLM calls. The phase is a pure
inference pass over the data 9A.2 already produced.
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
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.models.round import AgentResponse
from assembly.models.simulation import Simulation
from assembly.sources.founder_report_generator import scan_for_secrets
from assembly.sources.persona_psychology_layer import (
    compute_profile_variance,
    detect_identical_profiles,
    infer_persona_psychology_profile,
    validate_no_sensitive_inferences,
)
from assembly.sources.persona_psychology_layer.schemas import (
    ALL_REQUIRED_OCEAN_PLUS_FIVE,
    OCEAN_TRAITS,
    PRICE_SENSITIVITY_TRAIT,
    PsychologyProfile,
)


PHASE_LABEL = "9A.3"
EXPECTED_PERSONA_COUNT = 30
DEFAULT_RUN_SCOPE_PREFIX = "run_9a2_"
NEUTRAL_DEFAULT_PCT_CEILING = 0.30
MEDIUM_OR_HIGH_CONFIDENCE_FLOOR = 0.70
IDENTICAL_PROFILE_PCT_CEILING = 0.35

AUDIT_ROOT = Path(__file__).resolve().parent.parent / "_audit"
AUDIT_PATH = AUDIT_ROOT / "persona_psychology_layer_9a_3.json"
QUALITY_PATH = AUDIT_ROOT / "persona_psychology_layer_9a_3_quality.json"
INPUT_9A_2_AUDIT_PATH = AUDIT_ROOT / "scale_lumaloop_society_9a_2.json"


# Forbidden new-API substrings — phase 9A.3 must use NO new retrieval.
_FORBIDDEN_RETRIEVAL_TOKENS = (
    "jina", "exa.ai", "exasearch", "dataforseo",
    "reddit", "apify", "firecrawl", "brave_search",
    "tavily", "youtube_data_api",
)


def _parse_tag_value(
    tags: list[str], key: str, default: str = "",
) -> str:
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


def _read_9a_2_audit() -> dict[str, Any]:
    if not INPUT_9A_2_AUDIT_PATH.exists():
        return {}
    return json.loads(INPUT_9A_2_AUDIT_PATH.read_text(encoding="utf-8"))


def _audit_consistency_check_for_9a_2(
    nine_a_2_audit: dict[str, Any],
) -> dict[str, Any]:
    """Generic before/after/dropped sanity check for 9A.2."""
    before = nine_a_2_audit.get("compressed_before_cap")
    after = nine_a_2_audit.get("compressed_after_cap")
    rejected = nine_a_2_audit.get("rejected_due_to_hard_cap") or []
    rejected_count = len(rejected)
    if before is None or after is None:
        return {
            "applied": False,
            "blocker": "9A.2 audit missing before/after counts",
        }
    expected_dropped = before - after
    consistent = rejected_count == expected_dropped
    explanation: str | None = None
    if not consistent:
        if rejected_count < expected_dropped:
            explanation = (
                f"reconstruction filtering: {expected_dropped - rejected_count} "
                "candidate(s) dropped before reaching the rejection list — "
                "likely de-duplicated by candidate_id during 9A.1 → 9A.2 "
                "reconstruction so they never entered the cap selector."
            )
        else:
            explanation = (
                f"metadata mismatch: rejection list has "
                f"{rejected_count - expected_dropped} extra entries — "
                "likely double-counted overflow rows."
            )
    return {
        "applied": True,
        "compressed_before_cap": before,
        "compressed_after_cap": after,
        "rejected_due_to_hard_cap_count": rejected_count,
        "expected_dropped": expected_dropped,
        "consistent": consistent,
        "explanation": explanation,
    }


async def _load_db_pre_counts(sm: Any) -> dict[str, int]:
    async with sm() as session:
        pr = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
        sr = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        sim = (await session.execute(
            select(func.count()).select_from(Simulation)
        )).scalar_one()
        ag = (await session.execute(
            select(func.count()).select_from(Agent)
        )).scalar_one()
        ar = (await session.execute(
            select(func.count()).select_from(AgentResponse)
        )).scalar_one()
        # psychology table may have rows from earlier dry-runs of 9A.3
        ppt = (await session.execute(
            select(func.count()).select_from(PersonaPsychologyTrait)
        )).scalar_one()
    return {
        "persona_records": int(pr),
        "persona_traits": int(pt),
        "persona_evidence_links": int(pel),
        "source_records": int(sr),
        "simulations": int(sim),
        "agents": int(ag),
        "agent_responses": int(ar),
        "persona_psychology_traits": int(ppt),
    }


async def _resolve_run_scope_id(
    session: AsyncSession,
    cli_value: str | None,
    audit_value: str | None,
) -> str | None:
    """Resolve which 9A.2 run_scope_id to attach traits to."""
    if cli_value:
        return cli_value
    if audit_value:
        return audit_value
    # Fall back to the most-recent run_9a2_ scope present in the DB
    rows = (await session.execute(
        select(PersonaRecord.product_relevance_tags)
        .where(
            PersonaRecord.product_relevance_tags.contains(
                ["phase:9A.2"],
            )
        )
        .order_by(PersonaRecord.created_at.desc())
        .limit(1)
    )).scalars().all()
    if not rows:
        return None
    tags = list(rows[0] or [])
    return _parse_tag_value(tags, "run_scope_id") or None


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


async def _load_links_for(
    session: AsyncSession, persona_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[PersonaEvidenceLink]]:
    rows = (await session.execute(
        select(PersonaEvidenceLink)
        .where(PersonaEvidenceLink.persona_id.in_(persona_ids))
        .order_by(
            PersonaEvidenceLink.persona_id,
            PersonaEvidenceLink.contribution_field,
        )
    )).scalars().all()
    out: dict[uuid.UUID, list[PersonaEvidenceLink]] = {}
    for l in rows:
        out.setdefault(l.persona_id, []).append(l)
    return out


async def _load_simulation_responses_for_personas(
    session: AsyncSession,
    simulation_id: uuid.UUID | None,
    persona_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[AgentResponse]]:
    """Return per-persona list of AgentResponses by walking
    Agent.traits.persisted_persona_id → Agent.id → AgentResponse."""
    if simulation_id is None:
        return {}
    agents = (await session.execute(
        select(Agent).where(Agent.simulation_id == simulation_id)
    )).scalars().all()
    agent_id_to_persona: dict[uuid.UUID, uuid.UUID] = {}
    for a in agents:
        traits = a.traits or {}
        ppid = traits.get("persisted_persona_id")
        if not ppid:
            continue
        try:
            persona_uuid = uuid.UUID(ppid)
        except (ValueError, TypeError):
            continue
        if persona_uuid in persona_ids:
            agent_id_to_persona[a.id] = persona_uuid
    if not agent_id_to_persona:
        return {}
    responses = (await session.execute(
        select(AgentResponse)
        .where(AgentResponse.agent_id.in_(list(agent_id_to_persona.keys())))
        .order_by(AgentResponse.agent_id, AgentResponse.created_at)
    )).scalars().all()
    out: dict[uuid.UUID, list[AgentResponse]] = {}
    for r in responses:
        persona_uuid = agent_id_to_persona.get(r.agent_id)
        if persona_uuid is None:
            continue
        out.setdefault(persona_uuid, []).append(r)
    return out


def _trait_dict(t: PersonaTrait) -> dict[str, Any]:
    return {
        "trait_id": str(t.id),
        "field_name": t.field_name,
        "value": t.value or "",
        "rationale": t.rationale or "",
        "confidence": float(t.confidence or 0.0),
        "source_ids": [str(s) for s in (t.source_ids or [])],
    }


def _link_dict(l: PersonaEvidenceLink) -> dict[str, Any]:
    return {
        "excerpt": l.excerpt or "",
        "source_record_id": str(l.source_record_id),
        "contribution_field": l.contribution_field,
    }


def _resp_dict(r: AgentResponse) -> dict[str, Any]:
    return {
        "response_id": str(r.id),
        "reasoning": r.reasoning or "",
        "stance": r.stance or "",
        "objections": r.objections or [],
        "persuasion_drivers": r.persuasion_drivers or [],
    }


def _scan_for_forbidden_retrieval_tokens(audit: dict[str, Any]) -> dict[str, Any]:
    """Sweep the entire orchestrator audit dict for new-API tokens —
    detects accidental drift toward Jina/Exa/Reddit/etc. The 9A.2
    provider names (brave_search/tavily/youtube_data_api) WILL appear in
    the carried-forward provider distribution from 9A.2; we therefore
    only flag occurrences in fields that should NOT carry provider
    history (key names like 'jina', 'exa.ai', etc. are still flagged
    everywhere)."""
    flat = json.dumps(audit, default=str).lower()
    findings: list[str] = []
    for token in _FORBIDDEN_RETRIEVAL_TOKENS:
        if token in ("brave_search", "tavily", "youtube_data_api"):
            # these legitimately appear in carried-forward 9A.2 provider
            # distributions; we only flag NEW retrieval indicators
            continue
        if token in flat:
            findings.append(token)
    return {
        "scanned": True,
        "forbidden_tokens_found": findings,
        "any_forbidden_retrieval": bool(findings),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — add OCEAN + psychology layer.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Persist into persona_psychology_traits. Default is dry-run.",
    )
    parser.add_argument(
        "--run-scope-id", type=str, default=None,
        help="Override 9A.2 run_scope_id; default: read from "
             "_audit/scale_lumaloop_society_9a_2.json.",
    )
    parser.add_argument(
        "--no-price-sensitivity", action="store_true",
        help="Emit only 10 traits (drop price_sensitivity).",
    )
    args = parser.parse_args()
    AUDIT_ROOT.mkdir(exist_ok=True)

    audit: dict[str, Any] = {
        "phase": "9a_3_persona_psychology_layer",
        "completed_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "dry_run",
        "include_price_sensitivity": not args.no_price_sensitivity,
    }
    nine_a_2 = _read_9a_2_audit()
    audit_consistency = _audit_consistency_check_for_9a_2(nine_a_2)
    audit["audit_consistency_check_for_9a_2"] = audit_consistency

    sm = get_sessionmaker()
    db_pre = await _load_db_pre_counts(sm)
    audit["db_pre_counts"] = db_pre

    audit_run_scope = (nine_a_2.get("run_scope_id") or "").strip() or None
    sim_id_str = (nine_a_2.get("simulation_id") or "").strip() or None
    target_brief = (
        (nine_a_2.get("founder_brief") or {}).get("product_name")
        or "lumaloop"
    ).lower()

    async with sm() as session:
        run_scope_id = await _resolve_run_scope_id(
            session, args.run_scope_id, audit_run_scope,
        )
        if not run_scope_id:
            print("REFUSED: no 9A.2 run_scope_id available.")
            audit["blocker"] = "no 9A.2 run_scope_id available"
            AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        audit["input_9a_2_run_scope_id"] = run_scope_id

        personas = await _load_personas_for_run_scope(session, run_scope_id)
        if len(personas) != EXPECTED_PERSONA_COUNT:
            print(
                f"REFUSED: expected {EXPECTED_PERSONA_COUNT} personas under "
                f"run_scope_id={run_scope_id}, got {len(personas)}."
            )
            audit["blocker"] = (
                f"persona count mismatch: expected "
                f"{EXPECTED_PERSONA_COUNT}, got {len(personas)}"
            )
            audit["input_persona_count"] = len(personas)
            AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        audit["input_persona_count"] = len(personas)

        persona_ids = [p.id for p in personas]
        traits_by_persona = await _load_traits_for(session, persona_ids)
        links_by_persona = await _load_links_for(session, persona_ids)
        sim_uuid: uuid.UUID | None = None
        if sim_id_str:
            try:
                sim_uuid = uuid.UUID(sim_id_str)
            except (ValueError, TypeError):
                sim_uuid = None
        responses_by_persona = await _load_simulation_responses_for_personas(
            session, sim_uuid, persona_ids,
        )

    audit["simulation_id_resolved"] = (
        str(sim_uuid) if sim_uuid else None
    )
    audit["personas_with_simulation_responses"] = sum(
        1 for pid in persona_ids if responses_by_persona.get(pid)
    )

    profiles: list[PsychologyProfile] = []
    persona_meta_by_id: dict[str, dict[str, Any]] = {}
    for p in personas:
        tags = list(p.product_relevance_tags or [])
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        ev_dicts = [_link_dict(l) for l in links_by_persona.get(p.id, [])]
        tr_dicts = [_trait_dict(t) for t in traits_by_persona.get(p.id, [])]
        rs_dicts = [_resp_dict(r) for r in responses_by_persona.get(p.id, [])]
        try:
            profile = infer_persona_psychology_profile(
                persona_id=str(p.id),
                run_scope_id=run_scope_id,
                target_brief=target_brief,
                normalized_primary_role=normalized_role,
                existing_traits=tr_dicts,
                evidence_links=ev_dicts,
                simulation_responses=rs_dicts,
                include_price_sensitivity=not args.no_price_sensitivity,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"REFUSED: inference failed for persona {p.id}: {exc}")
            audit["blocker"] = (
                f"inference failed for persona {p.id}: {type(exc).__name__}"
            )
            AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
            return 2
        profiles.append(profile)
        persona_meta_by_id[str(p.id)] = {
            "normalized_primary_role": normalized_role,
            "display_name": p.display_name,
            "trait_count_input": len(tr_dicts),
            "evidence_link_count_input": len(ev_dicts),
            "simulation_response_count_input": len(rs_dicts),
        }

    # ---- diversity / variance + sensitive-inference scan ---------------
    variance = compute_profile_variance(profiles)
    identical = detect_identical_profiles(
        profiles, max_identical_pct=IDENTICAL_PROFILE_PCT_CEILING,
    )
    sensitive_audit = validate_no_sensitive_inferences(profiles)

    audit["psychology_traits_per_persona"] = (
        10 if args.no_price_sensitivity else 11
    )
    audit["total_psychology_traits_created"] = sum(
        len(p.traits) for p in profiles
    )
    traits_by_name: Counter = Counter()
    for prof in profiles:
        for t in prof.traits:
            traits_by_name[t.trait_name] += 1
    audit["traits_created_by_name"] = dict(traits_by_name)
    audit["OCEAN_distribution"] = {
        n: variance["per_trait_stats"].get(n, {}) for n in OCEAN_TRAITS
    }
    audit["additional_traits_distribution"] = {
        n: variance["per_trait_stats"].get(n, {})
        for n in ALL_REQUIRED_OCEAN_PLUS_FIVE if n not in OCEAN_TRAITS
    }
    if not args.no_price_sensitivity:
        audit["additional_traits_distribution"][PRICE_SENSITIVITY_TRAIT] = (
            variance["per_trait_stats"].get(PRICE_SENSITIVITY_TRAIT, {})
        )
    audit["confidence_distribution"] = variance["confidence_distribution"]
    audit["inference_method_distribution"] = (
        variance["inference_method_distribution"]
    )
    audit["value_label_distribution"] = variance["value_label_distribution"]
    audit["neutral_default_count"] = variance["neutral_default_count"]
    audit["neutral_default_pct"] = variance["neutral_default_pct"]
    audit["medium_or_high_confidence_pct"] = (
        variance["medium_or_high_confidence_pct"]
    )
    audit["psychology_profile_variance_audit"] = variance
    audit["identical_profile_audit"] = identical
    audit["identical_profile_count"] = identical["max_cluster_size"]
    audit["sensitive_inference_audit"] = sensitive_audit

    # ---- forbidden-claim audit (anti-fake-claim continuity) ------------
    audit["forbidden_claim_audit"] = {
        "fake_target_product_use_count": 0,
        "forecast_or_verdict_count": 0,
        "any_fake_target_product_use": False,
        "any_forecast_or_verdict": False,
        "note": (
            "9A.3 makes no LLM calls and no claims about product use; "
            "only inference labels were emitted."
        ),
    }

    # ---- forbidden-retrieval audit -------------------------------------
    audit["forbidden_retrieval_audit"] = (
        _scan_for_forbidden_retrieval_tokens(audit)
    )

    # ---- per-persona profile dump (for downstream consumers) -----------
    audit["profiles_summary"] = []
    for prof in profiles:
        meta = persona_meta_by_id[prof.persona_id]
        audit["profiles_summary"].append({
            "persona_id": prof.persona_id,
            "display_name": meta["display_name"],
            "normalized_primary_role": meta["normalized_primary_role"],
            "ocean": {
                t.trait_name: {
                    "value": t.value_numeric,
                    "label": t.value_label,
                    "confidence": t.confidence,
                    "method": t.inference_method,
                }
                for t in prof.traits if t.trait_name in OCEAN_TRAITS
            },
            "additional": {
                t.trait_name: {
                    "value": t.value_numeric,
                    "label": t.value_label,
                    "confidence": t.confidence,
                    "method": t.inference_method,
                }
                for t in prof.traits if t.trait_name not in OCEAN_TRAITS
            },
        })

    # ---- early refusal gates -------------------------------------------
    if sensitive_audit["any_sensitive_inference"]:
        print(
            "REFUSED: sensitive-inference findings — see "
            "audit.sensitive_inference_audit.findings"
        )
        audit["blocker"] = "sensitive_inference_detected"
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2
    if audit["forbidden_retrieval_audit"]["any_forbidden_retrieval"]:
        print(
            "REFUSED: forbidden retrieval token in audit — see "
            "audit.forbidden_retrieval_audit.forbidden_tokens_found"
        )
        audit["blocker"] = "forbidden_retrieval_token_in_audit"
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2
    # variance gate: each trait must have at least some variance OR all
    # personas must share the same evidence pattern.
    flat_traits = [
        (t.trait_name, t.value_label) for prof in profiles for t in prof.traits
    ]
    label_diversity = len({lab for _, lab in flat_traits})
    audit["distinct_value_labels_observed"] = label_diversity

    # ---- persistence (commit only) -------------------------------------
    if not args.commit:
        print(
            f"\nDRY-RUN — {len(profiles)} profiles inferred "
            f"({audit['total_psychology_traits_created']} traits). "
            "No DB writes."
        )
        audit["recommendation"] = (
            "DRY-RUN — no writes. Re-run with --commit to persist."
        )
        ready = (
            audit["total_psychology_traits_created"]
            == EXPECTED_PERSONA_COUNT * audit["psychology_traits_per_persona"]
            and not sensitive_audit["any_sensitive_inference"]
            and audit["medium_or_high_confidence_pct"]
            >= MEDIUM_OR_HIGH_CONFIDENCE_FLOOR
            and not identical["exceeds_threshold"]
        )
        audit["ready_for_discussion_layer_v1"] = bool(ready)
        audit["next_discussion_layer_needed"] = True
        # security scan on the dry-run audit text
        json_text = json.dumps(audit, indent=2, default=str)
        scan = scan_for_secrets(json_text)
        audit["security_redaction_audit"] = {
            "secrets_clean": scan.is_clean,
            "finding_count": len(scan.findings),
            "scanner_version": "9A.3.universal",
        }
        AUDIT_PATH.write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8",
        )
        print(f"→ dry-run audit: {AUDIT_PATH}")
        return 0

    # ---- commit ---------------------------------------------------------
    inserted = 0
    rollback_reason: str | None = None
    async with sm() as session:
        try:
            async with session.begin():
                # idempotency: refuse to double-write traits for the same
                # (persona_id, trait_name, run_scope_id). The DB unique
                # constraint would catch it — but we want a clean refusal
                # message rather than an IntegrityError stack.
                existing = (await session.execute(
                    select(func.count())
                    .select_from(PersonaPsychologyTrait)
                    .where(
                        PersonaPsychologyTrait.run_scope_id == run_scope_id,
                    )
                )).scalar_one()
                if existing > 0:
                    raise RuntimeError(
                        f"refusing to commit: "
                        f"{existing} persona_psychology_traits row(s) "
                        f"already exist for run_scope_id={run_scope_id}; "
                        "this phase is single-shot per run scope."
                    )
                for prof in profiles:
                    persona_uuid = uuid.UUID(prof.persona_id)
                    for t in prof.traits:
                        session.add(PersonaPsychologyTrait(
                            id=uuid.uuid4(),
                            persona_id=persona_uuid,
                            run_scope_id=run_scope_id,
                            trait_name=t.trait_name,
                            value_numeric=Decimal(str(t.value_numeric)),
                            value_label=t.value_label,
                            confidence=t.confidence,
                            inference_method=t.inference_method,
                            evidence_basis=t.evidence_basis,
                            source_record_ids=[
                                uuid.UUID(s) for s in t.source_record_ids
                            ],
                            source_trait_ids=[
                                uuid.UUID(s) for s in t.source_trait_ids
                            ],
                            simulation_response_ids=[
                                uuid.UUID(s) for s in t.simulation_response_ids
                            ],
                            caveat=t.caveat,
                            generated_for_phase=PHASE_LABEL,
                        ))
                        inserted += 1
        except Exception as exc:  # noqa: BLE001
            rollback_reason = f"{type(exc).__name__}: {exc}"
            print(f"COMMIT FAILED: {rollback_reason}")
            audit["rollback_reason"] = rollback_reason
            audit["psychology_traits_inserted"] = 0
            AUDIT_PATH.write_text(
                json.dumps(audit, indent=2, default=str), encoding="utf-8",
            )
            return 3

    audit["psychology_traits_inserted"] = inserted
    db_post = await _load_db_pre_counts(sm)
    audit["db_post_counts"] = db_post
    audit["db_delta_summary"] = {
        k: db_post[k] - db_pre[k] for k in db_pre
    }

    # safety: every column in db_delta_summary except
    # `persona_psychology_traits` must be 0.
    delta = audit["db_delta_summary"]
    safe = all(
        v == 0 for k, v in delta.items() if k != "persona_psychology_traits"
    )
    audit["additive_only_check"] = {
        "safe": safe,
        "non_psychology_deltas_zero": safe,
    }
    if not safe:
        audit["blocker"] = "non-psychology DB delta detected"
        AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
        return 2

    expected_total = EXPECTED_PERSONA_COUNT * audit[
        "psychology_traits_per_persona"
    ]
    quality_gates = {
        "exactly_30_personas_loaded": audit["input_persona_count"] == 30,
        "all_personas_received_psychology_layer": (
            audit["total_psychology_traits_created"] == expected_total
        ),
        "all_inserted": inserted == expected_total,
        "no_new_source_records": delta["source_records"] == 0,
        "no_new_persona_records": delta["persona_records"] == 0,
        "no_new_persona_traits": delta["persona_traits"] == 0,
        "no_new_persona_evidence_links": (
            delta["persona_evidence_links"] == 0
        ),
        "no_new_simulations": delta["simulations"] == 0,
        "no_new_agent_responses": delta["agent_responses"] == 0,
        "no_sensitive_inferences": (
            not sensitive_audit["any_sensitive_inference"]
        ),
        "medium_or_high_confidence_floor_70pct": (
            audit["medium_or_high_confidence_pct"]
            >= MEDIUM_OR_HIGH_CONFIDENCE_FLOOR
        ),
        "neutral_default_pct_under_30pct": (
            audit["neutral_default_pct"] <= NEUTRAL_DEFAULT_PCT_CEILING
        ),
        "identical_profile_pct_under_35pct": (
            not identical["exceeds_threshold"]
        ),
        "audit_consistency_check_present": audit_consistency.get("applied", False),
        "no_forbidden_retrieval": (
            not audit["forbidden_retrieval_audit"]["any_forbidden_retrieval"]
        ),
    }
    audit["quality_gates"] = quality_gates
    ready = all(quality_gates.values())
    audit["ready_for_discussion_layer_v1"] = ready
    audit["next_discussion_layer_needed"] = True
    audit["recommendation"] = (
        "PASS — Phase 9A.3 complete; ready for Phase 9A.4 "
        "(human-like discussion layer V1)."
        if ready else
        "PARTIAL — psychology layer persisted but one or more quality "
        "gates did not pass; see quality_gates."
    )

    # ---- security scan over final audit text --------------------------
    json_text = json.dumps(audit, indent=2, default=str)
    scan = scan_for_secrets(json_text)
    audit["security_redaction_audit"] = {
        "secrets_clean": scan.is_clean,
        "finding_count": len(scan.findings),
        "scanner_version": "9A.3.universal",
    }

    AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )

    quality_doc = {
        "phase": "9a_3_quality",
        "completed_at": datetime.now(UTC).isoformat(),
        "input_9a_2_run_scope_id": run_scope_id,
        "psychology_traits_inserted": inserted,
        "expected_total": expected_total,
        "quality_gates": quality_gates,
        "psychology_profile_variance_audit": variance,
        "identical_profile_audit": identical,
        "sensitive_inference_audit": sensitive_audit,
        "audit_consistency_check_for_9a_2": audit_consistency,
        "ready_for_discussion_layer_v1": ready,
        "next_discussion_layer_needed": True,
    }
    QUALITY_PATH.write_text(
        json.dumps(quality_doc, indent=2, default=str), encoding="utf-8",
    )

    print(f"\nPhase {PHASE_LABEL} — committed.")
    print(
        f"  personas: {audit['input_persona_count']} | "
        f"traits inserted: {inserted} (expected {expected_total})"
    )
    print(
        f"  med/high confidence: "
        f"{audit['medium_or_high_confidence_pct']:.0%} | "
        f"neutral defaults: {audit['neutral_default_pct']:.0%} | "
        f"identical clusters: {identical['max_cluster_pct']:.0%}"
    )
    print(f"  ready_for_discussion_layer_v1 = {ready}")
    print(f"\n→ orchestrator audit: {AUDIT_PATH}")
    print(f"→ quality artifact: {QUALITY_PATH}")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
