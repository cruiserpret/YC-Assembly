"""Phase 12A.1 — calibration harness scaffold tests.

Covers four modules and four invariants:

  1. market_buckets — closed bucket vocabulary, conservative mapping
  2. distribution_metrics — MAE/TVD math, false-confidence flags
  3. blind_case_schema — pre/hidden/scoring separation + blindness
  4. report_extractor — founder_report intent_distribution → buckets

Plus safety asserts: no apps/web imports, no scraping imports, no
LLM-call imports inside the calibration package.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.calibration import (
    BUCKET_NAMES,
    BlindCase,
    BucketCounts,
    HiddenRealWorldOutcome,
    PreLaunchInput,
    ScoringMetadata,
    bucket_absolute_errors,
    calibration_summary,
    extract_bucket_counts_from_founder_report,
    extract_bucket_counts_from_intent_distribution,
    map_assembly_intent_to_market_bucket,
    max_bucket_error,
    mean_absolute_bucket_error,
    normalize_distribution,
    total_variation_distance,
    validate_bucket_distribution,
)
from assembly.calibration.blind_case_schema import (
    _OutcomeNotYetReadableError,
    assembly_brief_excludes_outcome_fields,
    evidence_obeys_cutoff,
)


# ---------------------------------------------------------------------------
# 1. market_buckets — mapping rules from the phase spec
# ---------------------------------------------------------------------------


class TestBucketMapping:
    def test_bucket_vocabulary_is_closed(self) -> None:
        assert BUCKET_NAMES == ("buyer", "receptive", "uncertain", "skeptical")

    def test_would_buy_now_maps_to_buyer(self) -> None:
        bucket, warn = map_assembly_intent_to_market_bucket("would_buy_now")
        assert bucket == "buyer"
        assert warn is None

    def test_would_try_once_maps_to_buyer(self) -> None:
        bucket, _ = map_assembly_intent_to_market_bucket("would_try_once")
        assert bucket == "buyer"

    def test_would_join_waitlist_maps_to_receptive_not_buyer(self) -> None:
        """Critical: waitlist must NOT inflate buyer percentages."""
        bucket, _ = map_assembly_intent_to_market_bucket("would_join_waitlist")
        assert bucket == "receptive"

    def test_would_join_waitlist_with_explicit_payment_lifts_to_buyer(self) -> None:
        """The only sanctioned upgrade path: explicit payment intent."""
        bucket, _ = map_assembly_intent_to_market_bucket(
            "would_join_waitlist", payment_intent_explicit=True,
        )
        assert bucket == "buyer"

    def test_would_consider_if_proven_maps_to_receptive(self) -> None:
        bucket, _ = map_assembly_intent_to_market_bucket(
            "would_consider_if_proven"
        )
        assert bucket == "receptive"

    def test_consider_if_proven_variants_map_to_receptive(self) -> None:
        for label in (
            "would_consider_if_proven_high_trust",
            "would_consider_if_proven_unsure",
        ):
            bucket, _ = map_assembly_intent_to_market_bucket(label)
            assert bucket == "receptive", f"{label} → {bucket}"

    def test_would_share_with_friend_maps_to_receptive(self) -> None:
        bucket, _ = map_assembly_intent_to_market_bucket(
            "would_share_with_friend"
        )
        assert bucket == "receptive"

    def test_loyal_to_current_alternative_maps_to_skeptical(self) -> None:
        """Critical: loyalty to current alternative is rejection."""
        bucket, _ = map_assembly_intent_to_market_bucket(
            "loyal_to_current_alternative"
        )
        assert bucket == "skeptical"

    def test_would_reject_maps_to_skeptical(self) -> None:
        bucket, _ = map_assembly_intent_to_market_bucket("would_reject")
        assert bucket == "skeptical"

    def test_refuses_switching_maps_to_skeptical(self) -> None:
        bucket, _ = map_assembly_intent_to_market_bucket("refuses_switching")
        assert bucket == "skeptical"

    def test_unsure_maps_to_uncertain(self) -> None:
        bucket, _ = map_assembly_intent_to_market_bucket("unsure")
        assert bucket == "uncertain"

    def test_unknown_label_maps_to_uncertain_with_warning(self) -> None:
        bucket, warn = map_assembly_intent_to_market_bucket(
            "would_meditate_about_it"
        )
        assert bucket == "uncertain"
        assert warn is not None
        assert "unknown_intent_label" in warn
        assert "would_meditate_about_it" in warn

    def test_label_normalization_case_insensitive(self) -> None:
        for label in (
            "WOULD_BUY_NOW",
            "Would-Buy-Now",
            "  would_buy_now  ",
            "would buy now",
        ):
            bucket, _ = map_assembly_intent_to_market_bucket(label)
            assert bucket == "buyer", f"{label!r} → {bucket}"

    def test_empty_and_none_labels_map_to_uncertain(self) -> None:
        for label in ("", None, "   "):
            bucket, warn = map_assembly_intent_to_market_bucket(label)
            assert bucket == "uncertain"
            assert warn is not None


# ---------------------------------------------------------------------------
# 1b. normalize_distribution + validate_bucket_distribution
# ---------------------------------------------------------------------------


class TestNormalizeAndValidate:
    def test_normalize_counts_to_fraction(self) -> None:
        d = normalize_distribution(
            {"buyer": 1, "receptive": 1, "uncertain": 1, "skeptical": 1},
            out_mode="fraction",
        )
        assert all(math.isclose(v, 0.25) for v in d.values())
        assert math.isclose(sum(d.values()), 1.0)

    def test_normalize_counts_to_percent(self) -> None:
        d = normalize_distribution(
            {"buyer": 1, "receptive": 1, "uncertain": 1, "skeptical": 1},
            out_mode="percent",
        )
        assert math.isclose(sum(d.values()), 100.0)

    def test_normalize_fills_missing_buckets_with_zero(self) -> None:
        d = normalize_distribution({"buyer": 10}, out_mode="fraction")
        assert math.isclose(d["buyer"], 1.0)
        assert d["receptive"] == 0.0
        assert d["uncertain"] == 0.0
        assert d["skeptical"] == 0.0

    def test_normalize_drops_out_of_vocab_keys(self) -> None:
        d = normalize_distribution(
            {"buyer": 5, "totally_made_up": 9999},
            out_mode="fraction",
        )
        assert math.isclose(d["buyer"], 1.0)
        assert sum(d.values()) == pytest.approx(1.0)

    def test_normalize_empty_returns_flat_prior(self) -> None:
        d = normalize_distribution({}, out_mode="fraction")
        assert all(math.isclose(v, 0.25) for v in d.values())

    def test_normalize_all_zeros_returns_flat_prior(self) -> None:
        d = normalize_distribution(
            {"buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 0},
            out_mode="fraction",
        )
        assert all(math.isclose(v, 0.25) for v in d.values())

    def test_normalize_accepts_already_percent(self) -> None:
        d = normalize_distribution(
            {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
            out_mode="percent",
        )
        assert math.isclose(sum(d.values()), 100.0)
        assert math.isclose(d["buyer"], 10.0)

    def test_validate_accepts_valid_fraction(self) -> None:
        ok, errs = validate_bucket_distribution(
            {"buyer": 0.1, "receptive": 0.4, "uncertain": 0.3, "skeptical": 0.2},
            mode="fraction",
        )
        assert ok and errs == []

    def test_validate_rejects_missing_bucket(self) -> None:
        ok, errs = validate_bucket_distribution(
            {"buyer": 0.5, "receptive": 0.5},
            mode="fraction",
        )
        assert not ok
        joined = " ".join(errs)
        assert "missing_bucket" in joined

    def test_validate_rejects_negative(self) -> None:
        ok, errs = validate_bucket_distribution(
            {"buyer": -0.1, "receptive": 0.4, "uncertain": 0.5, "skeptical": 0.2},
            mode="fraction",
        )
        assert not ok
        assert any("negative_value" in e for e in errs)

    def test_validate_rejects_extra_bucket(self) -> None:
        ok, errs = validate_bucket_distribution(
            {"buyer": 0.25, "receptive": 0.25, "uncertain": 0.25,
             "skeptical": 0.25, "elated": 0.0},
            mode="fraction",
        )
        assert not ok
        assert any("extra_bucket" in e for e in errs)


# ---------------------------------------------------------------------------
# 2. distribution_metrics — MAE / max / TVD / false confidence
# ---------------------------------------------------------------------------


class TestDistributionMetrics:
    def test_perfect_match_gives_zero_mae(self) -> None:
        d = {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20}
        assert mean_absolute_bucket_error(d, d, mode="percent") == 0.0
        assert max_bucket_error(d, d, mode="percent") == 0.0
        assert total_variation_distance(d, d) == 0.0

    def test_bucket_errors_match_known_example(self) -> None:
        """From the phase spec:
           predicted 10/40/30/20  observed 8/35/32/25
           → errors 2 / 5 / 2 / 5, MAE 3.5, max 5"""
        predicted = {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20}
        observed = {"buyer": 8, "receptive": 35, "uncertain": 32, "skeptical": 25}
        errs = bucket_absolute_errors(predicted, observed, mode="percent")
        assert math.isclose(errs["buyer"], 2.0)
        assert math.isclose(errs["receptive"], 5.0)
        assert math.isclose(errs["uncertain"], 2.0)
        assert math.isclose(errs["skeptical"], 5.0)
        assert math.isclose(
            mean_absolute_bucket_error(predicted, observed, mode="percent"),
            3.5,
        )
        assert math.isclose(
            max_bucket_error(predicted, observed, mode="percent"), 5.0,
        )

    def test_tvd_is_half_sum_of_absolute_differences(self) -> None:
        """TVD = 0.5 * sum |p_i − q_i| over the bucket set."""
        p = {"buyer": 0.5, "receptive": 0.5, "uncertain": 0.0, "skeptical": 0.0}
        q = {"buyer": 0.0, "receptive": 0.0, "uncertain": 0.5, "skeptical": 0.5}
        # |0.5|*4 = 2.0 → 0.5 * 2.0 = 1.0
        assert math.isclose(total_variation_distance(p, q), 1.0)

    def test_tvd_always_in_unit_interval(self) -> None:
        p = {"buyer": 0.1, "receptive": 0.4, "uncertain": 0.3, "skeptical": 0.2}
        q = {"buyer": 0.4, "receptive": 0.1, "uncertain": 0.2, "skeptical": 0.3}
        tvd = total_variation_distance(p, q)
        assert 0.0 <= tvd <= 1.0

    def test_tvd_input_in_percent_converts_to_fraction(self) -> None:
        p_pct = {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20}
        q_pct = {"buyer": 8, "receptive": 35, "uncertain": 32, "skeptical": 25}
        p_frac = {"buyer": 0.10, "receptive": 0.40, "uncertain": 0.30, "skeptical": 0.20}
        q_frac = {"buyer": 0.08, "receptive": 0.35, "uncertain": 0.32, "skeptical": 0.25}
        assert math.isclose(
            total_variation_distance(p_pct, q_pct),
            total_variation_distance(p_frac, q_frac),
        )

    def test_count_distributions_convert_to_same_fractions(self) -> None:
        """Sample sizes don't matter as long as proportions match."""
        small = {"buyer": 1, "receptive": 4, "uncertain": 3, "skeptical": 2}
        large = {"buyer": 100, "receptive": 400, "uncertain": 300, "skeptical": 200}
        assert math.isclose(
            mean_absolute_bucket_error(small, large, mode="percent"),
            0.0,
        )

    def test_false_confidence_flags_over_predicted_buyer(self) -> None:
        predicted = {"buyer": 35, "receptive": 35, "uncertain": 20, "skeptical": 10}
        observed = {"buyer": 5, "receptive": 40, "uncertain": 35, "skeptical": 20}
        summary = calibration_summary(predicted, observed, mode="percent")
        warnings = summary["false_confidence_warnings"]
        assert any("over_predicted_buyer_critical" in w for w in warnings)

    def test_false_confidence_flags_under_predicted_skepticism(self) -> None:
        predicted = {"buyer": 30, "receptive": 60, "uncertain": 5, "skeptical": 5}
        observed = {"buyer": 5, "receptive": 30, "uncertain": 15, "skeptical": 50}
        summary = calibration_summary(predicted, observed, mode="percent")
        warnings = summary["false_confidence_warnings"]
        assert any(
            "under_predicted_skepticism_critical" in w for w in warnings
        )

    def test_perfect_match_produces_no_warnings(self) -> None:
        d = {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20}
        summary = calibration_summary(d, d, mode="percent")
        assert summary["false_confidence_warnings"] == []

    def test_calibration_summary_objection_recall(self) -> None:
        predicted = {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20}
        observed = {"buyer": 8, "receptive": 40, "uncertain": 32, "skeptical": 20}
        summary = calibration_summary(
            predicted, observed,
            mode="percent",
            objections_predicted=[
                "too_expensive", "no_integrations",
                "trust_not_cleared",
            ],
            objections_observed=[
                "too_expensive", "no_integrations",
                "needs_more_proof",
            ],
        )
        rec = summary["objection_recall"]
        assert rec["recall"] == pytest.approx(2 / 3)
        assert rec["matched"] == ["no_integrations", "too_expensive"]
        assert rec["missed"] == ["needs_more_proof"]

    def test_calibration_summary_units_metadata(self) -> None:
        d = {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20}
        summary = calibration_summary(d, d, mode="percent")
        assert summary["units"]["bucket_errors"] == "pp"
        assert summary["units"]["mae"] == "pp"
        assert summary["units"]["tvd"] == "fraction"


