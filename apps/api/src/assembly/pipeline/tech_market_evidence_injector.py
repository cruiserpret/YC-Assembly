"""Phase 11D.9 / 11D.11 — tech-market evidence injection.

This module owns two related but distinct surfaces, both reading
only from the local `tech_market_signal` table populated via the
Phase 11D.2 CSV ingestion CLI:

  1. `build_tech_market_evidence_section_from_dict_brief` (Phase
     11D.9) — AUDIT-ONLY. Returns a uniform dict that lands at
     `main_report["technical"]["tech_market_signals"]` regardless
     of flag state, so the report shape stays consistent across
     runs. Triple-flag-gated only insofar as the third flag is
     echoed in the dict; the audit itself fires whenever both
     ENABLED and RUNTIME_ENABLED are true.
  2. `build_tech_market_persona_prompt_block` (Phase 11D.11) —
     OPTIONAL persona prompt block. Returns `str | None`. None
     unless ALL THREE flags are true. Format mirrors the Phase
     11C.5 Amazon persona-prompt block: a compact bulleted block
     suitable for prepending to per-persona prompts.

Both surfaces share:
  * Same-category invariant. The retriever's signal-type pool
    intentionally crosses categories for diversity; both surfaces
    drop any cross-category row BEFORE applying caps so a Devtool
    brief never sees AI_SaaS signals (and vice versa).
  * Hard caps. ≤ 20 considered, ≤ 12 kept, ≤ 3 per signal_type.
  * Fail-closed on unresolved `category_hint`. No fallback to
    cross-category retrieval.
  * No raw author/user/row identifiers ever surfaced. Only the
    distiller-capped `short_snippet` (≤ 240 chars) reaches the
    output.

Phase 11D.11 design — block built ONCE per simulation, not per
persona. Mirrors the Phase 11C.5 Amazon design: cheaper, simpler,
easier to audit, deterministic across personas in the same run.
"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from assembly.sources.tech_market_provider import (
    TechMarketRetrievalConfig,
    TechMarketSignalRetriever,
    TechProductBriefShape,
)
from assembly.sources.tech_market_provider.postgres_source import (
    PostgresTechMarketSignalSource,
)
from assembly.sources.tech_market_provider.retrieval import (
    _classify_product_category_hint,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from assembly.config import Settings
    from assembly.sources.tech_market_provider import (
        MarketContext,
        RetrievedTechSignal,
    )


# Phase 11D.9 audit-only caps.
_AUDIT_MAX_CONSIDERED = 20
_AUDIT_MAX_KEPT = 12
_AUDIT_MAX_PER_SIGNAL_TYPE = 3

# Number of kept signals to embed in the audit's sample_kept_signals.
_AUDIT_SAMPLE_SIZE = 6


# Map product_category -> inferred default market_context. Used when
# the brief doesn't carry an explicit market_context_hint. Keeps the
# retriever scoped to the right context without cross-category leak.
_PRODUCT_CATEGORY_DEFAULT_MARKET: dict[str, "MarketContext"] = {
    "ai_saas": "AI_tool",
    "browser_extension": "B2C",
    "devtool_api": "devtool",
    "b2b_workflow_saas": "B2B",
    "consumer_mobile_app": "B2C",
    "marketplace": "marketplace",
}


def _brief_to_shape(brief: dict[str, Any]) -> TechProductBriefShape:
    """Adapt the founder brief dict to the slim shape the retriever
    expects. Pure projection — no derived fields beyond the
    product_category → market_context default lookup."""
    competitors: list[str] = []
    for c in (brief.get("competitors_or_alternatives") or []):
        if isinstance(c, str) and c.strip():
            competitors.append(c.strip())
        elif isinstance(c, dict):
            name = c.get("name")
            if isinstance(name, str) and name.strip():
                competitors.append(name.strip())

    raw_hint = brief.get("category_hint")
    product_category_hint = (
        str(raw_hint).strip() if isinstance(raw_hint, str) and raw_hint.strip()
        else None
    )

    # If the brief carries an explicit `market_context` use it;
    # otherwise infer from the resolved product_category.
    market_hint = brief.get("market_context")
    if isinstance(market_hint, str) and market_hint.strip():
        market_context_hint: "MarketContext | None" = market_hint.strip()  # type: ignore[assignment]
    else:
        resolved = _classify_product_category_hint(product_category_hint)
        market_context_hint = (
            _PRODUCT_CATEGORY_DEFAULT_MARKET.get(resolved)
            if resolved else None
        )

    return TechProductBriefShape(
        product_name=str(brief.get("product_name") or "").strip(),
        description=str(brief.get("product_description") or "").strip(),
        product_category_hint=product_category_hint,
        market_context_hint=market_context_hint,
        competitors=tuple(competitors),
    )


def _bucket_balance(
    signals: list["RetrievedTechSignal"],
    *,
    max_total: int = _AUDIT_MAX_KEPT,
    max_per_type: int = _AUDIT_MAX_PER_SIGNAL_TYPE,
) -> list["RetrievedTechSignal"]:
    """Operator-spec'd cap: at most `max_total` signals, with no
    more than `max_per_type` per `signal_type`. Round-robin across
    types so no one bucket dominates the audit."""
    by_type: dict[str, list["RetrievedTechSignal"]] = {}
    for s in signals:
        by_type.setdefault(s.signal_type, []).append(s)
    picked: list["RetrievedTechSignal"] = []
    # Iterate signal_types in stable order; within each bucket, take
    # at most `max_per_type` items in retriever order.
    for stype in sorted(by_type.keys()):
        if len(picked) >= max_total:
            break
        for s in by_type[stype][:max_per_type]:
            if len(picked) >= max_total:
                break
            picked.append(s)
    return picked


def _disabled_audit(
    config: TechMarketRetrievalConfig,
) -> dict[str, Any]:
    """Off-state audit dict — never touches the DB. Returned when
    either gate is False, so callers always get a uniform audit
    shape they can stash in `main_report["technical"]`."""
    return {
        "provider": "tech_market_signal_local_dev",
        "enabled": config.enabled,
        "runtime_enabled": config.runtime_enabled,
        "persona_injection_enabled": config.persona_injection_enabled,
        "attempted": False,
        "query_category": None,
        "query_market_context": None,
        "signals_considered": 0,
        "signals_kept": 0,
        "signals_rejected": 0,
        "signal_type_distribution": {},
        "buyer_type_distribution": {},
        "market_context_distribution": {},
        "sample_kept_signals": [],
        "fallback_used": False,
        "rejection_reasons": {
            "feature_flag_off": 1,
        },
        "source_providers_used": [],
        "notes": [
            "feature_flag_off — tech-market retrieval disabled, "
            "no DB read attempted",
        ],
    }


def _audit_from_signals(
    *,
    config: TechMarketRetrievalConfig,
    query_category: str | None,
    query_market_context: "MarketContext | None",
    considered: list["RetrievedTechSignal"],
    kept: list["RetrievedTechSignal"],
    fallback_used: bool,
    notes: list[str],
) -> dict[str, Any]:
    """Render the Phase-11D.9 audit dict per operator spec."""
    rejected = len(considered) - len(kept)
    type_dist = Counter(s.signal_type for s in kept)
    buyer_dist = Counter(s.buyer_type for s in kept)
    market_dist = Counter(s.market_context for s in kept)
    sources = sorted({s.source_provider for s in kept})

    rejection_reasons: dict[str, int] = {}
    if rejected > 0:
        rejection_reasons["per_bucket_cap_or_total_cap"] = rejected
    if not kept and considered:
        rejection_reasons["no_kept_after_balancing"] = (
            len(considered) - len(kept)
        )

    return {
        "provider": "tech_market_signal_local_dev",
        "enabled": config.enabled,
        "runtime_enabled": config.runtime_enabled,
        "persona_injection_enabled": config.persona_injection_enabled,
        "attempted": True,
        "query_category": query_category,
        "query_market_context": query_market_context,
        "signals_considered": len(considered),
        "signals_kept": len(kept),
        "signals_rejected": rejected,
        "signal_type_distribution": dict(type_dist),
        "buyer_type_distribution": dict(buyer_dist),
        "market_context_distribution": dict(market_dist),
        "sample_kept_signals": [
            {
                "source_provider": s.source_provider,
                "product_category": s.product_category,
                "company_or_product": s.company_or_product,
                "competitor_name": s.competitor_name,
                "signal_type": s.signal_type,
                "sentiment_bucket": s.sentiment_bucket,
                "buyer_type": s.buyer_type,
                "market_context": s.market_context,
                "theme": s.theme,
                "short_snippet": s.short_snippet,
                "evidence_url": s.evidence_url,
                "relevance_score": s.relevance_score,
            }
            for s in kept[:_AUDIT_SAMPLE_SIZE]
        ],
        "fallback_used": fallback_used,
        "rejection_reasons": rejection_reasons,
        "source_providers_used": sources,
        "notes": list(notes),
    }


async def build_tech_market_evidence_section_from_dict_brief(
    brief: dict[str, Any],
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> dict[str, Any]:
    """Phase 11D.9 — audit-only tech-market evidence section.

    Returns a uniform audit dict in every case (the disabled-state
    dict when flags are off, the empty-package dict when no category
    matches, or the full audit dict when retrieval ran). NEVER
    feeds personas or shapes the report's persuasion narrative —
    the dict lands only under
    `main_report["technical"]["tech_market_signals"]`.
    """
    config = TechMarketRetrievalConfig.from_settings(settings)
    if not config.fully_enabled:
        return _disabled_audit(config)

    shape = _brief_to_shape(brief)

    # Same-category invariant: if the brief's category_hint doesn't
    # resolve, refuse to retrieve. No cross-category leak.
    matched_category = _classify_product_category_hint(
        shape.product_category_hint,
    )
    if matched_category is None:
        return {
            **_disabled_audit(config),
            "attempted": True,
            "query_category": None,
            "query_market_context": None,
            "fallback_used": False,
            "rejection_reasons": {"product_category_unresolved": 1},
            "notes": [
                "product_category did not resolve from category_hint "
                f"{shape.product_category_hint!r} — refusing cross-"
                "category fallback. Update the brief's category_hint "
                "to one of: ai_saas, browser_extension, devtool_api, "
                "b2b_workflow_saas, consumer_mobile_app, marketplace.",
            ],
        }

    source = PostgresTechMarketSignalSource(sessionmaker)
    retriever = TechMarketSignalRetriever(source, config=config)
    pkg = await retriever.retrieve_for_product_brief(shape)

    # SAME-CATEGORY GUARD: the retriever's `fetch_by_signal_types`
    # pool intentionally crosses categories for diversity. Audit-only
    # mode must NOT surface cross-category rows, so we filter them
    # out before applying caps. This is the Phase 11D.9 invariant
    # the operator specified ("AI SaaS brief may use ai_saas /
    # AI_tool signals; Devtool brief may use devtool_api / devtool
    # signals; …no cross-category leak").
    same_category = [
        s for s in pkg.signals
        if s.product_category == matched_category
    ]

    # Apply the audit-only caps (operator-spec'd Phase 11D.9):
    #   1. max 20 considered
    #   2. max 12 kept
    #   3. max 3 per signal_type
    considered = same_category[:_AUDIT_MAX_CONSIDERED]
    kept = _bucket_balance(considered)

    notes: list[str] = list(pkg.notes)
    if not kept:
        notes.append(
            "no signals matched product_category + market_context + "
            "competitor + signal_type pools after caps",
        )

    return _audit_from_signals(
        config=config,
        query_category=pkg.product_category_matched,
        query_market_context=pkg.market_context_matched,
        considered=considered,
        kept=kept,
        fallback_used=False,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Phase 11D.11 — gated tech-market persona prompt block
# ---------------------------------------------------------------------------
#
# Mirrors Phase 11C.5's Amazon `build_amazon_persona_prompt_block`
# shape. Returns `str | None`. Triple-flag-gated: all three flags
# must be True for the function to return a non-None block. Built
# once per simulation and broadcast to every persona via
# `run_live_discussion(amazon_persona_block=..., tech_market_persona_block=...)`.

# Hard caps for the persona block (same constants as the audit's
# bucket-balance pass; kept as module-level so a future tweak in
# one place doesn't drift the other).
_PERSONA_BLOCK_MAX_BULLETS = _AUDIT_MAX_KEPT  # 12
_PERSONA_BLOCK_PER_SIGNAL_TYPE = _AUDIT_MAX_PER_SIGNAL_TYPE  # 3
_PERSONA_BLOCK_SNIPPET_CHAR_CAP = 180


def _format_persona_bullet(s: "RetrievedTechSignal") -> str:
    """Render one signal as a single bullet. No raw fields, no row
    ids, no author handles — just the distilled snippet with its
    signal_type label."""
    snippet = (s.short_snippet or "").strip()
    if len(snippet) > _PERSONA_BLOCK_SNIPPET_CHAR_CAP:
        snippet = (
            snippet[: _PERSONA_BLOCK_SNIPPET_CHAR_CAP - 1].rstrip() + "…"
        )
    sentiment = (s.sentiment_bucket or "").lower()
    return f"- [{s.signal_type}/{sentiment}] {snippet}"


async def build_tech_market_persona_prompt_block(
    brief: dict[str, Any],
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> str | None:
    """Phase 11D.11 — produce a compact, capped tech-market evidence
    text block suitable for injecting into a persona's discussion
    prompt.

    Returns None when ANY of the three gates is off:
      * tech_market_signals_enabled
      * tech_market_signals_runtime_enabled
      * tech_market_signals_persona_injection_enabled

    Returns None when the brief's `category_hint` cannot be resolved
    to a controlled-vocabulary `product_category` (same-category
    invariant — never falls back to cross-category retrieval).

    Returns None when no signals survive the same-category filter +
    bucket-balance caps. Production prompts stay byte-for-byte
    identical to the Phase 11D.9-era shape whenever the block is
    None.

    Format:

        Tech-market signals from similar products (treat as buyer
        language, NOT as ground truth):
        - [pain_urgency/negative] Users complain that …
        - [feature_inquiry/positive] Users ask whether …
        - [developer_skepticism/negative] Developers question …

    The block is built ONCE per simulation and passed to every
    persona — never customized per persona (deliberate Phase 11D.11
    design decision; mirrors the Amazon Phase 11C.5 design).
    """
    config = TechMarketRetrievalConfig.from_settings(settings)

    # Triple-gate check — all three must be on. We deliberately do
    # NOT use config.fully_enabled here because that only covers the
    # first two flags; the persona-block path needs the third gate
    # too.
    if not (
        config.enabled
        and config.runtime_enabled
        and config.persona_injection_enabled
    ):
        return None

    shape = _brief_to_shape(brief)
    matched_category = _classify_product_category_hint(
        shape.product_category_hint,
    )
    if matched_category is None:
        # Same-category invariant. No cross-category fallback for
        # the persona-block path either.
        return None

    source = PostgresTechMarketSignalSource(sessionmaker)
    retriever = TechMarketSignalRetriever(source, config=config)
    pkg = await retriever.retrieve_for_product_brief(shape)

    # SAME-CATEGORY GUARD (mirrors the audit-only path in Phase
    # 11D.9). The retriever's signal_type pool can cross categories
    # for diversity; we drop those rows here so the persona block
    # never carries cross-category evidence.
    same_category = [
        s for s in pkg.signals
        if s.product_category == matched_category
    ]

    considered = same_category[:_AUDIT_MAX_CONSIDERED]
    kept = _bucket_balance(
        considered,
        max_total=_PERSONA_BLOCK_MAX_BULLETS,
        max_per_type=_PERSONA_BLOCK_PER_SIGNAL_TYPE,
    )
    if not kept:
        return None

    category_label = pkg.product_category_matched or "matched category"
    header = (
        f"Tech-market signals from similar products "
        f"(real distilled signals from category={category_label}; "
        f"treat as buyer language, NOT as ground truth):"
    )
    body = "\n".join(_format_persona_bullet(s) for s in kept)
    return f"{header}\n{body}"


__all__ = [
    "build_tech_market_evidence_section_from_dict_brief",
    "build_tech_market_persona_prompt_block",
]
