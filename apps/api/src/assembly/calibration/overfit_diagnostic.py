"""Phase 12E.5E — anti-overfit diagnostic helpers.

Pure module. No DB, no LLM, no network. Operates on saved paid-run
artifacts under `_audit/live_runs/<run_id>/` plus operator-supplied
labels files. Built to answer the single question:

  "Is a single-product failure an isolated case or a systemic
  Assembly weakness?"

Design rules:
  1. The diagnostic NEVER recommends a global fix from a single
     product. Every check produces both the product-level signal AND
     the cross-product signal.
  2. Counterfactual projections are diagnostic-only — they never
     mutate SOURCE_PROFILES or any production state.
  3. The module is import-clean from `assembly.calibration`; the
     existing `score_product_under_profile` is reused so there is one
     source of truth for offline projection.

Surfaces:
  * `RunArtifact` — typed view of one paid run's relevant fields.
  * `load_paid_run(run_id, observed_pct, ...)` — assemble RunArtifact
    from disk.
  * `compute_per_run_diagnostic(...)` — receptive-skew + skeptic-
    underpred + buyer-miss + uncertain-injection for one run.
  * `compare_across_products(...)` — does the same failure pattern
    appear in N≥2 products?
  * `counterfactual_intent_distribution(...)` — re-route X% of
    `would_consider_if_proven` voices to a chosen bucket and re-
    project.
  * `intent_distribution_for_target_mae(...)` — back-solve what
    intent_distribution would be required to reach a target MAE band.
  * `classify_root_cause(...)` — labels each candidate cause as
    PRODUCT-SPECIFIC / LIKELY-SYSTEMIC / INCONCLUSIVE with evidence.

Anti-overfit invariant: the operator-set GLOBAL_FIX_THRESHOLD requires
≥2 products to exhibit the same pattern before this module emits a
"systemic" classification.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assembly.calibration.market_fidelity import (
    BUCKETS,
    compute_decision_fidelity,
)
from assembly.calibration.source_profile_recalibration import (
    ProductFixture,
    score_product_under_profile,
)
from assembly.sources.audience.role_taxonomy import SOURCE_PROFILES


# The anti-overfit threshold — N products must exhibit the same
# failure pattern before the diagnostic classifies it as systemic.
GLOBAL_FIX_THRESHOLD: int = 2

# Thresholds for "this bucket is mispredicted" used in the per-run
# diagnostic. Aligned with Phase 12E.5A gates.
RECEPTIVE_SKEW_WARN_PP: float = 10.0
SKEPTIC_UNDERPRED_WARN_PP: float = 10.0
BUYER_MISS_WARN_PP: float = 5.0
UNCERTAIN_INJECTION_WARN_PP: float = 10.0


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class RunArtifact:
    """Compact view of one paid run's diagnostic-relevant artifacts."""

    product: str
    run_id: str
    launch_source: str
    intent_distribution: dict[str, int]
    target_market_view_pct: dict[str, float]
    source_audience_view_pct: dict[str, float]
    noise_meta_count: int
    augmentation_audit: dict[str, Any]
    observed_pct: dict[str, float] | None
    diversity_health: dict[str, Any]


@dataclass
class PerRunDiagnostic:
    """Per-run mis-prediction breakdown."""

    product: str
    run_id: str
    launch_source: str
    source_audience_mae_pp: float
    receptive_signed_pp: float
    skeptic_signed_pp: float
    uncertain_signed_pp: float
    buyer_signed_pp: float
    receptive_overpredict: bool
    skeptic_underpredict: bool
    uncertain_overinject: bool
    buyer_miss: bool
    pct_legacy_would_consider_if_proven: float
    pct_legacy_competitor_loyal_or_reject: float


@dataclass
class CrossProductComparison:
    """Does the same pattern appear in ≥N products?"""

    pattern_name: str
    per_product_signal: dict[str, bool]  # product -> exhibits pattern
    n_products_with_pattern: int
    crosses_threshold: bool  # n_products_with_pattern >= GLOBAL_FIX_THRESHOLD
    note: str = ""


