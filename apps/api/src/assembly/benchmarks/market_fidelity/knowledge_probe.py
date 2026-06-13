"""Phase 17C — base-model outcome-knowledge probe.

Open-weight does NOT automatically mean blind: a base model can already KNOW a
retrospective outcome from its pretrained weights. This module builds probe questions
and assesses (from supplied answers — no model is called here in 17C) whether the
model appears to already know the outcome. If it does, the retrospective result is
NOT public claim-grade (see eligibility.py / blindness.py). Pure; deterministic.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from assembly.benchmarks.market_fidelity.canonicalize import canonical_bytes
from assembly.benchmarks.market_fidelity.hash_lock import sha256_hex

MemorizationRisk = Literal["low", "medium", "high", "unknown"]

_PROBE_TEMPLATES = (
    "What happened to {subject}?",
    "How much did {subject} raise?",
    "Did {subject} succeed or fail?",
    "How many backers / customers / users did {subject} get?",
    "What was the final outcome of {subject}?",
)


def build_probe_questions(subject: str) -> list[str]:
    """The outcome-knowledge probe questions for a case subject (product/company/
    campaign). These are ASKED BEFORE showing the model the case, to detect prior
    memorization."""
    return [t.format(subject=subject) for t in _PROBE_TEMPLATES]


class KnowledgeProbeResult(BaseModel):
    """A probe artifact for one (model, case). ``model_answers`` are supplied by the
    operator/test fixture in 17C (no model is called). ``probe_hash`` commits it."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    case_id: str
    subject: str
    probe_questions: list[str]
    model_answers: list[str] = []
    outcome_markers: list[str] = []  # known outcome strings to look for in answers
    outcome_knowledge_detected: bool | Literal["unknown"] = "unknown"
    memorization_risk: MemorizationRisk = "unknown"
    notes: str = ""
    probe_hash: str = ""


def assess_probe(
    *,
    model_id: str,
    case_id: str,
    subject: str,
    model_answers: Sequence[str],
    outcome_markers: Sequence[str],
) -> KnowledgeProbeResult:
    """Deterministically assess whether the answers reveal outcome knowledge. If any
    answer contains an outcome marker -> detected/high risk. If the model only
    disclaims ("I don't know", "no information") -> not detected/low risk. Otherwise
    unknown/medium (a human should review)."""
    questions = build_probe_questions(subject)
    answers = [str(a) for a in model_answers]
    markers = [m for m in outcome_markers if m]
    joined = " \n ".join(answers).lower()

    detected: bool | str
    risk: MemorizationRisk
    if markers and any(m.lower() in joined for m in markers):
        detected, risk = True, "high"
        note = "an answer contains a known outcome marker — the model likely knows the outcome"
    elif answers and all(
        any(p in a.lower() for p in ("i don't know", "i do not know", "no information",
                                     "not aware", "cannot find", "unknown", "i'm not sure"))
        for a in answers
    ):
        detected, risk = False, "low"
        note = "the model disclaimed knowledge on every probe"
    elif not answers:
        detected, risk = "unknown", "unknown"
        note = "no probe answers supplied"
    else:
        detected, risk = "unknown", "medium"
        note = "answers neither disclaim nor match a known marker — human review required"

    result = KnowledgeProbeResult(
        model_id=model_id, case_id=case_id, subject=subject,
        probe_questions=questions, model_answers=answers, outcome_markers=markers,
        outcome_knowledge_detected=detected, memorization_risk=risk, notes=note,
    )
    payload = result.model_dump(mode="json", exclude={"probe_hash"})
    result.probe_hash = sha256_hex(canonical_bytes(payload))
    return result


def probe_blocks_public_claim(result: Mapping | KnowledgeProbeResult) -> bool:
    """True if this probe result means the retrospective case CANNOT be public
    claim-grade (outcome knowledge detected, or memorization risk high)."""
    d = result.model_dump() if isinstance(result, KnowledgeProbeResult) else dict(result)
    return d.get("outcome_knowledge_detected") is True or d.get("memorization_risk") == "high"
