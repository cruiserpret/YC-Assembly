"""Phase 8.2C — drift tests for the ingestion framework.

These tests structurally guarantee:

  - no network / browser / scraping library is imported anywhere in
    `pipeline/ingestion/`
  - no `*_adapter.py` file exists OUTSIDE `mock_adapters.py` (no real
    adapters yet)
  - no environment variable named like a Reddit API key is referenced
    in the framework code
  - `fetch_live` is declared on the base class as raising
    `NotImplementedError` — no concrete `fetch_live` exists in the
    package
  - the redaction-before-storage path is the ONLY path that produces
    the SourceRecord insert dict (no other code constructs SourceRecord
    rows by hand)
  - no direct provider calls (`<x>.chat`, `<x>.structured_output`,
    `<x>.embed`) appear in `pipeline/ingestion/`
  - the adapter base class checks compliance before its first write
    (asserted by reading the source for the gate symbol)
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
# Forbidden imports
# ---------------------------------------------------------------------------

# Phase 8.2C forbade every network library here. Phase 8.2E selectively
# permits `httpx` ONLY inside `tavily_adapter.py` (the first approved
# live adapter). Everything else stays forbidden everywhere — including
# any Reddit / Firecrawl / Brave / SerpAPI / Jina / Scrapy import, and
# `httpx` outside of the Tavily adapter file.
FORBIDDEN_IMPORTS = (
    "requests",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "playwright",
    "selenium",
    "firecrawl",
    "tavily",          # Tavily SDK is forbidden; we use httpx directly
    "brave",
    "jina",
    "scrapy",
    "bs4",
    "beautifulsoup4",
    "praw",
    "tweepy",
    "googleapiclient",
)

# httpx is forbidden EVERYWHERE in pipeline/ingestion/ except in
# specific approved adapter files. Each entry is the path RELATIVE to
# `INGESTION_PKG`. Phase 8.3B adds firecrawl/client.py (compliance memo
# at apps/api/docs/source_compliance/firecrawl.md, status='review').
_HTTPX_ALLOWED_REL_PATHS: frozenset[str] = frozenset({
    "tavily_adapter.py",
    "firecrawl/client.py",
})


def test_no_network_or_scraping_imports_in_pipeline_ingestion() -> None:
    """`pipeline/ingestion/` must NOT import any network / browser
    automation / scraping library, with the per-source exception that
    `httpx` is permitted ONLY inside the relative paths in
    `_HTTPX_ALLOWED_REL_PATHS` (each tied to a per-source compliance
    memo). Adding any new entry here requires its own memo + operator
    approval."""
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
                    if any(
                        name == f or name.startswith(f + ".")
                        for f in FORBIDDEN_IMPORTS
                    ):
                        offenders.append(
                            f"{rel_path}:{node.lineno}: import {name}"
                        )
                    elif (
                        (name == "httpx" or name.startswith("httpx."))
                        and rel_path not in _HTTPX_ALLOWED_REL_PATHS
                    ):
                        offenders.append(
                            f"{rel_path}:{node.lineno}: import httpx "
                            "(httpx is restricted to authorized adapter files)"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if any(
                    mod == f or mod.startswith(f + ".")
                    for f in FORBIDDEN_IMPORTS
                ):
                    offenders.append(
                        f"{rel_path}:{node.lineno}: from {mod} import …"
                    )
                elif (
                    (mod == "httpx" or mod.startswith("httpx."))
                    and rel_path not in _HTTPX_ALLOWED_REL_PATHS
                ):
                    offenders.append(
                        f"{rel_path}:{node.lineno}: from httpx import … "
                        "(httpx is restricted to authorized adapter files)"
                    )
    assert offenders == [], (
        "pipeline/ingestion/ must not import network/scraping libraries "
        f"(httpx allowlist: {sorted(_HTTPX_ALLOWED_REL_PATHS)}):\n  "
        + "\n  ".join(offenders)
    )


_ALLOWED_ADAPTER_FILES: frozenset[str] = frozenset({
    "adapter_base.py",
    "mock_adapters.py",
    "tavily_adapter.py",   # Phase 8.2E — first approved live adapter
})


def test_no_real_adapter_files_yet() -> None:
    """Phase 8.2E ships ONE live adapter — Tavily. Any other live adapter
    file (e.g. `reddit_adapter.py`, `shopify_adapter.py`,
    `producthunt_adapter.py`) sneaking into the package indicates a
    framework violation. Tavily is the only approved live API for now;
    individual domains it surfaces (Reddit, Product Hunt, etc.) get
    public-web treatment, NOT dedicated adapters."""
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    candidates: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        name = py_file.name
        if name in _ALLOWED_ADAPTER_FILES:
            continue
        if "adapter" in name.lower():
            candidates.append(name)
    assert candidates == [], (
        f"Unauthorized adapter files found: {candidates}. "
        "Phase 8.2E only authorizes tavily_adapter.py as the live "
        "adapter (plus the existing adapter_base.py and mock_adapters.py)."
    )


def test_no_reddit_api_key_env_vars_referenced() -> None:
    """No code path under `pipeline/ingestion/` should reference a
    Reddit-API-shaped environment variable in Phase 8.2C — Reddit
    remains a candidate source pending approval."""
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    forbidden_env_patterns = (
        re.compile(r'REDDIT_CLIENT_ID', re.IGNORECASE),
        re.compile(r'REDDIT_CLIENT_SECRET', re.IGNORECASE),
        re.compile(r'REDDIT_USERNAME', re.IGNORECASE),
        re.compile(r'REDDIT_PASSWORD', re.IGNORECASE),
        re.compile(r'REDDIT_REFRESH_TOKEN', re.IGNORECASE),
        re.compile(r'PRAW_CLIENT_ID', re.IGNORECASE),
    )
    offenders: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pat in forbidden_env_patterns:
            m = pat.search(text)
            if m:
                offenders.append(f"{py_file.name}: matched {pat.pattern!r} → {m.group(0)!r}")
    assert offenders == [], (
        "Reddit API credentials must not be referenced in 8.2C:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Provider-call AST scan (extends the universal Phase 6.6 scan)
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


def test_no_direct_provider_calls_in_pipeline_ingestion() -> None:
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    offenders: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        for ln, snippet in _find_forbidden_calls_ast(py_file):
            offenders.append(f"{py_file.name}:{ln}: {snippet}")
    assert offenders == [], (
        "pipeline/ingestion/ must not call provider methods directly:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# fetch_live discipline
# ---------------------------------------------------------------------------


_FETCH_LIVE_ALLOWED_FILES: frozenset[str] = frozenset({"tavily_adapter.py"})


def test_fetch_live_only_overridden_by_approved_adapters() -> None:
    """`SourceAdapter.fetch_live` raises NotImplementedError on the base
    class. Only approved live adapters (Phase 8.2E onwards: Tavily) may
    override it with a real body. Any other override is a framework
    violation."""
    base_src = (INGESTION_PKG / "adapter_base.py").read_text(encoding="utf-8")
    assert "raise NotImplementedError" in base_src

    for py_file in INGESTION_PKG.rglob("*.py"):
        if py_file.name == "adapter_base.py":
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                and node.name == "fetch_live"
            ):
                first = node.body[0] if node.body else None
                is_only_raise = (
                    isinstance(first, ast.Raise)
                    and len(node.body) == 1
                )
                if py_file.name in _FETCH_LIVE_ALLOWED_FILES:
                    # The approved adapter file may carry a real fetch_live.
                    continue
                assert is_only_raise, (
                    f"{py_file.name} overrides fetch_live with a real body "
                    "but is not in the approved-adapter allow-list. "
                    "Approved adapters in 8.2E: tavily_adapter.py."
                )


# ---------------------------------------------------------------------------
# Compliance-gate wiring
# ---------------------------------------------------------------------------


def test_adapter_base_calls_assert_adapter_approved() -> None:
    """The base class's ingest_mocked path must reference the gate
    symbol — drift test ensures the gate isn't silently removed."""
    src = (INGESTION_PKG / "adapter_base.py").read_text(encoding="utf-8")
    assert "assert_adapter_approved" in src, (
        "adapter_base.py must call assert_adapter_approved before ingestion"
    )


def test_no_other_caller_constructs_source_record_directly() -> None:
    """Outside of `redaction.py` (which produces the insert dict) and
    `adapter_base.py` (which calls `SourceRecord(...)` from that dict),
    no module under `pipeline/ingestion/` may instantiate SourceRecord
    directly. This is the structural rule that "redaction-before-
    storage" can never be bypassed.

    This test is permissive: it only enforces the rule WITHIN the
    ingestion package; tests + manual seeding scripts may still
    construct SourceRecord directly outside the package.
    """
    if not INGESTION_PKG.exists():
        pytest.skip("ingestion package not yet created")
    pat = re.compile(r"\bSourceRecord\s*\(")
    allowed = {"adapter_base.py"}
    offenders: list[str] = []
    for py_file in INGESTION_PKG.rglob("*.py"):
        if py_file.name in allowed:
            continue
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "Only adapter_base.py may construct SourceRecord(...) inside "
        "pipeline/ingestion/. Found in: " + ", ".join(offenders)
    )
