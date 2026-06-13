"""Phase 17B — deterministic naive / statistical baselines.

The reference forecasts every real method must beat. All are PURE and deterministic,
emit a valid BenchmarkPrediction, and use ONLY pre-lock fields from the frozen input
bundle (never the outcome, never live web). Each is hash-lockable like any other
method. The remainder-distribution choices below are documented modeling
conventions, NOT values tuned to any case.
"""
from __future__ import annotations

from pydantic import ValidationError

from assembly.benchmarks.market_fidelity.schema import BenchmarkPrediction

NAIVE_BASELINE_IDS = (
    "always_zero_buyer",
    "majority_receptive",
    "uniform_distribution",
    "category_prior_placeholder",
    "crowdfunding_goal_progress_placeholder",
)

_THIRD = 100.0 / 3.0


def always_zero_buyer() -> BenchmarkPrediction:
    """0% buyer/action; the remaining mass spread uniformly over the three non-buyer
    buckets (an agnostic 'no buyers' null). Matches the maximally-conservative
    posture Assembly's two current locks happen to take."""
    return BenchmarkPrediction(
        buyer_action_positive=0.0,
        receptive=_THIRD,
        uncertain_proof_needed=_THIRD,
        skeptical_resistant=_THIRD,
        confidence=0.2,
        forecast_notes="naive baseline: always_zero_buyer (0% buyer; remainder uniform over non-buyer buckets)",
    )


def majority_receptive() -> BenchmarkPrediction:
    """All mass on the modal 'receptive' bucket."""
    return BenchmarkPrediction(
        buyer_action_positive=0.0,
        receptive=100.0,
        uncertain_proof_needed=0.0,
        skeptical_resistant=0.0,
        confidence=0.2,
        forecast_notes="naive baseline: majority_receptive (all mass on receptive)",
    )


def uniform_distribution() -> BenchmarkPrediction:
    """Max-entropy reference: 25% per bucket."""
    return BenchmarkPrediction(
        buyer_action_positive=25.0,
        receptive=25.0,
        uncertain_proof_needed=25.0,
        skeptical_resistant=25.0,
        confidence=0.1,
        forecast_notes="naive baseline: uniform_distribution (25/25/25/25 max-entropy reference)",
    )


def category_prior_placeholder(input_bundle: dict | None = None) -> BenchmarkPrediction:
    """If the frozen input bundle carries a pre-lock ``category_prior`` (a 4-bucket
    dict), use it; otherwise return an HONEST schema_failure (no prior exists yet —
    the ledger currently has 0 direct-observed category priors). Never invents one."""
    prior = (input_bundle or {}).get("category_prior")
    keys = ("buyer_action_positive", "receptive", "uncertain_proof_needed", "skeptical_resistant")
    if isinstance(prior, dict) and all(k in prior for k in keys):
        try:
            return BenchmarkPrediction(
                **{k: float(prior[k]) for k in keys},
                confidence=0.25,
                forecast_notes="naive baseline: category_prior_placeholder (used pre-lock category_prior from input bundle)",
            )
        except (ValidationError, ValueError, TypeError) as e:
            return BenchmarkPrediction(
                confidence=0.0,
                schema_failure=True,
                schema_failure_reason=f"supplied category_prior is invalid (e.g. does not sum to ~100): {e}",
                forecast_notes="naive baseline: category_prior_placeholder (invalid prior → schema_failure)",
            )
    return BenchmarkPrediction(
        confidence=0.0,
        schema_failure=True,
        schema_failure_reason=(
            "no pre-lock category_prior available in the input bundle (the ledger has "
            "0 direct-observed category priors); refusing to invent one"
        ),
        forecast_notes="naive baseline: category_prior_placeholder (no prior → schema_failure, honest)",
    )


def crowdfunding_goal_progress_placeholder(input_bundle: dict | None = None) -> BenchmarkPrediction:
    """A buyer-anchor extrapolation from PRE-LOCK crowdfunding progress ONLY — used
    iff the input bundle includes ``crowdfunding_progress`` with
    ``pct_of_goal_at_lock`` and ``frac_time_elapsed`` (both pre-outcome). Linearly
    projects final % of goal, then maps it to a buyer-leaning vs skeptical
    distribution via a fixed monotone heuristic (a documented PLACEHOLDER, not a
    tuned model). No progress fields → honest schema_failure. Never reads the
    realized outcome."""
    prog = (input_bundle or {}).get("crowdfunding_progress")
    if not (isinstance(prog, dict) and "pct_of_goal_at_lock" in prog and "frac_time_elapsed" in prog):
        return BenchmarkPrediction(
            confidence=0.0,
            schema_failure=True,
            schema_failure_reason=(
                "input bundle has no pre-lock crowdfunding_progress "
                "{pct_of_goal_at_lock, frac_time_elapsed}; baseline not applicable"
            ),
            forecast_notes="naive baseline: crowdfunding_goal_progress_placeholder (no pre-lock progress → schema_failure)",
        )
    pct = max(0.0, float(prog["pct_of_goal_at_lock"]))
    frac = min(1.0, max(1e-6, float(prog["frac_time_elapsed"])))
    projected_final_pct = pct / frac  # simple linear run-rate projection to close
    # Fixed monotone map: more projected funding -> more buyer mass, less skeptical.
    # buyer share saturates at 40pp by ~2x-goal; remainder split receptive/uncertain/skeptical.
    buyer = min(40.0, max(0.0, projected_final_pct - 100.0) * 0.4)
    rest = 100.0 - buyer
    receptive = rest * 0.45
    uncertain = rest * 0.35
    skeptical = rest * 0.20
    return BenchmarkPrediction(
        buyer_action_positive=buyer,
        receptive=receptive,
        uncertain_proof_needed=uncertain,
        skeptical_resistant=skeptical,
        confidence=0.3,
        expected_action_signal="kickstarter_pledge",
        forecast_notes=(
            f"naive baseline: crowdfunding_goal_progress_placeholder "
            f"(pct_at_lock={pct:.1f}, frac_time_elapsed={frac:.3f}, projected_final_pct={projected_final_pct:.1f}; "
            "fixed monotone placeholder map, pre-lock only)"
        ),
    )


def naive_baseline(name: str, input_bundle: dict | None = None) -> BenchmarkPrediction:
    """Dispatch by id. Raises KeyError for an unknown baseline name."""
    if name == "always_zero_buyer":
        return always_zero_buyer()
    if name == "majority_receptive":
        return majority_receptive()
    if name == "uniform_distribution":
        return uniform_distribution()
    if name == "category_prior_placeholder":
        return category_prior_placeholder(input_bundle)
    if name == "crowdfunding_goal_progress_placeholder":
        return crowdfunding_goal_progress_placeholder(input_bundle)
    raise KeyError(f"unknown naive baseline: {name!r} (known: {NAIVE_BASELINE_IDS})")
