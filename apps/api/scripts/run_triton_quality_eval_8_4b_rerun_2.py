"""Phase 8.4B-RERUN-2 — operator-only quality evaluation of the
post-prompt-fix Triton 8.4B live JSON.

READ-ONLY. NO LLM. NO new ingestion. NO DB writes (except the new
audit JSON file). NO live simulation rerun.

Loads `_audit/triton_micro_simulation_live_8_4b_rerun_2.json`, runs
the deterministic 9-dimension quality evaluator, and writes
`_audit/triton_micro_simulation_quality_8_4b_rerun_2.json`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from assembly.pipeline.micro_simulation.quality_evaluator import (
    evaluate_micro_simulation_quality,
    report_to_dict,
)


TRITON_COMPETITORS = [
    "Red Bull", "Monster", "Celsius", "Prime", "Gatorade",
    "pre-workout", "preworkout", "cold brew", "coffee",
    "electrolyte",
]


def main() -> int:
    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    src = audit_root / "triton_micro_simulation_live_8_4b_rerun_2.json"
    if not src.is_file():
        print(f"ERROR: rerun_2 audit JSON missing: {src}")
        return 2
    result_dict = json.loads(src.read_text(encoding="utf-8"))
    total_plan_categories = 23

    report = evaluate_micro_simulation_quality(
        result_dict=result_dict,
        product_name="Triton Drinks",
        competitors=TRITON_COMPETITORS,
        total_plan_categories=total_plan_categories,
    )

    out_path = audit_root / "triton_micro_simulation_quality_8_4b_rerun_2.json"
    out_path.write_text(
        json.dumps(report_to_dict(report), indent=2),
        encoding="utf-8",
    )

    print("=" * 72)
    print("Phase 8.4B-RERUN-2 — Triton micro-simulation QUALITY EVALUATION")
    print("=" * 72)
    print(f"product: {report.product_name}")
    print(f"sample_size: {report.sample_size}")
    print(f"overall_score: {report.overall_score}")
    print(f"expansion_readiness: {report.expansion_readiness.value}")
    print(f"reason: {report.expansion_reason}")
    print()
    print("PER-DIMENSION:")
    for name, d in report.dimensions.items():
        print(
            f"  [{d.status.value:7s}] {name:32s} "
            f"score={d.score:.3f}"
        )
        print(f"      -> {d.detail}")
        for issue in d.issues[:3]:
            print(f"      issue: {issue[:120]}")
    print()
    print("RECOMMENDATIONS:")
    for r in report.recommendations:
        print(f"  -> {r}")
    print(f"\n-> audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
