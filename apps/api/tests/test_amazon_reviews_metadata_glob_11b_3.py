"""Phase 11B.3 — metadata-discovery glob fix tests.

Operator-spec'd acceptance:

  1. Discovery finds `meta_<Category>.jsonl[.gz]` (McAuley canonical).
  2. Discovery still finds `<Category>_meta.jsonl[.gz]` (legacy suffix).
  3. When both forms exist in one directory, the canonical prefix
     form wins (it is the source of truth).
  4. Discovery is exact on the category portion — must NOT match
     `meta_Toys_and_Games.jsonl.gz` when the caller asks for `Toys`.
  5. End-to-end: with a McAuley-named metadata file on disk, the
     provider's distilled signals carry brand + product_title.
  6. Phase 11A synthetic fixtures (suffix form) still join cleanly.
  7. No HTTP imports introduced by the patch.
  8. Feature flag remains off by default.
  9. Provider not wired into production live runtime.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES. Pure local-fixture
unit tests.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from assembly.sources.amazon_reviews_2023 import AmazonReviewRecord
from assembly.sources.amazon_reviews_provider import (
    AmazonReviewsProvider,
    AmazonReviewsProviderConfig,
)
from assembly.sources.amazon_reviews_provider.provider import (
    _category_metadata_path,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def _meta_row(parent_asin: str, title: str, store: str) -> dict:
    return {
        "parent_asin": parent_asin,
        "title": title,
        "store": store,
        "main_category": "All Beauty",
        "average_rating": 4.5,
        "rating_number": 10,
    }


def _review_row(parent_asin: str, asin: str, text: str) -> dict:
    return {
        "rating": 5.0,
        "title": "love this",
        "text": text,
        "images": [],
        "asin": asin,
        "parent_asin": parent_asin,
        "user_id": "AEMJ2EG5ODOCYUTI54NBXZHDJGSQ",
        "timestamp": 1602133857705,
        "helpful_vote": 2,
        "verified_purchase": True,
    }


# ---------------------------------------------------------------------------
# 1. McAuley canonical prefix form
# ---------------------------------------------------------------------------


def test_discovery_finds_mcauley_prefix_form(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    target = raw / "meta_All_Beauty.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    found = _category_metadata_path(tmp_path, "All_Beauty")
    assert found == target


def test_discovery_finds_mcauley_prefix_form_gzipped(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    target = raw / "meta_All_Beauty.jsonl.gz"
    target.write_bytes(b"")  # empty gz placeholder is fine for the glob
    found = _category_metadata_path(tmp_path, "All_Beauty")
    assert found == target


# ---------------------------------------------------------------------------
# 2. Legacy suffix form still supported
# ---------------------------------------------------------------------------


def test_discovery_finds_legacy_suffix_form(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    target = raw / "All_Beauty_meta.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    found = _category_metadata_path(tmp_path, "All_Beauty")
    assert found == target


def test_phase_11a_synthetic_fixtures_still_resolve() -> None:
    """Direct check against the existing Phase 11A test fixtures —
    they use the suffix form `<Category>_meta.jsonl` and must keep
    working after the glob patch."""
    fixture_dir = (
        Path(__file__).resolve().parent
        / "fixtures" / "amazon_reviews_provider"
    )
    for category in ("Electronics", "All_Beauty", "Home_and_Kitchen"):
        path = _category_metadata_path(fixture_dir, category)
        assert path is not None, (
            f"Phase 11A fixture for {category} stopped resolving "
            f"after the 11B.3 glob change"
        )
        assert path.name == f"{category}_meta.jsonl"


# ---------------------------------------------------------------------------
# 3. Prefix form wins when both are present
# ---------------------------------------------------------------------------


def test_discovery_prefers_prefix_form_when_both_exist(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    suffix = raw / "All_Beauty_meta.jsonl"
    prefix = raw / "meta_All_Beauty.jsonl"
    suffix.write_text("{}\n", encoding="utf-8")
    prefix.write_text("{}\n", encoding="utf-8")
    found = _category_metadata_path(tmp_path, "All_Beauty")
    assert found == prefix


# ---------------------------------------------------------------------------
# 4. Exact-match — short category names must not over-match
# ---------------------------------------------------------------------------


def test_discovery_does_not_cross_contaminate_short_categories(
    tmp_path: Path,
) -> None:
    """Asking for category `Toys` must NOT discover
    `meta_Toys_and_Games.jsonl.gz` — the patterns are anchored
    exactly on the category portion."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "meta_Toys_and_Games.jsonl.gz").write_bytes(b"")
    assert _category_metadata_path(tmp_path, "Toys") is None
    # And the real category resolves correctly.
    assert (
        _category_metadata_path(tmp_path, "Toys_and_Games")
        == raw / "meta_Toys_and_Games.jsonl.gz"
    )


