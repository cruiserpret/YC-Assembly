"""Phase 6 — The mandatory single LLM-call helper.

Every LLM call inside the Phase 6 worker MUST go through this function.
Direct `provider.chat(...)` and `provider.structured_output(...)` calls
inside `pipeline/simulation/` are forbidden — `tests/test_no_drift.py`
greps for them and fails the suite if any appear outside this file.

This is the structural enforcement of standing entry condition O1
(every orchestrated LLM call must go through `with_cost_guard`).

The helper composes:
  1. `with_cost_guard` (Postgres row lock + cost-cap check + log row)
  2. JSON-strip + Pydantic schema validation
  3. `validate_text` sweep over every text leaf in the parsed schema
     (buyer-state-friendly profile from Phase 5.5)
  4. A repair loop that re-prompts on schema OR validator violations,
     bounded by `max_repair_attempts`

Each attempt — INCLUDING repair attempts — is its own cost-guarded LLM
call. So the cap can never be bypassed by failed validations triggering
loops.
"""
from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.errors import LLMRepairExhausted
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
)
from assembly.pipeline.aggregation.validator import (
    Violation,
    validate_text,
)

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


# Buyer-state-friendly profile from Phase 5.5: agent reasoning may
# legitimately reference dollar amounts and metric acronyms (the buyer's
# real-world vocabulary). Forecast shapes (`%`, `convert at X`), forced
# verdicts, objective sentiment, and absolute claims still fire.
_BUYER_STATE_SKIP_RULES: frozenset[str] = frozenset(
    {"num.dollar_forecast", "num.metric_acronym"}
)


