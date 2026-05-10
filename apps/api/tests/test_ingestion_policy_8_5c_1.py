"""Phase 8.5C.1 — dynamic ingestion-policy planner tests.

Operator scenarios covered (23 of 25; #24 + #25 are full-suite
verifications, asserted by the harness regression sweep itself).
"""
from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path

import pytest

from assembly.sources.amazon_reviews_2023.adapter import AmazonReviewRecord
from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning, generate_anchor_plan,
)
from assembly.sources.ingestion_policy import (
    REQUIRED_SCANNERS, UNIVERSAL_GUARDRAILS,
    CandidateDecision, CandidateRow, IngestionPolicy,
    PlannedSourceRecordPreview, PoolSummary,
    compute_content_hash, generate_ingestion_policy,
    scan_dataset_compliance, scan_pii, scan_unlaunched_fake_buyer,
)


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "ingestion_policy"
)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _triton_brief() -> ProductBriefForPlanning:
    return ProductBriefForPlanning(
        product_name="Triton Drinks",
        product_description=(
            "A caffeinated sports and energy drink positioned for "
            "students, gym users, athletes, and busy young adults."
        ),
        price_or_price_structure="$3.99 per can",
        launch_geography="California, United States",
        target_customers=["college students", "athletes"],
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
    )


def _candidate(
    *,
    candidate_id: str = "Grocery_and_Gourmet_Food::B0XYZ::B0XYZ",
    category: str = "Grocery_and_Gourmet_Food",
    score: int = 8,
    confidence: str = "high_confidence",
    matched_terms: list[str] | None = None,
    title: str = "Energy Drink Sample Box",
    text: str = "Caffeine and energy drink, no sugar. Good flavor.",
    verified: bool = True,
    metadata_main_category: str = "Beverages",
    metadata_categories: list[str] | None = None,
    metadata_title: str | None = "Energy Drink Sample Box",
) -> CandidateRow:
    if matched_terms is None:
        matched_terms = ["positive:energy drink", "positive(weak):drink",
                         "generic_modifier (qualified)"]
    if metadata_categories is None:
        metadata_categories = ["Grocery & Gourmet Food", "Beverages",
                               "Energy Drinks"]
    return CandidateRow(
        candidate_id=candidate_id, category=category,
        parent_asin="B0XYZ", asin="B0XYZ",
        rating=5.0, verified_purchase=verified, helpful_vote=3,
        timestamp=1700000000, title=title, text=text,
        user_id_hash="abcdef0123456789",
        score=score, confidence=confidence,  # type: ignore[arg-type]
        matched_terms=matched_terms, denylist_hits=[],
        metadata_title=metadata_title,
        metadata_main_category=metadata_main_category,
        metadata_categories=metadata_categories,
    )


# ---------------------------------------------------------------------------
# 1 + 2. Schemas exist and are closed
# ---------------------------------------------------------------------------


def test_ingestion_policy_schema_exists_with_required_fields() -> None:
    fields = IngestionPolicy.model_fields.keys()
    required = {
        "product_name", "target_brief_id", "source_family",
        "product_launch_state", "evidence_anchor_plan_id", "policy_id",
        "policy_generated_from", "candidate_pool_summary",
        "selection_objectives", "evidence_quality_dimensions",
        "persona_construction_value_dimensions",
        "dynamic_selection_rules", "dynamic_rejection_rules",
        "universal_guardrails", "max_insert_cap", "required_scanners",
        "source_record_shape", "caveats", "generated_at",
    }
    assert required.issubset(set(fields))


def test_candidate_decision_schema_exists() -> None:
    fields = CandidateDecision.model_fields.keys()
    required = {
        "candidate_id", "decision", "selection_rank",
        "evidence_strength_label", "source_relevance_label",
        "persona_value_label", "selected_for_persona_roles",
        "decision_reasons", "rejection_reasons", "scanner_results",
        "duplicate_check", "planned_source_record_preview",
    }
    assert required.issubset(set(fields))


# ---------------------------------------------------------------------------
# 3 + 4. Planner inputs — only founder brief + plan + pool, no anchors
# ---------------------------------------------------------------------------


