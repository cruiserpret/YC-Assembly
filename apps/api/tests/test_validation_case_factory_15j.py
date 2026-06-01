"""Phase 15J — Validation Case Factory tests.

Pure, deterministic, isolated: no LLM, no network, no DB, no paid simulation.
Every write goes to a tmp dir/file — the REAL ledger + candidate store are only
ever read, never mutated, by these tests.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.market_calibration.action_signals import ActionSignal
from assembly.validation_factory.candidate_factory import (
    build_case_payload_from_candidate,
    candidate_fingerprint,
    evaluate_promotion_gates,
    factory_dashboard,
    find_duplicates,
)
from assembly.validation_factory.candidate_schema import (
    CANDIDATE_PURPOSE,
    CandidateCase,
    ReviewerChecklist,
)
from assembly.validation_factory.candidate_store import (
    load_all_candidates,
    load_candidate,
    save_candidate,
)
from assembly.validation_factory.evidence_grading import (
    recommended_evidence_tier,
    validate_evidence_tier,
)
from assembly.validation_factory.outcome_mapping_protocol import ProposedOutcomeMapping
from assembly.validation_ledger.loader import (
    holdout_cases,
    load_all_cases,
    load_cases,
    training_cases,
)
from assembly.validation_ledger.schema import MarketDistribution, ValidationCase

API_DIR = Path(__file__).resolve().parent.parent
SCRIPT = API_DIR / "scripts" / "phase_15j_candidate_factory.py"
_PKG_DIR = API_DIR / "src" / "assembly" / "validation_factory"


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _complete_checklist(**over) -> ReviewerChecklist:
    base = dict(
        real_product_or_market_test="yes",
        outcome_externally_observable="yes",
        sources_provided="yes",
        population_or_source_biased="no",
        enough_evidence_to_map_buckets="yes",
        should_reject="no",
        suitable_for="training",
        evidence_tier=1,
        reviewer="reviewer@example.com",
        reviewed_at="2026-05-30",
    )
    base.update(over)
    return ReviewerChecklist(**base)


def _tier1_signal(**over) -> ActionSignal:
    base = dict(
        signal_type="kickstarter_pledge",
        source_type="kickstarter",
        count=512,
        source_reference="https://kickstarter.com/projects/x",
        direction="positive",
        observed_at="2026-03-15",
    )
    base.update(over)
    return ActionSignal(**base)


def _candidate(**over) -> CandidateCase:
    base = dict(
        candidate_id="ks_widget_2026",
        product_or_company_name="Widget Pro",
        category="crowdfunding_hardware",
        market_type="crowdfunding",
        launch_or_test_date="2026-03-01",
        source_urls=["https://kickstarter.com/projects/x"],
        source_type="kickstarter",
        candidate_summary="A hardware widget crowdfunding campaign.",
        observed_outcome_summary="Funded at 312% with 512 backers.",
        claimed_outcome_proportions=MarketDistribution(
            buyer_action_positive=40, receptive=25, uncertain_proof_needed=20,
            skeptical_resistant=15,
        ),
        action_signal_candidates=[_tier1_signal()],
        reviewer_checklist=_complete_checklist(),
        evidence_tier=1,
    )
    base.update(over)
    return CandidateCase(**base)


# --------------------------------------------------------------------------
# Schema + isolation
# --------------------------------------------------------------------------


def test_valid_candidate_loads():
    c = _candidate()
    assert c.candidate_id == "ks_widget_2026"
    assert c.purpose == CANDIDATE_PURPOSE
    assert c.status == "candidate"


@pytest.mark.parametrize("ledger_field", ["observed", "predicted", "anti_overfit", "metrics"])
def test_candidate_forbids_ledger_only_fields(ledger_field):
    payload = _candidate().model_dump()
    payload[ledger_field] = {"anything": 1}
    with pytest.raises(ValidationError):
        CandidateCase.model_validate(payload)


def test_rejected_candidate_requires_reason():
    with pytest.raises(ValidationError):
        _candidate(status="rejected")
    # with a reason it is fine
    _candidate(status="rejected", rejection_reason="not a real market test")


def test_checklist_completion_logic():
    assert _complete_checklist().is_complete()
    assert not _complete_checklist(should_reject="unknown").is_complete()
    assert not _complete_checklist(suitable_for="undecided").is_complete()
    assert not _complete_checklist(evidence_tier=None).is_complete()
    # a 'reject' decision does not require an evidence tier
    assert _complete_checklist(suitable_for="reject", evidence_tier=None).is_complete()


# --------------------------------------------------------------------------
# Promotion gates
# --------------------------------------------------------------------------


def test_clean_training_candidate_is_promotable():
    assert evaluate_promotion_gates(_candidate(), "training") == []


def test_missing_source_url_blocks_promotion():
    issues = evaluate_promotion_gates(_candidate(source_urls=[]), "training")
    assert any("source_url" in i for i in issues)


def test_missing_reviewer_checklist_blocks_promotion():
    issues = evaluate_promotion_gates(_candidate(reviewer_checklist=None), "training")
    assert any("reviewer_checklist is required" in i for i in issues)


def test_incomplete_checklist_blocks_promotion():
    c = _candidate(reviewer_checklist=_complete_checklist(sources_provided="unknown"))
    issues = evaluate_promotion_gates(c, "training")
    assert any("unanswered" in i for i in issues)


def test_tier4_cannot_masquerade_as_tier1():
    # all evidence is a Tier-4 synthetic forecast, but evidence_tier claims 1
    c = _candidate(
        action_signal_candidates=[ActionSignal(signal_type="deep_agent_forecast")],
        evidence_tier=1,
        reviewer_checklist=_complete_checklist(evidence_tier=1),
        claimed_outcome_proportions=MarketDistribution(
            buyer_action_positive=25, receptive=25, uncertain_proof_needed=25,
            skeptical_resistant=25,
        ),
    )
    assert any("masquerade" in i for i in validate_evidence_tier(c))
    assert any("masquerade" in i for i in evaluate_promotion_gates(c, "training"))


def test_unknown_signal_cannot_declare_action_tier():
    c = _candidate(
        action_signal_candidates=[
            ActionSignal(signal_type="totally_made_up_signal", tier=1, count=5,
                         source_reference="https://x")
        ],
    )
    issues = evaluate_promotion_gates(c, "training")
    assert any("unknown signal_type may not declare" in i for i in issues)


def test_tier1_signal_requires_source_reference_and_count():
    c = _candidate(action_signal_candidates=[
        ActionSignal(signal_type="kickstarter_pledge")  # no ref, no count
    ])
    issues = evaluate_promotion_gates(c, "training")
    assert any("source_reference" in i for i in issues)
    assert any("positive count" in i for i in issues)


def test_critical_uncertainty_flag_blocks_promotion():
    c = _candidate(uncertainty_flags=["critical: source may be fabricated"])
    issues = evaluate_promotion_gates(c, "training")
    assert any("critical uncertainty" in i for i in issues)


def test_designation_mismatch_blocks():
    c = _candidate(reviewer_checklist=_complete_checklist(suitable_for="training"))
    issues = evaluate_promotion_gates(c, "pending")
    assert any("suitable_for" in i for i in issues)


# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------


def test_duplicate_by_fingerprint_blocked():
    a = _candidate(candidate_id="a")
    b = _candidate(candidate_id="b")  # same name/date/category/url/observed
    assert candidate_fingerprint(a) == candidate_fingerprint(b)
    issues = evaluate_promotion_gates(a, "training", existing_candidates=[b])
    assert any("duplicate" in i for i in issues)


def test_duplicate_by_name_date_source_blocked():
    a = _candidate(candidate_id="a")
    # different url/observed -> different fingerprint, but same (name,date,source)
    b = _candidate(candidate_id="b", source_urls=["https://other.example/y"],
                   claimed_outcome_proportions=MarketDistribution(
                       buyer_action_positive=10, receptive=10,
                       uncertain_proof_needed=40, skeptical_resistant=40))
    assert candidate_fingerprint(a) != candidate_fingerprint(b)
    dups = find_duplicates(a, existing_candidates=[b])
    assert any("name + date + source" in i for i in dups)


def test_duplicate_vs_ledger_case_blocked():
    # the seed has a real case 'opslane'; mimic its (name,date,source) key
    seed = {c.case_id: c for c in load_cases()}
    op = seed["opslane"]
    c = _candidate(
        product_or_company_name=op.metadata.product_name,
        launch_or_test_date=op.metadata.date_run,
        source_type=op.metadata.source_type,
    )
    dups = find_duplicates(c, existing_cases=load_all_cases())
    assert any("ledger case" in i for i in dups)


def test_allow_duplicate_override():
    a = _candidate(candidate_id="a")
    b = _candidate(candidate_id="b")
    assert evaluate_promotion_gates(
        a, "training", existing_candidates=[b], allow_duplicate=True
    ) == []


# --------------------------------------------------------------------------
# Observed-outcome discipline + anti-leakage
# --------------------------------------------------------------------------


def test_pending_must_not_carry_observed():
    c = _candidate(reviewer_checklist=_complete_checklist(suitable_for="pending"))
    issues = evaluate_promotion_gates(c, "pending")
    assert any("pending case must NOT carry an observed outcome" in i for i in issues)


def test_pending_without_observed_is_promotable():
    c = _candidate(
        claimed_outcome_proportions=None,
        observed_outcome_summary="outcome not yet bucketized",
        reviewer_checklist=_complete_checklist(
            suitable_for="pending", enough_evidence_to_map_buckets="no"),
    )
    assert evaluate_promotion_gates(c, "pending") == []


def test_training_requires_observed():
    c = _candidate(claimed_outcome_proportions=None)
    issues = evaluate_promotion_gates(c, "training")
    assert any("requires claimed_outcome_proportions" in i for i in issues)


def test_holdout_blocked_by_anti_leakage():
    # a retrospective case with a known outcome and no prior locked prediction
    c = _candidate(reviewer_checklist=_complete_checklist(suitable_for="holdout"))
    issues = evaluate_promotion_gates(c, "holdout")
    assert any("anti-leakage" in i for i in issues)


def test_built_training_case_has_no_predicted_and_correct_flags():
    case = ValidationCase.model_validate(
        build_case_payload_from_candidate(_candidate(), "training")
    )
    assert case.predicted is None
    assert case.observed is not None
    assert case.anti_overfit.used_for_training is True
    assert case.anti_overfit.used_for_holdout is False
    assert case.metadata.validation_status == "partial"
    assert "case_factory.v1" in case.metadata.notes


def test_built_pending_case_has_no_observed():
    c = _candidate(
        claimed_outcome_proportions=None,
        reviewer_checklist=_complete_checklist(suitable_for="pending"),
    )
    case = ValidationCase.model_validate(build_case_payload_from_candidate(c, "pending"))
    assert case.observed is None
    assert case.predicted is None
    assert case.metadata.validation_status == "pending"


# --------------------------------------------------------------------------
# Evidence grading
# --------------------------------------------------------------------------


def test_recommended_evidence_tier():
    assert recommended_evidence_tier(_candidate()) == 1  # kickstarter_pledge -> Tier1
    c3 = _candidate(action_signal_candidates=[ActionSignal(signal_type="comment_sentiment")])
    assert recommended_evidence_tier(c3) == 3


# --------------------------------------------------------------------------
# Store (tmp only)
# --------------------------------------------------------------------------


def test_store_dry_run_makes_no_write(tmp_path):
    p = save_candidate(_candidate(), tmp_path, dry_run=True)
    assert not p.exists()
    assert load_all_candidates(tmp_path) == []


def test_store_roundtrip(tmp_path):
    c = _candidate()
    p = save_candidate(c, tmp_path)
    assert p.exists()
    loaded = load_candidate(c.candidate_id, tmp_path)
    assert loaded.candidate_id == c.candidate_id
    assert loaded.evidence_tier == 1
    # README.md and other non-json files are ignored
    (tmp_path / "README.md").write_text("not a candidate")
    assert len(load_all_candidates(tmp_path)) == 1


# --------------------------------------------------------------------------
# Candidate <-> ledger isolation + dataset reality
# --------------------------------------------------------------------------


def test_candidates_dir_not_in_manifest():
    manifest = json.loads(
        (API_DIR / "validation_cases" / "manifest.json").read_text(encoding="utf-8")
    )
    paths = [e["path"] if isinstance(e, dict) else e for e in manifest.get("files", [])]
    assert not any("candidate" in p for p in paths), paths


def test_real_candidate_store_never_loaded_as_validation_case():
    # the real candidates dir (if any files exist) is never merged into the ledger
    before = len(load_all_cases())
    real_candidates = load_all_candidates()  # default dir
    # candidates load through a SEPARATE path and never affect the ledger count
    assert len(load_all_cases()) == before
    # any real candidate ids are disjoint from ledger case ids
    case_ids = {c.case_id for c in load_all_cases()}
    cand_ids = {c.candidate_id for c in real_candidates}
    assert case_ids.isdisjoint(cand_ids)


def test_official_dataset_unchanged():
    # The frozen seed + split files are untouched by Phase 15J.
    assert len(load_cases()) == 6  # seed only
    all_cases = load_all_cases()
    assert len(all_cases) == 6  # seed + empty holdout/pending/training
    assert len(training_cases(all_cases)) == 6
    assert len(holdout_cases(all_cases)) == 0
    # the new training file exists and is empty
    tf = API_DIR / "validation_cases" / "training_cases.json"
    assert json.loads(tf.read_text(encoding="utf-8")) == []


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


def test_dashboard_reports_phase_15e_blocked():
    board = factory_dashboard([], ledger_cases=load_all_cases())
    assert board["phase_15e_blocked"] is True
    assert board["ledger_clean_holdout"] == 0
    assert any("clean holdout" in r for r in board["phase_15e_unmet_requirements"])


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------


def _pkg_sources() -> list[Path]:
    return sorted(_PKG_DIR.glob("*.py"))


def test_factory_package_imports_only_allowed_modules():
    allowed = {
        "__future__", "json", "hashlib", "re", "pathlib", "typing",
        "collections", "pydantic", "assembly",
    }
    for p in _pkg_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            top = None
            if isinstance(node, ast.Import):
                top = node.names[0].name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
            if top is not None:
                assert top in allowed, f"{p.name} imports unexpected module: {top}"


def test_factory_has_no_llm_or_network_imports():
    forbidden = {"anthropic", "openai", "httpx", "requests", "aiohttp",
                 "sqlalchemy", "redis"}
    for p in _pkg_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    mods.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert not (mods & forbidden), f"{p.name} imports forbidden: {mods & forbidden}"


# --------------------------------------------------------------------------
# CLI (subprocess; writes only to tmp)
# --------------------------------------------------------------------------


def _run_cli(*args, expect=0):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(API_DIR),
    )
    assert proc.returncode == expect, (
        f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return proc


def _write_candidate_json(tmp_path: Path, candidate: CandidateCase) -> Path:
    f = tmp_path / "in.json"
    f.write_text(json.dumps(candidate.model_dump(mode="json", exclude_none=True)))
    return f


def test_cli_create_dry_run_makes_no_write(tmp_path):
    cdir = tmp_path / "candidates"
    src = _write_candidate_json(tmp_path, _candidate())
    _run_cli("--candidates-dir", str(cdir), "create", "--from", str(src), "--dry-run")
    assert load_all_candidates(cdir) == []


def _write_direct_mapping_json(tmp_path: Path, candidate_id: str) -> Path:
    """A valid Phase 15L-B direct_observed mapping matching _candidate()'s 40/25/20/15."""
    rationales = [
        {"bucket": b, "basis": "observed", "rationale": "measured",
         "source_reference": "https://example.org/panel"}
        for b in ("buyer_action_positive", "receptive",
                  "uncertain_proof_needed", "skeptical_resistant")
    ]
    m = ProposedOutcomeMapping(
        candidate_id=candidate_id,
        mapping_type="direct_observed_distribution",
        proposed_proportions=MarketDistribution(
            buyer_action_positive=40, receptive=25,
            uncertain_proof_needed=20, skeptical_resistant=15),
        bucket_rationales=rationales,
        denominator_type="independent_voices", denominator_count=1500,
        denominator_quality="fixed_external_census", estimate_quality="audited_official",
        confidence="high", reviewer="reviewer@example.com", reviewed_at="2026-05-30",
    )
    f = tmp_path / "mapping.json"
    f.write_text(json.dumps(m.model_dump(mode="json")))
    return f


