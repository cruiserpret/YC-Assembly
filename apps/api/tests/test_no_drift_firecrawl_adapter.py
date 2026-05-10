"""Phase 8.3B — drift tests for the Firecrawl extraction adapter.

Asserts the structural rules that keep the adapter compliance-safe:

  * `httpx` is imported only in `firecrawl/client.py`.
  * `FIRECRAWL_API_KEY` literal appears only in `firecrawl/client.py`
    and `firecrawl/compliance_gate.py` (the latter references it in
    docstrings / module-level `_ENV_VAR` only — no env reads outside
    `client.py`).
  * `os.environ` reads happen only in `firecrawl/client.py`.
  * The package contains no construction of `SourceRecord`,
    `PersonaRecord`, `PersonaTrait`, `PersonaEvidenceLink`, or any
    other persistence ORM row — the adapter is a pure extractor.
  * The package contains no construction of population-graph or
    Phase-7 simulation surface rows.
  * No frontend / FastAPI / next.js references.
  * The Firecrawl SDK package (`firecrawl`) is not imported anywhere.
  * Only `/v1/scrape` is referenced — never `/v1/crawl` or `/v1/map`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "pipeline" / "ingestion" / "firecrawl"
)


def _rel(p: Path) -> str:
    return p.relative_to(PKG).as_posix()


# ---------------------------------------------------------------------------
# httpx isolation
# ---------------------------------------------------------------------------


_HTTPX_ALLOWED: frozenset[str] = frozenset({"client.py"})


def test_httpx_only_in_client_py() -> None:
    if not PKG.exists():
        pytest.skip("firecrawl package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        rel = _rel(py_file)
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "httpx" or alias.name.startswith("httpx."):
                        if rel not in _HTTPX_ALLOWED:
                            offenders.append(
                                f"{rel}:{node.lineno}: import {alias.name}"
                            )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if (
                    node.module == "httpx" or node.module.startswith("httpx.")
                ) and rel not in _HTTPX_ALLOWED:
                    offenders.append(
                        f"{rel}:{node.lineno}: from {node.module}"
                    )
    assert offenders == [], (
        "httpx must only be imported from firecrawl/client.py:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# API-key isolation
# ---------------------------------------------------------------------------


_KEY_LITERAL_ALLOWED: frozenset[str] = frozenset({
    # client.py reads the env var; compliance_gate.py + __init__.py /
    # docstrings of the package may mention the var by NAME for human
    # readers but must NOT call os.environ on it.
    "client.py",
    "compliance_gate.py",
    "__init__.py",
})


def test_api_key_literal_only_in_authorized_files() -> None:
    if not PKG.exists():
        pytest.skip("firecrawl package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        rel = _rel(py_file)
        text = py_file.read_text(encoding="utf-8")
        if "FIRECRAWL_API_KEY" in text and rel not in _KEY_LITERAL_ALLOWED:
            offenders.append(rel)
    assert offenders == [], (
        "FIRECRAWL_API_KEY may only appear in client.py / "
        "compliance_gate.py / __init__.py: "
        + ", ".join(offenders)
    )


def test_os_environ_only_in_client_py() -> None:
    """`os.environ` is the only legitimate path to the API key. Any
    other module that reads the env directly is a drift bug (the gate
    should be the only `os.environ` reader on the compliance side, but
    in 8.3B we keep all env reads in client.py and the gate calls
    `assert_adapter_approved` which does not touch env)."""
    if not PKG.exists():
        pytest.skip("firecrawl package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        rel = _rel(py_file)
        if rel == "client.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if "os.environ" in text or "os.getenv" in text:
            offenders.append(rel)
    assert offenders == [], (
        "os.environ / os.getenv may only be referenced in client.py: "
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Forbidden ORM-row construction
# ---------------------------------------------------------------------------


_FORBIDDEN_ORM = (
    # Source / persona persistence — adapter is read-only by contract:
    "SourceRecord",
    "PersonaRecord",
    "PersonaTrait",
    "PersonaEvidenceLink",
    # Population-graph / clustering / opinions:
    "PersonaGraphEdge",
    "PersonaCluster",
    "PersonaClusterMembership",
    "PersonaOpinion",
    # Phase 7 simulation surface:
    "SimulationOutput",
    "SimulationRound",
    "AgentResponse",
    "DebateTurn",
    "Agent",
    "AgentEdge",
    # Audit / compliance write surfaces (gate is read-only here):
    "AdapterComplianceStatus",
    "AudienceRetrievalRun",
    "PopulationConstructionAudit",
)


def test_no_forbidden_orm_writes() -> None:
    if not PKG.exists():
        pytest.skip("firecrawl package not yet created")
    pat = re.compile(r"\b(?:" + "|".join(_FORBIDDEN_ORM) + r")\s*\(")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pat.search(text):
            offenders.append(_rel(py_file))
    assert offenders == [], (
        "Firecrawl adapter must not construct persistence ORM rows: "
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Frontend / FastAPI / web framework references
# ---------------------------------------------------------------------------


def test_no_frontend_references() -> None:
    if not PKG.exists():
        pytest.skip("firecrawl package not yet created")
    forbidden = ("apps/web", "fastapi", "next/router", "next.js")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for s in forbidden:
            if s in text:
                offenders.append(f"{_rel(py_file)}: {s}")
    assert offenders == [], (
        "Firecrawl adapter must not reference frontend / FastAPI:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Firecrawl SDK forbidden — direct httpx only
# ---------------------------------------------------------------------------


def test_firecrawl_sdk_not_imported() -> None:
    """The official `firecrawl` Python SDK is forbidden package-wide.
    We use direct httpx so the request shape is auditable."""
    if not PKG.exists():
        pytest.skip("firecrawl package not yet created")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        rel = _rel(py_file)
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "firecrawl" or alias.name.startswith(
                        "firecrawl."
                    ):
                        offenders.append(
                            f"{rel}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "firecrawl" or node.module.startswith(
                    "firecrawl."
                ):
                    offenders.append(
                        f"{rel}:{node.lineno}: from {node.module}"
                    )
    assert offenders == [], (
        "Firecrawl SDK must not be imported (use direct httpx):\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Endpoint scope: only /v1/scrape
# ---------------------------------------------------------------------------


def test_only_scrape_endpoint_referenced() -> None:
    """The 8.3B compliance memo authorizes ONLY `/v1/scrape`.
    `/v1/crawl` and `/v1/map` (multi-page operations) are out of scope
    in this phase."""
    if not PKG.exists():
        pytest.skip("firecrawl package not yet created")
    forbidden = ("/v1/crawl", "/v1/map", "/v0/crawl", "/v0/map")
    offenders: list[str] = []
    for py_file in PKG.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for s in forbidden:
            if s in text:
                offenders.append(f"{_rel(py_file)}: {s}")
    assert offenders == [], (
        "Only /v1/scrape is authorized in 8.3B. Other endpoints "
        "require a memo update + status re-review:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Compliance-gate signature: live extract MUST require explicit gate call
# ---------------------------------------------------------------------------


def test_compliance_gate_module_uses_assert_adapter_approved() -> None:
    """The Firecrawl gate MUST go through the framework's existing
    `assert_adapter_approved`. We verify by string match in the gate
    file — the indirection itself is what guarantees memo + DB row
    are both checked."""
    src = (PKG / "compliance_gate.py").read_text(encoding="utf-8")
    assert "assert_adapter_approved" in src, (
        "compliance_gate.py must delegate to assert_adapter_approved"
    )
    assert 'FIRECRAWL_DEFAULT_STATUS = "review"' in src, (
        "compliance_gate.py must declare default status as 'review'"
    )


def test_compliance_gate_memo_path_constant() -> None:
    """Memo path must point to the operator-readable
    `docs/source_compliance/firecrawl.md`."""
    src = (PKG / "compliance_gate.py").read_text(encoding="utf-8")
    assert (
        'FIRECRAWL_MEMO_PATH = "apps/api/docs/source_compliance/firecrawl.md"'
        in src
    )


# ---------------------------------------------------------------------------
# Memo file is present and opens at Status: review
# ---------------------------------------------------------------------------


def test_compliance_memo_present_at_review_status() -> None:
    memo_path = (
        Path(__file__).resolve().parent.parent
        / "docs" / "source_compliance" / "firecrawl.md"
    )
    assert memo_path.is_file(), (
        f"Firecrawl compliance memo missing at {memo_path}"
    )
    text = memo_path.read_text(encoding="utf-8")
    assert "**Status:** review" in text, (
        "Memo must open at Status: review until operator approval"
    )
    # Sanity: every section of the operator-readable memo is present.
    for section in (
        "## 1. Status",
        "## 2. Source name + scope",
        "## 3. Access method",
        "## 4. robots.txt status",
        "## 5. Terms of Service notes",
        "## 6. Commercial-use notes",
        "## 7. Rate limits",
        "## 8. What we collect",
        "## 9. What we do NOT collect",
        "## 10. Identity surface",
        "## 11. Redaction-before-storage path",
        "## 12. Sensitive data policy",
        "## 13. Allowed downstream use",
        "## 14. Refresh / deletion policy",
        "## 15. Risks + mitigations",
        "## 16. Sign-off",
    ):
        assert section in text, f"Memo is missing section: {section}"