@dataclass
class RootCauseAssessment:
    """One candidate cause; classified PRODUCT-SPECIFIC,
    LIKELY-SYSTEMIC, or INCONCLUSIVE."""

    cause_id: str
    classification: str  # one of "product_specific" | "likely_systemic" | "inconclusive"
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    estimated_mae_contribution_pp: float | None = None
    products_exhibiting: list[str] = field(default_factory=list)
    certainty: str = "low"  # low | medium | high
    recommended_action: str = ""


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


_AUDIT_ROOT = Path(
    "/Users/hamza40/Desktop/Aseembly/assembly-v0/apps/api/_audit/live_runs"
)


def _normalize_view(d: dict[str, int] | dict[str, float]) -> dict[str, float]:
    total = sum(d.get(b, 0) for b in BUCKETS)
    if total <= 0:
        return {b: 0.0 for b in BUCKETS}
    return {b: 100.0 * d.get(b, 0) / total for b in BUCKETS}


def load_paid_run(
    *,
    product: str,
    run_id: str,
    observed_pct: dict[str, float] | None,
    audit_root: Path | None = None,
) -> RunArtifact:
    """Load a paid run's diagnostic-relevant fields. Reads:

      - simulated_intent.json (intent_distribution + Phase 12E views)
      - founder_report.json (audience_breakdown)
      - diversity_health.json (skeptic_retention_rate + Phase 12C
        per-round transitions)
    """
    root = audit_root or _AUDIT_ROOT
    rd = root / run_id
    if not rd.exists():
        raise FileNotFoundError(f"run dir not found: {rd}")
    si = json.loads((rd / "simulated_intent.json").read_text())
    fr = json.loads((rd / "founder_report.json").read_text())
    dh_path = rd / "diversity_health.json"
    dh = json.loads(dh_path.read_text()) if dh_path.exists() else {}
    intent_distribution = si.get("intent_distribution") or {}
    ab = fr.get("audience_breakdown") or {}
    return RunArtifact(
        product=product,
        run_id=run_id,
        launch_source=ab.get("launch_source_used", "unknown"),
        intent_distribution=intent_distribution,
        target_market_view_pct=_normalize_view(
            ab.get("target_market_reaction") or {},
        ),
        source_audience_view_pct=_normalize_view(
            ab.get("source_audience_reaction") or {},
        ),
        noise_meta_count=(ab.get("noise_meta_estimate") or {}).get("count", 0),
        augmentation_audit=ab.get("augmentation_audit") or {},
        observed_pct=observed_pct,
        diversity_health=dh,
    )


# ---------------------------------------------------------------------------
# Per-run diagnostic
# ---------------------------------------------------------------------------


def compute_per_run_diagnostic(art: RunArtifact) -> PerRunDiagnostic:
    """Compute per-bucket mis-prediction breakdown vs observed."""
    if art.observed_pct is None:
        raise ValueError(
            f"{art.product}/{art.run_id} has no observed_pct — can't "
            "diagnose"
        )
    d = compute_decision_fidelity(
        predicted_pct=art.source_audience_view_pct,
        observed_pct=art.observed_pct,
    )
    n_legacy_total = sum(art.intent_distribution.values()) or 1
    n_wcip = art.intent_distribution.get("would_consider_if_proven", 0)
    n_loyal = art.intent_distribution.get(
        "loyal_to_current_alternative", 0,
    )
    n_reject = art.intent_distribution.get("would_reject", 0)
    return PerRunDiagnostic(
        product=art.product,
        run_id=art.run_id,
        launch_source=art.launch_source,
        source_audience_mae_pp=d.mae_pp,
        receptive_signed_pp=d.signed_errors_pp["receptive"],
        skeptic_signed_pp=d.signed_errors_pp["skeptical"],
        uncertain_signed_pp=d.signed_errors_pp["uncertain"],
        buyer_signed_pp=d.signed_errors_pp["buyer"],
        # Positive signed error on receptive = OVERPREDICT
        receptive_overpredict=(
            d.signed_errors_pp["receptive"] > RECEPTIVE_SKEW_WARN_PP
        ),
        # Positive `skeptic_underprediction_pp` (observed > predicted)
        skeptic_underpredict=(
            d.skeptic_underprediction_pp > SKEPTIC_UNDERPRED_WARN_PP
        ),
        uncertain_overinject=(
            d.uncertain_injection_pp > UNCERTAIN_INJECTION_WARN_PP
        ),
        # buyer "miss" = observed buyer not zero AND we predicted near zero
        buyer_miss=(
            art.observed_pct["buyer"] >= BUYER_MISS_WARN_PP
            and art.source_audience_view_pct["buyer"] < 1.0
        ),
        pct_legacy_would_consider_if_proven=(
            100.0 * n_wcip / n_legacy_total
        ),
        pct_legacy_competitor_loyal_or_reject=(
            100.0 * (n_loyal + n_reject) / n_legacy_total
        ),
    )


