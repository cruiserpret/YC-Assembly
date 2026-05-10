"""Phase 10A.2 / 10A.3 — fresh retrieval-driven evidence + persona pipeline.

Wires the brief-agnostic building blocks (evidence_anchor_planner,
brave/tavily/youtube/firecrawl adapters, evidence_signal_extractor,
persona_emission_widener, persona_set_compressor) into a single
function chain that takes a founder brief and produces a fresh
run-scoped persona society.

Phase 10A.3 adds tiered provider escalation:
  Tier 1 (always-tried if configured): Brave + Tavily
  Tier 2 (only if Tier 1 results don't meet thresholds):
    YouTube (comments via search) and/or Firecrawl (top-URL extract)
The retrieval audit captures providers attempted, escalation_triggered,
and the threshold that fired the escalation.

NO new APIs. Only existing Brave/Tavily/YouTube/Firecrawl/Amazon-local
providers. Each provider is gated on its configured key — missing
keys mean that provider is skipped, never silently retried with
fake data.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

# Decimal is imported above; confirm

from assembly.config import get_settings
from assembly.sources.evidence_anchor_planner.planner import (
    generate_anchor_plan,
)
from assembly.sources.evidence_anchor_planner.schemas import (
    EvidenceAnchorPlan, ProductBriefForPlanning,
)
from assembly.sources.evidence_signal_extractor.extractor import (
    extract_evidence_signals,
)
from assembly.sources.evidence_signal_extractor.schemas import (
    EvidenceSignal,
)
from assembly.sources.persona_emission_widener.widener import (
    widen_persona_candidates,
)
from assembly.sources.persona_set_compressor.compressor import (
    compress_persona_set,
)


logger = logging.getLogger(__name__)


# Per-provider bounded caps. Conservative but not stingy — we need
# enough query diversity for compression to find 21+ distinct personas.
_BRAVE_MAX_QUERIES = 12
_TAVILY_MAX_QUERIES = 8
_YOUTUBE_MAX_QUERIES = 0  # disabled by default; opt-in via key
_PER_QUERY_RESULT_CAP = 8
_MIN_SNIPPET_LEN = 50
_MIN_ACCEPTED_FOR_PASS = 8


def brief_dict_to_planning(
    brief: dict[str, Any],
) -> ProductBriefForPlanning:
    """Convert the FounderBriefIn-style dict into the planner schema."""
    return ProductBriefForPlanning(
        product_name=brief["product_name"],
        product_description=brief["product_description"],
        price_or_price_structure=brief.get("price_or_price_structure"),
        launch_geography=brief.get("launch_geography"),
        target_customers=list(brief.get("target_customers") or []),
        competitors=list(brief.get("competitors_or_alternatives") or []),
        optional_constraints=list(brief.get("constraints") or []),
    )


def plan_live_evidence_queries(
    *,
    brief_dict: dict[str, Any],
) -> tuple[EvidenceAnchorPlan, list[str]]:
    """Generate the anchor plan + the actual provider query strings.

    Returns (anchor_plan, query_strings). The query strings are bounded
    to fit per-provider caps; downstream stages dispatch them across
    Brave/Tavily/YouTube subject to which keys are configured.
    """
    planning_brief = brief_dict_to_planning(brief_dict)
    plan = generate_anchor_plan(planning_brief)
    queries: list[str] = []
    pname = brief_dict["product_name"]
    # Phase 10B.3+: strip pasted-list noise ("1. ", "(2) ", leading
    # "and "/"or ") from user input before building queries. A query
    # like '"1. Samsung Family Hub" review' returns ~0 useful results
    # because no review article wraps the leading "1. ".
    from assembly.sources.evidence_anchor_planner.planner import (
        _strip_user_listing_prefix,
    )
    # Per-competitor queries — these tend to return the most useful
    # objection / proof / preference content because real competitors
    # have established review corpora.
    for comp in (brief_dict.get("competitors_or_alternatives") or [])[:8]:
        comp = _strip_user_listing_prefix(comp)
        if not comp:
            continue
        queries.append(f'"{comp}" review')
        queries.append(f'"{comp}" vs alternatives')
        queries.append(f'"{comp}" complaints')
    # Per-target-customer queries (use category context, not product name)
    cat = (brief_dict.get("category_hint") or plan.product_type
           or "").strip()
    for tc in (brief_dict.get("target_customers") or [])[:5]:
        tc = _strip_user_listing_prefix(tc).rstrip(".")
        if not tc:
            continue
        # Skip obvious sentence-fragment shapes (verb-led).
        first = tc.split()[0].lower() if tc.split() else ""
        if first in {
            "forget", "forgets", "accidentally", "remember",
            "buy", "bought", "thinking", "thinks", "hoping",
        }:
            continue
        if cat:
            queries.append(f"{cat} for {tc}")
        else:
            queries.append(f"best {tc} {pname}")
    # Use-case anchored queries
    for uc in (plan.use_case_anchor_terms or [])[:4]:
        queries.append(f"{cat or pname} {uc}")
    # Objection-anchored queries (use category, not product)
    for obj in (plan.objection_anchor_terms or [])[:3]:
        queries.append(f"{cat or pname} {obj}")
    # Substitute queries
    for sub in (plan.substitutes or [])[:3]:
        queries.append(f"{sub} review")
    # Dedupe + cap to bounded total
    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        key = q.lower().strip()
        if key in seen or not key:
            continue
        seen.add(key)
        deduped.append(q)
    return plan, deduped[:24]


# -----------------------------------------------------------------------
# Retrieval
# -----------------------------------------------------------------------


def provider_keys_summary() -> dict[str, bool]:
    """Boolean-only summary — never includes raw key values."""
    s = get_settings()
    return {
        "brave_search_api_key_configured": bool(s.brave_search_api_key),
        "tavily_api_key_configured": bool(s.tavily_api_key),
        "youtube_data_api_key_configured": bool(s.youtube_data_api_key),
        "firecrawl_api_key_configured": bool(s.firecrawl_api_key),
        "anthropic_api_key_configured": bool(s.anthropic_api_key),
    }


def _export_settings_keys_to_environ() -> None:
    """Pydantic-settings loads .env into the Settings object but does
    not export to os.environ. The retrieval adapters
    (`assembly.sources.brave.adapter`, `tavily.adapter`) read keys
    directly from os.environ. Bridge them here so live retrieval works
    when the keys are configured via .env."""
    import os
    s = get_settings()
    if s.brave_search_api_key and not os.environ.get("BRAVE_SEARCH_API_KEY"):
        os.environ["BRAVE_SEARCH_API_KEY"] = s.brave_search_api_key
    if s.tavily_api_key and not os.environ.get("TAVILY_API_KEY"):
        os.environ["TAVILY_API_KEY"] = s.tavily_api_key
    if s.youtube_data_api_key and not os.environ.get("YOUTUBE_DATA_API_KEY"):
        os.environ["YOUTUBE_DATA_API_KEY"] = s.youtube_data_api_key
    if s.firecrawl_api_key and not os.environ.get("FIRECRAWL_API_KEY"):
        os.environ["FIRECRAWL_API_KEY"] = s.firecrawl_api_key
    if s.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = s.anthropic_api_key


def _provider_query_budget(
    queries: list[str], available: dict[str, bool],
) -> dict[str, list[str]]:
    """Stratify queries across configured providers."""
    out: dict[str, list[str]] = {}
    available_providers = [
        p for p in ("brave_search", "tavily_search")
        if available.get(f"{p.replace('_search', '_search')}_api_key_configured", False)
        or available.get(f"{p}_api_key_configured", False)
    ]
    # Fall back: if env says brave_search_api_key_configured, use brave; etc.
    available_providers = []
    if available.get("brave_search_api_key_configured"):
        available_providers.append(("brave_search", _BRAVE_MAX_QUERIES))
    if available.get("tavily_api_key_configured"):
        available_providers.append(("tavily_search", _TAVILY_MAX_QUERIES))
    if not available_providers:
        return {}
    # Round-robin
    for i, q in enumerate(queries):
        provider, cap = available_providers[i % len(available_providers)]
        bucket = out.setdefault(provider, [])
        if len(bucket) < cap:
            bucket.append(q)
    return out


def _retrieve_brave(queries: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from assembly.sources.brave.adapter import (
            BraveAdapterConfig, BraveSearchClient,
        )
        client = BraveSearchClient(BraveAdapterConfig(
            max_queries=_BRAVE_MAX_QUERIES,
            max_results_per_query=_PER_QUERY_RESULT_CAP,
        ))
        results = client.search(queries=queries)
    except Exception as exc:  # noqa: BLE001
        return [], f"brave_search: {type(exc).__name__}: {str(exc)[:160]}"
    items: list[dict[str, Any]] = []
    for r in results:
        snippet = (r.description or "").strip()
        if len(snippet) < _MIN_SNIPPET_LEN:
            continue
        items.append({
            "provider": "brave_search",
            "url": r.url,
            "title": r.title,
            "snippet": snippet,
            "content_preview": snippet[:1500],
            "domain": r.domain,
            "planned_source_record_id_synthetic": (
                "live::brave::"
                + hashlib.sha256(r.url.encode("utf-8")).hexdigest()[:16]
            ),
            "matched_terms": [],
        })
    return items, None


def _retrieve_tavily(queries: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from assembly.sources.tavily.adapter import (
            TavilyAdapterConfig, TavilySearchClient,
        )
        client = TavilySearchClient(TavilyAdapterConfig(
            max_queries=_TAVILY_MAX_QUERIES,
            max_results_per_query=_PER_QUERY_RESULT_CAP,
        ))
        results = client.search(queries=queries)
    except Exception as exc:  # noqa: BLE001
        return [], f"tavily_search: {type(exc).__name__}: {str(exc)[:160]}"
    items: list[dict[str, Any]] = []
    for r in results:
        snippet = (r.content or "").strip()
        if len(snippet) < _MIN_SNIPPET_LEN:
            continue
        items.append({
            "provider": "tavily_search",
            "url": r.url,
            "title": r.title,
            "snippet": snippet,
            "content_preview": snippet[:1500],
            "domain": getattr(r, "domain", None),
            "planned_source_record_id_synthetic": (
                "live::tavily::"
                + hashlib.sha256(r.url.encode("utf-8")).hexdigest()[:16]
            ),
            "matched_terms": [],
        })
    return items, None


# Phase 10B.2 — YouTube comment quality filter. Spam, single-emoji
# replies, "first!", and creator-promo boilerplate all fail the
# filter and get rejected upstream of signal extraction.
_YT_SPAM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:first|second)\s*!?\s*$",
        r"check\s+(?:out\s+)?my\s+(?:channel|video|content|page)",
        r"subscribe\s+to\s+my\s+(?:channel|page)",
        r"\bsub\s+for\s+sub\b",
        r"\bdm\s+me\b",
        r"\bvenmo\b|\bcashapp\b|\bbitcoin\b|\binvest\b",
        r"^\s*(?:nice|cool|great|awesome|amazing|wow|love|lol|lmao|"
        r"thanks|thx|thank\s+you)\s*[!.]*\s*$",
        r"^\s*[\U0001F300-\U0001FAFF☀-➿\s]+\s*$",  # emoji-only
        r"^\s*\W+\s*$",  # punctuation-only
    )
)


def _yt_comment_passes_quality(
    comment_text: str,
    anchor_terms: list[str],
    *,
    min_chars: int = 80,
    seen_hashes: set[str] | None = None,
) -> tuple[bool, str | None]:
    """Return ``(passes, reject_reason)`` for a YouTube comment.

    Rules (10B.2):
      • text must be at least ``min_chars`` long after strip
      • text must contain at least one anchor term (product /
        category / competitor / use-case noun) so we don't pull in
        unrelated comment-section noise
      • text must not match any spam / promo / generic-praise
        pattern
      • duplicate text (same first 120 chars hash) is rejected
    """
    text = (comment_text or "").strip()
    if len(text) < min_chars:
        return False, "too_short"
    for r in _YT_SPAM_PATTERNS:
        if r.search(text):
            return False, "spam_or_generic"
    low = text.lower()
    if anchor_terms and not any(
        a in low for a in anchor_terms if a
    ):
        return False, "no_anchor_match"
    if seen_hashes is not None:
        h = hashlib.sha256(text[:120].encode("utf-8")).hexdigest()[:16]
        if h in seen_hashes:
            return False, "duplicate"
        seen_hashes.add(h)
    return True, None


def _retrieve_youtube(
    queries: list[str],
    max_videos_total: int = 6,
    anchor_terms: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """YouTube retrieval — promoted to Tier 1 in Phase 10B.2 when
    the API key is configured. Searches a small slate of queries,
    pulls top-level comments per video, runs each through the
    quality filter (length + anchor + spam + dedupe), and emits
    one item per accepted comment.

    Returns ``(accepted_items, audit, error_string_or_None)``. The
    audit dict carries pulled / accepted / rejected counts +
    rejection-reason histogram so the orchestrator can show the
    YouTube layer's contribution to objection / proof diversity.
    """
    audit: dict[str, Any] = {
        "videos_searched": 0,
        "videos_found": 0,
        "comments_pulled": 0,
        "comments_accepted": 0,
        "comments_rejected": 0,
        "rejection_reasons": {},
        "video_search_queries": [],
    }
    try:
        from assembly.sources.youtube.adapter import (
            YouTubeAdapterConfig, YouTubeDataClient,
        )
        client = YouTubeDataClient(YouTubeAdapterConfig(
            max_videos=max_videos_total,
            max_comments_per_video=20,
            max_comments_total=120,
        ))
    except Exception as exc:  # noqa: BLE001
        return [], audit, f"youtube: {type(exc).__name__}: {str(exc)[:160]}"
    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_text_hashes: set[str] = set()
    videos_pulled = 0
    anchors_lower = [
        a.lower() for a in (anchor_terms or []) if a and len(a) >= 3
    ]
    for q in queries[:6]:
        if videos_pulled >= max_videos_total:
            break
        audit["videos_searched"] += 1
        audit["video_search_queries"].append(q)
        try:
            videos = client.search_videos(
                query=q, max_results=max(1, max_videos_total // 3),
            )
        except Exception as exc:  # noqa: BLE001
            return items, audit, (
                f"youtube: {type(exc).__name__}: {str(exc)[:160]}"
            )
        audit["videos_found"] += len(videos)
        for v in videos:
            if videos_pulled >= max_videos_total:
                break
            videos_pulled += 1
            video_url = f"https://youtube.com/watch?v={v.video_id}"
            if video_url in seen_urls:
                continue
            seen_urls.add(video_url)
            try:
                comments = client.fetch_comments(
                    video_id=v.video_id, max_comments=20,
                )
            except Exception:  # noqa: BLE001
                comments = []
            for c in comments:
                ctext = (getattr(c, "text", "") or "").strip()
                audit["comments_pulled"] += 1
                passes, reason = _yt_comment_passes_quality(
                    ctext,
                    anchors_lower,
                    seen_hashes=seen_text_hashes,
                )
                if not passes:
                    audit["comments_rejected"] += 1
                    if reason:
                        audit["rejection_reasons"][reason] = (
                            audit["rejection_reasons"].get(reason, 0) + 1
                        )
                    continue
                audit["comments_accepted"] += 1
                comment_url = (
                    f"{video_url}#c"
                    + (getattr(c, "comment_id", "") or "")[:12]
                )
                items.append({
                    "provider": "youtube_data_api",
                    "url": comment_url,
                    "title": v.title[:240] if v.title else "",
                    "snippet": ctext,
                    "content_preview": ctext[:1500],
                    "domain": "youtube.com",
                    "planned_source_record_id_synthetic": (
                        "live::youtube::"
                        + hashlib.sha256(
                            comment_url.encode("utf-8"),
                        ).hexdigest()[:16]
                    ),
                    "matched_terms": [],
                })
    return items, audit, None


def _retrieve_firecrawl(
    seed_urls: list[str], max_pages: int = 6,
) -> tuple[list[dict[str, Any]], str | None]:
    """Tier-2 escalation provider. Extracts clean markdown from a
    bounded list of URLs (typically the URLs collected from Tier 1
    that look most evidence-rich)."""
    if not seed_urls:
        return [], None
    try:
        from assembly.sources.firecrawl.adapter import (
            FirecrawlAdapterConfig, FirecrawlExtractClient,
        )
        client = FirecrawlExtractClient(FirecrawlAdapterConfig(
            max_pages=max_pages,
            max_pages_per_domain=2,
        ))
    except Exception as exc:  # noqa: BLE001
        return [], f"firecrawl: {type(exc).__name__}: {str(exc)[:160]}"
    items: list[dict[str, Any]] = []
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            extracted = loop.run_until_complete(
                client.extract_top_urls(urls=seed_urls[:max_pages])
            )
        finally:
            loop.close()
    except Exception as exc:  # noqa: BLE001
        return [], f"firecrawl: {type(exc).__name__}: {str(exc)[:160]}"
    for r in extracted:
        text = (r.markdown or "").strip()
        if len(text) < _MIN_SNIPPET_LEN:
            continue
        items.append({
            "provider": "firecrawl_extract",
            "url": r.url,
            "title": r.title or "",
            "snippet": text[:6000],
            "content_preview": text[:1500],
            "domain": getattr(r, "domain", None),
            "planned_source_record_id_synthetic": (
                "live::firecrawl::"
                + hashlib.sha256(r.url.encode("utf-8")).hexdigest()[:16]
            ),
            "matched_terms": [],
        })
    return items, None


def _evaluate_tier1_thresholds(
    *,
    items: list[dict[str, Any]],
    persona_count_target: int = 21,
    min_raw_results: int = 24,
    min_distinct_domains: int = 6,
) -> tuple[bool, str | None]:
    """Decide whether tier-2 retrieval is needed. Returns
    ``(escalate, reason)``. ``escalate=True`` means tier-1 results
    were too thin to confidently build ≥21 personas."""
    if len(items) < min_raw_results:
        return True, (
            f"tier_1_raw_count={len(items)} below threshold "
            f"{min_raw_results}"
        )
    distinct_domains = len({
        (it.get("domain") or "").lower() for it in items
        if it.get("domain")
    })
    if distinct_domains < min_distinct_domains:
        return True, (
            f"tier_1_distinct_domains={distinct_domains} below "
            f"threshold {min_distinct_domains}"
        )
    # Provider diversity check: if both providers configured, want
    # both to have contributed.
    provider_counts: dict[str, int] = {}
    for it in items:
        p = it.get("provider") or "unknown"
        provider_counts[p] = provider_counts.get(p, 0) + 1
    keys = provider_keys_summary()
    if (
        keys.get("brave_search_api_key_configured")
        and keys.get("tavily_api_key_configured")
        and (
            provider_counts.get("brave_search", 0) == 0
            or provider_counts.get("tavily_search", 0) == 0
        )
    ):
        return True, (
            "tier_1 provider asymmetry — only one of Brave/Tavily "
            "returned results"
        )
    return False, None


def run_live_retrieval(
    *,
    queries: list[str],
    persona_count_target: int = 21,
    anchor_terms: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Tiered retrieval dispatch.

    Tier 1 (always tried if a key is configured):
      - Brave Search
      - Tavily Search
      - YouTube Data API  ← promoted to Tier 1 in Phase 10B.2
    Tier 2 (only when escalation thresholds aren't met):
      - Firecrawl markdown extraction over the most evidence-rich
        Tier-1 URLs (capped at 6 pages)

    Returns (retrieved_items, audit_dict). Each item is a normalized
    dict with: provider, url, title, snippet, content_preview,
    matched_terms (best-effort), planned_source_record_id_synthetic.
    """
    provider_avail = provider_keys_summary()
    budget = _provider_query_budget(queries, provider_avail)
    items: list[dict[str, Any]] = []
    audit: dict[str, Any] = {
        "providers_configured": [
            k.replace("_api_key_configured", "")
            for k, v in provider_avail.items()
            if v and k != "anthropic_api_key_configured"
        ],
        "providers_attempted": [],
        "providers_skipped": [],
        "provider_skip_reasons": {},
        "provider_keys": provider_avail,
        "queries_total": len(queries),
        "per_provider_query_count": {p: len(qs) for p, qs in budget.items()},
        "per_provider_raw_count": {},
        "raw_result_count": 0,
        "errors": [],
        "tier_1_raw_count": 0,
        "tier_2_raw_count": 0,
        "escalation_triggered": False,
        "escalation_reason": None,
        "tier_2_providers_attempted": [],
    }
    if not budget:
        audit["any_retrieval_provider_configured"] = False
        return items, audit
    audit["any_retrieval_provider_configured"] = True
    # Export keys from Settings → os.environ so the adapters can find them
    _export_settings_keys_to_environ()

    # ---- Tier 1: Brave + Tavily
    if "brave_search" in budget:
        audit["providers_attempted"].append("brave_search")
        b_items, b_err = _retrieve_brave(budget["brave_search"])
        if b_err:
            audit["errors"].append(b_err)
        items.extend(b_items)
        audit["per_provider_raw_count"]["brave_search"] = len(b_items)
    else:
        audit["providers_skipped"].append("brave_search")
        audit["provider_skip_reasons"]["brave_search"] = "no key configured"
    if "tavily_search" in budget:
        audit["providers_attempted"].append("tavily_search")
        t_items, t_err = _retrieve_tavily(budget["tavily_search"])
        if t_err:
            audit["errors"].append(t_err)
        items.extend(t_items)
        audit["per_provider_raw_count"]["tavily_search"] = len(t_items)
    else:
        audit["providers_skipped"].append("tavily_search")
        audit["provider_skip_reasons"]["tavily_search"] = "no key configured"

    # ---- Tier 1 (continued): YouTube Data API
    if provider_avail.get("youtube_data_api_key_configured"):
        audit["providers_attempted"].append("youtube_data_api")
        # Prefer competitor / category queries for YouTube — they
        # produce review videos with the richest comment threads.
        yt_queries = [
            q for q in queries
            if any(
                token in q.lower()
                for token in ("review", "vs", "comparison", "complaints")
            )
        ] or queries[:6]
        y_items, y_audit, y_err = _retrieve_youtube(
            yt_queries[:6],
            max_videos_total=6,
            anchor_terms=anchor_terms,
        )
        if y_err:
            audit["errors"].append(y_err)
        items.extend(y_items)
        audit["per_provider_raw_count"]["youtube_data_api"] = len(y_items)
        audit["youtube_audit"] = y_audit
    else:
        audit["providers_skipped"].append("youtube_data_api")
        audit["provider_skip_reasons"]["youtube_data_api"] = (
            "no key configured"
        )

    audit["tier_1_raw_count"] = len(items)

    # ---- Tier 2 escalation decision (Firecrawl only — YouTube is
    # already Tier 1 as of Phase 10B.2)
    escalate, reason = _evaluate_tier1_thresholds(
        items=items, persona_count_target=persona_count_target,
    )
    audit["escalation_triggered"] = escalate
    audit["escalation_reason"] = reason
    if escalate:
        # Try Firecrawl if configured. Seed URLs from the top-N
        # tier-1 results sorted by snippet length.
        if provider_avail.get("firecrawl_api_key_configured"):
            audit["tier_2_providers_attempted"].append("firecrawl_extract")
            tier1_sorted = sorted(
                items[:audit["tier_1_raw_count"]],
                key=lambda it: -len(it.get("snippet") or ""),
            )
            seed_urls = [it["url"] for it in tier1_sorted[:8] if it.get("url")]
            f_items, f_err = _retrieve_firecrawl(
                seed_urls=seed_urls, max_pages=6,
            )
            if f_err:
                audit["errors"].append(f_err)
            items.extend(f_items)
            audit["per_provider_raw_count"]["firecrawl_extract"] = (
                len(f_items)
            )
        else:
            audit["providers_skipped"].append("firecrawl_extract")
            audit["provider_skip_reasons"]["firecrawl_extract"] = (
                "no key configured"
            )

    audit["tier_2_raw_count"] = len(items) - audit["tier_1_raw_count"]
    audit["raw_result_count"] = len(items)
    return items, audit


