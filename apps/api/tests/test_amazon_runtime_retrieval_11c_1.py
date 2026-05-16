"""Phase 11C.1 — runtime retrieval scaffold tests.

Covers the operator's acceptance checklist:

  1. Retriever returns empty package when feature flags off
  2. Retriever queries source when both flags on
  3. Category retrieval works
  4. Competitor / brand retrieval works
  5. Theme retrieval works
  6. Dedup by hash works
  7. Dedup by snippet collision works
  8. Per-brand cap enforced
  9. Per-theme cap enforced
 10. Retrieved signals NEVER carry user_id / images / source_review_hash
 11. Snippets stay capped
 12. retrieve_for_product_brief mixes signal types
 13. Brief→category classifier returns sensible categories
 14. Feature flag defaults are off
 15. Drift: no HTTP imports
 16. Drift: no live-pipeline integration

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES. Pure deterministic
in-memory tests.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

from assembly.sources.amazon_reviews_provider import (
    AmazonEvidencePackage,
    AmazonSignalRetriever,
    InMemorySignalSource,
    ProductBriefShape,
    RetrievalConfig,
    RetrievedSignal,
    SignalRow,
    classify_brief_to_category,
)


# ---------------------------------------------------------------------------
# Fixture factory
# ---------------------------------------------------------------------------


def _row(
    *,
    signal_type: str = "objection",
    sentiment: str = "negative",
    theme: str | None = "generic_disappointment",
    category: str = "All_Beauty",
    brand: str | None = "BrandX",
    title: str | None = "Title X",
    asin: str = "B000000001",
    parent_asin: str = "B000000001",
    rating: int | None = 1,
    verified: bool | None = True,
    helpful: int | None = 3,
    snippet: str = "This product was disappointing and overpriced.",
    competitor: str | None = None,
    use_case: str | None = None,
    review_hash: str = "h000",
) -> SignalRow:
    return SignalRow(
        signal_type=signal_type,
        sentiment_bucket=sentiment,
        theme=theme,
        category=category,
        brand=brand,
        product_title=title,
        asin=asin,
        parent_asin=parent_asin,
        rating=rating,
        verified_purchase=verified,
        helpful_votes=helpful,
        short_snippet=snippet,
        competitor_mention=competitor,
        use_case=use_case,
        source_review_hash=review_hash,
    )


def _enabled_cfg() -> RetrievalConfig:
    return RetrievalConfig(
        enabled=True,
        runtime_enabled=True,
        max_signals_per_run=80,
        max_signals_per_category=40,
        max_signals_per_competitor=20,
        max_signals_per_brand=8,
        max_signals_per_theme=10,
    )


def _retriever(rows: Iterable[SignalRow]) -> AmazonSignalRetriever:
    return AmazonSignalRetriever(
        InMemorySignalSource(rows), config=_enabled_cfg(),
    )


# ---------------------------------------------------------------------------
# 1. Feature-flag gates
# ---------------------------------------------------------------------------


def test_retriever_returns_empty_when_flags_off() -> None:
    """Either gate off → no DB access, empty results everywhere."""
    src = InMemorySignalSource([_row()])

    for cfg in (
        RetrievalConfig(enabled=False, runtime_enabled=False),
        RetrievalConfig(enabled=True, runtime_enabled=False),
        RetrievalConfig(enabled=False, runtime_enabled=True),
    ):
        r = AmazonSignalRetriever(src, config=cfg)
        assert r.is_active is False
        assert asyncio.run(r.retrieve_by_category("All_Beauty")) == []
        assert asyncio.run(r.retrieve_by_brand_or_competitor(["BrandX"])) == []
        assert asyncio.run(r.retrieve_by_theme(["objection"])) == []
        pkg = asyncio.run(r.retrieve_for_product_brief(
            ProductBriefShape(product_name="x"),
        ))
        assert pkg.attempted is False
        assert pkg.signals == []
        assert "feature_flag_off" in pkg.notes[0]


def test_retriever_queries_source_when_both_flags_on() -> None:
    rows = [_row(category="All_Beauty"),
            _row(category="Software", review_hash="h_sw")]
    r = _retriever(rows)
    assert r.is_active is True
    res = asyncio.run(r.retrieve_by_category("All_Beauty"))
    assert len(res) == 1
    assert res[0].category == "All_Beauty"


def test_settings_default_for_runtime_flag_off() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.amazon_reviews_enabled is False
    assert s.amazon_reviews_runtime_enabled is False


def test_retrieval_config_from_settings_reads_all_fields() -> None:
    """Confirm RetrievalConfig.from_settings picks up every new
    settings field — guards against typos when adding more knobs."""
    class _S:
        amazon_reviews_enabled = True
        amazon_reviews_runtime_enabled = True
        amazon_reviews_max_signals_per_run = 100
        amazon_reviews_max_signals_per_category = 50
        amazon_reviews_max_signals_per_competitor = 25
        amazon_reviews_max_signals_per_brand = 7
        amazon_reviews_max_signals_per_theme = 12
    cfg = RetrievalConfig.from_settings(_S())
    assert cfg.fully_enabled
    assert cfg.max_signals_per_run == 100
    assert cfg.max_signals_per_category == 50
    assert cfg.max_signals_per_competitor == 25
    assert cfg.max_signals_per_brand == 7
    assert cfg.max_signals_per_theme == 12


# ---------------------------------------------------------------------------
# 2. retrieve_by_category
# ---------------------------------------------------------------------------


def test_retrieve_by_category_returns_only_matching_rows() -> None:
    rows = [
        _row(category="All_Beauty", review_hash="h1",
             snippet="first beauty review"),
        _row(category="Software", review_hash="h2",
             snippet="software complaint"),
        _row(category="All_Beauty", review_hash="h3", brand="Y",
             snippet="second beauty review"),
    ]
    out = asyncio.run(_retriever(rows).retrieve_by_category("All_Beauty"))
    assert {s.category for s in out} == {"All_Beauty"}
    assert len(out) == 2


# ---------------------------------------------------------------------------
# 3. retrieve_by_brand_or_competitor
# ---------------------------------------------------------------------------


def test_retrieve_by_competitor_matches_brand_or_competitor_field() -> None:
    rows = [
        _row(brand="Nira", review_hash="h_brand",
             snippet="Brand-side review one."),
        _row(competitor="Nira", brand="OtherCo", review_hash="h_comp",
             snippet="Competitor-side review two."),
        _row(brand="UnrelatedBrand", review_hash="h_other",
             snippet="Unrelated brand review three."),
    ]
    out = asyncio.run(
        _retriever(rows).retrieve_by_brand_or_competitor(["Nira"]),
    )
    assert len(out) == 2
    assert all(
        (s.brand == "Nira") or (s.competitor_mention == "Nira")
        for s in out
    )


def test_retrieve_by_competitor_dedups_across_lookups() -> None:
    """If two competitor names point at the same row (e.g. brand
    AND competitor field both match), don't double-count."""
    rows = [_row(brand="Nira", competitor="Nira", review_hash="h1")]
    out = asyncio.run(
        _retriever(rows).retrieve_by_brand_or_competitor(["Nira", "Nira"]),
    )
    assert len(out) == 1


