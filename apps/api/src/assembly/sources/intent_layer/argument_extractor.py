"""Phase 9E — extract society-wide arguments from existing 9B/9D data.

Produces ArgumentDraft objects keyed off real cohort summaries and
discussion turns. Each draft cites a real `origin_ref_id` so the DB
CHECK on `society_arguments` accepts it.

Universal — argument types come from a closed set; texts are summary-
style ("the strongest objection in cohort X was about Y"), never
fabricated agent speech.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from assembly.sources.intent_layer.schemas import ArgumentDraft


_OBJ_BUCKET_TO_ARG_TYPE: dict[str, str] = {
    "no_ip_rating_or_durability_proof": "trust_safety",
    "battery_or_runtime_concern": "objection",
    "specs_not_disclosed": "trust_safety",
    "price_value_concern": "price_value",
    "competitor_already_solves": "loyalist_resistance",
    "trust_or_review_gap": "trust_safety",
    "no_use_case_fit": "objection",
    "social_visibility_concern": "objection",
}
_PROOF_BUCKET_TO_ARG_TYPE: dict[str, str] = {
    "ip_rating_disclosure": "proof_need",
    "lumens_disclosure": "proof_need",
    "battery_runtime_proof": "proof_need",
    "third_party_review": "proof_need",
    "head_to_head_comparison": "proof_need",
    "durability_test": "proof_need",
    "warranty_or_returns": "proof_need",
}


def extract_society_arguments(
    *,
    cohorts: list[dict[str, Any]],
    discussion_turns: list[dict[str, Any]],
) -> list[ArgumentDraft]:
    """Build the canonical argument list. Each cohort contributes:
      - its top objection bucket → one argument (objection / trust_safety
        / loyalist_resistance / price_value depending on bucket)
      - its top proof_need bucket → one argument (proof_need)
      - up to 1 additional (persuasion_lever) if the cohort had any
        reflection content suggesting an argument that worked

    `cohorts[i]` must include: cohort_id (str), member_persona_ids,
    objection_summary.by_bucket, proof_need_summary.by_bucket, and
    at least one supporting discussion turn id from cohort members.
    """
    drafts: list[ArgumentDraft] = []
    turns_by_speaker: dict[str, list[dict[str, Any]]] = {}
    for t in discussion_turns:
        spk = t.get("speaker_persona_id")
        if spk:
            turns_by_speaker.setdefault(spk, []).append(t)

    for cohort in cohorts:
        cohort_id = cohort.get("cohort_id") or cohort.get("id")
        if not cohort_id:
            continue
        member_ids = cohort.get("member_persona_ids") or []
        cohort_turns: list[dict[str, Any]] = []
        for m in member_ids:
            cohort_turns.extend(turns_by_speaker.get(str(m), []))
        # Top objection
        obj_bb = (
            (cohort.get("objection_summary") or {}).get("by_bucket") or {}
        )
        obj_top = next(iter(sorted(
            obj_bb.items(), key=lambda kv: -kv[1],
        )), None)
        if obj_top:
            obj_bucket, _count = obj_top
            arg_type = _OBJ_BUCKET_TO_ARG_TYPE.get(obj_bucket, "objection")
            anchor_turn = next(
                (t for t in cohort_turns
                 if t.get("turn_type") == "challenge"
                 and obj_bucket.split("_")[0] in (t.get("public_text") or "").lower()),
                cohort_turns[0] if cohort_turns else None,
            )
            origin_ref = (
                anchor_turn.get("turn_id")
                if anchor_turn else (
                    str(member_ids[0]) if member_ids else None
                )
            )
            origin_type = (
                "discussion_turn" if anchor_turn else "persona"
            )
            if origin_ref:
                drafts.append(ArgumentDraft(
                    origin_type=origin_type,  # type: ignore[arg-type]
                    origin_ref_id=str(origin_ref),
                    argument_text=(
                        f"Cohort raises objection bucket "
                        f"`{obj_bucket}`: members repeatedly flagged "
                        "this concern in pre/final ballots and "
                        "discussion turns."
                    )[:1500],
                    argument_type=arg_type,  # type: ignore[arg-type]
                    source_cohort_id=str(cohort_id),
                    supporting_turn_ids=[
                        str(t.get("turn_id"))
                        for t in cohort_turns[:5]
                        if t.get("turn_id")
                    ],
                    supporting_memory_atom_ids=[],
                ))
        # Top proof need
        proof_bb = (
            (cohort.get("proof_need_summary") or {}).get("by_bucket") or {}
        )
        proof_top = next(iter(sorted(
            proof_bb.items(), key=lambda kv: -kv[1],
        )), None)
        if proof_top:
            proof_bucket, _count = proof_top
            arg_type = _PROOF_BUCKET_TO_ARG_TYPE.get(
                proof_bucket, "proof_need",
            )
            anchor_turn = next(
                (t for t in cohort_turns
                 if t.get("turn_type") == "proof_discussion"),
                cohort_turns[-1] if cohort_turns else None,
            )
            origin_ref = (
                anchor_turn.get("turn_id")
                if anchor_turn else (
                    str(member_ids[0]) if member_ids else None
                )
            )
            origin_type = (
                "discussion_turn" if anchor_turn else "persona"
            )
            if origin_ref:
                drafts.append(ArgumentDraft(
                    origin_type=origin_type,  # type: ignore[arg-type]
                    origin_ref_id=str(origin_ref),
                    argument_text=(
                        f"Cohort proof-need bucket "
                        f"`{proof_bucket}`: this artifact would shift "
                        "the cohort's stance if delivered."
                    )[:1500],
                    argument_type=arg_type,  # type: ignore[arg-type]
                    source_cohort_id=str(cohort_id),
                    supporting_turn_ids=[
                        str(t.get("turn_id"))
                        for t in cohort_turns[:5]
                        if t.get("turn_id")
                    ],
                    supporting_memory_atom_ids=[],
                ))
        # Optional persuasion_lever — if the cohort had at least one
        # private_acceptance public_private_delta entry, that means
        # something persuaded a member.
        delta_dist = (
            (cohort.get("discussion_behavior_summary") or {})
            .get("public_private_delta_distribution") or {}
        )
        if delta_dist.get("private_acceptance", 0) >= 1:
            anchor_turn = next(
                (t for t in cohort_turns
                 if t.get("turn_type") == "peer_response"),
                cohort_turns[0] if cohort_turns else None,
            )
            origin_ref = (
                anchor_turn.get("turn_id")
                if anchor_turn else (
                    str(member_ids[0]) if member_ids else None
                )
            )
            origin_type = (
                "discussion_turn" if anchor_turn else "persona"
            )
            if origin_ref:
                drafts.append(ArgumentDraft(
                    origin_type=origin_type,  # type: ignore[arg-type]
                    origin_ref_id=str(origin_ref),
                    argument_text=(
                        "Cohort had at least one private_acceptance "
                        "stance shift; the peer-response argument "
                        "appears to have moved a member privately."
                    )[:1500],
                    argument_type="persuasion_lever",
                    source_cohort_id=str(cohort_id),
                    supporting_turn_ids=[
                        str(t.get("turn_id"))
                        for t in cohort_turns[:5]
                        if t.get("turn_id")
                    ],
                    supporting_memory_atom_ids=[],
                ))
    return drafts
