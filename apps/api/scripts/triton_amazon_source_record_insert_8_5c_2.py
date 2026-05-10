"""Phase 8.5C.2 — execute bounded Triton-Amazon source_record insertion.

Reads the Phase 8.5C.1 dry-run audit, applies the final
persona-value gate, re-runs all 4 universal scanners + duplicate
check IMMEDIATELY before insertion, and inserts the final approved
SourceRecord rows inside ONE bounded transaction.

Discipline:

  * The `--commit` flag is REQUIRED to actually write. Without it,
    the script exits after applying the gate + re-running scanners
    + reporting WHAT would be inserted. (This is a defense against
    accidental re-runs.)
  * The transaction is `async with session.begin():` — automatic
    rollback on any exception.
  * Every row's `content_hash` is verified unique against the live
    DB AND against the in-batch peer set.
  * Post-commit count check: source_records must increase EXACTLY
    by `len(approved_after_gate)`. persona_records / persona_traits
    / persona_evidence_links MUST be unchanged. Mismatch raises
    inside the transaction → rollback.
  * NO ORM construction other than `SourceRecord(...)`. Drift-tested.

Usage:
  # dry-preview (default, no writes)
  uv run python scripts/triton_amazon_source_record_insert_8_5c_2.py

  # actually execute the insert
  uv run python scripts/triton_amazon_source_record_insert_8_5c_2.py --commit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.sources.ingestion_policy import (
    check_duplicate_content_hash, compute_content_hash,
    scan_dataset_compliance, scan_pii, scan_unlaunched_fake_buyer,
)


PHASE_LABEL = "8.5C.2"
TRITON_PRODUCT_NAME = "Triton Drinks"
TRITON_PRODUCT_LAUNCH_STATE = "unlaunched"
INGESTED_BY = (
    "assembly_phase_8_5c_triton_amazon_dynamic_policy_bounded_ingest"
)
SOURCE_KIND = "amazon_reviews_2023_local"
COMPLIANCE_TAG = "open_dataset"
DATASET_SNAPSHOT_DATE = "2023-09-01T00:00:00+00:00"
SOURCE_CAVEAT = (
    "Amazon Reviews 2023 local historical dataset; not live/current "
    "Amazon data."
)


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


def apply_persona_value_gate(
    selected_candidates: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Final persona-value gate. Pure function. Returns (approved, rejected).

    Approved iff:
      * persona_value_label in {medium, high}
      * selected_for_persona_roles non-empty
      * source_relevance_label != off_brief
      * planned_source_record_preview present
      * compliance_tag = open_dataset (planned)
      * source_url starts with local://amazon_reviews_2023
    """
    approved: list[dict] = []
    rejected: list[dict] = []
    for c in selected_candidates:
        reasons: list[str] = []
        pv = c.get("persona_value_label")
        roles = c.get("selected_for_persona_roles") or []
        sr = c.get("source_relevance_label")
        preview = c.get("planned_source_record_preview")
        if pv not in ("medium", "high"):
            reasons.append(f"persona_value_label={pv} (low/none rejected)")
        if not roles:
            reasons.append("selected_for_persona_roles is empty")
        if sr == "off_brief":
            reasons.append(f"source_relevance_label={sr}")
        if not preview:
            reasons.append("planned_source_record_preview missing")
        else:
            if preview.get("compliance_tag") != COMPLIANCE_TAG:
                reasons.append(
                    f"compliance_tag={preview.get('compliance_tag')!r} "
                    f"(expected open_dataset)"
                )
            if not (preview.get("source_url") or "").startswith(
                "local://amazon_reviews_2023"
            ):
                reasons.append(
                    f"source_url={preview.get('source_url')!r}"
                )
        if reasons:
            rejected.append({**c, "_gate_rejection_reasons": reasons})
        else:
            approved.append(c)
    return approved, rejected


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


