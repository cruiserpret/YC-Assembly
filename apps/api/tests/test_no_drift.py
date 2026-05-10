"""Architecture-Critic mechanical drift tests.

These exist as a tripwire: if anyone adds a forced verdict, fake metric, or
objective sentiment phrase to a *prompt template* or output schema, this
test fails.

The validator already covers runtime *output* checking. This file checks the
prompts and source code themselves don't accidentally encode bad patterns.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "assembly"
    / "prompts"
)


def _read_all_prompts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in PROMPTS_DIR.glob("*.md")}


# ---------------------------------------------------------------------------
# prompts must FORBID bad patterns; they may MENTION them in negative form
# (i.e. "do not invent ... do not output verdict words"). We check that the
# prompt does NOT frame the LLM's *job* as producing those things.
# ---------------------------------------------------------------------------


def test_prompts_dont_instruct_llm_to_produce_verdicts() -> None:
    """Prompts must not say things like 'output verdict' or 'recommend kill'."""
    bad_instructions = [
        re.compile(r"\boutput\s+(?:a\s+)?verdict\b", re.IGNORECASE),
        re.compile(r"\brecommend\s+(?:building|killing|pivoting)\b", re.IGNORECASE),
        re.compile(r"\bdecide\s+(?:to\s+)?(?:build|kill|pivot)\b", re.IGNORECASE),
        re.compile(r"\bgive\s+(?:a\s+)?(?:final\s+)?verdict\b", re.IGNORECASE),
    ]
    prompts = _read_all_prompts()
    for name, text in prompts.items():
        for pat in bad_instructions:
            m = pat.search(text)
            assert m is None, (
                f"prompt {name!r} contains a verdict-instructing phrase: {m.group(0)!r}"
            )


def test_prompts_dont_instruct_llm_to_invent_metrics() -> None:
    """Prompts must not ask the LLM to predict CTR/CAC/conversion-rate/etc."""
    bad = [
        re.compile(r"\bpredict\s+(?:the\s+)?(?:CTR|CAC|conversion|revenue)\b", re.IGNORECASE),
        re.compile(r"\bestimate\s+(?:the\s+)?(?:CTR|CAC|conversion\s+rate)\b", re.IGNORECASE),
        re.compile(r"\bforecast\s+(?:the\s+)?(?:sales|revenue|conversion)\b", re.IGNORECASE),
    ]
    prompts = _read_all_prompts()
    for name, text in prompts.items():
        for pat in bad:
            m = pat.search(text)
            assert m is None, (
                f"prompt {name!r} instructs the LLM to forecast/predict: {m.group(0)!r}"
            )


def test_prompts_explicitly_forbid_invention() -> None:
    """Both intake_parser.md and evidence_extractor.md must contain the
    phrase 'never invent' or equivalent strong language so the LLM is on
    notice."""
    prompts = _read_all_prompts()
    for required in ("intake_parser.md", "evidence_extractor.md"):
        text = prompts.get(required, "")
        # must say one of these
        markers = [
            "never invent",
            "do not invent",
            "no invention",
            "must not invent",
        ]
        present = any(m.lower() in text.lower() for m in markers)
        assert present, f"{required} must explicitly forbid invention; current text does not."


def test_prompts_explicitly_forbid_objective_sentiment() -> None:
    """At least one of the canonical forbidden objective-sentiment phrases
    must appear as a forbidden example in intake_parser.md."""
    prompts = _read_all_prompts()
    text = prompts.get("intake_parser.md", "").lower()
    # The intake parser prompt must call out objective-sentiment as forbidden.
    markers = [
        "objective sentiment",
        "the market is positive",
        "customers want this",
    ]
    present = any(m in text for m in markers)
    assert present, (
        "intake_parser.md must explicitly forbid objective sentiment phrasings; "
        "found none of: " + ", ".join(markers)
    )


def test_pio_schema_has_no_numeric_forecast_fields() -> None:
    """The ProductIntelligenceObject schema must not have any field whose
    name implies a numeric forecast."""
    from assembly.schemas.product_intelligence import ProductIntelligenceObject

    fields = ProductIntelligenceObject.model_fields.keys()
    forbidden = {
        "ctr", "cac", "ltv", "mrr", "arr", "roi", "conversion_rate",
        "revenue_forecast", "expected_ctr", "expected_cac", "predicted_revenue",
    }
    for f in fields:
        assert f.lower() not in forbidden, (
            f"PIO schema must not have numeric-forecast field {f!r}"
        )


def test_evidence_kinds_separate_real_from_simulated() -> None:
    """The EVIDENCE_KINDS constant must contain direct/analogical/missing —
    the three-way split that prevents real-vs-simulated confusion."""
    from assembly.models.evidence import EVIDENCE_KINDS

    assert set(EVIDENCE_KINDS) == {"direct", "analogical", "missing"}


# ---------------------------------------------------------------------------
# Phase 6 — O1 enforcement tripwire
# ---------------------------------------------------------------------------


_SIMULATION_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "simulation"
)


# The single file allowed to call provider.chat / provider.structured_output
# inside pipeline/simulation/. Any other file calling them is a violation
# of standing entry condition O1 (every orchestrated LLM call must go
# through with_cost_guard via call_llm_for_simulation).
_BLESSED_LLM_FILE = "call_llm.py"


_FORBIDDEN_METHOD_NAMES = {"chat", "structured_output"}


def _find_forbidden_calls_ast(py_file: Path) -> list[tuple[int, str]]:
    """AST-based scan for forbidden LLM-call shapes. Returns
    (line_no, snippet) for any `Call` node whose `.func` is an `Attribute`
    with `.attr` in `_FORBIDDEN_METHOD_NAMES`.

    Using the AST means docstring code-examples (e.g.
    `provider.chat(messages, ctx)` inside a triple-quoted string) DO NOT
    register — only real function calls do."""
    import ast

    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_METHOD_NAMES:
                try:
                    snippet = ast.unparse(node)[:120]
                except Exception:
                    snippet = f"<{func.attr} call>"
                offenders.append((node.lineno, snippet))
    return offenders


def test_simulation_pipeline_uses_only_blessed_helper() -> None:
    """O1 tripwire (AST-based): only `call_llm.py` may invoke
    `<x>.chat(...)` / `<x>.structured_output(...)` inside
    `pipeline/simulation/`. Other files must go through
    `call_llm_for_simulation`."""
    if not _SIMULATION_PKG.exists():
        pytest.skip("simulation package not yet created")

    offenders: list[str] = []
    for py_file in _SIMULATION_PKG.rglob("*.py"):
        if py_file.name == _BLESSED_LLM_FILE:
            continue
        for ln, snippet in _find_forbidden_calls_ast(py_file):
            offenders.append(
                f"{py_file.relative_to(_SIMULATION_PKG)}:{ln}: {snippet}"
            )

    assert not offenders, (
        "Direct LLM calls found in pipeline/simulation/ outside call_llm.py "
        "(O1 violation). Route every LLM call through "
        "call_llm_for_simulation:\n  " + "\n  ".join(offenders)
    )


def test_phase_6_does_not_write_simulation_outputs() -> None:
    """Phase 6 is raw simulation state. Phase 7 writes simulation_outputs.
    If anything in pipeline/simulation/ creates a SimulationOutput row,
    that's a violation of the layering."""
    if not _SIMULATION_PKG.exists():
        pytest.skip("simulation package not yet created")

    forbidden = [
        re.compile(r"\bSimulationOutput\("),
        re.compile(r"\bsession\.add\(SimulationOutput"),
    ]
    offenders: list[str] = []
    for py_file in _SIMULATION_PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pat in forbidden:
            if pat.search(text):
                offenders.append(str(py_file.relative_to(_SIMULATION_PKG)))
                break

    assert not offenders, (
        "Phase 6 must not write simulation_outputs (that's Phase 7). "
        f"Offending files: {offenders}"
    )


