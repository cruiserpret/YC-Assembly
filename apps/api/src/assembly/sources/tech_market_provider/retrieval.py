"""Phase 11D.1 — tech-market signal retriever scaffold.

Mirrors the Amazon provider's Phase-11C.1 design at a structural
level: a `TechMarketSignalSource` Protocol (production hits
Postgres, tests use in-memory), a `TechMarketSignalRetriever` that
short-circuits to empty results unless both feature flags are on,
and a `TechMarketEvidencePackage` carrying the persona-grade
results plus a thin audit dict.

Phase 11D.1 ships the IN-MEMORY source only. Phase 11D.2 will add
the `PostgresTechMarketSignalSource` implementation once the
ingestion pipeline lands.

Safety properties (drift-tested in Phase 11D.2):

  * The retriever returns an empty package unless BOTH
    `ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED=true` AND
    `ASSEMBLY_TECH_MARKET_SIGNALS_RUNTIME_ENABLED=true`.
  * The exposed `RetrievedTechSignal` carries NO author handle,
    NO author id, NO raw post body — only the distilled short
    snippet and the structured tags.
  * `max_per_run` bounds how much tech-market evidence any single
    simulation can consume.

Zero HTTP imports. Zero scraping. Zero live data sources.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from assembly.sources.tech_market_provider.signal_types import (
    BuyerType,
    MarketContext,
    SentimentBucket,
    SignalType,
)


# ---------------------------------------------------------------------------
# Config + brief shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechMarketRetrievalConfig:
    """Wraps the Phase-11D.1 settings without importing them directly
    so the retriever can be unit-tested without a full `Settings`
    object.

    `enabled` and `runtime_enabled` mirror the Phase-11C Amazon
    pattern — both must be True for any source-side work to happen.
    `persona_injection_enabled` is observability-only on the
    retriever (the actual injection decision lives in a future
    pipeline helper).
    """

    enabled: bool = False
    runtime_enabled: bool = False
    persona_injection_enabled: bool = False
    max_per_run: int = 80
    min_relevance: float = 0.20

    @classmethod
    def from_settings(
        cls, settings: object,
    ) -> "TechMarketRetrievalConfig":
        return cls(
            enabled=bool(
                getattr(
                    settings, "tech_market_signals_enabled", False,
                ),
            ),
            runtime_enabled=bool(
                getattr(
                    settings,
                    "tech_market_signals_runtime_enabled",
                    False,
                ),
            ),
            persona_injection_enabled=bool(
                getattr(
                    settings,
                    "tech_market_signals_persona_injection_enabled",
                    False,
                ),
            ),
            max_per_run=int(
                getattr(
                    settings, "tech_market_signals_max_per_run", 80,
                ),
            ),
            min_relevance=float(
                getattr(
                    settings, "tech_market_signals_min_relevance", 0.20,
                ),
            ),
        )

    @property
    def fully_enabled(self) -> bool:
        return self.enabled and self.runtime_enabled


@dataclass(frozen=True)
class TechProductBriefShape:
    """The narrow slice of a founder's product brief that the
    tech-market retriever cares about. Decoupled from any larger
    `SimulationBriefIn` so the retriever stays usable without
    Pydantic in tests."""

    product_name: str
    description: str = ""
    product_category_hint: str | None = None  # e.g. 'ai_saas'
    market_context_hint: MarketContext | None = None
    competitors: Sequence[str] = ()


# ---------------------------------------------------------------------------
# Row + persona-grade output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechSignalRow:
    """Internal shape — what the Postgres source returns. Mirrors the
    `tech_market_signal` table columns. Production code never
    surfaces this directly to the persona layer; the retriever
    converts to `RetrievedTechSignal` first."""

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
    evidence_url: str | None
    source_timestamp: int | None
    relevance_score: float | None
    metadata: dict


@dataclass(frozen=True)
class RetrievedTechSignal:
    """Persona-grade shape — what the retriever returns to callers.

    Deliberately omits every field the persona layer must never see:
      * no author / handle / user id (the table never stores one)
      * no raw post body (only the distilled short snippet)
      * no internal row id / created_at (DB plumbing)
    """

    source_provider: str
    product_category: str
    company_or_product: str | None
    competitor_name: str | None
    signal_type: SignalType
    sentiment_bucket: SentimentBucket
    buyer_type: BuyerType
    market_context: MarketContext
    theme: str | None
    short_snippet: str
    evidence_url: str | None
    relevance_score: float | None


@dataclass
class TechMarketEvidencePackage:
    """What the retriever returns to the simulation pipeline."""

    attempted: bool = False
    feature_flag_status: dict[str, bool] = field(default_factory=dict)
    product_category_matched: str | None = None
    market_context_matched: MarketContext | None = None
    signals: list[RetrievedTechSignal] = field(default_factory=list)
    distribution: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source Protocol + in-memory impl
# ---------------------------------------------------------------------------


class TechMarketSignalSource(Protocol):
    """Decoupled data-access layer. Production implementation (Phase
    11D.2) will hit Postgres via SQLAlchemy; tests use the in-memory
    list."""

    async def fetch_by_product_category(
        self,
        product_category: str,
        *,
        limit: int,
    ) -> list[TechSignalRow]:  # pragma: no cover - protocol
        ...

    async def fetch_by_market_context(
        self,
        market_context: MarketContext,
        *,
        limit: int,
    ) -> list[TechSignalRow]:  # pragma: no cover - protocol
        ...

    async def fetch_by_competitor(
        self,
        competitor: str,
        *,
        limit: int,
    ) -> list[TechSignalRow]:  # pragma: no cover - protocol
        ...

    async def fetch_by_signal_types(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int,
    ) -> list[TechSignalRow]:  # pragma: no cover - protocol
        ...


class InMemoryTechMarketSignalSource:
    """Test/dev signal source — static list of rows. NEVER imported
    from production code paths. Phase 11D.1 fixtures call this
    directly; Phase 11D.2 ingestion will populate Postgres via the
    Persister, and live retrieval will route through the future
    `PostgresTechMarketSignalSource`."""

    def __init__(self, rows: Iterable[TechSignalRow]) -> None:
        self.rows: list[TechSignalRow] = list(rows)

    async def fetch_by_product_category(
        self,
        product_category: str,
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        out = [r for r in self.rows if r.product_category == product_category]
        return _rank_rows(out)[:limit]

    async def fetch_by_market_context(
        self,
        market_context: MarketContext,
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        out = [r for r in self.rows if r.market_context == market_context]
        return _rank_rows(out)[:limit]

    async def fetch_by_competitor(
        self,
        competitor: str,
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        needle = (competitor or "").strip().lower()
        if not needle:
            return []
        out = [
            r for r in self.rows
            if (r.competitor_name or "").lower() == needle
            or (r.company_or_product or "").lower() == needle
        ]
        return _rank_rows(out)[:limit]

    async def fetch_by_signal_types(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        wanted = set(signal_types)
        out = [r for r in self.rows if r.signal_type in wanted]
        return _rank_rows(out)[:limit]


def _rank_rows(rows: list[TechSignalRow]) -> list[TechSignalRow]:
    """Simple ranking: higher relevance_score first, then negative
    sentiment first (objections are the most useful persona signal),
    then a stable sort by snippet prefix."""

    def key(r: TechSignalRow) -> tuple[float, int, str]:
        # We sort ascending, so negate "higher-is-better" scores.
        rel = -(r.relevance_score or 0.0)
        sentiment_rank = 0 if r.sentiment_bucket == "negative" else 1
        return (rel, sentiment_rank, r.short_snippet[:64])

    return sorted(rows, key=key)


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


_DEFAULT_PRIORITY_SIGNAL_TYPES: tuple[SignalType, ...] = (
    "pain_urgency",
    "switching_objection",
    "pricing_objection",
    "trust_security_concern",
    "integration_friction",
    "onboarding_friction",
    "support_complaint",
    "competitor_comparison",
    "willingness_to_pay",
    "workflow_fit",
    "developer_skepticism",
    "procurement_friction",
    "feature_not_company_risk",
    "nice_to_have_risk",
)


def _classify_product_category_hint(
    hint: str | None,
) -> str | None:
    """Map a free-text product category hint (e.g. 'AI SaaS tool',
    'browser extension') to the controlled vocabulary used in the
    `tech_market_signal.product_category` column.

    Returns None when the hint doesn't match a known label — the
    retriever then fails closed in `retrieve_for_product_brief`."""
    if not hint:
        return None
    h = hint.strip().lower()
    if not h:
        return None
    # Order matters: more specific labels first.
    rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("ai_saas",
         ("ai saas", "ai tool", "ai-powered saas", "llm tool", "llm app",
          "ai_saas")),
        ("browser_extension",
         ("browser extension", "chrome extension", "firefox extension",
          "browser_extension")),
        ("devtool_api",
         ("dev tool", "developer tool", "dev api", "developer api",
          "sdk", "devtool_api")),
        ("b2b_workflow_saas",
         ("b2b workflow", "b2b saas", "enterprise saas",
          "workflow saas", "b2b_workflow_saas")),
        ("consumer_mobile_app",
         ("consumer mobile app", "mobile app", "ios app", "android app",
          "consumer app", "consumer_mobile_app")),
        ("marketplace",
         ("marketplace", "two-sided marketplace", "marketplace platform")),
    )
    for label, needles in rules:
        for n in needles:
            if n in h:
                return label
    return None


class TechMarketSignalRetriever:
    """Phase 11D.1 retriever scaffold.

    Construction is always safe. Every public method short-circuits
    to an empty result when the feature flags are off — the
    in-memory source is never queried in that case.
    """

    def __init__(
        self,
        source: TechMarketSignalSource,
        *,
        config: TechMarketRetrievalConfig | None = None,
    ) -> None:
        self._source = source
        self.config = config or TechMarketRetrievalConfig()

    @property
    def is_active(self) -> bool:
        return self.config.fully_enabled

    async def retrieve_by_product_category(
        self,
        product_category: str,
        *,
        limit: int | None = None,
    ) -> list[RetrievedTechSignal]:
        if not self.is_active or not product_category.strip():
            return []
        rows = await self._source.fetch_by_product_category(
            product_category,
            limit=(limit or self.config.max_per_run),
        )
        return [_to_retrieved(r) for r in rows]

    async def retrieve_by_market_context(
        self,
        market_context: MarketContext,
        *,
        limit: int | None = None,
    ) -> list[RetrievedTechSignal]:
        if not self.is_active:
            return []
        rows = await self._source.fetch_by_market_context(
            market_context,
            limit=(limit or self.config.max_per_run),
        )
        return [_to_retrieved(r) for r in rows]

    async def retrieve_by_competitor(
        self,
        competitors: Sequence[str],
        *,
        limit: int | None = None,
    ) -> list[RetrievedTechSignal]:
        if not self.is_active or not competitors:
            return []
        merged: list[TechSignalRow] = []
        per_competitor = limit or self.config.max_per_run
        for c in competitors:
            if not c or not c.strip():
                continue
            merged.extend(
                await self._source.fetch_by_competitor(
                    c, limit=per_competitor,
                ),
            )
        return [_to_retrieved(r) for r in merged]

    async def retrieve_by_signal_types(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int | None = None,
    ) -> list[RetrievedTechSignal]:
        if not self.is_active or not signal_types:
            return []
        rows = await self._source.fetch_by_signal_types(
            signal_types,
            limit=(limit or self.config.max_per_run),
        )
        return [_to_retrieved(r) for r in rows]

    async def retrieve_for_product_brief(
        self,
        brief: TechProductBriefShape,
    ) -> TechMarketEvidencePackage:
        """Top-level scaffold method. Mixes the four single-pool
        helpers above and packages the result. Phase 11D.1 returns
        a simple deduped concatenation — Phase 11D.2+ will layer
        relevance scoring and bucket balancing on top.
        """
        pkg = TechMarketEvidencePackage(
            feature_flag_status={
                "tech_market_signals_enabled": self.config.enabled,
                "tech_market_signals_runtime_enabled":
                    self.config.runtime_enabled,
                "tech_market_signals_persona_injection_enabled":
                    self.config.persona_injection_enabled,
            },
        )
        if not self.is_active:
            pkg.notes.append(
                "feature_flag_off — tech-market retrieval disabled",
            )
            return pkg

        pkg.attempted = True
        product_category = _classify_product_category_hint(
            brief.product_category_hint,
        )
        pkg.product_category_matched = product_category
        pkg.market_context_matched = brief.market_context_hint

        collected: list[TechSignalRow] = []
        if product_category:
            collected.extend(
                await self._source.fetch_by_product_category(
                    product_category, limit=self.config.max_per_run,
                ),
            )
        if brief.market_context_hint:
            collected.extend(
                await self._source.fetch_by_market_context(
                    brief.market_context_hint,
                    limit=self.config.max_per_run,
                ),
            )
        for c in brief.competitors:
            if not c.strip():
                continue
            collected.extend(
                await self._source.fetch_by_competitor(
                    c, limit=self.config.max_per_run,
                ),
            )
        collected.extend(
            await self._source.fetch_by_signal_types(
                _DEFAULT_PRIORITY_SIGNAL_TYPES,
                limit=self.config.max_per_run,
            ),
        )

        deduped = _dedup_rows(collected)
        capped = deduped[: self.config.max_per_run]
        pkg.signals = [_to_retrieved(r) for r in capped]
        pkg.distribution = _distribution(capped)
        return pkg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEDUP_PREFIX = 96


def _dedup_key(row: TechSignalRow) -> tuple[str, str]:
    return (
        row.signal_type,
        " ".join((row.short_snippet or "").lower().split())[:_DEDUP_PREFIX],
    )


def _dedup_rows(rows: list[TechSignalRow]) -> list[TechSignalRow]:
    seen: set[tuple[str, str]] = set()
    out: list[TechSignalRow] = []
    for r in rows:
        key = _dedup_key(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _distribution(rows: list[TechSignalRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.signal_type] = counts.get(r.signal_type, 0) + 1
    return counts


def _to_retrieved(row: TechSignalRow) -> RetrievedTechSignal:
    """Strip every internal/PII-leaning column on the way out."""
    return RetrievedTechSignal(
        source_provider=row.source_provider,
        product_category=row.product_category,
        company_or_product=row.company_or_product,
        competitor_name=row.competitor_name,
        signal_type=row.signal_type,
        sentiment_bucket=row.sentiment_bucket,
        buyer_type=row.buyer_type,
        market_context=row.market_context,
        theme=row.theme,
        short_snippet=row.short_snippet,
        evidence_url=row.evidence_url,
        relevance_score=row.relevance_score,
    )


__all__ = [
    "InMemoryTechMarketSignalSource",
    "RetrievedTechSignal",
    "TechMarketEvidencePackage",
    "TechMarketRetrievalConfig",
    "TechMarketSignalRetriever",
    "TechMarketSignalSource",
    "TechProductBriefShape",
    "TechSignalRow",
]
