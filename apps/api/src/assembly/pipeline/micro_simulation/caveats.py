"""Phase 8.4B.1 — generic, product-agnostic caveat builder for the
micro-simulation runner.

Replaces the previous hardcoded "8 Amboras stakeholder categories"
string in `runner.py` with a context-aware builder that takes:

  * product_name           — from the brief
  * product_type           — from the brief (optional)
  * geography              — from the brief (optional)
  * total_categories       — len(plan.stakeholder_categories)
  * represented_categories — distinct categories matched by the
                             persona pool
  * sample_size            — n personas in the simulation
  * core_count             — RELEVANT/HIGHLY_RELEVANT in the pool
  * adjacent_count         — WEAKLY_RELEVANT in the pool
  * is_market_entry        — was the plan dynamic-planner-generated
                             (per Phase 8.4A.4 detection)
  * is_unlaunched          — does the brief carry market-entry
                             auto-detection signals
  * geography_strength     — `'strong' | 'soft' | 'absent'`

Emits up to 7 caveat strings:

  1. MICRO-TEST                 (always)
  2. sample-size                (always)
  3. not-a-forecast             (always)
  4. coverage-thinness          (always; uses real category counts)
  5. geography                  (when geography is soft / absent)
  6. adjacent-tier              (when adjacent_count > 0)
  7. unlaunched-product         (when is_market_entry or is_unlaunched)

NOT a presentation layer: these caveats are persisted into
`MicroSimulationResult.caveats` so audit consumers can rely on them.
The Phase 8.2K + 8.2K.1 forbidden-language scanner runs over them
just like any other text leaf (these caveats must NOT contain
forecast / market-reaction / society-as-singular language).
"""
from __future__ import annotations

from typing import Literal


GeographyStrength = Literal["strong", "soft", "absent"]


def build_micro_simulation_caveats(
    *,
    product_name: str,
    product_type: str | None = None,
    geography: str | None = None,
    total_categories: int,
    represented_categories: int,
    sample_size: int,
    core_count: int,
    adjacent_count: int,
    is_market_entry: bool = False,
    is_unlaunched: bool = False,
    geography_strength: GeographyStrength = "absent",
) -> list[str]:
    """Return the ordered caveat list for one micro-simulation result.

    Every caveat is a self-contained sentence that names the active
    product (so audit consumers can attribute correctly across
    multiple simulations) and uses real plan-driven counts (no
    hardcoded `8`).
    """
    if total_categories < 1:
        # Defensive: a plan should always have ≥1 category. If the
        # caller passes 0 we still emit a coverage caveat with
        # honest "0 of 0" framing rather than crashing.
        total_categories = max(0, total_categories)
    if represented_categories < 0:
        represented_categories = 0
    if sample_size < 0:
        sample_size = 0

    # `society-shape` phrase — describes the simulation's audience
    # universe in one short fragment. Avoids "society" by itself
    # because the forbidden-language scanner blocks "X society
    # thinks" — but "society plan" / "energy-drink-category society"
    # is fine since we never claim the society as a singular voice.
    if product_type:
        society_shape = f"{product_type} for {product_name}"
    else:
        society_shape = product_name

    caveats: list[str] = []

    # 1. MICRO-TEST label (always)
    caveats.append(
        "MICRO-TEST: this is a mechanical micro-test, not a "
        "real-world simulation."
    )

    # 2. sample-size (always)
    if adjacent_count > 0:
        caveats.append(
            f"sample-size caveat: this is a MICRO-TEST on n="
            f"{sample_size} personas (core={core_count}, "
            f"adjacent={adjacent_count}); that is NOT a "
            f"population-level sample."
        )
    else:
        caveats.append(
            f"sample-size caveat: this is a MICRO-TEST on n="
            f"{sample_size} personas; that is NOT a "
            f"population-level sample."
        )

    # 3. not-a-forecast (always)
    caveats.append(
        "not-a-forecast caveat: output is NOT a demand forecast, "
        "NOT a buy / adoption percentage, and NOT a verdict on "
        f"whether {product_name} should launch."
    )

    # 4. coverage-thinness (always; uses real plan counts)
    caveats.append(
        f"coverage-thinness caveat: {represented_categories} of "
        f"{total_categories} stakeholder categories represented "
        f"for {society_shape}; this is NOT a full society."
    )

    # 5. geography (only when geography is soft or absent)
    if geography_strength in ("soft", "absent") and geography:
        caveats.append(
            f"geography caveat: most evidence is not "
            f"{geography}-specific. {geography} is a soft market "
            "context, not a fully grounded local sample."
        )
    elif geography_strength == "absent" and not geography:
        # No geography at all — emit a less-specific note
        caveats.append(
            "geography caveat: no geographic anchoring in this "
            "audience; results are not regionally bounded."
        )

    # 6. adjacent-tier (only when adjacent_count > 0)
    if adjacent_count > 0:
        caveats.append(
            f"adjacent-tier caveat: {adjacent_count} of "
            f"{sample_size} personas are ADJACENT_RELEVANT — "
            "lower-weight, caveated category / substitute / "
            "use-case voices, not direct-competitor users."
        )

    # 7. unlaunched-product (only for market-entry / unlaunched)
    if is_market_entry or is_unlaunched:
        caveats.append(
            f"unlaunched-product caveat: {product_name} is treated "
            "as unlaunched / market-entry. Persona reactions are "
            "anchored on competitor / substitute / use-case / "
            "category-objection evidence — there is no direct-"
            "product evidence and no persona was characterized as "
            f"a customer of {product_name}."
        )

    return caveats


def detect_geography_strength(
    *,
    geography: str | None,
    geography_categories_in_audience: int,
    total_geography_categories: int,
) -> GeographyStrength:
    """Heuristic geography-strength detector.

    `strong`: brief has geography AND ≥1 geography-tagged category
              has ≥3 personas (strong local anchoring).
    `soft`:   brief has geography but local-evidence count is thin
              (the typical case for public-web micro-tests).
    `absent`: brief has no geography.
    """
    if not geography:
        return "absent"
    if (
        total_geography_categories > 0
        and geography_categories_in_audience >= 3
    ):
        return "strong"
    return "soft"


__all__ = [
    "GeographyStrength",
    "build_micro_simulation_caveats",
    "detect_geography_strength",
]
