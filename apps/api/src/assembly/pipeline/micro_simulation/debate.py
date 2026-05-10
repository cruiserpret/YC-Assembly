"""Phase 8.2K — optional pairwise debate (≤1 turn each direction).

Used by the runner only when there are ≥2 persona states. Each turn:
the speaker produces ONE argument citing one of their bound evidence
excerpts; the target's stance updates iff the LLM's response carries a
canonical-enum value.

This module makes ONE LLM call per direction; on a malformed
`target_stance_after`, ONE additional repair call fires (Phase 8.2K.1
hardening). Output passes through the same forbidden-language scanner.

Phase 8.2K.1 hardening:
  * The system prompt enumerates the closed stance enum literally;
    the LLM is told "EXACTLY one of these literal lowercase strings".
  * On invalid `target_stance_after`, the runner makes one more call
    explicitly listing the allowed values and quoting the offending
    response. If the second call also fails, the turn is marked
    `output_audit_passed=False` with explicit notes; the target's
    stance is preserved (no silent coercion to a different value).
  * Forbidden-language scanning is unchanged.
"""
from __future__ import annotations

from textwrap import dedent
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.pipeline.micro_simulation.llm_call import (
    STAGE_DEBATE,
    micro_llm_call,
)
from assembly.pipeline.micro_simulation.output_audit import audit_debate_turn
from assembly.pipeline.micro_simulation.rounds import _strip_json_fences
from assembly.pipeline.micro_simulation.schemas import (
    MicroDebateTurn,
    MicroPersonaState,
    MicroStance,
)


_STANCE_LITERAL_LIST = ", ".join(f'"{s.value}"' for s in MicroStance)


_DEBATE_SYSTEM_PROMPT = dedent("""
    You are running ONE pairwise debate turn in a MICRO-TEST harness.
    The speaker is presenting their argument to the target. Both
    personas are anonymous, source-grounded individuals. This is NOT
    a market simulation.

    Output ONE JSON object with these EXACT keys (no others):
        argument               (string ≤ 500 chars; speaker's voice)
        cited_evidence_excerpt (string OR null; quote from speaker's
                                bound evidence)
        target_stance_after    (EXACTLY one of these literal
                                lowercase strings, with no extra
                                punctuation, capitalization, prefixes,
                                suffixes, or commentary:
                                {stance_list})

    Rules for `target_stance_after`:
      * Pass the value VERBATIM. No quotes around the value other than
        the JSON string quotes themselves.
      * Do NOT add words like "stance", "still", "(no shift)", or any
        parenthetical.
      * Do NOT capitalize. The values are all lowercase with
        underscores.
      * Do NOT invent new stance labels.

    NO forecast / verdict / build-kill-pivot / market-reaction / %-
    adoption / "society thinks" / "tiny_ready" language. The
    forbidden-language scanner will reject your reply if any appears.
    """).strip().format(stance_list=_STANCE_LITERAL_LIST)


class _LLMDebateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argument: str
    cited_evidence_excerpt: str | None = None
    target_stance_after: str


def _build_user_prompt(
    *, speaker: MicroPersonaState, target: MicroPersonaState,
) -> str:
    speaker_traits = "\n".join(
        f"  - {k}: {v}" for k, v in speaker.supported_traits.items()
    )
    target_traits = "\n".join(
        f"  - {k}: {v}" for k, v in target.supported_traits.items()
    )
    return dedent("""
        Speaker: {sname} (relevance={srel}, category={scat},
                          stance={sstance})
        Speaker source-bound traits:
        {strait}

        Target:  {tname} (relevance={trel}, category={tcat},
                          stance={tstance})
        Target source-bound traits:
        {ttrait}

        Speaker presents one argument to target. Emit the JSON
        described in the system prompt. The argument must cite the
        speaker's own bound evidence; never invent a quote.
        target_stance_after MUST be EXACTLY one of: {stance_list}.
        """).strip().format(
            sname=speaker.display_name, srel=speaker.relevance_label.value,
            scat=speaker.matched_category_key,
            sstance=speaker.current_stance.value,
            strait=speaker_traits or "  (none)",
            tname=target.display_name, trel=target.relevance_label.value,
            tcat=target.matched_category_key,
            tstance=target.current_stance.value,
            ttrait=target_traits or "  (none)",
            stance_list=_STANCE_LITERAL_LIST,
        )


def _parse_debate_response(raw: str) -> tuple[
    _LLMDebateOut | None, MicroStance | None, str | None,
]:
    """Returns (payload | None, parsed_stance | None, parse_error_note).

    `parsed_stance is None` means the response either failed to parse
    as JSON or carried a stance value not in `MicroStance`.
    """
    try:
        payload = _LLMDebateOut.model_validate_json(_strip_json_fences(raw))
    except Exception as e:
        return None, None, (
            f"JSON parse failed: {type(e).__name__}: {str(e)[:200]}"
        )
    try:
        stance = MicroStance(payload.target_stance_after)
    except ValueError:
        return payload, None, (
            f"target_stance_after={payload.target_stance_after!r} not in "
            f"closed stance enum"
        )
    return payload, stance, None


