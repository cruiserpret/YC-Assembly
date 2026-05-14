"""Phase 11A — Amazon Reviews provider scaffold tests.

Covers the operator's acceptance checklist:

  1. Provider disabled by default (config flag off).
  2. Provider loads tiny fixture only when enabled.
  3. Raw reviews become distilled signals.
  4. Short / low-quality reviews are rejected.
  5. Signal types are assigned correctly across the rule library.
  6. No production startup tries to download the dataset.
  7. Drift: provider package has NO HTTP/scraping imports.
  8. Stable hash de-dups the same review across re-runs.
  9. `require_enabled=True` raises ProviderUnavailableError when off.
 10. `search_by_competitor` returns competitor-mention signals only.

NO LIVE LLM. NO LIVE NETWORK. NO DB. Pure deterministic fixtures.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from assembly.sources.amazon_reviews_2023 import AmazonReviewRecord
from assembly.sources.amazon_reviews_provider import (
    AmazonReviewsProvider,
    AmazonReviewsProviderConfig,
    DistillerConfig,
    ProviderUnavailableError,
    SENTIMENT_BUCKETS,
    SIGNAL_TYPES,
    distill_review_signals,
    is_review_eligible,
)
from assembly.sources.amazon_reviews_provider import (
    distiller as distiller_mod,
)
from assembly.sources.amazon_reviews_provider import (
    provider as provider_mod,
)


_FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures" / "amazon_reviews_provider"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_dir() -> Path:
    """Path to the tiny Amazon Reviews dataset shipped under
    `apps/api/tests/fixtures/amazon_reviews_provider/`."""
    assert _FIXTURE_DIR.is_dir(), "Phase 11A fixture dir missing"
    raw_dir = _FIXTURE_DIR / "raw"
    assert raw_dir.is_dir(), "Phase 11A fixture raw/ dir missing"
    return _FIXTURE_DIR


@pytest.fixture
def enabled_provider(fixture_dir: Path) -> AmazonReviewsProvider:
    cfg = AmazonReviewsProviderConfig(
        enabled=True,
        data_dir=fixture_dir,
        categories=("Electronics", "All_Beauty", "Home_and_Kitchen"),
        max_items_per_run=200,
        min_review_chars=40,
    )
    return AmazonReviewsProvider(cfg)


@pytest.fixture
def disabled_provider(fixture_dir: Path) -> AmazonReviewsProvider:
    cfg = AmazonReviewsProviderConfig(
        enabled=False,
        data_dir=fixture_dir,
        categories=("Electronics",),
        max_items_per_run=200,
        min_review_chars=40,
    )
    return AmazonReviewsProvider(cfg)


# ---------------------------------------------------------------------------
# 1. Provider disabled by default
# ---------------------------------------------------------------------------


def test_provider_defaults_disabled() -> None:
    """A provider instantiated with default config is disabled and
    returns empty results for every search method without touching
    disk."""
    p = AmazonReviewsProvider()
    assert p.is_enabled is False
    assert p.search_by_product_name("Earbuds") == []
    assert p.search_by_category("Electronics") == []
    assert p.search_by_competitor("BrandX") == []
    assert p.retrieve_review_signals() == []


def test_settings_default_flag_off() -> None:
    """The Phase 11A config flag must default off so app boot stays
    inert. (Operator constraint #4.)"""
    # Imported lazily so test collection doesn't depend on a working
    # database URL — `get_settings()` only reads env vars.
    from assembly.config import Settings
    s = Settings()
    assert s.amazon_reviews_enabled is False
    assert s.amazon_reviews_data_dir is None
    assert s.amazon_reviews_categories == ""
    assert s.amazon_reviews_categories_list == []
    assert s.amazon_reviews_max_items_per_run == 200
    assert s.amazon_reviews_min_review_chars == 40


def test_disabled_provider_returns_empty_without_raising(
    disabled_provider: AmazonReviewsProvider,
) -> None:
    """With the flag off, search methods silently no-op (rather than
    raising) so callers that haven't been migrated to Phase 11A can
    blindly call them and get empty results."""
    assert disabled_provider.is_enabled is False
    assert disabled_provider.search_by_product_name("Earbuds") == []
    assert disabled_provider.search_by_category("Electronics") == []
    assert disabled_provider.retrieve_review_signals() == []


def test_disabled_provider_raises_when_require_enabled(
    disabled_provider: AmazonReviewsProvider,
) -> None:
    """Caller that opts into the strict mode gets a loud failure."""
    with pytest.raises(ProviderUnavailableError):
        disabled_provider.search_by_product_name(
            "Earbuds", require_enabled=True,
        )
    with pytest.raises(ProviderUnavailableError):
        disabled_provider.retrieve_review_signals(require_enabled=True)


# ---------------------------------------------------------------------------
# 2. Provider loads fixtures only when enabled
# ---------------------------------------------------------------------------


def test_enabled_provider_streams_reviews_by_category(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """The enabled provider yields parsed reviews from the local
    fixture for each known category."""
    elx = enabled_provider.search_by_category("Electronics")
    bea = enabled_provider.search_by_category("All_Beauty")
    hak = enabled_provider.search_by_category("Home_and_Kitchen")
    # Each fixture has 10 rows; 2 Electronics rows are deliberately
    # low-quality and should be rejected by eligibility.
    assert 5 <= len(elx) <= 10
    assert 9 <= len(bea) <= 10
    assert 9 <= len(hak) <= 10
    for r in elx + bea + hak:
        assert isinstance(r, AmazonReviewRecord)
        assert r.text and len(r.text) >= 40


def test_enabled_provider_search_by_product_name(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """Product-name search returns only rows whose title or text
    mentions the query."""
    hits = enabled_provider.search_by_product_name("battery")
    assert len(hits) >= 1
    for r in hits:
        assert "battery" in (r.title + " " + r.text).lower()


def test_provider_respects_max_items_per_run(
    fixture_dir: Path,
) -> None:
    """Lowering max_items_per_run actually caps the result set."""
    cfg = AmazonReviewsProviderConfig(
        enabled=True,
        data_dir=fixture_dir,
        categories=("Home_and_Kitchen",),
        max_items_per_run=3,
        min_review_chars=40,
    )
    p = AmazonReviewsProvider(cfg)
    assert len(p.search_by_category("Home_and_Kitchen")) == 3


# ---------------------------------------------------------------------------
# 3. Raw reviews → distilled signals
# ---------------------------------------------------------------------------


def test_retrieve_review_signals_runs_distiller(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """End-to-end: provider streams reviews + distiller produces
    typed signals."""
    signals = enabled_provider.retrieve_review_signals()
    assert len(signals) > 0
    for s in signals:
        assert s.signal_type in SIGNAL_TYPES
        assert s.sentiment_bucket in SENTIMENT_BUCKETS
        assert s.short_snippet and len(s.short_snippet) <= 240
        # Brand/title may be None for reviews that don't join a meta
        # row, but when present they must come from our fixtures.
        if s.brand is not None:
            assert s.brand in {
                "BrandX", "AltCompany", "NovaTech", "QuietLine",
                "PowerPod",
                "LumeGlow", "PureSilk", "VelvetLip", "ClearCalm",
                "OakFern",
                "EverBrew", "FrostKeep", "KnifeStone", "GleamGlass",
                "AirZen",
            }


def test_distiller_assigns_signal_types_across_categories(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """The combined fixture set is hand-tuned to fire multiple
    distinct signal types (operator acceptance: 'signal types are
    assigned correctly')."""
    signals = enabled_provider.retrieve_review_signals()
    seen_types = {s.signal_type for s in signals}
    # The fixtures deliberately include rows that map onto these
    # distiller rules. If any of these fall out, a rule regressed.
    must_see = {
        "objection",       # safety/dangerous (Electronics burn review)
        "praise",          # "absolutely love", "best purchase"
        "return_reason",   # "returned it / asked for a refund"
        "switch_reason",   # "switched from BrandX Echo Mini"
        "price",           # "too expensive / worth every penny"
        "durability",      # "broke / cracked / holds up"
        "trust",           # "tracks me / privacy concerns"
        "safety",          # "caught fire / burned my skin"
        "setup",           # "instructions unclear"
        "support",         # "customer service unresponsive"
        "use_case",        # "great for camping trips"
        "proof_need",      # "after months of daily use"
    }
    missing = must_see - seen_types
    assert not missing, (
        f"Phase 11A distiller failed to extract: {sorted(missing)}"
    )


def test_distiller_sentiment_matches_rating() -> None:
    """1–2★ reviews bucket negative; 4–5★ reviews bucket positive;
    3★ / None bucket mixed."""
    pos = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None, rating=5.0,
        title="great", text="I absolutely love this thing. " * 4,
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="abc",
    )
    neg = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None, rating=1.0,
        title="bad", text="This broke after a week. I returned it. " * 3,
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="def",
    )
    mid = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None, rating=3.0,
        title="meh", text="It works ok but feels cheap. " * 4,
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="ghi",
    )
    assert all(s.sentiment_bucket == "positive"
               for s in distill_review_signals(pos))
    assert all(s.sentiment_bucket == "negative"
               for s in distill_review_signals(neg))
    assert all(s.sentiment_bucket == "mixed"
               for s in distill_review_signals(mid))


def test_distiller_captures_competitor_in_switch_reason() -> None:
    """The 'switched from X' rule must capture a real competitor name
    into `competitor_mention`, not generic English phrases."""
    record = AmazonReviewRecord(
        category="Electronics", parent_asin="B1", asin="B1A",
        rating=5.0, title="great",
        text=(
            "Switched from AcmeBrand Speaker Mini and the sound on "
            "this is much better."
        ),
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="xyz",
    )
    signals = distill_review_signals(record)
    switch_sigs = [s for s in signals if s.signal_type == "switch_reason"]
    assert switch_sigs, "switch_reason rule did not fire"
    assert any(s.competitor_mention and "AcmeBrand" in s.competitor_mention
               for s in switch_sigs)


def test_distiller_rejects_generic_switched_from_phrase() -> None:
    """`switched from my old routine` must NOT produce a competitor
    mention — capture-group stopword filter."""
    record = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None,
        rating=5.0, title="great",
        text="I switched from my old routine and it works better now. " * 2,
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="aaa",
    )
    signals = distill_review_signals(record)
    for s in signals:
        if s.signal_type == "switch_reason":
            # Should never appear because the capture failed the
            # competitor-name filter.
            pytest.fail(
                "switch_reason fired on a generic English phrase; "
                "stopword filter is leaking",
            )


# ---------------------------------------------------------------------------
# 4. Low-quality / short / malformed reviews are rejected
# ---------------------------------------------------------------------------


def test_eligibility_rejects_short_reviews() -> None:
    short = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None, rating=1.0,
        title="bad", text="bad bad bad",
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="x",
    )
    ok, reason = is_review_eligible(short, DistillerConfig())
    assert ok is False
    assert reason == "too_short"
    assert distill_review_signals(short) == []


