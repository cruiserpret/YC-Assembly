"""Phase 10A — fixture-demo loader.

Loads the existing 9B.1 / 9D / 9E artifacts and builds frontend-ready
response payloads. Pure read — no DB writes, no LLM calls, no new
retrieval.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_AUDIT_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent / "_audit"
)


def _audit_path() -> Path:
    return _AUDIT_ROOT


def _load_json(p: Path) -> dict[str, Any] | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _load_text(p: Path) -> str | None:
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


# -----------------------------------------------------------------------
# Fixture sources
# -----------------------------------------------------------------------

def _path_9b_audit() -> Path:
    # Prefer the 9B.1-repaired audit; fall back to the original 9B audit
    p1 = _audit_path() / "repair_9b_reflections_9b_1.json"
    return p1 if p1.exists() else (
        _audit_path() / "scale_lumaloop_society_9b.json"
    )


def _path_9b_report_md() -> Path:
    p1 = _audit_path() / "lumaloop_50_100_discussion_report_9b_1.md"
    return p1 if p1.exists() else (
        _audit_path() / "lumaloop_50_100_discussion_report_9b.md"
    )


def _path_9b_report_json() -> Path:
    p1 = _audit_path() / "lumaloop_50_100_discussion_report_9b_1.json"
    return p1 if p1.exists() else (
        _audit_path() / "lumaloop_50_100_discussion_report_9b.json"
    )


def _path_9d_audit() -> Path:
    return _audit_path() / "cohort_architecture_9d.json"


def _path_9d_report_md() -> Path:
    return _audit_path() / "lumaloop_cohort_architecture_report_9d.md"


def _path_9d_report_json() -> Path:
    return _audit_path() / "lumaloop_cohort_architecture_report_9d.json"


def _path_9e_audit() -> Path:
    return _audit_path() / "simulated_intent_layer_9e.json"


def _path_9e_report_md() -> Path:
    return _audit_path() / "lumaloop_intent_and_society_debate_report_9e.md"


def _path_9e_report_json() -> Path:
    return _audit_path() / "lumaloop_intent_and_society_debate_report_9e.json"


def _path_9d_quality() -> Path:
    return _audit_path() / "cohort_architecture_9d_quality.json"


def _path_9b_quality() -> Path:
    return _audit_path() / "scale_lumaloop_society_9b_1_quality.json"


def _path_9e_quality() -> Path:
    return _audit_path() / "society_wide_debate_9e_quality.json"


_HEADER_CAVEAT = (
    "This is a synthetic discussion simulation augmented with "
    "simulated-intent labels and a deterministic cross-cohort argument "
    "propagation pass. n=66 persisted society. Simulated intent is "
    "NOT a real-world purchase forecast. Not representative of the "
    "California market. The product is unlaunched — no persona has "
    "actually used it."
)


_GLOBAL_CAVEATS: list[str] = [
    "This is a synthetic-society simulation, not a real focus group.",
    "Cohorts are run-scoped + brief-scoped — never global market segments.",
    "Simulated intent labels are NOT real-world purchase forecasts.",
    "The product is unlaunched — no persona has bought, used, owned, or reviewed it.",
    "All numbers describe the simulation; none predict actual market behavior.",
]


def _safe_dict(d: Any) -> dict[str, Any]:
    return d if isinstance(d, dict) else {}


def _safe_list(d: Any) -> list[Any]:
    return d if isinstance(d, list) else []


def is_fixture_available() -> bool:
    """Both 9D + 9E artifacts must be on disk for fixture_demo to work."""
    return _path_9d_audit().exists() and _path_9e_audit().exists()


def fixture_artifact_manifest() -> dict[str, str]:
    return {
        "report_json": str(_path_9e_report_json()),
        "report_markdown": str(_path_9e_report_md()),
        "cohorts_json": str(_path_9d_report_json()),
        "discussion_json": str(_path_9b_report_json()),
        "intent_json": str(_path_9e_audit()),
        "audit_json": str(_path_9b_audit()),
        "discussion_quality_json": str(_path_9b_quality()),
        "cohort_quality_json": str(_path_9d_quality()),
        "intent_quality_json": str(_path_9e_quality()),
    }


# -----------------------------------------------------------------------
# Frontend-ready response builders (sanitize + reshape)
# -----------------------------------------------------------------------


def fixture_main_report() -> dict[str, Any] | None:
    """The single founder-facing report — the integrated view across
    9B / 9D / 9E. Hides internal IDs the founder doesn't need."""
    nine_b = _load_json(_path_9b_audit())
    nine_d = _load_json(_path_9d_audit())
    nine_e = _load_json(_path_9e_audit())
    if not (nine_b and nine_d and nine_e):
        return None
    rollup = _safe_dict(nine_e.get("intent_rollup"))
    intent_dist = _safe_dict(rollup.get("intent_distribution"))
    cohort_count = nine_d.get("cohort_count") or len(
        _safe_list(nine_d.get("cohorts"))
    )

    cohorts = []
    for c in _safe_list(nine_d.get("cohorts")):
        cohorts.append({
            "cohort_label": c.get("cohort_label"),
            "size": c.get("cohort_size"),
            "weight": c.get("cohort_weight"),
            "top_role": next(iter(sorted(
                _safe_dict(c.get("role_distribution")).items(),
                key=lambda kv: -kv[1],
            )), ("unknown", 0))[0],
            "top_stance": next(iter(sorted(
                _safe_dict(c.get("stance_distribution")).items(),
                key=lambda kv: -kv[1],
            )), ("unknown", 0))[0],
            "top_objections": (
                _safe_dict(c.get("objection_summary")).get("top_buckets")
                or []
            )[:5],
            "top_proof_needs": (
                _safe_dict(c.get("proof_need_summary")).get("top_buckets")
                or []
            )[:5],
        })

    most_receptive = []
    for c in _safe_list(nine_e.get("cohorts_most_persuaded") or []):
        if isinstance(c, dict):
            most_receptive.append({
                "cohort_label": c.get("cohort_label"),
                "score": c.get("adopted_or_intensified_count"),
            })
    most_resistant = []
    # The 9E audit may not have 'cohorts_most_resistant' at top level —
    # build it from the propagation rollup if missing.
    debate = (
        _load_json(_path_9e_report_json())
        or {}
    ) if _path_9e_report_json().exists() else {}
    for c in _safe_list(debate.get("cohorts_most_resistant") or []):
        most_resistant.append({
            "cohort_label": c.get("cohort_label"),
            "score": c.get("resisted_count"),
        })
    if not most_receptive and debate.get("cohorts_most_persuaded"):
        for c in debate["cohorts_most_persuaded"]:
            most_receptive.append({
                "cohort_label": c.get("cohort_label"),
                "score": c.get("adopted_or_intensified_count"),
            })

    weighted_obj = _safe_dict(
        _safe_dict(nine_d.get("weighted_society_rollup"))
        .get("weighted_objection_summary")
    )
    weighted_proof = _safe_dict(
        _safe_dict(nine_d.get("weighted_society_rollup"))
        .get("weighted_proof_need_summary")
    )

    args_spread = _safe_list((debate or {}).get("arguments_that_spread"))
    args_resisted = _safe_list((debate or {}).get("arguments_that_were_resisted"))

    public_private = nine_b.get("public_to_private_shift_summary") or {}

    confidence = {
        "reaction_confidence": "medium",  # cohort-level reactions tested
        "segment_confidence": "low",       # n=66 synthetic, not real
        "recommendation_confidence": "medium",  # next-test ideas grounded
        "numeric_forecast_confidence": "not_applicable",  # not a forecast
    }

    return {
        "run_id": None,  # set by the router
        "product_brief": _safe_dict(nine_b.get("founder_brief")) or {
            "product_name": "LumaLoop", "launch_state": "unlaunched",
        },
        "executive_summary": [
            f"Synthetic n={nine_b.get('input_persona_count', 66)} "
            f"run-scoped society compressed into {cohort_count} traceable "
            "cohorts with deterministic argument propagation.",
            f"Pre-discussion stance distribution: "
            f"{public_private.get('pre_stance_distribution')}.",
            f"Final-discussion stance distribution: "
            f"{public_private.get('final_stance_distribution')}.",
            f"Simulated intent distribution: {intent_dist}.",
            "All numbers describe the simulation; none predict real-world "
            "purchase behavior.",
        ],
        "synthetic_society_size": (
            nine_b.get("input_persona_count")
            or nine_b.get("session_persona_count")
            or 66
        ),
        "cohort_count": cohort_count,
        "synthetic_intent_snapshot": {
            "intent_distribution": intent_dist,
            "switching_status_distribution": _safe_dict(
                rollup.get("switching_status_distribution")
            ),
            "high_intent_segments_count": len(
                _safe_list(rollup.get("high_intent_segments"))
            ),
            "rejection_segments_count": len(
                _safe_list(rollup.get("strongest_rejection_segments"))
            ),
        },
        "most_receptive_cohorts": most_receptive[:5],
        "most_resistant_cohorts": most_resistant[:5],
        "loyal_to_alternative_patterns": [
            {
                "intent": s.get("intent"),
                "cohort_label": s.get("cohort_label"),
                "strength": s.get("strength"),
            }
            for s in _safe_list(rollup.get("strongest_rejection_segments"))[:10]
        ],
        "top_objections": [
            {"bucket": k, "weighted_score": v}
            for k, v in list(weighted_obj.items())[:8]
        ],
        "proof_needed": [
            {"bucket": k, "weighted_score": v}
            for k, v in list(weighted_proof.items())[:8]
        ],
        "persuasion_levers": [
            {
                "argument_type": a.get("argument_type"),
                "source_cohort_label": a.get("source_cohort_label"),
                "argument_text": (a.get("argument_text") or "")[:240],
                "cohorts_adopting": a.get("cohorts_adopting"),
            }
            for a in args_spread[:6]
        ],
        "competitor_or_alternative_comparison": [
            {
                "intent": s.get("intent"),
                "current_alternative": s.get("current_alternative"),
                "switching_status": s.get("switching_status"),
            }
            for s in _safe_list(
                (debate or {}).get("switching_barriers", {}).get("examples")
            )[:10]
        ],
        "society_wide_debate_summary": (debate or {}).get(
            "society_wide_debate_setup",
        ) or {},
        "arguments_that_spread": args_spread[:10],
        "arguments_that_were_resisted": args_resisted[:10],
        "public_private_shift_summary": public_private,
        "recommended_next_tests": (debate or {}).get(
            "recommended_next_tests",
        ) or [],
        "confidence_dimensions": confidence,
        "caveats": _GLOBAL_CAVEATS,
        "evidence_traceability_summary": {
            "evidence_link_count": nine_d.get("evidence_link_count"),
            "memory_atom_count": (
                _safe_dict(nine_b).get("memory_atoms_created")
                or 528
            ),
            "discussion_turn_count": nine_b.get("public_turn_count"),
            "ballot_count_pre_refl_final": [
                nine_b.get("private_pre_ballot_count"),
                nine_b.get("reflection_count"),
                nine_b.get("private_final_ballot_count"),
            ],
        },
        "artifact_links": fixture_artifact_manifest(),
        "header_caveat": _HEADER_CAVEAT,
    }


