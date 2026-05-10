"""Phase 9E — universal deterministic intent inference.

Given a persona's psychology + ballots + discussion behavior + cohort
context, infer ONE simulated intent label from the closed set:

  would_buy_now, would_try_once, would_join_waitlist,
  would_consider_if_proven, would_share_with_friend,
  would_compare_to_current_brand, loyal_to_current_alternative,
  would_reject, would_block

Rules-based, fully deterministic, no random assignment, no LLM.
Universal — no LumaLoop hardcoding.

Every returned `SimulatedIntentDraft` carries:
  - evidence_basis (non-empty list of rule names that fired)
  - confidence (high/medium/low — high when ≥3 rule signals agree)
  - caveat (mandatory disclaimer text)
"""
from __future__ import annotations

import re
from typing import Any

from assembly.sources.intent_layer.schemas import (
    SimulatedIntentDraft,
)


_LOYALTY_TOKENS = (
    "stick with", "stick to", "incumbent", "won't switch",
    "loyal to my", "no reason to change", "what i already have",
    "current alternative", "tried and true", "my current",
    "i prefer my", "already works",
)
_REJECTION_TOKENS = (
    "not interested", "won't buy", "would not buy", "would block",
    "would reject", "no way", "absolutely not", "garbage",
    "worthless", "i don't see the point", "this isn't for me",
)
_PRICE_TOKENS = (
    "expensive", "overpriced", "too much", "cheaper", "for the price",
    "not worth", "value", "$", "afford",
)
_NOVELTY_TOKENS = (
    "new format", "rechargeable", "snap-on", "snap on", "clip-on",
    "clip on", "different approach", "i'd try",
)
_BUY_NOW_TOKENS = (
    "i'd buy", "i would buy", "yes i would", "i'm sold", "let me get",
    "i'll order", "shut up and take",
)
_SHARE_TOKENS = (
    "i'd recommend it", "i would recommend it", "tell my friends",
    "tell my running group", "tell my buddies", "i'd tell people",
    "send this to", "share this with", "forward this to",
)
_WAITLIST_TOKENS = (
    "let me know when", "tell me when it launches", "early access",
    "waitlist", "preorder", "pre-order", "notify me",
)


def _scan(
    text: str | None, tokens: tuple[str, ...],
) -> tuple[int, list[str]]:
    if not text:
        return 0, []
    lowered = text.lower()
    hits = [t for t in tokens if t in lowered]
    return len(hits), hits[:5]


def _psy_label(value_map: dict[str, float], name: str) -> str:
    v = float(value_map.get(name, 0.5))
    if v < 0.4:
        return "low"
    if v > 0.6:
        return "high"
    return "medium"


def _psy_value(value_map: dict[str, float], name: str) -> float:
    return float(value_map.get(name, 0.5))


_LOYALTY_ROLE_PATTERNS = (
    re.compile(r"^competitor_user_", re.I),
    re.compile(r"^trust_seeker$", re.I),
)