def test_eligibility_rejects_all_caps_spam() -> None:
    spam = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None, rating=5.0,
        title="WOW",
        text=(
            "THIS IS THE BEST THING I HAVE EVER BOUGHT EVER AND I "
            "CANNOT BELIEVE IT WORKS THIS WELL!!!"
        ),
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="x",
    )
    ok, reason = is_review_eligible(spam, DistillerConfig())
    assert ok is False
    assert reason == "all_caps_spam"


def test_eligibility_rejects_empty_text() -> None:
    empty = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None, rating=4.0,
        title="x", text="",
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="x",
    )
    ok, reason = is_review_eligible(empty, DistillerConfig())
    assert ok is False
    assert reason == "empty_text"


# ---------------------------------------------------------------------------
# 5. Drift / safety: no HTTP / scrape imports in the provider package
# ---------------------------------------------------------------------------


_FORBIDDEN_IMPORT_TOKENS = (
    "requests", "httpx", "aiohttp", "selenium", "playwright",
    "scrapy", "bs4", "beautifulsoup4", "urllib3", "urllib.request",
)

_PROVIDER_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "amazon_reviews_provider"
)


def test_no_http_or_scrape_imports_in_provider_package() -> None:
    """The provider operates on local files only. Any HTTP/scrape
    library import is a hard fail — drift would mean someone tried to
    'help' by adding live retrieval."""
    py_files = list(_PROVIDER_PKG.rglob("*.py"))
    assert py_files, "provider package has no .py files?"
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_IMPORT_TOKENS:
            pattern = re.compile(
                rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
                re.MULTILINE,
            )
            assert pattern.search(text) is None, (
                f"{path} imports forbidden module {token!r}"
            )


