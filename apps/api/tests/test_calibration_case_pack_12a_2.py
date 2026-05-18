"""Phase 12A.2 — Blinded validation case pack tests.

Covers two new modules:

  - assembly.calibration.case_pack_loader
  - assembly.calibration.case_scoring

Plus end-to-end blindness invariants: hidden outcomes must not be
readable before prediction, post-cutoff sources cannot appear in
Assembly-visible briefs, and the pack-level summary must agree with
the per-case Pydantic guards.

All fixtures are synthetic. ``observed_source_type`` is always set
to ``"synthetic_test_fixture"`` so a reviewer can grep for any
non-synthetic outcome data in the repo and find none.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from assembly.calibration import (
    BlindCase,
    BlindCaseLoadError,
    CasePack,
    CaseScoringResult,
    load_blind_case_from_dict,
    load_blind_case_from_json_path,
    load_case_pack_from_directory,
    score_blind_case_against_prediction,
    score_case_pack,
    summarize_case_pack,
    summarize_case_pack_scores,
    validate_case_pack_blindness,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures — three fake products, never real outcomes
# ---------------------------------------------------------------------------


def _fake_ai_video_tool_payload() -> dict[str, Any]:
    return {
        "pre_launch_input": {
            "case_id": "fake_ai_video_tool",
            "product_name": "FakeClipper",
            "category": "AI SaaS tool",
            "pre_launch_brief": {
                "product_name": "FakeClipper",
                "product_description": (
                    "Synthetic AI video product for unit tests only."
                ),
                "category_hint": "AI SaaS tool",
                "target_customers": ["pretend founders"],
                "competitors_or_alternatives": ["NotRunway"],
                "price_or_price_structure": "$29/month",
            },
            "cutoff_date": "2026-01-01",
            "forbidden_post_cutoff_sources": [
                "fakeleak.example/post-launch",
            ],
        },
        "hidden_real_world_outcome": {
            "observed_distribution": {
                "buyer": 8, "receptive": 35,
                "uncertain": 32, "skeptical": 25,
            },
            "observed_sample_size": 100,
            "observed_source_type": "synthetic_test_fixture",
            "observed_collection_date": "2026-04-01",
            "observed_objections": ["too_expensive", "needs_proof"],
        },
    }


def _fake_devtool_search_payload() -> dict[str, Any]:
    return {
        "pre_launch_input": {
            "case_id": "fake_devtool_search",
            "product_name": "FakeRepoLens",
            "category": "developer tool",
            "pre_launch_brief": {
                "product_name": "FakeRepoLens",
                "product_description": (
                    "Synthetic devtool product for unit tests only."
                ),
                "category_hint": "developer tool",
                "target_customers": ["pretend developers"],
                "competitors_or_alternatives": ["NotSourcegraph"],
                "price_or_price_structure": "Free CLI",
            },
            "cutoff_date": "2026-02-01",
            "forbidden_post_cutoff_sources": [],
        },
        "hidden_real_world_outcome": {
            "observed_distribution": {
                "buyer": 5, "receptive": 30,
                "uncertain": 35, "skeptical": 30,
            },
            "observed_sample_size": 200,
            "observed_source_type": "synthetic_test_fixture",
            "observed_collection_date": "2026-05-01",
            "observed_objections": ["no_team_sync", "needs_benchmarks"],
        },
    }


def _fake_consumer_app_payload() -> dict[str, Any]:
    return {
        "pre_launch_input": {
            "case_id": "fake_consumer_app",
            "product_name": "FakeBudgetPal",
            "category": "consumer mobile app",
            "pre_launch_brief": {
                "product_name": "FakeBudgetPal",
                "product_description": (
                    "Synthetic consumer app for unit tests only."
                ),
                "category_hint": "consumer mobile app",
                "target_customers": ["pretend consumers"],
                "competitors_or_alternatives": ["NotMint"],
                "price_or_price_structure": "Freemium",
            },
            "cutoff_date": "2026-01-15",
            "forbidden_post_cutoff_sources": [],
        },
        "hidden_real_world_outcome": {
            "observed_distribution": {
                "buyer": 2, "receptive": 18,
                "uncertain": 40, "skeptical": 40,
            },
            "observed_sample_size": 500,
            "observed_source_type": "synthetic_test_fixture",
            "observed_collection_date": "2026-04-15",
            "observed_objections": ["another_finance_app"],
        },
    }


def _all_three_payloads() -> list[dict[str, Any]]:
    return [
        _fake_ai_video_tool_payload(),
        _fake_devtool_search_payload(),
        _fake_consumer_app_payload(),
    ]


def _write_payloads_to_directory(
    payloads: list[dict[str, Any]],
    directory: Path,
) -> None:
    for p in payloads:
        cid = p["pre_launch_input"]["case_id"]
        (directory / f"{cid}.json").write_text(json.dumps(p))


def _write_prediction_artifact(
    path: Path,
    intent_distribution: dict[str, int],
) -> None:
    payload = {
        "synthetic_intent_snapshot": {
            "intent_distribution": intent_distribution,
        },
    }
    path.write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# load_blind_case_from_dict
# ---------------------------------------------------------------------------


class TestLoadFromDict:
    def test_valid_dict_loads(self) -> None:
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        assert isinstance(case, BlindCase)
        assert case.pre_launch_input.case_id == "fake_ai_video_tool"

    def test_unknown_top_level_key_rejected(self) -> None:
        payload = _fake_ai_video_tool_payload()
        payload["sneaky_extra_section"] = {"hidden": "data"}
        with pytest.raises(BlindCaseLoadError) as exc_info:
            load_blind_case_from_dict(payload)
        assert any(
            "unknown_top_level_key" in v for v in exc_info.value.violations
        )

    def test_missing_pre_launch_input_rejected(self) -> None:
        payload = _fake_ai_video_tool_payload()
        del payload["pre_launch_input"]
        with pytest.raises(BlindCaseLoadError) as exc_info:
            load_blind_case_from_dict(payload)
        assert any(
            "missing_section" in v and "pre_launch_input" in v
            for v in exc_info.value.violations
        )

    def test_missing_hidden_outcome_rejected(self) -> None:
        payload = _fake_ai_video_tool_payload()
        del payload["hidden_real_world_outcome"]
        with pytest.raises(BlindCaseLoadError) as exc_info:
            load_blind_case_from_dict(payload)
        assert any(
            "missing_section" in v and "hidden_real_world_outcome" in v
            for v in exc_info.value.violations
        )

    def test_outcome_shaped_key_in_brief_rejected(self) -> None:
        """The Pydantic validator on PreLaunchInput rejects keys that
        look like outcome data. The loader surfaces this as a
        BlindCaseLoadError so callers have a single exception type."""
        payload = _fake_ai_video_tool_payload()
        payload["pre_launch_input"]["pre_launch_brief"][
            "observed_signups"
        ] = 1000
        with pytest.raises(BlindCaseLoadError):
            load_blind_case_from_dict(payload)

    def test_observed_date_before_cutoff_constructs_but_fails_loader_check(
        self,
    ) -> None:
        """observed_collection_date BEFORE cutoff is a loader-level
        violation: the case may pass schema construction (Pydantic
        does not cross-check dates), but the loader's blindness
        sweep rejects it before the case enters the pipeline."""
        payload = _fake_ai_video_tool_payload()
        payload["pre_launch_input"]["cutoff_date"] = "2026-12-01"
        # observed_collection_date stays at 2026-04-01 (before cutoff)
        with pytest.raises(BlindCaseLoadError) as exc_info:
            load_blind_case_from_dict(payload)
        assert any(
            "observed_collection_date" in v and "not strictly after" in v
            for v in exc_info.value.violations
        )

    def test_forbidden_source_in_brief_is_detected(self) -> None:
        """If the brief mentions a string from
        forbidden_post_cutoff_sources, that's a known leak."""
        payload = _fake_ai_video_tool_payload()
        payload["pre_launch_input"]["pre_launch_brief"][
            "product_description"
        ] = (
            "FakeClipper, as reviewed on fakeleak.example/post-launch"
        )
        with pytest.raises(BlindCaseLoadError) as exc_info:
            load_blind_case_from_dict(payload)
        assert any(
            "forbidden_source" in v
            for v in exc_info.value.violations
        )

    def test_non_dict_payload_rejected(self) -> None:
        with pytest.raises(BlindCaseLoadError):
            load_blind_case_from_dict("not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# load_blind_case_from_json_path
# ---------------------------------------------------------------------------


class TestLoadFromJsonPath:
    def test_valid_file_loads(self, tmp_path: Path) -> None:
        p = tmp_path / "case.json"
        p.write_text(json.dumps(_fake_ai_video_tool_payload()))
        case = load_blind_case_from_json_path(p)
        assert case.pre_launch_input.case_id == "fake_ai_video_tool"

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(BlindCaseLoadError, match="not found"):
            load_blind_case_from_json_path(tmp_path / "absent.json")

    def test_malformed_json_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{ not valid json")
        with pytest.raises(BlindCaseLoadError, match="malformed"):
            load_blind_case_from_json_path(p)

    def test_directory_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(BlindCaseLoadError, match="not a regular file"):
            load_blind_case_from_json_path(tmp_path)


# ---------------------------------------------------------------------------
# load_case_pack_from_directory
# ---------------------------------------------------------------------------


class TestLoadCasePack:
    def test_valid_directory_loads_all_cases(self, tmp_path: Path) -> None:
        _write_payloads_to_directory(_all_three_payloads(), tmp_path)
        pack = load_case_pack_from_directory(tmp_path)
        assert isinstance(pack, CasePack)
        assert len(pack) == 3
        assert sorted(pack.case_ids()) == [
            "fake_ai_video_tool",
            "fake_consumer_app",
            "fake_devtool_search",
        ]
        # pre_launch_hashes computed for every case
        assert set(pack.pre_launch_hashes.keys()) == set(pack.case_ids())
        assert all(
            isinstance(h, str) and len(h) == 64
            for h in pack.pre_launch_hashes.values()
        )

    def test_missing_directory_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(BlindCaseLoadError, match="not found"):
            load_case_pack_from_directory(tmp_path / "no_such_dir")

    def test_file_path_rejected_as_directory(self, tmp_path: Path) -> None:
        p = tmp_path / "not_a_dir.json"
        p.write_text("{}")
        with pytest.raises(BlindCaseLoadError, match="not a directory"):
            load_case_pack_from_directory(p)

    def test_duplicate_case_id_rejected(self, tmp_path: Path) -> None:
        a = _fake_ai_video_tool_payload()
        b = _fake_ai_video_tool_payload()
        # Two files, same case_id
        (tmp_path / "a.json").write_text(json.dumps(a))
        (tmp_path / "b.json").write_text(json.dumps(b))
        with pytest.raises(BlindCaseLoadError, match="duplicate"):
            load_case_pack_from_directory(tmp_path)

    def test_invalid_member_aborts_pack_load(self, tmp_path: Path) -> None:
        """A single bad file fails the whole pack — no half-loaded packs."""
        valid = _fake_ai_video_tool_payload()
        invalid = _fake_devtool_search_payload()
        # Make the second case violate the cutoff invariant
        invalid["hidden_real_world_outcome"][
            "observed_collection_date"
        ] = "2025-01-01"
        (tmp_path / "valid.json").write_text(json.dumps(valid))
        (tmp_path / "invalid.json").write_text(json.dumps(invalid))
        with pytest.raises(BlindCaseLoadError):
            load_case_pack_from_directory(tmp_path)


# ---------------------------------------------------------------------------
# validate_case_pack_blindness — pack-level audit
# ---------------------------------------------------------------------------


class TestPackBlindness:
    def test_clean_pack_validates(self, tmp_path: Path) -> None:
        _write_payloads_to_directory(_all_three_payloads(), tmp_path)
        pack = load_case_pack_from_directory(tmp_path)
        ok, viols = validate_case_pack_blindness(pack)
        assert ok and viols == []

    def test_pack_blindness_detects_duplicate_hash(self) -> None:
        """Two cases with the SAME pre_launch_input but different
        ``case_id``s would share a pre_launch_hash — that's a copy.
        The pack-level audit flags it."""
        a = _fake_ai_video_tool_payload()
        b = _fake_ai_video_tool_payload()
        b["pre_launch_input"]["case_id"] = "fake_ai_video_tool_copy"
        case_a = load_blind_case_from_dict(a)
        case_b = load_blind_case_from_dict(b)
        pack = CasePack()
        pack.cases = {
            case_a.pre_launch_input.case_id: case_a,
            case_b.pre_launch_input.case_id: case_b,
        }
        pack.pre_launch_hashes = {
            cid: c.compute_pre_launch_hash()
            for cid, c in pack.cases.items()
        }
        ok, viols = validate_case_pack_blindness(pack)
        assert not ok
        assert any("duplicate_pre_launch_hash" in v for v in viols)


# ---------------------------------------------------------------------------
# summarize_case_pack
# ---------------------------------------------------------------------------


class TestPackSummary:
    def test_summary_counts_by_category_and_source(
        self, tmp_path: Path,
    ) -> None:
        _write_payloads_to_directory(_all_three_payloads(), tmp_path)
        pack = load_case_pack_from_directory(tmp_path)
        summary = summarize_case_pack(pack)
        assert summary["case_count"] == 3
        assert summary["by_category"] == {
            "AI SaaS tool": 1,
            "consumer mobile app": 1,
            "developer tool": 1,
        }
        assert summary["by_observed_source_type"] == {
            "synthetic_test_fixture": 3,
        }
        assert set(summary["synthetic_test_fixture_case_ids"]) == set(
            pack.case_ids()
        )
        assert summary["blindness_ok"] is True
        assert summary["observed_sample_size_total"] == 800


# ---------------------------------------------------------------------------
# score_blind_case_against_prediction
# ---------------------------------------------------------------------------


class TestScoreSingleCase:
    def test_scores_a_well_calibrated_case(self, tmp_path: Path) -> None:
        """Predicted distribution from artifact closely matches
        the synthetic observed distribution."""
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        # Observed: buyer=8, receptive=35, uncertain=32, skeptical=25 (sample 100)
        # Build an Assembly artifact whose intent_distribution rounds
        # to roughly that shape over 24 personas.
        artifact = tmp_path / "fr.json"
        _write_prediction_artifact(artifact, {
            "would_buy_now": 2,            # buyer
            "would_consider_if_proven": 8, # receptive
            "wait_and_see": 8,             # uncertain
            "would_reject": 4,             # skeptical
            "loyal_to_current_alternative": 2,  # skeptical
        })
        result = score_blind_case_against_prediction(case, artifact)
        assert result.scoring_status == "scored"
        assert result.case_id == "fake_ai_video_tool"
        assert result.observed_sample_size == 100
        # MAE should be reasonably small (< 15pp on each bucket)
        assert result.mean_absolute_bucket_error_pp is not None
        assert result.mean_absolute_bucket_error_pp < 15.0
        # Blindness hash present and deterministic
        assert len(result.blindness_hash) == 64

    def test_missing_prediction_returns_structured_status(
        self, tmp_path: Path,
    ) -> None:
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        result = score_blind_case_against_prediction(
            case, tmp_path / "no_such_artifact.json",
        )
        assert result.scoring_status == "missing_prediction"
        assert result.error_message is not None
        # No metric fields populated
        assert result.mean_absolute_bucket_error_pp is None

    def test_blindness_violation_when_cutoff_invariant_fails(
        self, tmp_path: Path,
    ) -> None:
        """Even if the artifact exists, an unenforceable post-launch
        date triggers a blindness_violation status — note the
        loader normally catches this earlier, but a mutated case
        instance reaching the scorer must still be refused."""
        # Construct the case via load (which would pass), then
        # mutate the model post-load to simulate a downstream bug.
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        bad_case = case.model_copy(update={
            "pre_launch_input": case.pre_launch_input.model_copy(
                update={"cutoff_date": date(2026, 12, 1)},
            ),
        })
        artifact = tmp_path / "fr.json"
        _write_prediction_artifact(artifact, {"would_buy_now": 1})
        result = score_blind_case_against_prediction(bad_case, artifact)
        assert result.scoring_status == "blindness_violation"
        assert "strictly after" in (result.error_message or "")

    def test_unknown_label_in_artifact_records_warning(
        self, tmp_path: Path,
    ) -> None:
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        artifact = tmp_path / "fr.json"
        _write_prediction_artifact(artifact, {
            "would_buy_now": 1,
            "would_meditate_about_it": 5,  # unknown — falls to uncertain
        })
        result = score_blind_case_against_prediction(case, artifact)
        assert result.scoring_status == "scored"
        assert any(
            "would_meditate_about_it" in w
            for w in result.extractor_warnings
        )

    def test_objection_recall_computed_when_predictions_passed(
        self, tmp_path: Path,
    ) -> None:
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        artifact = tmp_path / "fr.json"
        _write_prediction_artifact(artifact, {
            "would_buy_now": 1, "would_consider_if_proven": 10,
            "would_reject": 5, "loyal_to_current_alternative": 2,
        })
        result = score_blind_case_against_prediction(
            case, artifact,
            objections_predicted=["too_expensive", "no_brand_fit"],
        )
        rec = result.objection_recall
        assert rec is not None
        # Observed objections: ["too_expensive", "needs_proof"]
        assert rec["recall"] == pytest.approx(0.5)
        assert rec["matched"] == ["too_expensive"]
        assert rec["missed"] == ["needs_proof"]

    def test_blindness_hash_is_outcome_independent(
        self, tmp_path: Path,
    ) -> None:
        """Two scoring runs over the same case but different outcomes
        produce the same blindness_hash."""
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        case_b = case.model_copy(update={
            "hidden_real_world_outcome": (
                case.hidden_real_world_outcome.model_copy(
                    update={"observed_sample_size": 99},
                )
            ),
        })
        artifact = tmp_path / "fr.json"
        _write_prediction_artifact(artifact, {"would_buy_now": 1})
        r1 = score_blind_case_against_prediction(case, artifact)
        r2 = score_blind_case_against_prediction(case_b, artifact)
        assert r1.blindness_hash == r2.blindness_hash


# ---------------------------------------------------------------------------
# score_case_pack + summarize_case_pack_scores
# ---------------------------------------------------------------------------


class TestScorePack:
    def test_scores_multiple_cases(self, tmp_path: Path) -> None:
        case_dir = tmp_path / "cases"
        case_dir.mkdir()
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        _write_payloads_to_directory(_all_three_payloads(), case_dir)
        pack = load_case_pack_from_directory(case_dir)
        # Build a fake artifact per case
        artifact_paths: dict[str, Path] = {}
        for cid in pack.case_ids():
            p = artifacts_dir / f"{cid}.json"
            _write_prediction_artifact(p, {
                "would_buy_now": 1,
                "would_consider_if_proven": 12,
                "wait_and_see": 5,
                "would_reject": 4,
                "loyal_to_current_alternative": 2,
            })
            artifact_paths[cid] = p
        results = score_case_pack(pack, artifact_paths)
        assert len(results) == 3
        for r in results:
            assert r.scoring_status == "scored"
            assert r.mean_absolute_bucket_error_pp is not None

    def test_missing_artifact_for_one_case_does_not_break_others(
        self, tmp_path: Path,
    ) -> None:
        case_dir = tmp_path / "cases"
        case_dir.mkdir()
        _write_payloads_to_directory(
            [_fake_ai_video_tool_payload(), _fake_devtool_search_payload()],
            case_dir,
        )
        pack = load_case_pack_from_directory(case_dir)
        artifact = tmp_path / "ai_only.json"
        _write_prediction_artifact(artifact, {"would_buy_now": 5})
        results = score_case_pack(
            pack,
            {"fake_ai_video_tool": artifact},  # devtool missing
        )
        statuses = {r.case_id: r.scoring_status for r in results}
        assert statuses["fake_ai_video_tool"] == "scored"
        assert statuses["fake_devtool_search"] == "missing_prediction"

    def test_pack_summary_aggregates_correctly(
        self, tmp_path: Path,
    ) -> None:
        """summarize_case_pack_scores reports averages and the worst
        per-case MAE."""
        case_dir = tmp_path / "cases"
        case_dir.mkdir()
        _write_payloads_to_directory(_all_three_payloads(), case_dir)
        pack = load_case_pack_from_directory(case_dir)
        # Deliberately mismatched artifact for one case to ensure
        # `worst_case_id` is computed deterministically.
        artifacts: dict[str, Path] = {}
        for cid in pack.case_ids():
            p = tmp_path / f"{cid}.json"
            if cid == "fake_consumer_app":
                # Heavily skewed buyer prediction → high MAE
                _write_prediction_artifact(p, {"would_buy_now": 20})
            else:
                _write_prediction_artifact(p, {
                    "would_consider_if_proven": 12,
                    "wait_and_see": 8,
                    "would_reject": 4,
                })
            artifacts[cid] = p
        results = score_case_pack(pack, artifacts)
        summary = summarize_case_pack_scores(results)
        assert summary["case_count"] == 3
        assert summary["scored_count"] == 3
        assert summary["missing_prediction_count"] == 0
        assert summary["average_mae_pp"] is not None
        assert summary["worst_case_id"] == "fake_consumer_app"
        # The consumer app prediction (100% buyer) → buyer over-prediction
        # critical warning should appear in cases_with_critical_warnings
        assert any(
            cid == "fake_consumer_app"
            for cid, _ in summary["cases_with_critical_warnings"]
        )

    def test_pack_summary_when_no_predictions(self, tmp_path: Path) -> None:
        case_dir = tmp_path / "cases"
        case_dir.mkdir()
        _write_payloads_to_directory(_all_three_payloads(), case_dir)
        pack = load_case_pack_from_directory(case_dir)
        results = score_case_pack(pack, {})  # no artifacts
        summary = summarize_case_pack_scores(results)
        assert summary["missing_prediction_count"] == 3
        assert summary["scored_count"] == 0
        assert summary["average_mae_pp"] is None
        assert summary["worst_case_id"] is None


# ---------------------------------------------------------------------------
# Blindness across the full pack pipeline
# ---------------------------------------------------------------------------


class TestPipelineBlindness:
    def test_hidden_outcome_unreadable_before_prediction_artifact(
        self, tmp_path: Path,
    ) -> None:
        """Even after a case is loaded, you cannot get the outcome
        without an existing prediction artifact on disk."""
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        missing = tmp_path / "no_artifact.json"
        result = score_blind_case_against_prediction(case, missing)
        assert result.scoring_status == "missing_prediction"
        # And — defensively — no observed proportions surface
        assert result.observed_distribution == {}

    def test_brief_extracted_for_assembly_excludes_outcome(
        self,
    ) -> None:
        """The single sanctioned input extractor returns only the
        pre-launch brief, never outcome fields."""
        case = load_blind_case_from_dict(_fake_ai_video_tool_payload())
        brief = case.to_assembly_brief()
        for k in brief:
            assert not any(
                s in k.lower() for s in (
                    "observed_", "post_launch", "real_world_",
                    "outcome_", "ground_truth",
                )
            ), f"leaked outcome-shaped field in brief: {k}"

    def test_post_cutoff_source_inside_brief_is_blocked(self) -> None:
        payload = _fake_devtool_search_payload()
        payload["pre_launch_input"]["forbidden_post_cutoff_sources"] = [
            "ycombinator.example/2030/launch",
        ]
        payload["pre_launch_input"]["pre_launch_brief"][
            "optional_context"
        ] = "Coverage on ycombinator.example/2030/launch is great."
        with pytest.raises(BlindCaseLoadError) as exc_info:
            load_blind_case_from_dict(payload)
        assert any(
            "forbidden_source" in v for v in exc_info.value.violations
        )


# ---------------------------------------------------------------------------
# Safety / structural guards
# ---------------------------------------------------------------------------


class TestPackageSafety:
    def test_phase_12a_2_modules_have_no_forbidden_imports(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        pkg_root = Path(pkg.__file__).resolve().parent
        forbidden_substrings = (
            "import httpx", "import requests", "import aiohttp",
            "import scrapy", "import selenium", "import playwright",
            "import bs4", "from bs4",
            "from anthropic", "from openai",
            "with_cost_guard",
            "AnthropicProvider", "OpenAIProvider",
            "from assembly.llm",
            "alembic", "railway", "apps.web", "apps/web",
        )
        new_files = (
            "case_pack_loader.py",
            "case_scoring.py",
        )
        for name in new_files:
            py = pkg_root / name
            content = py.read_text(encoding="utf-8")
            for bad in forbidden_substrings:
                assert bad not in content, (
                    f"forbidden substring {bad!r} found in {name}"
                )

    def test_phase_12a_2_does_not_introduce_schema(self) -> None:
        """No SQLAlchemy/Alembic imports in new modules."""
        from pathlib import Path
        import assembly.calibration as pkg
        pkg_root = Path(pkg.__file__).resolve().parent
        for name in ("case_pack_loader.py", "case_scoring.py"):
            content = (pkg_root / name).read_text(encoding="utf-8")
            assert "from sqlalchemy" not in content
            assert "import sqlalchemy" not in content
            assert "alembic" not in content.lower()
