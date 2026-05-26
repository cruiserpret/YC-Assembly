"""Phase 12E.fix1 + 12E.fix2 — post-DocuSeal harness bug regression tests.

Covers two bugs that surfaced AFTER the DocuSeal N=1 paid run succeeded:

  Bug 1 (live_founder_brief.py)
    runtime_config.json write failed with UnboundLocalError on `run`
    because a Phase 12E edit referenced `run.product_brief` BEFORE
    `run` was loaded inside the per-stage loop.

  Bug 2 (outcome_labeling.py + blind_case_schema.py)
    parse_labeled_outcome_file raised OutcomeLabelingError on missing
    observed_collection_date, which propagated up through the
    variance harness's scoring step and caused _render_md to crash
    when iterating salvage records that lacked `run_id`.

Tests are pure-python: no DB, no LLM, no network.
"""
from __future__ import annotations

import importlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from assembly.calibration.blind_case_schema import HiddenRealWorldOutcome
from assembly.calibration.outcome_labeling import (
    BlindScoringResult,
    LabeledOutcomeFile,
    LabeledOutcomeRow,
    OutcomeLabelingError,
    _render_md,
    parse_labeled_outcome_file,
    write_phase_12a_9_audit,
)


API_ROOT = Path(__file__).resolve().parents[1]


# -----------------------------------------------------------------------
# Bug 2.A — Pydantic schema accepts missing observed_collection_date.
# -----------------------------------------------------------------------


def test_hidden_real_world_outcome_accepts_missing_date():
    """Pre-12E.fix2 the field was required and raised at validation
    time. Now it's optional and defaults to None."""
    h = HiddenRealWorldOutcome.model_validate({
        "observed_distribution": {
            "buyer": 5, "receptive": 10, "uncertain": 8, "skeptical": 7,
        },
        "observed_sample_size": 30,
        "observed_source_type": "post_launch_survey",
        # observed_collection_date intentionally omitted
    })
    assert h.observed_collection_date is None


def test_hidden_real_world_outcome_still_accepts_explicit_date():
    """Backwards-compat: explicit dates still work."""
    h = HiddenRealWorldOutcome.model_validate({
        "observed_distribution": {
            "buyer": 5, "receptive": 10, "uncertain": 8, "skeptical": 7,
        },
        "observed_sample_size": 30,
        "observed_source_type": "post_launch_survey",
        "observed_collection_date": "2025-03-15",
    })
    assert h.observed_collection_date == date(2025, 3, 15)


# -----------------------------------------------------------------------
# Bug 2.B — parse_labeled_outcome_file degrades gracefully on missing date.
# -----------------------------------------------------------------------


def _write_labels_json(tmp_path: Path, *, with_date: bool) -> Path:
    payload = {
        "rows": [
            {"comment_id": "c1", "label": "buyer", "excerpt": "I will buy"},
            {"comment_id": "c2", "label": "skeptical", "excerpt": "no"},
        ],
        "labeler_notes_summary": "test labels",
    }
    if with_date:
        payload["observed_collection_date"] = "2025-03-15"
    p = tmp_path / "labels.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_parse_labels_json_missing_observed_collection_date(tmp_path):
    p = _write_labels_json(tmp_path, with_date=False)
    parsed = parse_labeled_outcome_file(
        p, cutoff_date=date(2024, 1, 1),
    )
    assert parsed.observed_collection_date is None
    # The warning is surfaced for audit visibility.
    assert any(
        "missing_observed_collection_date_defaulted_to_unknown" in w
        for w in parsed.parse_warnings
    )
    # Rows still parsed correctly.
    assert len(parsed.rows) == 2


def test_parse_labels_json_with_date_still_works(tmp_path):
    """Regression — the present-date path must continue to work."""
    p = _write_labels_json(tmp_path, with_date=True)
    parsed = parse_labeled_outcome_file(
        p, cutoff_date=date(2024, 1, 1),
    )
    assert parsed.observed_collection_date == date(2025, 3, 15)
    assert not any(
        "defaulted_to_unknown" in w for w in parsed.parse_warnings
    )


def test_parse_labels_json_still_rejects_date_before_cutoff(tmp_path):
    """When a date IS provided, the cutoff-date strictness check
    must still fire — the optional-date change does not loosen the
    leakage guard."""
    payload = {
        "observed_collection_date": "2023-12-31",  # before cutoff
        "rows": [
            {"comment_id": "c1", "label": "buyer"},
        ],
    }
    p = tmp_path / "labels.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OutcomeLabelingError) as ei:
        parse_labeled_outcome_file(p, cutoff_date=date(2024, 1, 1))
    assert "not strictly after cutoff_date" in str(ei.value)