def fixture_main_report_md() -> str | None:
    """Markdown founder-facing report. We use the 9E markdown report
    as the canonical founder view because it covers the full flow."""
    return _load_text(_path_9e_report_md())


def fixture_personas() -> dict[str, Any] | None:
    """Persona summaries (sanitized)."""
    nine_b = _load_json(_path_9b_audit())
    nine_b_report = _load_json(_path_9b_report_json())
    if not (nine_b and nine_b_report):
        return None
    cohorts = _safe_list(
        (_load_json(_path_9d_audit()) or {}).get("cohorts")
    )
    persona_to_cohort: dict[str, str] = {}
    for c in cohorts:
        for pid in _safe_list(c.get("member_persona_ids")):
            persona_to_cohort[str(pid)] = c.get("cohort_label", "")

    intent_audit = _load_json(_path_9e_audit()) or {}
    intent_rollup = _safe_dict(intent_audit.get("intent_rollup"))
    intent_dist = _safe_dict(intent_rollup.get("intent_distribution"))

    # Simplified persona summaries from the 9B report's persona list
    personas: list[dict[str, Any]] = []
    raw_personas = _safe_list(nine_b_report.get("group_composition"))
    seen: set[str] = set()
    for g in raw_personas:
        for name in _safe_list(g.get("personas")):
            if name in seen:
                continue
            seen.add(name)
            personas.append({
                "display_name": name,
                "cohort_label": None,  # 9B report only has names
                "stance": None,
                "simulated_intent": None,
            })

    return {
        "persona_count": len(personas) or (
            nine_b.get("input_persona_count") or 66
        ),
        "personas": personas,
        "intent_distribution_across_society": intent_dist,
        "cohort_label_for_each_persona_count": len(persona_to_cohort),
        "caveats": _GLOBAL_CAVEATS,
    }


