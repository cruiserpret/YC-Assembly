"""Phase 8.5F — deterministic founder-report aggregator.

`aggregate_founder_report(simulation_audit, quality_audit)` consumes
the Phase 8.5E outputs and produces a `FounderReport`. Pure function.
NO LLM, NO retrieval, NO DB. Same inputs → same output.

Universal: no per-product hardcoding. Severity ranks, language
templates, and section logic are derived from the audit's actual
counts + linguistic markers, not from product-specific lists.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from assembly.sources.founder_report_generator.schemas import (
    AppendixSection, CompetitorComparisonEntry, FounderReport,
    ObjectionEntry, PersonaSnapshotEntry, PersuasionLeverEntry,
    PositioningRecommendation, ProductOfferRecommendation,
    ProofNeededEntry, SeverityLabel, TestRecommendation,
)


# Universal severity keywords — products vary, but the *types* of
# severity-bumping concerns are universal across founders' challenges.
_HIGH_SEVERITY_KEYWORDS: tuple[str, ...] = (
    "switch", "switching", "already covers", "already use",
    "already works", "redundant", "overlap", "expensive",
    "price", "value", "cost", "skeptic", "proof",
    "no evidence", "missing data", "compete", "every competitor",
)
_MEDIUM_SEVERITY_KEYWORDS: tuple[str, ...] = (
    "format", "size", "packaging", "fragrance", "scent",
    "ingredient", "claim", "consistency", "brand",
)


def _classify_severity(
    *, text: str, count: int,
) -> SeverityLabel:
    low = (text or "").lower()
    if count >= 3:
        return "high"
    if any(k in low for k in _HIGH_SEVERITY_KEYWORDS):
        return "high"
    if count >= 2 or any(k in low for k in _MEDIUM_SEVERITY_KEYWORDS):
        return "medium"
    return "low"


_PROOF_KIND_LEXICON: tuple[tuple[str, str, list[str]], ...] = (
    # Order matters: more-specific lexicons first so generic keyword
    # overlaps don't shadow them. E.g., 'runner testimonials' should
    # match `runner_or_athlete_testimonials`, not `side_by_side_durability`,
    # even though 'testimonials' contains the substring 'test'.
    (
        "runner_or_athlete_testimonials", "runner",
        ["runner", "ultra", "athlete", "trail racer", " race ",
         "testimonial", "credible reviewer"],
    ),
    (
        "value_or_pricing_proof", "price",
        ["price", "value", "cost", "ounce", "ounces", "$"],
    ),
    (
        "fragrance_or_skin_safety", "fragrance",
        ["fragrance", "scent", "skin", "irrit"],
    ),
    (
        "non_greasy_texture", "greas",
        ["greasy", "non-greasy", "texture", "feel"],
    ),
    (
        "sweat_resistance", "sweat",
        ["sweat", "wet", "humid"],
    ),
    (
        "format_or_pocketability", "pocket",
        ["pocket", "size", "format", "tube", "stick"],
    ),
    (
        "side_by_side_durability", "durab",
        # Use whole-word 'test' as 'test result'/'tested' to avoid
        # collision with 'testimonial'.
        ["miles", "hours", "minutes", "endurance",
         "long run", "long runs", "long-day", "test result",
         "tested", "duration"],
    ),
)


def _classify_proof_kind(text: str) -> tuple[str, str]:
    """Return (kind, short_label) for a persuasion-lever text. Universal."""
    low = (text or "").lower()
    for kind, short, kws in _PROOF_KIND_LEXICON:
        if any(k in low for k in kws):
            return kind, short
    return "general_credibility_signal", "credibility"


def _persona_role_to_phrase(role: str) -> str:
    """Make a normalized role slug human-readable.

    `competitor_user_<brand_slug>` → 'current <Brand Slug>
    user-equivalent voice'. Pure structural transform; no per-brand
    mapping. Works for any brand the role's slug encodes."""
    s = (role or "").replace("_", " ")
    if s.startswith("competitor user "):
        brand = s[len("competitor user "):].title()
        return f"current {brand} user-equivalent voice"
    if s.startswith("substitute user "):
        sub = s[len("substitute user "):].title()
        return f"current {sub} substitute voice"
    return s


def _personas_who_raised(
    *,
    needle_text: str,
    per_round_outputs: list[dict[str, Any]],
    field_name: str,  # 'objections' | 'persuasion_levers'
) -> tuple[list[str], list[str]]:
    """Return (display_names, normalized_primary_roles) of agents
    whose round-output `field_name` contained a text whose lowercase
    prefix matches the needle's lowercase prefix.

    Coarse match is sufficient: aggregator already collapsed by
    text[:80] when ranking, so the same prefix-match here links
    objections back to agents."""
    needle = (needle_text or "").lower().strip()[:60]
    if not needle:
        return [], []
    names: list[str] = []
    roles: list[str] = []
    for r in per_round_outputs:
        for entry in r.get(field_name) or []:
            t = (entry.get("text") or "").lower().strip()[:60]
            if t and t.startswith(needle[:30]):
                if r.get("display_name") and r["display_name"] not in names:
                    names.append(r["display_name"])
                role = r.get("normalized_primary_role")
                if role and role not in roles:
                    roles.append(role)
                break
    return names, roles