def test_discovery_suffix_form_also_exact_match(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "Toys_and_Games_meta.jsonl").write_text("{}\n", encoding="utf-8")
    # `Toys` must not resolve to the longer file.
    assert _category_metadata_path(tmp_path, "Toys") is None
    assert (
        _category_metadata_path(tmp_path, "Toys_and_Games")
        == raw / "Toys_and_Games_meta.jsonl"
    )


# ---------------------------------------------------------------------------
# 5. End-to-end metadata join — McAuley naming on disk → brand + title
# ---------------------------------------------------------------------------


def test_provider_joins_brand_and_title_from_mcauley_naming(
    tmp_path: Path,
) -> None:
    """Create a tiny fixture using the McAuley canonical naming and
    confirm distilled signals carry the joined `brand` + `product_title`."""
    raw = tmp_path / "raw"
    raw.mkdir()

    # Metadata file with the canonical prefix name.
    _write_jsonl(
        raw / "meta_All_Beauty.jsonl",
        [
            _meta_row(
                "B0BEAUTY01",
                "Acme Lavender Hand Cream 50ml",
                "Acme Skincare",
            ),
            _meta_row(
                "B0BEAUTY02",
                "Nova Botanicals Vitamin C Serum",
                "Nova Botanicals",
            ),
        ],
    )

    # Reviews — text deliberately fires the praise + use_case rules.
    _write_jsonl(
        raw / "All_Beauty.jsonl",
        [
            _review_row(
                "B0BEAUTY01", "B0BEAUTY01A",
                "I absolutely love this. Used daily for 6 months. "
                "Worth every penny.",
            ),
            _review_row(
                "B0BEAUTY02", "B0BEAUTY02A",
                "Perfect for my morning routine. Great for travel.",
            ),
        ],
    )

    cfg = AmazonReviewsProviderConfig(
        enabled=True,
        data_dir=tmp_path,
        categories=("All_Beauty",),
        max_items_per_run=100,
        min_review_chars=40,
    )
    provider = AmazonReviewsProvider(cfg)
    signals = provider.retrieve_review_signals()
    assert signals, "fixture should produce signals"

    # Every signal must have brand + title joined from the metadata.
    populated_brand = [s for s in signals if s.brand]
    populated_title = [s for s in signals if s.product_title]
    assert len(populated_brand) == len(signals), (
        f"brand join missed: {len(populated_brand)}/{len(signals)}"
    )
    assert len(populated_title) == len(signals), (
        f"title join missed: {len(populated_title)}/{len(signals)}"
    )
    # And the actual values come from the right metadata row.
    by_asin = {s.parent_asin: s for s in signals}
    assert by_asin["B0BEAUTY01"].brand == "Acme Skincare"
    assert by_asin["B0BEAUTY01"].product_title == (
        "Acme Lavender Hand Cream 50ml"
    )
    assert by_asin["B0BEAUTY02"].brand == "Nova Botanicals"
    assert by_asin["B0BEAUTY02"].product_title == (
        "Nova Botanicals Vitamin C Serum"
    )


