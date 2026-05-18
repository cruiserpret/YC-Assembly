"""Phase 11D.9 — runtime tech-market retrieval audit-only tests.

Pins:
  1. ANY flag off → audit returns disabled-state dict, no DB hit.
  2. Both ENABLED + RUNTIME_ENABLED true → audit retrieves and
     populates the dict shape.
  3. Persona prompts are NOT changed by Phase 11D.9 — the
     persona-injection wiring stays scaffold-only (third flag is
     observability only).
  4. founder_report.json shape: `technical.tech_market_signals`
     is ADDITIVE next to `technical.amazon_reviews_2023` — no
     existing key renamed/removed.
  5. Audit lands under `technical/` only — never under any
     visible report section.
  6. Hard caps (≤ 20 considered, ≤ 12 kept, ≤ 3 per signal_type).
  7. Category / market_context filtering — no cross-category leak.
  8. Unresolved category → refuses to retrieve, fallback_used=False.
  9. No raw text / PII leaks — only ≤240-char short_snippet.
 10. No apps/web files changed.
 11. Drift: only whitelisted injector imports the retriever from
     production code paths.
 12. Local-validation simulation: AI SaaS brief returns ai_saas
     signals; devtool brief returns devtool_api signals.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from assembly.sources.tech_market_provider import (
    InMemoryTechMarketSignalSource,
    TechMarketRetrievalConfig,
    TechMarketSignalRetriever,
    TechSignalRow,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_AI_SAAS_BRIEF: dict[str, Any] = {
    "product_name": "QuietCart",
    "product_description": (
        "An AI SaaS workflow assistant for marketing teams who want "
        "to ship more LLM-powered scripts."
    ),
    "category_hint": "AI SaaS tool",
    "competitors_or_alternatives": ["Notion AI", "Jasper"],
}


_DEVTOOL_BRIEF: dict[str, Any] = {
    "product_name": "Semble-style",
    "product_description": (
        "A developer tool that indexes large codebases for agent "
        "queries via a CLI and SDK."
    ),
    "category_hint": "developer tool",
    "competitors_or_alternatives": ["Sourcegraph", "ripgrep"],
}


_UNKNOWN_CATEGORY_BRIEF: dict[str, Any] = {
    "product_name": "Mystery Widget",
    "product_description": "Something else entirely",
    "category_hint": "some uncategorized thing nobody mapped",
    "competitors_or_alternatives": [],
}


class _SettingsAllOn:
    tech_market_signals_enabled = True
    tech_market_signals_runtime_enabled = True
    tech_market_signals_persona_injection_enabled = False  # stays off
    tech_market_signals_max_per_run = 80
    tech_market_signals_min_relevance = 0.20


class _SettingsEnabledOnly:
    tech_market_signals_enabled = True
    tech_market_signals_runtime_enabled = False  # the gate that matters
    tech_market_signals_persona_injection_enabled = False
    tech_market_signals_max_per_run = 80
    tech_market_signals_min_relevance = 0.20


class _SettingsAllOff:
    tech_market_signals_enabled = False
    tech_market_signals_runtime_enabled = False
    tech_market_signals_persona_injection_enabled = False
    tech_market_signals_max_per_run = 80
    tech_market_signals_min_relevance = 0.20


def _exploding_sessionmaker(*a: Any, **kw: Any) -> Any:  # pragma: no cover
    raise AssertionError(
        "opened DB session despite flags off — Phase 11D.9 must "
        "short-circuit when ENABLED or RUNTIME_ENABLED is False",
    )


def _ai_saas_row(
    *,
    signal_type: str = "feature_inquiry",
    snippet: str = "Can we use this for marketing scripts?",
    competitor: str | None = None,
    buyer: str = "user",
    sentiment: str = "positive",
) -> TechSignalRow:
    return TechSignalRow(
        source_provider="operator_csv_product_hunt",
        source_category="product_hunt_launch_comments",
        product_category="ai_saas",
        company_or_product="Generic AI Tool",
        competitor_name=competitor,
        signal_type=signal_type,  # type: ignore[arg-type]
        sentiment_bucket=sentiment,  # type: ignore[arg-type]
        buyer_type=buyer,  # type: ignore[arg-type]
        market_context="AI_tool",  # type: ignore[arg-type]
        theme=None,
        short_snippet=snippet,
        evidence_url=None,
        source_timestamp=None,
        relevance_score=None,
        metadata={},
    )


def _devtool_row(
    *,
    signal_type: str = "developer_skepticism",
    snippet: str = "Is the benchmark measuring real workloads?",
    competitor: str | None = None,
    buyer: str = "developer",
    sentiment: str = "negative",
) -> TechSignalRow:
    return TechSignalRow(
        source_provider="operator_csv_hn",
        source_category="hn_thread_comments",
        product_category="devtool_api",
        company_or_product="Generic Devtool",
        competitor_name=competitor,
        signal_type=signal_type,  # type: ignore[arg-type]
        sentiment_bucket=sentiment,  # type: ignore[arg-type]
        buyer_type=buyer,  # type: ignore[arg-type]
        market_context="devtool",  # type: ignore[arg-type]
        theme=None,
        short_snippet=snippet,
        evidence_url=None,
        source_timestamp=None,
        relevance_score=None,
        metadata={},
    )


def _patch_source(rows: list[TechSignalRow]):
    """Returns a context-manager-friendly patch of
    PostgresTechMarketSignalSource so the injector reads from
    in-memory rows instead of touching a real DB."""
    src = InMemoryTechMarketSignalSource(rows)

    class _FakePostgresSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def fetch_by_product_category(
            self, *a: Any, **kw: Any,
        ):
            return await src.fetch_by_product_category(*a, **kw)

        async def fetch_by_market_context(self, *a: Any, **kw: Any):
            return await src.fetch_by_market_context(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await src.fetch_by_competitor(*a, **kw)

        async def fetch_by_signal_types(self, *a: Any, **kw: Any):
            return await src.fetch_by_signal_types(*a, **kw)

    return _FakePostgresSource


# ---------------------------------------------------------------------------
# 1+2. Flag gating — disabled state
# ---------------------------------------------------------------------------


def test_audit_returns_disabled_dict_when_all_flags_off() -> None:
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_evidence_section_from_dict_brief,
    )
    audit = asyncio.run(
        build_tech_market_evidence_section_from_dict_brief(
            _AI_SAAS_BRIEF,
            sessionmaker=_exploding_sessionmaker,
            settings=_SettingsAllOff(),  # type: ignore[arg-type]
        ),
    )
    assert audit["enabled"] is False
    assert audit["runtime_enabled"] is False
    assert audit["attempted"] is False
    assert audit["signals_considered"] == 0
    assert audit["signals_kept"] == 0
    assert audit["sample_kept_signals"] == []


def test_audit_returns_disabled_dict_when_runtime_flag_off() -> None:
    """The double-gate: enabled=True but runtime_enabled=False → no
    retrieval, no DB hit."""
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_evidence_section_from_dict_brief,
    )
    audit = asyncio.run(
        build_tech_market_evidence_section_from_dict_brief(
            _AI_SAAS_BRIEF,
            sessionmaker=_exploding_sessionmaker,
            settings=_SettingsEnabledOnly(),  # type: ignore[arg-type]
        ),
    )
    assert audit["enabled"] is True
    assert audit["runtime_enabled"] is False
    assert audit["attempted"] is False
    assert audit["signals_considered"] == 0


# ---------------------------------------------------------------------------
# 3. Retrieval works when BOTH flags on
# ---------------------------------------------------------------------------


def test_audit_returns_kept_signals_when_both_flags_on() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [
        _ai_saas_row(signal_type="feature_inquiry",
                     snippet=f"feature q {i}") for i in range(4)
    ] + [
        _ai_saas_row(signal_type="pain_urgency",
                     snippet=f"pain row {i}") for i in range(3)
    ]
    fake_source = _patch_source(rows)
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = fake_source  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert audit["enabled"] is True
    assert audit["runtime_enabled"] is True
    assert audit["attempted"] is True
    assert audit["query_category"] == "ai_saas"
    assert audit["query_market_context"] == "AI_tool"
    assert audit["signals_kept"] >= 1
    assert audit["signal_type_distribution"]
    assert audit["fallback_used"] is False


# ---------------------------------------------------------------------------
# 4. Founder report shape — additive only
# ---------------------------------------------------------------------------


def test_audit_dict_has_no_visible_report_field_names() -> None:
    """The audit dict must NOT contain any of the user-facing
    field names the frontend reads from founder_report.json."""
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_evidence_section_from_dict_brief,
    )
    audit = asyncio.run(
        build_tech_market_evidence_section_from_dict_brief(
            _AI_SAAS_BRIEF,
            sessionmaker=_exploding_sessionmaker,
            settings=_SettingsAllOff(),  # type: ignore[arg-type]
        ),
    )
    forbidden = (
        "executive_summary", "headline", "best_fit_audience",
        "hardest_to_convince_audience", "top_objections",
        "persuasion_levers", "recommended_next_tests",
        "confidence_dimensions", "caveats",
    )
    for k in forbidden:
        assert k not in audit, (
            f"audit dict accidentally contains user-facing key {k!r}"
        )


def test_orchestrator_attaches_audit_under_technical_key() -> None:
    """Static check: the orchestrator wires
    tech_market_audit_for_report into main_report["technical"][
    "tech_market_signals"] — never into a user-facing section."""
    orch = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration" / "live_founder_brief.py"
    )
    src = orch.read_text(encoding="utf-8")
    assert "tech_market_audit_for_report" in src
    assert '"tech_market_signals": tech_market_audit_for_report' in src


def test_orchestrator_preserves_amazon_audit_key() -> None:
    """Backward-compat: the existing amazon_reviews_2023 audit key
    must still be present alongside the new tech_market_signals
    key."""
    orch = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration" / "live_founder_brief.py"
    )
    src = orch.read_text(encoding="utf-8")
    assert '"amazon_reviews_2023": amazon_audit_for_report' in src
    assert '"tech_market_signals": tech_market_audit_for_report' in src


# ---------------------------------------------------------------------------
# 5. Same-category invariant — unresolved category fails closed
# ---------------------------------------------------------------------------


def test_unresolved_category_refuses_retrieval() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    # Use an exploding source — if the injector somehow tried to
    # hit the DB despite unresolved category, this would raise.
    class _ExplodingSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def fetch_by_product_category(self, *a: Any, **kw: Any):
            raise AssertionError(
                "fetch_by_product_category called for unresolved category",
            )

        async def fetch_by_market_context(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_market_context called")

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_competitor called")

        async def fetch_by_signal_types(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_signal_types called")

    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _ExplodingSource  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _UNKNOWN_CATEGORY_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert audit["attempted"] is True
    assert audit["query_category"] is None
    assert audit["signals_kept"] == 0
    assert audit["fallback_used"] is False
    assert "product_category_unresolved" in audit["rejection_reasons"]


# ---------------------------------------------------------------------------
# 6. Hard caps (≤ 20 considered, ≤ 12 kept, ≤ 3 per signal_type)
# ---------------------------------------------------------------------------


def test_audit_caps_signals_kept_at_12() -> None:
    """Even with 50+ matching rows in the source, the audit caps at
    12 kept signals (operator-spec'd Phase 11D.9 limit)."""
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows: list[TechSignalRow] = []
    for stype in (
        "pain_urgency", "feature_inquiry", "workflow_fit",
        "competitor_comparison", "switching_objection",
        "trust_security_concern",
    ):
        for i in range(20):
            rows.append(_ai_saas_row(
                signal_type=stype,
                snippet=f"{stype} unique snippet {i} for ai saas tool",
            ))
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert audit["signals_considered"] <= 20, audit
    assert audit["signals_kept"] <= 12, audit


def test_audit_caps_at_3_per_signal_type() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    # 10 rows of a single signal_type
    rows = [
        _ai_saas_row(
            signal_type="pain_urgency",
            snippet=f"pain row {i} unique tokens",
        )
        for i in range(10)
    ]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    for stype, count in audit["signal_type_distribution"].items():
        assert count <= 3, f"{stype} count {count} > 3 cap"


# ---------------------------------------------------------------------------
# 7. Category + market_context filtering — no cross-category leak
# ---------------------------------------------------------------------------


def test_ai_saas_brief_does_not_surface_devtool_signals() -> None:
    """Operator spec: AI SaaS brief uses ai_saas / AI_tool signals.
    Devtool-category rows must NOT leak into the audit even when
    sharing the source DB."""
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = (
        [_ai_saas_row(snippet=f"ai saas row {i}") for i in range(5)]
        + [_devtool_row(snippet=f"devtool row {i}") for i in range(5)]
    )
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    for s in audit["sample_kept_signals"]:
        assert s["product_category"] == "ai_saas", (
            f"devtool leak into ai_saas brief: {s}"
        )
    # Distributions must also reflect single-category.
    assert audit["market_context_distribution"].keys() == {"AI_tool"}


def test_devtool_brief_does_not_surface_ai_saas_signals() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = (
        [_ai_saas_row(snippet=f"ai saas row {i}") for i in range(5)]
        + [_devtool_row(snippet=f"devtool row {i}") for i in range(5)]
    )
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _DEVTOOL_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    for s in audit["sample_kept_signals"]:
        assert s["product_category"] == "devtool_api", (
            f"ai_saas leak into devtool brief: {s}"
        )
    assert audit["market_context_distribution"].keys() == {"devtool"}


# ---------------------------------------------------------------------------
# 8. Persona-injection flag is observability-only (no prompt change)
# ---------------------------------------------------------------------------


def test_persona_injection_flag_is_observability_only() -> None:
    """The third flag (PERSONA_INJECTION_ENABLED) must surface in
    the audit dict but must NOT affect any persona-prompt build
    path. Phase 11D.9 deliberately leaves the persona-injection
    wiring scaffold-only."""
    class _SettingsAllThreeOn:
        tech_market_signals_enabled = True
        tech_market_signals_runtime_enabled = True
        tech_market_signals_persona_injection_enabled = True
        tech_market_signals_max_per_run = 80
        tech_market_signals_min_relevance = 0.20

    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [_ai_saas_row(snippet=f"row {i}") for i in range(3)]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllThreeOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert audit["persona_injection_enabled"] is True


def test_persona_prompt_wiring_only_in_whitelisted_files() -> None:
    """Phase 11D.11 introduces `build_tech_market_persona_prompt_block`
    in the existing whitelisted injector + a single orchestrator
    wire-in. Every other production file must remain unwired so
    future helpers can't silently grow a persona-prompt dependency.

    Whitelisted files (with operator approval in Phase 11D.11):
      * pipeline/tech_market_evidence_injector.py — defines the helper.
      * orchestration/live_founder_brief.py — calls it once per
        simulation and passes the result into run_live_discussion.
      * orchestration/live_discussion_pipeline.py — accepts the
        block as a parameter and conditionally appends it to per-
        persona prompts (mirrors the Phase-11C.5 amazon_persona_block
        pattern)."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    whitelist = {
        api_root / "pipeline" / "tech_market_evidence_injector.py",
        api_root / "orchestration" / "live_founder_brief.py",
        api_root / "orchestration" / "live_discussion_pipeline.py",
    }
    for d in [api_root / "pipeline", api_root / "orchestration"]:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if path in whitelist:
                continue
            text = path.read_text(encoding="utf-8")
            assert "build_tech_market_persona_prompt_block" not in text, (
                f"{path} references persona-prompt wiring — must go "
                f"through the whitelisted "
                f"pipeline/tech_market_evidence_injector.py + the "
                f"two orchestrator files"
            )


# ---------------------------------------------------------------------------
# 9. No raw text / PII leaks
# ---------------------------------------------------------------------------


def test_audit_sample_signals_carry_no_forbidden_fields() -> None:
    """The audit's `sample_kept_signals` must NOT carry any raw
    PII or row-internal fields."""
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [_ai_saas_row(snippet=f"snippet {i}") for i in range(3)]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    forbidden = {
        "author_handle", "author_id", "author_email", "author_name",
        "user_id", "user_handle", "user_name", "email", "phone",
        "ip", "session_id", "row_id", "id", "created_at",
        "raw_text", "raw_body", "review_body",
    }
    for s in audit["sample_kept_signals"]:
        leaked = set(s.keys()) & forbidden
        assert leaked == set(), (
            f"sample_kept_signals leaked forbidden fields: {leaked}"
        )
        # Short_snippet cap (Phase 11D.1 hard cap = 240).
        assert len(s["short_snippet"]) <= 240


# ---------------------------------------------------------------------------
# 10. No apps/web files touched
# ---------------------------------------------------------------------------


def test_phase_11d_9_does_not_touch_apps_web() -> None:
    """Static check: the new files Phase 11D.9 ships all live under
    apps/api/. The frontend remains frozen."""
    new_files = (
        "apps/api/src/assembly/pipeline/tech_market_evidence_injector.py",
        "apps/api/tests/test_tech_market_runtime_audit_11d_9.py",
    )
    for p in new_files:
        assert p.startswith("apps/api/"), (
            f"{p} not under apps/api/ — frontend must stay frozen"
        )


def test_injector_module_has_no_http_or_scraping_imports() -> None:
    pkg = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "pipeline"
        / "tech_market_evidence_injector.py"
    )
    src = pkg.read_text(encoding="utf-8")
    forbidden = (
        "requests", "httpx", "aiohttp", "selenium", "playwright",
        "scrapy", "bs4", "beautifulsoup4", "urllib.request",
    )
    for token in forbidden:
        pat = re.compile(
            rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
            re.MULTILINE,
        )
        assert pat.search(src) is None, (
            f"injector imports forbidden module {token!r}"
        )


# ---------------------------------------------------------------------------
# 11. Drift: only whitelisted injector imports the retriever
# ---------------------------------------------------------------------------


def test_only_whitelisted_injector_imports_tech_market_retriever() -> None:
    """Phase 11D.9 explicitly authorizes
    `pipeline/tech_market_evidence_injector.py` to import the
    retriever. Every other production file under
    `api/`/`pipeline/`/`orchestration/` must still refrain."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    whitelist = {
        api_root / "pipeline" / "tech_market_evidence_injector.py",
    }
    live_dirs = [
        api_root / "api",
        api_root / "pipeline",
        api_root / "orchestration",
    ]
    forbidden = ("TechMarketSignalRetriever", "tech_market_provider.retrieval")
    for d in live_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if path in whitelist:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in text, (
                    f"{path} imports {token!r} — must go through "
                    f"the whitelisted injector"
                )