def test_cli_create_then_approve_then_ingest_dry_run(tmp_path):
    cdir = tmp_path / "candidates"
    src = _write_candidate_json(tmp_path, _candidate())
    _run_cli("--candidates-dir", str(cdir), "create", "--from", str(src))
    assert len(load_all_candidates(cdir)) == 1

    # Phase 15L-C: approve/ingest for training now REQUIRE a gate-passing mapping.
    mapping = _write_direct_mapping_json(tmp_path, "ks_widget_2026")
    _run_cli("--candidates-dir", str(cdir), "approve", "--id", "ks_widget_2026",
             "--target", "training", "--mapping", str(mapping))
    assert load_candidate("ks_widget_2026", cdir).status == "approved_for_training"

    # ingest to a tmp ledger file with --dry-run -> no write anywhere
    target = tmp_path / "training_out.json"
    _run_cli("--candidates-dir", str(cdir), "ingest", "--id", "ks_widget_2026",
             "--mapping", str(mapping), "--to", str(target), "--dry-run")
    assert not target.exists()

    # real ingest to the tmp file -> the case lands there, real ledger untouched
    before = len(load_all_cases())
    _run_cli("--candidates-dir", str(cdir), "ingest", "--id", "ks_widget_2026",
             "--mapping", str(mapping), "--to", str(target))
    written = json.loads(target.read_text(encoding="utf-8"))
    assert len(written) == 1
    assert written[0]["case_id"] == "cand_ks_widget_2026"
    assert "predicted" not in written[0]
    assert written[0]["anti_overfit"]["used_for_training"] is True
    # observed now carries the mapping's real denominator + provenance marker
    assert written[0]["observed"]["denominator_type"] == "independent_voices"
    assert "15L-mapping-provenance" in written[0]["observed"]["observation_notes"]
    assert len(load_all_cases()) == before  # real ledger count unchanged


