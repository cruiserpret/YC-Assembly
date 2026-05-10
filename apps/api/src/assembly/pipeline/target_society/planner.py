"""Phase 8.2G — deterministic target-society planner.

Given a `ProductBriefInput`, returns a fully-populated
`TargetSocietyPlan`. The planner is deterministic-only:

  1. detect a `ProductFamily` via keyword classifier
  2. instantiate the family's baseline stakeholder template
  3. augment with competitor-specific categories
  4. augment with geography hooks (when geography is provided)
  5. detect sensitive-attribute markers and tag categories +
     emit warnings
  6. build per-category source query plan
  7. build persona retrieval plan
  8. build coverage requirements + readiness gates
  9. build expected-outputs section
 10. assemble warnings list

The planner NEVER calls an LLM, NEVER calls the network, NEVER writes
to the DB. Drift tests enforce.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from assembly.pipeline.target_society.constants import (
    B2B_SAAS_TEMPLATE,
    COMMERCE_PLATFORM_TEMPLATE,
    CONSUMER_ELECTRONICS_TEMPLATE,
    CONSUMER_PACKAGED_GOOD_TEMPLATE,
    DEFAULT_GENERAL_TEMPLATE,
    FAMILY_DETECTION_KEYWORDS,
    FAMILY_TEMPLATES,
    FINANCIAL_PRODUCT_TEMPLATE,
    ProductFamily,
    SENSITIVE_TARGETING_KEYWORDS,
    WARNING_LLM_SIMULATION_LIMITATION,
    WARNING_MISSING_COMPETITORS,
    WARNING_MISSING_GEOGRAPHY,
    WARNING_MISSING_PRICE,
    WARNING_PROTECTED_ATTRIBUTE_INFERENCE_FORBIDDEN,
    WARNING_PUBLIC_DATA_SKEW,
    WARNING_SENSITIVE_TARGETING_CAVEAT,
    WARNING_THIN_EVIDENCE_RISK,
    WarningSeverity,
)
from assembly.pipeline.target_society.coverage import (
    build_coverage_requirements,
    build_readiness_gates,
)
from assembly.pipeline.target_society.query_plan import (
    build_source_query_plan_for_category,
)
from assembly.pipeline.target_society.schemas import (
    ExpectedOutputs,
    InterpretedBrief,
    PersonaRetrievalPlan,
    ProductBriefInput,
    SocietyPlanWarning,
    SourceQueryPlan,
    StakeholderCategory,
    TargetSocietyPlan,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_target_society_plan(brief: ProductBriefInput) -> TargetSocietyPlan:
    """Deterministic planner. Returns a fully-populated plan.

    Phase 8.4A.2: when the brief looks like an unlaunched / market-
    entry product test (per `looks_like_market_entry_brief`), the
    stakeholder categories are generated dynamically from generic
    primitives (competitors / substitutes / use-cases / objections /
    buyer-types / geography) rather than from a hardcoded product-
    family template. The classic CPG / SaaS / financial templates
    still serve launched-product briefs (Amboras-style).
    """
    from assembly.pipeline.target_society.dynamic_market_entry_planner import (
        build_dynamic_market_entry_categories,
        looks_like_market_entry_brief,
    )

    family = detect_product_family(brief)
    sensitive_markers = detect_sensitive_markers(brief)
    is_market_entry = looks_like_market_entry_brief(brief)

    interpreted = _interpret_brief(brief, family, sensitive_markers)

    if is_market_entry:
        categories = build_dynamic_market_entry_categories(brief)
    else:
        categories = _build_stakeholder_categories(
            brief=brief, family=family, sensitive_markers=sensitive_markers,
        )

    source_query_plans = [
        build_source_query_plan_for_category(category=c, brief=brief)
        for c in categories
    ]

    persona_retrieval = _build_persona_retrieval_plan(brief, family)
    coverage = build_coverage_requirements(
        brief=brief, family=family, categories=categories,
        is_market_entry=is_market_entry,
    )
    gates = build_readiness_gates(
        brief=brief, family=family, categories=categories,
    )
    expected = _build_expected_outputs(
        brief=brief, family=family, categories=categories,
    )
    warnings = _build_warnings(
        brief=brief, family=family,
        sensitive_markers=sensitive_markers,
        categories=categories,
    )

    # Phase 8.2J — derive plan-aware weighted-scorer weights from the
    # brief shape (has_competitors / has_geography / simulation goal).
    # The audience-retrieval scorer reads this field; the weight
    # vector is normalized to sum 8.0 so the threshold (27/36) keeps
    # the same proportional meaning (67.5% / 90% of max-40).
    from assembly.pipeline.target_society.constants import SimulationGoal as _SG
    from assembly.pipeline.audience_retrieval.weights import (
        derive_scorer_weights_for_plan,
    )
    scorer_weights = derive_scorer_weights_for_plan(
        has_competitors=bool(brief.competitors),
        has_geography=bool(brief.geography),
        simulation_goal_is_price_test=(brief.simulation_goal is _SG.TEST_PRICE),
        is_market_entry=is_market_entry,
    )

    return TargetSocietyPlan(
        interpreted_brief=interpreted,
        stakeholder_categories=categories,
        source_query_plan=source_query_plans,
        persona_retrieval_plan=persona_retrieval,
        coverage_requirements=coverage,
        simulation_readiness_gates=gates,
        expected_outputs=expected,
        warnings_and_limitations=warnings,
        scorer_weights=scorer_weights,
    )


# ---------------------------------------------------------------------------
# Family detection
# ---------------------------------------------------------------------------


def detect_product_family(brief: ProductBriefInput) -> ProductFamily:
    """Classify a brief into a product family by keyword.

    Returns the FIRST family whose keyword set has a hit in the brief
    text. Order in `FAMILY_DETECTION_KEYWORDS` is intentional —
    more-specific families come first.
    """
    blob = _brief_text_blob(brief).lower()
    for family, keywords in FAMILY_DETECTION_KEYWORDS.items():
        for k in keywords:
            if k in blob:
                return family
    return ProductFamily.DEFAULT_GENERAL


def _brief_text_blob(brief: ProductBriefInput) -> str:
    parts = [
        brief.product_name,
        brief.product_type or "",
        brief.product_description,
        brief.price_or_price_structure or "",
        " ".join(brief.competitors),
        brief.target_market_or_society or "",
        brief.geography or "",
        brief.intended_user_or_buyer or "",
        brief.extra_context or "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sensitive-attribute markers
# ---------------------------------------------------------------------------


def detect_sensitive_markers(brief: ProductBriefInput) -> tuple[str, ...]:
    """Return every sensitive-attribute keyword that appears in the
    brief text. Used to tag categories + emit
    SENSITIVE_TARGETING_CAVEAT warnings."""
    blob = _brief_text_blob(brief).lower()
    found: list[str] = []
    for k in SENSITIVE_TARGETING_KEYWORDS:
        if k in blob:
            found.append(k)
    return tuple(found)


# ---------------------------------------------------------------------------
# Interpreted brief
# ---------------------------------------------------------------------------


def _interpret_brief(
    brief: ProductBriefInput,
    family: ProductFamily,
    sensitive_markers: tuple[str, ...],
) -> InterpretedBrief:
    missing: list[str] = []
    if not brief.competitors:
        missing.append("competitors")
    if not brief.geography:
        missing.append("geography")
    if not brief.price_or_price_structure:
        missing.append("price_or_price_structure")
    if not brief.target_market_or_society:
        missing.append("target_market_or_society")
    if not brief.intended_user_or_buyer:
        missing.append("intended_user_or_buyer")

    assumptions: list[str] = []
    if not brief.target_market_or_society:
        assumptions.append(
            "No explicit target market provided; planner inferred it "
            f"from product description + family={family.value}."
        )
    if not brief.competitors:
        assumptions.append(
            "No competitors provided; planner used generic alternative "
            "categories (current alternative, do nothing)."
        )
    if sensitive_markers:
        assumptions.append(
            "Sensitive / protected-attribute markers detected; categories "
            "tagged with compliance caveats. Individual-persona "
            "inference of protected attributes is forbidden."
        )

    product_summary = _short_summary(
        f"{brief.product_name}: {brief.product_description}"
    )

    target_market_interpretation = (
        brief.target_market_or_society
        or brief.intended_user_or_buyer
        or _default_target_market_text(family)
    )

    competitor_interpretation = (
        ", ".join(brief.competitors)
        if brief.competitors
        else "no explicit competitors provided; alternatives inferred"
    )

    return InterpretedBrief(
        product_summary=product_summary,
        target_market_interpretation=target_market_interpretation[:2000],
        competitor_interpretation=competitor_interpretation[:2000],
        price_context=brief.price_or_price_structure,
        geography_context=brief.geography,
        detected_product_family=family,
        missing_inputs=missing,
        assumptions=assumptions,
    )


def _short_summary(s: str) -> str:
    # Collapse whitespace and trim to ~600 chars.
    return re.sub(r"\s+", " ", s).strip()[:1800]


def _default_target_market_text(family: ProductFamily) -> str:
    return {
        ProductFamily.COMMERCE_PLATFORM_OR_TOOLING:
            "online merchants and e-commerce founders",
        ProductFamily.CONSUMER_PACKAGED_GOOD:
            "general consumer-packaged-good shoppers",
        ProductFamily.CONSUMER_ELECTRONICS:
            "current-product owners and competitor-platform users",
        ProductFamily.FINANCIAL_PRODUCT:
            "buyers in the relevant financial-product category",
        ProductFamily.B2B_SAAS:
            "decision-makers and end-users at the target customer companies",
        ProductFamily.DEFAULT_GENERAL:
            "buyers, rejectors, and competitor-product users for this category",
    }[family]


# ---------------------------------------------------------------------------
# Stakeholder categories
# ---------------------------------------------------------------------------


def _build_stakeholder_categories(
    *,
    brief: ProductBriefInput,
    family: ProductFamily,
    sensitive_markers: tuple[str, ...],
) -> list[StakeholderCategory]:
    """Instantiate the family's baseline template and augment it with
    competitor-specific and geography-specific categories.

    Sensitive markers attach a compliance note to every category.
    """
    template = FAMILY_TEMPLATES[family]
    categories: list[StakeholderCategory] = []
    for entry in template:
        d = deepcopy(dict(entry))
        # Pop the internal flag — not part of the schema.
        sensitivity_default = d.pop("_sensitivity_default", False)
        if sensitive_markers:
            d["sensitivity_or_compliance_notes"] = (
                "Sensitive / protected-attribute targeting detected in brief "
                f"(markers: {sorted(sensitive_markers)}). Keep this "
                "category broad; never infer protected attributes for "
                "individual personas."
            )
        elif sensitivity_default:
            d["sensitivity_or_compliance_notes"] = (
                "Compliance-conscious framing — keep individual personas "
                "broad; never infer religion / regulatory-eligibility "
                "for a specific person."
            )
        categories.append(StakeholderCategory(**d))

    # Augment with competitor-specific categories.
    competitor_cats = _build_competitor_categories(brief, family)
    categories.extend(competitor_cats)

    # Augment with geography category (only if geography is provided).
    geo_cat = _build_geography_category(brief, family, sensitive_markers)
    if geo_cat is not None:
        categories.append(geo_cat)

    # De-duplicate by category_key, preserving first occurrence.
    seen: set[str] = set()
    deduped: list[StakeholderCategory] = []
    for c in categories:
        if c.category_key in seen:
            continue
        seen.add(c.category_key)
        deduped.append(c)
    return deduped


_KEY_SAFE_RE = re.compile(r"[^a-z0-9_]+")


def _safe_key(s: str, prefix: str) -> str:
    base = _KEY_SAFE_RE.sub("_", s.lower()).strip("_")[:40]
    base = base or "unknown"
    return f"{prefix}_{base}"[:64]


def _build_competitor_categories(
    brief: ProductBriefInput, family: ProductFamily,
) -> list[StakeholderCategory]:
    """For each named competitor, emit a `current_alternative_<name>`
    stakeholder category. We cap at 3 to avoid an explosion of
    competitor-specific categories — operators can re-run with a
    different competitor focus if needed."""
    out: list[StakeholderCategory] = []
    for comp in brief.competitors[:3]:
        if not comp or not comp.strip():
            continue
        key = _safe_key(comp, "current_alternative")
        out.append(StakeholderCategory(
            category_key=key,
            display_name=f"Current {comp} user",
            description=(
                f"Buyers / users currently using {comp} as their incumbent "
                "alternative; their switching-cost and trust-gap voice is "
                "required to test the product's competitor-replacement story."
            ),
            why_relevant=(
                f"Direct head-to-head test of the product against {comp}."
            ),
            likely_pains=[
                f"limitations of {comp}",
                "switching cost from incumbent",
            ],
            likely_objections=[
                f"why leave {comp}",
                "switching cost too high",
            ],
            likely_current_alternatives=[comp],
            evidence_needed=[
                f"first-person {comp} user complaints",
                f"public {comp} review or comparison thread",
            ],
            source_query_themes=[
                f"{comp} review",
                f"{comp} complaints",
                f"switch from {comp}",
            ],
            inclusion_signals=[
                f"self-described {comp} user",
                f"explicit {comp} mention",
            ],
            exclusion_signals=[
                f"{comp} marketing voice",
            ],
            minimum_persona_target_tiny=1,
            minimum_persona_target_small=2,
            minimum_persona_target_serious=4,
            priority="medium",
        ))
    return out


def _build_geography_category(
    brief: ProductBriefInput,
    family: ProductFamily,
    sensitive_markers: tuple[str, ...],
) -> StakeholderCategory | None:
    if not brief.geography:
        return None
    geo = brief.geography.strip()
    if not geo:
        return None
    note = None
    if sensitive_markers:
        note = (
            f"Geography-tied + sensitive targeting detected (region={geo}). "
            "Keep individual-persona attribute claims broad; avoid "
            "country-of-residence + protected-attribute combinations."
        )
    return StakeholderCategory(
        category_key=_safe_key(geo, "geography"),
        display_name=f"{geo} regional buyer",
        description=(
            f"Buyers / users explicitly tied to {geo} who provide regional "
            "context (regulation, language, channel availability)."
        ),
        why_relevant=(
            f"Geography-specific voice anchors the simulation in {geo}."
        ),
        likely_pains=[
            "regional channel availability",
            "regional pricing",
        ],
        likely_objections=[
            f"is this product available in {geo}",
        ],
        likely_current_alternatives=[
            f"regional incumbent in {geo}",
        ],
        evidence_needed=[
            f"public discussion explicitly mentioning {geo}",
        ],
        source_query_themes=[
            f"{geo} consumer review",
            f"{geo} buyer forum",
        ],
        inclusion_signals=[
            f"explicit {geo} mention",
        ],
        exclusion_signals=[
            "geography-agnostic generic content",
        ],
        minimum_persona_target_tiny=1,
        minimum_persona_target_small=2,
        minimum_persona_target_serious=4,
        sensitivity_or_compliance_notes=note,
        priority="medium",
    )


# ---------------------------------------------------------------------------
# Persona retrieval plan
# ---------------------------------------------------------------------------


def _build_persona_retrieval_plan(
    brief: ProductBriefInput, family: ProductFamily,
) -> PersonaRetrievalPlan:
    return PersonaRetrievalPlan(
        trait_fields_to_match=[
            "role_or_context",
            "objection_patterns",
            "current_alternatives",
            "price_sensitivity",
            "trust_triggers",
            "interests",
            "buying_constraints",
            "communication_style",
        ],
        relevance_signals=[
            "role_or_context overlap with target stakeholder category",
            "current_alternatives overlap with brief competitors",
            "objection_patterns overlap with category likely_objections",
            "price_sensitivity overlap with brief price context",
            "geography_broad overlap with brief geography (when present)",
        ],
        exclusion_rules=[
            "exclude personas whose source_records are entirely "
            "context_only marketing pages",
            "exclude personas with zero direct/inferred traits",
            "exclude personas whose value strings contain identity "
            "markers (post-redaction regression)",
            "exclude personas matched only by a sensitive / protected "
            "attribute heuristic",
        ],
        minimum_relevance_threshold=27,
        use_existing_personas_when=[
            "an existing persona scores >= relevant on the Phase 8.2F.7 "
            "rubric AND its trait fields overlap a target stakeholder "
            "category for THIS brief",
        ],
        trigger_topup_when=[
            "any high-priority stakeholder category has zero matching "
            "existing personas",
            "fewer than tiny_minimum_personas existing personas score "
            ">= relevant",
            "the brief's target geography is not represented",
            "the brief's competitors are not represented in any "
            "current_alternative trait",
        ],
    )


# ---------------------------------------------------------------------------
# Expected outputs
# ---------------------------------------------------------------------------


def _build_expected_outputs(
    *,
    brief: ProductBriefInput,
    family: ProductFamily,
    categories: list[StakeholderCategory],
) -> ExpectedOutputs:
    answerable = [
        f"What stance distribution would emerge across {len(categories)} "
        f"stakeholder categories for {brief.product_name}?",
        "Which objections does the product fail to address?",
        "Which trust triggers would be required to convert skeptics?",
        "What rejection language would surface from price-sensitive segments?",
    ]
    if brief.competitors:
        answerable.append(
            "Which competitor-replacement stories carry conviction "
            "across the simulated population?"
        )
    if brief.geography:
        answerable.append(
            f"How does {brief.geography} regional voice differ from "
            "the dominant pattern?"
        )

    unanswerable = [
        "Specific revenue / conversion / market-share forecasts.",
        "Real-individual-level prediction of behavior.",
        "Build / kill / pivot product verdicts.",
        "Market-success probability claims.",
    ]
    if family is ProductFamily.FINANCIAL_PRODUCT:
        unanswerable.append(
            "Regulatory eligibility for any specific person."
        )

    return ExpectedOutputs(
        answerable_questions=answerable,
        unanswerable_questions=unanswerable,
        expected_report_sections=[
            "stance_distribution",
            "top_objections",
            "top_persuasion_drivers",
            "current_alternative_breakdown",
            "price_sensitivity_breakdown",
            "trust_triggers",
            "rejection_segments",
            "missing_evidence_warnings",
        ],
        expected_society_map_categories=[
            c.category_key for c in categories
        ],
    )


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def _build_warnings(
    *,
    brief: ProductBriefInput,
    family: ProductFamily,
    sensitive_markers: tuple[str, ...],
    categories: list[StakeholderCategory],
) -> list[SocietyPlanWarning]:
    out: list[SocietyPlanWarning] = []

    if not brief.geography:
        out.append(SocietyPlanWarning(
            code=WARNING_MISSING_GEOGRAPHY,
            message=(
                "No geography provided; planner used a geography-agnostic "
                "category set. Regional reaction differences cannot be "
                "tested."
            ),
            severity=WarningSeverity.WARNING,
        ))
    if not brief.price_or_price_structure:
        out.append(SocietyPlanWarning(
            code=WARNING_MISSING_PRICE,
            message=(
                "No price / price structure provided; price-sensitivity "
                "tests will rely on inferred-from-description signal only."
            ),
            severity=WarningSeverity.WARNING,
        ))
    if not brief.competitors:
        out.append(SocietyPlanWarning(
            code=WARNING_MISSING_COMPETITORS,
            message=(
                "No competitors provided; planner could not generate "
                "competitor-specific stakeholder categories. Switching-"
                "cost analysis is limited to generic alternatives."
            ),
            severity=WarningSeverity.WARNING,
        ))
    if sensitive_markers:
        out.append(SocietyPlanWarning(
            code=WARNING_SENSITIVE_TARGETING_CAVEAT,
            message=(
                f"Sensitive / protected-attribute markers detected in brief "
                f"(markers: {sorted(sensitive_markers)}). Categories tagged "
                "with compliance notes. Individual-persona inference of "
                "protected attributes is forbidden by the framework."
            ),
            severity=WarningSeverity.CAVEAT,
        ))
        out.append(SocietyPlanWarning(
            code=WARNING_PROTECTED_ATTRIBUTE_INFERENCE_FORBIDDEN,
            message=(
                "Downstream code MUST NOT infer religion, race, ethnicity, "
                "sexuality, health, immigration status, or any other "
                "protected attribute for an individual persona. Only the "
                "explicitly-stated source claim may be used."
            ),
            severity=WarningSeverity.BLOCKER,
        ))
    # Always-on caveats
    out.append(SocietyPlanWarning(
        code=WARNING_PUBLIC_DATA_SKEW,
        message=(
            "Public-web data over-represents Western / English-language "
            "voices and active forum participants. Treat the simulation "
            "as a directional signal, not a representative survey."
        ),
        severity=WarningSeverity.CAVEAT,
    ))
    out.append(SocietyPlanWarning(
        code=WARNING_LLM_SIMULATION_LIMITATION,
        message=(
            "Synthetic personas + LLM-driven debate produce a structured "
            "directional signal, not a probabilistic forecast. Any "
            "downstream report must avoid prediction / verdict language."
        ),
        severity=WarningSeverity.CAVEAT,
    ))
    out.append(SocietyPlanWarning(
        code=WARNING_THIN_EVIDENCE_RISK,
        message=(
            "If category-level evidence is thin after retrieval, the "
            "simulation must surface that thinness explicitly rather "
            "than compensating with mechanism priors."
        ),
        severity=WarningSeverity.CAVEAT,
    ))

    return out