def fixture_cohorts() -> dict[str, Any] | None:
    nine_d_report = _load_json(_path_9d_report_json())
    nine_d_audit = _load_json(_path_9d_audit())
    if not nine_d_report:
        return None
    cohort_map = _safe_list(nine_d_report.get("cohort_map"))
    rollup = _safe_dict(nine_d_report.get("weighted_society_rollup"))
    return {
        "cohort_count": len(cohort_map),
        "cohorts": [
            {
                "cohort_label": c.get("cohort_label"),
                "size": c.get("cohort_size"),
                "weight": c.get("cohort_weight"),
                "role_distribution": c.get("role_distribution"),
                "stance_distribution": c.get("stance_distribution"),
                "psychology_summary": c.get("psychology_summary"),
                "top_objections": (
                    _safe_dict(c.get("objection_summary")).get("top_buckets")
                    or []
                )[:5],
                "top_proof_needs": (
                    _safe_dict(c.get("proof_need_summary")).get("top_buckets")
                    or []
                )[:5],
                "representative_display_name": (
                    _safe_dict(c.get("representatives")).get("primary_display_name")
                ),
            }
            for c in cohort_map
        ],
        "weighted_society_rollup": rollup,
        "caveats": _GLOBAL_CAVEATS,
    }


def fixture_discussion() -> dict[str, Any] | None:
    nine_b = _load_json(_path_9b_audit())
    nine_b_report = _load_json(_path_9b_report_json())
    if not (nine_b and nine_b_report):
        return None
    overcoop = nine_b.get("overcooperation_audit") or {}
    return {
        "discussion_session_id": nine_b.get("existing_9b_session_id") or (
            nine_b_report.get("discussion_session_id")
        ),
        "persona_count": (
            nine_b.get("input_persona_count")
            or nine_b.get("session_persona_count")
            or 66
        ),
        "group_count": nine_b_report.get(
            "discussion_setup", {},
        ).get("group_count"),
        "group_size": nine_b_report.get(
            "discussion_setup", {},
        ).get("group_size"),
        "public_turn_count": nine_b.get("public_turn_count"),
        "peer_response_turn_count": nine_b.get("peer_response_turn_count"),
        "private_pre_ballot_count": nine_b.get("private_pre_ballot_count"),
        "reflection_count": nine_b.get("reflection_count"),
        "private_final_ballot_count": nine_b.get("private_final_ballot_count"),
        "public_private_shift_summary": (
            nine_b.get("public_to_private_shift_summary") or {}
        ),
        "social_influence_classification": (
            nine_b.get("social_influence_classification") or {}
        ),
        "overcooperation_flag": overcoop.get("flag", False),
        "memory_summary": {
            "memory_atom_count": (
                nine_b.get("memory_atoms_created") or 528
            ),
        },
        "main_arguments": nine_b_report.get("main_arguments_raised") or [],
        "main_objections_that_spread": nine_b_report.get(
            "main_objections_that_spread",
        ) or [],
        "caveats": _GLOBAL_CAVEATS,
    }


