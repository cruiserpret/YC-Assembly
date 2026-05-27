"""Phase 12F.1 — "Why Assembly predicted this" panel builder.

Pure aggregation. Inputs are ctx + brief; output is a JSON-safe dict
to be injected as a top-level key in founder_report.json.

Rules:
  * No chain-of-thought exposed; only structured artifacts.
  * Drivers / blockers cite evidence_anchor strings sourced from
    `evidence_basis` on each intent draft.
  * `assumptions_in_play` declares known weak priors so the founder
    can see them.
  * Confidence block delegated to confidence_score.compute_confidence.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from assembly.explainability.confidence_score import compute_confidence
from assembly.sources.audience.role_taxonomy import get_profile


# All advanced-context schema fields the founder MAY supply. Used for
# the inputs_used.fields_provided / fields_missing diff. Required
# fields are excluded (they're always provided by construction).
_OPTIONAL_BRIEF_FIELDS: tuple[str, ...] = (
    "category_hint",
    "optional_context",
    "constraints",
    "product_url",
    "max_budget_usd",
    "preferred_society_size",
    "launch_source",
    # 12F.1 fields:
    "company_stage",
    "current_traction",
    "retention_or_churn_signal",
    "founder_hypothesis",
    "customer_interviews",
    "known_objections",
    "icp_segments",
    "pricing_assumptions",
    "gtm_channel",
    "competitors_with_context",
    "current_messaging",
    "decision_being_tested",
    "what_would_change_my_mind",
    "uploaded_artifacts",
)


def _is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, tuple, set, str)):
        return len(value) > 0
    return True


def _inputs_used(brief: dict[str, Any]) -> dict[str, Any]:
    provided: list[str] = []
    missing: list[str] = []
    for f in _OPTIONAL_BRIEF_FIELDS:
        if _is_populated(brief.get(f)):
            provided.append(f)
        else:
            missing.append(f)
    return {
        "fields_provided": sorted(provided),
        "fields_missing": sorted(missing),
        "n_provided": len(provided),
        "n_total_optional": len(_OPTIONAL_BRIEF_FIELDS),
    }


def _source_audience_profile_block(
    launch_source: str | None,
) -> dict[str, Any]:
    src = launch_source or "default"
    profile = get_profile(src)
    role_mix_pct = {
        role: round(float(share) * 100.0, 2)
        for role, share in profile.items()
        if float(share) > 0.0
    }
    rationale = (
        "Brief specified launch_source=" + src + ". Profile proportions "
        "are calibration-stage priors derived from the source-audience "
        "taxonomy, not a real-world launch forecast."
    )
    if src == "default":
        rationale = (
            "No launch_source specified; using the legacy-compatible "
            "default profile (target-customer-heavy). The 4-view split "
            "collapses to the target-market view under this profile."
        )
    return {
        "profile_used": src,
        "role_mix_pct": role_mix_pct,
        "rationale": rationale,
    }


def _persona_composition_block(
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate counts over augmented_intent_drafts. Falls back to
    legacy intent_drafts when augmentation hasn't run yet."""
    augmented = ctx.get("augmented_intent_drafts") or []
    if not augmented:
        # Fallback: legacy intent drafts. Don't have audience_role per
        # draft; emit segment_label counts only.
        intent_drafts = ctx.get("intent_drafts") or []
        by_segment: Counter[str] = Counter()
        persona_meta = ctx.get("persona_meta") or {}
        for d in intent_drafts:
            pid = str(getattr(d, "persona_id", ""))
            seg = (persona_meta.get(pid, {}) or {}).get(
                "segment_label", "unknown",
            )
            by_segment[seg] += 1
        return {
            "n_total": len(intent_drafts),
            "by_audience_role": {},
            "by_segment_label": dict(by_segment),
            "by_scorable_status": {},
            "n_synthetic_non_customer_voices": 0,
        }
    by_role: Counter[str] = Counter()
    by_segment2: Counter[str] = Counter()
    by_scorable: Counter[str] = Counter()
    n_synthetic = 0
    persona_meta = ctx.get("persona_meta") or {}
    for d in augmented:
        role = d.get("audience_role") or "unknown"
        by_role[role] += 1
        seg = (
            persona_meta.get(str(d.get("persona_id")), {}) or {}
        ).get("segment_label") or d.get("cohort_id") or "unknown"
        by_segment2[str(seg)] += 1
        is_scorable = bool(d.get("is_scorable", True))
        by_scorable["scorable" if is_scorable else "non_scorable"] += 1
        if d.get("is_synthetic_non_customer_voice"):
            n_synthetic += 1
    return {
        "n_total": len(augmented),
        "by_audience_role": dict(by_role),
        "by_segment_label": dict(by_segment2),
        "by_scorable_status": dict(by_scorable),
        "n_synthetic_non_customer_voices": n_synthetic,
    }