# ---------------------------------------------------------------------------
# 4. retrieve_by_theme
# ---------------------------------------------------------------------------


def test_retrieve_by_theme_filters_to_requested_types() -> None:
    rows = [
        _row(signal_type="objection", review_hash="h1"),
        _row(signal_type="praise", review_hash="h2"),
        _row(signal_type="setup", review_hash="h3"),
    ]
    out = asyncio.run(
        _retriever(rows).retrieve_by_theme(["objection", "setup"]),
    )
    types = {s.signal_type for s in out}
    assert types == {"objection", "setup"}


# ---------------------------------------------------------------------------
# 5. Dedup behaviour
# ---------------------------------------------------------------------------


def test_dedup_by_hash_signal_type_pair() -> None:
    """Same (source_review_hash, signal_type) pair appearing twice
    (e.g. fetched once via category and once via theme) must collapse."""
    r1 = _row(review_hash="h", signal_type="objection", brand="A")
    r2 = _row(review_hash="h", signal_type="objection", brand="A")
    out = asyncio.run(
        _retriever([r1, r2]).retrieve_by_category("All_Beauty"),
    )
    # Both rows passed the category filter, but only one survives dedup.
    assert len(out) == 1


def test_dedup_by_snippet_collision() -> None:
    """Two different hash-rows with substantially identical snippets
    (Amazon template reviews) should also collapse."""
    r1 = _row(
        review_hash="hA", signal_type="praise",
        snippet="This product is great and I would recommend it.",
        brand="A",
    )
    r2 = _row(
        review_hash="hB", signal_type="praise",
        snippet="This product is great and I would recommend it.",
        brand="B",
    )
    # Bypass per-category by routing through theme.
    out = asyncio.run(_retriever([r1, r2]).retrieve_by_theme(["praise"]))
    # Note: brand-aware dedup only collapses within the same signal_type
    # bucket. Both rows are praise, so the second collapses.
    assert len(out) == 1


