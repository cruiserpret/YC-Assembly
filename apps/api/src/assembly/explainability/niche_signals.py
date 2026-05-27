"""Phase 12F.1 — Niche signals panel.

Surfaces what the bucket-level summary collapses:
  * minority_objections — objections raised by 1-3 personas across
    ≥2 audience_roles (or ≥2 cohorts in legacy mode).
  * unexpected_segments — cohorts whose bucket distribution diverges
    from the global distribution by TVD ≥0.25 (n_personas ≥3).
  * edge_case_use_cases — singleton conditions_to_buy entries on
    one persona with non-trivial length.
  * one_question_for_real_customers — the highest-signal minority
    objection NOT already in known_objections, ALWAYS phrased as a
    question.

Every signal cites evidence_anchors. No new LLM calls. The
clustering is intentionally trivial (lowercased substring match) —
deep clustering is deferred to a later phase.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any


# Minority threshold: how few raisers makes something a niche signal.
_MINORITY_MIN_RAISERS = 1
_MINORITY_MAX_RAISERS = 3
# Cross-role/cohort minimum: avoids surfacing single-persona idiosyncrasies.
_MIN_DISTINCT_ROLES = 2
# Unexpected segment TVD threshold + minimum cohort size.
_TVD_THRESHOLD = 0.25
_MIN_COHORT_PERSONAS = 3
# Edge-case use-case min text length to filter out fragments.
_EDGE_CASE_MIN_LEN = 12


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _objection_text_for_persona(
    pid: str,
    pre_dicts: dict[str, dict[str, Any]],
    final_dicts: dict[str, dict[str, Any]],
) -> str | None:
    final = final_dicts.get(pid) or {}
    if final.get("top_objection"):
        return str(final["top_objection"])
    pre = pre_dicts.get(pid) or {}
    if pre.get("top_objection"):
        return str(pre["top_objection"])
    return None


def _build_minority_objections(
    *,
    drafts: list[dict[str, Any]],
    pre_dicts: dict[str, dict[str, Any]],
    final_dicts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_objection: dict[str, dict[str, Any]] = {}
    for d in drafts:
        pid = str(d.get("persona_id") or "")
        role = d.get("audience_role") or "unknown"
        raw = _objection_text_for_persona(pid, pre_dicts, final_dicts)
        if not raw:
            continue
        key = _normalize(raw)
        if not key:
            continue
        entry = by_objection.setdefault(key, {
            "representative_text": raw,
            "raisers": [],
            "roles": set(),
            "evidence_anchors": set(),
        })
        entry["raisers"].append(pid)
        entry["roles"].add(role)
        anchor = d.get("evidence_basis") or d.get("intent_signal_basis")
        if anchor:
            entry["evidence_anchors"].add(str(anchor))
    out: list[dict[str, Any]] = []
    for key, e in by_objection.items():
        n = len(e["raisers"])
        if not (_MINORITY_MIN_RAISERS <= n <= _MINORITY_MAX_RAISERS):
            continue
        if len(e["roles"]) < _MIN_DISTINCT_ROLES:
            continue
        if not e["evidence_anchors"]:
            continue
        out.append({
            "cluster_id": key[:80],
            "representative_text": e["representative_text"],
            "raised_by_count": n,
            "raised_by_roles": sorted(e["roles"]),
            "raised_by_persona_ids": list(e["raisers"]),
            "evidence_anchors": sorted(e["evidence_anchors"])[:5],
        })
    # Determinism: sort by (count desc, text asc)
    out.sort(key=lambda r: (-r["raised_by_count"], r["cluster_id"]))
    return out


def _bucket_distribution_for_drafts(
    drafts: list[dict[str, Any]],
) -> dict[str, int]:
    from assembly.calibration.market_buckets import (
        pick_market_bucket_with_role,
    )
    counts = {"buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 0}
    for d in drafts:
        if not d.get("is_scorable", True):
            continue
        try:
            bucket, _ = pick_market_bucket_with_role(
                audience_role=d.get("audience_role"),
                intent_signal=d.get("intent_signal"),
                intent_label=d.get("simulated_intent"),
                intent_signal_routing_enabled=None,
            )
        except Exception:
            continue
        if bucket in counts:
            counts[bucket] += 1
    return counts


def _normalize_distribution(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}


def _tvd(a: dict[str, float], b: dict[str, float]) -> float:
    """Total Variation Distance between two probability distributions
    over the same keys (missing keys treated as 0)."""
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def _build_unexpected_segments(
    *,
    drafts: list[dict[str, Any]],
    cohort_summaries: list[dict[str, Any]],
    cohort_persona_lists: list[list[str]],
) -> list[dict[str, Any]]:
    if not cohort_persona_lists:
        return []
    global_dist = _normalize_distribution(
        _bucket_distribution_for_drafts(drafts),
    )
    drafts_by_pid: dict[str, dict[str, Any]] = {
        str(d.get("persona_id") or ""): d for d in drafts
    }
    out: list[dict[str, Any]] = []
    for i, persona_ids in enumerate(cohort_persona_lists):
        # Skip undersized cohorts.
        if len(persona_ids) < _MIN_COHORT_PERSONAS:
            continue
        cohort_drafts = [
            drafts_by_pid[str(pid)] for pid in persona_ids
            if str(pid) in drafts_by_pid
        ]
        if not cohort_drafts:
            continue
        cohort_counts = _bucket_distribution_for_drafts(cohort_drafts)
        cohort_dist = _normalize_distribution(cohort_counts)
        divergence = _tvd(cohort_dist, global_dist)
        if divergence < _TVD_THRESHOLD:
            continue
        # Build anchors from member drafts
        anchors: list[str] = []
        for d in cohort_drafts:
            a = d.get("evidence_basis")
            if a and a not in anchors:
                anchors.append(str(a))
            if len(anchors) >= 5:
                break
        cohort_label = (
            (cohort_summaries[i].get("cohort_label")
             if i < len(cohort_summaries) else None)
            or f"cohort_{i}"
        )
        out.append({
            "cohort_index": i,
            "cohort_label": cohort_label,
            "n_personas": len(cohort_drafts),
            "bucket_distribution_pct": {
                k: round(100.0 * v, 2) for k, v in cohort_dist.items()
            },
            "global_bucket_distribution_pct": {
                k: round(100.0 * v, 2) for k, v in global_dist.items()
            },
            "diverges_from_global_by_tvd": round(divergence, 3),
            "evidence_anchors": anchors,
            "interpretation_hint": (
                "This segment's bucket mix diverges substantially "
                "from the global distribution — worth investigating "
                "before assuming the audience is uniform."
            ),
        })
    out.sort(key=lambda r: -r["diverges_from_global_by_tvd"])
    return out


def _build_edge_case_use_cases(
    drafts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_condition: dict[str, list[tuple[str, str | None]]] = {}
    for d in drafts:
        for cond in (d.get("conditions_to_buy") or []):
            raw = str(cond).strip()
            if len(raw) < _EDGE_CASE_MIN_LEN:
                continue
            key = _normalize(raw)
            anchor = (
                d.get("evidence_basis") or d.get("intent_signal_basis")
            )
            by_condition.setdefault(key, []).append(
                (str(d.get("persona_id") or ""), anchor),
            )
    out: list[dict[str, Any]] = []
    for key, raisers in by_condition.items():
        if len(raisers) != 1:
            continue
        pid, anchor = raisers[0]
        if not anchor:
            continue
        out.append({
            "use_case": key,
            "raised_by_persona_id": pid,
            "evidence_anchor": anchor,
        })
    out.sort(key=lambda r: r["use_case"])
    # Cap to keep panel readable.
    return out[:8]


def _phrase_as_question(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = t.rstrip(".!")
    if t.endswith("?"):
        return t
    lower = t.lower()
    # Heuristic prefixing: anchor the question on real customers.
    if lower.startswith(("does ", "do ", "is ", "are ", "would ",
                         "could ", "should ", "have ", "has ",
                         "what ", "why ", "how ", "when ", "where ",
                         "who ", "which ")):
        return t + "?"
    return f"Has anyone ever raised this with you: {t}?"


def _build_one_question(
    *,
    minority_objections: list[dict[str, Any]],
    known_objections_normalized: set[str],
) -> str | None:
    for entry in minority_objections:
        key = entry["cluster_id"]
        # Match against known_objections (substring either way).
        if any(
            k in key or key in k
            for k in known_objections_normalized
        ):
            continue
        return _phrase_as_question(entry["representative_text"])
    return None


def build_niche_signals(
    *,
    brief: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    drafts = ctx.get("augmented_intent_drafts") or []
    if not drafts:
        # Fallback: build minimal drafts list from legacy intent drafts
        legacy = ctx.get("intent_drafts") or []
        drafts = []
        for d in legacy:
            row = (
                d.model_dump(mode="json")
                if hasattr(d, "model_dump") else dict(d)
            )
            row.setdefault(
                "audience_role", "target_customer_evaluator",
            )
            row.setdefault("is_scorable", True)
            drafts.append(row)
    pre_dicts = ctx.get("pre_dicts") or {}
    final_dicts = ctx.get("final_dicts") or {}
    cohort_summaries = ctx.get("cohort_summaries") or []
    cohort_persona_lists = ctx.get("cohort_persona_lists") or []

    minority_objections = _build_minority_objections(
        drafts=drafts,
        pre_dicts=pre_dicts,
        final_dicts=final_dicts,
    )
    unexpected_segments = _build_unexpected_segments(
        drafts=drafts,
        cohort_summaries=cohort_summaries,
        cohort_persona_lists=cohort_persona_lists,
    )
    edge_case_use_cases = _build_edge_case_use_cases(drafts)
    known_norm = {
        _normalize(k) for k in (brief.get("known_objections") or [])
        if k
    }
    one_question = _build_one_question(
        minority_objections=minority_objections,
        known_objections_normalized=known_norm,
    )
    return {
        "phase": "12f.1",
        "minority_objections": minority_objections[:10],
        "unexpected_segments": unexpected_segments[:5],
        "edge_case_use_cases": edge_case_use_cases,
        "one_question_for_real_customers": one_question,
        "_caveat": (
            "Niche signals are aggregated from the SAME synthetic "
            "ballots that produced the headline distribution; they "
            "highlight low-frequency objections rather than introducing "
            "new ones. Treat them as prompts for real customer "
            "conversations, not as standalone findings."
        ),
    }
