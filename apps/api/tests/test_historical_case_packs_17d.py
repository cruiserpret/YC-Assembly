"""Phase 17D — historical case-pack builder tests.

Input/outcome separation, leakage rejection, buyer-only-no-fabrication, deterministic
+ sensitive hashes, missing-timestamp downgrade, diversity guard, CLI dry-run/write,
isolation (packs are never validation cases; no model deps/calls), and the 6 fixtures'
classifications. Pure/deterministic; no model is run.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.benchmarks.market_fidelity.historical_cases import (
    CandidateMetadata,
    InputBundle,
    OutcomeRecord,
    ProvenanceInputs,
    build_case_pack,
    check_diversity,
    default_packs_dir,
    hash_obj,
    run_leakage_audit,
    validate_case_pack,
)

_API = Path(__file__).resolve().parents[1]
_PKG = _API / "src" / "assembly" / "benchmarks" / "market_fidelity" / "historical_cases"
_FIX = _API / "benchmarks" / "market_fidelity" / "historical_case_packs" / "fixtures"


def _bundle(**over) -> InputBundle:
    base = dict(case_id="c1", prediction_timestamp="2022-05-01", evidence_items=[
        {"source_id": "s1", "published_at": "2022-04-10", "accessed_at": "2022-04-11",
         "source_text_excerpt": "a campaign launches", "content_hash": "sha256:a",
         "pre_outcome_status": "verified_pre_outcome"},
    ])
    base.update(over)
    return InputBundle.model_validate(base)


def _outcome(**over) -> OutcomeRecord:
    base = dict(case_id="c1", outcome_timestamp="2022-06-20", outcome_type="action_anchor_only",
                scoring_mapping_type="action_anchor_only", buyer_action_positive_observed=0.0,
                metrics={"backers": 1200}, buyer_anchor_scoreable=True)
    base.update(over)
    return OutcomeRecord.model_validate(base)


def _cm(**over) -> CandidateMetadata:
    base = dict(expected_outcome_class="success", category="hardware", platform="kickstarter", fame_level="niche")
    base.update(over)
    return CandidateMetadata.model_validate(base)


def _prov(**over) -> ProvenanceInputs:
    base = dict(subject="X", is_open_weight=True, model_release_date="2021-09-01",
                training_cutoff="2021-06-01", has_temporal_proof=True, tier1_provenance_justified=True)
    base.update(over)
    return ProvenanceInputs.model_validate(base)


def _build(b=None, o=None, **prov):
    b = b or _bundle()
    o = o or _outcome()
    return build_case_pack(input_bundle=b, outcome_record=o, candidate_metadata=_cm(),
                           provenance=_prov(**prov), product_name="X")


# --------------------------------------------------------------------------
# Leakage / separation
# --------------------------------------------------------------------------
def test_post_outcome_source_rejected():
    b = _bundle(evidence_items=[
        {"source_id": "ok", "published_at": "2022-04-10", "source_text_excerpt": "launch", "content_hash": "sha256:a"},
        {"source_id": "post", "published_at": "2022-07-01", "source_text_excerpt": "later news", "content_hash": "sha256:b"},
    ])
    rep = _build(b)
    assert "post" in rep.leakage_audit["excluded_sources"]
    assert rep.leakage_audit["input_bundle_clean"] is False
    assert rep.pack.case_status == "rejected"


def test_outcome_revealing_text_rejected():
    b = _bundle(evidence_items=[
        {"source_id": "leak", "published_at": "2022-04-10",
         "source_text_excerpt": "the project raised $250,000 from 3,000 backers", "content_hash": "sha256:a"},
    ])
    rep = _build(b)
    assert "leak" in rep.leakage_audit["outcome_leakage_flags"]
    assert rep.pack.case_status == "rejected"


def test_outcome_record_separate_from_input_bundle():
    rep = _build()
    assert validate_case_pack(input_bundle=_bundle(), outcome_record=_outcome(), pack=rep.pack) == []
    # an explicitly-flagged outcome value appearing in the bundle is caught by the audit
    leaky = _bundle(product_description="confirmed: the final raised total was 42 thousand dollars")
    rep2 = build_case_pack(input_bundle=leaky, outcome_record=_outcome(), candidate_metadata=_cm(),
                           provenance=_prov(), product_name="X", flagged_outcome_values=["42 thousand dollars"])
    assert rep2.pack.case_status == "rejected"
    assert rep2.leakage_audit["input_bundle_clean"] is False


def test_leakage_audit_zeroes_excluded_weights():
    audit = run_leakage_audit(_bundle(evidence_items=[
        {"source_id": "bad", "published_at": "2099-01-01", "source_text_excerpt": "x", "content_hash": "sha256:a"},
    ]), "2022-06-20")
    assert audit["retrieval_weight_overrides"]["bad"] == 0.0


# --------------------------------------------------------------------------
# Buyer-only must not fabricate a full distribution
# --------------------------------------------------------------------------
def test_buyer_only_cannot_carry_full_distribution():
    with pytest.raises(ValidationError):
        OutcomeRecord.model_validate(dict(
            case_id="c", outcome_timestamp="2022-06-20", outcome_type="action_anchor_only",
            scoring_mapping_type="action_anchor_only", buyer_anchor_scoreable=True,
            full_distribution_observed={"buyer_action_positive": 50, "receptive": 30,
                                        "uncertain_proof_needed": 10, "skeptical_resistant": 10}))


def test_full_distribution_requires_direct_observed_and_sum():
    OutcomeRecord.model_validate(dict(  # valid defensible distribution
        case_id="c", outcome_timestamp="2022-06-20", outcome_type="survey_distribution",
        scoring_mapping_type="direct_observed_distribution", full_distribution_scoreable=True,
        full_distribution_observed={"buyer_action_positive": 22, "receptive": 41,
                                    "uncertain_proof_needed": 27, "skeptical_resistant": 10}))
    with pytest.raises(ValidationError):  # non-summing
        OutcomeRecord.model_validate(dict(
            case_id="c", outcome_timestamp="2022-06-20", outcome_type="survey_distribution",
            scoring_mapping_type="direct_observed_distribution", full_distribution_scoreable=True,
            full_distribution_observed={"buyer_action_positive": 10, "receptive": 10,
                                        "uncertain_proof_needed": 10, "skeptical_resistant": 10}))


# --------------------------------------------------------------------------
# Hashes
# --------------------------------------------------------------------------
def test_hashes_deterministic_and_sensitive():
    rep1 = _build()
    rep2 = _build()
    assert rep1.pack.full_case_pack_hash == rep2.pack.full_case_pack_hash
    # changing the outcome changes the outcome + full hash, not the input bundle hash
    rep3 = _build(o=_outcome(metrics={"backers": 9999}))
    assert rep3.pack.outcome_record_hash != rep1.pack.outcome_record_hash
    assert rep3.pack.full_case_pack_hash != rep1.pack.full_case_pack_hash
    assert rep3.pack.input_bundle_hash == rep1.pack.input_bundle_hash
    assert hash_obj(_bundle()) == hash_obj(_bundle())


# --------------------------------------------------------------------------
# Eligibility downgrade + diversity
# --------------------------------------------------------------------------
def test_missing_high_confidence_timestamp_downgrades():
    b = _bundle(evidence_items=[
        {"source_id": "acc", "accessed_at": "2022-04-20", "source_text_excerpt": "teased", "content_hash": "sha256:a"},
    ])
    rep = _build(b)  # only accessed_at -> medium confidence -> not Tier 1
    assert rep.pack.blindness_tier == 2 and rep.pack.case_status == "case_study_only"


def test_diversity_guard():
    assert check_diversity([_cm(expected_outcome_class="success")])["balanced"] is False
    balanced = check_diversity([
        _cm(expected_outcome_class="success", category="hardware"),
        _cm(expected_outcome_class="failure", category="saas", platform="producthunt"),
        _cm(expected_outcome_class="middling", category="games", platform="kickstarter", fame_level="obscure"),
    ])
    assert balanced["balanced"] is True


# --------------------------------------------------------------------------
# CLI dry-run / write
# --------------------------------------------------------------------------
def _cli(name):
    path = _API / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_dry_run_writes_nothing(tmp_path):
    packs = tmp_path / "packs"
    rc = _cli("phase_17d_create_historical_case_pack.py").main([
        "--metadata", str(_FIX / "01_clean_accepted_tier1.json"), "--packs-dir", str(packs)])
    assert rc == 0
    assert not packs.exists() or not list(packs.rglob("*.json"))


def test_cli_write_persists_only_to_packs_dir(tmp_path):
    packs = tmp_path / "packs"
    rc = _cli("phase_17d_create_historical_case_pack.py").main([
        "--metadata", str(_FIX / "01_clean_accepted_tier1.json"), "--packs-dir", str(packs), "--write"])
    assert rc == 0
    written = list(packs.rglob("case_pack.json"))
    assert len(written) == 1
    assert "accepted" in str(written[0])  # accepted pack -> accepted/ subdir


def test_cli_validate_ok(tmp_path):
    rc = _cli("phase_17d_validate_historical_case_pack.py").main([
        "--metadata", str(_FIX / "01_clean_accepted_tier1.json")])
    assert rc == 0


# --------------------------------------------------------------------------
# Fixture classifications
# --------------------------------------------------------------------------
@pytest.mark.parametrize("fname,status", [
    ("01_clean_accepted_tier1.json", "accepted"),
    ("02_post_outcome_leakage_rejected.json", "rejected"),
    ("03_action_anchor_only.json", "accepted"),
    ("04_direct_observed_distribution.json", "accepted"),
    ("05_missing_timestamp_downgrade.json", "case_study_only"),
    ("06_famous_memorization_risk.json", "rejected"),
])
def test_fixture_classifications(fname, status):
    meta = json.loads((_FIX / fname).read_text())
    rep = build_case_pack(
        input_bundle=InputBundle.model_validate(meta["input_bundle"]),
        outcome_record=OutcomeRecord.model_validate(meta["outcome_record"]),
        candidate_metadata=CandidateMetadata.model_validate(meta["candidate_metadata"]),
        provenance=ProvenanceInputs.model_validate(meta["provenance"]),
        product_name=meta["product_name"], flagged_outcome_values=meta.get("flagged_outcome_values"))
    assert rep.pack.case_status == status


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------
def test_packs_dir_not_under_validation_cases():
    d = str(default_packs_dir())
    assert "historical_case_packs" in d and "validation_cases" not in d


def test_pack_is_not_a_validation_case():
    from assembly.validation_ledger.schema import ValidationCase
    rep = _build()
    with pytest.raises(ValidationError):
        ValidationCase.model_validate(json.loads(rep.pack.model_dump_json()))


def test_no_model_or_runtime_imports():
    banned = ("import torch", "import vllm", "import ollama", "from llama_cpp", "import transformers",
              "import openai", "import anthropic", "import requests", "import httpx",
              "assembly.validation_ledger", "assembly.config", "assembly.orchestration", "assembly.pipeline")
    for py in _PKG.glob("*.py"):
        src = py.read_text()
        for b in banned:
            assert b not in src, f"{py.name} must not import {b}"


def test_ledger_unchanged_and_15e_blocked():
    from assembly.validation_factory.outcome_mapping_protocol import mapping_readiness
    from assembly.validation_ledger.loader import load_all_cases
    cases = load_all_cases()
    assert len(cases) == 8
    assert mapping_readiness(ledger_cases=cases)["phase_15e_blocked"] is True


# --------------------------------------------------------------------------
# Hardening regressions (from the Phase 17D adversarial review)
# --------------------------------------------------------------------------
def test_structured_field_leak_rejected():
    # outcome text in a model-facing STRUCTURED field (not evidence) must be caught
    b = _bundle(product_description="the campaign successfully funded, raising $250,000 from 3,000 backers")
    rep = _build(b)
    assert "product_description" in rep.leakage_audit["structured_field_leak_flags"]
    assert rep.leakage_audit["input_bundle_clean"] is False
    assert rep.pack.case_status == "rejected"
    # if the status were FORGED to 'accepted', the validator catches the structured leak
    forged = rep.pack.model_copy(update={"case_status": "accepted"})
    assert any("structured" in i for i in validate_case_pack(input_bundle=b, outcome_record=_outcome(), pack=forged))


def test_structured_leak_traction_field():
    b = _bundle(traction_signals_pre_outcome="postmortem: it crushed its goal; final tally 3000 backers")
    rep = _build(b)
    assert rep.pack.case_status == "rejected"


def test_unverified_evidence_only_candidate_not_accepted():
    # a clean Tier-1 case whose evidence is merely 'uncertain' (not attested) -> candidate
    b = _bundle(evidence_items=[
        {"source_id": "u", "published_at": "2022-04-10", "source_text_excerpt": "launch",
         "content_hash": "sha256:a", "pre_outcome_status": "uncertain"}])
    rep = _build(b)
    assert rep.pack.case_status == "candidate"
    assert rep.pack.eligible_for_public_claim is False


def test_outcome_bucket_range_and_extra_keys_rejected():
    with pytest.raises(ValidationError):  # negative bucket
        OutcomeRecord.model_validate(dict(
            case_id="c", outcome_timestamp="2022-06-20", outcome_type="survey_distribution",
            scoring_mapping_type="direct_observed_distribution", full_distribution_scoreable=True,
            full_distribution_observed={"buyer_action_positive": -5, "receptive": 55,
                                        "uncertain_proof_needed": 30, "skeptical_resistant": 20}))
    with pytest.raises(ValidationError):  # unknown bucket
        OutcomeRecord.model_validate(dict(
            case_id="c", outcome_timestamp="2022-06-20", outcome_type="survey_distribution",
            scoring_mapping_type="direct_observed_distribution", full_distribution_scoreable=True,
            full_distribution_observed={"buyer_action_positive": 22, "receptive": 41,
                                        "uncertain_proof_needed": 27, "skeptical_resistant": 10, "evil": 5}))


def test_not_scoreable_consistency():
    with pytest.raises(ValidationError):  # type says not_scoreable but flag is False
        OutcomeRecord.model_validate(dict(
            case_id="c", outcome_timestamp="2022-06-20", outcome_type="other",
            scoring_mapping_type="not_scoreable", buyer_anchor_scoreable=True))
    with pytest.raises(ValidationError):  # not_scoreable + a positive scoreability flag
        OutcomeRecord.model_validate(dict(
            case_id="c", outcome_timestamp="2022-06-20", outcome_type="other",
            scoring_mapping_type="evidence_only", not_scoreable=True, qualitative_scoreable=True))


def test_path_traversal_case_id_rejected(tmp_path):
    from assembly.benchmarks.market_fidelity.historical_cases.storage import write_case_pack
    b = _bundle(case_id="../../etc/evil")
    o = _outcome(case_id="../../etc/evil")
    rep = _build(b, o)
    with pytest.raises(ValueError):
        write_case_pack(rep, b, o, allow_write=True, packs_dir=tmp_path)


def test_cli_malformed_json_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    rc = _cli("phase_17d_create_historical_case_pack.py").main(["--metadata", str(bad)])
    assert rc == 1