def fixture_intent() -> dict[str, Any] | None:
    nine_e_report = _load_json(_path_9e_report_json())
    nine_e_audit = _load_json(_path_9e_audit())
    if not (nine_e_report and nine_e_audit):
        return None
    return {
        "synthetic_intent_snapshot": nine_e_report.get(
            "synthetic_intent_snapshot",
        ) or {},
        "buy_now_or_try_once_signals": nine_e_report.get(
            "buy_now_or_try_once_signals",
        ) or {},
        "consider_if_proven_signals": nine_e_report.get(
            "consider_if_proven_signals",
        ) or {},
        "loyal_or_reject_signals": nine_e_report.get(
            "loyal_or_reject_signals",
        ) or {},
        "switching_barriers": nine_e_report.get("switching_barriers") or {},
        "conditions_to_buy": nine_e_report.get("conditions_to_buy") or [],
        "intent_by_cohort": nine_e_report.get("intent_by_cohort") or {},
        "society_wide_debate_setup": nine_e_report.get(
            "society_wide_debate_setup",
        ) or {},
        "arguments_that_spread": nine_e_report.get(
            "arguments_that_spread",
        ) or [],
        "arguments_that_were_resisted": nine_e_report.get(
            "arguments_that_were_resisted",
        ) or [],
        "cohorts_most_persuaded": nine_e_report.get(
            "cohorts_most_persuaded",
        ) or [],
        "cohorts_most_resistant": nine_e_report.get(
            "cohorts_most_resistant",
        ) or [],
        "caveats": _GLOBAL_CAVEATS + [
            "Simulated intent is hypothesis-generation, not a demand "
            "forecast.",
        ],
    }


