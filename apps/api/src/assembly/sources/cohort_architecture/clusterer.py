"""Phase 9D — deterministic agglomerative clustering for cohort assembly.

Pure-Python, no scipy / sklearn dependency. Time complexity for our
target n=66 is trivial. The algorithm:

  1. Each persona starts as its own singleton cluster.
  2. Compute the pairwise distance matrix (euclidean over the feature
     vector dict — vectors must share keys).
  3. Use single-link (closest pair of points) merging; ties broken by
     a stable sort over (persona_id_a, persona_id_b) so the result is
     fully reproducible.
  4. Stop when cluster_count <= target_max AND no remaining merge
     would push the largest cluster size above max_cluster_size.
  5. If cluster_count is still > target_max but max-cluster-size is
     blocking, allow one merge that violates max_cluster_size and
     audit the exception.

Returns a list of clusters where each cluster is a list of persona_id
strings, sorted internally by persona_id and externally by size desc.
"""
from __future__ import annotations

import math
from typing import Iterable


def _euclidean(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a.keys()) | set(b.keys())
    total = 0.0
    for k in keys:
        d = a.get(k, 0.0) - b.get(k, 0.0)
        total += d * d
    return math.sqrt(total)


def _pair_key(i: str, j: str) -> tuple[str, str]:
    return (i, j) if i < j else (j, i)


def cluster_personas_into_cohorts(
    *,
    persona_ids: list[str],
    feature_vectors: list[dict[str, float]],
    target_min_cohorts: int = 8,
    target_max_cohorts: int = 14,
    min_cluster_size: int = 3,
    max_cluster_size: int = 10,
) -> tuple[list[list[str]], dict[str, object]]:
    """Return (cohorts, audit). `cohorts[i]` is a sorted list of
    persona_id strings. The audit dict tracks the merge sequence,
    final cluster sizes, and any size-cap exceptions."""
    if len(persona_ids) != len(feature_vectors):
        raise ValueError(
            "persona_ids and feature_vectors must align"
        )
    n = len(persona_ids)
    audit: dict[str, object] = {
        "input_persona_count": n,
        "target_min_cohorts": target_min_cohorts,
        "target_max_cohorts": target_max_cohorts,
        "min_cluster_size": min_cluster_size,
        "max_cluster_size": max_cluster_size,
        "merges": 0,
        "size_cap_exceptions": [],
        "method": "deterministic_agglomerative_v1",
    }

    if n == 0:
        audit["final_cluster_count"] = 0
        return [], audit

    # Each cluster is a sorted list of persona_id strings.
    clusters: list[list[str]] = [[pid] for pid in persona_ids]
    # Map cluster index → list of feature vector indices (kept stable
    # by persona_id sort).
    pid_to_idx = {pid: i for i, pid in enumerate(persona_ids)}

    # Precompute sorted list of pairwise distances using the original
    # singleton assumption. We recompute on-demand at merge time.
    def _cluster_dist(a: list[str], b: list[str]) -> float:
        # single-link: min point distance
        best = math.inf
        for ia in a:
            for ib in b:
                va = feature_vectors[pid_to_idx[ia]]
                vb = feature_vectors[pid_to_idx[ib]]
                d = _euclidean(va, vb)
                if d < best:
                    best = d
        return best

    # Initialize pairwise singleton distances
    pair_dist: dict[tuple[str, str], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            pid_i, pid_j = persona_ids[i], persona_ids[j]
            pair_dist[_pair_key(pid_i, pid_j)] = _euclidean(
                feature_vectors[i], feature_vectors[j],
            )

    while len(clusters) > target_max_cohorts:
        # Find the closest pair of clusters honoring max_cluster_size
        best_pair: tuple[int, int] | None = None
        best_dist = math.inf
        for ci in range(len(clusters)):
            for cj in range(ci + 1, len(clusters)):
                merged_size = len(clusters[ci]) + len(clusters[cj])
                if merged_size > max_cluster_size:
                    continue
                d = _cluster_dist(clusters[ci], clusters[cj])
                # Stable tie-break: by sorted (head_id_a, head_id_b)
                if d < best_dist or (
                    d == best_dist
                    and best_pair is not None
                    and (clusters[ci][0], clusters[cj][0])
                    < (clusters[best_pair[0]][0], clusters[best_pair[1]][0])
                ):
                    best_dist = d
                    best_pair = (ci, cj)
        if best_pair is None:
            # No legal merge under max_cluster_size; allow a single
            # exception merge to keep collapsing toward target.
            best_dist = math.inf
            for ci in range(len(clusters)):
                for cj in range(ci + 1, len(clusters)):
                    d = _cluster_dist(clusters[ci], clusters[cj])
                    if d < best_dist:
                        best_dist = d
                        best_pair = (ci, cj)
            if best_pair is None:
                break
            audit_size = len(clusters[best_pair[0]]) + len(clusters[best_pair[1]])
            audit["size_cap_exceptions"].append({
                "merged_size": audit_size,
                "max_cluster_size": max_cluster_size,
                "reason": (
                    "no legal merge under max_cluster_size; allowed one "
                    "exception merge to reach target_max_cohorts"
                ),
            })
        ci, cj = best_pair
        merged = sorted(clusters[ci] + clusters[cj])
        # Remove larger index first so the smaller index stays valid
        del clusters[max(ci, cj)]
        del clusters[min(ci, cj)]
        clusters.append(merged)
        audit["merges"] = int(audit["merges"]) + 1

    # Now re-merge any clusters under min_cluster_size into their
    # nearest neighbour (helps the 3-min-size requirement).
    moved_undersize = 0
    while True:
        small = [
            i for i, c in enumerate(clusters) if len(c) < min_cluster_size
        ]
        if not small:
            break
        if len(clusters) <= target_min_cohorts:
            break
        si = small[0]
        # find nearest other cluster
        best_target: int | None = None
        best_dist = math.inf
        for cj in range(len(clusters)):
            if cj == si:
                continue
            merged_size = len(clusters[si]) + len(clusters[cj])
            if merged_size > max_cluster_size:
                continue
            d = _cluster_dist(clusters[si], clusters[cj])
            if d < best_dist:
                best_dist = d
                best_target = cj
        if best_target is None:
            break
        merged = sorted(clusters[si] + clusters[best_target])
        for idx in sorted([si, best_target], reverse=True):
            del clusters[idx]
        clusters.append(merged)
        moved_undersize += 1
    audit["undersize_remerges"] = moved_undersize

    # Sort clusters by size desc, then by lex of first persona_id
    clusters.sort(key=lambda c: (-len(c), c[0]))
    audit["final_cluster_count"] = len(clusters)
    audit["final_cluster_sizes"] = [len(c) for c in clusters]
    return clusters, audit


def assignment_audit(
    persona_ids: Iterable[str],
    cohorts: list[list[str]],
) -> dict[str, object]:
    """Verify every persona is assigned exactly once."""
    pid_set = set(persona_ids)
    assigned = []
    for c in cohorts:
        assigned.extend(c)
    assigned_set = set(assigned)
    duplicate_count = len(assigned) - len(assigned_set)
    missing = sorted(pid_set - assigned_set)
    extra = sorted(assigned_set - pid_set)
    return {
        "input_persona_count": len(pid_set),
        "assigned_count": len(assigned),
        "distinct_assigned_count": len(assigned_set),
        "duplicate_assignments": duplicate_count,
        "missing_persona_ids": missing[:30],
        "extra_persona_ids": extra[:30],
        "every_persona_assigned_exactly_once": (
            duplicate_count == 0
            and not missing
            and not extra
        ),
    }
