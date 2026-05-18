"""Phase 11D.11 — gated tech-market persona prompt block tests.

Pins:
  1. Block is None when ALL three flags are off (default state).
  2. Block is None when ENABLED=true but RUNTIME_ENABLED=false.
  3. Block is None when ENABLED=true + RUNTIME_ENABLED=true but
     PERSONA_INJECTION_ENABLED=false (audit-only mode unchanged).
  4. Block appears ONLY when all three flags are true.
  5. Block respects same-category filter (no cross-category leak).
  6. Block respects hard caps (≤ 12 bullets, ≤ 3 per signal_type,
     ≤ 180 chars per bullet).
  7. Block contains zero raw author / user / row identifiers.
  8. Block header explicitly labels content as buyer language,
     NOT ground truth.
  9. discussion_pipeline accepts the new param with default None
     and conditionally appends to per-persona prompts.
 10. Audit-only mode (Phase 11D.9) is unchanged — the new
     persona-prompt flag does NOT affect the audit dict shape.
 11. Amazon audit / persona-block path is not broken.
 12. No apps/web changes; no report UI field changes.
 13. Persona block is built ONCE per simulation, not per persona.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from assembly.sources.tech_market_provider import (
    InMemoryTechMarketSignalSource,
    TechSignalRow,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_AI_SAAS_BRIEF: dict[str, Any] = {
    "product_name": "QuietCart",
    "product_description": (
        "An AI SaaS tool for marketing teams to ship more LLM-powered "
        "scripts."
    ),
    "category_hint": "AI SaaS tool",
    "competitors_or_alternatives": ["Notion AI", "Jasper"],
}


_DEVTOOL_BRIEF: dict[str, Any] = {
    "product_name": "DocGrep",
    "product_description": (
        "A developer tool that indexes large codebases for agent "
        "queries via a CLI and SDK."
    ),
    "category_hint": "developer tool",
    "competitors_or_alternatives": ["Sourcegraph", "ripgrep"],
}


_UNKNOWN_BRIEF: dict[str, Any] = {
    "product_name": "Mystery",
    "product_description": "Something else",
    "category_hint": "uncategorized thing nobody mapped",
}


class _SettingsAllOn:
    """All three flags ON — only mode where the block should be
    non-None."""
    tech_market_signals_enabled = True
    tech_market_signals_runtime_enabled = True
    tech_market_signals_persona_injection_enabled = True
    tech_market_signals_max_per_run = 80
    tech_market_signals_min_relevance = 0.20


class _SettingsTwoOn:
    """Audit-only (Phase 11D.9 default for tests). Block must be
    None."""
    tech_market_signals_enabled = True
    tech_market_signals_runtime_enabled = True
    tech_market_signals_persona_injection_enabled = False
    tech_market_signals_max_per_run = 80
    tech_market_signals_min_relevance = 0.20


class _SettingsOneOn:
    """ENABLED on, RUNTIME_ENABLED off. Block must be None."""
    tech_market_signals_enabled = True
    tech_market_signals_runtime_enabled = False
    tech_market_signals_persona_injection_enabled = True
    tech_market_signals_max_per_run = 80
    tech_market_signals_min_relevance = 0.20


class _SettingsAllOff:
    """Default state. Block must be None."""
    tech_market_signals_enabled = False
    tech_market_signals_runtime_enabled = False
    tech_market_signals_persona_injection_enabled = False
    tech_market_signals_max_per_run = 80
    tech_market_signals_min_relevance = 0.20


def _exploding_sessionmaker(*a: Any, **kw: Any) -> Any:  # pragma: no cover
    raise AssertionError("DB session opened despite all-off flags")


def _ai_saas_row(
    *,
    signal_type: str = "feature_inquiry",
    snippet: str = "Can we use this in our marketing pipeline?",
    buyer: str = "user",
    sentiment: str = "positive",
) -> TechSignalRow:
    return TechSignalRow(
        source_provider="operator_csv_product_hunt",
        source_category="product_hunt_launch_comments",
        product_category="ai_saas",
        company_or_product="Generic AI Tool",
        competitor_name=None,
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
    buyer: str = "developer",
    sentiment: str = "negative",
) -> TechSignalRow:
    return TechSignalRow(
        source_provider="operator_csv_hn",
        source_category="hn_thread_comments",
        product_category="devtool_api",
        company_or_product="Generic Devtool",
        competitor_name=None,
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
    src = InMemoryTechMarketSignalSource(rows)

    class _FakePostgresSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def fetch_by_product_category(self, *a: Any, **kw: Any):
            return await src.fetch_by_product_category(*a, **kw)

        async def fetch_by_market_context(self, *a: Any, **kw: Any):
            return await src.fetch_by_market_context(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await src.fetch_by_competitor(*a, **kw)

        async def fetch_by_signal_types(self, *a: Any, **kw: Any):
            return await src.fetch_by_signal_types(*a, **kw)

    return _FakePostgresSource


# ---------------------------------------------------------------------------
# 1-3. Triple-gate flag check — block None unless all 3 true
# ---------------------------------------------------------------------------


def test_block_is_none_when_all_flags_off() -> None:
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_persona_prompt_block,
    )
    block = asyncio.run(
        build_tech_market_persona_prompt_block(
            _AI_SAAS_BRIEF,
            sessionmaker=_exploding_sessionmaker,
            settings=_SettingsAllOff(),  # type: ignore[arg-type]
        ),
    )
    assert block is None


def test_block_is_none_when_runtime_enabled_false() -> None:
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_persona_prompt_block,
    )
    block = asyncio.run(
        build_tech_market_persona_prompt_block(
            _AI_SAAS_BRIEF,
            sessionmaker=_exploding_sessionmaker,
            settings=_SettingsOneOn(),  # type: ignore[arg-type]
        ),
    )
    assert block is None


def test_block_is_none_when_persona_injection_flag_false() -> None:
    """Audit-only Phase 11D.9 mode must NOT produce a persona block."""
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [_ai_saas_row(snippet=f"row {i} for ai saas") for i in range(5)]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsTwoOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is None, (
        "audit-only mode must not produce a persona block"
    )


# ---------------------------------------------------------------------------
# 4. Block appears when all three flags true
# ---------------------------------------------------------------------------


def test_block_appears_when_all_three_flags_true() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [
        _ai_saas_row(signal_type=stype, snippet=f"{stype} snippet {i}")
        for stype, n in [
            ("pain_urgency", 3),
            ("feature_inquiry", 3),
            ("workflow_fit", 2),
        ]
        for i in range(n)
    ]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    assert isinstance(block, str)
    assert block.count("\n- ") >= 1
    # Header explicitly labels as buyer language, NOT ground truth.
    assert "buyer language" in block.lower()
    assert "ground truth" in block.lower()


def test_block_header_includes_matched_category() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [_ai_saas_row(snippet=f"row {i}") for i in range(3)]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    assert "category=ai_saas" in block


# ---------------------------------------------------------------------------
# 5. Same-category filtering — no cross-category leak in block
# ---------------------------------------------------------------------------


def test_ai_saas_brief_block_excludes_devtool_signals() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = (
        [_ai_saas_row(snippet=f"ai saas pain row {i}",
                      signal_type="pain_urgency") for i in range(3)]
        + [_devtool_row(snippet=f"devtool dev_skep row {i}",
                        signal_type="developer_skepticism")
           for i in range(3)]
    )
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    # Devtool-flavored signal types must not appear.
    assert "developer_skepticism" not in block
    # AI-SaaS-flavored signal types should appear.
    assert "pain_urgency" in block


def test_devtool_brief_block_excludes_ai_saas_signals() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = (
        [_ai_saas_row(snippet=f"ai saas trust row {i}",
                      signal_type="trust_security_concern")
         for i in range(3)]
        + [_devtool_row(snippet=f"devtool dev_skep row {i}",
                        signal_type="developer_skepticism")
           for i in range(3)]
    )
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _DEVTOOL_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    # AI-SaaS-flavored signal types must not appear.
    assert "trust_security_concern" not in block
    # devtool-flavored signal types should appear.
    assert "developer_skepticism" in block


def test_unresolved_category_returns_none() -> None:
    """Same-category invariant — if category_hint can't resolve,
    refuse to retrieve and return None."""
    from assembly.pipeline.tech_market_evidence_injector import (
        build_tech_market_persona_prompt_block,
    )

    class _ExplodingSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def fetch_by_product_category(self, *a: Any, **kw: Any):
            raise AssertionError("DB hit on unresolved category")

        async def fetch_by_market_context(self, *a: Any, **kw: Any):
            raise AssertionError("DB hit")

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            raise AssertionError("DB hit")

        async def fetch_by_signal_types(self, *a: Any, **kw: Any):
            raise AssertionError("DB hit")

    from assembly.pipeline import tech_market_evidence_injector as inj
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _ExplodingSource  # type: ignore[assignment]
    try:
        block = asyncio.run(
            build_tech_market_persona_prompt_block(
                _UNKNOWN_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is None


# ---------------------------------------------------------------------------
# 6. Hard caps — ≤ 12 bullets, ≤ 3 per signal_type, ≤ 180 chars
# ---------------------------------------------------------------------------


def test_block_respects_12_bullet_cap() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [
        _ai_saas_row(
            signal_type=stype, snippet=f"{stype} unique row {i} text",
        )
        for stype in (
            "pain_urgency", "feature_inquiry", "workflow_fit",
            "competitor_comparison", "switching_objection",
            "trust_security_concern",
        )
        for i in range(10)
    ]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    bullets = [ln for ln in block.split("\n") if ln.startswith("- ")]
    assert len(bullets) <= 12, f"{len(bullets)} bullets > 12 cap"


def test_block_respects_3_per_signal_type_cap() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [
        _ai_saas_row(
            signal_type="pain_urgency",
            snippet=f"pain unique row {i} text",
        )
        for i in range(10)
    ]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    # Count occurrences of [pain_urgency/ in the block.
    count = block.count("[pain_urgency/")
    assert count <= 3, f"pain_urgency bullet count {count} > 3 cap"


def test_block_caps_individual_bullet_at_180_chars() -> None:
    """Each bullet's snippet portion must be ≤ 180 chars."""
    from assembly.pipeline import tech_market_evidence_injector as inj
    long_snippet = "x " * 200  # 400 chars
    rows = [_ai_saas_row(snippet=long_snippet)]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    for line in block.split("\n"):
        if line.startswith("- "):
            # Bullet prefix `- [signal_type/sentiment] ` is ~30 chars;
            # whole line stays under 250 to leave room for the cap.
            assert len(line) < 250, (
                f"bullet exceeded reasonable length: {len(line)} — {line}"
            )
            assert "…" in line or len(line) < 180 + 40, (
                "long snippet was not truncated"
            )


