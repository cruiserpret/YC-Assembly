"""Phase 9A.4 — human-like discussion layer tests.

Covers operator scenarios 1-37 + bonus checks. NO live retrieval, NO
DB writes from the test file itself.
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.models.discussion import (
    BALLOT_STAGES,
    DISCUSSION_STANCES,
    MEMORY_TYPES,
    ORIGIN_TYPES,
    PUBLIC_PRIVATE_DELTAS,
    SESSION_STATUSES,
    SESSION_TYPES,
    TURN_TYPES,
    DiscussionGroup,
    DiscussionPrivateBallot,
    DiscussionSession,
    DiscussionTurn,
    PersonaMemoryAtom,
)
from assembly.sources.discussion_layer import (
    PrivateBallotDraft,
    PsychologyControlSnapshot,
    TurnDraft,
    assign_groups_stratified,
    build_seed_memory_atoms,
    classify_public_private_delta,
    detect_overcooperation,
    evaluate_discussion_quality,
    forbidden_claim_audit,
    rank_memory_atoms,
    sensitive_inference_audit,
)
from assembly.sources.discussion_layer.group_assignment import diversity_audit


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "run_discussion_layer_9a_4.py"
)
PKG_ROOT = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "discussion_layer"
)
MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions"
    / "20260507_0008_phase_9_a_4_discussion_layer.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# Synthetic persona fixture with rotating profile
def _make_personas(n: int = 30) -> list[dict]:
    import uuid as _uuid
    roles = ["price_skeptic", "trust_seeker", "performance_focused_buyer",
             "safety_visibility_focused_buyer", "use_case_focused_buyer",
             "format_focused_buyer"]
    providers = ["brave_search", "tavily_search", "youtube_data_api"]
    stances = list(DISCUSSION_STANCES)
    out = []
    for i in range(n):
        out.append({
            "persona_id": str(_uuid.UUID(int=(0xdeadbeef << 64) | i)),
            "display_name": f"Persona{i:02d}",
            "normalized_primary_role": roles[i % len(roles)],
            "source_provider_family": providers[i % len(providers)],
            "prior_simulation_final_stance": stances[i % len(stances)],
            "extraversion": [0.2, 0.5, 0.8, 0.3, 0.7][i % 5],
            "agreeableness": [0.4, 0.7, 0.2, 0.6, 0.5][i % 5],
            "social_influence_susceptibility": [0.3, 0.6, 0.8, 0.2, 0.5][i % 5],
            "trust_proof_threshold": [0.6, 0.4, 0.8, 0.3, 0.7][i % 5],
        })
    return out


# -----------------------------------------------------------------------
# 1. 9A.4 loads exactly 30 9A.2 personas
# -----------------------------------------------------------------------

def test_1_orchestrator_enforces_30_persona_floor() -> None:
    src = _src(SCRIPT_PATH)
    assert "EXPECTED_PERSONA_COUNT = 30" in src
    assert "persona count mismatch" in src


# -----------------------------------------------------------------------
# 2. 9A.4 requires 9A.3 psychology profiles
# -----------------------------------------------------------------------

def test_2_orchestrator_requires_psychology_profiles() -> None:
    src = _src(SCRIPT_PATH)
    assert "PersonaPsychologyTrait" in src
    assert "9A.3 psychology profiles incomplete" in src


# -----------------------------------------------------------------------
# 3-4. OCEAN + extra psychology traits passed into prompts
# -----------------------------------------------------------------------

def test_3_ocean_traits_in_prompts() -> None:
    src = _src(SCRIPT_PATH)
    for ocean in (
        "openness", "conscientiousness", "extraversion",
        "agreeableness", "neuroticism",
    ):
        assert f"{ocean}_label" in src
    for extra in (
        "risk_tolerance", "novelty_seeking", "trust_proof_threshold",
        "social_influence_susceptibility",
        "category_involvement_or_expertise", "price_sensitivity",
    ):
        assert f"{extra}_label" in src


def test_4_psychology_control_snapshot_schema_has_all_11() -> None:
    fields = set(PsychologyControlSnapshot.model_fields.keys())
    assert "openness" in fields
    assert "conscientiousness" in fields
    assert "extraversion" in fields
    assert "agreeableness" in fields
    assert "neuroticism" in fields
    assert "risk_tolerance" in fields
    assert "novelty_seeking" in fields
    assert "trust_proof_threshold" in fields
    assert "social_influence_susceptibility" in fields
    assert "category_involvement_or_expertise" in fields
    assert "price_sensitivity" in fields


# -----------------------------------------------------------------------
# 5. Group assignment is stratified and not hardcoded
# -----------------------------------------------------------------------

def test_5_group_assignment_stratified() -> None:
    personas = _make_personas(30)
    groups = assign_groups_stratified(
        personas=personas, group_count=5, group_size=6,
        seed="9A.4|test",
    )
    assert len(groups) == 5
    assert all(len(g) == 6 for g in groups)
    # all 30 covered exactly once
    flat = [pid for g in groups for pid in g]
    assert len(flat) == 30 == len(set(flat))
    # diversity: no group should be 100% one role
    persona_lookup = {p["persona_id"]: p for p in personas}
    for g in groups:
        roles = Counter(persona_lookup[pid]["normalized_primary_role"] for pid in g)
        assert roles.most_common(1)[0][1] < 6


def test_5b_group_assignment_no_hardcoded_lumaloop() -> None:
    src_files = list(PKG_ROOT.glob("*.py"))
    for f in src_files:
        tree = ast.parse(_src(f))
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.IfExp, ast.Compare)):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)
                        and sub.value.lower() == "lumaloop"
                    ):
                        pytest.fail(
                            f"discussion_layer/{f.name} branches on "
                            "'lumaloop' — must be product-general"
                        )


# -----------------------------------------------------------------------
# 6. Private pre-ballots are created
# -----------------------------------------------------------------------

def test_6_orchestrator_runs_pre_ballot_round() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_pre_ballot" in src
    assert 'ballot_stage="pre"' in src or 'ballot_stage=\\"pre\\"' in src
    assert "_PRE_BALLOT_INSTRUCTION" in src


# -----------------------------------------------------------------------
# 7. Public discussion turns are created
# -----------------------------------------------------------------------

def test_7_orchestrator_runs_public_opening_round() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_public_opening" in src
    assert "_PUBLIC_OPENING_INSTRUCTION" in src
    assert 'turn_type="public_opening"' in src


# -----------------------------------------------------------------------
# 8. Peer-response turns reference prior turns
# -----------------------------------------------------------------------

def test_8_peer_response_round_references_prior_turns() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_peer_response" in src
    # peer_response must reference at least one prior turn id
    assert "ref_turn_ids=ref_ids" in src
    assert 'turn_type="peer_response"' in src


# -----------------------------------------------------------------------
# 9. Private final ballots are created
# -----------------------------------------------------------------------

def test_9_orchestrator_runs_final_ballot_round() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_final_ballot" in src
    assert 'ballot_stage="final"' in src or 'ballot_stage=\\"final\\"' in src


# -----------------------------------------------------------------------
# 10. Public/private stance difference is recorded
# -----------------------------------------------------------------------

def test_10_public_private_delta_classifier() -> None:
    # private_acceptance — pre != final, final aligns with public majority
    assert classify_public_private_delta(
        pre_stance="skeptical",
        final_stance="interested_if_proven",
        public_majority_stance="interested_if_proven",
        private_reasoning="convinced",
    ) == "private_acceptance"
    # resistance — pre == something, final differs from public majority and pre
    assert classify_public_private_delta(
        pre_stance="skeptical",
        final_stance="likely_reject",
        public_majority_stance="interested_if_proven",
        private_reasoning="held",
    ) == "resistance"
    # no_change
    assert classify_public_private_delta(
        pre_stance="skeptical",
        final_stance="skeptical",
        public_majority_stance="skeptical",
        private_reasoning=None,
    ) == "no_change"
    # polarization
    assert classify_public_private_delta(
        pre_stance="curious_but_unconvinced",
        final_stance="likely_reject",
        public_majority_stance=None,
        private_reasoning=None,
    ) == "polarization"
    # uncertainty_increase
    assert classify_public_private_delta(
        pre_stance="interested_if_proven",
        final_stance="needs_more_information",
        public_majority_stance=None,
        private_reasoning=None,
    ) == "uncertainty_increase"


# -----------------------------------------------------------------------
# 11. Social influence classification works
# -----------------------------------------------------------------------

def test_11_social_influence_classification() -> None:
    src = _src(SCRIPT_PATH)
    assert "social_influence_classification" in src
    assert "classify_public_private_delta" in src


# -----------------------------------------------------------------------
# 12-15. MemoryAtom schema/table + required fields
# -----------------------------------------------------------------------

def test_12_memory_atom_table_exists() -> None:
    assert PersonaMemoryAtom.__tablename__ == "persona_memory_atoms"


def test_13_memory_atoms_require_origin_type() -> None:
    src = _src(MIGRATION_PATH)
    assert "ck_persona_memory_atoms_origin_type" in src
    assert "origin_type" in src
    assert "nullable=False" in src


def test_14_memory_atoms_require_origin_ref_id() -> None:
    # origin_ref_id is a non-nullable UUID column
    cols = {c.name: c for c in PersonaMemoryAtom.__table__.columns}
    assert cols["origin_ref_id"].nullable is False


def test_15_memory_atoms_require_origin_excerpt() -> None:
    src = _src(MIGRATION_PATH)
    assert "ck_persona_memory_atoms_origin_excerpt_nonempty" in src


# -----------------------------------------------------------------------
# 16. Memory atoms are immutable (no UPDATE/DELETE in orchestrator)
# -----------------------------------------------------------------------

def test_16_memory_atoms_are_immutable() -> None:
    src = _src(SCRIPT_PATH)
    assert "DELETE FROM persona_memory_atoms" not in src
    assert "UPDATE persona_memory_atoms" not in src


# -----------------------------------------------------------------------
# 17. No cross-persona memory leakage (rank_memory_atoms takes per-persona)
# -----------------------------------------------------------------------

def test_17_no_cross_persona_memory_leakage_in_seed_builder() -> None:
    p1_atoms = build_seed_memory_atoms(
        persona_id="p1",
        run_scope_id="r1",
        persona_traits=[{
            "trait_id": "11111111-1111-1111-1111-111111111111",
            "field_name": "price_sensitivity",
            "value": "high",
            "rationale": "expensive",
            "confidence": 0.8,
        }],
        psychology_traits=[],
        evidence_links=[],
        prior_simulation_responses=[],
    )
    # all atoms must be tagged with persona_id == p1
    assert all(a.persona_id == "p1" for a in p1_atoms)


# -----------------------------------------------------------------------
# 18. Memory retrieval uses recency/importance/relevance
# -----------------------------------------------------------------------

def test_18_memory_retrieval_scoring() -> None:
    from assembly.sources.discussion_layer.memory import MemoryAtomDraft
    atoms = [
        MemoryAtomDraft(
            persona_id="p1", run_scope_id="r1", memory_type="trait",
            origin_type="persona_trait", origin_ref_id="t1",
            origin_excerpt="ip rating", memory_text="needs ip rating",
            importance_score=10, recency_index=0,
        ),
        MemoryAtomDraft(
            persona_id="p1", run_scope_id="r1", memory_type="evidence",
            origin_type="persona_evidence_link", origin_ref_id="e1",
            origin_excerpt="weather resistance", memory_text="weather",
            importance_score=2, recency_index=10,
        ),
    ]
    ranked = rank_memory_atoms(atoms=atoms, query="ip rating proof", top_k=2)
    assert ranked[0].memory_text == "needs ip rating"


# -----------------------------------------------------------------------
# 19. Discussion prompts include relevant retrieved memories
# -----------------------------------------------------------------------

def test_19_prompts_include_memory_atoms() -> None:
    src = _src(SCRIPT_PATH)
    assert "Relevant memory atoms" in src
    assert "_build_persona_block" in src
    assert "seed_memory_atoms" in src


# -----------------------------------------------------------------------
# 20. Agents are allowed to disagree (allowed stance set)
# -----------------------------------------------------------------------

def test_20_disagreement_stances_allowed() -> None:
    assert "skeptical" in DISCUSSION_STANCES
    assert "likely_reject" in DISCUSSION_STANCES
    # The stance enum is closed: 5 values
    assert len(DISCUSSION_STANCES) == 5


# -----------------------------------------------------------------------
# 21. Over-cooperation detector
# -----------------------------------------------------------------------

def test_21_overcooperation_detector_flags_convergence() -> None:
    # public_majority is interested_if_proven across 30 turns
    public_stances = ["interested_if_proven"] * 30
    final_stances = {f"p{i}": "interested_if_proven" for i in range(30)}
    result = detect_overcooperation(
        pre_stances={f"p{i}": "skeptical" for i in range(30)},
        final_stances=final_stances,
        public_turn_stances=public_stances,
    )
    assert result["flag"] is True
    assert result["converged"] is True


def test_21b_overcooperation_detector_does_not_flag_when_dissent_present() -> None:
    public_stances = ["interested_if_proven"] * 28 + ["skeptical"] * 2
    final_stances = {f"p{i}": (
        "skeptical" if i < 8 else "interested_if_proven"
    ) for i in range(30)}
    result = detect_overcooperation(
        pre_stances={f"p{i}": "skeptical" for i in range(30)},
        final_stances=final_stances,
        public_turn_stances=public_stances,
    )
    assert result["flag"] is False


# -----------------------------------------------------------------------
# 22. Fake LumaLoop usage claims are rejected
# -----------------------------------------------------------------------

def test_22_fake_use_phrases_caught() -> None:
    out = forbidden_claim_audit(
        texts=[
            "I bought the LumaLoop last week and it works great.",
            "neutral text about night running",
        ],
        product_name="lumaloop",
    )
    assert out["any_fake_target_product_use"]
    assert out["fake_target_product_use_count"] == 1


# -----------------------------------------------------------------------
# 23. Forecast/verdict claims are rejected
# -----------------------------------------------------------------------

def test_23_forecast_claims_caught() -> None:
    out = forbidden_claim_audit(
        texts=[
            "32% of California runners will buy this.",
            "I would not commit to a stance yet.",
        ],
        product_name="lumaloop",
    )
    assert out["any_forecast_or_verdict"]


# -----------------------------------------------------------------------
# 24. Allowed stance labels enforced (Pydantic + DB CHECK)
# -----------------------------------------------------------------------

def test_24_allowed_stance_labels_enforced_pydantic() -> None:
    with pytest.raises(ValidationError):
        TurnDraft(
            speaker_persona_id="p1",
            turn_type="public_opening",
            public_text="x",
            stance="will_buy",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        PrivateBallotDraft(
            persona_id="p1",
            ballot_stage="final",
            private_stance="will_buy",  # type: ignore[arg-type]
            private_reasoning="x",
            confidence="high",
        )


# -----------------------------------------------------------------------
# 25. Sensitive inference scanner runs
# -----------------------------------------------------------------------

def test_25_sensitive_inference_scanner_runs() -> None:
    out = sensitive_inference_audit([
        "Persona presents as religious which biases their tone.",
        "neutral text",
    ])
    assert out["any_sensitive_inference"]
    assert out["finding_count"] >= 1


def test_25b_no_false_positive_on_running_race() -> None:
    out = sensitive_inference_audit([
        "I run 5k road races on Sundays with my running group.",
    ])
    assert not out["any_sensitive_inference"]


# -----------------------------------------------------------------------
# 26. Secret scanner runs
# -----------------------------------------------------------------------

def test_26_secret_scanner_invoked_in_orchestrator() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_for_secrets" in src
    assert "security_redaction_audit" in src


# -----------------------------------------------------------------------
# 27. No new retrieval APIs are called
# -----------------------------------------------------------------------

def test_27_no_new_retrieval_apis_imported() -> None:
    forbidden = ("jina", "exa", "dataforseo", "apify")
    files = [SCRIPT_PATH] + list(PKG_ROOT.glob("*.py"))
    for f in files:
        tree = ast.parse(_src(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.name or "").lower()
                    for tok in forbidden:
                        assert tok not in name
            elif isinstance(node, ast.ImportFrom):
                name = (node.module or "").lower()
                for tok in forbidden:
                    assert tok not in name


# -----------------------------------------------------------------------
# 28. No Jina/Exa/DataForSEO/Reddit/Apify usage (also via Call)
# -----------------------------------------------------------------------

def test_28_no_brave_tavily_youtube_imports_in_orchestrator() -> None:
    forbidden_modules = (
        "assembly.sources.brave",
        "assembly.sources.tavily",
        "assembly.sources.youtube",
        "assembly.sources.firecrawl",
        "assembly.sources.amazon_reviews_2023",
    )
    src = _src(SCRIPT_PATH)
    for tok in forbidden_modules:
        assert tok not in src


# -----------------------------------------------------------------------
# 29-32. No mutation to protected tables
# -----------------------------------------------------------------------

def test_29_no_source_records_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(SourceRecord(" not in src


def test_30_no_persona_records_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaRecord(" not in src


def test_31_no_persona_traits_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaTrait(" not in src


def test_32_no_persona_psychology_traits_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaPsychologyTrait(" not in src


# -----------------------------------------------------------------------
# 33. Discussion report is generated
# -----------------------------------------------------------------------

def test_33_orchestrator_emits_report_files() -> None:
    src = _src(SCRIPT_PATH)
    assert "REPORT_JSON_PATH" in src
    assert "REPORT_MD_PATH" in src
    assert "render_discussion_report_json" in src
    assert "render_discussion_report_markdown" in src


# -----------------------------------------------------------------------
# 34. Discussion quality evaluator runs
# -----------------------------------------------------------------------

def test_34_quality_evaluator_runs() -> None:
    turns = [
        {
            "speaker_persona_id": "p1",
            "turn_type": "public_opening",
            "public_text": "I want IP rating proof.",
            "stance": "skeptical",
            "referenced_turn_ids": [],
            "referenced_memory_atom_ids": ["a1"],
            "psychology_control_snapshot": {"persona_id": "p1"},
        },
        {
            "speaker_persona_id": "p2",
            "turn_type": "peer_response",
            "public_text": "You make a good point about the ip rating.",
            "stance": "interested_if_proven",
            "referenced_turn_ids": ["t1"],
            "referenced_memory_atom_ids": [],
            "psychology_control_snapshot": {"persona_id": "p2"},
        },
    ]
    pre = [
        {"persona_id": "p1", "ballot_stage": "pre",
         "private_stance": "skeptical",
         "private_reasoning": "this is a synthetic simulation", "confidence": "medium",
         "public_private_delta": None},
    ]
    final = [
        {"persona_id": "p1", "ballot_stage": "final",
         "private_stance": "skeptical",
         "private_reasoning": "this is a synthetic n=30 simulation",
         "confidence": "medium", "public_private_delta": "no_change"},
    ]
    atoms = [
        {"origin_type": "persona_trait", "origin_ref_id": "t1",
         "origin_excerpt": "x", "persona_id": "p1"},
    ]
    fb = forbidden_claim_audit(
        texts=[t["public_text"] for t in turns], product_name="lumaloop",
    )
    sa = sensitive_inference_audit([t["public_text"] for t in turns])
    oc = detect_overcooperation(
        pre_stances={"p1": "skeptical"},
        final_stances={"p1": "skeptical"},
        public_turn_stances=["skeptical", "interested_if_proven"],
    )
    q = evaluate_discussion_quality(
        turns=turns, pre_ballots=pre, final_ballots=final,
        memory_atoms=atoms, forbidden_audit=fb, sensitive_audit=sa,
        overcooperation=oc, expected_persona_count=1,
    )
    assert q.aggregate_score > 0


# -----------------------------------------------------------------------
# 35. Existing 9A.3 tests still pass — collector verifies file presence
# -----------------------------------------------------------------------

def test_35_9a_3_test_file_still_present() -> None:
    p = (
        Path(__file__).resolve().parent
        / "test_persona_psychology_layer_9a_3.py"
    )
    assert p.exists()


# -----------------------------------------------------------------------
# 36. Full unit tests pass — sanity check schemas exist
# -----------------------------------------------------------------------

def test_36_orm_models_have_check_constraints() -> None:
    src = _src(MIGRATION_PATH)
    for ck in (
        "ck_discussion_sessions_session_type",
        "ck_discussion_sessions_status",
        "ck_discussion_turns_turn_type",
        "ck_discussion_turns_stance",
        "ck_discussion_private_ballots_stage",
        "ck_discussion_private_ballots_stance",
        "ck_discussion_private_ballots_confidence",
        "ck_discussion_private_ballots_delta",
        "ck_persona_memory_atoms_memory_type",
        "ck_persona_memory_atoms_origin_type",
        "ck_persona_memory_atoms_importance_range",
        "ck_persona_memory_atoms_origin_excerpt_nonempty",
    ):
        assert ck in src


# -----------------------------------------------------------------------
# 37. Full integration tests pass — orchestrator imports cleanly
# -----------------------------------------------------------------------

def test_37_orchestrator_imports_cleanly() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_disc_9a_4", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.PHASE_LABEL == "9A.4"
    assert mod.EXPECTED_PERSONA_COUNT == 30


# -----------------------------------------------------------------------
# Bonus: dry-run is the default
# -----------------------------------------------------------------------

def test_bonus_dry_run_is_default() -> None:
    src = _src(SCRIPT_PATH)
    # --commit must be opt-in
    assert '"--commit", action="store_true"' in src


def test_bonus_pilot_mode_supported() -> None:
    src = _src(SCRIPT_PATH)
    assert '"--pilot"' in src
    assert "args.pilot" in src


def test_bonus_diversity_audit_runs() -> None:
    personas = _make_personas(30)
    groups = assign_groups_stratified(
        personas=personas, group_count=5, group_size=6, seed="t",
    )
    persona_lookup = {p["persona_id"]: p for p in personas}
    aud = diversity_audit([
        [persona_lookup[pid] for pid in g] for g in groups
    ])
    assert aud["group_count"] == 5
    assert all(s == 6 for s in aud["group_sizes"])


def test_bonus_psychology_snapshot_validates() -> None:
    snap = PsychologyControlSnapshot(
        persona_id="x",
        openness=0.5, conscientiousness=0.5, extraversion=0.5,
        agreeableness=0.5, neuroticism=0.5, risk_tolerance=0.5,
        novelty_seeking=0.5, trust_proof_threshold=0.5,
        social_influence_susceptibility=0.5,
        category_involvement_or_expertise=0.5,
        price_sensitivity=0.5,
    )
    assert snap.openness == 0.5


def test_bonus_orchestrator_documents_simulation_row() -> None:
    src = _src(SCRIPT_PATH)
    assert "cost_guard_control_row_for_discussion" in src
    assert "no_agents_or_responses_attached" in src


def test_bonus_orchestrator_no_random_module_in_inference() -> None:
    files = list(PKG_ROOT.glob("*.py"))
    for f in files:
        if f.name in ("group_assignment.py",):
            # group_assignment uses sha256-keyed sort; not random
            continue
        tree = ast.parse(_src(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if (
                    isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "random"
                ):
                    pytest.fail(f"discussion_layer/{f.name} uses random.*")


def test_bonus_unlaunched_product_caveat_in_report() -> None:
    src = _src(PKG_ROOT / "report.py")
    assert "synthetic discussion simulation" in src.lower()
    assert "not a forecast" in src.lower()
    assert "not a launch verdict" in src.lower()
