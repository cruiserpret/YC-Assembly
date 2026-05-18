"""Phase 12A.5 — Operator candidate metadata template tests.

Covers ``assembly.calibration.operator_candidate_template``:

  - Required + recommended field lists
  - Empty template builder
  - Shape validator (catches missing required + forbidden
    outcome-shaped keys + empty required)
  - Deterministic rendered request packet
  - Round-trip into the Phase 12A.4 intake layer once filled

All fixtures are synthetic. No real product names, no real outcome
data, no URLs. The shape validator's job is to refuse anything that
looks like real outcome data at this phase.
"""
from __future__ import annotations

import pytest

from assembly.calibration import (
    TemplateShapeValidation,
    build_empty_operator_candidate_template,
    candidate_metadata_help_text,
    candidate_metadata_optional_fields,
    candidate_metadata_required_fields,
    render_operator_candidate_request,
    score_operator_candidates,
    validate_candidate_template_shape,
)


# ---------------------------------------------------------------------------
# Field-list accessors
# ---------------------------------------------------------------------------


class TestFieldLists:
    def test_required_fields_match_spec(self) -> None:
        assert candidate_metadata_required_fields() == (
            "candidate_id", "product_name", "category",
        )

    def test_optional_fields_match_spec(self) -> None:
        opt = candidate_metadata_optional_fields()
        for k in (
            "launch_or_cutoff_date",
            "pre_launch_sources_available",
            "outcome_sources_available",
            "estimated_observation_count",
            "contamination_risk",
            "model_prior_risk",
            "outcome_quality",
            "cutoff_clarity",
            "category_fit",
            "source_access_risk",
            "notes",
        ):
            assert k in opt, f"missing optional field: {k}"
        assert len(opt) == 11

    def test_required_and_optional_are_disjoint(self) -> None:
        req = set(candidate_metadata_required_fields())
        opt = set(candidate_metadata_optional_fields())
        assert req.isdisjoint(opt)

    def test_help_text_covers_every_required_and_optional_field(self) -> None:
        help_text = candidate_metadata_help_text()
        for k in candidate_metadata_required_fields():
            assert k in help_text, f"missing help text for required {k}"
            assert "REQUIRED" in help_text[k], (
                f"help text for required field {k} should say REQUIRED"
            )
        for k in candidate_metadata_optional_fields():
            assert k in help_text, f"missing help text for optional {k}"

    def test_help_text_warns_about_vivago_and_semble(self) -> None:
        help_text = candidate_metadata_help_text()
        product_name_help = help_text["product_name"]
        assert "Vivago" in product_name_help
        assert "Semble" in product_name_help
        # contamination_risk help also names them
        contam_help = help_text["contamination_risk"]
        assert "Vivago" in contam_help and "Semble" in contam_help

    def test_help_text_returns_a_copy(self) -> None:
        h1 = candidate_metadata_help_text()
        h1["product_name"] = "MUTATED"
        h2 = candidate_metadata_help_text()
        assert h2["product_name"] != "MUTATED"


# ---------------------------------------------------------------------------
# Empty template builder
# ---------------------------------------------------------------------------