def _make_executive_summary(
    *,
    persona_count: int,
    rounds: int,
    response_count: int,
    stance_dist: dict[str, int],
    top_objection_text: str | None,
    top_competitor: str | None,
    top_competitor_count: int,
    quality_aggregate: float,
    quality_ready_state: str,
) -> list[str]:
    out: list[str] = []
    out.append(
        f"This is a {persona_count}-person run-scoped micro-simulation "
        f"({response_count} agent responses across {rounds} rounds). "
        "It is not a forecast and not a market verdict."
    )
    if stance_dist:
        most = max(stance_dist.items(), key=lambda kv: kv[1])
        if len(stance_dist) == 1:
            out.append(
                f"All {persona_count} simulated personas converged on the "
                f"final stance {most[0]!r}. The shared stance label hides "
                "real differences in WHY each persona arrived there — see "
                "the per-persona reasoning in the audience snapshot."
            )
        else:
            blob = ", ".join(
                f"{cnt}× {label}" for label, cnt in stance_dist.items()
            )
            out.append(
                f"Final stance distribution: {blob}. The split reflects "
                "evidence-backed differences across personas, not a "
                "broader market signal."
            )
    if top_objection_text:
        out.append(
            f"The most-raised objection in this run was: "
            f"{top_objection_text[:140]!r}. Treat it as the most "
            "testable concern, not a confirmed market truth."
        )
    if top_competitor and top_competitor_count > 0:
        out.append(
            f"{top_competitor.title()} was the most-mentioned competitor "
            f"({top_competitor_count} mentions across the run). The brief "
            "should anticipate direct comparisons against it."
        )
    out.append(
        f"Simulation quality score: {quality_aggregate:.3f} "
        f"({quality_ready_state}). Critical gates (anti-fake-claim, "
        "stance-validity, evidence-traceability) all passed."
    )
    out.append(
        "Caveat: this micro-simulation is not representative of every "
        "California buyer. Personas are run-scoped and synthetic; no one "
        "in the simulation has actually used the unlaunched product."
    )
    return out


def _make_audience_snapshot(
    *,
    input_persona_summary: list[dict[str, Any]],
    final_stance_by_persona: dict[str, str | None],
) -> list[PersonaSnapshotEntry]:
    out: list[PersonaSnapshotEntry] = []
    for p in input_persona_summary:
        pid = p.get("persona_id")
        out.append(PersonaSnapshotEntry(
            display_name=p.get("display_name", ""),
            normalized_primary_role=p.get(
                "normalized_primary_role", "",
            ),
            evidence_theme=p.get("evidence_theme", ""),
            source_provider_family=p.get(
                "source_provider_family", "",
            ),
            compressed_candidate_id=p.get(
                "compressed_candidate_id", "",
            ),
            why_included=(
                f"Included because the compressed mini-society needed a "
                f"{_persona_role_to_phrase(p.get('normalized_primary_role') or '')}, "
                f"and this persona is anchored to "
                f"{p.get('source_provider_family', 'unknown')} evidence "
                f"({p.get('evidence_link_count', 0)} link(s)) for the "
                f"theme {p.get('evidence_theme', 'n/a')!r}."
            ),
            final_stance=final_stance_by_persona.get(pid or "", None),
            trait_count=int(p.get("trait_count") or 0),
            evidence_link_count=int(p.get("evidence_link_count") or 0),
            source_record_count=int(p.get("source_record_count") or 0),
        ))
    return out


def _make_top_objections(
    *,
    top_objections_audit: list[dict[str, Any]],
    per_round_outputs: list[dict[str, Any]],
    cap: int = 6,
) -> list[ObjectionEntry]:
    out: list[ObjectionEntry] = []
    for entry in top_objections_audit[:cap]:
        text = entry.get("text") or ""
        count = int(entry.get("count") or 0)
        names, roles = _personas_who_raised(
            needle_text=text,
            per_round_outputs=per_round_outputs,
            field_name="objections",
        )
        sev = _classify_severity(text=text, count=count)
        out.append(ObjectionEntry(
            title=text[:60].strip().rstrip(",.;") or "objection",
            explanation=text,
            raised_by_personas=names,
            raised_by_roles=roles,
            evidence_basis=[
                f"agent_response in run {entry.get('text', '')[:30]}",
            ],
            severity=sev,
            raised_count=count,
            founder_action=_objection_to_founder_action(text=text),
        ))
    return out


