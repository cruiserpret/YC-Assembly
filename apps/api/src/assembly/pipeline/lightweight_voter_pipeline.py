"""Phase 12C — lightweight voter overlay pipeline integration.

Called as a new stage AFTER `inferring_simulated_intent`. Loads
SocietyCohort centroids + rich-persona ballots from DB, runs the
voter overlay end-to-end, and writes 8 new audit artifacts to the
run dir.

FAILURE-TOLERANT BY DESIGN: any exception inside this stage is
caught, logged, and a `voter_overlay_failed.json` placeholder is
written. The existing 24-rich pipeline output is NEVER mutated.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from assembly.calibration.market_buckets import (
    map_assembly_intent_to_market_bucket,
    pick_market_bucket,
)
from assembly.sources.intent_layer.inference import (
    is_intent_signal_routing_enabled,
)
from assembly.sources.lightweight_voters import (
    aggregate_voter_distribution,
    build_social_graph,
    calibrated_distribution,
    compute_diversity_health,
    generate_voters_from_cohorts,
    run_influence_rounds,
)

logger = logging.getLogger(__name__)

_VOTER_COUNT = 100


def _serialize(obj: Any) -> Any:
    """JSON-safe serializer for UUIDs, datetimes, Pydantic models."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    return obj


def _derive_cluster_arguments_from_ctx(
    cohort_dicts: list[dict[str, Any]],
    ballots: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, str]]:
    """Derive per-cohort top objection + top proof_need from the
    rich-persona ballots already loaded in ctx. No DB call needed —
    the orchestrator already pulled these into memory."""
    # Bucket ballots by cohort via member_persona_ids
    persona_to_cohort: dict[str, str] = {}
    for c in cohort_dicts:
        for pid in c.get("member_persona_ids", []):
            persona_to_cohort[str(pid)] = c["cohort_label"]
    by_cohort_obj: dict[str, Counter] = {}
    by_cohort_proof: dict[str, Counter] = {}
    # ballots: {stage_label: [ballot_dict, ...]}. We aggregate
    # ALL stages (pre + final + reflection) for the per-cohort top.
    all_ballots: list[dict[str, Any]] = []
    for stage_list in ballots.values():
        all_ballots.extend(stage_list or [])
    for b in all_ballots:
        pid = b.get("persona_id")
        if not pid:
            continue
        seg = persona_to_cohort.get(str(pid))
        if not seg:
            continue
        obj = b.get("top_objection")
        pn = b.get("top_proof_need")
        if obj:
            by_cohort_obj.setdefault(seg, Counter())[
                str(obj)[:120]
            ] += 1
        if pn:
            by_cohort_proof.setdefault(seg, Counter())[
                str(pn)[:120]
            ] += 1
    out: dict[str, dict[str, str]] = {}
    for seg in {c["cohort_label"] for c in cohort_dicts}:
        obj_c = by_cohort_obj.get(seg, Counter())
        proof_c = by_cohort_proof.get(seg, Counter())
        out[seg] = {
            "top_objection": (
                obj_c.most_common(1)[0][0] if obj_c else ""
            ),
            "top_proof_need": (
                proof_c.most_common(1)[0][0] if proof_c else ""
            ),
        }
    return out


