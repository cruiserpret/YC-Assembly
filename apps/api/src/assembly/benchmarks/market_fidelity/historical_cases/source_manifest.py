"""Phase 17D — source manifest + timestamp-confidence validation.

Records every source's timestamps and grades how confidently it can be placed BEFORE
the prediction/outcome. Missing or coarse (year-only) timestamps are flagged and
downgrade eligibility. Reuses the tz-aware parser from the 17C retrieval filter so a
post-outcome source can never slip past on a lexical date prefix. Pure; no network.
"""
from __future__ import annotations

from collections.abc import Sequence

from assembly.benchmarks.market_fidelity.historical_cases.input_bundle import EvidenceItem
from assembly.benchmarks.market_fidelity.retrieval_filter import _parse_instant

TimestampConfidence = dict[str, str]  # source_id -> high|medium|low|none


def _confidence(item: EvidenceItem) -> str:
    """high = a parseable published/archived date; medium = parseable accessed-only;
    low = present-but-unparseable/coarse; none = no timestamp at all."""
    if _parse_instant(item.published_at) is not None or _parse_instant(item.archived_at) is not None:
        return "high"
    if _parse_instant(item.accessed_at) is not None:
        return "medium"
    if item.published_at or item.archived_at or item.accessed_at:
        return "low"  # present but unparseable / coarser-than-day
    return "none"


def build_source_manifest(evidence_items: Sequence[EvidenceItem]) -> dict:
    """A manifest of per-source timestamp provenance + confidence."""
    entries = []
    confidence: TimestampConfidence = {}
    coarse_or_missing: list[str] = []
    for e in evidence_items:
        c = _confidence(e)
        confidence[e.source_id] = c
        if c in ("low", "none"):
            coarse_or_missing.append(e.source_id)
        entries.append({
            "source_id": e.source_id,
            "publisher": e.publisher,
            "url": e.url,
            "archive_url": e.archive_url,
            "published_at": e.published_at,
            "archived_at": e.archived_at,
            "accessed_at": e.accessed_at,
            "timestamp_confidence": c,
            "content_hash": e.content_hash,
        })
    # High-confidence pre-outcome proof requires a parseable PUBLICATION/ARCHIVE date
    # for EVERY source. An accessed-only ('medium') timestamp is a fetch time, not a
    # publication date, so it does NOT qualify as pre-outcome proof and downgrades.
    all_high = bool(entries) and all(c == "high" for c in confidence.values())
    medium_only = [sid for sid, c in confidence.items() if c == "medium"]
    return {
        "n_sources": len(entries),
        "entries": entries,
        "timestamp_confidence": confidence,
        "coarse_or_missing_timestamps": coarse_or_missing,
        "accessed_only_timestamps": medium_only,
        "all_timestamps_high_confidence": all_high,
    }