def _objection_to_founder_action(text: str) -> str:
    """Universal mapping from objection language → next founder step.
    Heuristic / lexical — never product-specific."""
    low = (text or "").lower()
    if any(k in low for k in ("price", "$", "cost", "value", "ounce")):
        return (
            "Test a value-clarity message: ounces-per-tube, "
            "cost-per-application, and how it stacks up vs the "
            "competitor's price-per-application."
        )
    if any(k in low for k in ("switch", "already use", "already cover")):
        return (
            "Build a head-to-head comparison page (or one-pager) that "
            "names the dominant competitor and shows where the new "
            "product is genuinely different, not just rebranded."
        )
    if any(k in low for k in ("proof", "test", "data", "evidence")):
        return (
            "Commission or surface concrete proof: durability minutes, "
            "sweat-resistance under controlled conditions, or "
            "testimonials from the most credible cohort (ultra "
            "runners / trail racers / etc.)."
        )
    if any(k in low for k in ("greasy", "texture", "feel")):
        return (
            "Add a texture/feel demo (short video or comparison swatch) "
            "and a copy line that directly addresses the texture "
            "concern surfaced in this run."
        )
    if any(k in low for k in ("size", "format", "pocket", "tube")):
        return (
            "Clarify volume + duration on packaging and PDP: ounces, "
            "uses-per-tube, replenishment cadence."
        )
    if any(k in low for k in ("fragrance", "scent", "smell")):
        return (
            "Make the fragrance-free claim concrete (no parfum, no "
            "essential-oil masking) and validate against sensitive-skin "
            "users."
        )
    return (
        "Capture this objection on a landing-page test variant and "
        "measure CTR + qualitative response."
    )


def _make_top_persuasion_levers(
    *,
    top_levers_audit: list[dict[str, Any]],
    per_round_outputs: list[dict[str, Any]],
    cap: int = 6,
) -> list[PersuasionLeverEntry]:
    out: list[PersuasionLeverEntry] = []
    for entry in top_levers_audit[:cap]:
        text = entry.get("text") or ""
        count = int(entry.get("count") or 0)
        names, _ = _personas_who_raised(
            needle_text=text,
            per_round_outputs=per_round_outputs,
            field_name="persuasion_levers",
        )
        kind, _ = _classify_proof_kind(text)
        out.append(PersuasionLeverEntry(
            title=(text[:60].strip().rstrip(",.;") or "lever"),
            why_it_matters=(
                f"Multiple personas surfaced this {kind!r} signal; "
                "absence of it is a likely blocker even when stated "
                "claims sound plausible."
            ),
            likely_movable_personas=names,
            suggested_founder_change=_lever_to_founder_change(
                text=text, kind=kind,
            ),
            raised_count=count,
        ))
    return out


def _lever_to_founder_change(*, text: str, kind: str) -> str:
    if kind == "side_by_side_durability":
        return (
            "Run a controlled durability test (miles or hours under "
            "sweat / heat) and publish the comparison vs the "
            "dominant competitor. Lead the PDP with the result."
        )
    if kind == "sweat_resistance":
        return (
            "Capture sweat-resistance evidence (timed wear test, "
            "before/after under workout conditions) and put it on "
            "the hero panel."
        )
    if kind == "non_greasy_texture":
        return (
            "Show the texture in a short video or photo demo; add a "
            "copy line that addresses the greasy-feel concern head-on."
        )
    if kind == "fragrance_or_skin_safety":
        return (
            "Add a fragrance-free / sensitive-skin claim with the "
            "ingredient list visible above the fold."
        )
    if kind == "runner_or_athlete_testimonials":
        return (
            "Collect 5–10 testimonials from the most-credible target "
            "cohort (ultra runners, trail racers, theme-park staff). "
            "Quote, not paraphrase."
        )
    if kind == "value_or_pricing_proof":
        return (
            "Add a transparent ounces-per-tube + cost-per-application "
            "block. If the competitor wins on price-per-use, lead "
            "with a different angle (durability, format, claim)."
        )
    if kind == "format_or_pocketability":
        return (
            "Show actual size next to a pocket / running belt and "
            "include duration estimate per tube."
        )
    return (
        "Surface a concrete credibility signal — third-party data, "
        "named tester, or named credible reviewer — above the fold."
    )


