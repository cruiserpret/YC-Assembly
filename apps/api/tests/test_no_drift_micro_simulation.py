"""Phase 8.2K — drift tests for the micro_simulation package."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "micro_simulation"
)


FORBIDDEN_IMPORTS = (
    "httpx", "requests", "aiohttp", "urllib.request", "urllib3",
    "playwright", "selenium", "firecrawl", "tavily", "brave",
    "jina", "scrapy", "bs4", "beautifulsoup4", "praw", "tweepy",
    "googleapiclient",
    "anthropic", "openai",
)


def test_no_network_or_llm_provider_imports() -> None:
    if not PKG.exists():
        pytest.skip("micro_simulation package not yet created")
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
                        offenders.append(f"{py_file.name}:{node.lineno}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(
                    node.module == f or node.module.startswith(f + ".")
                    for f in FORBIDDEN_IMPORTS
                ):
                    offenders.append(f"{py_file.name}:{node.lineno}: {node.module}")
    assert offenders == [], (
        "micro_simulation must not import network/LLM-provider libs:\n  "
        + "\n  ".join(offenders)
    )


_FORBIDDEN_METHODS = {"chat", "structured_output", "embed"}


def test_no_direct_provider_calls() -> None:
    if not PKG.exists():
        pytest.skip("micro_simulation package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_METHODS:
                    offenders.append(f"{py_file.name}:{node.lineno}: {func.attr}")
    assert offenders == [], (
        "micro_simulation must not call provider methods directly: "
        + "; ".join(offenders)
    )


def test_cost_guarded_chat_only_in_llm_call() -> None:
    """Only `llm_call.py` is allowed to import or invoke
    `cost_guarded_chat`. Every other module routes through
    `micro_llm_call`."""
    if not PKG.exists():
        pytest.skip("micro_simulation package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        if py_file.name == "llm_call.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if "cost_guarded_chat" in text:
            offenders.append(py_file.name)
    assert offenders == [], (
        "cost_guarded_chat may only be invoked from llm_call.py: "
        + ", ".join(offenders)
    )


_FORBIDDEN_ORM = (
    "PersonaGraphEdge", "PersonaCluster", "PersonaClusterMembership",
    "PersonaOpinion", "AudienceRetrievalRun", "PopulationConstructionAudit",
    "SimulationOutput", "SimulationRound", "DebateTurn", "AgentResponse",
    "Agent", "AgentEdge", "AdapterComplianceStatus",
    "MechanismInitializationAudit",
    # Source / persona row construction not allowed in micro:
    "SourceRecord", "PersonaRecord", "PersonaTrait",
    "PersonaEvidenceLink",
)


def test_no_forbidden_orm_writes() -> None:
    if not PKG.exists():
        pytest.skip("micro_simulation package not yet created")
    pat = re.compile(r"\b(?:" + "|".join(_FORBIDDEN_ORM) + r")\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "micro_simulation must not construct forbidden ORM rows: "
        + ", ".join(offenders)
    )


def test_no_frontend_references() -> None:
    if not PKG.exists():
        pytest.skip("micro_simulation package not yet created")
    forbidden = ("apps/web", "fastapi", "next/router", "next.js")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for s in forbidden:
            if s in text:
                offenders.append(f"{py_file.name}: {s}")
    assert offenders == [], (
        "micro_simulation must not reference frontend / FastAPI:\n  "
        + "\n  ".join(offenders)
    )


def test_micro_stage_label_prefix_enforced() -> None:
    """Every stage label constant in llm_call.py starts with `micro_`,
    AND `micro_llm_call` rejects any stage that doesn't."""
    src = (PKG / "llm_call.py").read_text(encoding="utf-8")
    assert 'STAGE_BASELINE = "micro_baseline"' in src
    assert 'STAGE_FIRST_EXPOSURE = "micro_first_exposure"' in src
    assert 'STAGE_OBJECTION = "micro_objection"' in src
    assert 'STAGE_FINAL_STANCE = "micro_final_stance"' in src
    assert 'STAGE_DEBATE = "micro_debate_turn"' in src
    # The runtime guard:
    assert 'startswith("micro_")' in src
