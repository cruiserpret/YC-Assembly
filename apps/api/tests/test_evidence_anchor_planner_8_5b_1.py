"""Phase 8.5B.1 — dynamic-anchor-planner tests.

Operator scenarios covered (23 total — see report for the numbered
list). Pure deterministic tests over the planner + scorer. No live
LLM, no network, no DB.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from assembly.sources.amazon_reviews_2023.adapter import (
    AmazonReviewRecord,
)
from assembly.sources.amazon_reviews_2023.filters import (
    AmazonProductMetadata, ReviewConfidence,
)
from assembly.sources.evidence_anchor_planner import (
    AmbiguousEntity, EvidenceAnchorPlan, MetadataRelevanceRule,
    ProductBriefForPlanning,
    UNIVERSAL_AMBIGUITY_CONTEXTS,
    UNIVERSAL_GENERIC_MODIFIERS,
    UNIVERSAL_STOPWORDS,
    generate_anchor_plan, score_review_with_plan,
)


PLANNER_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "evidence_anchor_planner"
)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


# ---------------------------------------------------------------------------
# Fixture briefs — Triton (regression) + Solara (generalization)
# ---------------------------------------------------------------------------


def _triton_brief() -> ProductBriefForPlanning:
    return ProductBriefForPlanning(
        product_name="Triton Drinks",
        product_description=(
            "A caffeinated sports and energy drink positioned for "
            "students, gym users, athletes, and busy young adults who "
            "want energy for studying, workouts, alertness, and "
            "performance. Substitutes considered in scope: cold brew, "
            "coffee, pre-workout powders, electrolyte drinks. Triton is "
            "unlaunched."
        ),
        price_or_price_structure="$3.99 per can",
        launch_geography="California, United States",
        target_customers=[
            "college students", "athletes", "gym-goers",
            "busy young adults",
        ],
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
    )


def _solara_brief() -> ProductBriefForPlanning:
    return ProductBriefForPlanning(
        product_name="Solara Shield",
        product_description=(
            "A portable mineral sunscreen stick designed for "
            "acne-prone college students and outdoor athletes who want "
            "daily face protection they can reapply during school, "
            "workouts, hikes, and outdoor sports without feeling greasy "
            "or causing breakouts."
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


# ---------------------------------------------------------------------------
# 1. Schema exists and is closed
# ---------------------------------------------------------------------------


def test_evidence_anchor_plan_schema_exists_with_required_fields() -> None:
    fields = EvidenceAnchorPlan.model_fields.keys()
    required = {
        "product_name", "product_type", "launch_geography",
        "target_customers", "competitors", "substitutes",
        "positive_anchor_terms", "competitor_anchor_terms",
        "substitute_anchor_terms", "use_case_anchor_terms",
        "objection_anchor_terms", "generic_modifier_terms",
        "ambiguous_entities", "negative_context_terms",
        "metadata_relevance_rules", "generated_from",
        "caveats", "plan_id", "generated_at",
    }
    assert required.issubset(set(fields))


def test_brief_schema_forbids_unexpected_extra_fields() -> None:
    # extra='forbid' enforced
    import pydantic
    try:
        ProductBriefForPlanning(
            product_name="X",
            product_description="some description here",
            unexpected="boom",  # type: ignore[call-arg]
        )
    except pydantic.ValidationError as e:
        assert "unexpected" in str(e).lower() or "extra" in str(e).lower()
    else:
        raise AssertionError("expected ValidationError on unknown field")


# ---------------------------------------------------------------------------
# 2 + 3. Planner takes only founder-style input; no manual category anchors
# ---------------------------------------------------------------------------


def test_planner_signature_takes_only_brief() -> None:
    import inspect
    sig = inspect.signature(generate_anchor_plan)
    # Single positional arg, no manual-anchor or hint kwarg.
    params = sig.parameters
    assert list(params.keys()) == ["brief"]


def test_planner_output_is_self_contained_no_external_lists_required() -> None:
    plan = generate_anchor_plan(_triton_brief())
    # Plan can be passed to score_review_with_plan with NO additional
    # arguments beyond the review + metadata; no global lookup tables
    # are pulled in at score time.
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="Caffeine kick", text="Caffeine and electrolyte hit.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert isinstance(score.confidence, ReviewConfidence)


# ---------------------------------------------------------------------------
# 4 + 5. Triton + Solara plans generated from briefs
# ---------------------------------------------------------------------------


def test_triton_plan_is_derived_from_brief_not_hardcoded() -> None:
    plan = generate_anchor_plan(_triton_brief())
    # Triton's brief mentions "energy drink" and "sports" — those
    # should appear in the plan even though no global energy-drink
    # constants exist in the planner.
    pos_blob = " ".join(plan.positive_anchor_terms).lower()
    assert "energy" in pos_blob
    assert "drink" in pos_blob or "energy drink" in pos_blob
    # The competitor list comes directly from the brief
    assert "Red Bull" in plan.competitor_anchor_terms
    assert "Prime" in plan.competitor_anchor_terms


def test_solara_plan_is_derived_from_brief_not_hardcoded() -> None:
    plan = generate_anchor_plan(_solara_brief())
    # Solara's brief mentions "sunscreen stick" and "mineral" —
    # those should appear in the plan even though no global sunscreen
    # constants exist in the planner.
    pos_blob = " ".join(plan.positive_anchor_terms).lower()
    assert "sunscreen" in pos_blob
    assert "mineral" in pos_blob or "stick" in pos_blob
    # Solara competitors echoed
    assert "Supergoop" in plan.competitor_anchor_terms
    assert "Neutrogena" in plan.competitor_anchor_terms
    # Generic modifiers list is universal
    assert "flavor" in plan.generic_modifier_terms
    assert "price" in plan.generic_modifier_terms


# ---------------------------------------------------------------------------
# 6. Triton and Solara plans are meaningfully different
# ---------------------------------------------------------------------------


def test_triton_and_solara_plans_diverge_meaningfully() -> None:
    t = generate_anchor_plan(_triton_brief())
    s = generate_anchor_plan(_solara_brief())
    t_pos = set(t.positive_anchor_terms)
    s_pos = set(s.positive_anchor_terms)
    # Each plan must have product-specific anchors.
    assert any("drink" in x or "energy" in x for x in t_pos)
    assert any("sunscreen" in x or "mineral" in x or "stick" in x for x in s_pos)
    # Symmetric cross-contamination check: Triton plan must NOT
    # contain sunscreen anchors, Solara plan must NOT contain energy
    # drink anchors.
    triton_blob = " ".join(t.positive_anchor_terms).lower()
    solara_blob = " ".join(s.positive_anchor_terms).lower()
    assert "sunscreen" not in triton_blob
    assert "mineral" not in triton_blob
    assert "energy drink" not in solara_blob
    assert "caffeine" not in solara_blob
    # Plan IDs differ (different briefs → different sha256-derived IDs)
    assert t.plan_id != s.plan_id


# ---------------------------------------------------------------------------
# 7 + 8. Generic modifiers — never alone, only with brief anchors
# ---------------------------------------------------------------------------


def test_generic_modifier_alone_does_not_qualify_review() -> None:
    plan = generate_anchor_plan(_triton_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="Tasty seasoning",
        text="The flavor of this seasoning is great. Worth the price.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert score.confidence is ReviewConfidence.REJECTED


def test_generic_modifier_with_brief_anchor_qualifies() -> None:
    plan = generate_anchor_plan(_triton_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="Decent caffeine",
        text="Has good flavor and the caffeine hits hard for workouts.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    # Caffeine + workouts = brief anchors; flavor → +1
    assert score.confidence in (
        ReviewConfidence.MEDIUM_CONFIDENCE,
        ReviewConfidence.HIGH_CONFIDENCE,
    )
    assert "generic_modifier (qualified)" in score.matched_terms


# ---------------------------------------------------------------------------
# 9 + 10. Ambiguous entity resolver
# ---------------------------------------------------------------------------


def test_planner_flags_prime_as_ambiguous_for_triton() -> None:
    plan = generate_anchor_plan(_triton_brief())
    entities = [a.entity.lower() for a in plan.ambiguous_entities]
    # "Prime" is short + appears in shipping_commerce + streaming_video
    # ambiguity-context lexicon → must be flagged.
    assert "prime" in entities
    prime_amb = next(
        a for a in plan.ambiguous_entities if a.entity.lower() == "prime"
    )
    # intended_sense_phrases include drink-context constructions
    intended_blob = " ".join(prime_amb.intended_sense_phrases).lower()
    assert "drink" in intended_blob or "energy" in intended_blob or "sports" in intended_blob
    # wrong_sense_phrases include shipping-context phrases
    wrong_blob = " ".join(prime_amb.wrong_sense_phrases).lower()
    assert "amazon prime" in wrong_blob or "prime shipping" in wrong_blob


def test_wrong_context_prime_review_is_rejected() -> None:
    plan = generate_anchor_plan(_triton_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="yoga prop blocks",
        text=(
            "Lightweight yoga prop blocks. Arrived quickly "
            "(I have prime) and in excellent condition."
        ),
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert score.confidence is ReviewConfidence.REJECTED


def test_intended_context_prime_review_is_accepted() -> None:
    plan = generate_anchor_plan(_triton_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="Prime Energy tropical was great",
        text=(
            "Prime Energy in tropical punch flavor. Has caffeine and "
            "zero sugar. Better than Red Bull for me before workouts."
        ),
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert score.confidence in (
        ReviewConfidence.HIGH_CONFIDENCE,
        ReviewConfidence.MEDIUM_CONFIDENCE,
    )


# ---------------------------------------------------------------------------
# 11 + 12. Metadata join + missing-metadata handling
# ---------------------------------------------------------------------------


def test_metadata_join_validates_relevance_via_categories() -> None:
    plan = generate_anchor_plan(_solara_brief())
    rec = AmazonReviewRecord(
        category="Health_and_Household",
        parent_asin="ASIN1", asin="ASIN1", rating=5.0,
        title="Reapply during hikes",
        text="Reapply during hikes — does not feel greasy and no breakouts.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    meta = AmazonProductMetadata(
        parent_asin="ASIN1",
        title="Mineral Sunscreen Stick SPF 50",
        store="StoreX",
        main_category="Beauty & Personal Care",
        categories=("Beauty & Personal Care", "Sun Care", "Sunscreens",
                    "Mineral Sunscreen"),
    )
    score = score_review_with_plan(review=rec, metadata=meta, plan=plan)
    assert score.has_metadata is True
    assert score.confidence in (
        ReviewConfidence.HIGH_CONFIDENCE,
        ReviewConfidence.MEDIUM_CONFIDENCE,
    )


def test_missing_metadata_does_not_crash() -> None:
    plan = generate_anchor_plan(_triton_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="t", text="The caffeine and electrolyte mix is solid.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    s = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert s.has_metadata is False
    # Determinism
    s2 = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert s == s2


# ---------------------------------------------------------------------------
# 13. Confidence labels deterministic + 4-bucket
# ---------------------------------------------------------------------------


def test_confidence_label_set_unchanged() -> None:
    assert {c.value for c in ReviewConfidence} == {
        "high_confidence", "medium_confidence",
        "low_confidence", "rejected",
    }


def test_planner_output_is_deterministic() -> None:
    p1 = generate_anchor_plan(_triton_brief())
    p2 = generate_anchor_plan(_triton_brief())
    # Compare every field except `generated_at` (ISO timestamp)
    d1 = p1.model_dump(exclude={"generated_at"})
    d2 = p2.model_dump(exclude={"generated_at"})
    assert d1 == d2
    # Plan ID must also be stable
    assert p1.plan_id == p2.plan_id


# ---------------------------------------------------------------------------
# 14 + 15. Privacy: raw user_id not stored, image URLs not surfaced
# ---------------------------------------------------------------------------


def test_planner_output_contains_no_image_or_user_id() -> None:
    plan = generate_anchor_plan(_triton_brief())
    blob = plan.model_dump_json()
    assert "media-amazon" not in blob
    assert "user_id" not in blob
    assert ".jpg" not in blob


def test_scorer_output_does_not_leak_pii() -> None:
    plan = generate_anchor_plan(_triton_brief())
    rec = AmazonReviewRecord(
        category="x",
        parent_asin="B0", asin="B0", rating=5.0,
        title="caffeine review",
        text="The caffeine hit is solid and the flavor is fine.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abcdef0123456789",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    blob = repr(score)
    # Hash never embedded in the score detail
    assert "abcdef0123456789" not in blob


# ---------------------------------------------------------------------------
# 16. No Amazon.com scraping code
# ---------------------------------------------------------------------------


def test_no_amazon_dot_com_url_strings_in_planner_pkg() -> None:
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    for f in PLANNER_PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert pat.search(text) is None, f"amazon.com URL string in {f.name}"


def test_no_http_libs_in_planner_pkg() -> None:
    forbidden = {
        "httpx", "requests", "aiohttp", "urllib", "urllib3",
        "selenium", "playwright", "scrapy",
        "beautifulsoup4", "bs4",
        "yt_dlp", "youtube_dl", "pytube", "scrapetube",
    }
    for f in PLANNER_PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, f"{f.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden, f"{f.name}: {node.module}"


# ---------------------------------------------------------------------------
# 17-20. No DB / persona / graph / UI writes
# ---------------------------------------------------------------------------


_FORBIDDEN_ORM_NAMES = (
    "SourceRecord", "PersonaRecord", "PersonaTrait", "PersonaEvidenceLink",
    "PersonaGraphEdge", "PersonaCluster", "PersonaClusterMembership",
    "PersonaOpinion", "AudienceRetrievalRun",
    "PopulationConstructionAudit", "SimulationOutput", "SimulationRound",
    "DebateTurn", "AgentResponse", "Agent", "AgentEdge",
)


def test_no_orm_construction_in_planner_pkg() -> None:
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\("
    )
    for f in PLANNER_PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert pat.search(text) is None, f"forbidden ORM construction in {f.name}"


def test_no_orm_construction_in_8_5b_1_preflight_script() -> None:
    src = (
        SCRIPTS_DIR / "amazon_reviews_2023_preflight_8_5b_1_dynamic.py"
    ).read_text(encoding="utf-8")
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\("
    )
    assert pat.search(src) is None


def test_no_frontend_references_in_planner_pkg() -> None:
    forbidden = ("apps/web", "next/router", "next.js")
    for f in PLANNER_PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for s in forbidden:
            assert s not in text, f"{f.name}: {s}"


# ---------------------------------------------------------------------------
# 21. Existing 8.5B baseline tests still pass — proxy: 8.5B scorer
# constants unchanged.
# ---------------------------------------------------------------------------


def test_8_5b_baseline_scorer_constants_unchanged() -> None:
    """The Phase 8.5B scorer constants (`ReviewConfidence`,
    `PrimeContext`) are still exported — Phase 8.5B.1 reuses them
    without modification."""
    from assembly.sources.amazon_reviews_2023.filters import (
        PrimeContext, ReviewConfidence as RC85B,
    )
    assert {c.value for c in RC85B} == {
        "high_confidence", "medium_confidence",
        "low_confidence", "rejected",
    }
    assert {c.value for c in PrimeContext} == {
        "drink", "shipping", "ambiguous",
    }


# ---------------------------------------------------------------------------
# Bonus: universal lexicons are non-empty and product-agnostic
# ---------------------------------------------------------------------------


def test_universal_lexicons_are_non_empty_and_product_agnostic() -> None:
    # Generic modifiers contain "flavor" / "price" / "quality"
    assert "flavor" in UNIVERSAL_GENERIC_MODIFIERS
    assert "price" in UNIVERSAL_GENERIC_MODIFIERS
    assert "quality" in UNIVERSAL_GENERIC_MODIFIERS
    # Stopwords contain "the", "a", "and"
    assert "the" in UNIVERSAL_STOPWORDS
    assert "and" in UNIVERSAL_STOPWORDS
    # Ambiguity contexts cover commerce + media + tech
    assert "shipping_commerce" in UNIVERSAL_AMBIGUITY_CONTEXTS
    assert any(
        "amazon prime" in p
        for p in UNIVERSAL_AMBIGUITY_CONTEXTS["shipping_commerce"]
    )


def test_negative_context_terms_seeded_from_ambiguous_entities() -> None:
    plan = generate_anchor_plan(_triton_brief())
    if plan.ambiguous_entities:
        # Plan's negative_context_terms is the union of every
        # ambiguous entity's wrong_sense_phrases
        ambiguous_pool: set[str] = set()
        for a in plan.ambiguous_entities:
            ambiguous_pool.update(a.wrong_sense_phrases)
        assert set(plan.negative_context_terms) == ambiguous_pool


# ---------------------------------------------------------------------------
# Phase 10B.3 hotfix: pasted-list noise sanitization
# ---------------------------------------------------------------------------


def test_competitor_anchors_strip_leading_numeric_prefixes() -> None:
    """Founders frequently paste competitor lists from numbered docs.
    Without sanitation, an entry "1. Samsung Family Hub" becomes a
    literal anchor that no review snippet ever matches."""
    from assembly.sources.evidence_anchor_planner.planner import (
        _build_competitor_anchors, _strip_user_listing_prefix,
    )
    assert _strip_user_listing_prefix("1. Samsung Family Hub") == "Samsung Family Hub"
    assert _strip_user_listing_prefix("(2) FridgeCam") == "FridgeCam"
    assert _strip_user_listing_prefix("3) AnyList") == "AnyList"
    assert _strip_user_listing_prefix("4- HotLogic") == "HotLogic"
    out = _build_competitor_anchors(["1. Samsung Family Hub", "2. FridgeCam"])
    assert "Samsung Family Hub" in out
    assert "FridgeCam" in out
    assert "1. Samsung Family Hub" not in out


def test_use_case_anchors_strip_conjunction_prefixes_and_filter_fragments() -> None:
    """target_customers pasted from a paragraph gets split on commas
    and yields fragments like "or accidentally buy duplicates" — the
    builder must skip those, not anchor on them."""
    from assembly.sources.evidence_anchor_planner.planner import (
        _build_use_case_anchors,
    )
    from assembly.sources.evidence_anchor_planner.schemas import (
        ProductBriefForPlanning,
    )
    brief = ProductBriefForPlanning(
        product_name="Foo",
        product_description="Foo is a useful tool for kitchens.",
        launch_geography="USA",
        target_customers=[
            "urban renters",
            "and households that waste groceries",
            "or accidentally buy duplicates",
            "forget leftovers",
        ],
        competitors=[],
        optional_constraints=[],
    )
    uc = _build_use_case_anchors(brief)
    assert "urban renters" in uc
    assert any("households that waste groceries" in x for x in uc)
    assert not any("accidentally" in x for x in uc)
    assert not any("forget leftovers" in x for x in uc)