def _evidence_snapshot_block(ctx: dict[str, Any]) -> dict[str, Any]:
    snap = ctx.get("_snapshot")
    if snap is None:
        return {
            "snapshot_present": False,
            "note": (
                "No evidence snapshot was attached to this run; "
                "confidence dimensions degrade accordingly."
            ),
        }
    by_source: Counter[str] = Counter()
    for item in (getattr(snap, "raw_evidence_items", None) or []):
        src = (item.get("source") or item.get("provenance")
               if isinstance(item, dict) else None) or "unknown"
        by_source[str(src)] += 1
    return {
        "snapshot_present": True,
        "evidence_snapshot_id": getattr(snap, "evidence_snapshot_id", None),
        "snapshot_hash": getattr(snap, "snapshot_hash", None),
        "brief_hash": getattr(snap, "brief_hash", None),
        "raw_result_count": getattr(snap, "raw_result_count", None),
        "accepted_evidence_count": getattr(snap, "accepted_evidence_count", None),
        "by_source": dict(by_source) or None,
    }


def _assumptions_in_play(
    *,
    brief: dict[str, Any],
    launch_source: str | None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    src = launch_source or "default"
    if src == "hn_show_hn":
        out.append({
            "id": "source_profile_prior_hn_show_hn",
            "statement": (
                "The hn_show_hn audience profile is a weak prior; "
                "proportions are calibrated on a single product to "
                "date (Phase 12E DocuSeal)."
            ),
            "impact": (
                "Could over- or under-estimate non-customer voice "
                "mass on products outside that calibration set."
            ),
        })
    elif src == "default":
        out.append({
            "id": "source_profile_default_legacy_compat",
            "statement": (
                "No launch_source supplied; using the legacy default "
                "profile (target-customer-heavy)."
            ),
            "impact": (
                "Non-customer voices (observers, meta-commenters) are "
                "under-represented compared to a public-launch audience."
            ),
        })
    if not _is_populated(brief.get("pricing_assumptions")):
        out.append({
            "id": "pricing_specificity_low",
            "statement": (
                "Pricing was supplied as free text rather than structured "
                "tiers; price-sensitivity reactions vary more."
            ),
            "impact": (
                "Bucket proportions in the price-sensitive cohorts have "
                "higher variance."
            ),
        })
    if not _is_populated(brief.get("customer_interviews")):
        out.append({
            "id": "no_founder_supplied_customer_evidence",
            "statement": (
                "No real customer interviews / quotes were attached. "
                "Personas are anchored only to public retrieved evidence."
            ),
            "impact": (
                "Niche / personalized objections are less likely to "
                "match what your specific customers actually say."
            ),
        })
    if not _is_populated(brief.get("competitors_with_context")) and not (
        _is_populated(brief.get("competitors_or_alternatives"))
    ):
        out.append({
            "id": "competitors_not_declared",
            "statement": (
                "No competitors or alternatives were named in the brief."
            ),
            "impact": (
                "Competitor-comparison reactions and switching-trigger "
                "analyses are based on inferred competitors only."
            ),
        })
    return out


def _bucket_explanations(
    ctx: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build per-bucket {drivers, blockers, pct} aggregation by
    grouping augmented intent drafts by their final bucket and
    aggregating their conditions_to_buy / reason_for_rejection /
    proof_needed lists.

    Drivers come from conditions_to_buy; blockers come from
    reason_for_rejection + proof_needed. Every entry carries an
    evidence_anchor sourced from evidence_basis.
    """
    augmented = ctx.get("augmented_intent_drafts") or []
    if not augmented:
        return {}
    # Lazy import to keep this module free of calibration deps at
    # module-import time.
    from assembly.calibration.market_buckets import (
        pick_market_bucket_with_role,
    )
    by_bucket_drivers: dict[str, Counter[str]] = {
        "buyer": Counter(), "receptive": Counter(),
        "uncertain": Counter(), "skeptical": Counter(),
    }
    by_bucket_blockers: dict[str, Counter[str]] = {
        "buyer": Counter(), "receptive": Counter(),
        "uncertain": Counter(), "skeptical": Counter(),
    }
    by_bucket_anchors: dict[str, list[str]] = {
        "buyer": [], "receptive": [], "uncertain": [], "skeptical": [],
    }
    bucket_counts: Counter[str] = Counter()
    total = 0
    for d in augmented:
        role = d.get("audience_role")
        intent_label = d.get("simulated_intent")
        intent_signal = d.get("intent_signal")
        try:
            bucket, _ = pick_market_bucket_with_role(
                audience_role=role,
                intent_signal=intent_signal,
                intent_label=intent_label,
                intent_signal_routing_enabled=None,
            )
        except Exception:
            continue
        is_scorable = bool(d.get("is_scorable", True))
        if not is_scorable:
            continue
        if bucket not in by_bucket_drivers:
            continue
        bucket_counts[bucket] += 1
        total += 1
        for cond in (d.get("conditions_to_buy") or [])[:3]:
            by_bucket_drivers[bucket][str(cond).strip().lower()] += 1
        for proof in (d.get("proof_needed") or [])[:3]:
            by_bucket_blockers[bucket][str(proof).strip().lower()] += 1
        rfr = d.get("reason_for_rejection")
        if rfr:
            by_bucket_blockers[bucket][str(rfr).strip().lower()] += 1
        anchor = d.get("evidence_basis")
        if anchor:
            by_bucket_anchors[bucket].append(str(anchor))
    out: dict[str, dict[str, Any]] = {}
    for b in ("buyer", "receptive", "uncertain", "skeptical"):
        n = bucket_counts[b]
        out[b] = {
            "count": n,
            "pct": round(100.0 * n / total, 2) if total else 0.0,
            "top_drivers": [
                {"text": txt, "raised_by_count": cnt}
                for txt, cnt in by_bucket_drivers[b].most_common(5)
            ],
            "top_blockers": [
                {"text": txt, "raised_by_count": cnt}
                for txt, cnt in by_bucket_blockers[b].most_common(5)
            ],
            "evidence_anchors_sample": (
                list(dict.fromkeys(by_bucket_anchors[b]))[:5]
            ),
        }
    return out


def build_explainability_panel(
    *,
    brief: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Top-level entry point. Returns a JSON-safe dict for direct
    inclusion in founder_report.json under the key `explainability`.
    """
    launch_source = ctx.get("launch_source") or brief.get("launch_source")
    return {
        "phase": "12f.1",
        "decision_being_tested": brief.get("decision_being_tested"),
        "what_would_change_founder_mind": (
            brief.get("what_would_change_my_mind")
        ),
        "inputs_used": _inputs_used(brief),
        "source_audience_profile": (
            _source_audience_profile_block(launch_source)
        ),
        "persona_composition": _persona_composition_block(ctx),
        "evidence_snapshot": _evidence_snapshot_block(ctx),
        "assumptions_in_play": _assumptions_in_play(
            brief=brief, launch_source=launch_source,
        ),
        "bucket_explanations": _bucket_explanations(ctx),
        "confidence": compute_confidence(
            brief=brief, ctx=ctx, launch_source=launch_source,
        ),
        "_caveat": (
            "Structured reasoning artifacts only — no LLM chain-of-"
            "thought is exposed. Drivers and blockers are aggregated "
            "from per-persona conditions_to_buy / proof_needed / "
            "reason_for_rejection fields, each sourced to an "
            "evidence_basis rule string."
        ),
    }
