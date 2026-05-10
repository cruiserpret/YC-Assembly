"""Phase 8.5C.1 — deterministic dynamic ingestion-policy planner.

`generate_ingestion_policy(...)` returns an `IngestionPolicy` derived
from:

  * the founder brief (ProductBriefForPlanning)
  * the previously-generated EvidenceAnchorPlan
  * the candidate pool (HIGH/MEDIUM-confidence rows from the dynamic
    Amazon scorer)
  * the source family
  * the product launch state
  * the current DB baseline counts
  * the operator-supplied max_insert_cap

`decide_candidates(...)` runs the four required scanners on every
candidate, applies the dynamic selection + rejection rules, and
returns a list of `CandidateDecision` (one per candidate) plus the
final SELECTED ranking.

Pure functions. Same inputs → same outputs. No LLM. No network.
The only async surface is the optional `check_duplicate_content_hash`
DB call (READ-ONLY).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from statistics import median, quantiles
from typing import Any

from assembly.sources.evidence_anchor_planner import (
    EvidenceAnchorPlan, ProductBriefForPlanning,
)
from assembly.sources.ingestion_policy.constants import (
    REQUIRED_SCANNERS, UNIVERSAL_GUARDRAILS,
)
from assembly.sources.ingestion_policy.scanners import (
    check_duplicate_content_hash, compute_content_hash,
    scan_dataset_compliance, scan_pii, scan_unlaunched_fake_buyer,
)
from assembly.sources.ingestion_policy.schemas import (
    CandidateDecision, CandidateRow, IngestionPolicy,
    PlannedSourceRecordPreview, PoolSummary, ProductLaunchState,
    RejectionRule, SelectionRule,
)


# ---------------------------------------------------------------------------
# Pool summarization
# ---------------------------------------------------------------------------


def _summarize_pool(
    candidates: list[CandidateRow],
    plan: EvidenceAnchorPlan,
) -> PoolSummary:
    """Compute aggregate pool stats — used by the planner to derive
    pool-shape-aware selection rules."""
    if not candidates:
        return PoolSummary(
            total_candidates=0,
            candidates_by_category={}, candidates_by_confidence={},
            score_p25=0.0, score_p50=0.0, score_p75=0.0, score_max=0.0,
            distinct_competitor_brands_seen=[],
            distinct_substitute_terms_seen=[],
            distinct_metadata_main_categories_seen=[],
        )
    cat_counts: Counter = Counter(c.category for c in candidates)
    conf_counts: Counter = Counter(c.confidence for c in candidates)
    scores = sorted(c.score for c in candidates)
    if len(scores) >= 4:
        q = quantiles(scores, n=4)
        p25, p50, p75 = q[0], q[1], q[2]
    else:
        p25 = scores[0]
        p50 = float(median(scores))
        p75 = scores[-1]
    competitor_set = set(c.lower() for c in plan.competitor_anchor_terms)
    substitute_set = set(s.lower() for s in plan.substitute_anchor_terms)
    seen_competitors: set[str] = set()
    seen_substitutes: set[str] = set()
    seen_main_categories: set[str] = set()
    for c in candidates:
        if c.metadata_main_category:
            seen_main_categories.add(c.metadata_main_category)
        for term in c.matched_terms:
            t_low = term.lower()
            for comp in competitor_set:
                if comp in t_low:
                    seen_competitors.add(comp)
            for sub in substitute_set:
                if sub in t_low:
                    seen_substitutes.add(sub)
    return PoolSummary(
        total_candidates=len(candidates),
        candidates_by_category=dict(cat_counts),
        candidates_by_confidence=dict(conf_counts),
        score_p25=float(p25), score_p50=float(p50), score_p75=float(p75),
        score_max=float(max(scores)),
        distinct_competitor_brands_seen=sorted(seen_competitors),
        distinct_substitute_terms_seen=sorted(seen_substitutes),
        distinct_metadata_main_categories_seen=sorted(seen_main_categories),
    )


# ---------------------------------------------------------------------------
# Dynamic dimension derivation
# ---------------------------------------------------------------------------


def _derive_selection_objectives(
    brief: ProductBriefForPlanning,
    plan: EvidenceAnchorPlan,
    pool: PoolSummary,
) -> list[str]:
    """Human-readable selection objectives derived from brief + plan +
    pool. None are product-category-specific code paths — they all
    reference plan.* fields and pool.* fields."""
    objectives: list[str] = []
    if plan.competitor_anchor_terms:
        objectives.append(
            f"prefer evidence that names a brief-supplied competitor "
            f"({', '.join(brief.competitors[:5])})"
        )
    if plan.substitute_anchor_terms:
        objectives.append(
            f"prefer evidence that names a brief-derived substitute "
            f"({', '.join(plan.substitute_anchor_terms[:4])})"
        )
    multi_word_positives = [
        t for t in plan.positive_anchor_terms if " " in t.strip()
    ]
    if multi_word_positives:
        objectives.append(
            f"prefer evidence with multi-word product-type anchor "
            f"({', '.join(multi_word_positives[:3])}) over single-token "
            f"matches"
        )
    if plan.objection_anchor_terms:
        objectives.append(
            f"prefer evidence that surfaces brief-derived objection "
            f"terms ({', '.join(plan.objection_anchor_terms[:5])})"
        )
    if plan.use_case_anchor_terms:
        objectives.append(
            f"prefer evidence whose use-case overlaps a brief-supplied "
            f"customer / use case "
            f"({', '.join(plan.use_case_anchor_terms[:4])})"
        )
    if pool.candidates_by_category and len(pool.candidates_by_category) > 1:
        objectives.append(
            "balance selection across source-categories to avoid "
            "single-category over-representation"
        )
    objectives.append(
        "prefer verified_purchase=true reviews when available "
        "(stronger trust signal)"
    )
    objectives.append(
        "exclude candidates whose only signal is generic-modifier or "
        "wrong-context match"
    )
    return objectives


def _derive_evidence_quality_dimensions(
    plan: EvidenceAnchorPlan,
) -> list[str]:
    """The dimensions by which evidence is judged. Pulled from plan."""
    dims = [
        "brief-derived multi-word positive-anchor match (strong)",
        "brief-derived single-token positive-anchor match (weak)",
        "named-competitor match (intended sense, ambiguity-resolved)",
        "use-case anchor co-occurrence",
        "objection anchor co-occurrence",
        "metadata-category alignment with brief's product_type",
        "metadata-title contains brief-derived multi-word phrase",
        "score percentile vs candidate pool",
    ]
    if plan.ambiguous_entities:
        dims.append(
            "ambiguity classification: intended-context vs wrong-context"
        )
    return dims


def _derive_persona_value_dimensions(
    brief: ProductBriefForPlanning,
    plan: EvidenceAnchorPlan,
) -> list[str]:
    """Dimensions describing what persona ROLE a candidate could
    support during future Phase 8.5D persona construction. Generic —
    derived purely from brief + plan, not product-category-coded."""
    dims: list[str] = []
    for c in brief.competitors:
        dims.append(f"competitor_user_{c.lower().replace(' ', '_')}")
    for s in plan.substitute_anchor_terms[:5]:
        norm = s.lower().replace(" ", "_").replace("-", "_")
        dims.append(f"substitute_user_{norm}")
    for o in plan.objection_anchor_terms[:5]:
        norm = o.lower().replace(" ", "_").replace("-", "_")
        dims.append(f"objection_voice_{norm}")
    dims.extend([
        "category_rejecter",
        "safety_skeptic",
        "price_skeptic",
        "convenience_focused_buyer",
        "flavor_focused_buyer",
        "performance_use_case_buyer",
        "health_conscious_buyer",
    ])
    return dims


def _derive_selection_rules(
    plan: EvidenceAnchorPlan,
    pool: PoolSummary,
    max_cap: int,
) -> list[SelectionRule]:
    """Concrete, weight-tagged selection rules. The downstream
    `decide_candidates` reads these to score candidates."""
    rules: list[SelectionRule] = []
    rules.append(SelectionRule(
        rule_id="confidence_high_only",
        description="only HIGH_CONFIDENCE candidates qualify for selection",
        derived_from="evidence_anchor_plan", weight=5,
    ))
    rules.append(SelectionRule(
        rule_id="score_top_quartile_preferred",
        description=(
            f"prefer score >= pool.p75={pool.score_p75:.1f} when more "
            f"than {max_cap} candidates remain after scanners"
        ),
        derived_from="candidate_pool_distribution", weight=4,
    ))
    if pool.candidates_by_category and len(pool.candidates_by_category) > 1:
        per_cat_cap = max(1, max_cap // max(1, len(pool.candidates_by_category)))
        rules.append(SelectionRule(
            rule_id="per_category_diversity_cap",
            description=(
                f"cap selections per source-category at "
                f"{per_cat_cap} to ensure {len(pool.candidates_by_category)}"
                "-category coverage"
            ),
            derived_from="candidate_pool_distribution", weight=4,
        ))
    rules.append(SelectionRule(
        rule_id="multi_word_anchor_required",
        description=(
            "require at least one multi-word positive-anchor match in "
            "matched_terms (single-token-only matches are too noisy)"
        ),
        derived_from="evidence_anchor_plan", weight=4,
    ))
    if plan.competitor_anchor_terms:
        rules.append(SelectionRule(
            rule_id="competitor_coverage_preferred",
            description=(
                "prefer candidates that name at least one brief-supplied "
                "competitor — these support competitor-user persona "
                "construction in Phase 8.5D"
            ),
            derived_from="founder_brief", weight=3,
        ))
    rules.append(SelectionRule(
        rule_id="verified_purchase_tiebreaker",
        description=(
            "when ranks tie, prefer verified_purchase=true "
            "(stronger trust signal)"
        ),
        derived_from="universal_safety", weight=2,
    ))
    return rules


def _derive_rejection_rules(
    plan: EvidenceAnchorPlan,
) -> list[RejectionRule]:
    """Concrete rejection rules. Universal-safety rules are flagged
    `is_universal=True`; product-specific rules derived from the plan
    get `is_universal=False`."""
    rules: list[RejectionRule] = []
    # Universal safety
    rules.append(RejectionRule(
        rule_id="reject_pii_hit",
        description="reject any candidate whose text contains email, phone, external URL, raw @handle, or image URL",
        derived_from="universal_safety", is_universal=True,
    ))
    rules.append(RejectionRule(
        rule_id="reject_fake_buyer_for_unlaunched",
        description=(
            "for unlaunched products, reject any text that fabricates "
            "a buying / customer / loyalty / direct-experience "
            "relationship to the unlaunched product"
        ),
        derived_from="universal_safety", is_universal=True,
    ))
    rules.append(RejectionRule(
        rule_id="reject_dataset_non_compliance",
        description=(
            "reject any candidate whose planned source_record fails "
            "the dataset-compliance scan (wrong tag, .com URL, etc.)"
        ),
        derived_from="universal_safety", is_universal=True,
    ))
    rules.append(RejectionRule(
        rule_id="reject_duplicate_content_hash",
        description=(
            "reject any candidate whose normalized content_hash "
            "already exists in source_records (no-overwrite)"
        ),
        derived_from="universal_safety", is_universal=True,
    ))
    # Plan-derived
    rules.append(RejectionRule(
        rule_id="reject_no_multi_word_anchor",
        description=(
            "reject candidates whose only positive matches are "
            "single-token (too noisy without a multi-word category "
            "phrase)"
        ),
        derived_from="evidence_anchor_plan", is_universal=False,
    ))
    rules.append(RejectionRule(
        rule_id="reject_below_high_confidence",
        description=(
            "reject MEDIUM / LOW / REJECTED candidates from the "
            "8.5B.1 dynamic-anchor scorer"
        ),
        derived_from="evidence_anchor_plan", is_universal=False,
    ))
    return rules


# ---------------------------------------------------------------------------
# Top-level policy factory
# ---------------------------------------------------------------------------


def _policy_id(
    brief: ProductBriefForPlanning,
    plan: EvidenceAnchorPlan,
    candidates: list[CandidateRow],
    source_family: str,
    max_cap: int,
) -> str:
    payload = json.dumps({
        "brief_id": plan.plan_id,
        "n": len(candidates),
        "first_ids": [c.candidate_id for c in candidates[:5]],
        "source_family": source_family,
        "max_cap": max_cap,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _source_record_shape_template(
    source_family: str,
    target_brief_id: str,
    plan_id: str,
    policy_id: str,
) -> dict[str, Any]:
    """The TEMPLATE for what every selected candidate's planned
    source_record looks like. Per-candidate fields are filled in
    `_build_planned_record_preview`."""
    return {
        "source_kind": f"{source_family}",
        "source_url_template": (
            f"local://{source_family}/<category>/<parent_asin>"
        ),
        "compliance_tag": "open_dataset",
        "captured_at_iso": "2023-09-01T00:00:00+00:00",
        "ingested_by_template": (
            f"assembly_phase_8_5c_{target_brief_id}_amazon_dynamic_"
            f"policy_bounded_ingest"
        ),
        "metadata_template_keys": [
            "target_brief", "source_dataset", "source_category",
            "parent_asin", "asin", "rating", "verified_purchase",
            "helpful_vote", "timestamp", "metadata_title",
            "metadata_store", "metadata_categories",
            "anchor_score", "anchor_confidence", "matched_terms",
            "evidence_anchor_plan_id", "ingestion_policy_id",
            "candidate_decision_rank", "persona_value_roles", "phase",
        ],
        "evidence_anchor_plan_id": plan_id,
        "ingestion_policy_id": policy_id,
        "language": "en",
        "user_handle_hash": None,
        "pii_redaction_status_planned": "planned_clean",
        "sensitive_scan_status_planned": "planned_clean",
    }


def generate_ingestion_policy(
    *,
    brief: ProductBriefForPlanning,
    evidence_anchor_plan: EvidenceAnchorPlan,
    candidate_pool: list[CandidateRow],
    source_family: str,
    product_launch_state: ProductLaunchState,
    db_baseline: dict[str, int],
    max_insert_cap: int,
    target_brief_id: str | None = None,
) -> IngestionPolicy:
    """Deterministic. Pure function over the inputs. No LLM, no
    network, no DB. Same inputs → same policy (modulo `generated_at`
    timestamp)."""
    plan = evidence_anchor_plan
    pool = _summarize_pool(candidate_pool, plan)
    target_brief_id = (
        target_brief_id
        or brief.product_name.lower().replace(" ", "_")
    )
    selection_objectives = _derive_selection_objectives(brief, plan, pool)
    quality_dims = _derive_evidence_quality_dimensions(plan)
    persona_dims = _derive_persona_value_dimensions(brief, plan)
    selection_rules = _derive_selection_rules(plan, pool, max_insert_cap)
    rejection_rules = _derive_rejection_rules(plan)
    pid = _policy_id(brief, plan, candidate_pool, source_family, max_insert_cap)
    shape = _source_record_shape_template(
        source_family=source_family,
        target_brief_id=target_brief_id,
        plan_id=plan.plan_id,
        policy_id=pid,
    )
    caveats = [
        "Policy is deterministic — derived from the founder brief, "
        "evidence-anchor plan, and candidate-pool distribution. No "
        "LLM, no network. Same inputs → same policy.",
        "Per-product selection LOGIC is generated dynamically; "
        "universal safety/compliance guardrails are hardcoded.",
        f"max_insert_cap={max_insert_cap} is a UNIVERSAL DB safety "
        "bound, not a product-specific relevance rule.",
        "Phase 8.5C.1 is dry-run only — no DB writes occur. The "
        "policy + per-candidate decisions are written to an audit "
        "JSON. Phase 8.5C.2 (separate operator approval) executes "
        "the planned inserts inside one bounded transaction.",
    ]
    if plan.ambiguous_entities:
        caveats.append(
            f"{len(plan.ambiguous_entities)} ambiguous competitor"
            "(s) flagged in the anchor plan; the scorer's wrong-"
            "context filter is treated as a hard rejection rule."
        )
    return IngestionPolicy(
        product_name=brief.product_name,
        target_brief_id=target_brief_id,
        source_family=source_family,
        product_launch_state=product_launch_state,
        evidence_anchor_plan_id=plan.plan_id,
        policy_id=pid,
        policy_generated_from="deterministic",
        candidate_pool_summary=pool,
        selection_objectives=selection_objectives,
        evidence_quality_dimensions=quality_dims,
        persona_construction_value_dimensions=persona_dims,
        dynamic_selection_rules=selection_rules,
        dynamic_rejection_rules=rejection_rules,
        universal_guardrails=list(UNIVERSAL_GUARDRAILS),
        max_insert_cap=max_insert_cap,
        required_scanners=list(REQUIRED_SCANNERS),
        source_record_shape=shape,
        caveats=caveats,
        generated_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Per-candidate decision
# ---------------------------------------------------------------------------


def _evidence_strength_label(
    candidate: CandidateRow, pool: PoolSummary,
) -> str:
    """Map a candidate's score to a label, relative to pool."""
    if candidate.score >= max(8, int(pool.score_p75)):
        return "very_strong"
    if candidate.score >= max(6, int(pool.score_p50)):
        return "strong"
    if candidate.score >= 3:
        return "moderate"
    return "weak"


