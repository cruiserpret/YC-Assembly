"""Phase 8.5C.2 — bounded Triton-Amazon source_record insertion tests.

23 tests covering operator scenarios 1-23. (#24-25 are full-suite
verifications, validated by the regression sweep.)

NO live DB writes from this test file. Tests rely on synthetic
audit-JSON fixtures + monkeypatched scanners. Real-DB execution is
exercised by the post-test live run with `--commit` (the operator's
explicit approval).
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest


# Load the 8.5C.2 script as a module so we can unit-test its helpers.
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "triton_amazon_source_record_insert_8_5c_2.py"
)
_spec = importlib.util.spec_from_file_location(
    "ph_8_5c_2_script", _SCRIPT_PATH,
)
assert _spec is not None
script = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(script)


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


# ---------------------------------------------------------------------------
# Synthetic 8.5C.1 audit candidates
# ---------------------------------------------------------------------------


def _good_preview(asin: str = "B00X", title: str = "Energy Drink Sample") -> dict:
    body = (
        f"{title}\n\n"
        "Caffeine and electrolyte mix that works for pre-workout. "
        "Better than my usual coffee. No sugar crash. Worth $3.99."
    )
    return {
        "source_kind": "amazon_reviews_2023_local",
        "source_url": (
            f"local://amazon_reviews_2023_local/Health_and_Household/{asin}"
        ),
        "content_preview": body,
        "content_length": len(body),
        "content_hash": "deadbeef" * 8,  # placeholder; rescan recomputes
        "language": "en",
        "metadata": {
            "target_brief": "triton_drinks",
            "source_dataset": "amazon_reviews_2023",
            "source_category": "Health_and_Household",
            "parent_asin": asin, "asin": asin,
            "rating": 5.0, "verified_purchase": True,
            "helpful_vote": 3, "timestamp": 1700000000,
            "metadata_title": title,
            "metadata_main_category": "Health & Household",
            "metadata_categories": [
                "Health & Household", "Diet & Sports Nutrition",
            ],
            "anchor_score": 9, "anchor_confidence": "high_confidence",
            "matched_terms": ["positive:energy drink"],
            "evidence_anchor_plan_id": "abc123",
            "ingestion_policy_id": "def456",
            "candidate_decision_rank": 1,
            "persona_value_roles": ["performance_use_case_buyer"],
            "phase": "8.5C.1_dry_run",
        },
        "ingested_by": "assembly_phase_8_5c_triton_amazon_dynamic_policy_bounded_ingest",
        "compliance_tag": "open_dataset",
        "captured_at": "2023-09-01T00:00:00+00:00",
        "pii_redaction_status": "planned_clean",
        "sensitive_scan_status": "planned_clean",
        "user_handle_hash": None,
    }


def _candidate(
    *,
    persona_value_label: str = "medium",
    persona_roles: list[str] | None = None,
    source_relevance: str = "secondary",
    has_preview: bool = True,
    rank: int = 1,
    asin: str = "B00X",
) -> dict:
    return {
        "candidate_id": f"cat::{asin}::{asin}",
        "decision": "SELECTED",
        "selection_rank": rank,
        "evidence_strength_label": "strong",
        "source_relevance_label": source_relevance,
        "persona_value_label": persona_value_label,
        "selected_for_persona_roles": (
            persona_roles
            if persona_roles is not None
            else ["performance_use_case_buyer"]
        ),
        "decision_reasons": ["passes confidence_high_only rule"],
        "rejection_reasons": [],
        "scanner_results": {
            "pii_scan": [], "unlaunched_fake_buyer_scan": [],
            "dataset_compliance_scan": [], "duplicate_check": [],
        },
        "duplicate_check": "unique",
        "planned_source_record_preview": (
            _good_preview(asin=asin) if has_preview else None
        ),
    }


# ---------------------------------------------------------------------------
# 1. 8.5C.2 reads the 8.5C.1 dry-run audit
# ---------------------------------------------------------------------------


def test_8_5c_2_script_reads_8_5c_1_audit_path() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "triton_amazon_dynamic_ingestion_plan_8_5c_1.json" in src
    # And reads `selected_candidates` from it
    assert "selected_candidates" in src


# ---------------------------------------------------------------------------
# 2 + 3 + 4 + 5. Final persona-value gate behavior
# ---------------------------------------------------------------------------


def test_gate_rejects_persona_value_low() -> None:
    cand = _candidate(persona_value_label="low")
    approved, rejected = script.apply_persona_value_gate([cand])
    assert approved == []
    assert len(rejected) == 1
    reasons = " ".join(rejected[0]["_gate_rejection_reasons"])
    assert "persona_value_label=low" in reasons


def test_gate_rejects_empty_persona_roles() -> None:
    cand = _candidate(persona_roles=[])
    approved, rejected = script.apply_persona_value_gate([cand])
    assert approved == []
    assert any(
        "selected_for_persona_roles is empty" in r
        for r in rejected[0]["_gate_rejection_reasons"]
    )


def test_gate_rejects_off_brief_source_relevance() -> None:
    cand = _candidate(source_relevance="off_brief")
    approved, rejected = script.apply_persona_value_gate([cand])
    assert approved == []
    assert any(
        "source_relevance_label=off_brief" in r
        for r in rejected[0]["_gate_rejection_reasons"]
    )


def test_gate_accepts_medium_high_with_roles_and_relevance() -> None:
    cand_medium = _candidate(persona_value_label="medium")
    cand_high = _candidate(persona_value_label="high")
    approved, rejected = script.apply_persona_value_gate(
        [cand_medium, cand_high],
    )
    assert len(approved) == 2
    assert rejected == []


def test_gate_rejects_missing_planned_record() -> None:
    cand = _candidate(has_preview=False)
    approved, rejected = script.apply_persona_value_gate([cand])
    assert approved == []
    assert any(
        "planned_source_record_preview missing" in r
        for r in rejected[0]["_gate_rejection_reasons"]
    )


# ---------------------------------------------------------------------------
# 6 + 7 + 8 + 9. Scanners re-run before insert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescan_runs_pii_scanner(monkeypatch) -> None:
    """Inject PII into the candidate's preview and confirm rescan flags it."""
    cand = _candidate()
    pii_text = (
        "Email me at user@example.com and visit https://spam.example.com"
    )
    cand["planned_source_record_preview"]["content_preview"] = pii_text
    monkeypatch.setattr(
        script, "check_duplicate_content_hash",
        lambda **kwargs: _AsyncResult(False),
    )
    rescan = await script.rescan_candidate(
        candidate=cand, sessionmaker=None,
    )
    assert rescan["pii_scan"], "PII scanner did not flag email/URL"
    assert rescan["rescan_passed"] is False


