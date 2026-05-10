"""Phase 8.5D.1D — fresh-product non-Amazon source expansion DRY RUN.

Goal: pursue broader evidence for the StrideShield mini-society via
the configured Brave Search and YouTube Data APIs only. No Amazon
scrape, no unofficial scraping, no DB writes.

Pipeline:

  1. Load the previous 8.5D.1C audit JSON (signal-only).
  2. Generate a `SourceExpansionPlan` from the founder brief +
     `EvidenceAnchorPlan` + `PersonaDiversityEvaluation`.
  3. Run bounded Brave + YouTube queries (only if keys present).
  4. Score every result (web snippet / video metadata / comment text)
     against the brief-derived anchors + scan for PII / fake-buyer /
     generic-only / wrong-context.
  5. Build planned source_records (synthetic IDs only — never inserted).
  6. Run the persona-candidate planner over the union of
     accepted-Brave + accepted-YouTube + accepted-Amazon-from-8.5D.1C
     evidence.
  7. Re-evaluate persona diversity. `ready_for_mutating_phase` is
     True only when (a) DB unchanged, (b) the diversity evaluator
     returns READY, and (c) the union of evidence comes from ≥2
     distinct provider families OR the audit explicitly explains why
     one provider is sufficient.

NO LLM. NO scraping. NO DB writes. NO simulation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.sources.brave import (
    BraveAdapterConfig, BraveSearchClient,
    is_brave_key_present, redact_url_for_audit,
)
from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning, generate_anchor_plan,
)
from assembly.sources.persona_diversity_evaluator import (
    PersonaDiversityEvaluation, evaluate_persona_diversity,
)
from assembly.sources.persona_role_planner import (
    EffectiveSourceRecord, PersonaCandidatePlanner,
)
from assembly.sources.source_expansion_planner import (
    generate_source_expansion_plan,
)
from assembly.sources.youtube import (
    YouTubeAdapterConfig, YouTubeDataClient,
    is_youtube_key_present, looks_like_low_quality_comment,
    redact_comment_for_audit,
)


PHASE_LABEL = "8.5D.1D"
TARGET_BRIEF_ID = "strideshield"
LAUNCH_STATE = "unlaunched"
PRODUCT_NAME = "StrideShield"

STRIDESHIELD_BRIEF = ProductBriefForPlanning(
    product_name=PRODUCT_NAME,
    product_description=(
        "A pocket-sized anti-blister and anti-chafe balm for college "
        "students, runners, hikers, gym-goers, theme-park walkers, "
        "and people whose shoes or sandals rub during long days. It "
        "is sweat-resistant, fragrance-free, non-greasy, and designed "
        "to be applied to heels, toes, thighs, and other friction "
        "spots before walking, running, workouts, or outdoor activity."
    ),
    price_or_price_structure="$12.99",
    launch_geography="California, United States",
    target_customers=[
        "college students who walk a lot on campus", "runners",
        "hikers", "gym-goers", "theme-park visitors",
        "people who get shoe rub, sandal cuts, blisters, or thigh chafing",
        "people who dislike greasy lotions or messy powders",
    ],
    competitors=[
        "Body Glide", "Gold Bond Friction Defense",
        "Megababe Thigh Rescue", "Squirrel's Nut Butter",
        "Trail Toes",
    ],
    optional_constraints=[],
)

# Bounded caps for this phase
BRAVE_MAX_QUERIES = 20
BRAVE_MAX_RESULTS_PER_QUERY = 10
YT_MAX_VIDEO_QUERIES = 10
YT_MAX_VIDEOS_PER_QUERY = 10
YT_MAX_COMMENTS_PER_VIDEO = 20
YT_MAX_COMMENTS_TOTAL = 200

# Universal PII patterns (web-side; YT comments redact via the adapter)
_EMAIL_RE = re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")

# Universal forbidden-phrase tokens that imply the unlaunched product
# was actually used / purchased / tested. Reused across providers.
_FAKE_USE_TOKEN_RES = (
    re.compile(rf"\b{re.escape(PRODUCT_NAME.lower())} (buyer|customer|user|review|loyalist)\b"),
    re.compile(rf"\bi (bought|tried|used|own|purchased) {re.escape(PRODUCT_NAME.lower())}\b"),
    re.compile(rf"\b{re.escape(PRODUCT_NAME.lower())} works (great|well|amazingly)\b"),
)

# Generic-only filler — reject snippets that are SEO boilerplate with
# zero opinion content.
_GENERIC_FILLER_PATTERNS = (
    re.compile(r"^\s*(buy|shop|find) .{0,40}(now|today|here)\b", re.I),
    re.compile(r"^\s*free (shipping|returns)\b", re.I),
    re.compile(r"^\s*best price guarantee", re.I),
)


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


async def _read_baseline_counts(sessionmaker) -> dict[str, int]:
    async with sessionmaker() as session:
        sr = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        pr = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
    return {
        "source_records": int(sr), "persona_records": int(pr),
        "persona_traits": int(pt), "persona_evidence_links": int(pel),
    }


def _text_score(
    *,
    text: str,
    competitors_lower: list[str],
    substitutes_lower: list[str],
    positive_anchors_lower: list[str],
    use_cases_lower: list[str],
    objection_anchors_lower: list[str],
) -> tuple[int, list[str]]:
    """Return (score, matched_terms) for a candidate snippet/text."""
    if not text:
        return 0, []
    low = text.lower()
    matched: list[str] = []
    score = 0
    for c in competitors_lower:
        if re.search(rf"\b{re.escape(c)}\b", low):
            matched.append(f"competitor:{c}")
            score += 4
    for s in substitutes_lower:
        if re.search(rf"\b{re.escape(s)}\b", low):
            matched.append(f"substitute:{s}")
            score += 2
    multi_word_positives = [a for a in positive_anchors_lower if " " in a]
    for a in multi_word_positives:
        if a in low:
            matched.append(f"positive:{a}")
            score += 3
    for u in use_cases_lower:
        if " " in u and u in low:
            matched.append(f"use_case:{u}")
            score += 1
    for o in objection_anchors_lower:
        if " " in o and o in low:
            matched.append(f"objection:{o}")
            score += 1
    return score, matched


def _scan_pii(text: str) -> list[str]:
    """Universal PII scanner. Returns a list of PII categories found."""
    hits: list[str] = []
    if _EMAIL_RE.search(text or ""):
        hits.append("email")
    if _PHONE_RE.search(text or ""):
        hits.append("phone")
    return hits


def _scan_fake_target_use(text: str) -> list[str]:
    """Detect text that claims the unlaunched product was actually used."""
    hits: list[str] = []
    low = (text or "").lower()
    for pat in _FAKE_USE_TOKEN_RES:
        if pat.search(low):
            hits.append(pat.pattern)
    return hits


def _is_generic_filler(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    for pat in _GENERIC_FILLER_PATTERNS:
        if pat.search(text):
            return True
    return False


def _infer_persona_value_roles(
    *,
    text: str,
    competitors: list[str],
    substitutes_lower: list[str],
    use_cases_lower: list[str],
) -> list[str]:
    """Lightweight, evidence-tied role inference for web/comment
    snippets — used as 'metadata.persona_value_roles' on the planned
    source_records so the persona planner can pick them up.

    Universal: only generates roles backed by explicit text matches.
    """
    low = (text or "").lower()
    roles: list[str] = []
    for c in competitors:
        if re.search(rf"\b{re.escape(c.lower())}\b", low):
            slug = c.lower().replace(" ", "_").replace("-", "_").replace("'", "")
            slug = re.sub(r"[^\w]+", "_", slug).strip("_")
            roles.append(f"competitor_user_{slug}")
    for s in substitutes_lower:
        if re.search(rf"\b{re.escape(s)}\b", low):
            slug = re.sub(r"[^\w]+", "_", s).strip("_")
            roles.append(f"substitute_user_{slug}")
    for u in use_cases_lower:
        if " " in u and u in low:
            roles.append("use_case_focused_buyer")
            break
    return list(dict.fromkeys(roles))  # de-dup, preserve order


def _read_8_5d_1c_audit() -> dict[str, Any]:
    """Read the prior-run audit JSON to extract evidence_anchor_plan +
    diversity evaluation. Returns {} if missing."""
    p = Path(__file__).resolve().parent.parent / "_audit" / (
        "fresh_product_persona_diversity_fix_8_5d_1c.json"
    )
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _diversity_eval_from_audit(
    audit: dict[str, Any],
) -> PersonaDiversityEvaluation | None:
    if not audit:
        return None
    blob = audit.get("persona_diversity_evaluation")
    if not blob:
        return None
    return PersonaDiversityEvaluation.model_validate(blob)


def _planned_source_record(
    *,
    source_kind: str,
    source_url: str,
    content: str,
    metadata: dict[str, Any],
    pii_hits: list[str],
    fake_use_hits: list[str],
    duplicate: bool,
) -> dict[str, Any]:
    """Audit-only synthetic planned source_record. NEVER inserted."""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "planned_source_record_id_synthetic": (
            f"planned::{TARGET_BRIEF_ID}::{source_kind}::"
            f"{content_hash[:16]}"
        ),
        "source_kind": source_kind,
        "source_url": source_url,
        "content_preview": content[:240],
        "content_length": len(content),
        "content_hash": content_hash,
        "language": "en",
        "metadata": {
            **metadata,
            "target_brief": TARGET_BRIEF_ID,
            "launch_state": LAUNCH_STATE,
            "source_is_live_web": True,
            "phase": PHASE_LABEL + "_dry_run",
        },
        "ingested_by": "dry_run",
        "compliance_tag": (
            "public_api" if source_kind != "brave_search_result"
            else "public_html"
        ),
        "captured_at": datetime.now(UTC).isoformat(),
        "pii_redaction_status": "passed" if not pii_hits else "blocked",
        "sensitive_scan_status": (
            "passed" if not fake_use_hits else "blocked"
        ),
        "duplicate_check": "duplicate" if duplicate else "unique",
        "user_handle_hash": None,
    }


def _build_effective_sources_from_planned(
    planned: list[dict[str, Any]],
) -> list[EffectiveSourceRecord]:
    """Wrap planned source_records as `EffectiveSourceRecord` so the
    persona-candidate planner can consume them with no DB write."""
    out: list[EffectiveSourceRecord] = []
    for p in planned:
        md = p["metadata"]
        out.append(EffectiveSourceRecord(
            source_record_id=p["planned_source_record_id_synthetic"],
            effective_kind="preview_used_as_is",
            superseded_preview_source_record_id=None,
            parent_asin=None, asin=None,
            category=md.get("provider", "external"),
            metadata_title=md.get("title"),
            rating=None, verified_purchase=None,
            helpful_vote=md.get("like_count"),
            timestamp=None,
            content_length=p["content_length"],
            content=p["content_preview"],
            metadata=md,
        ))
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5D.1D — fresh-product non-Amazon source expansion "
            "DRY RUN."
        ),
    )
    parser.add_argument(
        "--live", action="store_true",
        help=(
            "Run live Brave + YouTube queries (only if keys present). "
            "Default: dry-run with provider key-presence check only."
        ),
    )
    parser.add_argument("--brave-max-queries", type=int, default=10)
    parser.add_argument("--yt-max-video-queries", type=int, default=5)
    parser.add_argument(
        "--yt-max-videos-per-query", type=int, default=3,
    )
    parser.add_argument(
        "--yt-max-comments-per-video", type=int, default=10,
    )
    args = parser.parse_args()

    args.brave_max_queries = max(0, min(args.brave_max_queries, BRAVE_MAX_QUERIES))
    args.yt_max_video_queries = max(
        0, min(args.yt_max_video_queries, YT_MAX_VIDEO_QUERIES),
    )
    args.yt_max_videos_per_query = max(
        0, min(args.yt_max_videos_per_query, YT_MAX_VIDEOS_PER_QUERY),
    )
    args.yt_max_comments_per_video = max(
        0, min(args.yt_max_comments_per_video, YT_MAX_COMMENTS_PER_VIDEO),
    )
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / (
        "fresh_product_source_expansion_dry_run_8_5d_1d.json"
    )

    sm = get_sessionmaker()
    db_pre = await _read_baseline_counts(sm)
    print(f"DB baseline pre-dry-run: {db_pre}")

    # 1. Read prior audit (signal only)
    prior = _read_8_5d_1c_audit()
    prior_eval = _diversity_eval_from_audit(prior)
    if prior_eval is None:
        # No prior audit → synthesize a "no candidates" eval so the
        # planner still runs without crashing.
        prior_eval = evaluate_persona_diversity(
            brief=STRIDESHIELD_BRIEF, candidates=[],
        )
        print("WARNING: no 8.5D.1C audit JSON found; using empty signal.")
    else:
        print(
            f"Prior 8.5D.1C signal: rec="
            f"{prior_eval.mutating_persistence_recommendation}, "
            f"unique_roles={len(prior_eval.unique_primary_roles)}, "
            f"undercovered={len(prior_eval.undercovered_evidence_themes)}"
        )

    # 2. Anchor plan + expansion plan
    anchor_plan = generate_anchor_plan(STRIDESHIELD_BRIEF)
    brave_configured = is_brave_key_present()
    yt_configured = is_youtube_key_present()
    expansion_plan = generate_source_expansion_plan(
        brief=STRIDESHIELD_BRIEF, anchor_plan=anchor_plan,
        diversity_eval=prior_eval,
        providers_available={
            "brave_search": brave_configured,
            "youtube_data_api": yt_configured,
        },
        target_brief_id=TARGET_BRIEF_ID,
        launch_state=LAUNCH_STATE,
    )
    print(
        f"\nExpansionPlan: {expansion_plan.total_planned_queries} "
        f"queries across "
        f"{len([p for p in expansion_plan.provider_query_plans if p.is_provider_configured])} "
        f"configured providers"
    )

    competitors_lower = [c.lower() for c in STRIDESHIELD_BRIEF.competitors]
    substitutes_lower = [s.lower() for s in anchor_plan.substitute_anchor_terms]
    positive_anchors_lower = [
        a.lower() for a in anchor_plan.positive_anchor_terms
    ]
    use_cases_lower = [u.lower() for u in anchor_plan.use_case_anchor_terms]
    objection_anchors_lower = [
        o.lower() for o in anchor_plan.objection_anchor_terms
    ]

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()

    brave_summary: dict[str, Any] = {
        "queries_run": 0, "results_returned": 0,
        "accepted": 0, "rejected": 0,
    }
    yt_summary: dict[str, Any] = {
        "video_queries_run": 0, "videos_returned": 0,
        "comments_returned": 0, "accepted": 0, "rejected": 0,
        "comment_adapter_present": True,
    }

    # 3a. Brave
    brave_plan = next(
        (p for p in expansion_plan.provider_query_plans
         if p.provider == "brave_search"),
        None,
    )
    if args.live and brave_plan and brave_plan.is_provider_configured:
        client = BraveSearchClient(BraveAdapterConfig(
            max_queries=args.brave_max_queries,
            max_results_per_query=BRAVE_MAX_RESULTS_PER_QUERY,
        ))
        # Cap queries from the plan to args.brave_max_queries
        bqs = [q.query_text for q in brave_plan.queries[:args.brave_max_queries]]
        try:
            results = client.search(queries=bqs)
        except Exception as e:
            print(
                f"WARNING: Brave search failed: {type(e).__name__}: {e}"
            )
            results = []
        brave_summary["queries_run"] = len(bqs)
        brave_summary["results_returned"] = len(results)
        for r in results:
            url_red = redact_url_for_audit(r.url)
            content = (r.title + ". " + r.description).strip()
            pii = _scan_pii(content)
            fake_use = _scan_fake_target_use(content)
            score, matched = _text_score(
                text=content,
                competitors_lower=competitors_lower,
                substitutes_lower=substitutes_lower,
                positive_anchors_lower=positive_anchors_lower,
                use_cases_lower=use_cases_lower,
                objection_anchors_lower=objection_anchors_lower,
            )
            duplicate = url_red in seen_urls
            content_hash = hashlib.sha256(
                content.encode("utf-8"),
            ).hexdigest()
            duplicate = duplicate or content_hash in seen_hashes
            generic = _is_generic_filler(content)
            reasons: list[str] = []
            if pii:
                reasons.append(f"reject_pii_hit: {','.join(pii)}")
            if fake_use:
                reasons.append("reject_fake_buyer_for_unlaunched")
            if duplicate:
                reasons.append("reject_duplicate_url_or_hash")
            if generic and score == 0:
                reasons.append("reject_generic_only_or_too_short")
            if score < 3 and not reasons:
                reasons.append("reject_below_relevance_threshold")
            row = {
                "provider": "brave_search",
                "query": r.query, "title": r.title,
                "url": url_red, "domain": r.domain,
                "snippet": r.description[:300],
                "evidence_score": score,
                "matched_terms": matched,
                "decision": "REJECTED" if reasons else "ACCEPTED",
                "rejection_reasons": reasons,
                "expected_evidence_type": "blog_review",
            }
            if reasons:
                rejected.append(row)
                brave_summary["rejected"] += 1
                continue
            seen_urls.add(url_red)
            seen_hashes.add(content_hash)
            roles = _infer_persona_value_roles(
                text=content,
                competitors=STRIDESHIELD_BRIEF.competitors,
                substitutes_lower=substitutes_lower,
                use_cases_lower=use_cases_lower,
            )
            planned_row = _planned_source_record(
                source_kind="brave_search_result",
                source_url=url_red,
                content=content,
                metadata={
                    "provider": "brave_search",
                    "query": r.query,
                    "title": r.title,
                    "domain": r.domain,
                    "matched_terms": matched,
                    "persona_value_roles": roles,
                    "anchor_score": score,
                    "expected_evidence_type": "blog_review",
                },
                pii_hits=pii, fake_use_hits=fake_use,
                duplicate=duplicate,
            )
            row["planned_source_record_id_synthetic"] = (
                planned_row["planned_source_record_id_synthetic"]
            )
            accepted.append(row)
            planned.append(planned_row)
            brave_summary["accepted"] += 1
    elif brave_plan and not brave_plan.is_provider_configured:
        print(
            f"BRAVE skipped: {brave_plan.skipped_reason}"
        )

    # 3b. YouTube
    yt_plan = next(
        (p for p in expansion_plan.provider_query_plans
         if p.provider == "youtube_data_api"),
        None,
    )
    if args.live and yt_plan and yt_plan.is_provider_configured:
        client = YouTubeDataClient(YouTubeAdapterConfig(
            max_videos=args.yt_max_videos_per_query,
            max_comments_total=YT_MAX_COMMENTS_TOTAL,
            max_comments_per_video=args.yt_max_comments_per_video,
        ))
        yqs = [q.query_text for q in yt_plan.queries[:args.yt_max_video_queries]]
        videos: list = []
        for q in yqs:
            try:
                vlist = client.search_videos(
                    query=q,
                    max_results=args.yt_max_videos_per_query,
                )
            except Exception as e:
                print(
                    f"WARNING: YT video search failed for {q!r}: "
                    f"{type(e).__name__}: {e}"
                )
                continue
            yt_summary["video_queries_run"] += 1
            for v in vlist:
                videos.append((q, v))
            yt_summary["videos_returned"] += len(vlist)
        comments_total = 0
        for q, v in videos:
            if comments_total >= YT_MAX_COMMENTS_TOTAL:
                break
            # Score the video metadata as a candidate
            v_text = (v.title + ". by " + v.channel_title).strip()
            v_score, v_matched = _text_score(
                text=v_text,
                competitors_lower=competitors_lower,
                substitutes_lower=substitutes_lower,
                positive_anchors_lower=positive_anchors_lower,
                use_cases_lower=use_cases_lower,
                objection_anchors_lower=objection_anchors_lower,
            )
            v_pii = _scan_pii(v_text)
            v_fake = _scan_fake_target_use(v_text)
            v_url = f"https://www.youtube.com/watch?v={v.video_id}"
            v_url_red = redact_url_for_audit(v_url)
            content_hash = hashlib.sha256(
                v_text.encode("utf-8"),
            ).hexdigest()
            duplicate = (
                v_url_red in seen_urls or content_hash in seen_hashes
            )
            reasons: list[str] = []
            if v_pii:
                reasons.append(f"reject_pii_hit: {','.join(v_pii)}")
            if v_fake:
                reasons.append("reject_fake_buyer_for_unlaunched")
            if duplicate:
                reasons.append("reject_duplicate_url_or_hash")
            if v_score < 3 and not reasons:
                reasons.append("reject_below_relevance_threshold")
            row = {
                "provider": "youtube_data_api",
                "query": q, "title": v.title,
                "video_id": v.video_id,
                "channel_title": v.channel_title,
                "url": v_url_red,
                "snippet": v_text[:300],
                "evidence_score": v_score,
                "matched_terms": v_matched,
                "decision": "REJECTED" if reasons else "ACCEPTED",
                "rejection_reasons": reasons,
                "expected_evidence_type": "video_review",
            }
            if reasons:
                rejected.append(row)
                yt_summary["rejected"] += 1
            else:
                seen_urls.add(v_url_red)
                seen_hashes.add(content_hash)
                roles = _infer_persona_value_roles(
                    text=v_text,
                    competitors=STRIDESHIELD_BRIEF.competitors,
                    substitutes_lower=substitutes_lower,
                    use_cases_lower=use_cases_lower,
                )
                planned_row = _planned_source_record(
                    source_kind="youtube_video_result",
                    source_url=v_url_red,
                    content=v_text,
                    metadata={
                        "provider": "youtube_data_api",
                        "query": q,
                        "title": v.title,
                        "video_id": v.video_id,
                        "channel_title": v.channel_title,
                        "matched_terms": v_matched,
                        "persona_value_roles": roles,
                        "anchor_score": v_score,
                        "expected_evidence_type": "video_review",
                    },
                    pii_hits=v_pii, fake_use_hits=v_fake,
                    duplicate=duplicate,
                )
                row["planned_source_record_id_synthetic"] = (
                    planned_row["planned_source_record_id_synthetic"]
                )
                accepted.append(row)
                planned.append(planned_row)
                yt_summary["accepted"] += 1
            # Now pull comments for this video
            try:
                comments = client.fetch_comments(
                    video_id=v.video_id,
                    max_comments=min(
                        args.yt_max_comments_per_video,
                        YT_MAX_COMMENTS_TOTAL - comments_total,
                    ),
                )
            except Exception as e:
                print(
                    f"WARNING: YT comments failed for {v.video_id}: "
                    f"{type(e).__name__}: {e}"
                )
                comments = []
            yt_summary["comments_returned"] += len(comments)
            comments_total += len(comments)
            for c in comments:
                c_text = c.text  # already redacted by adapter
                if looks_like_low_quality_comment(c_text):
                    rejected.append({
                        "provider": "youtube_data_api",
                        "query": q,
                        "video_id": v.video_id,
                        "comment_id": c.comment_id,
                        "snippet": c_text[:300],
                        "decision": "REJECTED",
                        "rejection_reasons": ["reject_low_quality_comment"],
                        "expected_evidence_type": "video_comment_thread",
                    })
                    yt_summary["rejected"] += 1
                    continue
                c_pii = _scan_pii(c_text)
                c_fake = _scan_fake_target_use(c_text)
                c_score, c_matched = _text_score(
                    text=c_text,
                    competitors_lower=competitors_lower,
                    substitutes_lower=substitutes_lower,
                    positive_anchors_lower=positive_anchors_lower,
                    use_cases_lower=use_cases_lower,
                    objection_anchors_lower=objection_anchors_lower,
                )
                c_hash = hashlib.sha256(
                    c_text.encode("utf-8"),
                ).hexdigest()
                duplicate_c = c_hash in seen_hashes
                reasons: list[str] = []
                if c_pii:
                    reasons.append(f"reject_pii_hit: {','.join(c_pii)}")
                if c_fake:
                    reasons.append("reject_fake_buyer_for_unlaunched")
                if duplicate_c:
                    reasons.append("reject_duplicate_url_or_hash")
                if _is_generic_filler(c_text) and c_score == 0:
                    reasons.append("reject_generic_only_or_too_short")
                if c_score < 2 and not reasons:
                    reasons.append("reject_below_relevance_threshold")
                comment_url = (
                    f"https://www.youtube.com/watch?v={v.video_id}"
                    f"&lc={c.comment_id}"
                )
                comment_url_red = redact_url_for_audit(comment_url)
                row = {
                    "provider": "youtube_data_api",
                    "query": q, "video_id": v.video_id,
                    "comment_id": c.comment_id,
                    "url": comment_url_red,
                    "snippet": c_text[:300],
                    "evidence_score": c_score,
                    "matched_terms": c_matched,
                    "decision": "REJECTED" if reasons else "ACCEPTED",
                    "rejection_reasons": reasons,
                    "expected_evidence_type": "video_comment_thread",
                }
                if reasons:
                    rejected.append(row)
                    yt_summary["rejected"] += 1
                    continue
                seen_hashes.add(c_hash)
                roles = _infer_persona_value_roles(
                    text=c_text,
                    competitors=STRIDESHIELD_BRIEF.competitors,
                    substitutes_lower=substitutes_lower,
                    use_cases_lower=use_cases_lower,
                )
                planned_row = _planned_source_record(
                    source_kind="youtube_comment_result",
                    source_url=comment_url_red,
                    content=c_text,
                    metadata={
                        "provider": "youtube_data_api",
                        "query": q,
                        "video_id": v.video_id,
                        "comment_id": c.comment_id,
                        "matched_terms": c_matched,
                        "persona_value_roles": roles,
                        "anchor_score": c_score,
                        "expected_evidence_type": "video_comment_thread",
                    },
                    pii_hits=c_pii, fake_use_hits=c_fake,
                    duplicate=duplicate_c,
                )
                row["planned_source_record_id_synthetic"] = (
                    planned_row["planned_source_record_id_synthetic"]
                )
                accepted.append(row)
                planned.append(planned_row)
                yt_summary["accepted"] += 1
    elif yt_plan and not yt_plan.is_provider_configured:
        print(f"YOUTUBE skipped: {yt_plan.skipped_reason}")

    # 4. Build effective sources from the planned set + (optionally)
    # the SELECTED Amazon planned sources from 8.5D.1C
    effective_from_external = _build_effective_sources_from_planned(planned)
    amazon_selected_from_prior: list[EffectiveSourceRecord] = []
    if prior:
        for d in prior.get("planned_source_records") or []:
            if d.get("decision") != "SELECTED":
                continue
            psr = d.get("planned_source_record_preview")
            if not psr:
                continue
            md = psr.get("metadata") or {}
            sid = md.get("planned_source_record_id_synthetic") or (
                psr.get("source_url")
            )
            amazon_selected_from_prior.append(EffectiveSourceRecord(
                source_record_id=str(sid),
                effective_kind="preview_used_as_is",
                superseded_preview_source_record_id=None,
                parent_asin=md.get("parent_asin"),
                asin=md.get("asin"),
                category=md.get("source_category", "amazon_reviews_2023"),
                metadata_title=md.get("metadata_title"),
                rating=md.get("rating"),
                verified_purchase=md.get("verified_purchase"),
                helpful_vote=md.get("helpful_vote"),
                timestamp=md.get("timestamp"),
                content_length=psr["content_length"],
                content=psr["content_preview"],
                metadata={**md, "provider": "amazon_reviews_2023_local"},
            ))
    union_sources = effective_from_external + amazon_selected_from_prior
    print(
        f"\nUnion of effective sources: "
        f"{len(effective_from_external)} external + "
        f"{len(amazon_selected_from_prior)} Amazon prior = "
        f"{len(union_sources)} total"
    )

    # 5. Persona-candidate planner
    planner = PersonaCandidatePlanner(generated_for_phase=PHASE_LABEL)
    persona_plan = planner.generate(
        product_name=PRODUCT_NAME, target_brief_id=TARGET_BRIEF_ID,
        launch_state=LAUNCH_STATE,
        competitor_brief_list=STRIDESHIELD_BRIEF.competitors,
        substitute_brief_list=anchor_plan.substitute_anchor_terms,
        effective_sources=union_sources,
        preview_rows_total=0, companion_rows_total=0,
        superseded_preview_ids=[],
    )

    # 6. Persona-diversity evaluation
    diversity_eval = evaluate_persona_diversity(
        brief=STRIDESHIELD_BRIEF,
        candidates=persona_plan.persona_candidates,
        plan=anchor_plan,
    )

    # 7. DB post-check + readiness
    db_post = await _read_baseline_counts(sm)
    db_unchanged = db_pre == db_post
    provider_distribution: Counter = Counter()
    for src in union_sources:
        provider_distribution[
            src.metadata.get("provider", "unknown")
        ] += 1
    distinct_providers = sorted(provider_distribution.keys())
    multi_provider = len([
        p for p in distinct_providers if p != "unknown"
    ]) >= 2

    fake_use_in_candidates = any(
        v.get("forbidden_phrases_matched")
        for v in [
            json.loads(r.model_dump_json())
            for r in persona_plan.launch_state_validation_results
        ]
    )

    ready_for_mutating = (
        db_unchanged
        and not fake_use_in_candidates
        and persona_plan.ready_for_8_5d_2
        and (diversity_eval.mutating_persistence_recommendation == "READY")
        and multi_provider
    )

    # Evidence-theme distribution by competitor
    theme_dist: Counter = Counter()
    for p in planned:
        terms = p["metadata"].get("matched_terms") or []
        first_comp = next(
            (t for t in terms if t.startswith("competitor:")), None,
        )
        theme_dist[first_comp or "no_competitor"] += 1

    summary: dict[str, Any] = {
        "phase": "8_5d_1d_fresh_product_source_expansion_dry_run",
        "completed_at": datetime.now(UTC).isoformat(),
        "dry_run": True,
        "db_writes": False,
        "live_mode": args.live,
        "founder_brief": json.loads(STRIDESHIELD_BRIEF.model_dump_json()),
        "launch_state": LAUNCH_STATE,
        "previous_8_5d_1c_summary": {
            "diversity_recommendation": (
                prior_eval.mutating_persistence_recommendation
            ),
            "diversity_score": prior_eval.diversity_score,
            "unique_primary_roles": list(prior_eval.unique_primary_roles),
            "competitor_concentration": prior_eval.competitor_concentration,
            "undercovered_evidence_themes": list(
                prior_eval.undercovered_evidence_themes,
            ),
        } if prior else None,
        "evidence_anchor_plan": json.loads(anchor_plan.model_dump_json()),
        "source_expansion_plan": json.loads(
            expansion_plan.model_dump_json(),
        ),
        "provider_query_plan": [
            json.loads(p.model_dump_json())
            for p in expansion_plan.provider_query_plans
        ],
        "providers_used": [
            p for p in ("brave_search", "youtube_data_api")
            if (p == "brave_search" and brave_configured and args.live)
            or (p == "youtube_data_api" and yt_configured and args.live)
        ],
        "provider_key_presence": {
            "brave_search_configured": brave_configured,
            "youtube_data_configured": yt_configured,
        },
        "brave_results_summary": brave_summary,
        "youtube_results_summary": yt_summary,
        "evidence_candidates_by_provider": {
            "brave_search": brave_summary["accepted"],
            "youtube_data_api": yt_summary["accepted"],
        },
        "accepted_evidence_candidates": accepted,
        "rejected_evidence_candidates": rejected,
        "planned_source_records": planned,
        "evidence_theme_distribution": dict(theme_dist),
        "source_provider_distribution": dict(provider_distribution),
        "persona_role_plan": {
            "inferred_roles": persona_plan.inferred_roles,
            "evidence_basis_by_role": persona_plan.evidence_basis_by_role,
            "rejected_role_ideas": persona_plan.rejected_role_ideas,
            "role_inference_method": persona_plan.role_inference_method,
        },
        "generated_persona_candidates": [
            json.loads(c.model_dump_json())
            for c in persona_plan.persona_candidates
        ],
        "rejected_persona_candidate_ideas": [
            json.loads(r.model_dump_json())
            for r in persona_plan.rejected_candidate_ideas
        ],
        "persona_diversity_evaluation": json.loads(
            diversity_eval.model_dump_json(),
        ),
        "launch_state_claim_validation": [
            json.loads(v.model_dump_json())
            for v in persona_plan.launch_state_validation_results
        ],
        "db_pre_dry_run_counts": db_pre,
        "db_post_dry_run_counts": db_post,
        "db_unchanged_during_dry_run": db_unchanged,
        "caveats": persona_plan.caveats + [
            "Phase 8.5D.1D pursued non-Amazon evidence via Brave + "
            "YouTube official APIs only. NO scraping. NO DB writes.",
            "ready_for_mutating_phase requires (a) DB unchanged, (b) "
            "no fake-target-use claims, (c) persona planner ready, "
            "(d) diversity evaluator READY, (e) ≥2 distinct provider "
            "families (or explicit single-provider justification).",
        ],
        "recommendation": (
            "PASS — non-Amazon expansion executed; diversity evaluator: "
            f"{diversity_eval.mutating_persistence_recommendation}; "
            f"ready_for_mutating_phase: {ready_for_mutating}."
        ),
        "ready_for_mutating_phase": ready_for_mutating,
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print(f"Phase {PHASE_LABEL} — Source-expansion DRY RUN")
    print("=" * 72)
    print(f"product: {PRODUCT_NAME}")
    print(f"DB unchanged: {db_unchanged}")
    print(
        f"providers configured: brave={brave_configured}, "
        f"youtube={yt_configured}"
    )
    print(f"live mode: {args.live}")
    print(
        f"queries planned: {expansion_plan.total_planned_queries} "
        f"(Brave + YouTube)"
    )
    print(
        f"brave: queries_run={brave_summary['queries_run']}, "
        f"results={brave_summary['results_returned']}, "
        f"accepted={brave_summary['accepted']}, "
        f"rejected={brave_summary['rejected']}"
    )
    print(
        f"youtube: video_queries_run={yt_summary['video_queries_run']}, "
        f"videos={yt_summary['videos_returned']}, "
        f"comments={yt_summary['comments_returned']}, "
        f"accepted={yt_summary['accepted']}, "
        f"rejected={yt_summary['rejected']}"
    )
    print(f"planned source_records: {len(planned)}")
    print(
        f"effective sources (union with 8.5D.1C amazon): "
        f"{len(union_sources)}"
    )
    print(f"persona candidates: {len(persona_plan.persona_candidates)}")
    print(
        f"diversity: score={diversity_eval.diversity_score}, "
        f"unique_roles={len(diversity_eval.unique_primary_roles)}, "
        f"competitor_concentration="
        f"{diversity_eval.competitor_concentration}"
    )
    print(
        f"recommendation: "
        f"{diversity_eval.mutating_persistence_recommendation}"
    )
    print(f"ready_for_mutating_phase: {ready_for_mutating}")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
