"""Phase 8.2F.6 — extractor prompt + parser hardening tests.

Asserts:
  * SYSTEM_PROMPT contains explicit field-level extraction triggers
    for role_or_context, objection_patterns, trust_triggers, and
    current_alternatives — these are the four fields the Phase 8.2F.7
    audit identified as under-extracted.
  * SYSTEM_PROMPT explicitly forbids extra keys and markdown fences.
  * SYSTEM_PROMPT and USER_PROMPT contain a worked JSON example for
    the model to pattern-match.
  * `_strip_json_fences` correctly extracts the JSON object from:
      - markdown-fenced (```json {...} ```)
      - prose-prefixed ("Here is the result: {...}")
      - prose-suffixed ("{...} Let me know if …")
      - clean (already raw JSON)
  * The LLM extractor accepts a fenced response WITHOUT triggering a
    repair attempt — i.e. repair_attempts=0 on a previously-failing
    fenced shape.
"""
from __future__ import annotations

import json

import pytest

from assembly.llm.mock import MockProvider
from assembly.pipeline.persona_construction.extractor import (
    LLMTraitExtractor,
    _strip_json_fences,
)
from assembly.pipeline.persona_construction.grouping import (
    CandidatePersonaShell,
)
from assembly.pipeline.persona_construction.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
)
from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
)


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", [
    "role_or_context",
    "objection_patterns",
    "trust_triggers",
    "current_alternatives",
])
def test_system_prompt_lists_extraction_trigger_for_field(field: str) -> None:
    assert field in SYSTEM_PROMPT


def test_system_prompt_explicitly_forbids_markdown_fences() -> None:
    assert "markdown fences" in SYSTEM_PROMPT or "no markdown" in SYSTEM_PROMPT.lower()
    assert "no ```json" in SYSTEM_PROMPT or "```json" in SYSTEM_PROMPT


def test_system_prompt_forbids_extra_keys_explicitly() -> None:
    # "EXACTLY" wording or equivalent must appear at least twice.
    occurrences = SYSTEM_PROMPT.count("EXACTLY")
    assert occurrences >= 2


def test_user_prompt_contains_worked_example() -> None:
    user = build_user_prompt(aggregated_content="### record 1\nstub")
    assert '"traits"' in user
    assert '"role_or_context"' in user
    assert '"objection_patterns"' in user
    assert '"current_alternatives"' in user


def test_user_prompt_contains_complaint_trigger_signal() -> None:
    """The complaint-language trigger word "frustrated" or "fed up" or
    similar appears in the worked example so the model has at least one
    in-context illustration of when extraction should fire."""
    user = build_user_prompt(aggregated_content="### record 1\nstub")
    assert any(
        token in user.lower()
        for token in ("frustrated", "fed up", "overwhelming", "complaint")
    )


# ---------------------------------------------------------------------------
# JSON fence stripper
# ---------------------------------------------------------------------------


def test_strip_json_fences_passes_through_clean_json() -> None:
    s = '{"traits": []}'
    assert _strip_json_fences(s) == s


def test_strip_json_fences_strips_markdown_fence() -> None:
    s = '```json\n{"traits": []}\n```'
    out = _strip_json_fences(s)
    assert out == '{"traits": []}'


def test_strip_json_fences_strips_lowercase_fence() -> None:
    s = '```\n{"traits": []}\n```'
    out = _strip_json_fences(s)
    assert out == '{"traits": []}'


def test_strip_json_fences_strips_prose_prefix() -> None:
    s = 'Here is the JSON for the persona:\n{"traits": []}'
    out = _strip_json_fences(s)
    assert out == '{"traits": []}'


def test_strip_json_fences_strips_prose_suffix() -> None:
    s = '{"traits": []}\n\nLet me know if you need any adjustments.'
    out = _strip_json_fences(s)
    assert out == '{"traits": []}'


def test_strip_json_fences_strips_prose_prefix_and_suffix() -> None:
    s = (
        "Here is the result:\n```json\n"
        '{"traits": [{"field_name": "x"}]}\n'
        "```\n\nLet me know if needed."
    )
    out = _strip_json_fences(s)
    assert out == '{"traits": [{"field_name": "x"}]}'