# ---------------------------------------------------------------------------
# Phase 6.5 — extend O1 tripwire to workers/ + pipeline/orchestration.py
# ---------------------------------------------------------------------------


_WORKERS_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "workers"
)
_ORCHESTRATION_FILE = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "orchestration.py"
)


def test_workers_package_uses_only_blessed_helper() -> None:
    """O1 tripwire (extended, AST-based): no direct LLM calls in `workers/`.
    The worker is glue — every LLM call must go through
    `call_llm_for_simulation` inside the orchestrator → simulation modules."""
    if not _WORKERS_PKG.exists():
        pytest.skip("workers package not yet created")

    offenders: list[str] = []
    for py_file in _WORKERS_PKG.rglob("*.py"):
        for ln, snippet in _find_forbidden_calls_ast(py_file):
            offenders.append(
                f"{py_file.relative_to(_WORKERS_PKG)}:{ln}: {snippet}"
            )
    assert not offenders, (
        "Direct LLM calls in workers/ (O1 violation). "
        "Route every LLM call through call_llm_for_simulation via the "
        "orchestrator:\n  " + "\n  ".join(offenders)
    )


def test_orchestration_uses_only_blessed_helper() -> None:
    """O1 tripwire (extended, AST-based): no direct LLM calls in
    pipeline/orchestration.py."""
    if not _ORCHESTRATION_FILE.is_file():
        pytest.skip("orchestration.py not yet created")
    offenders = _find_forbidden_calls_ast(_ORCHESTRATION_FILE)
    assert not offenders, (
        "Direct LLM calls in pipeline/orchestration.py (O1 violation). "
        "Orchestrator must delegate:\n  "
        + "\n  ".join(f"L{ln}: {snippet}" for ln, snippet in offenders)
    )


