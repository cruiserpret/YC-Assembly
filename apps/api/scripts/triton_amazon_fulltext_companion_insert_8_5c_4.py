"""Phase 8.5C.4 — full-text companion source_record insertion.

Reads the Phase 8.5C.3 audit, identifies the 2 rows labeled
NEEDS_FULL_TEXT_COMPANION, reconstructs each row's full Amazon
review by streaming the local JSONL, re-runs all 4 universal
scanners, and inserts exactly 2 new SourceRecord companion rows
inside ONE bounded transaction with rollback on any failure.

The original 6 preview source_records are NEVER updated or
deleted — they remain immutable. Each companion record carries
lineage metadata pointing back to its preview record:

  * `original_preview_source_record_id` — UUID of the 8.5C.2 row
  * `supersedes_preview_source_record_id` — same UUID
  * `full_text_reconstruction: true`
  * `source_record_lineage: "full_text_companion"`
  * `inserted_from_phase: "8.5C.4"`

Discipline:
  * `--commit` flag is REQUIRED to actually write. Default is
    preview-only (gate + scanner re-run + report).
  * Single transaction via `async with session.begin():` —
    automatic rollback on exception.
  * Count guards inside transaction: source_records must increase
    EXACTLY by len(companion_set); persona/trait/evidence-link
    counts MUST be unchanged. Mismatch raises → rollback.
  * NO ORM construction other than `SourceRecord(...)`.
  * NO Amazon API call. NO Amazon.com scrape.
  * Inline `reconstruct_full_review` — same logic as 8.5C.3.
    Duplicated rather than cross-imported to keep each phase
    script self-contained.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


PHASE_LABEL = "8.5C.4"
TRITON_PRODUCT_NAME = "Triton Drinks"
INGESTED_BY = (
    "assembly_phase_8_5c4_triton_amazon_fulltext_companion_ingest"
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


# ---------------------------------------------------------------------------
# Streaming full-text review reconstruction (mirrors 8.5C.3 logic)
# ---------------------------------------------------------------------------


def _normalize_for_compare(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def reconstruct_full_review(
    *,
    raw_dir: Path,
    category: str,
    parent_asin: str,
    asin: str | None,
    inserted_preview_content: str,
) -> dict[str, Any]:
    """Stream JSONL line-by-line, find matching review by
    parent_asin + asin + title/text prefix. Returns:

      * status='FOUND' with full_title, full_text, combined_length
      * status='AMBIGUOUS' if multiple candidate matches
      * status='NOT_FOUND' if no match
      * status='FILE_MISSING' if JSONL missing

    Drops `images` and `user_id` at parse time.
    """
    candidate_files = list(raw_dir.glob(f"{category}.jsonl*"))
    if not candidate_files:
        return {"status": "FILE_MISSING", "candidates_seen": 0}
    file_path = candidate_files[0]
    if "\n\n" in inserted_preview_content:
        ip_title, ip_text = inserted_preview_content.split("\n\n", 1)
    else:
        ip_title = inserted_preview_content
        ip_text = ""
    norm_ip_title = _normalize_for_compare(ip_title)
    norm_ip_text = _normalize_for_compare(ip_text)

    matches: list[dict[str, Any]] = []
    candidates_seen = 0
    opener = gzip.open if file_path.suffix == ".gz" else open
    try:
        with opener(file_path, "rt", encoding="utf-8") as fh:  # type: ignore[arg-type]
            for raw in fh:
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if obj.get("parent_asin") != parent_asin:
                    continue
                if asin is not None and obj.get("asin") != asin:
                    continue
                candidates_seen += 1
                full_title = (obj.get("title") or "").strip()
                full_text = (obj.get("text") or "").strip()
                norm_ft_title = _normalize_for_compare(full_title)
                norm_ft_text = _normalize_for_compare(full_text)
                title_match = (
                    norm_ft_title == norm_ip_title
                    or norm_ft_title.startswith(norm_ip_title)
                    or norm_ip_title.startswith(norm_ft_title)
                )
                text_match = (
                    not norm_ip_text
                    or norm_ft_text.startswith(norm_ip_text)
                    or norm_ip_text in norm_ft_text[:300]
                )
                if title_match and text_match:
                    matches.append({
                        "rating": obj.get("rating"),
                        "verified_purchase": obj.get("verified_purchase"),
                        "helpful_vote": obj.get("helpful_vote"),
                        "timestamp": obj.get("timestamp"),
                        "title": full_title,
                        "text": full_text,
                    })
    except OSError as e:
        return {"status": "FILE_MISSING", "error": str(e)}

    if len(matches) == 0:
        return {"status": "NOT_FOUND", "candidates_seen": candidates_seen}
    if len(matches) > 1:
        return {"status": "AMBIGUOUS", "candidates_seen": candidates_seen}
    m = matches[0]
    full_combined = (
        m["title"] + "\n\n" + m["text"] if m["text"] else m["title"]
    )
    return {
        "status": "FOUND",
        "title": m["title"],
        "text": m["text"],
        "rating": m["rating"],
        "verified_purchase": m["verified_purchase"],
        "helpful_vote": m["helpful_vote"],
        "timestamp": m["timestamp"],
        "full_combined_content": full_combined,
        "combined_length": len(full_combined),
        "candidates_seen": candidates_seen,
    }


# ---------------------------------------------------------------------------
# DB helpers (READ-ONLY pre-transaction; INSERT only inside transaction)
# ---------------------------------------------------------------------------


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


async def _resolve_preview_record_id_by_parent_asin(
    sessionmaker, *, parent_asin: str, category: str,
) -> str | None:
    """Find the 8.5C.2 preview SourceRecord row whose metadata.parent_asin
    matches. Read-only."""
    async with sessionmaker() as session:
        rows = (await session.execute(
            select(SourceRecord)
            .where(
                SourceRecord.ingested_by ==
                "assembly_phase_8_5c_triton_amazon_dynamic_policy_bounded_ingest",
                SourceRecord.metadata_["parent_asin"].astext == parent_asin,
                SourceRecord.metadata_["source_category"].astext == category,
            )
        )).scalars().all()
    if len(rows) == 1:
        return str(rows[0].id)
    return None


# ---------------------------------------------------------------------------
# Per-candidate processing
# ---------------------------------------------------------------------------


async def _build_companion_record_kwargs(
    *,
    audit_row: dict,
    raw_dir: Path,
    sessionmaker,
) -> dict:
    """Reconstruct full text + run scanners + build SourceRecord kwargs.

    Returns a dict with `kwargs` (None if any check fails),
    `scanner_results`, `lineage_id`, `recon_status`, etc."""
    category = audit_row["category"]
    parent_asin = audit_row["parent_asin"]
    asin = audit_row.get("asin")
    inserted_preview_content = audit_row.get("inserted_content_preview", "")

    recon = reconstruct_full_review(
        raw_dir=raw_dir, category=category,
        parent_asin=parent_asin, asin=asin,
        inserted_preview_content=inserted_preview_content,
    )
    if recon.get("status") != "FOUND":
        return {
            "kwargs": None, "recon_status": recon.get("status"),
            "scanner_results": {}, "skip_reason": (
                f"reconstruction_status={recon.get('status')}"
            ),
            "lineage_id": None, "content_hash": None,
            "full_combined_content": None,
        }
    full_combined = recon["full_combined_content"]

    # Scanners
    pii = scan_pii(full_combined)
    fb = scan_unlaunched_fake_buyer(
        text=full_combined, product_name=TRITON_PRODUCT_NAME,
    )
    source_url = (
        f"local://{SOURCE_KIND}/{category}/{parent_asin}/fulltext"
    )
    compliance = scan_dataset_compliance(
        source_kind=SOURCE_KIND, source_url=source_url,
        compliance_tag=COMPLIANCE_TAG, source_family=SOURCE_KIND,
    )
    content_hash = compute_content_hash(
        content=full_combined, source_kind=SOURCE_KIND,
    )
    dup = await check_duplicate_content_hash(
        content_hash=content_hash, sessionmaker=sessionmaker,
    )
    scanner_results = {
        "pii_scan": list(pii.issues),
        "unlaunched_fake_buyer_scan": list(fb.issues),
        "dataset_compliance_scan": compliance,
        "duplicate_check": (
            ["content_hash already in source_records"] if dup else []
        ),
    }
    all_clean = (
        not pii.issues and not fb.issues and not compliance and not dup
    )
    lineage_id = await _resolve_preview_record_id_by_parent_asin(
        sessionmaker, parent_asin=parent_asin, category=category,
    )
    if not all_clean or not lineage_id:
        skip = []
        if not all_clean:
            skip.append("scanner_failure")
        if not lineage_id:
            skip.append("lineage_id_unresolvable")
        return {
            "kwargs": None, "recon_status": "FOUND",
            "scanner_results": scanner_results,
            "skip_reason": ",".join(skip),
            "lineage_id": lineage_id, "content_hash": content_hash,
            "full_combined_content": full_combined,
        }

    metadata = {
        "target_brief": "triton_drinks",
        "source_dataset": "amazon_reviews_2023",
        "source_category": category,
        "parent_asin": parent_asin,
        "asin": asin,
        "rating": recon.get("rating"),
        "verified_purchase": recon.get("verified_purchase"),
        "helpful_vote": recon.get("helpful_vote"),
        "timestamp": recon.get("timestamp"),
        "metadata_title": audit_row.get("metadata_title"),
        "metadata_main_category": (
            audit_row.get("metadata_main_category")
        ),
        "metadata_categories": audit_row.get("metadata_categories", []),
        "source_is_historical": True,
        "source_caveat": SOURCE_CAVEAT,
        "full_text_reconstruction": True,
        "source_record_lineage": "full_text_companion",
        "original_preview_source_record_id": lineage_id,
        "supersedes_preview_source_record_id": lineage_id,
        "reconstruction_status": "FOUND",
        "inserted_from_phase": PHASE_LABEL,
        "previous_sufficiency_label": (
            audit_row.get("sufficiency_label")
        ),
        "previous_truncation_ratio": (
            audit_row.get("content_truncation_ratio")
        ),
        "persona_value_roles": (
            audit_row.get("extended_persona_roles_from_full_text")
            or audit_row.get("persona_value_roles_from_8_5c_1", [])
        ),
        "additional_persona_roles_unlocked_by_full": (
            audit_row.get("additional_persona_roles_unlocked_by_full", [])
        ),
        "recommended_for_persona_build": True,
    }
    captured_at = datetime.fromisoformat(DATASET_SNAPSHOT_DATE)
    kwargs = {
        "source_kind": SOURCE_KIND,
        "source_url": source_url,
        "captured_at": captured_at,
        "content": full_combined,
        "content_hash": content_hash,
        "language": "en",
        "metadata_": metadata,
        "ingested_by": INGESTED_BY,
        "compliance_tag": COMPLIANCE_TAG,
        "user_handle_hash": None,
        "pii_redaction_status": "clean",
        "sensitive_scan_status": "clean",
    }
    return {
        "kwargs": kwargs, "recon_status": "FOUND",
        "scanner_results": scanner_results,
        "skip_reason": None,
        "lineage_id": lineage_id, "content_hash": content_hash,
        "full_combined_content": full_combined,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5C.4 — Triton Amazon full-text companion "
            "source_record insertion."
        ),
    )
    parser.add_argument(
        "--commit", action="store_true",
        help=(
            "Required to actually write to the database. Default is "
            "preview-only (reconstruct + scanner re-run + report)."
        ),
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    in_path = audit_root / (
        "triton_amazon_source_record_content_integrity_8_5c_3.json"
    )
    out_path = audit_root / (
        "triton_amazon_fulltext_companion_insert_8_5c_4.json"
    )
    if not in_path.is_file():
        print(f"ERROR: {in_path} missing. Run 8.5C.3 first.")
        return 2
    audit_8_5c_3 = json.loads(in_path.read_text(encoding="utf-8"))

    dir_str = os.environ.get("AMAZON_REVIEWS_2023_DIR")
    if not dir_str:
        print("ERROR: AMAZON_REVIEWS_2023_DIR is unset.")
        return 2
    raw_dir = Path(dir_str) / "raw"
    if not raw_dir.is_dir():
        print(f"ERROR: {raw_dir} does not exist.")
        return 2

    targets = [
        r for r in audit_8_5c_3.get("per_record_audit", [])
        if r.get("sufficiency_label") == "NEEDS_FULL_TEXT_COMPANION"
    ]
    print(f"NEEDS_FULL_TEXT_COMPANION rows from 8.5C.3: {len(targets)}")
    if len(targets) != 2:
        print(
            f"ERROR: expected exactly 2 NEEDS_FULL_TEXT_COMPANION "
            f"rows, found {len(targets)}. Aborting."
        )
        return 2

    sm = get_sessionmaker()
    baseline = await _read_baseline_counts(sm)
    print(f"baseline counts: {baseline}")

    # Reconstruct + scan all targets BEFORE the transaction
    processed: list[dict] = []
    for t in targets:
        proc = await _build_companion_record_kwargs(
            audit_row=t, raw_dir=raw_dir, sessionmaker=sm,
        )
        processed.append({"audit_row": t, "processed": proc})

    approved = [p for p in processed if p["processed"]["kwargs"] is not None]
    skipped = [p for p in processed if p["processed"]["kwargs"] is None]
    print(f"approved: {len(approved)}, skipped: {len(skipped)}")
    if skipped:
        for s in skipped:
            print(
                f"  SKIP {s['audit_row']['source_record_id'][:8]}... "
                f"reason={s['processed']['skip_reason']}"
            )

    # Intra-batch hash uniqueness check
    hashes = [p["processed"]["content_hash"] for p in approved]
    intra_batch_dup = (
        len(hashes) != len(set(hashes)) if hashes else False
    )
    if intra_batch_dup:
        print("ERROR: intra-batch content_hash collision.")
        return 2

    inserted_ids: list[str] = []
    transaction_committed = False
    rollback_reason: str | None = None
    final_counts = baseline

    if not args.commit:
        print(
            "\n⚠ --commit NOT supplied. PREVIEW ONLY. No writes.\n"
            "Re-run with `--commit` to execute the insert."
        )
    else:
        try:
            async with sm() as session:
                async with session.begin():
                    rows: list[SourceRecord] = []
                    for p in approved:
                        kwargs = p["processed"]["kwargs"]
                        row = SourceRecord(**kwargs)
                        session.add(row)
                        rows.append(row)
                    await session.flush()
                    inserted_ids = [str(r.id) for r in rows]
                    # Verify exact source_records delta
                    actual_sr = (await session.execute(
                        select(func.count()).select_from(SourceRecord)
                    )).scalar_one()
                    expected_sr = baseline["source_records"] + len(rows)
                    if int(actual_sr) != expected_sr:
                        raise RuntimeError(
                            f"source_records count mismatch: "
                            f"{int(actual_sr)} != {expected_sr}"
                        )
                    # Verify persona/trait/link tables unchanged
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
            inserted_ids = []
            print(f"\nROLLBACK: {rollback_reason}")
        final_counts = await _read_baseline_counts(sm)

    expected_post = {
        "source_records": (
            baseline["source_records"]
            + (len(approved) if transaction_committed else 0)
        ),
        "persona_records": baseline["persona_records"],
        "persona_traits": baseline["persona_traits"],
        "persona_evidence_links": baseline["persona_evidence_links"],
    }
    expected_counts_match = final_counts == expected_post

    summary = {
        "phase": "8_5c_4_triton_amazon_fulltext_companion_insert",
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
        "companion_candidates_count": len(targets),
        "approved_count": len(approved),
        "skipped_count": len(skipped),
        "inserted_count": len(inserted_ids),
        "inserted_source_record_ids": inserted_ids,
        "inserted_companion_summary": [
            {
                "companion_source_record_id": (
                    inserted_ids[i] if i < len(inserted_ids) else None
                ),
                "original_preview_source_record_id": (
                    p["processed"]["lineage_id"]
                ),
                "category": p["audit_row"].get("category"),
                "parent_asin": p["audit_row"].get("parent_asin"),
                "asin": p["audit_row"].get("asin"),
                "metadata_title": (
                    p["audit_row"].get("metadata_title")
                ),
                "inserted_content_length": (
                    len(p["processed"]["full_combined_content"])
                    if p["processed"]["full_combined_content"]
                    else 0
                ),
                "original_preview_content_length": (
                    p["audit_row"].get("inserted_content_length")
                ),
                "previous_truncation_ratio": (
                    p["audit_row"].get("content_truncation_ratio")
                ),
                "persona_value_roles": (
                    p["audit_row"]
                    .get("extended_persona_roles_from_full_text")
                    or p["audit_row"]
                    .get("persona_value_roles_from_8_5c_1", [])
                ),
                "additional_roles_unlocked_by_full": (
                    p["audit_row"]
                    .get("additional_persona_roles_unlocked_by_full", [])
                ),
                "content_hash": p["processed"]["content_hash"],
                "source_url": (
                    f"local://{SOURCE_KIND}/"
                    f"{p['audit_row']['category']}/"
                    f"{p['audit_row']['parent_asin']}/fulltext"
                ),
                "scanner_results": p["processed"]["scanner_results"],
                "content_preview_first_240": (
                    p["processed"]["full_combined_content"][:240]
                    if p["processed"]["full_combined_content"]
                    else None
                ),
            }
            for i, p in enumerate(approved)
        ],
        "skipped_candidates": [
            {
                "audit_row_source_record_id": (
                    p["audit_row"].get("source_record_id")
                ),
                "skip_reason": p["processed"]["skip_reason"],
                "scanner_results": p["processed"]["scanner_results"],
            }
            for p in skipped
        ],
        "caveats": [
            "Phase 8.5C.4 inserts ONLY full-text companion "
            "source_records (zero personas, traits, evidence-links).",
            "The 6 original 8.5C.2 preview source_records are "
            "IMMUTABLE — never updated, never deleted.",
            "Each companion's metadata.original_preview_source_record_id "
            "and metadata.supersedes_preview_source_record_id point "
            "back to its preview row for full lineage traceability.",
            "captured_at uses the dataset snapshot date "
            f"({DATASET_SNAPSHOT_DATE}); historical-evidence caveat "
            "applies as for the preview rows.",
        ],
        "recommendation": (
            "PASS — full-text companions executed. Phase 8.5D.1 "
            "persona-build dry-run is ready against the now 4-as-is "
            "+ 2-companioned source_records."
        ) if transaction_committed else (
            "PREVIEW ONLY — re-run with --commit to execute."
        ) if not args.commit else (
            f"FAIL — transaction rolled back. Reason: {rollback_reason}."
        ),
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print("Phase 8.5C.4 — Triton Amazon full-text companion insert")
    print("=" * 72)
    print(f"baseline:   {baseline}")
    print(f"final:      {final_counts}")
    print(f"approved:   {len(approved)}, skipped: {len(skipped)}")
    print(f"committed:  {transaction_committed}")
    print(f"inserted:   {len(inserted_ids)}")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
