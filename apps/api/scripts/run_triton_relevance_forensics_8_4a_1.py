"""Phase 8.4A.1 — Triton relevance forensics.

READ-ONLY. No DB writes (except a single audit JSON file). No LLM
calls. No live Tavily / Firecrawl. No persona / trait / evidence-link
writes. No graph / simulation / UI writes.

Goal: diagnose why 44 source-grounded Triton personas were all
excluded by the relevance audit (top score 18, threshold unchanged
at 27, all 10 categories show coverage=missing).

Outputs (saved to _audit/triton_relevance_forensics_8_4a_1.json):
  * per-persona × per-category full score breakdown (8 axes)
  * per-persona best-fit category + best score
  * per-persona heuristic classification:
      - true_relevant_market_entry_persona
      - weak_market_entry_persona
      - off_topic_persona
      - insufficient_evidence_persona
  * per-axis averages across all 44 personas
  * per-category coverage under current scorer
  * per-category coverage under HUMAN market-entry interpretation
    (richer keyword set covering competitors / substitutes /
    use-occasions / objections — read-only, no scorer change)
  * source-domain breakdown (which domains produced strong
    personas, which produced weak)
  * Tavily-only-snippet vs Firecrawl-bodied persona share
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


TARGET_BRIEF_TAG = "triton_drinks"


# ---------------------------------------------------------------------------
# Human market-entry keyword sets (READ-ONLY heuristic; the scorer is
# NOT modified). These reflect the user's market-entry-relevance
# definition: relevant if evidence-bound to competitor use, substitute
# use, category objections, occasion behavior, or category price/taste/
# health/trust concerns. The scorer's existing keyword sets are
# narrower (per-category specific signals); these are deliberately
# broader to surface the "would a human market researcher consider
# this evidence relevant for an energy-drink launch?" question.
# ---------------------------------------------------------------------------


_HUMAN_MARKET_ENTRY_PATTERNS: dict[str, re.Pattern[str]] = {
    "red_bull_use": re.compile(
        r"\b(?:red\s*bull|RedBull)\b", re.IGNORECASE,
    ),
    "monster_use": re.compile(r"\bmonster\s*(?:energy)?\b", re.IGNORECASE),
    "celsius_use": re.compile(r"\bcelsius\b", re.IGNORECASE),
    "prime_use": re.compile(r"\bprime\s*(?:energy|drink|hydration)?\b", re.IGNORECASE),
    "gatorade_use": re.compile(r"\bgatorade\b", re.IGNORECASE),
    "rockstar_other_energy": re.compile(
        r"\b(?:rockstar|bang|reign|alani|c4|nos|amp|venom|raze)\b",
        re.IGNORECASE,
    ),
    "energy_drink_general": re.compile(
        r"\benergy\s*drink(?:s)?\b", re.IGNORECASE,
    ),
    "preworkout_use": re.compile(
        r"\bpre[-\s]?workout\b", re.IGNORECASE,
    ),
    "caffeine_for_study": re.compile(
        r"\b(?:caffeine|coffee|cold\s*brew).{0,40}\b"
        r"(?:study|studying|finals|exam|all-?nighter|focus)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "college_or_student": re.compile(
        r"\b(?:college|university|student|grad\s*school|dorm)\b",
        re.IGNORECASE,
    ),
    "gym_workout_athlete": re.compile(
        r"\b(?:gym|workout|training|athlete|sport(?:s)?|fitness|"
        r"performance)\b",
        re.IGNORECASE,
    ),
    "sugar_caffeine_crash": re.compile(
        r"\b(?:sugar\s*crash|caffeine\s*crash|jittery|jitters|"
        r"crash\s*later|sugar\s*high)\b",
        re.IGNORECASE,
    ),
    "health_skepticism": re.compile(
        r"\b(?:too\s*much\s*caffeine|unhealthy|bad\s*for\s*you|"
        r"sugar(?:\s*free)?|low\s*sugar|zero\s*sugar|natural|"
        r"clean\s*ingredient)\b",
        re.IGNORECASE,
    ),
    "taste_complaint": re.compile(
        r"\b(?:taste|flavor|gross|disgusting|bitter|sweet|sickly|"
        r"chemical|aftertaste)\b",
        re.IGNORECASE,
    ),
    "price_sensitivity": re.compile(
        r"\b(?:expensive|cost\s*too\s*much|too\s*pricey|"
        r"\$\s*\d+(?:\.\d+)?|cheap(?:er)?|afford(?:able)?|"
        r"budget|broke|on\s*a\s*budget)\b",
        re.IGNORECASE,
    ),
    "convenience_store_impulse": re.compile(
        r"\b(?:convenience\s*store|gas\s*station|7-?eleven|"
        r"7\s*eleven|circle\s*k|impulse|grab(?:bed)?)\b",
        re.IGNORECASE,
    ),
    "brand_loyalty_or_skepticism": re.compile(
        r"\b(?:loyal\s*to|stick\s*with|new\s*brand|never\s*tried|"
        r"would\s*never|skeptical|hype|overrated|gimmick)\b",
        re.IGNORECASE,
    ),
    "rejector": re.compile(
        r"\b(?:don'?t\s*drink|stopped\s*drinking|never\s*drink|"
        r"won'?t\s*touch|gave\s*up|quit)\b",
        re.IGNORECASE,
    ),
}


_MARKET_ENTRY_CATEGORIES_HUMAN = (
    ("red_bull_loyalist_or_user", ("red_bull_use",)),
    ("monster_loyalist_or_user", ("monster_use",)),
    ("celsius_or_functional_buyer", ("celsius_use",)),
    ("prime_or_sports_drink_buyer", ("prime_use", "gatorade_use")),
    ("college_caffeine_or_study_user", (
        "caffeine_for_study", "college_or_student",
    )),
    ("gym_or_preworkout_user", ("gym_workout_athlete", "preworkout_use")),
    ("athlete_performance_drink_buyer", (
        "gym_workout_athlete", "preworkout_use",
    )),
    ("health_conscious_skeptic", ("health_skepticism",)),
    ("sugar_or_crash_concerned_user", ("sugar_caffeine_crash",)),
    ("price_sensitive_convenience_buyer", (
        "price_sensitivity", "convenience_store_impulse",
    )),
    ("taste_first_buyer", ("taste_complaint",)),
    ("brand_or_status_buyer", ("brand_loyalty_or_skepticism",)),
    ("new_brand_skeptic", ("brand_loyalty_or_skepticism",)),
    ("energy_drink_rejector", ("rejector",)),
)


async def _amain() -> int:
    _load_env()
    from sqlalchemy import select

    from assembly.db import get_sessionmaker
    from assembly.models.persona import (
        PersonaEvidenceLink,
        PersonaRecord,
        PersonaTrait,
        SourceRecord,
    )
    from assembly.pipeline.audience_retrieval.scorer import (
        score_persona_against_category,
    )
    from assembly.pipeline.persona_relevance.auditor import (
        EvidenceLinkView,
        PersonaAuditInput,
        TraitView,
    )
    from assembly.pipeline.target_society import build_target_society_plan
    from assembly.pipeline.target_society.constants import SimulationGoal
    from assembly.pipeline.target_society.schemas import ProductBriefInput

    sm = get_sessionmaker()
    print("=" * 70)
    print("Phase 8.4A.1 — Triton Relevance Forensics (READ-ONLY)")
    print("=" * 70)

    # ---- Build the same Triton brief + plan ---------------------------
    triton_brief = ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is a new caffeinated sports/energy drink "
            "launching in California at $3.99 per can. Targeted at college "
            "students, athletes, gym-goers, and busy young adults. "
            "Competes with Red Bull and Monster; substitutes include "
            "Celsius, Prime, Gatorade, pre-workout drinks, cold brew, "
            "electrolyte drinks. Triton is unlaunched; relevance means "
            "evidence-backed buyers / rejectors / influencers in the "
            "category, not Triton-specific buyers."
        ),
        price_or_price_structure="$3.99 per can (single-serve)",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        target_market_or_society=(
            "California consumers in the energy / sports / functional-"
            "beverage occasion."
        ),
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        simulation_goal=SimulationGoal.TEST_PRICE,
    )
    plan = build_target_society_plan(triton_brief)
    plan_categories = list(plan.stakeholder_categories)
    geography_required = plan.coverage_requirements.geography_coverage_required

    # ---- Load 44 Triton personas via Triton-tagged source_records ----
    async with sm() as session:
        triton_sr = (await session.execute(
            select(SourceRecord)
            .where(
                SourceRecord.metadata_["target_brief"].astext
                == TARGET_BRIEF_TAG
            )
        )).scalars().all()
        triton_sr_ids = {r.id for r in triton_sr}
        sr_by_id = {r.id: r for r in triton_sr}

        links = (await session.execute(
            select(PersonaEvidenceLink)
            .where(
                PersonaEvidenceLink.source_record_id.in_(triton_sr_ids)
            )
        )).scalars().all() if triton_sr_ids else []
        triton_pids: set[UUID] = {el.persona_id for el in links}

        triton_persona_rows = (await session.execute(
            select(PersonaRecord).where(PersonaRecord.id.in_(triton_pids))
        )).scalars().all() if triton_pids else []

        all_triton_traits = (await session.execute(
            select(PersonaTrait).where(PersonaTrait.persona_id.in_(triton_pids))
        )).scalars().all() if triton_pids else []

        all_triton_links = (await session.execute(
            select(PersonaEvidenceLink).where(
                PersonaEvidenceLink.persona_id.in_(triton_pids)
            )
        )).scalars().all() if triton_pids else []

    print(f"Triton-tagged source_records:    {len(triton_sr)}")
    print(f"Triton evidence-link rows:       {len(links)}")
    print(f"Triton-pids (distinct personas): {len(triton_pids)}")
    print(f"Triton persona records loaded:   {len(triton_persona_rows)}")
    print(f"Triton trait rows loaded:        {len(all_triton_traits)}")
    print(f"Triton evidence-link rows total: {len(all_triton_links)}")

    # ---- Sanity check: confirm none reference Amboras source_records --
    async with sm() as session:
        # Cross-check: are any of these personas linked to Amboras
        # (target_brief='amboras') sources too? That would indicate
        # cross-pollination.
        all_links_for_pids = (await session.execute(
            select(PersonaEvidenceLink)
            .where(PersonaEvidenceLink.persona_id.in_(triton_pids))
        )).scalars().all() if triton_pids else []
        cross_source_ids = {el.source_record_id for el in all_links_for_pids}
        cross_sr = (await session.execute(
            select(SourceRecord).where(SourceRecord.id.in_(cross_source_ids))
        )).scalars().all() if cross_source_ids else []
    cross_brief_tags: Counter = Counter()
    for r in cross_sr:
        tag = (r.metadata_ or {}).get("target_brief", "<untagged>")
        cross_brief_tags[tag] += 1
    print(f"\ncross-brief evidence tag distribution: {dict(cross_brief_tags)}")

    # ---- Confirm no Triton-loyalist / Triton-buyer naming -----------
    triton_naming_violations: list[str] = []
    for p in triton_persona_rows:
        name_blob = (p.display_name or "").lower()
        if "triton" in name_blob:
            triton_naming_violations.append(p.display_name)
    print(f"persona names containing 'triton': {len(triton_naming_violations)}")

    # ---- Build PersonaAuditInput for each Triton persona ------------
    traits_by_pid: dict[UUID, list[PersonaTrait]] = defaultdict(list)
    for t in all_triton_traits:
        traits_by_pid[t.persona_id].append(t)
    links_by_pid: dict[UUID, list[PersonaEvidenceLink]] = defaultdict(list)
    for el in all_triton_links:
        links_by_pid[el.persona_id].append(el)

    audit_inputs: list[PersonaAuditInput] = []
    for p in triton_persona_rows:
        ts = traits_by_pid.get(p.id, [])
        ls = links_by_pid.get(p.id, [])
        audit_inputs.append(PersonaAuditInput(
            persona_id=p.id,
            display_name=p.display_name,
            traits=tuple(
                TraitView(
                    field_name=t.field_name,
                    support_level=t.support_level,
                    value=t.value,
                    confidence=float(t.confidence),
                    source_ids=tuple(t.source_ids or ()),
                    rationale=t.rationale,
                )
                for t in ts
            ),
            evidence_links=tuple(
                EvidenceLinkView(
                    persona_id=l.persona_id,
                    source_record_id=l.source_record_id,
                    contribution_kind=l.contribution_kind,
                    contribution_field=l.contribution_field,
                    excerpt=l.excerpt or "",
                    source_likely_human_signal=None,
                )
                for l in ls
            ),
        ))

    # ---- Score each persona against each Triton category ------------
    persona_results: list[dict] = []
    weights = plan.scorer_weights
    if weights is None:
        weights_dict = None
    elif hasattr(weights, "model_dump"):
        weights_dict = weights.model_dump()
    else:
        # Already a plain dict (Phase 8.2J derive_scorer_weights_for_plan
        # returns dict[str, float]).
        weights_dict = dict(weights)

    for pa in audit_inputs:
        per_category = []
        for c in plan_categories:
            try:
                bd = score_persona_against_category(
                    pa, c,
                    geography_required=geography_required,
                    weights=weights_dict,
                )
            except Exception as e:
                per_category.append({
                    "category_key": c.category_key,
                    "error": f"{type(e).__name__}: {e}",
                })
                continue
            per_category.append({
                "category_key": c.category_key,
                "category_priority": c.priority,
                "role_context_match": bd.role_context_match,
                "pain_objection_match": bd.pain_objection_match,
                "current_alternative_match": bd.current_alternative_match,
                "price_budget_match": bd.price_budget_match,
                "trust_trigger_match": bd.trust_trigger_match,
                "category_specific_match": bd.category_specific_match,
                "geography_match": bd.geography_match,
                "source_strength": bd.source_strength,
                "exclusion_penalty": bd.exclusion_penalty,
                "total_score": bd.total_score,
                "matched_signals": list(bd.matched_signals)[:6],
                "missing_signals": list(bd.missing_signals)[:6],
            })
        # Best-fit category = max total_score
        best = max(
            (c for c in per_category if "error" not in c),
            key=lambda c: c["total_score"],
            default=None,
        )

        # Build full text blob (traits + evidence excerpts) for human-
        # market-entry classification heuristic.
        blob_parts: list[str] = []
        for t in pa.traits:
            if t.value:
                blob_parts.append(t.value)
        for el in pa.evidence_links:
            if el.excerpt:
                blob_parts.append(el.excerpt)
        full_blob = " ".join(blob_parts)

        # Match against human market-entry pattern set.
        market_entry_hits: list[str] = []
        for tag, pat in _HUMAN_MARKET_ENTRY_PATTERNS.items():
            if pat.search(full_blob):
                market_entry_hits.append(tag)

        # Map persona to human market-entry categories.
        human_category_hits: list[str] = []
        for cat_key, required_tags in _MARKET_ENTRY_CATEGORIES_HUMAN:
            if any(t in market_entry_hits for t in required_tags):
                human_category_hits.append(cat_key)

        # Heuristic classification:
        trait_count = sum(1 for t in pa.traits if t.value)
        excerpt_count = len([el for el in pa.evidence_links if el.excerpt])
        market_entry_signal_count = len(market_entry_hits)
        category_specific_axis_score = (
            best.get("category_specific_match", 0) if best else 0
        )

        # Decision rules:
        if market_entry_signal_count >= 2 and trait_count >= 5:
            classification = "true_relevant_market_entry_persona"
        elif market_entry_signal_count >= 1 and trait_count >= 3:
            classification = "weak_market_entry_persona"
        elif market_entry_signal_count == 0:
            classification = "off_topic_persona"
        else:
            classification = "insufficient_evidence_persona"

        # Strongest evidence excerpts (longest 2 by char count, capped).
        strongest_excerpts = sorted(
            [el.excerpt for el in pa.evidence_links if el.excerpt],
            key=lambda s: -len(s),
        )[:2]
        strongest_excerpts = [e[:300] for e in strongest_excerpts]

        # Source-domain breakdown for this persona.
        src_domains: Counter = Counter()
        for el in pa.evidence_links:
            sr = sr_by_id.get(el.source_record_id)
            if sr is not None:
                d = (sr.metadata_ or {}).get("domain") or "unknown"
                src_domains[d] += 1

        # Source-kind breakdown.
        src_kinds: Counter = Counter()
        for el in pa.evidence_links:
            sr = sr_by_id.get(el.source_record_id)
            if sr is not None:
                src_kinds[sr.source_kind] += 1

        # Identify weakest scoring axes (across all 10 categories).
        axis_means: dict[str, float] = {}
        axes = (
            "role_context_match", "pain_objection_match",
            "current_alternative_match", "price_budget_match",
            "trust_trigger_match", "category_specific_match",
            "geography_match", "source_strength",
        )
        for ax in axes:
            vals = [c.get(ax, 0) for c in per_category if "error" not in c]
            axis_means[ax] = round(
                sum(vals) / len(vals) if vals else 0.0, 2
            )

        persona_results.append({
            "persona_id": str(pa.persona_id),
            "display_name": pa.display_name,
            "trait_count": trait_count,
            "evidence_link_count": excerpt_count,
            "best_category": (
                best["category_key"] if best else None
            ),
            "best_score": best["total_score"] if best else None,
            "best_breakdown": (
                {
                    k: v for k, v in best.items()
                    if k not in ("matched_signals", "missing_signals")
                }
                if best else None
            ),
            "best_matched_signals": (
                best.get("matched_signals", []) if best else []
            ),
            "best_missing_signals": (
                best.get("missing_signals", []) if best else []
            ),
            "per_axis_means_across_10_categories": axis_means,
            "source_domain_breakdown": dict(src_domains),
            "source_kind_breakdown": dict(src_kinds),
            "strongest_excerpts": strongest_excerpts,
            "human_market_entry_signals": market_entry_hits,
            "human_market_entry_signal_count": market_entry_signal_count,
            "human_category_hits": human_category_hits,
            "heuristic_classification": classification,
        })

    persona_results.sort(key=lambda r: -(r["best_score"] or 0))

    # ---- Aggregate analytics ----------------------------------------
    # (a) per-axis averages across all personas (using the persona's
    # best-fit-category breakdown).
    axis_totals: Counter = Counter()
    axis_n = 0
    for r in persona_results:
        bd = r.get("best_breakdown")
        if not bd:
            continue
        axis_n += 1
        for ax in (
            "role_context_match", "pain_objection_match",
            "current_alternative_match", "price_budget_match",
            "trust_trigger_match", "category_specific_match",
            "geography_match", "source_strength",
        ):
            axis_totals[ax] += bd.get(ax, 0)
    axis_avg_best_fit: dict[str, float] = {
        ax: round(axis_totals[ax] / axis_n, 2) if axis_n else 0.0
        for ax in (
            "role_context_match", "pain_objection_match",
            "current_alternative_match", "price_budget_match",
            "trust_trigger_match", "category_specific_match",
            "geography_match", "source_strength",
        )
    }

    # (b) classification distribution
    classification_counts: Counter = Counter(
        r["heuristic_classification"] for r in persona_results
    )

    # (c) coverage under current scorer (best-fit-category counts)
    current_scorer_coverage: Counter = Counter(
        r["best_category"] for r in persona_results if r["best_category"]
    )

    # (d) coverage under human market-entry interpretation
    human_coverage: Counter = Counter()
    for r in persona_results:
        for cat in r["human_category_hits"]:
            human_coverage[cat] += 1

    # (e) source-domain breakdown across all personas
    domain_to_classifications: defaultdict[str, Counter] = defaultdict(Counter)
    for r in persona_results:
        cls = r["heuristic_classification"]
        for d in r["source_domain_breakdown"]:
            domain_to_classifications[d][cls] += 1
    domain_quality: dict[str, dict] = {}
    for d, cls_counts in domain_to_classifications.items():
        total = sum(cls_counts.values())
        domain_quality[d] = {
            "total_persona_links": total,
            "true_relevant": cls_counts.get(
                "true_relevant_market_entry_persona", 0
            ),
            "weak_market_entry": cls_counts.get(
                "weak_market_entry_persona", 0
            ),
            "off_topic": cls_counts.get("off_topic_persona", 0),
            "insufficient": cls_counts.get(
                "insufficient_evidence_persona", 0
            ),
        }

    # (f) source-kind breakdown
    sk_counts: Counter = Counter()
    for r in persona_results:
        for k, c in r["source_kind_breakdown"].items():
            sk_counts[k] += c

    # (g) lowest-scoring axes overall
    sorted_axes = sorted(axis_avg_best_fit.items(), key=lambda kv: kv[1])
    lowest_axes = sorted_axes[:3]

    # ---- Write audit JSON --------------------------------------------
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "triton_relevance_forensics_8_4a_1.json"
    summary = {
        "phase": "8_4a_1_triton_relevance_forensics",
        "completed_at": datetime.now(UTC).isoformat(),
        "personas_inspected": len(persona_results),
        "amboras_persona_cross_pollination_check": {
            "cross_brief_tag_distribution": dict(cross_brief_tags),
            "all_triton_only": (
                set(cross_brief_tags.keys()) == {"triton_drinks"}
            ),
        },
        "triton_naming_violations": triton_naming_violations,
        "plan_categories": [c.category_key for c in plan_categories],
        "current_scorer_coverage_top_category_per_persona":
            dict(current_scorer_coverage),
        "human_market_entry_coverage": dict(human_coverage),
        "axis_averages_at_best_fit_category": axis_avg_best_fit,
        "lowest_scoring_axes_overall": [
            {"axis": ax, "average": av} for ax, av in lowest_axes
        ],
        "classification_distribution": dict(classification_counts),
        "source_domain_quality": domain_quality,
        "source_kind_breakdown_total_links": dict(sk_counts),
        "personas_top20": persona_results[:20],
        "personas_all": persona_results,
    }
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # ---- Operator-facing print ---------------------------------------
    print()
    print("=" * 70)
    print("FORENSIC SUMMARY")
    print("=" * 70)
    print(f"personas inspected:      {len(persona_results)}")
    print(f"naming violations:       {len(triton_naming_violations)}")
    print(
        f"cross-brief tag check:   "
        f"{dict(cross_brief_tags)}"
    )
    print()
    print("classification distribution:")
    for cls, n in classification_counts.most_common():
        print(f"  {cls}: {n}")
    print()
    print("axis averages (at best-fit category):")
    for ax, av in axis_avg_best_fit.items():
        print(f"  {ax}: {av}")
    print()
    print("lowest-scoring axes:")
    for ax, av in lowest_axes:
        print(f"  {ax}: {av}")
    print()
    print("current scorer best-fit-category distribution:")
    for cat, n in current_scorer_coverage.most_common():
        print(f"  {cat}: {n}")
    print()
    print("HUMAN market-entry coverage (broader, read-only):")
    for cat, n in human_coverage.most_common():
        print(f"  {cat}: {n}")
    print()
    print("source-kind breakdown (total evidence-link counts):")
    for k, n in sk_counts.items():
        print(f"  {k}: {n}")
    print()
    print(f"→ audit JSON: {out_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