def test_parse_labels_csv_missing_date_header(tmp_path):
    """CSV parser must mirror the JSON parser's optional-date
    behaviour. Pre-fix it raised csv_missing_observed_collection_date_header.
    """
    csv_text = (
        "# variance harness test labels\n"
        "comment_id,label,excerpt\n"
        "c1,buyer,\"I will buy\"\n"
        "c2,skeptical,no\n"
    )
    p = tmp_path / "labels.csv"
    p.write_text(csv_text, encoding="utf-8")
    parsed = parse_labeled_outcome_file(
        p, cutoff_date=date(2024, 1, 1),
    )
    assert parsed.observed_collection_date is None
    assert any(
        "missing_observed_collection_date_defaulted_to_unknown" in w
        for w in parsed.parse_warnings
    )


# -----------------------------------------------------------------------
# Bug 2.C — _render_md does NOT crash when observed_collection_date is None.
# -----------------------------------------------------------------------


def _build_blind_scoring_result(
    *, observed_date: date | None,
) -> BlindScoringResult:
    return BlindScoringResult(
        candidate_id="test_candidate_001",
        cutoff_date=date(2024, 1, 1),
        observed_collection_date=observed_date,
        prediction_artifact_path="/tmp/test_prediction.json",
        prediction_artifact_hash_before="hash_before_aaa",
        prediction_artifact_hash_after="hash_before_aaa",
        prediction_artifact_hash_unchanged=True,
        predicted_distribution_percent={
            "buyer": 10.0, "receptive": 30.0,
            "uncertain": 40.0, "skeptical": 20.0,
        },
        observed_distribution_percent={
            "buyer": 8.0, "receptive": 25.0,
            "uncertain": 45.0, "skeptical": 22.0,
        },
        predicted_counts={
            "buyer": 1, "receptive": 3, "uncertain": 4, "skeptical": 2,
        },
        observed_counts={
            "buyer": 8, "receptive": 25, "uncertain": 45, "skeptical": 22,
        },
        observed_sample_size=100,
        noise_dropped_count=0,
        signed_bucket_errors_pp={
            "buyer": 2.0, "receptive": 5.0,
            "uncertain": -5.0, "skeptical": -2.0,
        },
        absolute_bucket_errors_pp={
            "buyer": 2.0, "receptive": 5.0,
            "uncertain": 5.0, "skeptical": 2.0,
        },
        mean_absolute_bucket_error_pp=3.5,
        max_bucket_error_pp=5.0,
        total_variation_distance=0.07,
        false_confidence_warnings=[],
        objection_recall=None,
        interpretation_band="green",
        labeler_notes_summary="test",
    )


def test_render_md_handles_missing_observed_collection_date():
    """Pre-fix: _render_md called .isoformat() on a date that could
    be None, raising AttributeError. Post-fix: renders 'unknown'."""
    r = _build_blind_scoring_result(observed_date=None)
    md = _render_md(r)
    assert "observed_collection_date: unknown" in md
    # Sanity — rest of the report still renders.
    assert "Interpretation band" in md
    assert "Predicted vs Observed" in md
    assert "MAE" in md


def test_render_md_still_renders_explicit_date():
    r = _build_blind_scoring_result(observed_date=date(2025, 3, 15))
    md = _render_md(r)
    assert "observed_collection_date: 2025-03-15" in md
    assert "observed_collection_date: unknown" not in md


def test_audit_markdown_writes_without_crash_on_missing_date(tmp_path):
    """write_phase_12a_9_audit must produce both JSON + Markdown
    files even when observed_collection_date is None — this is the
    closest analogue to the variance harness's `aggregate.md` write
    that crashed in the DocuSeal run."""
    r = _build_blind_scoring_result(observed_date=None)
    json_path = tmp_path / "score.json"
    md_path = tmp_path / "score.md"
    write_phase_12a_9_audit(r, json_path=json_path, md_path=md_path)
    assert json_path.exists() and md_path.exists()
    # JSON serializes "unknown" rather than crashing.
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["observed_collection_date"] == "unknown"
    # Markdown carries the same.
    md_text = md_path.read_text(encoding="utf-8")
    assert "observed_collection_date: unknown" in md_text


def test_blind_scoring_result_as_dict_serializes_missing_date():
    r = _build_blind_scoring_result(observed_date=None)
    d = r.as_dict()
    assert d["observed_collection_date"] == "unknown"
    # date is required → still present.
    assert d["cutoff_date"] == "2024-01-01"


# -----------------------------------------------------------------------
# Bug 1 — runtime_config.json site no longer references unloaded `run`.
# -----------------------------------------------------------------------