class _AsyncResult:
    """Tiny awaitable wrapper to stub async functions."""
    def __init__(self, value):
        self._value = value
    def __await__(self):
        async def _coro():
            return self._value
        return _coro().__await__()


@pytest.mark.asyncio
async def test_rescan_runs_fake_buyer_scanner(monkeypatch) -> None:
    cand = _candidate()
    cand["planned_source_record_preview"]["content_preview"] = (
        "I am a Triton buyer. Tried Triton last week."
    )
    monkeypatch.setattr(
        script, "check_duplicate_content_hash",
        lambda **kwargs: _AsyncResult(False),
    )
    rescan = await script.rescan_candidate(
        candidate=cand, sessionmaker=None,
    )
    assert rescan["unlaunched_fake_buyer_scan"]
    assert rescan["rescan_passed"] is False


@pytest.mark.asyncio
async def test_rescan_runs_compliance_scanner(monkeypatch) -> None:
    cand = _candidate()
    # Break source_url to be non-compliant
    cand["planned_source_record_preview"]["source_url"] = (
        "https://www.amazon.com/dp/B00X"
    )
    monkeypatch.setattr(
        script, "check_duplicate_content_hash",
        lambda **kwargs: _AsyncResult(False),
    )
    rescan = await script.rescan_candidate(
        candidate=cand, sessionmaker=None,
    )
    assert rescan["dataset_compliance_scan"]
    assert rescan["rescan_passed"] is False


