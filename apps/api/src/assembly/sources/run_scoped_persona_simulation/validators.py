"""Phase 8.5E — universal validators for run-scoped simulation outputs.

  * `scan_unlaunched_product_use_claims` — detects fabricated direct-
    use claims for an unlaunched product. Parameterized by
    `product_name`. Universal across products.
  * `scan_forecast_or_verdict_claims` — detects buy-percentages,
    market-share claims, "will/won't launch" verdicts, "X% of market"
    statements. Universal — never product-specific.
  * `validate_market_entry_stance_label` — closed-set check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from assembly.sources.run_scoped_persona_simulation.schemas import (
    MARKET_ENTRY_STANCES,
)


# Universal forbidden-phrase templates for fake unlaunched-product use.
# Parameterized by the {p} placeholder which is `re.escape(product_name)`.
_FAKE_USE_TEMPLATES: tuple[str, ...] = (
    r"\b{p} buyer\b",
    r"\b{p} customer\b",
    r"\b{p} user\b",
    r"\b{p} reviewer\b",
    r"\b{p} loyalist\b",
    r"\bi (bought|tried|used|own|purchased|am using) {p}\b",
    r"\bmy {p}\b",
    r"\b{p} works (great|well|amazingly)\b",
    r"\bi('?ve| have) (used|bought|tried) {p}\b",
    r"\b{p} (has|had) been (great|amazing|terrible|disappointing)\b",
    r"\brepeat (purchase|buyer) of {p}\b",
)


# Universal forbidden-phrase templates for forecast / verdict claims.
# These must NEVER appear in a calibrated buyer-state simulation
# output regardless of product or persona.
_FORECAST_PATTERNS: tuple[re.Pattern, ...] = (
    # buy / adoption percentages
    re.compile(
        r"\b(\d{1,3}(?:\.\d+)?%) of (the |our |this )?"
        r"(market|customers|buyers|consumers|users|audience)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,3}(?:\.\d+)?%) (will|would|are likely to|are going to)"
        r" (buy|purchase|adopt|switch|convert|use)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(adoption rate|conversion rate|cac|ltv) (will|would|is)"
        r" (around|approximately|about|roughly)? \d",
        re.IGNORECASE,
    ),
    # market-size forecast
    re.compile(
        r"\bmarket (size|share) (will|would|is) "
        r"(\$[\d.,]+|\d+(\.\d+)?[mb]\b)",
        re.IGNORECASE,
    ),
    # absolute launch verdicts (not "should consider", but "should")
    re.compile(
        r"\b(should|shouldn't|do not|don't) (launch|build|ship) "
        r"(this|the product|stride|.{0,40} product)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(this product will|the market will|consumers will) "
        r"(succeed|fail|crush|dominate|win|lose)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(launch|build|kill|pivot|ship)\s+(it|this|the product)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(go-to-market|gtm)\s+(verdict|recommendation):\s*(launch|kill|pivot)\b",
        re.IGNORECASE,
    ),
    # "the market is positive/negative" objective sentiment
    re.compile(
        r"\b(the market|the public|consumers|buyers) (is|are) "
        r"(positive|negative|bullish|bearish|enthusiastic|hostile)",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class UniversalClaimValidationResult:
    is_valid: bool
    forbidden_phrases_matched: list[str]
    rejection_reason: str | None  # short label
    explanation: str


def _build_fake_use_patterns(product_name: str) -> list[re.Pattern]:
    p = re.escape(product_name.lower())
    pats: list[re.Pattern] = []
    for tmpl in _FAKE_USE_TEMPLATES:
        pats.append(re.compile(tmpl.format(p=p)))
    # also first-word match (e.g., "Stride" of "StrideShield")
    if product_name and " " not in product_name:
        # already a single token — first-word match would be itself
        pass
    else:
        first = re.escape(product_name.split()[0].lower()) if product_name else ""
        if first and first != p:
            for tmpl in _FAKE_USE_TEMPLATES:
                pats.append(re.compile(tmpl.format(p=first)))
    return pats


def scan_unlaunched_product_use_claims(
    *, text: str, product_name: str,
) -> UniversalClaimValidationResult:
    """Return validation result. `is_valid=False` iff text contains
    a phrase implying direct use/purchase/review of the unlaunched
    `product_name`."""
    if not text:
        return UniversalClaimValidationResult(
            is_valid=True, forbidden_phrases_matched=[],
            rejection_reason=None,
            explanation="empty text — nothing to validate",
        )
    pats = _build_fake_use_patterns(product_name)
    low = text.lower()
    matches: list[str] = []
    for p in pats:
        if p.search(low):
            matches.append(p.pattern)
    if matches:
        return UniversalClaimValidationResult(
            is_valid=False,
            forbidden_phrases_matched=matches,
            rejection_reason="fabricated_unlaunched_target_product_use",
            explanation=(
                f"Text contains phrases implying direct "
                f"{product_name!r} usage. Universal scanner."
            ),
        )
    return UniversalClaimValidationResult(
        is_valid=True, forbidden_phrases_matched=[],
        rejection_reason=None,
        explanation="passes universal launch-state validator",
    )


def scan_forecast_or_verdict_claims(
    *, text: str,
) -> UniversalClaimValidationResult:
    """Return validation result. `is_valid=False` iff text contains
    a buy-percentage, market-size, or launch-verdict claim. Universal
    — never product-specific."""
    if not text:
        return UniversalClaimValidationResult(
            is_valid=True, forbidden_phrases_matched=[],
            rejection_reason=None,
            explanation="empty text — nothing to validate",
        )
    matches: list[str] = []
    for p in _FORECAST_PATTERNS:
        if p.search(text):
            matches.append(p.pattern)
    if matches:
        return UniversalClaimValidationResult(
            is_valid=False,
            forbidden_phrases_matched=matches,
            rejection_reason="fake_forecast_or_verdict_claim",
            explanation=(
                "Text contains forecast / market-size / launch-"
                "verdict claims. Universal scanner."
            ),
        )
    return UniversalClaimValidationResult(
        is_valid=True, forbidden_phrases_matched=[],
        rejection_reason=None,
        explanation="passes universal forecast/verdict scanner",
    )


def validate_market_entry_stance_label(
    label: str,
) -> UniversalClaimValidationResult:
    """Closed-set validation. `is_valid=True` iff label is in the
    `MARKET_ENTRY_STANCES` tuple."""
    if label in MARKET_ENTRY_STANCES:
        return UniversalClaimValidationResult(
            is_valid=True, forbidden_phrases_matched=[],
            rejection_reason=None,
            explanation=f"stance label {label!r} is allowed",
        )
    return UniversalClaimValidationResult(
        is_valid=False, forbidden_phrases_matched=[label],
        rejection_reason="invalid_market_entry_stance_label",
        explanation=(
            f"Stance label {label!r} not in allowed set "
            f"{MARKET_ENTRY_STANCES!r}."
        ),
    )