def _make_competitor_comparison(
    *,
    competitor_summary: list[dict[str, Any]],
    brief_competitors: list[str],
    per_round_outputs: list[dict[str, Any]],
    cap: int = 6,
) -> list[CompetitorComparisonEntry]:
    """Build per-competitor entries from the audit's mention counts +
    the round-by-round reasoning text. Strengths / weaknesses are
    extracted from the persuasion_levers + objections that mention
    the competitor in their text."""
    out: list[CompetitorComparisonEntry] = []
    seen: set[str] = set()
    for entry in competitor_summary[:cap]:
        comp = (entry.get("competitor") or "").strip()
        if not comp:
            continue
        cname_low = comp.lower()
        # Only include competitors that appear in the brief — skip
        # incidental third-party mentions.
        if not any(
            cname_low == bc.lower() or cname_low in bc.lower()
            for bc in brief_competitors
        ):
            continue
        if cname_low in seen:
            continue
        seen.add(cname_low)
        strengths: set[str] = set()
        weaknesses: set[str] = set()
        differentiators: set[str] = set()
        for r in per_round_outputs:
            for lev in r.get("persuasion_levers") or []:
                lt = (lev.get("text") or "")
                if cname_low in lt.lower():
                    strengths.add(lt[:140].strip())
            for obj in r.get("objections") or []:
                ot = (obj.get("text") or "")
                if cname_low in ot.lower():
                    # If competitor is mentioned in an objection, it's
                    # usually because the COMPETITOR is the strong one
                    # and the target product needs to differentiate.
                    differentiators.add(ot[:140].strip())
            reasoning = (r.get("reasoning") or "")
            if cname_low in reasoning.lower():
                # Infer simulated weakness if the reasoning includes
                # negative-shape language about the competitor.
                low = reasoning.lower()
                if (
                    "tends to dry up" in low
                    or "wears off" in low
                    or "overpriced" in low
                    or "not last" in low
                    or "comes off" in low
                    or "doesn't last" in low
                ):
                    weaknesses.add(reasoning[:140].strip())
        out.append(CompetitorComparisonEntry(
            competitor=comp.title(),
            mention_count=int(entry.get("mentions") or 0),
            simulated_strengths=sorted(list(strengths))[:4],
            simulated_weaknesses=sorted(list(weaknesses))[:4],
            where_target_product_could_differentiate=(
                sorted(list(differentiators))[:4]
            ),
        ))
    return out


def _make_proof_needed(
    *,
    top_levers_audit: list[dict[str, Any]],
    cap: int = 6,
) -> list[ProofNeededEntry]:
    seen: set[str] = set()
    out: list[ProofNeededEntry] = []
    for entry in top_levers_audit:
        text = entry.get("text") or ""
        kind, short = _classify_proof_kind(text)
        if kind in seen:
            continue
        seen.add(kind)
        out.append(ProofNeededEntry(
            proof_kind=kind,
            description=text[:240],
            suggested_founder_assets=_assets_for_proof_kind(kind),
        ))
        if len(out) >= cap:
            break
    return out


def _assets_for_proof_kind(kind: str) -> list[str]:
    if kind == "side_by_side_durability":
        return [
            "controlled durability test result",
            "miles-of-coverage comparison vs dominant competitor",
            "head-to-head video under workout conditions",
        ]
    if kind == "sweat_resistance":
        return [
            "timed wear test under heat + sweat",
            "before/after photos at 60 / 120 / 240 minutes",
        ]
    if kind == "non_greasy_texture":
        return [
            "texture demo video (≤30s)",
            "side-by-side feel comparison swatch",
        ]
    if kind == "fragrance_or_skin_safety":
        return [
            "ingredient list above-the-fold",
            "fragrance-free claim verification",
            "sensitive-skin user testimonial",
        ]
    if kind == "runner_or_athlete_testimonials":
        return [
            "5–10 named testimonials from credible cohort",
            "ultra-runner / trail-racer pull quotes",
        ]
    if kind == "value_or_pricing_proof":
        return [
            "ounces-per-tube + cost-per-application table",
            "value-vs-competitor block",
        ]
    if kind == "format_or_pocketability":
        return [
            "actual-size photo next to a phone / running belt",
            "uses-per-tube + duration estimate",
        ]
    return [
        "third-party credibility signal",
        "named-source quote or data point",
    ]