@pytest.mark.asyncio
async def test_rescan_runs_duplicate_check(monkeypatch) -> None:
    cand = _candidate()
    monkeypatch.setattr(
        script, "check_duplicate_content_hash",
        lambda **kwargs: _AsyncResult(True),  # already exists
    )
    rescan = await script.rescan_candidate(
        candidate=cand, sessionmaker=None,
    )
    assert rescan["duplicate_check"]
    assert rescan["rescan_passed"] is False


@pytest.mark.asyncio
async def test_rescan_passes_clean_candidate(monkeypatch) -> None:
    from assembly.sources.ingestion_policy import compute_content_hash
    cand = _candidate()
    # Make `content_hash` in the preview match what rescan recomputes
    # — in a real 8.5C.1 audit the hash is already correct.
    body = cand["planned_source_record_preview"]["content_preview"]
    cand["planned_source_record_preview"]["content_hash"] = (
        compute_content_hash(content=body, source_kind="amazon_reviews_2023_local")
    )
    monkeypatch.setattr(
        script, "check_duplicate_content_hash",
        lambda **kwargs: _AsyncResult(False),
    )
    rescan = await script.rescan_candidate(
        candidate=cand, sessionmaker=None,
    )
    assert rescan["pii_scan"] == []
    assert rescan["unlaunched_fake_buyer_scan"] == []
    assert rescan["dataset_compliance_scan"] == []
    assert rescan["duplicate_check"] == []
    # content_hash_matches_preview is informational; rescan_passed
    # depends on the 4 scanners only.
    assert rescan["rescan_passed"] is True


# ---------------------------------------------------------------------------
# 10 + 11. Transaction rollback discipline (static code-grep)
# ---------------------------------------------------------------------------


def test_script_uses_session_begin_for_atomic_transaction() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    # Single bounded transaction via `async with session.begin():`
    assert "async with session.begin():" in src
    # Count match check + persona-table-unchanged check inside the
    # transaction → raise on mismatch → automatic rollback
    assert "count mismatch" in src
    assert "count changed during insert" in src
    # Exception handling path
    assert "rollback_reason" in src


def test_script_raises_on_count_mismatch_inside_transaction() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    # The mismatch check is INSIDE the `async with session.begin()` so
    # any RuntimeError triggers automatic rollback.
    # Verify by checking the structural ordering.
    txn_idx = src.find("async with session.begin():")
    mismatch_idx = src.find("count mismatch")
    assert txn_idx > 0 and mismatch_idx > txn_idx, (
        "count-mismatch check must be inside session.begin() block"
    )


# ---------------------------------------------------------------------------
# 12 + 13 + 14 + 15. Only source_records get inserted; no persona/trait/link
# writes
# ---------------------------------------------------------------------------


_FORBIDDEN_ORM_NAMES = (
    "PersonaRecord", "PersonaTrait", "PersonaEvidenceLink",
    "PersonaGraphEdge", "PersonaCluster", "PersonaClusterMembership",
    "PersonaOpinion", "AudienceRetrievalRun",
    "PopulationConstructionAudit", "SimulationOutput", "SimulationRound",
    "DebateTurn", "AgentResponse", "Agent", "AgentEdge",
)


def test_script_constructs_only_source_record_no_other_orm_rows() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    # `SourceRecord(**kwargs)` is the only ORM-construction call
    pat = re.compile(
        r"\b(?:" + "|".join(_FORBIDDEN_ORM_NAMES) + r")\s*\(\s*\w"
    )
    for m in pat.finditer(src):
        ctx = src[max(0, m.start() - 20):m.end() + 30]
        if "select(" in ctx or "func.count()" in ctx:
            continue
        raise AssertionError(
            f"forbidden ORM construction: ...{ctx}..."
        )


