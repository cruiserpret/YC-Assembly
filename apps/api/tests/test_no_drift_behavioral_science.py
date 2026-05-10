"""Phase 8.2D — drift tests for the behavioral science mechanism library.

These tests structurally guarantee:

  - no network / browser / scraping / LLM-provider library is imported
    anywhere in `pipeline/behavioral_science/`
  - no direct `<x>.chat(`, `<x>.structured_output(`, `<x>.embed(`
    calls appear in the package
  - no module under `pipeline/behavioral_science/` writes to
    `persona_records`, `persona_traits`, `persona_evidence_links`, or
    other Phase 8.2A persona tables (mechanisms NEVER fabricate persona
    facts — they only describe priors)
  - the audit module is the only file that constructs
    `MechanismInitializationAudit(...)`
  - the seed catalog satisfies the user's required minimum counts
    (≥8 sources, ≥22 mechanisms, ≥14 strategies, ≥1 belief rule, ≥1
    applicability rule, every mechanism has ≥1 evidence link)
  - the closed-enum constants stay in sync with what the migration
    encodes (in particular: `'strong'` is NOT in `INFERENCE_STRENGTHS`)
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "behavioral_science"
)


# ---------------------------------------------------------------------------
# Forbidden imports
# ---------------------------------------------------------------------------


FORBIDDEN_IMPORTS = (
    "httpx",
    "requests",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "playwright",
    "selenium",
    "firecrawl",
    "tavily",
    "brave",
    "jina",
    "scrapy",
    "bs4",
    "beautifulsoup4",
    "praw",
    "tweepy",
    "googleapiclient",
    "anthropic",
    "openai",
)


def test_no_network_or_llm_imports_in_behavioral_science() -> None:
    """`pipeline/behavioral_science/` must NOT import any network /
    browser-automation / scraping / LLM-provider library."""
    if not PKG.exists():
        pytest.skip("behavioral_science package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == f or alias.name.startswith(f + ".")
                        for f in FORBIDDEN_IMPORTS
                    ):
                        offenders.append(
                            f"{py_file.name}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(
                    node.module == f or node.module.startswith(f + ".")
                    for f in FORBIDDEN_IMPORTS
                ):
                    offenders.append(
                        f"{py_file.name}:{node.lineno}: from {node.module} import …"
                    )
    assert offenders == [], (
        "pipeline/behavioral_science/ must not import network/LLM libraries:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Provider-call AST scan (extends Phase 6.6 / 8.2C scans)
# ---------------------------------------------------------------------------


_FORBIDDEN_METHOD_NAMES = {"chat", "structured_output", "embed"}


def _find_forbidden_calls_ast(py_file: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_METHOD_NAMES:
                try:
                    snippet = ast.unparse(node)[:120]
                except Exception:
                    snippet = f"<{func.attr} call>"
                out.append((node.lineno, snippet))
    return out


def test_no_direct_provider_calls_in_behavioral_science() -> None:
    if not PKG.exists():
        pytest.skip("behavioral_science package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        for ln, snippet in _find_forbidden_calls_ast(py_file):
            offenders.append(f"{py_file.name}:{ln}: {snippet}")
    assert offenders == [], (
        "pipeline/behavioral_science/ must not call provider methods directly:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# No persona-table writes from this package
# ---------------------------------------------------------------------------


_PERSONA_WRITE_NAMES = (
    "PersonaRecord",
    "PersonaTrait",
    "PersonaEvidenceLink",
    "PersonaOpinion",
    "PersonaGraphEdge",
    "PersonaCluster",
    "PersonaClusterMembership",
    "AudienceRetrievalRun",
    "PopulationConstructionAudit",
    "SourceRecord",
)


def test_no_persona_table_writes_from_behavioral_science() -> None:
    """The behavioral mechanism library MUST NOT construct or write any
    persona-table ORM rows. Mechanisms describe priors; they NEVER
    fabricate persona facts. Source evidence always outranks mechanism
    priors, and the only path that creates a persona row lives in Phase
    8.2A's persona pipeline.
    """
    if not PKG.exists():
        pytest.skip("behavioral_science package not yet created")
    pat = re.compile(
        r"\b(?:" + "|".join(_PERSONA_WRITE_NAMES) + r")\s*\("
    )
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "behavioral_science modules must not construct persona ORM rows: "
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Audit table: only audit.py constructs MechanismInitializationAudit
# ---------------------------------------------------------------------------


def test_audit_writes_only_audit_table() -> None:
    """`MechanismInitializationAudit(...)` may be constructed only inside
    `audit.py`. This is the structural guarantee that the audit row's
    write surface is single."""
    if not PKG.exists():
        pytest.skip("behavioral_science package not yet created")
    pat = re.compile(r"\bMechanismInitializationAudit\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        if py_file.name == "audit.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "Only audit.py may construct MechanismInitializationAudit(...); "
        "found in: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Seed minimum counts
# ---------------------------------------------------------------------------


def test_seed_catalog_meets_minimum_counts() -> None:
    from assembly.pipeline.behavioral_science.seed_data import (
        SEED_APPLICABILITY_RULES,
        SEED_BELIEF_RULES,
        SEED_MECHANISMS,
        SEED_SOURCES,
        SEED_STRATEGIES,
    )
    assert len(SEED_SOURCES) >= 8, (
        f"Phase 8.2D requires ≥ 8 research sources; got {len(SEED_SOURCES)}."
    )
    assert len(SEED_MECHANISMS) >= 22, (
        f"Phase 8.2D requires ≥ 22 mechanisms; got {len(SEED_MECHANISMS)}."
    )
    assert len(SEED_STRATEGIES) == 14, (
        f"Phase 8.2D requires exactly 14 persuasion strategies; "
        f"got {len(SEED_STRATEGIES)}."
    )
    assert len(SEED_BELIEF_RULES) >= 1
    assert len(SEED_APPLICABILITY_RULES) >= 1
    # Every mechanism has at least one evidence link.
    for m in SEED_MECHANISMS:
        assert len(m.sources) >= 1, (
            f"Mechanism {m.key!r} has no evidence links — every mechanism "
            "must trace to at least one research source."
        )


def test_seed_strategy_names_match_constants() -> None:
    from assembly.pipeline.behavioral_science.constants import (
        PERSUASION_STRATEGIES,
    )
    from assembly.pipeline.behavioral_science.seed_data import SEED_STRATEGIES

    seeded = {s.name for s in SEED_STRATEGIES}
    expected = set(PERSUASION_STRATEGIES)
    assert seeded == expected, (
        f"Seeded strategy names diverge from PERSUASION_STRATEGIES.\n"
        f"  only in seed:     {sorted(seeded - expected)}\n"
        f"  only in constants: {sorted(expected - seeded)}"
    )


# ---------------------------------------------------------------------------
# Closed-enum drift check: 'strong' is NOT in INFERENCE_STRENGTHS
# ---------------------------------------------------------------------------


def test_inference_strengths_excludes_strong() -> None:
    """The whole framework rule that 'priors never outrank evidence' is
    encoded by EXCLUDING 'strong' from the allowed inference strengths.
    This drift test fails the moment that invariant is violated."""
    from assembly.pipeline.behavioral_science.constants import (
        FORBIDDEN_INFERENCE_STRENGTHS,
        INFERENCE_STRENGTHS,
    )
    assert "strong" not in INFERENCE_STRENGTHS, (
        "INFERENCE_STRENGTHS must NOT include 'strong'. The framework's "
        "invariant is that source evidence ALWAYS outranks mechanism "
        "priors; the strongest spillover is 'moderate'."
    )
    assert "strong" in FORBIDDEN_INFERENCE_STRENGTHS


def test_seed_belief_rules_never_use_strong() -> None:
    from assembly.pipeline.behavioral_science.seed_data import SEED_BELIEF_RULES
    for br in SEED_BELIEF_RULES:
        assert br.allowed_inference_strength != "strong", (
            f"Seed rule {br.topic_a}->{br.topic_b} attempted strength="
            f"'strong'; forbidden."
        )