class TestEmptyTemplate:
    def test_empty_template_contains_all_required_fields(self) -> None:
        tpl = build_empty_operator_candidate_template()
        for k in candidate_metadata_required_fields():
            assert k in tpl

    def test_empty_template_contains_all_optional_fields(self) -> None:
        tpl = build_empty_operator_candidate_template()
        for k in candidate_metadata_optional_fields():
            assert k in tpl

    def test_empty_template_does_not_contain_outcome_fields(self) -> None:
        tpl = build_empty_operator_candidate_template()
        for forbidden in (
            "observed_distribution",
            "observed_sample_size",
            "observed_collection_date",
            "observed_objections",
            "hidden_real_world_outcome",
            "ground_truth",
            "actual_buyers",
        ):
            assert forbidden not in tpl, (
                f"empty template leaks outcome field: {forbidden}"
            )

    def test_empty_template_has_no_buyer_count_keys(self) -> None:
        """No buyer / receptive / uncertain / skeptical count fields
        anywhere in the template."""
        tpl = build_empty_operator_candidate_template()
        for k in tpl:
            for bucket in ("buyer", "receptive", "uncertain", "skeptical"):
                assert bucket not in k.lower(), (
                    f"empty template has bucket-named key: {k}"
                )

    def test_empty_template_key_order_is_stable(self) -> None:
        """Required fields first, optional fields after — keys
        appear in a fixed order so JSON exports are byte-stable."""
        tpl = build_empty_operator_candidate_template()
        expected = (
            "candidate_id", "product_name", "category",
            "launch_or_cutoff_date",
            "pre_launch_sources_available",
            "outcome_sources_available",
            "estimated_observation_count",
            "contamination_risk", "model_prior_risk",
            "outcome_quality", "cutoff_clarity",
            "category_fit", "source_access_risk",
            "notes",
        )
        assert tuple(tpl.keys()) == expected

    def test_empty_template_accepts_candidate_id_pre_population(self) -> None:
        tpl = build_empty_operator_candidate_template(
            candidate_id="fake_candidate_X",
        )
        assert tpl["candidate_id"] == "fake_candidate_X"


# ---------------------------------------------------------------------------
# Shape validation
# ---------------------------------------------------------------------------


class TestShapeValidation:
    def test_empty_template_is_invalid_until_required_filled(self) -> None:
        val = validate_candidate_template_shape(
            build_empty_operator_candidate_template(),
        )
        assert not val.is_valid_shape
        assert set(val.empty_required) == {
            "candidate_id", "product_name", "category",
        }

    def test_filled_required_fields_make_valid(self) -> None:
        tpl = build_empty_operator_candidate_template()
        tpl["candidate_id"] = "fake_x"
        tpl["product_name"] = "FakeProduct"
        tpl["category"] = "AI SaaS tool"
        val = validate_candidate_template_shape(tpl)
        assert val.is_valid_shape, (
            f"unexpectedly invalid: empty_required={val.empty_required}, "
            f"missing={val.missing_required}, "
            f"forbidden={val.forbidden_outcome_keys}"
        )

    def test_missing_required_key_detected(self) -> None:
        payload = {
            "product_name": "FakeProduct",
            "category": "AI SaaS tool",
            # candidate_id missing
        }
        val = validate_candidate_template_shape(payload)
        assert not val.is_valid_shape
        assert "candidate_id" in val.missing_required

    def test_empty_string_required_is_invalid(self) -> None:
        """Operator submitted the template but left product_name blank."""
        val = validate_candidate_template_shape({
            "candidate_id": "fake_x",
            "product_name": "   ",  # whitespace only
            "category": "AI SaaS tool",
        })
        assert not val.is_valid_shape
        assert "product_name" in val.empty_required

    def test_forbidden_outcome_key_in_template_rejected(self) -> None:
        """Operator sneakily included an outcome key. Shape validator
        must catch it before it can reach the intake layer."""
        tpl = build_empty_operator_candidate_template()
        tpl["candidate_id"] = "x"
        tpl["product_name"] = "X"
        tpl["category"] = "AI SaaS tool"
        tpl["observed_distribution"] = {"buyer": 0.1}
        val = validate_candidate_template_shape(tpl)
        assert not val.is_valid_shape
        assert "observed_distribution" in val.forbidden_outcome_keys

    def test_multiple_forbidden_outcome_keys_all_listed(self) -> None:
        tpl = build_empty_operator_candidate_template()
        tpl["candidate_id"] = "x"
        tpl["product_name"] = "X"
        tpl["category"] = "AI SaaS tool"
        tpl["observed_sample_size"] = 100
        tpl["ground_truth"] = "leak"
        tpl["actual_buyers"] = 5
        val = validate_candidate_template_shape(tpl)
        assert set(val.forbidden_outcome_keys) >= {
            "observed_sample_size", "ground_truth", "actual_buyers",
        }
        assert not val.is_valid_shape

    def test_unknown_keys_warned_not_invalid(self) -> None:
        """An unknown top-level key is a warning, not a hard fail —
        the intake layer also tolerates this."""
        tpl = build_empty_operator_candidate_template()
        tpl["candidate_id"] = "x"
        tpl["product_name"] = "X"
        tpl["category"] = "AI SaaS tool"
        tpl["random_extra"] = "ignored"
        val = validate_candidate_template_shape(tpl)
        assert val.is_valid_shape
        assert "random_extra" in val.unknown_keys

    def test_non_dict_payload_is_invalid(self) -> None:
        val = validate_candidate_template_shape("not a dict")  # type: ignore[arg-type]
        assert not val.is_valid_shape
        assert set(val.missing_required) == {
            "candidate_id", "product_name", "category",
        }