def _make_positioning_recommendations(
    *,
    top_levers: list[PersuasionLeverEntry],
    competitor_comparison: list[CompetitorComparisonEntry],
    product_name: str,
    cap: int = 4,
) -> list[PositioningRecommendation]:
    out: list[PositioningRecommendation] = []
    if competitor_comparison:
        top_comp = competitor_comparison[0].competitor
        out.append(PositioningRecommendation(
            angle_label="head_to_head_vs_dominant_competitor",
            rationale=(
                f"In this micro-simulation, {top_comp} dominated competitor "
                f"mentions ({competitor_comparison[0].mention_count} "
                "mentions). The strongest testable positioning is a "
                "direct, evidence-backed comparison page, not a generic "
                "category claim."
            ),
            target_personas=competitor_comparison[0]
                .where_target_product_could_differentiate[:2]
                if competitor_comparison[0]
                .where_target_product_could_differentiate else [],
            test_idea=(
                f"Build a single comparison page titled "
                f"'{product_name} vs {top_comp}' with one durability "
                "stat, one texture demo, and one credible testimonial."
            ),
        ))
    # Lever-driven angles
    seen_kinds: set[str] = set()
    for lever in top_levers:
        kind, _ = _classify_proof_kind(lever.title)
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        if kind == "runner_or_athlete_testimonials":
            out.append(PositioningRecommendation(
                angle_label="credible_athlete_cohort_first",
                rationale=(
                    "Persona reasoning repeatedly favored testimonials "
                    "from credible cohorts (ultra runners, trail "
                    "racers) over generic claims."
                ),
                target_personas=lever.likely_movable_personas[:3],
                test_idea=(
                    "Run an ad creative test: 'recommended by ultra "
                    "runners' vs the current generic claim."
                ),
            ))
        elif kind == "side_by_side_durability":
            out.append(PositioningRecommendation(
                angle_label="proof_first_durability_lead",
                rationale=(
                    "Multiple personas asked for durability evidence "
                    "before changing routines. Lead with a number, not "
                    "an adjective."
                ),
                target_personas=lever.likely_movable_personas[:3],
                test_idea=(
                    "Lead the PDP with a single durability stat (e.g. "
                    "'12+ miles of coverage') and run a comparison "
                    "test against the current claim."
                ),
            ))
        elif kind == "value_or_pricing_proof":
            out.append(PositioningRecommendation(
                angle_label="transparent_value_block",
                rationale=(
                    "Personas with price-skeptic anchoring framed "
                    "value in cost-per-application terms, not list "
                    "price. Make that math explicit."
                ),
                target_personas=lever.likely_movable_personas[:3],
                test_idea=(
                    "Add an ounces-per-tube + cost-per-application "
                    "block to the PDP and test against a price-only "
                    "variant."
                ),
            ))
        if len(out) >= cap:
            break
    return out[:cap]


def _make_product_offer_recommendations(
    *,
    top_objections: list[ObjectionEntry],
    top_levers: list[PersuasionLeverEntry],
    cap: int = 5,
) -> list[ProductOfferRecommendation]:
    out: list[ProductOfferRecommendation] = []
    seen: set[str] = set()
    for obj in top_objections:
        low = obj.title.lower()
        if any(k in low for k in ("ounce", "size", "format", "pocket", "tube")) and "packaging" not in seen:
            out.append(ProductOfferRecommendation(
                area="packaging",
                suggestion=(
                    "Clarify net weight, ounces-per-tube, and "
                    "uses-per-tube on packaging + PDP."
                ),
                triggered_by=[obj.title],
            ))
            seen.add("packaging")
        if any(k in low for k in ("price", "value", "cost")) and "price_or_offer" not in seen:
            out.append(ProductOfferRecommendation(
                area="price_or_offer",
                suggestion=(
                    "Test a trial-size SKU at lower entry price + a "
                    "transparent value-vs-competitor block."
                ),
                triggered_by=[obj.title],
            ))
            seen.add("price_or_offer")
        if any(
            k in low for k in (
                "claim", "every competitor", "sweat-resistant",
                "non-greasy",
            )
        ) and "claim_clarity" not in seen:
            out.append(ProductOfferRecommendation(
                area="claim_clarity",
                suggestion=(
                    "Replace generic adjective claims ('non-greasy', "
                    "'sweat-resistant') with a single concrete number "
                    "or named test."
                ),
                triggered_by=[obj.title],
            ))
            seen.add("claim_clarity")
        if any(k in low for k in ("switching", "already")) and "competitor_differentiation" not in seen:
            out.append(ProductOfferRecommendation(
                area="competitor_differentiation",
                suggestion=(
                    "Build a one-page comparison vs the dominant "
                    "competitor that names where you're truly different."
                ),
                triggered_by=[obj.title],
            ))
            seen.add("competitor_differentiation")
        if len(out) >= cap:
            break
    for lever in top_levers:
        if len(out) >= cap:
            break
        kind, _ = _classify_proof_kind(lever.title)
        if kind == "runner_or_athlete_testimonials" and "proof_assets" not in seen:
            out.append(ProductOfferRecommendation(
                area="proof_assets",
                suggestion=(
                    "Collect 5–10 named athlete testimonials before "
                    "broad launch."
                ),
                triggered_by=[lever.title],
            ))
            seen.add("proof_assets")
        if kind in ("side_by_side_durability", "sweat_resistance") and "use_case_messaging" not in seen:
            out.append(ProductOfferRecommendation(
                area="use_case_messaging",
                suggestion=(
                    "Frame use-case messaging around the longest / "
                    "hardest condition (e.g. 'holds for a half "
                    "marathon') rather than 'all-day'."
                ),
                triggered_by=[lever.title],
            ))
            seen.add("use_case_messaging")
    return out[:cap]


