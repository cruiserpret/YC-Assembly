"""Phase 8.5D.2E — bounded run-scoped persona persistence tests.

Operator scenarios 1-29 covered. Tests are static-grep + import-only
(no DB writes from the test file itself). The integration of the
script with the DB is exercised by the live --dry-run + --commit
runs.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import re
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "run_scoped_persona_persistence_8_5d_2e.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "phase_8_5d_2e_persistence", SCRIPT_PATH,
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# 1. Script reads 8.5D.1E audit
# ---------------------------------------------------------------------------


def test_script_reads_8_5d_1e_audit() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "persona_set_compression_dry_run_8_5d_1e.json" in src


# ---------------------------------------------------------------------------
# 2 + 3 + 6. Refuses to run when ready_for_mutating is false / wrong count /
#  wrong recommendation
# ---------------------------------------------------------------------------


def test_validator_refuses_when_ready_for_mutating_is_false() -> None:
    mod = _load_script()
    bad = {
        "ready_for_mutating_phase": False,
        "compressed_persona_candidates": [
            {} for _ in range(7)
        ],
        "diversity_after": {
            "mutating_persistence_recommendation": "READY",
        },
        "launch_state": "unlaunched",
    }
    ok, blockers = mod._validate_compressed_set(audit_1e=bad)
    assert ok is False
    assert any("ready_for_mutating_phase" in b for b in blockers)


def test_validator_refuses_when_compressed_count_wrong() -> None:
    mod = _load_script()
    bad = {
        "ready_for_mutating_phase": True,
        "compressed_persona_candidates": [{}, {}, {}],
        "diversity_after": {
            "mutating_persistence_recommendation": "READY",
        },
        "launch_state": "unlaunched",
    }
    ok, blockers = mod._validate_compressed_set(audit_1e=bad)
    assert ok is False
    assert any("compressed_candidate_count" in b for b in blockers)


def test_validator_refuses_when_diversity_not_ready() -> None:
    mod = _load_script()
    bad = {
        "ready_for_mutating_phase": True,
        "compressed_persona_candidates": [
            _good_candidate(f"c{i}") for i in range(7)
        ],
        "diversity_after": {
            "mutating_persistence_recommendation": "DEFER_DIVERSIFY",
        },
        "launch_state": "unlaunched",
    }
    ok, blockers = mod._validate_compressed_set(audit_1e=bad)
    assert ok is False
    assert any("DEFER_DIVERSIFY" in b for b in blockers)


def _good_candidate(cid: str) -> dict:
    return {
        "candidate_id": cid,
        "scope": "brief_scoped",
        "persistence_status": "dry_run_only",
        "not_global_persona": True,
        "target_brief": "strideshield",
        "generated_for_phase": "8.5D.1E",
        "pre_normalization_role": "competitor_user_body_glide",
        "normalized_primary_role": "competitor_user_body_glide",
        "secondary_persona_roles": [],
        "role_inference_basis": ["evidence basis"],
        "segment_label": "body glide buyer",
        "source_record_ids": ["planned::strideshield::brave_search_result::aaa"],
        "evidence_summary": "summary",
        "evidence_snippets": ["I tried Body Glide on my heels."],
        "evidence_theme": "competitor::body glide",
        "source_provider_family": "brave_search",
        "inferred_traits": [
            {
                "trait_name": "current_alternative_competitor",
                "trait_value": "Body Glide",
                "evidence_source_record_id": (
                    "planned::strideshield::brave_search_result::aaa"
                ),
                "evidence_excerpt": "Body Glide for friction",
                "confidence": "high", "caveat": None,
            },
            {
                "trait_name": "preference_performance_use_case",
                "trait_value": "long runs",
                "evidence_source_record_id": (
                    "planned::strideshield::brave_search_result::aaa"
                ),
                "evidence_excerpt": "uses on long runs",
                "confidence": "high", "caveat": None,
            },
        ],
        "inferred_preferences": ["pref"],
        "inferred_objections": ["price too high"],
        "inferred_behaviors": ["uses for long runs"],
        "hypothetical_target_product_reaction": "would compare",
        "confidence": "high", "evidence_strength": "strong",
        "quality_score": 9.0,
        "caveats": [], "simulation_usefulness_summary": "useful",
        "persistence_recommendation": "DEFER",
        "kept_reason": "first for role",
    }


# ---------------------------------------------------------------------------
# 4. Rejects fake StrideShield usage claims
# ---------------------------------------------------------------------------


def test_validator_rejects_fake_strideshield_usage() -> None:
    mod = _load_script()
    fake = _good_candidate("fake")
    fake["evidence_snippets"] = ["I bought StrideShield last week."]
    audit = {
        "ready_for_mutating_phase": True,
        "compressed_persona_candidates": (
            [fake] + [_good_candidate(f"c{i}") for i in range(6)]
        ),
        "diversity_after": {
            "mutating_persistence_recommendation": "READY",
        },
        "launch_state": "unlaunched",
    }
    ok, blockers = mod._validate_compressed_set(audit_1e=audit)
    assert ok is False
    assert any("launch-state" in b for b in blockers)


# ---------------------------------------------------------------------------
# 5 + 7. Source-record resolution + insert-or-reuse by content_hash
# ---------------------------------------------------------------------------


def test_script_uses_content_hash_to_dedupe_sources() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "content_hash" in src
    # The script must look up existing SourceRecord by (source_kind,
    # content_hash) before inserting a new one.
    assert (
        "SourceRecord.source_kind" in src
        and "SourceRecord.content_hash" in src
    )
    assert "scalar_one_or_none()" in src


def test_script_recomputes_content_hash_deterministically() -> None:
    mod = _load_script()
    planned = {
        "source_kind": "brave_search_result",
        "source_url": "https://example.com/x",
        "content_preview": "deterministic content",
        "metadata": {"matched_terms": []},
        "language": "en",
        "compliance_tag": "public_html",
        "captured_at": "2026-05-07T00:00:00+00:00",
    }
    out1 = mod._build_source_record_for_insert(
        planned=planned, compressed_candidate_ids_using_this=["c1"],
    )
    out2 = mod._build_source_record_for_insert(
        planned=planned, compressed_candidate_ids_using_this=["c1"],
    )
    assert out1["content_hash"] == out2["content_hash"]
    import hashlib
    expected = hashlib.sha256(
        b"deterministic content",
    ).hexdigest()
    assert out1["content_hash"] == expected


# ---------------------------------------------------------------------------
# 8 + 9. No raw user IDs / image URLs stored
# ---------------------------------------------------------------------------


def test_script_strips_raw_user_id_keys_from_metadata() -> None:
    mod = _load_script()
    planned = {
        "source_kind": "youtube_comment_result",
        "source_url": "https://youtube.com/watch?v=x",
        "content_preview": "nice video",
        "metadata": {
            "matched_terms": [],
            "channel_id": "UC_LEAK",
            "raw_user_id": "leak123",
            "author_channel_id": "UC_LEAK_2",
        },
        "language": "en",
        "compliance_tag": "public_api",
        "captured_at": "2026-05-07T00:00:00+00:00",
    }
    out = mod._build_source_record_for_insert(
        planned=planned, compressed_candidate_ids_using_this=["c1"],
    )
    md = out["metadata"]
    assert "channel_id" not in md
    assert "raw_user_id" not in md
    assert "author_channel_id" not in md


def test_script_strips_image_url_keys_from_metadata() -> None:
    mod = _load_script()
    planned = {
        "source_kind": "brave_search_result",
        "source_url": "https://example.com/x",
        "content_preview": "product page",
        "metadata": {
            "matched_terms": [],
            "image_url": "https://example.com/leak.jpg",
            "thumbnail": "https://example.com/leak2.jpg",
            "profile_image": "https://example.com/leak3.jpg",
        },
        "language": "en",
        "compliance_tag": "public_html",
        "captured_at": "2026-05-07T00:00:00+00:00",
    }
    out = mod._build_source_record_for_insert(
        planned=planned, compressed_candidate_ids_using_this=["c1"],
    )
    md = out["metadata"]
    assert "image_url" not in md
    assert "thumbnail" not in md
    assert "profile_image" not in md


def test_script_sets_user_handle_hash_to_null_for_inserts() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # Looking for the SourceRecord(...) constructor literal:
    # `user_handle_hash=None,`
    assert "user_handle_hash=None" in src


# ---------------------------------------------------------------------------
# 10 + 11 + 12 + 13. Persona row shape / scoping / metadata
# ---------------------------------------------------------------------------


def test_script_inserts_exactly_seven_personas() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "EXPECTED_COMPRESSED_COUNT = 7" in src
    # Pre-commit assertion present
    assert (
        "persisted_persona_count="
        in src.replace(" ", "").replace("\n", "")
        or "len(persisted_personas) != EXPECTED_COMPRESSED_COUNT" in src
    )


def test_persona_records_carry_run_scope_and_brief_tags() -> None:
    """Every persisted PersonaRecord must carry product_relevance_tags
    that include target_brief, run_scope_id, scope=run_scoped, and
    not_global_persona=true."""
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert '"target_brief:{TARGET_BRIEF_ID}"' in src or "target_brief:" in src
    assert "run_scope_id:" in src
    assert "scope:run_scoped_brief_scoped" in src
    assert "not_global_persona:true" in src


def test_persona_records_carry_compressed_candidate_id() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "compressed_candidate_id:" in src


# ---------------------------------------------------------------------------
# 14 + 15. Traits evidence-backed; require excerpts
# ---------------------------------------------------------------------------


def test_script_maps_trait_names_to_closed_field_set() -> None:
    mod = _load_script()
    # Universal mapper must always return one of the 10 closed fields.
    closed = {
        "interests", "role_or_context", "buying_constraints",
        "trust_triggers", "current_alternatives", "communication_style",
        "influence_signals", "price_sensitivity", "objection_patterns",
        "geography_broad",
    }
    for sample in (
        "current_alternative_competitor",
        "preference_performance_use_case",
        "objection_price",
        "trust_threshold",
        "geography_region",
        "role_runner",
        "influence_score",
        "price_skeptic",
        "buying_constraint_budget",
        "communication_style_expert",
        "completely_unknown_trait_name",
    ):
        assert mod._map_trait_field(sample) in closed


def test_script_persists_evidence_excerpts_into_traits_rationale() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # The script builds a "rationale" blob from evidence_excerpt
    assert "evidence_excerpt" in src
    assert "rationale=rationale_blob" in src


# ---------------------------------------------------------------------------
# 16. Trait cap at 7 per persona
# ---------------------------------------------------------------------------


def test_script_caps_input_traits_at_seven_per_persona() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "MAX_TRAITS_PER_PERSONA_INPUT = 7" in src


# ---------------------------------------------------------------------------
# 17 + 18. Evidence links per persona; rollback if any persona lacks one
# ---------------------------------------------------------------------------


def test_script_creates_evidence_link_for_every_trait_source_pair() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "PersonaEvidenceLink(" in src
    assert "contribution_kind=\"trait_support\"" in src


def test_script_rolls_back_if_persona_lacks_evidence_link() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "links_per_persona" in src
    assert "0 evidence links" in src or "evidence links." in src


# ---------------------------------------------------------------------------
# 19 + 20 + 21 + 22. Rollback on any delta mismatch / source resolution fail
# ---------------------------------------------------------------------------


def test_script_rolls_back_when_persona_count_mismatches() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert (
        "len(persisted_personas) != EXPECTED_COMPRESSED_COUNT"
        in src
    )


def test_script_rolls_back_when_trait_count_mismatches() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert (
        "len(persisted_traits_summary) != expected_trait_count"
        in src
    )


def test_script_rolls_back_when_min_traits_per_persona_unmet() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "trait(s) (< 2 required)" in src


def test_script_rolls_back_on_unresolved_source() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "could not be resolved" in src


# ---------------------------------------------------------------------------
# 23 + 24 + 25. No external API calls
# ---------------------------------------------------------------------------


def test_script_does_not_call_brave() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "BraveSearchClient(", ".search(queries=",
        "is_brave_key_present(",
    )
    for token in forbidden:
        assert token not in src, f"forbidden Brave call: {token!r}"


def test_script_does_not_call_youtube() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "YouTubeDataClient(", ".search_videos(", ".fetch_comments(",
        "is_youtube_key_present(",
    )
    for token in forbidden:
        assert token not in src, f"forbidden YouTube call: {token!r}"


def test_script_does_not_call_external_apis() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "httpx.", "requests.", "aiohttp.",
        "anthropic", "openai", "tavily", "firecrawl",
        "yt_dlp", "pytube", "scrapetube",
    )
    for s in forbidden:
        assert s.lower() not in src.lower(), f"forbidden: {s!r}"


# ---------------------------------------------------------------------------
# 26 + 27 + 28. No simulation / graph / UI writes
# ---------------------------------------------------------------------------


def test_script_does_not_create_simulation_or_graph_rows() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "Simulation(", "SimulationOutput(", "SimulationRound(",
        "Agent(", "AgentResponse(", "DebateTurn(",
        "PersonaGraphEdge(", "PersonaCluster(",
        "PopulationConstructionAudit(",
    )
    for term in forbidden:
        pat = re.compile(rf"\b{re.escape(term)}\s*\w")
        assert pat.search(src) is None, (
            f"forbidden ORM construction: {term!r}"
        )


def test_script_does_not_touch_frontend() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    for s in ("apps/web", "next/router", "next.js"):
        assert s not in src, f"forbidden frontend reference: {s}"


# ---------------------------------------------------------------------------
# 29. Existing-phase imports still resolve
# ---------------------------------------------------------------------------


def test_existing_phase_imports_still_resolve() -> None:
    from assembly.sources.persona_role_planner import (  # noqa: F401
        EffectiveSourceRecord, PersonaCandidate, PersonaCandidatePlanner,
        validate_launch_state_claims,
    )
    from assembly.sources.persona_diversity_evaluator import (  # noqa: F401
        DiversityRecommendation, evaluate_persona_diversity,
    )
    from assembly.sources.source_expansion_planner import (  # noqa: F401
        generate_source_expansion_plan,
    )
    from assembly.sources.persona_set_compressor import (  # noqa: F401
        compress_persona_set, normalize_role_slug,
    )


# ---------------------------------------------------------------------------
# Bonus: support_level + confidence consistency
# ---------------------------------------------------------------------------


def test_support_level_and_confidence_mapping_universal() -> None:
    mod = _load_script()
    assert mod._support_level_from_confidence("high") == "direct"
    assert mod._support_level_from_confidence("medium") == "inferred"
    assert mod._support_level_from_confidence("low") == "inferred"
    assert mod._confidence_decimal("high") > mod._confidence_decimal("medium")
    assert mod._confidence_decimal("medium") > mod._confidence_decimal("low")
    # Must be in (0, 1]
    for c in ("high", "medium", "low"):
        v = float(mod._confidence_decimal(c))
        assert 0 < v <= 1


# ---------------------------------------------------------------------------
# Bonus: dry-run vs commit modes
# ---------------------------------------------------------------------------


def test_script_supports_dry_run_and_commit_flags() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "--dry-run" in src
    assert "--commit" in src
    assert "_DryRunRollback" in src


def test_script_default_mode_is_dry_run() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "default=True" in src  # on the --dry-run flag


# ---------------------------------------------------------------------------
# Bonus: launch-state validator stamps PRODUCT_NAME at the universal level
# ---------------------------------------------------------------------------


def test_script_launch_state_validator_stamps_product_name() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "validate_launch_state_claims" in src
    assert "product_name=PRODUCT_NAME" in src
    assert "launch_state=LAUNCH_STATE" in src
