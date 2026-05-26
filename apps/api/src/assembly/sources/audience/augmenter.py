"""Phase 12E — source-audience augmenter.

Bridges the legacy persona pipeline (which produces target-customer
evaluator voices only) with the Phase 12E role taxonomy. Two surfaces:

  * `assign_audience_role(...)` — heuristic mapping from an existing
    intent_draft to one of the 10 audience roles.

  * `augment_intent_drafts_with_source_audience(...)` — given the
    legacy `intent_drafts` list (LLM-generated, all customer voices)
    and a `launch_source`, return a SOURCE-AUDIENCE intent_drafts
    list that INCLUDES synthetic non-customer voices proportional to
    the source profile. Zero LLM calls; the synthetic voices are
    deterministic stubs with role-locked intent + bucket.

The legacy `intent_drafts` is NOT modified — both lists are returned
so the orchestrator can write a target-market view AND a source-
audience view side-by-side.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any
from uuid import uuid5, NAMESPACE_OID

from assembly.sources.audience.role_taxonomy import (
    AUDIENCE_ROLES,
    AudienceRole,
    SOURCE_PROFILES,
    get_role_spec,
    resolve_launch_source,
)
from assembly.sources.intent_layer.schemas import SimulatedIntentDraft


# Heuristic mapping: existing segment_label / role → audience_role.
# This applies to LLM-generated personas which today are ONLY
# customer-evaluator types.
_SEGMENT_TO_AUDIENCE_ROLE: dict[str, AudienceRole] = {
    # "competitor_user_*" segments → existing_competitor_user
    "competitor_user_*": "existing_competitor_user",
    # All other persona segments are evaluator-style buyers
    "trust_seeker": "target_customer_evaluator",
    "price_skeptic": "target_customer_evaluator",
    "performance_focused_buyer": "target_customer_evaluator",
    "convenience_focused_buyer": "target_customer_evaluator",
    "format_focused_buyer": "target_customer_evaluator",
    "objection_focused_buyer": "target_customer_evaluator",
    "use_case_focused_buyer": "target_customer_evaluator",
}


# Per-role default intent labels. Used when synthesizing non-customer
# voices — these labels go through map_assembly_intent_to_market_bucket
# and the role-locked default_bucket is the floor.
_ROLE_DEFAULT_INTENT: dict[AudienceRole, str] = {
    "target_customer_evaluator": "would_consider_if_proven",
    "existing_competitor_user": "loyal_to_current_alternative",
    "proof_seeker_only": "wait_and_see",
    "industry_observer": "wait_and_see",
    "technical_or_legal_explainer": "wait_and_see",
    "meta_commenter": "wait_and_see",
    "category_skeptic": "would_reject",
    "incumbent_defender": "loyal_to_current_alternative",
    "casual_bystander": "wait_and_see",
    "off_topic_noise_candidate": "wait_and_see",
    # Phase 12E.5O — Product Hunt-flavored roles.
    # shallow_positive_commenter is receptive-locked, so the intent
    # must map to a receptive bucket. would_consider_if_proven is the
    # narrowest fit ("looks great" implies mild positive interest,
    # not adoption).
    "shallow_positive_commenter": "would_consider_if_proven",
    # founder_network_supporter is is_scorable=False (noise) and
    # uncertain-locked. wait_and_see is the right intent because the
    # commenter isn't evaluating the product, just supporting the
    # launch.
    "founder_network_supporter": "wait_and_see",
    # early_adopter is bucket-flexible; the default intent maps to
    # receptive, but a downstream router can move it to buyer if the
    # ballot text shows clear adoption language.
    "early_adopter": "would_consider_if_proven",
}


# Per-role evidence_basis template (audit string).
_ROLE_BASIS_TEMPLATE: dict[AudienceRole, str] = {
    "target_customer_evaluator": (
        "rule:audience_role_target_customer_evaluator (legacy_compat)"
    ),
    "existing_competitor_user": (
        "rule:audience_role_existing_competitor_user (legacy_compat)"
    ),
    "proof_seeker_only": (
        "rule:audience_role_proof_seeker_only "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    "industry_observer": (
        "rule:audience_role_industry_observer "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    "technical_or_legal_explainer": (
        "rule:audience_role_technical_or_legal_explainer "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    "meta_commenter": (
        "rule:audience_role_meta_commenter "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    "category_skeptic": (
        "rule:audience_role_category_skeptic "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    "incumbent_defender": (
        "rule:audience_role_incumbent_defender "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    "casual_bystander": (
        "rule:audience_role_casual_bystander "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    "off_topic_noise_candidate": (
        "rule:audience_role_off_topic_noise_candidate "
        "(synthetic_non_customer_voice_phase_12e)"
    ),
    # Phase 12E.5O — Product Hunt-flavored roles.
    "shallow_positive_commenter": (
        "rule:audience_role_shallow_positive_commenter "
        "(synthetic_non_customer_voice_phase_12e5o)"
    ),
    "founder_network_supporter": (
        "rule:audience_role_founder_network_supporter "
        "(synthetic_non_customer_voice_phase_12e5o)"
    ),
    "early_adopter": (
        "rule:audience_role_early_adopter "
        "(synthetic_non_customer_voice_phase_12e5o)"
    ),
}


def assign_audience_role(
    *,
    segment_label: str | None,
    role: str | None = None,
) -> AudienceRole:
    """Heuristic: classify an existing LLM-generated persona into one
    of the 10 audience roles based on its segment_label / role tag.

    All existing personas today are customer-evaluator types, so this
    classifier only distinguishes `existing_competitor_user` from
    `target_customer_evaluator`. Future persona-generation work can
    have the LLM emit `audience_role` directly.
    """
    seg = (segment_label or "").lower()
    if seg.startswith("competitor_user_"):
        return "existing_competitor_user"
    # Direct lookup
    if seg in _SEGMENT_TO_AUDIENCE_ROLE:
        return _SEGMENT_TO_AUDIENCE_ROLE[seg]  # type: ignore[return-value]
    # Role-based fallback
    role_lc = (role or "").lower()
    if role_lc.startswith("competitor_user_"):
        return "existing_competitor_user"
    # Default: everything else is a target customer evaluator.
    return "target_customer_evaluator"


def _stable_synthetic_persona_id(
    *,
    run_scope_id: str,
    audience_role: str,
    index: int,
) -> str:
    """Deterministic UUID5 for a synthetic non-customer persona."""
    return str(uuid5(
        NAMESPACE_OID,
        f"phase_12e|{run_scope_id}|{audience_role}|{index}",
    ))


def _synthesize_non_customer_draft(
    *,
    run_scope_id: str,
    audience_role: AudienceRole,
    index: int,
) -> dict[str, Any]:
    """Emit ONE deterministic non-customer voice as a dict matching
    the SimulatedIntentDraft schema.

    Returned as a dict (not Pydantic) so the orchestrator can carry
    it alongside real `SimulatedIntentDraft` objects without forcing
    a schema upgrade for synthetic voices.
    """
    spec = AUDIENCE_ROLES[audience_role]
    intent_label = _ROLE_DEFAULT_INTENT[audience_role]
    basis = _ROLE_BASIS_TEMPLATE[audience_role]
    return {
        "persona_id": _stable_synthetic_persona_id(
            run_scope_id=run_scope_id,
            audience_role=audience_role,
            index=index,
        ),
        "cohort_id": f"synthetic_{audience_role}",
        "stance_label": "curious_but_unconvinced",
        "simulated_intent": intent_label,
        "intent_strength": "low",
        "switching_status": "weakly_attached_to_alternative",
        "current_alternative": None,
        "conditions_to_buy": [],
        "reason_for_rejection": None,
        "proof_needed": [],
        "evidence_basis": basis,
        "discussion_turn_ids": [],
        "ballot_ids": [],
        "memory_atom_ids": [],
        "confidence": "low",
        "caveat": (
            "Synthetic non-customer source-audience voice (Phase 12E)."
            " Deterministic stub representing the proportional share"
            " of this audience role in the launch-source profile."
            " Does NOT come from an LLM-generated ballot."
        ),
        "intent_signal": None,
        "intent_signal_basis": basis,
        # Phase 12E additional fields, attached via dict (not in
        # SimulatedIntentDraft schema):
        "audience_role": audience_role,
        "is_synthetic_non_customer_voice": True,
        "default_bucket": spec.default_bucket,
        "is_scorable": spec.is_scorable,
    }


def augment_intent_drafts_with_source_audience(
    *,
    intent_drafts: list[SimulatedIntentDraft] | list[dict[str, Any]],
    persona_metadata_by_pid: dict[str, dict[str, Any]] | None,
    launch_source: str | None,
    run_scope_id: str,
    profile_override: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (augmented_drafts, augmentation_audit).

    `augmented_drafts` is a list of dicts (one per voice, including
    the legacy LLM drafts AND any synthesized non-customer voices).
    Each dict carries:
      - all standard SimulatedIntentDraft fields
      - audience_role
      - is_synthetic_non_customer_voice (bool)

    Algorithm:
      1. Classify each legacy draft into an audience_role.
      2. Resolve the launch_source profile.
      3. Compute the implied total source-audience size N such that
         the LLM-generated customer voices (target_customer +
         existing_competitor_user) hit their target share.
      4. For each non-customer role with profile weight > 0,
         synthesize `round(share * N)` voices.

    If `launch_source` resolves to `default`, the profile is
    target-customer-heavy and synthetic injection is small or zero
    (the legacy 24 LLM personas already cover ~95% of the default
    profile). Behavior is effectively identity for default.

    Phase 12E.5B — `profile_override` is an offline-only hook for the
    role-weight recalibration pipeline. When supplied, the augmenter
    uses the override dict instead of looking up `SOURCE_PROFILES`.
    The override must be the SAME shape as the built-in profiles
    (one weight per AudienceRole in [0, 1], sum = 1.0). The pipeline
    does not use this in production paths — `launch_source` remains
    the founder-supplied control.
    """
    src = resolve_launch_source(launch_source)
    if profile_override is not None:
        profile = dict(profile_override)
    else:
        profile = SOURCE_PROFILES[src]
    persona_meta = persona_metadata_by_pid or {}

    # Convert legacy drafts to dicts (so the synthetic + real lists
    # are the same type).
    legacy: list[dict[str, Any]] = []
    for d in intent_drafts:
        if hasattr(d, "model_dump"):
            row = d.model_dump(mode="json")
        else:
            row = dict(d)
        # Heuristic role assignment for legacy personas.
        meta = persona_meta.get(str(row.get("persona_id")), {})
        seg = meta.get("segment_label") or ""
        # Note: persona records don't carry a role directly; we use
        # segment_label as the heuristic.
        ar = assign_audience_role(segment_label=seg)
        row["audience_role"] = ar
        row["is_synthetic_non_customer_voice"] = False
        spec = AUDIENCE_ROLES[ar]
        row["default_bucket"] = spec.default_bucket
        row["is_scorable"] = spec.is_scorable
        legacy.append(row)

    # Customer-voice share of the legacy drafts (target + comp_user).
    n_legacy = len(legacy)
    customer_roles: set[AudienceRole] = {
        "target_customer_evaluator", "existing_competitor_user",
    }
    n_legacy_customer = sum(
        1 for r in legacy if r["audience_role"] in customer_roles
    )
    # Profile's customer share
    profile_customer_share = sum(
        profile[r] for r in customer_roles if r in profile
    )

    augment_audit: dict[str, Any] = {
        "launch_source_used": src,
        "profile_customer_share": profile_customer_share,
        "n_legacy_drafts": n_legacy,
        "n_legacy_customer_voices": n_legacy_customer,
        "n_synthetic_added_by_role": {},
        "total_after_augmentation": n_legacy,
    }

    if (
        src == "default"
        or profile_customer_share >= 0.99
        or n_legacy_customer == 0
    ):
        # No synthetic injection needed.
        return legacy, augment_audit

    # Imply N such that customer voices hit profile_customer_share.
    # n_legacy_customer / N = profile_customer_share
    implied_total = math.ceil(n_legacy_customer / profile_customer_share)

    # Distribute (implied_total - n_legacy) synthetic voices across
    # non-customer roles, weighted by their profile share among
    # non-customer roles.
    non_customer_share_total = sum(
        v for r, v in profile.items() if r not in customer_roles
    )
    if non_customer_share_total <= 0:
        return legacy, augment_audit

    n_to_add_total = max(0, implied_total - n_legacy)
    augmented = list(legacy)
    counts_by_role: dict[str, int] = {}

    # Largest-remainder allocation across non-customer roles.
    raw_counts: dict[AudienceRole, float] = {
        r: (profile[r] / non_customer_share_total) * n_to_add_total
        for r in profile if r not in customer_roles
    }
    floors: dict[AudienceRole, int] = {
        r: int(v) for r, v in raw_counts.items()
    }
    leftover = n_to_add_total - sum(floors.values())
    by_frac = sorted(
        raw_counts.items(),
        key=lambda kv: -(kv[1] - int(kv[1])),
    )
    final_counts: dict[AudienceRole, int] = dict(floors)
    i = 0
    while leftover > 0 and i < len(by_frac) * 4:
        r, _ = by_frac[i % len(by_frac)]
        if profile[r] > 0:
            final_counts[r] += 1
            leftover -= 1
        i += 1

    for role, count in final_counts.items():
        if count <= 0:
            continue
        counts_by_role[role] = count
        for idx in range(count):
            augmented.append(_synthesize_non_customer_draft(
                run_scope_id=run_scope_id,
                audience_role=role,
                index=idx,
            ))

    augment_audit["n_synthetic_added_by_role"] = counts_by_role
    augment_audit["implied_source_audience_size"] = implied_total
    augment_audit["total_after_augmentation"] = len(augmented)
    return augmented, augment_audit


