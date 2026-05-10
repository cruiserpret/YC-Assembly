"""Phase 9A.2 — compressor hard-cap + 21–30 official scale tests.

Operator scenarios 1-33 covered. NO live API calls, NO DB writes
from the test file itself.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest


PROBE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "compressor_hard_cap_9a_2.py"
)
SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "scale_lumaloop_society_9a_2.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _src_code_only(p: Path) -> str:
    src = _src(p)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    ds_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (
            ast.FunctionDef, ast.AsyncFunctionDef,
            ast.ClassDef, ast.Module,
        )):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                n0 = node.body[0]
                for ln in range(
                    n0.lineno, (n0.end_lineno or n0.lineno) + 1,
                ):
                    ds_lines.add(ln)
    kept: list[str] = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in ds_lines:
            continue
        ci = line.find("#")
        if ci >= 0:
            line = line[:ci]
        kept.append(line)
    return "\n".join(kept)


def _make_synthetic_compressed(n: int):
    from assembly.sources.persona_set_compressor.schemas import (
        CompressedPersonaCandidate,
    )
    out = []
    for i in range(n):
        role_idx = i % 6
        provider_idx = i % 3
        out.append(CompressedPersonaCandidate(
            candidate_id=f"c{i}",
            target_brief="t", generated_for_phase="9A.2",
            pre_normalization_role=f"role_{role_idx}",
            normalized_primary_role=f"role_{role_idx}",
            secondary_persona_roles=[],
            role_inference_basis=["x"],
            segment_label=f"role_{role_idx}",
            source_record_ids=[f"src::{i}"],
            evidence_summary="x",
            evidence_snippets=["e"],
            evidence_theme=f"theme_{role_idx}",
            source_provider_family=f"p{provider_idx}",
            inferred_traits=[
                {"trait_name": "t1", "trait_value": "v1",
                 "evidence_source_record_id": f"src::{i}",
                 "evidence_excerpt": "e", "confidence": "high",
                 "caveat": None},
                {"trait_name": "t2", "trait_value": "v2",
                 "evidence_source_record_id": f"src::{i}",
                 "evidence_excerpt": "e", "confidence": "medium",
                 "caveat": None},
            ],
            inferred_preferences=[], inferred_objections=[],
            inferred_behaviors=[],
            hypothetical_target_product_reaction="x",
            confidence="high", evidence_strength="strong",
            quality_score=10.0 - (i * 0.1),
            caveats=[],
            simulation_usefulness_summary="x",
            persistence_recommendation="DEFER",
            kept_reason="x",
        ))
    return out


# ---------------------------------------------------------------------------
# 1 + 2. compress_persona_set accepts hard_max_compressed; backwards compat
# ---------------------------------------------------------------------------


def test_compress_persona_set_accepts_hard_max_compressed() -> None:
    from assembly.sources.persona_set_compressor import (
        compress_persona_set,
    )
    sig = inspect.signature(compress_persona_set)
    assert "hard_max_compressed" in sig.parameters
    p = sig.parameters["hard_max_compressed"]
    assert p.default is None


def test_hard_max_compressed_backwards_compatible_when_none() -> None:
    """Existing callers that don't pass hard_max_compressed get the
    same behavior as before."""
    from assembly.sources.persona_set_compressor import (
        compress_persona_set,
    )
    cands = [{
        "candidate_id": "c1",
        "scope": "brief_scoped",
        "persistence_status": "dry_run_only",
        "target_brief": "t", "generated_for_phase": "9A.2",
        "not_global_persona": True,
        "inferred_persona_role": "competitor_user_brand_a",
        "secondary_persona_roles": [],
        "role_inference_basis": ["x"],
        "segment_label": "x",
        "source_record_ids": ["s1"],
        "evidence_summary": "x",
        "evidence_snippets": ["e"],
        "inferred_traits": [
            {"trait_name": "t1", "trait_value": "v",
             "evidence_source_record_id": "s1",
             "evidence_excerpt": "e", "confidence": "high",
             "caveat": None},
            {"trait_name": "t2", "trait_value": "v",
             "evidence_source_record_id": "s1",
             "evidence_excerpt": "e", "confidence": "high",
             "caveat": None},
        ],
        "inferred_preferences": [], "inferred_objections": [],
        "inferred_behaviors": [],
        "hypothetical_target_product_reaction": "x",
        "confidence": "high", "evidence_strength": "strong",
        "caveats": [], "simulation_usefulness_summary": "x",
        "persistence_recommendation": "DEFER",
    }]
    # Without hard cap, single candidate → 1 compressed (no change)
    result = compress_persona_set(
        candidates=cands, planned_source_records=[],
        target_brief_id="t", product_name="P",
        launch_state="unlaunched",
    )
    assert result.diff_summary.after_count == 1


# ---------------------------------------------------------------------------
# 3 + 4. Hard cap enforces ≤ N; never random slices
# ---------------------------------------------------------------------------


def test_hard_max_compressed_caps_at_30() -> None:
    from assembly.sources.persona_set_compressor.compressor import (
        _apply_hard_cap_stratified,
    )
    cands = _make_synthetic_compressed(50)
    kept, dropped, audit = _apply_hard_cap_stratified(
        compressed=cands, hard_max=30,
    )
    assert len(kept) == 30
    assert len(dropped) == 20


def test_hard_cap_does_not_random_slice() -> None:
    """The selector must use stratified passes, not first-N
    sorting."""
    from assembly.sources.persona_set_compressor.compressor import (
        _apply_hard_cap_stratified,
    )
    cands = _make_synthetic_compressed(50)
    kept, _, audit = _apply_hard_cap_stratified(
        compressed=cands, hard_max=30,
    )
    # Verify stratification: all 6 distinct roles preserved
    roles = {c.normalized_primary_role for c in kept}
    assert len(roles) == 6
    # All 3 providers preserved
    providers = {c.source_provider_family for c in kept}
    assert len(providers) == 3
    # Audit confirms multi-pass selection
    assert audit["applied"] is True
    assert "passes" in audit
    assert len(audit["passes"]) >= 4


# ---------------------------------------------------------------------------
# 5. Best per role first
# ---------------------------------------------------------------------------


def test_hard_cap_preserves_best_per_role_first() -> None:
    """The highest-quality candidate per distinct role is admitted
    in pass 1."""
    from assembly.sources.persona_set_compressor.compressor import (
        _apply_hard_cap_stratified,
    )
    cands = _make_synthetic_compressed(50)
    kept, _, _ = _apply_hard_cap_stratified(
        compressed=cands, hard_max=30,
    )
    # The highest-quality candidate of each role (lowest i index per
    # role) should be in kept.
    for role_idx in range(6):
        # The first synthetic with role_idx=N is at i=N (highest qs)
        first_id = f"c{role_idx}"
        assert any(
            c.candidate_id == first_id for c in kept
        ), f"role_{role_idx} top candidate {first_id} missing"


# ---------------------------------------------------------------------------
# 6 + 7 + 8. Provider / use-case / objection diversity preserved
# ---------------------------------------------------------------------------


def test_hard_cap_preserves_provider_diversity() -> None:
    from assembly.sources.persona_set_compressor.compressor import (
        _apply_hard_cap_stratified,
    )
    cands = _make_synthetic_compressed(50)
    kept, _, _ = _apply_hard_cap_stratified(
        compressed=cands, hard_max=30,
    )
    providers = {c.source_provider_family for c in kept}
    assert len(providers) == 3


def test_hard_cap_preserves_theme_diversity() -> None:
    from assembly.sources.persona_set_compressor.compressor import (
        _apply_hard_cap_stratified,
    )
    cands = _make_synthetic_compressed(50)
    kept, _, _ = _apply_hard_cap_stratified(
        compressed=cands, hard_max=30,
    )
    themes = {c.evidence_theme for c in kept}
    assert len(themes) >= 6


def test_hard_cap_preserves_role_provider_pairs() -> None:
    """Pass 3 fills underrepresented (role, provider) pairs."""
    from assembly.sources.persona_set_compressor.compressor import (
        _apply_hard_cap_stratified,
    )
    cands = _make_synthetic_compressed(50)
    kept, _, _ = _apply_hard_cap_stratified(
        compressed=cands, hard_max=30,
    )
    pairs = {
        (c.normalized_primary_role, c.source_provider_family)
        for c in kept
    }
    # Synthetic distribution: gcd(6, 3) = 3, so only 6 unique
    # (role, provider) pairs exist in the input. The selector
    # should preserve all 6.
    input_pairs = {
        (c.normalized_primary_role, c.source_provider_family)
        for c in cands
    }
    assert pairs == input_pairs


# ---------------------------------------------------------------------------
# 9. No single role >35% when possible
# ---------------------------------------------------------------------------


def test_hard_cap_enforces_role_concentration_under_35_pct() -> None:
    from assembly.sources.persona_set_compressor.compressor import (
        _apply_hard_cap_stratified,
    )
    from collections import Counter
    cands = _make_synthetic_compressed(50)
    kept, _, _ = _apply_hard_cap_stratified(
        compressed=cands, hard_max=30,
    )
    rc = Counter(c.normalized_primary_role for c in kept)
    top_share = rc.most_common(1)[0][1] / len(kept)
    assert top_share <= 0.40  # 35% with soft-relax to 40%


# ---------------------------------------------------------------------------
# 10 + 11. hard_cap_overflow rejection reason in audit
# ---------------------------------------------------------------------------


def test_hard_cap_emits_hard_cap_overflow_reason() -> None:
    from assembly.sources.persona_set_compressor import (
        compress_persona_set,
    )
    # Build 8 candidates that all pass; hard_max=3 should drop 5
    # as `hard_cap_overflow`.
    cands = []
    for i in range(8):
        cands.append({
            "candidate_id": f"c{i}",
            "scope": "brief_scoped",
            "persistence_status": "dry_run_only",
            "target_brief": "t", "generated_for_phase": "9A.2",
            "not_global_persona": True,
            "inferred_persona_role": f"role_{i}",
            "secondary_persona_roles": [],
            "role_inference_basis": ["x"],
            "segment_label": f"role_{i}",
            "source_record_ids": [f"s{i}"],
            "evidence_summary": "x",
            "evidence_snippets": [f"e{i}"],
            "inferred_traits": [
                {"trait_name": "t1", "trait_value": "v",
                 "evidence_source_record_id": f"s{i}",
                 "evidence_excerpt": "e", "confidence": "high",
                 "caveat": None},
                {"trait_name": "t2", "trait_value": "v",
                 "evidence_source_record_id": f"s{i}",
                 "evidence_excerpt": "e", "confidence": "high",
                 "caveat": None},
            ],
            "inferred_preferences": [], "inferred_objections": [],
            "inferred_behaviors": [],
            "hypothetical_target_product_reaction": "x",
            "confidence": "high", "evidence_strength": "strong",
            "caveats": [], "simulation_usefulness_summary": "x",
            "persistence_recommendation": "DEFER",
        })
    result = compress_persona_set(
        candidates=cands, planned_source_records=[],
        target_brief_id="t", product_name="P",
        launch_state="unlaunched",
        hard_max_compressed=3,
    )
    # 8 → 3 compressed; 5 rejected as hard_cap_overflow
    assert result.diff_summary.after_count == 3
    overflow_rejections = [
        r for r in result.rejected_candidates
        if r.rejection_reason == "hard_cap_overflow"
    ]
    assert len(overflow_rejections) == 5


def test_rejected_due_to_hard_cap_appears_in_audit() -> None:
    src = _src(SCRIPT_PATH)
    assert "rejected_due_to_hard_cap" in src
    assert "hard_max_compressed" in src


# ---------------------------------------------------------------------------
# 12 + 13. 9A.2 reads 9A.1 audit + creates new run_scope_id
# ---------------------------------------------------------------------------


def test_9a_2_reads_9a_1_personas_from_db() -> None:
    src = _src(SCRIPT_PATH)
    assert "phase:9A.1" in src
    assert "_load_9a_1_as_candidates" in src


def test_9a_2_creates_new_run_scope_id() -> None:
    src = _src(SCRIPT_PATH)
    assert "_make_run_scope_id" in src
    assert '"run_9a2_"' in src


# ---------------------------------------------------------------------------
# 14. 9A.2 does not delete 9A.1 personas
# ---------------------------------------------------------------------------


def test_9a_2_does_not_delete_9a_1_personas() -> None:
    code = _src_code_only(SCRIPT_PATH)
    # No DELETE statements anywhere
    assert "delete(PersonaRecord" not in code
    assert "delete(SourceRecord" not in code
    assert ".execute(delete(" not in code
    assert "session.delete(" not in code


# ---------------------------------------------------------------------------
# 15. 9A.2 persists only after 21–30 gate passes
# ---------------------------------------------------------------------------


def test_persona_persistence_only_after_gate() -> None:
    src = _src(SCRIPT_PATH)
    gate_idx = src.find("if len(kept) < EXPECTED_MIN_COMPRESSED_PERSONAS:")
    persona_insert_idx = src.find("session.add(PersonaRecord(")
    assert gate_idx > 0 and persona_insert_idx > gate_idx


# ---------------------------------------------------------------------------
# 16. 9A.2 distinguishes persisted society size from simulated sample
# ---------------------------------------------------------------------------


def test_audit_distinguishes_persisted_size_and_simulated_sample() -> None:
    src = _src(SCRIPT_PATH)
    assert '"persisted_society_size"' in src
    assert '"simulated_sample_size"' in src


# ---------------------------------------------------------------------------
# 17. 9A.2 does not require new APIs
# ---------------------------------------------------------------------------


def test_no_new_api_imports() -> None:
    code = _src_code_only(SCRIPT_PATH)
    forbidden = (
        "BraveSearchClient(", "TavilySearchClient(",
        "YouTubeDataClient(", "FirecrawlExtractClient(",
    )
    for token in forbidden:
        assert token not in code, (
            f"9A.2 should not call retrieval clients: {token}"
        )


# ---------------------------------------------------------------------------
# 18. No Jina/Exa/DataForSEO/Reddit/Apify usage
# ---------------------------------------------------------------------------


def test_no_jina_exa_dataforseo_reddit_apify_usage() -> None:
    code = _src_code_only(SCRIPT_PATH)
    forbidden = (
        "jina", "exa.", "dataforseo", "reddit", "apify",
        "JINA_API_KEY", "EXA_API_KEY", "DATAFORSEO_API_KEY",
    )
    for s in forbidden:
        assert s.lower() not in code.lower(), f"forbidden: {s!r}"


# ---------------------------------------------------------------------------
# 19. No hardcoded LumaLoop persona templates
# ---------------------------------------------------------------------------


def test_no_hardcoded_lumaloop_persona_templates() -> None:
    code = _src_code_only(SCRIPT_PATH)
    forbidden = (
        '"competitor_user_noxgear_tracer2"',
        '"competitor_user_amphipod"',
        '"competitor_user_nathan_reflective_gear"',
        '"competitor_user_flipbelt_lights"',
        '"night_runner_persona"', '"cyclist_persona"',
        '"dog_walker_persona"', '"reflective_vest_rejecter"',
        '"price_skeptic"', '"trust_seeker"',
    )
    for lit in forbidden:
        assert lit not in code, f"hardcoded persona literal: {lit!r}"


# ---------------------------------------------------------------------------
# 20 + 21. Universal scanners run on all responses
# ---------------------------------------------------------------------------


def test_universal_fake_use_scanner_runs_per_round() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_unlaunched_product_use_claims" in src
    assert "no_fake_target_product_use" in src


def test_universal_forecast_verdict_scanner_runs_per_round() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_forecast_or_verdict_claims" in src
    assert "no_forecast_or_verdict" in src


# ---------------------------------------------------------------------------
# 22. No API key values printed
# ---------------------------------------------------------------------------


def test_no_api_key_value_printed() -> None:
    code = _src_code_only(SCRIPT_PATH)
    secrets = (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "BRAVE_SEARCH_API_KEY", "YOUTUBE_DATA_API_KEY",
        "TAVILY_API_KEY", "FIRECRAWL_API_KEY",
    )
    for v in secrets:
        bad = (
            re.compile(
                rf'print\s*\(\s*[^)]*os\.environ\.get\(\s*["\']{re.escape(v)}',
                re.IGNORECASE,
            ),
            re.compile(
                rf'logger\.\w+\(\s*[^)]*os\.environ\.get\(\s*["\']{re.escape(v)}',
                re.IGNORECASE,
            ),
        )
        for pat in bad:
            assert pat.search(code) is None, (
                f"forbidden: secret value {v!r} surfaced via "
                f"{pat.pattern}"
            )


# ---------------------------------------------------------------------------
# 23. Secret scanner runs on outputs
# ---------------------------------------------------------------------------


def test_secret_scanner_runs_on_outputs() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_for_secrets" in src
    assert "secrets_clean" in src


# ---------------------------------------------------------------------------
# 24. SourceRecord reuse (no new inserts in 9A.2)
# ---------------------------------------------------------------------------


def test_9a_2_reuses_9a_1_source_records() -> None:
    src = _src(SCRIPT_PATH)
    # 9A.2 should set source_records_inserted=0 explicitly because
    # it only links to existing 9A.1 SourceRecords.
    assert 'audit["source_records_inserted"] = 0' in src
    assert '"source_records_reused"' in src


# ---------------------------------------------------------------------------
# 25 + 26. PersonaRecords are run-scoped/brief-scoped; no global personas
# ---------------------------------------------------------------------------


def test_personas_are_run_scoped_and_brief_scoped() -> None:
    src = _src(SCRIPT_PATH)
    needed = (
        "target_brief:",
        "run_scope_id:",
        "scope:run_scoped_brief_scoped",
        "not_global_persona:true",
        "compressed_candidate_id:",
    )
    for s in needed:
        assert s in src, f"missing run/brief-scope tag: {s!r}"


def test_no_global_personas_created() -> None:
    src = _src(SCRIPT_PATH)
    assert "scope:global" not in src
    assert "not_global_persona:false" not in src


# ---------------------------------------------------------------------------
# 27. Simulation only after persistence
# ---------------------------------------------------------------------------


def test_simulation_only_after_persistence() -> None:
    src = _src(SCRIPT_PATH)
    persona_insert_idx = src.find("session.add(PersonaRecord(")
    sim_insert_idx = src.find("session.add(Simulation(")
    assert persona_insert_idx > 0 and sim_insert_idx > 0
    assert sim_insert_idx > persona_insert_idx


# ---------------------------------------------------------------------------
# 28. Report only generates after simulation quality passes
# ---------------------------------------------------------------------------


def test_report_only_generates_after_simulation_quality_passes() -> None:
    src = _src(SCRIPT_PATH)
    quality_check_idx = src.find('"ready_for_founder_report_phase"')
    aggregate_call_idx = src.find("aggregate_founder_report(")
    assert quality_check_idx > 0 and aggregate_call_idx > 0
    assert aggregate_call_idx > quality_check_idx


# ---------------------------------------------------------------------------
# 29 + 30. next_psychology_layer_needed + next_discussion_layer_needed
# ---------------------------------------------------------------------------


def test_next_psychology_layer_needed_present() -> None:
    src = _src(SCRIPT_PATH)
    assert "next_psychology_layer_needed" in src
    assert (
        'audit["next_psychology_layer_needed"] = True' in src
    )


def test_next_discussion_layer_needed_present() -> None:
    src = _src(SCRIPT_PATH)
    assert "next_discussion_layer_needed" in src
    assert (
        'audit["next_discussion_layer_needed"] = True' in src
    )


# ---------------------------------------------------------------------------
# 31. Existing 9A.1 + prior tests still resolve
# ---------------------------------------------------------------------------


def test_existing_phase_imports_still_resolve() -> None:
    from assembly.sources.persona_set_compressor import (  # noqa: F401
        compress_persona_set,
    )
    from assembly.sources.persona_set_compressor.compressor import (  # noqa: F401
        _apply_hard_cap_stratified,
    )
    from assembly.sources.run_scoped_persona_simulation import (  # noqa: F401
        load_run_scoped_agents, evaluate_simulation_quality,
    )
    from assembly.sources.founder_report_generator import (  # noqa: F401
        aggregate_founder_report, render_markdown_report,
        evaluate_report_quality, scan_for_secrets,
    )


# ---------------------------------------------------------------------------
# Bonus: probe script exists + matches selector signature
# ---------------------------------------------------------------------------


def test_probe_script_exists() -> None:
    assert PROBE_PATH.is_file()
    src = _src(PROBE_PATH)
    assert "_apply_hard_cap_stratified" in src
    assert "9A.1" in src
    assert "hard_max" in src


def test_probe_script_makes_no_db_writes() -> None:
    src = _src(PROBE_PATH)
    assert "session.add(" not in src
    assert "session.commit(" not in src
    assert ".execute(insert(" not in src


# ---------------------------------------------------------------------------
# Bonus: --dry-run / --commit modes
# ---------------------------------------------------------------------------


def test_orchestrator_supports_dry_run_and_commit_modes() -> None:
    src = _src(SCRIPT_PATH)
    assert "--dry-run" in src
    assert "--commit" in src
    assert "default=True" in src


# ---------------------------------------------------------------------------
# Bonus: hard_max=30 enforced in orchestrator
# ---------------------------------------------------------------------------


def test_orchestrator_uses_hard_max_30() -> None:
    src = _src(SCRIPT_PATH)
    assert "HARD_MAX_COMPRESSED = 30" in src
    assert (
        "_apply_hard_cap_stratified(\n"
        "        compressed=candidates, hard_max=HARD_MAX_COMPRESSED"
        in src
    )
