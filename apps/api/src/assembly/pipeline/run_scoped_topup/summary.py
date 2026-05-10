"""Phase 8.2I — operator-text formatter for the top-up loop result."""
from __future__ import annotations

from assembly.pipeline.run_scoped_topup.schemas import (
    RunScopedTopUpLoopResult,
    RunScopedTopUpPlan,
)


def render_topup_plan_summary(plan: RunScopedTopUpPlan) -> str:
    bar = "=" * 64
    lines: list[str] = []
    lines.append(bar)
    lines.append(f"Run-Scoped Top-Up Plan — brief={plan.brief_label}")
    lines.append(bar)
    lines.append(f"target_categories ({len(plan.target_categories)}):")
    for k in plan.target_categories:
        qs = plan.queries_by_category.get(k, [])
        lines.append(f"  - {k} ({len(qs)} queries)")
        for q in qs:
            lines.append(f"      • {q}")
    lines.append("")
    lines.append("Caps:")
    lines.append(
        f"  total_queries={plan.total_queries} "
        f"(max {plan.max_total_queries}); "
        f"max_results_per_query={plan.max_results_per_query}; "
        f"max_accepted={plan.max_accepted_records}; "
        f"max_content_chars={plan.max_content_chars}"
    )
    lines.append(
        f"  persona_write_cap={plan.persona_write_cap}; "
        f"cost_cap_usd=${plan.cost_cap_usd}"
    )
    if plan.requires_compliance_approval:
        lines.append("  ⚠ requires_compliance_approval: True")
    if plan.sensitive_caveats:
        lines.append("Sensitive caveats:")
        for c in plan.sensitive_caveats:
            lines.append(f"  · {c}")
    lines.append(bar)
    return "\n".join(lines)


def render_run_scoped_topup_summary(result: RunScopedTopUpLoopResult) -> str:
    bar = "=" * 64
    lines: list[str] = []
    lines.append(bar)
    mode = "DRY-RUN" if result.dry_run else "LIVE"
    lines.append(f"Run-Scoped Top-Up Loop — {mode} — brief={result.brief_label}")
    lines.append(bar)
    lines.append(render_topup_plan_summary(result.plan))
    lines.append("")
    if result.ingestion is not None:
        i = result.ingestion
        lines.append("Tavily ingest:")
        lines.append(
            f"  fetched={i.fetched_count}  accepted={i.accepted_count}  "
            f"rejected={i.rejected_count}  deduped={i.deduped_count}  "
            f"runtime={i.runtime_seconds:.1f}s  live={i.live_network_used}"
        )
        if i.accepted_by_category:
            lines.append("  accepted by category:")
            for k, n in sorted(i.accepted_by_category.items(),
                               key=lambda kv: -kv[1]):
                lines.append(f"    - {k}: {n}")
        if i.accepted_source_domains:
            top = sorted(
                i.accepted_source_domains.items(),
                key=lambda kv: -kv[1],
            )[:8]
            lines.append("  top domains: " + ", ".join(
                f"{d}({n})" for d, n in top
            ))
        if i.rejected_reason_codes:
            lines.append("  rejection codes: " + ", ".join(
                f"{c}({n})" for c, n in i.rejected_reason_codes.items()
            ))
        lines.append("")
    if result.persona_write is not None:
        w = result.persona_write
        lines.append("Persona write:")
        lines.append(
            f"  shells: total={w.candidate_shells} "
            f"strong={w.strong_signal_shells} "
            f"weak={w.weak_signal_shells} "
            f"context={w.context_only_shells}"
        )
        lines.append(
            f"  personas: created={w.personas_created} "
            f"skipped={w.personas_skipped} "
            f"traits_created={w.traits_created} "
            f"traits_rejected={w.traits_rejected} "
            f"links_created={w.evidence_links_created}"
        )
        if w.cost_actual_usd is not None:
            lines.append(f"  cost_actual_usd: ${w.cost_actual_usd:.4f}")
        lines.append("")
    if result.reaudit is not None:
        r = result.reaudit
        lines.append("Re-audit (before → after):")
        lines.append(
            f"  matched personas: {r.before_matched_count} → "
            f"{r.after_matched_count}  (Δ={r.matched_delta:+d})"
        )
        lines.append(
            f"  tiny_ready:     {r.before_tiny_ready} → {r.after_tiny_ready}"
        )
        lines.append(
            f"  small_ready:    {r.before_small_ready} → {r.after_small_ready}"
        )
        lines.append(
            f"  serious_ready:  {r.before_serious_ready} → {r.after_serious_ready}"
        )
        lines.append(
            f"  next_step:      {r.next_step_recommendation_before.value} → "
            f"{r.next_step_recommendation_after.value}"
        )
        flipped = [
            c for c in r.per_category
            if c.coverage_label_before != c.coverage_label_after
        ]
        if flipped:
            lines.append("  per-category coverage flips:")
            for c in flipped:
                lines.append(
                    f"    {c.category_key}: "
                    f"{c.coverage_label_before} → {c.coverage_label_after} "
                    f"(matched {c.before_matched} → {c.after_matched})"
                )
        if r.remaining_missing_categories:
            lines.append(
                "  remaining MISSING/THIN high-priority categories: "
                + ", ".join(r.remaining_missing_categories)
            )
        if r.new_caveats:
            lines.append("  new caveats:")
            for c in r.new_caveats:
                lines.append(f"    · {c}")
        lines.append("")
    lines.append("Safety assertions:")
    for a in result.safety_assertions:
        lines.append(f"  ✓ {a}")
    lines.append(bar)
    return "\n".join(lines)
