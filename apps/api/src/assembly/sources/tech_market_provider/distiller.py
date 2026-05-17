"""Phase 11D.1 — tech-market signal distiller.

Takes a raw piece of source text (e.g. a SaaS review body, a HN
thread comment, an App Store review) and emits zero or more
structured `DistilledTechSignal` rows. Distillation is rule-based
and deterministic — Phase 11D.1 ships a stub that uses keyword
heuristics; Phase 11D.2 will swap in real provider-specific
distillers.

Safety properties (carried over from the Amazon distiller):

  * Snippet output is hard-capped at 240 chars. The raw body never
    leaves this module — only the distilled snippet is returned.
  * No user IDs / handles / author names are persisted. If the
    provider passes one as `author_handle`, the distiller IGNORES
    it (only used in tests to confirm the field is dropped).
  * `relevance_score` is OPTIONAL and defaults to None — Phase 11D.1
    does not score; Phase 11D.3+ will plug in a brief-aware scorer.

Pure function, no I/O, no LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from assembly.sources.tech_market_provider.signal_types import (
    BUYER_TYPES,
    BuyerType,
    MARKET_CONTEXTS,
    MarketContext,
    SentimentBucket,
    SIGNAL_TYPES,
    SignalType,
)


# Per-signal-type keyword cues. Heuristics, not ML. Each line below
# is a tuple of (signal_type, regex) — first match wins. The order
# matters: more-specific signal types (procurement_friction) come
# before broader ones (pain_urgency) so a procurement complaint
# doesn't get mislabeled as generic pain.
_SIGNAL_RULES: tuple[tuple[SignalType, re.Pattern[str]], ...] = (
    (
        "procurement_friction",
        re.compile(
            r"\b(procurement|legal review|vendor approval|"
            r"security questionnaire|SOC ?2|MSA|"
            r"compliance review|finance team blocked)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "trust_security_concern",
        re.compile(
            r"\b(privacy|security|GDPR|PII|leak|breach|"
            r"data residency|encryption|exfiltrat|"
            r"sketchy|scam|untrust)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "integration_friction",
        re.compile(
            r"\b(API|webhook|integration|SDK|SSO|"
            r"OAuth|connector|sync|connector broke|"
            r"didn'?t integrate|won'?t connect)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "onboarding_friction",
        re.compile(
            r"\b(onboarding|setup|install|getting started|"
            r"first[- ]?run|tutorial|docs were|hard to use|"
            r"figure out)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "support_complaint",
        re.compile(
            r"\b(support ticket|no response|customer service|"
            r"help desk|support was|nobody replied|ghosted)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pricing_objection",
        re.compile(
            r"\b(too expensive|overpriced|cost too much|"
            r"per[- ]seat|enterprise pricing|cheaper alternative|"
            r"price hike|raised prices)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "willingness_to_pay",
        re.compile(
            r"\b(would pay|happy to pay|gladly pay|"
            r"I'?d pay|I'?d (happily|gladly)\s+pay|"
            r"worth (every|the)|"
            r"paid for it because)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "switching_objection",
        re.compile(
            r"\b(switch(ed|ing|es)?|migrat(ed|ing)?|"
            r"moved away|left for|replaced with|"
            r"moving back|kept using)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "competitor_comparison",
        re.compile(
            r"\b(vs\.?|compared to|better than|worse than|"
            r"compared with|alternative to)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "developer_skepticism",
        re.compile(
            r"\b(toy|hello world|prototype quality|"
            r"not production[- ]ready|stack overflow|"
            r"docs are wrong|black box)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "feature_not_company_risk",
        re.compile(
            r"\b(just a feature|one-trick|big[- ]co will|"
            r"google could|microsoft could|easily replicated|"
            r"thin wrapper)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "nice_to_have_risk",
        re.compile(
            r"\b(nice[- ]to[- ]have|not mission critical|"
            r"first to cut|budget cut|low priority|"
            r"vitamin not painkiller)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "workflow_fit",
        re.compile(
            r"\b(workflow|day[- ]to[- ]day|"
            r"slot(s|ted)? into|fit our process|"
            r"hand[- ]?off)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pain_urgency",
        re.compile(
            r"\b(urgent|need this yesterday|critical pain|"
            r"painful|massive headache|hair on fire|"
            r"on fire|deadline)\b",
            re.IGNORECASE,
        ),
    ),
)


# Coarse buyer-type cues. The order matters: more-specific roles
# (founder, investor) come before broader ones (developer, user).
_BUYER_TYPE_RULES: tuple[tuple[BuyerType, re.Pattern[str]], ...] = (
    (
        "investor",
        re.compile(
            r"\b(VC|venture capital|fund(ing|ed)|portfolio|"
            r"YC partner|partner at|series ?[A-D])\b",
            re.IGNORECASE,
        ),
    ),
    (
        "founder",
        re.compile(
            r"\b(founder|cofounder|co-founder|CEO|"
            r"started (a|my) company|bootstrapp)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "admin",
        re.compile(
            r"\b(IT admin|sysadmin|workspace admin|"
            r"tenant admin|account admin|admin console)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "developer",
        re.compile(
            r"\b(developer|engineer|API|SDK|integrate|"
            r"github|stack overflow|webhook|backend|frontend)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "buyer",
        re.compile(
            r"\b(procurement|purchas(e|ing)|signed the contract|"
            r"renew(al|ed)|RFP|approved the budget|"
            r"VP of|director of)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "user",
        re.compile(
            r"\b(I use|day[- ]to[- ]day|every day|"
            r"my daily|workflow)\b",
            re.IGNORECASE,
        ),
    ),
)


# Market-context cues. Same order-matters logic.
_MARKET_CONTEXT_RULES: tuple[
    tuple[MarketContext, re.Pattern[str]], ...
] = (
    (
        "AI_tool",
        re.compile(
            r"\b(LLM|GPT|Claude|Gemini|AI model|"
            r"prompt(s|ing)?|inference|fine[- ]?tun)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "devtool",
        re.compile(
            r"\b(developer|API|SDK|CLI|webhook|"
            r"open[- ]?source|GitHub|npm|pypi)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "marketplace",
        re.compile(
            r"\b(marketplace|two[- ]?sided|supply side|"
            r"demand side|seller|listing|takerate)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "B2B",
        re.compile(
            r"\b(B2B|enterprise|company plan|team plan|"
            r"procurement|SSO|seat license)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "B2C",
        re.compile(
            r"\b(B2C|consumer|app store|play store|"
            r"household|family plan)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prosumer",
        re.compile(
            r"\b(prosumer|power user|indie|solo entrepreneur|"
            r"creator)\b",
            re.IGNORECASE,
        ),
    ),
)


_SNIPPET_HARD_CAP = 240


def _cap_snippet(text: str) -> str:
    """Strip whitespace, collapse internal whitespace, and hard-cap
    at the persona-grade snippet length."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= _SNIPPET_HARD_CAP:
        return cleaned
    return cleaned[: _SNIPPET_HARD_CAP - 1].rstrip() + "…"