def test_phase_6_5_does_not_write_simulation_outputs() -> None:
    """Phase 7 has not shipped — the orchestrator + worker + aggregation
    stub must NOT write simulation_outputs rows."""
    paths = [_WORKERS_PKG, _ORCHESTRATION_FILE]
    aggregation_init = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "pipeline" / "aggregation" / "__init__.py"
    )
    paths.append(aggregation_init)
    forbidden = [
        re.compile(r"\bSimulationOutput\s*\("),
        re.compile(r"\bsession\.add\(SimulationOutput"),
    ]
    offenders: list[str] = []
    for p in paths:
        if not p.exists():
            continue
        files = p.rglob("*.py") if p.is_dir() else [p]
        for f in files:
            text = f.read_text(encoding="utf-8")
            for pat in forbidden:
                if pat.search(text):
                    offenders.append(str(f))
                    break
    assert not offenders, (
        "Phase 7 has not shipped — orchestrator/worker/aggregation stub "
        "must NOT write simulation_outputs rows. Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Phase 6.6 — universal AST tripwire across the entire pipeline/ package
# ---------------------------------------------------------------------------
# After 6.6, no file under `pipeline/` may call `<provider>.chat(...)` or
# `<provider>.structured_output(...)` directly. Every LLM call routes through
# `assembly.llm.guarded_chat.cost_guarded_chat`, which itself wraps
# `with_cost_guard` (Postgres row lock + cap check + log row). This single
# entry point is the structural enforcement of standing entry condition O1
# AND the foundation Phase 6.75 (RAG / Evidence Graph) builds on — every new
# RAG call site is auto-covered by this AST scan from day one.


_PIPELINE_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline"
)


