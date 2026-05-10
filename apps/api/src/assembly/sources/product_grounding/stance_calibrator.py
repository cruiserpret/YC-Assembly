"""Phase 10B.1 — stance calibration.

Post-hoc rule-based review of each ballot's `private_stance` vs
its `private_reasoning`. The discussion enum is:

    curious_but_unconvinced     ← UNCERTAIN bucket
    interested_if_proven        ← RECEPTIVE bucket
    skeptical                   ← RESISTANT bucket
    likely_reject               ← RESISTANT bucket
    needs_more_information      ← UNCERTAIN bucket

Bucket mapping (frontend display): for→Receptive, against→Resistant,
neutral→Uncertain.

Rules:
  * If labelled RECEPTIVE but reasoning is mostly objections /
    proof demands without a positive intent signal → downgrade to
    UNCERTAIN (`curious_but_unconvinced`).
  * If labelled RESISTANT but reasoning shows positive intent
    ("I'd buy it", "I'd try one") → upgrade to RECEPTIVE.
  * If labelled UNCERTAIN but reasoning shows clear positive intent
    → upgrade to RECEPTIVE.
  * Otherwise keep the original.

Adds a `stance_justification` field to each calibrated ballot,
returned as part of the audit dict so the caller can persist it.
"""
from __future__ import annotations

import re
from typing import Any


_STANCE_TO_BUCKET: dict[str, str] = {
    "interested_if_proven": "for",
    "curious_but_unconvinced": "neutral",
    "needs_more_information": "neutral",
    "skeptical": "against",
    "likely_reject": "against",
}


_POSITIVE_INTENT_RE = re.compile(
    r"\b(i('?d|\s+would)\s+(?:buy|try|order|preorder|join\s+(?:the\s+)?waitlist|"
    r"recommend|switch))|"
    r"\bi\s+(?:want\s+to\s+)?(?:buy|order|preorder|try)\s+(?:one|this|it)\b|"
    r"\bsign\s+me\s+up\b|"
    r"\b(?:absolutely|definitely)\s+would\s+(?:buy|try|consider|switch)\b|"
    r"\b(?:make|makes)\s+sense\s+for\s+me\b|"
    r"\bi['']?m\s+(?:in|sold|interested\s+enough\s+to)\b",
    re.IGNORECASE,
)

_PROOF_DEMAND_RE = re.compile(
    r"\b(?:i\s+(?:'d|would)\s+need|i\s+need\s+to\s+see|"
    r"i\s+(?:'d|would)\s+want\s+to\s+(?:see|understand|know)|"
    r"i\s+want\s+to\s+(?:see|understand|know)|"
    r"i\s+need\s+to\s+know\s+(?:what['']?s\s+actually\s+inside|"
    r"(?:what|how|why|whether|if))|"
    r"(?:i['']?m\s+)?willing\s+to\s+be\s+convinced|"
    r"i\s+could\s+see\s+myself\s+(?:trying|considering)\s+(?:it|this)\s+if|"
    r"before\s+i\s+(?:can\s+)?(?:get|consider|commit|trust|move|think)|"
    r"(?:would|will)\s+only\s+(?:buy|consider)\s+(?:if|after|once)|"
    r"only\s+after\s+(?:hard\s+)?(?:specs|proof|comparisons|reviews)|"
    r"i\s+can(?:'t|not)\s+(?:tell|judge)|"
    r"there['']?s\s+no\s+(?:way\s+)?(?:for\s+me\s+)?to\s+(?:tell|know)|"
    r"i\s+(?:'d|would)\s+want\s+(?:proof|evidence|data|specs|"
    r"runtime|benchmarks?|reviews?))\b",
    re.IGNORECASE,
)

_RESISTANT_RE = re.compile(
    r"\b(?:not\s+for\s+me|not\s+interested|"
    r"(?:i\s+)?already\s+(?:have|own|use)\s+(?:one|something|"
    r"a\s+\w+)|i['']?ll\s+(?:pass|stick\s+with)|"
    r"hard\s+pass|no\s+thanks|i\s+wouldn['']?t\s+buy|"
    r"i\s+would\s+not\s+(?:buy|switch|consider)|"
    r"too\s+expensive|"
    r"i\s+don['']?t\s+see\s+(?:the\s+)?(?:point|value))",
    re.IGNORECASE,
)


