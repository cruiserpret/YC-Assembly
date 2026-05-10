"""Phase 8.5D.1E — persona-set compression DRY RUN.

Reads the 8.5D.1D audit (and folds in the 8.5D.1C amazon planned
source records so every candidate's provider family resolves) and
runs the universal `compress_persona_set` planner over its 27
candidates. Produces a smaller, non-duplicative compressed set, an
updated diversity evaluation, and a clear `ready_for_mutating_phase`
decision.

NO LLM. NO Brave / YouTube calls. NO new evidence retrieval. NO DB
writes. NO simulation. NO frontend.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning, generate_anchor_plan,
)
from assembly.sources.persona_diversity_evaluator import (
    evaluate_persona_diversity,
)
from assembly.sources.persona_role_planner.schemas import (
    InferredPersonaTrait, PersonaCandidate,
)
from assembly.sources.persona_set_compressor import (
    compress_persona_set, normalize_role_slugs_for_candidates,
)


PHASE_LABEL = "8.5D.1E"
TARGET_BRIEF_ID = "strideshield"
LAUNCH_STATE = "unlaunched"
PRODUCT_NAME = "StrideShield"

STRIDESHIELD_BRIEF = ProductBriefForPlanning(
    product_name=PRODUCT_NAME,
    product_description=(
        "A pocket-sized anti-blister and anti-chafe balm for college "
        "students, runners, hikers, gym-goers, theme-park walkers, "
        "and people whose shoes or sandals rub during long days. It "
        "is sweat-resistant, fragrance-free, non-greasy, and designed "
        "to be applied to heels, toes, thighs, and other friction "
        "spots before walking, running, workouts, or outdoor activity."
    ),
    price_or_price_structure="$12.99",
    launch_geography="California, United States",
    target_customers=[
        "college students who walk a lot on campus", "runners",
        "hikers", "gym-goers", "theme-park visitors",
        "people who get shoe rub, sandal cuts, blisters, or thigh chafing",
        "people who dislike greasy lotions or messy powders",
    ],
    competitors=[
        "Body Glide", "Gold Bond Friction Defense",
        "Megababe Thigh Rescue", "Squirrel's Nut Butter",
        "Trail Toes",
    ],
    optional_constraints=[],
)


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


async def _read_baseline_counts(sessionmaker) -> dict[str, int]:
    async with sessionmaker() as session:
        sr = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        pr = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
    return {
        "source_records": int(sr), "persona_records": int(pr),
        "persona_traits": int(pt), "persona_evidence_links": int(pel),
    }


def _read_audit(name: str) -> dict[str, Any]:
    p = Path(__file__).resolve().parent.parent / "_audit" / name
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _amazon_planned_records_from_8_5d_1c(
    audit_1c: dict[str, Any],
) -> list[dict[str, Any]]:
    """The 8.5D.1C audit stores SELECTED ingestion decisions — extract
    their `planned_source_record_preview` rows.

    The 8.5D.1D persona-candidate planner uses the row's `source_url`
    as the candidate's `source_record_id` when no
    `planned_source_record_id_synthetic` is in metadata (which is the
    case for 8.5D.1C rows). We mirror that here so the compressor can
    resolve provider via the same key."""
    out: list[dict[str, Any]] = []
    for d in audit_1c.get("planned_source_records") or []:
        if d.get("decision") != "SELECTED":
            continue
        psr = d.get("planned_source_record_preview")
        if not psr:
            continue
        md = psr.get("metadata") or {}
        # Match the 8.5D.1D fallback: use source_url as the synthetic
        # ID when metadata didn't carry one.
        sid_used_by_1d = (
            md.get("planned_source_record_id_synthetic")
            or psr.get("source_url")
        )
        if not sid_used_by_1d:
            continue
        out.append({
            "planned_source_record_id_synthetic": sid_used_by_1d,
            "source_kind": "amazon_reviews_2023_local",
            "source_url": psr.get("source_url"),
            "content_preview": psr.get("content_preview"),
            "content_length": psr.get("content_length"),
            "content_hash": psr.get("content_hash"),
            "language": psr.get("language"),
            "metadata": {
                **md,
                "provider": "amazon_reviews_2023_local",
            },
            "ingested_by": psr.get("ingested_by", "dry_run"),
            "compliance_tag": psr.get("compliance_tag", "open_dataset"),
            "captured_at": psr.get("captured_at", ""),
            "pii_redaction_status": psr.get(
                "pii_redaction_status", "passed",
            ),
            "sensitive_scan_status": psr.get(
                "sensitive_scan_status", "passed",
            ),
            "user_handle_hash": psr.get("user_handle_hash"),
        })
    return out


def _candidate_to_persona_for_validation(
    cand_dict: dict[str, Any],
) -> PersonaCandidate:
    """Re-hydrate a JSON candidate dict back into a PersonaCandidate
    so the launch-state validator can run over it."""
    traits = [
        InferredPersonaTrait(**t)
        for t in cand_dict.get("inferred_traits") or []
    ]
    return PersonaCandidate(
        candidate_id=cand_dict["candidate_id"],
        target_brief=cand_dict["target_brief"],
        generated_for_phase=cand_dict.get(
            "generated_for_phase", PHASE_LABEL,
        ),
        inferred_persona_role=cand_dict.get("inferred_persona_role", ""),
        secondary_persona_roles=list(
            cand_dict.get("secondary_persona_roles") or []
        ),
        role_inference_basis=list(
            cand_dict.get("role_inference_basis") or []
        ),
        segment_label=cand_dict.get("segment_label") or "",
        source_record_ids=list(cand_dict.get("source_record_ids") or []),
        superseded_preview_source_record_ids=list(
            cand_dict.get("superseded_preview_source_record_ids") or []
        ),
        evidence_summary=cand_dict.get("evidence_summary") or "",
        evidence_snippets=list(cand_dict.get("evidence_snippets") or []),
        inferred_traits=traits,
        inferred_preferences=list(
            cand_dict.get("inferred_preferences") or []
        ),
        inferred_objections=list(
            cand_dict.get("inferred_objections") or []
        ),
        inferred_behaviors=list(
            cand_dict.get("inferred_behaviors") or []
        ),
        hypothetical_target_product_reaction=(
            cand_dict.get("hypothetical_target_product_reaction") or ""
        ),
        confidence=cand_dict.get("confidence", "medium"),
        evidence_strength=cand_dict.get("evidence_strength", "moderate"),
        caveats=list(cand_dict.get("caveats") or []),
        simulation_usefulness_summary=(
            cand_dict.get("simulation_usefulness_summary") or ""
        ),
        persistence_recommendation=cand_dict.get(
            "persistence_recommendation", "DEFER",
        ),
    )


def _evaluate_diversity_on_compressed(
    compressed_candidates: list,
) -> Any:
    """Re-run persona_diversity_evaluator on the compressed set.

    The compressor's `CompressedPersonaCandidate` shape carries enough
    fields to reconstruct a `PersonaCandidate` for the evaluator."""
    if not compressed_candidates:
        return evaluate_persona_diversity(
            brief=STRIDESHIELD_BRIEF, candidates=[],
        )
    rebuilt: list[PersonaCandidate] = []
    for c in compressed_candidates:
        traits = [
            InferredPersonaTrait(**t)
            for t in c.inferred_traits
        ]
        rebuilt.append(PersonaCandidate(
            candidate_id=c.candidate_id,
            target_brief=c.target_brief,
            generated_for_phase=c.generated_for_phase,
            inferred_persona_role=c.normalized_primary_role,
            secondary_persona_roles=list(c.secondary_persona_roles),
            role_inference_basis=list(c.role_inference_basis),
            segment_label=c.segment_label,
            source_record_ids=list(c.source_record_ids),
            evidence_summary=c.evidence_summary,
            evidence_snippets=list(c.evidence_snippets),
            inferred_traits=traits,
            inferred_preferences=list(c.inferred_preferences),
            inferred_objections=list(c.inferred_objections),
            inferred_behaviors=list(c.inferred_behaviors),
            hypothetical_target_product_reaction=(
                c.hypothetical_target_product_reaction
            ),
            confidence=c.confidence,
            evidence_strength=c.evidence_strength,
            caveats=list(c.caveats),
            simulation_usefulness_summary=c.simulation_usefulness_summary,
            persistence_recommendation=c.persistence_recommendation,
        ))
    return evaluate_persona_diversity(
        brief=STRIDESHIELD_BRIEF, candidates=rebuilt,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Phase {PHASE_LABEL} — persona-set compression DRY RUN."
        ),
    )
    parser.add_argument(
        "--input-audit",
        default="fresh_product_source_expansion_dry_run_8_5d_1d.json",
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / (
        "persona_set_compression_dry_run_8_5d_1e.json"
    )

    audit_1d_path = audit_root / args.input_audit
    audit_1c_path = audit_root / (
        "fresh_product_persona_diversity_fix_8_5d_1c.json"
    )

    audit_1d = _read_audit(args.input_audit)
    audit_1c = _read_audit(
        "fresh_product_persona_diversity_fix_8_5d_1c.json",
    )
    if not audit_1d:
        print(f"ERROR: 8.5D.1D audit not found at {audit_1d_path}")
        return 2

    sm = get_sessionmaker()
    db_pre = await _read_baseline_counts(sm)
    print(f"DB baseline pre-dry-run: {db_pre}")

    candidates = audit_1d.get("generated_persona_candidates") or []
    planned_external = audit_1d.get("planned_source_records") or []
    planned_amazon = _amazon_planned_records_from_8_5d_1c(audit_1c)
    planned_all = planned_external + planned_amazon
    input_role_dist = Counter(
        c.get("inferred_persona_role") or "" for c in candidates
    )
    input_provider_dist = audit_1d.get(
        "source_provider_distribution", {},
    )

    print(
        f"Input: {len(candidates)} candidates, "
        f"{len(planned_external)} external + "
        f"{len(planned_amazon)} amazon = {len(planned_all)} sources"
    )

    # Role-slug normalization (audit-only — informs but does not
    # mutate the input candidates)
    role_map, normalization_rows = normalize_role_slugs_for_candidates(
        candidates,
    )
    print(
        f"Role-slug normalization: {len(normalization_rows)} role(s) "
        f"changed."
    )

    # Compression
    compressed = compress_persona_set(
        candidates=candidates,
        planned_source_records=planned_all,
        target_brief_id=TARGET_BRIEF_ID,
        product_name=PRODUCT_NAME,
        launch_state=LAUNCH_STATE,
        generated_for_phase=PHASE_LABEL,
        max_target_range=(6, 8),
        min_behavioral_differential=2,
    )
    print(
        f"Compression: {compressed.diff_summary.before_count} → "
        f"{compressed.diff_summary.after_count} "
        f"({compressed.diff_summary.rejected_count} rejected)"
    )

    # Re-evaluate diversity on compressed set
    diversity_after = _evaluate_diversity_on_compressed(
        compressed.compressed_candidates,
    )
    print(
        f"Diversity after: score={diversity_after.diversity_score}, "
        f"unique_roles={len(diversity_after.unique_primary_roles)}, "
        f"recommendation="
        f"{diversity_after.mutating_persistence_recommendation}"
    )

    # Diversity-before snapshot from 8.5D.1D audit
    diversity_before = audit_1d.get(
        "persona_diversity_evaluation", {},
    )

    # Universal launch-state validator over the compressed set.
    # We re-use the existing persona_role_planner validator.
    from assembly.sources.persona_role_planner import (
        validate_launch_state_claims,
    )
    launch_state_validation_after: list[dict[str, Any]] = []
    fake_use_in_compressed = False
    for c in compressed.compressed_candidates:
        as_pers = _candidate_to_persona_for_validation({
            "candidate_id": c.candidate_id,
            "target_brief": c.target_brief,
            "generated_for_phase": c.generated_for_phase,
            "inferred_persona_role": c.normalized_primary_role,
            "secondary_persona_roles": list(c.secondary_persona_roles),
            "role_inference_basis": list(c.role_inference_basis),
            "segment_label": c.segment_label,
            "source_record_ids": list(c.source_record_ids),
            "evidence_summary": c.evidence_summary,
            "evidence_snippets": list(c.evidence_snippets),
            "inferred_traits": c.inferred_traits,
            "inferred_preferences": list(c.inferred_preferences),
            "inferred_objections": list(c.inferred_objections),
            "inferred_behaviors": list(c.inferred_behaviors),
            "hypothetical_target_product_reaction": (
                c.hypothetical_target_product_reaction
            ),
            "confidence": c.confidence,
            "evidence_strength": c.evidence_strength,
            "caveats": list(c.caveats),
            "simulation_usefulness_summary": c.simulation_usefulness_summary,
            "persistence_recommendation": c.persistence_recommendation,
        })
        v = validate_launch_state_claims(
            candidate=as_pers, launch_state=LAUNCH_STATE,
            product_name=PRODUCT_NAME,
        )
        launch_state_validation_after.append(json.loads(v.model_dump_json()))
        if not v.is_valid:
            fake_use_in_compressed = True

    # Provider families in the compressed set (recomputed for the
    # gate calculation, since `unknown` is treated as one family for
    # the diff_summary but should be excluded from the multi-provider
    # gate to be honest).
    real_providers = sorted({
        c.source_provider_family
        for c in compressed.compressed_candidates
        if c.source_provider_family
        not in ("unknown", "")
    })
    multi_provider = len(real_providers) >= 2

    # DB post-check
    db_post = await _read_baseline_counts(sm)
    db_unchanged = db_pre == db_post

    # ready_for_mutating_phase
    every_has_traits = all(
        len(c.inferred_traits) >= 2
        for c in compressed.compressed_candidates
    )
    every_has_evidence = all(
        len(c.source_record_ids) >= 1
        and len(c.evidence_snippets) >= 1
        for c in compressed.compressed_candidates
    )
    every_brief_scoped = all(
        c.scope == "brief_scoped"
        and c.persistence_status == "dry_run_only"
        and c.not_global_persona is True
        for c in compressed.compressed_candidates
    )
    diversity_ready = (
        diversity_after.mutating_persistence_recommendation == "READY"
    )
    candidate_count_in_target = (
        compressed.diff_summary.after_count >= 1
    )

    ready_for_mutating = (
        db_unchanged
        and not fake_use_in_compressed
        and every_brief_scoped
        and every_has_evidence
        and every_has_traits
        and candidate_count_in_target
        and diversity_ready
        and multi_provider
    )

    blockers: list[str] = []
    if not db_unchanged:
        blockers.append("db_changed")
    if fake_use_in_compressed:
        blockers.append("fake_target_product_use_present")
    if not every_brief_scoped:
        blockers.append("non_brief_scoped_or_global_personas_present")
    if not every_has_evidence:
        blockers.append("missing_evidence_in_a_candidate")
    if not every_has_traits:
        blockers.append("below_min_traits_in_a_candidate")
    if not candidate_count_in_target:
        blockers.append("zero_compressed_candidates")
    if not diversity_ready:
        blockers.append(
            f"diversity_not_ready: "
            f"{diversity_after.mutating_persistence_recommendation}"
        )
    if not multi_provider:
        blockers.append(
            f"multi_provider_gate_unmet: "
            f"providers={real_providers}"
        )

    # Evidence → compressed-persona mapping
    evidence_to_persona_mapping = []
    for c in compressed.compressed_candidates:
        for sid in c.source_record_ids:
            evidence_to_persona_mapping.append({
                "planned_source_record_id_synthetic": sid,
                "candidate_id": c.candidate_id,
                "normalized_primary_role": c.normalized_primary_role,
                "source_provider_family": c.source_provider_family,
            })

    summary: dict[str, Any] = {
        "phase": "8_5d_1e_persona_set_compression_dry_run",
        "completed_at": datetime.now(UTC).isoformat(),
        "dry_run": True,
        "db_writes": False,
        "founder_brief": json.loads(STRIDESHIELD_BRIEF.model_dump_json()),
        "launch_state": LAUNCH_STATE,
        "input_audit_path": str(audit_1d_path),
        "input_candidate_count": len(candidates),
        "input_role_distribution": dict(input_role_dist),
        "input_provider_distribution": input_provider_dist,
        "role_slug_normalization": {
            "normalized_role_map": role_map,
            "changed_roles": [
                json.loads(r.model_dump_json())
                for r in normalization_rows
            ],
            "affected_candidate_ids": sorted({
                cid for r in normalization_rows
                for cid in r.affected_candidate_ids
            }),
        },
        "compression_policy": json.loads(compressed.policy.model_dump_json()),
        "compressed_persona_candidates": [
            json.loads(c.model_dump_json())
            for c in compressed.compressed_candidates
        ],
        "rejected_persona_candidates": [
            json.loads(r.model_dump_json())
            for r in compressed.rejected_candidates
        ],
        "compression_summary": json.loads(
            compressed.diff_summary.model_dump_json(),
        ),
        "diversity_before": diversity_before,
        "diversity_after": json.loads(
            diversity_after.model_dump_json(),
        ),
        "launch_state_claim_validation_after": launch_state_validation_after,
        "evidence_to_compressed_persona_mapping": evidence_to_persona_mapping,
        "db_pre_dry_run_counts": db_pre,
        "db_post_dry_run_counts": db_post,
        "db_unchanged_during_dry_run": db_unchanged,
        "ready_for_mutating_phase": ready_for_mutating,
        "ready_blockers": blockers,
        "multi_provider_gate": {
            "real_provider_families_in_compressed_set": real_providers,
            "multi_provider_gate_passed": multi_provider,
        },
        "caveats": list(compressed.caveats) + [
            "Phase 8.5D.1E does NOT call Brave / YouTube. It works only "
            "with existing audit artifacts from 8.5D.1C and 8.5D.1D.",
            "Compression is deterministic and brief-agnostic. Operator "
            "did not name which personas to keep — Assembly chose them "
            "via universal quality + diversity rules.",
        ],
        "recommendation": (
            f"PASS — compressed {len(candidates)} candidates → "
            f"{len(compressed.compressed_candidates)}; diversity_after: "
            f"{diversity_after.mutating_persistence_recommendation}; "
            f"ready_for_mutating_phase: {ready_for_mutating}."
        ),
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print(f"Phase {PHASE_LABEL} — Persona-set compression DRY RUN")
    print("=" * 72)
    print(f"product: {PRODUCT_NAME}")
    print(f"DB unchanged: {db_unchanged}")
    print(
        f"input → output: "
        f"{compressed.diff_summary.before_count} → "
        f"{compressed.diff_summary.after_count} "
        f"({compressed.diff_summary.rejected_count} rejected)"
    )
    print(
        f"unique roles: "
        f"{len(compressed.diff_summary.roles_before)} → "
        f"{len(compressed.diff_summary.roles_after)}"
    )
    print(
        f"duplicate-role clusters: "
        f"{compressed.diff_summary.duplicate_role_clusters_before} → "
        f"{compressed.diff_summary.duplicate_role_clusters_after}"
    )
    print(
        f"competitor concentration: "
        f"{compressed.diff_summary.competitor_concentration_before} → "
        f"{compressed.diff_summary.competitor_concentration_after}"
    )
    print(
        f"diversity score (compressor calibrated): "
        f"{compressed.diff_summary.diversity_score_before} → "
        f"{compressed.diff_summary.diversity_score_after}"
    )
    print(
        f"persona-diversity-evaluator on compressed: "
        f"{diversity_after.mutating_persistence_recommendation} "
        f"(score={diversity_after.diversity_score})"
    )
    print(f"providers in compressed set: {real_providers}")
    print(f"ready_for_mutating_phase: {ready_for_mutating}")
    if blockers:
        print(f"ready_blockers: {blockers}")
    print()
    for c in compressed.compressed_candidates:
        print(
            f"  q={c.quality_score:5.2f}  "
            f"role={c.normalized_primary_role:48s}  "
            f"theme={c.evidence_theme[:40]:40s}  "
            f"provider={c.source_provider_family}"
        )
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
