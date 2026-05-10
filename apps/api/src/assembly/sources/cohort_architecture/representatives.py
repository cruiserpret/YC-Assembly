"""Phase 9D — representative selection within a cohort.

Each cohort gets:
  - primary representative: persona closest to cohort centroid
  - dissent representative: persona whose final stance differs most
                             from cohort majority
  - proof-threshold representative: persona with the highest
                                     trust_proof_threshold psychology
                                     value, used for proof-need framing

Returns persona_ids only; never invents a new persona.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any


def _euclidean(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a.keys()) | set(b.keys())
    total = 0.0
    for k in keys:
        d = a.get(k, 0.0) - b.get(k, 0.0)
        total += d * d
    return math.sqrt(total)


def _centroid(vecs: list[dict[str, float]]) -> dict[str, float]:
    if not vecs:
        return {}
    keys: set = set()
    for v in vecs:
        keys.update(v.keys())
    out: dict[str, float] = {}
    for k in keys:
        s = sum(v.get(k, 0.0) for v in vecs)
        out[k] = s / max(len(vecs), 1)
    return out


def select_cohort_representatives(
    *,
    cohort_persona_ids: list[str],
    persona_features: dict[str, dict[str, float]],
    persona_meta: dict[str, dict[str, Any]],
) -> dict[str, str | None]:
    """Return a dict {primary, dissent, proof_threshold} of persona_ids
    drawn from the cohort. `persona_meta` carries final_stance and
    psychology_value_map per persona."""
    if not cohort_persona_ids:
        return {
            "primary": None, "dissent": None, "proof_threshold": None,
        }
    vecs = [persona_features[p] for p in cohort_persona_ids if p in persona_features]
    centroid = _centroid(vecs)
    distances = sorted(
        ((pid, _euclidean(centroid, persona_features.get(pid, {})))
         for pid in cohort_persona_ids),
        key=lambda t: (t[1], t[0]),
    )
    primary = distances[0][0]
    # Dissent: persona whose final_stance is the rarest in the cohort,
    # tie-broken by largest distance from centroid (most different).
    final_counter = Counter(
        persona_meta.get(pid, {}).get("final_stance")
        for pid in cohort_persona_ids
    )
    rarest_stance = (
        min(
            final_counter,
            key=lambda s: (final_counter[s], s or ""),
        ) if final_counter else None
    )
    dissent_candidates = [
        pid for pid in cohort_persona_ids
        if persona_meta.get(pid, {}).get("final_stance") == rarest_stance
        and pid != primary
    ]
    dissent: str | None = None
    if dissent_candidates:
        dissent = max(
            dissent_candidates,
            key=lambda pid: (
                _euclidean(centroid, persona_features.get(pid, {})),
                pid,
            ),
        )
    # Proof-threshold rep: persona with highest trust_proof_threshold
    proof_rep_candidates = sorted(
        cohort_persona_ids,
        key=lambda pid: (
            -float(persona_meta.get(pid, {}).get(
                "psychology_value_map", {},
            ).get("trust_proof_threshold", 0.5)),
            pid,
        ),
    )
    proof_rep: str | None = None
    for cand in proof_rep_candidates:
        if cand != primary:
            proof_rep = cand
            break
    if proof_rep is None and proof_rep_candidates:
        proof_rep = proof_rep_candidates[0]
    return {
        "primary": primary,
        "dissent": dissent,
        "proof_threshold": proof_rep,
    }
