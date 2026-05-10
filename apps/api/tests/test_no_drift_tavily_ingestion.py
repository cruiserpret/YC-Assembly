"""Phase 8.2E — Tavily-specific drift tests.

Asserts the structural rules that make Tavily the ONLY live API in
this phase:

  - Only `tavily_adapter.py` is allowed to import `httpx`. Every other
    file under `pipeline/ingestion/` MUST NOT import httpx.
  - No PRAW / Reddit-specific HTTP libraries import anywhere.
  - No Firecrawl / Brave / SerpAPI / Jina / Scrapy / bs4 / playwright /
    selenium imports anywhere.
  - No file matching `*_adapter.py` outside of `tavily_adapter.py` (and
    `adapter_base.py` / `mock_adapters.py`) — i.e. no dedicated Reddit,
    Shopify, Trustpilot, Product Hunt, G2, Capterra, etc. adapters.
  - The Tavily adapter does NOT write `persona_records`,
    `persona_traits`, `persona_evidence_links`, or any persona-table
    ORM rows.
  - `TAVILY_API_KEY` string literal does not appear anywhere under
    `pipeline/ingestion/` outside of `tavily_adapter.py`.
  - The Tavily adapter does NOT contain any literal that matches a
    secret-shaped pattern (hardcoded `tvly-...` string).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


INGESTION_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "ingestion"
)


# ---------------------------------------------------------------------------
# Forbidden imports — extends the 8.2C set with explicit Phase 8.2E rules.
# ---------------------------------------------------------------------------


FORBIDDEN_IMPORTS_GLOBAL = (
    "requests",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "playwright",
    "selenium",
    "firecrawl",
    "tavily",          # SDK is forbidden; we use httpx directly
    "brave",
    "jina",
    "scrapy",
    "bs4",
    "beautifulsoup4",
    "praw",
    "tweepy",
    "googleapiclient",
)

# Path-relative allowlist (relative to INGESTION_PKG). Phase 8.3B
# extends the Tavily allowlist to include the Firecrawl client. Adding
# any other entry here requires its own per-source compliance memo +
# operator approval.
_HTTPX_ALLOWED_REL_PATHS: frozenset[str] = frozenset({
    "tavily_adapter.py",
    "firecrawl/client.py",
})
_ADAPTER_ALLOWED_FILES: frozenset[str] = frozenset({
    "adapter_base.py", "mock_adapters.py", "tavily_adapter.py",
})


def test_only_authorized_files_import_httpx() -> None:
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    offenders: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        rel_path = py_file.relative_to(INGESTION_PKG).as_posix()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if (name == "httpx" or name.startswith("httpx.")) and (
                        rel_path not in _HTTPX_ALLOWED_REL_PATHS
                    ):
                        offenders.append(
                            f"{rel_path}:{node.lineno}: import {name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if (
                    node.module == "httpx" or node.module.startswith("httpx.")
                ) and (rel_path not in _HTTPX_ALLOWED_REL_PATHS):
                    offenders.append(
                        f"{rel_path}:{node.lineno}: from {node.module}"
                    )
    assert offenders == [], (
        "httpx may only be imported in the authorized adapter files "
        f"({sorted(_HTTPX_ALLOWED_REL_PATHS)}):\n  "
        + "\n  ".join(offenders)
    )


def test_no_other_live_adapter_libraries_imported() -> None:
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    offenders: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == f or alias.name.startswith(f + ".")
                        for f in FORBIDDEN_IMPORTS_GLOBAL
                    ):
                        offenders.append(
                            f"{py_file.name}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(
                    node.module == f or node.module.startswith(f + ".")
                    for f in FORBIDDEN_IMPORTS_GLOBAL
                ):
                    offenders.append(
                        f"{py_file.name}:{node.lineno}: from {node.module}"
                    )
    assert offenders == [], (
        "Forbidden network/scraping import in pipeline/ingestion/:\n  "
        + "\n  ".join(offenders)
    )


def test_no_dedicated_third_party_adapter_files_added() -> None:
    """Phase 8.2E forbids dedicated Reddit / Shopify / Trustpilot /
    Product Hunt / G2 / Capterra / Firecrawl / Brave / SerpAPI / Jina /
    YouTube adapters. Anything matching `*adapter*.py` outside the
    approved list is a violation."""
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    candidates: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        name = py_file.name
        if name in _ADAPTER_ALLOWED_FILES:
            continue
        if "adapter" in name.lower():
            candidates.append(name)
    assert candidates == [], (
        f"Unauthorized adapter files: {candidates}. "
        f"Phase 8.2E only authorizes {sorted(_ADAPTER_ALLOWED_FILES)}."
    )


# ---------------------------------------------------------------------------
# Persona-table writes are forbidden from this phase
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
)


def test_tavily_adapter_does_not_construct_persona_rows() -> None:
    src = (INGESTION_PKG / "tavily_adapter.py").read_text(encoding="utf-8")
    pat = re.compile(r"\b(?:" + "|".join(_PERSONA_WRITE_NAMES) + r")\s*\(")
    assert not pat.search(src), (
        "tavily_adapter.py must not construct persona ORM rows in 8.2E."
    )


def test_no_persona_writes_anywhere_in_pipeline_ingestion() -> None:
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    pat = re.compile(r"\b(?:" + "|".join(_PERSONA_WRITE_NAMES) + r")\s*\(")
    offenders: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "pipeline/ingestion/ must not construct persona ORM rows in 8.2E: "
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# API key isolation
# ---------------------------------------------------------------------------


def test_tavily_api_key_string_only_referenced_in_tavily_adapter() -> None:
    """The string literal `TAVILY_API_KEY` must not appear in any other
    module under `pipeline/ingestion/`."""
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    offenders: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        if py_file.name == "tavily_adapter.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if "TAVILY_API_KEY" in text:
            offenders.append(py_file.name)
    assert offenders == [], (
        "TAVILY_API_KEY may only be referenced in tavily_adapter.py: "
        + ", ".join(offenders)
    )


def test_tavily_adapter_has_no_hardcoded_secret_literal() -> None:
    """No `tvly-<hex>` string should be present in the adapter source."""
    src = (INGESTION_PKG / "tavily_adapter.py").read_text(encoding="utf-8")
    pat = re.compile(r"['\"]tvly-[A-Za-z0-9_-]{8,}['\"]")
    matches = pat.findall(src)
    assert matches == [], (
        f"hardcoded Tavily-shaped secret literal found in tavily_adapter.py: "
        f"{matches}"
    )


# ---------------------------------------------------------------------------
# Other Reddit-API-shaped env vars stay out of the Tavily adapter
# ---------------------------------------------------------------------------


def test_no_reddit_api_credentials_referenced_in_tavily_adapter() -> None:
    src = (INGESTION_PKG / "tavily_adapter.py").read_text(encoding="utf-8")
    forbidden_patterns = (
        re.compile(r"REDDIT_CLIENT_ID", re.IGNORECASE),
        re.compile(r"REDDIT_CLIENT_SECRET", re.IGNORECASE),
        re.compile(r"REDDIT_USERNAME", re.IGNORECASE),
        re.compile(r"REDDIT_PASSWORD", re.IGNORECASE),
        re.compile(r"PRAW_CLIENT_ID", re.IGNORECASE),
        re.compile(r"FIRECRAWL_API_KEY", re.IGNORECASE),
        re.compile(r"BRAVE_API_KEY", re.IGNORECASE),
        re.compile(r"SERPAPI_API_KEY", re.IGNORECASE),
        re.compile(r"JINA_API_KEY", re.IGNORECASE),
        re.compile(r"YOUTUBE_API_KEY", re.IGNORECASE),
    )
    offenders = []
    for pat in forbidden_patterns:
        if pat.search(src):
            offenders.append(pat.pattern)
    assert offenders == [], (
        "tavily_adapter.py must reference no non-Tavily live-API "
        f"credentials. Found: {offenders}"
    )


# ---------------------------------------------------------------------------
# Provider-call / network-call AST scans extend cleanly here
# ---------------------------------------------------------------------------


_FORBIDDEN_METHOD_NAMES = {"chat", "structured_output", "embed"}


def test_no_direct_provider_calls_in_tavily_adapter() -> None:
    src = (INGESTION_PKG / "tavily_adapter.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _FORBIDDEN_METHOD_NAMES
            ):
                offenders.append(
                    f"line {node.lineno}: {func.attr} call"
                )
    assert offenders == [], (
        "tavily_adapter.py must not call LLM provider methods directly:\n  "
        + "\n  ".join(offenders)
    )
