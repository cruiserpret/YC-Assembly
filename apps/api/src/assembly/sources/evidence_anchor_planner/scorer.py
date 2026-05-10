"""Phase 8.5B.1 — dynamic Amazon scorer driven by an EvidenceAnchorPlan.

`score_review_with_plan(review, metadata, plan)` reuses the Phase 8.5B
`ReviewConfidence` + `PrimeContext` closed enums and the
`ReviewScoreDetail` shape, but every term list comes from the plan
rather than module-level constants. That makes the scorer
product-agnostic.

Scoring summary:

  * +3 per positive_anchor_term hit (review or metadata).
  * +2 per competitor_anchor_term hit (subject to ambiguity check).
  * +2 per substitute_anchor_term hit.
  * +1 per use_case_anchor_term hit.
  * +1 per objection_anchor_term hit.
  * For each ambiguous entity, the scorer classifies context as
    INTENDED / WRONG / AMBIGUOUS, awarding +3 / -3 / 0.
  * Generic modifiers (flavor, price, taste, etc.) count +1 ONLY if
    a brief-derived anchor co-occurs in the same review.
  * Metadata rules: each rule awards its `weight` if its predicate
    matches.

  * REJECTED if total score < 1, OR if matches are exclusively
    `flavor (unqualified)` / wrong-context-only.
  * LOW (1–2) / MEDIUM (3–5) / HIGH (≥ 6).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from assembly.sources.amazon_reviews_2023.adapter import AmazonReviewRecord
from assembly.sources.amazon_reviews_2023.filters import (
    AmazonProductMetadata, PrimeContext, ReviewConfidence,
    ReviewScoreDetail,
)
from assembly.sources.evidence_anchor_planner.constants import (
    UNIVERSAL_GENERIC_MODIFIERS,
)
from assembly.sources.evidence_anchor_planner.schemas import (
    EvidenceAnchorPlan,
)


_GENERIC_MODIFIER_SET = frozenset(t.lower() for t in UNIVERSAL_GENERIC_MODIFIERS)


def _hits_in(text: str, terms: list[str]) -> list[str]:
    if not text or not terms:
        return []
    low = text.lower()
    return [t for t in terms if t.lower() in low]


def _classify_ambiguous_entity(
    text: str,
    intended_phrases: list[str],
    wrong_phrases: list[str],
    entity: str,
) -> PrimeContext:
    """Classify a mention of an ambiguous entity as DRINK (intended)
    / SHIPPING (wrong) / AMBIGUOUS — reusing the 8.5B PrimeContext
    enum because its 3-value shape generalizes to any ambiguous
    entity, not just Prime."""
    if not text:
        return PrimeContext.AMBIGUOUS
    low = text.lower()
    if any(p.lower() in low for p in intended_phrases):
        return PrimeContext.DRINK
    if any(p.lower() in low for p in wrong_phrases):
        return PrimeContext.SHIPPING
    if entity.lower() in low:
        return PrimeContext.AMBIGUOUS
    return PrimeContext.AMBIGUOUS


def _check_metadata_rule(
    rule_kind: str,
    values: list[str],
    metadata: AmazonProductMetadata,
) -> bool:
    """Check one rule against the metadata. Returns True iff the
    rule matches and its weight should apply."""
    if rule_kind == "category_includes_any":
        cat_blob = (
            metadata.main_category + " | "
            + " | ".join(metadata.categories)
        ).lower()
        return any(v.lower() in cat_blob for v in values)
    if rule_kind == "title_contains_any":
        title_low = (metadata.title or "").lower()
        return any(v.lower() in title_low for v in values)
    if rule_kind == "category_excludes_any":
        cat_blob = (
            metadata.main_category + " | "
            + " | ".join(metadata.categories)
        ).lower()
        # Rule matches when NONE of the excluded values appear,
        # i.e. when the metadata is "safe". The rule's weight is
        # negative, so by inverting the predicate we award a
        # PENALTY when ANY excluded value DOES appear.
        return any(v.lower() in cat_blob for v in values)
    return False


def score_review_with_plan(
    *,
    review: AmazonReviewRecord,
    metadata: AmazonProductMetadata | None,
    plan: EvidenceAnchorPlan,
) -> ReviewScoreDetail:
    """Score one (review, joined-metadata) candidate using the plan.

    Pure deterministic. Same inputs → same output. NO LLM, NO I/O."""
    review_blob = f"{review.title} {review.text}".strip()
    review_low = review_blob.lower()
    meta_blob = ""
    title_blob = ""
    if metadata is not None:
        title_blob = metadata.title or ""
        meta_blob = " | ".join(filter(None, (
            metadata.title, metadata.store, metadata.main_category,
            " | ".join(metadata.categories), metadata.description,
            " | ".join(metadata.features),
            " | ".join(f"{k}={v}" for k, v in metadata.details_summary.items()),
        )))
    meta_low = meta_blob.lower()
    full_blob = f"{review_blob} {meta_blob}"

    score = 0
    matched_terms: list[str] = []
    denylist_hits: list[str] = []

    # --- Positive product-type anchors ---
    # Phase 8.5B.1 quality fix: distinguish multi-word phrases (strong,
    # +3 each capped at 2 hits) from single tokens (weak, +1 each
    # capped at 2 hits). Single tokens like "sports" / "drink" /
    # "athletes" appear in many off-topic reviews; gating them at +1
    # prevents the false-positive flood seen in the first 8.5B.1 run
    # (961/1000 HIGH in Sports_and_Outdoors for Triton).
    multi_word_hits = 0
    single_word_hits = 0
    for t in plan.positive_anchor_terms:
        if t.lower() not in review_low and t.lower() not in meta_low:
            continue
        is_multi_word = " " in t.strip()
        if is_multi_word:
            if multi_word_hits >= 2:
                continue
            score += 3
            multi_word_hits += 1
            matched_terms.append(f"positive:{t}")
        else:
            if single_word_hits >= 2:
                continue
            score += 1
            single_word_hits += 1
            matched_terms.append(f"positive(weak):{t}")

    # --- Substitute anchors ---
    for t in plan.substitute_anchor_terms:
        if t.lower() in review_low or t.lower() in meta_low:
            score += 2
            matched_terms.append(f"substitute:{t}")

    # --- Use-case anchors (capped at 2 hits to prevent over-weighting
    # broad single-word terms like "sports" / "athletes" / "gym") ---
    use_case_hits = 0
    for t in plan.use_case_anchor_terms:
        if use_case_hits >= 2:
            break
        if t.lower() in review_low:
            score += 1
            matched_terms.append(f"usecase:{t}")
            use_case_hits += 1

    # --- Objection anchors ---
    for t in plan.objection_anchor_terms:
        if t.lower() in review_low:
            score += 1
            matched_terms.append(f"objection:{t}")

    # --- Competitor anchors with ambiguity handling ---
    ambiguous_set = {a.entity.lower() for a in plan.ambiguous_entities}
    primary_context = PrimeContext.AMBIGUOUS
    for c in plan.competitor_anchor_terms:
        c_low = c.lower()
        if c_low not in review_low and c_low not in meta_low:
            continue
        # Find matching ambiguous-entity rule (case-insensitive on
        # the .entity field).
        amb = next(
            (a for a in plan.ambiguous_entities
             if a.entity.lower() == c_low),
            None,
        )
        if amb is None:
            score += 2
            matched_terms.append(f"competitor:{c}")
            continue
        ctx = _classify_ambiguous_entity(
            full_blob,
            amb.intended_sense_phrases,
            amb.wrong_sense_phrases,
            amb.entity,
        )
        if ctx is PrimeContext.DRINK:
            score += 3
            matched_terms.append(f"competitor:{c}(intended)")
            primary_context = PrimeContext.DRINK
        elif ctx is PrimeContext.SHIPPING:
            score -= 3
            denylist_hits.append(f"competitor:{c}(wrong-context)")
            if primary_context is PrimeContext.AMBIGUOUS:
                primary_context = PrimeContext.SHIPPING

    # --- Generic modifiers — co-occurrence required ---
    has_brief_anchor = any(
        m.startswith(("positive:", "competitor:", "substitute:",
                      "usecase:", "objection:"))
        and "(wrong-context)" not in m
        for m in matched_terms
    )
    generic_present = any(
        g in review_low or g in meta_low
        for g in _GENERIC_MODIFIER_SET
    )
    if generic_present and has_brief_anchor:
        score += 1
        matched_terms.append("generic_modifier (qualified)")
    elif generic_present and not has_brief_anchor:
        denylist_hits.append("generic_modifier (unqualified)")

    # --- Metadata-relevance rules ---
    metadata_category_hits: list[str] = []
    title_hits: list[str] = []
    if metadata is not None:
        for rule in plan.metadata_relevance_rules:
            matched = _check_metadata_rule(
                rule.kind, rule.values, metadata,
            )
            if not matched:
                continue
            score += rule.weight
            if rule.kind == "category_includes_any" and rule.weight > 0:
                metadata_category_hits.extend([
                    v for v in rule.values
                    if v.lower() in (
                        metadata.main_category + " "
                        + " ".join(metadata.categories)
                    ).lower()
                ])
                matched_terms.append(f"meta:category({rule.kind})")
            elif rule.kind == "title_contains_any" and rule.weight > 0:
                title_hits.extend([
                    v for v in rule.values
                    if v.lower() in (metadata.title or "").lower()
                ])
                matched_terms.append(f"meta:title({rule.kind})")
            elif rule.kind == "category_excludes_any" and rule.weight < 0:
                denylist_hits.append(
                    f"meta:category-excludes({rule.kind})"
                )

    # --- Decide rejection / label ---
    rejection_reason: str | None = None

    has_signal = any(
        m.startswith(("positive:", "competitor:", "substitute:",
                      "usecase:", "objection:", "meta:"))
        and "(wrong-context)" not in m
        for m in matched_terms
    )
    only_wrong_context = (
        not has_signal
        and any(d.endswith("(wrong-context)") for d in denylist_hits)
    )
    only_unqualified_generic = (
        not has_signal
        and any(d == "generic_modifier (unqualified)" for d in denylist_hits)
    )

    if not has_signal:
        if only_wrong_context:
            rejection_reason = "wrong_context_only"
        elif only_unqualified_generic:
            rejection_reason = "unqualified_generic_only"
        else:
            rejection_reason = "no_brief_anchor"
    elif (
        only_wrong_context  # any wrong-context AND no signal — dup but explicit
    ):
        rejection_reason = "wrong_context_only"

    if rejection_reason:
        confidence = ReviewConfidence.REJECTED
    elif score >= 6:
        confidence = ReviewConfidence.HIGH_CONFIDENCE
    elif score >= 3:
        confidence = ReviewConfidence.MEDIUM_CONFIDENCE
    elif score >= 1:
        confidence = ReviewConfidence.LOW_CONFIDENCE
    else:
        confidence = ReviewConfidence.REJECTED
        rejection_reason = rejection_reason or "score_below_threshold"

    return ReviewScoreDetail(
        confidence=confidence,
        score=score,
        matched_terms=tuple(matched_terms),
        denylist_hits=tuple(denylist_hits),
        metadata_category_hits=tuple(set(metadata_category_hits)),
        product_title_hits=tuple(set(title_hits)),
        review_text_hits=tuple(),  # not separately tracked here
        prime_context=primary_context,
        rejection_reason=rejection_reason,
        has_metadata=metadata is not None,
    )
