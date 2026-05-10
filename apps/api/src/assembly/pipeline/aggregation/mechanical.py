"""Phase 7 — mechanical (deterministic) report sections.

These sections are pure aggregations over `ReportInputBundle`. They run
without the LLM:

  Section 8 — debate_shift_markers (from debate_turns + agent_responses.shift_from_previous)
  Section 9a — split_confidence + per-round stance distribution (from agent_responses)
  Section 9b — evidence_ledger counts + missing + claim_traceability (from graph service)

All numeric values here are SIMULATION-STATE measurements (counts, ratios
inside the simulation), not market-reality forecasts. They are allowed.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from assembly.pipeline.aggregation.reader import ReportInputBundle
from assembly.pipeline.aggregation.section_schema import (
    ClaimTraceabilityEntry,
    ConfidenceSection,
    DebateShiftMarker,
    DebateShiftMarkersSection,
    EvidenceLedgerCounts,
    EvidenceLedgerSection,
    MissingEvidenceLedgerEntry,
    SplitConfidence,
    StanceCount,
)


# ---------------------------------------------------------------------------
# Section 8 — debate shift markers
# ---------------------------------------------------------------------------


def build_debate_shift_markers(
    bundle: ReportInputBundle,
) -> DebateShiftMarkersSection:
    """Pure aggregation over `agent_responses.shift_from_previous` and
    round-6 `debate_turns.caused_shifts`.

    Each marker references real ids — debate_turn_id / speaker_agent_id /
    target_agent_id when round 6, and triggered_by free-text label or
    evidence_id from agent_responses for other rounds."""
    rounds_by_id = {r.id: r for r in bundle.rounds}
    debate_turns_by_id = {t.id: t for t in bundle.debate_turns}

    # Bucket shifts by (round_number, from_stance, to_stance, triggered_by).
    grouped: dict[tuple[int, str, str, str | None], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "debate_turn_id": None,
                 "speaker_agent_id": None, "target_agent_id": None,
                 "example_argument": None}
    )

    rounds_with_shifts: set[int] = set()

    for ar in bundle.agent_responses:
        if not ar.shift_from_previous:
            continue
        round_obj = rounds_by_id.get(ar.round_id)
        if round_obj is None:
            continue
        rn = round_obj.round_number
        sfp = ar.shift_from_previous or {}
        from_stance = sfp.get("from_stance") or sfp.get("from") or "unknown"
        to_stance = sfp.get("to_stance") or sfp.get("to") or "unknown"
        triggered_by_str = sfp.get("triggered_by")
        if from_stance == to_stance and from_stance == "unknown":
            continue
        rounds_with_shifts.add(rn)
        # Try to resolve triggered_by to a UUID (debate turn) for round 6.
        debate_turn_id = None
        speaker_id = None
        target_id = None
        example_arg = None
        if triggered_by_str:
            try:
                maybe_uuid = UUID(triggered_by_str)
                if maybe_uuid in debate_turns_by_id:
                    dt = debate_turns_by_id[maybe_uuid]
                    debate_turn_id = dt.id
                    speaker_id = dt.speaker_agent_id
                    target_id = dt.target_agent_id
                    example_arg = (dt.argument or "")[:240]
            except (ValueError, TypeError):
                pass
        key = (rn, str(from_stance), str(to_stance), triggered_by_str)
        bucket = grouped[key]
        bucket["count"] += 1
        if debate_turn_id is not None and bucket["debate_turn_id"] is None:
            bucket["debate_turn_id"] = debate_turn_id
            bucket["speaker_agent_id"] = speaker_id
            bucket["target_agent_id"] = target_id
            bucket["example_argument"] = example_arg

    # Also include round-6 debate_turns.caused_shifts directly (some shifts
    # are recorded only on the turn, not also on agent_responses).
    for dt in bundle.debate_turns:
        round_obj = rounds_by_id.get(dt.round_id)
        if round_obj is None:
            continue
        rn = round_obj.round_number
        for shift in (dt.caused_shifts or []):
            from_stance = shift.get("from_stance") or shift.get("from") or "unknown"
            to_stance = shift.get("to_stance") or shift.get("to") or "unknown"
            if from_stance == to_stance:
                continue
            rounds_with_shifts.add(rn)
            key = (rn, str(from_stance), str(to_stance), str(dt.id))
            bucket = grouped[key]
            bucket["count"] += 1
            if bucket["debate_turn_id"] is None:
                bucket["debate_turn_id"] = dt.id
                bucket["speaker_agent_id"] = dt.speaker_agent_id
                bucket["target_agent_id"] = dt.target_agent_id
                bucket["example_argument"] = (dt.argument or "")[:240]

    markers: list[DebateShiftMarker] = []
    for (rn, from_stance, to_stance, trig), bucket in grouped.items():
        markers.append(
            DebateShiftMarker(
                round_number=rn,
                from_stance=from_stance,
                to_stance=to_stance,
                count=bucket["count"],
                triggered_by=trig,
                debate_turn_id=bucket["debate_turn_id"],
                speaker_agent_id=bucket["speaker_agent_id"],
                target_agent_id=bucket["target_agent_id"],
                example_argument=bucket["example_argument"],
            )
        )

    if not markers:
        summary = "No stance shifts were recorded across rounds."
    else:
        summary = (
            f"{len(markers)} shift cluster(s) recorded across "
            f"{len(rounds_with_shifts)} round(s)."
        )

    return DebateShiftMarkersSection(
        summary=summary,
        markers=sorted(markers, key=lambda m: (m.round_number, -m.count)),
        rounds_with_shifts=sorted(rounds_with_shifts),
    )


# ---------------------------------------------------------------------------
# Section 9a — confidence (split + per-round stance distribution)
# ---------------------------------------------------------------------------


def _entropy(counts: dict[str, int]) -> float:
    """Shannon entropy in bits over a stance-count dict."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    probs = [c / total for c in counts.values() if c > 0]
    return -sum(p * math.log2(p) for p in probs)


