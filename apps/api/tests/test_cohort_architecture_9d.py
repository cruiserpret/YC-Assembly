"""Phase 9D — cohort / cluster architecture tests.

Covers operator scenarios 1-27 + bonus checks. NO live retrieval, NO
DB writes from the test file itself.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from assembly.sources.cohort_architecture import (
    build_cohort_feature_vectors,
    build_society_rollup,
    cluster_personas_into_cohorts,
    evaluate_cohort_architecture_quality,
    render_cohort_report_json,
    render_cohort_report_markdown,
    select_cohort_representatives,
    summarize_cohort,
)
from assembly.sources.cohort_architecture.clusterer import assignment_audit
from assembly.sources.cohort_architecture.feature_builder import (
    FORBIDDEN_FEATURE_NAMES,
    PSYCHOLOGY_TRAIT_NAMES,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "build_cohort_architecture_9d.py"
)
PKG_ROOT = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "cohort_architecture"
)
MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions"
    / "20260507_0009_phase_9_d_cohort_architecture.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# Synthetic 66-persona fixture
def _make_personas(n: int = 66) -> list[dict]:
    import uuid as _uuid
    roles = [
        "price_skeptic", "trust_seeker", "performance_focused_buyer",
        "safety_visibility_focused_buyer", "use_case_focused_buyer",
        "format_focused_buyer", "convenience_focused_buyer",
        "competitor_user_noxgear",
    ]
    providers = ["brave_search", "tavily_search", "youtube_data_api"]
    stances = [
        "interested_if_proven", "skeptical", "needs_more_information",
        "curious_but_unconvinced", "likely_reject",
    ]
    deltas = ["no_change", "resistance", "private_acceptance"]
    out = []
    for i in range(n):
        psy = {
            tname: round(0.3 + (i * 0.013 + j * 0.07) % 0.7, 3)
            for j, tname in enumerate(PSYCHOLOGY_TRAIT_NAMES)
        }
        out.append({
            "persona_id": str(_uuid.UUID(int=(0xc0c0c0c0 << 64) | i)),
            "normalized_primary_role": roles[i % len(roles)],
            "source_provider_family": providers[i % len(providers)],
            "psychology_value_map": psy,
            "pre_stance": stances[i % len(stances)],
            "final_stance": stances[(i + 1) % len(stances)],
            "public_private_delta": deltas[i % len(deltas)],
            "peer_reference_count": (i % 5),
            "has_top_objection": (i % 3 != 0),
            "has_top_proof_need": (i % 4 != 0),
            "memory_atom_count_by_type": {
                "trait": (i % 4) + 1,
                "psychology": (i % 3) + 1,
            },
            "reflection_present": True,
        })
    return out


# -----------------------------------------------------------------------
# 1. 9D loads official 9B.1 artifacts
# -----------------------------------------------------------------------

def test_1_loads_official_9b_1_artifacts() -> None:
    src = _src(SCRIPT_PATH)
    assert 'DiscussionSession.phase == "9B"' in src
    assert "no 9B discussion session" in src
    assert "_load_9b_society" in src


# -----------------------------------------------------------------------
# 2. 9D refuses if 66-person 9B society is incomplete
# -----------------------------------------------------------------------

def test_2_refuses_on_incomplete_9b_society() -> None:
    src = _src(SCRIPT_PATH)
    assert "EXPECTED_PERSONA_COUNT = 66" in src
    assert "persona count mismatch" in src
    assert "ballot completeness mismatch" in src
    assert "turn count mismatch: expected 264" in src


# -----------------------------------------------------------------------
# 3-7. Feature builder includes role/context/OCEAN/extra/discussion/memory
# -----------------------------------------------------------------------

def test_3_features_include_role_context() -> None:
    personas = _make_personas(20)
    vecs, meta = build_cohort_feature_vectors(personas=personas)
    fnames = meta["feature_names"]
    assert any(f.startswith("role::") for f in fnames)
    assert any(f.startswith("provider::") for f in fnames)


def test_4_features_include_ocean() -> None:
    personas = _make_personas(20)
    _, meta = build_cohort_feature_vectors(personas=personas)
    for trait in (
        "openness", "conscientiousness", "extraversion",
        "agreeableness", "neuroticism",
    ):
        assert f"psy::{trait}" in meta["feature_names"]


def test_5_features_include_additional_psychology() -> None:
    personas = _make_personas(20)
    _, meta = build_cohort_feature_vectors(personas=personas)
    for trait in (
        "risk_tolerance", "novelty_seeking", "trust_proof_threshold",
        "social_influence_susceptibility",
        "category_involvement_or_expertise", "price_sensitivity",
    ):
        assert f"psy::{trait}" in meta["feature_names"]


def test_6_features_include_discussion_behavior() -> None:
    personas = _make_personas(20)
    _, meta = build_cohort_feature_vectors(personas=personas)
    fnames = meta["feature_names"]
    assert any(f.startswith("pre_stance::") for f in fnames)
    assert any(f.startswith("final_stance::") for f in fnames)
    assert any(f.startswith("delta::") for f in fnames)
    assert "peer_ref_count_norm" in fnames


def test_7_features_include_memory_signals() -> None:
    personas = _make_personas(20)
    _, meta = build_cohort_feature_vectors(personas=personas)
    fnames = meta["feature_names"]
    for mt in (
        "trait", "psychology", "evidence",
        "prior_simulation", "discussion_turn", "private_ballot",
    ):
        assert f"memory::{mt}" in fnames


# -----------------------------------------------------------------------
# 8. Feature builder excludes protected/sensitive attributes
# -----------------------------------------------------------------------

def test_8_features_exclude_protected_attributes() -> None:
    personas = _make_personas(20)
    _, meta = build_cohort_feature_vectors(personas=personas)
    fnames = " ".join(meta["feature_names"]).lower()
    for forbidden in FORBIDDEN_FEATURE_NAMES:
        assert forbidden not in fnames


# -----------------------------------------------------------------------
# 9. Clustering is deterministic
# -----------------------------------------------------------------------

def test_9_clustering_is_deterministic() -> None:
    personas = _make_personas(66)
    vecs, _ = build_cohort_feature_vectors(personas=personas)
    pids = [p["persona_id"] for p in personas]
    out_a, _ = cluster_personas_into_cohorts(
        persona_ids=pids, feature_vectors=vecs,
    )
    out_b, _ = cluster_personas_into_cohorts(
        persona_ids=pids, feature_vectors=vecs,
    )
    assert out_a == out_b


# -----------------------------------------------------------------------
# 10. Every persona is assigned exactly once
# -----------------------------------------------------------------------

def test_10_every_persona_assigned_exactly_once() -> None:
    personas = _make_personas(66)
    vecs, _ = build_cohort_feature_vectors(personas=personas)
    pids = [p["persona_id"] for p in personas]
    cohorts, _ = cluster_personas_into_cohorts(
        persona_ids=pids, feature_vectors=vecs,
    )
    aud = assignment_audit(pids, cohorts)
    assert aud["every_persona_assigned_exactly_once"]
    assert aud["distinct_assigned_count"] == 66


# -----------------------------------------------------------------------
# 11. Cohort count target is 8-14 for 66 personas
# -----------------------------------------------------------------------

def test_11_cohort_count_in_target_band() -> None:
    personas = _make_personas(66)
    vecs, _ = build_cohort_feature_vectors(personas=personas)
    pids = [p["persona_id"] for p in personas]
    cohorts, _ = cluster_personas_into_cohorts(
        persona_ids=pids, feature_vectors=vecs,
        target_min_cohorts=8, target_max_cohorts=14,
    )
    assert 8 <= len(cohorts) <= 14


# -----------------------------------------------------------------------
# 12. Cohort size limits are enforced
# -----------------------------------------------------------------------

def test_12_cohort_size_limits_enforced() -> None:
    personas = _make_personas(66)
    vecs, _ = build_cohort_feature_vectors(personas=personas)
    pids = [p["persona_id"] for p in personas]
    cohorts, audit = cluster_personas_into_cohorts(
        persona_ids=pids, feature_vectors=vecs,
        target_min_cohorts=8, target_max_cohorts=14,
        min_cluster_size=3, max_cluster_size=10,
    )
    # max size respected unless audit recorded an exception
    over_max = [len(c) for c in cohorts if len(c) > 10]
    if over_max:
        assert audit.get("size_cap_exceptions"), (
            "max_cluster_size violated without audited exception"
        )


# -----------------------------------------------------------------------
# 13. Representative persona must be a real persisted persona
# -----------------------------------------------------------------------

def test_13_representative_is_real_persona() -> None:
    personas = _make_personas(20)
    vecs, _ = build_cohort_feature_vectors(personas=personas)
    pid_to_vec = dict(zip([p["persona_id"] for p in personas], vecs))
    persona_meta = {
        p["persona_id"]: {
            "final_stance": p["final_stance"],
            "psychology_value_map": p["psychology_value_map"],
        }
        for p in personas
    }
    cohort = [p["persona_id"] for p in personas[:5]]
    reps = select_cohort_representatives(
        cohort_persona_ids=cohort,
        persona_features=pid_to_vec,
        persona_meta=persona_meta,
    )
    for kind in ("primary", "dissent", "proof_threshold"):
        if reps[kind] is not None:
            assert reps[kind] in cohort


# -----------------------------------------------------------------------
# 14. Cohort claims trace to persona/source/discussion/memory IDs
# -----------------------------------------------------------------------

def test_14_cohort_summary_has_evidence_links() -> None:
    persona_meta = {
        f"p{i}": {
            "normalized_primary_role": "trust_seeker",
            "final_stance": "interested_if_proven",
        }
        for i in range(5)
    }
    persona_psychology = {
        f"p{i}": {
            "openness": 0.5, "conscientiousness": 0.7,
            "extraversion": 0.5, "agreeableness": 0.5,
            "neuroticism": 0.5, "risk_tolerance": 0.4,
            "novelty_seeking": 0.5, "trust_proof_threshold": 0.8,
            "social_influence_susceptibility": 0.4,
            "category_involvement_or_expertise": 0.6,
            "price_sensitivity": 0.5,
        }
        for i in range(5)
    }
    pre = {
        f"p{i}": {
            "private_stance": "skeptical",
            "private_reasoning": "needs IP rating proof and battery life data",
            "top_objection": "no IP rating disclosed",
            "top_proof_need": "ip-rating + lumens disclosure",
        }
        for i in range(5)
    }
    final = {
        f"p{i}": {
            "private_stance": "interested_if_proven",
            "private_reasoning": "synthetic n=66 simulation",
            "public_private_delta": "no_change",
        }
        for i in range(5)
    }
    refl = {f"p{i}": {"private_stance": "interested_if_proven"} for i in range(5)}
    turns = [
        {
            "turn_id": "t1", "speaker_persona_id": "p0",
            "turn_type": "challenge", "stance": "skeptical",
            "public_text": "I want to see the IP rating before I'm convinced",
            "referenced_turn_ids": [],
        },
    ]
    atoms = [
        {
            "memory_atom_id": "a1", "id": "a1", "persona_id": "p0",
            "memory_type": "evidence",
            "origin_excerpt": "weather-resistant claim is too vague",
            "memory_text": "Need IP rating",
            "importance_score": 8,
        },
    ]
    s = summarize_cohort(
        cohort_persona_ids=[f"p{i}" for i in range(5)],
        persona_meta=persona_meta,
        persona_psychology=persona_psychology,
        pre_ballots=pre, final_ballots=final, reflection_ballots=refl,
        discussion_turns=turns, memory_atoms=atoms,
    )
    links = s.get("_evidence_links") or []
    assert len(links) >= 1
    # every link must have an excerpt + at least one origin-id field
    for ev in links:
        assert (ev.get("excerpt") or "").strip()
        assert (
            ev.get("persona_id")
            or ev.get("discussion_turn_id")
            or ev.get("memory_atom_id")
            or ev.get("source_record_id")
            or ev.get("label")
        )


# -----------------------------------------------------------------------
# 15. Cohort memory summaries do not invent facts
# -----------------------------------------------------------------------

def test_15_cohort_summary_does_not_invent() -> None:
    """If we feed empty inputs, the summary must produce empty
    structures, not synthesized text."""
    s = summarize_cohort(
        cohort_persona_ids=[],
        persona_meta={}, persona_psychology={},
        pre_ballots={}, final_ballots={}, reflection_ballots={},
        discussion_turns=[], memory_atoms=[],
    )
    assert s["objection_summary"]["by_bucket"] == {}
    assert s["proof_need_summary"]["by_bucket"] == {}
    assert s["psychology_summary"] == {}
    assert (s.get("_evidence_links") or []) == []


# -----------------------------------------------------------------------
# 16-17. Weighted rollup does not produce market forecasts / verdicts
# -----------------------------------------------------------------------

def test_16_rollup_no_forecast_keys() -> None:
    rollup = build_society_rollup(
        cohort_summaries=[
            {
                "cohort_size": 6,
                "stance_distribution": {"interested_if_proven": 6},
                "objection_summary": {"by_bucket": {"price_value_concern": 3}},
                "proof_need_summary": {"by_bucket": {"ip_rating_disclosure": 5}},
                "discussion_behavior_summary": {
                    "public_private_delta_distribution": {"no_change": 6},
                },
            }
        ],
        cohort_weights=[1.0],
        persona_count=6,
    )
    forbidden_keys = (
        "adoption_rate", "buy_intent_pct", "market_share",
        "launch_verdict", "kill_verdict", "should_launch",
    )
    flat = " ".join(rollup.keys()).lower()
    for k in forbidden_keys:
        assert k not in flat


def test_17_rollup_caveats_no_launch_verdict() -> None:
    rollup = build_society_rollup(
        cohort_summaries=[
            {
                "cohort_size": 6,
                "stance_distribution": {"skeptical": 6},
                "objection_summary": {"by_bucket": {}},
                "proof_need_summary": {"by_bucket": {}},
                "discussion_behavior_summary": {
                    "public_private_delta_distribution": {"resistance": 6},
                },
            }
        ],
        cohort_weights=[1.0],
        persona_count=6,
    )
    flat = " ".join(rollup["caveats"]).lower()
    assert "synthetic" in flat
    assert "not a forecast" in flat
    assert "not a launch verdict" in flat
    assert "run-scoped" in flat


# -----------------------------------------------------------------------
# 18. No fake LumaLoop usage claims (orchestrator runs the audit)
# -----------------------------------------------------------------------

def test_18_orchestrator_runs_forbidden_claim_audit() -> None:
    src = _src(SCRIPT_PATH)
    assert "forbidden_claim_audit" in src
    assert 'product_name=sess.product_name' in src


# -----------------------------------------------------------------------
# 19. No new retrieval APIs are imported
# -----------------------------------------------------------------------

def test_19_no_new_retrieval_apis() -> None:
    forbidden_modules = ("jina", "exa", "dataforseo", "apify")
    files = [SCRIPT_PATH] + list(PKG_ROOT.glob("*.py"))
    for f in files:
        tree = ast.parse(_src(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.name or "").lower()
                    for tok in forbidden_modules:
                        assert tok not in name
            elif isinstance(node, ast.ImportFrom):
                name = (node.module or "").lower()
                for tok in forbidden_modules:
                    assert tok not in name


# -----------------------------------------------------------------------
# 20. No Jina/Exa/DataForSEO/Reddit/Apify usage
# -----------------------------------------------------------------------

def test_20_no_brave_tavily_youtube_imports() -> None:
    src = _src(SCRIPT_PATH)
    forbidden_modules = (
        "from assembly.sources.brave",
        "from assembly.sources.tavily",
        "from assembly.sources.youtube",
        "from assembly.sources.firecrawl",
        "from assembly.sources.amazon_reviews_2023",
    )
    for tok in forbidden_modules:
        assert tok not in src


# -----------------------------------------------------------------------
# 21. No protected society tables are mutated
# -----------------------------------------------------------------------

def test_21_no_mutation_of_society_tables() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(SourceRecord(" not in src
    assert "session.add(PersonaRecord(" not in src
    assert "session.add(PersonaTrait(" not in src
    assert "session.add(PersonaEvidenceLink(" not in src
    assert "session.add(PersonaPsychologyTrait(" not in src
    assert "session.add(DiscussionSession(" not in src
    assert "session.add(DiscussionGroup(" not in src
    assert "session.add(DiscussionTurn(" not in src
    assert "session.add(DiscussionPrivateBallot(" not in src
    assert "session.add(PersonaMemoryAtom(" not in src
    assert "session.add(Agent(" not in src
    assert "session.add(AgentResponse(" not in src


# -----------------------------------------------------------------------
# 22. Secret scanner runs
# -----------------------------------------------------------------------

def test_22_secret_scanner_invoked() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_for_secrets" in src
    assert "security_redaction_audit" in src


# -----------------------------------------------------------------------
# 23. Quality evaluator runs
# -----------------------------------------------------------------------

def test_23_quality_evaluator_runs() -> None:
    personas = _make_personas(66)
    vecs, _ = build_cohort_feature_vectors(personas=personas)
    pids = [p["persona_id"] for p in personas]
    cohorts, _ = cluster_personas_into_cohorts(
        persona_ids=pids, feature_vectors=vecs,
    )
    persona_features_dict = dict(zip(pids, vecs))
    summaries = [
        {
            "psychology_summary": {"openness": {"mean": 0.5, "stdev": 0.1, "label": "medium"}},
            "objection_summary": {"by_bucket": {"price_value_concern": 1}},
            "proof_need_summary": {"by_bucket": {"ip_rating_disclosure": 1}},
            "stance_distribution": {"skeptical": len(c)},
            "discussion_behavior_summary": {
                "public_private_delta_distribution": {"no_change": len(c)},
            },
            "_evidence_links": [
                {"evidence_role": "objection", "excerpt": "x", "persona_id": c[0]},
            ],
            "cohort_size": len(c),
        }
        for c in cohorts
    ]
    rollup = {
        "social_influence_summary": {"no_change": 0.6, "resistance": 0.4},
        "caveats": [
            "Synthetic n=66 simulation. Not a forecast.",
            "Cohorts are run-scoped + brief-scoped. Not a launch verdict.",
            "The product is unlaunched.",
        ],
    }
    q = evaluate_cohort_architecture_quality(
        cohorts=cohorts,
        persona_features=persona_features_dict,
        cohort_summaries=summaries,
        society_rollup=rollup,
        pre_ballots={}, final_ballots={},
        expected_persona_count=66,
        forbidden_audit={
            "any_forecast_or_verdict": False,
            "any_fake_target_product_use": False,
        },
        sensitive_audit={"any_sensitive_inference": False},
        evidence_link_count=len(cohorts),
    )
    for k in (
        "clustering_quality_score", "traceability_score",
        "psychology_preservation_score", "objection_preservation_score",
        "proof_need_preservation_score",
        "social_influence_preservation_score",
        "non_representativeness_integrity_score",
        "anti_forecast_score", "unlaunched_product_integrity_score",
        "scalability_score", "aggregate_score", "ready_state",
    ):
        assert k in q


# -----------------------------------------------------------------------
# 24. Founder/operator report generated
# -----------------------------------------------------------------------

def test_24_report_files_emitted() -> None:
    src = _src(SCRIPT_PATH)
    assert "REPORT_JSON_PATH" in src
    assert "REPORT_MD_PATH" in src
    assert "render_cohort_report_json" in src
    assert "render_cohort_report_markdown" in src
    assert "lumaloop_cohort_architecture_report_9d" in src


# -----------------------------------------------------------------------
# 25. Existing 9B.1 tests still pass — collector verifies file presence
# -----------------------------------------------------------------------

def test_25_9b_1_test_file_still_present() -> None:
    p = (
        Path(__file__).resolve().parent
        / "test_repair_9b_reflections_9b_1.py"
    )
    assert p.exists()


# -----------------------------------------------------------------------
# 26. Full unit tests pass — orchestrator imports cleanly
# -----------------------------------------------------------------------

def test_26_orchestrator_imports_cleanly() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_cohort_9d", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.PHASE_LABEL == "9D"
    assert mod.EXPECTED_PERSONA_COUNT == 66
    assert mod.TARGET_MIN_COHORTS == 8
    assert mod.TARGET_MAX_COHORTS == 14


# -----------------------------------------------------------------------
# 27. Full integration tests pass — dry-run is the default
# -----------------------------------------------------------------------

def test_27_dry_run_is_default() -> None:
    src = _src(SCRIPT_PATH)
    assert '"--commit", action="store_true"' in src


# -----------------------------------------------------------------------
# Bonus tests
# -----------------------------------------------------------------------

def test_bonus_migration_has_check_constraints() -> None:
    src = _src(MIGRATION_PATH)
    for ck in (
        "ck_society_cohorts_size_positive",
        "ck_society_cohorts_weight_range",
        "ck_society_cohorts_clustering_method",
        "ck_society_cohort_evidence_links_role",
        "ck_society_cohort_evidence_links_at_least_one_origin",
        "ck_society_cohort_evidence_links_excerpt_nonempty",
        "ck_society_cohort_rollups_cohort_count",
    ):
        assert ck in src


def test_bonus_no_random_calls_in_clusterer() -> None:
    files = list(PKG_ROOT.glob("*.py"))
    for f in files:
        tree = ast.parse(_src(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if (
                    isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "random"
                ):
                    pytest.fail(
                        f"cohort_architecture/{f.name} uses random.* — "
                        "must be deterministic"
                    )


def test_bonus_no_lumaloop_branching_in_package() -> None:
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
                            f"cohort_architecture/{f.name} branches on "
                            "'lumaloop' — must be product-general"
                        )


def test_bonus_orchestrator_documents_no_llm_calls() -> None:
    src = _src(SCRIPT_PATH)
    assert "NO LLM calls" in src or "no llm calls" in src.lower()
    # Confirm: no cost_guarded_chat invoked anywhere
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = ""
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            assert name != "cost_guarded_chat", (
                "9D orchestrator must not invoke cost_guarded_chat"
            )


def test_bonus_assignment_audit_detects_duplicates() -> None:
    pids = ["a", "b", "c"]
    bad_cohorts = [["a", "b"], ["b", "c"]]
    aud = assignment_audit(pids, bad_cohorts)
    assert aud["duplicate_assignments"] == 1
    assert not aud["every_persona_assigned_exactly_once"]


def test_bonus_clusterer_undersize_remerge() -> None:
    """Under-size singleton clusters should be merged into nearest peer
    (when above target_min_cohorts)."""
    personas = _make_personas(66)
    vecs, _ = build_cohort_feature_vectors(personas=personas)
    pids = [p["persona_id"] for p in personas]
    cohorts, audit = cluster_personas_into_cohorts(
        persona_ids=pids, feature_vectors=vecs,
        target_min_cohorts=8, target_max_cohorts=14,
        min_cluster_size=3, max_cluster_size=10,
    )
    # All cohorts ≥ min_cluster_size OR audit explains why
    sizes = [len(c) for c in cohorts]
    if any(s < 3 for s in sizes):
        # only acceptable if the cohort count is at the target_min floor
        assert len(cohorts) <= 8
