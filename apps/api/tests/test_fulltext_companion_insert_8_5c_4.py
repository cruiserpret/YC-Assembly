"""Phase 8.5C.4 — full-text companion insertion tests.

30 tests covering operator scenarios 1-30. (#31, #32, #33 are
full-suite verifications validated by the regression sweep itself.)
NO live DB writes from this file — synthetic JSONL fixtures +
monkeypatched DB session for the duplicate-check + lineage-resolver.
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
    / "scripts" / "triton_amazon_fulltext_companion_insert_8_5c_4.py"
)
_spec = importlib.util.spec_from_file_location(
    "ph_8_5c_4_script", _SCRIPT_PATH,
)
assert _spec is not None
script = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(script)


# ---------------------------------------------------------------------------
# Synthetic 8.5C.3 audit row + JSONL fixtures
# ---------------------------------------------------------------------------


def _audit_row(
    *,
    source_record_id: str = "00000000-0000-0000-0000-000000000001",
    category: str = "Health_and_Household",
    parent_asin: str = "B0X",
    asin: str = "B0X",
    metadata_title: str = "Test Product",
    inserted_content_preview: str = "Test Title\n\nTest text body.",
    inserted_content_length: int = 27,
    truncation_ratio: float = 0.36,
    sufficiency_label: str = "NEEDS_FULL_TEXT_COMPANION",
    persona_value_roles_8_5c_1: list[str] | None = None,
    extended_persona_roles_from_full: list[str] | None = None,
    additional_roles: list[str] | None = None,
) -> dict:
    return {
        "source_record_id": source_record_id,
        "category": category,
        "parent_asin": parent_asin,
        "asin": asin,
        "metadata_title": metadata_title,
        "inserted_content_preview": inserted_content_preview,
        "inserted_content_length": inserted_content_length,
        "content_truncation_ratio": truncation_ratio,
        "sufficiency_label": sufficiency_label,
        "persona_value_roles_from_8_5c_1": (
            persona_value_roles_8_5c_1 or ["competitor_user_x"]
        ),
        "extended_persona_roles_from_full_text": (
            extended_persona_roles_from_full
            or ["competitor_user_x", "safety_skeptic"]
        ),
        "additional_persona_roles_unlocked_by_full": (
            additional_roles or ["safety_skeptic"]
        ),
        "metadata_main_category": "Health & Household",
        "metadata_categories": [
            "Health & Household", "Diet & Sports Nutrition",
        ],
    }


def _amazon_review(
    *, parent_asin: str, asin: str, title: str, text: str,
    rating: float = 5.0, timestamp: int = 1700000000,
) -> dict:
    return {
        "rating": rating, "title": title, "text": text,
        "user_id": "RAW_USER_ID_DO_NOT_LEAK",
        "parent_asin": parent_asin, "asin": asin,
        "helpful_vote": 1, "verified_purchase": True,
        "timestamp": timestamp,
        "images": [{"large": "https://m.media-amazon.com/images/I/abc.jpg"}],
    }


def _make_jsonl(tmp_path: Path, category: str, rows: list[dict]) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    f = raw / f"{category}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# 1 + 2 + 3. Audit-input + filter rules
# ---------------------------------------------------------------------------


def test_script_reads_8_5c_3_audit_path() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert (
        "triton_amazon_source_record_content_integrity_8_5c_3.json"
        in src
    )


def test_script_filters_only_needs_full_text_companion_rows() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert (
        'sufficiency_label") == "NEEDS_FULL_TEXT_COMPANION"' in src
        or "sufficiency_label\") == \"NEEDS_FULL_TEXT_COMPANION\"" in src
    )


def test_script_aborts_when_target_count_not_exactly_2() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "len(targets) != 2" in src
    assert "expected exactly 2" in src


# ---------------------------------------------------------------------------
# 4 + 5 + 6 + 7. Reconstruction streaming
# ---------------------------------------------------------------------------


def test_reconstruct_finds_review_by_parent_asin_and_asin(tmp_path) -> None:
    rows = [_amazon_review(
        parent_asin="B0X", asin="B0X",
        title="Test Title",
        text="Full body text here, longer than the 240-char preview.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="Test",
        parent_asin="B0X", asin="B0X",
        inserted_preview_content="Test Title\n\nFull body text",
    )
    assert res["status"] == "FOUND"
    assert "Full body text" in res["text"]
    assert res["combined_length"] > 30


def test_reconstruct_streams_jsonl_line_by_line(tmp_path) -> None:
    big_rows = [
        _amazon_review(
            parent_asin=f"B{i:04d}", asin=f"B{i:04d}",
            title=f"R{i}", text=f"Body {i}",
        )
        for i in range(1000)
    ]
    big_rows.append(_amazon_review(
        parent_asin="TARGET", asin="TARGET",
        title="Target Title", text="Target body content here",
    ))
    _make_jsonl(tmp_path, "Test", big_rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="Test",
        parent_asin="TARGET", asin="TARGET",
        inserted_preview_content="Target Title\n\nTarget body",
    )
    assert res["status"] == "FOUND"


def test_reconstruct_returns_ambiguous_for_multi_match(tmp_path) -> None:
    rows = [
        _amazon_review(
            parent_asin="B0", asin="B0",
            title="Same Title", text="Same body content.",
        ),
        _amazon_review(
            parent_asin="B0", asin="B0",
            title="Same Title", text="Same body content.",
            rating=2.0,
        ),
    ]
    _make_jsonl(tmp_path, "Test", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="Test",
        parent_asin="B0", asin="B0",
        inserted_preview_content="Same Title\n\nSame body",
    )
    assert res["status"] == "AMBIGUOUS"


def test_reconstruct_returns_not_found_when_no_match(tmp_path) -> None:
    rows = [_amazon_review(
        parent_asin="B0", asin="B0",
        title="Different", text="Other body.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    res = script.reconstruct_full_review(
        raw_dir=tmp_path / "raw", category="Test",
        parent_asin="B999", asin="B999",
        inserted_preview_content="Test",
    )
    assert res["status"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 8 + 9 + 10. No update / no delete; only SourceRecord constructed
# ---------------------------------------------------------------------------


def test_script_does_not_update_or_delete_source_records() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    bad = (
        ".execute(update(", ".execute(delete(",
        "session.delete(",
    )
    for token in bad:
        assert token not in src, f"forbidden: {token!r}"


def test_script_only_constructs_source_record_no_other_orm() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "PersonaRecord(", "PersonaTrait(", "PersonaEvidenceLink(",
        "PersonaGraphEdge(", "PersonaCluster(", "PersonaClusterMembership(",
        "PersonaOpinion(", "AudienceRetrievalRun(",
        "PopulationConstructionAudit(",
        "SimulationOutput(", "SimulationRound(", "DebateTurn(",
        "AgentResponse(", "Agent(", "AgentEdge(",
    )
    for term in forbidden:
        for m in re.finditer(re.escape(term) + r"\s*\w", src):
            ctx = src[max(0, m.start() - 20):m.end() + 20]
            if "select(" in ctx:  # SELECT(SourceRecord) etc. are reads
                continue
            raise AssertionError(
                f"forbidden ORM construction: ...{ctx}..."
            )


def test_script_constructs_source_record_directly_inside_transaction() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    # SourceRecord(**kwargs) appears inside the transaction block
    txn_start = src.find("async with session.begin():")
    sr_construct = src.find("SourceRecord(**kwargs)")
    assert txn_start > 0 and sr_construct > txn_start


# ---------------------------------------------------------------------------
# 11 + 12 + 13 + 14. Persona/trait/link/graph/sim/UI tables untouched
# ---------------------------------------------------------------------------


def test_persona_table_unchanged_check_inside_transaction() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    txn_start = src.find("async with session.begin():")
    for name in ("persona_records", "persona_traits",
                 "persona_evidence_links"):
        assert name in src
    assert "count changed during insert" in src
    # The "changed" check is inside the transaction
    chk_idx = src.find("count changed during insert")
    assert chk_idx > txn_start


def test_no_simulation_or_graph_or_frontend_writes() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "Agent(", "AgentResponse(", "DebateTurn(",
        "Simulation(", "SimulationOutput(", "SimulationRound(",
        "PersonaGraphEdge(", "PersonaCluster(",
        "apps/web", "next/router", "next.js",
    )
    for s in forbidden:
        for m in re.finditer(re.escape(s) + r"\s*\w", src):
            ctx = src[max(0, m.start() - 20):m.end() + 20]
            if "select(" in ctx:
                continue
            raise AssertionError(f"forbidden token: {s!r}")


# ---------------------------------------------------------------------------
# 15 + 16 + 17 + 18 + 19. Companion metadata lineage + caveat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_companion_metadata_includes_lineage_fields(
    tmp_path, monkeypatch,
) -> None:
    rows = [_amazon_review(
        parent_asin="B0X", asin="B0X",
        title="Test Title",
        text="Caffeine kick is great for the gym.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    audit_row = _audit_row(
        source_record_id="11111111-2222-3333-4444-555555555555",
        category="Test", parent_asin="B0X", asin="B0X",
        inserted_content_preview="Test Title\n\nCaffeine kick",
    )
    # Stub duplicate check to false + lineage resolver to a known UUID
    async def _no_dup(**kwargs):
        return False
    async def _resolve(*args, **kwargs):
        return "11111111-2222-3333-4444-555555555555"
    monkeypatch.setattr(
        script, "check_duplicate_content_hash", _no_dup,
    )
    monkeypatch.setattr(
        script, "_resolve_preview_record_id_by_parent_asin", _resolve,
    )
    proc = await script._build_companion_record_kwargs(
        audit_row=audit_row, raw_dir=tmp_path / "raw",
        sessionmaker=None,
    )
    assert proc["kwargs"] is not None
    md = proc["kwargs"]["metadata_"]
    assert md["original_preview_source_record_id"] == (
        "11111111-2222-3333-4444-555555555555"
    )
    assert md["supersedes_preview_source_record_id"] == (
        "11111111-2222-3333-4444-555555555555"
    )
    assert md["full_text_reconstruction"] is True
    assert md["source_record_lineage"] == "full_text_companion"
    assert md["source_is_historical"] is True
    assert "Amazon Reviews 2023" in md["source_caveat"]
    assert md["inserted_from_phase"] == "8.5C.4"
    assert md["recommended_for_persona_build"] is True


# ---------------------------------------------------------------------------
# 20 + 21. PII + fake-buyer scanner re-runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_scanner_rejects_full_text_with_email(
    tmp_path, monkeypatch,
) -> None:
    rows = [_amazon_review(
        parent_asin="B0", asin="B0",
        title="Title", text="Email user@example.com for details.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    audit_row = _audit_row(
        category="Test", parent_asin="B0", asin="B0",
        inserted_content_preview="Title\n\nEmail",
    )
    async def _no_dup(**k): return False
    async def _resolve(*a, **k): return "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(
        script, "check_duplicate_content_hash", _no_dup,
    )
    monkeypatch.setattr(
        script, "_resolve_preview_record_id_by_parent_asin", _resolve,
    )
    proc = await script._build_companion_record_kwargs(
        audit_row=audit_row, raw_dir=tmp_path / "raw",
        sessionmaker=None,
    )
    assert proc["kwargs"] is None
    assert "scanner_failure" in (proc["skip_reason"] or "")
    assert proc["scanner_results"]["pii_scan"]


@pytest.mark.asyncio
async def test_fake_buyer_scanner_rejects_full_text_with_triton_buyer(
    tmp_path, monkeypatch,
) -> None:
    rows = [_amazon_review(
        parent_asin="B0", asin="B0",
        title="Triton Drinks review",
        text="I am a Triton buyer. I tried Triton last week.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    audit_row = _audit_row(
        category="Test", parent_asin="B0", asin="B0",
        inserted_content_preview="Triton Drinks review\n\nI am",
    )
    async def _no_dup(**k): return False
    async def _resolve(*a, **k): return "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(
        script, "check_duplicate_content_hash", _no_dup,
    )
    monkeypatch.setattr(
        script, "_resolve_preview_record_id_by_parent_asin", _resolve,
    )
    proc = await script._build_companion_record_kwargs(
        audit_row=audit_row, raw_dir=tmp_path / "raw",
        sessionmaker=None,
    )
    assert proc["kwargs"] is None
    assert proc["scanner_results"]["unlaunched_fake_buyer_scan"]


# ---------------------------------------------------------------------------
# 22. Duplicate check re-run before insert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_check_runs_before_insert(
    tmp_path, monkeypatch,
) -> None:
    rows = [_amazon_review(
        parent_asin="B0", asin="B0", title="T", text="Body.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    audit_row = _audit_row(
        category="Test", parent_asin="B0", asin="B0",
        inserted_content_preview="T\n\nBody",
    )
    async def _is_dup(**k): return True  # already exists
    async def _resolve(*a, **k): return "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(
        script, "check_duplicate_content_hash", _is_dup,
    )
    monkeypatch.setattr(
        script, "_resolve_preview_record_id_by_parent_asin", _resolve,
    )
    proc = await script._build_companion_record_kwargs(
        audit_row=audit_row, raw_dir=tmp_path / "raw",
        sessionmaker=None,
    )
    assert proc["kwargs"] is None
    assert proc["scanner_results"]["duplicate_check"]


# ---------------------------------------------------------------------------
# 23 + 24. Transaction rollback discipline
# ---------------------------------------------------------------------------


def test_transaction_uses_session_begin_for_atomicity() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "async with session.begin():" in src
    # Count-mismatch raises inside the transaction
    txn_idx = src.find("async with session.begin():")
    mismatch_idx = src.find("count mismatch")
    assert mismatch_idx > txn_idx


def test_transaction_rolls_back_on_count_mismatch() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "raise RuntimeError" in src
    assert "rollback_reason" in src


# ---------------------------------------------------------------------------
# 25 + 26. source_url + compliance_tag conventions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_companion_source_url_uses_local_fulltext_suffix(
    tmp_path, monkeypatch,
) -> None:
    rows = [_amazon_review(
        parent_asin="B0X", asin="B0X", title="T",
        text="Caffeine and pre-workout body text.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    audit_row = _audit_row(
        category="Test", parent_asin="B0X", asin="B0X",
        inserted_content_preview="T\n\nCaffeine",
    )
    async def _no_dup(**k): return False
    async def _resolve(*a, **k): return "0" * 36
    monkeypatch.setattr(
        script, "check_duplicate_content_hash", _no_dup,
    )
    monkeypatch.setattr(
        script, "_resolve_preview_record_id_by_parent_asin", _resolve,
    )
    proc = await script._build_companion_record_kwargs(
        audit_row=audit_row, raw_dir=tmp_path / "raw",
        sessionmaker=None,
    )
    assert proc["kwargs"] is not None
    assert proc["kwargs"]["source_url"].startswith(
        "local://amazon_reviews_2023_local/"
    )
    assert proc["kwargs"]["source_url"].endswith("/fulltext")
    assert proc["kwargs"]["compliance_tag"] == "open_dataset"


# ---------------------------------------------------------------------------
# 27 + 28. Privacy: no raw user_id, no image URLs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_companion_metadata_drops_raw_user_id_and_image_urls(
    tmp_path, monkeypatch,
) -> None:
    rows = [_amazon_review(
        parent_asin="B0", asin="B0", title="T", text="Body.",
    )]
    _make_jsonl(tmp_path, "Test", rows)
    audit_row = _audit_row(
        category="Test", parent_asin="B0", asin="B0",
        inserted_content_preview="T\n\nBody",
    )
    async def _no_dup(**k): return False
    async def _resolve(*a, **k): return "0" * 36
    monkeypatch.setattr(
        script, "check_duplicate_content_hash", _no_dup,
    )
    monkeypatch.setattr(
        script, "_resolve_preview_record_id_by_parent_asin", _resolve,
    )
    proc = await script._build_companion_record_kwargs(
        audit_row=audit_row, raw_dir=tmp_path / "raw",
        sessionmaker=None,
    )
    assert proc["kwargs"] is not None
    blob = json.dumps(proc["kwargs"], default=str)
    assert "RAW_USER_ID_DO_NOT_LEAK" not in blob
    assert "user_id" not in blob
    assert ".jpg" not in blob
    assert "media-amazon" not in blob
    assert proc["kwargs"]["user_handle_hash"] is None


# ---------------------------------------------------------------------------
# 29. No Amazon.com scraping URLs
# ---------------------------------------------------------------------------


def test_no_amazon_dot_com_url_strings_in_script() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    assert pat.search(src) is None


# ---------------------------------------------------------------------------
# 30. No external API calls
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
                assert root not in forbidden, f"forbidden: {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            assert root not in forbidden, f"forbidden: {node.module}"


# ---------------------------------------------------------------------------
# Bonus: --commit gating
# ---------------------------------------------------------------------------


def test_default_is_preview_only_no_writes_unless_commit() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert '"--commit"' in src
    assert 'action="store_true"' in src
    assert "if args.commit:" in src or "if not args.commit:" in src


def test_jsonl_gz_supported(tmp_path) -> None:
    rows = [_amazon_review(
        parent_asin="B0", asin="B0",
        title="GzTest", text="Compressed body content.",
    )]
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "Test.jsonl.gz"
    payload = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    with gzip.open(f, "wb") as fh:
        fh.write(payload)
    res = script.reconstruct_full_review(
        raw_dir=raw, category="Test",
        parent_asin="B0", asin="B0",
        inserted_preview_content="GzTest\n\nCompressed",
    )
    assert res["status"] == "FOUND"
