"""Phase 12A.9 — Blind outcome scoring tests.

Covers ``assembly.calibration.outcome_labeling``:

  - Parse + validate operator-labeled JSON / CSV files
  - Compute observed distribution (noise excluded)
  - sha256 prediction-artifact hashing
  - End-to-end score_blind_outcome against a synthetic prediction
  - Tamper-detection (hash before/after equal)
  - Audit-artifact emission

All fixtures are synthetic. No real Opslane HN comments. No real
prediction artifact. The Opslane artifact is mentioned only by hash
for one anchoring test that the real prediction's hash is recorded.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from assembly.calibration import (
    BlindScoringResult,
    LabeledOutcomeFile,
    LabeledOutcomeRow,
    ObservedDistribution,
    OutcomeLabelingError,
    compute_observed_distribution,
    parse_labeled_outcome_file,
    score_blind_outcome,
    sha256_of_prediction_artifact,
    write_phase_12a_9_audit,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _synthetic_outcome_payload() -> dict[str, Any]:
    """A tiny 10-row labeled outcome file. Known counts:
       buyer=2, receptive=4, uncertain=2, skeptical=1, noise=1
       observed_sample_size = 9 (after dropping noise)
       percent: 22.22 / 44.44 / 22.22 / 11.11"""
    return {
        "observed_collection_date": "2024-09-01",
        "observed_objections": ["too_expensive", "needs_proof"],
        "labeler_notes_summary": (
            "Synthetic 10-row fixture for unit tests only. "
            "No real HN data."
        ),
        "rows": [
            {"comment_id": "c1", "label": "buyer",
             "excerpt": "I'll install this tonight."},
            {"comment_id": "c2", "label": "buyer",
             "excerpt": "Adding to my stack now."},
            {"comment_id": "c3", "label": "receptive",
             "excerpt": "Looks promising; would consider once docs land.",
             "objection_tags": ["docs_thin"]},
            {"comment_id": "c4", "label": "receptive",
             "excerpt": "Interesting approach. How does X work?"},
            {"comment_id": "c5", "label": "receptive",
             "excerpt": "Curious about pricing.",
             "objection_tags": ["pricing_unclear"]},
            {"comment_id": "c6", "label": "receptive",
             "excerpt": "Will try in a side project."},
            {"comment_id": "c7", "label": "uncertain",
             "excerpt": "Not sure if this fits us."},
            {"comment_id": "c8", "label": "uncertain",
             "excerpt": "Wait-and-see."},
            {"comment_id": "c9", "label": "skeptical",
             "excerpt": "We already use Tool X for this.",
             "objection_tags": ["already_solved"]},
            {"comment_id": "c10", "label": "noise",
             "excerpt": "Congrats on the launch!"},
        ],
    }


def _write_synthetic_outcome_file(
    payload: dict[str, Any],
    path: Path,
) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_synthetic_prediction_artifact(
    path: Path,
    intent_distribution: dict[str, int],
) -> None:
    """Tiny founder_report.json shape sufficient for the
    extract_bucket_counts_from_founder_report extractor."""
    payload = {
        "synthetic_intent_snapshot": {
            "intent_distribution": intent_distribution,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Parse + validate
# ---------------------------------------------------------------------------


class TestParseLabeledOutcomeFile:
    def test_valid_json_loads(self, tmp_path: Path) -> None:
        p = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), p)
        out = parse_labeled_outcome_file(p, cutoff_date=date(2024, 7, 28))
        assert isinstance(out, LabeledOutcomeFile)
        assert len(out.rows) == 10
        assert out.observed_collection_date == date(2024, 9, 1)
        assert out.cutoff_date == date(2024, 7, 28)
        assert out.observed_objections == ["too_expensive", "needs_proof"]

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(OutcomeLabelingError, match="not_found"):
            parse_labeled_outcome_file(
                tmp_path / "missing.json", cutoff_date=date(2024, 7, 28),
            )

    def test_malformed_json_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(OutcomeLabelingError, match="malformed_json"):
            parse_labeled_outcome_file(
                p, cutoff_date=date(2024, 7, 28),
            )

    def test_invalid_label_rejected(self, tmp_path: Path) -> None:
        payload = _synthetic_outcome_payload()
        payload["rows"][0]["label"] = "extremely_buyer_yes"
        p = tmp_path / "bad.json"
        _write_synthetic_outcome_file(payload, p)
        with pytest.raises(OutcomeLabelingError) as exc_info:
            parse_labeled_outcome_file(
                p, cutoff_date=date(2024, 7, 28),
            )
        assert any(
            "invalid_label" in v for v in exc_info.value.violations
        )

    def test_label_aliases_accepted(self, tmp_path: Path) -> None:
        payload = _synthetic_outcome_payload()
        payload["rows"][0]["label"] = "BUY"             # alias of buyer
        payload["rows"][1]["label"] = "Reject"          # alias of skeptical
        payload["rows"][2]["label"] = "wait_and_see"    # alias of uncertain
        p = tmp_path / "aliases.json"
        _write_synthetic_outcome_file(payload, p)
        out = parse_labeled_outcome_file(
            p, cutoff_date=date(2024, 7, 28),
        )
        labels = [r.label for r in out.rows]
        assert labels[0] == "buyer"
        assert labels[1] == "skeptical"
        assert labels[2] == "uncertain"

    def test_observed_date_must_be_strictly_after_cutoff(
        self, tmp_path: Path,
    ) -> None:
        payload = _synthetic_outcome_payload()
        payload["observed_collection_date"] = "2024-07-28"  # == cutoff
        p = tmp_path / "leak.json"
        _write_synthetic_outcome_file(payload, p)
        with pytest.raises(OutcomeLabelingError, match="not strictly after"):
            parse_labeled_outcome_file(
                p, cutoff_date=date(2024, 7, 28),
            )

    def test_duplicate_comment_id_rejected(self, tmp_path: Path) -> None:
        payload = _synthetic_outcome_payload()
        payload["rows"].append({
            "comment_id": "c1", "label": "uncertain",
        })
        p = tmp_path / "dup.json"
        _write_synthetic_outcome_file(payload, p)
        with pytest.raises(OutcomeLabelingError) as exc_info:
            parse_labeled_outcome_file(
                p, cutoff_date=date(2024, 7, 28),
            )
        assert any(
            "duplicate_comment_id" in v for v in exc_info.value.violations
        )

    def test_empty_rows_rejected(self, tmp_path: Path) -> None:
        payload = {
            "observed_collection_date": "2024-09-01",
            "rows": [],
        }
        p = tmp_path / "empty.json"
        _write_synthetic_outcome_file(payload, p)
        with pytest.raises(OutcomeLabelingError, match="no_rows"):
            parse_labeled_outcome_file(
                p, cutoff_date=date(2024, 7, 28),
            )

    def test_unsupported_extension_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "labels.txt"
        p.write_text("anything", encoding="utf-8")
        with pytest.raises(
            OutcomeLabelingError, match="unsupported_outcome_file_extension",
        ):
            parse_labeled_outcome_file(
                p, cutoff_date=date(2024, 7, 28),
            )

    def test_valid_csv_loads(self, tmp_path: Path) -> None:
        csv_text = (
            "# observed_collection_date=2024-09-01\n"
            "comment_id,label,excerpt,objection_tags,labeler_notes\n"
            "c1,buyer,Going to install tonight,,\n"
            "c2,receptive,Looks promising,docs_thin,\n"
            "c3,uncertain,Wait and see,,\n"
            "c4,skeptical,We use PagerDuty already,already_solved,\n"
            "c5,noise,Congrats!,,\n"
        )
        p = tmp_path / "labels.csv"
        p.write_text(csv_text, encoding="utf-8")
        out = parse_labeled_outcome_file(
            p, cutoff_date=date(2024, 7, 28),
        )
        assert len(out.rows) == 5
        assert out.observed_collection_date == date(2024, 9, 1)


# ---------------------------------------------------------------------------
# 2. Observed distribution math
# ---------------------------------------------------------------------------


class TestObservedDistribution:
    def test_synthetic_fixture_gives_known_counts(
        self, tmp_path: Path,
    ) -> None:
        p = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), p)
        out = parse_labeled_outcome_file(
            p, cutoff_date=date(2024, 7, 28),
        )
        obs = compute_observed_distribution(out)
        assert obs.buyer == 2
        assert obs.receptive == 4
        assert obs.uncertain == 2
        assert obs.skeptical == 1
        assert obs.noise == 1
        assert obs.observed_sample_size == 9

    def test_observed_percent_sums_to_one_hundred(
        self, tmp_path: Path,
    ) -> None:
        p = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), p)
        out = parse_labeled_outcome_file(
            p, cutoff_date=date(2024, 7, 28),
        )
        obs = compute_observed_distribution(out)
        pct = obs.as_percent()
        assert pytest.approx(sum(pct.values()), abs=1e-6) == 100.0
        # buyer = 2/9 = 22.22, receptive = 4/9 = 44.44 etc.
        assert pct["buyer"] == pytest.approx(2 / 9 * 100, abs=1e-6)
        assert pct["receptive"] == pytest.approx(4 / 9 * 100, abs=1e-6)

    def test_noise_excluded_from_sample_size(self, tmp_path: Path) -> None:
        payload = _synthetic_outcome_payload()
        # Add 5 more noise rows
        for i in range(5):
            payload["rows"].append({
                "comment_id": f"n{i}", "label": "noise",
            })
        p = tmp_path / "labels.json"
        _write_synthetic_outcome_file(payload, p)
        out = parse_labeled_outcome_file(
            p, cutoff_date=date(2024, 7, 28),
        )
        obs = compute_observed_distribution(out)
        assert obs.noise == 6
        assert obs.observed_sample_size == 9   # unchanged

    def test_all_noise_distribution_is_zero(self) -> None:
        out = LabeledOutcomeFile(
            rows=[
                LabeledOutcomeRow(comment_id="c1", label="noise"),
                LabeledOutcomeRow(comment_id="c2", label="noise"),
            ],
            observed_collection_date=date(2024, 9, 1),
            cutoff_date=date(2024, 7, 28),
        )
        obs = compute_observed_distribution(out)
        assert obs.observed_sample_size == 0
        # as_percent returns all zeros (not flat prior — caller decides)
        for v in obs.as_percent().values():
            assert v == 0.0


# ---------------------------------------------------------------------------
# 3. sha256 prediction artifact hashing
# ---------------------------------------------------------------------------


class TestArtifactHash:
    def test_hash_is_deterministic(self, tmp_path: Path) -> None:
        p = tmp_path / "founder_report.json"
        p.write_text('{"x": 1}', encoding="utf-8")
        h1 = sha256_of_prediction_artifact(p)
        h2 = sha256_of_prediction_artifact(p)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_hash_changes_when_artifact_mutates(
        self, tmp_path: Path,
    ) -> None:
        p = tmp_path / "founder_report.json"
        p.write_text('{"x": 1}', encoding="utf-8")
        h1 = sha256_of_prediction_artifact(p)
        p.write_text('{"x": 2}', encoding="utf-8")
        h2 = sha256_of_prediction_artifact(p)
        assert h1 != h2

    def test_hash_missing_artifact_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            sha256_of_prediction_artifact(tmp_path / "no_such_file.json")

    def test_real_opslane_prediction_hash_is_recorded(self) -> None:
        """Anchoring test: the locked Opslane prediction artifact
        should be present on disk and produce the hash captured at
        Phase 12A.9 setup. This is the artifact Phase 12A.9 will
        score against — any drift here means the prediction was
        mutated outside the protocol."""
        opslane_artifact = Path(
            "/Users/hamza40/Desktop/Aseembly/assembly-v0/"
            "apps/api/_audit/live_runs/"
            "f8aff6fc-a75f-43ef-8cf2-f3ec09e023d9/founder_report.json"
        )
        if not opslane_artifact.exists():
            pytest.skip("Opslane prediction artifact not present here")
        h = sha256_of_prediction_artifact(opslane_artifact)
        assert h == (
            "efb60159ddc7c9a11bfdcc157789f427"
            "012434e5af87265c35630216b80cc095"
        ), (
            f"Opslane prediction artifact hash drifted to {h!r}. "
            "Either the artifact was mutated outside the blinded "
            "protocol, or the locked hash needs to be updated "
            "(which itself should be an explicit, audited action)."
        )


# ---------------------------------------------------------------------------
# 4. score_blind_outcome end-to-end
# ---------------------------------------------------------------------------


class TestScoreBlindOutcome:
    def test_perfect_calibration_gives_zero_mae(
        self, tmp_path: Path,
    ) -> None:
        """Build a prediction whose intent_distribution maps to the
        same buckets as the synthetic observed file (2/4/2/1), and
        verify MAE == 0."""
        artifact = tmp_path / "founder_report.json"
        _write_synthetic_prediction_artifact(artifact, {
            "would_buy_now": 2,                  # buyer  : 2
            "would_consider_if_proven": 4,       # recept : 4
            "wait_and_see": 2,                   # uncert : 2
            "loyal_to_current_alternative": 1,   # skept  : 1
        })
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        result = score_blind_outcome(
            candidate_id="synthetic_perfect_match",
            prediction_artifact_path=artifact,
            labeled_outcome=parsed,
        )
        assert result.mean_absolute_bucket_error_pp == pytest.approx(
            0.0, abs=1e-6,
        )
        assert result.max_bucket_error_pp == pytest.approx(0.0, abs=1e-6)
        assert result.total_variation_distance == pytest.approx(
            0.0, abs=1e-6,
        )
        assert result.interpretation_band == "strict_success"
        assert result.prediction_artifact_hash_unchanged is True
        assert result.observed_sample_size == 9
        assert result.noise_dropped_count == 1

    def test_known_mismatch_produces_known_errors(
        self, tmp_path: Path,
    ) -> None:
        """Predicted = (0/24, 15/24, 0/24, 9/24) = (0, 62.5, 0, 37.5)
        — the actual Opslane prediction shape.
        Observed = synthetic 2/4/2/1 → (22.22, 44.44, 22.22, 11.11)
        Bucket abs errors (pp):
          buyer:    22.22, receptive: 18.06,
          uncertain: 22.22, skeptical: 26.39
        MAE = (22.22+18.06+22.22+26.39)/4 = 22.22 pp
        max = 26.39 pp
        TVD = (22.22+18.06+22.22+26.39)/200 ≈ 0.4444
        """
        artifact = tmp_path / "founder_report.json"
        _write_synthetic_prediction_artifact(artifact, {
            "would_consider_if_proven": 15,
            "loyal_to_current_alternative": 7,
            "would_reject": 2,
        })
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        result = score_blind_outcome(
            candidate_id="synthetic_opslane_shape_vs_known_obs",
            prediction_artifact_path=artifact,
            labeled_outcome=parsed,
        )
        # Predicted percents
        assert result.predicted_distribution_percent["buyer"] == pytest.approx(0.0)
        assert result.predicted_distribution_percent["receptive"] == pytest.approx(62.5)
        assert result.predicted_distribution_percent["uncertain"] == pytest.approx(0.0)
        assert result.predicted_distribution_percent["skeptical"] == pytest.approx(37.5)
        # Observed percents
        assert result.observed_distribution_percent["buyer"] == pytest.approx(2/9 * 100)
        # Bucket abs errors (within rounding)
        assert result.absolute_bucket_errors_pp["buyer"] == pytest.approx(
            22.222, abs=0.01,
        )
        assert result.absolute_bucket_errors_pp["receptive"] == pytest.approx(
            18.055, abs=0.01,
        )
        assert result.absolute_bucket_errors_pp["uncertain"] == pytest.approx(
            22.222, abs=0.01,
        )
        assert result.absolute_bucket_errors_pp["skeptical"] == pytest.approx(
            26.388, abs=0.01,
        )
        # MAE
        assert result.mean_absolute_bucket_error_pp == pytest.approx(
            22.222, abs=0.01,
        )
        # interpretation band: MAE > 12 + max_err > 25 → problem
        assert result.interpretation_band == "problem_fix_before_next_case"

    def test_missing_prediction_artifact_refuses(
        self, tmp_path: Path,
    ) -> None:
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        with pytest.raises(
            OutcomeLabelingError, match="prediction_artifact_missing",
        ):
            score_blind_outcome(
                candidate_id="synthetic_no_artifact",
                prediction_artifact_path=tmp_path / "no_such_file.json",
                labeled_outcome=parsed,
            )

    def test_all_noise_labels_refuse_to_score(self, tmp_path: Path) -> None:
        artifact = tmp_path / "founder_report.json"
        _write_synthetic_prediction_artifact(artifact, {
            "would_consider_if_proven": 1,
        })
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file({
            "observed_collection_date": "2024-09-01",
            "rows": [
                {"comment_id": "c1", "label": "noise"},
                {"comment_id": "c2", "label": "noise"},
            ],
        }, labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        with pytest.raises(
            OutcomeLabelingError,
            match="observed_sample_size_is_zero",
        ):
            score_blind_outcome(
                candidate_id="synthetic_all_noise",
                prediction_artifact_path=artifact,
                labeled_outcome=parsed,
            )

    def test_hash_unchanged_after_scoring(self, tmp_path: Path) -> None:
        artifact = tmp_path / "founder_report.json"
        _write_synthetic_prediction_artifact(artifact, {
            "would_consider_if_proven": 5,
            "would_buy_now": 1,
        })
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        h_before = sha256_of_prediction_artifact(artifact)
        result = score_blind_outcome(
            candidate_id="synthetic_hash_check",
            prediction_artifact_path=artifact,
            labeled_outcome=parsed,
        )
        h_after = sha256_of_prediction_artifact(artifact)
        assert h_before == h_after
        assert result.prediction_artifact_hash_before == h_before
        assert result.prediction_artifact_hash_after == h_after
        assert result.prediction_artifact_hash_unchanged is True

    def test_objection_recall_computed_when_supplied(
        self, tmp_path: Path,
    ) -> None:
        artifact = tmp_path / "founder_report.json"
        _write_synthetic_prediction_artifact(artifact, {
            "would_consider_if_proven": 5, "would_buy_now": 1,
            "would_reject": 3,
        })
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        result = score_blind_outcome(
            candidate_id="synthetic_with_objections",
            prediction_artifact_path=artifact,
            labeled_outcome=parsed,
        )
        # Synthetic file declares observed_objections=["too_expensive",
        # "needs_proof"]. Scoring did not pass objections_predicted,
        # so recall is computed against an empty predicted list:
        # recall = 0/2 = 0.0 with both observed objections in `missed`.
        assert result.objection_recall is not None
        assert result.objection_recall["observed_count"] == 2
        assert result.objection_recall["predicted_count"] == 0
        assert result.objection_recall["recall"] == 0.0
        assert sorted(result.objection_recall["missed"]) == [
            "needs_proof", "too_expensive",
        ]

    def test_objection_recall_with_caller_supplied_predicted(
        self, tmp_path: Path,
    ) -> None:
        """When the caller supplies predicted objections, recall
        is computed against the intersection."""
        artifact = tmp_path / "founder_report.json"
        _write_synthetic_prediction_artifact(artifact, {
            "would_consider_if_proven": 5, "would_buy_now": 1,
            "would_reject": 3,
        })
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        result = score_blind_outcome(
            candidate_id="synthetic_with_pred_objections",
            prediction_artifact_path=artifact,
            labeled_outcome=parsed,
            objections_predicted=["too_expensive", "unrelated_objection"],
        )
        # observed = [too_expensive, needs_proof]; predicted has 1 hit
        assert result.objection_recall is not None
        assert result.objection_recall["recall"] == pytest.approx(0.5)
        assert result.objection_recall["matched"] == ["too_expensive"]
        assert result.objection_recall["missed"] == ["needs_proof"]


# ---------------------------------------------------------------------------
# 5. Audit-artifact emission
# ---------------------------------------------------------------------------


class TestAuditEmission:
    def test_writes_json_and_markdown(self, tmp_path: Path) -> None:
        artifact = tmp_path / "founder_report.json"
        _write_synthetic_prediction_artifact(artifact, {
            "would_buy_now": 2,
            "would_consider_if_proven": 4,
            "wait_and_see": 2,
            "loyal_to_current_alternative": 1,
        })
        labels = tmp_path / "labels.json"
        _write_synthetic_outcome_file(_synthetic_outcome_payload(), labels)
        parsed = parse_labeled_outcome_file(
            labels, cutoff_date=date(2024, 7, 28),
        )
        result = score_blind_outcome(
            candidate_id="synthetic_audit",
            prediction_artifact_path=artifact,
            labeled_outcome=parsed,
        )
        json_path = tmp_path / "audit.json"
        md_path = tmp_path / "audit.md"
        write_phase_12a_9_audit(
            result, json_path=json_path, md_path=md_path,
        )
        assert json_path.exists() and md_path.exists()
        # JSON parses; MD contains key headings
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["phase"] == "12a_9_blind_outcome_scoring"
        assert data["interpretation_band"] == "strict_success"
        md_text = md_path.read_text(encoding="utf-8")
        assert "Phase 12A.9" in md_text
        assert "Predicted vs Observed" in md_text
        assert "Headline metrics" in md_text


# ---------------------------------------------------------------------------
# 6. Safety / structural guards
# ---------------------------------------------------------------------------


class TestPackageSafety:
    def test_no_network_or_llm_imports_in_phase_12a_9_module(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        py = Path(pkg.__file__).resolve().parent / "outcome_labeling.py"
        content = py.read_text(encoding="utf-8")
        forbidden_substrings = (
            "import httpx", "import requests", "import aiohttp",
            "import scrapy", "import selenium", "import playwright",
            "import bs4", "from bs4",
            "from anthropic", "from openai",
            "with_cost_guard",
            "AnthropicProvider", "OpenAIProvider",
            "from assembly.llm",
            "run_live_founder_brief_pipeline",
            "run_live_discussion",
        )
        for bad in forbidden_substrings:
            assert bad not in content, (
                f"forbidden substring {bad!r} found in "
                "outcome_labeling.py"
            )

    def test_no_schema_or_db_imports_in_phase_12a_9_module(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        py = Path(pkg.__file__).resolve().parent / "outcome_labeling.py"
        content = py.read_text(encoding="utf-8")
        assert "from sqlalchemy" not in content
        assert "import sqlalchemy" not in content
        assert "alembic" not in content.lower()

    def test_no_apps_web_references(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        py = Path(pkg.__file__).resolve().parent / "outcome_labeling.py"
        content = py.read_text(encoding="utf-8")
        assert "apps/web" not in content
        assert "apps.web" not in content

    def test_calibration_package_still_imports_cleanly(self) -> None:
        import assembly.calibration  # noqa: F401
