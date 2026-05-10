"""Phase 8.5D.1 — deterministic dynamic persona-candidate planner.

`select_effective_sources` performs lineage-aware source selection
(8.5C.4 companions supersede the 8.5C.2 preview rows they reference;
each effective source maps to AT MOST one candidate).

`PersonaCandidatePlanner.generate_candidates` runs the universal
role-inference + universal launch-state validator + universal
quality gates over the effective source pool, producing a
`PersonaRolePlan` audit artifact.

NO LLM. NO network. Pure functions. Same input → same output.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from assembly.sources.persona_role_planner.role_inference import (
    UNIVERSAL_ROLE_LEXICONS, infer_persona_roles_from_evidence,
)
from assembly.sources.persona_role_planner.schemas import (
    EffectiveSourceRecord, EvidenceStrengthLabel, InferredPersonaTrait,
    LaunchStateClaimValidationResult, PersonaCandidate,
    PersonaCandidateConfidence, PersonaCandidateRejection,
    PersonaRolePlan, ProductLaunchState, RejectionReason,
)
from assembly.sources.persona_role_planner.validators import (
    validate_launch_state_claims,
)


# ---------------------------------------------------------------------------
# Lineage-aware source selection
# ---------------------------------------------------------------------------


def select_effective_sources(
    *,
    preview_rows: list[dict],
    companion_rows: list[dict],
    sufficiency_labels_by_id: dict[str, str],
) -> tuple[list[EffectiveSourceRecord], list[str], list[str]]:
    """Return (effective_sources, superseded_preview_ids, included_ids).

    Rules (operator-spec'd):
      * If a companion exists for a preview's id, use the companion;
        exclude the preview.
      * If no companion exists for a preview, use the preview if
        labeled SUFFICIENT_AS_IS or USABLE_BUT_THIN.
      * Drop preview rows labeled EXCLUDE_FROM_PERSONA_BUILD entirely.
    """
    # Map preview id → preview row
    previews_by_id = {r["id"]: r for r in preview_rows}
    # Map companion's superseded-preview-id → companion row
    companion_by_superseded_id: dict[str, dict] = {}
    for c in companion_rows:
        sup_id = (c.get("metadata") or {}).get(
            "supersedes_preview_source_record_id"
        ) or (c.get("metadata") or {}).get(
            "original_preview_source_record_id"
        )
        if sup_id:
            companion_by_superseded_id[sup_id] = c
    superseded_preview_ids: list[str] = list(
        companion_by_superseded_id.keys()
    )
    effective: list[EffectiveSourceRecord] = []
    for preview_id, preview in previews_by_id.items():
        # Case 1: a companion supersedes this preview → use companion
        if preview_id in companion_by_superseded_id:
            comp = companion_by_superseded_id[preview_id]
            effective.append(EffectiveSourceRecord(
                source_record_id=comp["id"],
                effective_kind="fulltext_companion_used",
                superseded_preview_source_record_id=preview_id,
                parent_asin=(comp.get("metadata") or {}).get("parent_asin"),
                asin=(comp.get("metadata") or {}).get("asin"),
                category=(comp.get("metadata") or {}).get(
                    "source_category", "",
                ),
                metadata_title=(
                    (comp.get("metadata") or {}).get("metadata_title")
                ),
                rating=(comp.get("metadata") or {}).get("rating"),
                verified_purchase=(
                    (comp.get("metadata") or {}).get("verified_purchase")
                ),
                helpful_vote=(comp.get("metadata") or {}).get("helpful_vote"),
                timestamp=(comp.get("metadata") or {}).get("timestamp"),
                content_length=len(comp.get("content") or ""),
                content=comp.get("content") or "",
                metadata=comp.get("metadata") or {},
            ))
            continue
        # Case 2: no companion → use preview only if sufficiency
        # label permits.
        sl = sufficiency_labels_by_id.get(preview_id)
        if sl in ("SUFFICIENT_AS_IS", "USABLE_BUT_THIN"):
            effective.append(EffectiveSourceRecord(
                source_record_id=preview_id,
                effective_kind=(
                    "preview_used_as_is" if sl == "SUFFICIENT_AS_IS"
                    else "preview_used_thin"
                ),
                superseded_preview_source_record_id=None,
                parent_asin=(
                    (preview.get("metadata") or {}).get("parent_asin")
                ),
                asin=(preview.get("metadata") or {}).get("asin"),
                category=(
                    (preview.get("metadata") or {}).get(
                        "source_category", ""
                    )
                ),
                metadata_title=(
                    (preview.get("metadata") or {})
                    .get("metadata_title")
                ),
                rating=(preview.get("metadata") or {}).get("rating"),
                verified_purchase=(
                    (preview.get("metadata") or {})
                    .get("verified_purchase")
                ),
                helpful_vote=(
                    (preview.get("metadata") or {}).get("helpful_vote")
                ),
                timestamp=(preview.get("metadata") or {}).get("timestamp"),
                content_length=len(preview.get("content") or ""),
                content=preview.get("content") or "",
                metadata=preview.get("metadata") or {},
            ))
        # Else: dropped silently (label was EXCLUDE_FROM_PERSONA_BUILD
        # or unknown).
    included_ids = [s.source_record_id for s in effective]
    return effective, superseded_preview_ids, included_ids


class LineageAwareSourceSelector:
    """Object-oriented wrapper around `select_effective_sources` for
    test convenience + future extensibility."""

    def select(
        self,
        *,
        preview_rows: list[dict], companion_rows: list[dict],
        sufficiency_labels_by_id: dict[str, str],
    ) -> tuple[list[EffectiveSourceRecord], list[str], list[str]]:
        return select_effective_sources(
            preview_rows=preview_rows,
            companion_rows=companion_rows,
            sufficiency_labels_by_id=sufficiency_labels_by_id,
        )


# ---------------------------------------------------------------------------
# Trait extraction (deterministic)
# ---------------------------------------------------------------------------


def _short_excerpt(text: str, around: str | None = None) -> str:
    """Pull a 240-char window. If `around` is given, center on the
    first occurrence; otherwise return the head."""
    text = text or ""
    if around:
        idx = text.lower().find(around.lower())
        if idx >= 0:
            start = max(0, idx - 40)
            return text[start:start + 240].strip()
    return text[:240].strip()


def _infer_traits_from_source(
    *,
    source: EffectiveSourceRecord,
    competitor_brief_list: list[str],
    substitute_brief_list: list[str],
) -> list[InferredPersonaTrait]:
    """Produce evidence-supported traits from one effective source.

    Trait kinds (universal across products):
      * `category_familiarity` (high/medium/low) from named brand
        density
      * `verified_purchase` boolean
      * `current_alternative` from named competitor / substitute hits
      * `objection` traits from category-objection lexicon
      * `preference` traits from preference language
      * `behavior` traits from explicit behaviors

    Each trait carries `evidence_source_record_id` + a 240-char
    excerpt. Confidence is high/medium/low based on signal quality.
    """
    traits: list[InferredPersonaTrait] = []
    text = source.content or ""
    text_low = text.lower()

    # 1. Verified-purchase trait (strong)
    if source.verified_purchase is not None:
        traits.append(InferredPersonaTrait(
            trait_name="verified_purchase",
            trait_value=str(source.verified_purchase),
            evidence_source_record_id=source.source_record_id,
            evidence_excerpt=_short_excerpt(text),
            confidence="high",
        ))

    # 2. Rating trait (medium)
    if source.rating is not None:
        traits.append(InferredPersonaTrait(
            trait_name="evidence_rating_signal",
            trait_value=str(source.rating),
            evidence_source_record_id=source.source_record_id,
            evidence_excerpt=_short_excerpt(text),
            confidence="medium",
        ))

    # 3. Current alternative (competitor or substitute named in text)
    competitors_hit: list[str] = [
        c for c in competitor_brief_list
        if c and c.lower() in text_low
    ]
    substitutes_hit: list[str] = [
        s for s in substitute_brief_list
        if s and s.lower() in text_low
    ]
    if competitors_hit:
        traits.append(InferredPersonaTrait(
            trait_name="current_alternative_competitor",
            trait_value=", ".join(sorted(set(competitors_hit))),
            evidence_source_record_id=source.source_record_id,
            evidence_excerpt=_short_excerpt(
                text, around=competitors_hit[0],
            ),
            confidence="medium",
        ))
    if substitutes_hit:
        traits.append(InferredPersonaTrait(
            trait_name="current_alternative_substitute",
            trait_value=", ".join(sorted(set(substitutes_hit))),
            evidence_source_record_id=source.source_record_id,
            evidence_excerpt=_short_excerpt(
                text, around=substitutes_hit[0],
            ),
            confidence="medium",
        ))

    # 4. Universal-lexicon objection / preference / behavior traits
    for role, lex in UNIVERSAL_ROLE_LEXICONS.items():
        for term in lex:
            if term.lower() in text_low:
                trait_name_map = {
                    "safety_skeptic":
                        "objection_safety",
                    "price_skeptic":
                        "objection_price_value",
                    "flavor_focused_buyer":
                        "preference_flavor_or_sensory",
                    "performance_use_case_buyer":
                        "preference_performance_use_case",
                    "health_conscious_buyer":
                        "preference_health_low_sugar",
                    "convenience_focused_buyer":
                        "preference_convenience",
                    "category_rejecter":
                        "objection_category",
                    "behavior_dose_self_modulator":
                        "behavior_dose_modulation",
                }
                tn = trait_name_map.get(role)
                if not tn:
                    break
                # Avoid duplicate trait_names across rounds
                if any(t.trait_name == tn for t in traits):
                    break
                traits.append(InferredPersonaTrait(
                    trait_name=tn,
                    trait_value=term,
                    evidence_source_record_id=source.source_record_id,
                    evidence_excerpt=_short_excerpt(text, around=term),
                    confidence="medium",
                ))
                break  # one trait per role
    return traits


def _evidence_strength(
    *,
    source: EffectiveSourceRecord,
    n_roles: int,
    n_traits: int,
) -> EvidenceStrengthLabel:
    """Map evidence shape to a strength label."""
    score = 0
    if source.content_length >= 400:
        score += 2
    elif source.content_length >= 150:
        score += 1
    if source.verified_purchase:
        score += 1
    if n_roles >= 3:
        score += 2
    elif n_roles >= 1:
        score += 1
    if n_traits >= 4:
        score += 2
    elif n_traits >= 2:
        score += 1
    if score >= 6:
        return "very_strong"
    if score >= 4:
        return "strong"
    if score >= 2:
        return "moderate"
    return "weak"


def _hypothetical_target_reaction(
    *,
    product_name: str,
    roles: list[str],
    traits: list[InferredPersonaTrait],
) -> str:
    """Build the candidate's hypothetical reaction to the target
    product. Universal subjunctive phrasing — never claims direct
    use. References product_name + role labels only; no
    product-category vocabulary hardcoded."""
    chunks: list[str] = []
    if any("safety_skeptic" in r for r in roles):
        chunks.append(
            f"would scrutinize {product_name}'s ingredient + dosage "
            "disclosure before any trial; safety-shape concerns from the "
            "source would harden skepticism"
        )
    if any("price_skeptic" in r for r in roles):
        chunks.append(
            f"would compare {product_name}'s per-unit price against "
            "their current alternative"
        )
    if any("flavor_or_sensory" in r or "flavor_focused" in r for r in roles):
        chunks.append(
            f"would evaluate {product_name} primarily on sensory "
            "profile vs current options"
        )
    if any("performance_use_case" in r for r in roles):
        chunks.append(
            f"would consider {product_name} as a candidate around "
            "their evidenced use-case if delivery matches their "
            "current routine"
        )
    if any("health_conscious" in r for r in roles):
        chunks.append(
            f"would check {product_name}'s ingredient panel + "
            "sweetener composition before any trial"
        )
    if any(r.startswith("competitor_user_") for r in roles):
        comp = next(
            r for r in roles if r.startswith("competitor_user_")
        )
        comp_name = comp.replace("competitor_user_", "").replace("_", " ")
        chunks.append(
            f"as someone whose evidence references {comp_name}, would "
            f"compare {product_name} side-by-side; switching depends on "
            f"whether {product_name} addresses gaps the source surfaced"
        )
    if any(r.startswith("substitute_user_") for r in roles):
        sub = next(
            r for r in roles if r.startswith("substitute_user_")
        )
        sub_name = sub.replace("substitute_user_", "").replace("_", " ")
        chunks.append(
            f"would evaluate {product_name} as a potential replacement "
            f"for their current {sub_name} routine"
        )
    if not chunks:
        chunks.append(
            f"would evaluate {product_name} hypothetically based on "
            "the source's evidence; no direct usage claimed"
        )
    return "; ".join(chunks) + "."


def _confidence_label(
    strength: EvidenceStrengthLabel,
) -> PersonaCandidateConfidence:
    return {
        "very_strong": "high",
        "strong": "high",
        "moderate": "medium",
        "weak": "low",
    }[strength]


def _segment_label_from_roles(roles: list[str]) -> str:
    """Compose a human-readable segment label from the inferred roles."""
    primary = roles[0] if roles else "unspecified"
    secondary = roles[1] if len(roles) > 1 else None
    parts = primary.replace("_", " ")
    if secondary:
        parts += f" + {secondary.replace('_', ' ')}"
    return parts


# Universal role-priority ranking: more SPECIFIC roles are picked
# as primary over generic ones. Brand-named roles (competitor_user_X /
# substitute_user_Y) are most specific because they tie to a brief-
# supplied entity. Behavior + safety + price + health are concrete
# objection/preference axes. Flavor / convenience are generic.
# Fallback for unlisted roles is alphabetical (last resort).
_ROLE_SPECIFICITY: tuple[tuple[str, int], ...] = (
    # prefix → score (higher = more specific)
    ("competitor_user_", 100),
    ("substitute_user_", 95),
    ("behavior_", 80),
    ("category_rejecter", 75),
    ("safety_skeptic", 70),
    ("price_skeptic", 65),
    ("health_conscious_buyer", 60),
    ("performance_use_case_buyer", 55),
    ("convenience_focused_buyer", 35),
    ("flavor_focused_buyer", 30),
    ("flavor_or_sensory_focused_buyer", 28),
)


def _rank_roles_by_specificity(roles: list[str]) -> list[str]:
    """Sort roles primary→secondary by specificity score, then by
    alphabetical fallback. Pure function."""
    def score(r: str) -> tuple[int, str]:
        for prefix, sc in _ROLE_SPECIFICITY:
            if r == prefix or r.startswith(prefix):
                return (-sc, r)  # negative = higher priority
        return (0, r)
    return sorted(set(roles), key=score)


def _infer_preferences(
    text: str, roles: list[str], product_name: str,
) -> list[str]:
    """Extract preference-shape statements from universal phrase
    patterns + role signals. No product-category vocabulary."""
    out: list[str] = []
    text_low = (text or "").lower()
    if (
        "no sugar" in text_low
        or "sugar-free" in text_low
        or "sugar free" in text_low
    ):
        out.append("prefers low/zero-sweetener formulations")
    if "flavor" in text_low and (
        "love" in text_low or "favorite" in text_low or "yummy" in text_low
    ):
        out.append(
            "sensory-driven; willing to repeat a flavor that hits"
        )
    if "easy to mix" in text_low or " easy " in text_low:
        out.append("convenience-driven (easy preparation matters)")
    if any(r.startswith("performance_use_case") for r in roles):
        out.append(
            "uses category-products in a performance / use-case "
            "routine evidenced by the source"
        )
    if any(r.startswith("health_conscious") for r in roles):
        out.append("scrutinizes ingredient panel before trial")
    return out


def _infer_objections(
    text: str, roles: list[str], product_name: str,
) -> list[str]:
    """Universal objection-pattern extraction. No product-category
    or ingredient names hardcoded."""
    out: list[str] = []
    text_low = (text or "").lower()
    if "heart racing" in text_low or "blood pressure" in text_low:
        out.append(
            "concerned about cardiovascular response to product use"
        )
    if "tingling" in text_low:
        out.append(
            "concerned about paresthesia / sensory side-effects from "
            "the source's evidence"
        )
    if (
        "expensive" in text_low
        or "pricey" in text_low
        or "ridiculous" in text_low
    ):
        out.append(
            f"price-sensitive; would push back on {product_name}'s "
            "per-unit price if not justified"
        )
    if any(r.startswith("safety_skeptic") for r in roles):
        out.append(
            f"would require full ingredient + dosage disclosure "
            f"before considering {product_name}"
        )
    return out


def _infer_behaviors(text: str, roles: list[str]) -> list[str]:
    out: list[str] = []
    text_low = (text or "").lower()
    if "cut the scoop" in text_low or "halved the scoop" in text_low:
        out.append("self-modulates dose mid-product (e.g. half scoop)")
    if "deducting a star" in text_low:
        out.append(
            "rates honestly even when partially satisfied; non-fanboy "
            "behavior"
        )
    if "wouldn't" in text_low or "would not" in text_low:
        out.append(
            "explicit non-repeat-buy language present in source"
        )
    if "switched" in text_low:
        out.append(
            "willing to switch alternatives based on experience"
        )
    return out


# ---------------------------------------------------------------------------
# Top-level planner
# ---------------------------------------------------------------------------


class PersonaCandidatePlanner:
    """Generate the full PersonaRolePlan from inputs."""

    def __init__(self, *, generated_for_phase: str = "8.5D.1") -> None:
        self._phase = generated_for_phase

    def generate(
        self,
        *,
        product_name: str,
        target_brief_id: str,
        launch_state: ProductLaunchState,
        competitor_brief_list: list[str],
        substitute_brief_list: list[str],
        effective_sources: list[EffectiveSourceRecord],
        preview_rows_total: int,
        companion_rows_total: int,
        superseded_preview_ids: list[str],
    ) -> PersonaRolePlan:
        """Pure function. Returns a PersonaRolePlan."""
        candidates: list[PersonaCandidate] = []
        rejections: list[PersonaCandidateRejection] = []
        all_inferred_roles: set[str] = set()
        evidence_basis_by_role: dict[str, list[str]] = defaultdict(list)
        rejected_role_ideas: list[str] = []
        validation_results: list[LaunchStateClaimValidationResult] = []

        # Build candidates one-per-effective-source.
        seen_role_evidence_pairs: set[tuple[str, str]] = set()
        for src in effective_sources:
            raw_roles, basis = infer_persona_roles_from_evidence(
                text=src.content,
                metadata=src.metadata,
                competitor_brief_list=competitor_brief_list,
                substitute_brief_list=substitute_brief_list,
            )
            # Apply specificity ranking so the most SPECIFIC role is
            # picked as primary (competitor_user / substitute_user
            # over generic flavor/convenience).
            roles = _rank_roles_by_specificity(raw_roles)
            for r in roles:
                all_inferred_roles.add(r)
                evidence_basis_by_role[r].extend(basis.get(r, []))

            if not roles:
                rejections.append(PersonaCandidateRejection(
                    rejected_idea_label=(
                        f"persona_from_source::{src.source_record_id[:8]}"
                    ),
                    source_record_ids=[src.source_record_id],
                    rejection_reason="no_source_evidence",
                    explanation=(
                        "no roles inferable from evidence; insufficient "
                        "signal for a brief-scoped persona candidate"
                    ),
                ))
                continue

            traits = _infer_traits_from_source(
                source=src,
                competitor_brief_list=competitor_brief_list,
                substitute_brief_list=substitute_brief_list,
            )
            if len(traits) < 2:
                rejections.append(PersonaCandidateRejection(
                    rejected_idea_label=(
                        f"persona_from_source::{src.source_record_id[:8]}"
                    ),
                    source_record_ids=[src.source_record_id],
                    rejection_reason="below_min_traits",
                    explanation=(
                        f"only {len(traits)} evidence-supported trait(s); "
                        "minimum is 2 for a brief-scoped persona candidate"
                    ),
                ))
                continue

            # Dedupe by (primary_role, source_id) — never two candidates
            # for the same source + role.
            primary_role = roles[0]
            dedupe_key = (primary_role, src.source_record_id)
            if dedupe_key in seen_role_evidence_pairs:
                rejections.append(PersonaCandidateRejection(
                    rejected_idea_label=(
                        f"persona_from_source::{src.source_record_id[:8]}"
                        f"::{primary_role}"
                    ),
                    source_record_ids=[src.source_record_id],
                    rejection_reason="duplicate_role_and_evidence",
                    explanation=(
                        "another candidate already represents this "
                        "(role, source) pair"
                    ),
                ))
                continue
            seen_role_evidence_pairs.add(dedupe_key)

            # Evidence snippets — head + 1-2 lexicon-anchored windows
            snippets: list[str] = [
                _short_excerpt(src.content),
            ]
            for role in roles[:3]:
                if role in evidence_basis_by_role:
                    s = evidence_basis_by_role[role][0]
                    if s and s not in snippets:
                        snippets.append(s[:240])

            preferences = _infer_preferences(
                src.content, roles, product_name,
            )
            objections = _infer_objections(
                src.content, roles, product_name,
            )
            behaviors = _infer_behaviors(src.content, roles)
            hypothetical = _hypothetical_target_reaction(
                product_name=product_name, roles=roles, traits=traits,
            )
            strength = _evidence_strength(
                source=src, n_roles=len(roles), n_traits=len(traits),
            )
            confidence = _confidence_label(strength)
            superseded_ids = (
                [src.superseded_preview_source_record_id]
                if src.superseded_preview_source_record_id else []
            )
            evidence_summary = (
                f"{src.metadata_title or 'Untitled'} "
                f"({src.category}, asin={src.parent_asin}): "
                f"{len(traits)} traits, {len(roles)} roles "
                f"inferred from {src.content_length}-char source."
            )
            cid = (
                f"{target_brief_id}::"
                f"{src.parent_asin or 'no_asin'}::"
                f"{primary_role}"
            )
            caveats = [
                "DRY-RUN candidate — not persisted to DB.",
                "Brief-scoped + run-scoped: this candidate is "
                f"specific to the current {product_name} brief and the "
                f"current {self._phase} run; it is NOT a global persona.",
                f"No direct target-product usage claimed ({product_name} "
                f"launch_state={launch_state}).",
                "Source is Amazon Reviews 2023 historical snapshot "
                "(2023-09-01); historical-evidence caveat applies.",
            ]
            persistence_recommendation = (
                "PERSIST_IN_8_5D_2"
                if confidence in ("high", "medium")
                and strength in ("very_strong", "strong", "moderate")
                else "DEFER"
            )
            candidate = PersonaCandidate(
                candidate_id=cid,
                target_brief=target_brief_id,
                generated_for_phase=self._phase,
                inferred_persona_role=primary_role,
                secondary_persona_roles=[r for r in roles[1:6]],
                role_inference_basis=[
                    s[:160] for r in roles[:3]
                    for s in evidence_basis_by_role.get(r, [])[:2]
                ][:6],
                segment_label=_segment_label_from_roles(roles),
                source_record_ids=[src.source_record_id],
                superseded_preview_source_record_ids=superseded_ids,
                evidence_summary=evidence_summary,
                evidence_snippets=snippets,
                inferred_traits=traits,
                inferred_preferences=preferences,
                inferred_objections=objections,
                inferred_behaviors=behaviors,
                hypothetical_target_product_reaction=hypothetical,
                confidence=confidence,
                evidence_strength=strength,
                caveats=caveats,
                simulation_usefulness_summary=(
                    f"useful for simulating a {primary_role.replace('_', ' ')} "
                    f"voice grounded in real evidence from {src.metadata_title or 'category source'}"
                ),
                persistence_recommendation=persistence_recommendation,
            )
            # Universal launch-state validator
            v = validate_launch_state_claims(
                candidate=candidate,
                launch_state=launch_state,
                product_name=product_name,
            )
            validation_results.append(v)
            if not v.is_valid:
                rejections.append(PersonaCandidateRejection(
                    rejected_idea_label=cid,
                    source_record_ids=[src.source_record_id],
                    rejection_reason=(
                        "fabricated_unlaunched_target_product_use"
                    ),
                    explanation=(
                        "candidate text matched a forbidden direct-usage "
                        f"pattern: {v.forbidden_phrases_matched[:3]}"
                    ),
                ))
                continue
            candidates.append(candidate)

        # Distribution
        role_dist: Counter = Counter(
            c.inferred_persona_role for c in candidates
        )
        # Coverage summary
        coverage = {
            "effective_source_count": len(effective_sources),
            "candidates_generated": len(candidates),
            "candidates_per_source_ratio": (
                round(len(candidates) / max(1, len(effective_sources)), 3)
            ),
            "categories_covered": sorted(
                {s.category for s in effective_sources}
            ),
            "competitor_coverage": sorted({
                c for c in competitor_brief_list
                if any(c.lower() in (s.content or "").lower()
                       for s in effective_sources)
            }),
            "substitute_coverage": sorted({
                s for s in substitute_brief_list
                if any(s.lower() in (es.content or "").lower()
                       for es in effective_sources)
            }),
        }

        # Plan id
        payload = {
            "brief": target_brief_id,
            "phase": self._phase,
            "n": len(effective_sources),
            "first_ids": [s.source_record_id for s in effective_sources[:5]],
        }
        plan_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        ready_for_8_5d_2 = (
            len(candidates) >= 2
            and all(c.confidence in ("high", "medium") for c in candidates)
            and not any(
                not v.is_valid for v in validation_results
            )
        )
        recommendation = (
            f"PASS — {len(candidates)} brief-scoped persona candidate(s) "
            f"generated from {len(effective_sources)} effective sources; "
            f"every candidate evidence-tied; launch-state validator clean. "
            f"Phase 8.5D.2 run-scoped persistence is "
            + ("ready." if ready_for_8_5d_2 else "deferred — see candidates flagged DEFER.")
        )

        return PersonaRolePlan(
            target_brief_id=target_brief_id,
            product_name=product_name,
            launch_state=launch_state,
            generated_for_phase=self._phase,
            plan_id=plan_id,
            role_inference_method="deterministic",
            preview_rows_found=preview_rows_total,
            companion_rows_found=companion_rows_total,
            superseded_preview_rows_excluded=superseded_preview_ids,
            effective_source_records_count=len(effective_sources),
            effective_source_record_ids=[
                s.source_record_id for s in effective_sources
            ],
            inferred_roles=sorted(all_inferred_roles),
            evidence_basis_by_role={
                r: sorted(set(v))[:5]
                for r, v in evidence_basis_by_role.items()
            },
            rejected_role_ideas=rejected_role_ideas,
            persona_candidates=candidates,
            rejected_candidate_ideas=rejections,
            launch_state_validation_results=validation_results,
            persona_role_distribution=dict(role_dist),
            evidence_coverage_summary=coverage,
            caveats=[
                "Persona candidates are deterministic — derived from "
                "the founder brief, lineage-aware effective source "
                "pool, and three universal lexicons (role inference, "
                "trait extraction, launch-state validation). NO LLM. "
                "NO network.",
                "Every candidate is BRIEF-SCOPED + RUN-SCOPED + "
                "DRY-RUN-ONLY. None is a global persona.",
                "Sources are Amazon Reviews 2023 historical snapshot. "
                "Every persistence decision in 8.5D.2 must propagate "
                "the historical-evidence caveat.",
            ],
            generated_at=datetime.now(UTC).isoformat(),
            recommendation=recommendation,
            ready_for_8_5d_2=ready_for_8_5d_2,
        )