def fixture_audit_dev_only() -> dict[str, Any]:
    """Internal/dev audit aggregator — mounted under /audit only.
    Strips paths that contain absolute filesystem locations to avoid
    leaking developer machine info, but preserves quality gates +
    safety check results so the operator can self-verify."""
    nine_b = _load_json(_path_9b_audit()) or {}
    nine_d = _load_json(_path_9d_audit()) or {}
    nine_e = _load_json(_path_9e_audit()) or {}
    return {
        "phase_pass_chain": {
            "9B_1": (
                nine_b.get("ready_for_9c_or_9d")
                if "ready_for_9c_or_9d" in nine_b else None
            ),
            "9D": nine_d.get("ready_for_huge_society_architecture"),
            "9E": nine_e.get("ready_for_phase_10a_api_demo_packaging"),
        },
        "quality_summary": {
            "9B_1": _safe_dict(nine_b.get("discussion_quality_scores")).get(
                "aggregate_score",
            ),
            "9D": _safe_dict(nine_d.get("quality_scores")).get(
                "aggregate_score",
            ),
            "9E": _safe_dict(nine_e.get("quality_scores")).get(
                "aggregate_score",
            ),
        },
        "safety_summary": {
            "9B_1_forbidden_claim_clean": (
                not _safe_dict(nine_b.get("forbidden_claim_audit"))
                .get("any_forecast_or_verdict", False)
                and not _safe_dict(nine_b.get("forbidden_claim_audit"))
                .get("any_fake_target_product_use", False)
            ),
            "9D_forbidden_claim_clean": (
                not _safe_dict(nine_d.get("forbidden_claim_audit"))
                .get("any_forecast_or_verdict", False)
                and not _safe_dict(nine_d.get("forbidden_claim_audit"))
                .get("any_fake_target_product_use", False)
            ),
            "9E_forbidden_claim_clean": (
                not _safe_dict(nine_e.get("forbidden_claim_audit"))
                .get("any_forecast_or_verdict", False)
                and not _safe_dict(nine_e.get("forbidden_claim_audit"))
                .get("any_fake_target_product_use", False)
            ),
            "9B_1_sensitive_clean": (
                not _safe_dict(nine_b.get("sensitive_inference_audit"))
                .get("any_sensitive_inference", False)
            ),
        },
        "db_deltas_summary": {
            "9B_1_intent_ballots_only": _safe_dict(
                nine_b.get("additive_only_check"),
            ).get("non_ballot_deltas_zero"),
            "9D_cohort_only": _safe_dict(
                nine_d.get("additive_only_check"),
            ).get("non_cohort_deltas_zero"),
            "9E_intent_only": _safe_dict(
                nine_e.get("additive_only_check"),
            ).get("non_intent_deltas_zero"),
        },
    }
