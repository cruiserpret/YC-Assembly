"""Phase 8.2H — drift tests for the audience_retrieval package."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "audience_retrieval"
)


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


def test_no_network_or_llm_imports() -> None:
    if not PKG.exists():
        pytest.skip("audience_retrieval package not yet created")
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
        "audience_retrieval must not import network/LLM libs:\n  "
        + "\n  ".join(offenders)
    )


_FORBIDDEN_METHOD_NAMES = {"chat", "structured_output", "embed"}


def test_no_direct_provider_calls() -> None:
    if not PKG.exists():
        pytest.skip("audience_retrieval package not yet created")
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
        "audience_retrieval must not call provider methods directly:\n  "
        + "\n  ".join(offenders)
    )


def test_no_cost_guarded_chat_in_deterministic_phase() -> None:
    if not PKG.exists():
        pytest.skip("audience_retrieval package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if "cost_guarded_chat" in text:
            offenders.append(py_file.name)
    assert offenders == [], (
        "audience_retrieval is deterministic-only in 8.2H; "
        "no cost_guarded_chat: " + ", ".join(offenders)
    )


def test_no_tavily_or_live_ingestion_symbols() -> None:
    if not PKG.exists():
        pytest.skip("audience_retrieval package not yet created")
    forbidden_symbols = (
        "TavilySearchExtractAdapter",
        "ingest_live",
        "ingest_mocked",
        "fetch_live",
        "fetch_mocked",
        "MockRedditPublicAPIAdapter",
        "register_or_update_adapter_status",
        "assert_adapter_approved",
        "AnthropicProvider",
        "OpenAIProvider",
    )
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for sym in forbidden_symbols:
            if sym in text:
                offenders.append(f"{py_file.name}: {sym}")
    assert offenders == [], (
        "audience_retrieval must not reference live-ingestion symbols:\n  "
        + "\n  ".join(offenders)
    )


_FORBIDDEN_ORM_WRITES = (
    "PersonaRecord",
    "PersonaTrait",
    "PersonaEvidenceLink",
    "PersonaGraphEdge",
    "PersonaCluster",
    "PersonaClusterMembership",
    "PersonaOpinion",
    "AudienceRetrievalRun",
    "PopulationConstructionAudit",
    "Simulation",
    "SimulationOutput",
    "SimulationRound",
    "SourceRecord",
    "AdapterComplianceStatus",
    "MechanismInitializationAudit",
)


def test_no_orm_row_construction() -> None:
    if not PKG.exists():
        pytest.skip("audience_retrieval package not yet created")
    pat = re.compile(r"\b(?:" + "|".join(_FORBIDDEN_ORM_WRITES) + r")\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "audience_retrieval must not construct ORM rows: "
        + ", ".join(offenders)
    )


def test_no_session_db_writes() -> None:
    if not PKG.exists():
        pytest.skip("audience_retrieval package not yet created")
    forbidden_patterns = (
        re.compile(r"\bsession\.add\b"),
        re.compile(r"\bsession\.flush\b"),
        re.compile(r"\bsession\.commit\b"),
        re.compile(r"\bsession\.execute\s*\(\s*(?:insert|update|delete)"),
    )
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            if pat.search(text):
                offenders.append(f"{py_file.name}: {pat.pattern}")
    assert offenders == [], (
        "audience_retrieval must not perform DB writes:\n  "
        + "\n  ".join(offenders)
    )