def build_confidence_section(bundle: ReportInputBundle) -> ConfidenceSection:
    """Per-round stance distribution + split-confidence summary at round 7."""
    rounds_by_id = {r.id: r for r in bundle.rounds}

    # Group agent_responses by round_number.
    by_round: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ar in bundle.agent_responses:
        round_obj = rounds_by_id.get(ar.round_id)
        if round_obj is None:
            continue
        by_round[round_obj.round_number][ar.stance] += 1

    # Per-round dist as list of (stance, count).
    distribution: list[list[StanceCount]] = []
    for rn in sorted(by_round.keys()):
        per = by_round[rn]
        distribution.append(
            sorted(
                [StanceCount(stance=s, count=c) for s, c in per.items()],
                key=lambda x: -x.count,
            )
        )

    # Split confidence at round 7 (or last round if fewer).
    last_round = max(by_round.keys()) if by_round else 0
    final_dist = dict(by_round.get(last_round, {}))
    sorted_buckets = sorted(final_dist.items(), key=lambda x: -x[1])
    largest = sorted_buckets[0] if sorted_buckets else ("none", 0)
    second = sorted_buckets[1] if len(sorted_buckets) > 1 else (None, 0)
    total = sum(final_dist.values())
    sep = (largest[1] / total) if total > 0 else 0.0

    e1 = _entropy(by_round.get(1, {}))
    e7 = _entropy(by_round.get(7, by_round.get(last_round, {})))
    if sep >= 0.7:
        interp = "narrow"
    elif sep >= 0.45:
        interp = "split"
    else:
        interp = "broad"

    sc = SplitConfidence(
        largest_bucket_stance=largest[0],
        largest_bucket_count=largest[1],
        second_bucket_stance=second[0],
        second_bucket_count=second[1] or 0,
        separation_ratio=round(sep, 3),
        entropy_round_1=round(e1, 3),
        entropy_round_7=round(e7, 3),
        interpretation=interp,
    )

    summary = (
        f"At the final round, the largest stance bucket was '{sc.largest_bucket_stance}' "
        f"with {sc.largest_bucket_count} of {total} agent(s); the distribution "
        f"appeared {interp} (separation ratio {sc.separation_ratio:.2f})."
    )
    return ConfidenceSection(
        summary=summary,
        split_confidence=sc,
        stance_distribution_by_round=distribution,
    )


# ---------------------------------------------------------------------------
# Section 9b — evidence ledger counts + missing + claim traceability
# ---------------------------------------------------------------------------