def test_no_direct_provider_calls_anywhere_in_pipeline() -> None:
    """Universal O1 tripwire (AST-based): scan EVERY `.py` file under
    `pipeline/` for `<x>.chat(...)` / `<x>.structured_output(...)` calls.

    The blessed simulation file `pipeline/simulation/call_llm.py` is also
    scanned — after 6.6 it delegates to `cost_guarded_chat` and contains
    zero direct provider calls. Every other pipeline file must use
    `cost_guarded_chat`."""
    if not _PIPELINE_PKG.exists():
        pytest.skip("pipeline package not yet created")

    offenders: list[str] = []
    for py_file in _PIPELINE_PKG.rglob("*.py"):
        for ln, snippet in _find_forbidden_calls_ast(py_file):
            offenders.append(
                f"{py_file.relative_to(_PIPELINE_PKG)}:{ln}: {snippet}"
            )

    assert not offenders, (
        "Direct LLM calls in pipeline/ (Phase 6.6 O1 violation). "
        "Route every LLM call through "
        "`assembly.llm.guarded_chat.cost_guarded_chat`:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Phase 6.75 — embed AST tripwire
# ---------------------------------------------------------------------------
# After 6.75, no file under `pipeline/` may call `<x>.embed(...)` directly.
# Embeddings flow through `assembly.llm.guarded_chat.cost_guarded_embed`,
# which writes to `llm_call_log` like any other guarded call.


def _find_embed_calls_ast(py_file) -> list[tuple[int, str]]:
    import ast

    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "embed":
                # Match only when the receiver looks like an embedding
                # provider — i.e., the attribute is `.embed`. Capture all,
                # then exclude if the file is the cost_guarded_embed wrapper
                # itself (it MUST call provider.embed by design).
                try:
                    snippet = ast.unparse(node)[:120]
                except Exception:
                    snippet = "<embed call>"
                offenders.append((node.lineno, snippet))
    return offenders


def test_no_direct_embed_calls_in_pipeline() -> None:
    """Phase 6.75 — universal embed tripwire.

    Direct `.embed(...)` calls anywhere in `pipeline/` are forbidden.
    Use `assembly.llm.guarded_chat.cost_guarded_embed` so every embedding
    call writes a row to `llm_call_log` and respects the cost cap.

    The blessed paths are `assembly/embeddings/*` (the providers themselves)
    and `assembly/llm/guarded_chat.py` (the wrapper that calls them). The
    scan is restricted to `pipeline/` so those blessed files aren't seen.
    """
    if not _PIPELINE_PKG.exists():
        pytest.skip("pipeline package not yet created")

    offenders: list[str] = []
    for py_file in _PIPELINE_PKG.rglob("*.py"):
        for ln, snippet in _find_embed_calls_ast(py_file):
            offenders.append(
                f"{py_file.relative_to(_PIPELINE_PKG)}:{ln}: {snippet}"
            )
    assert not offenders, (
        "Direct .embed(...) calls in pipeline/ (Phase 6.75 violation). "
        "Route every embedding call through "
        "`assembly.llm.guarded_chat.cost_guarded_embed`:\n  "
        + "\n  ".join(offenders)
    )


def test_evidence_graph_does_not_fetch_live_pages() -> None:
    """Phase 6.75 — graph builder is read-only over evidence_items.
    No new network fetches, no httpx, no live retrieval. Cutoff-date
    safety relies on this."""
    graph_pkg = _PIPELINE_PKG / "evidence_graph"
    if not graph_pkg.exists():
        pytest.skip("evidence_graph package not yet created")
    forbidden = [
        re.compile(r"\bfetch_url\("),
        re.compile(r"\bhttpx\.(get|post|head)\("),
        re.compile(r"\brequests\.(get|post|head)\("),
        re.compile(r"\bSearchProvider\("),
        re.compile(r"\bExtractionProvider\("),
    ]
    offenders: list[str] = []
    for py_file in graph_pkg.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pat in forbidden:
            if pat.search(text):
                offenders.append(f"{py_file.name}: {pat.pattern}")
    assert not offenders, (
        "evidence_graph package must not fetch live pages. Offenders: "
        + ", ".join(offenders)
    )


def test_round_prompts_dont_instruct_forecasting_or_verdicts() -> None:
    """Round prompts must not ASK the LLM to produce verdicts or forecasts.
    (The runtime validator catches output; this is a prompt-side check that
    the prompt itself doesn't push the LLM in that direction.)"""
    rounds_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "prompts"
    )
    bad = [
        re.compile(r"\bpredict\s+(?:CTR|CAC|conversion|revenue)", re.IGNORECASE),
        re.compile(r"\brecommend\s+(?:building|killing|pivoting)", re.IGNORECASE),
        re.compile(r"\boutput\s+(?:a\s+)?verdict", re.IGNORECASE),
        re.compile(r"\bdecide\s+(?:to\s+)?(?:build|kill|pivot)", re.IGNORECASE),
    ]
    for path in rounds_dir.glob("round_*.md"):
        text = path.read_text(encoding="utf-8")
        for pat in bad:
            m = pat.search(text)
            assert m is None, (
                f"{path.name}: round prompt contains forecast/verdict-instructing "
                f"language: {m.group(0)!r}"
            )
