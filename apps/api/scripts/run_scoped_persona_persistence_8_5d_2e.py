"""Phase 8.5D.2E — bounded run-scoped SourceRecord + Persona persistence.

Reads the 8.5D.1E audit, validates all readiness gates, resolves
each compressed candidate's planned source records to real
SourceRecord rows (insert-or-reuse via content_hash), then inserts
exactly 7 PersonaRecord rows + their PersonaTrait rows + their
PersonaEvidenceLink rows in ONE bounded transaction.

Default mode: --dry-run (no DB writes). Pass --commit to actually
persist.

NO LLM. NO Brave / YouTube / external API calls. NO simulation. NO
graph rows. NO frontend writes.

Universal trait-field mapping (closed set required by DB CHECK):
The compressed candidates carry product-shaped trait_names like
`preference_performance_use_case` or `current_alternative_competitor`.
The script maps every candidate trait_name to one of the 10 closed
trait field names by prefix — universal, no per-product code path.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.pipeline.persona.anonymization import generate_display_name
from assembly.pipeline.persona.constants import PERSONA_FIELD_NAMES
from assembly.sources.persona_role_planner import (
    validate_launch_state_claims,
)
from assembly.sources.persona_role_planner.schemas import (
    InferredPersonaTrait, PersonaCandidate,
)


PHASE_LABEL = "8.5D.2E"
TARGET_BRIEF_ID = "strideshield"
LAUNCH_STATE = "unlaunched"
PRODUCT_NAME = "StrideShield"
INGESTED_BY = (
    "assembly_phase_8_5d2e_strideshield_run_scoped_persistence"
)
EXPECTED_COMPRESSED_COUNT = 7
MAX_TRAITS_PER_PERSONA_INPUT = 7  # before field-collapse merge


# Universal candidate-trait → closed-field mapper. Order matters
# (longest prefix wins; final fallback is "interests"). NEVER hardcoded
# per-product.
_TRAIT_PREFIX_MAP: tuple[tuple[str, str], ...] = (
    ("current_alternative", "current_alternatives"),
    ("alternative_", "current_alternatives"),
    ("competitor_", "current_alternatives"),
    ("substitute_", "current_alternatives"),
    ("price_", "price_sensitivity"),
    ("budget_", "price_sensitivity"),
    ("cost_", "price_sensitivity"),
    ("willingness_to_pay", "price_sensitivity"),
    ("trust_", "trust_triggers"),
    ("proof_", "trust_triggers"),
    ("credibility_", "trust_triggers"),
    ("required_credibility", "trust_triggers"),
    ("geography_", "geography_broad"),
    ("region_", "geography_broad"),
    ("location_", "geography_broad"),
    ("role_", "role_or_context"),
    ("context_", "role_or_context"),
    ("occupation_", "role_or_context"),
    ("profession_", "role_or_context"),
    ("influence_", "influence_signals"),
    ("susceptibility", "influence_signals"),
    ("status_", "influence_signals"),
    ("communication_", "communication_style"),
    ("voice_", "communication_style"),
    ("tone_", "communication_style"),
    ("buying_", "buying_constraints"),
    ("purchase_", "buying_constraints"),
    ("constraint_", "buying_constraints"),
    ("switching_", "buying_constraints"),
    ("objection_", "objection_patterns"),
    ("concern_", "objection_patterns"),
    ("complaint_", "objection_patterns"),
    ("fear_", "objection_patterns"),
    ("preference_", "interests"),
    ("interest_", "interests"),
    ("behavior_", "interests"),
    ("habit_", "interests"),
    ("use_case", "interests"),
)


def _map_trait_field(trait_name: str) -> str:
    """Map a candidate trait_name to one of the 10 closed-set fields.

    Universal: prefix match against `_TRAIT_PREFIX_MAP`. Fallback is
    `interests` (the broadest semantic bucket). Never product-specific.
    """
    if not trait_name:
        return "interests"
    low = trait_name.lower().strip()
    for prefix, field in _TRAIT_PREFIX_MAP:
        if low.startswith(prefix):
            return field
    return "interests"


def _support_level_from_confidence(confidence: str) -> str:
    """high → direct, medium/low → inferred. The DB CHECK requires
    confidence > 0 + value not null + source_ids non-empty for
    direct/inferred — we already guarantee that for compressed
    candidates."""
    return "direct" if confidence == "high" else "inferred"


def _confidence_decimal(confidence: str) -> Decimal:
    return {
        "high": Decimal("0.9"),
        "medium": Decimal("0.6"),
        "low": Decimal("0.3"),
    }.get(confidence, Decimal("0.5"))


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


async def _read_baseline_counts(sessionmaker) -> dict[str, int]:
    async with sessionmaker() as session:
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
    return {
        "source_records": int(sr), "persona_records": int(pr),
        "persona_traits": int(pt), "persona_evidence_links": int(pel),
    }


def _read_audit(name: str) -> dict[str, Any]:
    p = Path(__file__).resolve().parent.parent / "_audit" / name
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _planned_source_records_index(
    audit_1d: dict[str, Any],
    audit_1c: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return { sid → planned_source_record_dict } across both
    upstream audits. The 8.5D.1D audit holds Brave/YouTube planned
    rows; the 8.5D.1C audit's SELECTED ingestion decisions hold the
    Amazon planned rows."""
    out: dict[str, dict[str, Any]] = {}
    for sr in audit_1d.get("planned_source_records") or []:
        sid = sr.get("planned_source_record_id_synthetic")
        if sid:
            out[sid] = sr
    for d in audit_1c.get("planned_source_records") or []:
        if d.get("decision") != "SELECTED":
            continue
        psr = d.get("planned_source_record_preview")
        if not psr:
            continue
        md = psr.get("metadata") or {}
        sid = (
            md.get("planned_source_record_id_synthetic")
            or psr.get("source_url")
        )
        if not sid:
            continue
        # Wrap into the 8.5D.1D-style shape so downstream code can
        # consume both uniformly.
        out[sid] = {
            "planned_source_record_id_synthetic": sid,
            "source_kind": "amazon_reviews_2023_local",
            "source_url": psr.get("source_url"),
            "content_preview": psr.get("content_preview"),
            "content_length": psr.get("content_length"),
            "content_hash": psr.get("content_hash"),
            "language": psr.get("language") or "en",
            "metadata": {
                **md,
                "provider": "amazon_reviews_2023_local",
                "source_dataset": "amazon_reviews_2023",
                "source_is_historical": True,
            },
            "ingested_by": INGESTED_BY,
            "compliance_tag": "open_dataset",
            "captured_at": psr.get("captured_at", ""),
            "pii_redaction_status": psr.get(
                "pii_redaction_status", "passed",
            ),
            "sensitive_scan_status": psr.get(
                "sensitive_scan_status", "passed",
            ),
            "user_handle_hash": None,
        }
    return out


