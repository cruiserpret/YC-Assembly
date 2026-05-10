"""Phase 8.2F.7 — drift tests for the persona-relevance audit package.

Asserts:
  - no network / browser / scraping / LLM-provider library imports
  - no direct provider calls (`<x>.chat(`, `<x>.structured_output(`,
    `<x>.embed(`)
  - `cost_guarded_chat` is NOT referenced (this phase is deterministic;
    if a future revision adds an LLM path it MUST go through the helper)
  - no `SourceRecord` / `PersonaRecord` / `PersonaTrait` /
    `PersonaEvidenceLink` / `PersonaGraphEdge` / `PersonaCluster` /
    `PersonaOpinion` / `Simulation` / `SimulationOutput` /
    `AdapterComplianceStatus` / `MechanismInitializationAudit` row
    construction (the audit READS data; never writes)
  - no Tavily / live-ingestion symbols are referenced
  - no UI / frontend paths are referenced
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "persona_relevance"
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
        pytest.skip("persona_relevance package not yet created")
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
                        f"{py_file.name}:{node.lineno}: from {node.module} import"
                    )
    assert offenders == [], (
        "persona_relevance package must not import network/LLM libs:\n  "
        + "\n  ".join(offenders)
    )


_FORBIDDEN_METHOD_NAMES = {"chat", "structured_output", "embed"}


def test_no_direct_provider_calls() -> None:
    if not PKG.exists():
        pytest.skip("persona_relevance package not yet created")
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
        "persona_relevance must not call provider methods directly:\n  "
        + "\n  ".join(offenders)
    )


def test_no_cost_guarded_chat_in_deterministic_phase() -> None:
    """Phase 8.2F.7 ships deterministic-only. If a future revision adds
    an LLM path, drift this test (and update the prompt-side discipline
    accordingly). Today: no LLM calls anywhere."""
    if not PKG.exists():
        pytest.skip("persona_relevance package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if "cost_guarded_chat" in text:
            offenders.append(py_file.name)
    assert offenders == [], (
        "persona_relevance is deterministic-only in 8.2F.7; no cost_guarded_chat: "
        + ", ".join(offenders)
    )


_FORBIDDEN_ORM_WRITES = (
    "SourceRecord",
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
    "AdapterComplianceStatus",
    "MechanismInitializationAudit",
)


def test_no_orm_row_construction() -> None:
    if not PKG.exists():
        pytest.skip("persona_relevance package not yet created")
    pat = re.compile(r"\b(?:" + "|".join(_FORBIDDEN_ORM_WRITES) + r")\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "persona_relevance must not construct ORM rows: " + ", ".join(offenders)
    )


def test_no_tavily_or_live_symbols() -> None:
    if not PKG.exists():
        pytest.skip("persona_relevance package not yet created")
    forbidden_symbols = (
        "TavilySearchExtractAdapter",
        "ingest_live",
        "ingest_mocked",
        "fetch_live",
        "fetch_mocked",
        "MockRedditPublicAPIAdapter",
        "register_or_update_adapter_status",
        "assert_adapter_approved",
    )
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for sym in forbidden_symbols:
            if sym in text:
                offenders.append(f"{py_file.name}: {sym}")
    assert offenders == [], (
        "persona_relevance must not reference live-ingestion symbols:\n  "
        + "\n  ".join(offenders)
    )


def test_no_session_db_writes() -> None:
    """Audit code MUST NOT call `session.add`, `session.execute(insert)`,
    `session.execute(update)`, or `session.execute(delete)`."""
    if not PKG.exists():
        pytest.skip("persona_relevance package not yet created")
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
        "persona_relevance must not perform DB writes:\n  "
        + "\n  ".join(offenders)
    )