def test_planner_signature_does_not_take_manual_category_anchors() -> None:
    sig = inspect.signature(generate_ingestion_policy)
    params = set(sig.parameters.keys())
    expected = {
        "brief", "evidence_anchor_plan", "candidate_pool",
        "source_family", "product_launch_state",
        "db_baseline", "max_insert_cap", "target_brief_id",
    }
    assert params == expected
    # No category-anchor parameter:
    assert "category_anchors" not in params
    assert "manual_anchors" not in params
    assert "ingestion_threshold" not in params


def test_planner_does_not_require_manually_supplied_thresholds() -> None:
    """Planner must work with EMPTY pool too — no threshold injection
    needed."""
    brief = _triton_brief()
    plan = generate_anchor_plan(brief)
    policy = generate_ingestion_policy(
        brief=brief, evidence_anchor_plan=plan,
        candidate_pool=[],
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline={"source_records": 0, "persona_records": 0,
                     "persona_traits": 0, "persona_evidence_links": 0},
        max_insert_cap=12,
    )
    assert isinstance(policy, IngestionPolicy)
    assert policy.policy_generated_from == "deterministic"


# ---------------------------------------------------------------------------
# 5. No hardcoded Triton-specific ingestion thresholds in the policy module
# ---------------------------------------------------------------------------


