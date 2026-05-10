"""Phase 8.2A — drift tests asserting no scraping / ingestion / external
calls were implemented.

The whole point of Phase 8.2A is that it ships the safe foundation
WITHOUT real-world data ingestion. These tests structurally enforce
that property so future commits can't quietly add a scraper without an
adapter framework + compliance review.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PERSONA_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "persona"
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
    "beautifulsoup4",
    "bs4",
    "praw",          # Reddit API wrapper
    "tweepy",        # Twitter
    "googleapiclient",
)


def test_pipeline_persona_has_no_network_or_scraping_imports() -> None:
    """Phase 8.2A package MUST NOT import network / browser / scraping
    libraries. Future Phase 8.2B will introduce ingestion through a
    dedicated `pipeline/ingestion/` package — not under
    `pipeline/persona/`."""
    if not PERSONA_PKG.exists():
        pytest.skip("persona package not yet created")
    offenders: list[str] = []
    for py_file in PERSONA_PKG.rglob("*.py"):
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
                        offenders.append(f"{py_file.name}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(
                    node.module == f or node.module.startswith(f + ".")
                    for f in FORBIDDEN_IMPORTS
                ):
                    offenders.append(
                        f"{py_file.name}:{node.lineno}: from {node.module} import …"
                    )
    assert offenders == [], (
        "pipeline/persona/ must not import network / scraping libraries:\n  "
        + "\n  ".join(offenders)
    )


def test_no_ingestion_adapter_files_yet() -> None:
    """No `*_adapter.py` modules outside the blessed Phase 8.2C
    ingestion package, and inside `pipeline/ingestion/` only the
    mocked-adapter framework files (`adapter_base.py`, `mock_adapters.py`)
    are allowed.

    Phase 8.2A's original test forbade every adapter file. Phase 8.2C
    deliberately introduces a mocked-only framework (no live network).
    This test now allows that surface but rejects anything else.
    Phase 8.2C's own drift test (`test_no_real_adapter_files_yet`)
    enforces that the ingestion package contains ONLY the mock + base
    files until Phase 8.2D approves a real adapter.
    """
    pipeline_root = PERSONA_PKG.parent
    allowed_in_ingestion = {
        "adapter_base.py",
        "mock_adapters.py",
        "tavily_adapter.py",  # Phase 8.2E — first approved live adapter
    }
    candidates: list[str] = []
    for py_file in pipeline_root.rglob("*adapter*.py"):
        rel = py_file.relative_to(pipeline_root)
        if rel.parts[:1] == ("ingestion",) and py_file.name in allowed_in_ingestion:
            continue
        candidates.append(str(rel))
    for py_file in pipeline_root.rglob("*ingest*.py"):
        rel = py_file.relative_to(pipeline_root)
        # The whole `pipeline/ingestion/` package is allowed in 8.2C+;
        # 8.2C's own drift test polices its contents.
        if rel.parts[:1] == ("ingestion",):
            continue
        # Phase 8.2I — `pipeline/run_scoped_topup/ingestion_plan.py`
        # is the planner that converts audience-retrieval top-up recs
        # into a Tavily-ready query plan. It does NOT do any
        # ingestion itself; the executor uses the existing approved
        # Tavily adapter.
        if (
            rel.parts[:1] == ("run_scoped_topup",)
            and py_file.name == "ingestion_plan.py"
        ):
            continue
        candidates.append(str(rel))
    assert candidates == [], (
        "Adapter / ingestion files outside the blessed Phase 8.2C "
        "framework are forbidden until Phase 8.2D approves a real "
        "adapter. Found:\n  " + "\n  ".join(candidates)
    )


# ---------------------------------------------------------------------------
# AST drift — no direct provider calls in pipeline/persona/
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


def test_no_direct_provider_calls_in_pipeline_persona() -> None:
    """`pipeline/persona/` must not call provider.chat / provider.structured_output
    / provider.embed directly. Future LLM use will route through
    `cost_guarded_chat` / `cost_guarded_embed`."""
    if not PERSONA_PKG.exists():
        pytest.skip("persona package not yet created")
    offenders: list[str] = []
    for py_file in PERSONA_PKG.rglob("*.py"):
        for ln, snippet in _find_forbidden_calls_ast(py_file):
            offenders.append(f"{py_file.name}:{ln}: {snippet}")
    assert offenders == [], (
        "pipeline/persona/ must not call provider methods directly:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# No real-identity columns on persona_records
# ---------------------------------------------------------------------------


def test_persona_record_orm_has_no_real_identity_columns() -> None:
    from assembly.models.persona import PersonaRecord
    forbidden = {
        "raw_handle", "handle", "username", "email", "phone",
        "real_name", "first_name", "last_name", "given_name", "family_name",
        "address", "street_address", "zip", "zip_code",
        "photo", "photo_url", "avatar_url", "profile_url",
        "ssn", "dob", "birthdate",
    }
    cols = {c.name for c in PersonaRecord.__table__.columns}
    leak = cols & forbidden
    assert not leak, f"persona_records must not carry real-identity columns; found {sorted(leak)}"


def test_source_record_orm_has_no_raw_handle_column() -> None:
    from assembly.models.persona import SourceRecord
    cols = {c.name for c in SourceRecord.__table__.columns}
    forbidden = {"raw_handle", "real_name", "email", "phone", "photo"}
    leak = cols & forbidden
    assert not leak, f"source_records must not carry real-identity columns; found {sorted(leak)}"


# ---------------------------------------------------------------------------
# Sensitive filter wired into validator
# ---------------------------------------------------------------------------


def test_sensitive_filter_is_imported_by_persona_validator() -> None:
    """Future refactors must not silently drop the sensitive filter from
    the validator path."""
    val_src = (PERSONA_PKG / "validator.py").read_text(encoding="utf-8")
    assert "sensitive_filter" in val_src or "scan_sensitive_attributes" in val_src
    assert "scan_sensitive_attributes" in val_src