def build_evidence_ledger_section(
    bundle: ReportInputBundle,
) -> EvidenceLedgerSection:
    direct = sum(1 for r in bundle.competitor_evidence.ranked
                 + bundle.pricing_evidence.ranked
                 + bundle.trust_barrier_evidence.ranked
                 + bundle.positioning_evidence.ranked
                 + bundle.market_acceptance_evidence.ranked
                 if r.item.kind == "direct")
    analogical = sum(1 for r in bundle.competitor_evidence.ranked
                     + bundle.pricing_evidence.ranked
                     + bundle.trust_barrier_evidence.ranked
                     + bundle.positioning_evidence.ranked
                     + bundle.market_acceptance_evidence.ranked
                     if r.item.kind == "analogical")
    counts = EvidenceLedgerCounts(
        direct_count=direct,
        analogical_count=analogical,
        missing_count=bundle.missing_evidence.total,
    )

    missing_entries: list[MissingEvidenceLedgerEntry] = []
    for node_class, items in bundle.missing_evidence.by_node_class.items():
        for it in items:
            missing_entries.append(
                MissingEvidenceLedgerEntry(
                    evidence_id=it.id,
                    node_class=node_class,
                    summary=(it.content or "")[:200],
                )
            )

    traceability_entries: list[ClaimTraceabilityEntry] = []
    for ct in bundle.claim_traceability:
        if ct.source_evidence is None:
            continue
        traceability_entries.append(
            ClaimTraceabilityEntry(
                claim_id=ct.claim.id,
                claim_text=ct.claim.text,
                source_evidence_id=ct.claim.source_evidence_id,
                source_url=ct.claim.source_url,
                source_excerpt=ct.claim.source_excerpt,
                claim_type=ct.claim.claim_type,  # type: ignore[arg-type]
                basis=ct.claim.basis,  # type: ignore[arg-type]
            )
        )

    return EvidenceLedgerSection(
        counts=counts,
        missing=missing_entries,
        claim_traceability=traceability_entries,
    )


# ---------------------------------------------------------------------------
# Convenience — roll-ups consumed by Calls A/B/C
# ---------------------------------------------------------------------------


def collect_top_objections(
    bundle: ReportInputBundle, *, limit: int = 12,
) -> list[dict[str, Any]]:
    """Roll up `agent_responses.objections[*]` across all rounds. Used as
    fenced data input for Call A so the LLM grounds resistance language in
    actual simulation responses, not invention."""
    by_text: dict[str, dict[str, Any]] = {}
    for ar in bundle.agent_responses:
        for obj in (ar.objections or []):
            text = (obj.get("text") or "").strip()
            if not text:
                continue
            entry = by_text.setdefault(
                text,
                {
                    "text": text,
                    "category": obj.get("category"),
                    "severity": obj.get("severity"),
                    "count": 0,
                    "agent_response_ids": [],
                },
            )
            entry["count"] += 1
            entry["agent_response_ids"].append(str(ar.id))
    sorted_objs = sorted(by_text.values(), key=lambda x: -x["count"])
    return sorted_objs[:limit]


def collect_top_persuasion_drivers(
    bundle: ReportInputBundle, *, limit: int = 12,
) -> list[dict[str, Any]]:
    by_text: dict[str, dict[str, Any]] = {}
    for ar in bundle.agent_responses:
        for d in (ar.persuasion_drivers or []):
            text = (d.get("text") or "").strip()
            if not text:
                continue
            entry = by_text.setdefault(
                text,
                {
                    "text": text,
                    "category": d.get("category"),
                    "strength": d.get("strength"),
                    "count": 0,
                    "agent_response_ids": [],
                    "evidence_anchors": d.get("evidence_anchors") or [],
                },
            )
            entry["count"] += 1
            entry["agent_response_ids"].append(str(ar.id))
    sorted_drv = sorted(by_text.values(), key=lambda x: -x["count"])
    return sorted_drv[:limit]


def collect_round_summaries(bundle: ReportInputBundle) -> list[dict[str, Any]]:
    """Per-round summary blocks (no LLM-generated text inside)."""
    out: list[dict[str, Any]] = []
    for r in sorted(bundle.rounds, key=lambda x: x.round_number):
        out.append(
            {
                "round_number": r.round_number,
                "round_type": r.round_type,
                "summary": r.summary or {},
            }
        )
    return out