async def run_debate_turn(
    *,
    speaker: MicroPersonaState,
    target: MicroPersonaState,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    provider: LLMProvider,
    model: str | None = None,
) -> MicroDebateTurn:
    """Run one pairwise debate turn with stance-enum repair loop.

    Flow:
      1. First call with the hardened system prompt.
      2. Parse `target_stance_after` against `MicroStance`.
      3. If invalid, ONE repair call: include the offending value +
         the allowed list verbatim in a new user message; ask for the
         JSON object alone.
      4. If repair also returns an invalid value, mark the turn
         `output_audit_passed=False`, preserve target's current stance
         (no silent coercion to a different value), and surface both
         attempts' errors in `output_audit_notes`.
      5. Forbidden-language scan runs over the final argument
         regardless. A hit flips `output_audit_passed=False`.
    """
    user_prompt = _build_user_prompt(speaker=speaker, target=target)
    audit_notes: list[str] = []

    # ---- Attempt 1 ---------------------------------------------------
    response_1 = await micro_llm_call(
        sessionmaker=sessionmaker, simulation_id=simulation_id,
        stage=STAGE_DEBATE, provider=provider, model=model,
        messages=[
            LLMMessage(role="system", content=_DEBATE_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ],
    )
    payload_1, stance_1, err_1 = _parse_debate_response(response_1.text or "")

    payload_final = payload_1
    stance_final = stance_1

    # ---- Attempt 2 (repair) — only if stance_1 invalid --------------
    if stance_1 is None:
        audit_notes.append(f"attempt_1: {err_1}")
        repair_user = dedent("""
            Your previous JSON had `target_stance_after={prev_value!r}`,
            which is NOT in the closed stance enum.

            EMIT THE JSON OBJECT AGAIN. The argument and
            cited_evidence_excerpt may be re-emitted unchanged or
            adjusted, but `target_stance_after` MUST be EXACTLY one
            of these lowercase literal strings, verbatim, with no
            additional words, punctuation, or commentary:

                {stance_list}

            Re-emit only the JSON object. Do not apologize, do not
            explain, do not wrap in markdown.
            """).strip().format(
                prev_value=(
                    payload_1.target_stance_after if payload_1 is not None
                    else "<unparseable>"
                ),
                stance_list=_STANCE_LITERAL_LIST,
            )
        response_2 = await micro_llm_call(
            sessionmaker=sessionmaker, simulation_id=simulation_id,
            stage=STAGE_DEBATE, provider=provider, model=model,
            messages=[
                LLMMessage(role="system", content=_DEBATE_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_prompt),
                LLMMessage(role="user", content=repair_user),
            ],
        )
        payload_2, stance_2, err_2 = _parse_debate_response(
            response_2.text or ""
        )
        if stance_2 is not None:
            audit_notes.append("attempt_2: repair succeeded")
            payload_final = payload_2
            stance_final = stance_2
        else:
            audit_notes.append(f"attempt_2: {err_2}")
            audit_notes.append("stance-enum repair exhausted; debate marked failed")
            payload_final = payload_2 or payload_1
            stance_final = None  # remains invalid; do not coerce

    # ---- Build the result -------------------------------------------
    if stance_final is None:
        # Repair exhausted. Mark visibly failed; do NOT silently shift
        # the target stance. Argument text is preserved when available
        # so an operator can inspect what the LLM actually said.
        argument = (
            payload_final.argument[:2000]
            if payload_final is not None and payload_final.argument
            else "[MICRO-TEST] stance-enum repair exhausted; argument empty"
        )
        cited = (
            payload_final.cited_evidence_excerpt[:500]
            if (payload_final is not None
                and payload_final.cited_evidence_excerpt is not None)
            else None
        )
        interim = MicroDebateTurn(
            speaker_persona_id=speaker.persona_id,
            target_persona_id=target.persona_id,
            argument=argument,
            cited_evidence_excerpt=cited,
            target_stance_before=target.current_stance,
            target_stance_after=target.current_stance,
            output_audit_passed=False,
            output_audit_notes=audit_notes,
        )
    else:
        # Valid stance — either first attempt or successful repair.
        assert payload_final is not None
        argument = payload_final.argument[:2000]
        cited = (
            payload_final.cited_evidence_excerpt[:500]
            if payload_final.cited_evidence_excerpt is not None else None
        )
        interim = MicroDebateTurn(
            speaker_persona_id=speaker.persona_id,
            target_persona_id=target.persona_id,
            argument=argument,
            cited_evidence_excerpt=cited,
            target_stance_before=target.current_stance,
            target_stance_after=stance_final,
            output_audit_passed=True,
            output_audit_notes=audit_notes,
        )

    # ---- Forbidden-language audit (unchanged) -----------------------
    found = audit_debate_turn(interim)
    if found:
        return interim.model_copy(update={
            "output_audit_passed": False,
            "output_audit_notes": list(interim.output_audit_notes) + [
                f"forbidden language detected: {sorted(set(found))}"
            ],
        })
    return interim