def _persona_value_roles(
    candidate: CandidateRow,
    plan: EvidenceAnchorPlan,
) -> list[str]:
    """Infer what persona role(s) the candidate's evidence could
    support. Derived purely from candidate.matched_terms vs plan
    anchor lists — no per-product code path."""
    roles: list[str] = []
    matched_low = " ".join(t.lower() for t in candidate.matched_terms)
    text_low = (candidate.title + " " + candidate.text).lower()
    blob = matched_low + " " + text_low

    for c in plan.competitors:
        if c.lower() in blob:
            roles.append(
                f"competitor_user_{c.lower().replace(' ', '_').replace('-','')}"
            )
    for s in plan.substitute_anchor_terms[:6]:
        if s.lower() in blob:
            norm = s.lower().replace(" ", "_").replace("-", "_")
            roles.append(f"substitute_user_{norm}")
    for o in plan.objection_anchor_terms[:6]:
        if o.lower() in blob:
            norm = o.lower().replace(" ", "_").replace("-", "_")
            roles.append(f"objection_voice_{norm}")
    # Generic-persona-role inference is universal English-language
    # scaffolding (safety, price, flavor, performance, health). These
    # ARE universal patterns, not product-category-coded — every
    # consumer-product brief has these axes.
    if any(
        t in blob
        for t in ("safety", "recall", "side effect", "warning",
                  "doctor", "blood pressure")
    ):
        roles.append("safety_skeptic")
    if any(
        t in blob
        for t in ("$", "expensive", "cheap", "value", "overpriced")
    ):
        roles.append("price_skeptic")
    if any(
        t in blob for t in ("flavor", "flavour", "taste", "tastes",
                             "smell", "smells", "scent")
    ):
        roles.append("flavor_or_sensory_focused_buyer")
    if any(
        t in blob for t in ("workout", "gym", "endurance",
                            "performance", "athletic", "fitness")
    ):
        roles.append("performance_use_case_buyer")
    if any(
        t in blob
        for t in ("health", "natural", "clean ingredients",
                  "organic", "low sugar", "sugar-free")
    ):
        roles.append("health_conscious_buyer")
    return sorted(set(roles))


