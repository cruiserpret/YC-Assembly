"""Phase 8.2K — round logic.

Four rounds:
  1. baseline         — deterministic; no LLM call
  2. first_exposure   — LLM call per persona
  3. objection        — LLM call per persona
  4. final_stance     — LLM call per persona

Every round produces a MicroRoundResult per persona. Output audit
runs immediately after each LLM round; rounds failing audit are
emitted with `output_audit_passed=False` so the runner can surface
the failure to the operator.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from textwrap import dedent
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.pipeline.micro_simulation.llm_call import (
    STAGE_FIRST_EXPOSURE,
    STAGE_FINAL_STANCE,
    STAGE_OBJECTION,
    micro_llm_call,
)
from assembly.pipeline.micro_simulation.output_audit import audit_round_result
from assembly.pipeline.micro_simulation.schemas import (
    MicroPersonaState,
    MicroRoundKind,
    MicroRoundResult,
    MicroStance,
)


# ---------------------------------------------------------------------------
# Baseline (deterministic)
# ---------------------------------------------------------------------------


def run_baseline_round(state: MicroPersonaState) -> MicroRoundResult:
    """Pure. Re-asserts the deterministically-derived initial stance.

    No LLM call. The persona's `initial_stance` was already computed
    in `persona_state.py::_derive_initial_stance` from its source-
    bound traits. This round just records the baseline so the trace
    has a starting point per persona.
    """
    reasoning = (
        f"Baseline stance derived deterministically from "
        f"{len(state.supported_traits)} source-bound traits. "
        f"No LLM call. MICRO-TEST mechanical baseline."
    )
    return MicroRoundResult(
        persona_id=state.persona_id,
        round_kind=MicroRoundKind.BASELINE,
        stance_before=state.initial_stance,
        stance_after=state.initial_stance,
        reasoning=reasoning,
        objections=[],
        evidence_citations=list(state.evidence_excerpts.values())[:2],
        triggered_by_evidence_excerpt=None,
        llm_call_was_used=False,
        output_audit_passed=True,
        output_audit_notes=[],
    )


# ---------------------------------------------------------------------------
# LLM-backed rounds
# ---------------------------------------------------------------------------


_STANCE_LIST = ", ".join(repr(s.value) for s in MicroStance)


# Phase 8.4B.2 — objection-specificity contract. Shared across the
# three LLM-backed rounds. The contract names anchor categories that
# the deterministic evaluator (`quality_evaluator.py`) actually scores
# as specific (`$X` price tokens, mg/g/ml/oz/cans quantities, the
# vocabulary set: caffeine, sugar, ingredient, dose, dosage,
# sweetener, flavor, stack, recall, distribution, channel,
# availability) AND honors the operator's broader anchor list (named
# competitor, switching trigger, use-case mismatch, safety concern,
# proof / review requirement). The contract also forbids fabricating
# missing product facts and bans standalone hedge phrasing.
_OBJECTION_SPECIFICITY_CONTRACT = dedent("""
    SPECIFICITY CONTRACT — every objection in this round must satisfy
    ALL of these rules:

      1. CONCRETE ANCHOR (mandatory). The objection MUST contain at
         least one of:
           - a price or dollar amount (e.g. "$3.99", "$30/mo")
           - a quantity with a unit (e.g. "200 mg", "12 oz", "3 cans")
           - a named ingredient / category term from this set:
             caffeine, sugar, sweetener, dose, dosage, ingredient
           - a flavor / taste / sweetness detail
           - a distribution / channel / availability concern (campus
             store, gas station, retailer, geography, where stocked)
           - a named competitor or substitute from the brief, plus
             WHY the product compares poorly to it
           - a recall, safety, or stimulant-stacking concern
           - a switching trigger or use-case mismatch (e.g. "if it's
             not on campus", "if the flavors I drink aren't there")
           - a proof or review requirement (e.g. "no third-party
             test", "no verified ingredient panel")

      2. EVIDENCE TIE-BACK (when the persona has evidence). Quote or
         paraphrase one of the persona's bound evidence excerpts in
         `evidence_citations`. The objection must derive from a trait
         the persona already has — never invented from nothing.

      3. NO FACT INVENTION. If the brief does not disclose caffeine
         mg, sugar grams, specific ingredients, distribution
         channels, or flavors, DO NOT make them up. Object to the
         ABSENCE of disclosure instead — phrasing like "caffeine
         load is not disclosed", "flavor list is not available",
         "ingredient panel is missing", "no recall history shown",
         or "no third-party safety data". Inventing a specific
         number, ingredient name, or flavor is forbidden.

      4. NO STANDALONE HEDGE. The objection must NOT be only a
         hedge phrase like "I'm not sure", "maybe", "I don't know",
         "might be risky", "could be a problem", or "just a regular"
         anything. Hedge phrasing is allowed ONLY when the SAME
         sentence pairs it with a concrete anchor from rule 1.
         Example (allowed): "I'm not sure this product has less
         sugar than my current go-to — sugar load matters to me."
         Example (forbidden): "Maybe it's not for me."

      5. PRESERVE FORBIDDEN-LANGUAGE RULES (already enforced by the
         system message): no forecast, no verdict, no buy-percentage,
         no society-as-singular framing.
    """).strip()


_SYSTEM_PROMPT = dedent("""
    You are running ONE round of a MICRO-TEST mechanical harness on a
    single source-grounded persona. This is NOT a market simulation,
    NOT a population study, and NOT a forecast. The output you emit
    will be checked by an automated forbidden-language scanner.

    DISCIPLINE — read carefully:

      * Your ENTIRE response is ONE JSON object. No prose, no
        commentary, no markdown fences. The first character is `{{`
        and the last character is `}}`.
      * The JSON has these EXACT keys (no others):
          stance_after        (one of: {stance_list})
          reasoning           (string, ≤ 500 chars; one paragraph;
                               persona-voice; no forecast or verdict)
          objections          (list of strings; each ≤ 200 chars)
          evidence_citations  (list of source-excerpt strings the
                               persona is citing)
          triggered_by_evidence_excerpt   (string OR null; required
                               when stance_after differs from
                               stance_before)
      * `reasoning` and every `objections` entry MUST be in the
        persona's first-person voice. NEVER write population-level
        framing ("the market", "all merchants", "X% of buyers").
      * `evidence_citations` MUST quote excerpts from the persona's
        own evidence; do NOT invent citations.
      * If you reference a number, it must be a SPECIFIC number from
        the persona's evidence ("$30/mo basic fee"), NEVER a
        forecast or %-claim about the market.

    FORBIDDEN — your reply WILL be rejected if it contains any of:

      * "will succeed", "will fail", "will dominate"
      * "X% of merchants will adopt"
      * "build it", "kill it", "pivot"
      * "verdict:", "tiny_ready"
      * "representative of the market"
      * "the society thinks"
      * any market-reaction sentiment claim

    Reasoning is one persona's voice. That's the whole frame.
    """).strip().format(stance_list=_STANCE_LIST)


def _build_user_prompt(
    *,
    state: MicroPersonaState,
    round_kind: MicroRoundKind,
    brief_summary: str,
) -> str:
    traits_block = "\n".join(
        f"  - {k}: {v}" for k, v in state.supported_traits.items()
    )
    excerpts_block = "\n".join(
        f"  - [{k}] {v[:200]}"
        for k, v in state.evidence_excerpts.items()
    )

    if round_kind is MicroRoundKind.FIRST_EXPOSURE:
        prompt = (
            "Round: FIRST_EXPOSURE. The persona has just been told "
            "about the product. Emit their first reaction.\n\n"
            "Keep `objections` short. Either emit an empty list "
            "(deferring to the OBJECTION round) OR emit AT MOST ONE "
            "objection. Any objection you emit MUST satisfy the "
            "SPECIFICITY CONTRACT below.\n\n"
            f"{_OBJECTION_SPECIFICITY_CONTRACT}\n\n"
            "Possible stance_after values: any of the closed set."
        )
    elif round_kind is MicroRoundKind.OBJECTION:
        prompt = (
            "Round: OBJECTION. The persona surfaces their SINGLE "
            "STRONGEST objection to the product.\n\n"
            "Emit `objections` as a list of length EXACTLY 1 — the "
            "one primary objection. Do NOT pad with secondary "
            "worries; the final_stance round handles closing "
            "objections.\n\n"
            f"{_OBJECTION_SPECIFICITY_CONTRACT}\n\n"
            "STANCE can stay or shift. If it shifts, supply "
            "`triggered_by_evidence_excerpt`."
        )
    elif round_kind is MicroRoundKind.FINAL_STANCE:
        prompt = (
            "Round: FINAL_STANCE. The persona is closing out their "
            "stance. Re-affirm the SINGLE strongest objection that "
            "remains, plus the strongest signal that would change "
            "their stance (if any). No new objections.\n\n"
            "Emit `objections` as a list of length EXACTLY 1 — the "
            "remaining strongest one. That objection MUST satisfy "
            "the SPECIFICITY CONTRACT below.\n\n"
            f"{_OBJECTION_SPECIFICITY_CONTRACT}"
        )
    else:
        raise ValueError(f"unsupported round kind: {round_kind}")

    return dedent("""
        --- Persona snapshot (MICRO-TEST input) ---
        display_name: {display_name}
        relevance_label: {relevance}
        matched_category: {category}
        current_stance: {current_stance}

        Source-bound traits:
        {traits_block}

        Bound evidence excerpts:
        {excerpts_block}

        --- Product brief summary ---
        {brief_summary}

        --- Round instruction ---
        {round_prompt}

        --- Output ---
        Emit the JSON described in the system message. The persona's
        stance_before is `{stance_before}`. Choose stance_after from
        the closed enum.
        """).strip().format(
            display_name=state.display_name,
            relevance=state.relevance_label.value,
            category=state.matched_category_key,
            current_stance=state.current_stance.value,
            stance_before=state.current_stance.value,
            traits_block=traits_block or "  (none)",
            excerpts_block=excerpts_block or "  (none)",
            brief_summary=brief_summary[:1500],
            round_prompt=prompt,
        )


# ---------------------------------------------------------------------------
# Pydantic schema for LLM output
# ---------------------------------------------------------------------------


class _LLMRoundOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stance_after: str
    reasoning: str
    objections: list[str] = []
    evidence_citations: list[str] = []
    triggered_by_evidence_excerpt: str | None = None


def _strip_json_fences(text: str) -> str:
    import re
    if not isinstance(text, str):
        return text  # type: ignore[unreachable]
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1].strip()
    return s


# ---------------------------------------------------------------------------
# Round runner
# ---------------------------------------------------------------------------


async def run_llm_round(
    *,
    state: MicroPersonaState,
    round_kind: MicroRoundKind,
    brief_summary: str,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    provider: LLMProvider,
    model: str | None = None,
) -> MicroRoundResult:
    """Run one LLM round on one persona. Output is parsed + audited
    in-line; the returned MicroRoundResult records everything."""
    stage_label = {
        MicroRoundKind.FIRST_EXPOSURE: STAGE_FIRST_EXPOSURE,
        MicroRoundKind.OBJECTION: STAGE_OBJECTION,
        MicroRoundKind.FINAL_STANCE: STAGE_FINAL_STANCE,
    }[round_kind]
    user_prompt = _build_user_prompt(
        state=state, round_kind=round_kind, brief_summary=brief_summary,
    )

    response = await micro_llm_call(
        sessionmaker=sessionmaker,
        simulation_id=simulation_id,
        stage=stage_label,
        messages=[
            LLMMessage(role="system", content=_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ],
        provider=provider,
        model=model,
    )
    raw = response.text or ""
    audit_notes: list[str] = []
    audit_passed = True
    stance_after = state.current_stance
    reasoning = ""
    objections: list[str] = []
    citations: list[str] = []
    triggered: str | None = None

    try:
        payload = _LLMRoundOut.model_validate_json(_strip_json_fences(raw))
        # Validate stance_after against closed enum.
        try:
            stance_after = MicroStance(payload.stance_after)
        except ValueError:
            audit_passed = False
            audit_notes.append(
                f"stance_after={payload.stance_after!r} not in closed enum; "
                "round failed audit."
            )
            stance_after = state.current_stance
        reasoning = payload.reasoning[:2000]
        objections = [o[:300] for o in payload.objections][:6]
        citations = [c[:500] for c in payload.evidence_citations][:6]
        triggered = (
            payload.triggered_by_evidence_excerpt[:500]
            if payload.triggered_by_evidence_excerpt is not None
            else None
        )
    except Exception as e:
        audit_passed = False
        audit_notes.append(
            f"LLM output failed schema parse: {type(e).__name__}: {e}"
        )
        reasoning = (
            f"[MICRO-TEST] LLM output failed parse; round result is empty."
        )

    # Stance shift requires evidence excerpt.
    if stance_after != state.current_stance and not triggered:
        audit_passed = False
        audit_notes.append(
            "stance shifted but triggered_by_evidence_excerpt was null."
        )

    # Build the result and run the forbidden-language audit.
    interim = MicroRoundResult(
        persona_id=state.persona_id,
        round_kind=round_kind,
        stance_before=state.current_stance,
        stance_after=stance_after,
        reasoning=reasoning or "[MICRO-TEST] empty reasoning",
        objections=objections,
        evidence_citations=citations,
        triggered_by_evidence_excerpt=triggered,
        llm_call_was_used=True,
        output_audit_passed=audit_passed,
        output_audit_notes=audit_notes,
    )
    found = audit_round_result(interim)
    if found:
        # Forbidden language hit — flip audit_passed and stamp the
        # categories that fired.
        return interim.model_copy(update={
            "output_audit_passed": False,
            "output_audit_notes": list(audit_notes) + [
                f"forbidden language detected: {sorted(set(found))}"
            ],
        })
    return interim
