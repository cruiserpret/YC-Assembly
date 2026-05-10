"""Phase 8.2G — operator-only target-society planner runner.

Runs the deterministic planner against the four fixture briefs:
  - Amboras (commerce platform)
  - $10 water bottle in California (consumer-packaged-good)
  - iPhone 17 (consumer-electronics)
  - Halal financing (financial-product, sensitive)

Prints a human-readable summary for each. NO live ingestion. NO LLM
calls. NO persona writes. NO graph / simulation / UI writes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    from assembly.pipeline.target_society import (
        ALL_EXAMPLES,
        build_target_society_plan,
        explain_next_steps,
        render_target_society_plan_summary,
        validate_target_society_plan,
    )

    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)

    for key, brief in ALL_EXAMPLES:
        plan = build_target_society_plan(brief)
        result = validate_target_society_plan(plan, brief=brief)
        print()
        print(render_target_society_plan_summary(plan))
        print()
        print(f"validator passed: {result.passed}")
        if not result.passed:
            for v in result.violations:
                print(f"  - {v.rule_id} @ {v.field_path}")
        print("Next steps:")
        for s in explain_next_steps(plan):
            print(f"  • {s}")
        # Persist per-example JSON for review.
        path = out_dir / f"target_society_plan_{key}.json"
        path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print(f"\n→ full plan JSON written to: {path}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