def split_view_distributions(
    augmented_drafts: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Return the 4 founder-report views as bucket counters.

    Views:
      * target_market_reaction — target_customer_evaluator +
        existing_competitor_user only.
      * source_audience_reaction — all scorable roles (i.e.,
        is_scorable=True), including non-customer voices.
      * scorable_market_reaction — same as source_audience_reaction.
      * noise_meta_estimate — non-scorable roles aggregated.

    Each value is `{bucket: count}` with bucket in
    {"buyer","receptive","uncertain","skeptical","noise"}. The
    "noise" key counts the non-scorable voices regardless of role's
    default_bucket.
    """
    from assembly.calibration.market_buckets import (
        map_assembly_intent_to_market_bucket,
        pick_market_bucket_with_role,
    )
    out: dict[str, dict[str, int]] = {
        "target_market_reaction": {
            "buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 0,
        },
        "source_audience_reaction": {
            "buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 0,
        },
        "scorable_market_reaction": {
            "buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 0,
        },
        "noise_meta_estimate": {
            "count": 0,
        },
    }
    customer_roles = {
        "target_customer_evaluator", "existing_competitor_user",
    }
    for d in augmented_drafts:
        role = d.get("audience_role")
        intent_label = d.get("simulated_intent")
        intent_signal = d.get("intent_signal")
        # Use role-aware bucket selection; this respects locked roles
        # (proof_seeker → uncertain, category_skeptic → skeptical, etc).
        bucket, _ = pick_market_bucket_with_role(
            audience_role=role,
            intent_signal=intent_signal,
            intent_label=intent_label,
            intent_signal_routing_enabled=None,
        )
        is_scorable = bool(d.get("is_scorable", True))
        # Target market view: customer roles only
        if role in customer_roles:
            out["target_market_reaction"][bucket] += 1
        # Source audience + scorable
        if is_scorable:
            out["source_audience_reaction"][bucket] += 1
            out["scorable_market_reaction"][bucket] += 1
        else:
            out["noise_meta_estimate"]["count"] += 1
    return out