# ---------------------------------------------------------------------------
# Cross-product comparison
# ---------------------------------------------------------------------------


def compare_across_products(
    *,
    pattern_name: str,
    per_run: list[PerRunDiagnostic],
    pattern_attr: str,
    threshold_distinct_products: int = GLOBAL_FIX_THRESHOLD,
    note: str = "",
) -> CrossProductComparison:
    """Does the per-run pattern (boolean attribute) appear across N
    distinct products? The threshold is the anti-overfit guard
    (default 2 — DO NOT call systemic from a single product)."""
    per_product: dict[str, bool] = {}
    for r in per_run:
        signal = bool(getattr(r, pattern_attr))
        per_product[r.product] = per_product.get(r.product, False) or signal
    n_with = sum(1 for v in per_product.values() if v)
    return CrossProductComparison(
        pattern_name=pattern_name,
        per_product_signal=per_product,
        n_products_with_pattern=n_with,
        crosses_threshold=(n_with >= threshold_distinct_products),
        note=note,
    )


# ---------------------------------------------------------------------------
# Counterfactual projection helpers
# ---------------------------------------------------------------------------


def counterfactual_route_wcip_to_uncertain(
    *,
    art: RunArtifact,
    fraction: float,
    profile_key: str = "hn_show_hn_v2",
) -> dict[str, Any]:
    """Counterfactual E: route `fraction` of the legacy
    `would_consider_if_proven` voices to `wait_and_see` (uncertain
    bucket), then re-project under `profile_key`.

    Pure diagnostic — no production state changed.
    """
    if not (0.0 <= fraction <= 1.0):
        raise ValueError(f"fraction must be in [0, 1], got {fraction}")
    intents = dict(art.intent_distribution)
    n_wcip = intents.get("would_consider_if_proven", 0)
    n_move = int(round(fraction * n_wcip))
    if n_move > 0:
        intents["would_consider_if_proven"] = n_wcip - n_move
        intents["wait_and_see"] = intents.get(
            "wait_and_see", 0,
        ) + n_move
    if art.observed_pct is None:
        raise ValueError(f"need observed_pct on {art.product}")
    fixture = ProductFixture(
        name=f"{art.product}_cf",
        intent_distribution=intents,
        observed_pct=art.observed_pct,
        run_scope_id=f"{art.product}_cf_route_{fraction:.2f}",
        evidence_snapshot_hash="cf:diagnostic",
        brief_hash="cf:diagnostic",
    )
    out = score_product_under_profile(
        product=fixture,
        candidate_profile=SOURCE_PROFILES[profile_key],
    )
    return {
        "counterfactual_id": (
            f"E_route_{fraction:.2f}_wcip_to_uncertain_under_{profile_key}"
        ),
        "moved_n": n_move,
        "modified_intent_distribution": intents,
        "predicted_pct_source_audience": out["predicted_pct_source_audience"],
        "mae_pp": out["fidelity"]["decision"]["mae_pp"],
        "max_bucket_error_pp": (
            out["fidelity"]["decision"]["max_bucket_error_pp"]
        ),
        "signed_errors_pp": (
            out["fidelity"]["decision"]["signed_errors_pp"]
        ),
    }


