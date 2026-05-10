"""Phase 6.6 — universal cost-guarded LLM entry point for `pipeline/`.

Every LLM call inside `pipeline/` (parser, evidence builder, society builder,
all simulation rounds, future RAG / aggregation calls) MUST go through
`cost_guarded_chat`. The AST drift test in `tests/test_no_drift.py` greps
the entire `pipeline/` package and fails the suite if any direct
`provider.chat(...)` or `provider.structured_output(...)` slips in.

This is the structural enforcement of standing entry condition O1
(every orchestrated LLM call must go through `with_cost_guard`).

What this helper does:
  1. Builds an `LLMCallContext` from the caller's parameters
  2. Wraps the call in `with_cost_guard` (Postgres row lock + cap check + log row)
  3. Returns the raw `LLMResponse`

What it deliberately does NOT do:
  - Schema parsing (caller owns this — different domains have different schemas)
  - Repair loops (caller owns this — parser, evidence, society, simulation
    all have domain-specific repair strategies that should not be unified)
  - Validator sweeps (caller owns this — the buyer-state validator only
    applies to simulation outputs, not parser / evidence / society outputs)

The pattern callers should use is:

    for attempt in range(max_repair_attempts + 1):
        response = await cost_guarded_chat(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            stage="my_stage",
            messages=messages,
            provider=provider,
            model=model,
            ...,
        )
        try:
            parsed = my_domain_parse(response.text)
        except MyDomainError as e:
            messages = my_domain_repair_message(messages, response.text, e)
            continue
        return parsed

Each attempt — INCLUDING repair attempts — is its own cost-guarded LLM call.
The cap can never be bypassed by repair loops. Each attempt also writes its
own row to `llm_call_log` so the audit trail captures every retry.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.config import get_settings
from assembly.llm.cost_guard import with_cost_guard
from assembly.llm.provider import (
    LLMCallContext,
    LLMMessage,
    LLMProvider,
    LLMResponse,
)
from assembly.llm.router import pick_model_for_stage

logger = logging.getLogger(__name__)


async def cost_guarded_chat(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    stage: str,
    messages: Sequence[LLMMessage],
    provider: LLMProvider,
    model: str | None = None,
    hard_cap_usd: Decimal | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.4,
    capture_prompt_snapshot: bool = True,
    estimated_prompt_tokens: int = 4000,
    estimated_completion_tokens: int = 1000,
) -> LLMResponse:
    """The single universal LLM entry point for the entire `pipeline/` package.

    Every call site in `pipeline/` (parser, evidence builder, society builder,
    simulation rounds, aggregation, future RAG) MUST go through this function.
    Direct `provider.chat(...)` / `provider.structured_output(...)` calls in
    that package are blocked by `tests/test_no_drift.py`.

    Behavior:
      - Acquires the per-simulation row lock via `with_cost_guard`
      - Enforces the hard cost cap (uses `hard_cap_usd` or settings default)
      - Calls `provider.chat(messages, ctx)`
      - Writes a row to `llm_call_log` on success and on failure
      - Returns the raw `LLMResponse` (caller owns parsing / repair)

    Required arguments:
      sessionmaker: async session factory bound to the simulations DB
      simulation_id: required — must reference an existing `simulations` row.
        Tests that don't have a real sim row should patch this function via
        the `patched_cost_guarded_chat` fixture.
      stage: free-form string written to `llm_call_log.stage`. Conventional
        values: 'intake_parser', 'evidence_extractor', 'society_builder',
        'round_baseline', 'round_first_exposure', ..., 'round_final_stance',
        'round_social_influence_debate'. Future stages: 'rag_chunk_extract',
        'rag_query_rewrite', 'aggregation_section_*'.
      messages: list of `LLMMessage` (system + user[+assistant repair turns]).
      provider: the `LLMProvider` instance.

    Optional arguments:
      model: override the stage-default model. If `None`, resolved via
        `pick_model_for_stage(stage)` (Sonnet for role-play, Opus for synthesis).
      hard_cap_usd: override the simulation's hard cap. If `None`, uses
        `settings.cost_hard_usd`.
      max_tokens, temperature: passed through to `LLMCallContext`. Streaming
        is automatically used by the Anthropic provider when `max_tokens > 8192`.
      capture_prompt_snapshot: when True, the resolved prompt is stored on the
        response and persisted to `llm_call_log.prompt_snapshot`. Always True
        in production paths — only flip for low-value retrieval-extract calls
        if storage cost becomes a concern.
      estimated_prompt_tokens, estimated_completion_tokens: feed the cap
        pre-check. Real cost is recorded from `response.usage` after the call.

    Returns:
      The raw `LLMResponse`. Caller is responsible for parsing / validation /
      repair-loop construction. Construct repairs by calling this function
      again with the prior assistant message + a corrective user message.

    Raises:
      `CostCapExceeded`: if `total_so_far + estimated_cost > hard_cap_usd`
        (raised before the LLM call is made — no API spend).
      Any provider-level exception (e.g. Anthropic 4xx/5xx) is logged with
        `success=False` and re-raised. Callers may catch and retry with a
        new `cost_guarded_chat` invocation if they want — that retry is
        also cost-guarded and logged.
    """
    settings = get_settings()
    resolved_model = model or pick_model_for_stage(stage)
    cap = hard_cap_usd or Decimal(str(settings.cost_hard_usd))

    ctx = LLMCallContext(
        stage=stage,
        model=resolved_model,
        simulation_id=simulation_id,
        max_tokens=max_tokens,
        temperature=temperature,
        capture_prompt_snapshot=capture_prompt_snapshot,
    )

    # The closure captures `messages` so the same `with_cost_guard` shape used
    # by the simulation worker (Phase 6.5) is reused unchanged. This keeps the
    # row-lock + cap-check + log_llm_call infrastructure as the single source
    # of truth — `cost_guarded_chat` is a thin contextual wrapper.
    async def _do_one_call() -> LLMResponse:
        return await provider.chat(list(messages), ctx)

    return await with_cost_guard(
        sessionmaker,
        simulation_id=simulation_id,
        stage=stage,
        provider=provider.name,
        model=resolved_model,
        estimated_prompt_tokens=estimated_prompt_tokens,
        estimated_completion_tokens=estimated_completion_tokens,
        hard_cap_usd=cap,
        actual_call=_do_one_call,
    )


async def cost_guarded_embed(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    stage: str,
    texts: Sequence[str],
    provider,  # EmbeddingProvider — typed loosely to avoid cyclic import
    hard_cap_usd: Decimal | None = None,
    estimated_tokens_per_text: int = 200,
) -> list[list[float] | None]:
    """Phase 6.75 — universal cost-guarded embedding entry point.

    Same discipline as `cost_guarded_chat`: every call writes a row to
    `llm_call_log`, takes the per-simulation row lock, and refuses
    pre-emptively if the projected cost would exceed the hard cap. Direct
    `provider.embed(...)` calls anywhere in `pipeline/` are blocked by the
    AST drift scan.

    The cap pre-check uses `estimated_tokens_per_text * len(texts)` as the
    estimated prompt-token count and 0 completion tokens (embeddings have
    no completion side). The pricing router knows embedding rates per-model.

    Returns a list parallel to `texts` — embeddings or None for each input.
    """
    settings = get_settings()
    cap = hard_cap_usd or Decimal(str(settings.cost_hard_usd))

    estimated_prompt_tokens = max(1, estimated_tokens_per_text * len(texts))

    async def _do_one_call() -> LLMResponse:
        # Wrap the embed call in an LLMResponse so with_cost_guard's logging
        # path stays uniform (it reads .model, .prompt_tokens, etc.). The
        # `text` field carries a JSON-stringified count for audit but we
        # don't actually parse it back — the real return is the vectors.
        t0 = perf_counter()
        vectors = await provider.embed(list(texts))
        latency_ms = int((perf_counter() - t0) * 1000)
        # Stash the vectors on a sentinel attribute so the outer call can
        # extract them. LLMResponse is a dataclass so we use the `raw` field.
        response = LLMResponse(
            text=f"<embedded {len(texts)} texts>",
            prompt_tokens=estimated_prompt_tokens,
            completion_tokens=0,
            latency_ms=latency_ms,
            model=provider.name,
            provider="embedding",
            raw={"vectors": vectors},
        )
        return response

    response = await with_cost_guard(
        sessionmaker,
        simulation_id=simulation_id,
        stage=stage,
        provider="embedding",
        model=provider.name,
        estimated_prompt_tokens=estimated_prompt_tokens,
        estimated_completion_tokens=0,
        hard_cap_usd=cap,
        actual_call=_do_one_call,
    )
    assert response.raw is not None and "vectors" in response.raw, (
        "cost_guarded_embed: provider closure must populate response.raw['vectors']"
    )
    return list(response.raw["vectors"])


__all__ = ["cost_guarded_chat", "cost_guarded_embed"]
