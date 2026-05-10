"""Phase 8.5A — source-expansion setup tests.

Operator scenarios covered (17 total):

  1. Env config exposes BRAVE_SEARCH_API_KEY, YOUTUBE_DATA_API_KEY,
     AMAZON_REVIEWS_2023_DIR, AMAZON_REVIEWS_2023_MODE,
     AMAZON_REVIEWS_2023_CATEGORIES.
  2. API keys are never written into audit-JSON-shaped output (the
     literal value cannot leak through `repr()` or `redact_url_for_audit`
     or any helper).
  3. Brave preflight defaults to no live call.
  4. YouTube preflight defaults to no live call.
  5. Amazon preflight handles missing files safely.
  6. Amazon supports specific categories AND `ALL`.
  7. Amazon reader supports JSONL and JSONL.gz.
  8. Amazon adapter does not store raw user_id.
  9. Amazon adapter drops image URLs.
 10. No Amazon.com scraping code exists in the Amazon adapter
     package.
 11. No unofficial YouTube scraping libs imported in the YouTube
     adapter package.
 12. No `source_records` writes happen in 8.5A code paths
     (drift via grep over the adapter modules + preflight scripts).
 13. No `Persona*` row construction happens in 8.5A code paths.
 14. No persona-trait / evidence-link writes happen in 8.5A.
 15. No graph / simulation / UI writes happen in 8.5A.
 16-17. Full unit + integration suites pass (verified by harness
     regression — see end-of-phase log).

NO LIVE LLM. NO LIVE API CALLS. NO INGESTION. Pure import +
text-inspection + tiny synthetic-file tests.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from pathlib import Path

import pytest

from assembly.config import Settings
from assembly.sources.amazon_reviews_2023 import (
    AmazonReviewRecord,
    AmazonReviewsAdapterConfig,
    AmazonReviewsLocalReader,
    discover_category_files,
    looks_like_low_quality_review,
    matches_search_terms,
    parse_amazon_review_line,
    resolve_categories,
)
from assembly.sources.brave import (
    BraveAdapterConfig,
    BraveSearchClient,
    build_brave_query_set,
    is_brave_key_present,
    redact_url_for_audit,
)
from assembly.sources.youtube import (
    YouTubeAdapterConfig,
    YouTubeDataClient,
    is_youtube_key_present,
    looks_like_low_quality_comment,
    redact_comment_for_audit,
)


SOURCES_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources"
)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


# ---------------------------------------------------------------------------
# 1. Env config
# ---------------------------------------------------------------------------


def test_settings_exposes_8_5a_source_fields() -> None:
    s = Settings()
    # All five env-backed fields exist as attributes on the settings
    # object (typed). Their values are not asserted — only that the
    # plumbing is present.
    assert hasattr(s, "brave_search_api_key")
    assert hasattr(s, "youtube_data_api_key")
    assert hasattr(s, "amazon_reviews_2023_dir")
    assert hasattr(s, "amazon_reviews_2023_mode")
    assert hasattr(s, "amazon_reviews_2023_categories")
    # Mode enum is constrained.
    assert s.amazon_reviews_2023_mode in ("local", "off")


def test_settings_amazon_categories_helper_parses_specific_list() -> None:
    s = Settings(
        AMAZON_REVIEWS_2023_CATEGORIES=(
            "Grocery_and_Gourmet_Food, Health_and_Household,"
            "Sports_and_Outdoors"
        )
    )
    assert s.amazon_categories_list == [
        "Grocery_and_Gourmet_Food",
        "Health_and_Household",
        "Sports_and_Outdoors",
    ]


def test_settings_amazon_categories_helper_supports_ALL() -> None:
    s = Settings(AMAZON_REVIEWS_2023_CATEGORIES="ALL")
    assert s.amazon_categories_list == "ALL"
    s = Settings(AMAZON_REVIEWS_2023_CATEGORIES="all")
    assert s.amazon_categories_list == "ALL"


def test_settings_amazon_categories_empty_returns_empty_list() -> None:
    s = Settings(AMAZON_REVIEWS_2023_CATEGORIES=None)
    assert s.amazon_categories_list == []


# ---------------------------------------------------------------------------
# 2. API keys never leak through helpers / repr
# ---------------------------------------------------------------------------


def test_brave_redact_url_drops_query_string() -> None:
    redacted = redact_url_for_audit(
        "https://example.com/page?token=ABC123&utm=foo"
    )
    assert redacted == "https://example.com/page"
    assert "ABC123" not in redacted


def test_brave_client_repr_does_not_leak_key(monkeypatch) -> None:
    # Even when key is present, neither repr() nor str() may carry it.
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fake-key-do-not-leak-1234")
    c = BraveSearchClient()
    assert "fake-key" not in repr(c)
    assert "fake-key" not in str(c)
    assert "fake-key" not in repr(c.config)


def test_youtube_client_repr_does_not_leak_key(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "fake-yt-key-do-not-leak")
    c = YouTubeDataClient()
    assert "fake-yt-key" not in repr(c)
    assert "fake-yt-key" not in str(c)
    assert "fake-yt-key" not in repr(c.config)


def test_brave_client_refuses_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    c = BraveSearchClient()
    with pytest.raises(RuntimeError, match="BRAVE_SEARCH_API_KEY"):
        c.search(queries=["q"])


def test_youtube_client_refuses_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    c = YouTubeDataClient()
    with pytest.raises(RuntimeError, match="YOUTUBE_DATA_API_KEY"):
        c.search_videos(query="q")
    with pytest.raises(RuntimeError, match="YOUTUBE_DATA_API_KEY"):
        c.fetch_comments(video_id="abc")


# ---------------------------------------------------------------------------
# 3 + 4. Preflight scripts default to no live call
# ---------------------------------------------------------------------------


def test_brave_preflight_script_default_is_dry_run() -> None:
    src = (SCRIPTS_DIR / "brave_source_preflight_8_5a.py").read_text(
        encoding="utf-8"
    )
    # `--live` flag is opt-in (action="store_true"). Default args.live
    # is False; the script branches `if args.live:` to gate live
    # execution. Both invariants must be present.
    assert 'action="store_true"' in src
    assert "if args.live:" in src


def test_youtube_preflight_script_default_is_dry_run() -> None:
    src = (SCRIPTS_DIR / "youtube_comments_preflight_8_5a.py").read_text(
        encoding="utf-8"
    )
    assert 'action="store_true"' in src
    assert "if args.live:" in src


# ---------------------------------------------------------------------------
# 5. Amazon preflight handles missing files safely
# ---------------------------------------------------------------------------


def test_amazon_discover_handles_missing_dataset_dir(tmp_path) -> None:
    # Pointing at a nonexistent dir returns an empty mapping, not a
    # crash.
    nope = tmp_path / "does_not_exist"
    out = discover_category_files(
        dataset_dir=nope, categories=["Grocery_and_Gourmet_Food"],
    )
    assert out == {}


def test_amazon_discover_handles_empty_raw_dir(tmp_path) -> None:
    # raw/ exists but is empty — should report categories with empty
    # file lists, not crash.
    (tmp_path / "raw").mkdir()
    out = discover_category_files(
        dataset_dir=tmp_path,
        categories=["Grocery_and_Gourmet_Food", "Health_and_Household"],
    )
    assert out == {
        "Grocery_and_Gourmet_Food": [],
        "Health_and_Household": [],
    }


# ---------------------------------------------------------------------------
# 6. Amazon supports specific categories AND ALL
# ---------------------------------------------------------------------------


def test_amazon_resolve_categories_specific_list(tmp_path) -> None:
    out = resolve_categories(
        raw_setting="A,B,  C  ,  ", dataset_dir=tmp_path,
    )
    assert out == ["A", "B", "C"]


def test_amazon_resolve_categories_ALL_returns_sentinel(tmp_path) -> None:
    out = resolve_categories(raw_setting="ALL", dataset_dir=tmp_path)
    assert out == "ALL"


def test_amazon_resolve_categories_empty_returns_empty(tmp_path) -> None:
    assert resolve_categories(raw_setting=None, dataset_dir=tmp_path) == []
    assert resolve_categories(raw_setting="", dataset_dir=tmp_path) == []


def test_amazon_discover_ALL_finds_every_category(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "Grocery_and_Gourmet_Food.jsonl").write_text("")
    (raw / "Health_and_Household.jsonl.gz").write_bytes(b"")
    (raw / "Sports_and_Outdoors.jsonl").write_text("")
    out = discover_category_files(dataset_dir=tmp_path, categories="ALL")
    assert set(out.keys()) >= {
        "Grocery_and_Gourmet_Food",
        "Health_and_Household",
        "Sports_and_Outdoors",
    }


# ---------------------------------------------------------------------------
# 7. Amazon reader supports JSONL and JSONL.gz
# ---------------------------------------------------------------------------


def _amazon_record_dict(text: str) -> dict:
    return {
        "rating": 4,
        "title": "Pretty good caffeine",
        "text": text,
        "user_id": "RAW_USER_ID_PLAINTEXT_DO_NOT_LEAK",
        "parent_asin": "B0XYZ",
        "asin": "B0XYZ",
        "helpful_vote": 3,
        "verified_purchase": True,
        "timestamp": 1700000000,
        "images": ["https://images.amazon.com/x.jpg"],
    }


def test_amazon_reader_supports_plain_jsonl(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "Health_and_Household.jsonl"
    rows = [_amazon_record_dict(
        f"This Red Bull alternative gives me less of a sugar crash than "
        f"Monster, and the caffeine load feels right for pre-workout. "
        f"Sample {i}."
    ) for i in range(3)]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    reader = AmazonReviewsLocalReader(dataset_dir=tmp_path)
    out = list(reader.iter_category(
        category="Health_and_Household", files=[f], max_records=10,
    ))
    assert len(out) == 3


def test_amazon_reader_supports_jsonl_gz(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "Sports_and_Outdoors.jsonl.gz"
    rows = [_amazon_record_dict(
        f"Decent electrolyte option — caffeine free which I prefer over "
        f"Celsius. Flavor is hit or miss. Sample {i}."
    ) for i in range(2)]
    payload = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    with gzip.open(f, "wb") as fh:
        fh.write(payload)
    reader = AmazonReviewsLocalReader(dataset_dir=tmp_path)
    out = list(reader.iter_category(
        category="Sports_and_Outdoors", files=[f], max_records=10,
    ))
    assert len(out) == 2


# ---------------------------------------------------------------------------
# 8. Amazon adapter does not store raw user_id
# ---------------------------------------------------------------------------


def test_amazon_parse_hashes_user_id_does_not_store_raw() -> None:
    raw = json.dumps(_amazon_record_dict(
        "This is a real review of an energy drink with caffeine."
    ))
    rec = parse_amazon_review_line(raw_line=raw, category="Test")
    assert rec is not None
    # Hash stored
    assert rec.user_id_hash is not None
    assert len(rec.user_id_hash) == 16
    # Raw user_id never appears anywhere on the record
    rec_str = repr(rec)
    assert "RAW_USER_ID_PLAINTEXT_DO_NOT_LEAK" not in rec_str
    # And the hash itself isn't the raw value
    assert rec.user_id_hash != "RAW_USER_ID_PLAINTEXT_DO_NOT_LEAK"


# ---------------------------------------------------------------------------
# 9. Amazon adapter drops image URLs
# ---------------------------------------------------------------------------


def test_amazon_record_has_no_images_field() -> None:
    fields = AmazonReviewRecord.__dataclass_fields__
    assert "images" not in fields
    assert "image_url" not in fields
    assert "thumbnail" not in fields


def test_amazon_parse_drops_image_urls_from_record() -> None:
    raw = json.dumps(_amazon_record_dict(
        "An energy drink review with sufficient text length to pass "
        "the quality filter. Sugar load was a problem for me."
    ))
    rec = parse_amazon_review_line(raw_line=raw, category="Test")
    assert rec is not None
    rec_blob = repr(rec)
    assert "images.amazon.com" not in rec_blob
    assert ".jpg" not in rec_blob


# ---------------------------------------------------------------------------
# 10 + 11. No web-scraping libraries imported in source adapters
# ---------------------------------------------------------------------------


_FORBIDDEN_SCRAPING_LIBS = (
    "requests", "aiohttp", "urllib.request", "urllib3",
    "selenium", "playwright", "scrapy",
    "beautifulsoup4", "bs4",
    "yt_dlp", "youtube_dl", "pytube", "scrapetube",
    "youtube_comment_downloader",
)


def _imported_modules(py_text: str) -> set[str]:
    import ast
    imported: set[str] = set()
    try:
        tree = ast.parse(py_text)
    except SyntaxError:
        return imported
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_amazon_package_imports_no_http_or_scraping_libs() -> None:
    pkg = SOURCES_PKG / "amazon_reviews_2023"
    forbidden_for_amazon = (
        "httpx",
        ) + _FORBIDDEN_SCRAPING_LIBS  # Amazon adapter must be local-only
    offenders: list[str] = []
    for f in pkg.rglob("*.py"):
        for mod in _imported_modules(f.read_text(encoding="utf-8")):
            if any(
                mod == m or mod.startswith(m + ".")
                for m in forbidden_for_amazon
            ):
                offenders.append(f"{f.name}: {mod}")
    assert offenders == [], (
        f"amazon_reviews_2023 package must NOT import HTTP / scraping "
        f"libs (Amazon is LOCAL-ONLY): {offenders}"
    )


def test_youtube_package_imports_no_unofficial_scraping_libs() -> None:
    pkg = SOURCES_PKG / "youtube"
    offenders: list[str] = []
    for f in pkg.rglob("*.py"):
        for mod in _imported_modules(f.read_text(encoding="utf-8")):
            if any(
                mod == m or mod.startswith(m + ".")
                for m in _FORBIDDEN_SCRAPING_LIBS
            ):
                offenders.append(f"{f.name}: {mod}")
    assert offenders == [], (
        f"youtube package must NOT import unofficial-scraping libs: "
        f"{offenders}"
    )


def test_no_amazon_dot_com_string_in_amazon_package_code() -> None:
    pkg = SOURCES_PKG / "amazon_reviews_2023"
    # Allowed: docstrings + comments mentioning amazon.com as
    # forbidden. Disallowed: any code-level string that looks like a
    # URL pointing at amazon.com (would imply a fetch).
    pat = re.compile(
        r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE,
    )
    offenders: list[str] = []
    for f in pkg.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(f.name)
    assert offenders == [], (
        f"amazon_reviews_2023 package must NOT contain "
        f"`http(s)://...amazon.com` URL strings (no scraping): "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# 12-15. No persistence writes in 8.5A code paths
# ---------------------------------------------------------------------------


_FORBIDDEN_ORM_NAMES = (
    "SourceRecord", "PersonaRecord", "PersonaTrait",
    "PersonaEvidenceLink",
    "PersonaGraphEdge", "PersonaCluster", "PersonaClusterMembership",
    "PersonaOpinion", "AudienceRetrievalRun",
    "PopulationConstructionAudit",
    "SimulationOutput", "SimulationRound", "DebateTurn",
    "AgentResponse", "Agent", "AgentEdge",
    "AdapterComplianceStatus", "MechanismInitializationAudit",
)


def test_no_orm_construction_in_sources_packages() -> None:
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\("
    )
    offenders: list[str] = []
    for f in SOURCES_PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(f.name)
    assert offenders == [], (
        f"sources/ packages must NOT construct ORM rows in 8.5A: "
        f"{offenders}"
    )


def test_no_orm_construction_in_8_5a_preflight_scripts() -> None:
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\("
    )
    offenders: list[str] = []
    for name in (
        "brave_source_preflight_8_5a.py",
        "youtube_comments_preflight_8_5a.py",
        "amazon_reviews_2023_preflight_8_5a.py",
    ):
        path = SCRIPTS_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(name)
    assert offenders == [], (
        f"Phase 8.5A preflight scripts must NOT construct ORM rows: "
        f"{offenders}"
    )


def test_no_frontend_references_in_sources_packages() -> None:
    forbidden = ("apps/web", "next/router", "next.js")
    offenders: list[str] = []
    for f in SOURCES_PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for s in forbidden:
            if s in text:
                offenders.append(f"{f.name}: {s}")
    assert offenders == [], (
        f"sources/ packages must NOT reference frontend paths: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# Bonus: brave query builder + youtube comment quality
# ---------------------------------------------------------------------------


def test_brave_query_builder_caps_at_max_queries() -> None:
    qs = build_brave_query_set(
        product_name="Triton Drinks",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        extra_terms=["energy drink caffeine safety gym"],
        max_queries=3,
    )
    assert len(qs) == 3


def test_brave_is_key_present_no_value_leak(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "this-should-not-leak-1234")
    assert is_brave_key_present() is True
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert is_brave_key_present() is False


def test_youtube_is_key_present_no_value_leak(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "this-should-not-leak-yt")
    assert is_youtube_key_present() is True
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    assert is_youtube_key_present() is False


def test_youtube_redact_strips_email_phone_url() -> None:
    s = (
        "Email me at user@example.com or call +1 (415) 555-1212 "
        "and visit https://spam.example.com/path now"
    )
    out = redact_comment_for_audit(s)
    assert "user@example.com" not in out
    assert "555-1212" not in out
    assert "https://spam.example.com" not in out
    assert "[REDACTED-EMAIL]" in out
    assert "[REDACTED-PHONE]" in out
    assert "[REDACTED-URL]" in out


def test_youtube_low_quality_filter_rejects_obvious_spam() -> None:
    assert looks_like_low_quality_comment("") is True
    assert looks_like_low_quality_comment("ok") is True
    assert looks_like_low_quality_comment("first!") is True
    assert looks_like_low_quality_comment("FIRST COMMENT YEEAH") is True
    assert looks_like_low_quality_comment("🔥🔥🔥🔥") is True
    assert looks_like_low_quality_comment(
        "Honestly the sugar crash from Monster is the reason I "
        "switched to Celsius — the caffeine hit feels cleaner."
    ) is False


# ---------------------------------------------------------------------------
# Amazon search-term filter
# ---------------------------------------------------------------------------


def _amzn_record(text: str, title: str = "") -> AmazonReviewRecord:
    return AmazonReviewRecord(
        category="x",
        parent_asin="p", asin="a", rating=5.0,
        title=title, text=text,
        helpful_vote=0, verified_purchase=True,
        timestamp=0, user_id_hash="abc",
    )


def test_amazon_term_filter_word_boundary() -> None:
    rec = _amzn_record(
        "I switched from Red Bull to Celsius because of the sugar crash"
    )
    matched = matches_search_terms(
        record=rec,
        search_terms=["Red Bull", "Monster", "sugar", "crash", "caffeine"],
    )
    assert "Red Bull" in matched
    assert "sugar" in matched
    assert "crash" in matched
    assert "Monster" not in matched
    assert "caffeine" not in matched


def test_amazon_low_quality_filter() -> None:
    cfg = AmazonReviewsAdapterConfig()
    short_rec = _amzn_record("ok")
    assert looks_like_low_quality_review(record=short_rec, config=cfg) is True
    good_rec = _amzn_record(
        "I drink Celsius before workouts because the caffeine hits "
        "without sugar crashes; better than Red Bull or Monster for me."
    )
    assert (
        looks_like_low_quality_review(record=good_rec, config=cfg) is False
    )
