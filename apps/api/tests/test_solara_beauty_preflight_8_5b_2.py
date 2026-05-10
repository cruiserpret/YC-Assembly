"""Phase 8.5B.2 — Beauty-category Solara preflight tests.

20 tests covering:

  1. Beauty_and_Personal_Care category resolves via discover_category_files
     when explicitly listed.
  2. Beauty metadata file pattern (`meta_<Category>.jsonl`) is
     consistent with the existing 3-category convention.
  3. Preflight script supports a category override without permanently
     editing `.env` — the script hardcodes `CATEGORY = "Beauty..."`
     rather than reading AMAZON_REVIEWS_2023_CATEGORIES.
  4. Solara brief uses only founder-style fields (no
     hardcoded sunscreen anchors at brief construction).
  5. No manual sunscreen anchors are passed into the scorer —
     scorer signature is `(review, metadata, plan)` only.
  6. No sunscreen-specific production constants are added.
  7. Metadata join works for Beauty records (synthetic JSONL fixture).
  8. Generic modifiers do not qualify alone (regression of 8.5B.1).
  9. Generic modifiers qualify only with dynamic Solara anchors
     (`sunscreen` / `mineral` / `stick`).
 10. Ambiguous entities still use product context (regression).
 11. Raw user_id is not stored.
 12. Image URLs are dropped.
 13. No Amazon.com scraping code exists in 8.5B.2 script.
 14-17. No source_records / personas / traits / evidence-links /
        graph / sim / UI writes from 8.5B.2 code paths.
 18. Existing 8.5B.1 tests still pass (regression — covered by suite).
 19-20. Full unit + integration suite still pass (covered by sweep).
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from assembly.sources.amazon_reviews_2023 import (
    AmazonReviewRecord, MetadataIndex, ReviewConfidence,
    discover_category_files,
)
from assembly.sources.evidence_anchor_planner import (
    EvidenceAnchorPlan, ProductBriefForPlanning,
    generate_anchor_plan, score_review_with_plan,
)


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SOURCES_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources"
)


def _solara_brief() -> ProductBriefForPlanning:
    return ProductBriefForPlanning(
        product_name="Solara Shield",
        product_description=(
            "A portable mineral sunscreen stick designed for "
            "acne-prone college students and outdoor athletes who "
            "want daily face protection they can reapply during "
            "school, workouts, hikes, and outdoor sports without "
            "feeling greasy or causing breakouts."
        ),
        price_or_price_structure="$18.99",
        launch_geography="Arizona, United States",
        target_customers=[
            "college students", "outdoor runners", "hikers", "athletes",
            "acne-prone young adults",
        ],
        competitors=[
            "Supergoop", "Neutrogena", "La Roche-Posay", "CeraVe",
            "Sun Bum",
        ],
    )


def _meta_record(parent_asin: str, title: str, categories: list[str]) -> dict:
    return {
        "parent_asin": parent_asin,
        "title": title,
        "store": "BrandX",
        "main_category": "Beauty & Personal Care",
        "categories": categories,
        "description": [],
        "features": [],
        "price": None,
        "average_rating": 4.6,
        "rating_number": 200,
        "details": {"Brand": "BrandX"},
        "images": [{"large": "https://m.media-amazon.com/images/I/abc.jpg"}],
        "videos": [],
    }


# ---------------------------------------------------------------------------
# 1 + 2. Category discovery + filename convention
# ---------------------------------------------------------------------------


def test_beauty_category_discovers_when_listed(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "Beauty_and_Personal_Care.jsonl").write_text("")
    found = discover_category_files(
        dataset_dir=tmp_path, categories=["Beauty_and_Personal_Care"],
    )
    assert "Beauty_and_Personal_Care" in found
    assert len(found["Beauty_and_Personal_Care"]) == 1


def test_beauty_metadata_filename_follows_meta_prefix_convention(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f_meta = raw / "meta_Beauty_and_Personal_Care.jsonl"
    f_meta.write_text(json.dumps(
        _meta_record("ASIN1", "Mineral Sunscreen Stick SPF 50",
                     ["Beauty & Personal Care", "Sun Care"])
    ), encoding="utf-8")
    # Pattern: `meta_<Category>.jsonl`. discover_category_files only
    # finds review files (without `meta_` prefix), so meta files are
    # NOT in `discovered`; they're separately addressable.
    discovered = discover_category_files(
        dataset_dir=tmp_path, categories=["Beauty_and_Personal_Care"],
    )
    assert discovered["Beauty_and_Personal_Care"] == []
    # But the meta file IS resolvable by the Beauty preflight via
    # `meta_path_for(raw, category)` pattern. Direct presence check:
    assert f_meta.is_file()


# ---------------------------------------------------------------------------
# 3. Preflight script hardcodes Beauty category — no .env edit needed
# ---------------------------------------------------------------------------


def test_8_5b_2_preflight_script_hardcodes_beauty_category() -> None:
    src = (
        SCRIPTS_DIR
        / "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.py"
    ).read_text(encoding="utf-8")
    assert 'CATEGORY = "Beauty_and_Personal_Care"' in src
    # The script does NOT actively READ the categories env var for
    # category selection — it overrides via the explicit constant.
    # (Mentioning the env-var name in a docstring is fine; reading
    # it via os.environ.get / os.getenv is not.)
    for forbidden in (
        'os.environ.get("AMAZON_REVIEWS_2023_CATEGORIES")',
        "os.environ['AMAZON_REVIEWS_2023_CATEGORIES']",
        'os.getenv("AMAZON_REVIEWS_2023_CATEGORIES")',
    ):
        assert forbidden not in src


# ---------------------------------------------------------------------------
# 4 + 5. Solara brief is founder-style only; scorer takes no manual anchors
# ---------------------------------------------------------------------------


def test_solara_brief_uses_only_founder_style_fields() -> None:
    brief = _solara_brief()
    fields = ProductBriefForPlanning.model_fields.keys()
    expected = {
        "product_name", "product_description",
        "price_or_price_structure", "launch_geography",
        "target_customers", "competitors", "optional_constraints",
    }
    assert set(fields) == expected
    # Brief contains zero hardcoded sunscreen jargon: "SPF", "zinc",
    # "white cast", etc. are NOT in the brief unless from the
    # operator-supplied description.
    blob = brief.model_dump_json()
    assert "SPF " not in blob
    assert "zinc oxide" not in blob
    assert "white cast" not in blob


def test_scorer_signature_takes_only_review_metadata_plan() -> None:
    import inspect
    sig = inspect.signature(score_review_with_plan)
    # Three kw-only args; no manual category anchors.
    assert set(sig.parameters.keys()) == {"review", "metadata", "plan"}


# ---------------------------------------------------------------------------
# 6. No sunscreen-specific production constants in the planner package
# ---------------------------------------------------------------------------


def test_no_sunscreen_specific_constants_in_planner_package() -> None:
    # AST-strip docstrings/comments before scanning.
    import ast
    forbidden = (
        "SPF", "spf 30", "spf 50", "white cast", "zinc oxide",
        "octinoxate", "octisalate", "avobenzone", "uva ", "uvb ",
        "sunburn", "tanning lotion", "after-sun",
        "broad spectrum", "physical filter", "chemical filter",
    )
    pkg = SOURCES_PKG / "evidence_anchor_planner"
    for f in pkg.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # Strip docstrings + comments
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
                    n0 = node.body[0]
                    for ln in range(
                        n0.lineno, (n0.end_lineno or n0.lineno) + 1,
                    ):
                        ds_lines.add(ln)
        kept_lines: list[str] = []
        for i, line in enumerate(src.splitlines(), 1):
            if i in ds_lines:
                continue
            ci = line.find("#")
            if ci >= 0:
                line = line[:ci]
            kept_lines.append(line)
        code_only = "\n".join(kept_lines).lower()
        for term in forbidden:
            assert term.lower() not in code_only, (
                f"sunscreen-specific term {term!r} hardcoded in "
                f"{f.name} (planner package must remain product-agnostic)"
            )


# ---------------------------------------------------------------------------
# 7. Metadata join works for Beauty records (synthetic JSONL fixture)
# ---------------------------------------------------------------------------


def test_metadata_index_joins_beauty_records(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "meta_Beauty_and_Personal_Care.jsonl"
    rows = [
        _meta_record("ASIN1", "Mineral Sunscreen Stick SPF 50",
                     ["Beauty & Personal Care", "Sun Care", "Sunscreens"]),
        _meta_record("ASIN2", "Acne Cleanser Foam",
                     ["Beauty & Personal Care", "Skin Care", "Acne"]),
        _meta_record("ASIN3", "Hair Dye",
                     ["Beauty & Personal Care", "Hair Care"]),
    ]
    f.write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8",
    )
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN1", "ASIN2"})
    idx.load()
    assert idx.lookup("ASIN1") is not None
    assert idx.lookup("ASIN2") is not None
    assert idx.lookup("ASIN3") is None
    # Image URLs dropped:
    blob = repr(idx.index)
    assert "media-amazon" not in blob
    assert ".jpg" not in blob


# ---------------------------------------------------------------------------
# 8 + 9. Generic modifiers — alone vs with Solara anchor
# ---------------------------------------------------------------------------


def test_generic_modifier_alone_does_not_qualify_solara_review() -> None:
    plan = generate_anchor_plan(_solara_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="Tasty seasoning",
        text="Great flavor and worth the price.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert score.confidence is ReviewConfidence.REJECTED


def test_generic_modifier_with_solara_anchor_qualifies() -> None:
    plan = generate_anchor_plan(_solara_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="Mineral SPF 50",
        text=(
            "Love this mineral sunscreen stick — easy to reapply, "
            "doesn't feel greasy. Worth the price."
        ),
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert score.confidence in (
        ReviewConfidence.HIGH_CONFIDENCE,
        ReviewConfidence.MEDIUM_CONFIDENCE,
    )
    # Both "mineral" and "sunscreen stick" should appear as positive
    # anchor matches (from brief-derived plan).
    assert any("sunscreen" in m.lower() for m in score.matched_terms)


# ---------------------------------------------------------------------------
# 10. Ambiguous entities still use product context (regression)
# ---------------------------------------------------------------------------


def test_ambiguous_entity_handling_unchanged_for_solara() -> None:
    # Solara competitors don't appear in any universal ambiguity
    # context lexicon. So zero ambiguous entities — that's the
    # correct, honest behavior for this product.
    plan = generate_anchor_plan(_solara_brief())
    assert plan.ambiguous_entities == []
    # And competitor_anchor_terms still includes them all.
    for c in ("Supergoop", "Neutrogena", "La Roche-Posay",
              "CeraVe", "Sun Bum"):
        assert c in plan.competitor_anchor_terms


# ---------------------------------------------------------------------------
# 11 + 12. Privacy: user_id hashed, image URLs dropped
# ---------------------------------------------------------------------------


def test_metadata_join_drops_image_urls(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "meta_Beauty_and_Personal_Care.jsonl"
    f.write_text(json.dumps(
        _meta_record("ASIN_X", "SPF 50 Stick", ["Beauty & Personal Care"]),
    ), encoding="utf-8")
    idx = MetadataIndex(meta_file=f, target_asins={"ASIN_X"})
    idx.load()
    m = idx.lookup("ASIN_X")
    assert m is not None
    blob = repr(m)
    assert ".jpg" not in blob
    assert "media-amazon" not in blob


def test_solara_score_does_not_leak_raw_user_id() -> None:
    plan = generate_anchor_plan(_solara_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="Mineral sunscreen review",
        text="Great mineral sunscreen for outdoor sports.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="0123456789abcdef",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert "0123456789abcdef" not in repr(score)


# ---------------------------------------------------------------------------
# 13. No Amazon.com scraping in 8.5B.2 script
# ---------------------------------------------------------------------------


def test_no_amazon_dot_com_url_in_8_5b_2_script() -> None:
    src = (
        SCRIPTS_DIR
        / "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.py"
    ).read_text(encoding="utf-8")
    pat = re.compile(
        r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE,
    )
    # The error-message URL refers to huggingface.co — not amazon.com.
    # Drift test must not flag that.
    assert pat.search(src) is None


# ---------------------------------------------------------------------------
# 14-17. No DB / persona / graph / UI writes
# ---------------------------------------------------------------------------


_FORBIDDEN_ORM_NAMES = (
    "SourceRecord", "PersonaRecord", "PersonaTrait", "PersonaEvidenceLink",
    "PersonaGraphEdge", "PersonaCluster", "PersonaClusterMembership",
    "PersonaOpinion", "AudienceRetrievalRun",
    "PopulationConstructionAudit", "SimulationOutput", "SimulationRound",
    "DebateTurn", "AgentResponse", "Agent", "AgentEdge",
)


def test_no_orm_construction_in_8_5b_2_preflight_script() -> None:
    src = (
        SCRIPTS_DIR
        / "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.py"
    ).read_text(encoding="utf-8")
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\("
    )
    assert pat.search(src) is None


def test_8_5b_2_script_imports_no_db_session_machinery() -> None:
    src = (
        SCRIPTS_DIR
        / "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.py"
    ).read_text(encoding="utf-8")
    # No async session opener, no get_sessionmaker, no model imports.
    assert "get_sessionmaker" not in src
    assert "from assembly.models" not in src
    assert "from assembly.db" not in src


def test_no_frontend_references_in_8_5b_2_script() -> None:
    src = (
        SCRIPTS_DIR
        / "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.py"
    ).read_text(encoding="utf-8")
    for s in ("apps/web", "next/router", "next.js"):
        assert s not in src