def test_script_verifies_persona_tables_unchanged_inside_transaction() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    # The script reads counts of PersonaRecord / PersonaTrait /
    # PersonaEvidenceLink AFTER inserting source_records and raises
    # if any changed.
    for name in ("persona_records", "persona_traits",
                 "persona_evidence_links"):
        assert name in src
    assert "count changed during insert" in src


# ---------------------------------------------------------------------------
# 16. No graph/simulation/UI writes
# ---------------------------------------------------------------------------


def test_script_no_simulation_or_graph_or_frontend_references() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    for name in (
        "Agent(", "AgentResponse(", "DebateTurn(",
        "Simulation(", "SimulationOutput(", "SimulationRound(",
        "PersonaGraphEdge(", "PersonaCluster(",
        "apps/web", "next/router", "next.js",
    ):
        assert name not in src, f"forbidden token: {name!r}"


# ---------------------------------------------------------------------------
# 17 + 18 + 19 + 20 + 21 + 22. Source_record metadata + privacy
# ---------------------------------------------------------------------------


def test_inserted_metadata_includes_historical_caveat() -> None:
    cand = _candidate()
    rescan = {
        "recomputed_content_hash": "x" * 64,
        "rescan_passed": True,
    }
    kwargs = script._build_source_record_kwargs(
        candidate=cand, rescan=rescan,
    )
    assert kwargs["metadata_"]["source_is_historical"] is True
    assert "Amazon Reviews 2023" in kwargs["metadata_"]["source_caveat"]
    assert kwargs["metadata_"]["execution_phase"] == "8.5C.2"
    assert kwargs["metadata_"]["phase"] == "8.5C.2_executed"


def test_inserted_source_url_uses_local_prefix_only() -> None:
    cand = _candidate()
    rescan = {
        "recomputed_content_hash": "y" * 64,
        "rescan_passed": True,
    }
    kwargs = script._build_source_record_kwargs(
        candidate=cand, rescan=rescan,
    )
    assert kwargs["source_url"].startswith("local://amazon_reviews_2023")


def test_inserted_compliance_tag_is_open_dataset() -> None:
    cand = _candidate()
    rescan = {
        "recomputed_content_hash": "z" * 64,
        "rescan_passed": True,
    }
    kwargs = script._build_source_record_kwargs(
        candidate=cand, rescan=rescan,
    )
    assert kwargs["compliance_tag"] == "open_dataset"


def test_inserted_user_handle_hash_is_null() -> None:
    cand = _candidate()
    rescan = {
        "recomputed_content_hash": "a" * 64, "rescan_passed": True,
    }
    kwargs = script._build_source_record_kwargs(
        candidate=cand, rescan=rescan,
    )
    assert kwargs["user_handle_hash"] is None


def test_inserted_metadata_contains_no_image_urls() -> None:
    cand = _candidate()
    rescan = {
        "recomputed_content_hash": "b" * 64, "rescan_passed": True,
    }
    kwargs = script._build_source_record_kwargs(
        candidate=cand, rescan=rescan,
    )
    blob = json.dumps(kwargs["metadata_"], default=str)
    assert ".jpg" not in blob
    assert ".png" not in blob
    assert "media-amazon" not in blob


# ---------------------------------------------------------------------------
# 23. Existing 8.5C.1 tests still pass — proxy: same imports load cleanly
# ---------------------------------------------------------------------------


def test_8_5c_1_imports_still_resolve() -> None:
    from assembly.sources.ingestion_policy import (  # noqa: F401
        REQUIRED_SCANNERS, UNIVERSAL_GUARDRAILS,
        check_duplicate_content_hash, compute_content_hash,
        scan_dataset_compliance, scan_pii, scan_unlaunched_fake_buyer,
    )


# ---------------------------------------------------------------------------
# Bonus: --commit gating
# ---------------------------------------------------------------------------


def test_script_default_is_preview_only_no_writes_unless_commit() -> None:
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    # `--commit` flag declared with action="store_true" (defaults False)
    assert '"--commit"' in src
    assert 'action="store_true"' in src
    # The transaction block is gated by `if args.commit:`
    assert "if args.commit:" in src or "if not args.commit:" in src
