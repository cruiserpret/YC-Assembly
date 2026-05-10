"""Phase 6.5: tests for the workers package.

We bypass `Settings(...)` (which would read .env and pick up the user's
real keys) by using a plain SimpleNamespace fake — the worker only reads
five attributes from settings, so a bare namespace is enough.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from assembly.llm.errors import LLMProviderError
from assembly.workers import pipeline_worker


def _fake_settings(**kwargs) -> SimpleNamespace:
    defaults = dict(
        anthropic_api_key=None,
        openai_api_key=None,
        llm_primary_provider="anthropic",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_build_provider_picks_anthropic_when_primary(monkeypatch):
    monkeypatch.setattr(
        pipeline_worker, "get_settings",
        lambda: _fake_settings(
            anthropic_api_key="sk-ant-xxx",
            openai_api_key="sk-xxx",
        ),
    )
    from assembly.llm import anthropic as anthropic_mod
    monkeypatch.setattr(
        anthropic_mod, "AnthropicProvider",
        lambda **_kw: type("_FakeAnthropic", (), {"name": "anthropic"})(),
    )
    p = pipeline_worker.build_provider()
    assert p.name == "anthropic"


def test_build_provider_falls_back_to_openai(monkeypatch):
    monkeypatch.setattr(
        pipeline_worker, "get_settings",
        lambda: _fake_settings(
            anthropic_api_key=None,
            openai_api_key="sk-xxx",
        ),
    )
    from assembly.llm import openai as openai_mod
    monkeypatch.setattr(
        openai_mod, "OpenAIProvider",
        lambda **_kw: type("_FakeOpenAI", (), {"name": "openai"})(),
    )
    p = pipeline_worker.build_provider()
    assert p.name == "openai"


def test_build_provider_raises_when_no_key(monkeypatch):
    monkeypatch.setattr(
        pipeline_worker, "get_settings",
        lambda: _fake_settings(),
    )
    with pytest.raises(LLMProviderError):
        pipeline_worker.build_provider()
