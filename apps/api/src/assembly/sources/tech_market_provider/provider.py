"""Phase 11D.1 — TechMarketSignalProvider scaffold.

The provider is the high-level orchestrator over the distiller +
the persister. Phase 11D.1 ships only:

  * The `TechMarketSignalProvider` Protocol.
  * A `FixtureTechMarketSignalProvider` that emits the synthetic
    Phase-11D.1 fixtures and is gated on `enabled=true`. The
    fixture path is DEV/TEST ONLY — it raises if called when
    `ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED` is False.

Real provider implementations (G2-style review scraper, Product
Hunt comment ingester, HN thread reader, etc.) land in Phase 11D.2
and beyond.

No live HTTP. No live scraping. No real data files. The Phase
11D.1 fixture is a hand-curated synthetic corpus designed to
exercise the distiller + retriever scaffolds.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from assembly.sources.tech_market_provider.distiller import (
    DistilledTechSignal,
    RuleBasedTechMarketDistiller,
    TechMarketSignalDistiller,
)
from assembly.sources.tech_market_provider.signal_types import MarketContext


@dataclass(frozen=True)
class TechMarketSignalProviderConfig:
    """Minimal config — only the gating flag the provider itself
    cares about. The retriever-side knobs live on
    `TechMarketRetrievalConfig`."""

    enabled: bool = False


class ProviderDisabledError(RuntimeError):
    """Raised when a fixture provider is called with
    `ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED=false`. Production code
    must check the flag before instantiating the provider."""


class TechMarketSignalProvider(Protocol):
    """Production interface. Concrete implementations:

      * `FixtureTechMarketSignalProvider` — dev/test only.
      * Phase 11D.2+ — `G2ReviewProvider`, `HNCommentProvider`,
        `ProductHuntProvider`, each with a real upstream and
        provider-specific distiller.
    """

    @property
    def name(self) -> str:  # pragma: no cover - protocol
        ...

    def load_raw_records(self) -> Iterable[
        tuple[str, MarketContext | None, dict]
    ]:
        """Yield (raw_text, market_context_hint, metadata) triples.
        Production providers do whatever IO they need here; the
        distiller never sees the IO."""
        ...  # pragma: no cover

    def distill(self) -> list[DistilledTechSignal]:
        """Drive the distiller across every yielded raw record."""
        ...  # pragma: no cover


@dataclass
class FixtureTechMarketSignalProvider:
    """Dev/test provider that emits the Phase-11D.1 synthetic
    fixtures and runs them through the rule-based distiller.

    Importable from production code (so the drift / structural
    tests can confirm the Protocol is satisfied), but the
    `distill()` entrypoint REFUSES to run unless
    `config.enabled=True`. That guarantees a production code path
    that accidentally instantiates the fixture provider can never
    silently inject synthetic data into a live simulation.
    """

    config: TechMarketSignalProviderConfig
    distiller: TechMarketSignalDistiller = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.distiller is None:
            self.distiller = RuleBasedTechMarketDistiller()

    @property
    def name(self) -> str:
        return "tech_market_fixture_synthetic"

    def load_raw_records(
        self,
    ) -> Iterable[tuple[str, MarketContext | None, dict]]:
        # Local import to avoid a module-level cycle and to keep
        # production imports of this module cheap (the fixture corpus
        # only gets walked when explicitly requested).
        from assembly.sources.tech_market_provider.fixtures import (
            iter_phase_11d_1_fixtures,
        )
        if not self.config.enabled:
            raise ProviderDisabledError(
                "FixtureTechMarketSignalProvider.load_raw_records "
                "called while ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED="
                "false — provider must remain off in production",
            )
        yield from iter_phase_11d_1_fixtures()

    def distill(self) -> list[DistilledTechSignal]:
        if not self.config.enabled:
            raise ProviderDisabledError(
                "FixtureTechMarketSignalProvider.distill "
                "called while ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED="
                "false — provider must remain off in production",
            )
        signals: list[DistilledTechSignal] = []
        for record in self.load_raw_records():
            text, market_hint, metadata = record
            provider_name = metadata.get(
                "_assembly_internal_source_provider", self.name,
            )
            product_category = metadata.get(
                "_assembly_internal_product_category", "unknown",
            )
            source_category = metadata.get(
                "_assembly_internal_source_category",
            )
            company_or_product = metadata.get(
                "_assembly_internal_company_or_product",
            )
            competitor_name = metadata.get(
                "_assembly_internal_competitor_name",
            )
            evidence_url = metadata.get(
                "_assembly_internal_evidence_url",
            )
            source_timestamp = metadata.get(
                "_assembly_internal_source_timestamp",
            )
            # Strip the internal scaffolding keys before passing the
            # rest through as provider metadata — keeps the audit
            # surface clean.
            public_meta = {
                k: v for k, v in metadata.items()
                if not k.startswith("_assembly_internal_")
            }
            signals.extend(
                self.distiller.distill(
                    text,
                    source_provider=provider_name,
                    source_category=source_category,
                    product_category=product_category,
                    company_or_product=company_or_product,
                    competitor_name=competitor_name,
                    market_context_hint=market_hint,
                    evidence_url=evidence_url,
                    source_timestamp=source_timestamp,
                    metadata=public_meta,
                ),
            )
        return signals


__all__ = [
    "FixtureTechMarketSignalProvider",
    "ProviderDisabledError",
    "TechMarketSignalProvider",
    "TechMarketSignalProviderConfig",
]
