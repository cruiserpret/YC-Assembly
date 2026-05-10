"""Phase 10B.3 — stricter RECEPTIVE classification.

KEEPS the user-facing label "RECEPTIVE". Does NOT rename it.

Strengthens *who earns it*. Phase 10B.2's calibrator already
downgraded `interested_if_proven` ballots whose reasoning was pure
proof demand. Phase 10B.3 layers two more strictness rules on top:

  • A stance only stays RECEPTIVE if the reasoning shows at least
    ONE positive purchase-driver signal (clear personal use case,
    willingness to try / buy / preorder, clear preference over an
    alternative). Curiosity alone is not enough.
  • If RECEPTIVE reasoning is dominated by a major proof gate
    ("safety / certification / coating durability before I care"),
    the stance is downgraded to UNCERTAIN even when one weak
    positive signal is present — because the proof gate is doing
    the heavy lifting.

Audit artifact: stance_strictness_quality.json.
"""
from __future__ import annotations

import re
from typing import Any

from assembly.sources.product_grounding.stance_calibrator import (
    _POSITIVE_INTENT_RE,
    _PROOF_DEMAND_RE,
    _RESISTANT_RE,
    _STANCE_TO_BUCKET,
)


# Major proof gates — when these dominate the reasoning, the
# persona is NOT receptive even if one weak positive line is
# present. These cover the GlowPlate hardest-to-convince patterns
# the report missed.
_MAJOR_PROOF_GATE_RE = re.compile(
    r"\b(?:safety\s+(?:certification|cert|proof|standards?|"
    r"testing|test|approval|approved)|"
    r"food[\- ]contact\s+(?:material|certification|safety)|"
    r"ul[\- ]?listed|ul/etl|ul\s+listing|etl\s+listed?|"
    r"fda\s+(?:approved|certified|cert|approval|clearance)|"
    r"lfgb|"
    r"(?:material|coating|durability)\s+(?:proof|tests?|"
    r"certification|certifications|guarantee)|"
    r"third[\- ]party\s+(?:certification|cert|test|review|audit)|"
    r"(?:before\s+i\s+(?:can\s+)?(?:trust|consider|even\s+"
    r"consider|believe))|"
    r"i'?m\s+not\s+(?:sold|convinced)\s+until|"
    r"i\s+do(?:n'?t)?\s+(?:trust|believe|buy)\s+(?:the\s+)?"
    r"(?:claim|claims|mechanism)|"
    r"unsafe|burn\s+(?:risk|hazard)|fire\s+(?:risk|hazard)|"
    r"electrical\s+safety|auto[\- ]shutoff)\b",
    re.IGNORECASE,
)

# A clear personal-use-case signal: "I work from home and my food
# gets cold", "this would solve a real problem for me", etc. Not
# the same as positive intent (which is about willingness to buy);
# a use-case signal is about whether the product fits *their life*.
_USE_CASE_FIT_RE = re.compile(
    r"\b(?:my\s+(?:food|lunch|meal|coffee|tea|drink)\s+(?:gets|stays)\s+cold|"
    r"i\s+(?:work\s+from\s+home|wfh|eat\s+slowly|am\s+a\s+slow\s+eater|"
    r"spread\s+(?:out|over)\s+(?:my\s+)?(?:meals|lunches))|"
    r"i\s+already\s+(?:re-?heat|microwave|put\s+(?:my\s+)?food\s+back)|"
    r"this\s+would\s+(?:actually\s+)?(?:solve|fix|address|"
    r"help\s+with)\s+(?:a\s+real\s+|the\s+)?(?:problem|annoyance|"
    r"pain\s+point|issue)\s+(?:for\s+me|i\s+have)|"
    r"(?:fits|matches|suits)\s+(?:my\s+)?routine|"
    r"i\s+would\s+(?:actually\s+)?use\s+(?:this|it|one))\b",
    re.IGNORECASE,
)


