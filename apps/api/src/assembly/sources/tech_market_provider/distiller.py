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
# before broader ones (workflow_fit) so a procurement complaint
# doesn't get mislabeled as generic pain.
#
# Phase 11D.7 v3 changes (HN/devtool corpus generalization):
#   * developer_skepticism PROMOTED above competitor_comparison so
#     methodology / tech-choice skepticism wins on rows like
#     "Wouldn't NDCG/token results vary wildly… instead of one big
#     grep" (the "instead of" no longer mis-fires competitor).
#   * developer_skepticism BROADENED with HN/methodology dialect:
#     "vary wildly", "is the benchmark measuring", "how do you
#     measure", "not pretty", "fewer than zero", "NDCG", "why write/
#     use/choose X in Y", "would surely be faster".
#   * workflow_fit PROMOTED above competitor_comparison + BROADENED
#     with adoption-friction patterns from HN: "agent does not
#     trust", "falls back to grep", "use X over Y", "prefer to use
#     the tool", "part of the harness", "forces compliance".
#   * pain_urgency broadened: "wastes tokens", "token savings (are)
#     lost", "falls apart", "biggest challenge".
#   * feature_inquiry broadened with HN/devtool question forms:
#     "would (this|it|you)", "could you", "should(n't) it",
#     "wouldn't it", "does this", "is this", "how many".
#   * onboarding_friction TIGHTENED — bare "setup" alone (e.g.
#     "setup hooks" as a recommendation) no longer fires. Requires
#     "setup was/took/failed/broke/hard to" or explicit setup-pain.
#   * integration_friction TIGHTENED — bare "API"/"SDK"/"webhook"
#     alone (e.g. "API docs" as a feature inquiry) no longer fires.
#     Requires breakage language alongside (broke/failed/won't/
#     doesn't/cannot/crashed/timed out).
#
# Phase 11D.4 v2 history (still applies):
#   * pain_urgency wins over weak workflow / competitor cues in
#     the same sentence.
#   * trust_security_concern handles Product Hunt demo-skepticism
#     ("too good to be true", "cherry-picked").
#   * workflow_fit v2 prevents bare-"workflow"-praise false-positive.
#   * competitor_comparison includes brand names (Sora, Runway,
#     Pika, Claude Code, Cursor, Copilot, ...).
#   * switching_objection covers "used other tools" framing.
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
        # Phase 11D.4 — broadened to cover demo-skepticism wording
        # ("too good to be true", "cherry-picked", "does it
        # actually X", "is it one of those", "prove it", "what is
        # the catch"). Anchored as a leading skepticism question
        # so "they actually look like themselves" (praise) does NOT
        # match. The "does it actually" pattern requires the
        # interrogative framing.
        "trust_security_concern",
        re.compile(
            r"\b("
            r"privacy|security|GDPR|PII|leak|breach|"
            r"data residency|encryption|exfiltrat|"
            r"sketchy|scam|untrust|"
            r"too good to be true|what['']?s? the catch|"
            r"cherry[- ]?picked|"
            r"does it actually (work|look|deliver|do)|"
            r"is it actually|"
            r"is it (one|just one) of those|"
            r"prove it|"
            r"real (output|outputs|results)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Phase 11D.4 — promoted earlier so implicit pain wording
        # ("bottleneck", "burned by", "failed attempts", "quit after",
        # "tired of") wins over broader cues like competitor names
        # or workflow keywords in the same sentence.
        "pain_urgency",
        re.compile(
            r"\b("
            r"urgent|need this yesterday|critical pain|"
            r"painful|massive headache|hair on fire|"
            r"on fire|deadline|"
            r"bottleneck|biggest bottleneck|biggest challenge|"
            r"burned by|burnt by|"
            r"tired of|fed up|"
            r"\d+ failed attempts|failed attempts (to|with|at)|"
            r"quit (after|using)|quit AI |"
            r"takes too long|too slow|"
            # Phase 11D.7 — HN/devtool implicit pain wording.
            r"wastes? tokens|wasted tokens|"
            r"token savings (are )?lost|"
            r"falls apart|fall apart"
            # NOTE: "frustrated/frustrating" is intentionally NOT in
            # this set — it's too generic and co-occurs with virtually
            # any objection (pricing, integration, etc.). Adding it
            # here would route too-expensive-plus-frustrated rows to
            # pain_urgency, hiding the actual pricing signal the
            # founder cares about.
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Phase 11D.7 — PROMOTED above competitor_comparison and
        # broadened with HN/devtool dialect:
        #   * methodology / benchmark skepticism ("vary wildly",
        #     "is the benchmark measuring", "how do you measure",
        #     "depending on the agent's query", "NDCG", "not pretty",
        #     "fewer than zero")
        #   * tech-choice skepticism ("why write/use/choose X in Y",
        #     "would surely be (faster|better|more portable)")
        # Operator chose to fold methodology skepticism into
        # developer_skepticism rather than add a new signal_type
        # (would have required a schema migration).
        "developer_skepticism",
        re.compile(
            r"\b("
            # original Phase-11D.1 patterns
            r"toy|hello world|prototype quality|"
            r"not production[- ]ready|stack overflow|"
            r"docs are wrong|black box|"
            # Phase 11D.7 — methodology / benchmark skepticism.
            # Patterns are universal across any product that publishes
            # measurable claims (B2B SaaS benchmarks, AI tools,
            # marketplaces, dev tools). The "depending on the agent's"
            # variant is AI-agent-category-aware per operator's
            # allowed-list (category-aware, not product-name-specific).
            r"vary wildly|varies wildly|"
            r"is the benchmark|benchmark measuring|"
            r"how do you measure|how do you compare accuracy|"
            r"depending on the agent'?s? (query|queries|prompt)|"
            r"fewer than zero|"
            r"not pretty|"
            # Phase 11D.7 — tech-choice skepticism
            r"why\s+(write|use|choose|build|pick)|"
            r"would surely be (faster|better|more portable|cheaper)|"
            r"would be faster (with|in|using)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Phase 11D.7 — PROMOTED above competitor_comparison so
        # adoption-friction signals win over weak competitor brand
        # mentions in the same comment (e.g., HN row 1 mentions
        # Claude Code AND has "agent does not trust results" — the
        # adoption-friction is the more useful founder signal).
        #
        # v2 patterns (Product Hunt workflow language) preserved.
        # v3 adds HN/devtool adoption patterns:
        #   * "agent does not trust" / "do not trust the results"
        #   * "falls back to grep" / "falls back to <baseline>"
        #   * "use X over Y" / "prefer to use the tool"
        #   * "part of the harness" / "local codebase harness"
        #   * "forces compliance"
        #   * "retry and reread" / "retry or reread" (adoption-cost
        #     symptom: the agent doesn't trust new tools)
        "workflow_fit",
        re.compile(
            r"("
            # Phase 11D.4 v2 — Product Hunt strong workflow cues
            r"\bapproval flow\b|"
            r"\breusable (story|across|brand|template)\b|"
            r"\bbrand bible\b|"
            r"\bteam workflow\b|"
            r"\bproduction process\b|"
            r"\bcreative direction\b|"
            r"\bdirector workflow\b|"
            r"\bday[- ]to[- ]day\b|"
            r"\bslot(s|ted)? into\b|"
            r"\bfit our (process|workflow|team)\b|"
            r"\bhand[- ]?off\b|"
            r"\buse every day\b|"
            r"\bfit into our\b|"
            r"\bcustom brand guidelines?\b|"
            r"\bstrict color palettes?\b|"
            r"\biterate without starting (over|from scratch)\b|"
            r"\bworkflow for our team\b|"
            r"\bvisible creative memory\b|"
            r"\bvisual consistency across\b|"
            # Phase 11D.7 — HN/devtool adoption-friction cues
            r"\bagent(s)? do(es)? not trust\b|"
            r"\b(do|does) not trust (the )?results\b|"
            r"\b(do|does) not trust results\b|"
            r"\bretry (or|and) reread\b|"
            r"\bretries (or|and) rereads\b|"
            r"\bcontinually retry\b|"
            r"\bfalls? back to (grep|bash|the\s+\w+)\b|"
            r"\bprefer to use the tool\b|"
            r"\buse\s+\w+\s+over\s+(bash|grep|the\s+\w+)\b|"
            # "part of the harness" generalizes to any evaluation /
            # CI / agent harness. The narrower "local codebase
            # harness" variant was removed in the Phase 11D.7
            # product-agnosticism audit (too code-search-specific —
            # `part of the harness` already covers it).
            r"\bpart of the harness\b|"
            r"\bforces compliance\b|"
            r"\bin (my|our) pipeline\b|"
            r"\bcustom harness\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        # Phase 11D.7 — TIGHTENED. The bare word "API"/"SDK"/"webhook"/
        # "integration" alone is no longer enough — e.g. "Does this
        # work for API docs?" (HN row 18 in 11D.6) is a feature
        # inquiry, not integration friction. Require breakage /
        # connectivity language alongside.
        "integration_friction",
        re.compile(
            r"("
            # API/SDK/webhook + explicit breakage/failure word
            r"\b(API|webhook|integration|SDK|SSO|OAuth|connector|sync)\b"
            r"[^.!?\n]{0,40}\b"
            r"(broke|broken|failed|fails|failing|down|crashed|"
            r"hangs?|hung|times?\s+out|timed\s+out|"
            r"won'?t|wouldn'?t|cannot|can'?t|doesn'?t|isn'?t|"
            r"keeps?\s+(dropping|breaking|failing|crashing))\b|"
            # OR breakage word followed by API/SDK/etc.
            r"\b(broken|broke|failed?)\s+(API|webhook|integration|SDK|SSO|"
            r"OAuth|connector|sync)\b|"
            # OR descriptive integration-pain phrases
            r"\b(API|SDK|integration|webhook)\s+(was|is|has been)\s+"
            r"(hard|painful|complicated|broken|down|brittle)\b|"
            r"\bwebhook keeps dropping\b|"
            r"\bconnector broke\b|"
            r"\bdidn'?t integrate\b|"
            r"\bwon'?t connect\b|\bcannot connect\b|\bcan'?t connect\b|"
            r"\bwon'?t sync\b|\bwouldn'?t sync\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        # Phase 11D.7 — TIGHTENED. The bare word "setup" alone is no
        # longer enough — e.g. "Setup hooks. Hooks are how your
        # harness forces compliance" (HN row 15 in 11D.6) is a
        # recommendation, not setup friction. Require explicit
        # setup-pain language alongside.
        "onboarding_friction",
        re.compile(
            r"\b("
            r"onboarding\s+(was|is|took|kept|failed|broke|hard|painful|"
            r"confusing|outdated|unclear)|"
            r"setup\s+(was|is|took|kept|failed|broke|hard|painful|"
            r"confusing|outdated|unclear|crashed|hangs?|hung)|"
            r"hard\s+to\s+(set\s*up|install|onboard|figure\s+out)|"
            r"confusing\s+(setup|onboarding|install)|"
            r"install(ed|ation|er)?\s+(failed|broke|won'?t|crashed|"
            r"hung|hangs?|kept failing)|"
            r"getting\s+started\s+(was|is|kept|took|painful|hard|"
            r"confusing)|"
            r"first[- ]?run\s+(failed|broke|kept|crashed)|"
            r"tutorial\s+(was|is|kept|broke|outdated|unclear|wrong|"
            r"missing|terrible)|"
            r"docs\s+were\s+(wrong|outdated|missing|unclear|terrible|"
            r"hard\s+to|written\s+for|stale|out\s+of\s+date)|"
            r"docs\s+(are|kept being)\s+(wrong|outdated|missing|"
            r"unclear|terrible|stale|out\s+of\s+date)|"
            # Verb-before-noun ordering: "kept failing during setup"
            r"(kept|keeps?)\s+(failing|breaking|crashing|hanging|"
            r"timing\s*out)\s+(during|in|while|after)\s+(the\s+)?"
            r"(onboarding|setup|install|getting\s+started)|"
            # "during onboarding" / "during setup" + nearby fail/break
            r"during\s+(onboarding|setup|install)|"
            r"hard\s+to\s+use|"
            r"figure\s+out (how|the|what)"
            r")\b",
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
        # Phase 11D.4 — added "used other tools" and
        # "failed attempts (to|with) <competitor>" patterns so the
        # Product Hunt language ("failed attempts to get Sora to
        # do my bidding", "I have used other tools before") lands
        # as switching evidence.
        "switching_objection",
        re.compile(
            r"\b("
            r"switch(ed|ing|es)?|migrat(ed|ing)?|"
            r"moved away|left for|replaced with|"
            r"moving back|kept using|"
            # Phase 11D.7 product-agnosticism: broadened from the
            # original "used other tools" (devtool-specific) to
            # match any product-category noun. The bare "tools"
            # version was overfitting to a single HN dev-tool
            # corpus. The `[\w\s\-]{0,40}?` allows a category
            # modifier between "used other" and the noun
            # ("used other habit-tracking apps", "used other
            # CRM solutions", "used other shopping sites").
            r"used other[\w\s\-]{0,40}?(tools?|apps?|products?|"
            r"services?|solutions?|platforms?|software|options?|"
            r"alternatives?|sites?|providers?|vendors?)|"
            r"used to use|"
            r"came from (a|an|the|different)|prior (tool|app|"
            r"product|service|platform|vendor)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Phase 11D.4 — broadened to catch "competitor to X",
        # "alternative to X", "instead of X", "X equivalent", and
        # a small set of well-known competitor brand names in the
        # AI-tool / video-gen / dev-tool space. Phase 11D.7 leaves
        # this rule unchanged but DEMOTES it below
        # developer_skepticism + workflow_fit + integration/onboarding
        # so methodology comments with "instead of" no longer mis-fire
        # competitor_comparison (HN row 23 in 11D.6).
        "competitor_comparison",
        re.compile(
            r"\b("
            r"vs\.?|compared to|compared with|"
            r"better than|worse than|"
            r"alternative to|instead of|"
            r"competitor to|competitor of|"
            r"\w+ equivalent|"
            # known competitors / tool names worth flagging when
            # a Product-Hunt-style comment mentions them
            r"Sora|Runway|Pika|Veo|Kling|Hailuo|Luma|"
            r"Midjourney|Stable Diffusion|DALL[- ]?E|"
            r"Claude Code|Cursor|Copilot"
            r")\b",
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
        # Phase 11D.5 — launch-community question patterns. Placed
        # LAST in the rule order so the more specific signals
        # (trust_security_concern for skeptical questions, pain_urgency
        # for pain-laced questions, competitor_comparison for brand
        # mentions, switching_objection for "used other tools"
        # framing, workflow_fit for strong workflow cues) always win
        # when they co-occur.
        #
        # Captures demand signal from Product Hunt / HN / Show HN /
        # G2-style question text. These are valuable founder-market
        # signals (what users want clarified before adoption) that
        # would otherwise be rejected.
        #
        # The patterns require an *interrogative framing* (question
        # mark or interrogative phrase) plus one of a small set of
        # common question stems — this prevents declarative sentences
        # like "I can use this" from accidentally matching "can I".
        "feature_inquiry",
        re.compile(
            r"("
            # Phase 11D.5 — explicit question stems at sentence start
            # OR after a clause boundary (comma/semicolon) so a
            # post-comma question ("..., is the render locked?")
            # still classifies.
            #
            # Phase 11D.7 — broadened with HN/devtool question forms:
            #   * would/wouldn't (this|it|you|that)
            #   * could/couldn't (you|this|it|we)
            #   * should/shouldn't (this|it)
            #   * "does this" / "is this" (Product Hunt row 18 form)
            #   * "how many"
            r"(?:^|[.!?,;]\s+|^\s*)("
            r"can\s+(I|we|a\s+team)|"
            r"could\s+(you|this|it|we)|"
            r"couldn'?t\s+(you|this|it|we)|"
            r"would\s+(this|it|you|that)|"
            r"wouldn'?t\s+(this|it|that)|"
            r"should\s+(this|it)|"
            r"shouldn'?t\s+(it|this)|"
            r"does\s+(it|this|the\s+\w+|a\s+\w+)|"
            r"is\s+(it|this|the\s+\w+|a\s+\w+)|"
            r"how\s+(does|long|much|do|many)|"
            r"what\s+(does|input|are|is\s+the)|"
            r"is\s+it\s+possible"
            r")\b|"
            # phrasal cues that imply inquiry
            r"\bcurious\s+how\b|"
            r"\bcurious\s+about\b|"
            r"\bwondering\s+(how|if|whether)\b|"
            r"\bquestion\s+about\b"
            r")",
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
