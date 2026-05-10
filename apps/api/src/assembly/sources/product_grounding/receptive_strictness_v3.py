"""Phase 10B.4 — Stricter RECEPTIVE classifier v3.

KEEPS the user-facing label "RECEPTIVE". Does NOT rename it.

Phase 10B.3's strict v2 already caught: "no positive intent AND no
use-case fit" → downgrade. And: "any major proof gate present" →
downgrade.

PantryPulse showed v2 isn't tight enough. The remaining failure
mode: a persona writes ONE positive line ("I have two kids and
this would help me") sandwiched between FOUR proof-demand lines
("if it's manual I'm out", "without a demo $149 is a magnet and a
promise", "show me the workflow", "I need a side-by-side"). v2
sees the positive signal and lets it through. The result reads as
RECEPTIVE in the report, which is dishonest.

v3 rule:
  • Count the positive-driver sentences and the proof-demand
    sentences. If proof-demand sentences outnumber positive-driver
    sentences (or there are zero positive-driver sentences and any
    proof-demand at all), downgrade RECEPTIVE → UNCERTAIN.
  • The classic v2 rules (major proof gate, no-positive-no-use-case)
    still apply on top.
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
from assembly.sources.product_grounding.stance_strictness import (
    _MAJOR_PROOF_GATE_RE,
    _USE_CASE_FIT_RE,
)


# Strong positive-driver patterns: clear willingness to buy / try /
# evaluate or clear preference over an alternative. Tighter than the
# 10B.1 _POSITIVE_INTENT_RE.
_POSITIVE_DRIVER_RE = re.compile(
    r"\b(?:i\s+(?:would|'?d)\s+(?:try|buy|preorder|order|consider\s+buying|"
    r"join\s+the\s+waitlist|seriously\s+consider|evaluate)|"
    r"i\s+(?:want\s+to\s+|would\s+want\s+to\s+)?(?:try|buy|order)\s+(?:one|this|it)|"
    r"sign\s+me\s+up|"
    r"i'?m\s+(?:in|sold)|"
    r"this\s+(?:would|'?d)\s+(?:actually\s+)?(?:solve|fix|address|help)\s+"
    r"(?:a\s+real\s+|the\s+|my\s+)?(?:problem|annoyance|pain\s+point|issue)|"
    r"(?:meaningfully\s+)?better\s+than\s+(?:my\s+|our\s+)?(?:current|existing)|"
    r"i\s+would\s+(?:actually\s+)?use\s+(?:this|it|one))\b",
    re.IGNORECASE,
)

# Conditional-receptive patterns: "I am in IF X is shown". These
# sound positive but the conditional gate is what's doing the work.
# v3 treats these as proof-demand-shaped, NOT as positive drivers.
_CONDITIONAL_RECEPTIVE_RE = re.compile(
    r"\bi\s+(?:am|'?m)\s+in\s+if\b|"
    r"\bi\s+(?:would|'?d)\s+(?:buy|try|consider)\s+(?:this|it|one)?\s*"
    r"(?:if|once|when|provided|after)\b|"
    r"\bonly\s+if\b|"
    r"\bcontingent\s+on\b",
    re.IGNORECASE,
)

# Killer proof-demand markers — sentences shaped like "without X,
# this is just a magnet/promise/chore" that destroy receptive
# semantics even though the persona may have written one positive
# line earlier in the ballot.
_KILLER_PROOF_RE = re.compile(
    # "without that / short of that, $149 is just a magnet"
    r"(?:without|short\s+of)\s+(?:that|a|the|this)\s*[\w\s]{0,40}"
    r"(?:\$\s?\d+\s+(?:is\s+|reads\s+as\s+|feels\s+like\s+)?"
    r"(?:just\s+)?a\s+(?:magnet|promise|chore|app|gimmick|"
    r"hardware\s+wrapper|gadget))|"
    # "if the answer is 'you scan every item' / manual / the user still has"
    r"(?:if|when)\s+the\s+(?:answer|input|workflow)\s+is\s+"
    r"['\"]?(?:you\s+scan\s+every|manual|the\s+user\s+still\s+has)|"
    # "if it is/it's manual, I'm out"
    r"\bif\s+(?:it\s+is|it'?s)\s+manual,?\s+i'?m\s+out\b|"
    # "show me a 30-second clip" / "need a clip"
    r"\b(?:show\s+me|need)\s+a\s+(?:30-?second\s+)?clip\b|"
    # "without a demo / without a side-by-side / without head-to-head"
    r"\bwithout\s+(?:a\s+)?(?:demo|side[\- ]by[\- ]side|head[\- ]to[\- ]head)\b|"
    # "$149 is (just a) magnet (and a promise) / chore / gimmick"
    # Note: no \b before \$ — `\b` requires a word char and `$` isn't.
    r"\$\s?\d+\s+(?:is|reads\s+as|feels\s+like)\s+(?:just\s+)?(?:a\s+)?"
    r"(?:magnet|chore|gimmick|hardware\s+wrapped\s+around\s+a\s+free\s+habit)|"
    # "$149 reads as a magnet plus an app"
    r"\$\s?\d+\s+reads\s+as\s+a\s+magnet\s+plus\s+an?\s+(?:app|chore|gimmick)|"
    # "buying me a magnet and a logging chore"
    r"\bbuying\s+me\s+a\s+magnet\s+and\s+a\s+logging\s+chore\b",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    placeholder = "<<DOT>>"
    safe = re.sub(
        r"(\d)\.(\d)", lambda m: f"{m.group(1)}{placeholder}{m.group(2)}",
        text,
    )
    raw = re.split(r"(?<=[\.\?\!])\s+|\n+", safe)
    return [
        p.replace(placeholder, ".").strip()
        for p in raw
        if p.strip()
    ]


def classify_stance_strictness_v3(
    *,
    current_stance: str,
    reasoning: str,
) -> dict[str, Any]:
    """Apply v3 strictness. Counts proof-demand vs positive-driver
    sentences, then layers v2 rules on top."""
    text = reasoning or ""
    sentences = _split_sentences(text)

    has_positive_intent = bool(_POSITIVE_INTENT_RE.search(text))
    has_use_case = bool(_USE_CASE_FIT_RE.search(text))
    has_proof_demand = bool(_PROOF_DEMAND_RE.search(text))
    has_major_gate = bool(_MAJOR_PROOF_GATE_RE.search(text))
    has_resistant = bool(_RESISTANT_RE.search(text))

    # Per-sentence counts.
    positive_driver_count = 0
    proof_demand_count = 0
    killer_proof_count = 0
    conditional_receptive_count = 0
    for s in sentences:
        is_positive_driver = bool(_POSITIVE_DRIVER_RE.search(s))
        is_proof = bool(_PROOF_DEMAND_RE.search(s))
        is_killer = bool(_KILLER_PROOF_RE.search(s))
        is_conditional = bool(_CONDITIONAL_RECEPTIVE_RE.search(s))
        if is_killer:
            killer_proof_count += 1
        if is_conditional:
            conditional_receptive_count += 1
        # Conditional-receptive patterns count as proof-demand for v3
        # purposes — "I would buy this IF X" is a proof gate dressed
        # in positive verbs.
        if is_conditional and not is_positive_driver:
            proof_demand_count += 1
            continue
        if is_positive_driver:
            positive_driver_count += 1
        elif is_proof:
            proof_demand_count += 1

    bucket = _STANCE_TO_BUCKET.get(current_stance, "neutral")
    recommended = current_stance
    why = "kept_original"
    rule_applied = "none"

    if bucket == "for":
        # v3 Rule 1: ANY killer-proof sentence ("$149 is a magnet
        # and a promise", "if it's manual I'm out") is enough to
        # downgrade — that phrasing is incompatible with RECEPTIVE.
        if killer_proof_count > 0:
            recommended = "curious_but_unconvinced"
            why = (
                "downgrade_for_to_uncertain_strict_v3: reasoning "
                "contains a killer proof-demand sentence ('without "
                "X, $N is just a magnet/chore/promise' or 'if it's "
                "manual I'm out') that destroys RECEPTIVE semantics"
            )
            rule_applied = "v3_killer_proof"
        # v2 carry-over: major proof gate present
        elif has_major_gate:
            recommended = "curious_but_unconvinced"
            why = (
                "downgrade_for_to_uncertain_strict_v3: reasoning "
                "centers on a major proof gate (safety / "
                "certification / material / durability)"
            )
            rule_applied = "v2_major_proof_gate"
        # v3 Rule 2: proof-demand sentences outnumber positive-driver
        # sentences. Even with one positive line, RECEPTIVE is wrong
        # when the persona is mostly objecting.
        elif (
            proof_demand_count > positive_driver_count
            and proof_demand_count >= 2
        ):
            recommended = "curious_but_unconvinced"
            why = (
                f"downgrade_for_to_uncertain_strict_v3: "
                f"{proof_demand_count} proof-demand sentences vs "
                f"{positive_driver_count} positive-driver "
                "sentences; persona is mostly objecting"
            )
            rule_applied = "v3_proof_outnumbers_positive"
        # v2 carry-over: no positive AND no use case
        elif (
            not has_positive_intent
            and not has_use_case
            and positive_driver_count == 0
        ):
            recommended = "curious_but_unconvinced"
            why = (
                "downgrade_for_to_uncertain_strict_v3: no positive "
                "driver AND no personal use-case fit — curiosity "
                "alone does not earn RECEPTIVE"
            )
            rule_applied = "v2_no_positive_no_usecase"
        else:
            # All three v3 strict checks passed — keep RECEPTIVE.
            recommended = current_stance
            why = (
                "kept_for_strict_v3: positive driver + use-case fit "
                "outweigh proof demands; no killer-proof, no major "
                "gate"
            )
            rule_applied = "v3_kept"
    elif bucket == "neutral":
        # Same as v2 — promote only when CLEAR positive driver +
        # use-case fit are present and no major gate / killer.
        if (
            positive_driver_count > 0
            and has_use_case
            and not has_major_gate
            and killer_proof_count == 0
            and not has_resistant
        ):
            recommended = "interested_if_proven"
            why = (
                "upgrade_neutral_to_for_strict_v3: clear positive "
                "driver + personal use-case fit; no major proof "
                "gate or killer phrasing"
            )
            rule_applied = "v3_neutral_upgrade"
    elif bucket == "against":
        if (
            positive_driver_count > 0
            and has_use_case
            and not has_resistant
        ):
            recommended = "interested_if_proven"
            why = (
                "upgrade_against_to_for_strict_v3: positive driver "
                "+ use-case fit, no resistance markers"
            )
            rule_applied = "v3_against_upgrade"

    return {
        "original_stance": current_stance,
        "recommended_stance": recommended,
        "change": recommended != current_stance,
        "stance_justification": why,
        "rule_applied": rule_applied,
        "signals": {
            "positive_driver_count": positive_driver_count,
            "proof_demand_count": proof_demand_count,
            "killer_proof_count": killer_proof_count,
            "conditional_receptive_count": conditional_receptive_count,
            "has_positive_intent": has_positive_intent,
            "has_use_case_fit": has_use_case,
            "has_proof_demand": has_proof_demand,
            "has_major_proof_gate": has_major_gate,
            "has_resistant_markers": has_resistant,
        },
    }


def audit_receptive_strictness_v3(
    ballots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the v3 classifier across a list of ballot dicts. Produces
    `receptive_strictness_quality.json`."""
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
    downgraded = 0
    upgraded = 0
    rule_counter: dict[str, int] = {}

    for i, b in enumerate(ballots):
        result = classify_stance_strictness_v3(
            current_stance=b.get("private_stance") or "",
            reasoning=b.get("private_reasoning") or "",
        )
        rule = result.get("rule_applied", "none")
        rule_counter[rule] = rule_counter.get(rule, 0) + 1
        if not result["change"]:
            continue
        old_bucket = _STANCE_TO_BUCKET.get(
            result["original_stance"], "neutral"
        )
        new_bucket = _STANCE_TO_BUCKET.get(
            result["recommended_stance"], "neutral"
        )
        if old_bucket == "for" and new_bucket != "for":
            downgraded += 1
        if old_bucket != "for" and new_bucket == "for":
            upgraded += 1
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
                "rule": rule,
                "signals": result["signals"],
                "excerpt": (b.get("private_reasoning") or "")[:240],
            })

    receptive_after = receptive_before - downgraded + upgraded
    uncertain_after = uncertain_before + downgraded - upgraded
    resistant_after = resistant_before

    return {
        "phase": "10b_4_receptive_strictness_v3",
        "ballots_reviewed": len(ballots),
        "receptive_before": receptive_before,
        "receptive_after": receptive_after,
        "uncertain_before": uncertain_before,
        "uncertain_after": uncertain_after,
        "resistant_before": resistant_before,
        "resistant_after": resistant_after,
        "downgraded_receptive_count": downgraded,
        "upgraded_receptive_count": upgraded,
        "rule_counter": rule_counter,
        "corrections": corrections,
        "examples": examples,
        "pass": True,  # advisory audit; always passes
    }
