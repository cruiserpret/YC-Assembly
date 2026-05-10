"""Phase 9D — per-cohort summary builder (universal).

Produces traceable summaries (psychology + objections + proof needs +
discussion behavior + role distribution) over a cohort's member
personas. Every aggregated claim cites a list of supporting persona /
turn / atom / source IDs so the audit can walk back to ground truth.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any


# Universal lexical buckets — NOT LumaLoop-specific. The same buckets
# work for any product brief because they only describe the *kind* of
# objection / proof, not its specific phrasing.
_OBJECTION_BUCKETS: dict[str, tuple[str, ...]] = {
    "no_ip_rating_or_durability_proof": (
        "ip rating", "ip-rating", "weather-resistant", "weatherproof",
        "durability", "durab", "drop test", "shock test",
    ),
    "battery_or_runtime_concern": (
        "battery life", "runtime", "battery", "rechargeable life",
        "hours of use", "charge cycles",
    ),
    "specs_not_disclosed": (
        "lumens", "spec sheet", "specs", "candela", "beam pattern",
        "beam angle",
    ),
    "price_value_concern": (
        "expensive", "overpriced", "cheaper", "for the price",
        "value", "$", "budget", "afford", "cost",
    ),
    "competitor_already_solves": (
        "noxgear", "amphipod", "nathan", "flipbelt", "black diamond",
        "incumbent", "current alternative", "tracer", "what i already",
    ),
    "trust_or_review_gap": (
        "third-party", "third party", "review", "athlete", "tested by",
        "lab test", "independent test",
    ),
    "no_use_case_fit": (
        "doesn't fit", "wrong shape", "not for my", "not suited",
        "no use", "doesn't work for",
    ),
    "social_visibility_concern": (
        "look silly", "vest is ugly", "uncool", "stigma", "feel weird",
    ),
}
_PROOF_BUCKETS: dict[str, tuple[str, ...]] = {
    "ip_rating_disclosure": (
        "ip rating", "ip-rating", "ip67", "ip68", "ipx",
    ),
    "lumens_disclosure": (
        "lumens", "candela",
    ),
    "battery_runtime_proof": (
        "battery life", "runtime", "hours at",
    ),
    "third_party_review": (
        "review", "third-party", "third party", "athlete review",
    ),
    "head_to_head_comparison": (
        "head-to-head", "side-by-side", "compared to", "vs ", "versus ",
    ),
    "durability_test": (
        "drop test", "durability test", "stress test",
    ),
    "warranty_or_returns": (
        "warranty", "return", "guarantee",
    ),
}


def _classify_text(
    text: str, buckets: dict[str, tuple[str, ...]],
) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    out: list[str] = []
    for label, terms in buckets.items():
        if any(term in lowered for term in terms):
            out.append(label)
    return out


def summarize_cohort(
    *,
    cohort_persona_ids: list[str],
    persona_meta: dict[str, dict[str, Any]],
    persona_psychology: dict[str, dict[str, float]],
    pre_ballots: dict[str, dict[str, Any]],
    final_ballots: dict[str, dict[str, Any]],
    reflection_ballots: dict[str, dict[str, Any]],
    discussion_turns: list[dict[str, Any]],
    memory_atoms: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a single cohort's traceable summary dict.

    `discussion_turns` should be filtered upstream to only the turns
    relevant to this cohort (i.e. spoken by its members).
    `memory_atoms` likewise filtered to atoms owned by cohort members.

    Returns a dict with the seven required summary fields plus a list
    of evidence-link dicts (cohort_id is filled in by the orchestrator).
    """
    role_dist = Counter(
        persona_meta.get(pid, {}).get("normalized_primary_role")
        for pid in cohort_persona_ids
    )
    final_dist = Counter(
        final_ballots.get(pid, {}).get("private_stance")
        for pid in cohort_persona_ids
    )
    pre_dist = Counter(
        pre_ballots.get(pid, {}).get("private_stance")
        for pid in cohort_persona_ids
    )

    # Psychology summary: per-trait mean + stdev, plus a label
    # ("low"/"medium"/"high") on the cohort mean.
    psy_summary: dict[str, dict[str, float | str]] = {}
    for trait_name in (
        "openness", "conscientiousness", "extraversion",
        "agreeableness", "neuroticism", "risk_tolerance",
        "novelty_seeking", "trust_proof_threshold",
        "social_influence_susceptibility",
        "category_involvement_or_expertise", "price_sensitivity",
    ):
        values = [
            persona_psychology.get(pid, {}).get(trait_name)
            for pid in cohort_persona_ids
        ]
        values = [float(v) for v in values if v is not None]
        if not values:
            continue
        mean = round(sum(values) / len(values), 4)
        stdev = round(
            statistics.stdev(values) if len(values) >= 2 else 0.0, 4,
        )
        if mean < 0.4:
            label = "low"
        elif mean > 0.6:
            label = "high"
        else:
            label = "medium"
        psy_summary[trait_name] = {
            "mean": mean, "stdev": stdev, "label": label,
        }

    # Objection / proof bucket counts from pre + final + reflection
    # reasoning + top_objection / top_proof_need fields, plus
    # discussion turn texts.
    obj_counter: Counter = Counter()
    proof_counter: Counter = Counter()
    obj_evidence: list[dict[str, Any]] = []
    proof_evidence: list[dict[str, Any]] = []
    psy_evidence: list[dict[str, Any]] = []
    stance_evidence: list[dict[str, Any]] = []
    discuss_evidence: list[dict[str, Any]] = []

    for pid in cohort_persona_ids:
        pre = pre_ballots.get(pid) or {}
        final = final_ballots.get(pid) or {}
        refl = reflection_ballots.get(pid) or {}
        for src_label, ballot in (
            ("pre", pre), ("final", final), ("reflection", refl),
        ):
            text = " ".join(filter(None, [
                ballot.get("private_reasoning"),
                ballot.get("top_objection") or "",
                ballot.get("top_proof_need") or "",
            ]))
            for label in _classify_text(text, _OBJECTION_BUCKETS):
                obj_counter[label] += 1
                if len(obj_evidence) < 60:
                    excerpt = (
                        ballot.get("top_objection")
                        or ballot.get("private_reasoning") or ""
                    )[:240]
                    if excerpt:
                        obj_evidence.append({
                            "evidence_role": "objection",
                            "label": label,
                            "persona_id": pid,
                            "excerpt": excerpt,
                            "source_kind": (
                                f"discussion_private_ballots:{src_label}"
                            ),
                        })
            for label in _classify_text(text, _PROOF_BUCKETS):
                proof_counter[label] += 1
                if len(proof_evidence) < 60:
                    excerpt = (
                        ballot.get("top_proof_need")
                        or ballot.get("private_reasoning") or ""
                    )[:240]
                    if excerpt:
                        proof_evidence.append({
                            "evidence_role": "proof_need",
                            "label": label,
                            "persona_id": pid,
                            "excerpt": excerpt,
                            "source_kind": (
                                f"discussion_private_ballots:{src_label}"
                            ),
                        })
        # stance anchor — final ballot
        if final.get("private_stance"):
            if len(stance_evidence) < 30:
                stance_evidence.append({
                    "evidence_role": "stance_anchor",
                    "persona_id": pid,
                    "excerpt": (
                        f"final_stance={final.get('private_stance')}; "
                        f"reasoning={(final.get('private_reasoning') or '')[:200]}"
                    ),
                })

    # Discussion turn classification — turns spoken by cohort members
    cohort_member_set = set(cohort_persona_ids)
    cohort_turns = [
        t for t in discussion_turns
        if t.get("speaker_persona_id") in cohort_member_set
    ]
    turn_count_by_type = Counter(t.get("turn_type") for t in cohort_turns)
    challenge_turn_count = turn_count_by_type.get("challenge", 0)
    peer_response_turn_count = turn_count_by_type.get("peer_response", 0)
    proof_discussion_turn_count = turn_count_by_type.get(
        "proof_discussion", 0,
    )
    peer_ref_count = sum(
        len(t.get("referenced_turn_ids") or []) for t in cohort_turns
    )
    for t in cohort_turns[:30]:
        if len(discuss_evidence) >= 30:
            break
        discuss_evidence.append({
            "evidence_role": "discussion_anchor",
            "persona_id": t.get("speaker_persona_id"),
            "discussion_turn_id": t.get("turn_id"),
            "excerpt": (t.get("public_text") or "")[:240],
        })

    # public/private delta classification
    delta_counter = Counter(
        (final_ballots.get(pid) or {}).get("public_private_delta")
        or "no_change"
        for pid in cohort_persona_ids
    )

    # psychology evidence: pick traits with the most extreme cohort mean
    psy_extremes = sorted(
        psy_summary.items(),
        key=lambda kv: -abs(float(kv[1]["mean"]) - 0.5),
    )[:3]
    for trait_name, summary in psy_extremes:
        psy_evidence.append({
            "evidence_role": "psychology_anchor",
            "label": trait_name,
            "excerpt": (
                f"cohort {trait_name} mean={summary['mean']} "
                f"({summary['label']}); stdev={summary['stdev']}"
            ),
        })

    # memory atom anchors
    memory_evidence: list[dict[str, Any]] = []
    by_persona: dict[str, list[dict[str, Any]]] = {}
    for a in memory_atoms:
        if a.get("persona_id") in cohort_member_set:
            by_persona.setdefault(a["persona_id"], []).append(a)
    for pid, atoms in by_persona.items():
        # pick the single highest-importance atom per cohort member
        if not atoms:
            continue
        top = max(atoms, key=lambda a: int(a.get("importance_score") or 0))
        if len(memory_evidence) >= 30:
            break
        memory_evidence.append({
            "evidence_role": "evidence_anchor",
            "persona_id": pid,
            "memory_atom_id": top.get("memory_atom_id") or top.get("id"),
            "excerpt": (top.get("origin_excerpt") or "")[:240],
        })

    return {
        "role_distribution": dict(role_dist),
        "stance_distribution": dict(final_dist),
        "pre_stance_distribution": dict(pre_dist),
        "psychology_summary": psy_summary,
        "objection_summary": {
            "by_bucket": dict(obj_counter),
            "top_buckets": [b for b, _ in obj_counter.most_common(5)],
        },
        "proof_need_summary": {
            "by_bucket": dict(proof_counter),
            "top_buckets": [b for b, _ in proof_counter.most_common(5)],
        },
        "discussion_behavior_summary": {
            "turn_count_by_type": dict(turn_count_by_type),
            "challenge_turn_count": challenge_turn_count,
            "peer_response_turn_count": peer_response_turn_count,
            "proof_discussion_turn_count": proof_discussion_turn_count,
            "peer_reference_count": peer_ref_count,
            "public_private_delta_distribution": dict(delta_counter),
        },
        "_evidence_links": (
            obj_evidence + proof_evidence + psy_evidence
            + stance_evidence + discuss_evidence + memory_evidence
        ),
    }