def test_strip_json_fences_handles_multiline_json() -> None:
    s = '```json\n{\n  "traits": [\n    {"field_name": "x"}\n  ]\n}\n```'
    out = _strip_json_fences(s)
    parsed = json.loads(out)
    assert parsed == {"traits": [{"field_name": "x"}]}


def test_strip_json_fences_returns_input_when_no_braces() -> None:
    """No `{...}` substring → return the input so JSON parsing fails
    with a useful error rather than a silent empty parse."""
    s = "no json here"
    assert _strip_json_fences(s) == "no json here"


# ---------------------------------------------------------------------------
# Extractor: fenced response no longer needs a repair attempt
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_cost_guarded_chat(monkeypatch: pytest.MonkeyPatch):
    from assembly.llm import guarded_chat as guarded_chat_mod
    from assembly.llm.provider import LLMCallContext
    from assembly.pipeline.persona_construction import extractor as extractor_mod

    async def fake_cost_guarded_chat(
        *,
        sessionmaker, simulation_id, stage, messages, provider,
        model=None, hard_cap_usd=None, max_tokens=2048, temperature=0.4,
        capture_prompt_snapshot=True,
        estimated_prompt_tokens=4000, estimated_completion_tokens=1000,
    ):
        ctx = LLMCallContext(
            stage=stage,
            model=model or "test-model",
            simulation_id=simulation_id,
            max_tokens=max_tokens, temperature=temperature,
            capture_prompt_snapshot=capture_prompt_snapshot,
        )
        return await provider.chat(list(messages), ctx)

    monkeypatch.setattr(
        guarded_chat_mod, "cost_guarded_chat", fake_cost_guarded_chat,
    )
    monkeypatch.setattr(
        extractor_mod, "cost_guarded_chat", fake_cost_guarded_chat,
    )


def _shell_with(content: str) -> CandidatePersonaShell:
    return CandidatePersonaShell(
        shell_id="test_shell",
        record_ids=(),
        dominant_classification=SourceClassification.STRONG_PERSONA_SIGNAL,
        aggregated_content=content,
    )


@pytest.mark.asyncio
async def test_fenced_response_no_longer_triggers_repair_attempt(
    patched_cost_guarded_chat,
) -> None:
    """Pre-8.2F.6 the model's first emission was often markdown-fenced,
    which the parser rejected and triggered a repair. Now the parser
    strips the fence and accepts the inner JSON on the first call —
    repair_attempts must be 0."""
    shell = _shell_with(
        "### record 1\nI am a Shopify merchant doing $30k/month."
    )
    fenced = (
        "```json\n"
        + json.dumps({
            "traits": [
                {
                    "field_name": "role_or_context",
                    "support_level": "direct",
                    "value": "Shopify merchant",
                    "source_excerpt": "I am a Shopify merchant",
                    "confidence": 0.9,
                    "rationale": "self-description",
                },
            ],
        })
        + "\n```"
    )
    provider = MockProvider()
    provider.add_default(fenced)
    from uuid import uuid4
    ex = LLMTraitExtractor(
        sessionmaker=None,
        simulation_id=uuid4(),
        provider=provider,
        max_repair_attempts=0,
    )
    out = await ex.extract(shell)
    assert out.repair_attempts == 0
    assert len(out.candidates) == 1
    assert out.candidates[0].support_level == "direct"


@pytest.mark.asyncio
async def test_prose_wrapped_response_no_longer_triggers_repair(
    patched_cost_guarded_chat,
) -> None:
    shell = _shell_with(
        "### record 1\nI am a Shopify merchant doing $30k/month."
    )
    wrapped = (
        "Here is the JSON for the persona:\n\n"
        + json.dumps({
            "traits": [
                {
                    "field_name": "role_or_context",
                    "support_level": "direct",
                    "value": "Shopify merchant",
                    "source_excerpt": "I am a Shopify merchant",
                    "confidence": 0.9,
                    "rationale": "self-description",
                },
            ],
        })
        + "\n\nLet me know if you'd like adjustments."
    )
    provider = MockProvider()
    provider.add_default(wrapped)
    from uuid import uuid4
    ex = LLMTraitExtractor(
        sessionmaker=None,
        simulation_id=uuid4(),
        provider=provider,
        max_repair_attempts=0,
    )
    out = await ex.extract(shell)
    assert out.repair_attempts == 0
    assert len(out.candidates) == 1
