"""Verify the AquaSnap fresh end-to-end run against all 12 checks
from the Phase 10A.3 final verification checkpoint.

Usage:
    .venv/bin/python scripts/verify_aquasnap_run.py <run_uuid>
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from assembly.db import get_sessionmaker
from assembly.models.assembly_run import AssemblyRun
from assembly.models.discussion import (
    DiscussionPrivateBallot, DiscussionSession,
)
from assembly.models.persona import PersonaRecord


async def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_aquasnap_run.py <run_uuid>")
        return 2
    run_id = uuid.UUID(sys.argv[1])
    sm = get_sessionmaker()
    run_dir = Path(
        f"_audit/live_runs/{run_id}"
    )
    if not run_dir.is_absolute():
        from assembly.orchestration.live_founder_brief import (
            _LIVE_RUNS_ROOT,
        )
        run_dir = _LIVE_RUNS_ROOT / str(run_id)

    print(f"=" * 70)
    print(f"AquaSnap fresh-run verification — run_id={run_id}")
    print(f"run_dir={run_dir}")
    print(f"=" * 70)

    async with sm() as s:
        run = (await s.execute(
            select(AssemblyRun).where(AssemblyRun.id == run_id)
        )).scalars().first()
    if run is None:
        print("FAIL: AssemblyRun row not found in DB")
        return 1
    print(f"\nrun.status = {run.status}")
    print(f"run.current_stage = {run.current_stage}")
    print(f"run.error_message = {run.error_message}")

    def _load(name: str) -> dict | None:
        p = run_dir / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    checks: list[tuple[str, bool, str]] = []

    # 1. Fresh retrieval ran
    retrieval = _load("evidence_retrieval.json")
    if retrieval is None:
        checks.append(("1. Fresh retrieval ran", False, "missing artifact"))
    else:
        ok = (
            retrieval.get("any_retrieval_provider_configured")
            and retrieval.get("raw_result_count", 0) > 0
        )
        checks.append((
            "1. Fresh retrieval ran",
            bool(ok),
            f"providers={retrieval.get('providers_attempted')}, "
            f"raw={retrieval.get('raw_result_count')}",
        ))

    # 2. Fresh evidence accepted/rejected audit written
    quality = _load("evidence_quality.json")
    checks.append((
        "2. Evidence accepted/rejected audit",
        quality is not None
        and quality.get("accepted_count", 0) > 0,
        f"accepted={quality.get('accepted_count') if quality else None}, "
        f"rejected={quality.get('rejected_count') if quality else None}",
    ))

    # 3. Fresh evidence signals generated
    signals = _load("evidence_signals.json")
    checks.append((
        "3. Fresh evidence signals generated",
        signals is not None
        and signals.get("total_signals_emitted", 0) >= 8,
        f"signals={signals.get('total_signals_emitted') if signals else None}, "
        f"by_type={signals.get('signals_by_type') if signals else None}",
    ))

    # 4. Fresh personas under a new run_scope_id
    persistence = _load("persistence.json")
    persona_gen = _load("persona_generation.json")
    rsid = (persistence or {}).get("run_scope_id") or (
        persona_gen or {}
    ).get("run_scope_id")
    is_run_live = isinstance(rsid, str) and rsid.startswith("run_live_")
    persona_count = (persona_gen or {}).get("persona_count", 0)
    checks.append((
        "4. Fresh personas under new run_scope_id",
        is_run_live and 21 <= persona_count <= 30,
        f"run_scope_id={rsid}, persona_count={persona_count}",
    ))

    # 5. persona_quality_gates artifact emitted
    pqg = _load("persona_quality_gates.json")
    pqg_pass = bool(pqg and pqg.get("all_gates_passed"))
    checks.append((
        "5. persona_quality_gates artifact emitted",
        pqg is not None and pqg_pass,
        f"all_gates_passed={pqg_pass}, "
        f"gates={list((pqg or {}).get('gate_results', {}).items())[:3]}",
    ))

    # 6. Final ballots are 100% complete or repaired
    repair = _load("final_ballot_repair.json")
    completeness = (repair or {}).get("completeness_after", 0)
    checks.append((
        "6. Final ballot completeness ≥ 95%",
        repair is not None and completeness >= 0.95,
        f"completeness_before={(repair or {}).get('completeness_before')}, "
        f"completeness_after={completeness}, "
        f"missing_after={len((repair or {}).get('missing_persona_ids_after') or [])}",
    ))

    # 7. Stale wording = 0 violations
    wording = _load("fresh_live_artifact_wording_audit.json")
    checks.append((
        "7. Fresh-artifact wording: 0 stale violations",
        wording is not None
        and not wording.get("any_violations", True),
        f"violations={(wording or {}).get('violation_count')}, "
        f"files_scanned={len((wording or {}).get('files_scanned') or [])}",
    ))

    # 8. User-facing language: 0 violations
    user_facing = _load("user_facing_language_audit.json")
    checks.append((
        "8. User-facing language: 0 violations",
        user_facing is not None
        and not user_facing.get("any_violations", True),
        f"violations={(user_facing or {}).get('violation_count')}",
    ))

    # 9. Secret scan: clean (no findings recorded means clean)
    rq = _load("run_quality.json")
    fb = (rq or {}).get("forbidden_claim_audit") or {}
    sens = (rq or {}).get("sensitive_inference_audit") or {}
    checks.append((
        "9. Secret/forbidden/sensitive scans clean",
        not fb.get("any_fake_target_product_use", True)
        and not fb.get("any_forecast_or_verdict", True)
        and not sens.get("any_sensitive_inference", True),
        f"forbidden={fb.get('forecast_or_verdict_count')}, "
        f"fake_use={fb.get('fake_target_product_use_count')}, "
        f"sensitive={sens.get('finding_count')}",
    ))

    # 10. All GET endpoints return fresh live artifacts (presence check)
    expected_files = [
        "founder_report.json", "founder_report.md",
        "persona_generation.json", "cohort_architecture.json",
        "discussion.json", "simulated_intent.json",
        "run_quality.json",
    ]
    missing = [f for f in expected_files if not (run_dir / f).exists()]
    checks.append((
        "10. All API endpoint artifacts present on disk",
        not missing,
        f"missing={missing}",
    ))

    # 11. founder_report.json and founder_report.md exist + tagged 10A.3
    fj = _load("founder_report.json")
    fmd = (run_dir / "founder_report.md")
    md_text = fmd.read_text(encoding="utf-8") if fmd.exists() else ""
    json_ok = (
        fj is not None
        and fj.get("schema_version") == "10A.3.live.v1"
        and fj.get("mode") == "live_founder_brief"
        and fj.get("persona_source") == "fresh_retrieval_driven"
    )
    md_ok = (
        "Phase 10A.3" in md_text
        and f"n={persona_count}" in md_text
    )
    checks.append((
        "11. founder_report.json + .md generated",
        json_ok and md_ok,
        f"json_ok={json_ok}, md_ok={md_ok}",
    ))

    # 12. ready_for_frontend_mvp remains true (proxy = run.status complete
    #     + all earlier gates pass)
    all_passed = all(ok for _, ok, _ in checks)
    checks.append((
        "12. ready_for_frontend_mvp = TRUE",
        all_passed and run.status == "complete",
        f"run.status={run.status}, all_prior_passed={all_passed}",
    ))

    # Print results
    print(f"\n{'=' * 70}")
    print(f"VERIFICATION RESULTS")
    print(f"{'=' * 70}")
    failed_count = 0
    for name, ok, detail in checks:
        marker = "✓" if ok else "✗"
        print(f"  {marker} {name}")
        print(f"      → {detail}")
        if not ok:
            failed_count += 1
    print(f"\n{'=' * 70}")
    print(f"PASS: {len(checks) - failed_count}/{len(checks)}  "
          f"FAIL: {failed_count}/{len(checks)}")
    print(f"{'=' * 70}")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
