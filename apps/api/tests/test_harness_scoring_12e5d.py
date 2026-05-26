"""Phase 12E.5D — harness scoring durability tests.

Covers `apps/api/src/assembly/calibration/harness_scoring.py` and the
contract the variance harness relies on. No DB, no LLM, no network.

Test groups:
  A. Preflight verification (fail loud on missing / malformed labels).
  B. Durable copy semantics (file lands in batch_dir/labels_used/,
     hash is stable, original path is recorded).
  C. Scoring against durable labels (deterministic math, matches
     market_fidelity).
  D. Skeptic-retention extractor (handles both Phase 12C name and
     Phase 12E.5A pillar name).
  E. Variance harness wiring (the /tmp script uses the helper, fails
     loud post-batch when scoring requested but missing).
  F. Discipline (no provider imports, no apps/web, no DB migration).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from assembly.calibration.harness_scoring import (
    DurableLabels,
    LabelsFileError,
    copy_labels_into_batch_dir,
    extract_hard_resistant_count_from_diversity_health,
    extract_skeptic_retention_from_diversity_health,
    score_run_against_durable_labels,
    verify_labels_file_or_raise,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_labels_payload(date_str: str = "2025-03-15") -> dict:
    """A small valid labels-file payload matching parse_labeled_outcome_file's contract."""
    return {
        "observed_collection_date": date_str,
        "labeler_notes_summary": "test labels",
        "rows": [
            {"comment_id": "c1", "label": "buyer", "excerpt": "I will buy"},
            {"comment_id": "c2", "label": "buyer", "excerpt": "shipping it"},
            {"comment_id": "c3", "label": "receptive", "excerpt": "interesting"},
            {"comment_id": "c4", "label": "uncertain", "excerpt": "maybe"},
            {"comment_id": "c5", "label": "skeptical", "excerpt": "doubt it"},
            {"comment_id": "c6", "label": "noise", "excerpt": "lol"},
        ],
    }