def _infer_sentiment(text: str, signal_type: SignalType) -> SentimentBucket:
    """Coarse sentiment heuristic. Most signal_types we care about
    are negative-leaning by definition (objections, frictions,
    complaints); a few are positive-leaning (willingness_to_pay,
    workflow_fit)."""
    text_lower = (text or "").lower()
    positive_hits = sum(
        1 for kw in (
            "love", "great", "amazing", "happy", "fantastic", "worth",
            "perfect", "smooth",
        ) if kw in text_lower
    )
    negative_hits = sum(
        1 for kw in (
            "hate", "broken", "terrible", "frustrat", "bad",
            "disappointed", "useless",
        ) if kw in text_lower
    )
    if signal_type in {"willingness_to_pay", "workflow_fit"}:
        if negative_hits > positive_hits:
            return "mixed"
        return "positive"
    if positive_hits > 0 and negative_hits > 0:
        return "mixed"
    if positive_hits > 0:
        return "positive"
    # Default negative for objection-flavored types.
    return "negative"


def _classify_signal_type(text: str) -> SignalType | None:
    for sig_type, pattern in _SIGNAL_RULES:
        if pattern.search(text or ""):
            return sig_type
    return None


def _classify_buyer_type(text: str) -> BuyerType:
    for buyer, pattern in _BUYER_TYPE_RULES:
        if pattern.search(text or ""):
            return buyer
    return "unknown"


