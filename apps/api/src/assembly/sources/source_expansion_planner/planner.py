"""Phase 8.5D.1D — deterministic source-expansion planner.

`generate_source_expansion_plan(...)` takes a founder brief, an
`EvidenceAnchorPlan`, the previous-run `PersonaDiversityEvaluation`,
and the set of available providers. It returns a bounded
`SourceExpansionPlan` describing exactly which queries each provider
should run + why.

Universal by construction:

  * Queries are composed from the brief (competitors, target_customers,
    objection anchors, use-case anchors) — never from product-specific
    string templates.
  * Undercovered competitors flagged by the prior diversity evaluator
    are PROMOTED to the top of the query list (so the next pass
    actively pursues missing voices).
  * Per-provider hard caps are enforced (Brave: 20 × 10, YouTube: 10
    × 10).
  * Missing provider keys are handled gracefully — the plan still
    returns, with `is_provider_configured=False` and a `skipped_reason`.
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from assembly.sources.evidence_anchor_planner import (
    EvidenceAnchorPlan, ProductBriefForPlanning,
)
from assembly.sources.persona_diversity_evaluator import (
    PersonaDiversityEvaluation,
)
from assembly.sources.source_expansion_planner.schemas import (
    ExpansionQuery, ExpansionQueryKind, ExpectedEvidenceType,
    ProviderName, ProviderQueryPlan, SourceExpansionPlan,
)


# Universal hard caps — these match adapter-level caps so a plan can
# never request more work than the underlying client will execute.
_BRAVE_MAX_QUERIES = 20
_BRAVE_MAX_RESULTS_PER_QUERY = 10
_YT_MAX_QUERIES = 10
_YT_MAX_RESULTS_PER_QUERY = 10
_TAVILY_MAX_QUERIES = 20
_TAVILY_MAX_RESULTS_PER_QUERY = 10


def _slug(s: str) -> str:
    """Lowercase, dash-collapsed version of a brand/competitor token.
    Used to compare against `undercovered_evidence_themes` strings
    emitted by the diversity evaluator."""
    return re.sub(r"[^\w]+", "_", s.strip().lower()).strip("_")


def _undercovered_competitors(
    *,
    brief: ProductBriefForPlanning,
    diversity_eval: PersonaDiversityEvaluation,
) -> list[str]:
    """Return the brief's competitor names that the previous diversity
    evaluator flagged as undercovered."""
    out: list[str] = []
    pool = " | ".join(diversity_eval.undercovered_evidence_themes).lower()
    for c in brief.competitors:
        if c.lower() in pool:
            out.append(c)
    return out


def _over_concentrated_competitor(
    *,
    brief: ProductBriefForPlanning,
    diversity_eval: PersonaDiversityEvaluation,
) -> str | None:
    """If any single competitor dominates ≥0.6 of the prior run's
    persona candidates, surface it so its queries can be deprioritized
    in this pass."""
    if diversity_eval.competitor_concentration < 0.6:
        return None
    blob = " | ".join(diversity_eval.persona_similarity_warnings).lower()
    for c in brief.competitors:
        slug = c.lower().replace(" ", "_").replace("-", "_")
        if c.lower() in blob or slug in blob:
            return c
    return None


def _make_q(
    *,
    text: str,
    provider: ProviderName,
    kind: ExpansionQueryKind,
    fields: list[str],
    rationale: str,
    evidence_types: list[ExpectedEvidenceType],
    max_results: int,
    safety_notes: list[str] | None = None,
) -> ExpansionQuery:
    return ExpansionQuery(
        query_text=text.strip(),
        provider=provider, kind=kind,
        generated_from_fields=list(fields),
        rationale=rationale,
        expected_evidence_types=list(evidence_types),
        max_results=max_results,
        safety_notes=list(safety_notes or []),
    )


def _brave_queries(
    *,
    brief: ProductBriefForPlanning,
    plan: EvidenceAnchorPlan,
    undercovered: list[str],
    over_concentrated: str | None,
) -> list[ExpansionQuery]:
    """Build the Brave query batch with diversity-aware ordering.

    Order:
      0. Per-competitor coverage floor (Phase 8.5G.1) — every
         brief.competitor gets at least one dedicated query at the
         top of the batch, before any other query type. Universal
         change so niche fresh products (where undercovered list is
         empty) still get balanced competitor coverage.
      1. Undercovered-competitor reviews (FRESH-ROLE expansion).
      2. Undercovered-competitor vs another competitor pairs.
      3. Substitute-style queries.
      4. Use-case + product_type queries.
      5. Objection-shape queries.
      6. Over-concentrated competitor review (last).
    """
    q: list[ExpansionQuery] = []
    pt = plan.product_type
    cap = _BRAVE_MAX_QUERIES
    safety = ["snippets only — full-page extraction is gated by separate adapter"]
    seen_query_texts: set[str] = set()

    # 0. PER-COMPETITOR COVERAGE FLOOR (Phase 8.5G.1)
    # Every brief.competitor gets at least one dedicated review
    # query at the top of the batch. Within the floor we still
    # honor diversity ordering: undercovered first, then neutral
    # brief order, then over-concentrated last (consistent with the
    # 8.5D.1D semantics for products with prior diversity signal).
    floor_order: list[str] = []
    for c in undercovered:
        if c in brief.competitors and c not in floor_order:
            floor_order.append(c)
    for c in brief.competitors:
        if c in undercovered or c == over_concentrated:
            continue
        if c not in floor_order:
            floor_order.append(c)
    if over_concentrated and over_concentrated not in floor_order:
        floor_order.append(over_concentrated)
    for c in floor_order:
        text = f"{c} review"
        if text in seen_query_texts:
            continue
        seen_query_texts.add(text)
        q.append(_make_q(
            text=text, provider="brave_search",
            kind=(
                "undercovered_competitor"
                if c in undercovered else "competitor_review"
            ),
            fields=["brief.competitors"],
            rationale=(
                f"Per-competitor coverage floor: every brief "
                f"competitor gets ≥1 dedicated query. {c!r} "
                + (
                    "is also flagged as undercovered."
                    if c in undercovered else
                    "(over-concentrated)"
                    if c == over_concentrated else
                    "appears in brief.competitors."
                )
            ),
            evidence_types=[
                "blog_review", "comparison_article", "buyer_guide",
            ],
            max_results=_BRAVE_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 1. Undercovered competitor reviews — duplicates removed via
    # seen_query_texts; remaining undercovered queries get prioritized
    # if cap allows.
    for c in undercovered:
        text = f"{c} review"
        if text in seen_query_texts:
            continue
        seen_query_texts.add(text)
        q.append(_make_q(
            text=text,
            provider="brave_search",
            kind="undercovered_competitor",
            fields=[
                "brief.competitors",
                "previous_diversity_evaluation.undercovered_evidence_themes",
            ],
            rationale=(
                f"Previous run flagged {c!r} as undercovered. "
                "Pursue review pages to surface its user voice."
            ),
            evidence_types=[
                "blog_review", "comparison_article", "buyer_guide",
            ],
            max_results=_BRAVE_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 2. Undercovered competitor pair queries
    for i, a in enumerate(undercovered):
        for b in undercovered[i + 1:]:
            q.append(_make_q(
                text=f"{a} vs {b} review",
                provider="brave_search",
                kind="competitor_vs_competitor",
                fields=[
                    "brief.competitors",
                    "previous_diversity_evaluation.undercovered_evidence_themes",
                ],
                rationale=(
                    f"Pair-comparison surfaces both undercovered "
                    f"voices ({a} and {b}) on a single page."
                ),
                evidence_types=["comparison_article"],
                max_results=_BRAVE_MAX_RESULTS_PER_QUERY,
                safety_notes=safety,
            ))
            if len(q) >= cap:
                return q[:cap]

    # 3. Substitute-style queries (broadens beyond brand-named competitors)
    sub_terms = [s for s in plan.substitute_anchor_terms[:5] if s]
    for s in sub_terms:
        q.append(_make_q(
            text=f"{pt} alternative to {s}",
            provider="brave_search",
            kind="substitute_review",
            fields=[
                "anchor_plan.substitute_anchor_terms",
                "anchor_plan.product_type",
            ],
            rationale=(
                f"Substitute {s!r} surfaces buyers who chose a "
                "non-brand path; valuable for objection diversity."
            ),
            evidence_types=["blog_review", "buyer_guide"],
            max_results=_BRAVE_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 4. Use-case + product_type — pulls users with different motivations
    use_cases = [u for u in plan.use_case_anchor_terms[:8] if u][:6]
    for uc in use_cases:
        # Compose a tight 2-3 word query: <use-case> + <product_type>
        # Keep the use-case text but cap at 6 tokens.
        uc_short = " ".join(uc.split()[:6])
        text = f"best {pt} for {uc_short}".strip()
        q.append(_make_q(
            text=text,
            provider="brave_search",
            kind="use_case_problem",
            fields=[
                "anchor_plan.use_case_anchor_terms",
                "anchor_plan.product_type",
            ],
            rationale=(
                f"Use-case {uc_short!r} surfaces voices motivated "
                "by a different problem than the over-concentrated cluster."
            ),
            evidence_types=["blog_review", "buyer_guide", "forum_thread"],
            max_results=_BRAVE_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 5. Objection-shape queries
    for obj in plan.objection_anchor_terms[:5]:
        q.append(_make_q(
            text=f"{pt} {obj}",
            provider="brave_search",
            kind="objection_query",
            fields=[
                "anchor_plan.objection_anchor_terms",
                "anchor_plan.product_type",
            ],
            rationale=(
                f"Objection token {obj!r} surfaces complaints + "
                "tradeoff narratives in the category."
            ),
            evidence_types=["blog_review", "forum_thread"],
            max_results=_BRAVE_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 6. (deduped) tail competitor reviews — already covered by the
    # per-competitor coverage floor in step 0. The seen_query_texts
    # set prevents double-emission.
    return q[:cap]


def _youtube_queries(
    *,
    brief: ProductBriefForPlanning,
    plan: EvidenceAnchorPlan,
    undercovered: list[str],
) -> list[ExpansionQuery]:
    """Build the YouTube query batch.

    Video review queries trade off against text-blog queries — they
    surface a different voice (video creators + commenters). Same
    diversity-aware ordering as Brave but tighter cap (10 queries)."""
    q: list[ExpansionQuery] = []
    pt = plan.product_type
    cap = _YT_MAX_QUERIES
    safety = [
        "comments are public; never store channelId, email, phone, "
        "or external URLs",
        "low-quality comments (≤3 chars, all-caps spam, 'first!') "
        "are filtered before audit",
    ]

    # 1. Undercovered competitor video reviews
    for c in undercovered:
        q.append(_make_q(
            text=f"{c} review",
            provider="youtube_data_api",
            kind="undercovered_competitor",
            fields=[
                "brief.competitors",
                "previous_diversity_evaluation.undercovered_evidence_themes",
            ],
            rationale=(
                f"Video review of {c!r} surfaces creator + commenter "
                "voices missing from the prior Amazon-only pool."
            ),
            evidence_types=["video_review", "video_comment_thread"],
            max_results=_YT_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 2. Use-case + product_type
    for uc in plan.use_case_anchor_terms[:5]:
        uc_short = " ".join(uc.split()[:6])
        q.append(_make_q(
            text=f"{pt} for {uc_short}".strip(),
            provider="youtube_data_api",
            kind="use_case_problem",
            fields=[
                "anchor_plan.use_case_anchor_terms",
                "anchor_plan.product_type",
            ],
            rationale=(
                f"Use-case {uc_short!r} video search captures "
                "creators speaking to that motivation directly."
            ),
            evidence_types=["video_review", "video_comment_thread"],
            max_results=_YT_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 3. Category overview
    q.append(_make_q(
        text=f"best {pt}",
        provider="youtube_data_api",
        kind="category_overview",
        fields=["anchor_plan.product_type"],
        rationale=(
            "Category overview videos surface multi-product comparisons + "
            "a wider audience cross-section in the comments."
        ),
        evidence_types=["video_review", "video_comment_thread"],
        max_results=_YT_MAX_RESULTS_PER_QUERY,
        safety_notes=safety,
    ))
    return q[:cap]


def _tavily_queries(
    *,
    brief: ProductBriefForPlanning,
    plan: EvidenceAnchorPlan,
    undercovered: list[str],
) -> list[ExpansionQuery]:
    """Build the Tavily query batch (Phase 8.5G.1).

    Tavily is treated as a third broad-web discovery provider — used
    in addition to Brave so niche product briefs (where Brave's
    snippet pool is thin) get richer coverage.

    Order:
      0. Per-competitor coverage floor — every brief.competitor gets
         at least one dedicated query.
      1. Comparison queries: '<competitor> vs <competitor> review'.
      2. Use-case + product_type buyer-guide queries.
      3. Substitute / category-overview queries.
    """
    q: list[ExpansionQuery] = []
    pt = plan.product_type
    cap = _TAVILY_MAX_QUERIES
    safety = [
        "snippets only — full-page extraction is gated by separate "
        "Firecrawl adapter",
    ]
    seen: set[str] = set()

    # 0. Per-competitor coverage floor
    for c in brief.competitors:
        text = f"{c} review buyer guide"
        if text in seen:
            continue
        seen.add(text)
        q.append(_make_q(
            text=text, provider="tavily_search",
            kind=(
                "undercovered_competitor"
                if c in undercovered else "competitor_review"
            ),
            fields=["brief.competitors"],
            rationale=(
                f"Tavily per-competitor floor for {c!r}; targets "
                "comparison + buyer-guide articles."
            ),
            evidence_types=[
                "blog_review", "comparison_article", "buyer_guide",
            ],
            max_results=_TAVILY_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 1. Comparison pairs (top 3 pairs)
    pairs_emitted = 0
    for i, a in enumerate(brief.competitors):
        for b in brief.competitors[i + 1:]:
            text = f"{a} vs {b} review"
            if text in seen:
                continue
            seen.add(text)
            q.append(_make_q(
                text=text, provider="tavily_search",
                kind="competitor_vs_competitor",
                fields=["brief.competitors"],
                rationale=(
                    f"Pair-comparison surfaces {a} and {b} on a "
                    "single page; compresses cross-brand evidence."
                ),
                evidence_types=["comparison_article"],
                max_results=_TAVILY_MAX_RESULTS_PER_QUERY,
                safety_notes=safety,
            ))
            pairs_emitted += 1
            if len(q) >= cap or pairs_emitted >= 3:
                break
        if len(q) >= cap or pairs_emitted >= 3:
            break

    # 2. Use-case buyer guides
    for uc in plan.use_case_anchor_terms[:5]:
        uc_short = " ".join(uc.split()[:6])
        text = f"best {pt} for {uc_short} buyer guide".strip()
        if text in seen:
            continue
        seen.add(text)
        q.append(_make_q(
            text=text, provider="tavily_search",
            kind="use_case_problem",
            fields=[
                "anchor_plan.use_case_anchor_terms",
                "anchor_plan.product_type",
            ],
            rationale=(
                f"Use-case {uc_short!r} buyer-guide query — Tavily "
                "is strong on long-form review/comparison pages."
            ),
            evidence_types=["buyer_guide", "blog_review"],
            max_results=_TAVILY_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    # 3. Substitutes
    for s in plan.substitute_anchor_terms[:3]:
        text = f"{pt} alternative to {s}"
        if text in seen:
            continue
        seen.add(text)
        q.append(_make_q(
            text=text, provider="tavily_search",
            kind="substitute_review",
            fields=[
                "anchor_plan.substitute_anchor_terms",
                "anchor_plan.product_type",
            ],
            rationale=(
                f"Substitute {s!r} surfaces non-brand-path buyers."
            ),
            evidence_types=["blog_review", "buyer_guide"],
            max_results=_TAVILY_MAX_RESULTS_PER_QUERY,
            safety_notes=safety,
        ))
        if len(q) >= cap:
            return q[:cap]

    return q[:cap]


def _plan_id(
    brief: ProductBriefForPlanning,
    plan: EvidenceAnchorPlan,
    diversity_eval: PersonaDiversityEvaluation,
    providers_available: dict[ProviderName, bool],
) -> str:
    payload = "|".join((
        brief.product_name,
        plan.plan_id,
        str(diversity_eval.diversity_score),
        str(diversity_eval.competitor_concentration),
        ",".join(sorted(diversity_eval.unique_primary_roles)),
        ",".join(sorted(diversity_eval.undercovered_evidence_themes)),
        ",".join(f"{k}={int(v)}" for k, v in sorted(providers_available.items())),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def generate_source_expansion_plan(
    *,
    brief: ProductBriefForPlanning,
    anchor_plan: EvidenceAnchorPlan,
    diversity_eval: PersonaDiversityEvaluation,
    providers_available: dict[ProviderName, bool],
    target_brief_id: str,
    launch_state: str = "unlaunched",
) -> SourceExpansionPlan:
    """Pure function. Same inputs → same plan (modulo `generated_at`)."""
    if launch_state not in ("unlaunched", "launched", "in_market"):
        raise ValueError(f"unexpected launch_state: {launch_state!r}")
    undercovered = _undercovered_competitors(
        brief=brief, diversity_eval=diversity_eval,
    )
    over_concentrated = _over_concentrated_competitor(
        brief=brief, diversity_eval=diversity_eval,
    )
    rationale: list[str] = []
    if undercovered:
        rationale.append(
            f"Promoted {len(undercovered)} undercovered competitor(s) "
            "to the top of the query batch to actively pursue missing "
            "voices: " + ", ".join(undercovered) + "."
        )
    if over_concentrated:
        rationale.append(
            f"Deprioritized over-concentrated competitor "
            f"{over_concentrated!r} (concentration "
            f"{diversity_eval.competitor_concentration}); its review "
            "queries still run last to confirm dominance is a real "
            "market signal, not a query-batch artifact."
        )
    if diversity_eval.mutating_persistence_recommendation == "READY":
        rationale.append(
            "Prior run already READY — expansion plan is "
            "supplementary, not corrective."
        )
    rationale.append(
        "Query plan composed entirely from brief.competitors, "
        "brief.target_customers, anchor_plan.product_type, "
        "anchor_plan.substitute_anchor_terms, "
        "anchor_plan.use_case_anchor_terms, "
        "anchor_plan.objection_anchor_terms — no product-specific "
        "string templates."
    )

    plans: list[ProviderQueryPlan] = []
    total_q = 0
    total_max = 0

    # Brave plan
    if providers_available.get("brave_search"):
        bq = _brave_queries(
            brief=brief, plan=anchor_plan,
            undercovered=undercovered,
            over_concentrated=over_concentrated,
        )
        plans.append(ProviderQueryPlan(
            provider="brave_search",
            is_provider_configured=True,
            max_queries=_BRAVE_MAX_QUERIES,
            max_results_per_query=_BRAVE_MAX_RESULTS_PER_QUERY,
            max_total_results=_BRAVE_MAX_QUERIES * _BRAVE_MAX_RESULTS_PER_QUERY,
            queries=bq, skipped_reason=None,
        ))
        total_q += len(bq)
        total_max += sum(q.max_results for q in bq)
    else:
        plans.append(ProviderQueryPlan(
            provider="brave_search",
            is_provider_configured=False,
            max_queries=_BRAVE_MAX_QUERIES,
            max_results_per_query=_BRAVE_MAX_RESULTS_PER_QUERY,
            max_total_results=_BRAVE_MAX_QUERIES * _BRAVE_MAX_RESULTS_PER_QUERY,
            queries=[],
            skipped_reason=(
                "BRAVE_SEARCH_API_KEY missing from environment; "
                "Brave queries are NOT generated and NOT executed."
            ),
        ))

    # YouTube plan
    if providers_available.get("youtube_data_api"):
        yq = _youtube_queries(
            brief=brief, plan=anchor_plan,
            undercovered=undercovered,
        )
        plans.append(ProviderQueryPlan(
            provider="youtube_data_api",
            is_provider_configured=True,
            max_queries=_YT_MAX_QUERIES,
            max_results_per_query=_YT_MAX_RESULTS_PER_QUERY,
            max_total_results=_YT_MAX_QUERIES * _YT_MAX_RESULTS_PER_QUERY,
            queries=yq, skipped_reason=None,
        ))
        total_q += len(yq)
        total_max += sum(q.max_results for q in yq)
    else:
        plans.append(ProviderQueryPlan(
            provider="youtube_data_api",
            is_provider_configured=False,
            max_queries=_YT_MAX_QUERIES,
            max_results_per_query=_YT_MAX_RESULTS_PER_QUERY,
            max_total_results=_YT_MAX_QUERIES * _YT_MAX_RESULTS_PER_QUERY,
            queries=[],
            skipped_reason=(
                "YOUTUBE_DATA_API_KEY missing from environment; "
                "YouTube queries are NOT generated and NOT executed."
            ),
        ))

    # Tavily plan (Phase 8.5G.1) — third broad-web discovery provider
    if providers_available.get("tavily_search"):
        tq = _tavily_queries(
            brief=brief, plan=anchor_plan, undercovered=undercovered,
        )
        plans.append(ProviderQueryPlan(
            provider="tavily_search",
            is_provider_configured=True,
            max_queries=_TAVILY_MAX_QUERIES,
            max_results_per_query=_TAVILY_MAX_RESULTS_PER_QUERY,
            max_total_results=(
                _TAVILY_MAX_QUERIES * _TAVILY_MAX_RESULTS_PER_QUERY
            ),
            queries=tq, skipped_reason=None,
        ))
        total_q += len(tq)
        total_max += sum(q.max_results for q in tq)
    else:
        plans.append(ProviderQueryPlan(
            provider="tavily_search",
            is_provider_configured=False,
            max_queries=_TAVILY_MAX_QUERIES,
            max_results_per_query=_TAVILY_MAX_RESULTS_PER_QUERY,
            max_total_results=(
                _TAVILY_MAX_QUERIES * _TAVILY_MAX_RESULTS_PER_QUERY
            ),
            queries=[],
            skipped_reason=(
                "TAVILY_API_KEY missing from environment; Tavily "
                "queries are NOT generated and NOT executed."
            ),
        ))

    safety_caveats = [
        "Universal: only official provider APIs are used. Unofficial "
        "scraping (yt-dlp / pytube / scrapetube / browser automation) "
        "is forbidden and drift-tested.",
        "PII discipline: web snippets and YouTube comments pass through "
        "the existing PII / fake-buyer / sensitive-content scanners "
        "before any persona ever sees them.",
        "Bounded: per-provider hard caps are enforced at adapter level; "
        "the plan can never request more work than the adapter executes.",
        "Dry-run only: this plan does NOT cause any DB writes by itself. "
        "Execution + audit happens in a script that respects the same "
        "discipline.",
    ]
    return SourceExpansionPlan(
        plan_id=_plan_id(
            brief, anchor_plan, diversity_eval, providers_available,
        ),
        target_brief_id=target_brief_id,
        product_name=brief.product_name,
        launch_state=launch_state,  # type: ignore[arg-type]
        diversity_recommendation_in=(
            diversity_eval.mutating_persistence_recommendation
        ),
        undercovered_competitor_themes=list(undercovered),
        over_concentrated_competitor=over_concentrated,
        provider_query_plans=plans,
        total_planned_queries=total_q,
        total_planned_max_results=total_max,
        generated_from="deterministic",
        rationale=rationale,
        safety_caveats=safety_caveats,
        generated_at=datetime.now(UTC).isoformat(),
    )