def test_cli_approve_training_without_mapping_is_refused(tmp_path):
    cdir = tmp_path / "candidates"
    src = _write_candidate_json(tmp_path, _candidate())
    _run_cli("--candidates-dir", str(cdir), "create", "--from", str(src))
    # no --mapping and no sidecar -> REFUSED (exit 1) with the mapping-gate message
    proc = _run_cli("--candidates-dir", str(cdir), "approve", "--id", "ks_widget_2026",
                    "--target", "training", "--proposals-dir", str(tmp_path / "none"),
                    expect=1)
    assert "mapping gate (15L-B)" in proc.stderr


def test_cli_rejected_candidate_cannot_ingest(tmp_path):
    cdir = tmp_path / "candidates"
    src = _write_candidate_json(tmp_path, _candidate())
    _run_cli("--candidates-dir", str(cdir), "create", "--from", str(src))
    _run_cli("--candidates-dir", str(cdir), "reject", "--id", "ks_widget_2026",
             "--reason", "source not credible")
    # ingest must refuse a non-approved candidate (exit 1)
    _run_cli("--candidates-dir", str(cdir), "ingest", "--id", "ks_widget_2026", expect=1)


def test_cli_dashboard_runs(tmp_path):
    cdir = tmp_path / "candidates"
    proc = _run_cli("--candidates-dir", str(cdir), "dashboard", "--format", "json")
    board = json.loads(proc.stdout)
    assert board["phase_15e_blocked"] is True
    assert board["n_candidates"] == 0
