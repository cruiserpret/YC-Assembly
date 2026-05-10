"""Phase 8.5D.1C — diversity-aware reranker for ingestion-policy
SELECTED candidates.

`apply_diversity_aware_reranking` is a pure post-processor over the
output of `decide_candidates`. It NEVER relaxes quality gates: it
only swaps cap-rejected candidates (rejected for `*_cap` reasons,
not for PII / fake-buyer / scanner / strong-anchor failures) into
the SELECTED set when they would add a fresh role/competitor.

Quality discipline (universal, hardcoded):

  * Only candidates whose rejection reasons are ALL cap-related
    qualify for promotion.
  * Promotion happens only when a SELECTED cluster has ≥2 same-role
    candidates AND a cap-rejected candidate represents a fresh role.
  * No new candidate is promoted if it lacks a strong anchor (the
    original `decide_candidates` already enforced this).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from assembly.sources.ingestion_policy.schemas import (
    CandidateDecision,
)


def _role_key(decision: CandidateDecision) -> str:
    """Extract a coarse role key from a decision's matched_terms.

    Priority:
      1. competitor:<X> match → "competitor::<X>"
      2. substitute:<Y> match → "substitute::<Y>"
      3. positive multi-word anchor → "positive_multi"
      4. fallback → "generic"
    """
    if decision.planned_source_record_preview is None:
        return "no_preview"
    md = decision.planned_source_record_preview.metadata or {}
    matched = list(md.get("matched_terms") or [])
    for m in matched:
        if m.startswith("competitor:") and "(wrong-context)" not in m:
            return f"competitor::{m.split(':', 1)[1].strip()}"
    for m in matched:
        if m.startswith("substitute:"):
            return f"substitute::{m.split(':', 1)[1].strip()}"
    for m in matched:
        if m.startswith("positive:") and " " in m.split(":", 1)[1]:
            return "positive_multi"
    return "generic"


def _is_cap_only_rejection(decision: CandidateDecision) -> bool:
    """True iff every rejection_reason is cap-related (not a quality
    failure). Cap-related reasons currently emitted by
    `decide_candidates`:
      * `max_insert_cap=N reached`
      * `per_category_diversity_cap=N reached for category=...`
    Quality failures emit reasons starting with `reject_pii_hit`,
    `reject_fake_buyer_for_unlaunched`,
    `reject_dataset_non_compliance`, `reject_duplicate_content_hash`,
    `reject_no_strong_anchor`, `reject_below_high_confidence`."""
    if not decision.rejection_reasons:
        return False
    for r in decision.rejection_reasons:
        if r.startswith("max_insert_cap"):
            continue
        if r.startswith("per_category_diversity_cap"):
            continue
        # Any other reason → not cap-only
        return False
    return True


def apply_diversity_aware_reranking(
    decisions: list[CandidateDecision],
    *,
    target_min_unique_roles: int = 3,
) -> tuple[list[CandidateDecision], list[dict[str, Any]]]:
    """Reorder and possibly swap cap-rejected candidates into the
    SELECTED set when doing so increases unique_role_count without
    relaxing any quality gate.

    Returns (new_decisions, swap_log).

    Pure function over input decisions. Does not call any DB or LLM.

    Algorithm:
      1. Bucket SELECTED by `_role_key`.
      2. For each over-represented bucket (size ≥ 2), find the
         lowest-rank member as the swap candidate.
      3. Among cap-rejected candidates, find one whose role_key is
         NOT yet present in the SELECTED set (fresh role).
      4. If a fresh-role cap-rejected exists, swap. Otherwise leave
         the cluster as-is.
      5. Repeat until either max_distinct_roles is reached OR no more
         fresh swaps are possible.
    """
    selected = [d for d in decisions if d.decision == "SELECTED"]
    rejected = [d for d in decisions if d.decision == "REJECTED"]
    cap_rejected = [d for d in rejected if _is_cap_only_rejection(d)]
    other_rejected = [d for d in rejected if not _is_cap_only_rejection(d)]
    swap_log: list[dict[str, Any]] = []

    if not selected or not cap_rejected:
        # No swaps possible / needed.
        return list(decisions), swap_log

    selected = list(selected)
    cap_rejected = list(cap_rejected)

    # Iterative one-swap-at-a-time loop, capped by target.
    while True:
        sel_by_role: dict[str, list[CandidateDecision]] = defaultdict(list)
        for d in selected:
            sel_by_role[_role_key(d)].append(d)
        present_roles = set(sel_by_role.keys())
        unique_role_count = len(
            [r for r in present_roles if r not in ("generic", "no_preview")]
        )
        if unique_role_count >= target_min_unique_roles:
            break  # already diverse enough

        # Find the most over-represented cluster
        over_clusters = [
            (k, v) for k, v in sel_by_role.items() if len(v) >= 2
        ]
        if not over_clusters:
            break  # no more swappable clusters

        over_clusters.sort(key=lambda kv: (-len(kv[1]), kv[0]))
        cluster_key, cluster_members = over_clusters[0]

        # Find a cap-rejected with a FRESH role (not in present_roles)
        fresh_swap = None
        for cr in cap_rejected:
            cr_key = _role_key(cr)
            if cr_key in present_roles:
                continue
            if cr_key in ("generic", "no_preview"):
                continue
            fresh_swap = cr
            break
        if fresh_swap is None:
            break  # no fresh-role swap available

        # Demote the lowest-rank cluster member (last by selection_rank
        # ascending, which means highest rank number).
        cluster_members_sorted = sorted(
            cluster_members,
            key=lambda d: (d.selection_rank or 9999),
        )
        demoted = cluster_members_sorted[-1]
        # Build new demoted decision
        new_demoted = demoted.model_copy(update={
            "decision": "REJECTED",
            "selection_rank": None,
            "rejection_reasons": list(demoted.rejection_reasons) + [
                f"diversity_rerank_demoted: cluster {cluster_key!r} "
                "had >=2 members; swapped out for fresh-role candidate"
            ],
            "planned_source_record_preview": None,
        })
        # Build new promoted decision
        new_promoted = fresh_swap.model_copy(update={
            "decision": "SELECTED",
            "rejection_reasons": [],
            "decision_reasons": list(fresh_swap.decision_reasons) + [
                f"diversity_rerank_promoted: fresh role "
                f"{_role_key(fresh_swap)!r} replaces a same-role duplicate"
            ],
        })
        # Apply the swap
        selected.remove(demoted)
        selected.append(new_promoted)
        cap_rejected.remove(fresh_swap)
        # Recover the demoted as a new cap-rejection (so the loop
        # doesn't try to re-promote it accidentally)
        # Actually keep it OUT of cap_rejected so it can't be re-swapped.
        other_rejected.append(new_demoted)
        swap_log.append({
            "demoted_candidate_id": demoted.candidate_id,
            "demoted_role_key": cluster_key,
            "promoted_candidate_id": fresh_swap.candidate_id,
            "promoted_role_key": _role_key(fresh_swap),
        })

    # Renumber selection_rank by score-equivalent stable order: keep
    # original ranks where possible, append swapped-in candidates with
    # next available ranks.
    selected_renumbered: list[CandidateDecision] = []
    used_ranks: set[int] = set()
    for d in sorted(
        selected,
        key=lambda x: (x.selection_rank if x.selection_rank else 9999, x.candidate_id),
    ):
        if d.selection_rank and d.selection_rank not in used_ranks:
            selected_renumbered.append(d)
            used_ranks.add(d.selection_rank)
        else:
            next_rank = 1
            while next_rank in used_ranks:
                next_rank += 1
            new_d = d.model_copy(update={"selection_rank": next_rank})
            selected_renumbered.append(new_d)
            used_ranks.add(next_rank)

    new_decisions = selected_renumbered + other_rejected + cap_rejected
    return new_decisions, swap_log