# ---------------------------------------------------------------------------
# 6. Per-brand + per-theme caps
# ---------------------------------------------------------------------------


def test_per_brand_cap_enforced() -> None:
    """A single brand cannot fill the result pool beyond the cap."""
    rows = [
        _row(brand="HotBrand", review_hash=f"h{i}", snippet=f"snip {i}")
        for i in range(20)
    ]
    src = InMemorySignalSource(rows)
    r = AmazonSignalRetriever(
        src,
        config=RetrievalConfig(
            enabled=True, runtime_enabled=True,
            max_signals_per_category=40,
            max_signals_per_brand=3,
            max_signals_per_theme=999,
        ),
    )
    out = asyncio.run(r.retrieve_by_category("All_Beauty"))
    assert len(out) == 3
    assert {s.brand for s in out} == {"HotBrand"}


def test_per_theme_cap_enforced() -> None:
    """A single theme cannot crowd out other themes."""
    rows = [
        _row(brand=f"Brand{i}", theme="generic_disappointment",
             review_hash=f"h{i}", snippet=f"snip {i}")
        for i in range(20)
    ]
    src = InMemorySignalSource(rows)
    r = AmazonSignalRetriever(
        src,
        config=RetrievalConfig(
            enabled=True, runtime_enabled=True,
            max_signals_per_category=40,
            max_signals_per_brand=999,
            max_signals_per_theme=4,
        ),
    )
    out = asyncio.run(r.retrieve_by_category("All_Beauty"))
    assert len(out) == 4
    assert all(s.theme == "generic_disappointment" for s in out)


# ---------------------------------------------------------------------------
# 7. PII / forbidden-field invariants — RetrievedSignal must NEVER
#    expose user_id, images, source_review_hash, etc.
# ---------------------------------------------------------------------------


def test_retrieved_signal_dataclass_has_no_forbidden_fields() -> None:
    fields = set(RetrievedSignal.__dataclass_fields__)
    forbidden = {
        "user_id", "user_id_hash", "image", "images",
        "image_url", "image_urls", "raw_text", "full_text",
        "review_body", "review_text",
        "source_review_hash",  # implementation detail, not for personas
        "created_at", "id",
    }
    leaked = fields & forbidden
    assert leaked == set(), (
        f"RetrievedSignal exposes forbidden fields: {sorted(leaked)}"
    )


def test_retrieve_actually_drops_source_review_hash() -> None:
    """End-to-end: even though SignalRow carries
    source_review_hash, the RetrievedSignal returned to the caller
    must NOT."""
    rows = [_row(review_hash="this_must_never_leak")]
    out = asyncio.run(_retriever(rows).retrieve_by_category("All_Beauty"))
    assert out
    for s in out:
        assert not hasattr(s, "source_review_hash")


# ---------------------------------------------------------------------------
# 8. Snippet cap stays in force
# ---------------------------------------------------------------------------


def test_snippet_capped_to_distiller_limit() -> None:
    """Phase 11A capped snippets at 240 chars at distillation time.
    Retrieval must NOT re-expand them somehow."""
    rows = [_row(snippet="X" * 240)]
    out = asyncio.run(_retriever(rows).retrieve_by_category("All_Beauty"))
    assert out
    for s in out:
        assert len(s.short_snippet) <= 240


# ---------------------------------------------------------------------------
# 9. retrieve_for_product_brief mixes signal types
# ---------------------------------------------------------------------------


def test_retrieve_for_product_brief_returns_evidence_package() -> None:
    rows = [
        _row(category="All_Beauty", signal_type="objection",
             review_hash=f"hO{i}", snippet=f"obj {i}")
        for i in range(10)
    ] + [
        _row(category="All_Beauty", signal_type="price",
             review_hash=f"hP{i}", snippet=f"price {i}")
        for i in range(10)
    ] + [
        _row(category="All_Beauty", signal_type="praise",
             review_hash=f"hL{i}", snippet=f"love {i}")
        for i in range(10)
    ]
    r = _retriever(rows)
    pkg = asyncio.run(r.retrieve_for_product_brief(
        ProductBriefShape(
            product_name="Lavender hand cream",
            description="beauty product for skincare",
            competitors=[],
        ),
    ))
    assert isinstance(pkg, AmazonEvidencePackage)
    assert pkg.attempted is True
    assert pkg.feature_flag_status["amazon_reviews_enabled"] is True
    assert pkg.feature_flag_status["amazon_reviews_runtime_enabled"] is True
    assert pkg.category_matched == "All_Beauty"
    # Multiple signal types should appear in the distribution.
    assert len(pkg.distribution) >= 2
    assert pkg.signals