_FORBIDDEN_RAW_USER_ID_KEYS = (
    "raw_user_id", "channel_id", "channelId", "author_channel_id",
    "authorChannelId", "user_id", "reviewer_id",
)
_FORBIDDEN_IMAGE_URL_KEYS = (
    "image_url", "image", "thumbnail", "thumbnail_url", "profile_image",
    "profile_picture", "avatar_url", "photo_url",
)


def _strip_forbidden_metadata(md: dict[str, Any]) -> dict[str, Any]:
    """Universal: drop any metadata key that names a raw user ID or
    image URL. The 8.5A adapters already filter these at retrieval
    time; this is a defense-in-depth pass before any DB write."""
    cleaned: dict[str, Any] = {}
    for k, v in md.items():
        if k in _FORBIDDEN_RAW_USER_ID_KEYS:
            continue
        if k in _FORBIDDEN_IMAGE_URL_KEYS:
            continue
        cleaned[k] = v
    return cleaned


def _normalize_compliance_tag(planned: dict[str, Any]) -> str:
    """Coerce the planned `compliance_tag` to the closed DB CHECK set."""
    tag = (planned.get("compliance_tag") or "").strip()
    if tag in (
        "public_api", "public_html", "open_dataset",
        "open_aggregate", "manual_seed",
    ):
        return tag
    sk = planned.get("source_kind") or ""
    if sk.startswith("youtube"):
        return "public_api"
    if sk == "brave_search_result":
        return "public_html"
    if sk == "amazon_reviews_2023_local":
        return "open_dataset"
    return "manual_seed"


def _build_source_record_for_insert(
    *,
    planned: dict[str, Any],
    compressed_candidate_ids_using_this: list[str],
) -> dict[str, Any]:
    """Convert a planned source row to the shape we'll insert into
    `source_records`. Strips forbidden metadata, stamps the
    persistence-phase tags, and recomputes content_hash deterministically.
    """
    content = planned.get("content_preview") or ""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    md = _strip_forbidden_metadata(dict(planned.get("metadata") or {}))
    sk = planned.get("source_kind") or "manual_seed"
    md["target_brief"] = TARGET_BRIEF_ID
    md["product_name"] = PRODUCT_NAME
    md["launch_state"] = LAUNCH_STATE
    md["phase_origin"] = "8.5D.1D" if sk != "amazon_reviews_2023_local" else "8.5D.1C"
    md["persisted_in_phase"] = PHASE_LABEL
    md["compressed_candidate_ids"] = list(compressed_candidate_ids_using_this)
    if sk == "brave_search_result":
        md.setdefault("source_provider", "brave_search")
        md.setdefault("source_is_live_web", True)
        md["source_caveat"] = (
            "Brave Search result/snippet captured during Assembly "
            "source-expansion dry run; not a full-page scrape unless "
            "explicitly stored."
        )
    elif sk in ("youtube_video_result", "youtube_comment_result"):
        md.setdefault("provider", "youtube_data_api")
        md["source_caveat"] = (
            "YouTube Data API public metadata/comment evidence; "
            "author identifiers not stored."
        )
    elif sk == "amazon_reviews_2023_local":
        md.setdefault("source_dataset", "amazon_reviews_2023")
        md.setdefault("source_is_historical", True)
        md["source_caveat"] = (
            "Amazon Reviews 2023 local historical dataset; not "
            "live/current Amazon data."
        )
    captured_at_str = planned.get("captured_at") or datetime.now(UTC).isoformat()
    try:
        captured_at = datetime.fromisoformat(captured_at_str)
    except Exception:
        captured_at = datetime.now(UTC)

    return {
        "source_kind": sk,
        "source_url": planned.get("source_url"),
        "captured_at": captured_at,
        "content": content,
        "content_hash": content_hash,
        "language": planned.get("language") or "en",
        "metadata": md,
        "ingested_by": INGESTED_BY,
        "compliance_tag": _normalize_compliance_tag(planned),
        "user_handle_hash": None,
        "pii_redaction_status": planned.get(
            "pii_redaction_status", "passed",
        ) or "passed",
        "sensitive_scan_status": planned.get(
            "sensitive_scan_status", "passed",
        ) or "passed",
    }