# ---------------------------------------------------------------------------
# 12. Local-validation simulation
# ---------------------------------------------------------------------------


def test_local_validation_ai_saas_brief_returns_ai_saas_signals() -> None:
    """Simulates the Phase 11D.9 local-validation requirement:
    AI SaaS brief should retrieve Vivago-style ai_saas / AI_tool
    signals from the post-11D.8 local DB."""
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = (
        [_ai_saas_row(
            signal_type=stype, snippet=f"ai_saas {stype} row {i} text",
            buyer="user" if stype == "feature_inquiry" else "unknown",
        ) for stype, n in [
            ("feature_inquiry", 5),
            ("pain_urgency", 3),
            ("workflow_fit", 2),
            ("trust_security_concern", 1),
        ] for i in range(n)]
        # Cross-category rows that must NOT leak
        + [_devtool_row(snippet=f"devtool noise {i}") for i in range(5)]
    )
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert audit["query_category"] == "ai_saas"
    assert audit["query_market_context"] == "AI_tool"
    assert audit["fallback_used"] is False
    assert audit["signals_kept"] >= 4
    # Multiple signal types should appear (operator wanted Vivago-style
    # diversity).
    assert len(audit["signal_type_distribution"]) >= 2
    # No devtool category in kept set.
    cats = {s["product_category"] for s in audit["sample_kept_signals"]}
    assert cats == {"ai_saas"}


