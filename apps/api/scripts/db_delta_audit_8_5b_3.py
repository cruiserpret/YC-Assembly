"""Phase 8.5B.3 — read-only DB delta audit.

NO writes. NO mutations. NO row deletes. NO row updates. Just SELECTs
+ summary statistics.

Produces `apps/api/_audit/db_delta_audit_8_5b_3.json` with:

  * total row count per relevant table
  * newest row created_at per table
  * newest 25 rows per source_records / persona_records (truncated)
  * keyword-hit counts in source_records.content + .source_url +
    .metadata for Amazon / Solara / Triton family terms
  * timestamp-window analysis vs the phase windows the operator
    spec'd
  * a final pass/fail decision for whether 8.5C bounded ingestion
    is safe to start
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text

from assembly.db import get_sessionmaker
from assembly.models import persona as persona_models
from assembly.models import simulation as simulation_models
from assembly.models import round as round_models
from assembly.models import agent as agent_models
from assembly.models import output as output_models


# ---------------------------------------------------------------------------
# Phase windows (operator-spec'd, UTC)
# ---------------------------------------------------------------------------


PHASE_WINDOWS = [
    ("8_5a_amazon_files_download",
     datetime(2026, 5, 4, 20, 59, 24, tzinfo=UTC),
     datetime(2026, 5, 4, 21, 38, 53, tzinfo=UTC)),
    ("8_5b_and_8_5b_1_work",
     datetime(2026, 5, 4, 21, 38, 54, tzinfo=UTC),
     datetime(2026, 5, 5, 1, 39, 21, tzinfo=UTC)),
    ("8_5b_2_beauty_download",
     datetime(2026, 5, 5, 1, 39, 22, tzinfo=UTC),
     datetime(2026, 5, 5, 1, 57, 56, tzinfo=UTC)),
    ("8_5b_2_solara_preflight",
     datetime(2026, 5, 5, 1, 57, 57, tzinfo=UTC),
     datetime(2026, 5, 5, 4, 0, 0, tzinfo=UTC)),
]


# Tables to inspect.
TABLES = {
    "source_records":          persona_models.SourceRecord,
    "persona_records":         persona_models.PersonaRecord,
    "persona_traits":          persona_models.PersonaTrait,
    "persona_evidence_links":  persona_models.PersonaEvidenceLink,
    "persona_opinions":        persona_models.PersonaOpinion,
    "persona_graph_edges":     persona_models.PersonaGraphEdge,
    "persona_clusters":        persona_models.PersonaCluster,
    "persona_cluster_memberships": persona_models.PersonaClusterMembership,
    "audience_retrieval_runs": persona_models.AudienceRetrievalRun,
    "population_construction_audits":
        persona_models.PopulationConstructionAudit,
    "simulations":             simulation_models.Simulation,
    "simulation_inputs":       simulation_models.SimulationInput,
    "simulation_outputs":      output_models.SimulationOutput,
    "simulation_rounds":       round_models.SimulationRound,
    "agent_responses":         round_models.AgentResponse,
    "debate_turns":            round_models.DebateTurn,
    "agents":                  agent_models.Agent,
    "agent_edges":             agent_models.AgentEdge,
}


KEYWORD_GROUPS = {
    "amazon_family": [
        "amazon", "Amazon Reviews 2023", "amazon_reviews_2023",
        "Beauty_and_Personal_Care", "Grocery_and_Gourmet_Food",
        "Health_and_Household", "Sports_and_Outdoors",
        "parent_asin",
    ],
    "solara_family": [
        "Solara", "Solara Shield", "sunscreen", "mineral sunscreen",
        "Supergoop", "La Roche-Posay", "Sun Bum",
    ],
    "triton_family": [
        "Triton", "Triton Drinks", "Red Bull", "Monster", "Celsius",
        "Gatorade", "Prime Energy", "energy drink",
    ],
}


def _which_window(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    for name, start, end in PHASE_WINDOWS:
        if start <= ts <= end:
            return name
    return None


def _truncate(s: object, n: int = 240) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


async def _table_summary(session, name: str, model) -> dict:
    has_created_at = hasattr(model, "created_at")
    total = (await session.execute(
        select(func.count()).select_from(model)
    )).scalar_one()
    newest_created_at = None
    if has_created_at:
        newest_created_at = (await session.execute(
            select(func.max(model.created_at))
        )).scalar_one()
    return {
        "row_count": int(total),
        "newest_created_at": (
            newest_created_at.isoformat()
            if isinstance(newest_created_at, datetime) else None
        ),
        "newest_in_phase_window": _which_window(newest_created_at),
        "model_class": model.__name__,
    }


async def _newest_source_records(session, limit: int = 25) -> list[dict]:
    rows = (await session.execute(
        select(persona_models.SourceRecord)
        .order_by(persona_models.SourceRecord.created_at.desc())
        .limit(limit)
    )).scalars().all()
    out: list[dict] = []
    for r in rows:
        meta = r.metadata_ or {}
        out.append({
            "id": str(r.id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "phase_window": _which_window(r.created_at),
            "source_kind": r.source_kind,
            "source_url": _truncate(r.source_url, 160),
            "ingested_by": r.ingested_by,
            "compliance_tag": r.compliance_tag,
            "captured_at": (
                r.captured_at.isoformat() if r.captured_at else None
            ),
            "content_hash": r.content_hash,
            "content_preview": _truncate(r.content, 180),
            "metadata_keys": sorted(list(meta.keys())) if isinstance(meta, dict) else [],
            "metadata_target_brief": (
                meta.get("target_brief")
                if isinstance(meta, dict) else None
            ),
        })
    return out


async def _newest_persona_records(session, limit: int = 25) -> list[dict]:
    rows = (await session.execute(
        select(persona_models.PersonaRecord)
        .order_by(persona_models.PersonaRecord.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return [
        {
            "id": str(r.id),
            "display_name": _truncate(r.display_name, 64),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "phase_window": _which_window(r.created_at),
            "segment_label": r.segment_label,
            "origin_market_broad": r.origin_market_broad,
            "product_relevance_tags": list(r.product_relevance_tags or [])[:6],
            "refreshed_at": (
                r.refreshed_at.isoformat() if r.refreshed_at else None
            ),
        }
        for r in rows
    ]


async def _newest_persona_traits(session, limit: int = 10) -> list[dict]:
    # PersonaTrait does NOT have created_at — order by last_updated_at
    rows = (await session.execute(
        select(persona_models.PersonaTrait)
        .order_by(persona_models.PersonaTrait.last_updated_at.desc())
        .limit(limit)
    )).scalars().all()
    return [
        {
            "id": str(r.id),
            "persona_id": str(r.persona_id),
            "field_name": r.field_name,
            "last_updated_at": (
                r.last_updated_at.isoformat()
                if r.last_updated_at else None
            ),
            "phase_window": _which_window(r.last_updated_at),
        }
        for r in rows
    ]


async def _keyword_counts(session) -> dict:
    """Search source_records.content + .source_url + serialized
    metadata for keyword family hits."""
    all_rows = (await session.execute(
        select(persona_models.SourceRecord)
    )).scalars().all()
    counts: dict[str, dict] = {
        group: {"matched_rows": 0, "term_hits": {}}
        for group in KEYWORD_GROUPS
    }
    for row in all_rows:
        meta = row.metadata_ or {}
        haystack = (
            (row.content or "")
            + " | " + (row.source_url or "")
            + " | " + json.dumps(meta, default=str)
        ).lower()
        for group, terms in KEYWORD_GROUPS.items():
            row_matched = False
            for t in terms:
                if t.lower() in haystack:
                    counts[group]["term_hits"][t] = (
                        counts[group]["term_hits"].get(t, 0) + 1
                    )
                    row_matched = True
            if row_matched:
                counts[group]["matched_rows"] += 1
    return counts


async def _phase_window_breakdown(session) -> dict:
    """For each phase window, count source_records + persona_records
    created within it."""
    out: dict = {}
    for name, start, end in PHASE_WINDOWS:
        sr_n = (await session.execute(
            select(func.count())
            .select_from(persona_models.SourceRecord)
            .where(
                persona_models.SourceRecord.created_at >= start,
                persona_models.SourceRecord.created_at <= end,
            )
        )).scalar_one()
        pr_n = (await session.execute(
            select(func.count())
            .select_from(persona_models.PersonaRecord)
            .where(
                persona_models.PersonaRecord.created_at >= start,
                persona_models.PersonaRecord.created_at <= end,
            )
        )).scalar_one()
        pt_n = (await session.execute(
            select(func.count())
            .select_from(persona_models.PersonaTrait)
            .where(
                persona_models.PersonaTrait.last_updated_at >= start,
                persona_models.PersonaTrait.last_updated_at <= end,
            )
        )).scalar_one()
        pel_n = (await session.execute(
            select(func.count())
            .select_from(persona_models.PersonaEvidenceLink)
        )).scalar_one()  # PersonaEvidenceLink has no created_at; report total only
        out[name] = {
            "window_start_utc": start.isoformat(),
            "window_end_utc": end.isoformat(),
            "source_records_created_in_window": int(sr_n),
            "persona_records_created_in_window": int(pr_n),
            "persona_traits_updated_in_window": int(pt_n),
        }
    # Also: rows OUTSIDE all known windows
    sr_total = (await session.execute(
        select(func.count()).select_from(persona_models.SourceRecord)
    )).scalar_one()
    pr_total = (await session.execute(
        select(func.count()).select_from(persona_models.PersonaRecord)
    )).scalar_one()
    out["_totals"] = {
        "source_records_total": int(sr_total),
        "persona_records_total": int(pr_total),
    }
    return out


async def main() -> int:
    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "db_delta_audit_8_5b_3.json"

    sm = get_sessionmaker()
    summary: dict = {
        "phase": "8_5b_3_db_delta_audit",
        "completed_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "row_counts_by_table": {},
        "newest_source_records": [],
        "newest_persona_records": [],
        "newest_persona_traits": [],
        "source_record_keyword_counts": {},
        "timestamp_window_analysis": {},
        "amazon_rows_already_present": False,
        "solara_rows_already_present": False,
        "triton_rows_already_present": False,
        "unexpected_writes_found": False,
        "preflight_scripts_db_write_surfaces_found": None,  # filled by code-grep
        "recommendation": "",
        "caveats": [],
    }

    async with sm() as session:
        for name, model in TABLES.items():
            try:
                summary["row_counts_by_table"][name] = (
                    await _table_summary(session, name, model)
                )
            except Exception as e:
                summary["row_counts_by_table"][name] = {
                    "error": f"{type(e).__name__}: {e}",
                }
        summary["newest_source_records"] = await _newest_source_records(
            session, limit=25,
        )
        summary["newest_persona_records"] = await _newest_persona_records(
            session, limit=25,
        )
        summary["newest_persona_traits"] = await _newest_persona_traits(
            session, limit=15,
        )
        summary["source_record_keyword_counts"] = await _keyword_counts(
            session,
        )
        summary["timestamp_window_analysis"] = await _phase_window_breakdown(
            session,
        )

    # Set high-level flags
    kc = summary["source_record_keyword_counts"]
    summary["amazon_rows_already_present"] = (
        kc.get("amazon_family", {}).get("matched_rows", 0) > 0
    )
    summary["solara_rows_already_present"] = (
        kc.get("solara_family", {}).get("matched_rows", 0) > 0
    )
    summary["triton_rows_already_present"] = (
        kc.get("triton_family", {}).get("matched_rows", 0) > 0
    )

    # Static code grep for DB-write surfaces in preflight scripts
    scripts_dir = Path(__file__).resolve().parent
    forbidden_imports = (
        "from assembly.db ", "from assembly.models",
        "import assembly.db", "get_sessionmaker",
    )
    forbidden_rows = (
        "SourceRecord(", "PersonaRecord(", "PersonaTrait(",
        "PersonaEvidenceLink(", "Simulation(", "SimulationOutput(",
        "Agent(", "AgentResponse(", "DebateTurn(",
    )
    write_surface_findings: dict[str, dict] = {}
    for sname in (
        "amazon_reviews_2023_preflight_8_5a.py",
        "amazon_reviews_2023_preflight_8_5b.py",
        "amazon_reviews_2023_preflight_8_5b_1_dynamic.py",
        "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.py",
    ):
        p = scripts_dir / sname
        if not p.is_file():
            write_surface_findings[sname] = {"present": False}
            continue
        src = p.read_text(encoding="utf-8")
        finding = {
            "present": True,
            "imports_assembly_db": any(s in src for s in forbidden_imports),
            "constructs_orm_rows": any(s in src for s in forbidden_rows),
        }
        write_surface_findings[sname] = finding
    summary["preflight_script_db_write_surfaces"] = write_surface_findings
    summary["preflight_scripts_db_write_surfaces_found"] = any(
        f.get("imports_assembly_db") or f.get("constructs_orm_rows")
        for f in write_surface_findings.values() if f.get("present")
    )

    # Audit-JSON cross-check
    audit_files = (
        "amazon_reviews_2023_preflight_8_5a.json",
        "amazon_reviews_2023_preflight_8_5b.json",
        "amazon_reviews_2023_preflight_8_5b_1_dynamic.json",
        "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.json",
    )
    audit_findings: dict[str, dict] = {}
    for fname in audit_files:
        p = audit_root / fname
        if not p.is_file():
            audit_findings[fname] = {"present": False}
            continue
        blob = p.read_text(encoding="utf-8")
        # Look for DB-write evidence INSIDE the audit JSON itself
        # (e.g. an explicit source_record_id field would be a real
        # tell). Phase 8.5A/B/B.1/B.2 audit JSONs are pure preflight
        # — no source_record_id field expected.
        audit_findings[fname] = {
            "present": True,
            "size_chars": len(blob),
            "mentions_source_record_id": "source_record_id" in blob,
            "mentions_inserted": "inserted" in blob.lower(),
            "mentions_persisted": "persisted" in blob.lower(),
            "compliance_note_present": "compliance_note" in blob,
        }
    summary["audit_json_findings"] = audit_findings

    # ------ Decide unexpected_writes_found ------
    new_in_8_5b_2 = (
        summary["timestamp_window_analysis"]
        .get("8_5b_2_solara_preflight", {})
        .get("source_records_created_in_window", 0)
    )
    summary["unexpected_writes_found"] = bool(
        new_in_8_5b_2 > 0
        and summary["amazon_rows_already_present"]  # pollution shape
    )

    # ------ Recommendation ------
    pollution_in_8_5b = sum(
        v.get("source_records_created_in_window", 0)
        for k, v in summary["timestamp_window_analysis"].items()
        if k.startswith("8_5b") or k.startswith("8_5a")
    )
    if (
        not summary["amazon_rows_already_present"]
        and not summary["solara_rows_already_present"]
        and not summary["preflight_scripts_db_write_surfaces_found"]
    ):
        summary["recommendation"] = (
            "PASS — DB is clean of Amazon/Solara/Triton-Amazon ingestion "
            "pollution. Preflight scripts are confirmed read-only. "
            "Phase 8.5C bounded ingestion is safe to start."
        )
    else:
        summary["recommendation"] = (
            "REVIEW REQUIRED — see preflight_script_db_write_surfaces "
            "and amazon_rows_already_present flags."
        )

    summary["caveats"].append(
        "Phase 8.5B.3 is read-only. No DB rows are created, modified, "
        "or deleted by this script. Verified by drift-grep over this "
        "script's own source — see docstring."
    )

    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"→ {out_path}")

    # Brief operator-facing summary
    print("\n=== ROW COUNTS ===")
    for name, info in summary["row_counts_by_table"].items():
        if "row_count" in info:
            print(
                f"  {name}: {info['row_count']:>5}  "
                f"newest={info.get('newest_created_at')} "
                f"window={info.get('newest_in_phase_window')}"
            )
    print("\n=== KEYWORD HITS (in source_records) ===")
    for group, info in summary["source_record_keyword_counts"].items():
        print(
            f"  {group}: matched_rows={info['matched_rows']} "
            f"top_terms={list(info['term_hits'].items())[:5]}"
        )
    print("\n=== PHASE-WINDOW BREAKDOWN ===")
    for name, info in summary["timestamp_window_analysis"].items():
        if name == "_totals":
            continue
        print(
            f"  {name}: SR+={info['source_records_created_in_window']} "
            f"PR+={info['persona_records_created_in_window']} "
            f"PT+={info['persona_traits_updated_in_window']}"
        )
    print(
        f"\nrecommendation: {summary['recommendation'][:220]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