def test_retrieve_for_product_brief_balances_buckets() -> None:
    """30 rows in three signal types; with max_per_run=9 and theme
    cap=3, the retriever must spread across all 3 types instead of
    pulling 9 from the largest bucket."""
    rows = []
    for sig_type in ("objection", "price", "praise"):
        for i in range(20):
            rows.append(_row(
                category="All_Beauty",
                signal_type=sig_type,
                theme=sig_type,  # distinct theme per type
                review_hash=f"h_{sig_type}_{i}",
                brand=f"Brand{i}",
                snippet=f"{sig_type} {i}",
            ))
    src = InMemorySignalSource(rows)
    r = AmazonSignalRetriever(
        src,
        config=RetrievalConfig(
            enabled=True, runtime_enabled=True,
            max_signals_per_run=9,
            max_signals_per_category=60,
            max_signals_per_competitor=10,
            max_signals_per_brand=999,
            max_signals_per_theme=3,
        ),
    )
    pkg = asyncio.run(r.retrieve_for_product_brief(
        ProductBriefShape(product_name="lipstick beauty product"),
    ))
    # ≤ 9 signals total
    assert len(pkg.signals) <= 9
    # All 3 signal types should appear (each at the theme cap of 3)
    assert pkg.distribution.get("objection", 0) >= 1
    assert pkg.distribution.get("price", 0) >= 1
    assert pkg.distribution.get("praise", 0) >= 1


# ---------------------------------------------------------------------------
# 10. Brief → category classifier
# ---------------------------------------------------------------------------


def test_classifier_uses_explicit_hint_first() -> None:
    out = classify_brief_to_category(
        ProductBriefShape(
            product_name="any name",
            description="any description",
            category_hint="Software",
        ),
    )
    assert out == "Software"


@pytest.mark.parametrize("brief_text, expected", [
    ("AI-powered skincare serum for nightly use", "All_Beauty"),
    ("Browser extension that auto-fills passwords", "Software"),
    ("Wellness wearable that tracks stress and sleep",
     "Health_and_Personal_Care"),
    ("Industrial pressure sensor for laboratory ovens",
     "Industrial_and_Scientific"),
    ("Monthly subscription box for tea lovers", "Subscription_Boxes"),
])
def test_classifier_picks_sensible_category(
    brief_text: str, expected: str,
) -> None:
    out = classify_brief_to_category(
        ProductBriefShape(product_name=brief_text, description=""),
    )
    assert out == expected, (
        f"expected {expected!r} for {brief_text!r}, got {out!r}"
    )


def test_classifier_returns_none_when_no_match() -> None:
    out = classify_brief_to_category(
        ProductBriefShape(product_name="zzz uncategorized thingy"),
    )
    assert out is None


# ---------------------------------------------------------------------------
# 11. Coverage stats + skip reason reporting
# ---------------------------------------------------------------------------


def test_evidence_package_reports_coverage_and_skips() -> None:
    """`brand_coverage`/`title_coverage` are non-zero when populated;
    `skipped_reasons` is non-empty when caps trigger."""
    rows = [
        _row(brand="A", title="Title A",
             review_hash=f"hA{i}", snippet=f"a{i}")
        for i in range(15)
    ] + [
        _row(brand=None, title=None,
             review_hash=f"hB{i}", snippet=f"b{i}")
        for i in range(5)
    ]
    r = _retriever(rows)
    pkg = asyncio.run(r.retrieve_for_product_brief(
        ProductBriefShape(product_name="cosmetic beauty lotion"),
    ))
    assert 0.0 <= pkg.brand_coverage <= 1.0
    assert 0.0 <= pkg.title_coverage <= 1.0
    # At least one cap fired given 15 brand=A rows + brand cap=8.
    assert any(k.endswith("cap") for k in pkg.skipped_reasons), (
        f"expected at least one cap entry, got: {pkg.skipped_reasons}"
    )