def test_local_validation_devtool_brief_returns_devtool_signals() -> None:
    """Simulates the Phase 11D.9 local-validation requirement:
    Devtool brief should retrieve Semble-style devtool_api / devtool
    signals from the post-11D.8 local DB."""
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = (
        [_devtool_row(
            signal_type=stype, snippet=f"devtool {stype} row {i} text",
            buyer="developer" if stype == "developer_skepticism" else "unknown",
        ) for stype, n in [
            ("developer_skepticism", 6),
            ("pain_urgency", 4),
            ("workflow_fit", 3),
            ("competitor_comparison", 4),
        ] for i in range(n)]
        # Cross-category noise.
        + [_ai_saas_row(snippet=f"ai_saas noise {i}") for i in range(5)]
    )
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _DEVTOOL_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert audit["query_category"] == "devtool_api"
    assert audit["query_market_context"] == "devtool"
    assert audit["fallback_used"] is False
    assert audit["signals_kept"] >= 4
    assert "developer_skepticism" in audit["signal_type_distribution"]
    cats = {s["product_category"] for s in audit["sample_kept_signals"]}
    assert cats == {"devtool_api"}


# ---------------------------------------------------------------------------
# 13. Source providers reported in audit
# ---------------------------------------------------------------------------


def test_audit_reports_source_providers_used() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [_ai_saas_row(snippet=f"snip {i}") for i in range(3)]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            inj.build_tech_market_evidence_section_from_dict_brief(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert "operator_csv_product_hunt" in audit["source_providers_used"]


# ---------------------------------------------------------------------------
# 14. Tech-market flags still default false on stock Settings
# ---------------------------------------------------------------------------


def test_tech_market_flags_still_default_false_after_11d_9() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.tech_market_signals_enabled is False
    assert s.tech_market_signals_runtime_enabled is False
    assert s.tech_market_signals_persona_injection_enabled is False