def test_provider_joins_brand_and_title_from_suffix_naming_too(
    tmp_path: Path,
) -> None:
    """Same end-to-end check but with the legacy suffix-form
    metadata filename. Proves the patch didn't accidentally break
    the suffix path."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_jsonl(
        raw / "All_Beauty_meta.jsonl",   # suffix form
        [_meta_row(
            "B0LEGACY01",
            "Vintage Hair Oil Sandalwood",
            "Vintage Apothecary",
        )],
    )
    _write_jsonl(
        raw / "All_Beauty.jsonl",
        [_review_row(
            "B0LEGACY01", "B0LEGACY01A",
            "I love this oil. Used daily for months and the scent "
            "is amazing.",
        )],
    )
    cfg = AmazonReviewsProviderConfig(
        enabled=True, data_dir=tmp_path,
        categories=("All_Beauty",), max_items_per_run=10,
    )
    provider = AmazonReviewsProvider(cfg)
    signals = provider.retrieve_review_signals()
    assert signals
    s = signals[0]
    assert s.brand == "Vintage Apothecary"
    assert s.product_title == "Vintage Hair Oil Sandalwood"


# ---------------------------------------------------------------------------
# 6. Drift — patch did not add HTTP imports or live-flow integration
# ---------------------------------------------------------------------------


def test_metadata_glob_patch_did_not_add_http_imports() -> None:
    """Same drift gate as the 11A/11B suites — provider package
    must remain HTTP-free after this patch."""
    pkg = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "sources" / "amazon_reviews_provider"
    )
    forbidden = ("requests", "httpx", "aiohttp", "selenium",
                 "playwright", "scrapy", "bs4", "beautifulsoup4")
    for path in pkg.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            pattern = re.compile(
                rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
                re.MULTILINE,
            )
            assert pattern.search(text) is None, (
                f"{path} imports forbidden module {token!r}"
            )


def test_metadata_glob_patch_did_not_wire_provider_into_live_runtime() -> None:
    """The provider/ingestion must remain offline-only. Production
    routes/pipelines/orchestration must not import the package."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    live_dirs = [
        api_root / "api",
        api_root / "pipeline",
        api_root / "orchestration",
    ]
    forbidden_imports = (
        "amazon_reviews_provider",
        "AmazonReviewsProvider",
    )
    for d in live_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden_imports:
                assert token not in text, (
                    f"{path} now imports {token!r} — live wiring slipped "
                    f"in during the 11B.3 glob patch"
                )


def test_feature_flag_still_off_by_default() -> None:
    """Standing invariant — amazon_reviews_enabled defaults False."""
    from assembly.config import Settings
    s = Settings()
    assert s.amazon_reviews_enabled is False


# ---------------------------------------------------------------------------
# 7. Phase 11A regression — joined fixtures still produce branded signals
# ---------------------------------------------------------------------------


def test_phase_11a_synthetic_fixtures_still_populate_brand() -> None:
    """End-to-end regression: the existing Phase 11A synthetic
    fixtures (suffix form) must continue to populate `brand` for at
    least one signal per category after the glob patch."""
    fixture_dir = (
        Path(__file__).resolve().parent
        / "fixtures" / "amazon_reviews_provider"
    )
    cfg = AmazonReviewsProviderConfig(
        enabled=True, data_dir=fixture_dir,
        categories=("Electronics", "All_Beauty", "Home_and_Kitchen"),
        max_items_per_run=1000, min_review_chars=40,
    )
    provider = AmazonReviewsProvider(cfg)
    signals = provider.retrieve_review_signals()
    assert signals
    branded = [s for s in signals if s.brand is not None]
    # The synthetic fixtures have a meta row for every review row,
    # so every signal should be branded.
    assert len(branded) == len(signals), (
        f"Phase 11A brand join regressed: "
        f"{len(branded)}/{len(signals)} signals have a brand"
    )