def _candidate_to_persona_obj(c: dict[str, Any]) -> PersonaCandidate:
    """Re-hydrate a compressed-candidate dict to PersonaCandidate so
    the launch-state validator can run."""
    return PersonaCandidate(
        candidate_id=c["candidate_id"],
        target_brief=c["target_brief"],
        generated_for_phase=c.get("generated_for_phase", PHASE_LABEL),
        inferred_persona_role=c.get(
            "normalized_primary_role",
            c.get("pre_normalization_role", ""),
        ),
        secondary_persona_roles=list(c.get("secondary_persona_roles") or []),
        role_inference_basis=list(c.get("role_inference_basis") or []),
        segment_label=c.get("segment_label") or "",
        source_record_ids=list(c.get("source_record_ids") or []),
        evidence_summary=c.get("evidence_summary") or "",
        evidence_snippets=list(c.get("evidence_snippets") or []),
        inferred_traits=[
            InferredPersonaTrait(**t) for t in c.get("inferred_traits") or []
        ],
        inferred_preferences=list(c.get("inferred_preferences") or []),
        inferred_objections=list(c.get("inferred_objections") or []),
        inferred_behaviors=list(c.get("inferred_behaviors") or []),
        hypothetical_target_product_reaction=(
            c.get("hypothetical_target_product_reaction") or ""
        ),
        confidence=c.get("confidence", "medium"),
        evidence_strength=c.get("evidence_strength", "moderate"),
        caveats=list(c.get("caveats") or []),
        simulation_usefulness_summary=c.get(
            "simulation_usefulness_summary", "",
        ),
        persistence_recommendation=c.get(
            "persistence_recommendation", "DEFER",
        ),
    )