def _build_planned_record_preview(
    candidate: CandidateRow,
    policy: IngestionPolicy,
    content_hash: str,
    persona_roles: list[str],
    rank: int,
) -> PlannedSourceRecordPreview:
    """Construct the planned-source_record audit preview. NEVER
    inserted by 8.5C.1 — only serialized into the dry-run JSON."""
    content = (
        (candidate.title or "").strip() + "\n\n"
        + (candidate.text or "").strip()
    )
    metadata = {
        "target_brief": policy.target_brief_id,
        "source_dataset": "amazon_reviews_2023",
        "source_category": candidate.category,
        "parent_asin": candidate.parent_asin,
        "asin": candidate.asin,
        "rating": candidate.rating,
        "verified_purchase": candidate.verified_purchase,
        "helpful_vote": candidate.helpful_vote,
        "timestamp": candidate.timestamp,
        "metadata_title": candidate.metadata_title,
        "metadata_main_category": candidate.metadata_main_category,
        "metadata_categories": candidate.metadata_categories,
        "anchor_score": candidate.score,
        "anchor_confidence": candidate.confidence,
        "matched_terms": candidate.matched_terms,
        "evidence_anchor_plan_id": policy.evidence_anchor_plan_id,
        "ingestion_policy_id": policy.policy_id,
        "candidate_decision_rank": rank,
        "persona_value_roles": persona_roles,
        "phase": "8.5C.1_dry_run",
    }
    source_url = (
        f"local://{policy.source_family}/{candidate.category}/"
        f"{candidate.parent_asin or 'no_asin'}"
    )
    ingested_by = (
        f"assembly_phase_8_5c_{policy.target_brief_id}_amazon_"
        f"dynamic_policy_bounded_ingest"
    )
    return PlannedSourceRecordPreview(
        source_kind=policy.source_family,
        source_url=source_url,
        content_preview=content[:240],
        content_length=len(content),
        content_hash=content_hash,
        language="en",
        metadata=metadata,
        ingested_by=ingested_by,
        compliance_tag="open_dataset",
        captured_at="2023-09-01T00:00:00+00:00",
        pii_redaction_status="planned_clean",
        sensitive_scan_status="planned_clean",
        user_handle_hash=None,
    )