def _build_rich_distribution(
    run_dir: Path,
) -> dict[str, float]:
    """Read the existing 24-rich intent_distribution and convert to
    bucket %. Falls back to zeros if the artifact is missing.

    Phase 12A.10D — when `intent_signal_distribution` is present in
    the simulated_intent artifact AND
    ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED is true, the new
    intent_signal -> bucket mapping is preferred. Otherwise the
    legacy intent_label -> bucket mapping is used (preserves
    pre-12A.10D behavior by default).
    """
    try:
        data = json.loads(
            (run_dir / "simulated_intent.json").read_text()
        )
        intents: dict[str, int] = data.get("intent_distribution") or {}
        signal_dist: dict[str, int] = (
            data.get("intent_signal_distribution") or {}
        )
        bucket_counts: dict[str, int] = {
            "buyer": 0, "receptive": 0,
            "uncertain": 0, "skeptical": 0,
        }
        total = 0
        # If both signal-distribution and routing-flag are present,
        # use intent_signal -> bucket mapping. Otherwise fall back to
        # the legacy path.
        if signal_dist and is_intent_signal_routing_enabled():
            for sig, count in signal_dist.items():
                b, _ = pick_market_bucket(
                    intent_signal=sig, intent_label=None,
                    intent_signal_routing_enabled=True,
                )
                bucket_counts[b] += int(count)
                total += int(count)
        else:
            for label, count in intents.items():
                try:
                    b, _ = map_assembly_intent_to_market_bucket(label)
                except Exception:
                    continue
                bucket_counts[b] += int(count)
                total += int(count)
        if total <= 0:
            return {b: 0.0 for b in bucket_counts}
        return {b: 100.0 * n / total for b, n in bucket_counts.items()}
    except Exception:
        return {"buyer": 0.0, "receptive": 0.0,
                "uncertain": 0.0, "skeptical": 0.0}


def _build_representative_debates(
    *,
    cohort_dicts: list[dict[str, Any]],
    ballots_by_stage: dict[str, list[dict[str, Any]]],
    max_samples: int = 6,
) -> dict[str, Any]:
    """Pick one representative final-ballot snippet per cohort (up to
    `max_samples`) from the rich-persona ballots already loaded in ctx.

    Phase 12C.1 fix: previous version returned an empty list because
    it tried to source data from run_dir alone (the ballot text is not
    persisted there — only on the DB / in ctx). This version pulls
    from the ctx ballots already passed into the pipeline so the
    operator can see WHY each cluster landed where it did, without
    issuing a single new LLM call or DB query.
    """
    out: dict[str, Any] = {
        "phase": "12c_representative_debates",
        "samples": [],
        "notes": (
            "sampled from existing rich-persona final ballots; "
            "no new LLM"
        ),
        "completed_at": datetime.now(UTC).isoformat(),
    }
    persona_to_cohort: dict[str, str] = {}
    for c in cohort_dicts or []:
        for pid in c.get("member_persona_ids", []) or []:
            persona_to_cohort[str(pid)] = c.get("cohort_label") or str(
                c.get("cohort_id") or "unknown",
            )
    final_ballots = list(
        (ballots_by_stage or {}).get("final") or []
    )
    # Group ballots by cohort label
    by_cohort: dict[str, list[dict[str, Any]]] = {}
    for b in final_ballots:
        pid = str(b.get("persona_id") or "")
        if not pid:
            continue
        clabel = persona_to_cohort.get(pid)
        if not clabel:
            continue
        by_cohort.setdefault(clabel, []).append(b)

    # For each cohort, pick the ballot with the most informative text
    # (longest of private_reasoning, top_objection, top_proof_need).
    def _info_len(b: dict[str, Any]) -> int:
        return sum(
            len(str(b.get(k) or ""))
            for k in (
                "private_reasoning",
                "private_stance",
                "top_objection",
                "top_proof_need",
            )
        )

    sorted_cohorts = sorted(
        by_cohort.keys(),
        key=lambda c: -max(
            (_info_len(b) for b in by_cohort[c]), default=0,
        ),
    )
    samples: list[dict[str, Any]] = []
    for clabel in sorted_cohorts[:max_samples]:
        candidates = by_cohort[clabel]
        if not candidates:
            continue
        best = max(candidates, key=_info_len)
        samples.append({
            "cohort_label": clabel,
            "persona_id": str(best.get("persona_id") or ""),
            "private_stance": best.get("private_stance"),
            "top_objection": (
                (best.get("top_objection") or "")[:240]
            ),
            "top_proof_need": (
                (best.get("top_proof_need") or "")[:240]
            ),
            "private_reasoning_excerpt": (
                (best.get("private_reasoning") or "")[:480]
            ),
        })
    out["samples"] = samples
    return out


