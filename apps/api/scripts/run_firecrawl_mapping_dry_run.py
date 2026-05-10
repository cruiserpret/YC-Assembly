"""Phase 8.3B-LIVE-2A — Firecrawl evidence mapping dry-run.

READ-ONLY. No DB writes (except the audit JSON file). No LLM calls.
No live Firecrawl/Tavily/Brave/Reddit. No persona / trait /
evidence-link / graph / simulation writes.

Goals:
  1. Load the 3 firecrawl_v1_scrape source_records from 8.3B-LIVE-1.
  2. For each, find the matching Tavily source_record by source_url.
  3. Find personas already using the matching Tavily record via
     persona_evidence_links.
  4. Run audience retrieval (read-only) → collect near-miss personas
     scoring 22-26 (under the unchanged 27 threshold).
  5. Cross-reference: for each Firecrawl page + each candidate persona,
     scan the Firecrawl body for excerpts that look thematically
     aligned with the persona's existing trait gaps.
  6. Verify each candidate excerpt is a verbatim substring of the
     Firecrawl body (non-negotiable anti-fabrication invariant).
  7. Save a structured dry-run mapping report.

What this report enables (NOT does):
  * Phase 8.3B-LIVE-2B (separate approval) will use this report's
    candidate list to drive trait re-extraction via the LLM trait
    worker, with the substring-of-source invariant pre-validated.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


# ---------------------------------------------------------------------------
# Per-trait-field thematic-keyword heuristics (heuristic only; the LLM
# in 8.3B-LIVE-2B is the authority on trait extraction. This module just
# identifies which trait fields a given Firecrawl excerpt LOOKS aligned
# with so the dry-run report shows the operator the shape of the
# enrichment opportunity.)
# ---------------------------------------------------------------------------


_TRAIT_FIELD_KEYWORDS: dict[str, tuple[re.Pattern[str], ...]] = {
    "objection_patterns": (
        re.compile(r"\b(?:plugin|app)\s+(?:bloat|sprawl|overload)\b", re.I),
        re.compile(r"\btoo many (?:apps|plugins|tools)\b", re.I),
        re.compile(r"\b(?:scam|scammed|fraud|ripoff|rip-off)\b", re.I),
        re.compile(r"\b(?:slow|slows|slowing) (?:my |the |your )?store\b", re.I),
        re.compile(r"\bjavascript\b.*\bconflicts?\b", re.I),
        re.compile(r"\b(?:fed up|frustrat|sick of)\b", re.I),
        re.compile(r"\bI (?:can'?t|cannot)\b", re.I),
    ),
    "price_sensitivity": (
        re.compile(r"\$\s*\d+\s*(?:/(?:mo|month|yr|year|user)|monthly|per month)?", re.I),
        re.compile(r"\bexpensive\b|\bcostly\b|\bovercharged\b", re.I),
        re.compile(r"\bbudget\b|\bcost\b.*\b(?:too high|too much)\b", re.I),
        re.compile(r"\bfree(?:mium)? trial\b", re.I),
        re.compile(r"\bsubscription fatigue\b", re.I),
        re.compile(r"\bcumulative (?:fees|costs?)\b", re.I),
    ),
    "current_alternatives": (
        re.compile(r"\b(?:Klaviyo|Mailchimp|HubSpot|Oberlo|Spocket|DSers)\b", re.I),
        re.compile(r"\b(?:Wordpress|WooCommerce|BigCommerce|Wix|Squarespace)\b", re.I),
        re.compile(r"\b(?:freelancer|agency|in-?house|consultant|developer)\b", re.I),
        re.compile(r"\b(?:hired|currently using|switched (?:to|from))\b", re.I),
    ),
    "trust_triggers": (
        re.compile(r"\bbrand control\b|\bbrand voice\b|\bbrand identity\b", re.I),
        re.compile(r"\b(?:case study|case studies|testimonial|reviews?)\b", re.I),
        re.compile(r"\b(?:proof|evidence|track record|reputation)\b", re.I),
        re.compile(r"\bonly trust\b|\bwon'?t trust\b|\bdistrust\b", re.I),
        re.compile(r"\b(?:credibility|legitimacy|verified)\b", re.I),
    ),
    "buying_constraints": (
        re.compile(r"\b(?:require|need|must|cannot)\s+(?:approval|sign-?off)\b", re.I),
        re.compile(r"\bbefore (?:I|we) (?:commit|buy|sign|switch)\b", re.I),
        re.compile(r"\b(?:audit|evaluate|test) (?:apps|tools|platforms)\b", re.I),
        re.compile(r"\bdirectly contributing to (?:revenue|operations)\b", re.I),
    ),
    "role_or_context": (
        re.compile(
            r"\b(?:Shopify (?:merchant|operator|store owner)|"
            r"e-?commerce (?:founder|store owner|operator)|"
            r"DTC (?:founder|operator|brand)|"
            r"agency|freelancer)\b", re.I,
        ),
        re.compile(r"\bI (?:run|own|manage|operate) (?:a |my )?\b", re.I),
        re.compile(r"\b\$\d+(?:k|K) (?:/(?:mo|month)|monthly|in (?:revenue|GMV|sales))\b", re.I),
    ),
    "influence_signals": (
        # Engagement-shape signals: counts of public engagements,
        # explicit upvote/comment-volume references.
        re.compile(r"\b(?:upvotes?|votes?|likes?)\b", re.I),
        re.compile(r"\b\d+\s+(?:comments?|replies|responses?)\b", re.I),
    ),
    "communication_style": (
        re.compile(r"\b(?:I think|honestly|frankly|to be fair)\b", re.I),
        re.compile(r"\b(?:rant|vent|hot take|unpopular opinion)\b", re.I),
    ),
    # interests: deliberately empty — too generic to heuristically tag.
    # geography_broad: deliberately empty — extracted from URL/source not body.
}


_NEAR_MISS_LO = 22
_NEAR_MISS_HI = 26
_THRESHOLD = 27   # unchanged
_MIN_EXCERPT_CHARS = 60
_MAX_EXCERPT_CHARS = 350
_MAX_EXCERPTS_PER_PAGE_PER_FIELD = 2


# ---------------------------------------------------------------------------
# Excerpt sliding-window scanner: returns (field, excerpt) candidates
# whose excerpt is a verbatim substring of `body` matching one of the
# field's keyword patterns.
# ---------------------------------------------------------------------------


def _extract_candidate_excerpts(body: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not body:
        return out
    # Split on paragraph boundaries first; expand to char windows when
    # needed.
    paragraphs = re.split(r"\n\s*\n+", body)
    for field, patterns in _TRAIT_FIELD_KEYWORDS.items():
        if not patterns:
            continue
        hits: list[str] = []
        for para in paragraphs:
            for pat in patterns:
                m = pat.search(para)
                if not m:
                    continue
                # Snap to a sentence-bounded window around the match.
                start = max(0, m.start() - 100)
                end = min(len(para), m.end() + 200)
                # Prefer to start at a sentence boundary.
                snapped_start = para.rfind(". ", 0, start) + 2
                if snapped_start >= 2 and snapped_start < start + 50:
                    start = snapped_start
                # Snap end to the nearest sentence terminator.
                snapped_end = para.find(". ", end - 50, end + 80)
                if snapped_end != -1:
                    end = snapped_end + 1
                excerpt = para[start:end].strip()
                if (
                    _MIN_EXCERPT_CHARS <= len(excerpt) <= _MAX_EXCERPT_CHARS
                    and excerpt in body  # verbatim substring invariant
                    and excerpt not in hits
                ):
                    hits.append(excerpt)
                if len(hits) >= _MAX_EXCERPTS_PER_PAGE_PER_FIELD:
                    break
            if len(hits) >= _MAX_EXCERPTS_PER_PAGE_PER_FIELD:
                break
        for h in hits:
            out.append((field, h))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _amain() -> int:
    _load_env()
    from sqlalchemy import select

    from assembly.db import get_sessionmaker
    from assembly.models.persona import (
        PersonaEvidenceLink,
        PersonaRecord,
        PersonaTrait,
        SourceRecord,
    )
    from assembly.pipeline.audience_retrieval import (
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.run_scoped_topup.executor import (
        _load_audience_inputs,
    )
    from assembly.pipeline.target_society import (
        AMBORAS_BRIEF,
        build_target_society_plan,
    )

    sm = get_sessionmaker()
    print("=" * 64)
    print("Phase 8.3B-LIVE-2A — Firecrawl Evidence Mapping (DRY RUN)")
    print("=" * 64)
    print("READ-ONLY. No DB writes. No LLM. No live Firecrawl.")

    # ---- 1. Load 3 firecrawl source_records ---------------------------
    async with sm() as s:
        firecrawl_rows = (await s.execute(
            select(SourceRecord)
            .where(SourceRecord.source_kind == "firecrawl_v1_scrape")
            .order_by(SourceRecord.captured_at.desc())
        )).scalars().all()
    print(f"\nfirecrawl source_records loaded: {len(firecrawl_rows)}")
    if not firecrawl_rows:
        print("ERROR: no firecrawl_v1_scrape rows found. Aborting.")
        return 2

    # ---- 2. Find matching Tavily source_records by URL ----------------
    async with sm() as s:
        tavily_rows = (await s.execute(
            select(SourceRecord)
            .where(SourceRecord.source_kind == "tavily_search_extract")
            .where(SourceRecord.source_url.is_not(None))
        )).scalars().all()
    tavily_by_url: dict[str, SourceRecord] = {}
    for r in tavily_rows:
        if r.source_url:
            tavily_by_url[r.source_url] = r
    print(f"tavily source_records indexed by URL: {len(tavily_by_url)}")

    # ---- 3. Find all evidence-links targeting any of those Tavily rows
    matched_tavily_ids: list[UUID] = []
    fc_to_tv: dict[UUID, UUID] = {}  # firecrawl_id -> tavily_id
    for fc in firecrawl_rows:
        # Try: same URL match.
        url = fc.source_url
        if url and url in tavily_by_url:
            tv = tavily_by_url[url]
            fc_to_tv[fc.id] = tv.id
            matched_tavily_ids.append(tv.id)
            continue
        # Fallback: requested_url / final_url in metadata
        md = fc.metadata_ or {}
        for key in ("requested_url", "final_url"):
            cand = md.get(key)
            if cand and cand in tavily_by_url:
                tv = tavily_by_url[cand]
                fc_to_tv[fc.id] = tv.id
                matched_tavily_ids.append(tv.id)
                break
    print(f"firecrawl→tavily mappings: {len(fc_to_tv)} of {len(firecrawl_rows)}")

    # ---- 4. Personas already using those matched Tavily rows ----------
    async with sm() as s:
        existing_links = (await s.execute(
            select(PersonaEvidenceLink)
            .where(PersonaEvidenceLink.source_record_id.in_(matched_tavily_ids))
        )).scalars().all() if matched_tavily_ids else []
    # Index BOTH ways: by persona (for stats) and by tavily_id (for the
    # plan loop below; the original v1 mistakenly used tv_id against the
    # by-persona dict and returned empty).
    personas_using_matched: dict[UUID, list[PersonaEvidenceLink]] = defaultdict(list)
    personas_by_tavily_id: dict[UUID, set[UUID]] = defaultdict(set)
    for el in existing_links:
        personas_using_matched[el.persona_id].append(el)
        personas_by_tavily_id[el.source_record_id].add(el.persona_id)
    print(
        f"existing personas with evidence-links to matched Tavily rows: "
        f"{len(personas_using_matched)}"
    )

    # ---- 5. Audience retrieval to find near-miss personas (22-26) -----
    print("\nrunning audience retrieval (read-only) to compute scores…")
    plan = build_target_society_plan(AMBORAS_BRIEF)
    audience_inputs, domain_map = await _load_audience_inputs(sm)
    audience = retrieve_personas_for_target_society(
        brief=AMBORAS_BRIEF,
        plan=plan,
        personas=audience_inputs,
        domain_by_record_id=domain_map,
    )
    by_pid: dict[UUID, dict] = {}
    for m in audience.matched_personas:
        by_pid[UUID(m.persona_id)] = {
            "score": m.relevance_score,
            "classification": m.classification.value,
            "category": m.matched_category_key,
            "name": m.display_name,
        }
    for ex in audience.excluded_personas:
        # Excluded personas still carry a `score` field on this surface.
        try:
            pid = UUID(ex.persona_id)
        except Exception:
            continue
        if pid not in by_pid:
            by_pid[pid] = {
                "score": ex.score,
                "classification": "not_relevant",
                "category": ex.best_possible_category or "?",
                "name": ex.display_name,
            }
    near_miss_pids = {
        pid: info for pid, info in by_pid.items()
        if _NEAR_MISS_LO <= info["score"] <= _NEAR_MISS_HI
    }
    print(
        f"near-miss personas (scores {_NEAR_MISS_LO}-{_NEAR_MISS_HI}, "
        f"under unchanged threshold {_THRESHOLD}): {len(near_miss_pids)}"
    )

    # ---- 6. Per-Firecrawl-page candidate excerpts ---------------------
    fc_excerpts: dict[UUID, list[tuple[str, str]]] = {}
    for fc in firecrawl_rows:
        body = fc.content or ""
        candidates = _extract_candidate_excerpts(body)
        fc_excerpts[fc.id] = candidates

    # ---- 7. For each candidate persona × Firecrawl page, build plan ---
    enrichment_plans: list[dict] = []

    # Set of personas to consider: existing-evidence personas (using
    # matched Tavily rows) + near-miss personas (whose existing evidence
    # might overlap with Firecrawl URLs).
    all_candidate_pids = set(personas_using_matched.keys()) | set(near_miss_pids.keys())
    if all_candidate_pids:
        async with sm() as s:
            persona_records = (await s.execute(
                select(PersonaRecord).where(PersonaRecord.id.in_(all_candidate_pids))
            )).scalars().all()
            traits_rows = (await s.execute(
                select(PersonaTrait).where(PersonaTrait.persona_id.in_(all_candidate_pids))
            )).scalars().all()
        traits_by_pid: dict[UUID, list[PersonaTrait]] = defaultdict(list)
        for t in traits_rows:
            traits_by_pid[t.persona_id].append(t)
        persona_by_pid = {p.id: p for p in persona_records}
    else:
        persona_by_pid = {}
        traits_by_pid = defaultdict(list)

    # For each Firecrawl row, link to (a) personas using the same Tavily
    # source and (b) near-miss personas (broader pool — they may not yet
    # use this URL but the Firecrawl evidence could plug a coverage gap).
    for fc in firecrawl_rows:
        tv_id = fc_to_tv.get(fc.id)
        candidates = fc_excerpts.get(fc.id, [])

        # 7a. Personas already using the matching Tavily row → enrichment
        # candidates with strongest "already-grounded" link.
        if tv_id is not None:
            for pid_id in personas_by_tavily_id.get(tv_id, set()):
                p = persona_by_pid.get(pid_id)
                if p is None:
                    continue
                info = by_pid.get(pid_id, {})
                existing_field_names = {
                    t.field_name for t in traits_by_pid.get(pid_id, [])
                }
                # Trait fields where the persona has NO trait or only
                # weakly-supported traits — these are the "fillable"
                # gaps where Firecrawl excerpts could strengthen.
                gap_fields = [
                    f for f in _TRAIT_FIELD_KEYWORDS.keys()
                    if f not in existing_field_names
                ]
                # For each candidate excerpt aligned with a gap field,
                # surface the (excerpt, field) pair.
                surfaced: list[dict] = []
                for field, excerpt in candidates:
                    surfaced.append({
                        "candidate_field": field,
                        "is_gap_for_persona": field in gap_fields,
                        "excerpt": excerpt,
                        "excerpt_chars": len(excerpt),
                        "verbatim_substring_of_firecrawl_body": (
                            excerpt in (fc.content or "")
                        ),
                    })
                enrichment_plans.append({
                    "link_kind": "existing_evidence",
                    "persona_id": str(pid_id),
                    "display_name": p.display_name,
                    "current_relevance_score": info.get("score"),
                    "current_classification": info.get("classification"),
                    "matched_category_key": info.get("category"),
                    "tavily_source_id": str(tv_id),
                    "firecrawl_source_id": str(fc.id),
                    "firecrawl_source_url": fc.source_url,
                    "existing_traits_count": len(
                        traits_by_pid.get(pid_id, [])
                    ),
                    "existing_trait_fields": sorted(existing_field_names),
                    "candidate_excerpts": surfaced,
                    "reason_for_consideration": (
                        "Persona already has an evidence_link to this "
                        "URL's Tavily source; Firecrawl provides deeper "
                        "body for the same URL."
                    ),
                })

        # 7b. Near-miss personas who do NOT yet use this URL — flag as
        # broader-enrichment candidates. Surface ALL candidate excerpts
        # (both gap-field and existing-field), categorizing each so the
        # operator can see which excerpts would strengthen existing
        # traits vs. fill new ones. Score lift mostly comes from
        # strengthening existing fields with deeper evidence, NOT from
        # adding gap-field traits the persona doesn't have yet.
        for pid_id, info in near_miss_pids.items():
            if tv_id is not None and pid_id in personas_by_tavily_id.get(
                tv_id, set()
            ):
                # Already covered by 7a (existing_evidence link).
                continue
            p = persona_by_pid.get(pid_id)
            if p is None:
                continue
            existing_field_names = {
                t.field_name for t in traits_by_pid.get(pid_id, [])
            }
            gap_fields = [
                f for f in _TRAIT_FIELD_KEYWORDS.keys()
                if f not in existing_field_names
            ]
            relevant_excerpts = [
                {
                    "candidate_field": field,
                    "is_gap_for_persona": field in gap_fields,
                    "excerpt": excerpt,
                    "excerpt_chars": len(excerpt),
                    "verbatim_substring_of_firecrawl_body": (
                        excerpt in (fc.content or "")
                    ),
                }
                for field, excerpt in candidates
            ]
            if not relevant_excerpts:
                continue
            enrichment_plans.append({
                "link_kind": "broader_near_miss",
                "persona_id": str(pid_id),
                "display_name": p.display_name,
                "current_relevance_score": info.get("score"),
                "current_classification": info.get("classification"),
                "matched_category_key": info.get("category"),
                "tavily_source_id": str(tv_id) if tv_id else None,
                "firecrawl_source_id": str(fc.id),
                "firecrawl_source_url": fc.source_url,
                "existing_traits_count": len(
                    traits_by_pid.get(pid_id, [])
                ),
                "existing_trait_fields": sorted(existing_field_names),
                "candidate_excerpts": relevant_excerpts,
                "reason_for_consideration": (
                    "Persona is a near-miss (score in 22-26 band) and "
                    "this Firecrawl page surfaces excerpts in trait "
                    "fields the persona currently has gaps in."
                ),
            })

    # ---- 8. Decide usability per Firecrawl row ------------------------
    fc_usability: list[dict] = []
    for fc in firecrawl_rows:
        cands = fc_excerpts.get(fc.id, [])
        verbatim_ok = all(
            ex in (fc.content or "") for _, ex in cands
        )
        plans_for_fc = [
            p for p in enrichment_plans
            if p["firecrawl_source_id"] == str(fc.id)
        ]
        body = fc.content or ""

        # 8.3B-LIVE-2A finding: content-quality heuristics. Even when
        # mapping + plans succeed, the actual evidence may be dominated
        # by boilerplate (nav menus, breadcrumbs) or be a bot-protection
        # placeholder. The dry-run must surface this honestly so
        # 8.3B-LIVE-2B is not approved on a flawed evidence base.
        nav_link_lines = sum(
            1 for ln in body.splitlines()
            if re.match(r"^\s*[-*]\s*\[.*\]\(http.*\)\s*$", ln)
        )
        total_nonempty_lines = sum(
            1 for ln in body.splitlines() if ln.strip()
        )
        boilerplate_ratio = (
            (nav_link_lines / total_nonempty_lines)
            if total_nonempty_lines else 0.0
        )
        # Bot-protection / placeholder detection
        botblock_markers = (
            "Something went wrong. Wait a moment and try again",
            "Please enable cookies",
            "verify you are human",
            "captcha",
        )
        looks_botblocked = any(
            m.lower() in body.lower() for m in botblock_markers
        )
        # Substantive-content excerpts: those that DON'T look like nav
        # links, breadcrumbs, or pure URL/markup chunks.
        def _is_substantive(text: str) -> bool:
            if "Skip to" in text or "→" in text:
                return False
            if text.strip().startswith("[") and "](" in text and len(text) < 250:
                return False
            if text.strip().startswith("# [") and "](" in text:
                return False  # title-as-link
            # Must contain at least one full sentence-shaped clause
            if not re.search(r"[a-z]\s+[a-z]{2,}\s+[a-z]{2,}", text, re.I):
                return False
            return True

        substantive_excerpts = sum(
            1 for _, ex in cands if _is_substantive(ex)
        )

        fc_usability.append({
            "firecrawl_source_id": str(fc.id),
            "source_url": fc.source_url,
            "domain": (fc.metadata_ or {}).get("domain"),
            "title": (fc.metadata_ or {}).get("title"),
            "body_chars": len(body),
            "candidate_excerpt_count": len(cands),
            "substantive_excerpt_count": substantive_excerpts,
            "all_candidates_verbatim": verbatim_ok,
            "boilerplate_link_line_ratio": round(boilerplate_ratio, 3),
            "looks_bot_protected": looks_botblocked,
            "candidate_field_breakdown": dict(
                {f: sum(1 for c in cands if c[0] == f) for f in {x for x, _ in cands}}
            ),
            "tavily_match_found": fc.id in fc_to_tv,
            "personas_already_using_tavily_match": (
                len(personas_by_tavily_id.get(fc_to_tv[fc.id], set()))
                if fc.id in fc_to_tv else 0
            ),
            "enrichment_plans_generated": len(plans_for_fc),
            "usable_for_persona_enrichment": (
                fc.id in fc_to_tv
                and substantive_excerpts >= 1
                and verbatim_ok
                and not looks_botblocked
                and boilerplate_ratio < 0.5
                and len(plans_for_fc) > 0
            ),
            "unusable_reason": (
                None if (
                    fc.id in fc_to_tv
                    and substantive_excerpts >= 1
                    and verbatim_ok
                    and not looks_botblocked
                    and boilerplate_ratio < 0.5
                    and len(plans_for_fc) > 0
                )
                else (
                    "no Tavily URL match"
                    if fc.id not in fc_to_tv
                    else "bot-protection placeholder"
                    if looks_botblocked
                    else f"boilerplate-dominated body "
                         f"(nav-link ratio={boilerplate_ratio:.2f})"
                    if boilerplate_ratio >= 0.5
                    else "no substantive excerpts surfaced"
                    if substantive_excerpts < 1
                    else "verbatim invariant violated"
                    if not verbatim_ok
                    else "no candidate persona to enrich"
                )
            ),
        })

    # ---- 9. Save audit JSON ------------------------------------------
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = {
        "phase": "8_3b_live_2a_firecrawl_evidence_mapping_dry_run",
        "started_at": datetime.now(UTC).isoformat(),
        "firecrawl_rows_loaded": len(firecrawl_rows),
        "tavily_index_size": len(tavily_by_url),
        "firecrawl_to_tavily_mappings": {
            str(k): str(v) for k, v in fc_to_tv.items()
        },
        "mapping_success_count": len(fc_to_tv),
        "mapping_failure_count": len(firecrawl_rows) - len(fc_to_tv),
        "existing_personas_using_matched_tavily": len(personas_using_matched),
        "near_miss_personas_count": len(near_miss_pids),
        "near_miss_personas": [
            {
                "persona_id": str(pid),
                "name": info["name"],
                "score": info["score"],
                "category": info["category"],
            } for pid, info in sorted(
                near_miss_pids.items(),
                key=lambda x: -x[1]["score"],
            )
        ],
        "firecrawl_usability_per_row": fc_usability,
        "enrichment_plans": enrichment_plans,
        "enrichment_plan_count": len(enrichment_plans),
        "any_excerpt_failed_verbatim_check": any(
            not c["verbatim_substring_of_firecrawl_body"]
            for p in enrichment_plans
            for c in p["candidate_excerpts"]
        ),
        "thresholds": {
            "near_miss_lo": _NEAR_MISS_LO,
            "near_miss_hi": _NEAR_MISS_HI,
            "relevance_threshold_unchanged": _THRESHOLD,
        },
        "no_db_writes_made_except_audit_file": True,
    }
    out_path = out_dir / "firecrawl_evidence_mapping_dry_run.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # ---- 10. Print operator-facing report ----------------------------
    print()
    print("=" * 64)
    print("Phase 8.3B-LIVE-2A — DRY-RUN MAPPING SUMMARY")
    print("=" * 64)
    print(f"firecrawl rows loaded: {len(firecrawl_rows)}")
    print(f"firecrawl→tavily mappings: {len(fc_to_tv)}/{len(firecrawl_rows)}")
    print(f"existing personas using matched Tavily: {len(personas_using_matched)}")
    print(f"near-miss personas (22-26): {len(near_miss_pids)}")
    print(f"enrichment plans generated: {len(enrichment_plans)}")
    print()
    print("per-firecrawl-row usability:")
    for u in fc_usability:
        print(
            f"  - {u['domain']} ({u['body_chars']} chars): "
            f"usable={u['usable_for_persona_enrichment']} "
            f"(plans={u['enrichment_plans_generated']}; "
            f"reason={u['unusable_reason']})"
        )
    print()
    print(f"→ audit JSON: {out_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
