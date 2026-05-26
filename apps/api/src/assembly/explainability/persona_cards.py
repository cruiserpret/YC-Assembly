"""Phase 12F.1 — persona reasoning cards.

N representative persona cards (default N=8) stratified across
audience_role with one "shifter" + one "sticky" per role where
possible. Every field on every card is sourced to an existing
artifact — no chain-of-thought leakage, no fabricated rationale.

Cards with required fields unsourced are DROPPED (we'd rather show
fewer honest cards than synthesize plausible-sounding fillers).
"""
from __future__ import annotations

from typing import Any


# Per-stance ordering used to detect "shifts": higher index = more
# positive. A shift from skeptical → likely_accept is large; from
# curious → interested_if_proven is small but still a shift.
_STANCE_ORDER: dict[str, int] = {
    "likely_reject": 0,
    "skeptical": 1,
    "needs_more_information": 2,
    "curious_but_unconvinced": 3,
    "interested_if_proven": 4,
    "likely_accept": 5,
}


def _stance_idx(stance: str | None) -> int | None:
    if stance is None:
        return None
    return _STANCE_ORDER.get(stance)


def _final_bucket_for(
    draft: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Return (bucket, routing_note). Returns (None, None) if mapping
    fails (treated as unsourced)."""
    try:
        from assembly.calibration.market_buckets import (
            pick_market_bucket_with_role,
        )
        bucket, note = pick_market_bucket_with_role(
            audience_role=draft.get("audience_role"),
            intent_signal=draft.get("intent_signal"),
            intent_label=draft.get("simulated_intent"),
            intent_signal_routing_enabled=None,
        )
        return bucket, note
    except Exception:
        return None, None


def _movement_summary(
    *,
    pre: dict[str, Any] | None,
    final: dict[str, Any] | None,
    public_private_delta: str | None,
) -> tuple[str | None, str | None]:
    """Return (summary_text, triggered_by_kind). Both None if no
    source data is available — caller drops the card."""
    if not (pre and final):
        return None, None
    pre_stance = pre.get("private_stance")
    final_stance = final.get("private_stance")
    pre_idx = _stance_idx(pre_stance)
    final_idx = _stance_idx(final_stance)
    if pre_idx is None or final_idx is None:
        return None, None
    if final_idx > pre_idx:
        return (
            f"Moved from `{pre_stance}` to `{final_stance}` over the "
            "course of the synthetic discussion.",
            "private_stance_delta",
        )
    if final_idx < pre_idx:
        return (
            f"Regressed from `{pre_stance}` to `{final_stance}` during "
            "the discussion (objections compounded).",
            "private_stance_delta",
        )
    # No stance change.
    if public_private_delta and public_private_delta != "no_change":
        return (
            f"Stance held at `{pre_stance}`; public-private delta "
            f"`{public_private_delta}` observed.",
            "public_private_delta",
        )
    return (
        f"Stayed at `{pre_stance}` across the synthetic discussion "
        "(no stance movement, no public-private delta).",
        "no_change",
    )


def _build_one_card(
    *,
    draft: dict[str, Any],
    pre: dict[str, Any] | None,
    final: dict[str, Any] | None,
    persona_meta: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Build a single card or return None to indicate it should be
    dropped (insufficient sourced data)."""
    pid = str(draft.get("persona_id") or "")
    if not pid:
        return None
    audience_role = draft.get("audience_role")
    if not audience_role:
        return None
    final_bucket, routing_note = _final_bucket_for(draft)
    if not final_bucket:
        return None
    initial_stance = (pre or {}).get("private_stance")
    final_stance = (final or {}).get("private_stance")
    if not initial_stance or not final_stance:
        return None
    movement_text, triggered_by_kind = _movement_summary(
        pre=pre,
        final=final,
        public_private_delta=(final or {}).get("public_private_delta"),
    )
    if not movement_text:
        return None
    evidence_basis = draft.get("evidence_basis") or (
        draft.get("intent_signal_basis") if draft.get("intent_signal_basis") else None
    )
    if not evidence_basis:
        return None
    top_objection_text = (
        (final or {}).get("top_objection")
        or (pre or {}).get("top_objection")
    )
    top_proof_text = (
        (final or {}).get("top_proof_need")
        or (pre or {}).get("top_proof_need")
    )
    if not top_objection_text and not top_proof_text:
        # A card with neither objection nor proof need has no
        # actionable content — drop.
        return None
    conditions = draft.get("conditions_to_buy") or []
    adoption_trigger = conditions[0] if conditions else None
    proof_needed = draft.get("proof_needed") or []
    rfr = draft.get("reason_for_rejection")
    if final_bucket == "skeptical" and rfr:
        stayed_because = rfr
    elif final_bucket == "uncertain":
        stayed_because = (
            proof_needed[0] if proof_needed
            else "no specific blocker articulated — voice remains evaluative-passive"
        )
    elif final_bucket == "receptive":
        stayed_because = (
            "remains receptive but not committed; conditions to buy "
            "not fully satisfied yet" if conditions else None
        )
    else:
        stayed_because = None
    segment_label = (persona_meta.get(pid, {}) or {}).get("segment_label")
    confidence_in_persona = draft.get("confidence") or "low"
    card: dict[str, Any] = {
        "persona_id": pid,
        "audience_role": audience_role,
        "segment_label": segment_label,
        "is_synthetic_non_customer_voice": bool(
            draft.get("is_synthetic_non_customer_voice", False),
        ),
        "initial_stance": initial_stance,
        "final_stance": final_stance,
        "final_bucket": final_bucket,
        "top_objection": (
            {"text": top_objection_text, "evidence_anchor": evidence_basis}
            if top_objection_text else None
        ),
        "top_proof_need": (
            {"text": top_proof_text, "evidence_anchor": evidence_basis}
            if top_proof_text else None
        ),
        "what_moved_or_failed_to_move_them": {
            "summary": movement_text,
            "triggered_by_kind": triggered_by_kind,
            "evidence_anchor": evidence_basis,
        },
        "adoption_trigger": (
            {"text": adoption_trigger, "evidence_anchor": evidence_basis}
            if adoption_trigger else None
        ),
        "stayed_x_because": (
            {"text": stayed_because, "evidence_anchor": evidence_basis}
            if stayed_because else None
        ),
        "confidence_in_this_persona": confidence_in_persona,
        "bucket_routing_note": routing_note,
    }
    return card


def _select_personas_for_cards(
    *,
    drafts: list[dict[str, Any]],
    pre_dicts: dict[str, dict[str, Any]],
    final_dicts: dict[str, dict[str, Any]],
    n_target: int,
) -> list[dict[str, Any]]:
    """Stratify selection: for each audience_role with ≥1 persona,
    pick at most one shifter (largest |Δstance|) and one sticky
    (no stance change). Cap total at n_target. Deterministic order
    (by persona_id) to make tests reproducible."""
    by_role: dict[str, list[dict[str, Any]]] = {}
    for d in drafts:
        role = d.get("audience_role") or "unknown"
        # Skip synthetic non-customer voices — they have no ballots
        # and would always be dropped at card-build time. They appear
        # in persona_composition counts but not as reasoning cards.
        if d.get("is_synthetic_non_customer_voice"):
            continue
        by_role.setdefault(role, []).append(d)
    selected: list[dict[str, Any]] = []
    role_iter = sorted(by_role.keys())
    for role in role_iter:
        bucket = by_role[role]
        # Sort by persona_id for determinism
        bucket_sorted = sorted(
            bucket, key=lambda d: str(d.get("persona_id") or ""),
        )
        shifter: dict[str, Any] | None = None
        sticky: dict[str, Any] | None = None
        shifter_delta = -1
        for d in bucket_sorted:
            pid = str(d.get("persona_id") or "")
            pre = pre_dicts.get(pid)
            final = final_dicts.get(pid)
            if not (pre and final):
                continue
            pre_idx = _stance_idx(pre.get("private_stance"))
            final_idx = _stance_idx(final.get("private_stance"))
            if pre_idx is None or final_idx is None:
                continue
            delta = abs(final_idx - pre_idx)
            if delta > shifter_delta:
                shifter_delta = delta
                if delta > 0:
                    shifter = d
            if delta == 0 and sticky is None:
                sticky = d
        for candidate in (shifter, sticky):
            if candidate is not None and candidate not in selected:
                selected.append(candidate)
            if len(selected) >= n_target:
                return selected
    # Pad with remaining personas (sorted) if under target.
    if len(selected) < n_target:
        seen_pids = {str(d.get("persona_id")) for d in selected}
        for d in sorted(
            drafts, key=lambda d: str(d.get("persona_id") or ""),
        ):
            if d.get("is_synthetic_non_customer_voice"):
                continue
            if str(d.get("persona_id")) in seen_pids:
                continue
            selected.append(d)
            if len(selected) >= n_target:
                break
    return selected


def build_persona_reasoning_cards(
    *,
    ctx: dict[str, Any],
    n: int = 8,
) -> list[dict[str, Any]]:
    """Return the list of card dicts. Cards lacking sourced fields
    are dropped silently — output length may be less than `n`."""
    drafts = ctx.get("augmented_intent_drafts") or []
    if not drafts:
        # Fallback: derive minimal drafts from legacy intent_drafts so
        # cards still render on pre-Phase-12E runs.
        legacy = ctx.get("intent_drafts") or []
        drafts = []
        persona_meta = ctx.get("persona_meta") or {}
        for d in legacy:
            pid_str = str(getattr(d, "persona_id", ""))
            row = (
                d.model_dump(mode="json")
                if hasattr(d, "model_dump") else dict(d)
            )
            row.setdefault("persona_id", pid_str)
            row["audience_role"] = (
                "target_customer_evaluator"  # legacy fallback
            )
            row["is_synthetic_non_customer_voice"] = False
            row["is_scorable"] = True
            drafts.append(row)
    pre_dicts = ctx.get("pre_dicts") or {}
    final_dicts = ctx.get("final_dicts") or {}
    persona_meta = ctx.get("persona_meta") or {}
    selected = _select_personas_for_cards(
        drafts=drafts,
        pre_dicts=pre_dicts,
        final_dicts=final_dicts,
        n_target=max(1, n),
    )
    cards: list[dict[str, Any]] = []
    for d in selected:
        pid_str = str(d.get("persona_id") or "")
        card = _build_one_card(
            draft=d,
            pre=pre_dicts.get(pid_str),
            final=final_dicts.get(pid_str),
            persona_meta=persona_meta,
        )
        if card is not None:
            cards.append(card)
    return cards
