"""Phase 9E — universal deterministic intent inference.

Given a persona's psychology + ballots + discussion behavior + cohort
context, infer ONE simulated intent label from the closed set:

  would_buy_now, would_try_once, would_join_waitlist,
  would_consider_if_proven, would_share_with_friend,
  would_compare_to_current_brand, loyal_to_current_alternative,
  would_reject, would_block, wait_and_see

Rules-based, fully deterministic, no random assignment, no LLM.
Universal — no LumaLoop / no per-product hardcoding.

Phase 12A.10C — runtime rollback note:
  Phase 12A.10 attempted to route ambiguous personas (final stance
  `curious_but_unconvinced` / `needs_more_information` without
  positive adoption tokens) to a new `wait_and_see` intent →
  `uncertain` bucket, and rerouted the catch-all `else` branch the
  same way. The post-fix Opslane blind rerun (Phase 12A.10) showed
  MAE jumped from 9.40pp → 20.14pp — the cascade overcorrected by
  routing too many personas to uncertain. Offline replay (Phase
  12A.10B) confirmed the regression was cascade-driven and showed
  no token-or-psy-based variant could split the ambig-curious
  population into the observed ~10 receptive / ~4 uncertain mix.

  Phase 12A.10C reverts the two runtime decisions to their pre-12A.10
  shape (both branches → `would_consider_if_proven`). The
  `wait_and_see` label remains in the schema, INTENT_LABELS tuple,
  bucket vocabulary, and DB CHECK constraint — it is structurally
  present and ready for a future explicit `intent_signal` enum
  fix (Phase 12A.10D candidate), but is NOT emitted by the runtime
  cascade in this rollback.

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


# Phase 12A.10D — token banks for derive_intent_signal().
# Designed to disambiguate the three signals the legacy cascade
# collapsed: positive_interest_if_proven vs curious_but_unconvinced
# vs neutral_information_seeking. These are token-only (zero LLM).
_BUY_INTENT_TOKENS = (
    "i'll try", "i'll install", "installing now", "sign me up",
    "we need this", "i need this", "let me get", "i'll order",
    "i'm sold", "shut up and take", "i'd buy", "i would buy",
    "we'll use this", "we'll adopt this", "rolling this out",
    "i'd adopt", "going to use",
)
_TRY_TOKENS = (
    "i'd try", "i would try", "let me try", "i'd test",
    "i'd give it a shot", "willing to try",
    "i'd give this a try",
    # NOTE: "i can see using"/"i can see our team using" were
    # previously in this list but they are soft positives, not
    # commitment to try. They now route via _POSITIVE_INTEREST_TOKENS
    # to receptive instead of buyer.
)
_WAITLIST_INTENT_TOKENS = (
    "sign me up", "let me know when", "tell me when it launches",
    "waitlist", "early access", "preorder", "pre-order",
    "notify me", "keep me posted",
)
_COMPARE_TOKENS = (
    "i'd compare", "would compare", "compare to my", "compare it to",
    "vs my current", "versus my current", "how does it compare",
    "stack up against", "side by side",
)
_POSITIVE_INTEREST_TOKENS_GENERAL = (
    # ---- General positive-interest expressions (category-agnostic) ----
    # These phrases work for consumer goods, marketplaces, education,
    # health, creator tools, B2B SaaS, devtools — anywhere a person
    # might say "I think this is good and would consider it."
    "looks useful", "would be useful", "could be useful",
    "looks promising", "this is promising", "useful if",
    "i can see this working", "this is what i need", "i like this",
    "looks great", "looks cool",
    "i can see using", "i can see our team using",
    "could see using",
    "i'd give it a", "i'd give this a", "give it a look",
    "appealing", "genuinely useful", "genuinely interested",
    "interested but not committed",
    "worth a look", "worth a serious look", "worth a try",
    "worth trying",
)

# ---------------------------------------------------------------------------
# Phase 12A.10D anti-overfitting cleanup — vertical token packs.
#
# Tokens BELOW are devtools/SRE/B2B-tool flavored. They appear
# disproportionately in synthetic personas for engineering / on-call /
# infra products (e.g. Opslane). They are NOT used by default; they
# are gated behind `ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS=devtools_b2b`.
#
# A consumer-product brief (supplements, food, apparel, education,
# creator tools, dating, gaming) would never produce "i'd pilot" or
# "spin it up" — using these tokens by default would silently miss
# positive interest in those categories. Keeping them separated lets
# us measure their contribution and decide per-category.
# ---------------------------------------------------------------------------
_POSITIVE_INTEREST_TOKENS_DEVTOOLS_B2B = (
    "i'd pilot", "i would pilot", "i'd genuinely pilot",
    "i'd seriously pilot", "i'd happily pilot",
    "worth a pilot", "worth piloting", "worth a serious",
    "i'd spin", "i would spin", "spin it up", "spin up",
    "give it a serious look",
    "kick the tires", "kicking the tires",
    "scratches a real itch", "real pain point", "real itch",
    "fits how my team", "matches how my team",
    "matches where my team", "maps to how my team",
    "i'm intrigued", "intrigued enough",
    "i'd seriously", "leaning in",
    "this scratches",
)


def _resolve_vertical_token_pack() -> str:
    """Read the active vertical-token-pack setting at call time.

    Default: 'general'. Allowed: 'general', 'devtools_b2b',
    'auto_disabled_for_now'. Anything else falls back to 'general'.

    Read at call time (not import time) so tests can monkey-patch the
    environment without restarting the process.
    """
    import os
    val = (
        os.environ.get("ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS")
        or "general"
    ).strip().lower()
    if val not in ("general", "devtools_b2b", "auto_disabled_for_now"):
        val = "general"
    return val


def is_intent_signal_routing_enabled() -> bool:
    """Runtime check for whether downstream consumers should prefer
    `intent_signal` over the legacy `intent_label` when both are
    available.

    Default: False until validated against a fresh blind product on
    a different category. Read from `ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED`
    (lowercase strings 'true'/'1'/'yes' count as enabled).
    """
    import os
    val = (
        os.environ.get("ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED") or ""
    ).strip().lower()
    return val in ("true", "1", "yes", "on")


def _active_positive_interest_tokens() -> tuple[str, ...]:
    """Return the active positive-interest token bank. Always returns
    the general tier; adds the devtools_b2b tier only when the env
    var explicitly opts in. Read at call time so tests can
    monkey-patch ENV between calls."""
    pack = _resolve_vertical_token_pack()
    if pack == "devtools_b2b":
        return (
            _POSITIVE_INTEREST_TOKENS_GENERAL
            + _POSITIVE_INTEREST_TOKENS_DEVTOOLS_B2B
        )
    # 'general' or 'auto_disabled_for_now' — general only.
    return _POSITIVE_INTEREST_TOKENS_GENERAL


# Backward-compat alias for existing call sites — points at the
# general pack by default. Use `_active_positive_interest_tokens()`
# for the runtime-resolved version.
_POSITIVE_INTEREST_TOKENS = _POSITIVE_INTEREST_TOKENS_GENERAL
_PROOF_QUESTION_TOKENS = (
    "how does this", "how does it", "how do they", "how do you",
    "how is this", "how is it different", "what's the difference",
    "whats the difference", "show me", "prove it", "demo first",
    "need to see", "would need to see", "see it in production",
    "demo", "evidence", "case study", "case studies", "real example",
    "real examples", "screenshots", "screenshot", "actually work",
    "actually does", "can it handle", "what happens when",
    "what about ", "is there ",
)
_NEUTRAL_INFO_TOKENS = (
    "interesting,", "interesting.", "interesting but",
    "could be", "might be useful", "depends on", "depends if",
    "maybe useful", "maybe not", "not sure if", "i'm not sure",
    "hard to say", "hard to tell", "we'll see", "we'll have to see",
    "remains to be seen", "time will tell", "wait and see",
)
_TRUST_BLOCK_TOKENS = (
    # Require sentence-level expressions of distrust, NOT mere domain
    # mentions. Phrases like "hallucination" or "false positive" are
    # domain terms — they often appear in proof-seeking questions
    # ("how do you measure false positives?") which are uncertain,
    # not trust-blocked.
    "don't trust", "do not trust", "untrustworthy", "trust issue",
    "worried about reliability", "reliability concern",
    "privacy concern", "security concern",
    "won't connect to", "wouldn't connect to",
    "compliance blocker", "not compliant",
)
_PRICE_BLOCK_TOKENS = (
    "too expensive", "not worth", "not worth it", "overpriced",
    "won't pay", "would not pay", "can't afford", "cannot afford",
    "no budget", "out of budget",
)
_NOT_TARGET_TOKENS = (
    "not for me", "doesn't apply", "not my problem",
    "wrong audience", "not our problem", "we don't have this",
    "we don't have this issue", "we don't need this",
    "not a fit",
)
_OFFTOPIC_TOKENS = (
    "cool name", "great name", "love the name", "nice logo",
    "great logo", "off topic", "tangential",
)


def derive_intent_signal(
    *,
    private_stance: str | None,
    private_reasoning: str | None,
    top_objection: str | None,
    top_proof_need: str | None,
    normalized_role: str,
    psychology_value_map: dict[str, float],
    cohort_objection_summary: dict[str, int] | None = None,
    persona_text_corpus: str | None = None,
) -> tuple[str | None, str]:
    """Derive an `IntentSignal` from existing ballot fields. Zero new
    LLM calls. Returns ``(signal, basis_str)``.

    The function applies a deterministic priority order:
      1. Explicit adoption tokens (buy / try / waitlist) → buyer
      2. Hard resistance signals (rejection, loyalty, trust block,
         price block, not_target) → skeptical
      3. Proof-seeking / neutral-information / mixed signals →
         uncertain (THE KEY FIX — these used to fall through to
         receptive in the legacy cascade)
      4. Positive interest / compare-to-current with no explicit
         adoption verb → receptive
      5. Stance-based fallback (curious_but_unconvinced /
         needs_more_information → uncertain; interested_if_proven →
         receptive; skeptical/likely_reject → skeptical)
    """
    # Phase 12A.10D — TWO text views.
    #
    # `private_text` = only the persona's PRIVATE ballot output
    # (private_reasoning, top_objection, top_proof_need). This is the
    # persona's true post-discussion intent.
    #
    # `full_text` = `private_text` + the persona's public discussion
    # turns + memory atoms. This is the FULL signal pool, used only
    # for unambiguous-intent token scanning (loyalty / rejection /
    # explicit adoption verbs). Loyalty/rejection in a public turn IS
    # a real signal; proof-questions in a public turn are NOT (they
    # are normal evaluator behavior).
    private_parts: list[str] = []
    for t in (private_reasoning, top_objection, top_proof_need):
        if t:
            private_parts.append(t)
    private_text = " ".join(private_parts).lower() if private_parts else ""
    full_text = private_text
    if persona_text_corpus:
        full_text = f"{private_text} {persona_text_corpus.lower()}"

    def hit(tokens: tuple[str, ...]) -> int:
        """Token hit on the full corpus — used for loyalty / rejection
        / explicit-adoption token detection where any-source signal is
        meaningful."""
        return sum(1 for t in tokens if t in full_text)

    def hit_private(tokens: tuple[str, ...]) -> int:
        """Token hit restricted to the persona's PRIVATE ballot output.
        Used for proof-question and neutral-info detection: a "how
        does X work?" question in a public discussion turn is normal
        evaluator behavior, not a signal of internal uncertainty.
        Only the persona's private ballot text counts."""
        return sum(1 for t in tokens if t in private_text)

    obj_buckets = cohort_objection_summary or {}
    has_price_objection = obj_buckets.get("price_value_concern", 0) > 0
    has_competitor_objection = (
        obj_buckets.get("competitor_already_solves", 0) > 0
    )

    rule_log: list[str] = []

    # --- 1. Explicit adoption signals (buyer) -----------------------
    if hit(_BUY_INTENT_TOKENS) >= 1:
        rule_log.append("buy_intent_tokens")
        return "explicit_buy_or_use_now", "|".join(rule_log)
    if hit(_TRY_TOKENS) >= 1:
        rule_log.append("try_tokens")
        # Hard-resistance check overrides: if voter has rejection
        # OR strong loyalty signal, a "try" token is sarcasm.
        if hit(_REJECTION_TOKENS) >= 1 or hit(_LOYALTY_TOKENS) >= 2:
            rule_log.append("but_overridden_by_resistance")
        else:
            return "explicit_try_once", "|".join(rule_log)
    if hit(_WAITLIST_INTENT_TOKENS) >= 1:
        rule_log.append("waitlist_intent_tokens")
        return "explicit_waitlist_or_signup", "|".join(rule_log)

    # --- 2. Hard resistance signals (skeptical) --------------------
    if private_stance == "likely_reject" or hit(_REJECTION_TOKENS) >= 1:
        rule_log.append(
            f"rejection (stance={private_stance}, "
            f"tokens={hit(_REJECTION_TOKENS)})"
        )
        return "explicit_rejection", "|".join(rule_log)
    # Loyalty detection: an explicit COMPARE verb ("would compare",
    # "i'd compare") overrides loyalty token matches — references to
    # "my current tool" inside a comparison sentence are neutral, not
    # lock-in. Without this guard, "I would compare this to my
    # current tool" gets misclassified as competitor_loyal.
    has_compare_verb = (
        hit(_COMPARE_TOKENS) >= 1
        or "i would compare" in full_text
        or "i'll compare" in full_text
    )
    if (
        hit(_LOYALTY_TOKENS) >= 1
        and not has_compare_verb
        and private_stance in (
            "skeptical", "needs_more_information",
            "curious_but_unconvinced",
        )
    ):
        rule_log.append(
            f"loyalty (stance={private_stance}, "
            f"tokens={hit(_LOYALTY_TOKENS)})"
        )
        return "competitor_loyal", "|".join(rule_log)
    if hit(_TRUST_BLOCK_TOKENS) >= 1:
        rule_log.append(f"trust_block (tokens={hit(_TRUST_BLOCK_TOKENS)})")
        return "trust_blocked", "|".join(rule_log)
    if hit(_PRICE_BLOCK_TOKENS) >= 1 or (
        private_stance == "skeptical" and has_price_objection
    ):
        rule_log.append("price_block")
        return "price_blocked", "|".join(rule_log)
    if hit(_NOT_TARGET_TOKENS) >= 1:
        rule_log.append("not_target_tokens")
        return "not_target_customer", "|".join(rule_log)

    # --- 3a. Explicit compare-to-current (receptive) ---------------
    # Done BEFORE the stance-driven uncertain branches so an explicit
    # comparison verb wins over a default curious_but_unconvinced
    # stance with "my current" loyalty noise.
    if has_compare_verb and hit(_LOYALTY_TOKENS) <= 1:
        rule_log.append("explicit_compare_verb_pre_stance")
        return "would_compare_to_current_tool", "|".join(rule_log)

    # --- 3b. Uncertain signals (the fix) ---------------------------
    # Proof-seeking questions are the canonical "uncertain" — the
    # voter is information-gathering, NOT signaling positive interest.
    # Use hit_private (NOT full corpus) so public-discussion
    # evaluator-questions don't false-positive every persona.
    proof_q = hit_private(_PROOF_QUESTION_TOKENS)
    neutral_info = hit_private(_NEUTRAL_INFO_TOKENS)
    pos_interest = hit(_active_positive_interest_tokens())
    if (
        (proof_q >= 2 or (proof_q >= 1 and neutral_info >= 1))
        and pos_interest == 0
    ):
        rule_log.append(
            f"proof_seeking (q={proof_q}, neutral={neutral_info})"
        )
        return "needs_more_information", "|".join(rule_log)
    if neutral_info >= 1 and pos_interest == 0:
        rule_log.append(
            f"neutral_info_seeking (neutral={neutral_info})"
        )
        return "neutral_information_seeking", "|".join(rule_log)
    # Stance-driven uncertain — the legacy bug.
    if (
        private_stance == "needs_more_information"
        and pos_interest == 0
    ):
        rule_log.append("stance=needs_more_information_no_positive")
        return "needs_more_information", "|".join(rule_log)
    if (
        private_stance == "curious_but_unconvinced"
        and pos_interest == 0
        and hit(_BUY_NOW_TOKENS) == 0
        and proof_q == 0
    ):
        rule_log.append("stance=curious_but_unconvinced_no_positive")
        return "curious_but_unconvinced", "|".join(rule_log)

    # Mixed/ambiguous: stance says positive but text doesn't agree
    if (
        private_stance == "interested_if_proven"
        and hit(_active_positive_interest_tokens()) == 0
        and proof_q == 0
        and not any(t in full_text for t in (
            "useful", "promising", "would consider",
        ))
    ):
        rule_log.append("mixed_ambiguous (stance positive, text neutral)")
        return "mixed_or_ambiguous", "|".join(rule_log)

    # --- 4. Receptive signals --------------------------------------
    if hit(_active_positive_interest_tokens()) >= 1:
        rule_log.append(f"positive_interest (tokens={hit(_active_positive_interest_tokens())})")
        return "positive_interest_if_proven", "|".join(rule_log)
    if hit(_COMPARE_TOKENS) >= 1 and hit(_LOYALTY_TOKENS) == 0:
        rule_log.append("compare_to_current")
        return "would_compare_to_current_tool", "|".join(rule_log)
    if private_stance == "interested_if_proven":
        rule_log.append("stance=interested_if_proven_default")
        return "positive_interest_if_proven", "|".join(rule_log)

    # --- 5. Stance fallback ----------------------------------------
    if private_stance == "skeptical":
        rule_log.append("stance=skeptical_default")
        return "explicit_rejection", "|".join(rule_log)
    # Final catch-all: if we got here, we have an empty/weak ballot.
    # Route to uncertain rather than receptive (the previous catch-
    # all bug). This is the load-bearing change.
    rule_log.append(f"final_catchall (stance={private_stance})")
    return "curious_but_unconvinced", "|".join(rule_log)


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
        # Phase 12A.10C rollback: restored pre-12A.10 behavior.
        # The 12A.10 attempt to split this branch into wait_and_see /
        # would_consider_if_proven was reverted after Opslane MAE
        # regressed from 9.40pp → 20.14pp. Offline replay (12A.10B)
        # showed no token/psy-based variant could split this
        # population correctly; the proper fix is upstream
        # (explicit intent_signal enum on the final ballot), not in
        # the cascade.
        intent = "would_consider_if_proven"
        strength = "low"
        switching = "weakly_attached_to_alternative"
        rule_log.append(
            f"rule:would_consider_if_proven_unsure "
            f"(final={final_stance})"
        )
    else:
        # Phase 12A.10C rollback: restored pre-12A.10 catch-all.
        # The 12A.10 attempt to route this branch to wait_and_see
        # was reverted. See branch-comment above. The catch-all
        # remains receptive-leaning by design — every prior rule
        # has rejected the persona's signals, so the only neutral
        # destination consistent with the rest of the cascade is
        # `would_consider_if_proven`.
        intent = "would_consider_if_proven"
        strength = "low"
        switching = "weakly_attached_to_alternative"
        rule_log.append(
            f"rule:default_would_consider_if_proven "
            f"(final={final_stance})"
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

    # Phase 12A.10D — derive intent_signal from existing ballot
    # fields. Always computed (for diagnostics); the bucket mapper
    # consults `intent_signal` first when the runtime config flag is
    # on, otherwise falls back to the legacy `simulated_intent` label.
    intent_signal_val, intent_signal_basis_val = derive_intent_signal(
        private_stance=final_stance,
        private_reasoning=(
            (final_ballot or {}).get("private_reasoning")
            or (pre_ballot or {}).get("private_reasoning")
        ),
        top_objection=(final_ballot or {}).get("top_objection"),
        top_proof_need=(final_ballot or {}).get("top_proof_need"),
        normalized_role=normalized_role,
        psychology_value_map=psychology_value_map,
        cohort_objection_summary=cohort_objection_summary,
        persona_text_corpus=persona_text_corpus,
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
        intent_signal=intent_signal_val,  # type: ignore[arg-type]
        intent_signal_basis=(
            f"phase_12a_10d|{intent_signal_basis_val}"[:400]
            if intent_signal_basis_val else None
        ),
    )