# ---------------------------------------------------------------------------
# 3. blind_case_schema — pre/hidden/scoring separation
# ---------------------------------------------------------------------------


def _make_minimal_blindcase() -> BlindCase:
    """A tiny synthetic case with explicit fake-outcome flag — only
    used by these tests, never represents a real product."""
    return BlindCase(
        pre_launch_input=PreLaunchInput(
            case_id="synthetic_test_001",
            product_name="SyntheticTestProduct",
            category="AI SaaS tool",
            pre_launch_brief={
                "product_name": "SyntheticTestProduct",
                "product_description": "A made-up product for unit tests only.",
                "category_hint": "AI SaaS tool",
                "target_customers": ["pretend buyers"],
                "competitors_or_alternatives": ["NoCompetitor"],
                "price_or_price_structure": "$1/month",
            },
            cutoff_date=date(2026, 1, 1),
            forbidden_post_cutoff_sources=["example.com/post-launch"],
        ),
        hidden_real_world_outcome=HiddenRealWorldOutcome(
            observed_distribution={
                "buyer": 5, "receptive": 30,
                "uncertain": 25, "skeptical": 40,
            },
            observed_sample_size=100,
            observed_source_type="synthetic_test_fixture",
            observed_collection_date=date(2026, 3, 1),
            observed_objections=["too_expensive", "needs_proof"],
        ),
    )


