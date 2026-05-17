"""Phase 11C.2 — local-only Amazon evidence injection scaffold.

Only file outside `apps/api/src/assembly/sources/amazon_reviews_provider/`
that is permitted to import the Phase-11C runtime retriever. The drift
test in `tests/test_amazon_runtime_retrieval_11c_1.py` whitelists this
exact path and asserts no other production file imports the retriever
yet.

What this module does:

  * Builds a sanitized AUDIT-ONLY section describing the Amazon
    retrieval attempt for one product brief.
  * Is double-gated. Both `ASSEMBLY_AMAZON_REVIEWS_ENABLED` AND
    `ASSEMBLY_AMAZON_REVIEWS_RUNTIME_ENABLED` must be True for it
    to touch the DB. Otherwise it returns None (or an audit dict
    that explicitly reports the disabled state, depending on caller
    preference).
  * Never injects rows into `evidence_items`. Persona generation
    sees ZERO Amazon evidence for now. Phase 11C.3+ will decide
    whether/how to feed Amazon signals into personas, gated by
    measured prediction-accuracy improvements (Phase 12).

What this module does NOT do:

  * No persistence to `amazon_review_signal` (read-only).
  * No persistence to `evidence_items` (audit-only output).
  * No live retrieval from McAuley URLs (offline-only — reads the
    table the Phase-11B ingestion populated).
  * No exposure of raw review bodies, user IDs, source_review_hash,
    image URLs, or any DB row id — `RetrievedSignal` already strips
    those.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from assembly.sources.amazon_reviews_provider import (
    AmazonEvidencePackage,
    AmazonSignalRetriever,
    ProductBriefShape,
    RetrievalConfig,
)
from assembly.sources.amazon_reviews_provider.postgres_source import (
    PostgresSignalSource,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from assembly.config import Settings
    from assembly.schemas.brief import SimulationBriefIn


# Number of sample signals to embed in the audit dict so the operator
# can eyeball quality without opening Postgres. Mirrors the Phase-11B
# audit sample size.
_AUDIT_SAMPLE_SIZE = 6


def _brief_to_product_shape(brief: "SimulationBriefIn") -> ProductBriefShape:
    """Adapt the in-pipeline `SimulationBriefIn` to the slim shape
    the retriever expects. Pure projection — no derived fields."""
    competitors: list[str] = []
    for c in brief.competitors:
        # CompetitorRef has a `name` field in current schemas; tolerate
        # plain strings + objects without `.name` defensively.
        name = getattr(c, "name", None) or (
            c.get("name") if isinstance(c, dict) else None
        )
        if isinstance(name, str) and name.strip():
            competitors.append(name.strip())
    # `product_type` doubles as a category hint — the classifier
    # treats it as a coarse text input alongside name + description.
    return ProductBriefShape(
        product_name=brief.product_name,
        description=brief.description,
        category_hint=brief.product_type,
        competitors=tuple(competitors),
    )


def _audit_from_package(
    pkg: AmazonEvidencePackage,
    *,
    config: RetrievalConfig,
) -> dict[str, Any]:
    """Render the Phase-11C.2 audit-section dict per operator spec."""
    return {
        "provider": "amazon_reviews_2023",
        "amazon_attempted": pkg.attempted,
        "amazon_enabled": config.enabled,
        "amazon_runtime_enabled": config.runtime_enabled,
        "amazon_persona_injection_enabled": config.persona_injection_enabled,
        "same_category_only": config.same_category_only,
        "category_matched": pkg.category_matched,
        "signals_retrieved": len(pkg.signals),
        "signal_distribution": dict(pkg.distribution),
        "brand_coverage": round(pkg.brand_coverage, 3),
        "title_coverage": round(pkg.title_coverage, 3),
        "skipped_reasons": dict(pkg.skipped_reasons),
        "notes": list(pkg.notes),
        "sample_signals": [
            {
                "signal_type": s.signal_type,
                "sentiment_bucket": s.sentiment_bucket,
                "theme": s.theme,
                "category": s.category,
                "brand": s.brand,
                "product_title": (
                    s.product_title[:160] + "…"
                    if s.product_title and len(s.product_title) > 160
                    else s.product_title
                ),
                "rating": s.rating,
                "short_snippet": s.short_snippet,
                "competitor_mention": s.competitor_mention,
                "use_case": s.use_case,
                "verified_purchase": s.verified_purchase,
                "helpful_votes": s.helpful_votes,
            }
            for s in pkg.signals[:_AUDIT_SAMPLE_SIZE]
        ],
    }


def _disabled_audit(config: RetrievalConfig) -> dict[str, Any]:
    """Off-state audit dict — never touches the DB. Returned when
    either feature flag is False so callers always get a uniform
    audit shape they can stash in the per-run record."""
    return {
        "provider": "amazon_reviews_2023",
        "amazon_attempted": False,
        "amazon_enabled": config.enabled,
        "amazon_runtime_enabled": config.runtime_enabled,
        "amazon_persona_injection_enabled": config.persona_injection_enabled,
        "same_category_only": config.same_category_only,
        "category_matched": None,
        "signals_retrieved": 0,
        "signal_distribution": {},
        "brand_coverage": 0.0,
        "title_coverage": 0.0,
        "skipped_reasons": {},
        "notes": [
            "feature_flag_off — Amazon retrieval disabled, "
            "no DB read attempted",
        ],
        "sample_signals": [],
    }


async def build_amazon_evidence_section(
    brief: "SimulationBriefIn",
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> dict[str, Any] | None:
    """Run the Amazon retriever for `brief` and return an audit dict,
    or a uniform disabled-state dict when the flags forbid the call.

    The DB session is opened lazily by `PostgresSignalSource` only
    inside its three `fetch_*` methods — there is no connection cost
    when both flags are off.

    Caller is expected to attach the returned dict to whatever
    per-simulation audit structure they already keep. Phase 11C.2
    attaches it to `EvidenceBuildResult.amazon_audit`. Phase 11C.4
    surfaces it inside `founder_report.json` under
    `technical.amazon_reviews_2023`.
    """
    config = RetrievalConfig.from_settings(settings)
    if not config.fully_enabled:
        return _disabled_audit(config)

    source = PostgresSignalSource(sessionmaker)
    retriever = AmazonSignalRetriever(source, config=config)
    pkg = await retriever.retrieve_for_product_brief(
        _brief_to_product_shape(brief),
    )
    return _audit_from_package(pkg, config=config)


async def build_amazon_evidence_section_from_dict_brief(
    brief: dict[str, Any],
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> dict[str, Any]:
    """Phase 11C.4 — variant for callers that already hold the
    founder brief as a plain dict (e.g.
    `AssemblyRun.product_brief`) rather than a Pydantic
    `SimulationBriefIn`. Reads the same fields the founder form
    submits — `product_name`, `product_description`,
    `category_hint`, `competitors_or_alternatives` — and routes
    through the same retriever + audit serializer as the canonical
    helper above. Returns a uniform audit dict in every case (the
    disabled-state dict when flags are off)."""
    config = RetrievalConfig.from_settings(settings)
    if not config.fully_enabled:
        return _disabled_audit(config)

    competitors: list[str] = []
    for c in (brief.get("competitors_or_alternatives") or []):
        if isinstance(c, str) and c.strip():
            competitors.append(c.strip())
        elif isinstance(c, dict):
            name = c.get("name")
            if isinstance(name, str) and name.strip():
                competitors.append(name.strip())

    shape = ProductBriefShape(
        product_name=str(brief.get("product_name") or "").strip(),
        description=str(brief.get("product_description") or "").strip(),
        category_hint=(brief.get("category_hint") or None),
        competitors=tuple(competitors),
    )

    source = PostgresSignalSource(sessionmaker)
    retriever = AmazonSignalRetriever(source, config=config)
    pkg = await retriever.retrieve_for_product_brief(shape)
    return _audit_from_package(pkg, config=config)


# Phase 11C.5 — persona-injection prompt block.
#
# Hard cap and bucket-balance rules below are the production-safety
# contract. They MUST stay tight: every relaxation needs a test pin
# because Amazon is now influencing what personas see in their
# prompts.

# Max snippets in the prompt block, total. 12 is the operator's
# upper bound from the Phase-11C.5 spec.
_PROMPT_BLOCK_MAX_SNIPPETS = 12

# How many snippets per signal_type bucket — keeps one bucket from
# filling the entire 12-slot pool. Round-robins across the
# negative-leaning buckets first because Phase-11B.5 showed
# buyer-objection language is the most useful signal type.
_PROMPT_BLOCK_PER_BUCKET = 2

# Order matters — these buckets get pulled first, biasing the
# persona block toward objection/durability/trust/etc. The two
# positive buckets (praise + use_case) come last so a 12-slot pool
# always leans toward genuine buyer concerns rather than vague
# praise text.
_PROMPT_BLOCK_BUCKET_ORDER: tuple[str, ...] = (
    "objection",
    "durability",
    "price",
    "trust",
    "setup",
    "support",
    "safety",
    "switch_reason",
    "return_reason",
    "use_case",
    "praise",
    "proof_need",
)

# Per-snippet character cap inside the prompt block. Phase 11A
# already capped snippets at 240 chars at distillation time; this
# is a SECOND cap on top, sized for prompt context.
_PROMPT_SNIPPET_CHAR_CAP = 180


def _format_prompt_snippet(s: "RetrievedSignal") -> str:
    """Render one signal as a single bullet line, no raw fields."""
    snippet = (s.short_snippet or "").strip()
    if len(snippet) > _PROMPT_SNIPPET_CHAR_CAP:
        snippet = snippet[: _PROMPT_SNIPPET_CHAR_CAP - 1].rstrip() + "…"
    # Clearly label signal_type so the persona reads it as
    # buyer-language evidence, not authoritative fact.
    sentiment = (s.sentiment_bucket or "").lower()
    return f"- [{s.signal_type}/{sentiment}] {snippet}"


def _balanced_prompt_snippets(
    signals: list["RetrievedSignal"],
) -> list["RetrievedSignal"]:
    """Pick at most `_PROMPT_BLOCK_MAX_SNIPPETS` signals, round-
    robin across `_PROMPT_BLOCK_BUCKET_ORDER`, no more than
    `_PROMPT_BLOCK_PER_BUCKET` per signal_type."""
    by_type: dict[str, list] = {}
    for s in signals:
        by_type.setdefault(s.signal_type, []).append(s)
    picked: list = []
    for bucket in _PROMPT_BLOCK_BUCKET_ORDER:
        if len(picked) >= _PROMPT_BLOCK_MAX_SNIPPETS:
            break
        for s in by_type.get(bucket, [])[:_PROMPT_BLOCK_PER_BUCKET]:
            if len(picked) >= _PROMPT_BLOCK_MAX_SNIPPETS:
                break
            picked.append(s)
    return picked


async def build_amazon_persona_prompt_block(
    brief: dict[str, Any],
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> str | None:
    """Phase 11C.5 — produce a compact, capped Amazon evidence text
    block suitable for injecting into a persona's discussion prompt.

    Returns None when ANY of the three gates is off:
      * amazon_reviews_enabled
      * amazon_reviews_runtime_enabled
      * amazon_reviews_persona_injection_enabled

    The block is hand-formatted — no raw JSON, no row IDs, no
    source_review_hash, no user_id, no images. Each bullet shows
    signal_type, sentiment, and the Phase-11A-capped short_snippet
    (re-capped here to 180 chars). Bucket balance ensures one
    signal type cannot dominate the persona's view of buyer
    concerns.

    Phase 11C.7 — delegates to `build_amazon_persona_prompt_block_with_audit`
    so the expanded 4-pool retrieval + relevance filter is the single
    code path. The audit dict is discarded here; callers that want
    the audit fields should use the `_with_audit` variant directly.
    """
    block, _audit = await build_amazon_persona_prompt_block_with_audit(
        brief, sessionmaker=sessionmaker, settings=settings,
    )
    return block


async def build_amazon_persona_prompt_block_with_audit(
    brief: dict[str, Any],
    *,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> tuple[str | None, dict[str, Any]]:
    """Phase 11C.7 — produce the persona-injection prompt block AND a
    full relevance + candidate-pool audit dict.

    The retrieval flow:
      1. Expanded 4-pool candidate retrieval
         (`retrieve_candidate_pool_for_persona`) — pulls candidates
         from category, product-title-keyword, brand/competitor, and
         signal-type pools, then dedupes.
      2. Deterministic relevance filter (Phase 11C.6).
      3. Bucket-balanced prompt-block builder (Phase 11C.5).

    The audit dict surfaces every count + sample the operator needs
    to judge whether the filter behaved as expected:

        {
            "min_relevance_threshold": float,
            "category_matched": str | None,
            "candidate_pool_size": int,           # sum of all 4 pools
            "category_candidates": int,
            "title_keyword_candidates": int,
            "competitor_brand_candidates": int,
            "signal_type_candidates": int,
            "candidates_after_dedupe": int,
            "title_keywords_used": [str, ...],
            "matched_brands_or_competitors": [str, ...],
            "fallback_used": bool,
            "signals_considered": int,            # == candidates_after_dedupe
            "signals_kept_after_filter": int,
            "signals_rejected_relevance": int,
            "candidates_after_relevance": int,
            "final_snippets": int,
            "avg_relevance_score": float,
            "top_kept_scores": [float, ...],      # up to 5, sorted desc
            "top_rejected_scores": [float, ...],  # up to 5, sorted desc
            "rejection_reasons": {reason: count},
            "sample_rejected_snippets": [
                {signal_type, theme, category, score, drop_reason,
                 short_snippet}, ... up to 4
            ],
            "final_block_distribution": {signal_type: count},
        }

    Returns (block_text_or_None, audit_dict).
    """
    min_relevance = float(
        getattr(settings, "amazon_reviews_persona_min_relevance", 0.20),
    )
    empty_audit: dict[str, Any] = {
        "min_relevance_threshold": min_relevance,
        "category_matched": None,
        "candidate_pool_size": 0,
        "category_candidates": 0,
        "title_keyword_candidates": 0,
        "competitor_brand_candidates": 0,
        "signal_type_candidates": 0,
        "candidates_after_dedupe": 0,
        "title_keywords_used": [],
        "matched_brands_or_competitors": [],
        "fallback_used": False,
        "signals_considered": 0,
        "signals_kept_after_filter": 0,
        "signals_rejected_relevance": 0,
        "candidates_after_relevance": 0,
        "final_snippets": 0,
        "avg_relevance_score": 0.0,
        "top_kept_scores": [],
        "top_rejected_scores": [],
        "rejection_reasons": {},
        "sample_rejected_snippets": [],
        "final_block_distribution": {},
    }
    config = RetrievalConfig.from_settings(settings)
    if not config.fully_enabled:
        return (None, empty_audit)
    if not bool(
        getattr(
            settings, "amazon_reviews_persona_injection_enabled", False,
        ),
    ):
        return (None, empty_audit)

    # Adapt brief dict to the retriever's input shape.
    competitors: list[str] = []
    for c in (brief.get("competitors_or_alternatives") or []):
        if isinstance(c, str) and c.strip():
            competitors.append(c.strip())
        elif isinstance(c, dict):
            name = c.get("name")
            if isinstance(name, str) and name.strip():
                competitors.append(name.strip())
    shape = ProductBriefShape(
        product_name=str(brief.get("product_name") or "").strip(),
        description=str(brief.get("product_description") or "").strip(),
        category_hint=(brief.get("category_hint") or None),
        competitors=tuple(competitors),
    )

    source = PostgresSignalSource(sessionmaker)
    retriever = AmazonSignalRetriever(source, config=config)
    candidates, pool_stats, category_matched = (
        await retriever.retrieve_candidate_pool_for_persona(shape)
    )

    candidate_pool_size = (
        pool_stats.category_candidates
        + pool_stats.title_keyword_candidates
        + pool_stats.competitor_brand_candidates
        + pool_stats.signal_type_candidates
    )
    base_audit: dict[str, Any] = {
        "min_relevance_threshold": min_relevance,
        "category_matched": category_matched,
        "candidate_pool_size": candidate_pool_size,
        "category_candidates": pool_stats.category_candidates,
        "title_keyword_candidates": pool_stats.title_keyword_candidates,
        "competitor_brand_candidates":
            pool_stats.competitor_brand_candidates,
        "signal_type_candidates": pool_stats.signal_type_candidates,
        "candidates_after_dedupe": pool_stats.candidates_after_dedupe,
        "title_keywords_used": list(pool_stats.title_keywords_used),
        "matched_brands_or_competitors": list(
            pool_stats.matched_brands_or_competitors,
        ),
        "fallback_used": pool_stats.fallback_used,
        "signals_considered": len(candidates),
        "signals_kept_after_filter": 0,
        "signals_rejected_relevance": 0,
        "candidates_after_relevance": 0,
        "final_snippets": 0,
        "avg_relevance_score": 0.0,
        "top_kept_scores": [],
        "top_rejected_scores": [],
        "rejection_reasons": {},
        "sample_rejected_snippets": [],
        "final_block_distribution": {},
    }

    if not candidates:
        return (None, base_audit)

    from assembly.sources.amazon_reviews_provider.relevance import (
        filter_signals_by_relevance, score_signal_for_brief,
    )
    kept, rejected = filter_signals_by_relevance(
        list(candidates),
        brief=shape,
        min_score=min_relevance,
    )

    all_scores = [
        score_signal_for_brief(s, brief=shape).total for s in candidates
    ]
    avg = sum(all_scores) / max(len(all_scores), 1)
    kept_scores = sorted(
        (score_signal_for_brief(s, brief=shape).total for s in kept),
        reverse=True,
    )[:5]
    rejected_scores = sorted(
        (score.total for _, score in rejected), reverse=True,
    )[:5]

    rejection_counts: dict[str, int] = {}
    for _s, score in rejected:
        key = score.drop_reason or "below_threshold"
        rejection_counts[key] = rejection_counts.get(key, 0) + 1

    sample_rejected = [
        {
            "signal_type": s.signal_type,
            "theme": s.theme,
            "category": s.category,
            "score": score.total,
            "drop_reason": score.drop_reason,
            "short_snippet": (
                (s.short_snippet or "")[:120]
                + ("…" if len(s.short_snippet or "") > 120 else "")
            ),
        }
        for s, score in rejected[:4]
    ]

    picked = _balanced_prompt_snippets(kept)
    final_distribution: dict[str, int] = {}
    for s in picked:
        final_distribution[s.signal_type] = (
            final_distribution.get(s.signal_type, 0) + 1
        )

    audit: dict[str, Any] = {
        **base_audit,
        "signals_kept_after_filter": len(kept),
        "signals_rejected_relevance": len(rejected),
        "candidates_after_relevance": len(kept),
        "final_snippets": len(picked),
        "avg_relevance_score": round(avg, 4),
        "top_kept_scores": [round(v, 4) for v in kept_scores],
        "top_rejected_scores": [round(v, 4) for v in rejected_scores],
        "rejection_reasons": rejection_counts,
        "sample_rejected_snippets": sample_rejected,
        "final_block_distribution": final_distribution,
    }

    if not picked:
        return (None, audit)

    category = category_matched or "matched category"
    header = (
        f"Amazon Reviews 2023 buyer-language signals "
        f"(real product reviews from category={category}; treat as "
        f"buyer language, NOT as ground truth):"
    )
    body = "\n".join(_format_prompt_snippet(s) for s in picked)
    return (f"{header}\n{body}", audit)


__all__ = [
    "build_amazon_evidence_section",
    "build_amazon_evidence_section_from_dict_brief",
    "build_amazon_persona_prompt_block",
    "build_amazon_persona_prompt_block_with_audit",
]
