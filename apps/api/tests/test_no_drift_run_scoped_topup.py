"""Phase 8.2I — drift tests for the run_scoped_topup package.

Asserts:
  - no NON-Tavily live-API libraries imported (httpx is OK because the
    Tavily adapter uses it; the executor imports the adapter)
  - no `provider.chat` / `structured_output` / `embed` direct calls
  - all LLM calls go through cost_guarded_chat (transitively, via
    LLMTraitExtractor inside persona_construction)
  - no graph / cluster / simulation / UI / output ORM construction
  - no frontend changes

Note: this package legitimately imports the Tavily adapter, the
persona-construction worker, and the audience retrieval result types.
The drift test allowlists those.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "run_scoped_topup"
)


# httpx is forbidden — the executor uses TavilySearchExtractAdapter
# which encapsulates the httpx call internally. Same for tavily SDK.
FORBIDDEN_IMPORTS = (
    "httpx",
    "requests",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "playwright",
    "selenium",
    "firecrawl",
    "tavily",          # the SDK; we use the adapter
    "brave",
    "jina",
    "scrapy",
    "bs4",
    "beautifulsoup4",
    "praw",
    "tweepy",
    "googleapiclient",
    "openai",          # only Anthropic via AnthropicProvider
)


def test_no_non_tavily_network_or_llm_imports() -> None:
    if not PKG.exists():
        pytest.skip("run_scoped_topup package not yet created")
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
                        f"{py_file.name}:{node.lineno}: from {node.module}"
                    )
    assert offenders == [], (
        "run_scoped_topup must not import non-Tavily network libs:\n  "
        + "\n  ".join(offenders)
    )


_FORBIDDEN_METHOD_NAMES = {"chat", "structured_output", "embed"}


def test_no_direct_provider_calls() -> None:
    if not PKG.exists():
        pytest.skip("run_scoped_topup package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_METHOD_NAMES:
                    try:
                        snippet = ast.unparse(node)[:120]
                    except Exception:
                        snippet = f"<{func.attr} call>"
                    offenders.append(f"{py_file.name}:{node.lineno}: {snippet}")
    assert offenders == [], (
        "run_scoped_topup must not call provider methods directly:\n  "
        + "\n  ".join(offenders)
    )


def test_no_cost_guarded_chat_direct_in_topup_package() -> None:
    """LLM calls must go through `LLMTraitExtractor` (which itself uses
    cost_guarded_chat). Direct `cost_guarded_chat(...)` calls in the
    top-up package are forbidden — that path bypasses the persona-
    construction validator.

    persona_write.py does `import LLMTraitExtractor` — that's allowed
    because the extractor encapsulates cost_guarded_chat usage. Direct
    invocation by the top-up package is not."""
    if not PKG.exists():
        pytest.skip("run_scoped_topup package not yet created")
    pat = re.compile(r"\bcost_guarded_chat\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "run_scoped_topup must not call cost_guarded_chat directly: "
        + ", ".join(offenders)
    )


_FORBIDDEN_ORM_WRITES = (
    "PersonaGraphEdge",
    "PersonaCluster",
    "PersonaClusterMembership",
    "PersonaOpinion",
    "AudienceRetrievalRun",
    "PopulationConstructionAudit",
    "SimulationOutput",
    "SimulationRound",
    "AdapterComplianceStatus",
    "MechanismInitializationAudit",
)


def test_no_forbidden_orm_writes() -> None:
    """The top-up loop writes only `Simulation` (admin row for cost-
    guard anchoring), `SourceRecord` (via the Tavily adapter), and
    `PersonaRecord` / `PersonaTrait` / `PersonaEvidenceLink` (via
    `run_persona_construction`). Everything else — graph / cluster /
    opinion / simulation_output / simulation_round — is forbidden."""
    if not PKG.exists():
        pytest.skip("run_scoped_topup package not yet created")
    pat = re.compile(r"\b(?:" + "|".join(_FORBIDDEN_ORM_WRITES) + r")\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "run_scoped_topup must not construct forbidden ORM rows: "
        + ", ".join(offenders)
    )


def test_no_frontend_or_web_imports() -> None:
    """No reference to apps/web/ or any FastAPI route construction —
    this is a pipeline package, not an API surface."""
    if not PKG.exists():
        pytest.skip("run_scoped_topup package not yet created")
    forbidden_substrings = (
        "fastapi",
        "apps/web",
        "next.js",
        "next/router",
    )
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for s in forbidden_substrings:
            if s in text:
                offenders.append(f"{py_file.name}: {s}")
    assert offenders == [], (
        "run_scoped_topup must not import from frontend / FastAPI route "
        "code:\n  " + "\n  ".join(offenders)
    )
