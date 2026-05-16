"""Phase 11C.2 — local-only Amazon evidence injection scaffold.

Only file outside `apps/api/src/assembly/sources/amazon_reviews_provider/`
that is permitted to import the Phase-11C runtime retriever. The drift
test in `tests/test_amazon_runtime_retrieval_11c_1.py` whitelists this
exact path and asserts no other production file imports the retriever
yet.

What this module does:

  * Builds a sanitized AUDIT-ONLY section describing the Amazon
    retrieval attempt for one product brief.
  * Is double-gated. Both `ASSEMBLY_AMAZON_REVIEWS_ENABLED` AND
    `ASSEMBLY_AMAZON_REVIEWS_RUNTIME_ENABLED` must be True for it
    to touch the DB. Otherwise it returns None (or an audit dict
    that explicitly reports the disabled state, depending on caller
    preference).
  * Never injects rows into `evidence_items`. Persona generation
    sees ZERO Amazon evidence for now. Phase 11C.3+ will decide
    whether/how to feed Amazon signals into personas, gated by
    measured prediction-accuracy improvements (Phase 12).

What this module does NOT do:

  * No persistence to `amazon_review_signal` (read-only).
  * No persistence to `evidence_items` (audit-only output).
  * No live retrieval from McAuley URLs (offline-only — reads the
    table the Phase-11B ingestion populated).
  * No exposure of raw review bodies, user IDs, source_review_hash,
    image URLs, or any DB row id — `RetrievedSignal` already strips
    those.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from assembly.sources.amazon_reviews_provider import (
    AmazonEvidencePackage,
    AmazonSignalRetriever,
    ProductBriefShape,
    RetrievalConfig,
)
from assembly.sources.amazon_reviews_provider.postgres_source import (
    PostgresSignalSource,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from assembly.config import Settings
    from assembly.schemas.brief import SimulationBriefIn


# Number of sample signals to embed in the audit dict so the operator
# can eyeball quality without opening Postgres. Mirrors the Phase-11B
# audit sample size.
_AUDIT_SAMPLE_SIZE = 6


def _brief_to_product_shape(brief: "SimulationBriefIn") -> ProductBriefShape:
    """Adapt the in-pipeline `SimulationBriefIn` to the slim shape
    the retriever expects. Pure projection — no derived fields."""
    competitors: list[str] = []
    for c in brief.competitors:
        # CompetitorRef has a `name` field in current schemas; tolerate
        # plain strings + objects without `.name` defensively.
        name = getattr(c, "name", None) or (
            c.get("name") if isinstance(c, dict) else None
        )
        if isinstance(name, str) and name.strip():
            competitors.append(name.strip())
    # `product_type` doubles as a category hint — the classifier
    # treats it as a coarse text input alongside name + description.
    return ProductBriefShape(
        product_name=brief.product_name,
        description=brief.description,
        category_hint=brief.product_type,
        competitors=tuple(competitors),
    )


def _audit_from_package(
    pkg: AmazonEvidencePackage,
    *,
    config: RetrievalConfig,
) -> dict[str, Any]:
    """Render the Phase-11C.2 audit-section dict per operator spec."""
    return {
        "provider": "amazon_reviews_2023",
        "amazon_attempted": pkg.attempted,
        "amazon_enabled": config.enabled,
        "amazon_runtime_enabled": config.runtime_enabled,
        "same_category_only": config.same_category_only,
        "category_matched": pkg.category_matched,
        "signals_retrieved": len(pkg.signals),
        "signal_distribution": dict(pkg.distribution),
        "brand_coverage": round(pkg.brand_coverage, 3),
        "title_coverage": round(pkg.title_coverage, 3),
        "skipped_reasons": dict(pkg.skipped_reasons),
        "notes": list(pkg.notes),
        "sample_signals": [
            {
                "signal_type": s.signal_type,
                "sentiment_bucket": s.sentiment_bucket,
                "theme": s.theme,
                "category": s.category,
                "brand": s.brand,
                "product_title": (
                    s.product_title[:160] + "…"
                    if s.product_title and len(s.product_title) > 160
                    else s.product_title
                ),
                "rating": s.rating,
                "short_snippet": s.short_snippet,
                "competitor_mention": s.competitor_mention,
                "use_case": s.use_case,
                "verified_purchase": s.verified_purchase,
                "helpful_votes": s.helpful_votes,
            }
            for s in pkg.signals[:_AUDIT_SAMPLE_SIZE]
        ],
    }


def _disabled_audit(config: RetrievalConfig) -> dict[str, Any]:
    """Off-state audit dict — never touches the DB. Returned when
    either feature flag is False so callers always get a uniform
    audit shape they can stash in the per-run record."""
    return {
        "provider": "amazon_reviews_2023",
        "amazon_attempted": False,
        "amazon_enabled": config.enabled,
        "amazon_runtime_enabled": config.runtime_enabled,
        "same_category_only": config.same_category_only,
        "category_matched": None,
        "signals_retrieved": 0,
        "signal_distribution": {},
        "brand_coverage": 0.0,
        "title_coverage": 0.0,
        "skipped_reasons": {},
        "notes": [
            "feature_flag_off — Amazon retrieval disabled, "
            "no DB read attempted",
        ],
        "sample_signals": [],
    }


async def build_amazon_evidence_section(
    brief: "SimulationBriefIn",
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> dict[str, Any] | None:
    """Run the Amazon retriever for `brief` and return an audit dict,
    or a uniform disabled-state dict when the flags forbid the call.

    The DB session is opened lazily by `PostgresSignalSource` only
    inside its three `fetch_*` methods — there is no connection cost
    when both flags are off.

    Caller is expected to attach the returned dict to whatever
    per-simulation audit structure they already keep. Phase 11C.2
    attaches it to `EvidenceBuildResult.amazon_audit`. Phase 11C.4
    surfaces it inside `founder_report.json` under
    `technical.amazon_reviews_2023`.
    """
    config = RetrievalConfig.from_settings(settings)
    if not config.fully_enabled:
        return _disabled_audit(config)

    source = PostgresSignalSource(sessionmaker)
    retriever = AmazonSignalRetriever(source, config=config)
    pkg = await retriever.retrieve_for_product_brief(
        _brief_to_product_shape(brief),
    )
    return _audit_from_package(pkg, config=config)


async def build_amazon_evidence_section_from_dict_brief(
    brief: dict[str, Any],
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> dict[str, Any]:
    """Phase 11C.4 — variant for callers that already hold the
    founder brief as a plain dict (e.g.
    `AssemblyRun.product_brief`) rather than a Pydantic
    `SimulationBriefIn`. Reads the same fields the founder form
    submits — `product_name`, `product_description`,
    `category_hint`, `competitors_or_alternatives` — and routes
    through the same retriever + audit serializer as the canonical
    helper above. Returns a uniform audit dict in every case (the
    disabled-state dict when flags are off)."""
    config = RetrievalConfig.from_settings(settings)
    if not config.fully_enabled:
        return _disabled_audit(config)

    competitors: list[str] = []
    for c in (brief.get("competitors_or_alternatives") or []):
        if isinstance(c, str) and c.strip():
            competitors.append(c.strip())
        elif isinstance(c, dict):
            name = c.get("name")
            if isinstance(name, str) and name.strip():
                competitors.append(name.strip())

    shape = ProductBriefShape(
        product_name=str(brief.get("product_name") or "").strip(),
        description=str(brief.get("product_description") or "").strip(),
        category_hint=(brief.get("category_hint") or None),
        competitors=tuple(competitors),
    )

    source = PostgresSignalSource(sessionmaker)
    retriever = AmazonSignalRetriever(source, config=config)
    pkg = await retriever.retrieve_for_product_brief(shape)
    return _audit_from_package(pkg, config=config)


__all__ = [
    "build_amazon_evidence_section",
    "build_amazon_evidence_section_from_dict_brief",
]
