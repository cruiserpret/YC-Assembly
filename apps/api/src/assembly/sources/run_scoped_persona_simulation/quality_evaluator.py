"""Phase 8.5E — deterministic 9-dimension quality evaluator for
run-scoped simulation outputs.

Pure function. Same inputs → same scores. NO LLM, NO network.

Score range per dimension: 0.0 (failed) .. 1.0 (perfect).
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assembly.sources.run_scoped_persona_simulation.schemas import (
    MARKET_ENTRY_STANCES, RoundOutputAudit,
)
from assembly.sources.run_scoped_persona_simulation.validators import (
    scan_forecast_or_verdict_claims,
    scan_unlaunched_product_use_claims,
)


# Universal required caveats — every keyset is product-agnostic.
# Earlier StrideShield-era literals like `n=7` and `amazon` were
# replaced with broader markers (Phase 8.5G.1) so non-Amazon-sourced
# runs and non-7-persona simulations also pass the integrity check.
_REQUIRED_CAVEATS_KEYWORDS: tuple[tuple[str, ...], ...] = (
    ("micro-simulation",),               # the run shape itself
    ("n=",),                              # explicit sample size
    ("not a forecast",),
    ("not a market verdict",),
    ("not representative",),
    ("run-scoped",),                      # scope discipline
    ("synthetic",),                       # persona origin discipline
    ("unlaunched",),                      # launch-state discipline
)

ReadyState = Literal[
    "READY_FOR_FOUNDER_REPORT",
    "READY_FOR_PROMPT_FIX",
    "NOT_READY",
]


class QualityEvaluation(BaseModel):
    """9-dimension quality scoring result."""

    model_config = ConfigDict(extra="forbid")

    persona_grounding_score: float = Field(ge=0.0, le=1.0)
    competitor_comparison_score: float = Field(ge=0.0, le=1.0)
    objection_specificity_score: float = Field(ge=0.0, le=1.0)
    founder_actionability_score: float = Field(ge=0.0, le=1.0)
    caveat_integrity_score: float = Field(ge=0.0, le=1.0)
    anti_fake_claim_score: float = Field(ge=0.0, le=1.0)
    stance_validity_score: float = Field(ge=0.0, le=1.0)
    diversity_usage_score: float = Field(ge=0.0, le=1.0)
    evidence_traceability_score: float = Field(ge=0.0, le=1.0)
    aggregate_score: float = Field(ge=0.0, le=1.0)
    ready_state: ReadyState
    rationale: list[str]


def _grounding_score(
    *, agents_with_traits_count: int, total_agents: int,
    rounds: list[RoundOutputAudit],
) -> float:
    """Reward when agents reference their persisted traits / role /
    competitor / theme. Each round-output checks whether the agent's
    reasoning mentions any of the persona's evidence anchors."""
    if not rounds:
        return 0.0
    grounded = 0
    total = 0
    for r in rounds:
        total += 1
        text = (r.reasoning or "").lower()
        # Heuristic: reasoning mentions either the role's brand
        # token or a competitor name from competitor_mentions.
        role_tok = r.normalized_primary_role.lower().replace(
            "competitor_user_", "",
        ).replace("_", " ")
        if role_tok and role_tok in text:
            grounded += 1
            continue
        if any((c or "").lower() in text for c in r.competitor_mentions):
            grounded += 1
    return round(grounded / max(total, 1), 3)


def _competitor_comparison_score(
    rounds: list[RoundOutputAudit],
) -> float:
    """Reward rounds whose round_type is `competitor_comparison`
    AND whose `competitor_mentions` is non-empty. Also reward any
    round whose reasoning explicitly compares the product to a named
    competitor."""
    cc_rounds = [
        r for r in rounds if r.round_type == "competitor_comparison"
    ]
    if not cc_rounds:
        return 0.0
    with_comp = sum(1 for r in cc_rounds if r.competitor_mentions)
    return round(with_comp / max(len(cc_rounds), 1), 3)


