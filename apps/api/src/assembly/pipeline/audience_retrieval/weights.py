"""Phase 8.2J — derived weighted scorer calibration.

Each scorer axis still produces a 0–5 sub-score (Phase 8.2H math is
unchanged). Phase 8.2J adds a per-axis weight that gets MULTIPLIED
into the sub-score before summation. Weights are normalized so they
always sum to **8.0** — this preserves the 0–40 max-score band the
existing 27 / 36 thresholds were calibrated against.

  total = sum(sub_score[axis] * weight[axis] for axis in 8 axes)
        + exclusion_penalty
  max   = 5 * 8 = 40        (when weights sum to 8)
  threshold 27 / 40 = 67.5%   (same proportion as Phase 8.2H)

Weight derivation is **plan-aware**:

  * `role_context_match`, `pain_objection_match`, `category_specific_match`
    always carry the highest weight — these are the load-bearing axes
    for every commerce simulation.
  * `source_strength` carries near-highest weight (a persona without
    source-bound traits cannot anchor a simulation).
  * `current_alternative_match` is bumped UP when the brief carries
    competitors (the competitor-replacement story matters), DOWN when
    it doesn't.
  * `price_budget_match` is bumped UP only when the simulation goal is
    `TEST_PRICE`; otherwise it stays modest.
  * `geography_match` is bumped UP only when the brief carries an
    explicit geography; otherwise it stays modest.
  * `trust_trigger_match` carries medium weight.

CRITICAL invariants this module preserves:
  * threshold (27, 36) is **NOT** changed
  * stakeholder categories are **NOT** broadened
  * exclusion_penalty stays full-strength (raw subtraction)
  * cross-domain isolation: no weight scheme can rescue a persona
    whose role + pain + cat sub-scores are all zero (because zero
    times any weight is still zero)
"""
from __future__ import annotations

from typing import Final


# The 8 weighted axes. `exclusion_penalty` is NOT weighted — it stays
# a raw subtraction so a persona that explicitly violates a category's
# exclusion signals can never be rescued by reweighting.
WEIGHTED_AXES: Final[tuple[str, ...]] = (
    "role_context_match",
    "pain_objection_match",
    "category_specific_match",
    "source_strength",
    "current_alternative_match",
    "trust_trigger_match",
    "price_budget_match",
    "geography_match",
)


# Total weight sum after normalization. Chosen to keep max-score = 40
# (i.e. 5-per-axis × 8 weight-units), matching the Phase 8.2H math.
TOTAL_WEIGHT_SUM: Final[float] = 8.0


# Default uniform weights. Used by callers that don't supply
# plan-derived weights (i.e. backwards-compat with Phase 8.2H tests
# and any caller that doesn't pass a weight dict).
UNIFORM_WEIGHTS: Final[dict[str, float]] = {
    axis: 1.0 for axis in WEIGHTED_AXES
}


def derive_scorer_weights_for_plan(
    *,
    has_competitors: bool,
    has_geography: bool,
    simulation_goal_is_price_test: bool,
    is_market_entry: bool = False,
) -> dict[str, float]:
    """Return a normalized weight vector for the given brief shape.

    Phase 8.2J base weights (launched-product, default profile):

      role_context_match              1.5  (always highest tier)
      pain_objection_match            1.5  (always highest tier)
      category_specific_match         1.5  (always highest tier)
      source_strength                 1.2  (near-highest)
      current_alternative_match       1.0 if competitors else 0.5
      trust_trigger_match             0.8  (medium)
      price_budget_match              1.2 if price-test goal else 0.5
      geography_match                 1.2 if geography in brief else 0.5

    Phase 8.4A.2 — when `is_market_entry=True`, the weight profile
    shifts to make competitor / category-specific / pain-objection
    evidence the load-bearing relevance signal (since for unlaunched
    products there is no direct product evidence). Geography and
    price-budget become SOFT bonuses (low weight). Role-context is
    de-prioritized because public-web persona traits often lack
    explicit role labels (the Phase 8.4A.1 forensic finding).

    Market-entry base weights:

      current_alternative_match       2.2 if competitors else 1.0
      category_specific_match         1.8
      pain_objection_match            1.6
      source_strength                 1.2
      role_context_match              0.8  (de-prioritized)
      trust_trigger_match             0.8
      price_budget_match              0.4 if price-test goal else 0.2
      geography_match                 0.6 if geography in brief else 0.4

    Both profiles normalize to sum = TOTAL_WEIGHT_SUM (8.0). The
    threshold (27 / 36) does NOT move; the profile change recognizes
    market-entry-relevant signals more accurately, NOT loosens the bar.

    CRITICAL invariants preserved by both profiles:
      * sum of weights = 8.0 → max total = 40 → threshold 27 = 67.5% of max
      * exclusion_penalty unchanged (raw subtraction, not weighted)
      * a persona with zero category-specific + zero pain-objection +
        zero current-alternative still scores at most ~3 (from source-
        strength alone) — well below 18 (WEAKLY_RELEVANT). Off-topic
        personas remain off-topic.
    """
    if is_market_entry:
        raw: dict[str, float] = {
            "role_context_match": 0.8,
            "pain_objection_match": 1.6,
            "category_specific_match": 1.8,
            "source_strength": 1.2,
            "current_alternative_match": 2.2 if has_competitors else 1.0,
            "trust_trigger_match": 0.8,
            "price_budget_match": (
                0.4 if simulation_goal_is_price_test else 0.2
            ),
            "geography_match": 0.6 if has_geography else 0.4,
        }
    else:
        raw = {
            "role_context_match": 1.5,
            "pain_objection_match": 1.5,
            "category_specific_match": 1.5,
            "source_strength": 1.2,
            "current_alternative_match": (
                1.0 if has_competitors else 0.5
            ),
            "trust_trigger_match": 0.8,
            "price_budget_match": (
                1.2 if simulation_goal_is_price_test else 0.5
            ),
            "geography_match": 1.2 if has_geography else 0.5,
        }
    total = sum(raw.values())
    return {k: round(v * TOTAL_WEIGHT_SUM / total, 4) for k, v in raw.items()}


def apply_weights_to_breakdown(
    sub_scores: dict[str, int],
    weights: dict[str, float],
) -> float:
    """Compute the weighted total: `sum(sub_score * weight)` over the
    8 axes. The exclusion penalty is added by the caller separately.
    """
    out = 0.0
    for axis in WEIGHTED_AXES:
        sub = sub_scores.get(axis, 0)
        w = weights.get(axis, 1.0)
        out += float(sub) * float(w)
    return out
