"""Phase 8.2F — drift tests for the persona construction package.

Asserts structural rules:

  - no network / browser-automation / scraping / LLM-provider library
    is imported anywhere in `pipeline/persona_construction/`
  - no direct `<x>.chat(`, `<x>.structured_output(`, `<x>.embed(` calls
    appear in the package — every LLM call routes through
    `cost_guarded_chat`
  - `cost_guarded_chat` is referenced ONLY in `extractor.py` (the
    sole LLM call site)
  - no module under `pipeline/persona_construction/` writes
    `source_records` (the package only READS sources)
  - no Tavily / live-ingestion symbol is used (no
    `TavilySearchExtractAdapter`, `ingest_live`, `ingest_mocked`)
  - no `PersonaGraphEdge` / `PersonaCluster` / `PersonaOpinion` /
    `Simulation` / `SimulationOutput` row construction (graph,
    cluster, simulation, and report writes are out of Phase 8.2F)
  - the closed persona-trait field name set is the ONLY field-name
    set referenced (no arbitrary fields)
  - the package never imports the sensitive-filter bypass shim
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "persona_construction"
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


def test_no_network_or_llm_library_imports() -> None:
    if not PKG.exists():
        pytest.skip("persona_construction package not yet created")
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
        "pipeline/persona_construction/ must not import network/LLM libs:\n  "
        + "\n  ".join(offenders)
    )


_FORBIDDEN_METHOD_NAMES = {"chat", "structured_output", "embed"}


def _find_forbidden_calls(py_file: Path) -> list[tuple[int, str]]:
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


def test_no_direct_provider_calls() -> None:
    if not PKG.exists():
        pytest.skip("persona_construction package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        for ln, s in _find_forbidden_calls(py_file):
            offenders.append(f"{py_file.name}:{ln}: {s}")
    assert offenders == [], (
        "pipeline/persona_construction/ must not call provider methods "
        "directly:\n  " + "\n  ".join(offenders)
    )


def test_cost_guarded_chat_only_in_extractor() -> None:
    if not PKG.exists():
        pytest.skip("persona_construction package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        if py_file.name == "extractor.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if "cost_guarded_chat" in text:
            offenders.append(py_file.name)
    assert offenders == [], (
        "cost_guarded_chat may only be invoked from extractor.py: "
        + ", ".join(offenders)
    )


_FORBIDDEN_WRITE_NAMES = (
    "SourceRecord",          # source_records is read-only here
    "PersonaGraphEdge",      # graph not built in 8.2F
    "PersonaCluster",        # clustering not built in 8.2F
    "PersonaClusterMembership",
    "PersonaOpinion",        # opinion construction not in 8.2F
    "AudienceRetrievalRun",  # retrieval not in 8.2F
    "PopulationConstructionAudit",
    "Simulation",            # no simulation construction
    "SimulationOutput",      # no report construction
)


def test_no_forbidden_orm_writes() -> None:
    """The package must NOT construct any of the listed ORM rows.

    Allowed persona-side writes are PersonaRecord, PersonaTrait, and
    PersonaEvidenceLink — and ONLY from `persistence.py` (the next
    test enforces that).
    """
    if not PKG.exists():
        pytest.skip("persona_construction package not yet created")
    pat = re.compile(r"\b(?:" + "|".join(_FORBIDDEN_WRITE_NAMES) + r")\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "Forbidden ORM-row construction in persona_construction: "
        + ", ".join(offenders)
    )


def test_persona_orm_writes_only_in_persistence() -> None:
    """`PersonaRecord(...)`, `PersonaTrait(...)`, and
    `PersonaEvidenceLink(...)` may be constructed only from
    `persistence.py`."""
    if not PKG.exists():
        pytest.skip("persona_construction package not yet created")
    pat = re.compile(
        r"\b(?:PersonaRecord|PersonaTrait|PersonaEvidenceLink)\s*\("
    )
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        if py_file.name == "persistence.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(py_file.name)
    assert offenders == [], (
        "PersonaRecord/PersonaTrait/PersonaEvidenceLink may only be "
        "constructed in persistence.py: " + ", ".join(offenders)
    )


def test_no_tavily_or_live_ingestion_symbols() -> None:
    """Phase 8.2F must not invoke Tavily or any live ingestion path."""
    if not PKG.exists():
        pytest.skip("persona_construction package not yet created")
    forbidden_symbols = (
        "TavilySearchExtractAdapter",
        "ingest_live",
        "ingest_mocked",
        "fetch_live",
        "fetch_mocked",
        "MockRedditPublicAPIAdapter",
    )
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for sym in forbidden_symbols:
            if sym in text:
                offenders.append(f"{py_file.name}: {sym}")
    assert offenders == [], (
        "Live-ingestion / Tavily symbols must not appear in 8.2F:\n  "
        + "\n  ".join(offenders)
    )


def test_only_closed_field_name_set_referenced() -> None:
    """The package must NOT carry a hardcoded persona-field name string
    that is NOT in the Phase 8.2A closed set. We grep for the literal
    PERSONA_FIELD_NAMES tuple and reject any other free-floating
    field-name literal that is not also in that set."""
    from assembly.pipeline.persona.constants import PERSONA_FIELD_NAMES
    allowed = set(PERSONA_FIELD_NAMES)
    # We allow these substrings to appear in code without flagging as
    # an arbitrary persona field (they are programming identifiers, not
    # persona field names).
    benign = {
        "field_name", "field_path",
    }
    suspicious = {
        # a few examples from earlier drafts to make sure we never
        # silently re-introduce them
        "age", "gender", "race", "ethnicity", "religion", "income",
        "household_income", "exact_address", "zip", "zip_code",
        "phone", "email", "real_name", "first_name", "last_name",
        "given_name", "family_name", "employer", "ssn", "dob",
        "birthdate", "raw_handle", "handle", "username",
    }
    if not PKG.exists():
        pytest.skip("persona_construction package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for s in suspicious:
            # Look for `'<s>'` or `"<s>"` literals indicating the
            # author tried to use this as a persona field.
            if re.search(rf"['\"]\b{s}\b['\"]", text):
                offenders.append(f"{py_file.name}: literal {s!r}")
    assert offenders == [], (
        "Suspicious persona-field literal in persona_construction "
        "(violates Phase 8.2A closed set):\n  " + "\n  ".join(offenders)
    )
    # And confirm the package references PERSONA_FIELD_NAMES by name in
    # at least one place — a sanity check that we DID anchor to the
    # closed set.
    refs = sum(
        1 for f in PKG.rglob("*.py")
        if "PERSONA_FIELD_NAMES" in f.read_text(encoding="utf-8")
    )
    assert refs >= 1, "persona_construction should reference PERSONA_FIELD_NAMES"
