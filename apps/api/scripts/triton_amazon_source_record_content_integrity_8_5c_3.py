"""Phase 8.5C.3 — read-only content-integrity audit + full-text
reconstruction plan for the 6 source_records inserted by 8.5C.2.

WHAT THIS DOES:
  1. SELECTs the 6 inserted rows from `source_records` by their
     `ingested_by` tag.
  2. For each row, streams the corresponding local Amazon Reviews
     2023 JSONL file line-by-line and reconstructs the original
     full title + text by matching `(parent_asin, asin)` and
     verifying the inserted preview is a prefix.
  3. Compares inserted-content length vs full-content length and
     assigns one of 4 sufficiency labels:
        - SUFFICIENT_AS_IS
        - USABLE_BUT_THIN
        - NEEDS_FULL_TEXT_COMPANION
        - EXCLUDE_FROM_PERSONA_BUILD
  4. Writes an audit JSON.

WHAT THIS DOES NOT DO:
  * No INSERT / UPDATE / DELETE on any DB table. Strictly read-only.
  * No raw user_id storage.
  * No image URL storage.
  * No Amazon API call. No Amazon.com scrape.
  * No reconstructed full-text written into the database (only into
    the audit JSON, truncated to 1500 chars per record).

The script is invoked WITHOUT a write flag. Even with malicious
arguments, drift discipline blocks any DB write surface.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)


PHASE_LABEL = "8.5C.3"
PHASE_8_5C_2_INGESTED_BY = (
    "assembly_phase_8_5c_triton_amazon_dynamic_policy_bounded_ingest"
)
EXPECTED_INSERTED_COUNT = 6


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


# ---------------------------------------------------------------------------
# DB read helpers (READ-ONLY)
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


async def _fetch_inserted_rows(sessionmaker) -> list[dict]:
    """SELECT 6 inserted rows by ingested_by tag. Returns dicts (we
    do NOT keep ORM objects beyond the session scope — pure read)."""
    async with sessionmaker() as session:
        rows = (await session.execute(
            select(SourceRecord)
            .where(SourceRecord.ingested_by == PHASE_8_5C_2_INGESTED_BY)
            .order_by(SourceRecord.created_at.asc())
        )).scalars().all()
        out: list[dict] = []
        for r in rows:
            md = r.metadata_ or {}
            out.append({
                "id": str(r.id),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "source_kind": r.source_kind,
                "source_url": r.source_url,
                "content": r.content,
                "content_length": len(r.content or ""),
                "content_hash": r.content_hash,
                "compliance_tag": r.compliance_tag,
                "captured_at": r.captured_at.isoformat() if r.captured_at else None,
                "pii_redaction_status": r.pii_redaction_status,
                "sensitive_scan_status": r.sensitive_scan_status,
                "metadata": dict(md),
            })
    return out


# ---------------------------------------------------------------------------
# Local Amazon JSONL streaming reconstruction
# ---------------------------------------------------------------------------


def _normalize_for_compare(s: str) -> str:
    """Whitespace-collapse + lowercase for fuzzy prefix comparison."""
    import re
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def reconstruct_full_review(
    *,
    raw_dir: Path,
    category: str,
    parent_asin: str,
    asin: str | None,
    inserted_content: str,
) -> dict[str, Any]:
    """Stream the per-category JSONL line-by-line, find the matching
    review by parent_asin + asin, verify with title/preview match,
    return reconstruction details.

    Status values:
      * `FOUND` — exactly one (parent_asin, asin) match whose title
        + first chars match the inserted content.
      * `AMBIGUOUS` — multiple candidate matches; we don't guess.
      * `NOT_FOUND` — no matching row in the file.
      * `FILE_MISSING` — JSONL file unavailable.

    Memory-bounded: streams; never loads more than one review at a
    time. Drops user_id and image URLs at parse time.
    """
    candidate_files = list(raw_dir.glob(f"{category}.jsonl*"))
    if not candidate_files:
        return {"status": "FILE_MISSING", "candidates_seen": 0}
    file_path = candidate_files[0]

    # Inserted content shape (set by 8.5C.2): "<TITLE>\n\n<TEXT_PREFIX>"
    # truncated to 240 chars. Split on the first blank line so we get
    # the inserted-title and the inserted-text-prefix separately.
    if "\n\n" in inserted_content:
        inserted_title, inserted_text_prefix = inserted_content.split(
            "\n\n", 1,
        )
    else:
        inserted_title = inserted_content
        inserted_text_prefix = ""
    norm_inserted_title = _normalize_for_compare(inserted_title)
    norm_inserted_text = _normalize_for_compare(inserted_text_prefix)

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
                # Title + text without dropping privacy fields here
                # because we never RETURN user_id or images. They get
                # filtered immediately below.
                full_title = (obj.get("title") or "").strip()
                full_text = (obj.get("text") or "").strip()
                norm_full_title = _normalize_for_compare(full_title)
                norm_full_text = _normalize_for_compare(full_text)
                # Confidence checks: title prefix-match + text prefix-match
                title_match = (
                    norm_full_title == norm_inserted_title
                    or norm_full_title.startswith(norm_inserted_title)
                    or norm_inserted_title.startswith(norm_full_title)
                )
                text_match = (
                    not norm_inserted_text  # nothing to compare
                    or norm_full_text.startswith(norm_inserted_text)
                    or norm_inserted_text in norm_full_text[:300]
                )
                if title_match and text_match:
                    full_combined = (
                        full_title + "\n\n" + full_text
                        if full_text else full_title
                    )
                    inserted_is_prefix = full_combined.startswith(
                        inserted_content
                    )
                    matches.append({
                        "rating": obj.get("rating"),
                        "verified_purchase": obj.get("verified_purchase"),
                        "helpful_vote": obj.get("helpful_vote"),
                        "timestamp": obj.get("timestamp"),
                        "title": full_title[:200],
                        "text_length": len(full_text),
                        "text_preview": full_text[:1500],
                        "combined_length": len(full_combined),
                        "inserted_is_prefix_of_original": inserted_is_prefix,
                        "title_match": title_match,
                        "text_match": text_match,
                    })
    except OSError as e:
        return {"status": "FILE_MISSING", "error": str(e)}

    if len(matches) == 0:
        return {"status": "NOT_FOUND", "candidates_seen": candidates_seen}
    if len(matches) > 1:
        return {
            "status": "AMBIGUOUS", "candidates_seen": candidates_seen,
            "matches_summary": [
                {k: m[k] for k in ("rating", "title", "text_length",
                                    "timestamp")}
                for m in matches
            ],
        }
    m = matches[0]
    return {"status": "FOUND", "candidates_seen": candidates_seen, **m}


# ---------------------------------------------------------------------------
# Sufficiency labelling
# ---------------------------------------------------------------------------


def assign_sufficiency_label(
    *,
    inserted_content_length: int,
    reconstruction: dict[str, Any],
    persona_value_label: str,
    persona_value_roles: list[str],
) -> tuple[str, list[str]]:
    """Pure function. Returns (label, reasons).

    Labels:
      * SUFFICIENT_AS_IS
      * USABLE_BUT_THIN
      * NEEDS_FULL_TEXT_COMPANION
      * EXCLUDE_FROM_PERSONA_BUILD
    """
    status = reconstruction.get("status")
    reasons: list[str] = []

    if status == "FILE_MISSING":
        reasons.append("local Amazon JSONL file is missing")
        return "EXCLUDE_FROM_PERSONA_BUILD", reasons
    if status == "NOT_FOUND":
        reasons.append("original review not found in local Amazon file")
        return "EXCLUDE_FROM_PERSONA_BUILD", reasons
    if status == "AMBIGUOUS":
        reasons.append(
            "multiple candidate reviews matched parent_asin + asin; "
            "cannot disambiguate without operator review"
        )
        return "EXCLUDE_FROM_PERSONA_BUILD", reasons

    if not persona_value_roles:
        reasons.append(
            "selected_for_persona_roles is empty (8.5C.2 should have "
            "rejected this row pre-insert)"
        )
        return "EXCLUDE_FROM_PERSONA_BUILD", reasons

    full_combined = int(reconstruction.get("combined_length") or 0)
    if full_combined <= 0:
        reasons.append("reconstructed full content is empty")
        return "EXCLUDE_FROM_PERSONA_BUILD", reasons

    truncation_ratio = (
        inserted_content_length / full_combined
        if full_combined else 0.0
    )

    # Naturally-short review: full content fits within the 240-char
    # inserted-preview window. Inserted IS the full review.
    if full_combined <= 245:
        reasons.append(
            f"full review is {full_combined} chars — fits inside "
            "inserted preview window; no truncation"
        )
        if persona_value_label in ("medium", "high"):
            return "SUFFICIENT_AS_IS", reasons
        reasons.append(
            f"persona_value_label={persona_value_label} (low/none weakens utility)"
        )
        return "USABLE_BUT_THIN", reasons

    # Truncation classification for longer reviews.
    if truncation_ratio >= 0.85:
        reasons.append(
            f"truncation ratio {truncation_ratio:.2f} — inserted retains "
            ">=85% of original"
        )
        return "SUFFICIENT_AS_IS", reasons
    if truncation_ratio >= 0.5:
        reasons.append(
            f"truncation ratio {truncation_ratio:.2f} — inserted has "
            "useful signal but full review materially adds context"
        )
        return "USABLE_BUT_THIN", reasons
    # ratio < 0.5 — significant content lost
    if not reconstruction.get("inserted_is_prefix_of_original", False):
        reasons.append(
            "inserted content is not a strict prefix of full review "
            "(unexpected hash-time normalization?)"
        )
    reasons.append(
        f"truncation ratio {truncation_ratio:.2f} — full review has "
        "materially richer evidence; preview alone weakens persona quality"
    )
    return "NEEDS_FULL_TEXT_COMPANION", reasons


# ---------------------------------------------------------------------------
# Persona role inference (for the audit's persona-readiness section)
# ---------------------------------------------------------------------------


def _persona_role_summary_from_full(
    *,
    full_title: str,
    full_text: str,
    metadata_persona_roles: list[str],
) -> list[str]:
    """Combine the 8.5C.1 inferred persona roles with any additional
    roles the FULL text supports. The full text often surfaces roles
    that the truncated preview missed."""
    combined = set(metadata_persona_roles)
    blob = (full_title + " " + full_text).lower()
    if any(t in blob for t in ("safety", "recall", "side effect",
                                "warning", "doctor", "blood pressure",
                                "heart racing")):
        combined.add("safety_skeptic")
    if any(t in blob for t in ("$", "expensive", "cheap", "value",
                                "overpriced", "pricey")):
        combined.add("price_skeptic")
    if any(t in blob for t in ("flavor", "flavour", "taste", "tastes",
                                "smell", "scent")):
        combined.add("flavor_or_sensory_focused_buyer")
    if any(t in blob for t in ("workout", "gym", "endurance",
                                "performance", "athletic", "fitness",
                                "pre-workout", "preworkout")):
        combined.add("performance_use_case_buyer")
    if any(t in blob for t in ("health", "natural", "organic",
                                "low sugar", "sugar-free", "no sugar",
                                "zero sugar")):
        combined.add("health_conscious_buyer")
    return sorted(combined)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5C.3 — read-only content-integrity audit of "
            "the 6 8.5C.2-inserted Triton Amazon source_records."
        ),
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / (
        "triton_amazon_source_record_content_integrity_8_5c_3.json"
    )

    dir_str = os.environ.get("AMAZON_REVIEWS_2023_DIR")
    if not dir_str:
        print("ERROR: AMAZON_REVIEWS_2023_DIR is unset.")
        return 2
    raw_dir = Path(dir_str) / "raw"
    if not raw_dir.is_dir():
        print(f"ERROR: {raw_dir} does not exist.")
        return 2

    sm = get_sessionmaker()
    db_baseline_pre = await _read_baseline_counts(sm)

    rows = await _fetch_inserted_rows(sm)
    print(
        f"Found {len(rows)} 8.5C.2-inserted rows by ingested_by "
        f"(expected {EXPECTED_INSERTED_COUNT})"
    )

    per_record_audit: list[dict] = []
    label_counts: Counter = Counter()

    for row in rows:
        md = row["metadata"]
        category = md.get("source_category")
        parent_asin = md.get("parent_asin")
        asin = md.get("asin")
        inserted_content = row["content"]
        recon = reconstruct_full_review(
            raw_dir=raw_dir,
            category=category, parent_asin=parent_asin, asin=asin,
            inserted_content=inserted_content,
        )
        truncation_ratio = (
            row["content_length"] / recon["combined_length"]
            if recon.get("combined_length") else 0.0
        )
        persona_value_label = (
            md.get("anchor_confidence")  # not exactly persona_value...
        )
        # The 8.5C.1 audit's `persona_value_label` is at the candidate
        # decision level, not stored in the source_record metadata.
        # Recover it by reading the planned record's persona_value_roles
        # — non-empty implies medium/high; empty implies low/none.
        persona_value_roles = list(md.get("persona_value_roles") or [])
        persona_value_label_proxy = (
            "medium" if persona_value_roles else "low"
        )
        label, reasons = assign_sufficiency_label(
            inserted_content_length=row["content_length"],
            reconstruction=recon,
            persona_value_label=persona_value_label_proxy,
            persona_value_roles=persona_value_roles,
        )
        label_counts[label] += 1
        # Compute extended persona roles from the FULL text when
        # available (audit-only — never stored back to the DB).
        extended_roles: list[str] = []
        if recon.get("status") == "FOUND":
            extended_roles = _persona_role_summary_from_full(
                full_title=recon.get("title", ""),
                full_text=recon.get("text_preview", ""),
                metadata_persona_roles=persona_value_roles,
            )
        recommended_action = {
            "SUFFICIENT_AS_IS": (
                "use this source_record as-is in 8.5D persona builder; "
                "no full-text companion needed"
            ),
            "USABLE_BUT_THIN": (
                "use as-is for a small dry-run; consider full-text "
                "companion in Phase 8.5C.4 if persona quality is "
                "marginal"
            ),
            "NEEDS_FULL_TEXT_COMPANION": (
                "do NOT use this row alone for persona build; insert "
                "a full-text companion source_record in Phase 8.5C.4 "
                "with metadata.supersedes_preview_source_record_id "
                "set to this row's id"
            ),
            "EXCLUDE_FROM_PERSONA_BUILD": (
                "exclude from Phase 8.5D persona build; the inserted "
                "content cannot anchor a persona"
            ),
        }[label]
        per_record_audit.append({
            "source_record_id": row["id"],
            "category": category,
            "parent_asin": parent_asin,
            "asin": asin,
            "metadata_title": md.get("metadata_title"),
            "anchor_score": md.get("anchor_score"),
            "anchor_confidence": md.get("anchor_confidence"),
            "persona_value_roles_from_8_5c_1": persona_value_roles,
            "persona_value_label_proxy": persona_value_label_proxy,
            "extended_persona_roles_from_full_text": extended_roles,
            "additional_persona_roles_unlocked_by_full": sorted(
                set(extended_roles) - set(persona_value_roles)
            ),
            "inserted_content_length": row["content_length"],
            "inserted_content_preview": inserted_content[:200],
            "reconstructed_full_combined_length": recon.get(
                "combined_length", 0,
            ),
            "reconstructed_full_text_preview": recon.get(
                "text_preview", "",
            )[:600] if recon.get("status") == "FOUND" else None,
            "reconstruction_status": recon.get("status"),
            "candidates_seen_in_jsonl": recon.get("candidates_seen"),
            "inserted_is_prefix_of_original": recon.get(
                "inserted_is_prefix_of_original",
            ),
            "content_truncation_ratio": round(truncation_ratio, 3),
            "sufficiency_label": label,
            "sufficiency_reasons": reasons,
            "recommended_action": recommended_action,
        })

    db_baseline_post = await _read_baseline_counts(sm)
    db_unchanged = db_baseline_pre == db_baseline_post

    aggregate_summary = {
        "sufficient_as_is_count": label_counts["SUFFICIENT_AS_IS"],
        "usable_but_thin_count": label_counts["USABLE_BUT_THIN"],
        "needs_full_text_companion_count": label_counts[
            "NEEDS_FULL_TEXT_COMPANION"
        ],
        "exclude_count": label_counts["EXCLUDE_FROM_PERSONA_BUILD"],
    }

    # Persona-readiness assessment
    n_usable = (
        aggregate_summary["sufficient_as_is_count"]
        + aggregate_summary["usable_but_thin_count"]
    )
    n_companion_needed = aggregate_summary[
        "needs_full_text_companion_count"
    ]
    n_excluded = aggregate_summary["exclude_count"]
    if (
        len(rows) == EXPECTED_INSERTED_COUNT
        and n_usable >= 4
    ):
        readiness = "READY_FOR_SMALL_PERSONA_DRY_RUN"
        readiness_reason = (
            f"{n_usable} of {len(rows)} rows are SUFFICIENT_AS_IS or "
            f"USABLE_BUT_THIN — proceed to Phase 8.5D.1 persona-build "
            "dry-run, with the NEEDS_FULL_TEXT_COMPANION rows flagged "
            "for Phase 8.5C.4 future enhancement."
        )
        recommended_next_phase = (
            "Phase 8.5D.1 persona-build dry-run against the "
            f"{n_usable} usable source_records, OR Phase 8.5C.4 "
            f"full-text companion insertion for the "
            f"{n_companion_needed} truncated rows first if persona "
            "quality is the priority."
        )
    elif n_companion_needed >= 3:
        readiness = "RECOMMEND_FULL_TEXT_COMPANIONS_FIRST"
        readiness_reason = (
            f"{n_companion_needed} rows are materially truncated. "
            "Build full-text companion source_records before persona "
            "construction to avoid weak personas."
        )
        recommended_next_phase = (
            "Phase 8.5C.4 — insert full-text companion source_records "
            f"for the {n_companion_needed} truncated rows; preserve "
            "lineage via metadata.supersedes_preview_source_record_id."
        )
    else:
        readiness = "INSUFFICIENT_FOR_PERSONA_BUILD"
        readiness_reason = (
            f"only {n_usable} usable rows from {len(rows)} inserted. "
            "Diagnose before persona expansion."
        )
        recommended_next_phase = (
            "Investigate before proceeding."
        )

    summary = {
        "phase": "8_5c_3_triton_amazon_source_record_content_integrity",
        "completed_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "db_writes": False,
        "db_baseline_pre_audit": db_baseline_pre,
        "db_baseline_post_audit": db_baseline_post,
        "db_unchanged_during_audit": db_unchanged,
        "current_db_counts": db_baseline_post,
        "expected_inserted_count": EXPECTED_INSERTED_COUNT,
        "source_records_examined_count": len(rows),
        "exact_count_match": len(rows) == EXPECTED_INSERTED_COUNT,
        "inserted_source_record_ids": [r["id"] for r in rows],
        "per_record_audit": per_record_audit,
        "aggregate_summary": aggregate_summary,
        "persona_readiness_assessment": {
            "readiness": readiness,
            "reason": readiness_reason,
            "max_personas_safely_buildable_now": n_usable,
            "rows_needing_full_text_companion": n_companion_needed,
            "rows_to_exclude": n_excluded,
        },
        "recommended_next_phase": recommended_next_phase,
        "caveats": [
            "Phase 8.5C.3 is strictly READ-ONLY against the database. "
            "No source_records are inserted, updated, or deleted; no "
            "personas / traits / evidence-links are touched.",
            "Reconstructed full review text is captured ONLY in this "
            "audit JSON, truncated to 1500 chars per record, and is "
            "never written back to source_records.content.",
            "If full-text companions are inserted in Phase 8.5C.4, "
            "they must preserve lineage via "
            "metadata.supersedes_preview_source_record_id and the "
            "original 6 preview rows must remain immutable.",
            "The 240-char preview cap was a Phase 8.5C.2 design "
            "choice (compact source_records + audit-relevant excerpt). "
            "It is preserved as-is for the original 6 rows.",
        ],
    }

    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    # Operator-facing summary
    print("\n" + "=" * 72)
    print("Phase 8.5C.3 — Source-record content-integrity audit")
    print("=" * 72)
    print(
        f"db state: {db_baseline_pre} -> {db_baseline_post} "
        f"(unchanged: {db_unchanged})"
    )
    print(f"rows examined: {len(rows)} (expected {EXPECTED_INSERTED_COUNT})")
    print(f"sufficiency labels: {dict(label_counts)}")
    print(f"\nper-record sufficiency:")
    for r in per_record_audit:
        print(
            f"  {r['source_record_id'][:8]}... "
            f"cat={r['category'][:25]:25s} "
            f"parent_asin={r['parent_asin']:12s} "
            f"insert={r['inserted_content_length']:4d} "
            f"full={r['reconstructed_full_combined_length']:5d} "
            f"ratio={r['content_truncation_ratio']:.3f} "
            f"{r['sufficiency_label']}"
        )
    print(f"\nreadiness: {readiness}")
    print(f"recommended_next_phase: {recommended_next_phase[:200]}")
    print(f"\n→ audit JSON: {out_path}")

    return 0 if db_unchanged and len(rows) == EXPECTED_INSERTED_COUNT else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