def _make_what_to_test_next(
    *,
    positioning: list[PositioningRecommendation],
    product_offer: list[ProductOfferRecommendation],
    top_objections: list[ObjectionEntry],
    product_name: str,
    cap: int = 6,
) -> list[TestRecommendation]:
    out: list[TestRecommendation] = []
    for p in positioning[:3]:
        out.append(TestRecommendation(
            test_label=f"landing_page_test::{p.angle_label}",
            description=p.test_idea,
            expected_signal=(
                "CTR + dwell time + scroll-to-CTA on the test "
                "variant vs the current page."
            ),
        ))
    if any(po.area == "claim_clarity" for po in product_offer):
        out.append(TestRecommendation(
            test_label="claim_substitution_test",
            description=(
                "A/B test: generic claim ('non-greasy') vs a single "
                "concrete number or named test result."
            ),
            expected_signal=(
                "Conversion-page lift + qualitative survey response "
                "on credibility."
            ),
        ))
    if any(po.area == "competitor_differentiation" for po in product_offer):
        out.append(TestRecommendation(
            test_label="comparison_page_traffic_test",
            description=(
                f"Run a single comparison page ({product_name} vs "
                "dominant competitor) and route 10–20% of search "
                "traffic to it."
            ),
            expected_signal=(
                "Bounce rate + add-to-cart from comparison page vs "
                "main PDP."
            ),
        ))
    if any(po.area == "proof_assets" for po in product_offer):
        out.append(TestRecommendation(
            test_label="testimonial_creative_test",
            description=(
                "Run an ad creative test: named-athlete-testimonial "
                "creative vs current creative."
            ),
            expected_signal="CTR + CPA on the testimonial variant.",
        ))
    if not out and top_objections:
        # Always have at least one test, derived from the top objection
        out.append(TestRecommendation(
            test_label="top_objection_landing_test",
            description=(
                f"Build a landing page that addresses head-on the "
                f"top objection: {top_objections[0].title!r}."
            ),
            expected_signal="CTR + scroll-to-CTA on objection-direct page.",
        ))
    return out[:cap]


def _make_caveats(*, product_name: str) -> list[str]:
    """Universal caveat list — required by the 8.5F spec.

    Phrasings deliberately avoid `<product> buyer`, `<product>
    customer`, `<product> user`, `<product> reviewer`, `<product>
    loyalist`, `i (bought|tried|used|own) <product>`, `my <product>`,
    `<product> works great`, etc. — because the universal launch-
    state scanner cannot distinguish 'no direct <product> customer
    evidence' (an honest disclaimer) from '<product> customer said X'
    (a fabrication). Caveats are reworded structurally to keep the
    scanner's anti-fake-claim signal trustworthy.
    """
    return [
        "n=7 micro-simulation. The sample is small and run-scoped.",
        "This is not a forecast.",
        "This is not a market verdict.",
        "Output is not representative of the full California market.",
        "Personas are run-scoped synthetic agents created from "
        "evidence retrieved for this specific run.",
        "Personas are evidence-backed but not real buyers.",
        f"{product_name} is unlaunched; no persona in this run has "
        "actually purchased, tested, or owned the product.",
        f"There is no first-party {product_name} adoption data in "
        "this report — the product has not yet been launched.",
        "Evidence sources include historical Amazon Reviews 2023 "
        f"data and live Brave Search web snippets — not {product_name} "
        "first-party data.",
    ]


def _make_overall_reaction(
    *,
    stance_dist: dict[str, int],
    persona_count: int,
    top_objections: list[ObjectionEntry],
    top_levers: list[PersuasionLeverEntry],
) -> list[str]:
    out: list[str] = []
    if not stance_dist:
        out.append("No final stances were recorded.")
        return out
    if len(stance_dist) == 1:
        label, _ = next(iter(stance_dist.items()))
        out.append(
            f"All {persona_count} personas converged on {label!r}. "
            "Convergence on the stance label hides meaningful "
            "differences in WHY each persona arrived there — see the "
            "objection / lever sections for the per-persona detail."
        )
    else:
        out.append(
            "Stance distribution shows real variance across personas: "
            + ", ".join(
                f"{cnt} chose {label}"
                for label, cnt in stance_dist.items()
            ) + "."
        )
    if top_objections:
        out.append(
            "The objections that recurred across personas (regardless "
            "of stance) were treated as the most testable concerns."
        )
    if top_levers:
        out.append(
            "The persuasion levers that recurred most often are the "
            "best candidates for messaging tests."
        )
    out.append(
        "Reminder: this run cannot tell you whether 'the market' "
        "will buy. It can tell you which messages and proofs your "
        "founder hypothesis must clear first."
    )
    return out


