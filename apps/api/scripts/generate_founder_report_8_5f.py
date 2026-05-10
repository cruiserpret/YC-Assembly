"""Phase 8.5F — generate the founder-facing report for the StrideShield
8.5E simulation.

Reads the 8.5E simulation + quality audits, aggregates a deterministic
founder-facing report (NO LLM, NO retrieval), runs the secret scanner
+ report-quality evaluator, and writes:

  * `apps/api/_audit/strideshield_founder_report_8_5f.json`
  * `apps/api/_audit/strideshield_founder_report_8_5f.md`
  * `apps/api/_audit/strideshield_founder_report_quality_8_5f.json`

NO DB writes. Verifies pre/post DB counts unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.models.round import (
    AgentResponse, DebateTurn, SimulationRound,
)
from assembly.models.simulation import Simulation
from assembly.models.agent import Agent
from assembly.sources.founder_report_generator import (
    aggregate_founder_report, evaluate_report_quality,
    render_markdown_report, scan_for_secrets,
)


PHASE_LABEL = "8.5F"
INPUT_SIM_AUDIT = "strideshield_simulation_8_5e.json"
INPUT_QUALITY_AUDIT = "strideshield_simulation_quality_8_5e.json"
OUT_JSON = "strideshield_founder_report_8_5f.json"
OUT_MD = "strideshield_founder_report_8_5f.md"
OUT_QUALITY_JSON = "strideshield_founder_report_quality_8_5f.json"
EXPECTED_PRODUCT = "StrideShield"


async def _read_table_counts(sm) -> dict[str, int]:
    async with sm() as session:
        sr = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        pr = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
        sim = (await session.execute(
            select(func.count()).select_from(Simulation)
        )).scalar_one()
        ag = (await session.execute(
            select(func.count()).select_from(Agent)
        )).scalar_one()
        rd = (await session.execute(
            select(func.count()).select_from(SimulationRound)
        )).scalar_one()
        ar = (await session.execute(
            select(func.count()).select_from(AgentResponse)
        )).scalar_one()
        dt = (await session.execute(
            select(func.count()).select_from(DebateTurn)
        )).scalar_one()
    return {
        "source_records": int(sr), "persona_records": int(pr),
        "persona_traits": int(pt), "persona_evidence_links": int(pel),
        "simulations": int(sim), "agents": int(ag),
        "simulation_rounds": int(rd),
        "agent_responses": int(ar), "debate_turns": int(dt),
    }


def _read_audit(audit_root: Path, name: str) -> dict[str, Any]:
    p = audit_root / name
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _validate_inputs(
    *, sim: dict[str, Any], qual: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Pre-flight gate: refuse to build the founder report if the
    8.5E inputs say the simulation isn't ready."""
    blockers: list[str] = []
    if not sim:
        blockers.append("8.5E simulation audit JSON missing")
        return False, blockers
    if not sim.get("ready_for_founder_report_phase"):
        blockers.append(
            f"ready_for_founder_report_phase="
            f"{sim.get('ready_for_founder_report_phase')!r}; expected true."
        )
    if int(sim.get("input_persona_count") or 0) != 7:
        blockers.append(
            f"input_persona_count={sim.get('input_persona_count')}; "
            "expected 7."
        )
    if int(sim.get("rounds_completed") or 0) != 7:
        blockers.append(
            f"rounds_completed={sim.get('rounds_completed')}; "
            "expected 7."
        )
    db_delta = sim.get("db_delta_summary") or {}
    if int(db_delta.get("agent_responses") or 0) != 49:
        blockers.append(
            f"agent_responses delta={db_delta.get('agent_responses')}; "
            "expected 49."
        )
    qscores = (qual or {}).get("scores") or sim.get(
        "quality_evaluator_result", {},
    )
    if (
        qscores.get("ready_state")
        not in ("READY_FOR_FOUNDER_REPORT", "READY_FOR_PROMPT_FIX")
    ):
        blockers.append(
            f"quality.ready_state={qscores.get('ready_state')!r}; "
            "expected READY_FOR_FOUNDER_REPORT or READY_FOR_PROMPT_FIX."
        )
    return len(blockers) == 0, blockers


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Phase {PHASE_LABEL} — founder-facing report generation "
            "(deterministic, no LLM)."
        ),
    )
    parser.add_argument(
        "--input-simulation-audit", default=INPUT_SIM_AUDIT,
    )
    parser.add_argument(
        "--input-quality-audit", default=INPUT_QUALITY_AUDIT,
    )
    args = parser.parse_args()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_json = audit_root / OUT_JSON
    out_md = audit_root / OUT_MD
    out_quality = audit_root / OUT_QUALITY_JSON

    sm = get_sessionmaker()
    db_pre = await _read_table_counts(sm)
    print(f"DB pre-counts: {db_pre}")

    sim = _read_audit(audit_root, args.input_simulation_audit)
    qual = _read_audit(audit_root, args.input_quality_audit)
    ok, blockers = _validate_inputs(sim=sim, qual=qual)
    if not ok:
        print("REFUSED: pre-flight failed:")
        for b in blockers:
            print(f"  - {b}")
        out_json.write_text(json.dumps({
            "phase": "8_5f_founder_report_generation",
            "completed_at": datetime.now(UTC).isoformat(),
            "blockers": blockers,
            "ready_for_fresh_end_to_end_test": False,
        }, indent=2), encoding="utf-8")
        return 2

    # 1. Aggregate (deterministic)
    report = aggregate_founder_report(
        simulation_audit=sim, quality_audit=qual,
    )

    # 2. Render markdown
    md_text = render_markdown_report(report)

    # 3. Quality evaluation
    qual_eval = evaluate_report_quality(
        report=report, rendered_markdown=md_text,
        product_name=report.product_name or EXPECTED_PRODUCT,
    )

    # 4. Universal secret scan over BOTH the JSON and the markdown.
    # If anything secret-shaped is found, redact + fail the phase.
    json_text = json.dumps(
        report.model_dump(), indent=2, default=str,
    )
    json_scan = scan_for_secrets(json_text)
    md_scan = scan_for_secrets(md_text)
    secret_findings = list(json_scan.findings) + list(md_scan.findings)
    secrets_clean = json_scan.is_clean and md_scan.is_clean

    # Update the security_redaction_audit on the report
    report_dict = report.model_dump()
    report_dict["security_redaction_audit"] = {
        "secrets_detected_in_inputs": not secrets_clean,
        "redactions_applied": len(secret_findings),
        "scanner_version": "8.5F.universal",
        "finding_categories": sorted({
            f["category"] for f in secret_findings
        }),
    }
    report_dict["quality_reference"] = {
        **report_dict.get("quality_reference", {}),
        "report_quality_evaluation": json.loads(
            qual_eval.model_dump_json(),
        ),
    }
    # Final readiness override: if secrets found OR quality
    # NOT_READY, then ready_for_fresh_end_to_end_test = False.
    report_dict["ready_for_fresh_end_to_end_test"] = (
        report_dict.get("ready_for_fresh_end_to_end_test", False)
        and secrets_clean
        and qual_eval.ready_state in (
            "READY_FOR_FRESH_END_TO_END_TEST",
            "READY_FOR_REPORT_PROMPT_FIX",
        )
    )

    # 5. DB post-check
    db_post = await _read_table_counts(sm)
    db_unchanged = db_pre == db_post

    # If secrets were found, write redacted versions and fail loudly.
    if not secrets_clean:
        out_json.write_text(json_scan.redacted_text, encoding="utf-8")
        out_md.write_text(md_scan.redacted_text, encoding="utf-8")
        print(
            f"FAIL: {len(secret_findings)} secret pattern(s) "
            "detected in generated output. Files written with "
            "[REDACTED] placeholders."
        )
        return 1

    # 6. Write the JSON + markdown + quality JSON
    out_json.write_text(
        json.dumps(report_dict, indent=2, default=str),
        encoding="utf-8",
    )
    out_md.write_text(md_text, encoding="utf-8")
    out_quality.write_text(
        json.dumps({
            "phase": "8_5f_founder_report_generation_quality",
            "completed_at": datetime.now(UTC).isoformat(),
            "simulation_id": report.simulation_id,
            "run_scope_id": report.run_scope_id,
            "scores": json.loads(qual_eval.model_dump_json()),
            "secret_scan": {
                "is_clean": secrets_clean,
                "finding_count": len(secret_findings),
            },
        }, indent=2, default=str), encoding="utf-8",
    )

    # 7. Final summary
    print("\n" + "=" * 72)
    print(f"Phase {PHASE_LABEL} — Founder-facing report")
    print("=" * 72)
    print(f"product: {report.product_name}")
    print(f"run_scope_id: {report.run_scope_id}")
    print(f"simulation_id: {report.simulation_id}")
    print(f"db_unchanged: {db_unchanged}")
    print(
        f"top_objections: {len(report.top_objections)} | "
        f"top_persuasion_levers: {len(report.top_persuasion_levers)} | "
        f"competitor_comparisons: {len(report.competitor_comparison)}"
    )
    print(
        f"positioning: {len(report.positioning_recommendations)} | "
        f"product_offer: {len(report.product_offer_recommendations)} | "
        f"tests: {len(report.what_to_test_next)}"
    )
    print(
        f"quality.aggregate_score: {qual_eval.aggregate_score} | "
        f"quality.ready_state: {qual_eval.ready_state}"
    )
    print(f"secrets_clean: {secrets_clean}")
    print(
        f"ready_for_fresh_end_to_end_test: "
        f"{report_dict['ready_for_fresh_end_to_end_test']}"
    )
    print(f"\n→ JSON: {out_json}")
    print(f"→ Markdown: {out_md}")
    print(f"→ Quality JSON: {out_quality}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