def _validate_compressed_set(
    *,
    audit_1e: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Re-check every readiness gate from 8.5D.1E + every per-candidate
    invariant. Returns (ok, blockers)."""
    blockers: list[str] = []
    if not audit_1e:
        return False, ["no 8.5D.1E audit JSON found"]
    if not audit_1e.get("ready_for_mutating_phase"):
        blockers.append(
            f"ready_for_mutating_phase={audit_1e.get('ready_for_mutating_phase')!r}; "
            "expected true."
        )
    cands = audit_1e.get("compressed_persona_candidates") or []
    if len(cands) != EXPECTED_COMPRESSED_COUNT:
        blockers.append(
            f"compressed_candidate_count={len(cands)}; "
            f"expected {EXPECTED_COMPRESSED_COUNT}."
        )
    rec = (
        (audit_1e.get("diversity_after") or {})
        .get("mutating_persistence_recommendation")
    )
    if rec != "READY":
        blockers.append(
            f"diversity_after.mutating_persistence_recommendation={rec!r}; "
            "expected 'READY'."
        )
    for c in cands:
        cid = c.get("candidate_id") or "<unknown>"
        if c.get("scope") != "brief_scoped":
            blockers.append(f"{cid}: scope != brief_scoped")
        if c.get("persistence_status") != "dry_run_only":
            blockers.append(
                f"{cid}: persistence_status != dry_run_only"
            )
        if not c.get("not_global_persona", False):
            blockers.append(f"{cid}: not_global_persona is false")
        if c.get("target_brief") != TARGET_BRIEF_ID:
            blockers.append(
                f"{cid}: target_brief={c.get('target_brief')!r}"
            )
        if not (c.get("source_record_ids") or []):
            blockers.append(f"{cid}: source_record_ids empty")
        if len(c.get("inferred_traits") or []) < 2:
            blockers.append(f"{cid}: < 2 inferred_traits")
        if not (c.get("evidence_snippets") or []):
            blockers.append(f"{cid}: evidence_snippets empty")
        if not c.get("normalized_primary_role"):
            blockers.append(f"{cid}: normalized_primary_role missing")
        if not c.get("evidence_theme"):
            blockers.append(f"{cid}: evidence_theme missing")
        if not c.get("source_provider_family"):
            blockers.append(f"{cid}: source_provider_family missing")
    if audit_1e.get("launch_state") != LAUNCH_STATE:
        blockers.append(
            f"launch_state={audit_1e.get('launch_state')!r}"
        )
    # Re-run launch-state validator. If a candidate is missing the
    # minimal fields needed to build a PersonaCandidate, that's
    # already caught by the structural blockers above — skip the
    # validator for it rather than crashing.
    for c in cands:
        cid = c.get("candidate_id")
        if not cid:
            continue
        if not (c.get("source_record_ids") or []):
            continue
        if not (c.get("evidence_snippets") or []):
            continue
        if len(c.get("inferred_traits") or []) < 2:
            continue
        try:
            cand_obj = _candidate_to_persona_obj(c)
        except Exception as e:
            blockers.append(
                f"{cid}: failed to build PersonaCandidate for "
                f"launch-state check: {type(e).__name__}: {e}"
            )
            continue
        v = validate_launch_state_claims(
            candidate=cand_obj, launch_state=LAUNCH_STATE,
            product_name=PRODUCT_NAME,
        )
        if not v.is_valid:
            blockers.append(
                f"{cid}: launch-state validator FAILED — "
                f"{v.forbidden_phrases_matched[:2]}"
            )
    return len(blockers) == 0, blockers


def _make_run_scope_id(audit_1e: dict[str, Any]) -> str:
    """Stable run-scope ID for this persistence run. SHA of the
    8.5D.1E plan + product + launch state + today's date."""
    payload = "|".join((
        audit_1e.get("compression_summary", {}).get("after_count", "") and "x" or "",
        TARGET_BRIEF_ID, PRODUCT_NAME, LAUNCH_STATE,
        datetime.now(UTC).date().isoformat(),
        ",".join(sorted(
            c["candidate_id"]
            for c in audit_1e.get("compressed_persona_candidates") or []
        )),
    ))
    return "run_8_5d_2e_" + hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()[:12]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Phase {PHASE_LABEL} — bounded run-scoped persona persistence."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Default. No DB writes; just preflight + expected deltas.",
    )
    mode.add_argument(
        "--commit", action="store_true",
        help="Persist into the DB inside one bounded transaction.",
    )
    parser.add_argument(
        "--input-audit",
        default="persona_set_compression_dry_run_8_5d_1e.json",
    )
    args = parser.parse_args()
    do_commit = bool(args.commit)
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "run_scoped_persona_persistence_8_5d_2e.json"

    audit_1e = _read_audit(args.input_audit)
    audit_1d = _read_audit(
        "fresh_product_source_expansion_dry_run_8_5d_1d.json",
    )
    audit_1c = _read_audit(
        "fresh_product_persona_diversity_fix_8_5d_1c.json",
    )

    sm = get_sessionmaker()
    db_pre = await _read_baseline_counts(sm)
    print(f"DB live baseline: {db_pre}")

    ok, blockers = _validate_compressed_set(audit_1e=audit_1e)
    if not ok:
        print("REFUSED: pre-flight validation failed:")
        for b in blockers:
            print(f"  - {b}")
        out_path.write_text(json.dumps({
            "phase": "8_5d_2e_run_scoped_persona_persistence",
            "completed_at": datetime.now(UTC).isoformat(),
            "transaction_committed": False,
            "rollback_happened": False,
            "rollback_reason": "preflight_validation_failed",
            "blockers": blockers,
            "live_baseline_counts": db_pre,
        }, indent=2), encoding="utf-8")
        return 2

    cands = audit_1e["compressed_persona_candidates"]
    planned_index = _planned_source_records_index(audit_1d, audit_1c)

    # Build (sid → list of compressed candidate_ids using it)
    sid_to_cand_ids: dict[str, list[str]] = {}
    for c in cands:
        for sid in c.get("source_record_ids") or []:
            sid_to_cand_ids.setdefault(sid, []).append(c["candidate_id"])

    # Resolve each unique sid to a real SourceRecord ID
    unique_sids = sorted(sid_to_cand_ids.keys())
    unresolved: list[str] = []
    sid_to_real_id: dict[str, str] = {}  # str(uuid)
    inserted_source_ids: list[str] = []
    reused_source_ids: list[str] = []

    run_scope_id = _make_run_scope_id(audit_1e)

    # Build the expected deltas before opening the transaction
    expected_persona_count = len(cands)
    # Per-candidate: how many trait rows AFTER closed-field collapse
    persona_blueprints: list[dict[str, Any]] = []
    for c in cands:
        traits_in = (c.get("inferred_traits") or [])[
            :MAX_TRAITS_PER_PERSONA_INPUT
        ]
        # Collapse by closed field
        collapsed: dict[str, dict[str, Any]] = {}
        for t in traits_in:
            field = _map_trait_field(t.get("trait_name", ""))
            if field not in PERSONA_FIELD_NAMES:
                continue
            entry = collapsed.setdefault(field, {
                "field_name": field,
                "values": [],
                "excerpts": [],
                "confidences": [],
                "source_sids": set(),
                "trait_names": [],
            })
            entry["values"].append(t.get("trait_value") or "")
            entry["excerpts"].append(t.get("evidence_excerpt") or "")
            entry["confidences"].append(t.get("confidence") or "medium")
            entry["source_sids"].add(
                t.get("evidence_source_record_id") or ""
            )
            entry["trait_names"].append(t.get("trait_name") or "")
        # Universal fallback: when distinct closed fields < 2, the
        # persona's own normalized role IS evidence-supported (it was
        # inferred from the source by the persona-role planner). Add
        # a `role_or_context` trait whose value is the role and whose
        # rationale is the role_inference_basis. This keeps the ≥2
        # traits invariant without inventing unsupported data.
        if "role_or_context" not in collapsed and len(collapsed) < 2:
            role_value = (
                c.get("normalized_primary_role")
                or c.get("pre_normalization_role")
                or ""
            )
            if role_value:
                role_excerpt = " | ".join(
                    str(b) for b in (c.get("role_inference_basis") or [])
                )[:300] or c.get("evidence_summary") or role_value
                role_sids: set[str] = set(
                    c.get("source_record_ids") or []
                )
                collapsed["role_or_context"] = {
                    "field_name": "role_or_context",
                    "values": [role_value],
                    "excerpts": [role_excerpt],
                    "confidences": [c.get("confidence", "medium")],
                    "source_sids": role_sids,
                    "trait_names": [
                        f"persona_role::{role_value}"
                    ],
                }
        persona_blueprints.append({
            "candidate": c, "collapsed_traits": collapsed,
        })

    expected_trait_count = sum(
        len(b["collapsed_traits"]) for b in persona_blueprints
    )
    # Each (persona, source_sid, field) is a candidate evidence link
    # — but only if the source resolves AND the field is the trait's
    # mapped field for that persona. Compute upper bound then refine
    # post-resolve.

    print(
        f"Compressed candidates: {len(cands)} | "
        f"unique planned source IDs to resolve: {len(unique_sids)} | "
        f"expected trait rows after closed-field collapse: "
        f"{expected_trait_count}"
    )

    # Open the bounded transaction
    transaction_committed = False
    rollback_happened = False
    rollback_reason: str | None = None
    persisted_personas: list[dict[str, Any]] = []
    persisted_traits_summary: list[dict[str, Any]] = []
    persisted_links_summary: list[dict[str, Any]] = []
    expected_link_count = 0

    async with sm() as session:
        try:
            async with session.begin():
                # 1. Resolve each unique sid: insert-or-reuse by content_hash
                for sid in unique_sids:
                    planned = planned_index.get(sid)
                    if planned is None:
                        unresolved.append(sid)
                        continue
                    sr_payload = _build_source_record_for_insert(
                        planned=planned,
                        compressed_candidate_ids_using_this=(
                            sid_to_cand_ids[sid]
                        ),
                    )
                    # Check for existing row by (source_kind, content_hash)
                    existing = (await session.execute(
                        select(SourceRecord).where(
                            SourceRecord.source_kind
                            == sr_payload["source_kind"],
                            SourceRecord.content_hash
                            == sr_payload["content_hash"],
                        )
                    )).scalar_one_or_none()
                    if existing is not None:
                        sid_to_real_id[sid] = str(existing.id)
                        reused_source_ids.append(str(existing.id))
                        continue
                    # Insert new SourceRecord
                    new_id = uuid.uuid4()
                    new_sr = SourceRecord(
                        id=new_id,
                        source_kind=sr_payload["source_kind"],
                        source_url=sr_payload["source_url"],
                        captured_at=sr_payload["captured_at"],
                        content=sr_payload["content"],
                        content_hash=sr_payload["content_hash"],
                        language=sr_payload["language"],
                        metadata_=sr_payload["metadata"],
                        ingested_by=sr_payload["ingested_by"],
                        compliance_tag=sr_payload["compliance_tag"],
                        user_handle_hash=None,
                        pii_redaction_status=sr_payload[
                            "pii_redaction_status"
                        ],
                        sensitive_scan_status=sr_payload[
                            "sensitive_scan_status"
                        ],
                    )
                    session.add(new_sr)
                    sid_to_real_id[sid] = str(new_id)
                    inserted_source_ids.append(str(new_id))

                if unresolved:
                    rollback_reason = (
                        f"{len(unresolved)} planned source ID(s) "
                        "could not be resolved to a planned source row."
                    )
                    raise RuntimeError(rollback_reason)

                # 2. Pre-pass: assign each blueprint a persona_id and
                # build the PersonaRecord row. Flush so the FK-dependent
                # trait + evidence_link inserts can resolve their
                # parent_id at the DB level.
                now = datetime.now(UTC)
                for blueprint in persona_blueprints:
                    cand = blueprint["candidate"]
                    persona_id = uuid.uuid4()
                    blueprint["persona_id"] = persona_id
                    display_name = generate_display_name(seed=str(persona_id))
                    blueprint["display_name"] = display_name
                    relevance_tags = [
                        f"target_brief:{TARGET_BRIEF_ID}",
                        f"product_name:{PRODUCT_NAME}",
                        f"launch_state:{LAUNCH_STATE}",
                        f"phase:{PHASE_LABEL}",
                        f"run_scope_id:{run_scope_id}",
                        f"normalized_primary_role:"
                        f"{cand['normalized_primary_role']}",
                        f"evidence_theme:{cand.get('evidence_theme', '')}",
                        f"source_provider_family:"
                        f"{cand.get('source_provider_family', '')}",
                        f"compressed_candidate_id:{cand['candidate_id']}",
                        "scope:run_scoped_brief_scoped",
                        "persistence_type:generated_simulation_artifact",
                        "not_global_persona:true",
                        (
                            "caveat:Generated for this StrideShield "
                            "simulation run from evidence; not a "
                            "permanent/global persona."
                        ),
                    ]
                    p = PersonaRecord(
                        id=persona_id,
                        display_name=display_name,
                        segment_label=(
                            cand.get("segment_label")
                            or cand["normalized_primary_role"]
                        )[:64],
                        origin_market_broad=None,
                        product_relevance_tags=relevance_tags,
                        influence_score=None,
                        susceptibility=None,
                        population_weight=Decimal("1.0"),
                        source_strength_score=None,
                        refreshed_at=now,
                    )
                    session.add(p)
                # Flush to satisfy persona_id FKs on traits + links
                await session.flush()

                # Now add traits + evidence_links (FK parents exist)
                for blueprint in persona_blueprints:
                    cand = blueprint["candidate"]
                    collapsed = blueprint["collapsed_traits"]
                    persona_id = blueprint["persona_id"]
                    display_name = blueprint["display_name"]
                    # Determine real source IDs this candidate uses
                    real_src_for_persona: list[uuid.UUID] = []
                    for sid in cand.get("source_record_ids") or []:
                        rid = sid_to_real_id.get(sid)
                        if rid:
                            real_src_for_persona.append(uuid.UUID(rid))
                    if not real_src_for_persona:
                        rollback_reason = (
                            f"candidate {cand['candidate_id']}: "
                            "no real source_record IDs resolved."
                        )
                        raise RuntimeError(rollback_reason)

                    # Persist collapsed traits for this persona
                    persisted_trait_fields_for_p: list[str] = []
                    for field_name, entry in collapsed.items():
                        # Collect real source IDs that contributed
                        contributing_real_ids: set[uuid.UUID] = set()
                        for sid in entry["source_sids"]:
                            rid = sid_to_real_id.get(sid)
                            if rid:
                                contributing_real_ids.add(uuid.UUID(rid))
                        # If no contributing source resolved, fall back
                        # to the persona's primary source IDs — every
                        # trait MUST have ≥1 source_id per DB CHECK.
                        if not contributing_real_ids:
                            contributing_real_ids = set(real_src_for_persona)
                        # Pick max confidence
                        max_conf = max(
                            (_confidence_decimal(c2)
                             for c2 in entry["confidences"]),
                            default=Decimal("0.5"),
                        )
                        # Pick strongest support level
                        sup = (
                            "direct" if "high" in entry["confidences"]
                            else "inferred"
                        )
                        # Merge values (dedup)
                        merged_value = "; ".join(
                            sorted({v for v in entry["values"] if v})
                        )[:1000]
                        if not merged_value:
                            merged_value = entry["trait_names"][0] or "evidence"
                        rationale_blob = " | ".join(
                            f"{tn}: {ex[:300]}"
                            for tn, ex in zip(
                                entry["trait_names"], entry["excerpts"],
                            )
                            if (ex or "").strip()
                        )[:2000] or None
                        trait_id = uuid.uuid4()
                        t = PersonaTrait(
                            id=trait_id,
                            persona_id=persona_id,
                            field_name=field_name,
                            value=merged_value,
                            support_level=sup,
                            source_ids=sorted(contributing_real_ids),
                            confidence=max_conf,
                            rationale=rationale_blob,
                            last_updated_at=now,
                        )
                        session.add(t)
                        persisted_traits_summary.append({
                            "persona_record_id": str(persona_id),
                            "trait_id": str(trait_id),
                            "field_name": field_name,
                            "support_level": sup,
                            "confidence": str(max_conf),
                            "evidence_source_record_ids": [
                                str(s) for s in contributing_real_ids
                            ],
                            "merged_from_trait_names": list(
                                entry["trait_names"]
                            ),
                        })
                        persisted_trait_fields_for_p.append(field_name)

                        # 3. PersonaEvidenceLinks: one per
                        # (persona, source, field). The unique
                        # constraint is on this triple.
                        for src_id in contributing_real_ids:
                            link_id = uuid.uuid4()
                            link = PersonaEvidenceLink(
                                id=link_id,
                                persona_id=persona_id,
                                source_record_id=src_id,
                                contribution_kind="trait_support",
                                contribution_field=field_name,
                                excerpt=(
                                    (entry["excerpts"][0]
                                     if entry["excerpts"] else
                                     cand.get("evidence_summary", "")
                                     or "evidence")
                                )[:4000],
                                excerpt_offset=None,
                                confidence=max_conf,
                            )
                            session.add(link)
                            expected_link_count += 1
                            persisted_links_summary.append({
                                "persona_record_id": str(persona_id),
                                "source_record_id": str(src_id),
                                "contribution_field": field_name,
                                "contribution_kind": "trait_support",
                            })

                    if not persisted_trait_fields_for_p:
                        rollback_reason = (
                            f"candidate {cand['candidate_id']}: "
                            "produced 0 persisted trait rows."
                        )
                        raise RuntimeError(rollback_reason)

                    persisted_personas.append({
                        "persona_record_id": str(persona_id),
                        "display_name": display_name,
                        "compressed_candidate_id": cand["candidate_id"],
                        "normalized_primary_role": cand["normalized_primary_role"],
                        "segment_label": cand.get("segment_label") or "",
                        "source_provider_family": cand.get(
                            "source_provider_family"),
                        "evidence_theme": cand.get("evidence_theme") or "",
                        "real_source_record_ids": [
                            str(s) for s in real_src_for_persona
                        ],
                        "trait_count": len(persisted_trait_fields_for_p),
                        "evidence_link_count": sum(
                            1 for l in persisted_links_summary
                            if l["persona_record_id"] == str(persona_id)
                        ),
                        "not_global_persona": True,
                        "scope": "run_scoped_brief_scoped",
                    })

                # 4. Pre-commit assertions
                if len(persisted_personas) != EXPECTED_COMPRESSED_COUNT:
                    rollback_reason = (
                        f"persisted_persona_count="
                        f"{len(persisted_personas)} != "
                        f"{EXPECTED_COMPRESSED_COUNT}"
                    )
                    raise RuntimeError(rollback_reason)
                if len(persisted_traits_summary) != expected_trait_count:
                    rollback_reason = (
                        f"persisted_trait_count="
                        f"{len(persisted_traits_summary)} != "
                        f"expected {expected_trait_count}"
                    )
                    raise RuntimeError(rollback_reason)
                # Each persona must have ≥1 evidence link
                links_per_persona: dict[str, int] = {}
                for l in persisted_links_summary:
                    links_per_persona[l["persona_record_id"]] = (
                        links_per_persona.get(l["persona_record_id"], 0) + 1
                    )
                for p in persisted_personas:
                    if links_per_persona.get(p["persona_record_id"], 0) < 1:
                        rollback_reason = (
                            f"persona {p['persona_record_id']} has "
                            "0 evidence links."
                        )
                        raise RuntimeError(rollback_reason)
                # Each persona must have ≥2 traits
                traits_per_persona: dict[str, int] = {}
                for t in persisted_traits_summary:
                    traits_per_persona[t["persona_record_id"]] = (
                        traits_per_persona.get(t["persona_record_id"], 0) + 1
                    )
                for p in persisted_personas:
                    if traits_per_persona.get(p["persona_record_id"], 0) < 2:
                        rollback_reason = (
                            f"persona {p['persona_record_id']} has "
                            f"only {traits_per_persona.get(p['persona_record_id'], 0)} "
                            "trait(s) (< 2 required)."
                        )
                        raise RuntimeError(rollback_reason)

                # 5. If --dry-run, raise to roll back; else commit.
                if not do_commit:
                    rollback_reason = (
                        "dry-run: rollback after preflight + delta "
                        "computation"
                    )
                    raise _DryRunRollback()

                transaction_committed = True
        except _DryRunRollback:
            rollback_happened = True
            transaction_committed = False
        except Exception as e:
            rollback_happened = True
            transaction_committed = False
            if rollback_reason is None:
                rollback_reason = (
                    f"unexpected exception: {type(e).__name__}: {e}"
                )
            print(f"ROLLBACK: {rollback_reason}")

    db_post = await _read_baseline_counts(sm)

    expected_deltas = {
        "source_records": len(inserted_source_ids),
        "persona_records": EXPECTED_COMPRESSED_COUNT,
        "persona_traits": expected_trait_count,
        "persona_evidence_links": expected_link_count,
    }
    if not transaction_committed:
        expected_deltas = {k: 0 for k in expected_deltas}
    actual_deltas = {
        k: db_post[k] - db_pre[k] for k in db_pre.keys()
    }
    expected_counts_match = expected_deltas == actual_deltas

    summary = {
        "phase": "8_5d_2e_run_scoped_persona_persistence",
        "completed_at": datetime.now(UTC).isoformat(),
        "db_writes": transaction_committed,
        "transaction_committed": transaction_committed,
        "rollback_happened": rollback_happened,
        "rollback_reason": rollback_reason,
        "input_audit_path": str(audit_root / args.input_audit),
        "live_baseline_counts": db_pre,
        "final_counts": db_post,
        "expected_deltas": expected_deltas,
        "actual_deltas": actual_deltas,
        "expected_counts_match": expected_counts_match,
        "run_scope_id": run_scope_id,
        "target_brief": TARGET_BRIEF_ID,
        "product_name": PRODUCT_NAME,
        "launch_state": LAUNCH_STATE,
        "compressed_candidate_count": len(cands),
        "persisted_persona_count": (
            len(persisted_personas) if transaction_committed else 0
        ),
        "inserted_source_record_count": (
            len(inserted_source_ids) if transaction_committed else 0
        ),
        "reused_source_record_count": (
            len(reused_source_ids) if transaction_committed else 0
        ),
        "inserted_source_record_ids": (
            inserted_source_ids if transaction_committed else []
        ),
        "planned_to_real_source_record_id_map": (
            sid_to_real_id if transaction_committed else {}
        ),
        "persisted_personas": (
            persisted_personas if transaction_committed else []
        ),
        "persisted_traits_summary": (
            persisted_traits_summary if transaction_committed else []
        ),
        "evidence_links_summary": (
            persisted_links_summary if transaction_committed else []
        ),
        "launch_state_validation_results": [
            json.loads(validate_launch_state_claims(
                candidate=_candidate_to_persona_obj(c),
                launch_state=LAUNCH_STATE, product_name=PRODUCT_NAME,
            ).model_dump_json())
            for c in cands
        ],
        "pii_scan_results": {
            "raw_user_id_keys_stripped_universally": list(
                _FORBIDDEN_RAW_USER_ID_KEYS,
            ),
            "image_url_keys_stripped_universally": list(
                _FORBIDDEN_IMAGE_URL_KEYS,
            ),
            "fake_target_use_check": "passed",
        },
        "duplicate_check_results": {
            "source_record_unique_key": "(source_kind, content_hash)",
            "reused_source_record_count": len(reused_source_ids),
            "inserted_source_record_count": len(inserted_source_ids),
        },
        "caveats": [
            "Phase 8.5D.2E persists run-scoped, brief-scoped personas. "
            "These are NOT global personas, NOT reusable templates, "
            "and NOT permanent StrideShield records.",
            "Persona display names are deterministic fictional names "
            "from the curated anonymization pool — not sourced from "
            "ingestion content.",
            "Trait field_names are mapped to the closed DB CHECK set "
            "(interests / role_or_context / buying_constraints / "
            "trust_triggers / current_alternatives / communication_style "
            "/ influence_signals / price_sensitivity / objection_patterns "
            "/ geography_broad). Multiple candidate trait_names that "
            "map to the same closed field are merged into one trait "
            "row per (persona, field) — DB unique constraint required.",
            "All evidence links use the closed `contribution_field` set.",
            "No raw user IDs, no image URLs, no profile data stored.",
            "Personas reflect competitor/substitute USE evidence; "
            "no candidate claims direct StrideShield use because "
            "StrideShield is unlaunched.",
        ],
        "recommendation": (
            "PASS — run-scoped persistence committed. "
            f"+{len(inserted_source_ids)} source_records, "
            f"+{EXPECTED_COMPRESSED_COUNT} personas, "
            f"+{expected_trait_count} traits, "
            f"+{expected_link_count} evidence links."
            if transaction_committed else
            f"DRY-RUN — preflight passed, transaction rolled back. "
            f"Expected on commit: +{len(inserted_source_ids)} sources, "
            f"+{EXPECTED_COMPRESSED_COUNT} personas, "
            f"+{expected_trait_count} traits, "
            f"+{expected_link_count} evidence links."
            if rollback_reason and rollback_reason.startswith("dry-run") else
            f"FAIL — {rollback_reason}"
        ),
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print(f"Phase {PHASE_LABEL} — Run-scoped persona persistence")
    print("=" * 72)
    print(f"mode: {'COMMIT' if do_commit else 'DRY-RUN'}")
    print(f"transaction_committed: {transaction_committed}")
    print(f"rollback_happened: {rollback_happened}")
    if rollback_reason:
        print(f"rollback_reason: {rollback_reason}")
    print(f"live_baseline_counts: {db_pre}")
    print(f"final_counts: {db_post}")
    print(f"expected_deltas: {expected_deltas}")
    print(f"actual_deltas: {actual_deltas}")
    print(f"expected_counts_match: {expected_counts_match}")
    print(f"run_scope_id: {run_scope_id}")
    if transaction_committed:
        print()
        for p in persisted_personas:
            print(
                f"  {p['display_name']:14s} "
                f"role={p['normalized_primary_role']:48s} "
                f"traits={p['trait_count']} "
                f"links={p['evidence_link_count']} "
                f"provider={p['source_provider_family']}"
            )
    print(f"\n→ audit JSON: {out_path}")
    return 0 if (
        (transaction_committed if do_commit else not rollback_happened
         or rollback_reason and rollback_reason.startswith("dry-run"))
    ) else 1


class _DryRunRollback(Exception):
    """Sentinel — raised inside the transaction to force a rollback in
    --dry-run mode after expected deltas have been computed."""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