def counterfactual_route_wcip_to_loyal(
    *,
    art: RunArtifact,
    fraction: float,
    profile_key: str = "hn_show_hn_v2",
) -> dict[str, Any]:
    """Counterfactual F: route `fraction` of legacy
    `would_consider_if_proven` voices to `loyal_to_current_alternative`
    (skeptical bucket). Tests the bound where customer voices are
    actually competitor-loyal instead of proof-seeking."""
    if not (0.0 <= fraction <= 1.0):
        raise ValueError(f"fraction must be in [0, 1], got {fraction}")
    intents = dict(art.intent_distribution)
    n_wcip = intents.get("would_consider_if_proven", 0)
    n_move = int(round(fraction * n_wcip))
    if n_move > 0:
        intents["would_consider_if_proven"] = n_wcip - n_move
        intents["loyal_to_current_alternative"] = intents.get(
            "loyal_to_current_alternative", 0,
        ) + n_move
    if art.observed_pct is None:
        raise ValueError(f"need observed_pct on {art.product}")
    fixture = ProductFixture(
        name=f"{art.product}_cf",
        intent_distribution=intents,
        observed_pct=art.observed_pct,
        run_scope_id=f"{art.product}_cf_loyal_{fraction:.2f}",
        evidence_snapshot_hash="cf:diagnostic",
        brief_hash="cf:diagnostic",
    )
    out = score_product_under_profile(
        product=fixture,
        candidate_profile=SOURCE_PROFILES[profile_key],
    )
    return {
        "counterfactual_id": (
            f"F_route_{fraction:.2f}_wcip_to_loyal_under_{profile_key}"
        ),
        "moved_n": n_move,
        "modified_intent_distribution": intents,
        "predicted_pct_source_audience": out["predicted_pct_source_audience"],
        "mae_pp": out["fidelity"]["decision"]["mae_pp"],
        "max_bucket_error_pp": (
            out["fidelity"]["decision"]["max_bucket_error_pp"]
        ),
        "signed_errors_pp": (
            out["fidelity"]["decision"]["signed_errors_pp"]
        ),
    }


def intent_distribution_for_target_mae(
    *,
    art: RunArtifact,
    target_mae_pp: float = 8.0,
    profile_key: str = "hn_show_hn_v2",
    grid_step: float = 0.10,
) -> dict[str, Any]:
    """Counterfactual G: grid over (fraction_to_uncertain,
    fraction_to_loyal) on the legacy `would_consider_if_proven` mass
    and report the minimum-MAE result + the smallest re-routing that
    still hits the target MAE.

    Pure diagnostic; never modifies production state.
    """
    if art.observed_pct is None:
        raise ValueError(f"need observed_pct on {art.product}")
    intents = dict(art.intent_distribution)
    n_wcip = intents.get("would_consider_if_proven", 0)
    if n_wcip == 0:
        return {
            "counterfactual_id": f"G_minimum_for_target_under_{profile_key}",
            "target_mae_pp": target_mae_pp,
            "reachable_within_grid": False,
            "best_overall_mae_pp": None,
            "note": "no would_consider_if_proven voices to re-route",
        }
    best_overall: dict[str, Any] | None = None
    first_reaching: dict[str, Any] | None = None
    grid_points: list[dict[str, Any]] = []
    f_u = 0.0
    while f_u <= 1.0 + 1e-9:
        f_l = 0.0
        while f_l <= 1.0 - f_u + 1e-9:
            n_to_unc = int(round(f_u * n_wcip))
            n_to_loy = int(round(f_l * n_wcip))
            if n_to_unc + n_to_loy > n_wcip:
                f_l += grid_step
                continue
            modified = dict(intents)
            modified["would_consider_if_proven"] = (
                n_wcip - n_to_unc - n_to_loy
            )
            if n_to_unc:
                modified["wait_and_see"] = modified.get(
                    "wait_and_see", 0,
                ) + n_to_unc
            if n_to_loy:
                modified["loyal_to_current_alternative"] = modified.get(
                    "loyal_to_current_alternative", 0,
                ) + n_to_loy
            fixture = ProductFixture(
                name=f"{art.product}_cf",
                intent_distribution=modified,
                observed_pct=art.observed_pct,
                run_scope_id=f"{art.product}_g_{f_u:.2f}_{f_l:.2f}",
                evidence_snapshot_hash="cf:diagnostic",
                brief_hash="cf:diagnostic",
            )
            scored = score_product_under_profile(
                product=fixture,
                candidate_profile=SOURCE_PROFILES[profile_key],
            )
            mae = scored["fidelity"]["decision"]["mae_pp"]
            point = {
                "fraction_wcip_to_uncertain": round(f_u, 2),
                "fraction_wcip_to_loyal": round(f_l, 2),
                "n_to_uncertain": n_to_unc,
                "n_to_loyal": n_to_loy,
                "mae_pp": mae,
                "predicted_pct": scored["predicted_pct_source_audience"],
            }
            grid_points.append(point)
            if best_overall is None or mae < best_overall["mae_pp"]:
                best_overall = point
            if mae <= target_mae_pp and first_reaching is None:
                first_reaching = point
            f_l += grid_step
        f_u += grid_step
    return {
        "counterfactual_id": f"G_minimum_for_target_under_{profile_key}",
        "target_mae_pp": target_mae_pp,
        "reachable_within_grid": first_reaching is not None,
        "first_reaching": first_reaching,
        "best_overall": best_overall,
        "n_grid_points": len(grid_points),
        "n_wcip_original": n_wcip,
    }