# ---------------------------------------------------------------------------
# Rendered operator request
# ---------------------------------------------------------------------------


class TestRenderedRequest:
    def test_rendered_request_is_deterministic(self) -> None:
        a = render_operator_candidate_request(num_candidates=2)
        b = render_operator_candidate_request(num_candidates=2)
        assert a == b

    def test_rendered_request_contains_do_dont_rules(self) -> None:
        text = render_operator_candidate_request(num_candidates=2)
        assert "## Do" in text
        assert "## Don't" in text
        assert "Vivago" in text
        assert "Semble" in text

    def test_rendered_request_lists_all_required_fields(self) -> None:
        text = render_operator_candidate_request(num_candidates=2)
        assert "## Required fields" in text
        for k in candidate_metadata_required_fields():
            assert f"`{k}`" in text

    def test_rendered_request_lists_all_recommended_fields(self) -> None:
        text = render_operator_candidate_request(num_candidates=2)
        assert "## Recommended fields" in text
        for k in candidate_metadata_optional_fields():
            assert f"`{k}`" in text

    def test_rendered_request_emits_n_candidate_slots(self) -> None:
        for n in (1, 2, 3):
            text = render_operator_candidate_request(num_candidates=n)
            for i in range(1, n + 1):
                assert f"### Candidate {i}" in text

    def test_rendered_request_warns_against_outcome_keys(self) -> None:
        text = render_operator_candidate_request(num_candidates=2)
        # Footer reminder enumerates forbidden outcome-shaped keys
        for forbidden in (
            "observed_distribution", "observed_sample_size",
            "hidden_real_world_outcome", "ground_truth",
        ):
            assert forbidden in text

    def test_rendered_request_invalid_num_candidates_raises(self) -> None:
        for bad in (0, -1, "two", None):
            with pytest.raises(ValueError):
                render_operator_candidate_request(num_candidates=bad)  # type: ignore[arg-type]

    def test_rendered_request_does_not_request_buyer_counts(self) -> None:
        """The template MUST NOT ask the operator to fill in
        buyer / receptive / uncertain / skeptical counts at this
        phase. Those come in a later authorized phase."""
        text = render_operator_candidate_request(num_candidates=2)
        # Bucket names may appear as part of help text describing
        # outcome_quality, but NEVER as JSON keys in the candidate slot.
        # Verify there's no JSON-key-shape pattern like '"buyer":'
        import re
        for bucket in ("buyer", "receptive", "uncertain", "skeptical"):
            assert not re.search(
                rf'"{bucket}"\s*:', text,
            ), f"rendered template has a JSON key for bucket {bucket!r}"


# ---------------------------------------------------------------------------
# Round-trip into the Phase 12A.4 intake layer
# ---------------------------------------------------------------------------