async def decide_candidates(
    *,
    candidates: list[CandidateRow],
    policy: IngestionPolicy,
    plan: EvidenceAnchorPlan,
    sessionmaker,  # for read-only duplicate check
    product_name: str,
    product_launch_state: ProductLaunchState,
) -> list[CandidateDecision]:
    """Run all 4 required scanners + apply selection / rejection
    rules. Returns a `CandidateDecision` per input candidate. Mutates
    nothing; the only DB interaction is read-only `SELECT count` for
    duplicate_check."""
    decisions: list[CandidateDecision] = []
    selected_running: list[CandidateDecision] = []
    selected_per_category: Counter = Counter()
    per_category_cap = 0
    if policy.candidate_pool_summary.candidates_by_category:
        per_category_cap = max(
            1,
            policy.max_insert_cap
            // max(1, len(policy.candidate_pool_summary.candidates_by_category)),
        )

    # Sort candidates: highest score first, then verified_purchase=True
    # tie-break, then candidate_id stable.
    sorted_candidates = sorted(
        candidates,
        key=lambda c: (
            -c.score,
            0 if c.verified_purchase else 1,
            c.candidate_id,
        ),
    )

    rank = 0
    for cand in sorted_candidates:
        full_text = f"{cand.title}\n\n{cand.text}"
        scanner_results: dict[str, list[str]] = {}
        # 1. PII
        pii = scan_pii(full_text)
        scanner_results["pii_scan"] = list(pii.issues)
        # 2. Unlaunched fake buyer
        fake = scan_unlaunched_fake_buyer(
            text=full_text, product_name=product_name,
        )
        scanner_results["unlaunched_fake_buyer_scan"] = list(fake.issues)
        # 3. Dataset compliance (against the planned record shape)
        content = (
            (cand.title or "").strip() + "\n\n"
            + (cand.text or "").strip()
        )
        content_hash = compute_content_hash(
            content=content, source_kind=policy.source_family,
        )
        planned_url = (
            f"local://{policy.source_family}/{cand.category}/"
            f"{cand.parent_asin or 'no_asin'}"
        )
        compliance_issues = scan_dataset_compliance(
            source_kind=policy.source_family,
            source_url=planned_url,
            compliance_tag="open_dataset",
            source_family=policy.source_family,
        )
        scanner_results["dataset_compliance_scan"] = compliance_issues
        # 4. Duplicate check (READ-ONLY)
        dup = await check_duplicate_content_hash(
            content_hash=content_hash, sessionmaker=sessionmaker,
        )
        duplicate_label = "duplicate" if dup else "unique"
        scanner_results["duplicate_check"] = (
            ["content_hash already present in source_records"]
            if dup else []
        )

        # ---- Apply rejection rules ----
        rejection_reasons: list[str] = []
        if pii.issues:
            rejection_reasons.append(
                f"reject_pii_hit: {pii.issues[0]}"
            )
        if fake.issues and product_launch_state == "unlaunched":
            rejection_reasons.append(
                f"reject_fake_buyer_for_unlaunched: {fake.issues[0]}"
            )
        if compliance_issues:
            rejection_reasons.append(
                f"reject_dataset_non_compliance: {compliance_issues[0]}"
            )
        if dup:
            rejection_reasons.append(
                "reject_duplicate_content_hash"
            )
        if cand.confidence != "high_confidence":
            rejection_reasons.append(
                f"reject_below_high_confidence: confidence={cand.confidence}"
            )
        # Strong-anchor required: either a multi-word positive anchor
        # OR a named-competitor / substitute hit (both are brief-derived
        # strong signals; the policy treats them as equivalents because
        # cross-product evidence shape varies — some categories surface
        # multi-word product-type phrases verbatim, others surface only
        # competitor brand names + single-token category words).
        has_multi_word_positive = any(
            m.startswith("positive:") and " " in m.split(":", 1)[1]
            for m in cand.matched_terms
        )
        has_named_competitor_or_substitute = any(
            (m.startswith("competitor:") and "(wrong-context)" not in m)
            or m.startswith("substitute:")
            for m in cand.matched_terms
        )
        if not (has_multi_word_positive or has_named_competitor_or_substitute):
            rejection_reasons.append(
                "reject_no_strong_anchor"
            )

        # ---- Build base decision attributes ----
        evidence_strength = _evidence_strength_label(
            cand, policy.candidate_pool_summary,
        )
        persona_roles = _persona_value_roles(cand, plan)
        # `source_relevance` is derived from the plan's own
        # metadata_relevance_rules — purely brief-driven, no
        # product-category hardcoding. Primary = metadata main_category
        # OR categories include any plan-derived multi-word category
        # phrase. Secondary = has metadata but no rule match. Off_brief
        # = no metadata at all.
        meta_main_low = (cand.metadata_main_category or "").lower()
        meta_cats_low = " ".join(
            (cand.metadata_categories or [])
        ).lower()
        plan_category_terms: list[str] = []
        for rule in plan.metadata_relevance_rules:
            if rule.kind == "category_includes_any" and rule.weight > 0:
                plan_category_terms.extend(
                    v.lower() for v in rule.values
                )
        primary_match = any(
            t in meta_main_low or t in meta_cats_low
            for t in plan_category_terms
        )
        if primary_match:
            source_relevance = "primary"
        elif meta_main_low or meta_cats_low:
            source_relevance = "secondary"
        else:
            source_relevance = "off_brief"
        if persona_roles and len(persona_roles) >= 3:
            persona_value = "high"
        elif persona_roles:
            persona_value = "medium"
        elif cand.matched_terms:
            persona_value = "low"
        else:
            persona_value = "none"

        if rejection_reasons:
            decisions.append(CandidateDecision(
                candidate_id=cand.candidate_id, decision="REJECTED",
                selection_rank=None,
                evidence_strength_label=evidence_strength,
                source_relevance_label=source_relevance,
                persona_value_label=persona_value,
                selected_for_persona_roles=persona_roles,
                decision_reasons=[],
                rejection_reasons=rejection_reasons,
                scanner_results=scanner_results,
                duplicate_check=duplicate_label,
                planned_source_record_preview=None,
            ))
            continue

        # ---- Apply selection caps ----
        if len(selected_running) >= policy.max_insert_cap:
            decisions.append(CandidateDecision(
                candidate_id=cand.candidate_id, decision="REJECTED",
                selection_rank=None,
                evidence_strength_label=evidence_strength,
                source_relevance_label=source_relevance,
                persona_value_label=persona_value,
                selected_for_persona_roles=persona_roles,
                decision_reasons=[],
                rejection_reasons=[
                    f"max_insert_cap={policy.max_insert_cap} reached "
                    "(selection-cap rejection, not a quality issue)"
                ],
                scanner_results=scanner_results,
                duplicate_check=duplicate_label,
                planned_source_record_preview=None,
            ))
            continue
        if (
            per_category_cap > 0
            and selected_per_category[cand.category] >= per_category_cap
        ):
            decisions.append(CandidateDecision(
                candidate_id=cand.candidate_id, decision="REJECTED",
                selection_rank=None,
                evidence_strength_label=evidence_strength,
                source_relevance_label=source_relevance,
                persona_value_label=persona_value,
                selected_for_persona_roles=persona_roles,
                decision_reasons=[],
                rejection_reasons=[
                    f"per_category_diversity_cap={per_category_cap} "
                    f"reached for category={cand.category}"
                ],
                scanner_results=scanner_results,
                duplicate_check=duplicate_label,
                planned_source_record_preview=None,
            ))
            continue

        # ---- SELECTED ----
        rank += 1
        preview = _build_planned_record_preview(
            cand, policy, content_hash, persona_roles, rank,
        )
        decision_reasons: list[str] = []
        if cand.confidence == "high_confidence":
            decision_reasons.append(
                "passes confidence_high_only rule"
            )
        if has_multi_word_positive:
            decision_reasons.append(
                "satisfies multi_word_anchor_required rule"
            )
        if cand.score >= int(policy.candidate_pool_summary.score_p75):
            decision_reasons.append(
                f"score >= pool.p75 "
                f"({policy.candidate_pool_summary.score_p75:.1f})"
            )
        if cand.verified_purchase:
            decision_reasons.append(
                "verified_purchase=true (trust signal)"
            )
        if any(
            c.lower() in (cand.title + " " + cand.text).lower()
            for c in plan.competitors
        ):
            decision_reasons.append(
                "names a brief-supplied competitor"
            )
        if persona_roles:
            decision_reasons.append(
                f"supports {len(persona_roles)} persona role(s): "
                f"{persona_roles[:3]}"
            )
        d = CandidateDecision(
            candidate_id=cand.candidate_id, decision="SELECTED",
            selection_rank=rank,
            evidence_strength_label=evidence_strength,
            source_relevance_label=source_relevance,
            persona_value_label=persona_value,
            selected_for_persona_roles=persona_roles,
            decision_reasons=decision_reasons,
            rejection_reasons=[],
            scanner_results=scanner_results,
            duplicate_check=duplicate_label,
            planned_source_record_preview=preview,
        )
        decisions.append(d)
        selected_running.append(d)
        selected_per_category[cand.category] += 1
    return decisions