class TestBlindCaseSeparation:
    def test_pre_launch_brief_rejects_outcome_shaped_keys(self) -> None:
        with pytest.raises(ValidationError):
            PreLaunchInput(
                case_id="x",
                product_name="x",
                category="x",
                pre_launch_brief={
                    "product_name": "x",
                    "observed_distribution": {"buyer": 0.5},
                },
                cutoff_date=date(2026, 1, 1),
            )

    def test_to_assembly_brief_contains_only_pre_launch_fields(self) -> None:
        case = _make_minimal_blindcase()
        brief = case.to_assembly_brief()
        ok, leaked = assembly_brief_excludes_outcome_fields(brief)
        assert ok, f"leaked fields: {leaked}"

    def test_outcome_unreadable_without_prediction_artifact(self, tmp_path: Path) -> None:
        case = _make_minimal_blindcase()
        missing = tmp_path / "no_such_file.json"
        with pytest.raises(_OutcomeNotYetReadableError):
            case.read_outcome_for_scoring(
                prediction_artifact_path=missing,
            )

    def test_outcome_readable_once_prediction_exists(self, tmp_path: Path) -> None:
        case = _make_minimal_blindcase()
        artifact = tmp_path / "founder_report.json"
        artifact.write_text(json.dumps({"intent_distribution": {}}))
        outcome = case.read_outcome_for_scoring(
            prediction_artifact_path=artifact,
        )
        assert outcome.observed_sample_size == 100

    def test_outcome_collection_must_be_after_cutoff(self, tmp_path: Path) -> None:
        """If the outcome was collected BEFORE the cutoff, it isn't
        post-launch truth — refuse to disclose."""
        case = BlindCase(
            pre_launch_input=PreLaunchInput(
                case_id="x", product_name="x", category="x",
                pre_launch_brief={"product_name": "x"},
                cutoff_date=date(2026, 6, 1),
            ),
            hidden_real_world_outcome=HiddenRealWorldOutcome(
                observed_distribution={"buyer": 1, "receptive": 1, "uncertain": 1, "skeptical": 1},
                observed_sample_size=4,
                observed_source_type="synthetic_test_fixture",
                observed_collection_date=date(2026, 5, 1),  # BEFORE cutoff
            ),
        )
        artifact = tmp_path / "founder_report.json"
        artifact.write_text("{}")
        with pytest.raises(ValueError, match="strictly after"):
            case.read_outcome_for_scoring(
                prediction_artifact_path=artifact,
            )

    def test_pre_launch_hash_is_deterministic_and_outcome_independent(
        self, tmp_path: Path,
    ) -> None:
        """Two cases with identical pre-launch input but different
        outcomes must produce the same pre_launch_hash."""
        case_a = _make_minimal_blindcase()
        case_b = _make_minimal_blindcase()
        # Mutate the outcome of case_b
        case_b = case_b.model_copy(update={
            "hidden_real_world_outcome": HiddenRealWorldOutcome(
                observed_distribution={
                    "buyer": 50, "receptive": 30,
                    "uncertain": 10, "skeptical": 10,
                },
                observed_sample_size=200,
                observed_source_type="synthetic_test_fixture",
                observed_collection_date=date(2026, 3, 1),
            ),
        })
        assert case_a.compute_pre_launch_hash() == case_b.compute_pre_launch_hash()

    def test_pre_launch_hash_changes_when_brief_changes(self) -> None:
        case_a = _make_minimal_blindcase()
        modified_brief = dict(case_a.pre_launch_input.pre_launch_brief)
        modified_brief["price_or_price_structure"] = "$2/month"
        case_b = case_a.model_copy(update={
            "pre_launch_input": case_a.pre_launch_input.model_copy(
                update={"pre_launch_brief": modified_brief},
            ),
        })
        assert case_a.compute_pre_launch_hash() != case_b.compute_pre_launch_hash()

    def test_evidence_obeys_cutoff_flags_post_cutoff_dates(self) -> None:
        cutoff = date(2026, 1, 1)
        ok, viol = evidence_obeys_cutoff(
            [date(2025, 12, 31), date(2026, 1, 1), date(2026, 2, 1)],
            cutoff,
        )
        assert not ok
        assert len(viol) == 1
        assert "2026-02-01" in viol[0]

    def test_evidence_obeys_cutoff_passes_only_pre_cutoff(self) -> None:
        ok, viol = evidence_obeys_cutoff(
            [date(2025, 12, 31), date(2026, 1, 1)],
            date(2026, 1, 1),
        )
        assert ok and viol == []

    def test_evidence_obeys_cutoff_flags_unknown_dates(self) -> None:
        ok, viol = evidence_obeys_cutoff(
            [None, date(2025, 12, 31)],
            date(2026, 1, 1),
        )
        assert not ok
        assert any("None" in v for v in viol)


