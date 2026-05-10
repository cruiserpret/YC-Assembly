"""Phase 8.3B-LIVE-1 — Firecrawl live smoke extraction (operator-only).

Bounded, single-run extraction of 5–10 Amboras-related URLs already
discovered via Tavily. Proves live Firecrawl extraction + redaction +
sensitive-attribute scan + storage discipline. Does NOT create
personas, traits, or evidence links. Does NOT claim accuracy
improvement.

Pre-flight:
  * `FIRECRAWL_API_KEY` loaded from .env (NEVER printed).
  * Compliance memo at docs/source_compliance/firecrawl.md present.
  * `adapter_compliance_status[firecrawl_extract]` operator-flipped to
    `'approved'` with approver='phase_8_3b_live_1_firecrawl_smoke'.

In `finally:` (always, even on crash):
  * Status re-flipped to `'review'` with notes='post-smoke re-flip'.

Caps (also re-asserted in code):
  * MAX_URLS=10
  * MAX_CHARS=8000 (per record)
  * MIN_CHARS=80
  * TIMEOUT=30s
  * COST_HARD_CAP_USD=$1.00 (advisory; Firecrawl charges per request)

NEVER writes:
  * persona_records, persona_traits, persona_evidence_links
  * simulation_outputs, simulation_rounds, debate_turns,
    persona_graph_edges, persona_clusters, persona_opinions
  * any frontend / UI artifact
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for c in candidates:
        if c.is_file():
            load_dotenv(c, override=False)


# ---------------------------------------------------------------------------
# Caps (mirrored from compliance memo Section 7)
# ---------------------------------------------------------------------------

MAX_URLS = 10
MAX_CHARS = 8000
MIN_CHARS = 80
TIMEOUT_S = 30.0
COST_HARD_CAP_USD = Decimal("1.00")
APPROVER_LABEL = "phase_8_3b_live_1_5_firecrawl_content_quality_rerun"
HANDLE_SALT = "phase_8_3b_live_1_smoke_salt"


async def _amain() -> int:
    _load_env()
    if not os.environ.get("FIRECRAWL_API_KEY"):
        print("ERROR: FIRECRAWL_API_KEY not set after .env load. Aborting.")
        return 2

    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from assembly.db import get_sessionmaker
    from assembly.models import (
        Agent,
        AgentEdge,
        AgentResponse,
        DebateTurn,
        PersonaCluster,
        PersonaClusterMembership,
        PersonaEvidenceLink,
        PersonaGraphEdge,
        PersonaOpinion,
        PersonaRecord,
        PersonaTrait,
        SimulationOutput,
        SimulationRound,
        SourceRecord,
    )
    from assembly.pipeline.ingestion.compliance import (
        get_adapter_compliance_status,
        register_or_update_adapter_status,
    )
    from assembly.pipeline.ingestion.firecrawl import (
        FIRECRAWL_ADAPTER_NAME,
        FIRECRAWL_MEMO_PATH,
        FirecrawlBlockedPage,
        FirecrawlBodyRedactionFailed,
        FirecrawlBodyTooShort,
        FirecrawlBoilerplateDominated,
        FirecrawlBotProtectionPlaceholder,
        FirecrawlClient,
        FirecrawlError,
        assert_firecrawl_approved,
    )
    from assembly.pipeline.ingestion.redaction import prepare_source_record_insert
    from assembly.pipeline.ingestion.run_summary import NormalizedSourcePayload

    sm = get_sessionmaker()

    # ---- Banner / pre-flight echo --------------------------------------
    print("=" * 64)
    print("Phase 8.3B-LIVE-1 — Firecrawl SMOKE EXTRACTION (operator-only)")
    print("=" * 64)
    print(
        "policy: max_urls=10, max_chars=8000, min_chars=80, "
        "timeout=30s, cost_hard_cap=$1.00"
    )
    print(
        "FIRECRAWL_API_KEY: detected in environment (value not printed)"
    )

    # ---- Curate URL list from prior Tavily Amboras corpus -------------
    target_urls: list[tuple[str, str, str]] = []
    async with sm() as session:
        rows = (await session.execute(
            select(
                SourceRecord.id,
                SourceRecord.source_url,
                SourceRecord.metadata_,
            ).where(SourceRecord.source_url.is_not(None))
        )).all()
    amboras = [r for r in rows if (r.metadata_ or {}).get("target_brief") == "amboras"]
    human = [
        r for r in amboras
        if (r.metadata_ or {}).get("likely_human_signal_candidate") is True
    ]
    by_domain: dict = {}
    for r in human:
        d = (r.metadata_ or {}).get("domain") or "unknown"
        by_domain.setdefault(d, []).append(r)
    priority_domains = (
        "community.shopify.com", "reddit.com", "old.reddit.com",
        "indiehackers.com", "medium.com", "merchantmaverick.com",
        "forbes.com", "quora.com",
    )
    for d in priority_domains:
        if d in by_domain and by_domain[d]:
            for r in by_domain[d][:2]:
                title = (r.metadata_ or {}).get("title", "")
                target_urls.append((r.source_url, d, title or ""))
                if len(target_urls) >= MAX_URLS:
                    break
        if len(target_urls) >= MAX_URLS:
            break
    if len(target_urls) < 5:
        print(
            f"ERROR: only {len(target_urls)} curated URLs; "
            "smoke run requires 5-10. Aborting."
        )
        return 2
    print(f"\ncurated {len(target_urls)} URLs for smoke extraction:")
    for i, (u, d, t) in enumerate(target_urls, 1):
        print(f"  {i:2}. [{d}] {t[:80]}")
        print(f"      {u}")

    # ---- Snapshot forbidden tables BEFORE -----------------------------
    forbidden_models = [
        SimulationOutput, SimulationRound,
        PersonaGraphEdge, PersonaCluster, PersonaClusterMembership,
        PersonaOpinion, PersonaRecord, PersonaTrait,
        PersonaEvidenceLink, Agent, AgentResponse, DebateTurn, AgentEdge,
    ]
    async with sm() as session:
        before = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }
        before["SourceRecord"] = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
    print(f"\nforbidden-table row counts BEFORE:")
    for k, v in before.items():
        print(f"  {k}: {v}")

    # ---- Operator-flip: status='approved' -----------------------------
    print(f"\noperator-flip {FIRECRAWL_ADAPTER_NAME} → status='approved'")
    await register_or_update_adapter_status(
        sm,
        adapter_name=FIRECRAWL_ADAPTER_NAME,
        status="approved",
        memo_path=FIRECRAWL_MEMO_PATH,
        approver=APPROVER_LABEL,
        approved_at=datetime.now(UTC),
        notes=(
            "local-dev same-URL Firecrawl content-quality rerun only; "
            "not production/legal approval"
        ),
    )

    accepted: list[dict] = []
    rejections: list[dict] = []
    started = time.monotonic()
    error_in_run: str | None = None
    try:
        # Verify gate (should pass now after the flip)
        await assert_firecrawl_approved(sm)
        print("compliance gate: APPROVED for this run.")

        # ---- Live extraction loop ------------------------------------
        client = FirecrawlClient(
            max_chars=MAX_CHARS,
            min_chars=MIN_CHARS,
            timeout_s=TIMEOUT_S,
        )
        for idx, (url, domain, title) in enumerate(target_urls, 1):
            print(f"\n[{idx}/{len(target_urls)}] {url}")
            try:
                page = await client.extract(url)
            except FirecrawlBlockedPage as e:
                print(f"  REJECTED [{e.reason_code}]: {e}")
                rejections.append({
                    "url": url, "domain": domain,
                    "reason_code": e.reason_code,
                    "message": str(e)[:300],
                })
                continue
            except FirecrawlBotProtectionPlaceholder as e:
                print(f"  REJECTED [BOT_OR_PLACEHOLDER_CONTENT]: {e}")
                rejections.append({
                    "url": url, "domain": domain,
                    "reason_code": "BOT_OR_PLACEHOLDER_CONTENT",
                    "message": str(e)[:300],
                })
                continue
            except FirecrawlBoilerplateDominated as e:
                print(f"  REJECTED [BOILERPLATE_DOMINATED]: {e}")
                rejections.append({
                    "url": url, "domain": domain,
                    "reason_code": "BOILERPLATE_DOMINATED",
                    "message": str(e)[:300],
                })
                continue
            except FirecrawlBodyTooShort as e:
                print(f"  REJECTED [BODY_TOO_SHORT]: {e}")
                rejections.append({
                    "url": url, "domain": domain,
                    "reason_code": "BODY_TOO_SHORT",
                    "message": str(e)[:300],
                })
                continue
            except FirecrawlBodyRedactionFailed as e:
                print(f"  REJECTED [REDACTION_FAILED]: {e}")
                rejections.append({
                    "url": url, "domain": domain,
                    "reason_code": "REDACTION_FAILED",
                    "message": str(e)[:300],
                })
                continue
            except FirecrawlError as e:
                print(f"  REJECTED [FIRECRAWL_ERROR]: "
                      f"{type(e).__name__}: {e}")
                rejections.append({
                    "url": url, "domain": domain,
                    "reason_code": "FIRECRAWL_ERROR",
                    "message": f"{type(e).__name__}: {str(e)[:240]}",
                })
                continue

            # ---- Persistence path: prepare_source_record_insert ----
            payload = NormalizedSourcePayload(
                source_url=page.requested_url,
                captured_at=page.captured_at,
                content=page.body_markdown,
                raw_handle=None,  # Firecrawl never surfaces handles
                metadata={
                    "scraped_via": page.metadata.scraped_via,
                    "requested_url": page.metadata.requested_url,
                    "final_url": page.metadata.final_url,
                    "title": page.metadata.title,
                    "source_status_code": page.metadata.source_status_code,
                    "content_type": page.metadata.content_type,
                    "page_lang": page.metadata.page_lang,
                    "robots_allowed": page.metadata.robots_allowed,
                    "domain": domain,
                    "target_brief": "amboras",
                    "phase": "8_3b_live_1_5_firecrawl_content_quality_rerun",
                    "operator_run": True,
                    "test_fixture": False,
                    "truncated": page.truncated,
                    "body_chars": page.body_chars,
                },
                language=page.metadata.page_lang or "en",
            )
            insert_dict, rejection = prepare_source_record_insert(
                payload,
                source_kind="firecrawl_v1_scrape",
                compliance_tag="public_api",
                ingested_by="phase_8_3b_live_1_firecrawl_smoke",
                salt=HANDLE_SALT,
                # Phase 8.3B-LIVE-1.5: Firecrawl-specific persistence
                # cap. Tavily callers don't pass this; their 4000-char
                # cap is unchanged.
                max_content_chars=8000,
            )
            if insert_dict is None:
                msg = (
                    rejection.message[:300]
                    if rejection is not None else "<no message>"
                )
                code = (
                    rejection.reason_code
                    if rejection is not None else "UNKNOWN"
                )
                print(f"  REJECTED [{code}] post-pipeline: {msg}")
                rejections.append({
                    "url": url, "domain": domain,
                    "reason_code": code,
                    "message": msg,
                })
                continue

            # ---- Insert SourceRecord row (dedup-aware) ------------
            # The `uq_source_records_kind_hash` unique constraint is
            # the dedup gate; same pattern the Tavily adapter base
            # class uses (catch IntegrityError and treat as deduped).
            try:
                async with sm() as session:
                    async with session.begin():
                        session.add(SourceRecord(id=uuid4(), **insert_dict))
                print(
                    f"  ACCEPTED: body_chars={page.body_chars}, "
                    f"truncated={page.truncated}"
                )
                accepted.append({
                    "url": url, "domain": domain,
                    "title": page.metadata.title,
                    "body_chars": page.body_chars,
                    "truncated": page.truncated,
                    "deduped": False,
                })
            except IntegrityError as e:
                if "uq_source_records_kind_hash" in str(e.orig):
                    print("  ACCEPTED but DEDUPED (kind+hash collision)")
                    accepted.append({
                        "url": url, "domain": domain,
                        "title": page.metadata.title,
                        "body_chars": page.body_chars,
                        "truncated": page.truncated,
                        "deduped": True,
                    })
                else:
                    print(f"  REJECTED [DB_INTEGRITY_ERROR]: "
                          f"{str(e.orig)[:200]}")
                    rejections.append({
                        "url": url, "domain": domain,
                        "reason_code": "DB_INTEGRITY_ERROR",
                        "message": str(e.orig)[:300],
                    })
    except Exception as e:
        error_in_run = f"{type(e).__name__}: {e}"
        print(f"\nUNEXPECTED ERROR in extraction loop: {error_in_run}")
    finally:
        # ---- Always re-flip status to 'review' --------------------
        print(f"\nfinally: re-flip {FIRECRAWL_ADAPTER_NAME} → status='review'")
        await register_or_update_adapter_status(
            sm,
            adapter_name=FIRECRAWL_ADAPTER_NAME,
            status="review",
            memo_path=FIRECRAWL_MEMO_PATH,
            approver=None,
            approved_at=None,
            notes="post-smoke re-flip; live use no longer authorized",
        )
        post_status = await get_adapter_compliance_status(
            sm, FIRECRAWL_ADAPTER_NAME,
        )
        print(
            f"final compliance row: status={post_status.status if post_status else '<none>'}, "
            f"approver={post_status.approver if post_status else '<none>'}"
        )

    elapsed = time.monotonic() - started

    # ---- Snapshot forbidden tables AFTER ----------------------------
    async with sm() as session:
        after = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }
        after["SourceRecord"] = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
    deltas = {
        k: (before[k], after[k]) for k in before if before[k] != after[k]
    }

    # ---- Save audit JSON --------------------------------------------
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    body_chars = [a["body_chars"] for a in accepted]
    summary = {
        "phase": "8_3b_live_1_5_firecrawl_content_quality_rerun",
        "started_at": datetime.now(UTC).isoformat(),
        "runtime_s": round(elapsed, 1),
        "urls_attempted": len(target_urls),
        "domains_attempted": sorted({d for _, d, _ in target_urls}),
        "accepted_count": len(accepted),
        "rejected_count": len(rejections),
        "deduped_count": sum(1 for a in accepted if a["deduped"]),
        "accepted": accepted,
        "rejections": rejections,
        "body_chars_stats": {
            "min": min(body_chars) if body_chars else None,
            "max": max(body_chars) if body_chars else None,
            "avg": (sum(body_chars) // len(body_chars)) if body_chars else None,
            "sum": sum(body_chars),
        },
        "any_truncated": any(a["truncated"] for a in accepted),
        "redaction_ran_before_storage": True,
        "sensitive_scan_ran_before_storage": True,
        "compliance_gate_status_at_run": "approved",
        "compliance_gate_status_after_finally": (
            post_status.status if post_status else "<none>"
        ),
        "approver_during_run": APPROVER_LABEL,
        "forbidden_table_deltas": deltas,
        "error_in_run": error_in_run,
    }
    snap_path = out_dir / "firecrawl_live_smoke_v1.json"
    snap_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(
        f"\n→ audit summary: {snap_path}"
    )
    print(f"\nruntime: {elapsed:.1f}s")
    print(
        f"accepted: {len(accepted)}, rejected: {len(rejections)}, "
        f"deduped: {summary['deduped_count']}"
    )
    print(f"forbidden-table deltas (must be empty): {deltas}")
    return 0 if error_in_run is None else 1


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
