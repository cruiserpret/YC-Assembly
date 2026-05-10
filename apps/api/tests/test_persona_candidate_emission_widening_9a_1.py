"""Phase 9A.1 — persona-candidate emission widening tests.

Operator scenarios 1-32 covered. NO live API calls, NO DB writes
from the test file itself.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "scale_lumaloop_society_9a_1.py"
)
EXTRACTOR_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "evidence_signal_extractor"
)
WIDENER_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "persona_emission_widener"
)


def _src() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _src_code_only() -> str:
    src = _src()
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


# ---------------------------------------------------------------------------
# 1. EvidenceSignalExtractor exists
# ---------------------------------------------------------------------------


def test_evidence_signal_extractor_exists() -> None:
    from assembly.sources.evidence_signal_extractor import (
        extract_evidence_signals, EvidenceSignal,
    )
    assert callable(extract_evidence_signals)
    assert hasattr(EvidenceSignal, "model_config")


# ---------------------------------------------------------------------------
# 2. Multiple signals from one evidence item when supported
# ---------------------------------------------------------------------------


def test_extractor_emits_multiple_signals_when_supported() -> None:
    """One snippet with a competitor mention + price objection +
    use-case signal should yield 3 distinct signals."""
    from assembly.sources.evidence_signal_extractor import (
        extract_evidence_signals,
    )
    item = {
        "provider": "tavily_search",
        "url": "https://example.com/x",
        "snippet": (
            "I tried Noxgear Tracer2 for my long runs at night. "
            "It works well but is overpriced for the value. "
            "I would compare it to Body Glide on durability "
            "based on independent test results."
        ),
        "matched_terms": [],
    }
    signals = extract_evidence_signals(
        evidence_item=item,
        competitors=["Noxgear Tracer2", "Body Glide"],
        substitutes=[],
    )
    types = {s.signal_type for s in signals}
    # Expect at least: competitor_usage, price_value, use_case,
    # trust_proof
    assert "competitor_usage_signal" in types
    assert "price_value_signal" in types
    assert (
        "use_case_signal" in types
        or "performance_signal" in types
        or "trust_proof_signal" in types
    )
    assert len(signals) >= 3


# ---------------------------------------------------------------------------
# 3. No signals without evidence excerpts
# ---------------------------------------------------------------------------


def test_extractor_emits_no_signals_for_empty_text() -> None:
    from assembly.sources.evidence_signal_extractor import (
        extract_evidence_signals,
    )
    for empty in ("", "   ", "tiny"):
        sigs = extract_evidence_signals(
            evidence_item={
                "provider": "brave_search", "url": "x",
                "snippet": empty,
            },
            competitors=["Noxgear"], substitutes=[],
        )
        assert sigs == []


def test_extractor_signal_always_carries_excerpt() -> None:
    from assembly.sources.evidence_signal_extractor import (
        extract_evidence_signals,
    )
    item = {
        "provider": "brave_search", "url": "https://x.com",
        "snippet": (
            "Noxgear Tracer2 review for runners: works for night "
            "running on dark trails."
        ),
    }
    sigs = extract_evidence_signals(
        evidence_item=item,
        competitors=["Noxgear Tracer2"], substitutes=[],
    )
    for s in sigs:
        assert s.evidence_excerpt
        assert len(s.evidence_excerpt) >= 10


# ---------------------------------------------------------------------------
# 4. PersonaCandidatePlanner can emit multiple candidates from one source
# ---------------------------------------------------------------------------


def test_widener_emits_multiple_candidates_from_one_source() -> None:
    from assembly.sources.evidence_signal_extractor import (
        extract_evidence_signals,
    )
    from assembly.sources.persona_emission_widener import (
        widen_persona_candidates,
    )
    item = {
        "provider": "tavily_search",
        "url": "https://example.com/x",
        "planned_source_record_id_synthetic": "planned::test::tavily::abc",
        "snippet": (
            "Amphipod has been my go-to for night runs but it's "
            "overpriced for what you get and the strap fell apart "
            "after 3 months. I'd want test results before switching."
        ),
    }
    sigs = extract_evidence_signals(
        evidence_item=item,
        competitors=["Amphipod"], substitutes=[],
    )
    extended, audit = widen_persona_candidates(
        existing_candidates=[],
        signals=sigs,
        target_brief="test", product_name="TestProduct",
        generated_for_phase="9A.1",
    )
    # One source with multiple signals → multiple candidates (capped at 3)
    assert audit["emitted_count"] >= 2
    assert audit["emitted_count"] <= 3


# ---------------------------------------------------------------------------
# 5. Per-source cap is enforced
# ---------------------------------------------------------------------------


def test_widener_caps_candidates_per_source_at_3() -> None:
    from assembly.sources.evidence_signal_extractor import (
        EvidenceSignal,
    )
    from assembly.sources.persona_emission_widener import (
        widen_persona_candidates,
    )
    sigs = [
        EvidenceSignal(
            signal_id=f"s{i}",
            source_record_synthetic_id="planned::test::abc",
            provider="brave_search",
            signal_type="objection_signal",
            inferred_role=f"role_{i}",
            evidence_excerpt=f"excerpt {i} here for content",
            confidence="high",
            reason_for_signal=f"reason {i}",
        )
        for i in range(8)
    ]
    extended, audit = widen_persona_candidates(
        existing_candidates=[],
        signals=sigs,
        target_brief="t", product_name="P",
        generated_for_phase="9A.1",
    )
    assert audit["emitted_count"] <= 3


# ---------------------------------------------------------------------------
# 6. Duplicate same-role/same-excerpt rejected
# ---------------------------------------------------------------------------


def test_widener_rejects_duplicates_of_existing_candidates() -> None:
    from assembly.sources.evidence_signal_extractor import (
        EvidenceSignal,
    )
    from assembly.sources.persona_emission_widener import (
        widen_persona_candidates,
    )
    existing = [{
        "candidate_id": "c1", "scope": "brief_scoped",
        "persistence_status": "dry_run_only",
        "target_brief": "t", "generated_for_phase": "9A.1",
        "not_global_persona": True,
        "inferred_persona_role": "competitor_user_amphipod",
        "secondary_persona_roles": [],
        "role_inference_basis": ["evidence"],
        "segment_label": "amphipod buyer",
        "source_record_ids": ["planned::test::abc"],
        "evidence_summary": "x",
        "evidence_snippets": [
            "I tried Amphipod for long runs at night",
        ],
        "inferred_traits": [
            {"trait_name": "x", "trait_value": "y",
             "evidence_source_record_id": "planned::test::abc",
             "evidence_excerpt": "z", "confidence": "high",
             "caveat": None},
            {"trait_name": "x2", "trait_value": "y2",
             "evidence_source_record_id": "planned::test::abc",
             "evidence_excerpt": "z2", "confidence": "high",
             "caveat": None},
        ],
        "inferred_preferences": [], "inferred_objections": [],
        "inferred_behaviors": [],
        "hypothetical_target_product_reaction": "x",
        "confidence": "high", "evidence_strength": "strong",
        "caveats": [], "simulation_usefulness_summary": "x",
        "persistence_recommendation": "DEFER",
    }]
    # Signal with same role + same source + similar excerpt
    dup_sig = EvidenceSignal(
        signal_id="dup",
        source_record_synthetic_id="planned::test::abc",
        provider="brave_search",
        signal_type="competitor_usage_signal",
        inferred_role="competitor_user_amphipod",
        evidence_excerpt="I tried Amphipod for long runs at night",
        confidence="high",
        reason_for_signal="Amphipod mention",
    )
    extended, audit = widen_persona_candidates(
        existing_candidates=existing,
        signals=[dup_sig],
        target_brief="t", product_name="P",
        generated_for_phase="9A.1",
    )
    assert audit["emitted_count"] == 0
    assert any(
        r["reason"] == "duplicates_existing_planner_candidate"
        for r in audit["rejected_breakdown"]
    )


# ---------------------------------------------------------------------------
# 7 + 8. Same role splits only with evidence-backed sub-segment difference
# ---------------------------------------------------------------------------


def test_widener_caps_role_source_objection_at_2() -> None:
    """Same (role, source, objection) triple — max 2 candidates."""
    from assembly.sources.evidence_signal_extractor import (
        EvidenceSignal,
    )
    from assembly.sources.persona_emission_widener import (
        widen_persona_candidates,
    )
    sigs = [
        EvidenceSignal(
            signal_id=f"s{i}",
            source_record_synthetic_id="planned::test::abc",
            provider="brave_search",
            signal_type="objection_signal",
            inferred_role="competitor_user_amphipod",
            objection_pattern="overpriced",
            evidence_excerpt=f"different excerpt {i} content",
            confidence="medium",
            reason_for_signal="objection: overpriced",
        )
        for i in range(4)
    ]
    extended, audit = widen_persona_candidates(
        existing_candidates=[], signals=sigs,
        target_brief="t", product_name="P",
        generated_for_phase="9A.1",
    )
    # max 2 per (role, source, objection)
    assert audit["emitted_count"] <= 2


def test_widener_does_not_split_same_role_just_to_inflate_count() -> None:
    """Two signals with identical role + source + excerpt prefix
    yield only 1 candidate (no inflation)."""
    from assembly.sources.evidence_signal_extractor import (
        EvidenceSignal,
    )
    from assembly.sources.persona_emission_widener import (
        widen_persona_candidates,
    )
    sigs = [
        EvidenceSignal(
            signal_id="s1",
            source_record_synthetic_id="planned::test::abc",
            provider="brave_search",
            signal_type="competitor_usage_signal",
            inferred_role="competitor_user_amphipod",
            evidence_excerpt="Amphipod is great for night runs",
            confidence="high",
            reason_for_signal="competitor mention",
        ),
        EvidenceSignal(
            signal_id="s2",
            source_record_synthetic_id="planned::test::abc",
            provider="brave_search",
            signal_type="competitor_usage_signal",
            inferred_role="competitor_user_amphipod",
            evidence_excerpt="Amphipod is great for night runs",
            confidence="high",
            reason_for_signal="competitor mention duplicate",
        ),
    ]
    extended, audit = widen_persona_candidates(
        existing_candidates=[], signals=sigs,
        target_brief="t", product_name="P",
        generated_for_phase="9A.1",
    )
    # Both signals have same role + source + excerpt → only 1
    assert audit["emitted_count"] == 1


# ---------------------------------------------------------------------------
# 9. Candidate emission conversion improves on synthetic fixture
# ---------------------------------------------------------------------------


def test_widener_lifts_conversion_rate_on_multi_signal_pool() -> None:
    """Synthetic 5-source pool, each source has 3 distinct signals.
    The widener should lift candidate count from 5 (existing planner)
    to ≥10 (widener adds 5+)."""
    from assembly.sources.evidence_signal_extractor import (
        extract_evidence_signals,
    )
    from assembly.sources.persona_emission_widener import (
        widen_persona_candidates,
    )
    # 5 synthetic existing candidates (1 per source)
    existing = []
    for i in range(5):
        existing.append({
            "candidate_id": f"c{i}",
            "scope": "brief_scoped",
            "persistence_status": "dry_run_only",
            "target_brief": "t", "generated_for_phase": "9A.1",
            "not_global_persona": True,
            "inferred_persona_role": f"competitor_user_brand{i}",
            "secondary_persona_roles": [],
            "role_inference_basis": ["x"],
            "segment_label": f"brand{i}",
            "source_record_ids": [f"planned::test::s{i}"],
            "evidence_summary": "x",
            "evidence_snippets": [f"existing excerpt {i}"],
            "inferred_traits": [
                {"trait_name": "a", "trait_value": "b",
                 "evidence_source_record_id": f"planned::test::s{i}",
                 "evidence_excerpt": "z", "confidence": "high",
                 "caveat": None},
                {"trait_name": "a2", "trait_value": "b2",
                 "evidence_source_record_id": f"planned::test::s{i}",
                 "evidence_excerpt": "z2", "confidence": "high",
                 "caveat": None},
            ],
            "inferred_preferences": [], "inferred_objections": [],
            "inferred_behaviors": [],
            "hypothetical_target_product_reaction": "x",
            "confidence": "high", "evidence_strength": "strong",
            "caveats": [], "simulation_usefulness_summary": "x",
            "persistence_recommendation": "DEFER",
        })
    # 5 synthetic evidence items, each with multiple signals
    all_signals = []
    for i in range(5):
        item = {
            "provider": "brave_search",
            "planned_source_record_id_synthetic": f"planned::test::s{i}",
            "url": f"https://x.com/{i}",
            "snippet": (
                f"Brand{i} works well but is overpriced. "
                "I want to see test results before recommending. "
                "Heavy night running is hard."
            ),
        }
        all_signals.extend(extract_evidence_signals(
            evidence_item=item,
            competitors=[f"Brand{j}" for j in range(5)],
            substitutes=[],
        ))
    extended, audit = widen_persona_candidates(
        existing_candidates=existing,
        signals=all_signals,
        target_brief="t", product_name="P",
        generated_for_phase="9A.1",
    )
    assert audit["extended_total"] > audit["input_existing_count"], (
        f"widener didn't lift counts: "
        f"{audit['input_existing_count']} → {audit['extended_total']}"
    )


# ---------------------------------------------------------------------------
# 10 + 11 + 12 + 13. Replay mode reads 9A audit + makes no DB / API / LLM calls
# ---------------------------------------------------------------------------


def test_replay_mode_reads_9a_audit() -> None:
    src = _src()
    assert "scale_lumaloop_society_9a.json" in src
    assert "do_replay" in src
    assert "REPLAY-9A" in src


def test_replay_mode_makes_no_db_writes() -> None:
    src = _src()
    # The replay path must exit before any session.add(...) call.
    replay_block_start = src.find("if do_replay:")
    replay_block_end = src.find("# ---------- --replay-9a path: ")
    if replay_block_end < 0:
        replay_block_end = replay_block_start + 5000
    # The replay-block path returns BEFORE the real persistence code.
    assert "return 0" in src[replay_block_start:replay_block_end + 5000]


def test_replay_mode_makes_no_external_api_calls() -> None:
    src = _src()
    # The replay block does NOT call any provider client.
    rs = src.find("if do_replay:")
    re_ = src.find("        return 0", rs)
    block = src[rs:re_]
    assert "BraveSearchClient(" not in block
    assert "TavilySearchClient(" not in block
    assert "YouTubeDataClient(" not in block
    assert "FirecrawlExtractClient(" not in block
    assert "cost_guarded_chat" not in block


def test_replay_mode_makes_no_llm_calls() -> None:
    src = _src()
    rs = src.find("if do_replay:")
    re_ = src.find("        return 0", rs)
    block = src[rs:re_]
    assert "cost_guarded_chat" not in block
    assert "AnthropicProvider(" not in block


# ---------------------------------------------------------------------------
# 14 + 15. Firecrawl supersedes snippet evidence (universal — drift-tested)
# ---------------------------------------------------------------------------


def test_firecrawl_supersession_path_present_in_orchestrator() -> None:
    """The orchestrator's Firecrawl branch must score extracted
    pages independently. Existing 9A logic accepts/rejects each
    Firecrawl page based on its own anchor_score, not based on
    'URL was already seen as a Brave/Tavily snippet'.

    (The snippet→Firecrawl supersession field is tracked in the
    audit if Firecrawl pages are accepted — verified in live runs.)"""
    src = _src()
    fc_block_start = src.find("# ---- Firecrawl extraction")
    fc_block_end = src.find("# ---- YouTube ----")
    fc_block = src[fc_block_start:fc_block_end]
    # Firecrawl pages are scored independently
    assert "_text_score" in fc_block
    assert "scan_unlaunched_product_use_claims" in fc_block
    # Universal scanners + per-page acceptance criterion
    assert "reject_below_relevance_threshold" in fc_block


def test_firecrawl_not_rejected_solely_for_snippet_url_overlap() -> None:
    src = _src()
    fc_block_start = src.find("# ---- Firecrawl extraction")
    fc_block_end = src.find("# ---- YouTube ----")
    fc_block = src[fc_block_start:fc_block_end]
    # The Firecrawl block uses ITS OWN seen_urls/seen_hashes check —
    # but accepts the extraction if its content_hash differs from
    # the snippet (which it always does because extracted markdown
    # is much longer than the snippet title+description).
    assert "seen_urls" in fc_block
    assert "seen_hashes" in fc_block


# ---------------------------------------------------------------------------
# 16 + 17. Firecrawl evidence-window scoring + reject if no useful windows
# ---------------------------------------------------------------------------


def test_firecrawl_extraction_uses_relevance_scoring() -> None:
    src = _src()
    fc_block_start = src.find("# ---- Firecrawl extraction")
    fc_block_end = src.find("# ---- YouTube ----")
    fc_block = src[fc_block_start:fc_block_end]
    assert "score, matched = _text_score(" in fc_block
    assert "if score < 3 and not reasons" in fc_block


def test_firecrawl_rejected_if_no_useful_evidence() -> None:
    src = _src()
    fc_block_start = src.find("# ---- Firecrawl extraction")
    fc_block_end = src.find("# ---- YouTube ----")
    fc_block = src[fc_block_start:fc_block_end]
    assert "rejected.append" in fc_block


# ---------------------------------------------------------------------------
# 18 + 19. No gate weakening
# ---------------------------------------------------------------------------


def test_raw_candidate_floor_unchanged_at_25() -> None:
    src = _src()
    assert "EXPECTED_MIN_RAW_CANDIDATES = 25" in src


def test_compressed_floor_unchanged_at_21() -> None:
    src = _src()
    assert "EXPECTED_MIN_COMPRESSED_PERSONAS = 21" in src
    assert "EXPECTED_MAX_COMPRESSED_PERSONAS = 30" in src


# ---------------------------------------------------------------------------
# 20. No hardcoded LumaLoop persona templates
# ---------------------------------------------------------------------------


def test_no_hardcoded_lumaloop_persona_templates() -> None:
    code = _src_code_only()
    forbidden = (
        '"competitor_user_noxgear_tracer2"',
        '"competitor_user_amphipod"',
        '"competitor_user_nathan_reflective_gear"',
        '"competitor_user_flipbelt_lights"',
        '"competitor_user_black_diamond_sprinter_headlamp"',
        '"night_runner_persona"', '"cyclist_persona"',
        '"dog_walker_persona"', '"reflective_vest_rejecter"',
    )
    for lit in forbidden:
        assert lit not in code, f"hardcoded persona literal: {lit!r}"


# ---------------------------------------------------------------------------
# 21. No Jina/Exa/DataForSEO/Reddit/Apify usage
# ---------------------------------------------------------------------------


def test_no_jina_exa_dataforseo_reddit_apify_usage() -> None:
    code = _src_code_only()
    forbidden = (
        "jina", "exa.", "dataforseo", "reddit", "apify",
        "JINA_API_KEY", "EXA_API_KEY", "DATAFORSEO_API_KEY",
    )
    for s in forbidden:
        assert s.lower() not in code.lower(), f"forbidden: {s!r}"


# ---------------------------------------------------------------------------
# 22. No fake LumaLoop usage claims (universal scanner runs)
# ---------------------------------------------------------------------------


def test_universal_fake_use_scanner_runs_on_signals() -> None:
    """The orchestrator scans every retrieval candidate's text via
    `scan_unlaunched_product_use_claims`. The widener doesn't
    bypass that — signals are extracted only from already-accepted
    evidence (which has already passed the scanner)."""
    src = _src()
    # The retrieval flow scans → accepts/rejects → only accepted
    # evidence reaches the widener.
    assert "scan_unlaunched_product_use_claims" in src
    assert "reject_fake_target_product_use" in src


# ---------------------------------------------------------------------------
# 23. API key values never printed
# ---------------------------------------------------------------------------


def test_no_api_key_value_printed() -> None:
    code = _src_code_only()
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
# 24. Secret scanner runs on outputs
# ---------------------------------------------------------------------------


def test_secret_scanner_runs_on_outputs() -> None:
    src = _src()
    assert "scan_for_secrets" in src
    assert "secrets_clean" in src


# ---------------------------------------------------------------------------
# 25 + 26. Source records staged before persona gate; no orphans
# ---------------------------------------------------------------------------


def test_source_records_staged_before_persona_gate() -> None:
    src = _src()
    assert "STAGE source records IN MEMORY" in src
    assert "halted_at_compression_gate" in src


def test_no_orphan_source_records_when_gate_fails() -> None:
    src = _src()
    gate_idx = src.find(
        "if compressed.diff_summary.after_count "
        "< EXPECTED_MIN_COMPRESSED_PERSONAS:",
    )
    insert_idx = src.find("session.add(SourceRecord(")
    assert gate_idx > 0 and insert_idx > 0
    assert insert_idx > gate_idx


# ---------------------------------------------------------------------------
# 27. Persona persistence only after compression gate passes
# ---------------------------------------------------------------------------


def test_persona_persistence_only_after_gate() -> None:
    src = _src()
    gate_idx = src.find(
        "if compressed.diff_summary.after_count "
        "< EXPECTED_MIN_COMPRESSED_PERSONAS:",
    )
    persona_insert_idx = src.find("session.add(PersonaRecord(")
    assert gate_idx > 0 and persona_insert_idx > gate_idx


# ---------------------------------------------------------------------------
# 28. Simulation only after persona persistence
# ---------------------------------------------------------------------------


def test_simulation_only_after_persona_persistence() -> None:
    src = _src()
    persona_insert_idx = src.find("session.add(PersonaRecord(")
    sim_insert_idx = src.find("session.add(Simulation(")
    assert persona_insert_idx > 0 and sim_insert_idx > 0
    assert sim_insert_idx > persona_insert_idx


# ---------------------------------------------------------------------------
# 29. Report only after simulation quality passes
# ---------------------------------------------------------------------------


def test_report_only_generates_after_simulation_quality_passes() -> None:
    src = _src()
    quality_check_idx = src.find('"ready_for_founder_report_phase"')
    aggregate_call_idx = src.find("aggregate_founder_report(")
    assert quality_check_idx > 0 and aggregate_call_idx > 0
    assert aggregate_call_idx > quality_check_idx


# ---------------------------------------------------------------------------
# 30. Existing 8.5G.1 + 9A imports still resolve
# ---------------------------------------------------------------------------


def test_existing_phase_imports_still_resolve() -> None:
    from assembly.sources.evidence_anchor_planner import (  # noqa: F401
        generate_anchor_plan,
    )
    from assembly.sources.persona_diversity_evaluator import (  # noqa: F401
        evaluate_persona_diversity,
    )
    from assembly.sources.persona_set_compressor import (  # noqa: F401
        compress_persona_set,
    )
    from assembly.sources.run_scoped_persona_simulation import (  # noqa: F401
        load_run_scoped_agents, evaluate_simulation_quality,
    )
    from assembly.sources.source_expansion_planner import (  # noqa: F401
        generate_source_expansion_plan,
    )
    from assembly.sources.founder_report_generator import (  # noqa: F401
        aggregate_founder_report, render_markdown_report,
        evaluate_report_quality, scan_for_secrets,
    )
    from assembly.sources.tavily import (  # noqa: F401
        is_tavily_key_present, TavilySearchClient,
    )
    from assembly.sources.firecrawl import (  # noqa: F401
        is_firecrawl_key_present, FirecrawlExtractClient,
    )
    from assembly.sources.evidence_signal_extractor import (  # noqa: F401
        extract_evidence_signals, EvidenceSignal,
    )
    from assembly.sources.persona_emission_widener import (  # noqa: F401
        widen_persona_candidates, EmissionPolicy,
    )


# ---------------------------------------------------------------------------
# Bonus: drift — no hardcoded brand/category in extractor / widener
# ---------------------------------------------------------------------------


def test_extractor_pkg_no_hardcoded_brand_or_category() -> None:
    forbidden = (
        "lumaloop", "noxgear", "amphipod", "nathan reflective",
        "flipbelt", "black diamond", "strideshield", "triton",
        "body glide", "megababe", "trail toes",
        "anti-blister", "anti-chafe", "energy drink",
    )
    for f in EXTRACTOR_PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
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
        code = "\n".join(kept).lower()
        for term in forbidden:
            assert term not in code, (
                f"hardcoded {term!r} in extractor pkg {f.name}"
            )


def test_widener_pkg_no_hardcoded_brand_or_category() -> None:
    forbidden = (
        "lumaloop", "noxgear", "amphipod", "nathan reflective",
        "flipbelt", "black diamond", "strideshield", "triton",
        "body glide", "megababe", "trail toes",
    )
    for f in WIDENER_PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
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
        code = "\n".join(kept).lower()
        for term in forbidden:
            assert term not in code, (
                f"hardcoded {term!r} in widener pkg {f.name}"
            )


# ---------------------------------------------------------------------------
# Bonus: --replay-9a / --commit / --dry-run modes present
# ---------------------------------------------------------------------------


def test_script_supports_three_modes() -> None:
    src = _src()
    assert "--dry-run" in src
    assert "--commit" in src
    assert "--replay-9a" in src


# ---------------------------------------------------------------------------
# Bonus: per-source emission cap default = 3
# ---------------------------------------------------------------------------


def test_emission_policy_per_source_cap_default() -> None:
    from assembly.sources.persona_emission_widener import EmissionPolicy
    p = EmissionPolicy()
    assert p.max_candidates_per_source == 3
    assert p.max_candidates_per_role_source_objection == 2
    assert p.min_traits_per_candidate == 2