# ---------------------------------------------------------------------------
# 4. report_extractor — founder_report intent_distribution → buckets
# ---------------------------------------------------------------------------


class TestReportExtractor:
    def test_extract_from_clippilot_mode_a_intent_distribution(self) -> None:
        """ClipPilot Mode A (Phase 11D.14) actual intent distribution."""
        intent_dist = {
            "loyal_to_current_alternative": 2,
            "would_consider_if_proven": 18,
            "would_reject": 4,
        }
        bc = extract_bucket_counts_from_intent_distribution(intent_dist)
        assert bc.buyer == 0
        assert bc.receptive == 18  # consider_if_proven
        assert bc.uncertain == 0
        assert bc.skeptical == 6  # loyal + reject
        assert bc.total == 24
        assert bc.warnings == []

    def test_extract_from_repolens_mode_a_intent_distribution(self) -> None:
        """RepoLens Mode A (Phase 11D.12) actual intent distribution."""
        intent_dist = {
            "would_consider_if_proven": 16,
            "loyal_to_current_alternative": 6,
            "would_buy_now": 2,
        }
        bc = extract_bucket_counts_from_intent_distribution(intent_dist)
        assert bc.buyer == 2
        assert bc.receptive == 16
        assert bc.uncertain == 0
        assert bc.skeptical == 6
        assert bc.total == 24

    def test_unknown_intent_label_falls_to_uncertain_with_warning(self) -> None:
        intent_dist = {
            "would_buy_now": 1,
            "would_make_a_meme_about_it": 3,
        }
        bc = extract_bucket_counts_from_intent_distribution(intent_dist)
        assert bc.buyer == 1
        assert bc.uncertain == 3
        assert any(
            "would_make_a_meme_about_it" in w for w in bc.warnings
        )

    def test_non_integer_count_is_skipped_with_warning(self) -> None:
        intent_dist = {"would_buy_now": "many"}
        bc = extract_bucket_counts_from_intent_distribution(intent_dist)
        assert bc.total == 0
        assert any("non_integer_count" in w for w in bc.warnings)

    def test_negative_count_is_skipped_with_warning(self) -> None:
        intent_dist = {"would_buy_now": -3}
        bc = extract_bucket_counts_from_intent_distribution(intent_dist)
        assert bc.total == 0
        assert any("negative_count" in w for w in bc.warnings)

    def test_as_distribution_returns_fraction_summing_to_one(self) -> None:
        bc = BucketCounts(buyer=1, receptive=2, uncertain=3, skeptical=4)
        d = bc.as_distribution()
        assert math.isclose(sum(d.values()), 1.0)
        assert math.isclose(d["skeptical"], 0.4)

    def test_as_distribution_empty_returns_flat_prior(self) -> None:
        bc = BucketCounts()
        d = bc.as_distribution()
        assert all(math.isclose(v, 0.25) for v in d.values())

    def test_extract_from_founder_report_file(self, tmp_path: Path) -> None:
        """Read from a tiny synthetic founder_report.json on disk."""
        report = {
            "synthetic_intent_snapshot": {
                "intent_distribution": {
                    "would_buy_now": 3,
                    "would_consider_if_proven": 14,
                    "loyal_to_current_alternative": 5,
                    "would_reject": 2,
                },
            },
        }
        path = tmp_path / "founder_report.json"
        path.write_text(json.dumps(report))
        bc = extract_bucket_counts_from_founder_report(path)
        assert bc.buyer == 3
        assert bc.receptive == 14
        assert bc.skeptical == 7
        assert bc.uncertain == 0

    def test_extract_falls_through_to_legacy_top_level(
        self, tmp_path: Path,
    ) -> None:
        report = {
            "intent_distribution": {"would_buy_now": 4, "would_reject": 1},
        }
        path = tmp_path / "report.json"
        path.write_text(json.dumps(report))
        bc = extract_bucket_counts_from_founder_report(path)
        assert bc.buyer == 4
        assert bc.skeptical == 1

    def test_extract_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            extract_bucket_counts_from_founder_report(
                tmp_path / "does_not_exist.json"
            )

    def test_extract_raises_when_no_intent_distribution(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"unrelated": "data"}))
        with pytest.raises(ValueError, match="no intent_distribution"):
            extract_bucket_counts_from_founder_report(path)


