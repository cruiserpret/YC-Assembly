"""Phase 15D0 — print the calibration diagnostics report from the seed ledger.

Read-only. No DB, no network, no LLM, no paid simulations, no forecast change.

    cd apps/api && python scripts/phase_15d_calibration_diagnostics.py
"""
from __future__ import annotations

from assembly.market_calibration.calibration_diagnostics import (
    build_calibration_diagnostics_report,
)


def _fmt_dist(d: dict | None) -> str:
    if not d:
        return "—"
    return "  ".join(
        f"{k.split('_')[0]}={v:.1f}" for k, v in d.items()
    )


def main() -> int:
    r = build_calibration_diagnostics_report()
    print("=" * 70)
    print("PHASE 15D0 — SOURCE-BIAS / CATEGORY-PRIOR DIAGNOSTICS")
    print("Phase 15D0 does NOT change forecasts. It measures repeated error")
    print("patterns only. Profiles are diagnostic, not validated.")
    print("=" * 70)
    ds = r["dataset_summary"]
    print(
        f"\nCases: {ds['n_cases']}  (scored {ds['n_scored']})   "
        f"training {r['training_case_count']}   holdout {r['holdout_case_count']}"
    )
    print(
        f"Action-tier coverage: tier1={r['tier1_case_count']} "
        f"tier2={r['tier2_case_count']} tier3={r['tier3_case_count']}"
    )
    print(f"Validated: {r['validated']}   (changes_live_forecast={r['changes_live_forecast']})")

    print("\nWARNINGS:")
    for w in r["warnings"]:
        print(f"  ⚠ {w}")

    print("\nSOURCE-BIAS PROFILES (signed = predicted − observed; +over / −under):")
    for src, p in r["source_profiles"].items():
        print(
            f"\n  [{src}]  n={p['case_count']}  avg_mae={p['avg_mae_pp']}pp  "
            f"conf={p['confidence_level']}  validated={p['validated']}"
        )
        print(f"     signed bucket bias: {_fmt_dist(p['avg_signed_bucket_error'])}")
        print(f"     over-predicted : {p['overpredicted_buckets'] or '—'}")
        print(f"     under-predicted: {p['underpredicted_buckets'] or '—'}")
        if p["warning"]:
            print(f"     ⚠ {p['warning']}")

    print("\nCATEGORY-PRIOR PROFILES:")
    for cat, p in r["category_profiles"].items():
        print(
            f"\n  [{cat}]  n={p['case_count']}  avg_mae={p['avg_mae_pp']}pp  "
            f"conf={p['confidence_level']}"
        )
        print(f"     observed avg : {_fmt_dist(p['observed_avg'])}")
        print(f"     predicted avg: {_fmt_dist(p['predicted_avg'])}")

    print("\nRECOMMENDED NEXT DATA NEEDS:")
    for n in r["recommended_next_data_needs"]:
        print(f"  • {n}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
