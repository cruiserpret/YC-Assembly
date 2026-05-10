"""Phase 9A.3 — psychology profile diversity audit.

Computes per-trait variance, confidence + inference-method distributions,
and a fingerprint-based identical-profile detector. Universal — applies
to any psychology profile set, not LumaLoop-specific.
"""
from __future__ import annotations

import hashlib
import statistics
from collections import Counter
from typing import Iterable

from assembly.sources.persona_psychology_layer.schemas import (
    ALL_REQUIRED_OCEAN_PLUS_FIVE,
    OCEAN_TRAITS,
    PsychologyProfile,
)


def compute_profile_variance(
    profiles: Iterable[PsychologyProfile],
) -> dict[str, object]:
    """Return per-trait min/max/mean/stdev plus aggregate counts."""
    profile_list = list(profiles)
    by_trait: dict[str, list[float]] = {}
    for prof in profile_list:
        for t in prof.traits:
            by_trait.setdefault(t.trait_name, []).append(float(t.value_numeric))
    per_trait_stats: dict[str, dict[str, float]] = {}
    for name, vals in by_trait.items():
        if not vals:
            continue
        per_trait_stats[name] = {
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "mean": round(sum(vals) / len(vals), 4),
            "stdev": round(
                statistics.stdev(vals) if len(vals) >= 2 else 0.0, 4,
            ),
            "count": len(vals),
        }
    confidence_dist: Counter = Counter()
    method_dist: Counter = Counter()
    label_dist: Counter = Counter()
    neutral_default_count = 0
    total_traits = 0
    for prof in profile_list:
        for t in prof.traits:
            confidence_dist[t.confidence] += 1
            method_dist[t.inference_method] += 1
            label_dist[t.value_label] += 1
            if t.inference_method == "neutral_default":
                neutral_default_count += 1
            total_traits += 1
    ocean_means: dict[str, float] = {
        n: per_trait_stats.get(n, {}).get("mean", 0.0) for n in OCEAN_TRAITS
    }
    additional_means: dict[str, float] = {
        n: per_trait_stats.get(n, {}).get("mean", 0.0)
        for n in ALL_REQUIRED_OCEAN_PLUS_FIVE if n not in OCEAN_TRAITS
    }
    return {
        "profile_count": len(profile_list),
        "total_traits": total_traits,
        "per_trait_stats": per_trait_stats,
        "ocean_means": ocean_means,
        "additional_trait_means": additional_means,
        "confidence_distribution": dict(confidence_dist),
        "inference_method_distribution": dict(method_dist),
        "value_label_distribution": dict(label_dist),
        "neutral_default_count": neutral_default_count,
        "neutral_default_pct": round(
            neutral_default_count / max(total_traits, 1), 4,
        ),
        "medium_or_high_confidence_pct": round(
            (
                confidence_dist.get("high", 0)
                + confidence_dist.get("medium", 0)
            ) / max(total_traits, 1),
            4,
        ),
    }


def _profile_fingerprint(prof: PsychologyProfile) -> str:
    """Stable hash over (trait_name, value_label) pairs sorted by name.
    Two profiles with the same label-pattern collide regardless of
    persona_id or evidence_basis text.
    """
    pairs = sorted(
        (t.trait_name, t.value_label) for t in prof.traits
    )
    payload = "|".join(f"{n}={v}" for n, v in pairs)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def detect_identical_profiles(
    profiles: Iterable[PsychologyProfile],
    *,
    max_identical_pct: float = 0.35,
) -> dict[str, object]:
    """Group profiles by their (trait_name, value_label) fingerprint and
    flag the largest cluster if it exceeds `max_identical_pct` of the
    population."""
    profile_list = list(profiles)
    n = len(profile_list)
    if n == 0:
        return {
            "profile_count": 0,
            "fingerprint_count": 0,
            "max_cluster_size": 0,
            "max_cluster_pct": 0.0,
            "max_identical_pct_threshold": max_identical_pct,
            "warning": "no profiles supplied",
            "exceeds_threshold": False,
        }
    fingerprints: list[str] = [_profile_fingerprint(p) for p in profile_list]
    cluster_sizes = Counter(fingerprints)
    largest_fp, largest_n = cluster_sizes.most_common(1)[0]
    pct = largest_n / n
    return {
        "profile_count": n,
        "fingerprint_count": len(cluster_sizes),
        "max_cluster_fingerprint": largest_fp,
        "max_cluster_size": largest_n,
        "max_cluster_pct": round(pct, 4),
        "max_identical_pct_threshold": max_identical_pct,
        "exceeds_threshold": pct > max_identical_pct,
        "warning": (
            f"largest identical-profile cluster = {largest_n}/{n} "
            f"({pct:.0%}) — exceeds {max_identical_pct:.0%} ceiling"
            if pct > max_identical_pct else None
        ),
    }