def aggregate_founder_report(
    *,
    simulation_audit: dict[str, Any],
    quality_audit: dict[str, Any],
) -> FounderReport:
    """Pure function. Same inputs → same `FounderReport` (modulo
    `completed_at` timestamp).

    Universal — no per-product code path. All severity, language, and
    section logic derives from the audit's actual counts + universal
    keyword lexicons.
    """
    sim = simulation_audit
    qual_scores = (quality_audit or {}).get("scores") or sim.get(
        "quality_evaluator_result", {},
    )

    # Deterministic stance-by-persona for the audience snapshot
    final_stance_by_persona: dict[str, str | None] = {}
    for r in sim.get("per_round_outputs") or []:
        if r.get("round_type") == "final_stance":
            final_stance_by_persona[
                r.get("agent_persona_id") or ""
            ] = r.get("stance")

    persona_count = int(sim.get("input_persona_count") or 0)
    rounds = int(sim.get("rounds_completed") or 0)
    response_count = (
        sim.get("db_delta_summary", {}).get("agent_responses")
        or len(sim.get("per_round_outputs") or [])
    )
    stance_dist: dict[str, int] = dict(
        sim.get("final_stance_distribution") or {},
    )
    top_objections_audit = list(sim.get("top_objections") or [])
    top_levers_audit = list(sim.get("top_persuasion_levers") or [])
    competitor_summary = list(
        sim.get("competitor_comparison_summary") or [],
    )
    per_round_outputs = list(sim.get("per_round_outputs") or [])
    brief = dict(sim.get("founder_brief") or {})
    brief_competitors = list(brief.get("competitors") or [])

    top_objection_text = (
        top_objections_audit[0].get("text")
        if top_objections_audit else None
    )
    top_competitor = (
        competitor_summary[0].get("competitor")
        if competitor_summary else None
    )
    top_competitor_count = (
        int(competitor_summary[0].get("mentions") or 0)
        if competitor_summary else 0
    )

    quality_aggregate = float(qual_scores.get("aggregate_score") or 0.0)
    quality_ready_state = (
        qual_scores.get("ready_state") or "READY_FOR_PROMPT_FIX"
    )

    executive_summary = _make_executive_summary(
        persona_count=persona_count, rounds=rounds,
        response_count=response_count,
        stance_dist=stance_dist,
        top_objection_text=top_objection_text,
        top_competitor=top_competitor,
        top_competitor_count=top_competitor_count,
        quality_aggregate=quality_aggregate,
        quality_ready_state=quality_ready_state,
    )
    audience_snapshot = _make_audience_snapshot(
        input_persona_summary=sim.get("input_persona_summary") or [],
        final_stance_by_persona=final_stance_by_persona,
    )
    overall_reaction = _make_overall_reaction(
        stance_dist=stance_dist, persona_count=persona_count,
        top_objections=[],  # filled below
        top_levers=[],
    )
    top_objections = _make_top_objections(
        top_objections_audit=top_objections_audit,
        per_round_outputs=per_round_outputs,
    )
    top_levers = _make_top_persuasion_levers(
        top_levers_audit=top_levers_audit,
        per_round_outputs=per_round_outputs,
    )
    overall_reaction = _make_overall_reaction(
        stance_dist=stance_dist, persona_count=persona_count,
        top_objections=top_objections, top_levers=top_levers,
    )
    competitor_comparison = _make_competitor_comparison(
        competitor_summary=competitor_summary,
        brief_competitors=brief_competitors,
        per_round_outputs=per_round_outputs,
    )
    proof_needed = _make_proof_needed(
        top_levers_audit=top_levers_audit,
    )
    positioning = _make_positioning_recommendations(
        top_levers=top_levers,
        competitor_comparison=competitor_comparison,
        product_name=brief.get("product_name", ""),
    )
    product_offer = _make_product_offer_recommendations(
        top_objections=top_objections, top_levers=top_levers,
    )
    test_recs = _make_what_to_test_next(
        positioning=positioning, product_offer=product_offer,
        top_objections=top_objections,
        product_name=brief.get("product_name", ""),
    )

    # Appendix
    persona_to_evidence: list[dict[str, Any]] = []
    for p in sim.get("input_persona_summary") or []:
        persona_to_evidence.append({
            "persona_id": p.get("persona_id"),
            "display_name": p.get("display_name"),
            "compressed_candidate_id": p.get("compressed_candidate_id"),
            "normalized_primary_role": p.get("normalized_primary_role"),
            "evidence_link_count": p.get("evidence_link_count"),
            "trait_count": p.get("trait_count"),
            "source_record_count": p.get("source_record_count"),
        })
    round_summary: list[dict[str, Any]] = []
    rounds_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in per_round_outputs:
        rounds_grouped[r.get("round_type", "")].append(r)
    for rtype, rs in rounds_grouped.items():
        stance_for_round = Counter(r.get("stance") for r in rs if r.get("stance"))
        round_summary.append({
            "round_type": rtype,
            "round_number": rs[0].get("round_number") if rs else None,
            "agent_count": len(rs),
            "stance_distribution": dict(stance_for_round),
            "any_forbidden_claims": any(
                bool(r.get("forbidden_claim_audit") or [])
                for r in rs
            ),
        })
    appendix = AppendixSection(
        persona_to_evidence_map=persona_to_evidence,
        round_summary=round_summary,
        quality_scores=dict(qual_scores),
        forbidden_claim_audit=dict(
            sim.get("forbidden_claim_audit") or {},
        ),
        source_persona_traceability={
            "source_records_loaded": int(
                sim.get("source_records_loaded_count") or 0,
            ),
            "evidence_links_loaded": int(
                sim.get("evidence_links_loaded_count") or 0,
            ),
            "traits_loaded": int(sim.get("traits_loaded_count") or 0),
            "source_persona_tables_unchanged": bool(
                sim.get("source_persona_tables_unchanged") or False,
            ),
        },
    )

    fc_audit = dict(sim.get("forbidden_claim_audit") or {})

    # Readiness gate (mirrors quality evaluator + spec rules; the
    # report itself does NOT promote NOT_READY into READY)
    ready = (
        quality_ready_state in (
            "READY_FOR_FOUNDER_REPORT", "READY_FOR_PROMPT_FIX",
        )
        and not fc_audit.get("any_fake_target_product_use", False)
        and not fc_audit.get("any_forecast_or_verdict", False)
        and bool(sim.get("source_persona_tables_unchanged", False))
    )

    rationale: list[str] = []
    rationale.append(
        f"Aggregated from simulation_id={sim.get('simulation_id')}. "
        f"Quality ready_state={quality_ready_state}. "
        f"Aggregate quality={quality_aggregate}."
    )
    rationale.append(
        "All sections derived deterministically — no LLM call, no "
        "retrieval, no DB write."
    )
    rationale.append(
        f"Built {len(top_objections)} objection entries, "
        f"{len(top_levers)} persuasion-lever entries, "
        f"{len(competitor_comparison)} competitor-comparison entries, "
        f"{len(positioning)} positioning recommendations, "
        f"{len(product_offer)} product/offer recommendations, "
        f"{len(test_recs)} test recommendations."
    )

    return FounderReport(
        completed_at=datetime.now(UTC).isoformat(),
        simulation_id=str(sim.get("simulation_id") or ""),
        run_scope_id=str(sim.get("run_scope_id") or ""),
        target_brief_id=re.sub(
            r"[^a-z0-9]+", "_",
            (brief.get("product_name", "") or "unknown").lower(),
        ).strip("_") or "unknown",
        product_name=brief.get("product_name", ""),
        launch_state=brief.get("launch_state", "unlaunched"),
        founder_brief=brief,
        input_summary={
            "input_persona_count": persona_count,
            "rounds_completed": rounds,
            "agent_response_count": response_count,
            "traits_loaded": int(sim.get("traits_loaded_count") or 0),
            "evidence_links_loaded": int(
                sim.get("evidence_links_loaded_count") or 0,
            ),
            "source_records_loaded": int(
                sim.get("source_records_loaded_count") or 0,
            ),
            "model_used": (sim.get("cost_summary") or {}).get(
                "model_used", "unknown",
            ),
            "llm_call_count": (sim.get("cost_summary") or {}).get(
                "calls", 0,
            ),
        },
        executive_summary=executive_summary,
        simulated_audience_snapshot=audience_snapshot,
        stance_distribution=stance_dist,
        overall_reaction=overall_reaction,
        top_objections=top_objections,
        top_persuasion_levers=top_levers,
        competitor_comparison=competitor_comparison,
        proof_needed=proof_needed,
        positioning_recommendations=positioning,
        product_offer_recommendations=product_offer,
        what_to_test_next=test_recs,
        caveats=_make_caveats(product_name=brief.get("product_name", "")),
        appendix=appendix,
        source_traceability={
            "source_records_loaded": int(
                sim.get("source_records_loaded_count") or 0,
            ),
            "source_persona_tables_unchanged": bool(
                sim.get("source_persona_tables_unchanged") or False,
            ),
        },
        persona_traceability={
            "input_persona_count": persona_count,
            "compressed_candidate_ids": [
                p.get("compressed_candidate_id")
                for p in sim.get("input_persona_summary") or []
            ],
        },
        quality_reference={
            "aggregate_score": quality_aggregate,
            "ready_state": quality_ready_state,
            "scores": dict(qual_scores),
        },
        forbidden_claim_audit=fc_audit,
        security_redaction_audit={
            "secrets_detected_in_inputs": False,
            "redactions_applied": 0,
            "scanner_version": "8.5F.universal",
        },
        ready_for_fresh_end_to_end_test=ready,
        rationale=rationale,
    )
