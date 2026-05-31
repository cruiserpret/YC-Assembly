"""Phase 15B — validation-ledger schema / metrics / loader / safety tests.

Pure, deterministic: no LLM, no network, no DB, no production-simulation code.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.validation_ledger import metrics as mt
from assembly.validation_ledger.loader import (
    DEFAULT_LEDGER_PATH,
    compute_case_metrics,
    holdout_cases,
    ledger_summary,
    load_cases,
    training_cases,
)
from assembly.validation_ledger.schema import (
    AntiOverfit,
    CaseMetadata,
    MarketDistribution,
    ObservedProportions,
    ValidationCase,
)

_PKG_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "validation_ledger"
)


def _dist(b, r, u, s) -> dict[str, float]:
    return {
        "buyer_action_positive": b,
        "receptive": r,
        "uncertain_proof_needed": u,
        "skeptical_resistant": s,
    }


def _valid_case(**overrides) -> ValidationCase:
    base = dict(
        case_id="c1",
        metadata=CaseMetadata(
            product_name="X",
            source_type="hacker_news",
            product_category="developer_tools",
            launch_stage="launched",
            date_run="2026-05-23",
            validation_status="scored",
        ),
        predicted=MarketDistribution(**_dist(10, 40, 25, 25)),
        observed=ObservedProportions(**_dist(10, 30, 30, 30)),
    )
    base.update(overrides)
    return ValidationCase(**base)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_valid_case_loads():
    c = _valid_case()
    assert c.case_id == "c1"
    assert c.is_scorable()


def test_missing_bucket_fails():
    with pytest.raises(ValidationError):
        MarketDistribution(
            buyer_action_positive=10, receptive=40, uncertain_proof_needed=50
        )  # skeptical_resistant missing


def test_invalid_source_type_fails():
    with pytest.raises(ValidationError):
        CaseMetadata(
            product_name="X",
            source_type="twitter",  # not in the allowed Literal
            product_category="x",
            launch_stage="launched",
            date_run="2026-05-23",
            validation_status="scored",
        )


def test_bucket_out_of_range_fails():
    with pytest.raises(ValidationError):
        MarketDistribution(**_dist(150, 0, 0, 0))
    with pytest.raises(ValidationError):
        MarketDistribution(**_dist(-5, 35, 35, 35))


def test_distribution_must_sum_to_100():
    with pytest.raises(ValidationError):
        MarketDistribution(**_dist(25, 25, 25, 10))  # sums to 85
    # within tolerance is fine
    MarketDistribution(**_dist(25, 25, 25, 25.5))


def test_training_and_holdout_cannot_both_be_true():
    AntiOverfit(used_for_training=True, used_for_holdout=False)
    AntiOverfit(used_for_training=False, used_for_holdout=True)
    with pytest.raises(ValidationError):
        AntiOverfit(used_for_training=True, used_for_holdout=True)


def test_scored_requires_predicted_and_observed():
    with pytest.raises(ValidationError):
        _valid_case(predicted=None, observed=None)
    # 'pending' may omit predicted/observed
    meta = CaseMetadata(
        product_name="X",
        source_type="hacker_news",
        product_category="x",
        launch_stage="launched",
        date_run="2026-05-23",
        validation_status="pending",
    )
    c = ValidationCase(case_id="p1", metadata=meta)
    assert not c.is_scorable()


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_mae_pp():
    pred = _dist(0, 50, 0, 50)
    obs = _dist(0, 30, 20, 50)
    # |0|+|20|+|20|+|0| = 40 / 4 = 10
    assert mt.mae_pp(pred, obs) == 10.0


def test_total_variation_distance():
    pred = _dist(0, 100, 0, 0)
    obs = _dist(0, 0, 0, 100)
    # completely disjoint -> TVD == 1.0
    assert mt.total_variation_distance(pred, obs) == pytest.approx(1.0)
    assert mt.total_variation_distance(pred, pred) == pytest.approx(0.0)


def test_max_bucket_error_pp():
    assert mt.max_bucket_error_pp(_dist(0, 80, 0, 20), _dist(0, 30, 0, 70)) == 50.0


def test_bucket_errors_signed_correctly():
    errs = mt.bucket_errors(_dist(10, 60, 10, 20), _dist(40, 20, 10, 30))
    assert errs["buyer_action_positive"] == -30.0  # under-predicted
    assert errs["receptive"] == 40.0  # over-predicted
    assert errs["uncertain_proof_needed"] == 0.0
    assert errs["skeptical_resistant"] == -10.0


def test_normalization_works():
    out = mt.normalize_distribution(_dist(0, 25, 25, 50), scale=100.0)
    assert sum(out.values()) == pytest.approx(100.0)
    half = mt.normalize_distribution(_dist(0, 1, 1, 2), scale=1.0)
    assert half["skeptical_resistant"] == pytest.approx(0.5)


def test_normalize_rejects_nonpositive_sum():
    with pytest.raises(ValueError):
        mt.normalize_distribution(_dist(0, 0, 0, 0))


def test_validate_distribution_sums():
    assert mt.validate_distribution_sums(_dist(25, 25, 25, 25)) is True
    assert mt.validate_distribution_sums(_dist(25, 25, 25, 10)) is False


def test_direction_match():
    assert mt.direction_match(_dist(0, 10, 0, 90), _dist(0, 20, 0, 80)) is True  # both skeptical
    assert mt.direction_match(_dist(0, 90, 0, 10), _dist(0, 10, 0, 90)) is False


def test_buyer_false_confidence():
    assert mt.buyer_false_confidence(_dist(40, 20, 20, 20), _dist(5, 30, 30, 35)) is True
    assert mt.buyer_false_confidence(_dist(5, 30, 30, 35), _dist(40, 20, 20, 20)) is False


def test_partial_observed_handled_gracefully():
    meta = CaseMetadata(
        product_name="X",
        source_type="github",
        product_category="open_source_software",
        launch_stage="launched",
        date_run="2026-05-23",
        validation_status="partial",
    )
    c = ValidationCase(
        case_id="partial1",
        metadata=meta,
        predicted=MarketDistribution(**_dist(10, 40, 25, 25)),
        observed=None,
    )
    assert compute_case_metrics(c) is None  # no crash, graceful None


# --------------------------------------------------------------------------
# Ledger (seed_cases.json)
# --------------------------------------------------------------------------


def test_seed_ledger_loads():
    cases = load_cases()
    assert len(cases) == 6
    names = {c.case_id for c in cases}
    assert {"opslane", "docuseal_v2", "files_md", "naptick_ai", "hasdata",
            "tiiny_ai_pocket_lab"} == names


def test_all_case_ids_unique():
    cases = load_cases()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_all_scored_cases_have_predicted_and_observed():
    for c in load_cases():
        if c.metadata.validation_status == "scored":
            assert c.predicted is not None
            assert c.observed is not None


def test_all_scored_cases_compute_metrics():
    for c in load_cases():
        if c.metadata.validation_status == "scored":
            m = compute_case_metrics(c)
            assert m is not None
            assert m.mae_pp is not None and 0.0 <= m.mae_pp <= 100.0
            assert m.tvd is not None and 0.0 <= m.tvd <= 1.0


def test_no_case_is_both_training_and_holdout():
    for c in load_cases():
        assert not (c.anti_overfit.used_for_training and c.anti_overfit.used_for_holdout)


def test_anti_overfit_fields_exist():
    for c in load_cases():
        assert isinstance(c.anti_overfit.used_for_training, bool)
        assert isinstance(c.anti_overfit.used_for_holdout, bool)


def test_ledger_reproduces_known_baseline_mae():
    """Strong validation: the seed reproduces the known no-behavioral baseline
    avg MAE of ~25.30 — i.e. the locked predictions + metrics are correct."""
    summary = ledger_summary(load_cases())
    assert summary["avg_mae_pp"] == pytest.approx(25.30, abs=0.05)
    assert summary["n_scored"] == 6
    # All six are development/training cases; none is a clean holdout yet.
    assert len(training_cases(load_cases())) == 6
    assert len(holdout_cases(load_cases())) == 0


def test_default_ledger_path_exists():
    assert DEFAULT_LEDGER_PATH.exists()
    assert DEFAULT_LEDGER_PATH.name == "seed_cases.json"


# --------------------------------------------------------------------------
# Safety — the ledger package introduces NO model/behavioral/token/LLM logic
# --------------------------------------------------------------------------


def _pkg_sources() -> list[Path]:
    return sorted(_PKG_DIR.glob("*.py"))


def test_no_phase13_or_behavioral_or_flags_in_package():
    forbidden = (
        "behavioral_mind_layer",
        "assembly_behavioral",
        "behavioralvector",
        "compute_behavioral_response",
    )
    for p in _pkg_sources():
        src = p.read_text(encoding="utf-8").lower()
        for tok in forbidden:
            assert tok not in src, f"{p.name} must not reference {tok}"


def test_no_token_or_llm_or_network_imports_in_package():
    forbidden_mods = {
        "anthropic", "openai", "httpx", "requests", "aiohttp",
        "sqlalchemy", "redis",
    }
    for p in _pkg_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    mods.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        leaked = mods & forbidden_mods
        assert not leaked, f"{p.name} imports forbidden modules: {leaked}"


def test_package_imports_only_stdlib_pydantic_and_self():
    allowed = {
        # stdlib + pydantic + self. hashlib added in Phase 15I for the
        # deterministic prediction-lock hashing (prediction_lock.py).
        "__future__", "json", "hashlib", "pathlib", "typing", "collections",
        "pydantic", "assembly",
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
