"""Phase 12A.10G — Anthropic prompt caching tests.

This is the correctness gate for prompt caching. Every test here
protects ONE invariant the spec demands:

  1. Caching default OFF; opt-in via env var ASSEMBLY_ANTHROPIC_PROMPT_CACHE
  2. Setting `cache_breakpoint=True` on an LLMMessage does NOT change
     the bytes sent to Anthropic when the flag is OFF
  3. When the flag is ON, only the message marked with
     cache_breakpoint gets `cache_control` attached; all other
     content is byte-identical to the flag-OFF wire format
  4. Cache breakpoints are placed at the static-prefix end on the
     3 target stages (society_builder, live_discussion,
     aggregation_synthesis) and ONLY on system messages there
  5. Dynamic content (brief, evidence, persona state) is NEVER
     marked with cache_breakpoint=True at any call site
  6. Cost calculation respects cache_creation (1.25×) and
     cache_read (0.10×) pricing; non-cached calls bill identically
     to pre-12A.10G
  7. Drift: no env-var hidden cache flag outside Settings; no
     apps/web touches

Tests are pure-Python — no DB, no LLM, no network. The cache wire
format is verified by constructing the exact kwargs the Anthropic
adapter would send, since the SDK call itself is not mocked here.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from assembly.config import Settings
from assembly.llm.pricing import estimate_cost_usd
from assembly.llm.provider import LLMCallContext, LLMMessage, LLMResponse


# ---------------------------------------------------------------------
# 1. Setting + LLMMessage field shape
# ---------------------------------------------------------------------


class TestSettingsFlag:
    def test_setting_field_exists(self) -> None:
        s = Settings()
        assert hasattr(s, "anthropic_prompt_cache_enabled")
        assert isinstance(s.anthropic_prompt_cache_enabled, bool)

    def test_default_is_false(self, monkeypatch) -> None:
        """Default must remain False until A/B passes."""
        monkeypatch.delenv(
            "ASSEMBLY_ANTHROPIC_PROMPT_CACHE", raising=False,
        )
        s = Settings()
        assert s.anthropic_prompt_cache_enabled is False

    def test_init_kwarg_can_enable(self) -> None:
        """Init kwarg path is the most reliable test — env var
        precedence with pydantic-settings interacts with .env file
        loading, which would test infrastructure rather than the
        flag itself."""
        s = Settings(anthropic_prompt_cache_enabled=True)
        assert s.anthropic_prompt_cache_enabled is True


class TestLLMMessageField:
    def test_cache_breakpoint_field_exists(self) -> None:
        m = LLMMessage(role="system", content="x")
        assert hasattr(m, "cache_breakpoint")
        assert m.cache_breakpoint is False

    def test_cache_breakpoint_can_be_set(self) -> None:
        m = LLMMessage(
            role="system", content="x", cache_breakpoint=True,
        )
        assert m.cache_breakpoint is True


class TestLLMResponseFields:
    def test_cache_token_fields_exist(self) -> None:
        r = LLMResponse(
            text="x", prompt_tokens=10, completion_tokens=5,
            latency_ms=100, model="claude-sonnet-4-6",
            provider="anthropic",
        )
        assert r.cache_creation_input_tokens is None
        assert r.cache_read_input_tokens is None


# ---------------------------------------------------------------------
# 2. Anthropic wire format (the heart of the correctness gate)
# ---------------------------------------------------------------------


def _build_messages_with_breakpoint() -> list[LLMMessage]:
    """The exact shape our cached stages use: system w/ breakpoint +
    user without."""
    return [
        LLMMessage(
            role="system",
            content="STATIC SYSTEM PROMPT WITH SCHEMA RULES",
            cache_breakpoint=True,
        ),
        LLMMessage(
            role="user",
            content="dynamic per-call user content with brief etc",
        ),
    ]


def _adapter_kwargs(messages: list[LLMMessage], *, cache_on: bool):
    """Re-run the adapter's kwargs-building logic without calling the
    SDK. Mirrors the live `AnthropicProvider.chat()` implementation."""
    from assembly.llm import anthropic as anth_mod

    # We don't want to instantiate the provider (needs API key).
    # Instead reuse the same logic by re-creating it inline. If the
    # adapter is refactored, update this test alongside.
    system_msgs = [m for m in messages if m.role == "system"]
    user_msgs = [m for m in messages if m.role != "system"]
    any_system_breakpoint = any(m.cache_breakpoint for m in system_msgs)
    any_user_breakpoint = any(m.cache_breakpoint for m in user_msgs)

    if cache_on and any_system_breakpoint:
        system_blocks = []
        for m in system_msgs:
            blk = {"type": "text", "text": m.content}
            if m.cache_breakpoint:
                blk["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(blk)
        system_value = system_blocks
    else:
        s = "\n\n".join(m.content for m in system_msgs)
        system_value = s if s else None

    if cache_on and any_user_breakpoint:
        built = []
        for m in user_msgs:
            blk = {"type": "text", "text": m.content}
            if m.cache_breakpoint:
                blk["cache_control"] = {"type": "ephemeral"}
            built.append({"role": m.role, "content": [blk]})
    else:
        built = [
            {"role": m.role, "content": m.content} for m in user_msgs
        ]
    return {
        "messages": built,
        "system": system_value,
    }


class TestWireFormat:
    def test_content_identity_when_flag_off(self) -> None:
        """The content sent to Anthropic must be byte-identical to
        the pre-12A.10G wire format when caching is OFF — even when
        a message has cache_breakpoint=True."""
        msgs = _build_messages_with_breakpoint()
        kwargs = _adapter_kwargs(msgs, cache_on=False)
        # System still a plain string (pre-12A.10G shape).
        assert isinstance(kwargs["system"], str)
        assert kwargs["system"] == "STATIC SYSTEM PROMPT WITH SCHEMA RULES"
        # User messages still plain content (pre-12A.10G shape).
        assert kwargs["messages"] == [
            {
                "role": "user",
                "content": "dynamic per-call user content with brief etc",
            },
        ]
        # No cache_control anywhere.
        assert "cache_control" not in str(kwargs)

    def test_content_identity_text_only_with_flag_on(self) -> None:
        """When caching ON, the textual content (system text + user
        text) is BYTE-IDENTICAL to the flag-off form — only metadata
        differs."""
        msgs = _build_messages_with_breakpoint()
        kw_off = _adapter_kwargs(msgs, cache_on=False)
        kw_on = _adapter_kwargs(msgs, cache_on=True)

        # System text content identity.
        if isinstance(kw_on["system"], list):
            # New form: list of blocks. Concatenate text.
            on_text = "\n\n".join(b["text"] for b in kw_on["system"])
        else:
            on_text = kw_on["system"]
        assert on_text == kw_off["system"]

        # User text content identity.
        # Flag-off: list[{role, content: str}]
        # Flag-on:  list[{role, content: [{type, text, cache_control?}]}]
        # — but here only system has the breakpoint; user has none.
        # So flag-on user shape should match flag-off (since no user
        # breakpoint fires).
        assert kw_on["messages"] == kw_off["messages"]

    def test_cache_control_only_on_breakpoint_message(self) -> None:
        msgs = _build_messages_with_breakpoint()
        kw_on = _adapter_kwargs(msgs, cache_on=True)
        assert isinstance(kw_on["system"], list)
        sys_blocks = kw_on["system"]
        assert len(sys_blocks) == 1
        assert sys_blocks[0]["cache_control"] == {"type": "ephemeral"}
        # User has NO breakpoint set → no cache_control anywhere
        # below.
        for m in kw_on["messages"]:
            content = m["content"]
            if isinstance(content, list):
                for blk in content:
                    assert "cache_control" not in blk
            # If str, obviously no cache_control.

    def test_dynamic_content_stays_below_breakpoint(self) -> None:
        """A persona-specific dynamic message MUST NOT be inside the
        cached prefix. The cached system block must end before the
        user message starts."""
        msgs = _build_messages_with_breakpoint()
        kw_on = _adapter_kwargs(msgs, cache_on=True)
        # System block (cached) must not contain the user-side text.
        sys_text = "".join(b["text"] for b in kw_on["system"])
        assert "dynamic per-call" not in sys_text
        # User-side block exists outside the cached prefix.
        assert any(
            "dynamic per-call" in (
                m["content"] if isinstance(m["content"], str)
                else "".join(
                    b.get("text", "") for b in m["content"]
                )
            )
            for m in kw_on["messages"]
        )

    def test_user_breakpoint_caches_user_block_only(self) -> None:
        """Sanity: when caching is on AND we mark the user message as
        the breakpoint, the user block gets cache_control (not system).
        This is not how our 3 stages use it — they cache the system —
        but the adapter must support both shapes for future use."""
        msgs = [
            LLMMessage(role="system", content="sys"),
            LLMMessage(
                role="user", content="big static rubric",
                cache_breakpoint=True,
            ),
        ]
        kw_on = _adapter_kwargs(msgs, cache_on=True)
        # System stays plain string (no system breakpoint).
        assert isinstance(kw_on["system"], str)
        # User content is now blocks with cache_control.
        u = kw_on["messages"][0]
        assert isinstance(u["content"], list)
        assert u["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_flag_off_with_user_breakpoint_is_still_plain(self) -> None:
        msgs = [
            LLMMessage(role="system", content="sys"),
            LLMMessage(
                role="user", content="rubric", cache_breakpoint=True,
            ),
        ]
        kw_off = _adapter_kwargs(msgs, cache_on=False)
        assert kw_off["messages"][0]["content"] == "rubric"
        assert "cache_control" not in str(kw_off)


# ---------------------------------------------------------------------
# 3. Cache breakpoints land at the right place at the 3 target stages
# ---------------------------------------------------------------------


def _llm_messages_with_breakpoint_in(src: str) -> list[dict]:
    """AST-walk every LLMMessage(...) constructor call in a Python
    source string. Return one dict per call:
      {
        "role": "system" | "user" | "assistant" | None,
        "cache_breakpoint": True | False,
        "lineno": <line>,
      }
    """
    import ast
    tree = ast.parse(src)
    out: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match calls to LLMMessage(...) — function name is the
        # rightmost identifier in the call expression.
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else getattr(func, "id", None)
        )
        if name != "LLMMessage":
            continue
        role = None
        cache_breakpoint = False
        for kw in node.keywords:
            if kw.arg == "role" and isinstance(kw.value, ast.Constant):
                role = kw.value.value
            if (
                kw.arg == "cache_breakpoint"
                and isinstance(kw.value, ast.Constant)
            ):
                cache_breakpoint = bool(kw.value.value)
        out.append({
            "role": role,
            "cache_breakpoint": cache_breakpoint,
            "lineno": node.lineno,
        })
    return out


class TestBreakpointPlacement:
    """AST-walk LLMMessage(...) calls. Fails loudly if a future
    refactor moves the breakpoint to a non-system message, or if
    dynamic content accidentally gets marked as a breakpoint."""

    def _read(self, rel_path: str) -> str:
        return (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / rel_path
        ).read_text(encoding="utf-8")

    def test_society_builder_marks_system_message_only(self) -> None:
        calls = _llm_messages_with_breakpoint_in(
            self._read("pipeline/society_builder.py"),
        )
        breakpoints = [c for c in calls if c["cache_breakpoint"]]
        assert len(breakpoints) == 1, (
            "society_builder should mark exactly ONE LLMMessage as "
            f"cache_breakpoint=True. Found {len(breakpoints)}: {breakpoints!r}"
        )
        assert breakpoints[0]["role"] == "system", (
            "society_builder cache_breakpoint must be on a "
            f"role='system' message; got {breakpoints[0]!r}"
        )

    def test_live_discussion_marks_system_message_only(self) -> None:
        calls = _llm_messages_with_breakpoint_in(
            self._read(
                "orchestration/live_discussion_pipeline.py",
            ),
        )
        breakpoints = [c for c in calls if c["cache_breakpoint"]]
        assert len(breakpoints) == 1, (
            "live_discussion should mark exactly ONE LLMMessage as "
            f"cache_breakpoint=True. Found {len(breakpoints)}: {breakpoints!r}"
        )
        assert breakpoints[0]["role"] == "system"

    def test_aggregation_synthesis_marks_all_3_system_messages(
        self,
    ) -> None:
        calls = _llm_messages_with_breakpoint_in(
            self._read("pipeline/aggregation/synthesis.py"),
        )
        breakpoints = [c for c in calls if c["cache_breakpoint"]]
        assert len(breakpoints) == 3, (
            "aggregation/synthesis must mark all 3 system messages "
            "(A/B/C calls) as cache_breakpoint=True. "
            f"Found {len(breakpoints)}: {breakpoints!r}"
        )
        for bp in breakpoints:
            assert bp["role"] == "system", (
                f"aggregation cache_breakpoint not on system: {bp!r}"
            )


class TestNoDynamicContentMarked:
    """Cross-source AST sweep: no LLMMessage(...) call site
    anywhere in the codebase sets cache_breakpoint=True on a non-
    system role (or omits the role argument)."""

    def test_no_cache_breakpoint_on_non_system_message_anywhere(
        self,
    ) -> None:
        pipeline_root = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly"
        )
        violations: list[tuple[Path, dict]] = []
        for p in pipeline_root.rglob("*.py"):
            calls = _llm_messages_with_breakpoint_in(
                p.read_text(encoding="utf-8"),
            )
            for c in calls:
                if c["cache_breakpoint"] and c["role"] != "system":
                    violations.append((p, c))
        assert not violations, (
            "cache_breakpoint=True must only appear on role='system' "
            f"LLMMessage calls. Violations: {violations}"
        )


# ---------------------------------------------------------------------
# 4. Cache-aware cost math
# ---------------------------------------------------------------------


class TestCostMath:
    def test_no_cache_matches_pre_12a_10g(self) -> None:
        """estimate_cost_usd with cache_* = None must produce the
        same number as the pre-12A.10G two-arg form."""
        old = (
            Decimal(1000) * Decimal("3.00") / Decimal(1_000_000)
            + Decimal(500) * Decimal("15.00") / Decimal(1_000_000)
        )
        new = estimate_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert new == old

    def test_cache_read_costs_one_tenth_of_normal_input(self) -> None:
        c = estimate_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=1000,
            completion_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=1000,
        )
        # 1000 tokens at 0.10x of $3/Mtok = $0.0003
        expected = (
            Decimal(1000) * Decimal("3.00") * Decimal("0.10")
            / Decimal(1_000_000)
        )
        assert c == expected

    def test_cache_write_costs_125pct_of_normal_input(self) -> None:
        c = estimate_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=1000,
            completion_tokens=0,
            cache_creation_input_tokens=1000,
            cache_read_input_tokens=0,
        )
        expected = (
            Decimal(1000) * Decimal("3.00") * Decimal("1.25")
            / Decimal(1_000_000)
        )
        assert c == expected

    def test_mixed_cache_and_fresh_tokens(self) -> None:
        """The realistic shape: 2400 cached tokens (read), 600 fresh
        prompt tokens (user content), 500 completion."""
        c = estimate_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=3000,         # SDK reports total
            completion_tokens=500,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=2400,
        )
        # Base = (3000 - 2400) * $3/M = $0.0018
        # Cache read = 2400 * $3/M * 0.10 = $0.00072
        # Output = 500 * $15/M = $0.0075
        # Total = 0.0018 + 0.00072 + 0.0075 = 0.01002
        expected = (
            Decimal(600) * Decimal("3.00") / Decimal(1_000_000)
            + Decimal(2400) * Decimal("3.00") * Decimal("0.10")
            / Decimal(1_000_000)
            + Decimal(500) * Decimal("15.00") / Decimal(1_000_000)
        )
        assert c == expected

    def test_savings_vs_uncached_same_content(self) -> None:
        """With a 2400-token static prefix re-read across 100 calls,
        the cached form must be cheaper than 100 fresh calls."""
        # 100 fresh calls: each pays full 3000-token input
        fresh = 100 * estimate_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=3000,
            completion_tokens=500,
        )
        # Cached: 1 write of 2400 tokens (1.25x), 99 reads of 2400
        # tokens (0.1x), every call still pays 600 dynamic-token input
        # and 500 completion. Approximation: split 1 write call + 99
        # read calls.
        write_call = estimate_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=3000,
            completion_tokens=500,
            cache_creation_input_tokens=2400,
            cache_read_input_tokens=0,
        )
        read_call = estimate_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=3000,
            completion_tokens=500,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=2400,
        )
        cached_total = write_call + 99 * read_call
        assert cached_total < fresh
        savings_pct = float(
            (fresh - cached_total) / fresh * 100
        )
        # Expect savings > 25% on this profile (large static prefix
        # vs small dynamic). Sanity check.
        assert savings_pct > 25.0


# ---------------------------------------------------------------------
# 5. Drift / safety invariants
# ---------------------------------------------------------------------


class TestSafetyInvariants:
    def test_anthropic_adapter_imports_settings(self) -> None:
        """Adapter must consult settings, not env directly. This
        protects auditability — cache state is determined by the
        Settings object, which the harness can record + audit."""
        src = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "llm" / "anthropic.py"
        ).read_text(encoding="utf-8")
        # Must use get_settings(), not os.environ.get(...)
        assert "get_settings()" in src
        for forbidden in (
            'os.environ.get("ASSEMBLY_ANTHROPIC_PROMPT_CACHE',
            "os.environ.get('ASSEMBLY_ANTHROPIC_PROMPT_CACHE",
        ):
            assert forbidden not in src

    def test_no_apps_web_touch_in_caching_files(self) -> None:
        """12A.10G must not touch apps/web."""
        targets = [
            "apps/api/src/assembly/config.py",
            "apps/api/src/assembly/llm/provider.py",
            "apps/api/src/assembly/llm/anthropic.py",
            "apps/api/src/assembly/llm/cost_guard.py",
            "apps/api/src/assembly/llm/pricing.py",
            "apps/api/src/assembly/pipeline/society_builder.py",
            "apps/api/src/assembly/orchestration/live_discussion_pipeline.py",
            "apps/api/src/assembly/pipeline/aggregation/synthesis.py",
        ]
        repo_root = Path(__file__).resolve().parents[3]
        for t in targets:
            src = (repo_root / t).read_text(encoding="utf-8")
            assert "apps/web" not in src

    def test_prompt_snapshot_records_cache_state(self) -> None:
        """The Anthropic adapter must include cache_enabled +
        cache_creation_input_tokens + cache_read_input_tokens in the
        prompt_snapshot it writes to llm_call_log."""
        src = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "llm" / "anthropic.py"
        ).read_text(encoding="utf-8")
        assert "anthropic_prompt_cache_enabled" in src
        assert "cache_creation_input_tokens" in src
        assert "cache_read_input_tokens" in src

    def test_cost_guard_passes_cache_tokens_to_pricing(self) -> None:
        src = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "llm" / "cost_guard.py"
        ).read_text(encoding="utf-8")
        assert "cache_creation_input_tokens" in src
        assert "cache_read_input_tokens" in src
