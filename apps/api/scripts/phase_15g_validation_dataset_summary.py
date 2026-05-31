"""Phase 15G — print a discipline summary of the validation dataset.

Read-only. Loads ALL ledger files via the manifest and reports split counts,
action-tier coverage, source/category distribution, and the data-gap warnings
that gate Phase 15E. No DB, no network, no LLM, no forecast change.

    cd apps/api && python scripts/phase_15g_validation_dataset_summary.py
"""
from __future__ import annotations

from collections import Counter

from assembly.validation_ledger import (
    action_signal_coverage_summary,
    case_split_summary,
    load_all_cases,
    tier_coverage_summary,
)

_MIN_CASES = 20
_MIN_HOLDOUT = 5


def _warnings(cases, split, tiers, action) -> list[str]:
    w: list[str] = []
    if split["n_cases"] < _MIN_CASES:
        w.append(f"fewer than {_MIN_CASES} cases ({split['n_cases']}) — too few to calibrate")
    if split["holdout"] == 0:
        w.append("0 holdout cases — calibration cannot be validated")
    elif split["clean_holdout"] < _MIN_HOLDOUT:
        w.append(f"fewer than {_MIN_HOLDOUT} CLEAN holdout cases ({split['clean_holdout']})")
    if tiers["tier1_case_count"] == 0:
        w.append("0 cases with Tier-1 action outcomes — observed ground truth is opinion-grade")
    if split["train_holdout_overlap"] > 0:
        w.append(f"{split['train_holdout_overlap']} case(s) marked BOTH training and holdout — leakage")
    if split["high_leakage_risk"] > 0:
        w.append(f"{split['high_leakage_risk']} case(s) flagged high leakage risk")
    if action["cases_without_action_signals"] > 0:
        w.append(f"{action['cases_without_action_signals']} case(s) missing action_signals")
    return w


def main() -> int:
    cases = load_all_cases()
    split = case_split_summary(cases)
    tiers = tier_coverage_summary(cases)
    action = action_signal_coverage_summary(cases)
    sources = Counter(c.metadata.source_type for c in cases)
    categories = Counter(c.metadata.product_category for c in cases)

    print("=" * 64)
    print("PHASE 15G — VALIDATION DATASET SUMMARY (data discipline only)")
    print("=" * 64)
    print(
        f"\nTotal {split['n_cases']}   scored {split['scored']}   "
        f"pending {split['pending']}   partial {split['partial']}   "
        f"excluded {split['excluded']}"
    )
    print(
        f"Split: training {split['training']}   holdout {split['holdout']}   "
        f"clean_holdout {split['clean_holdout']}   "
        f"overlap {split['train_holdout_overlap']}   "
        f"high_leakage_risk {split['high_leakage_risk']}"
    )
    print(
        f"Action-tier coverage (cases): tier1={tiers['tier1_case_count']} "
        f"tier2={tiers['tier2_case_count']} tier3={tiers['tier3_case_count']} "
        f"tier4={tiers['tier4_case_count']}"
    )
    print(
        f"action_signals: {action['cases_with_action_signals']} of "
        f"{split['n_cases']} cases carry signals "
        f"({action['total_action_signals']} total)"
    )
    print(f"Sources:    {dict(sources)}")
    print(f"Categories: {dict(categories)}")

    print("\nWARNINGS:")
    warnings = _warnings(cases, split, tiers, action)
    if not warnings:
        print("  (none)")
    for x in warnings:
        print(f"  ⚠ {x}")
    print(
        "\nGATE: no calibration may be applied until there are 20+ diverse "
        "cases,\n      a clean holdout split, and Tier-1 action outcomes. "
        "(Phase 15E/15F)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