def classify_stance_strictness(
    *,
    current_stance: str,
    reasoning: str,
) -> dict[str, Any]:
    """Apply Phase 10B.3's stricter RECEPTIVE rule. Returns:
        recommended_stance: str
        change: bool
        stance_justification: str
        signals: dict (positive_intent, use_case_fit, proof_demand,
                       major_proof_gate, resistant_markers)
    """
    text = reasoning or ""
    has_positive = bool(_POSITIVE_INTENT_RE.search(text))
    has_use_case = bool(_USE_CASE_FIT_RE.search(text))
    has_proof = bool(_PROOF_DEMAND_RE.search(text))
    has_major_gate = bool(_MAJOR_PROOF_GATE_RE.search(text))
    has_resist = bool(_RESISTANT_RE.search(text))

    bucket = _STANCE_TO_BUCKET.get(current_stance, "neutral")
    recommended = current_stance
    why = "kept_original"

    # Strict RECEPTIVE rule:
    #   bucket="for" requires (positive_intent OR use_case_fit) AND
    #   NOT major_proof_gate. If a major gate is present, downgrade
    #   to UNCERTAIN regardless of the positive signal — the gate
    #   is fundamental.
    if bucket == "for":
        if has_major_gate:
            recommended = "curious_but_unconvinced"
            why = (
                "downgrade_for_to_uncertain_strict_v2: reasoning "
                "centers on a major proof gate (safety / "
                "certification / material / durability) that must "
                "clear before this can count as receptive"
            )
        elif not has_positive and not has_use_case:
            recommended = "curious_but_unconvinced"
            why = (
                "downgrade_for_to_uncertain_strict_v2: no positive "
                "intent AND no clear personal use-case signal — "
                "curiosity alone does not earn RECEPTIVE"
            )
        elif has_proof and not has_positive and has_use_case:
            # Use-case fit alone, with proof demand and no positive
            # intent — keep but flag for review.
            recommended = current_stance
            why = (
                "kept_for_use_case_only: use-case fit signal "
                "present but no purchase intent; reviewed and "
                "kept (qualifies under strict v2)"
            )
        else:
            recommended = current_stance
            why = (
                "kept_for_strict_v2: positive intent and/or "
                "use-case fit present; no major proof gate"
            )
    elif bucket == "neutral":
        if has_positive and has_use_case and not has_major_gate and not has_resist:
            recommended = "interested_if_proven"
            why = (
                "upgrade_neutral_to_for_strict_v2: clear positive "
                "intent + personal use-case fit; no major proof gate"
            )
    elif bucket == "against":
        if has_positive and has_use_case and not has_resist:
            recommended = "interested_if_proven"
            why = (
                "upgrade_against_to_for_strict_v2: positive intent "
                "AND use-case fit, no resistant markers"
            )

    return {
        "original_stance": current_stance,
        "recommended_stance": recommended,
        "change": recommended != current_stance,
        "stance_justification": why,
        "signals": {
            "has_positive_intent": has_positive,
            "has_use_case_fit": has_use_case,
            "has_proof_demand": has_proof,
            "has_major_proof_gate": has_major_gate,
            "has_resistant_markers": has_resist,
        },
    }


def audit_stance_strictness(
    ballots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the strict v2 classifier across a list of ballot dicts.
    Returns the audit dict for `stance_strictness_quality.json`."""
    receptive_before = 0
    uncertain_before = 0
    resistant_before = 0
    for b in ballots:
        bk = _STANCE_TO_BUCKET.get(b.get("private_stance") or "", "neutral")
        if bk == "for":
            receptive_before += 1
        elif bk == "neutral":
            uncertain_before += 1
        else:
            resistant_before += 1

    corrections: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    downgraded_receptive = 0
    upgraded_to_receptive = 0
    missing_justification = 0

    for i, b in enumerate(ballots):
        result = classify_stance_strictness(
            current_stance=b.get("private_stance") or "",
            reasoning=b.get("private_reasoning") or "",
        )
        if not result["stance_justification"]:
            missing_justification += 1
        if not result["change"]:
            continue
        old_bucket = _STANCE_TO_BUCKET.get(
            result["original_stance"], "neutral"
        )
        new_bucket = _STANCE_TO_BUCKET.get(
            result["recommended_stance"], "neutral"
        )
        if old_bucket == "for" and new_bucket != "for":
            downgraded_receptive += 1
        if old_bucket != "for" and new_bucket == "for":
            upgraded_to_receptive += 1
        corrections.append({
            "index": i,
            "persona_id": str(b.get("persona_id") or ""),
            "ballot_stage": b.get("ballot_stage"),
            **result,
        })
        if len(examples) < 8:
            examples.append({
                "persona_id": str(b.get("persona_id") or ""),
                "ballot_stage": b.get("ballot_stage"),
                "from": result["original_stance"],
                "to": result["recommended_stance"],
                "reason": result["stance_justification"],
                "signals": result["signals"],
                "excerpt": (b.get("private_reasoning") or "")[:240],
            })

    receptive_after = receptive_before - downgraded_receptive + upgraded_to_receptive
    uncertain_after = (
        uncertain_before
        + downgraded_receptive
        - upgraded_to_receptive
    )
    # downgrades to/from resistant aren't applied by this strict
    # classifier in v2 (it only operates on the receptive boundary)
    resistant_after = resistant_before

    return {
        "phase": "10b_3_stance_strictness",
        "ballots_reviewed": len(ballots),
        "receptive_count_before": receptive_before,
        "receptive_count_after": receptive_after,
        "uncertain_count_before": uncertain_before,
        "uncertain_count_after": uncertain_after,
        "resistant_count_before": resistant_before,
        "resistant_count_after": resistant_after,
        "downgraded_receptive_count": downgraded_receptive,
        "upgraded_receptive_count": upgraded_to_receptive,
        "stance_justification_missing_count": missing_justification,
        "corrections": corrections,
        "examples": examples,
    }
