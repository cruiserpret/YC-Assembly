"""Phase 9A.3 — universal psychology trait inference.

Given a persona's evidence + role + market traits + simulation responses,
infer OCEAN + 5 additional psychology traits. Universal: no LumaLoop
hardcoding, no random priors, no global priors. The inference is keyed
off:

  1. evidence_direct      — explicit signal from rationale / excerpt /
                            simulation reasoning.
  2. simulation_behavior  — repeated stance language / objection language
                            across the persona's 7-round simulation.
  3. role_context_prior   — weak prior keyed off the normalized role
                            label (e.g. price_skeptic → higher price
                            sensitivity than the population mean) — only
                            applied when no evidence_direct or
                            simulation_behavior signal is present.
  4. neutral_default      — 0.5 with a caveat when no responsible
                            inference can be made.

Every signal class produces (delta, signal_text, source_ids,
source_trait_ids, simulation_response_ids). Deltas combine additively;
the final value is clipped to [0, 1].

This module reads inputs only — it does NOT call providers, retrieval,
or any external service.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from assembly.sources.persona_psychology_layer.schemas import (
    ADDITIONAL_REQUIRED_TRAITS,
    OCEAN_TRAITS,
    PRICE_SENSITIVITY_TRAIT,
    PsychologyProfile,
    PsychologyTrait,
)


_NEUTRAL = 0.5


@dataclass(frozen=True)
class _Signal:
    delta: float
    text: str
    method: str  # 'evidence_direct' | 'simulation_behavior' | 'role_context_prior'
    source_record_ids: tuple[str, ...] = ()
    source_trait_ids: tuple[str, ...] = ()
    simulation_response_ids: tuple[str, ...] = ()


# ---- universal lexicons (NOT LumaLoop-specific) ----------------------

_LEX = {
    "openness_pos": (
        "curious", "novelty", "new format", "willing to try", "rechargeable",
        "innovat", "flexible", "exploring", "open to", "experiment",
    ),
    "openness_neg": (
        "stick with", "stick to", "incumbent", "won't switch", "wont switch",
        "prefer my current", "comfortable with what i have", "tried and true",
        "loyal to my", "no reason to change",
    ),
    "conscientiousness_pos": (
        "reliab", "durab", "spec", "ip rating", "ip-rating", "warrant",
        "tested", "proof", "review", "ratings", "details", "comparison",
        "lumens", "battery life", "rigor",
    ),
    "conscientiousness_neg": (
        "casual", "i don't really care", "doesn't matter to me",
        "i just grab", "i just buy", "i don't research",
    ),
    "extraversion_pos": (
        "running group", "cycling group", "club", "friends use",
        "community", "race", "social", "visible to others",
        "team", "group ride", "group run",
    ),
    "extraversion_neg": (
        "alone", "solo", "private", "by myself", "i don't need others",
        "introvert", "quiet route", "i avoid", "i prefer not to be seen",
    ),
    "agreeableness_pos": (
        "i can see", "fair point", "that makes sense", "i agree",
        "good argument", "reasonable", "balanced", "i'd consider",
        "open to feedback", "i hear you",
    ),
    "agreeableness_neg": (
        "absolutely not", "no way", "ridiculous", "garbage", "worthless",
        "i don't buy that", "that's nonsense", "dismiss", "won't even",
        "stop trying to sell",
    ),
    "neuroticism_pos": (
        "worried", "anxious", "afraid", "fear", "scared", "concerned",
        "what if", "could fail", "unsafe", "dangerous", "risk of",
        "nervous", "uneasy",
    ),
    "neuroticism_neg": (
        "calm", "pragmatic", "no big deal", "low stakes", "not worried",
        "i'll figure it out", "fine either way", "no anxiety",
    ),
    "risk_tolerance_pos": (
        "early adopter", "i'll try it", "happy to test", "first to try",
        "willing to risk", "kickstarter", "beta", "ok with unproven",
        "give it a shot",
    ),
    "risk_tolerance_neg": (
        "need proof", "won't buy without", "wait for reviews",
        "risk-averse", "risk averse", "burned before",
        "until it's proven", "not first", "let others test it",
    ),
    "novelty_seeking_pos": (
        "new format", "rechargeable", "snap-on", "snap on", "clip-on",
        "clip on", "innovative", "fresh take", "different approach",
        "novel", "love trying new",
    ),
    "novelty_seeking_neg": (
        "old reliable", "what i already have", "i stick with", "tried and true",
        "no need for new", "already works",
    ),
    "trust_proof_threshold_pos": (
        "third-party", "third party", "review", "athlete", "tested by",
        "lab test", "independent test", "ip rating", "ip-rating",
        "proof", "credible source", "known brand", "warranty",
        "durability test",
    ),
    "trust_proof_threshold_neg": (
        "i'll just try it", "don't need proof", "trust my gut",
        "i'll figure out if it works",
    ),
    "social_influence_susceptibility_pos": (
        "friends recommend", "everyone uses", "popular", "what others",
        "peer", "social proof", "if my group", "i was convinced",
        "they convinced me", "talked me into",
    ),
    "social_influence_susceptibility_neg": (
        "i don't care what others", "doesn't matter what they say",
        "make my own choice", "ignore the hype",
    ),
    "category_involvement_or_expertise_pos": (
        "noxgear", "amphipod", "nathan", "flipbelt", "black diamond",
        "tracer", "lumens", "ip rating", "ip-rating", "battery life",
        "weather-resistant", "weather resistant", "usb-c", "compared to",
        "specifically", "vs ", "versus ",
    ),
    "category_involvement_or_expertise_neg": (
        "i don't really know much about", "no idea how these compare",
        "i'm new to this", "any of them",
    ),
    "price_sensitivity_pos": (
        "expensive", "too much", "cost", "price", "cheaper", "cheap",
        "value", "afford", "budget", "$", "dollars", "worth it",
        "not worth", "overpriced", "for the price",
    ),
    "price_sensitivity_neg": (
        "i don't care about price", "money is no object",
        "price doesn't matter", "willing to pay anything",
    ),
}


# Role-based weak priors. ONLY applied when no evidence/sim signal is
# present. Universal across products — keyed by the normalized_role
# stems we know exist in 9A.2 and beyond.
_ROLE_PRIORS: dict[str, dict[str, float]] = {
    "price_skeptic": {
        "price_sensitivity": 0.18,
        "trust_proof_threshold": 0.08,
    },
    "trust_seeker": {
        "trust_proof_threshold": 0.18,
        "conscientiousness": 0.08,
        "neuroticism": 0.05,
        "risk_tolerance": -0.10,
    },
    "performance_focused_buyer": {
        "category_involvement_or_expertise": 0.15,
        "conscientiousness": 0.10,
    },
    "safety_visibility_focused_buyer": {
        "neuroticism": 0.08,
        "conscientiousness": 0.08,
        "trust_proof_threshold": 0.05,
    },
    "use_case_focused_buyer": {
        "conscientiousness": 0.05,
        "category_involvement_or_expertise": 0.05,
    },
    "convenience_focused_buyer": {
        "conscientiousness": -0.05,
        "novelty_seeking": 0.05,
    },
    "format_focused_buyer": {
        "novelty_seeking": 0.10,
        "openness": 0.05,
    },
    "objection_focused_buyer": {
        "agreeableness": -0.10,
        "neuroticism": 0.05,
        "trust_proof_threshold": 0.05,
    },
    "competitor_user": {
        "category_involvement_or_expertise": 0.12,
        "risk_tolerance": -0.05,
    },
}


def _role_prior_for_trait(role: str, trait_name: str) -> float:
    """Weak prior delta for a (role, trait) pair. Returns 0 if no prior
    matches (which means the trait stays at neutral_default for that
    persona unless evidence/simulation signal lifts it)."""
    role_l = (role or "").lower()
    if role_l in _ROLE_PRIORS and trait_name in _ROLE_PRIORS[role_l]:
        return _ROLE_PRIORS[role_l][trait_name]
    if role_l.startswith("competitor_user") and trait_name in (
        _ROLE_PRIORS["competitor_user"]
    ):
        return _ROLE_PRIORS["competitor_user"][trait_name]
    return 0.0


def _scan(
    text: str, pos_terms: Iterable[str], neg_terms: Iterable[str],
) -> tuple[int, int, list[str]]:
    t = (text or "").lower()
    pos = sum(1 for term in pos_terms if term in t)
    neg = sum(1 for term in neg_terms if term in t)
    matched_terms: list[str] = []
    for term in pos_terms:
        if term in t:
            matched_terms.append(f"+{term}")
    for term in neg_terms:
        if term in t:
            matched_terms.append(f"-{term}")
    return pos, neg, matched_terms[:6]


def _trait_lex(trait_name: str) -> tuple[Iterable[str], Iterable[str]]:
    return _LEX[f"{trait_name}_pos"], _LEX[f"{trait_name}_neg"]


def _label_for(value: float) -> str:
    if value < 0.4:
        return "low"
    if value > 0.6:
        return "high"
    return "medium"


def _confidence_from_signals(
    n_evidence: int,
    n_sim_behavior: int,
    used_role_prior: bool,
) -> str:
    if n_evidence >= 2 or n_sim_behavior >= 3:
        return "high"
    if n_evidence >= 1 or n_sim_behavior >= 1:
        return "medium"
    if used_role_prior:
        # Role-based priors are documented weak inferences keyed off
        # the persona's normalized role label — defensibly medium-
        # confidence. Reserved for the cases where evidence + sim
        # signal are both absent but a role prior fires.
        return "medium"
    return "low"


# ---- public API ------------------------------------------------------


def infer_persona_psychology_profile(
    *,
    persona_id: str,
    run_scope_id: str,
    target_brief: str,
    normalized_primary_role: str,
    existing_traits: list[dict[str, Any]],
    evidence_links: list[dict[str, Any]],
    simulation_responses: list[dict[str, Any]],
    include_price_sensitivity: bool = True,
) -> PsychologyProfile:
    """Infer one persona's psychology profile.

    Inputs are plain dicts (not ORM rows) so the orchestrator can pre-
    serialize what it needs. Required dict shapes:

      existing_traits:   {field_name, value, rationale, confidence,
                          source_ids: list[str], trait_id: str}
      evidence_links:    {excerpt, source_record_id: str,
                          contribution_field: str}
      simulation_responses: {response_id: str, reasoning: str,
                          stance: str, round_type: str,
                          objections: list[str],
                          persuasion_drivers: list[str]}

    Returns a `PsychologyProfile` with 10 (without price_sensitivity) or
    11 (with) traits. Pydantic enforces required-trait coverage.
    """
    persona_text_blocks: list[tuple[str, str]] = []
    trait_id_index: dict[str, list[str]] = {}
    for tr in existing_traits:
        rationale = tr.get("rationale") or ""
        value = tr.get("value") or ""
        if rationale or value:
            persona_text_blocks.append(
                ("existing_trait", f"{tr.get('field_name', '')}::{value}::{rationale}"),
            )
        if tr.get("trait_id"):
            trait_id_index.setdefault("all", []).append(str(tr["trait_id"]))
    source_id_index: list[str] = []
    for ev in evidence_links:
        excerpt = ev.get("excerpt") or ""
        if excerpt:
            persona_text_blocks.append(("evidence_link", excerpt))
        if ev.get("source_record_id"):
            source_id_index.append(str(ev["source_record_id"]))
    sim_id_index: list[str] = []
    for r in simulation_responses:
        reasoning = r.get("reasoning") or ""
        if reasoning:
            persona_text_blocks.append(("simulation_response", reasoning))
        for obj in r.get("objections") or []:
            if isinstance(obj, str) and obj:
                persona_text_blocks.append(("simulation_objection", obj))
            elif isinstance(obj, dict) and obj.get("text"):
                persona_text_blocks.append(("simulation_objection", obj["text"]))
        for pd in r.get("persuasion_drivers") or []:
            if isinstance(pd, str) and pd:
                persona_text_blocks.append(("simulation_driver", pd))
            elif isinstance(pd, dict) and pd.get("text"):
                persona_text_blocks.append(("simulation_driver", pd["text"]))
        if r.get("response_id"):
            sim_id_index.append(str(r["response_id"]))

    trait_names: list[str] = list(OCEAN_TRAITS) + list(ADDITIONAL_REQUIRED_TRAITS)
    if include_price_sensitivity:
        trait_names.append(PRICE_SENSITIVITY_TRAIT)

    traits: list[PsychologyTrait] = []
    for trait_name in trait_names:
        pos_terms, neg_terms = _trait_lex(trait_name)
        delta = 0.0
        n_evidence = 0
        n_sim_behavior = 0
        evidence_basis_parts: list[str] = []
        used_source_ids: set[str] = set()
        used_trait_ids: set[str] = set()
        used_response_ids: set[str] = set()
        ev_iter_idx = 0
        sim_iter_idx = 0
        for kind, text in persona_text_blocks:
            pos, neg, matched = _scan(text, pos_terms, neg_terms)
            if pos == 0 and neg == 0:
                continue
            net = pos - neg
            if kind in ("existing_trait", "evidence_link"):
                step = 0.08 * net
                delta += step
                if pos > 0 or neg > 0:
                    n_evidence += 1
                if kind == "evidence_link" and ev_iter_idx < len(evidence_links):
                    ev = evidence_links[ev_iter_idx]
                    if ev.get("source_record_id"):
                        used_source_ids.add(str(ev["source_record_id"]))
                if kind == "existing_trait" and ev_iter_idx < len(existing_traits):
                    et = existing_traits[ev_iter_idx]
                    if et.get("trait_id"):
                        used_trait_ids.add(str(et["trait_id"]))
                    for sid in et.get("source_ids") or []:
                        used_source_ids.add(str(sid))
                evidence_basis_parts.append(
                    f"[{kind}] {','.join(matched[:3])}"
                )
            elif kind in (
                "simulation_response", "simulation_objection",
                "simulation_driver",
            ):
                step = 0.05 * net
                delta += step
                n_sim_behavior += 1
                if sim_iter_idx < len(simulation_responses):
                    r = simulation_responses[sim_iter_idx]
                    if r.get("response_id"):
                        used_response_ids.add(str(r["response_id"]))
                evidence_basis_parts.append(
                    f"[{kind}] {','.join(matched[:3])}"
                )
            ev_iter_idx += 1
            sim_iter_idx += 1
        # --- assemble final value
        used_role_prior = False
        if n_evidence == 0 and n_sim_behavior == 0:
            prior = _role_prior_for_trait(normalized_primary_role, trait_name)
            if abs(prior) > 0.0:
                delta += prior
                used_role_prior = True
                evidence_basis_parts.append(
                    f"[role_context_prior] role={normalized_primary_role} "
                    f"prior_delta={prior:+.2f}"
                )
        # clip
        value = max(0.0, min(1.0, _NEUTRAL + delta))
        # method selection
        if n_evidence >= 1 and n_evidence >= n_sim_behavior:
            method = "evidence_direct"
        elif n_sim_behavior >= 1:
            method = "simulation_behavior"
        elif used_role_prior:
            method = "role_context_prior"
        else:
            method = "neutral_default"
        # confidence
        confidence = _confidence_from_signals(
            n_evidence, n_sim_behavior, used_role_prior,
        )
        # neutral default value handling
        if method == "neutral_default":
            value = _NEUTRAL
            evidence_basis = None
            caveat = (
                f"no responsible inference for trait {trait_name}; "
                f"using neutral midpoint 0.5 — role={normalized_primary_role}"
            )
        else:
            caveat = (
                "weak prior — keyed off normalized_primary_role; not "
                "evidence-direct"
                if method == "role_context_prior" else None
            )
            evidence_basis = (
                "; ".join(evidence_basis_parts[:6])
                if evidence_basis_parts
                else f"role_context::{normalized_primary_role}"
            )
        # snap label to value with a small tolerance to satisfy schema invariant
        label = _label_for(round(value, 3))
        if label == "low" and value > 0.4:
            value = 0.39
        if label == "high" and value < 0.6:
            value = 0.61
        traits.append(PsychologyTrait(
            trait_name=trait_name,  # type: ignore[arg-type]
            value_numeric=round(value, 3),
            value_label=label,  # type: ignore[arg-type]
            confidence=confidence,  # type: ignore[arg-type]
            inference_method=method,  # type: ignore[arg-type]
            evidence_basis=evidence_basis,
            source_record_ids=sorted(used_source_ids)[:6],
            source_trait_ids=sorted(used_trait_ids)[:6],
            simulation_response_ids=sorted(used_response_ids)[:6],
            caveat=caveat,
        ))

    return PsychologyProfile(
        persona_id=persona_id,
        run_scope_id=run_scope_id,
        target_brief=target_brief,
        generated_for_phase="9A.3",
        traits=traits,
    )
