"""Phase 8.2F — trait extractor tests (pure, no DB)."""
from __future__ import annotations

import json

import pytest

from assembly.llm.mock import MockProvider
from assembly.pipeline.persona_construction.extractor import (
    LLMTraitExtractor,
    MockTraitExtractor,
    TraitCandidate,
)
from assembly.pipeline.persona_construction.grouping import (
    CandidatePersonaShell,
)
from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
)


def _shell(
    *,
    aggregated_content: str = (
        "### record 1\nI am a Shopify merchant doing about $30k/month and "
        "I switched away from BigCommerce last year."
    ),
) -> CandidatePersonaShell:
    return CandidatePersonaShell(
        shell_id="testshell0001",
        record_ids=(),
        dominant_classification=SourceClassification.STRONG_PERSONA_SIGNAL,
        aggregated_content=aggregated_content,
    )


# ---------------------------------------------------------------------------
# Mock extractor returns its configured candidates only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_extractor_returns_configured_candidates() -> None:
    cands = (
        TraitCandidate(
            field_name="role_or_context",
            support_level="direct",
            value="Shopify merchant doing $30k/month",
            source_excerpt="I am a Shopify merchant",
            confidence=0.9,
            rationale="explicit self-description",
        ),
    )
    ex = MockTraitExtractor(candidates=cands)
    out = await ex.extract(_shell())
    assert out.candidates == cands
    assert out.shell_id == "testshell0001"


@pytest.mark.asyncio
async def test_mock_extractor_keys_by_shell_id() -> None:
    cands_a = (TraitCandidate(
        field_name="role_or_context",
        support_level="direct",
        value="merchant",
        source_excerpt="I am a Shopify merchant",
        confidence=0.9,
        rationale="x",
    ),)
    cands_b = ()
    ex = MockTraitExtractor(
        candidates_by_shell={"testshell0001": cands_a, "other": cands_b},
    )
    out = await ex.extract(_shell())
    assert out.candidates == cands_a


# ---------------------------------------------------------------------------
# LLM extractor: schema-conforming output → candidate list
# (uses MockProvider — never hits the network; cost_guard is patched)
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_cost_guarded_chat(monkeypatch: pytest.MonkeyPatch):
    """Bypass cost_guard for these tests — same pattern as
    `bypass_cost_guarded_chat` in conftest.py but local to extract."""
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
            max_tokens=max_tokens,
            temperature=temperature,
            capture_prompt_snapshot=capture_prompt_snapshot,
        )
        return await provider.chat(list(messages), ctx)

    monkeypatch.setattr(
        guarded_chat_mod, "cost_guarded_chat", fake_cost_guarded_chat,
    )
    monkeypatch.setattr(
        extractor_mod, "cost_guarded_chat", fake_cost_guarded_chat,
    )


@pytest.mark.asyncio
async def test_llm_extractor_drops_unquoted_excerpt(
    patched_cost_guarded_chat,
) -> None:
    """If the LLM emits a `source_excerpt` that does NOT appear verbatim
    in the shell's aggregated content, the extractor downgrades the
    trait to 'unknown' rather than fabricating a binding."""
    shell = _shell(
        aggregated_content="### record 1\nI run a small Shopify shop.",
    )
    bad_resp = json.dumps({
        "traits": [
            {
                "field_name": "role_or_context",
                "support_level": "direct",
                "value": "Pretend Shopify merchant",
                "source_excerpt": "I am a totally different sentence.",
                "confidence": 0.9,
                "rationale": "fake quote",
            },
        ],
    })
    provider = MockProvider()
    provider.add_default(bad_resp)
    from uuid import uuid4
    ex = LLMTraitExtractor(
        sessionmaker=None,
        simulation_id=uuid4(),
        provider=provider,
        max_repair_attempts=0,
    )
    out = await ex.extract(shell)
    assert len(out.candidates) == 1
    assert out.candidates[0].support_level == "unknown"
    assert out.candidates[0].value is None


@pytest.mark.asyncio
async def test_llm_extractor_keeps_well_quoted_trait(
    patched_cost_guarded_chat,
) -> None:
    shell = _shell()
    resp = json.dumps({
        "traits": [
            {
                "field_name": "role_or_context",
                "support_level": "direct",
                "value": "Shopify merchant doing about $30k/month",
                "source_excerpt": "I am a Shopify merchant doing about $30k/month",
                "confidence": 0.9,
                "rationale": "explicit self-description",
            },
        ],
    })
    provider = MockProvider()
    provider.add_default(resp)
    from uuid import uuid4
    ex = LLMTraitExtractor(
        sessionmaker=None,
        simulation_id=uuid4(),
        provider=provider,
        max_repair_attempts=0,
    )
    out = await ex.extract(shell)
    assert len(out.candidates) == 1
    assert out.candidates[0].support_level == "direct"


@pytest.mark.asyncio
async def test_llm_extractor_extra_field_is_rejected_by_schema(
    patched_cost_guarded_chat,
) -> None:
    """Pydantic with `extra='forbid'` should reject any extra field. The
    extractor returns an empty candidate list when parsing fails."""
    shell = _shell()
    resp = json.dumps({
        "traits": [
            {
                "field_name": "role_or_context",
                "support_level": "direct",
                "value": "merchant",
                "source_excerpt": "I am a Shopify merchant",
                "confidence": 0.9,
                "rationale": "x",
                "ARBITRARY_EXTRA_FIELD": "should be rejected",
            },
        ],
    })
    provider = MockProvider()
    provider.add_default(resp)
    from uuid import uuid4
    ex = LLMTraitExtractor(
        sessionmaker=None,
        simulation_id=uuid4(),
        provider=provider,
        max_repair_attempts=0,
    )
    out = await ex.extract(shell)
    # Parsing failed because Pydantic rejected the extra field; the
    # extractor returns no candidates after exhausting repair attempts.
    assert out.candidates == ()


@pytest.mark.asyncio
async def test_llm_extractor_repair_attempts_recorded(
    patched_cost_guarded_chat,
) -> None:
    """First response is malformed; second is valid. The extractor
    records the repair attempt count."""
    shell = _shell()
    bad = "this is not json"
    good = json.dumps({
        "traits": [
            {
                "field_name": "role_or_context",
                "support_level": "direct",
                "value": "Shopify merchant",
                "source_excerpt": "I am a Shopify merchant",
                "confidence": 0.8,
                "rationale": "x",
            },
        ],
    })
    provider = MockProvider()
    # First-call returns bad, second returns good. Use a sequence rule
    # by predicate.
    seq = iter([bad, good])

    def _next_pred(messages, ctx):
        return True

    # Configure two rules: each pops the next response.
    provider.add_response_for_stage("persona_trait_extraction", bad)
    provider.add_default(good)
    from uuid import uuid4
    ex = LLMTraitExtractor(
        sessionmaker=None,
        simulation_id=uuid4(),
        provider=provider,
        max_repair_attempts=1,
    )
    out = await ex.extract(shell)
    assert out.repair_attempts == 1
    assert len(out.candidates) == 1
    assert out.candidates[0].support_level == "direct"
