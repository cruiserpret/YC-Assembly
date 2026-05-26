"""Phase 12C — 4-round influence loop over the lightweight voter graph.

Pure Python. Zero LLM calls. Deterministic given (voters, edges,
cluster_arguments, simulation_seed).

  Round 0 (init):       voter.initial_intent is set during sampling
                        via the existing infer_simulated_intent
                        cascade. This module records the round but
                        does no work.
  Round 1 (receive):    voters collect signals from their neighbors
                        (intent + edge weight + edge_type) and from
                        their parent cohort's discussion (top
                        objection / proof_need from rich personas).
                        Intent does NOT change in round 1.
  Round 2 (update):     bounded ±1 step movement along INTENT_ORDER,
                        gated by switching_resistance and dominant
                        pull. social_influence_susceptibility (psy)
                        amplifies movement.
  Round 3 (finalize):   final_intent stamped; map to final_bucket
                        via the existing market_buckets table.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any

from assembly.calibration.market_buckets import (
    map_assembly_intent_to_market_bucket,
)
from assembly.sources.lightweight_voters.voter_schema import (
    INTENT_ORDER,
    InfluenceRound,
    InfluenceSignal,
    LightweightVoter,
    SocialEdge,
)


def _stable_seed(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _index_intent(intent: str) -> int:
    """Return the position on INTENT_ORDER for an intent label.
    Labels not in INTENT_ORDER (e.g., would_block) land near the
    skeptical end of the scale."""
    if intent in INTENT_ORDER:
        return INTENT_ORDER.index(intent)
    # Fall back: treat unknowns as most-skeptical neighbor.
    if intent == "would_block":
        return 0  # alias to would_reject
    # Unknown — middle of scale.
    return len(INTENT_ORDER) // 2


def _dominant_pull(
    incoming: list[InfluenceSignal],
) -> tuple[str | None, float, dict[str, float]]:
    """From a voter's incoming signals, compute the dominant
    intent (the intent neighbors are pulling toward) and its
    strength fraction. Returns (dominant_intent, strength, pull_map).
    """
    if not incoming:
        return None, 0.0, {}
    pull: dict[str, float] = {}
    for sig in incoming:
        if not sig.peer_intent:
            continue
        pull[sig.peer_intent] = (
            pull.get(sig.peer_intent, 0.0) + sig.edge_weight
        )
    total = sum(pull.values())
    if total <= 0:
        return None, 0.0, {}
    dominant = max(pull, key=pull.get)
    return dominant, pull[dominant] / total, pull


def run_influence_rounds(
    voters: list[LightweightVoter],
    edges: list[SocialEdge],
    *,
    simulation_seed: int,
    cluster_arguments: dict[str, dict[str, Any]] | None = None,
) -> list[InfluenceRound]:
    """Run all 4 rounds; mutate voters in place; return the audit
    list (one InfluenceRound per round).

    `cluster_arguments` (optional): dict keyed by segment with the
    top objection + top proof_need string from the rich-persona
    discussion. Voters incorporate this as a structured signal in
    Round 1. Missing keys default to whatever the voter sampled
    individually.
    """
    by_vid = {str(v.voter_id): v for v in voters}
    cluster_args = cluster_arguments or {}

    def _bucket_counts(get_bucket) -> dict[str, int]:  # noqa: ANN001
        counts = {"buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 0}
        for vt in voters:
            b = get_bucket(vt)
            if b in counts:
                counts[b] += 1
        return counts

    def _initial_bucket(vt: LightweightVoter) -> str:
        return vt.initial_bucket or (
            map_assembly_intent_to_market_bucket(vt.initial_intent)[0]
        )

    def _current_round_bucket(vt: LightweightVoter) -> str:
        """Bucket implied by whichever intent the voter currently
        holds (final if set, otherwise initial)."""
        intent = vt.final_intent or vt.initial_intent
        b, _ = map_assembly_intent_to_market_bucket(intent)
        return b

    def _skeptic_transitions(get_bucket) -> dict[str, int]:  # noqa: ANN001
        out = {
            "skeptical_to_skeptical": 0,
            "skeptical_to_uncertain": 0,
            "skeptical_to_receptive": 0,
            "skeptical_to_buyer": 0,
        }
        for vt in voters:
            if _initial_bucket(vt) != "skeptical":
                continue
            out[f"skeptical_to_{get_bucket(vt)}"] += 1
        return out

    rounds: list[InfluenceRound] = []

    # --------- Round 0 ---------
    # All voters at their initial bucket; no movement yet.
    rounds.append(InfluenceRound(
        round_idx=0,
        round_type="init",
        voters_affected=len(voters),
        intent_changes=0,
        bucket_changes=0,
        per_voter_log=[],
        notes="initial_intent set during voter sampling",
        bucket_distribution=_bucket_counts(_initial_bucket),
        skeptic_transitions=_skeptic_transitions(_initial_bucket),
    ))

    # --------- Round 1: collect signals ---------
    # For each edge, record the SOURCE's intent as a signal on the
    # TARGET. This is "B is influenced by A" semantics — the edge
    # source pushes toward the target.
    # We do NOT change intent in this round.
    for e in edges:
        src = by_vid.get(str(e.source_voter_id))
        tgt = by_vid.get(str(e.target_voter_id))
        if src is None or tgt is None:
            continue
        tgt.influence_received.append(InfluenceSignal(
            peer_voter_id=str(src.voter_id),
            edge_type=e.edge_type,
            edge_weight=e.weight,
            peer_intent=src.initial_intent,
            peer_segment=src.segment,
        ))
    # Augment each voter's record with their cluster arguments. This
    # is structured-text, not a full LLM call. Stored under
    # `evidence_basis` for audit.
    for v in voters:
        c = cluster_args.get(v.segment) or {}
        if c:
            v.evidence_basis = (
                f"{v.evidence_basis} | "
                f"cluster_top_objection={c.get('top_objection','')[:60]} | "
                f"cluster_top_proof_need={c.get('top_proof_need','')[:60]}"
            )
    rounds.append(InfluenceRound(
        round_idx=1,
        round_type="receive",
        voters_affected=len(voters),
        intent_changes=0,
        bucket_changes=0,
        per_voter_log=[
            {
                "voter_id": str(v.voter_id),
                "n_signals": len(v.influence_received),
            }
            for v in voters
        ],
        # No movement yet — bucket distribution same as round 0.
        bucket_distribution=_bucket_counts(_initial_bucket),
        skeptic_transitions=_skeptic_transitions(_initial_bucket),
    ))

    # --------- Round 2: update intent ---------
    intent_changes = 0
    constrained_count = 0
    per_voter_log: list[dict[str, Any]] = []
    for v in voters:
        rng = random.Random(_stable_seed(
            str(simulation_seed), "round2", str(v.voter_id),
        ))
        dominant, strength, pull_map = _dominant_pull(
            v.influence_received,
        )
        if not dominant:
            v.final_intent = v.initial_intent
            v.vote_confidence = "medium"
            per_voter_log.append({
                "voter_id": str(v.voter_id),
                "initial": v.initial_intent,
                "final": v.final_intent,
                "moved": False,
                "reason": "no_dominant_pull",
                "hard_resistant": v.hard_resistant,
            })
            continue

        # Movement probability: how strong is the pull vs how
        # resistant the voter is to switching. Phase 12C.1 — apply an
        # extra damping factor for hard_resistant voters so a strong
        # peer pull alone cannot soften them — they need a proof
        # satisfaction signal which only enters via the cluster
        # arguments (handled implicitly: their cohort's proof need is
        # baked into the cluster_arguments record, not into the
        # numeric pull).
        hr_damping = 0.4 if v.hard_resistant else 1.0
        movement_p = (
            (1.0 - v.switching_resistance) * strength * 1.2 * hr_damping
        )
        movement_p = max(0.0, min(1.0, movement_p))

        moved = False
        constrained_reason: str | None = None
        if rng.random() < movement_p:
            cur_idx = _index_intent(v.initial_intent)
            dom_idx = _index_intent(dominant)
            if dom_idx == cur_idx:
                step = 0
            else:
                step = 1 if dom_idx > cur_idx else -1
            new_idx = max(
                0, min(len(INTENT_ORDER) - 1, cur_idx + step),
            )

            # Phase 12C.1 — bucket-crossing constraints.
            # A voter starting in the `skeptical` bucket may only move
            # to `uncertain` (not directly to `receptive` or `buyer`)
            # in a single influence round. This caps over-conversion
            # while still permitting realistic softening.
            proposed_intent = INTENT_ORDER[new_idx]
            proposed_bucket, _ = map_assembly_intent_to_market_bucket(
                proposed_intent,
            )
            initial_bucket = v.initial_bucket or (
                map_assembly_intent_to_market_bucket(v.initial_intent)[0]
            )

            # Constraint: the ±1 step rule means any single move is at
            # most one position on INTENT_ORDER. If a constraint
            # fires, the voter stays put for this round (no
            # multi-step jumps allowed — that would re-introduce the
            # erasure pattern from a different angle).
            #
            # Phase 12C.1 (extended) — *Resistant softening guard.*
            # Hard-resistant voters (loyal_to_current_alternative,
            # would_reject, would_block, plus role-/psy-based
            # blockers) must NOT move to ANY non-skeptical bucket in a
            # single round unless we have explicit evidence their
            # proof need was satisfied. In MVP we have no proof-
            # satisfaction signal, so they stay put. This is the fix
            # for the "everyone becomes uncertain by the end" failure
            # mode: previously a hard-resistant voter could soften from
            # `loyal_to_current_alternative` (skeptical, idx 1) to
            # `wait_and_see` (uncertain, idx 2) via a single +1 step.
            if v.hard_resistant and proposed_bucket != "skeptical":
                new_idx = cur_idx
                constrained_reason = (
                    "hard_resistant_softening_disallowed"
                )
                constrained_count += 1
            elif (
                initial_bucket == "skeptical"
                and proposed_bucket in ("receptive", "buyer")
            ):
                # Soft skeptics (non-hard) can soften to uncertain in
                # one round, but never to receptive or buyer.
                new_idx = cur_idx
                constrained_reason = (
                    "skeptic_to_receptive_or_buyer_disallowed"
                )
                constrained_count += 1
            elif (
                initial_bucket == "skeptical"
                and proposed_bucket == "buyer"
            ):
                new_idx = cur_idx
                constrained_reason = "skeptic_to_buyer_disallowed"
                constrained_count += 1

            v.final_intent = INTENT_ORDER[new_idx]
            moved = v.final_intent != v.initial_intent
            if moved:
                intent_changes += 1
        else:
            v.final_intent = v.initial_intent

        # Confidence: high if dominant pull > 0.6 AND low resistance.
        if strength > 0.6 and v.switching_resistance < 0.4:
            v.vote_confidence = "high"
        elif strength > 0.3:
            v.vote_confidence = "medium"
        else:
            v.vote_confidence = "low"

        per_voter_log.append({
            "voter_id": str(v.voter_id),
            "initial": v.initial_intent,
            "final": v.final_intent,
            "dominant_pull": dominant,
            "dominant_strength": round(strength, 3),
            "movement_probability": round(movement_p, 3),
            "moved": moved,
            "switching_resistance": round(v.switching_resistance, 3),
            "vote_confidence": v.vote_confidence,
            "hard_resistant": v.hard_resistant,
            "constrained_reason": constrained_reason,
        })

    rounds.append(InfluenceRound(
        round_idx=2,
        round_type="update",
        voters_affected=len(voters),
        intent_changes=intent_changes,
        bucket_changes=0,
        per_voter_log=per_voter_log,
        notes=(
            f"movement_constrained={constrained_count} "
            f"(hard_resistant or skeptic-bucket clamps)"
            if constrained_count > 0 else None
        ),
        # Round 2 has assigned final_intent but final_bucket is still
        # computed lazily; use the intent-derived bucket here.
        bucket_distribution=_bucket_counts(_current_round_bucket),
        skeptic_transitions=_skeptic_transitions(_current_round_bucket),
    ))

    # --------- Round 3: finalize bucket ---------
    bucket_changes = 0
    per_voter_log_3: list[dict[str, Any]] = []
    for v in voters:
        # Final_intent may already be set (round 2). If not (e.g., we
        # bypassed round 2 in tests), default to initial.
        if not v.final_intent:
            v.final_intent = v.initial_intent
        try:
            bucket, _label = map_assembly_intent_to_market_bucket(
                v.final_intent,
            )
        except Exception:
            bucket = "uncertain"
        # Compare against the bucket that initial_intent would have
        # mapped to:
        try:
            initial_bucket, _ = map_assembly_intent_to_market_bucket(
                v.initial_intent,
            )
        except Exception:
            initial_bucket = "uncertain"
        if bucket != initial_bucket:
            bucket_changes += 1
        v.final_bucket = bucket
        per_voter_log_3.append({
            "voter_id": str(v.voter_id),
            "final_intent": v.final_intent,
            "final_bucket": bucket,
            "initial_bucket": initial_bucket,
            "bucket_changed": bucket != initial_bucket,
        })
    def _final_bucket(vt: LightweightVoter) -> str:
        return vt.final_bucket or _current_round_bucket(vt)

    rounds.append(InfluenceRound(
        round_idx=3,
        round_type="finalize",
        voters_affected=len(voters),
        # Round 3 itself does NOT change intents — it only assigns
        # final_bucket from final_intent. Earlier versions of this
        # code re-stamped round 2's intent_changes onto round 3,
        # which double-attributed the count in audit reports.
        intent_changes=0,
        bucket_changes=bucket_changes,
        per_voter_log=per_voter_log_3,
        bucket_distribution=_bucket_counts(_final_bucket),
        skeptic_transitions=_skeptic_transitions(_final_bucket),
    ))

    return rounds
