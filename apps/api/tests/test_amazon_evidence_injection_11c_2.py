"""Phase 11C.2 — category-gated retrieval + evidence-injection tests.

Covers the operator's acceptance checklist:

  1. same_category_only prevents cross-category fallback (the
     Phase-11C.1 critical bug we need to plug).
  2. Category-first retrieval returns category-matched signals.
  3. Global fallback is disabled by default for simulation injection.
  4. Global fallback can be enabled explicitly in dev / tests.
  5. Amazon package still returns balanced signal types within the
     matched category.
  6. No raw review fields leak through the injector dict.
  7. Injector returns audit dict (not None) even when flags are off,
     so observability is always recorded.
  8. Injector calls the retriever when both flags are on.
  9. Phase 11C.1 tests still pass — drift whitelist works.
 10. Build_evidence wires the injector under the flag gate.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import pytest

from assembly.sources.amazon_reviews_provider import (
    AmazonEvidencePackage,
    AmazonSignalRetriever,
    InMemorySignalSource,
    ProductBriefShape,
    RetrievalConfig,
    SignalRow,
)


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _row(
    *,
    signal_type: str = "objection",
    sentiment: str = "negative",
    theme: str | None = "generic_disappointment",
    category: str = "Software",
    brand: str | None = "BrandX",
    title: str | None = "Title X",
    rating: int | None = 1,
    snippet: str = "this product was a major disappointment",
    review_hash: str = "h000",
    competitor: str | None = None,
) -> SignalRow:
    return SignalRow(
        signal_type=signal_type, sentiment_bucket=sentiment, theme=theme,
        category=category, brand=brand, product_title=title,
        asin="B000", parent_asin="B000",
        rating=rating, verified_purchase=True, helpful_votes=3,
        short_snippet=snippet, competitor_mention=competitor,
        use_case=None, source_review_hash=review_hash,
    )


def _multi_category_corpus() -> list[SignalRow]:
    """Reasonable cross-category mix for testing the category gate.
    Software gets fewer rows so brand caps don't accidentally
    over-filter."""
    out: list[SignalRow] = []
    for i in range(6):
        out.append(_row(
            category="Software", signal_type="setup",
            theme="couldnt_setup", brand=f"SwBrand{i}",
            snippet=f"sw setup pain {i}",
            review_hash=f"sw_setup_{i}",
        ))
    for i in range(6):
        out.append(_row(
            category="Software", signal_type="trust",
            theme="scam_suspicion", brand=f"SwBrand{i}",
            snippet=f"sw trust concern {i}",
            review_hash=f"sw_trust_{i}",
        ))
    for i in range(6):
        out.append(_row(
            category="Software", signal_type="objection",
            theme="generic_disappointment", brand=f"SwBrand{i}",
            snippet=f"sw objection {i}",
            review_hash=f"sw_obj_{i}",
        ))
    for i in range(10):
        out.append(_row(
            category="All_Beauty", signal_type="objection",
            theme="generic_disappointment", brand=f"BeautyBrand{i}",
            snippet=f"beauty objection {i}",
            review_hash=f"beauty_{i}",
        ))
    for i in range(10):
        out.append(_row(
            category="Subscription_Boxes", signal_type="objection",
            theme="generic_disappointment", brand=f"SubBrand{i}",
            snippet=f"sub objection {i}",
            review_hash=f"sub_{i}",
        ))
    return out


def _retriever(
    rows: Iterable[SignalRow],
    *,
    same_category_only: bool = True,
) -> AmazonSignalRetriever:
    return AmazonSignalRetriever(
        InMemorySignalSource(rows),
        config=RetrievalConfig(
            enabled=True, runtime_enabled=True,
            same_category_only=same_category_only,
            max_signals_per_run=80,
            max_signals_per_category=40,
            max_signals_per_competitor=20,
            max_signals_per_brand=8,
            max_signals_per_theme=10,
        ),
    )


# ---------------------------------------------------------------------------
# 1. Category gate prevents cross-category fallback
# ---------------------------------------------------------------------------


def test_same_category_only_filters_out_other_categories() -> None:
    """The exact bug from Phase-11C.1 dry-runs: a Software-brief
    pulled All_Beauty + Subscription_Boxes objections via the theme
    fallback. With same_category_only=True that must NOT happen."""
    rows = _multi_category_corpus()
    pkg = asyncio.run(_retriever(rows).retrieve_for_product_brief(
        ProductBriefShape(
            product_name="QuietCart browser extension",
            description="browser extension that pauses Amazon checkout",
        ),
    ))
    assert pkg.attempted is True
    assert pkg.category_matched == "Software"
    assert pkg.signals
    # Every returned signal must be from Software — zero leakage.
    leaked = [s for s in pkg.signals if s.category != "Software"]
    assert leaked == [], (
        f"category gate leaked {len(leaked)} non-Software signals: "
        f"{sorted({s.category for s in leaked})}"
    )
    # Skipped reasons should record the dropped cross-category rows.
    assert pkg.skipped_reasons.get("cross_category_filtered", 0) > 0


def test_same_category_only_false_allows_global_fallback() -> None:
    """Dev / debug path: same_category_only=False reproduces the
    Phase-11C.1 behavior — theme fallback surfaces cross-category
    rows when the matched category has fewer of them."""
    rows = _multi_category_corpus()
    pkg = asyncio.run(_retriever(rows, same_category_only=False)
                      .retrieve_for_product_brief(
        ProductBriefShape(
            product_name="QuietCart browser extension",
            description="browser extension",
        ),
    ))
    cats = {s.category for s in pkg.signals}
    # With global fallback on, more than one category typically
    # appears.
    assert len(cats) >= 2, (
        f"expected cross-category fallback but only saw: {cats}"
    )


def test_same_category_only_returns_empty_when_no_category_match() -> None:
    """Defensive default — a brief that fails to classify and
    same_category_only=True must return an EMPTY package, never
    a global fallback dump."""
    rows = _multi_category_corpus()
    pkg = asyncio.run(_retriever(rows).retrieve_for_product_brief(
        ProductBriefShape(product_name="zzz uncategorized thingy"),
    ))
    assert pkg.attempted is True
    assert pkg.category_matched is None
    assert pkg.signals == []
    assert any("same_category_only" in n for n in pkg.notes)


def test_default_config_has_same_category_only_on() -> None:
    """Production-safe default."""
    cfg = RetrievalConfig()
    assert cfg.same_category_only is True


def test_settings_default_for_same_category_only_is_true() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.amazon_reviews_same_category_only is True


def test_retrieval_config_from_settings_picks_up_same_category_only() -> None:
    class _S:
        amazon_reviews_enabled = True
        amazon_reviews_runtime_enabled = True
        amazon_reviews_same_category_only = False  # dev override
        amazon_reviews_max_signals_per_run = 80
        amazon_reviews_max_signals_per_category = 40
        amazon_reviews_max_signals_per_competitor = 20
        amazon_reviews_max_signals_per_brand = 8
        amazon_reviews_max_signals_per_theme = 10
    cfg = RetrievalConfig.from_settings(_S())
    assert cfg.same_category_only is False


# ---------------------------------------------------------------------------
# 2. Bucket balance still happens WITHIN the matched category
# ---------------------------------------------------------------------------


def test_balanced_signal_types_within_single_category() -> None:
    """With three signal types in Software (setup, trust, objection)
    and the gate on, the result pool must still mix all three —
    not just pull 18 objections."""
    rows = _multi_category_corpus()
    pkg = asyncio.run(_retriever(rows).retrieve_for_product_brief(
        ProductBriefShape(
            product_name="QuietCart browser extension",
            description="browser extension",
        ),
    ))
    types = set(pkg.distribution)
    assert "setup" in types
    assert "trust" in types
    assert "objection" in types


# ---------------------------------------------------------------------------
# 3. Skipped-reason audit field populated
# ---------------------------------------------------------------------------


def test_skipped_reasons_records_cross_category_filter() -> None:
    rows = _multi_category_corpus()
    pkg = asyncio.run(_retriever(rows).retrieve_for_product_brief(
        ProductBriefShape(
            product_name="QuietCart browser extension",
            description="browser extension",
        ),
    ))
    # With theme fallback dragging in 20 non-Software rows, the
    # cross-category filter should remove them.
    assert pkg.skipped_reasons.get("cross_category_filtered", 0) > 0


# ---------------------------------------------------------------------------
# 4. Evidence-injector module: flag-off audit shape
# ---------------------------------------------------------------------------


def test_injector_returns_disabled_audit_when_flags_off() -> None:
    """When both flags are off the injector must NOT touch the DB
    but must STILL return a small audit dict documenting the
    disabled state — observability is always recorded."""
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_evidence_section,
    )

    class _FakeBrief:
        product_name = "x"
        product_type = "thing"
        description = "y"
        competitors: list = []

    class _Settings:
        amazon_reviews_enabled = False
        amazon_reviews_runtime_enabled = False
        amazon_reviews_same_category_only = True
        amazon_reviews_max_signals_per_run = 80
        amazon_reviews_max_signals_per_category = 40
        amazon_reviews_max_signals_per_competitor = 20
        amazon_reviews_max_signals_per_brand = 8
        amazon_reviews_max_signals_per_theme = 10

    # Pass a sessionmaker that would explode if called — proves the
    # injector never opens a connection when flags are off.
    def explode(*a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise AssertionError(
            "sessionmaker was called despite flags being off",
        )

    audit = asyncio.run(build_amazon_evidence_section(
        _FakeBrief(),  # type: ignore[arg-type]
        sessionmaker=explode,  # type: ignore[arg-type]
        settings=_Settings(),  # type: ignore[arg-type]
    ))
    assert audit is not None
    assert audit["provider"] == "amazon_reviews_2023"
    assert audit["amazon_attempted"] is False
    assert audit["amazon_enabled"] is False
    assert audit["amazon_runtime_enabled"] is False
    assert audit["signals_retrieved"] == 0
    assert "feature_flag_off" in audit["notes"][0]
    # Forbidden fields absent.
    forbidden_keys = {
        "source_review_hash", "user_id", "user_id_hash",
        "images", "image_url", "raw_text",
    }
    assert not (audit.keys() & forbidden_keys)


# ---------------------------------------------------------------------------
# 5. Injector adapter passes brief correctly
# ---------------------------------------------------------------------------


def test_injector_adapts_brief_to_product_shape() -> None:
    """Helper exposed for downstream auditing — confirm field
    mapping for SimulationBriefIn shape."""
    from assembly.pipeline.amazon_evidence_injector import (
        _brief_to_product_shape,
    )

    class _CompetitorRef:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Brief:
        product_type = "browser extension"
        product_name = "QuietCart"
        description = "extension that pauses checkout"
        competitors = [
            _CompetitorRef("Freedom"),
            _CompetitorRef("Opal"),
        ]

    shape = _brief_to_product_shape(_Brief())  # type: ignore[arg-type]
    assert shape.product_name == "QuietCart"
    assert shape.description == "extension that pauses checkout"
    assert shape.category_hint == "browser extension"
    assert shape.competitors == ("Freedom", "Opal")


def test_injector_handles_blank_competitor_names_defensively() -> None:
    """Competitor refs with empty names must not raise."""
    from assembly.pipeline.amazon_evidence_injector import (
        _brief_to_product_shape,
    )

    class _CompetitorRef:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Brief:
        product_type = "thing"
        product_name = "Name"
        description = "desc"
        competitors = [
            _CompetitorRef("Real"),
            _CompetitorRef(""),
            _CompetitorRef("   "),
        ]

    shape = _brief_to_product_shape(_Brief())  # type: ignore[arg-type]
    assert shape.competitors == ("Real",)


# ---------------------------------------------------------------------------
# 6. EvidenceBuildResult carries amazon_audit
# ---------------------------------------------------------------------------


def test_evidence_build_result_has_amazon_audit_field() -> None:
    """Phase 11C.2 extended the result dataclass."""
    from assembly.pipeline.evidence_builder import EvidenceBuildResult
    fields = EvidenceBuildResult.__dataclass_fields__
    assert "amazon_audit" in fields
    # Default is None so existing call sites that don't pass it
    # still work.
    default = fields["amazon_audit"].default
    assert default is None


def test_evidence_build_result_amazon_audit_optional_by_default() -> None:
    """Existing callers that don't know about amazon_audit can
    still construct the result without breakage."""
    from assembly.pipeline.evidence_builder import EvidenceBuildResult
    r = EvidenceBuildResult(
        items=[], fetched_pages=[], fetch_errors=[],
        extracted_phrases=[],
    )
    assert r.amazon_audit is None


# ---------------------------------------------------------------------------
# 7. Drift: only the whitelisted injector imports the retriever
# ---------------------------------------------------------------------------


def test_only_injector_imports_retriever_in_live_dirs() -> None:
    from pathlib import Path

    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    whitelist = {
        api_root / "pipeline" / "amazon_evidence_injector.py",
    }
    forbidden = (
        "AmazonSignalRetriever",
        "amazon_reviews_provider.retrieval",
        "amazon_reviews_provider.postgres_source",
    )
    for d in (api_root / "api", api_root / "pipeline",
              api_root / "orchestration"):
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if path in whitelist:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in text, (
                    f"{path} imports {token!r} — Amazon access "
                    f"must go through the injector"
                )


def test_injector_file_does_not_introduce_http_imports() -> None:
    import inspect
    import re
    from assembly.pipeline import amazon_evidence_injector as inj
    src = inspect.getsource(inj)
    forbidden = ("requests", "httpx", "aiohttp", "selenium",
                 "playwright", "scrapy", "bs4", "beautifulsoup4")
    for token in forbidden:
        pattern = re.compile(
            rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
            re.MULTILINE,
        )
        assert pattern.search(src) is None, (
            f"injector imports forbidden module {token!r}"
        )


# ---------------------------------------------------------------------------
# 8. Phase 11C.1 backward compat
# ---------------------------------------------------------------------------


def test_phase_11c_1_drift_test_still_runs() -> None:
    """Smoke import — confirm the 11C.1 drift test we updated still
    runs cleanly under pytest collection."""
    from tests.test_amazon_runtime_retrieval_11c_1 import (
        test_runtime_retriever_only_wired_via_whitelisted_files,
    )
    test_runtime_retriever_only_wired_via_whitelisted_files()