def infer_simulated_intent(
    *,
    persona_id: str,
    cohort_id: str | None,
    normalized_role: str,
    psychology_value_map: dict[str, float],
    pre_ballot: dict[str, Any] | None,
    final_ballot: dict[str, Any] | None,
    reflection_ballot: dict[str, Any] | None,
    persona_text_corpus: str,  # concatenated ballots + discussion + memory
    ballot_ids: list[str],
    discussion_turn_ids: list[str],
    memory_atom_ids: list[str],
    cohort_objection_summary: dict[str, int] | None = None,
) -> SimulatedIntentDraft:
    """Deterministic intent classifier. Returns one SimulatedIntentDraft.

    The rule cascade is applied in order; the first matching rule wins.
    Each rule logs its name + matched signals in `evidence_basis` so the
    audit can replay the decision.
    """
    final_stance = (
        (final_ballot or {}).get("private_stance")
        or (pre_ballot or {}).get("private_stance")
        or "needs_more_information"
    )
    pre_stance = (pre_ballot or {}).get("private_stance") or final_stance
    delta = (final_ballot or {}).get("public_private_delta") or "no_change"

    open_v = _psy_value(psychology_value_map, "openness")
    risk_t = _psy_value(psychology_value_map, "risk_tolerance")
    novelty = _psy_value(psychology_value_map, "novelty_seeking")
    trust_t = _psy_value(psychology_value_map, "trust_proof_threshold")
    sis = _psy_value(
        psychology_value_map, "social_influence_susceptibility",
    )
    price_s = _psy_value(psychology_value_map, "price_sensitivity")
    cat_inv = _psy_value(
        psychology_value_map, "category_involvement_or_expertise",
    )

    loyalty_n, loyalty_hits = _scan(persona_text_corpus, _LOYALTY_TOKENS)
    rejection_n, rejection_hits = _scan(persona_text_corpus, _REJECTION_TOKENS)
    price_n, _ = _scan(persona_text_corpus, _PRICE_TOKENS)
    novelty_n, _ = _scan(persona_text_corpus, _NOVELTY_TOKENS)
    buy_n, _ = _scan(persona_text_corpus, _BUY_NOW_TOKENS)
    share_n, _ = _scan(persona_text_corpus, _SHARE_TOKENS)
    waitlist_n, _ = _scan(persona_text_corpus, _WAITLIST_TOKENS)

    # Cohort objection signal (e.g. price_value_concern)
    obj_buckets = cohort_objection_summary or {}
    has_price_objection = obj_buckets.get("price_value_concern", 0) > 0
    has_competitor_objection = (
        obj_buckets.get("competitor_already_solves", 0) > 0
    )

    # Discover current alternative from competitor_user_* role
    current_alt: str | None = None
    if normalized_role.startswith("competitor_user_"):
        suffix = normalized_role.replace("competitor_user_", "")
        current_alt = suffix.replace("_", " ").strip().title() or None

    rule_log: list[str] = []
    intent: str
    strength: str
    switching: str
    conditions: list[str] = []
    proof_needs: list[str] = []
    rejection_reason: str | None = None

    # ---- Rule cascade -------------------------------------------------
    if (
        final_stance == "likely_reject"
        or rejection_n >= 1
    ):
        intent = "would_reject"
        strength = "high" if rejection_n >= 2 else "medium"
        switching = (
            "loyal_to_current_alternative"
            if loyalty_n >= 1 else "refuses_switching"
        )
        rule_log.append(
            f"rule:would_reject (final={final_stance}, "
            f"rejection_tokens={rejection_n})"
        )
        rejection_reason = (
            f"Persona's final stance is '{final_stance}' and rejection "
            f"language matched ({rejection_n} hit(s))."
        )
    elif (
        loyalty_n >= 1
        and final_stance in ("skeptical", "needs_more_information",
                             "curious_but_unconvinced")
    ):
        intent = "loyal_to_current_alternative"
        strength = "high" if loyalty_n >= 2 else "medium"
        switching = "loyal_to_current_alternative"
        rule_log.append(
            f"rule:loyal_to_current_alternative "
            f"(loyalty_tokens={loyalty_n}, final={final_stance})"
        )
        if loyalty_hits:
            conditions.append(
                f"Persona references loyalty markers: {', '.join(loyalty_hits[:3])}"
            )
    elif (
        any(p.match(normalized_role) for p in _LOYALTY_ROLE_PATTERNS)
        and final_stance == "skeptical"
        and not has_price_objection
    ):
        intent = "loyal_to_current_alternative"
        strength = "medium"
        switching = "loyal_to_current_alternative"
        rule_log.append(
            f"rule:loyal_role_skeptical "
            f"(role={normalized_role}, final={final_stance})"
        )
    elif (
        final_stance == "skeptical"
        and (price_s > 0.6 or has_price_objection)
    ):
        intent = "would_compare_to_current_brand"
        strength = "medium"
        switching = "actively_comparing"
        rule_log.append(
            f"rule:would_compare_price (price_sensitivity={price_s:.2f}, "
            f"has_price_obj={has_price_objection})"
        )
        conditions.append(
            "Persona comparing on price; would consider only at lower "
            "price or with stronger value justification."
        )
    elif buy_n >= 1 and final_stance == "interested_if_proven":
        intent = "would_buy_now"
        strength = "high" if buy_n >= 2 else "medium"
        switching = (
            "no_current_alternative"
            if loyalty_n == 0 else "weakly_attached_to_alternative"
        )
        rule_log.append(
            f"rule:would_buy_now (buy_tokens={buy_n}, final={final_stance})"
        )
    elif (
        final_stance == "interested_if_proven"
        and trust_t < 0.5
        and (novelty > 0.55 or risk_t > 0.55 or novelty_n >= 1)
    ):
        intent = "would_try_once"
        strength = "medium"
        switching = (
            "no_current_alternative"
            if loyalty_n == 0 else "actively_comparing"
        )
        rule_log.append(
            f"rule:would_try_once (final={final_stance}, "
            f"trust_proof_threshold={trust_t:.2f}, "
            f"novelty_seeking={novelty:.2f}, risk_tolerance={risk_t:.2f}, "
            f"novelty_tokens={novelty_n})"
        )
        if novelty_n >= 1:
            conditions.append(
                "Persona expressed interest in novel format / rechargeable design."
            )
    elif (
        delta in ("private_acceptance",)
        and sis > 0.55
        and final_stance == "interested_if_proven"
    ):
        intent = "would_join_waitlist"
        strength = "medium"
        switching = "actively_comparing"
        rule_log.append(
            f"rule:would_join_waitlist (private_acceptance + "
            f"social_influence_susceptibility={sis:.2f})"
        )
    elif waitlist_n >= 1:
        intent = "would_join_waitlist"
        strength = "low"
        switching = "actively_comparing"
        rule_log.append(
            f"rule:would_join_waitlist_explicit (waitlist_tokens={waitlist_n})"
        )
    elif share_n >= 1 and final_stance in (
        "interested_if_proven", "curious_but_unconvinced",
    ):
        intent = "would_share_with_friend"
        strength = "medium"
        switching = "weakly_attached_to_alternative"
        rule_log.append(
            f"rule:would_share_with_friend (share_tokens={share_n}, "
            f"final={final_stance})"
        )
    elif final_stance == "interested_if_proven" and trust_t >= 0.5:
        intent = "would_consider_if_proven"
        strength = "medium"
        switching = (
            "actively_comparing"
            if has_competitor_objection else "weakly_attached_to_alternative"
        )
        rule_log.append(
            f"rule:would_consider_if_proven_high_trust "
            f"(final={final_stance}, trust_proof_threshold={trust_t:.2f})"
        )
    elif final_stance in (
        "curious_but_unconvinced", "needs_more_information",
    ):
        intent = "would_consider_if_proven"
        strength = "low"
        switching = "weakly_attached_to_alternative"
        rule_log.append(
            f"rule:would_consider_if_proven_unsure (final={final_stance})"
        )
    else:
        # Catch-all: would_consider_if_proven + low confidence
        intent = "would_consider_if_proven"
        strength = "low"
        switching = "weakly_attached_to_alternative"
        rule_log.append(
            f"rule:default_consider (final={final_stance})"
        )

    # Build proof_needs from final ballot top_proof_need
    if (final_ballot or {}).get("top_proof_need"):
        proof_needs.append(
            (final_ballot or {}).get("top_proof_need")[:240]
        )
    if (pre_ballot or {}).get("top_proof_need"):
        pn = (pre_ballot or {}).get("top_proof_need")[:240]
        if pn and pn not in proof_needs:
            proof_needs.append(pn)

    # Confidence: high if 3+ rule signals agree (final stance + psy +
    # explicit token), medium if 2 signals, low if 1.
    sig_count = (
        (1 if rule_log else 0)
        + (1 if (final_ballot or {}).get("private_stance") else 0)
        + (1 if (pre_ballot or {}).get("private_stance") else 0)
        + (1 if (loyalty_n + buy_n + share_n + waitlist_n) >= 1 else 0)
        + (1 if any(
            abs(_psy_value(psychology_value_map, k) - 0.5) >= 0.15
            for k in ("trust_proof_threshold", "novelty_seeking",
                      "risk_tolerance", "price_sensitivity")
        ) else 0)
    )
    if sig_count >= 4:
        confidence = "high"
    elif sig_count >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    evidence_basis = (
        " | ".join(rule_log)
        + f" | psy_signals: trust_proof_threshold={trust_t:.2f}, "
        f"novelty_seeking={novelty:.2f}, risk_tolerance={risk_t:.2f}, "
        f"price_sensitivity={price_s:.2f}, "
        f"social_influence_susceptibility={sis:.2f}, "
        f"category_involvement_or_expertise={cat_inv:.2f} | "
        f"role={normalized_role} | "
        f"loyalty_hits={loyalty_n} | rejection_hits={rejection_n}"
    )

    caveat = (
        "Synthetic simulated intent — NOT a real-world purchase forecast. "
        "This persona is a run-scoped synthetic agent in an n=66 "
        "simulation. The persona has not bought, used, owned, or "
        "reviewed the unlaunched product."
    )

    return SimulatedIntentDraft(
        persona_id=persona_id,
        cohort_id=cohort_id,
        stance_label=final_stance,  # type: ignore[arg-type]
        simulated_intent=intent,  # type: ignore[arg-type]
        intent_strength=strength,  # type: ignore[arg-type]
        switching_status=switching,  # type: ignore[arg-type]
        current_alternative=current_alt,
        conditions_to_buy=conditions,
        reason_for_rejection=rejection_reason,
        proof_needed=proof_needs,
        evidence_basis=evidence_basis,
        discussion_turn_ids=discussion_turn_ids[:8],
        ballot_ids=ballot_ids[:8],
        memory_atom_ids=memory_atom_ids[:8],
        confidence=confidence,  # type: ignore[arg-type]
        caveat=caveat,
    )
