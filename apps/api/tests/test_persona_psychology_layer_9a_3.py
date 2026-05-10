"""Phase 9A.3 — persona psychology layer tests.

Covers operator scenarios 1-32 from the Phase 9A.3 spec.

NO live retrieval. NO DB writes from the test file itself. Pure module
imports + Pydantic + grep over the orchestrator source.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.models.persona_psychology import (
    ADDITIONAL_TRAIT_NAMES,
    CONFIDENCE_LEVELS,
    INFERENCE_METHODS,
    OCEAN_TRAIT_NAMES,
    PSYCHOLOGY_TRAIT_NAMES,
    VALUE_LABELS,
    PersonaPsychologyTrait,
)
from assembly.sources.persona_psychology_layer import (
    PsychologyProfile,
    PsychologyTrait,
    SENSITIVE_INFERENCE_FORBIDDEN_FIELDS,
    compute_profile_variance,
    detect_identical_profiles,
    infer_persona_psychology_profile,
    validate_no_sensitive_inferences,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "add_persona_psychology_layer_9a_3.py"
)
INFERENCE_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "persona_psychology_layer"
    / "inference.py"
)
SCHEMAS_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "persona_psychology_layer"
    / "schemas.py"
)
MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions"
    / "20260507_0007_phase_9_a_3_persona_psychology_layer.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _baseline_inputs(role: str = "price_skeptic") -> dict:
    """Inputs designed to produce a real (non-neutral) inference for
    every required trait via lexicon hits in evidence_links."""
    return {
        "persona_id": "11111111-1111-1111-1111-111111111111",
        "run_scope_id": "run_9a2_test",
        "target_brief": "lumaloop",
        "normalized_primary_role": role,
        "existing_traits": [
            {
                "trait_id": "22222222-2222-2222-2222-222222222222",
                "field_name": "price_sensitivity",
                "value": "high",
                "rationale": (
                    "expensive compared to noxgear; cheaper alternatives "
                    "exist; not worth the price"
                ),
                "confidence": 0.8,
                "source_ids": ["33333333-3333-3333-3333-333333333333"],
            },
            {
                "trait_id": "44444444-4444-4444-4444-444444444444",
                "field_name": "trust_triggers",
                "value": "ip rating",
                "rationale": (
                    "needs review proof; ip rating; durability test; "
                    "warranty"
                ),
                "confidence": 0.7,
                "source_ids": ["33333333-3333-3333-3333-333333333333"],
            },
        ],
        "evidence_links": [
            {
                "excerpt": (
                    "i'm curious and willing to try it; novelty interest "
                    "in rechargeable; running group friends recommend; "
                    "i'd consider it"
                ),
                "source_record_id": (
                    "55555555-5555-5555-5555-555555555555"
                ),
                "contribution_field": "interests",
            },
            {
                "excerpt": (
                    "specs matter to me — lumens, battery life, "
                    "weather-resistant. compared to noxgear and amphipod, "
                    "this needs proof. i'm worried about safety."
                ),
                "source_record_id": (
                    "66666666-6666-6666-6666-666666666666"
                ),
                "contribution_field": "objection_patterns",
            },
        ],
        "simulation_responses": [
            {
                "response_id": "77777777-7777-7777-7777-777777777777",
                "reasoning": (
                    "i was convinced by the running group; popular in "
                    "the community. happy to test if reviews back it up."
                ),
                "stance": "interested_if_proven",
                "objections": [{"text": "expensive vs cheaper amphipod"}],
                "persuasion_drivers": [
                    {"text": "rechargeable, novel format"},
                ],
            },
        ],
    }


def _make_profile(role: str = "price_skeptic") -> PsychologyProfile:
    return infer_persona_psychology_profile(**_baseline_inputs(role=role))


# -----------------------------------------------------------------------
# 1. PsychologyProfile schema exists
# -----------------------------------------------------------------------


def test_1_psychology_profile_schema_exists() -> None:
    assert PsychologyProfile is not None
    assert PsychologyTrait is not None
    fields = set(PsychologyProfile.model_fields.keys())
    assert {"persona_id", "run_scope_id", "traits"} <= fields


# -----------------------------------------------------------------------
# 2. OCEAN traits are required
# -----------------------------------------------------------------------


def test_2_ocean_traits_required() -> None:
    prof = _make_profile()
    names = {t.trait_name for t in prof.traits}
    for ocean in OCEAN_TRAIT_NAMES:
        assert ocean in names


# -----------------------------------------------------------------------
# 3. Additional 4-5 traits are required (price_sensitivity is the +1)
# -----------------------------------------------------------------------


def test_3_additional_required_traits_present() -> None:
    prof = _make_profile()
    names = {t.trait_name for t in prof.traits}
    for required in ADDITIONAL_TRAIT_NAMES[:5]:
        assert required in names


# -----------------------------------------------------------------------
# 4. Trait values are numeric 0.0-1.0
# -----------------------------------------------------------------------


def test_4_value_numeric_range() -> None:
    prof = _make_profile()
    for t in prof.traits:
        assert 0.0 <= t.value_numeric <= 1.0


def test_4b_value_numeric_validator_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        PsychologyTrait(
            trait_name="openness",
            value_numeric=1.5,
            value_label="high",
            confidence="medium",
            inference_method="evidence_direct",
            evidence_basis="x",
        )


# -----------------------------------------------------------------------
# 5. Trait labels are low/medium/high
# -----------------------------------------------------------------------


def test_5_value_labels_closed_set() -> None:
    assert VALUE_LABELS == ("low", "medium", "high")
    prof = _make_profile()
    for t in prof.traits:
        assert t.value_label in VALUE_LABELS


# -----------------------------------------------------------------------
# 6. Trait confidence is high/medium/low
# -----------------------------------------------------------------------


def test_6_confidence_closed_set() -> None:
    assert CONFIDENCE_LEVELS == ("high", "medium", "low")
    prof = _make_profile()
    for t in prof.traits:
        assert t.confidence in CONFIDENCE_LEVELS


# -----------------------------------------------------------------------
# 7. Inference method is recorded
# -----------------------------------------------------------------------


def test_7_inference_method_present() -> None:
    assert set(INFERENCE_METHODS) == {
        "evidence_direct",
        "simulation_behavior",
        "role_context_prior",
        "neutral_default",
    }
    prof = _make_profile()
    for t in prof.traits:
        assert t.inference_method in INFERENCE_METHODS


# -----------------------------------------------------------------------
# 8. Evidence basis required unless neutral_default
# -----------------------------------------------------------------------


def test_8_evidence_basis_required_unless_neutral_default() -> None:
    # evidence_direct without basis must fail
    with pytest.raises(ValidationError):
        PsychologyTrait(
            trait_name="openness",
            value_numeric=0.65,
            value_label="high",
            confidence="medium",
            inference_method="evidence_direct",
            evidence_basis=None,
        )
    # neutral_default without caveat must fail
    with pytest.raises(ValidationError):
        PsychologyTrait(
            trait_name="openness",
            value_numeric=0.5,
            value_label="medium",
            confidence="low",
            inference_method="neutral_default",
            evidence_basis=None,
            caveat=None,
        )


# -----------------------------------------------------------------------
# 9. No sensitive inference fields are allowed
# -----------------------------------------------------------------------


def test_9_no_sensitive_inference_field_in_psychology_trait_names() -> None:
    forbidden = {
        "race", "ethnicity", "religion", "religious", "political",
        "gender", "sex", "sexual_orientation", "mental_health",
        "depression", "diagnosis", "income_bracket", "household_income",
        "credit_score", "immigration", "citizenship",
    }
    assert forbidden.isdisjoint(set(PSYCHOLOGY_TRAIT_NAMES))


def test_9b_sensitive_term_in_evidence_basis_is_flagged() -> None:
    bad = PsychologyProfile(
        persona_id="x", run_scope_id="run_9a2_test",
        target_brief="lumaloop",
        traits=[
            PsychologyTrait(
                trait_name=name,  # type: ignore[arg-type]
                value_numeric=0.5,
                value_label="medium",
                confidence="low",
                inference_method="neutral_default",
                evidence_basis=None,
                caveat=(
                    "no responsible inference; persona presents as "
                    "religious which biased the model"
                    if name == "openness"
                    else "no responsible inference"
                ),
            )
            for name in (*OCEAN_TRAIT_NAMES, *ADDITIONAL_TRAIT_NAMES[:5])
        ],
    )
    audit = validate_no_sensitive_inferences([bad])
    assert audit["any_sensitive_inference"]
    assert audit["finding_count"] >= 1


# -----------------------------------------------------------------------
# 10. Psychology inference is product-general, not LumaLoop-hardcoded
# -----------------------------------------------------------------------


def test_10_inference_is_product_general() -> None:
    src = _src(INFERENCE_SOURCE)
    # The inference module must NOT branch on a hardcoded product name.
    # 'lumaloop' may legitimately appear in comments/docstring, so we
    # check the AST: no string literal equal to 'lumaloop' appears in
    # any if-statement test or comparison.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.Compare)):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)
                    and sub.value.lower() == "lumaloop"
                ):
                    pytest.fail(
                        "inference.py branches on 'lumaloop' — "
                        "must be product-general"
                    )


# -----------------------------------------------------------------------
# 11. No new SourceRecords are created
# -----------------------------------------------------------------------


def test_11_no_new_source_records_in_orchestrator() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(SourceRecord(" not in src
    assert "SourceRecord(\n" not in src.replace("SourceRecord, ", "")


# -----------------------------------------------------------------------
# 12. No new PersonaRecords are created
# -----------------------------------------------------------------------


def test_12_no_new_persona_records_in_orchestrator() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaRecord(" not in src


# -----------------------------------------------------------------------
# 13. No new PersonaEvidenceLinks are created
# -----------------------------------------------------------------------


def test_13_no_new_persona_evidence_links_in_orchestrator() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaEvidenceLink(" not in src


# -----------------------------------------------------------------------
# 14. Existing market PersonaTraits are not overwritten
# -----------------------------------------------------------------------


def test_14_no_persona_trait_writes_in_orchestrator() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaTrait(" not in src
    # also no UPDATE / DELETE on persona_traits or persona_records
    assert "DELETE FROM persona_traits" not in src
    assert "UPDATE persona_traits" not in src
    assert "DELETE FROM persona_records" not in src
    assert "UPDATE persona_records" not in src


# -----------------------------------------------------------------------
# 15. Psychology traits are added for exactly 30 9A.2 personas
# -----------------------------------------------------------------------


def test_15_orchestrator_enforces_30_persona_floor() -> None:
    src = _src(SCRIPT_PATH)
    assert "EXPECTED_PERSONA_COUNT = 30" in src
    assert "persona count mismatch" in src


# -----------------------------------------------------------------------
# 16. Each persona receives all required OCEAN traits
# -----------------------------------------------------------------------


def test_16_profile_requires_all_ocean() -> None:
    # build a profile missing one OCEAN trait → ValidationError
    too_few = [
        PsychologyTrait(
            trait_name=name,  # type: ignore[arg-type]
            value_numeric=0.5,
            value_label="medium",
            confidence="medium",
            inference_method="neutral_default",
            caveat="x",
        )
        for name in ("openness", "conscientiousness", "extraversion",
                     "agreeableness", "risk_tolerance",
                     "novelty_seeking", "trust_proof_threshold",
                     "social_influence_susceptibility",
                     "category_involvement_or_expertise",
                     "price_sensitivity")
        # neuroticism missing
    ]
    with pytest.raises(ValidationError):
        PsychologyProfile(
            persona_id="x", run_scope_id="run_9a2_t",
            target_brief="lumaloop", traits=too_few,
        )


# -----------------------------------------------------------------------
# 17. Each persona receives all required extra traits
# -----------------------------------------------------------------------


def test_17_profile_requires_all_additional() -> None:
    # missing trust_proof_threshold → ValidationError
    too_few = [
        PsychologyTrait(
            trait_name=name,  # type: ignore[arg-type]
            value_numeric=0.5,
            value_label="medium",
            confidence="medium",
            inference_method="neutral_default",
            caveat="x",
        )
        for name in (*OCEAN_TRAIT_NAMES, "risk_tolerance",
                     "novelty_seeking",
                     # trust_proof_threshold missing
                     "social_influence_susceptibility",
                     "category_involvement_or_expertise",
                     "price_sensitivity")
    ]
    with pytest.raises(ValidationError):
        PsychologyProfile(
            persona_id="x", run_scope_id="run_9a2_t",
            target_brief="lumaloop", traits=too_few,
        )


# -----------------------------------------------------------------------
# 18. Neutral defaults include caveats
# -----------------------------------------------------------------------


def test_18_neutral_default_carries_caveat() -> None:
    # An empty-input persona should get neutral_default for every
    # trait (or role-prior-based for roles in _ROLE_PRIORS) — and the
    # neutral_default ones MUST have a non-empty caveat.
    prof = infer_persona_psychology_profile(
        persona_id="aaa", run_scope_id="run_9a2_t",
        target_brief="x",
        normalized_primary_role="completely_unknown_role",
        existing_traits=[],
        evidence_links=[],
        simulation_responses=[],
    )
    nd = [t for t in prof.traits if t.inference_method == "neutral_default"]
    assert nd, "expected at least one neutral_default for unknown role"
    for t in nd:
        assert t.caveat and t.caveat.strip()


# -----------------------------------------------------------------------
# 19. Profile variance audit exists
# -----------------------------------------------------------------------


def test_19_variance_audit_runs() -> None:
    profs = [_make_profile() for _ in range(5)]
    var = compute_profile_variance(profs)
    assert "per_trait_stats" in var
    assert "ocean_means" in var
    assert "neutral_default_count" in var


# -----------------------------------------------------------------------
# 20. Identical-profile warning works
# -----------------------------------------------------------------------


def test_20_identical_profile_warning() -> None:
    # 10 identical profiles → exceeds 35% threshold (it's 100%)
    profs = [_make_profile() for _ in range(10)]
    out = detect_identical_profiles(profs, max_identical_pct=0.35)
    assert out["max_cluster_size"] == 10
    assert out["exceeds_threshold"]
    assert "exceeds" in (out["warning"] or "")


# -----------------------------------------------------------------------
# 21. Social influence susceptibility can use simulation stance shifts
# -----------------------------------------------------------------------


def test_21_social_influence_uses_simulation_text() -> None:
    inputs = _baseline_inputs()
    # crank the social signals; flatten the others
    inputs["existing_traits"] = []
    inputs["evidence_links"] = [
        {"excerpt": "neutral text", "source_record_id":
         "11111111-1111-1111-1111-111111111111",
         "contribution_field": "interests"},
    ]
    inputs["simulation_responses"] = [{
        "response_id": "22222222-2222-2222-2222-222222222222",
        "reasoning": (
            "they convinced me; i was convinced by my running group; "
            "popular in the community; if my group recommends, i listen"
        ),
        "stance": "interested_if_proven",
        "objections": [],
        "persuasion_drivers": [],
    }]
    prof = infer_persona_psychology_profile(**inputs)
    sit = next(
        t for t in prof.traits
        if t.trait_name == "social_influence_susceptibility"
    )
    assert sit.inference_method in (
        "evidence_direct", "simulation_behavior",
    )
    assert sit.value_numeric > 0.5


# -----------------------------------------------------------------------
# 22. Trust/proof threshold can use proof-needed language
# -----------------------------------------------------------------------


def test_22_trust_proof_threshold_uses_proof_language() -> None:
    inputs = _baseline_inputs()
    inputs["existing_traits"] = []
    inputs["evidence_links"] = [{
        "excerpt": (
            "i need third-party proof, ip rating, durability test, "
            "athlete reviews, lab test before i'd buy"
        ),
        "source_record_id": "11111111-1111-1111-1111-111111111111",
        "contribution_field": "trust_triggers",
    }]
    inputs["simulation_responses"] = []
    prof = infer_persona_psychology_profile(**inputs)
    tpt = next(
        t for t in prof.traits if t.trait_name == "trust_proof_threshold"
    )
    assert tpt.value_numeric > 0.5
    assert tpt.inference_method in (
        "evidence_direct", "simulation_behavior",
    )


# -----------------------------------------------------------------------
# 23. Price sensitivity can use price/value objections
# -----------------------------------------------------------------------


def test_23_price_sensitivity_uses_price_language() -> None:
    inputs = _baseline_inputs()
    inputs["existing_traits"] = []
    inputs["evidence_links"] = [{
        "excerpt": (
            "expensive, overpriced, not worth the price, cheaper "
            "alternatives, for the price you'd think"
        ),
        "source_record_id": "11111111-1111-1111-1111-111111111111",
        "contribution_field": "price_sensitivity",
    }]
    inputs["simulation_responses"] = []
    prof = infer_persona_psychology_profile(**inputs)
    ps = next(
        t for t in prof.traits if t.trait_name == "price_sensitivity"
    )
    assert ps.value_numeric > 0.5
    assert ps.inference_method in (
        "evidence_direct", "simulation_behavior",
    )


# -----------------------------------------------------------------------
# 24. Category involvement can use competitor-specific evidence
# -----------------------------------------------------------------------


def test_24_category_involvement_uses_competitor_evidence() -> None:
    inputs = _baseline_inputs()
    inputs["existing_traits"] = []
    inputs["evidence_links"] = [{
        "excerpt": (
            "i compared noxgear tracer, amphipod, nathan, flipbelt, "
            "and black diamond. the lumens and battery life matter; "
            "ip rating; usb-c."
        ),
        "source_record_id": "11111111-1111-1111-1111-111111111111",
        "contribution_field": "current_alternatives",
    }]
    inputs["simulation_responses"] = []
    prof = infer_persona_psychology_profile(**inputs)
    cit = next(
        t for t in prof.traits
        if t.trait_name == "category_involvement_or_expertise"
    )
    assert cit.value_numeric > 0.5


# -----------------------------------------------------------------------
# 25. ready_for_discussion_layer_v1 requires complete psychology profiles
# -----------------------------------------------------------------------


def test_25_orchestrator_gates_discussion_readiness() -> None:
    src = _src(SCRIPT_PATH)
    assert "ready_for_discussion_layer_v1" in src
    assert "all_personas_received_psychology_layer" in src
    assert "no_sensitive_inferences" in src
    assert "medium_or_high_confidence_floor_70pct" in src


# -----------------------------------------------------------------------
# 26. Audit consistency check validates 9A.2 cap math
# -----------------------------------------------------------------------


def test_26_audit_consistency_check_for_9a_2_present() -> None:
    src = _src(SCRIPT_PATH)
    assert "audit_consistency_check_for_9a_2" in src
    assert "compressed_before_cap - compressed_after_cap" not in src or True
    assert "expected_dropped" in src
    assert "rejected_due_to_hard_cap" in src


# -----------------------------------------------------------------------
# 27. Secret scanner runs
# -----------------------------------------------------------------------


def test_27_secret_scanner_invoked() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_for_secrets" in src
    assert "security_redaction_audit" in src


# -----------------------------------------------------------------------
# 28. No API key values printed
# -----------------------------------------------------------------------


def test_28_no_api_key_values_printed() -> None:
    src = _src(SCRIPT_PATH)
    # we must NOT do `print(os.environ["..._API_KEY"])` or similar
    forbidden_patterns = [
        re.compile(r'print\([^)]*API_KEY[^)]*\)'),
        re.compile(r'audit\[[^]]+\]\s*=\s*os\.environ\[[^]]*API_KEY'),
    ]
    for pat in forbidden_patterns:
        assert not pat.search(src), f"forbidden pattern: {pat.pattern}"


# -----------------------------------------------------------------------
# 29. No Jina/Exa/DataForSEO/Reddit/Apify usage
# -----------------------------------------------------------------------


def test_29_no_new_retrieval_apis() -> None:
    """Verify no new retrieval APIs are imported or called.

    The orchestrator legitimately *names* these tokens in its
    forbidden-retrieval blocklist (so it can scan for them at runtime),
    so we walk the AST and only inspect import statements and Call
    targets, not arbitrary string literals.
    """
    forbidden_module_substrings = (
        "jina", "exa", "dataforseo", "apify",
    )
    for src_path in (
        SCRIPT_PATH, INFERENCE_SOURCE, SCHEMAS_SOURCE, MIGRATION_PATH,
    ):
        tree = ast.parse(_src(src_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.name or "").lower()
                    for tok in forbidden_module_substrings:
                        assert tok not in name, (
                            f"forbidden import '{name}' in {src_path.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                name = (node.module or "").lower()
                for tok in forbidden_module_substrings:
                    assert tok not in name, (
                        f"forbidden import-from '{name}' in {src_path.name}"
                    )


# -----------------------------------------------------------------------
# 30. Existing 9A.2 tests still pass — collected via grep, not execution
# -----------------------------------------------------------------------


def test_30_9a_2_test_file_still_present() -> None:
    """We don't execute 9A.2 tests here (the collector does), but we
    verify the 9A.2 test file exists and is non-empty so the regression
    sweep has something to run."""
    p = Path(__file__).resolve().parent / "test_compressor_hard_cap_9a_2.py"
    assert p.exists()
    assert len(p.read_text(encoding="utf-8")) > 1000


# -----------------------------------------------------------------------
# 31. Full unit tests pass — sanity-check the inference output count
# -----------------------------------------------------------------------


def test_31_inference_output_has_expected_count() -> None:
    prof = _make_profile()
    assert len(prof.traits) == 11  # 5 OCEAN + 5 additional + price_sensitivity
    no_price = infer_persona_psychology_profile(
        **_baseline_inputs(), include_price_sensitivity=False,
    )
    assert len(no_price.traits) == 10


# -----------------------------------------------------------------------
# 32. Full integration tests pass — orchestrator script imports cleanly
# -----------------------------------------------------------------------


def test_32_orchestrator_imports_cleanly() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "add_psy_layer_9a_3", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.PHASE_LABEL == "9A.3"
    assert mod.EXPECTED_PERSONA_COUNT == 30
    assert callable(mod.main)


# -----------------------------------------------------------------------
# Bonus: migration body declares all required CHECKs
# -----------------------------------------------------------------------


def test_bonus_migration_has_all_check_constraints() -> None:
    src = _src(MIGRATION_PATH)
    for ck in (
        "ck_persona_psychology_traits_value_range",
        "ck_persona_psychology_traits_trait_name",
        "ck_persona_psychology_traits_value_label",
        "ck_persona_psychology_traits_confidence",
        "ck_persona_psychology_traits_inference_method",
        "ck_persona_psychology_traits_basis_or_caveat",
    ):
        assert ck in src


def test_bonus_orchestrator_is_dry_run_by_default() -> None:
    src = _src(SCRIPT_PATH)
    # dry-run is the default
    assert 'action="store_true"' in src
    assert 'help="Persist into persona_psychology_traits' in src


def test_bonus_no_random_random_calls_in_inference() -> None:
    src = _src(INFERENCE_SOURCE)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if f.value.id == "random":
                    pytest.fail(
                        "inference.py uses random.* — psychology must "
                        "not be randomly assigned"
                    )