class TestRoundTripIntoIntake:
    def test_filled_template_is_valid_intake_payload(self) -> None:
        """Once filled, the operator's template dict can be passed
        directly to ``score_operator_candidates`` — no translation
        needed."""
        tpl = build_empty_operator_candidate_template()
        tpl["candidate_id"] = "fake_round_trip"
        tpl["product_name"] = "FakeRoundTripProduct"
        tpl["category"] = "AI SaaS tool"
        tpl["launch_or_cutoff_date"] = "2024-09-15"
        tpl["pre_launch_sources_available"] = ["launch_post_text"]
        tpl["outcome_sources_available"] = ["public_review_text"]
        tpl["estimated_observation_count"] = "100-500"
        tpl["contamination_risk"] = "low"
        tpl["model_prior_risk"] = "low"
        tpl["outcome_quality"] = "medium"
        tpl["cutoff_clarity"] = "clear"
        tpl["category_fit"] = "strong"
        tpl["source_access_risk"] = "open_data"
        scored = score_operator_candidates([tpl])
        assert scored[0].candidate_id == "fake_round_trip"
        # All optional fields supplied → no follow-up questions
        assert scored[0].operator_followup_questions == []

    def test_partial_template_round_trips_with_followup_questions(self) -> None:
        tpl = build_empty_operator_candidate_template()
        tpl["candidate_id"] = "fake_partial"
        tpl["product_name"] = "FakePartialProduct"
        tpl["category"] = "B2B SaaS"
        # Leave everything else as default (mostly None)
        scored = score_operator_candidates([tpl])
        assert scored[0].recommendation == "unverified"
        assert len(scored[0].operator_followup_questions) >= 5

    def test_vivago_template_is_auto_rejected_through_intake(self) -> None:
        """The Vivago/Semble contamination guard still applies when
        the template flows through the intake layer."""
        tpl = build_empty_operator_candidate_template()
        tpl["candidate_id"] = "fake_vivago_template"
        tpl["product_name"] = "VivagoStyleProduct"
        tpl["category"] = "AI SaaS tool"
        tpl["launch_or_cutoff_date"] = "2024-09-15"
        tpl["pre_launch_sources_available"] = ["launch_post_text"]
        tpl["outcome_sources_available"] = ["public_review_text"]
        tpl["estimated_observation_count"] = "100-500"
        tpl["contamination_risk"] = "none"
        tpl["model_prior_risk"] = "low"
        tpl["outcome_quality"] = "strong"
        tpl["cutoff_clarity"] = "clear"
        tpl["category_fit"] = "strong"
        tpl["source_access_risk"] = "open_data"
        scored = score_operator_candidates([tpl])
        assert scored[0].recommendation == "reject"
        assert "contaminated_in_signal_layer" in scored[0].risk_flags


# ---------------------------------------------------------------------------
# Safety / structural guards
# ---------------------------------------------------------------------------


class TestPackageSafety:
    def test_no_network_or_llm_imports_in_phase_12a_5_module(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        py = (
            Path(pkg.__file__).resolve().parent
            / "operator_candidate_template.py"
        )
        content = py.read_text(encoding="utf-8")
        forbidden_substrings = (
            "import httpx", "import requests", "import aiohttp",
            "import scrapy", "import selenium", "import playwright",
            "import bs4", "from bs4",
            "from anthropic", "from openai",
            "with_cost_guard",
            "AnthropicProvider", "OpenAIProvider",
            "from assembly.llm",
        )
        for bad in forbidden_substrings:
            assert bad not in content, (
                f"forbidden substring {bad!r} found in "
                "operator_candidate_template.py"
            )

    def test_no_schema_or_db_imports_in_phase_12a_5_module(self) -> None:
        from pathlib import Path
        import assembly.calibration as pkg
        py = (
            Path(pkg.__file__).resolve().parent
            / "operator_candidate_template.py"
        )
        content = py.read_text(encoding="utf-8")
        assert "from sqlalchemy" not in content
        assert "import sqlalchemy" not in content
        assert "alembic" not in content.lower()
        assert "apps/web" not in content
        assert "apps.web" not in content

    def test_no_real_outcome_data_in_phase_12a_5_module(self) -> None:
        """The template module must not carry any real outcome
        proportions, URLs, or specific real product names beyond
        what appears in the Vivago/Semble warning."""
        from pathlib import Path
        import re
        import assembly.calibration as pkg
        py = (
            Path(pkg.__file__).resolve().parent
            / "operator_candidate_template.py"
        )
        content = py.read_text(encoding="utf-8")
        # No URLs.
        assert "http://" not in content
        assert "https://" not in content
        # No bucket count assignments like 'buyer: 8'.
        for bucket in ("buyer", "receptive", "uncertain", "skeptical"):
            assert not re.search(
                rf"{bucket}\s*[:=]\s*[0-9]", content,
            ), f"template module carries a {bucket} count"

    def test_calibration_package_still_imports_cleanly(self) -> None:
        import assembly.calibration  # noqa: F401
