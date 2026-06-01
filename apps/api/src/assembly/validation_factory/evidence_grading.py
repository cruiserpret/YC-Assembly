"""Phase 15J — evidence grading + anti-masquerade gates.

REUSES the Phase 15C action-signal taxonomy (``SIGNAL_TIERS`` / ``TIER*_SIGNALS``
/ ``classify_action_signal``) — it never redefines tiers. Its job is to stop weak
evidence from being graded as strong: a Tier-3/Tier-4 candidate must not claim a
Tier-1/Tier-2 (revealed-action) grade, and a Tier-1/2 signal must be backed by a
citation + a positive count. Pure, deterministic, no LLM/network/DB.
"""
from __future__ import annotations

from assembly.market_calibration.action_signals import (
    SIGNAL_TIERS,
    classify_action_signal,
)
from assembly.validation_factory.candidate_schema import CandidateCase


def signal_tiers(candidate: CandidateCase) -> list[int]:
    """The classifiable tier of each action_signal_candidate (skips unknown)."""
    out: list[int] = []
    for s in candidate.action_signal_candidates:
        t = classify_action_signal(s)
        if t is not None:
            out.append(int(t))
    return out


def strongest_supported_tier(candidate: CandidateCase) -> int | None:
    """The strongest (numerically smallest) tier the candidate's action signals
    justify, or None if it has no classifiable action signal."""
    tiers = signal_tiers(candidate)
    return min(tiers) if tiers else None


def recommended_evidence_tier(candidate: CandidateCase) -> int | None:
    """The evidence tier a reviewer should assign by default — the strongest tier
    the candidate's signals support."""
    return strongest_supported_tier(candidate)


def validate_evidence_tier(candidate: CandidateCase) -> list[str]:
    """Anti-masquerade: the assigned ``evidence_tier`` may not claim STRONGER
    evidence (a smaller tier number) than the candidate's best action signal."""
    issues: list[str] = []
    assigned = candidate.evidence_tier
    if assigned is None:
        return issues  # presence is enforced by the promotion gate, not here
    strongest = strongest_supported_tier(candidate)
    if strongest is None:
        if int(assigned) <= 2:
            issues.append(
                f"evidence_tier={assigned} claims Tier-1/2 (revealed-action) evidence "
                "but the candidate has no Tier-1/2 action_signal_candidate to support "
                "it — weak evidence must not masquerade as strong"
            )
    elif int(assigned) < strongest:
        issues.append(
            f"evidence_tier={assigned} claims stronger evidence than the candidate's "
            f"best action signal supports (strongest supported tier is {strongest}) — "
            "Tier-3/4 evidence must not masquerade as Tier-1/2"
        )
    return issues


def validate_tier_consistency(candidate: CandidateCase) -> list[str]:
    """Each action signal's tier must match the canonical taxonomy; an UNKNOWN
    signal_type may not self-declare a Tier-1/2 (revealed-action) tier."""
    issues: list[str] = []
    for i, s in enumerate(candidate.action_signal_candidates):
        canonical = SIGNAL_TIERS.get(s.signal_type)
        if canonical is not None:
            if s.tier is not None and int(s.tier) != int(canonical):
                issues.append(
                    f"action_signal[{i}] {s.signal_type!r}: tier {s.tier} != canonical "
                    f"taxonomy tier {canonical}"
                )
        elif s.tier is not None and int(s.tier) <= 2:
            issues.append(
                f"action_signal[{i}] {s.signal_type!r}: an unknown signal_type may not "
                f"declare Tier-{s.tier} (revealed action) — only the canonical "
                "TIER1/TIER2 taxonomy confers an action tier"
            )
    return issues


def validate_tier1_evidence(candidate: CandidateCase) -> list[str]:
    """No phantom action evidence: a Tier-1 or Tier-2 signal must carry a
    ``source_reference`` and a positive ``count`` (revealed action needs a
    citation and a magnitude)."""
    issues: list[str] = []
    for i, s in enumerate(candidate.action_signal_candidates):
        t = classify_action_signal(s)
        if t is None or int(t) > 2:
            continue
        if not (s.source_reference or "").strip():
            issues.append(
                f"action_signal[{i}] {s.signal_type!r} (Tier-{t}): requires a "
                "source_reference — no unverifiable action claims"
            )
        if s.count is None or float(s.count) <= 0:
            issues.append(
                f"action_signal[{i}] {s.signal_type!r} (Tier-{t}): requires a positive "
                "count"
            )
    return issues


def tier_composition(candidate: CandidateCase) -> dict[str, int]:
    """Per-tier counts of the candidate's action signals (for the dashboard)."""
    comp = {"tier1": 0, "tier2": 0, "tier3": 0, "tier4": 0, "unclassified": 0}
    for s in candidate.action_signal_candidates:
        t = classify_action_signal(s)
        if t is None:
            comp["unclassified"] += 1
        else:
            comp[f"tier{int(t)}"] += 1
    return comp