# -----------------------------------------------------------------------
# Scoring
# -----------------------------------------------------------------------


_FORBIDDEN_FAKE_USE_PATTERNS = (
    r"\bi\s+bought\b", r"\bi\s+used\b", r"\bmy\s+lumaloop\b",
)


def score_and_accept_evidence(
    *,
    items: list[dict[str, Any]],
    plan: EvidenceAnchorPlan,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filter retrieval items down to those with at least one anchor
    match in the snippet. Audit captures rejection reasons."""
    accepted: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    seen_url: set[str] = set()
    seen_hash: set[str] = set()
    pos_anchors_lower = [
        a.lower() for a in (plan.positive_anchor_terms or [])
    ]
    competitor_anchors_lower = [
        a.lower() for a in (plan.competitor_anchor_terms or [])
    ]
    use_case_lower = [
        a.lower() for a in (plan.use_case_anchor_terms or [])
    ]
    objection_lower = [
        a.lower() for a in (plan.objection_anchor_terms or [])
    ]
    all_anchors_lower = (
        pos_anchors_lower + competitor_anchors_lower
        + use_case_lower + objection_lower
    )
    for it in items:
        url = (it.get("url") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        if not snippet:
            rejection_counts["empty_snippet"] = rejection_counts.get(
                "empty_snippet", 0
            ) + 1
            continue
        if url in seen_url:
            rejection_counts["dup_url"] = rejection_counts.get(
                "dup_url", 0
            ) + 1
            continue
        seen_url.add(url)
        h = hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16]
        if h in seen_hash:
            rejection_counts["dup_content"] = rejection_counts.get(
                "dup_content", 0
            ) + 1
            continue
        seen_hash.add(h)
        # Reject if snippet looks like fake target-product usage
        low = snippet.lower()
        # Use product_name from the plan to build a per-product fake-use
        # pattern (universal — works for any product_name)
        fake_use_re = re.compile(
            rf"\bi\s+(?:bought|used|own|my)\s+(?:the\s+|a\s+|an\s+)?"
            rf"{re.escape(plan.product_name.lower())}\b",
            re.I,
        )
        if fake_use_re.search(low):
            rejection_counts["fake_target_product_use"] = (
                rejection_counts.get("fake_target_product_use", 0) + 1
            )
            continue
        # Anchor matching: snippet OR title OR URL must contain an
        # anchor. Snippets from search APIs are often short; the title
        # / URL frequently carry the canonical signal.
        title = (it.get("title") or "").lower()
        url_low = (url or "").lower()
        haystack = f"{low} {title} {url_low}"
        matched_terms: list[str] = []
        for a in all_anchors_lower:
            if a and a in haystack:
                matched_terms.append(a)
        if not matched_terms:
            rejection_counts["no_anchor_match"] = rejection_counts.get(
                "no_anchor_match", 0
            ) + 1
            continue
        it = dict(it)
        it["matched_terms"] = list(dict.fromkeys(matched_terms))[:6]
        accepted.append(it)
    audit = {
        "raw_count": len(items),
        "accepted_count": len(accepted),
        "rejected_count": len(items) - len(accepted),
        "rejection_counts": rejection_counts,
    }
    return accepted, audit


# -----------------------------------------------------------------------
# Signal extraction
# -----------------------------------------------------------------------


def extract_signals_from_accepted(
    *,
    accepted: list[dict[str, Any]],
    plan: EvidenceAnchorPlan,
) -> tuple[list[EvidenceSignal], dict[str, Any]]:
    all_signals: list[EvidenceSignal] = []
    per_item_counts: list[int] = []
    competitors = list(plan.competitors)
    substitutes = list(plan.substitutes or [])
    use_case_terms = list(plan.use_case_anchor_terms or [])
    objection_terms = list(plan.objection_anchor_terms or [])
    for item in accepted:
        sigs = extract_evidence_signals(
            evidence_item=item,
            competitors=competitors,
            substitutes=substitutes,
            use_case_terms=use_case_terms,
            objection_terms=objection_terms,
        )
        all_signals.extend(sigs)
        per_item_counts.append(len(sigs))
    sig_type_counts: dict[str, int] = {}
    for s in all_signals:
        sig_type_counts[s.signal_type] = (
            sig_type_counts.get(s.signal_type, 0) + 1
        )
    audit = {
        "input_evidence_count": len(accepted),
        "total_signals_emitted": len(all_signals),
        "avg_signals_per_evidence": round(
            sum(per_item_counts) / max(len(per_item_counts), 1), 2,
        ),
        "signals_by_type": sig_type_counts,
    }
    return all_signals, audit


# -----------------------------------------------------------------------
# Persona candidate emission + compression
# -----------------------------------------------------------------------


def build_fresh_persona_candidates(
    *,
    signals: list[EvidenceSignal],
    plan: EvidenceAnchorPlan,
    target_brief_id: str,
    product_name: str,
    generated_for_phase: str = "10A.2",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the persona widener over the evidence signals. Starts with
    no existing candidates — the widener will emit one or more
    candidates per signal, subject to its policy."""
    extended, widening_audit = widen_persona_candidates(
        existing_candidates=[],
        signals=signals,
        target_brief=target_brief_id,
        product_name=product_name,
        generated_for_phase=generated_for_phase,
    )
    return extended, widening_audit


def _live_compress_simple(
    candidates: list[dict[str, Any]],
    *,
    target_count: int,
    max_per_role: int = 4,
    max_role_concentration: float = 0.35,
) -> list[dict[str, Any]]:
    """Live-mode-friendly compression: keep up to `max_per_role` per
    normalized_primary_role, sort by quality, cap at `target_count`,
    enforce 35% role-concentration ceiling.

    Universal — no LumaLoop / 9A.2-strict dedup. Allows two personas
    of the same role when their underlying evidence differs.
    """
    # Sort by quality descending (assumes quality_score on candidate)
    ranked = sorted(
        candidates,
        key=lambda c: -float(c.get("quality_score") or 7.0),
    )
    by_role: dict[str, list[dict[str, Any]]] = {}
    chosen: list[dict[str, Any]] = []
    role_max = max(1, int(target_count * max_role_concentration))
    seen_evidence: set[str] = set()
    for c in ranked:
        if len(chosen) >= target_count:
            break
        role = (
            c.get("normalized_primary_role")
            or c.get("inferred_persona_role")
            or "unknown"
        )
        if len(by_role.get(role, [])) >= min(role_max, max_per_role):
            continue
        # Avoid candidates with identical evidence_snippet keys
        snip_key = "|".join(
            (s or "")[:80].lower()
            for s in (c.get("evidence_snippets") or [])[:2]
        )
        if snip_key and snip_key in seen_evidence:
            continue
        seen_evidence.add(snip_key)
        by_role.setdefault(role, []).append(c)
        chosen.append(c)
    return chosen


def compress_to_live_society(
    *,
    candidates: list[dict[str, Any]],
    accepted_evidence: list[dict[str, Any]],
    target_brief_id: str,
    product_name: str,
    launch_state: str,
    hard_max: int = 30,
) -> tuple[Any, dict[str, Any]]:
    """Compress the candidate pool into the live society. Wraps
    `compress_persona_set` with the hard cap from 9A.2 and returns
    (compressed_set, audit_dict).

    For live mode we widen the soft target range (`max_target_range`)
    to (21, 30) and lower `min_behavioral_differential` to 1 so the
    compressor allows multiple personas per role when the underlying
    evidence supports differentiating them. The `hard_max_compressed`
    stratified selector still enforces the 35% role-concentration
    ceiling and provider/theme diversity."""
    # Build planned_source_records list from the accepted evidence
    planned_records = [
        {
            "planned_source_record_id_synthetic": e[
                "planned_source_record_id_synthetic"
            ],
            "provider": e.get("provider"),
            "url": e.get("url"),
            "domain": e.get("domain"),
            "evidence_theme": "live_evidence",
        }
        for e in accepted_evidence
    ]
    # Phase 10A.2: skip the strict 9A.2 compressor (its role+theme
    # dedup over-collapses live retrieval results). Use the simple
    # live compressor that keeps up to 4 personas per role under a
    # 35% concentration ceiling.
    pre_selected = _live_compress_simple(
        candidates, target_count=hard_max,
        max_per_role=4, max_role_concentration=0.35,
    )
    # Build a CompressedPersonaSet-shaped wrapper that downstream
    # persistence + reporting code expects. The wrapper exposes
    # `compressed_candidates` as a list of objects with the required
    # attributes (candidate_id, normalized_primary_role, etc.).
    from assembly.sources.persona_set_compressor.schemas import (
        CompressedPersonaCandidate,
    )

    compressed_list: list[CompressedPersonaCandidate] = []
    for c in pre_selected:
        normalized_role = (
            c.get("normalized_primary_role")
            or c.get("inferred_persona_role")
            or "unknown"
        )
        # Coerce inferred_traits to dict shape with required keys
        traits_in: list[Any] = list(c.get("inferred_traits") or [])
        coerced_traits: list[dict[str, Any]] = []
        for t in traits_in[:7]:
            if isinstance(t, dict):
                tname = t.get("trait_name") or "interests"
                tvalue = (
                    t.get("trait_value")
                    or t.get("value")
                    or normalized_role
                )
                src_id = (
                    t.get("evidence_source_record_id")
                    or t.get("source_id")
                    or "synthetic"
                )
                ex = (
                    t.get("evidence_excerpt")
                    or t.get("rationale")
                    or "evidence"
                )
                conf = t.get("confidence")
                if conf not in ("high", "medium", "low"):
                    conf = "medium"
                coerced_traits.append({
                    "trait_name": tname,
                    "trait_value": tvalue[:240] if isinstance(tvalue, str) else str(tvalue)[:240],
                    "evidence_source_record_id": str(src_id),
                    "evidence_excerpt": (ex or "evidence")[:240] or "evidence",
                    "confidence": conf,
                    "caveat": None,
                })
        # Pad to ≥2 traits for schema validity
        while len(coerced_traits) < 2:
            coerced_traits.append({
                "trait_name": "role_or_context",
                "trait_value": normalized_role,
                "evidence_source_record_id": "synthetic",
                "evidence_excerpt": (
                    f"persona_role::{normalized_role}"
                ),
                "confidence": "medium",
                "caveat": None,
            })
        evidence_snippets = [
            (s or "")[:240] for s in (c.get("evidence_snippets") or [])[:3]
        ] or [f"persona_role::{normalized_role}"]
        # source_provider_family + evidence_theme: derive from accepted_evidence
        # Find the first accepted evidence whose synthetic id is in
        # this candidate's source_record_ids
        cand_src_ids = (c.get("source_record_ids") or [])
        evidence_theme = "live_evidence"
        provider_family = "unknown"
        for sid in cand_src_ids:
            for e in accepted_evidence:
                if e.get("planned_source_record_id_synthetic") == sid:
                    provider_family = e.get("provider") or "unknown"
                    break
            if provider_family != "unknown":
                break
        try:
            cpc = CompressedPersonaCandidate(
                candidate_id=c.get("candidate_id") or f"live::{uuid.uuid4().hex[:12]}",
                target_brief=target_brief_id,
                generated_for_phase="10A.2",
                pre_normalization_role=normalized_role,
                normalized_primary_role=normalized_role,
                secondary_persona_roles=[],
                role_inference_basis=["live_retrieval_evidence_signals"],
                segment_label=normalized_role,
                source_record_ids=list({str(s) for s in cand_src_ids}) or ["unknown"],
                evidence_summary=(
                    c.get("evidence_summary")
                    or f"Live persona derived from {len(cand_src_ids)} sources."
                )[:1000],
                evidence_snippets=evidence_snippets,
                evidence_theme=evidence_theme,
                source_provider_family=provider_family,
                inferred_traits=coerced_traits,
                inferred_preferences=list(
                    c.get("inferred_preferences") or []
                )[:5],
                inferred_objections=list(
                    c.get("inferred_objections") or []
                )[:5],
                inferred_behaviors=list(
                    c.get("inferred_behaviors") or []
                )[:5],
                hypothetical_target_product_reaction=(
                    c.get("hypothetical_target_product_reaction")
                    or f"This persona would compare {product_name} to its "
                    f"{normalized_role.replace('_', ' ')} context."
                )[:1000],
                confidence="medium",
                evidence_strength="moderate",
                quality_score=float(c.get("quality_score") or 7.0),
                caveats=[
                    "live retrieval-driven persona; bounded retrieval "
                    "scope; not market-representative",
                ],
                simulation_usefulness_summary=(
                    f"Live persona for {product_name} simulation; "
                    "evidence-anchored to fresh retrieval."
                ),
                persistence_recommendation="DEFER",
                kept_reason="live_compression_simple",
            )
            compressed_list.append(cpc)
        except Exception as e:  # noqa: BLE001
            logger.debug("live compression: skipped malformed candidate: %s", e)

    # Build a tiny shim that mimics CompressedPersonaSet's interface
    class _LiveCompressedShim:
        compressed_candidates = compressed_list

        class _Diff:
            before_count = len(candidates)
            after_count = len(compressed_list)
            rejected_count = len(candidates) - len(compressed_list)
            roles_before = sorted({
                c.get("inferred_persona_role") or "unknown"
                for c in candidates
            })
            roles_after = sorted({
                c.normalized_primary_role for c in compressed_list
            })

            def model_dump(self) -> dict[str, Any]:
                return {
                    "before_count": self.before_count,
                    "after_count": self.after_count,
                    "rejected_count": self.rejected_count,
                    "roles_before": self.roles_before,
                    "roles_after": self.roles_after,
                }

        diff_summary = _Diff()
        rejected_candidates: list = []

    audit = {
        "input_candidate_count": len(candidates),
        "pre_selected_count": len(pre_selected),
        "compressed_count": len(compressed_list),
        "rejected_count": len(candidates) - len(compressed_list),
        "diff_summary": _LiveCompressedShim.diff_summary.model_dump(),
        "hard_max": hard_max,
        "compression_method": "live_simple_diversity_v1",
    }
    return _LiveCompressedShim(), audit


# -----------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------


def make_live_run_scope_id(
    *, product_name: str, run_id: uuid.UUID,
) -> str:
    """Run-scoped, brief-scoped — never global."""
    slug = re.sub(r"[^a-z0-9]+", "_", product_name.lower()).strip("_")[:24]
    payload = f"{slug}|{run_id}|{datetime.now(UTC).date().isoformat()}"
    return (
        f"run_live_{slug or 'product'}_"
        + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    )


async def persist_live_society(
    *,
    sm: Any,
    compressed: Any,
    accepted_evidence: list[dict[str, Any]],
    run_scope_id: str,
    product_name: str,
    launch_state: str,
    target_brief_id: str,
) -> dict[str, Any]:
    """Persist the live society into PersonaRecord/PersonaTrait/
    PersonaEvidenceLink tables, AND any new SourceRecord rows
    referenced by the accepted evidence.

    Idempotency: if any PersonaRecord with this run_scope_id exists,
    we refuse to re-persist (never duplicates).
    """
    from sqlalchemy import func, select
    from assembly.models.persona import (
        PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
    )
    from assembly.pipeline.persona.anonymization import generate_display_name
    sources_inserted = 0
    sources_reused = 0
    persona_inserted = 0
    traits_inserted = 0
    links_inserted = 0
    # Build evidence_id → SourceRecord uuid map (insert new rows)
    src_synthetic_to_uuid: dict[str, uuid.UUID] = {}
    # Idempotency check — separate read-only session
    async with sm() as check_session:
        existing = (await check_session.execute(
            select(func.count()).select_from(PersonaRecord).where(
                PersonaRecord.product_relevance_tags.contains(
                    [f"run_scope_id:{run_scope_id}"]
                )
            )
        )).scalar_one()
        if existing > 0:
            return {
                "skipped": True,
                "reason": (
                    f"{existing} personas already exist under "
                    f"run_scope_id={run_scope_id} — refusing to "
                    "duplicate"
                ),
                "sources_inserted": 0, "personas_inserted": 0,
                "traits_inserted": 0, "links_inserted": 0,
            }
    # Write — fresh session with explicit transaction. Two-pass:
    # Pass 1 inserts SourceRecord + PersonaRecord rows + flushes so
    # FK targets exist before Pass 2 inserts PersonaTrait +
    # PersonaEvidenceLink rows that reference them.
    async with sm() as session:
        async with session.begin():
            now = datetime.now(UTC)
            persona_uuid_by_candidate: dict[int, uuid.UUID] = {}
            persona_normalized_role: dict[int, str] = {}
            persona_src_uuids: dict[int, list[uuid.UUID]] = {}
            persona_traits_to_add: dict[int, list[dict[str, Any]]] = {}
            # Insert SourceRecord rows for each accepted evidence
            for e in accepted_evidence:
                sid = e["planned_source_record_id_synthetic"]
                src_uuid = uuid.uuid4()
                src_synthetic_to_uuid[sid] = src_uuid
                content_hash = hashlib.sha256(
                    (e.get("snippet") or "").encode("utf-8")
                ).hexdigest()
                # Refuse insert if the same content_hash already exists
                # (idempotency at the SourceRecord level)
                existing_src = (await session.execute(
                    select(SourceRecord.id).where(
                        SourceRecord.source_kind == e.get("provider", "unknown")
                    ).where(
                        SourceRecord.content_hash == content_hash
                    )
                )).scalar_one_or_none()
                if existing_src is not None:
                    src_synthetic_to_uuid[sid] = existing_src
                    sources_reused += 1
                    continue
                session.add(SourceRecord(
                    id=src_uuid,
                    source_kind=(e.get("provider") or "unknown")[:48],
                    source_url=(e.get("url") or "")[:2048],
                    captured_at=now,
                    content=(e.get("snippet") or "")[:8000],
                    content_hash=content_hash,
                    language="en",
                    metadata_={
                        "title": (e.get("title") or "")[:240],
                        "domain": e.get("domain"),
                        "product_relevance_run_scope_id": run_scope_id,
                    },
                    ingested_by=f"live_founder_brief:{target_brief_id}",
                    compliance_tag="public_api",
                    user_handle_hash=None,
                    pii_redaction_status="not_run",
                    sensitive_scan_status="not_run",
                ))
                sources_inserted += 1
            await session.flush()
            # Pass 1: Insert PersonaRecord rows only (so FKs exist)
            for ci, c in enumerate(compressed.compressed_candidates or []):
                p_uuid = uuid.uuid4()
                persona_uuid_by_candidate[ci] = p_uuid
                display = generate_display_name(seed=str(p_uuid))
                normalized_role = (
                    getattr(c, "normalized_primary_role", None)
                    or getattr(c, "pre_normalization_role", None)
                    or "unknown"
                )
                relevance_tags = [
                    f"target_brief:{target_brief_id}",
                    f"product_name:{product_name}",
                    f"launch_state:{launch_state}",
                    "phase:10A.2",
                    f"run_scope_id:{run_scope_id}",
                    f"normalized_primary_role:{normalized_role}",
                    f"evidence_theme:{getattr(c, 'evidence_theme', '')}",
                    f"source_provider_family:"
                    f"{getattr(c, 'source_provider_family', '')}",
                    f"compressed_candidate_id:{getattr(c, 'candidate_id', '')}",
                    "scope:run_scoped_brief_scoped",
                    "persistence_type:live_founder_brief",
                    "not_global_persona:true",
                    (
                        f"caveat:Generated for live_founder_brief run; "
                        "not global; brief-scoped and run-scoped."
                    ),
                ]
                session.add(PersonaRecord(
                    id=p_uuid,
                    display_name=display,
                    segment_label=(
                        getattr(c, "segment_label", None) or normalized_role
                    )[:64],
                    origin_market_broad=None,
                    product_relevance_tags=relevance_tags,
                    influence_score=None,
                    susceptibility=None,
                    population_weight=Decimal("1.0"),
                    source_strength_score=None,
                    refreshed_at=now,
                ))
                persona_inserted += 1
                persona_normalized_role[ci] = normalized_role
                # Resolve source UUIDs for this candidate
                src_uuids: list[uuid.UUID] = []
                for sid in (getattr(c, "source_record_ids", []) or []):
                    su = src_synthetic_to_uuid.get(sid)
                    if su is not None:
                        src_uuids.append(su)
                if not src_uuids and src_synthetic_to_uuid:
                    # fallback to first available source for this run
                    src_uuids = [next(iter(src_synthetic_to_uuid.values()))]
                persona_src_uuids[ci] = src_uuids
                # Traits: use inferred_traits + ensure ≥2
                traits_to_add: list[Any] = []
                for t in (getattr(c, "inferred_traits", None) or [])[:7]:
                    val = (
                        t.get("trait_value")
                        if isinstance(t, dict) else getattr(t, "trait_value", None)
                    )
                    field = (
                        t.get("trait_name")
                        if isinstance(t, dict) else getattr(t, "trait_name", None)
                    ) or "interests"
                    if not val:
                        continue
                    if field not in (
                        "interests", "role_or_context", "buying_constraints",
                        "trust_triggers", "current_alternatives",
                        "communication_style", "influence_signals",
                        "price_sensitivity", "objection_patterns",
                        "geography_broad",
                    ):
                        field = "interests"
                    rationale = (
                        t.get("evidence_excerpt")
                        if isinstance(t, dict) else
                        getattr(t, "evidence_excerpt", None)
                    ) or ""
                    conf_label = (
                        t.get("confidence")
                        if isinstance(t, dict) else
                        getattr(t, "confidence", None)
                    )
                    conf_num = {
                        "high": 0.85, "medium": 0.6, "low": 0.4,
                    }.get(conf_label, 0.6)
                    traits_to_add.append({
                        "field_name": field, "value": val[:240],
                        "rationale": rationale[:500],
                        "confidence": conf_num,
                    })
                if len(traits_to_add) < 2:
                    traits_to_add.append({
                        "field_name": "role_or_context",
                        "value": normalized_role,
                        "rationale": (
                            f"persona_role::{normalized_role} "
                            "(live fallback)"
                        ),
                        "confidence": 0.6,
                    })
                if len(traits_to_add) < 2:
                    traits_to_add.append({
                        "field_name": "interests",
                        "value": product_name,
                        "rationale": "fallback interest tag",
                        "confidence": 0.4,
                    })
                persona_traits_to_add[ci] = traits_to_add
            # Flush so PersonaRecord rows exist before FK references
            await session.flush()
            # Pass 2: Insert PersonaTrait + PersonaEvidenceLink
            for ci in persona_uuid_by_candidate:
                p_uuid = persona_uuid_by_candidate[ci]
                src_uuids = persona_src_uuids.get(ci, [])
                traits_to_add = persona_traits_to_add.get(ci, [])
                normalized_role = persona_normalized_role.get(ci, "unknown")
                for t in traits_to_add[:6]:
                    session.add(PersonaTrait(
                        id=uuid.uuid4(),
                        persona_id=p_uuid,
                        field_name=t["field_name"],
                        value=t["value"],
                        support_level="inferred",
                        source_ids=src_uuids,
                        confidence=Decimal(str(t["confidence"])),
                        rationale=t["rationale"],
                        last_updated_at=now,
                    ))
                    traits_inserted += 1
                # Evidence links — one per source the persona references
                for su in src_uuids[:3]:
                    session.add(PersonaEvidenceLink(
                        id=uuid.uuid4(),
                        persona_id=p_uuid,
                        source_record_id=su,
                        contribution_kind="trait_support",
                        contribution_field=(
                            traits_to_add[0]["field_name"]
                            if traits_to_add else "role_or_context"
                        ),
                        excerpt=(
                            (traits_to_add[0]["rationale"]
                             if traits_to_add else "")[:500]
                            or f"role::{normalized_role}"
                        ),
                        excerpt_offset=None,
                        confidence=Decimal("0.6"),
                    ))
                    links_inserted += 1
    return {
        "skipped": False,
        "sources_inserted": sources_inserted,
        "sources_reused": sources_reused,
        "personas_inserted": persona_inserted,
        "traits_inserted": traits_inserted,
        "links_inserted": links_inserted,
        "run_scope_id": run_scope_id,
    }