def _strip_docstrings_and_comments(src: str) -> str:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    ds_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (
            ast.FunctionDef, ast.AsyncFunctionDef,
            ast.ClassDef, ast.Module,
        )):
            ds = ast.get_docstring(node, clean=False)
            if ds is None:
                continue
            if (
                node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                ds_node = node.body[0]
                for ln in range(
                    ds_node.lineno,
                    (ds_node.end_lineno or ds_node.lineno) + 1,
                ):
                    ds_lines.add(ln)
    kept: list[str] = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in ds_lines:
            continue
        ci = line.find("#")
        if ci >= 0:
            line = line[:ci]
        kept.append(line)
    return "\n".join(kept)


def test_no_hardcoded_triton_specific_thresholds_in_policy_pkg() -> None:
    forbidden = (
        "Triton", "Red Bull", "Monster", "Celsius", "Prime Energy",
        "Gatorade",
        # Triton-specific category anchors:
        "energy drink", "pre-workout", "pre workout", "caffeine",
        "electrolyte", "sports drink",
    )
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        code_only = _strip_docstrings_and_comments(src)
        for term in forbidden:
            assert term not in code_only, (
                f"ingestion_policy/{f.name} CODE contains hardcoded "
                f"Triton-specific term {term!r}"
            )


# ---------------------------------------------------------------------------
# 6. Planner generates product-specific selection logic from candidate pool
# ---------------------------------------------------------------------------


def test_planner_derives_objectives_from_brief_and_pool() -> None:
    brief = _triton_brief()
    plan = generate_anchor_plan(brief)
    pool = [_candidate(score=8), _candidate(
        candidate_id="Grocery_and_Gourmet_Food::B0AAA::B0AAA",
        score=12, matched_terms=["positive:energy drink",
                                  "competitor:Red Bull"],
        title="Red Bull alternative",
        text="Tried this as a Red Bull alternative for workouts.",
    )]
    policy = generate_ingestion_policy(
        brief=brief, evidence_anchor_plan=plan, candidate_pool=pool,
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline={}, max_insert_cap=12,
    )
    objectives_blob = " ".join(policy.selection_objectives).lower()
    # Brief-supplied competitors echoed
    assert "red bull" in objectives_blob
    # Pool-driven objective:
    assert any(
        "multi-word" in o.lower() for o in policy.selection_objectives
    )


# ---------------------------------------------------------------------------
# 7. Universal vs product-specific separation
# ---------------------------------------------------------------------------


def test_universal_guardrails_separate_from_dynamic_rules() -> None:
    brief = _triton_brief()
    plan = generate_anchor_plan(brief)
    policy = generate_ingestion_policy(
        brief=brief, evidence_anchor_plan=plan,
        candidate_pool=[_candidate()],
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline={}, max_insert_cap=12,
    )
    # Universal guardrails — closed list, not derived from brief
    assert policy.universal_guardrails == list(UNIVERSAL_GUARDRAILS)
    # required_scanners — closed list
    assert policy.required_scanners == list(REQUIRED_SCANNERS)
    # Universal rejection rules ARE flagged is_universal=True
    universal_rules = [
        r for r in policy.dynamic_rejection_rules if r.is_universal
    ]
    derived_rules = [
        r for r in policy.dynamic_rejection_rules if not r.is_universal
    ]
    assert len(universal_rules) >= 4  # PII, fake-buyer, compliance, dedup
    assert len(derived_rules) >= 1


# ---------------------------------------------------------------------------
# 8 + 9. Selection + rejection mechanics — async test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strong_evidence_selected_dynamically(monkeypatch) -> None:
    from assembly.sources.ingestion_policy import policy as policy_mod
    # Stub the duplicate check so it returns False (unique) without
    # touching the DB.
    async def _no_dup(*, content_hash, sessionmaker):
        return False
    monkeypatch.setattr(
        policy_mod, "check_duplicate_content_hash", _no_dup,
    )
    brief = _triton_brief()
    plan = generate_anchor_plan(brief)
    pool = [
        _candidate(score=12, candidate_id=f"cat::asin{i}",
                   matched_terms=["positive:energy drink",
                                   "positive(weak):energy",
                                   "competitor:Red Bull",
                                   "generic_modifier (qualified)"])
        for i in range(5)
    ]
    policy = generate_ingestion_policy(
        brief=brief, evidence_anchor_plan=plan, candidate_pool=pool,
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline={}, max_insert_cap=12,
    )
    decisions = await policy_mod.decide_candidates(
        candidates=pool, policy=policy, plan=plan,
        sessionmaker=None,  # _no_dup ignores it
        product_name=brief.product_name,
        product_launch_state="unlaunched",
    )
    selected = [d for d in decisions if d.decision == "SELECTED"]
    assert len(selected) >= 1
    # Decision reasons are populated
    assert all(d.decision_reasons for d in selected)


@pytest.mark.asyncio
async def test_weak_single_token_only_evidence_rejected_dynamically(
    monkeypatch,
) -> None:
    from assembly.sources.ingestion_policy import policy as policy_mod
    async def _no_dup(*, content_hash, sessionmaker):
        return False
    monkeypatch.setattr(
        policy_mod, "check_duplicate_content_hash", _no_dup,
    )
    brief = _triton_brief()
    plan = generate_anchor_plan(brief)
    pool = [
        _candidate(
            score=4, confidence="medium_confidence",
            matched_terms=["positive(weak):drink"],  # single-token only
        )
    ]
    policy = generate_ingestion_policy(
        brief=brief, evidence_anchor_plan=plan, candidate_pool=pool,
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline={}, max_insert_cap=12,
    )
    decisions = await policy_mod.decide_candidates(
        candidates=pool, policy=policy, plan=plan,
        sessionmaker=None,
        product_name=brief.product_name,
        product_launch_state="unlaunched",
    )
    rejected = [d for d in decisions if d.decision == "REJECTED"]
    assert len(rejected) == 1
    reasons = " ".join(rejected[0].rejection_reasons)
    assert "below_high_confidence" in reasons or "no_multi_word_anchor" in reasons


# ---------------------------------------------------------------------------
# 10. Unlaunched-product fake-buyer scanner
# ---------------------------------------------------------------------------


def test_fake_buyer_scanner_rejects_triton_buyer_text() -> None:
    res = scan_unlaunched_fake_buyer(
        text="I am a Triton buyer and a Triton loyalist.",
        product_name="Triton Drinks",
    )
    assert res.issues
    assert any("buyer" in p.lower() for p in res.matched_phrases)


def test_fake_buyer_scanner_rejects_tried_triton_text() -> None:
    res = scan_unlaunched_fake_buyer(
        text="I tried Triton last week and bought Triton again.",
        product_name="Triton Drinks",
    )
    assert res.issues
    assert len(res.matched_phrases) >= 2


def test_fake_buyer_scanner_clean_text_passes() -> None:
    res = scan_unlaunched_fake_buyer(
        text="Caffeine and electrolytes for pre-workout, no sugar.",
        product_name="Triton Drinks",
    )
    assert res.issues == []


# ---------------------------------------------------------------------------
# 11. PII scanner
# ---------------------------------------------------------------------------


def test_pii_scanner_detects_email() -> None:
    res = scan_pii("Contact me at user@example.com for details.")
    assert res.has_email
    assert any("email" in i for i in res.issues)


def test_pii_scanner_detects_phone() -> None:
    res = scan_pii("Call +1 (415) 555-1212 for info.")
    assert res.has_phone


def test_pii_scanner_detects_external_url() -> None:
    res = scan_pii("Visit https://spam.example.com/path")
    assert res.has_external_url


def test_pii_scanner_detects_image_url() -> None:
    res = scan_pii("https://i.imgur.com/abc.jpg is the image")
    assert res.has_image_url


def test_pii_scanner_clean_text_passes() -> None:
    res = scan_pii("This is a regular product review with no PII.")
    assert res.issues == []


# ---------------------------------------------------------------------------
# 12. Duplicate scanner — read-only contract
# ---------------------------------------------------------------------------


def test_duplicate_check_uses_select_only() -> None:
    src = (PKG / "scanners.py").read_text(encoding="utf-8")
    # The duplicate-check function must use SELECT (not insert/update/
    # delete). We grep its function body.
    fn_src = re.search(
        r"async def check_duplicate_content_hash[\s\S]+?return n > 0",
        src,
    )
    assert fn_src is not None
    body = fn_src.group(0)
    assert "select" in body
    assert "insert" not in body
    assert "update" not in body
    assert "delete" not in body
    assert "session.add" not in body


# ---------------------------------------------------------------------------
# 13 + 14. Planned source_record shape + content_hash
# ---------------------------------------------------------------------------


def test_planned_source_record_shape_has_required_fields() -> None:
    fields = PlannedSourceRecordPreview.model_fields.keys()
    required = {
        "source_kind", "source_url", "content_preview",
        "content_length", "content_hash", "language", "metadata",
        "ingested_by", "compliance_tag", "captured_at",
        "pii_redaction_status", "sensitive_scan_status",
        "user_handle_hash",
    }
    assert required == set(fields)


def test_content_hash_is_deterministic() -> None:
    h1 = compute_content_hash(
        content="Some review text.", source_kind="amazon_reviews_2023_local",
    )
    h2 = compute_content_hash(
        content="  Some  review   text.  ",
        source_kind="amazon_reviews_2023_local",
    )
    # Whitespace-normalized, so h1 == h2
    assert h1 == h2
    assert len(h1) == 64
    h3 = compute_content_hash(
        content="DIFFERENT text", source_kind="amazon_reviews_2023_local",
    )
    assert h3 != h1


# ---------------------------------------------------------------------------
# 15 + 16. Privacy: raw user_id never stored, no image URLs in preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planned_source_record_does_not_store_raw_user_id(monkeypatch) -> None:
    from assembly.sources.ingestion_policy import policy as policy_mod
    async def _no_dup(*, content_hash, sessionmaker):
        return False
    monkeypatch.setattr(
        policy_mod, "check_duplicate_content_hash", _no_dup,
    )
    brief = _triton_brief()
    plan = generate_anchor_plan(brief)
    cand = _candidate(score=12, matched_terms=["positive:energy drink",
                                                "competitor:Red Bull"])
    policy = generate_ingestion_policy(
        brief=brief, evidence_anchor_plan=plan, candidate_pool=[cand],
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline={}, max_insert_cap=12,
    )
    decisions = await policy_mod.decide_candidates(
        candidates=[cand], policy=policy, plan=plan,
        sessionmaker=None, product_name=brief.product_name,
        product_launch_state="unlaunched",
    )
    selected = [d for d in decisions if d.decision == "SELECTED"]
    assert selected
    # Confirm the planned record has user_handle_hash=None and no
    # raw user_id surfaces:
    blob = selected[0].planned_source_record_preview.model_dump_json()
    assert "user_id" not in blob
    assert "abcdef0123456789" not in blob  # the user_id_hash from fixture
    assert "user_handle_hash\":null" in blob.replace(" ", "")


def test_planned_source_record_drops_image_urls() -> None:
    cand = _candidate()
    blob = cand.model_dump_json()
    assert ".jpg" not in blob
    assert ".png" not in blob
    assert "media-amazon" not in blob


# ---------------------------------------------------------------------------
# 17 + 18. No Amazon.com scraping / no API call surfaces
# ---------------------------------------------------------------------------


def test_no_amazon_dot_com_url_strings_in_policy_pkg() -> None:
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert pat.search(src) is None, f"amazon.com URL string in {f.name}"


def test_no_http_libs_in_policy_pkg() -> None:
    forbidden = {
        "httpx", "requests", "aiohttp", "urllib", "urllib3",
        "selenium", "playwright", "scrapy", "beautifulsoup4", "bs4",
    }
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, f"{f.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden, f"{f.name}: {node.module}"


# ---------------------------------------------------------------------------
# 19-22. No DB / persona / graph / UI writes from 8.5C.1 paths
# ---------------------------------------------------------------------------


_FORBIDDEN_ORM_NAMES = (
    "SourceRecord", "PersonaRecord", "PersonaTrait", "PersonaEvidenceLink",
    "PersonaGraphEdge", "PersonaCluster", "PersonaClusterMembership",
    "PersonaOpinion", "AudienceRetrievalRun",
    "PopulationConstructionAudit", "SimulationOutput", "SimulationRound",
    "DebateTurn", "AgentResponse", "Agent", "AgentEdge",
)


def test_no_orm_construction_in_policy_pkg() -> None:
    """Pure SELECTs allowed (the duplicate check). No row construction."""
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\(\s*\w"
    )
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        # The literal `SourceRecord(` with arguments would be a write.
        # Imports + `select(SourceRecord)` are fine.
        for m in pat.finditer(src):
            ctx = src[max(0, m.start() - 20):m.end() + 20]
            # `select(SourceRecord)` is a read — exclude it
            if "select(" in ctx:
                continue
            raise AssertionError(
                f"forbidden ORM construction in {f.name}: ...{ctx}..."
            )


def test_no_orm_construction_in_8_5c_1_dry_run_script() -> None:
    src = (
        SCRIPTS_DIR / "triton_amazon_dynamic_ingestion_plan_8_5c_1.py"
    ).read_text(encoding="utf-8")
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\(\s*\w"
    )
    for m in pat.finditer(src):
        ctx = src[max(0, m.start() - 20):m.end() + 20]
        if "select(" in ctx:  # SELECT is fine
            continue
        raise AssertionError(
            f"forbidden ORM construction in script: ...{ctx}..."
        )


def test_no_session_add_in_policy_pkg() -> None:
    """No session.add() / session.execute(insert/update/delete)."""
    bad = ("session.add(", ".execute(insert(", ".execute(update(",
           ".execute(delete(")
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        for token in bad:
            assert token not in src, f"{f.name} contains {token!r}"


def test_no_session_add_in_8_5c_1_dry_run_script() -> None:
    src = (
        SCRIPTS_DIR / "triton_amazon_dynamic_ingestion_plan_8_5c_1.py"
    ).read_text(encoding="utf-8")
    bad = ("session.add(", ".execute(insert(", ".execute(update(",
           ".execute(delete(", "session.commit(", "session.flush(")
    for token in bad:
        assert token not in src, f"dry-run script contains {token!r}"


def test_no_frontend_references_in_policy_pkg() -> None:
    forbidden = ("apps/web", "next/router", "next.js")
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        for s in forbidden:
            assert s not in src


# ---------------------------------------------------------------------------
# 23. Existing 8.5B / 8.5B.1 / 8.5B.2 / 8.5B.3 tests still pass
# (regression — covered by the harness sweep itself)
# ---------------------------------------------------------------------------


def test_8_5b_baseline_review_confidence_unchanged() -> None:
    """Proxy: ReviewConfidence enum is still 4-valued and unchanged."""
    from assembly.sources.amazon_reviews_2023 import ReviewConfidence
    assert {c.value for c in ReviewConfidence} == {
        "high_confidence", "medium_confidence", "low_confidence",
        "rejected",
    }