def calibrate_stance(
    *, current_stance: str, reasoning: str
) -> dict[str, Any]:
    """Apply the calibration rubric to a single ballot. Returns
    a dict with `original_stance`, `recommended_stance`,
    `change`, and `stance_justification`."""
    text = reasoning or ""
    has_positive = bool(_POSITIVE_INTENT_RE.search(text))
    has_proof_demand = bool(_PROOF_DEMAND_RE.search(text))
    has_resistant = bool(_RESISTANT_RE.search(text))
    bucket = _STANCE_TO_BUCKET.get(current_stance, "neutral")

    recommended = current_stance
    why = "kept_original"

    # Rule 1: RECEPTIVE labelled but text is objections-without-signal
    if bucket == "for" and has_proof_demand and not has_positive:
        recommended = "curious_but_unconvinced"
        why = (
            "downgrade_for_to_neutral: reasoning is mostly proof "
            "demands without a clear positive intent signal"
        )
    # Rule 2: RESISTANT labelled but positive intent
    elif bucket == "against" and has_positive and not has_resistant:
        recommended = "interested_if_proven"
        why = (
            "upgrade_against_to_for: reasoning contains positive "
            "intent without resistance markers"
        )
    # Rule 3: UNCERTAIN with clear positive intent → upgrade
    elif bucket == "neutral" and has_positive and not has_proof_demand:
        recommended = "interested_if_proven"
        why = (
            "upgrade_neutral_to_for: reasoning contains clear "
            "positive intent and no major proof gate"
        )
    # Rule 4: UNCERTAIN with strong resistant markers → downgrade
    elif bucket == "neutral" and has_resistant and not has_positive:
        recommended = "skeptical"
        why = (
            "downgrade_neutral_to_against: reasoning contains "
            "explicit resistance markers"
        )

    return {
        "original_stance": current_stance,
        "recommended_stance": recommended,
        "change": recommended != current_stance,
        "stance_justification": why,
        "has_positive_intent": has_positive,
        "has_proof_demand": has_proof_demand,
        "has_resistant_markers": has_resistant,
    }


def calibrate_ballots(
    ballots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the calibrator over a list of ballot dicts (each with
    `persona_id`, `ballot_stage`, `private_stance`,
    `private_reasoning`). Returns an audit dict suitable for
    `stance_calibration_quality.json` and a list of corrections
    keyed by ballot index so the caller can apply them in DB.
    """
    corrections: list[dict[str, Any]] = []
    upgrade_count = 0
    downgrade_count = 0
    keep_count = 0
    examples: list[dict[str, Any]] = []
    for i, b in enumerate(ballots):
        result = calibrate_stance(
            current_stance=b.get("private_stance") or "",
            reasoning=b.get("private_reasoning") or "",
        )
        if result["change"]:
            corrections.append({
                "index": i,
                "persona_id": str(b.get("persona_id") or ""),
                "ballot_stage": b.get("ballot_stage"),
                **result,
            })
            old_bucket = _STANCE_TO_BUCKET.get(
                result["original_stance"], "neutral"
            )
            new_bucket = _STANCE_TO_BUCKET.get(
                result["recommended_stance"], "neutral"
            )
            order = {"against": 0, "neutral": 1, "for": 2}
            if order[new_bucket] > order[old_bucket]:
                upgrade_count += 1
            else:
                downgrade_count += 1
            if len(examples) < 8:
                examples.append({
                    "persona_id": str(b.get("persona_id") or ""),
                    "ballot_stage": b.get("ballot_stage"),
                    "from": result["original_stance"],
                    "to": result["recommended_stance"],
                    "reason": result["stance_justification"],
                    "excerpt": (
                        (b.get("private_reasoning") or "")[:200]
                    ),
                })
        else:
            keep_count += 1
    return {
        "phase": "10b_1_stance_calibration",
        "ballots_reviewed": len(ballots),
        "corrections_applied": len(corrections),
        "upgrades": upgrade_count,
        "downgrades": downgrade_count,
        "kept_count": keep_count,
        "corrections": corrections,
        "examples": examples,
    }