def _objection_specificity_score(
    rounds: list[RoundOutputAudit],
) -> float:
    """Concrete objections are non-empty `objections` lists with
    short, content-rich text (>20 chars after stripping)."""
    rounds_with_objs = [
        r for r in rounds if r.round_type in (
            "objection_formation",
            "competitor_comparison",
            "first_exposure",
            "final_stance",
        )
    ]
    if not rounds_with_objs:
        return 0.0
    concrete = 0
    total_objs = 0
    for r in rounds_with_objs:
        for o in r.objections or []:
            total_objs += 1
            text = (o.get("text") or "")
            if len(text.strip()) >= 20:
                concrete += 1
    if total_objs == 0:
        return 0.0
    return round(concrete / total_objs, 3)


def _founder_actionability_score(
    rounds: list[RoundOutputAudit],
) -> float:
    """Reward when the simulation produces enough volume of distinct
    objections + persuasion levers across the run for a founder to
    act on."""
    objs: set[str] = set()
    levers: set[str] = set()
    for r in rounds:
        for o in r.objections or []:
            t = (o.get("text") or "").strip()
            if len(t) >= 15:
                objs.add(t.lower()[:80])
        for l in r.persuasion_levers or []:
            t = (l.get("text") or "").strip()
            if len(t) >= 15:
                levers.add(t.lower()[:80])
    # 0.5 from objections, 0.5 from levers; each max at 6 distinct.
    obj_part = min(len(objs) / 6.0, 1.0) * 0.5
    lev_part = min(len(levers) / 6.0, 1.0) * 0.5
    return round(obj_part + lev_part, 3)


def _caveat_integrity_score(caveats: list[str]) -> float:
    """Required caveats present and non-empty."""
    if not caveats:
        return 0.0
    blob = " | ".join(caveats).lower()
    hits = 0
    for keyword_set in _REQUIRED_CAVEATS_KEYWORDS:
        if all(k in blob for k in keyword_set):
            hits += 1
    return round(hits / len(_REQUIRED_CAVEATS_KEYWORDS), 3)


def _anti_fake_claim_score(
    *, rounds: list[RoundOutputAudit], product_name: str,
) -> float:
    """Reward = 1.0 if every round passes BOTH the launch-state
    scanner AND the forecast/verdict scanner. Penalize for each
    failing round."""
    if not rounds:
        return 0.0
    bad = 0
    for r in rounds:
        text = (r.reasoning or "") + " | " + (r.raw_text or "")
        if not scan_unlaunched_product_use_claims(
            text=text, product_name=product_name,
        ).is_valid:
            bad += 1
            continue
        if not scan_forecast_or_verdict_claims(text=text).is_valid:
            bad += 1
            continue
        # Already-stamped audit on the round
        if r.forbidden_claim_audit:
            bad += 1
    return round(max(0.0, 1.0 - bad / max(len(rounds), 1)), 3)


def _stance_validity_score(
    rounds: list[RoundOutputAudit],
) -> float:
    """Final stance must be in MARKET_ENTRY_STANCES. Reward = fraction
    of agents whose `final_stance` round produced an allowed label."""
    final_rounds = [r for r in rounds if r.round_type == "final_stance"]
    if not final_rounds:
        return 0.0
    valid = sum(
        1 for r in final_rounds
        if r.stance in MARKET_ENTRY_STANCES
    )
    return round(valid / max(len(final_rounds), 1), 3)


def _diversity_usage_score(
    rounds: list[RoundOutputAudit],
) -> float:
    """Did the personas behave differently? Reward distinct final
    stances + distinct competitor mentions across agents."""
    final_rounds = [r for r in rounds if r.round_type == "final_stance"]
    if len(final_rounds) <= 1:
        return 0.0
    distinct_stances = len({
        r.stance for r in final_rounds if r.stance
    })
    distinct_competitors: set[str] = set()
    for r in rounds:
        for c in r.competitor_mentions or []:
            distinct_competitors.add((c or "").lower())
    # Up to 3 distinct stances + up to 5 distinct competitors give
    # full credit; below that scales linearly.
    stance_part = min(distinct_stances / 3.0, 1.0) * 0.5
    comp_part = min(len(distinct_competitors) / 5.0, 1.0) * 0.5
    return round(stance_part + comp_part, 3)