def _write_labels_json(tmp_path: Path, payload: dict | None = None,
                       name: str = "labels.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload or _minimal_labels_payload()))
    return p


# ---------------------------------------------------------------------------
# A. Preflight
# ---------------------------------------------------------------------------


def test_preflight_missing_path_raises_labels_error(tmp_path):
    with pytest.raises(LabelsFileError, match="not found"):
        verify_labels_file_or_raise(tmp_path / "does_not_exist.json")


def test_preflight_directory_path_raises(tmp_path):
    with pytest.raises(LabelsFileError, match="not a file"):
        verify_labels_file_or_raise(tmp_path)


def test_preflight_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json")
    with pytest.raises(LabelsFileError, match="malformed JSON"):
        verify_labels_file_or_raise(p)


def test_preflight_top_level_not_dict_raises(tmp_path):
    p = tmp_path / "list_root.json"
    p.write_text(json.dumps(["row1", "row2"]))
    with pytest.raises(LabelsFileError, match="top-level not a dict"):
        verify_labels_file_or_raise(p)


def test_preflight_missing_rows_raises(tmp_path):
    p = tmp_path / "no_rows.json"
    p.write_text(json.dumps({"observed_collection_date": "2025-03-15"}))
    with pytest.raises(LabelsFileError, match="no `rows` list"):
        verify_labels_file_or_raise(p)


def test_preflight_empty_rows_raises(tmp_path):
    p = tmp_path / "empty_rows.json"
    p.write_text(json.dumps({"rows": []}))
    with pytest.raises(LabelsFileError, match="empty"):
        verify_labels_file_or_raise(p)


def test_preflight_valid_file_returns_path(tmp_path):
    p = _write_labels_json(tmp_path)
    result = verify_labels_file_or_raise(p)
    assert isinstance(result, Path)
    assert result == p


# ---------------------------------------------------------------------------
# B. Durable copy semantics
# ---------------------------------------------------------------------------


def test_copy_creates_labels_used_subdir(tmp_path):
    src = _write_labels_json(tmp_path / "src_area" if (tmp_path / "src_area").mkdir() or True else tmp_path)
    batch = tmp_path / "batch_dir"
    result = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    assert (batch / "labels_used").is_dir()
    assert result.durable_path.parent.name == "labels_used"


def test_copy_returns_correct_sha256(tmp_path):
    src = _write_labels_json(tmp_path)
    batch = tmp_path / "batch"
    result = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    # Manually compute the expected hash.
    expected = hashlib.sha256(src.read_bytes()).hexdigest()
    assert result.sha256 == expected
    assert isinstance(result, DurableLabels)
    assert result.n_bytes == src.stat().st_size


def test_copy_preserves_filename(tmp_path):
    src = _write_labels_json(tmp_path, name="custom_labels.json")
    batch = tmp_path / "b"
    result = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    assert result.durable_path.name == "custom_labels.json"


def test_copy_records_original_path_resolved(tmp_path):
    src = _write_labels_json(tmp_path)
    batch = tmp_path / "b"
    result = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    assert result.original_path == src.resolve()


def test_copy_then_delete_original_does_not_corrupt_durable(tmp_path):
    """Core 12E.5D durability invariant: after the copy lands, the
    original can disappear (e.g. /tmp gets cleaned) and the durable
    copy is still usable."""
    src = _write_labels_json(tmp_path)
    batch = tmp_path / "b"
    result = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    src.unlink()  # simulate /tmp cleanup
    # The durable copy must still load + score correctly.
    score = score_run_against_durable_labels(
        predicted_pct={"buyer": 25, "receptive": 25, "uncertain": 25, "skeptical": 25},
        durable_labels_path=result.durable_path,
        cutoff_date=date(2024, 1, 1),
    )
    assert "mae_pp" in score
    assert score["observed_sample_size"] == 5  # 5 scorable rows


def test_copy_on_missing_src_raises(tmp_path):
    with pytest.raises(LabelsFileError):
        copy_labels_into_batch_dir(
            labels_src=tmp_path / "nope.json",
            batch_dir=tmp_path / "b",
        )


# ---------------------------------------------------------------------------
# C. Scoring against durable labels
# ---------------------------------------------------------------------------


def test_scoring_returns_required_keys(tmp_path):
    src = _write_labels_json(tmp_path)
    batch = tmp_path / "b"
    durable = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    score = score_run_against_durable_labels(
        predicted_pct={"buyer": 40, "receptive": 30, "uncertain": 20, "skeptical": 10},
        durable_labels_path=durable.durable_path,
        cutoff_date=date(2024, 1, 1),
    )
    for k in (
        "observed_pct", "signed_err_pp", "abs_err_pp",
        "mae_pp", "max_err_pp", "tvd",
        "observed_sample_size", "noise_dropped_count",
        "labels_path_used", "labels_parse_warnings",
    ):
        assert k in score


def test_scoring_math_matches_manual(tmp_path):
    """5 scorable rows: 2 buyer / 1 receptive / 1 uncertain / 1 skeptical.
    Observed_pct = {buyer 40, receptive 20, uncertain 20, skeptical 20}.
    If predicted matches exactly → MAE = 0."""
    src = _write_labels_json(tmp_path)
    batch = tmp_path / "b"
    durable = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    score = score_run_against_durable_labels(
        predicted_pct={"buyer": 40, "receptive": 20, "uncertain": 20, "skeptical": 20},
        durable_labels_path=durable.durable_path,
        cutoff_date=date(2024, 1, 1),
    )
    assert score["mae_pp"] == pytest.approx(0.0)
    assert score["tvd"] == pytest.approx(0.0)
    assert score["max_err_pp"] == pytest.approx(0.0)


def test_scoring_raises_if_durable_path_disappears_mid_batch(tmp_path):
    """Defensive guard: if someone deletes the durable copy between
    preflight and scoring, fail loud rather than silently skip."""
    src = _write_labels_json(tmp_path)
    batch = tmp_path / "b"
    durable = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    durable.durable_path.unlink()
    with pytest.raises(LabelsFileError, match="disappeared"):
        score_run_against_durable_labels(
            predicted_pct={"buyer": 25, "receptive": 25, "uncertain": 25, "skeptical": 25},
            durable_labels_path=durable.durable_path,
            cutoff_date=date(2024, 1, 1),
        )


def test_scoring_handles_missing_observed_date(tmp_path):
    """Phase 12E.fix2 — missing date no longer raises; scorer
    succeeds, parse_warnings carries the marker."""
    payload = _minimal_labels_payload()
    del payload["observed_collection_date"]
    src = _write_labels_json(tmp_path, payload=payload)
    batch = tmp_path / "b"
    durable = copy_labels_into_batch_dir(labels_src=src, batch_dir=batch)
    score = score_run_against_durable_labels(
        predicted_pct={"buyer": 25, "receptive": 25, "uncertain": 25, "skeptical": 25},
        durable_labels_path=durable.durable_path,
        cutoff_date=date(2024, 1, 1),
    )
    assert "labels_parse_warnings" in score
    assert any(
        "missing_observed_collection_date" in w
        for w in score["labels_parse_warnings"]
    )


# ---------------------------------------------------------------------------
# D. Skeptic-retention extractor (TASK 3)
# ---------------------------------------------------------------------------


def test_extract_skeptic_retention_handles_phase_12c_key():
    """Phase 12C diversity_health.json uses `skeptic_retention_rate`."""
    dh = {"skeptic_retention_rate": 0.85}
    assert extract_skeptic_retention_from_diversity_health(dh) == 0.85


def test_extract_skeptic_retention_handles_pillar_contract_key():
    """The Phase 12E.5A pillar contract uses `skeptic_retention`."""
    dh = {"skeptic_retention": 0.72}
    assert extract_skeptic_retention_from_diversity_health(dh) == 0.72


def test_extract_skeptic_retention_prefers_pillar_key_when_both_present():
    """When both fields are present (defensive), prefer the pillar-
    contract key for forward-compat."""
    dh = {"skeptic_retention": 0.9, "skeptic_retention_rate": 0.8}
    assert extract_skeptic_retention_from_diversity_health(dh) == 0.9


def test_extract_skeptic_retention_none_on_missing():
    assert extract_skeptic_retention_from_diversity_health(None) is None
    assert extract_skeptic_retention_from_diversity_health({}) is None


def test_extract_skeptic_retention_handles_string_value():
    dh = {"skeptic_retention_rate": "0.55"}
    assert extract_skeptic_retention_from_diversity_health(dh) == 0.55


def test_extract_hard_resistant_count_works():
    assert extract_hard_resistant_count_from_diversity_health(
        {"hard_resistant_count": 29}
    ) == 29
    assert extract_hard_resistant_count_from_diversity_health({}) is None
    assert extract_hard_resistant_count_from_diversity_health(None) is None


# ---------------------------------------------------------------------------
# E. Variance harness wiring (static guards)
# ---------------------------------------------------------------------------


def test_variance_harness_uses_durable_labels_helper():
    """The /tmp harness must import + use the durable-labels helpers
    from harness_scoring (not its own ad-hoc scoring logic)."""
    harness = Path("/tmp/phase_12a_10c_repeatability_harness.py")
    if not harness.exists():
        pytest.skip("variance harness not present in /tmp/")
    text = harness.read_text(encoding="utf-8")
    # Imports
    assert "from assembly.calibration.harness_scoring import" in text
    assert "score_run_against_durable_labels" in text
    assert "copy_labels_into_batch_dir" in text
    assert "verify_labels_file_or_raise" in text


def test_variance_harness_preflight_fails_loud_on_missing_labels():
    """The harness must call verify_labels_file_or_raise BEFORE any
    paid run starts so a missing labels file aborts the batch."""
    harness = Path("/tmp/phase_12a_10c_repeatability_harness.py")
    if not harness.exists():
        pytest.skip("variance harness not present in /tmp/")
    text = harness.read_text(encoding="utf-8")
    # The preflight string must appear and come BEFORE the main
    # for-loop launching paid runs.
    preflight_idx = text.find("verify_labels_file_or_raise(")
    loop_idx = text.find("for i in range(args.runs):")
    assert preflight_idx != -1, "no preflight verify call"
    assert loop_idx != -1, "no paid-run loop"
    assert preflight_idx < loop_idx, (
        "preflight verify must run BEFORE the paid-run loop"
    )


def test_variance_harness_post_batch_fails_loud_when_scoring_missing():
    """If --score-vs-labels was requested but any complete run lacks
    observed_pct, the harness must return non-zero so CI / operator
    notices the gap."""
    harness = Path("/tmp/phase_12a_10c_repeatability_harness.py")
    if not harness.exists():
        pytest.skip("variance harness not present in /tmp/")
    text = harness.read_text(encoding="utf-8")
    assert "scoring_failures" in text
    assert "return 2" in text


def test_variance_harness_records_labels_in_runtime_config_batch():
    """The `runtime_config_batch.json` must record the labels_used
    block (path + hash) when scoring was requested."""
    harness = Path("/tmp/phase_12a_10c_repeatability_harness.py")
    if not harness.exists():
        pytest.skip("variance harness not present in /tmp/")
    text = harness.read_text(encoding="utf-8")
    assert '"labels_used":' in text
    assert 'durable_labels.original_path' in text
    assert 'durable_labels.sha256' in text


# ---------------------------------------------------------------------------
# F. Discipline
# ---------------------------------------------------------------------------


def test_harness_scoring_module_has_no_llm_or_network_imports():
    p = (
        API_ROOT / "src" / "assembly" / "calibration"
        / "harness_scoring.py"
    )
    text = p.read_text(encoding="utf-8")
    for needle in (
        "provider.chat(", "provider.structured_output(",
        ".messages.create(", "with_cost_guard(",
        "import anthropic", "from anthropic",
        "import openai", "from openai",
        "import httpx", "import requests", "asyncpg",
    ):
        assert needle not in text, (
            f"harness_scoring.py contains forbidden surface: {needle!r}"
        )


def test_no_new_alembic_migration_in_12e5d():
    versions = API_ROOT / "alembic" / "versions"
    if not versions.exists():
        pytest.skip("alembic/versions not present")
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in ("phase_12e5d", "harness_scoring", "durable_labels"):
            assert needle not in text


def test_no_apps_web_changes_in_phase_12e5d():
    import subprocess
    apps_web = REPO_ROOT / "apps" / "web"
    if not apps_web.exists():
        pytest.skip("apps/web not present")
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "apps/web"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available")
    if (r.stdout or "").strip():
        raise AssertionError(
            f"apps/web touched in Phase 12E.5D:\n{r.stdout}"
        )
