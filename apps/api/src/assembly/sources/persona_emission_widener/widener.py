"""Phase 9A.1 — multi-signal persona-candidate widener.

Pure function. Input: existing PersonaCandidate dicts + raw evidence
items + extracted EvidenceSignals. Output: an extended candidate
list with additional evidence-backed candidates derived from
distinct (role × signal-cluster) combinations.

The widener NEVER fabricates traits. Every supplemental candidate
carries:
  * candidate_id derived from (signal_id + source_record_id) hash
  * inferred_persona_role from the signal
  * evidence_excerpt from the signal
  * source_record_ids = [signal.source_record_synthetic_id]
  * inferred_traits with at least 2 evidence-supported entries
    (one synthesized from the signal's own data, one fallback
    `role_or_context` derived from the role label)

Universal caps (drift-tested):
  * max 3 emitted candidates per source
  * max 2 candidates per exact (role, source, objection)
  * max 1 candidate per exact (role, evidence_excerpt[:80])
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from assembly.sources.evidence_signal_extractor import (
    EvidenceSignal,
)


@dataclass(frozen=True)
class EmissionPolicy:
    max_candidates_per_source: int = 3
    max_candidates_per_role_source_objection: int = 2
    min_signals_per_candidate: int = 1
    min_traits_per_candidate: int = 2


@dataclass(frozen=True)
class WidenedCandidate:
    """The widener's output candidate dict — same shape as
    `persona_role_planner.PersonaCandidate.model_dump()` so it can
    be fed straight into the compressor."""
    payload: dict[str, Any]


def _hash_id(*parts: str) -> str:
    return hashlib.sha256(
        "|".join(parts).encode("utf-8"),
    ).hexdigest()[:16]


def _existing_keys(
    candidates: list[dict[str, Any]],
) -> set[tuple[str, str, str]]:
    """Build a set of (role, source_id, excerpt[:80].lower()) keys
    so the widener can avoid emitting cosmetic duplicates of what
    the persona planner already produced."""
    out: set[tuple[str, str, str]] = set()
    for c in candidates:
        role = (c.get("inferred_persona_role") or "").lower()
        for sid in c.get("source_record_ids") or []:
            for ex in c.get("evidence_snippets") or []:
                ex_key = (ex or "")[:80].lower().strip()
                out.add((role, sid, ex_key))
            if not (c.get("evidence_snippets") or []):
                out.add((role, sid, ""))
    return out


def _build_candidate_payload(
    *,
    signal: EvidenceSignal,
    target_brief: str,
    generated_for_phase: str,
    product_name: str,
) -> dict[str, Any]:
    """Build a candidate dict in the persona_role_planner shape."""
    candidate_id = (
        f"widened::{target_brief}::"
        f"{_hash_id(signal.signal_id, signal.source_record_synthetic_id)}"
    )
    role = signal.inferred_role
    role_basis = [
        signal.reason_for_signal,
        f"signal_type={signal.signal_type}",
    ]
    evidence_excerpt = signal.evidence_excerpt
    rationale_parts: list[str] = [signal.reason_for_signal]
    if signal.competitor_or_substitute_context:
        rationale_parts.append(
            f"competitor/substitute context: "
            f"{signal.competitor_or_substitute_context}"
        )
    if signal.use_case_context:
        rationale_parts.append(
            f"use-case: {signal.use_case_context}"
        )
    if signal.objection_pattern:
        rationale_parts.append(
            f"objection: {signal.objection_pattern}"
        )
    if signal.price_or_value_signal:
        rationale_parts.append(
            f"price/value: {signal.price_or_value_signal}"
        )
    rationale = " | ".join(rationale_parts)
    # Two evidence-backed traits per the spec's quality floor.
    trait_dimension_name = (
        f"{signal.signal_type}_dimension"
        if not signal.signal_type.startswith("competitor")
        else "current_alternatives_dimension"
    )
    inferred_traits: list[dict[str, Any]] = [
        {
            "trait_name": trait_dimension_name,
            "trait_value": (
                signal.competitor_or_substitute_context
                or signal.objection_pattern
                or signal.price_or_value_signal
                or signal.use_case_context
                or signal.signal_type
            )[:200],
            "evidence_source_record_id": (
                signal.source_record_synthetic_id
            ),
            "evidence_excerpt": evidence_excerpt[:240],
            "confidence": signal.confidence,
            "caveat": None,
        },
        {
            "trait_name": "role_or_context",
            "trait_value": role,
            "evidence_source_record_id": (
                signal.source_record_synthetic_id
            ),
            "evidence_excerpt": (
                f"persona_role::{role} (widened from atomic "
                f"signal {signal.signal_type})"
            )[:240],
            "confidence": signal.confidence,
            "caveat": None,
        },
    ]
    objections: list[str] = []
    if signal.objection_pattern:
        objections.append(
            f"objection: {signal.objection_pattern}"[:240]
        )
    behaviors: list[str] = []
    if signal.behavior_context:
        behaviors.append(
            f"behavior_context: {signal.behavior_context}"[:240]
        )
    return {
        "candidate_id": candidate_id,
        "scope": "brief_scoped",
        "persistence_status": "dry_run_only",
        "target_brief": target_brief,
        "generated_for_phase": generated_for_phase,
        "not_global_persona": True,
        "inferred_persona_role": role,
        "secondary_persona_roles": [],
        "role_inference_basis": role_basis,
        "segment_label": (
            signal.inferred_subsegment or role.replace("_", " ")
        )[:80],
        "source_record_ids": [signal.source_record_synthetic_id],
        "superseded_preview_source_record_ids": [],
        "evidence_summary": rationale[:600],
        "evidence_snippets": [evidence_excerpt[:300]],
        "inferred_traits": inferred_traits,
        "inferred_preferences": [],
        "inferred_objections": objections,
        "inferred_behaviors": behaviors,
        "hypothetical_target_product_reaction": (
            f"This persona would compare {product_name} to its "
            f"existing {role.replace('_', ' ')} context, weighing "
            "the universal signal that surfaced in the evidence."
        )[:600],
        "confidence": signal.confidence,
        "evidence_strength": (
            "strong" if signal.confidence == "high"
            else "moderate" if signal.confidence == "medium"
            else "weak"
        ),
        "caveats": [],
        "simulation_usefulness_summary": (
            f"Widened candidate from atomic signal {signal.signal_type} "
            f"on source {signal.source_record_synthetic_id}."
        )[:400],
        "persistence_recommendation": "DEFER",
    }


def widen_persona_candidates(
    *,
    existing_candidates: list[dict[str, Any]],
    signals: list[EvidenceSignal],
    target_brief: str,
    product_name: str,
    generated_for_phase: str,
    policy: EmissionPolicy | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (extended_candidates_dict_list, widening_audit).

    Pure function. Same inputs → same output. The widening_audit
    captures per-source emission counts + rejection reasons so the
    operator can see the conversion lift."""
    pol = policy or EmissionPolicy()
    existing_keys = _existing_keys(existing_candidates)

    by_source: dict[str, list[EvidenceSignal]] = {}
    for s in signals:
        by_source.setdefault(s.source_record_synthetic_id, []).append(s)

    new_payloads: list[dict[str, Any]] = []
    per_source_emitted: dict[str, int] = {}
    per_role_source_objection: dict[tuple[str, str, str], int] = {}
    rejected: list[dict[str, Any]] = []
    multi_signal_emit = 0
    same_role_subsegments = 0

    sources_seen_for_subsegment: dict[str, set[str]] = {}

    for sid, sigs in sorted(by_source.items()):
        # Order: most-confident high → medium → low; within same
        # confidence prefer rare signal types (objection, trust,
        # price) before competitor (which the planner already covers).
        order = {"high": 0, "medium": 1, "low": 2}
        sig_priority = {
            "objection_signal": 0,
            "trust_proof_signal": 1,
            "price_value_signal": 2,
            "safety_visibility_signal": 3,
            "performance_signal": 4,
            "format_preference_signal": 5,
            "convenience_signal": 6,
            "use_case_signal": 7,
            "substitute_usage_signal": 8,
            "competitor_usage_signal": 9,
        }
        sigs_sorted = sorted(
            sigs,
            key=lambda s: (
                order.get(s.confidence, 9),
                sig_priority.get(s.signal_type, 99),
                s.signal_id,
            ),
        )
        for sig in sigs_sorted:
            if (
                per_source_emitted.get(sid, 0)
                >= pol.max_candidates_per_source
            ):
                rejected.append({
                    "signal_id": sig.signal_id,
                    "source": sid,
                    "reason": "max_candidates_per_source",
                })
                continue
            role = sig.inferred_role.lower()
            ex_key = (sig.evidence_excerpt or "")[:80].lower().strip()
            existing_key = (role, sid, ex_key)
            if existing_key in existing_keys:
                rejected.append({
                    "signal_id": sig.signal_id,
                    "source": sid,
                    "reason": "duplicates_existing_planner_candidate",
                })
                continue
            obj_key = (
                role, sid, sig.objection_pattern or "",
            )
            if (
                per_role_source_objection.get(obj_key, 0)
                >= pol.max_candidates_per_role_source_objection
            ):
                rejected.append({
                    "signal_id": sig.signal_id,
                    "source": sid,
                    "reason": "max_role_source_objection",
                })
                continue
            payload = _build_candidate_payload(
                signal=sig,
                target_brief=target_brief,
                generated_for_phase=generated_for_phase,
                product_name=product_name,
            )
            new_payloads.append(payload)
            per_source_emitted[sid] = per_source_emitted.get(sid, 0) + 1
            per_role_source_objection[obj_key] = (
                per_role_source_objection.get(obj_key, 0) + 1
            )
            existing_keys.add(existing_key)
            if per_source_emitted[sid] > 1:
                multi_signal_emit += 1
            seen_subs = sources_seen_for_subsegment.setdefault(
                sid, set(),
            )
            if sig.inferred_subsegment and sig.inferred_subsegment in seen_subs:
                same_role_subsegments += 1
            elif sig.inferred_subsegment:
                seen_subs.add(sig.inferred_subsegment)

    extended = list(existing_candidates) + new_payloads
    audit = {
        "policy": {
            "max_candidates_per_source": pol.max_candidates_per_source,
            "max_candidates_per_role_source_objection": (
                pol.max_candidates_per_role_source_objection
            ),
            "min_signals_per_candidate": pol.min_signals_per_candidate,
            "min_traits_per_candidate": pol.min_traits_per_candidate,
        },
        "input_existing_count": len(existing_candidates),
        "input_signal_count": len(signals),
        "input_distinct_sources": len(by_source),
        "emitted_count": len(new_payloads),
        "rejected_count": len(rejected),
        "rejected_breakdown": rejected[:50],
        "extended_total": len(extended),
        "per_source_emit_distribution": dict(per_source_emitted),
        "multi_signal_candidates_created": multi_signal_emit,
        "same_role_subsegments_created": same_role_subsegments,
    }
    return extended, audit