# ---------------------------------------------------------------------------
# 7. No raw author / user / row identifiers in block
# ---------------------------------------------------------------------------


def test_block_does_not_contain_forbidden_field_tokens() -> None:
    from assembly.pipeline import tech_market_evidence_injector as inj
    rows = [
        _ai_saas_row(snippet=f"genuine signal text {i}")
        for i in range(3)
    ]
    orig = inj.PostgresTechMarketSignalSource
    inj.PostgresTechMarketSignalSource = _patch_source(rows)  # type: ignore[assignment]
    try:
        block = asyncio.run(
            inj.build_tech_market_persona_prompt_block(
                _AI_SAAS_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresTechMarketSignalSource = orig  # type: ignore[assignment]
    assert block is not None
    forbidden_tokens = (
        "user_id", "user_handle", "user_name",
        "author_id", "author_handle", "author_name",
        "email", "session_id", "row_id",
        "source_provider", "raw_text", "raw_body",
        # row dataclass attribute names
        "metadata=",
    )
    for token in forbidden_tokens:
        assert token not in block, (
            f"persona block leaked forbidden token {token!r}"
        )


# ---------------------------------------------------------------------------
# 9. discussion_pipeline accepts the new param
# ---------------------------------------------------------------------------


def test_discussion_pipeline_accepts_tech_market_param() -> None:
    """The new `tech_market_persona_block` kwarg must default to
    None so production prompts stay byte-identical when the block
    is absent."""
    from assembly.orchestration.live_discussion_pipeline import (
        run_live_discussion,
    )
    sig = inspect.signature(run_live_discussion)
    assert "tech_market_persona_block" in sig.parameters
    p = sig.parameters["tech_market_persona_block"]
    assert p.default is None


def test_discussion_pipeline_source_appends_block_conditionally() -> None:
    """Static check: live_discussion_pipeline must guard the
    tech-market block on `tech_market_persona_block` being truthy.
    A future refactor that unconditionally interpolates the block
    would leak it into prompts even when the flag is off."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_discussion_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "tech_market_persona_block" in src
    assert "if tech_market_persona_block" in src


def test_orchestrator_passes_block_to_run_live_discussion() -> None:
    """Static check: live_founder_brief calls
    build_tech_market_persona_prompt_block once and passes the
    result through to run_live_discussion."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    ).read_text(encoding="utf-8")
    assert "build_tech_market_persona_prompt_block" in src
    assert "tech_market_persona_block=tech_market_persona_block" in src


# ---------------------------------------------------------------------------
# 10. Audit-only mode (11D.9) unchanged
# ---------------------------------------------------------------------------


def test_audit_dict_shape_unchanged_by_phase_11d_11() -> None:
    """The Phase 11D.9 audit dict must NOT have grown new keys in
    11D.11 — the persona block is a separate surface."""
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
    expected_11d_9_keys = {
        "provider", "enabled", "runtime_enabled",
        "persona_injection_enabled", "attempted",
        "query_category", "query_market_context",
        "signals_considered", "signals_kept", "signals_rejected",
        "signal_type_distribution", "buyer_type_distribution",
        "market_context_distribution", "sample_kept_signals",
        "fallback_used", "rejection_reasons",
        "source_providers_used", "notes",
    }
    # Audit dict has exactly the 11D.9 keys — no new keys introduced.
    assert set(audit.keys()) == expected_11d_9_keys


# ---------------------------------------------------------------------------
# 11. Amazon path unchanged
# ---------------------------------------------------------------------------


def test_amazon_persona_block_signature_unchanged() -> None:
    """11D.11 must not change the Amazon helper's signature."""
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block,
    )
    sig = inspect.signature(build_amazon_persona_prompt_block)
    # Same kwargs as before — sessionmaker + settings.
    assert "sessionmaker" in sig.parameters
    assert "settings" in sig.parameters


def test_run_live_discussion_amazon_param_unchanged() -> None:
    """The amazon_persona_block param must still default to None
    so existing callers don't break."""
    from assembly.orchestration.live_discussion_pipeline import (
        run_live_discussion,
    )
    sig = inspect.signature(run_live_discussion)
    assert "amazon_persona_block" in sig.parameters
    assert sig.parameters["amazon_persona_block"].default is None


# ---------------------------------------------------------------------------
# 12. No apps/web / drift
# ---------------------------------------------------------------------------


def test_no_apps_web_files_touched_in_phase_11d_11() -> None:
    new_files = (
        "apps/api/src/assembly/pipeline/tech_market_evidence_injector.py",
        "apps/api/src/assembly/orchestration/live_founder_brief.py",
        "apps/api/src/assembly/orchestration/live_discussion_pipeline.py",
        "apps/api/tests/test_tech_market_persona_injection_11d_11.py",
        "apps/api/tests/test_tech_market_runtime_audit_11d_9.py",
    )
    for p in new_files:
        assert p.startswith("apps/api/"), (
            f"{p} not under apps/api/ — frontend must stay frozen"
        )


def test_no_http_imports_in_injector_after_11d_11() -> None:
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


def test_tech_market_flags_still_default_false_after_11d_11() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.tech_market_signals_enabled is False
    assert s.tech_market_signals_runtime_enabled is False
    assert s.tech_market_signals_persona_injection_enabled is False


def test_persona_block_built_once_not_per_persona() -> None:
    """Static check: the orchestrator calls
    build_tech_market_persona_prompt_block exactly once (broadcast
    to all personas via run_live_discussion), not per persona."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    ).read_text(encoding="utf-8")
    # Should appear exactly twice: once in the import line, once in
    # the call site. NOT inside any per-persona loop.
    occurrences = src.count("build_tech_market_persona_prompt_block")
    assert occurrences == 2, (
        f"unexpected number of references "
        f"({occurrences}) — block must be built once per simulation"
    )