# ---------------------------------------------------------------------------
# 12. Drift — no HTTP imports, no live-pipeline wiring
# ---------------------------------------------------------------------------


_PKG_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "amazon_reviews_provider"
)


def test_retrieval_modules_have_no_http_imports() -> None:
    forbidden = ("requests", "httpx", "aiohttp", "selenium",
                 "playwright", "scrapy", "bs4", "beautifulsoup4")
    for fname in ("retrieval.py", "postgres_source.py"):
        path = _PKG_DIR / fname
        src = path.read_text(encoding="utf-8")
        for token in forbidden:
            pattern = re.compile(
                rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
                re.MULTILINE,
            )
            assert pattern.search(src) is None, (
                f"{fname} imports forbidden module {token!r}"
            )


def test_runtime_retriever_only_wired_via_whitelisted_files() -> None:
    """Phase 11C.2 explicitly authorizes wiring the retriever into
    the simulation pipeline via the single helper file
    `pipeline/amazon_evidence_injector.py`. Every other file under
    `apps/api/src/assembly/{api,pipeline,orchestration}/` must still
    refrain from importing the retriever or the Postgres source
    directly — they go through the injector or not at all.

    Any new file that needs Amazon access must be added to this
    whitelist explicitly, with the operator's approval."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    whitelist = {
        # Phase 11C.2 — sole authorized injector. Imports
        # `AmazonSignalRetriever` + `PostgresSignalSource` directly
        # and exposes a flag-gated `build_amazon_evidence_section`
        # helper.
        api_root / "pipeline" / "amazon_evidence_injector.py",
    }
    live_dirs = [
        api_root / "api",
        api_root / "pipeline",
        api_root / "orchestration",
    ]
    forbidden = (
        "AmazonSignalRetriever",
        "amazon_reviews_provider.retrieval",
        "amazon_reviews_provider.postgres_source",
    )
    for d in live_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if path in whitelist:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in text, (
                    f"{path} imports {token!r} — Amazon access "
                    f"must go through pipeline/amazon_evidence_injector.py"
                )


def test_retrieval_module_does_not_import_sqlalchemy_at_module_level() -> None:
    """The retrieval module is the in-process façade. SQLAlchemy
    lives in postgres_source — keeping retrieval.py cheap to import
    in tests that don't need DB access."""
    from assembly.sources.amazon_reviews_provider import retrieval as r
    src = inspect.getsource(r)
    # Module-level imports only — function bodies may legitimately
    # use sqlalchemy via the postgres_source.
    head = src.split("def ", 1)[0]
    assert "import sqlalchemy" not in head
    assert "from sqlalchemy" not in head


# ---------------------------------------------------------------------------
# 13. Feature flag presence in settings
# ---------------------------------------------------------------------------


def test_all_five_new_runtime_settings_exist_on_settings() -> None:
    from assembly.config import Settings
    s = Settings()
    for attr, expected_default in [
        ("amazon_reviews_runtime_enabled", False),
        ("amazon_reviews_max_signals_per_run", 80),
        ("amazon_reviews_max_signals_per_category", 40),
        ("amazon_reviews_max_signals_per_competitor", 20),
        ("amazon_reviews_max_signals_per_brand", 8),
        ("amazon_reviews_max_signals_per_theme", 10),
    ]:
        assert hasattr(s, attr), f"Settings missing {attr}"
        assert getattr(s, attr) == expected_default, (
            f"Settings.{attr} default != {expected_default}"
        )


# ---------------------------------------------------------------------------
# 14. Existing flow not broken (smoke check via Phase 11A imports)
# ---------------------------------------------------------------------------


def test_phase_11a_provider_still_importable_alongside_retrieval() -> None:
    """Adding 11C.1 modules to the package must not break the
    existing Phase-11A/11B import surface."""
    from assembly.sources.amazon_reviews_provider import (
        AmazonReviewsProvider, AmazonReviewsProviderConfig,
        distill_review_signals, ingest_category,
        # …plus the new 11C.1 symbols:
        AmazonSignalRetriever, RetrievalConfig, AmazonEvidencePackage,
    )
    # Sanity: each is a class/callable, not None.
    for sym in (AmazonReviewsProvider, AmazonReviewsProviderConfig,
                distill_review_signals, ingest_category,
                AmazonSignalRetriever, RetrievalConfig,
                AmazonEvidencePackage):
        assert sym is not None