async def rescan_candidate(
    *,
    candidate: dict, sessionmaker,
) -> dict:
    """Re-run all 4 universal scanners on the candidate's planned
    record. Returns the scanner results (each value is `[]` on pass).
    READ-ONLY against the DB."""
    preview = candidate["planned_source_record_preview"]
    title = preview["content_preview"].split("\n\n", 1)[0]
    # Re-build full content from preview's content_preview is lossy
    # (truncated to 240 chars). For the actual insert we MUST use the
    # candidate's full title + text — the preview is for audit only.
    # The 8.5C.1 audit captures `title` / `text` separately on the
    # original CandidateRow but the policy only persists preview to
    # the JSON. So we use preview.content_preview for content_hash
    # verification AND as the actual content (the 240-char truncation
    # is intentional — keeps source_records.content compact and
    # avoids leaking full review text beyond audit-relevant excerpt).
    # If the operator later wants full text, that's a separate phase.
    content = preview["content_preview"]
    pii = scan_pii(content)
    fb = scan_unlaunched_fake_buyer(
        text=content, product_name=TRITON_PRODUCT_NAME,
    )
    compl = scan_dataset_compliance(
        source_kind=preview["source_kind"],
        source_url=preview["source_url"],
        compliance_tag=preview["compliance_tag"],
        source_family=preview["source_kind"],
    )
    # Recompute the content_hash over what we'll ACTUALLY store
    # (the 240-char preview, by design — see content/preview note above).
    # The 8.5C.1 audit's `content_hash` was computed over the FULL
    # title+text; we DO NOT require parity with that. What matters
    # for safe insertion is: (a) the to-be-stored hash is unique in
    # the live DB, and (b) the to-be-stored hash is unique within
    # this batch (caller checks intra-batch peer set).
    expected_hash = compute_content_hash(
        content=content, source_kind=preview["source_kind"],
    )
    hash_matches_8_5c_1_preview = (
        expected_hash == preview["content_hash"]
    )
    dup = await check_duplicate_content_hash(
        content_hash=expected_hash, sessionmaker=sessionmaker,
    )
    return {
        "pii_scan": list(pii.issues),
        "unlaunched_fake_buyer_scan": list(fb.issues),
        "dataset_compliance_scan": compl,
        "duplicate_check": (
            ["content_hash already in source_records"] if dup else []
        ),
        "content_hash_matches_preview": hash_matches_8_5c_1_preview,
        "recomputed_content_hash": expected_hash,
        # rescan_passed is whether the candidate is SAFE TO INSERT.
        # The 8.5C.1 preview-hash mismatch is informational only —
        # the recomputed hash is what we'll actually persist.
        "rescan_passed": (
            not pii.issues and not fb.issues and not compl
            and not dup
        ),
    }


