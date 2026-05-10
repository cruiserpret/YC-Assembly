"""Phase 9B — transient-error retry helper for the discussion layer.

Wraps any awaitable LLM-call closure with bounded exponential backoff
on 429 (rate-limit), 529 (Anthropic overload), timeouts, and connection
errors. Schema/validation/forbidden-claim failures are NOT retried —
those need orchestrator-level repair, not blind re-tries.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


_TRANSIENT_TOKENS = (
    "429", "529", "rate_limit", "rate-limit", "overloaded",
    "overloaded_error", "timeout", "timed out", "connection",
    "connectionerror", "service unavailable", "503", "502", "504",
)


def _looks_transient(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}:{exc}".lower()
    return any(tok in msg for tok in _TRANSIENT_TOKENS)


async def call_with_retry(
    *,
    fn: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    base_delay_seconds: float = 4.0,
    max_delay_seconds: float = 30.0,
    label: str = "llm_call",
    on_attempt: Callable[[int, BaseException | None], None] | None = None,
) -> tuple[T | None, dict[str, Any]]:
    """Run `fn()` with bounded backoff on transient errors.

    Returns (result_or_None, audit_dict). audit_dict keys:
      attempts, succeeded, transient_failures, terminal_failure,
      last_error_class, last_error_message.

    Schema / forbidden-claim / validation errors should be raised by
    the *caller* outside `fn`, so they bypass this helper entirely.
    """
    audit = {
        "attempts": 0,
        "succeeded": False,
        "transient_failures": 0,
        "terminal_failure": False,
        "last_error_class": None,
        "last_error_message": None,
    }
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        audit["attempts"] = attempt
        try:
            result = await fn()
            audit["succeeded"] = True
            if on_attempt:
                on_attempt(attempt, None)
            return result, audit
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            audit["last_error_class"] = type(exc).__name__
            audit["last_error_message"] = str(exc)[:240]
            if not _looks_transient(exc):
                audit["terminal_failure"] = True
                logger.warning(
                    "%s: terminal error on attempt %d (%s); not retrying",
                    label, attempt, type(exc).__name__,
                )
                if on_attempt:
                    on_attempt(attempt, exc)
                return None, audit
            audit["transient_failures"] += 1
            if on_attempt:
                on_attempt(attempt, exc)
            if attempt >= max_attempts:
                logger.warning(
                    "%s: exhausted %d attempts; last error %s",
                    label, max_attempts, type(exc).__name__,
                )
                audit["terminal_failure"] = True
                return None, audit
            delay = min(
                max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)),
            )
            logger.info(
                "%s: transient %s on attempt %d; sleeping %.1fs",
                label, type(exc).__name__, attempt, delay,
            )
            await asyncio.sleep(delay)
    # Should be unreachable
    audit["terminal_failure"] = True
    return None, audit