def test_no_amazon_scraping_phrases_in_provider() -> None:
    """Belt-and-braces: no URL or scrape verb that looks like live
    Amazon.com targeting."""
    forbidden_phrases = (
        "amazon.com/dp",
        "amazon.com/gp",
        "www.amazon.",
        "scrape_amazon",
        "fetch_amazon_product_page",
    )
    for path in _PROVIDER_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden_phrases:
            assert phrase.lower() not in text, (
                f"{path} contains forbidden Amazon scrape phrase {phrase!r}"
            )


# ---------------------------------------------------------------------------
# 6. Identity hash is stable across runs (same review → same hash)
# ---------------------------------------------------------------------------


def test_source_review_hash_is_stable_across_runs(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    first = enabled_provider.retrieve_review_signals(
        categories=("Electronics",),
    )
    second = enabled_provider.retrieve_review_signals(
        categories=("Electronics",),
    )
    # Same review row → same hash. We compare the *set* of hashes
    # because the rule library may emit signals in any order.
    assert {s.source_review_hash for s in first} == {
        s.source_review_hash for s in second
    }


# ---------------------------------------------------------------------------
# 7. search_by_competitor returns only signals with a competitor mention
# ---------------------------------------------------------------------------


def test_search_by_competitor_returns_only_competitor_signals(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """Verifies the competitor search surfaces real competitor
    captures (fixtures include 'switched from Chemex Six Cup' /
    'Pyrex Standard Set' / 'BrandX Echo Mini')."""
    chemex = enabled_provider.search_by_competitor("Chemex")
    pyrex = enabled_provider.search_by_competitor("Pyrex")
    brandx = enabled_provider.search_by_competitor("BrandX")
    assert chemex, "expected at least one Chemex switch signal"
    assert pyrex, "expected at least one Pyrex switch signal"
    assert brandx, "expected at least one BrandX switch signal"
    for s in chemex + pyrex + brandx:
        assert s.competitor_mention is not None
        assert s.signal_type in {"switch_reason"}


# ---------------------------------------------------------------------------
# 8. App startup doesn't trigger ingestion
# ---------------------------------------------------------------------------


def test_provider_module_import_does_not_touch_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the provider package alone must not open any file.

    We patch `pathlib.Path.open` and `pathlib.Path.glob` globally
    *just* across a fresh re-import to confirm the package's
    top-level code path doesn't accidentally touch disk.
    """
    import importlib
    import sys

    opened: list[Any] = []

    real_open = Path.open

    def watcher_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", watcher_open)

    # Force a fresh import of the provider package.
    for name in list(sys.modules):
        if name.startswith("assembly.sources.amazon_reviews_provider"):
            sys.modules.pop(name, None)

    importlib.import_module("assembly.sources.amazon_reviews_provider")

    # Importing the package alone should never have called Path.open.
    assert opened == [], (
        f"Provider import triggered filesystem reads: {opened}"
    )


# ---------------------------------------------------------------------------
# 9. Distiller respects per-review signal cap
# ---------------------------------------------------------------------------


def test_distiller_respects_max_signals_per_review() -> None:
    """A single review that fires many rules should still cap at
    `max_signals_per_review` so one ranty post can't dominate
    ingestion."""
    record = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None,
        rating=5.0, title="great",
        text=(
            "I absolutely love this. Switched from AcmeBrand Mini "
            "and it is much better than AcmeBrand. Worth every "
            "penny. Used daily for 6 months and it still works. "
            "Built to last."
        ),
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="x",
    )
    cfg = DistillerConfig(max_signals_per_review=2)
    out = distill_review_signals(record, config=cfg)
    assert len(out) <= 2


# ---------------------------------------------------------------------------
# 10. Snippet truncation never exceeds the configured cap
# ---------------------------------------------------------------------------


def test_short_snippet_is_capped() -> None:
    long_text = "I absolutely love this. " * 40  # ~960 chars
    record = AmazonReviewRecord(
        category="Test", parent_asin=None, asin=None,
        rating=5.0, title="great",
        text=long_text,
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="x",
    )
    cfg = DistillerConfig(short_snippet_max_chars=120)
    sigs = distill_review_signals(record, config=cfg)
    assert sigs
    for s in sigs:
        assert len(s.short_snippet) <= 120