# ---------------------------------------------------------------------------
# Root-cause classifier
# ---------------------------------------------------------------------------


def classify_root_cause(
    *,
    cause_id: str,
    per_run: list[PerRunDiagnostic],
    pattern_attr: str | None = None,
    additional_evidence_for: list[str] | None = None,
    additional_evidence_against: list[str] | None = None,
    estimated_mae_contribution_pp: float | None = None,
    recommended_action: str = "",
) -> RootCauseAssessment:
    """Classify one candidate cause based on per-run evidence.

    Uses the anti-overfit rule: PRODUCT-SPECIFIC if only one product
    exhibits; LIKELY-SYSTEMIC if ≥2; INCONCLUSIVE if zero or signal is
    ambiguous.
    """
    evidence_for = list(additional_evidence_for or [])
    evidence_against = list(additional_evidence_against or [])
    products_exhibiting: list[str] = []
    if pattern_attr:
        by_product: dict[str, bool] = {}
        for r in per_run:
            signal = bool(getattr(r, pattern_attr))
            by_product[r.product] = by_product.get(r.product, False) or signal
        products_exhibiting = sorted(
            [p for p, s in by_product.items() if s]
        )
    n_exhibit = len(products_exhibiting)
    if n_exhibit >= GLOBAL_FIX_THRESHOLD:
        classification = "likely_systemic"
        certainty = "medium"
    elif n_exhibit == 1:
        classification = "product_specific"
        certainty = "medium"
    else:
        classification = "inconclusive"
        certainty = "low"
    return RootCauseAssessment(
        cause_id=cause_id,
        classification=classification,
        evidence_for=evidence_for,
        evidence_against=evidence_against,
        estimated_mae_contribution_pp=estimated_mae_contribution_pp,
        products_exhibiting=products_exhibiting,
        certainty=certainty,
        recommended_action=recommended_action,
    )


__all__ = [
    "GLOBAL_FIX_THRESHOLD",
    "RECEPTIVE_SKEW_WARN_PP",
    "SKEPTIC_UNDERPRED_WARN_PP",
    "BUYER_MISS_WARN_PP",
    "UNCERTAIN_INJECTION_WARN_PP",
    "RunArtifact",
    "PerRunDiagnostic",
    "CrossProductComparison",
    "RootCauseAssessment",
    "load_paid_run",
    "compute_per_run_diagnostic",
    "compare_across_products",
    "counterfactual_route_wcip_to_uncertain",
    "counterfactual_route_wcip_to_loyal",
    "intent_distribution_for_target_mae",
    "classify_root_cause",
]