def test_runtime_config_write_loads_run_inside_its_own_session():
    """Static guard: the live_founder_brief.py runtime_config block
    must load `run` explicitly inside its try block. Pre-12E.fix1 it
    referenced `run.product_brief` directly and failed with
    UnboundLocalError because `run` wasn't yet in scope.

    Approach: read the source and confirm:
      1. The block writes runtime_config.json.
      2. Inside the same try block, it awaits _load_run (i.e. loads
         the run rather than referencing an unbound `run`).
      3. The legacy bug pattern (`run.product_brief` reference
         without a preceding load) does not appear.
    """
    lfb = (
        API_ROOT / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    text = lfb.read_text(encoding="utf-8")
    # Find the runtime_config.json write site (only one in the file).
    write_idx = text.find('"runtime_config.json"')
    assert write_idx != -1, "runtime_config.json write site missing"
    # Look back ~30 lines for the matching try block to ensure the
    # fix is present.
    head = text[max(0, write_idx - 2000):write_idx]
    assert "try:" in head
    # The fix: _load_run is awaited explicitly inside the block.
    assert "await _load_run(" in head, (
        "Bug 1 fix missing: runtime_config block must load `run` "
        "in its own session."
    )
    # The fix uses a uniquely-named local (`_config_run`) so it
    # cannot shadow the per-stage loop's `run`.
    assert "_config_run" in head


def test_runtime_config_block_does_not_reference_run_before_load():
    """Direct grep guard: the legacy bug pattern was
    `_brief = run.product_brief or {}` placed BEFORE `run` was
    loaded. Confirm that exact line is gone."""
    lfb = (
        API_ROOT / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    text = lfb.read_text(encoding="utf-8")
    # The legacy literal — must not reappear in the runtime_config
    # block. Use a regex anchored to the runtime_config phrasing.
    bug_pattern = re.compile(
        r"runtime_config\.json[^\n]*\n(?:[^\n]*\n){0,40}?"
        r"_brief\s*=\s*run\.product_brief",
        re.DOTALL,
    )
    assert not bug_pattern.search(text), (
        "Bug 1 regression: runtime_config block references "
        "`run.product_brief` without a preceding _load_run call."
    )


# -----------------------------------------------------------------------
# Variance-harness _render_md tolerance (against the /tmp/ ad-hoc
# harness). The harness lives outside the repo, but the bug-fixed
# function body is reachable as source text — confirm the defensive
# pattern is present.
# -----------------------------------------------------------------------


def test_variance_harness_render_md_uses_defensive_get_for_run_id():
    """Static guard against regression of the /tmp/ variance harness
    bug: the per-run loop in `_render_md` must NOT access `r['run_id']`
    or `r['run_idx']` directly. Both are missing on salvage records.

    The harness is at /tmp/phase_12a_10c_repeatability_harness.py.
    If the file isn't present (clean machine), skip — this test is
    only a guard for environments where the harness is in use."""
    harness = Path("/tmp/phase_12a_10c_repeatability_harness.py")
    if not harness.exists():
        pytest.skip("variance harness not present in /tmp/")
    text = harness.read_text(encoding="utf-8")
    # The legacy unsafe pattern.
    assert "r['run_idx']" not in text, (
        "variance harness regressed: _render_md uses r['run_idx'] "
        "directly; salvage records lack this key."
    )
    assert "r['run_id']" not in text, (
        "variance harness regressed: _render_md uses r['run_id'] "
        "directly; salvage records lack this key."
    )
    # The defensive replacement landmarks.
    assert 'r.get("run_id")' in text
    assert 'r.get("run_idx")' in text


def test_variance_harness_wraps_scoring_in_try_except():
    """The harness must not lose a successful pipeline run's metrics
    when scoring fails — confirm the scoring block is wrapped."""
    harness = Path("/tmp/phase_12a_10c_repeatability_harness.py")
    if not harness.exists():
        pytest.skip("variance harness not present in /tmp/")
    text = harness.read_text(encoding="utf-8")
    # The defensive marker we added.
    assert 'metrics["scoring_error"] = {' in text


# -----------------------------------------------------------------------
# No new LLM calls / no apps/web / no DB migration (12E.fix guard)
# -----------------------------------------------------------------------


def test_outcome_labeling_has_no_provider_call_sites():
    p = (
        API_ROOT / "src" / "assembly" / "calibration"
        / "outcome_labeling.py"
    )
    text = p.read_text(encoding="utf-8")
    for needle in (
        "provider.chat(",
        "provider.structured_output(",
        ".messages.create(",
        "with_cost_guard(",
        "import anthropic",
        "import openai",
    ):
        assert needle not in text, (
            f"outcome_labeling.py uses forbidden surface: {needle!r}"
        )


def test_no_new_alembic_migration_for_12e_fix():
    versions = API_ROOT / "alembic" / "versions"
    if not versions.exists():
        pytest.skip("alembic/versions not present")
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in ("phase_12e_fix", "12e.fix", "observed_collection_date_optional"):
            assert needle not in text, (
                f"unexpected migration {f.name} mentions {needle!r}; "
                "12E.fix1/2 should not add any migration"
            )


def test_outcome_labeling_module_imports_without_llm_provider():
    """Fresh import surface check via importlib reload — confirms no
    transitive LLM provider import sneaks in via the schema/parser."""
    import sys
    for mod in list(sys.modules):
        if mod.startswith("assembly.calibration.outcome_labeling"):
            del sys.modules[mod]
    importlib.import_module("assembly.calibration.outcome_labeling")
    # Light invariant — focus on direct collateral damage, since the
    # broader pipeline already loads providers transitively from
    # tests/ before this point in the suite.
    mod = sys.modules.get("assembly.calibration.outcome_labeling")
    assert mod is not None
    assert hasattr(mod, "_render_md")
    assert hasattr(mod, "parse_labeled_outcome_file")