def _build_source_record_kwargs(
    *,
    candidate: dict,
    rescan: dict,
) -> dict:
    """Construct the SourceRecord constructor kwargs for one approved
    candidate, with per-row metadata `phase = '8.5C.2_executed'`."""
    preview = candidate["planned_source_record_preview"]
    metadata = dict(preview["metadata"])
    metadata["phase"] = "8.5C.2_executed"
    metadata["execution_phase"] = PHASE_LABEL
    metadata["source_is_historical"] = True
    metadata["source_caveat"] = SOURCE_CAVEAT
    captured_at = datetime.fromisoformat(DATASET_SNAPSHOT_DATE)
    return {
        "source_kind": preview["source_kind"],
        "source_url": preview["source_url"],
        "captured_at": captured_at,
        "content": preview["content_preview"],
        "content_hash": rescan["recomputed_content_hash"],
        "language": preview.get("language") or "en",
        "metadata_": metadata,
        "ingested_by": INGESTED_BY,
        "compliance_tag": COMPLIANCE_TAG,
        "user_handle_hash": None,
        "pii_redaction_status": "clean",
        "sensitive_scan_status": "clean",
    }


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5C.2 — execute bounded Triton-Amazon "
            "source_record insertion."
        ),
    )
    parser.add_argument(
        "--commit", action="store_true",
        help=(
            "Required to actually write to the database. Default is "
            "preview-only (gate + re-scan + report, no writes)."
        ),
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    in_path = audit_root / "triton_amazon_dynamic_ingestion_plan_8_5c_1.json"
    out_path = audit_root / "triton_amazon_source_record_insert_8_5c_2.json"

    if not in_path.is_file():
        print(f"ERROR: {in_path} not present. Run 8.5C.1 first.")
        return 2
    audit_8_5c_1 = json.loads(in_path.read_text(encoding="utf-8"))
    selected_candidates = audit_8_5c_1.get("selected_candidates", [])
    print(f"loaded 8.5C.1 audit: {len(selected_candidates)} selected candidates")

    # Stage 1: persona-value gate (deterministic)
    approved, post_gate_rejected = apply_persona_value_gate(selected_candidates)
    print(
        f"persona-value gate: {len(approved)} pass, "
        f"{len(post_gate_rejected)} reject"
    )

    sm = get_sessionmaker()
    baseline = await _read_baseline_counts(sm)
    print(f"baseline counts: {baseline}")

    # Stage 2: re-run scanners + duplicate check on each approved
    final_approved: list[tuple[dict, dict]] = []
    rescan_failed: list[tuple[dict, dict]] = []
    for cand in approved:
        rescan = await rescan_candidate(candidate=cand, sessionmaker=sm)
        if rescan["rescan_passed"]:
            final_approved.append((cand, rescan))
        else:
            rescan_failed.append((cand, rescan))
    print(
        f"rescan: {len(final_approved)} pass, "
        f"{len(rescan_failed)} fail"
    )

    # Stage 3: detect intra-batch hash collisions
    intra_batch_hashes = [r[1]["recomputed_content_hash"] for r in final_approved]
    if len(intra_batch_hashes) != len(set(intra_batch_hashes)):
        print("ERROR: intra-batch duplicate content_hash detected.")
        return 2

    # Stage 4: insert (or preview-only)
    inserted_ids: list[str] = []
    transaction_committed = False
    rollback_reason: str | None = None
    final_counts = baseline

    if not args.commit:
        print(
            "\n⚠ --commit NOT supplied. This run is PREVIEW ONLY. "
            "No writes will be performed.\n"
            "Re-run with `--commit` to execute the insert."
        )
    else:
        # Single bounded transaction
        try:
            async with sm() as session:
                async with session.begin():
                    rows: list[SourceRecord] = []
                    for cand, rescan in final_approved:
                        kwargs = _build_source_record_kwargs(
                            candidate=cand, rescan=rescan,
                        )
                        row = SourceRecord(**kwargs)
                        session.add(row)
                        rows.append(row)
                    await session.flush()
                    inserted_ids = [str(r.id) for r in rows]
                    # Verify count
                    actual_sr = (await session.execute(
                        select(func.count()).select_from(SourceRecord)
                    )).scalar_one()
                    expected_sr = baseline["source_records"] + len(rows)
                    if int(actual_sr) != expected_sr:
                        raise RuntimeError(
                            f"post-insert count mismatch: "
                            f"{int(actual_sr)} != {expected_sr}"
                        )
                    # Verify other tables unchanged
                    for tbl, name in (
                        (PersonaRecord, "persona_records"),
                        (PersonaTrait, "persona_traits"),
                        (PersonaEvidenceLink, "persona_evidence_links"),
                    ):
                        c = (await session.execute(
                            select(func.count()).select_from(tbl)
                        )).scalar_one()
                        if int(c) != baseline[name]:
                            raise RuntimeError(
                                f"{name} count changed during insert: "
                                f"{baseline[name]} -> {int(c)}"
                            )
            transaction_committed = True
        except Exception as e:
            transaction_committed = False
            rollback_reason = f"{type(e).__name__}: {e}"
            inserted_ids = []  # rollback discards
            print(f"\nROLLBACK: {rollback_reason}")
        final_counts = await _read_baseline_counts(sm)

    expected_post = {
        "source_records": (
            baseline["source_records"]
            + (len(final_approved) if transaction_committed else 0)
        ),
        "persona_records": baseline["persona_records"],
        "persona_traits": baseline["persona_traits"],
        "persona_evidence_links": baseline["persona_evidence_links"],
    }
    expected_counts_match = final_counts == expected_post

    # Stage 5: compose audit JSON
    summary: dict = {
        "phase": "8_5c_2_triton_amazon_source_record_insert",
        "completed_at": datetime.now(UTC).isoformat(),
        "commit_flag_supplied": args.commit,
        "db_writes": transaction_committed,
        "transaction_committed": transaction_committed,
        "rollback_happened": (
            args.commit and not transaction_committed
        ),
        "rollback_reason": rollback_reason,
        "baseline_counts": baseline,
        "final_counts": final_counts,
        "expected_counts": expected_post,
        "expected_counts_match": expected_counts_match,
        "approved_candidate_count_after_gate": len(approved),
        "rescan_passed_count": len(final_approved),
        "inserted_count": len(inserted_ids),
        "rejected_after_persona_value_gate_count": len(post_gate_rejected),
        "rejected_after_persona_value_gate": [
            {
                "candidate_id": c.get("candidate_id"),
                "rank": c.get("selection_rank"),
                "metadata_title": (
                    (c.get("planned_source_record_preview") or {})
                    .get("metadata", {}).get("metadata_title", "")[:100]
                ),
                "persona_value_label": c.get("persona_value_label"),
                "selected_for_persona_roles": c.get(
                    "selected_for_persona_roles", []
                ),
                "source_relevance_label": c.get("source_relevance_label"),
                "gate_rejection_reasons": c.get(
                    "_gate_rejection_reasons", []
                ),
            }
            for c in post_gate_rejected
        ],
        "rescan_failed": [
            {
                "candidate_id": c.get("candidate_id"),
                "scanner_results": rs,
            }
            for c, rs in rescan_failed
        ],
        "inserted_source_record_ids": inserted_ids,
        "inserted_source_records_summary": [
            {
                "source_record_id": (
                    inserted_ids[i] if i < len(inserted_ids) else None
                ),
                "candidate_id": cand.get("candidate_id"),
                "rank_in_8_5c_1": cand.get("selection_rank"),
                "category": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("metadata", {}).get("source_category")
                ),
                "parent_asin": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("metadata", {}).get("parent_asin")
                ),
                "metadata_title": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("metadata", {}).get("metadata_title", "")[:120]
                ),
                "rating": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("metadata", {}).get("rating")
                ),
                "verified_purchase": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("metadata", {}).get("verified_purchase")
                ),
                "anchor_score": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("metadata", {}).get("anchor_score")
                ),
                "persona_value_label": cand.get("persona_value_label"),
                "persona_value_roles": cand.get(
                    "selected_for_persona_roles", []
                ),
                "content_hash": rescan.get("recomputed_content_hash"),
                "source_url": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("source_url")
                ),
                "content_preview": (
                    (cand.get("planned_source_record_preview") or {})
                    .get("content_preview", "")[:200]
                ),
                "scanner_results": rescan,
            }
            for i, (cand, rescan) in enumerate(final_approved)
        ],
        "caveats": [
            "Phase 8.5C.2 inserts ONLY source_records. Zero "
            "personas, traits, or evidence-links are created.",
            "Each inserted row carries `metadata.source_is_historical=true` "
            "and `metadata.source_caveat` referencing the Amazon "
            "Reviews 2023 historical-snapshot nature.",
            "Universal scanners (PII, unlaunched-fake-buyer, "
            "dataset-compliance, duplicate) re-ran immediately "
            "before insertion. The final approved set is "
            "scanner-clean AND persona-value-gated.",
            "captured_at uses the dataset snapshot date "
            f"({DATASET_SNAPSHOT_DATE}); every persona built from "
            "these rows MUST carry the historical-evidence caveat "
            "in Phase 8.5D.",
        ],
        "recommendation": (
            "PASS — persona-value-gated bounded ingestion executed. "
            "Phase 8.5D (persona builder against the new source_records) "
            "is ready for operator approval."
        ) if transaction_committed else (
            "PREVIEW ONLY — re-run with --commit to execute the "
            "insert. The scanner re-run + gate output is final "
            "and reproducible."
        ) if not args.commit else (
            f"FAIL — transaction rolled back. Reason: {rollback_reason}. "
            "Diagnose before retrying."
        ),
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print("Phase 8.5C.2 — Triton Amazon source_record insert")
    print("=" * 72)
    print(f"baseline:           {baseline}")
    print(f"final:              {final_counts}")
    print(f"persona-value gate: {len(approved)} pass, {len(post_gate_rejected)} reject")
    print(f"rescan:             {len(final_approved)} pass, {len(rescan_failed)} fail")
    print(f"committed:          {transaction_committed}")
    print(f"rollback_reason:    {rollback_reason}")
    print(f"inserted_count:     {len(inserted_ids)}")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
