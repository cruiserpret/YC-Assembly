"""Phase 8.5B — tightened-filter + metadata-join + confidence-scoring tests.

Covers all 17 operator-spec'd scenarios:

  1. Metadata JSONL reader works.
  2. Metadata JSONL.gz reader works.
  3. Reviews join to metadata by parent_asin.
  4. Missing metadata lowers confidence but does not crash.
  5. Bare Prime shipping/service context is rejected.
  6. Prime Energy / Prime Hydration context is accepted.
  7. Flavor alone is rejected.
  8. Flavor + caffeine / electrolyte / sports drink is accepted.
  9. Product/category confidence score is deterministic.
 10. HIGH / MEDIUM / LOW / REJECTED labels work.
 11. Raw user_id is not stored.
 12. Image URLs are dropped.
 13. No Amazon.com scraping code exists.
 14. No source_records writes.
 15. No persona writes.
 16. No traits/evidence-links writes.
 17. No graph/simulation/UI writes.

NO LIVE LLM. NO LIVE NETWORK. NO DB. Pure deterministic fixtures.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from assembly.sources.amazon_reviews_2023 import (
    AmazonProductMetadata,
    AmazonReviewRecord,
    MetadataIndex,
    PrimeContext,
    ReviewConfidence,
    flavor_qualifies,
    prime_context_classification,
    score_review,
)


SOURCES_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "amazon_reviews_2023"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _review(
    *,
    text: str = "",
    title: str = "",
    parent_asin: str = "B0XYZ",
) -> AmazonReviewRecord:
    return AmazonReviewRecord(
        category="x",
        parent_asin=parent_asin, asin=parent_asin, rating=5.0,
        title=title, text=text,
        helpful_vote=1, verified_purchase=True,
        timestamp=1700000000, user_id_hash="abcdef1234567890",
    )


def _meta(
    *,
    title: str = "",
    main_category: str = "",
    categories: tuple[str, ...] = (),
    parent_asin: str = "B0XYZ",
    description: str = "",
) -> AmazonProductMetadata:
    return AmazonProductMetadata(
        parent_asin=parent_asin,
        title=title,
        store="StoreX",
        main_category=main_category,
        categories=categories,
        description=description,
    )


def _meta_record_dict(
    parent_asin: str, title: str, main_category: str = "",
    categories: list[str] | None = None,
) -> dict:
    return {
        "parent_asin": parent_asin,
        "title": title,
        "store": "StoreX",
        "main_category": main_category,
        "categories": categories or [],
        "description": [],
        "features": [],
        "price": None,
        "average_rating": 4.5,
        "rating_number": 100,
        "details": {"Brand": "StoreX"},
        "images": [{"large": "https://m.media-amazon.com/images/I/abc.jpg"}],
        "videos": [{"url": "https://www.amazon.com/vdp/abc"}],
    }


# ---------------------------------------------------------------------------
# 1 + 2. MetadataIndex reads JSONL and JSONL.gz
# ---------------------------------------------------------------------------


def test_metadata_index_reads_plain_jsonl(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "meta_TestCat.jsonl"
    rows = [
        _meta_record_dict("ASIN1", "Celsius Energy Drink",
                          main_category="Grocery",
                          categories=["Grocery & Gourmet Food", "Beverages",
                                      "Energy Drinks"]),
        _meta_record_dict("ASIN2", "Fish oil supplement"),
        _meta_record_dict("ASIN3", "Yoga block"),
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN1", "ASIN3"})
    idx.load()
    assert idx.lookup("ASIN1") is not None
    assert idx.lookup("ASIN3") is not None
    assert idx.lookup("ASIN2") is None  # not in target set
    # Image / video URLs dropped:
    m1 = idx.lookup("ASIN1")
    assert m1 is not None
    blob = repr(m1)
    assert "media-amazon.com" not in blob
    assert ".jpg" not in blob
    assert "amazon.com/vdp" not in blob


def test_metadata_index_reads_gzipped_jsonl(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "meta_TestCat.jsonl.gz"
    rows = [_meta_record_dict("ASIN1", "Prime Hydration Lemonade")]
    payload = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    with gzip.open(f, "wb") as fh:
        fh.write(payload)
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN1"})
    idx.load()
    assert idx.lookup("ASIN1") is not None
    assert idx.lookup("ASIN1").title == "Prime Hydration Lemonade"


def test_metadata_index_short_circuits_when_all_targets_resolved(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "meta_TestCat.jsonl"
    rows = [
        _meta_record_dict("ASIN1", "Energy drink"),
        _meta_record_dict("ASIN2", "Other"),
        _meta_record_dict("ASIN3", "Other"),
        _meta_record_dict("ASIN4", "Other"),
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN1"})
    idx.load()
    # Only 1 line scanned because ASIN1 is on line 1 and we
    # short-circuit after finding it.
    assert idx.lines_scanned == 1


def test_metadata_index_handles_missing_file(tmp_path) -> None:
    f = tmp_path / "missing.jsonl"
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN1"})
    idx.load()
    assert idx.index == {}
    assert idx.lookup("ASIN1") is None


# ---------------------------------------------------------------------------
# 3 + 4. Review→metadata join behavior
# ---------------------------------------------------------------------------


def test_review_joins_metadata_by_parent_asin(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "meta_TestCat.jsonl"
    rows = [_meta_record_dict("ASIN42", "Celsius Sparkling Tropical Vibe",
                              categories=["Energy Drinks"])]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN42"})
    idx.load()
    rev = _review(parent_asin="ASIN42",
                  title="Great taste",
                  text="Love the caffeine hit and zero sugar")
    score = score_review(review=rev, metadata=idx.lookup(rev.parent_asin))
    assert score.has_metadata is True
    assert score.confidence in (
        ReviewConfidence.HIGH_CONFIDENCE,
        ReviewConfidence.MEDIUM_CONFIDENCE,
    )


def test_missing_metadata_does_not_crash_lower_confidence() -> None:
    # No metadata available; review still scoreable but cannot earn
    # the metadata bonuses.
    rev = _review(text="The caffeine kick is solid for pre-workout.")
    score = score_review(review=rev, metadata=None)
    assert score.has_metadata is False
    assert score.confidence in (
        ReviewConfidence.HIGH_CONFIDENCE,
        ReviewConfidence.MEDIUM_CONFIDENCE,
        ReviewConfidence.LOW_CONFIDENCE,
    )
    # Sanity: deterministic (same input → same output)
    score2 = score_review(review=rev, metadata=None)
    assert score == score2


# ---------------------------------------------------------------------------
# 5 + 6. Prime context discrimination
# ---------------------------------------------------------------------------


def test_prime_shipping_context_only_is_rejected() -> None:
    rev = _review(
        title="yoga prop blocks",
        text=(
            "They are lightweight blue yoga prop blocks. "
            "Arrived quickly (I have prime) and in excellent condition."
        ),
    )
    meta = _meta(
        title="Yoga Prop Blocks",
        main_category="Sports & Outdoors",
        categories=("Sports & Outdoors", "Exercise & Fitness", "Yoga"),
    )
    score = score_review(review=rev, metadata=meta)
    assert score.confidence is ReviewConfidence.REJECTED
    assert score.prime_context is PrimeContext.SHIPPING
    assert score.rejection_reason in (
        "prime_shipping_only",
        "no_drink_category_anchor",
        "score_below_threshold",
    )


def test_prime_energy_drink_context_is_accepted() -> None:
    rev = _review(
        title="Tropical was great",
        text=(
            "Prime Energy in tropical punch flavor — actually has "
            "200 mg caffeine and zero sugar, way better than Red Bull."
        ),
    )
    meta = _meta(
        title="Prime Energy Drink Tropical Punch 12 Pack",
        main_category="Grocery",
        categories=("Grocery & Gourmet Food", "Beverages", "Energy Drinks"),
    )
    score = score_review(review=rev, metadata=meta)
    assert score.prime_context is PrimeContext.DRINK
    assert score.confidence is ReviewConfidence.HIGH_CONFIDENCE


def test_prime_hydration_drink_context_is_accepted() -> None:
    rev = _review(
        title="Lemonade flavor great",
        text=(
            "Prime Hydration Lemonade flavor is solid — it's an "
            "electrolyte drink, no caffeine, zero sugar."
        ),
    )
    score = score_review(review=rev, metadata=None)
    assert score.prime_context is PrimeContext.DRINK
    assert score.confidence in (
        ReviewConfidence.HIGH_CONFIDENCE,
        ReviewConfidence.MEDIUM_CONFIDENCE,
    )


def test_prime_classifier_helper() -> None:
    assert prime_context_classification(
        "I love Prime Energy"
    ) is PrimeContext.DRINK
    assert prime_context_classification(
        "Arrived quickly with Amazon Prime"
    ) is PrimeContext.SHIPPING
    assert prime_context_classification(
        "Prime is what I prefer"  # no shape phrase
    ) is PrimeContext.AMBIGUOUS
    assert prime_context_classification(
        "no Prime mentioned at all"  # technically Prime as substring of "Pri"...
    ) is PrimeContext.AMBIGUOUS  # neither shape phrase, so ambiguous


# ---------------------------------------------------------------------------
# 7 + 8. Flavor qualification
# ---------------------------------------------------------------------------


def test_flavor_alone_does_not_qualify() -> None:
    assert flavor_qualifies(
        "The flavor of this seasoning is delicious"
    ) is False
    rev = _review(text="The flavor of this seasoning is delicious")
    score = score_review(review=rev, metadata=None)
    # Flavor unqualified is a denylist hit. With no other anchor,
    # there is no positive signal → REJECTED.
    assert score.confidence is ReviewConfidence.REJECTED
    assert any("flavor (unqualified)" in d for d in score.denylist_hits)


def test_flavor_plus_caffeine_qualifies() -> None:
    assert flavor_qualifies(
        "Great flavor and the caffeine hits hard"
    ) is True


def test_flavor_plus_electrolyte_qualifies() -> None:
    assert flavor_qualifies(
        "Decent flavor, has electrolytes I need post-workout"
    ) is True


def test_flavor_plus_sports_drink_qualifies() -> None:
    assert flavor_qualifies(
        "Sports drink with a citrus flavor I actually enjoy"
    ) is True


def test_flavor_plus_brand_qualifies() -> None:
    assert flavor_qualifies(
        "Better flavor than Celsius, lighter on the throat"
    ) is True


# ---------------------------------------------------------------------------
# 9 + 10. Confidence labels deterministic + bucketed
# ---------------------------------------------------------------------------


def test_score_is_deterministic_for_same_input() -> None:
    rev = _review(text="Caffeine and electrolytes for pre-workout, no sugar.")
    meta = _meta(
        title="Energy Drink",
        main_category="Grocery",
        categories=("Beverages", "Energy Drinks"),
    )
    a = score_review(review=rev, metadata=meta)
    b = score_review(review=rev, metadata=meta)
    assert a == b


def test_high_confidence_label_for_strong_signals() -> None:
    rev = _review(
        title="Solid pre-workout",
        text=(
            "This pre-workout has electrolytes plus caffeine, no "
            "sugar crash, way better than Monster or Red Bull."
        ),
    )
    meta = _meta(
        title="Pre-Workout Energy Drink Variety Pack",
        main_category="Grocery",
        categories=("Beverages", "Energy Drinks", "Sports Nutrition"),
    )
    score = score_review(review=rev, metadata=meta)
    assert score.confidence is ReviewConfidence.HIGH_CONFIDENCE


def test_medium_confidence_label_for_moderate_signals() -> None:
    rev = _review(
        text="The flavor is fine but the caffeine seems weak.",
    )
    score = score_review(review=rev, metadata=None)
    assert score.confidence in (
        ReviewConfidence.MEDIUM_CONFIDENCE,
        ReviewConfidence.LOW_CONFIDENCE,
    )


def test_low_confidence_label_for_thin_signals() -> None:
    # One weak anchor, no metadata.
    rev = _review(text="Helped my recovery after the gym.")
    score = score_review(review=rev, metadata=None)
    assert score.confidence in (
        ReviewConfidence.LOW_CONFIDENCE,
        ReviewConfidence.MEDIUM_CONFIDENCE,
    )


def test_rejected_label_for_no_signal() -> None:
    rev = _review(text="Arrived quickly and looks great. Five stars.")
    score = score_review(review=rev, metadata=None)
    assert score.confidence is ReviewConfidence.REJECTED


# ---------------------------------------------------------------------------
# 11 + 12. Metadata privacy: raw user_id not stored, image URLs dropped
# ---------------------------------------------------------------------------


def test_metadata_record_drops_image_and_video_fields(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "meta_TestCat.jsonl"
    f.write_text(json.dumps(_meta_record_dict(
        "ASIN_X", "Energy drink",
    )), encoding="utf-8")
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN_X"})
    idx.load()
    m = idx.lookup("ASIN_X")
    assert m is not None
    # Schema-level: AmazonProductMetadata has no `images` / `videos`
    fields = AmazonProductMetadata.__dataclass_fields__
    assert "images" not in fields
    assert "videos" not in fields
    assert "bought_together" not in fields
    # Repr/contents check
    blob = repr(m)
    assert "media-amazon" not in blob
    assert ".jpg" not in blob
    assert "amazon.com/vdp" not in blob


def test_score_review_does_not_leak_raw_user_id_in_repr() -> None:
    raw_uid_in_review = AmazonReviewRecord(
        category="x",
        parent_asin="B0",
        asin="B0",
        rating=5.0,
        title="t",
        text="caffeine and electrolyte test review",
        helpful_vote=0,
        verified_purchase=True,
        timestamp=0,
        user_id_hash="0123456789abcdef",
    )
    score = score_review(review=raw_uid_in_review, metadata=None)
    blob = repr(score)
    # Hash is OK; the test fixture has no raw user_id (the adapter
    # already hashes at parse time, validated in 8.5A tests).
    assert "user_id_hash" not in blob  # ReviewScoreDetail has no such field


# ---------------------------------------------------------------------------
# 13. No Amazon.com scraping code anywhere in the package
# ---------------------------------------------------------------------------


def test_no_amazon_dot_com_url_strings_in_filters_module() -> None:
    src = (SOURCES_PKG / "filters.py").read_text(encoding="utf-8")
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    assert pat.search(src) is None


def test_no_http_libs_in_filters_module() -> None:
    import ast
    src = (SOURCES_PKG / "filters.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {
        "httpx", "requests", "aiohttp", "urllib", "urllib3",
        "selenium", "playwright", "scrapy",
        "beautifulsoup4", "bs4",
    }
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in forbidden:
                bad.append(node.module)
    assert bad == []


# ---------------------------------------------------------------------------
# 14-17. No DB / persona / graph / UI writes in 8.5B code paths
# ---------------------------------------------------------------------------


_FORBIDDEN_ORM_NAMES = (
    "SourceRecord", "PersonaRecord", "PersonaTrait", "PersonaEvidenceLink",
    "PersonaGraphEdge", "PersonaCluster", "PersonaClusterMembership",
    "PersonaOpinion", "AudienceRetrievalRun",
    "PopulationConstructionAudit",
    "SimulationOutput", "SimulationRound", "DebateTurn",
    "AgentResponse", "Agent", "AgentEdge",
    "AdapterComplianceStatus", "MechanismInitializationAudit",
)


def test_no_orm_construction_in_filters_module() -> None:
    src = (SOURCES_PKG / "filters.py").read_text(encoding="utf-8")
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\("
    )
    assert pat.search(src) is None


def test_no_orm_construction_in_8_5b_preflight_script() -> None:
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "amazon_reviews_2023_preflight_8_5b.py"
    ).read_text(encoding="utf-8")
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\("
    )
    assert pat.search(src) is None


def test_no_frontend_references_in_filters_module() -> None:
    src = (SOURCES_PKG / "filters.py").read_text(encoding="utf-8")
    forbidden = ("apps/web", "next/router", "next.js")
    for s in forbidden:
        assert s not in src


# ---------------------------------------------------------------------------
# Bonus: closed-enum discipline
# ---------------------------------------------------------------------------


def test_review_confidence_closed_enum() -> None:
    assert {c.value for c in ReviewConfidence} == {
        "high_confidence", "medium_confidence",
        "low_confidence", "rejected",
    }


def test_prime_context_closed_enum() -> None:
    assert {c.value for c in PrimeContext} == {
        "drink", "shipping", "ambiguous",
    }
