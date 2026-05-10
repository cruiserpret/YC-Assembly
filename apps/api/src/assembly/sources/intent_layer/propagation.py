"""Phase 9E — deterministic argument propagation across cohorts.

For each (argument, target_cohort) pair (excluding self-propagation),
compute a response based on:
  - Does the target cohort already share this objection / proof-need
    bucket?  → adopted (intensified if already top bucket)
  - Does the target cohort's psychology suggest disagreement?
    (e.g. high price_sensitivity argument hitting a low price_sensitivity
    cohort) → resisted
  - Does the target cohort have a related but different bucket?
    → modified
  - Otherwise → ignored

`effect_on_intent` follows from response_type:
  adopted/intensified → increased_uncertainty (more concern surfaced)
  resisted             → no_change
  modified             → no_change (but the modified text is captured)
  ignored              → no_change

Universal — no LumaLoop hardcoding.
"""
from __future__ import annotations

from typing import Any

from assembly.sources.intent_layer.schemas import (
    ArgumentDraft, PropagationDraft,
)


def _bucket_of_argument(arg: ArgumentDraft) -> str | None:
    """Reverse-engineer the bucket label from the argument_text we
    emitted in the extractor."""
    text = arg.argument_text or ""
    # The extractor uses literal "`bucket_name`:" syntax; pull it out.
    if "`" in text:
        try:
            start = text.index("`")
            end = text.index("`", start + 1)
            return text[start + 1:end]
        except ValueError:
            return None
    return None


def _intent_effect_for(response_type: str) -> str:
    return {
        "adopted": "increased_uncertainty",
        "intensified": "increased_uncertainty",
        "resisted": "no_change",
        "modified": "no_change",
        "ignored": "no_change",
    }.get(response_type, "no_change")


def propagate_arguments_across_cohorts(
    *,
    arguments_with_ids: list[tuple[str, ArgumentDraft]],
    cohorts: list[dict[str, Any]],
) -> list[PropagationDraft]:
    """Build PropagationDraft list for every (argument × target_cohort)
    pair where target_cohort is NOT the argument's source cohort.

    `arguments_with_ids[i]` = (argument_id_str, ArgumentDraft).
    `cohorts[i]` must include cohort_id, objection_summary.by_bucket,
    proof_need_summary.by_bucket, psychology_summary, representatives.
    """
    drafts: list[PropagationDraft] = []
    cohorts_by_id = {
        str(c.get("cohort_id") or c.get("id")): c for c in cohorts
    }

    for arg_id, arg in arguments_with_ids:
        bucket = _bucket_of_argument(arg)
        for target in cohorts:
            target_id = str(target.get("cohort_id") or target.get("id"))
            if target_id == (arg.source_cohort_id or ""):
                continue  # don't propagate to self
            target_obj_bb = (
                (target.get("objection_summary") or {}).get("by_bucket")
                or {}
            )
            target_proof_bb = (
                (target.get("proof_need_summary") or {}).get("by_bucket")
                or {}
            )
            psy = target.get("psychology_summary") or {}
            primary_rep = (
                (target.get("representatives") or {}).get("primary")
            )

            response_type = "ignored"
            response_text = (
                f"Cohort `{target_id[:8]}` did not engage with this "
                "argument; their summary contains no related bucket "
                "and no psychology signal pointing at it."
            )
            evidence = (
                f"Target cohort objection_buckets="
                f"{list(target_obj_bb.keys())} ; "
                f"proof_buckets={list(target_proof_bb.keys())}"
            )

            if bucket:
                # Adopted / intensified: target already has the bucket
                if bucket in target_obj_bb:
                    target_top_obj = next(iter(sorted(
                        target_obj_bb.items(), key=lambda kv: -kv[1],
                    )), None)
                    if target_top_obj and target_top_obj[0] == bucket:
                        response_type = "intensified"
                        response_text = (
                            f"Target cohort already treats `{bucket}` "
                            "as its top objection; argument intensifies "
                            "the existing concern."
                        )
                    else:
                        response_type = "adopted"
                        response_text = (
                            f"Target cohort recognizes `{bucket}` from "
                            "its own discussion; the argument lands."
                        )
                    evidence += f" ; bucket `{bucket}` present in target."
                elif bucket in target_proof_bb:
                    response_type = "adopted"
                    response_text = (
                        f"Target cohort lists `{bucket}` as a proof "
                        "need it would value."
                    )
                    evidence += (
                        f" ; bucket `{bucket}` is among target's proof "
                        "needs."
                    )
                else:
                    # Resistance check: psychology mismatch
                    # (e.g. price_value_concern argument hitting a cohort
                    #  with low price_sensitivity mean)
                    psy_label_for_bucket = {
                        "price_value_concern": (
                            "price_sensitivity", "low",
                        ),
                        "no_ip_rating_or_durability_proof": (
                            "trust_proof_threshold", "low",
                        ),
                        "competitor_already_solves": (
                            "category_involvement_or_expertise", "low",
                        ),
                        "trust_or_review_gap": (
                            "trust_proof_threshold", "low",
                        ),
                    }
                    mismatch = psy_label_for_bucket.get(bucket)
                    if mismatch:
                        trait_name, expected_low_label = mismatch
                        target_label = (
                            (psy.get(trait_name) or {}).get("label")
                        )
                        if target_label == expected_low_label:
                            response_type = "resisted"
                            response_text = (
                                f"Target cohort's `{trait_name}` is "
                                f"`{target_label}`; the `{bucket}` "
                                "argument doesn't resonate."
                            )
                            evidence += (
                                f" ; psychology mismatch: target "
                                f"{trait_name}={target_label}."
                            )
                        else:
                            response_type = "modified"
                            response_text = (
                                f"Target cohort acknowledges the "
                                f"argument's spirit but reframes it via "
                                f"its `{trait_name}` profile."
                            )
                            evidence += (
                                f" ; psychology partial match: target "
                                f"{trait_name}={target_label}."
                            )
                    else:
                        # Generic modified — bucket not in target
                        response_type = "modified"
                        response_text = (
                            f"Target cohort engages with the argument "
                            f"but rephrases `{bucket}` through its own "
                            "concerns."
                        )

            drafts.append(PropagationDraft(
                argument_id=arg_id,
                target_cohort_id=target_id,
                representative_persona_id=(
                    str(primary_rep) if primary_rep else None
                ),
                response_type=response_type,  # type: ignore[arg-type]
                response_text=response_text[:2000],
                effect_on_intent=_intent_effect_for(  # type: ignore[arg-type]
                    response_type,
                ),
                evidence_basis=evidence[:2000],
            ))
    return drafts
