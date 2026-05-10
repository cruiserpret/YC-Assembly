"""Phase 9A.2 — compressor hard-cap probe.

Reads the 66 personas persisted by Phase 9A.1, reconstructs
`CompressedPersonaCandidate`-shaped dicts in memory from the
`PersonaRecord` + `PersonaTrait` + `PersonaEvidenceLink` rows, then
runs the new `_apply_hard_cap_stratified` selector with
`hard_max=30`. Emits a probe audit so the operator can see exactly
which 30 would be persisted by the full Phase 9A.2 orchestrator.

NO DB writes from this probe. NO LLM calls. NO live retrieval.
Purely a deterministic projection of the 9A.1 → 9A.2 cap step.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.sources.persona_set_compressor.compressor import (
    _apply_hard_cap_stratified,
)
from assembly.sources.persona_set_compressor.schemas import (
    CompressedPersonaCandidate,
)


PHASE_LABEL = "9A.2-probe"


def _parse_tag_value(
    tags: list[str], key: str, default: str = "",
) -> str:
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


async def _load_9a_1_personas_as_candidates(
    session: AsyncSession,
) -> list[CompressedPersonaCandidate]:
    """Load 9A.1 persona records + their traits + evidence links and
    rebuild `CompressedPersonaCandidate` shapes for the hard-cap
    selector. Pure read."""
    persona_rows = (await session.execute(
        select(PersonaRecord).where(
            PersonaRecord.product_relevance_tags.contains(
                ["phase:9A.1"],
            )
        ).order_by(PersonaRecord.id)
    )).scalars().all()
    if not persona_rows:
        return []
    persona_ids = [p.id for p in persona_rows]
    trait_rows = (await session.execute(
        select(PersonaTrait)
        .where(PersonaTrait.persona_id.in_(persona_ids))
        .order_by(PersonaTrait.persona_id, PersonaTrait.field_name)
    )).scalars().all()
    link_rows = (await session.execute(
        select(PersonaEvidenceLink)
        .where(PersonaEvidenceLink.persona_id.in_(persona_ids))
        .order_by(
            PersonaEvidenceLink.persona_id,
            PersonaEvidenceLink.contribution_field,
        )
    )).scalars().all()
    traits_by_persona: dict[Any, list[Any]] = {}
    for t in trait_rows:
        traits_by_persona.setdefault(t.persona_id, []).append(t)
    links_by_persona: dict[Any, list[Any]] = {}
    for l in link_rows:
        links_by_persona.setdefault(l.persona_id, []).append(l)
    out: list[CompressedPersonaCandidate] = []
    for p in persona_rows:
        tags = list(p.product_relevance_tags or [])
        target_brief = _parse_tag_value(tags, "target_brief", "lumaloop")
        normalized_role = _parse_tag_value(
            tags, "normalized_primary_role",
        ) or (p.segment_label or "unknown")
        evidence_theme = _parse_tag_value(
            tags, "evidence_theme",
        ) or f"role::{normalized_role}"
        provider = _parse_tag_value(
            tags, "source_provider_family",
        ) or "unknown"
        compressed_candidate_id = _parse_tag_value(
            tags, "compressed_candidate_id",
        )
        # Build the trait dicts in InferredPersonaTrait shape so the
        # CompressedPersonaCandidate's min_length=2 invariant is met.
        traits = traits_by_persona.get(p.id, [])
        if len(traits) < 2:
            # Pad with a synthetic role_or_context fallback so the
            # schema accepts the rebuilt candidate. (This mirrors
            # the universal fallback we use in 9A persistence.)
            pass
        trait_dicts: list[dict[str, Any]] = []
        for t in traits[:7]:
            trait_dicts.append({
                "trait_name": t.field_name,
                "trait_value": t.value or normalized_role,
                "evidence_source_record_id": (
                    str(t.source_ids[0]) if t.source_ids else "unknown"
                ),
                "evidence_excerpt": (t.rationale or "")[:240] or "evidence",
                "confidence": (
                    "high" if float(t.confidence) >= 0.8
                    else "medium" if float(t.confidence) >= 0.5
                    else "low"
                ),
                "caveat": None,
            })
        # Ensure ≥2 trait dicts for schema; synthesize if needed
        if len(trait_dicts) < 2:
            trait_dicts.extend([
                {
                    "trait_name": "role_or_context",
                    "trait_value": normalized_role,
                    "evidence_source_record_id": "synthetic",
                    "evidence_excerpt": (
                        f"persona_role::{normalized_role} "
                        "(rebuilt from 9A.1 PersonaRecord)"
                    ),
                    "confidence": "medium",
                    "caveat": None,
                }
            ] * max(0, 2 - len(trait_dicts)))
        # Evidence snippets from links
        snippets: list[str] = []
        for l in links_by_persona.get(p.id, [])[:5]:
            ex = (l.excerpt or "")[:300]
            if ex:
                snippets.append(ex)
        if not snippets:
            snippets = [
                f"persona_role::{normalized_role} "
                "(rebuilt from 9A.1)"
            ]
        # Source record IDs from links
        src_ids = sorted({
            str(l.source_record_id)
            for l in links_by_persona.get(p.id, [])
        })
        if not src_ids:
            src_ids = ["unknown"]
        # Quality score: re-derive from population_weight + trait
        # confidence avg, since 9A.1 didn't persist the original.
        avg_conf = sum(
            float(t.confidence) for t in traits
        ) / max(len(traits), 1)
        qs = round(7.0 + 3.0 * avg_conf, 3)
        out.append(CompressedPersonaCandidate(
            candidate_id=(
                compressed_candidate_id or f"9a1::{str(p.id)[:8]}"
            ),
            target_brief=target_brief,
            generated_for_phase="9A.1",
            pre_normalization_role=normalized_role,
            normalized_primary_role=normalized_role,
            secondary_persona_roles=[],
            role_inference_basis=[
                "rebuilt from 9A.1 PersonaRecord product_relevance_tags",
            ],
            segment_label=p.segment_label or normalized_role,
            source_record_ids=src_ids,
            evidence_summary=(
                f"Rebuilt from 9A.1 persona {str(p.id)[:8]} "
                f"({len(traits)} traits, "
                f"{len(links_by_persona.get(p.id, []))} links)."
            ),
            evidence_snippets=snippets,
            evidence_theme=evidence_theme,
            source_provider_family=provider,
            inferred_traits=trait_dicts,
            inferred_preferences=[],
            inferred_objections=[],
            inferred_behaviors=[],
            hypothetical_target_product_reaction=(
                f"This persona would compare LumaLoop to its "
                f"{normalized_role.replace('_', ' ')} context."
            ),
            confidence="high",
            evidence_strength="strong",
            quality_score=qs,
            caveats=[
                "rebuilt from 9A.1 PersonaRecord; original 9A.1 "
                "compressed-candidate quality_score not preserved"
            ],
            simulation_usefulness_summary=(
                f"9A.1 → 9A.2 hard-cap reconstruction for role "
                f"{normalized_role}."
            ),
            persistence_recommendation="DEFER",
            kept_reason=(
                f"persisted by 9A.1; rebuilt for 9A.2 hard-cap probe."
            ),
        ))
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — hard-cap probe.",
    )
    parser.add_argument(
        "--hard-max", type=int, default=30,
        help="Hard cap for compressed personas (default 30).",
    )
    args = parser.parse_args()
    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "compressor_hard_cap_9a_2.json"

    sm = get_sessionmaker()
    async with sm() as session:
        candidates = await _load_9a_1_personas_as_candidates(session)
    if not candidates:
        print(
            "REFUSED: no Phase 9A.1 personas found in DB "
            "(filter: phase:9A.1 in product_relevance_tags)."
        )
        out_path.write_text(json.dumps({
            "phase": "9a_2_compressor_hard_cap_probe",
            "completed_at": datetime.now(UTC).isoformat(),
            "input_9a_1_personas": 0,
            "blocker": "no Phase 9A.1 personas found in DB",
        }, indent=2), encoding="utf-8")
        return 2

    print(f"Loaded {len(candidates)} Phase 9A.1 personas from DB.")

    role_dist_before = Counter(
        c.normalized_primary_role for c in candidates
    )
    provider_dist_before = Counter(
        c.source_provider_family for c in candidates
    )
    theme_dist_before = Counter(
        c.evidence_theme for c in candidates
    )

    kept, dropped, hard_cap_audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=args.hard_max,
    )

    role_dist_after = Counter(
        c.normalized_primary_role for c in kept
    )
    provider_dist_after = Counter(
        c.source_provider_family for c in kept
    )
    theme_dist_after = Counter(
        c.evidence_theme for c in kept
    )

    audit = {
        "phase": "9a_2_compressor_hard_cap_probe",
        "completed_at": datetime.now(UTC).isoformat(),
        "hard_max_compressed": args.hard_max,
        "input_9a_1_personas": len(candidates),
        "compressed_before_cap": len(candidates),
        "compressed_after_cap": len(kept),
        "rejected_due_to_hard_cap": [
            {
                "candidate_id": d.candidate_id,
                "normalized_primary_role": d.normalized_primary_role,
                "evidence_theme": d.evidence_theme,
                "source_provider_family": d.source_provider_family,
                "quality_score": d.quality_score,
            }
            for d in dropped
        ],
        "stratified_selection_policy": hard_cap_audit,
        "role_distribution_before_cap": dict(role_dist_before),
        "role_distribution_after_cap": dict(role_dist_after),
        "provider_distribution_before_cap": dict(provider_dist_before),
        "provider_distribution_after_cap": dict(provider_dist_after),
        "theme_distribution_before_cap": dict(theme_dist_before),
        "theme_distribution_after_cap": dict(theme_dist_after),
        "role_concentration_top_role": (
            f"{role_dist_after.most_common(1)[0][0]} "
            f"({role_dist_after.most_common(1)[0][1]}/{len(kept)} = "
            f"{role_dist_after.most_common(1)[0][1] / max(len(kept), 1):.0%})"
            if role_dist_after else None
        ),
        "distinct_role_count_after_cap": len(role_dist_after),
        "kept_candidate_ids": [c.candidate_id for c in kept],
        "dropped_candidate_ids": [d.candidate_id for d in dropped],
        "next_psychology_layer_needed": True,
        "next_discussion_layer_needed": True,
        "recommendation": (
            "Probe-only — no DB writes. Run "
            "scripts/scale_lumaloop_society_9a_2.py --commit to "
            "persist the capped 30 under a new 9A.2 run_scope_id "
            "+ run simulation + generate report."
        ),
    }
    out_path.write_text(
        json.dumps(audit, indent=2, default=str),
        encoding="utf-8",
    )
    print("\n" + "=" * 72)
    print(f"Phase {PHASE_LABEL} — Hard-cap probe")
    print("=" * 72)
    print(
        f"input={len(candidates)} → kept={len(kept)} "
        f"(dropped {len(dropped)})"
    )
    print(f"distinct roles after cap: {len(role_dist_after)}")
    top_role, top_count = role_dist_after.most_common(1)[0]
    print(
        f"top role: {top_role} = {top_count}/{len(kept)} "
        f"({top_count / max(len(kept), 1):.0%})"
    )
    print(f"providers represented: {sorted(provider_dist_after.keys())}")
    print(f"\n→ probe audit: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
