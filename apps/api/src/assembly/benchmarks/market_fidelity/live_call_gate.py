"""Phase 17B-L — explicit live-call safety gate for paid LLM baseline locking.

Pure decision logic that decides whether a live (paid) GPT/Claude/Gemini baseline call
is permitted. It makes NO call itself, imports NO provider SDK, opens NO network — it
only returns an approval decision. The default (no approval) is ALWAYS refuse, so the
preflight returns PREPARED_NOT_RUN and nothing is ever spent without explicit opt-in.

A live call is permitted ONLY when ALL of these hold (fail-closed):
  1. an explicit approval flag is set (env ``ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true``
     or ``--i-understand-this-costs-real-money``),
  2. a global USD cost cap > 0 is supplied,
  3. a per-provider USD cost cap > 0 is supplied,
  4. at least one provider is explicitly requested,
  5. the per-provider cap does not exceed the global cap.

Even when this gate APPROVES, executing the call is a separate, deliberately-not-wired
step: this isolated package never imports an SDK. Spending requires a follow-up executor
built behind this gate. Reading an env var is not a network/SDK action.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

APPROVAL_ENV_VAR = "ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS"
KNOWN_PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "google")


class LiveCallGateDecision(BaseModel):
    """The fail-closed verdict for a requested live baseline run."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    mode: str  # "preflight_dry_run" (default) | "approved_live"
    providers_requested: list[str]
    approval_flag_present: bool
    global_cost_cap_usd: float | None
    per_provider_cost_cap_usd: float | None
    blocking_conditions: list[str]
    notes: str = ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def evaluate_live_call_gate(
    *,
    approval_flag_present: bool,
    providers_requested: Sequence[str],
    global_cost_cap_usd: float | None,
    per_provider_cost_cap_usd: float | None,
) -> LiveCallGateDecision:
    """Pure gate evaluation. Returns a fail-closed decision; never raises for a normal
    'not approved' case (that is the expected default)."""
    providers = [p for p in providers_requested]
    blocking: list[str] = []

    if not approval_flag_present:
        blocking.append(
            f"no explicit approval (set {APPROVAL_ENV_VAR}=true or pass "
            "--i-understand-this-costs-real-money)"
        )
    if not providers:
        blocking.append("no provider requested (nothing to run)")
    unknown = [p for p in providers if p not in KNOWN_PROVIDERS]
    if unknown:
        blocking.append(f"unknown provider(s): {unknown} (known: {list(KNOWN_PROVIDERS)})")
    if global_cost_cap_usd is None or global_cost_cap_usd <= 0:
        blocking.append("no positive global cost cap (--max-total-usd)")
    if per_provider_cost_cap_usd is None or per_provider_cost_cap_usd <= 0:
        blocking.append("no positive per-provider cost cap (--max-per-provider-usd)")
    if (
        global_cost_cap_usd is not None
        and per_provider_cost_cap_usd is not None
        and per_provider_cost_cap_usd > 0
        and global_cost_cap_usd > 0
        and per_provider_cost_cap_usd > global_cost_cap_usd
    ):
        blocking.append("per-provider cap exceeds the global cap")

    approved = not blocking
    return LiveCallGateDecision(
        approved=approved,
        mode="approved_live" if approved else "preflight_dry_run",
        providers_requested=providers,
        approval_flag_present=approval_flag_present,
        global_cost_cap_usd=global_cost_cap_usd,
        per_provider_cost_cap_usd=per_provider_cost_cap_usd,
        blocking_conditions=blocking,
        notes=(
            "APPROVED — but executing the paid call is a separate, deliberately-unwired "
            "step (this package imports no SDK). Build the executor behind this gate."
            if approved
            else "PREPARED_NOT_RUN — fail-closed default; no paid call is possible."
        ),
    )


def gate_from_env(
    *,
    providers_requested: Sequence[str],
    global_cost_cap_usd: float | None,
    per_provider_cost_cap_usd: float | None,
    cli_approval: bool = False,
    env: Mapping[str, str] | None = None,
) -> LiveCallGateDecision:
    """Resolve the approval flag from the CLI flag OR the environment, then evaluate.
    ``env`` defaults to ``os.environ`` (injectable for tests)."""
    environ = env if env is not None else os.environ
    approval_flag_present = bool(cli_approval) or _truthy(environ.get(APPROVAL_ENV_VAR))
    return evaluate_live_call_gate(
        approval_flag_present=approval_flag_present,
        providers_requested=providers_requested,
        global_cost_cap_usd=global_cost_cap_usd,
        per_provider_cost_cap_usd=per_provider_cost_cap_usd,
    )