class _ValidatorViolation(Exception):
    """Raised when the post-parse validator finds forbidden language. The
    repair loop catches this and re-prompts with the violations.

    The string form embeds rule_id + matched_phrase + field_path so when
    this exception bubbles up through `LLMRepairExhausted` (whose message
    truncates to 400 chars), the user-facing failure tells you exactly
    which validator rule fired and on what phrase. Without this detail
    the only diagnostic was a count, which forced a manual re-run.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        # Compact each violation into one line. The first 3 are usually
        # enough to identify the pattern; we cap at 5 to stay under the
        # 400-char truncation in LLMRepairExhausted.
        head = violations[:5]
        details = "; ".join(
            f"{v.rule_id}@{v.field_path}={v.matched_phrase!r}" for v in head
        )
        more = f" (+{len(violations) - len(head)} more)" if len(violations) > len(head) else ""
        super().__init__(
            f"output_validator: {len(violations)} forbidden-language violations: {details}{more}"
        )


async def call_llm_for_simulation(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    stage: str,
    schema: type[T],
    messages: list[LLMMessage],
    provider: LLMProvider,
    model: str | None = None,
    hard_cap_usd: Decimal | None = None,
    estimated_prompt_tokens: int = 4000,
    estimated_completion_tokens: int = 1000,
    max_repair_attempts: int = 3,
    capture_prompt_snapshot: bool = True,
    max_tokens: int = 2048,
    temperature: float = 0.4,
) -> tuple[T, LLMResponse]:
    """The single blessed LLM entry point for the Phase 6 simulation worker.

    Every LLM call inside `pipeline/simulation/` MUST go through this
    function. Direct `provider.chat(...)` / `provider.structured_output(...)`
    calls in that package are blocked by the static drift tripwire test.
    """
    current_messages = list(messages)
    last_response: LLMResponse | None = None
    last_error: Exception | None = None

    for attempt in range(max_repair_attempts + 1):
        # Phase 6.6: every attempt — including repair retries — flows through
        # the universal `cost_guarded_chat` helper. The cap cannot be bypassed
        # by repair loops; every attempt is its own logged `llm_call_log` row.
        response = await cost_guarded_chat(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            stage=stage,
            messages=current_messages,
            provider=provider,
            model=model,
            hard_cap_usd=hard_cap_usd,
            max_tokens=max_tokens,
            temperature=temperature,
            capture_prompt_snapshot=capture_prompt_snapshot,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_completion_tokens=estimated_completion_tokens,
        )
        last_response = response

        # Stage A — JSON parse + Pydantic schema validation.
        try:
            parsed = _parse_into_schema(schema, response.text)
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            if attempt >= max_repair_attempts:
                break
            current_messages = _append_repair(
                current_messages,
                bad_response=response.text,
                schema=schema,
                error=e,
            )
            logger.info(
                "call_llm.repair simulation=%s stage=%s attempt=%d kind=schema",
                simulation_id, stage, attempt,
            )
            continue

        # Stage B — output validator sweep.
        violations = _walk_and_validate(parsed)
        if violations:
            last_error = _ValidatorViolation(violations)
            if attempt >= max_repair_attempts:
                break
            current_messages = _append_repair(
                current_messages,
                bad_response=response.text,
                schema=schema,
                error=last_error,
            )
            logger.info(
                "call_llm.repair simulation=%s stage=%s attempt=%d kind=validator violations=%d",
                simulation_id, stage, attempt, len(violations),
            )
            continue

        # Both passes clean.
        return parsed, response

    # Dump the final bad response + the messages that produced it to
    # /tmp/assembly_debug/ so a post-mortem can read exactly what the LLM
    # returned. This file is overwritten per-call; the prompt_snapshot in
    # llm_call_log persists the prompt side, this captures the response.
    if last_response is not None:
        try:
            debug_dir = Path("/tmp/assembly_debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = os.getpid()
            outfile = debug_dir / f"repair_exhausted_{stage}_{simulation_id}_{ts}.json"
            outfile.write_text(
                json.dumps(
                    {
                        "stage": stage,
                        "simulation_id": str(simulation_id),
                        "model": last_response.model,
                        "violations": [
                            {
                                "rule_id": v.rule_id,
                                "field_path": v.field_path,
                                "matched_phrase": v.matched_phrase,
                                "excerpt": v.excerpt,
                                "category": v.category.value,
                            }
                            for v in (
                                last_error.violations
                                if isinstance(last_error, _ValidatorViolation)
                                else []
                            )
                        ],
                        "final_response_text": last_response.text,
                        "final_messages": [
                            {"role": m.role, "content": m.content}
                            for m in current_messages
                        ],
                    },
                    indent=2,
                    default=str,
                )
            )
            logger.warning(
                "call_llm.repair_exhausted_dumped path=%s stage=%s simulation=%s",
                outfile, stage, simulation_id,
            )
        except Exception as dump_err:  # pragma: no cover  defensive
            logger.warning("call_llm.dump_failed: %s", dump_err)

    raise LLMRepairExhausted(
        f"call_llm_for_simulation failed after {max_repair_attempts} repair "
        f"attempts at stage={stage!r}. Last error: "
        f"{type(last_error).__name__ if last_error else 'unknown'}: "
        f"{(str(last_error) if last_error else '')[:400]}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_into_schema(schema: type[T], text: str) -> T:
    cleaned = _strip_code_fences(text).strip()
    if not cleaned:
        raise json.JSONDecodeError("empty response", text, 0)
    data = json.loads(cleaned)
    return schema.model_validate(data)


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _walk_and_validate(value: Any, path: str = "") -> list[Violation]:
    """Walk every string leaf inside a Pydantic model / dict / list and run
    the buyer-state-friendly validator on each."""
    out: list[Violation] = []
    if isinstance(value, BaseModel):
        return _walk_and_validate(value.model_dump(mode="json"), path)
    if isinstance(value, str):
        out.extend(
            validate_text(
                value, field_path=path or "<root>", skip_rules=_BUYER_STATE_SKIP_RULES
            )
        )
    elif isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}" if path else str(k)
            out.extend(_walk_and_validate(v, child))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.extend(_walk_and_validate(item, f"{path}[{i}]"))
    return out


def _append_repair(
    messages: list[LLMMessage],
    *,
    bad_response: str,
    schema: type[BaseModel],
    error: Exception,
) -> list[LLMMessage]:
    if isinstance(error, _ValidatorViolation):
        violations_blob = json.dumps(
            [
                {
                    "field_path": v.field_path,
                    "rule_id": v.rule_id,
                    "matched_phrase": v.matched_phrase,
                    "suggestion": v.suggestion,
                }
                for v in error.violations
            ],
            indent=2,
        )
        repair = (
            "Your previous response contained forbidden language. Fix every "
            "violation listed below — rephrase the affected field as a "
            "subjective buyer-state observation. Do not introduce numeric "
            "forecasts (`%`, `convert at X`), forced verdicts (build / kill / "
            "pivot / revise), or objective market sentiment (the market is X, "
            "customers want X). Buyer vocabulary like 'MRR' or '$10k' in "
            "context IS allowed, but forecasts and verdicts are not.\n\n"
            f"```json\n{violations_blob}\n```\n\n"
            "Return ONLY the corrected JSON object."
        )
    elif isinstance(error, ValidationError):
        details = error.json(indent=2, include_url=False)
        repair = (
            "Your previous response did not validate against the required "
            f"schema `{schema.__name__}`. Pydantic errors:\n\n"
            f"```json\n{details}\n```\n\n"
            "Return ONLY a corrected JSON object that validates exactly."
        )
    else:
        repair = (
            f"Your previous response could not be parsed as JSON: "
            f"{type(error).__name__}: {error}\n\n"
            "Return ONLY a single JSON object — no commentary, no markdown, "
            f"no code fences. Conform to schema `{schema.__name__}`."
        )

    return list(messages) + [
        LLMMessage(role="assistant", content=bad_response),
        LLMMessage(role="user", content=repair),
    ]


__all__ = ["call_llm_for_simulation"]