def run_lightweight_voter_overlay(
    *,
    run_id: uuid.UUID,
    run_dir: Path,
    run_scope_id: str,
    cohort_dicts: list[dict[str, Any]],
    ballots_by_stage: dict[str, list[dict[str, Any]]] | None,
    simulation_seed: int | None,
    category_hint: str | None = None,
    evidence_quality: float = 1.0,
) -> dict[str, Any]:
    """Run the full 100-voter overlay pipeline.

    Inputs come from the orchestrator's ctx (no DB queries here):
      cohort_dicts:       ctx['cohort_dicts'] — the 9 SocietyCohort
                          centroids in dict form (see _stage_building_cohorts)
      ballots_by_stage:   ctx['pre_dicts'] / ctx['final_dicts'] /
                          ctx['refl_dicts'] — the rich-persona ballot text
      run_scope_id:       ctx['live_run_scope_id']
      simulation_seed:    ctx['simulation_seed'] (Phase 12A.10F)

    Returns a summary dict for the orchestrator. NEVER raises — all
    exceptions are caught and a `voter_overlay_failed.json` is
    written instead. The orchestrator continues regardless.
    """
    started_at = datetime.now(UTC)
    seed = int(simulation_seed) if simulation_seed is not None else 0
    cohorts = cohort_dicts or []
    ballots = ballots_by_stage or {}

    try:
        if not cohorts:
            raise ValueError("no cohort_dicts supplied")

        # Derive cluster arguments from the rich-persona ballots
        # already loaded in ctx (no DB query).
        cluster_args = _derive_cluster_arguments_from_ctx(
            cohorts, ballots,
        )

        # 3. Generate voters
        voters, sampling_warnings = generate_voters_from_cohorts(
            cohorts,
            run_scope_id=run_scope_id,
            simulation_seed=seed,
            n=_VOTER_COUNT,
        )

        # 4. Build social graph
        edges, graph_warnings = build_social_graph(
            voters, simulation_seed=seed,
        )

        # 5. Run 4-round influence loop
        rounds = run_influence_rounds(
            voters, edges,
            simulation_seed=seed,
            cluster_arguments=cluster_args,
        )

        # 6. Aggregate voter distribution
        voter_dist = aggregate_voter_distribution(voters)

        # 7. Build rich distribution + calibrated distribution
        raw_24 = _build_rich_distribution(run_dir)
        cal = calibrated_distribution(
            raw_24, voter_dist,
            category=category_hint,
            evidence_quality=evidence_quality,
        )

        # 8. Diversity health (reads existing rich-persona diversity
        # artifact if present)
        rich_diversity: dict[str, Any] | None = None
        try:
            rich_diversity = json.loads(
                (run_dir / "discussion_diversity_quality.json")
                .read_text()
            )
        except Exception:
            pass
        diversity = compute_diversity_health(
            voters, edges, rounds,
            rich_persona_diversity=rich_diversity,
        )

        # 9. Write all 8 artifacts
        (run_dir / "rich_persona_distribution.json").write_text(
            json.dumps({
                "phase": "12c_rich_persona_distribution",
                "distribution_percent": raw_24,
                "source": "simulated_intent.json",
                "completed_at": datetime.now(UTC).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )
        (run_dir / "lightweight_voters.json").write_text(
            json.dumps({
                "phase": "12c_lightweight_voters",
                "n_voters": len(voters),
                "simulation_seed": seed,
                "voters": [v.model_dump(mode="json") for v in voters],
                "sampling_warnings": sampling_warnings,
                "completed_at": datetime.now(UTC).isoformat(),
            }, indent=2, default=str),
            encoding="utf-8",
        )
        (run_dir / "social_graph_nodes_edges.json").write_text(
            json.dumps({
                "phase": "12c_social_graph",
                "n_nodes": len(voters),
                "n_edges": len(edges),
                "edges": [e.model_dump(mode="json") for e in edges],
                "graph_warnings": graph_warnings,
                "completed_at": datetime.now(UTC).isoformat(),
            }, indent=2, default=str),
            encoding="utf-8",
        )
        (run_dir / "influence_rounds.json").write_text(
            json.dumps({
                "phase": "12c_influence_rounds",
                "rounds": [r.model_dump(mode="json") for r in rounds],
                "cluster_arguments": cluster_args,
                "completed_at": datetime.now(UTC).isoformat(),
            }, indent=2, default=str),
            encoding="utf-8",
        )
        (run_dir / "final_100_voter_distribution.json").write_text(
            json.dumps({
                "phase": "12c_final_voter_distribution",
                "lightweight_voter_distribution": (
                    voter_dist.model_dump(mode="json")
                ),
                "calibrated_distribution": (
                    cal.model_dump(mode="json")
                ),
                "raw_24_distribution_percent": raw_24,
                "completed_at": datetime.now(UTC).isoformat(),
            }, indent=2, default=str),
            encoding="utf-8",
        )
        (run_dir / "diversity_health.json").write_text(
            json.dumps({
                "phase": "12c_diversity_health",
                **diversity.model_dump(mode="json"),
                "completed_at": datetime.now(UTC).isoformat(),
            }, indent=2, default=str),
            encoding="utf-8",
        )
        (run_dir / "representative_debates.json").write_text(
            json.dumps(
                _build_representative_debates(
                    cohort_dicts=cohorts,
                    ballots_by_stage=ballots,
                ),
                indent=2, default=str,
            ),
            encoding="utf-8",
        )
        # phase_12c_summary.md — human-readable headline
        def _fmt_rate(r: float | None) -> str:
            return "n/a" if r is None else f"{r:.2f}"

        tm = diversity.transition_matrix or {}
        bucket_order = ("skeptical", "uncertain", "receptive", "buyer")
        transition_rows = "\n".join(
            f"| {init} | " + " | ".join(
                str(tm.get(init, {}).get(fin, 0)) for fin in bucket_order
            ) + " |"
            for init in bucket_order
        )
        summary_md = (
            f"# Phase 12C — 100-voter market graph (MVP)\n\n"
            f"- run_scope_id: `{run_scope_id}`\n"
            f"- simulation_seed: `{seed}`\n"
            f"- voters: {len(voters)}\n"
            f"- edges: {len(edges)}\n"
            f"- rounds: {len(rounds)}\n"
            f"- intent_changes (round 2): "
            f"{rounds[2].intent_changes}\n"
            f"- bucket_changes (round 3): "
            f"{rounds[3].bucket_changes}\n"
            f"- hard_resistant_count: "
            f"{diversity.hard_resistant_count}\n\n"
            f"## Distributions\n\n"
            f"| Bucket | 24-rich | 100-voter | calibrated |\n"
            f"|---|---:|---:|---:|\n"
            + "\n".join(
                f"| {b} | {raw_24[b]:.2f} | "
                f"{getattr(voter_dist, b):.2f} | "
                f"{cal.distribution_percent[b]:.2f} |"
                for b in ("buyer", "receptive", "uncertain", "skeptical")
            )
            + "\n\n"
            f"## Transition matrix (initial bucket → final bucket)\n\n"
            f"| ↓ initial / final → | skeptical | uncertain | "
            f"receptive | buyer |\n"
            f"|---|---:|---:|---:|---:|\n"
            f"{transition_rows}\n\n"
            f"## Resistance realism\n\n"
            f"**Bucket-level (load-bearing realism gates):**\n\n"
            f"- skeptic_retention_rate: "
            f"{_fmt_rate(diversity.skeptic_retention_rate)}\n"
            f"- hard_reject_bucket_retention_rate: "
            f"{_fmt_rate(diversity.hard_reject_bucket_retention_rate)}\n"
            f"- competitor_loyal_retention_rate: "
            f"{_fmt_rate(diversity.competitor_loyal_retention_rate)}\n"
            f"- hard_resistant_bucket_retention_rate: "
            f"{_fmt_rate(diversity.hard_resistant_bucket_retention_rate)}\n\n"
            f"**Exact-intent (diagnostic only; within-skeptical "
            f"micro-shifts are legitimate market behavior):**\n\n"
            f"- hard_reject_exact_intent_retention_rate: "
            f"{_fmt_rate(diversity.hard_reject_exact_intent_retention_rate)}\n"
            f"- hard_resistant_exact_intent_retention_rate: "
            f"{_fmt_rate(diversity.hard_resistant_exact_intent_retention_rate)}\n"
            f"- within_skeptical_intent_shift_count: "
            f"{diversity.within_skeptical_intent_shift_count}\n"
            f"- skeptic_to_uncertain_rate: "
            f"{_fmt_rate(diversity.skeptic_to_uncertain_rate)}\n"
            f"- skeptic_to_receptive_rate: "
            f"{_fmt_rate(diversity.skeptic_to_receptive_rate)}\n"
            f"- skeptic_to_buyer_rate: "
            f"{_fmt_rate(diversity.skeptic_to_buyer_rate)}\n"
            f"- hard_resistant_to_uncertain_rate: "
            f"{_fmt_rate(diversity.hard_resistant_to_uncertain_rate)}\n"
            f"- hard_resistant_to_receptive_rate: "
            f"{_fmt_rate(diversity.hard_resistant_to_receptive_rate)}\n"
            f"- hard_resistant_to_buyer_rate: "
            f"{_fmt_rate(diversity.hard_resistant_to_buyer_rate)}\n"
            + (
                f"- within_skeptical_intent_shift_examples (first "
                f"{len(diversity.within_skeptical_intent_shift_examples)}):\n"
                + "".join(
                    f"  - {ex['from_intent']} → {ex['to_intent']}\n"
                    for ex in diversity.within_skeptical_intent_shift_examples
                )
                if diversity.within_skeptical_intent_shift_examples
                else ""
            )
            + "\n"
            f"## Per-round bucket distribution\n\n"
            f"| round | type | buyer | receptive | uncertain | "
            f"skeptical | sk→sk | sk→un | sk→re | sk→bu |\n"
            f"|---|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
            + "\n".join(
                f"| {r.round_idx} | {r.round_type} | "
                f"{r.bucket_distribution.get('buyer', 0)} | "
                f"{r.bucket_distribution.get('receptive', 0)} | "
                f"{r.bucket_distribution.get('uncertain', 0)} | "
                f"{r.bucket_distribution.get('skeptical', 0)} | "
                f"{r.skeptic_transitions.get('skeptical_to_skeptical', 0)} | "
                f"{r.skeptic_transitions.get('skeptical_to_uncertain', 0)} | "
                f"{r.skeptic_transitions.get('skeptical_to_receptive', 0)} | "
                f"{r.skeptic_transitions.get('skeptical_to_buyer', 0)} |"
                for r in rounds
            )
            + "\n\n"
            f"## Diversity gates\n\n"
            f"- all_gates_passed: **{diversity.all_gates_passed}**\n"
            f"- warnings: {diversity.warnings or 'none'}\n\n"
            f"## Calibration warnings\n\n"
            f"- {', '.join(cal.calibration_warnings) or 'none'}\n\n"
            f"**Important:** calibrated_distribution is "
            f"EXPERIMENTAL/INTERNAL. It is NOT a validated prediction. "
            f"The 24-rich raw distribution is the primary source of "
            f"truth until calibration support strengthens "
            f"(>= 3 prior scored cases per category).\n"
        )
        (run_dir / "phase_12c_summary.md").write_text(
            summary_md, encoding="utf-8",
        )

        finished_at = datetime.now(UTC)
        return {
            "status": "complete",
            "n_voters": len(voters),
            "n_edges": len(edges),
            "diversity_gates_passed": diversity.all_gates_passed,
            "intent_changes": rounds[2].intent_changes,
            "bucket_changes": rounds[3].bucket_changes,
            "runtime_seconds": (
                finished_at - started_at
            ).total_seconds(),
        }

    except Exception as exc:  # noqa: BLE001 — failure-tolerant by design
        logger.warning(
            "phase_12c_voter_overlay_failed run_id=%s err=%s: %s",
            run_id, type(exc).__name__, str(exc)[:240],
        )
        try:
            (run_dir / "voter_overlay_failed.json").write_text(
                json.dumps({
                    "phase": "12c_failed",
                    "error_type": type(exc).__name__,
                    "error_msg": str(exc)[:500],
                    "completed_at": datetime.now(UTC).isoformat(),
                }, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            "runtime_seconds": (
                datetime.now(UTC) - started_at
            ).total_seconds(),
        }