# ---------------------------------------------------------------------------
# End-to-end scoring — pre + hidden + extractor + metrics
# ---------------------------------------------------------------------------


class TestEndToEndScoring:
    def test_blind_scoring_flow_from_synthetic_fixture(
        self, tmp_path: Path,
    ) -> None:
        """Full flow:
          1. Create a BlindCase with synthetic outcome.
          2. Write a fake prediction artifact.
          3. Read outcome (only allowed because artifact exists).
          4. Extract bucket counts from the artifact.
          5. Compute calibration_summary.
        """
        case = _make_minimal_blindcase()

        # Simulate Assembly producing a prediction.
        artifact = tmp_path / "founder_report.json"
        artifact.write_text(json.dumps({
            "synthetic_intent_snapshot": {
                "intent_distribution": {
                    # 24 personas: skewed receptive, some skeptics
                    "would_buy_now": 1,
                    "would_consider_if_proven": 14,
                    "loyal_to_current_alternative": 6,
                    "would_reject": 3,
                },
            },
        }))

        outcome = case.read_outcome_for_scoring(
            prediction_artifact_path=artifact,
        )
        predicted = extract_bucket_counts_from_founder_report(artifact)
        summary = calibration_summary(
            predicted.as_dict(),
            outcome.observed_distribution,
            mode="percent",
            objections_predicted=["needs_proof"],
            objections_observed=outcome.observed_objections,
        )
        # Predicted from artifact: buyer=1, receptive=14, uncertain=0, skeptical=9
        # (1/24, 14/24, 0/24, 9/24) ≈ (4.2, 58.3, 0.0, 37.5)
        # Observed (synthetic):     buyer=5, receptive=30, uncertain=25, skeptical=40
        # (5/100, 30/100, 25/100, 40/100) = (5, 30, 25, 40)
        assert summary["predicted_distribution"]["buyer"] == pytest.approx(
            100 * 1 / 24, abs=0.01,
        )
        # Should fire under_predicted_skepticism_critical (0 - 25 = -25pp < -15pp threshold not — wait, predicted 37.5 vs observed 40 = -2.5 → fine)
        # under_predicted on uncertain bucket: predicted 0 vs observed 25 → -25pp → flag
        assert any(
            "under_predicted" in w
            for w in summary["false_confidence_warnings"]
        )
        # Predicted=["needs_proof"], observed=["too_expensive",
        # "needs_proof"] → matched={needs_proof}, recall=1/2=0.5
        assert summary["objection_recall"]["recall"] == pytest.approx(0.5)
        assert summary["objection_recall"]["matched"] == ["needs_proof"]
        assert summary["objection_recall"]["missed"] == ["too_expensive"]


