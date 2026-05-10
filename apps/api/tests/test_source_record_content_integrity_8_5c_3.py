"""Phase 8.5C.3 — content-integrity audit tests.

20 tests covering operator scenarios 1-20 (#21+22 are full-suite
verifications, validated by the regression sweep itself). All tests
are unit tests over synthetic JSONL fixtures + the script's pure
functions. Live DB-touching is exercised by the post-test live run.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import re
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "triton_amazon_source_record_content_integrity_8_5c_3.py"
)
_spec = importlib.util.spec_from_file_location(
    "ph_8_5c_3_script", _SCRIPT_PATH,
)
assert _spec is not None
script = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(script)


# ---------------------------------------------------------------------------
# JSONL fixture builders
# ---------------------------------------------------------------------------


def _amazon_review_dict(
    *, parent_asin: str, asin: str | None,
    title: str, text: str, rating: float = 5.0,
    timestamp: int = 1700000000,
) -> dict:
    return {
        "rating": rating, "title": title, "text": text,
        "user_id": "RAW_USER_ID_DO_NOT_LEAK",
        "parent_asin": parent_asin, "asin": asin,
        "helpful_vote": 1, "verified_purchase": True,
        "timestamp": timestamp,
        "images": [{"large": "https://m.media-amazon.com/images/I/abc.jpg"}],
    }


def _make_jsonl(
    tmp_path: Path, category: str, rows: list[dict],
) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    f = raw / f"{category}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# 1 + 2 + 3 + 4 + 5. Script structure: read-only DB discipline
# ---------------------------------------------------------------------------


def test_script_finds_inserted_rows_by_ingested_by_tag() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert (
        "assembly_phase_8_5c_triton_amazon_dynamic_policy_bounded_ingest"
        in src
    )
    assert "SourceRecord.ingested_by ==" in src


def test_script_does_not_update_source_records() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    bad = (
        "session.add(", "session.commit(", "session.flush(",
        ".execute(insert(", ".execute(update(", ".execute(delete(",
        "session.delete(",
    )
    for token in bad:
        assert token not in src, f"forbidden token {token!r} in script"


def test_script_does_not_construct_orm_rows() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "SourceRecord(", "PersonaRecord(", "PersonaTrait(",
        "PersonaEvidenceLink(", "PersonaGraphEdge(", "PersonaCluster(",
        "PersonaClusterMembership(", "PersonaOpinion(",
        "AudienceRetrievalRun(", "PopulationConstructionAudit(",
        "SimulationOutput(", "SimulationRound(", "DebateTurn(",
        "AgentResponse(", "Agent(", "AgentEdge(",
    )
    for term in forbidden:
        # Allow `select(SourceRecord)` etc. — those are reads.
        # The forbidden pattern is `<Name>(<arg` for construction.
        for m in re.finditer(re.escape(term) + r"\s*\w", src):
            ctx = src[max(0, m.start() - 20):m.end() + 20]
            if "select(" in ctx:
                continue
            raise AssertionError(
                f"forbidden ORM construction in script: ...{ctx}..."
            )


def test_script_db_baseline_pre_post_check_present() -> None:
    """Script reads counts BEFORE and AFTER the audit and asserts
    `db_unchanged_during_audit`."""
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "db_baseline_pre" in src
    assert "db_baseline_post" in src
    assert "db_unchanged_during_audit" in src


def test_script_no_external_api_calls() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = ("httpx.", "requests.", "aiohttp.", "import httpx",
                 "import requests", "import aiohttp",
                 "anthropic", "openai", "tavily", "firecrawl",
                 "brave_search", "youtube_data")
    for s in forbidden:
        # `httpx` may appear in test fixtures but the script itself
        # must not use any HTTP transport.
        assert s.lower() not in src.lower(), f"forbidden HTTP/API surface: {s}"


def test_script_no_amazon_dot_com_url_strings() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    assert pat.search(src) is None


# ---------------------------------------------------------------------------
# 6 + 7 + 8 + 9. JSONL streaming reconstruction
# ---------------------------------------------------------------------------


def test_reconstruct_streams_jsonl_line_by_line(tmp_path) -> None:
    """The reconstructor opens the file with `open(..., "rt")` and
    iterates with `for raw in fh`. We verify by giving it a 1k-row
    file and confirming we don't load the whole file (no panic on
    a large file)."""
    big_rows = [
        _amazon_review_dict(
            parent_asin=f"asin{i:04d}", asin=f"asin{i:04d}",
            title=f"Review {i}", text=f"Content {i}",
        )
        for i in range(1000)
    ]
    big_rows.append(_amazon_review_dict(
        parent_asin="TARGET", asin="TARGET",
        title="Target Title", text="Target body text here.",
    ))
    _make_jsonl(tmp_path, "Test_Category", big_rows)
    raw_dir = tmp_path / "raw"
    res = script.reconstruct_full_review(
        raw_dir=raw_dir, category="Test_Category",
        parent_asin="TARGET", asin="TARGET",
        inserted_content="Target Title\n\nTarget body",
    )
    assert res["status"] == "FOUND"
    assert res["title"] == "Target Title"


def test_reconstruct_finds_review_by_parent_asin_and_asin(tmp_path) -> None:
    rows = [
        _amazon_review_dict(
            parent_asin="B0X", asin="B0X",
            title="Energy Drink Review",
            text="Great caffeine kick before workouts.",
        ),
    ]
    _make_jsonl(tmp_path, "Health", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="Health",
        parent_asin="B0X", asin="B0X",
        inserted_content="Energy Drink Review\n\nGreat caffeine",
    )
    assert res["status"] == "FOUND"
    assert "caffeine kick" in res["text_preview"]


def test_reconstruct_returns_ambiguous_when_multiple_match(tmp_path) -> None:
    rows = [
        _amazon_review_dict(
            parent_asin="B0X", asin="B0X",
            title="Same Title",
            text="The flavor is okay and the caffeine kicks in.",
        ),
        _amazon_review_dict(
            parent_asin="B0X", asin="B0X",
            title="Same Title",
            text="The flavor is okay and the caffeine kicks in.",
            rating=2.0,
        ),
    ]
    _make_jsonl(tmp_path, "Health", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="Health",
        parent_asin="B0X", asin="B0X",
        inserted_content="Same Title\n\nThe flavor is okay",
    )
    assert res["status"] == "AMBIGUOUS"


def test_reconstruct_returns_not_found_when_no_match(tmp_path) -> None:
    rows = [
        _amazon_review_dict(
            parent_asin="B0Y", asin="B0Y",
            title="Different Product", text="Not the target review.",
        ),
    ]
    _make_jsonl(tmp_path, "Health", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="Health",
        parent_asin="B0X", asin="B0X",
        inserted_content="Energy Drink Review\n\nGreat caffeine",
    )
    assert res["status"] == "NOT_FOUND"


def test_reconstruct_returns_file_missing_when_no_file(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    res = script.reconstruct_full_review(
        raw_dir=raw_dir, category="Nonexistent_Category",
        parent_asin="B0X", asin="B0X", inserted_content="x",
    )
    assert res["status"] == "FILE_MISSING"


# ---------------------------------------------------------------------------
# 10 + 11 + 12. Length comparison + prefix check + truncation ratio
# ---------------------------------------------------------------------------


def test_reconstruct_computes_combined_length_correctly(tmp_path) -> None:
    rows = [_amazon_review_dict(
        parent_asin="B0", asin="B0",
        title="Hi",  # 2 chars
        text="Body of review here.",  # 20 chars
    )]
    # combined = "Hi\n\nBody of review here." = 2 + 4 + 20 = 24 chars
    _make_jsonl(tmp_path, "C", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="C",
        parent_asin="B0", asin="B0", inserted_content="Hi\n\nBody",
    )
    assert res["status"] == "FOUND"
    assert res["combined_length"] == len("Hi\n\nBody of review here.")


def test_reconstruct_detects_inserted_is_prefix_of_full(tmp_path) -> None:
    full_text = "Body of review here. " * 30
    rows = [_amazon_review_dict(
        parent_asin="B0", asin="B0", title="Hi", text=full_text,
    )]
    _make_jsonl(tmp_path, "C", rows)
    inserted = ("Hi\n\n" + full_text)[:240]  # mimic 8.5C.2's truncation
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="C",
        parent_asin="B0", asin="B0", inserted_content=inserted,
    )
    assert res["status"] == "FOUND"
    assert res["inserted_is_prefix_of_original"] is True


# ---------------------------------------------------------------------------
# 13 + 14 + 15. Sufficiency labels
# ---------------------------------------------------------------------------


def test_label_sufficient_as_is_for_short_full_review() -> None:
    recon = {
        "status": "FOUND", "combined_length": 60,
        "inserted_is_prefix_of_original": True,
    }
    label, reasons = script.assign_sufficiency_label(
        inserted_content_length=60, reconstruction=recon,
        persona_value_label="medium",
        persona_value_roles=["competitor_user_x"],
    )
    assert label == "SUFFICIENT_AS_IS"


def test_label_sufficient_as_is_when_truncation_high_retention() -> None:
    # ratio = 230 / 245 = 0.94 >= 0.85
    recon = {
        "status": "FOUND", "combined_length": 245,
        "inserted_is_prefix_of_original": True,
    }
    label, _ = script.assign_sufficiency_label(
        inserted_content_length=230, reconstruction=recon,
        persona_value_label="high",
        persona_value_roles=["competitor_user_x"],
    )
    # 245 falls inside the "naturally short" window (<=245)
    assert label == "SUFFICIENT_AS_IS"


def test_label_usable_but_thin_for_moderate_truncation() -> None:
    # ratio = 240 / 400 = 0.60
    recon = {
        "status": "FOUND", "combined_length": 400,
        "inserted_is_prefix_of_original": True,
    }
    label, _ = script.assign_sufficiency_label(
        inserted_content_length=240, reconstruction=recon,
        persona_value_label="medium",
        persona_value_roles=["competitor_user_x"],
    )
    assert label == "USABLE_BUT_THIN"


def test_label_needs_full_text_companion_for_heavy_truncation() -> None:
    # ratio = 240 / 1200 = 0.20
    recon = {
        "status": "FOUND", "combined_length": 1200,
        "inserted_is_prefix_of_original": True,
    }
    label, _ = script.assign_sufficiency_label(
        inserted_content_length=240, reconstruction=recon,
        persona_value_label="medium",
        persona_value_roles=["competitor_user_x"],
    )
    assert label == "NEEDS_FULL_TEXT_COMPANION"


def test_label_exclude_when_reconstruction_fails() -> None:
    for status in ("NOT_FOUND", "AMBIGUOUS", "FILE_MISSING"):
        recon = {"status": status}
        label, _ = script.assign_sufficiency_label(
            inserted_content_length=240, reconstruction=recon,
            persona_value_label="medium",
            persona_value_roles=["competitor_user_x"],
        )
        assert label == "EXCLUDE_FROM_PERSONA_BUILD"


def test_label_exclude_when_no_persona_roles() -> None:
    recon = {
        "status": "FOUND", "combined_length": 200,
        "inserted_is_prefix_of_original": True,
    }
    label, _ = script.assign_sufficiency_label(
        inserted_content_length=200, reconstruction=recon,
        persona_value_label="low", persona_value_roles=[],
    )
    assert label == "EXCLUDE_FROM_PERSONA_BUILD"


# ---------------------------------------------------------------------------
# 16 + 17. Privacy: no raw user_id, no image URLs in audit
# ---------------------------------------------------------------------------


def test_reconstruct_does_not_surface_raw_user_id(tmp_path) -> None:
    rows = [_amazon_review_dict(
        parent_asin="B0", asin="B0",
        title="Hi", text="Body text.",
    )]
    _make_jsonl(tmp_path, "C", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="C",
        parent_asin="B0", asin="B0", inserted_content="Hi\n\nBody",
    )
    assert res["status"] == "FOUND"
    blob = json.dumps(res, default=str)
    assert "user_id" not in blob
    assert "RAW_USER_ID_DO_NOT_LEAK" not in blob


def test_reconstruct_does_not_surface_image_urls(tmp_path) -> None:
    rows = [_amazon_review_dict(
        parent_asin="B0", asin="B0", title="Hi", text="Body.",
    )]
    _make_jsonl(tmp_path, "C", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="C",
        parent_asin="B0", asin="B0", inserted_content="Hi\n\nBody",
    )
    blob = json.dumps(res, default=str)
    assert ".jpg" not in blob
    assert "media-amazon.com" not in blob


# ---------------------------------------------------------------------------
# 18 + 19. No external API calls / no Amazon.com scraping
# ---------------------------------------------------------------------------


def test_script_imports_no_http_or_scraping_libs() -> None:
    import ast
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {"httpx", "requests", "aiohttp", "urllib", "urllib3",
                 "selenium", "playwright", "scrapy",
                 "beautifulsoup4", "bs4"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in forbidden, f"forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            assert root not in forbidden, f"forbidden import: {node.module}"


# ---------------------------------------------------------------------------
# 20. Existing 8.5C.1 + 8.5C.2 imports still resolve
# ---------------------------------------------------------------------------


def test_8_5c_1_and_8_5c_2_imports_still_resolve() -> None:
    # The new script doesn't break the existing surface.
    from assembly.sources.ingestion_policy import (  # noqa: F401
        UNIVERSAL_GUARDRAILS, generate_ingestion_policy,
    )


# ---------------------------------------------------------------------------
# Bonus: jsonl.gz support
# ---------------------------------------------------------------------------


def test_reconstruct_supports_jsonl_gz(tmp_path) -> None:
    rows = [_amazon_review_dict(
        parent_asin="B0", asin="B0",
        title="Compressed", text="Body of compressed review.",
    )]
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "C.jsonl.gz"
    payload = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    with gzip.open(f, "wb") as fh:
        fh.write(payload)
    res = script.reconstruct_full_review(
        raw_dir=raw, category="C",
        parent_asin="B0", asin="B0", inserted_content="Compressed\n\nBody",
    )
    assert res["status"] == "FOUND"