def _evidence_traceability_score(
    rounds: list[RoundOutputAudit],
) -> float:
    """Every round audit must carry `agent_persona_id` +
    `compressed_candidate_id` AND a non-empty reasoning. Reward =
    fraction of rounds that satisfy all three."""
    if not rounds:
        return 0.0
    ok = 0
    for r in rounds:
        if (
            r.agent_persona_id
            and (r.reasoning or "").strip()
            and r.compressed_candidate_id is not None
        ):
            ok += 1
    return round(ok / max(len(rounds), 1), 3)


def evaluate_simulation_quality(
    *,
    rounds: list[RoundOutputAudit],
    caveats: list[str],
    product_name: str,
    agents_with_traits_count: int,
    total_agents: int,
) -> QualityEvaluation:
    """Pure deterministic scoring. Returns 9 dimensions + aggregate."""
    grounding = _grounding_score(
        agents_with_traits_count=agents_with_traits_count,
        total_agents=total_agents, rounds=rounds,
    )
    competitor = _competitor_comparison_score(rounds)
    objection = _objection_specificity_score(rounds)
    actionability = _founder_actionability_score(rounds)
    caveat = _caveat_integrity_score(caveats)
    anti_fake = _anti_fake_claim_score(
        rounds=rounds, product_name=product_name,
    )
    stance = _stance_validity_score(rounds)
    diversity = _diversity_usage_score(rounds)
    traceability = _evidence_traceability_score(rounds)
    weights = {
        "anti_fake_claim_score": 0.20,
        "stance_validity_score": 0.15,
        "caveat_integrity_score": 0.10,
        "evidence_traceability_score": 0.10,
        "persona_grounding_score": 0.10,
        "competitor_comparison_score": 0.10,
        "objection_specificity_score": 0.10,
        "diversity_usage_score": 0.075,
        "founder_actionability_score": 0.075,
    }
    scores = {
        "anti_fake_claim_score": anti_fake,
        "stance_validity_score": stance,
        "caveat_integrity_score": caveat,
        "evidence_traceability_score": traceability,
        "persona_grounding_score": grounding,
        "competitor_comparison_score": competitor,
        "objection_specificity_score": objection,
        "diversity_usage_score": diversity,
        "founder_actionability_score": actionability,
    }
    aggregate = round(
        sum(weights[k] * scores[k] for k in weights), 3,
    )

    rationale: list[str] = []
    rationale.append(
        f"aggregate={aggregate} (anti_fake={anti_fake}, "
        f"stance={stance}, caveat={caveat}, "
        f"traceability={traceability}, grounding={grounding}, "
        f"competitor={competitor}, objection={objection}, "
        f"diversity={diversity}, actionability={actionability})"
    )
    # Ready-state rules
    critical_blockers: list[str] = []
    if anti_fake < 1.0:
        critical_blockers.append("anti_fake_claim_score < 1.0")
    if stance < 0.8:
        critical_blockers.append("stance_validity_score < 0.8")
    if caveat < 0.6:
        critical_blockers.append("caveat_integrity_score < 0.6")
    if traceability < 0.8:
        critical_blockers.append("evidence_traceability_score < 0.8")

    if critical_blockers:
        ready: ReadyState = "NOT_READY"
        rationale.append(
            f"NOT_READY: critical blockers — {critical_blockers}"
        )
    elif (
        objection >= 0.6
        and grounding >= 0.5
        and competitor >= 0.6
        and actionability >= 0.5
    ):
        ready = "READY_FOR_FOUNDER_REPORT"
        rationale.append(
            "READY_FOR_FOUNDER_REPORT: critical gates pass + "
            "actionability/grounding strong."
        )
    else:
        ready = "READY_FOR_PROMPT_FIX"
        rationale.append(
            "READY_FOR_PROMPT_FIX: simulation works but at least "
            "one of {objection_specificity, persona_grounding, "
            "competitor_comparison, founder_actionability} is weak."
        )

    return QualityEvaluation(
        persona_grounding_score=grounding,
        competitor_comparison_score=competitor,
        objection_specificity_score=objection,
        founder_actionability_score=actionability,
        caveat_integrity_score=caveat,
        anti_fake_claim_score=anti_fake,
        stance_validity_score=stance,
        diversity_usage_score=diversity,
        evidence_traceability_score=traceability,
        aggregate_score=aggregate,
        ready_state=ready,
        rationale=rationale,
    )