def _classify_market_context(
    text: str,
    *,
    hint: MarketContext | None = None,
) -> MarketContext:
    if hint and hint in MARKET_CONTEXTS:
        return hint
    for ctx, pattern in _MARKET_CONTEXT_RULES:
        if pattern.search(text or ""):
            return ctx
    return "unknown"


@dataclass(frozen=True)
class DistilledTechSignal:
    """Output of the distiller — the persona-grade shape of one
    tech-market signal. Deliberately lacks every raw-PII field
    (author handle, source row id, full body text).

    Phase 11D.1 does not persist these; persistence happens in
    Phase 11D.2 via a `TechMarketSignalPersister`.
    """

    source_provider: str
    source_category: str | None
    product_category: str
    company_or_product: str | None
    competitor_name: str | None
    signal_type: SignalType
    sentiment_bucket: SentimentBucket
    buyer_type: BuyerType
    market_context: MarketContext
    theme: str | None
    short_snippet: str
    evidence_url: str | None = None
    source_timestamp: int | None = None
    relevance_score: float | None = None
    metadata: dict = field(default_factory=dict)


class TechMarketSignalDistiller(Protocol):
    """Pluggable distiller interface. Production code calls only this
    Protocol — concrete implementations stay swappable.

    Implementations MUST be:
      * pure (no network, no DB)
      * deterministic (same input → same output)
      * snippet-cap-respecting (≤ 240 chars per snippet)
    """

    def distill(
        self,
        text: str,
        *,
        source_provider: str,
        source_category: str | None = None,
        product_category: str = "unknown",
        company_or_product: str | None = None,
        competitor_name: str | None = None,
        market_context_hint: MarketContext | None = None,
        evidence_url: str | None = None,
        source_timestamp: int | None = None,
        metadata: dict | None = None,
    ) -> list[DistilledTechSignal]:  # pragma: no cover - protocol
        ...


class RuleBasedTechMarketDistiller:
    """Phase 11D.1 scaffold distiller — keyword-rule classification.

    Returns AT MOST ONE signal per input text (the first matching
    signal_type). Phase 11D.2 may evolve this to multi-emit if a
    single post carries two signal types — but for the scaffold we
    keep the per-source-row contract simple.
    """

    def distill(
        self,
        text: str,
        *,
        source_provider: str,
        source_category: str | None = None,
        product_category: str = "unknown",
        company_or_product: str | None = None,
        competitor_name: str | None = None,
        market_context_hint: MarketContext | None = None,
        evidence_url: str | None = None,
        source_timestamp: int | None = None,
        metadata: dict | None = None,
    ) -> list[DistilledTechSignal]:
        if not text or not text.strip():
            return []
        signal_type = _classify_signal_type(text)
        if signal_type is None:
            return []
        sentiment = _infer_sentiment(text, signal_type)
        buyer_type = _classify_buyer_type(text)
        market_context = _classify_market_context(
            text, hint=market_context_hint,
        )
        # Strip any author/handle PII from incoming metadata defensively.
        clean_meta: dict = {}
        for k, v in (metadata or {}).items():
            if k.lower() in _METADATA_PII_KEYS:
                continue
            clean_meta[k] = v
        return [DistilledTechSignal(
            source_provider=source_provider,
            source_category=source_category,
            product_category=product_category,
            company_or_product=company_or_product,
            competitor_name=competitor_name,
            signal_type=signal_type,
            sentiment_bucket=sentiment,
            buyer_type=buyer_type,
            market_context=market_context,
            theme=None,
            short_snippet=_cap_snippet(text),
            evidence_url=evidence_url,
            source_timestamp=source_timestamp,
            relevance_score=None,
            metadata=clean_meta,
        )]


_METADATA_PII_KEYS: frozenset[str] = frozenset(
    {
        "author_handle", "author_id", "author_email",
        "user_id", "user_handle", "user_name",
        "email", "phone", "ip", "ip_address", "session_id",
    },
)


__all__ = [
    "DistilledTechSignal",
    "RuleBasedTechMarketDistiller",
    "TechMarketSignalDistiller",
]