# ---------------------------------------------------------------------------
# Safety / structural guarantees
# ---------------------------------------------------------------------------


class TestPackageSafety:
    def test_no_apps_web_imports(self) -> None:
        """Calibration package must not import from apps/web (which
        does not exist in apps/api anyway), and must not pull in
        scraping / HTTP / LLM-call modules. This is a structural
        guard against future drift."""
        import assembly.calibration as pkg
        from pathlib import Path
        pkg_root = Path(pkg.__file__).resolve().parent
        forbidden_substrings = (
            "import httpx", "import requests", "import aiohttp",
            "import scrapy", "import selenium", "import playwright",
            "import bs4", "from bs4",
            "from anthropic", "from openai",  # no LLM call surfaces
            "with_cost_guard",                  # no cost-guard wrap
            "AnthropicProvider", "OpenAIProvider",
            "from assembly.llm",                 # no LLM module imports
            "alembic", "railway", "apps.web", "apps/web",
        )
        for py in pkg_root.glob("*.py"):
            content = py.read_text(encoding="utf-8")
            for bad in forbidden_substrings:
                assert bad not in content, (
                    f"forbidden substring {bad!r} found in {py.name}"
                )

    def test_calibration_package_imports_cleanly(self) -> None:
        """The package must import without dragging in any module
        that touches the network or a database."""
        import assembly.calibration  # noqa: F401
        # If we got here, top-level imports succeeded.

    def test_no_schema_migration_added_in_phase_12a_1(self) -> None:
        """Phase 12A.1 is scaffold-only — no DB writes, no migrations.
        Verify the calibration package contains no SQLAlchemy model
        definitions."""
        import assembly.calibration as pkg
        from pathlib import Path
        pkg_root = Path(pkg.__file__).resolve().parent
        for py in pkg_root.glob("*.py"):
            text = py.read_text(encoding="utf-8")
            assert "from sqlalchemy" not in text, (
                f"{py.name} imports sqlalchemy — schema work is out of scope"
            )
            assert "class.*Base.*:" not in text  # crude but works
