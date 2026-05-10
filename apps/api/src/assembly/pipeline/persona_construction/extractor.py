"""Phase 8.2F — trait extractors.

Two implementations:

  * `MockTraitExtractor` — deterministic test extractor. No LLM, no
    network, no DB. Tests use it to drive the worker's branches.

  * `LLMTraitExtractor` — live extractor. Routes through
    `cost_guarded_chat` (Phase 6.6 universal entry point). NEVER calls
    `provider.chat` directly — the drift test asserts.

Both extractors return a `TraitExtractionResult` with a list of
candidate traits + a `source_excerpt` string per direct/inferred trait.
The persistence layer maps the excerpt back to the underlying
source_record(s) via verbatim substring match — that's how candidate
traits are bound to `source_ids`.

The Phase 8.2F dry-run never calls `LLMTraitExtractor`. The class is
shipped so the live mode is wired but gated by the worker's explicit
`extractor` parameter.

Phase 8.2F.6 hardening: `_strip_json_fences` extracts the first JSON
object from a model response that may have wrapped the JSON in
markdown fences (```json ... ```) or prefaced it with prose. The pilot
showed every shell hit one repair attempt because the model's first
emission included a markdown fence; the prompt now forbids fences AND
the parser tolerates them so a fenced-but-otherwise-valid response no
longer needs a repair round-trip.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.pipeline.persona.constants import (
    INFERRED_MIN_CONFIDENCE,
    PERSONA_FIELD_NAMES,
    SUPPORT_LEVELS,
)
from assembly.pipeline.persona_construction.grouping import (
    CandidatePersonaShell,
)
from assembly.pipeline.persona_construction.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraitCandidate:
    """One extractor-emitted trait. The persistence layer maps
    `source_excerpt` back to the source_record(s) via verbatim
    substring match."""
    field_name: str
    support_level: str
    value: str | None
    source_excerpt: str | None
    confidence: float
    rationale: str | None


@dataclass(frozen=True)
class TraitExtractionResult:
    shell_id: str
    candidates: tuple[TraitCandidate, ...]
    raw_response_text: str | None = None
    repair_attempts: int = 0


class TraitExtractor(Protocol):
    """Extractors implement this single coroutine."""

    async def extract(
        self, shell: CandidatePersonaShell,
    ) -> TraitExtractionResult: ...  # pragma: no cover  protocol


# ---------------------------------------------------------------------------
# Pydantic schema for LLM output (extra='forbid' enforces the closed set)
# ---------------------------------------------------------------------------


class _LLMTraitOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    support_level: str
    value: str | None = None
    source_excerpt: str | None = None
    confidence: float = 0.0
    rationale: str | None = None


class _LLMExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traits: list[_LLMTraitOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Mock extractor
# ---------------------------------------------------------------------------


@dataclass
class MockTraitExtractor:
    """Deterministic extractor for tests. Returns the configured
    candidate list for any input shell. Optionally keys responses by
    `shell.shell_id`.

    The extractor performs NO content-quoting validation — the
    persistence layer is the single source of truth for excerpt-to-
    source-id binding. Tests that want to exercise the binding path
    must set `source_excerpt` to a substring that actually appears in
    the shell's `aggregated_content`.
    """

    candidates: tuple[TraitCandidate, ...] = field(default_factory=tuple)
    candidates_by_shell: dict[str, tuple[TraitCandidate, ...]] = field(
        default_factory=dict,
    )

    async def extract(
        self, shell: CandidatePersonaShell,
    ) -> TraitExtractionResult:
        if shell.shell_id in self.candidates_by_shell:
            cands = self.candidates_by_shell[shell.shell_id]
        else:
            cands = self.candidates
        return TraitExtractionResult(
            shell_id=shell.shell_id,
            candidates=tuple(cands),
            raw_response_text=None,
            repair_attempts=0,
        )


# ---------------------------------------------------------------------------
# LLM extractor — routes through cost_guarded_chat
# ---------------------------------------------------------------------------


@dataclass
class LLMTraitExtractor:
    """Live trait extractor. NEVER invoked during Phase 8.2F's dry-run.

    Required:
      sessionmaker:   async session factory
      simulation_id:  caller-bound simulation context (cost_guarded_chat
                      requires a simulations row to lock against)
      provider:       LLMProvider (real or MockProvider in tests)
      model:          override stage default; if None, picked by stage
    """
    sessionmaker: async_sessionmaker
    simulation_id: UUID
    provider: LLMProvider
    model: str | None = None
    max_repair_attempts: int = 1

    STAGE: str = "persona_trait_extraction"

    async def extract(
        self, shell: CandidatePersonaShell,
    ) -> TraitExtractionResult:
        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=build_user_prompt(
                    aggregated_content=shell.aggregated_content,
                ),
            ),
        ]
        last_text: str | None = None
        repair_attempts = 0
        for attempt in range(self.max_repair_attempts + 1):
            response = await cost_guarded_chat(
                sessionmaker=self.sessionmaker,
                simulation_id=self.simulation_id,
                stage=self.STAGE,
                messages=messages,
                provider=self.provider,
                model=self.model,
                max_tokens=1500,
                temperature=0.2,
                capture_prompt_snapshot=True,
                estimated_prompt_tokens=2500,
                estimated_completion_tokens=900,
            )
            last_text = response.text
            try:
                payload = _LLMExtractionPayload.model_validate_json(
                    _strip_json_fences(response.text)
                )
            except Exception as e:
                if attempt >= self.max_repair_attempts:
                    return TraitExtractionResult(
                        shell_id=shell.shell_id,
                        candidates=(),
                        raw_response_text=last_text,
                        repair_attempts=attempt,
                    )
                # Repair: send the parser error back as an assistant turn.
                repair_attempts += 1
                messages = list(messages) + [
                    LLMMessage(role="assistant", content=response.text),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Your previous response did not parse as the "
                            f"required JSON schema. Error: {type(e).__name__}: "
                            f"{e}. Re-emit the JSON object exactly per the "
                            "schema with no commentary."
                        ),
                    ),
                ]
                continue

            candidates = _payload_to_candidates(
                payload, content=shell.aggregated_content,
            )
            return TraitExtractionResult(
                shell_id=shell.shell_id,
                candidates=candidates,
                raw_response_text=last_text,
                repair_attempts=repair_attempts,
            )
        return TraitExtractionResult(
            shell_id=shell.shell_id,
            candidates=(),
            raw_response_text=last_text,
            repair_attempts=repair_attempts,
        )


def _payload_to_candidates(
    payload: _LLMExtractionPayload, *, content: str,
) -> tuple[TraitCandidate, ...]:
    """Convert the LLM payload into TraitCandidate tuples. We do
    NOT enforce field_name / support_level membership here — that
    happens in the persistence layer (which calls
    `validate_persona_trait_payload` per Phase 8.2A). The job here is
    only the wire-shape conversion.

    We DO drop entries whose source_excerpt does not appear verbatim
    in the aggregated content, because that excerpt is what binds the
    trait to a source_record at persistence time. A non-quotable
    excerpt is a hallucination by the model and we refuse to carry it
    forward.
    """
    out: list[TraitCandidate] = []
    for t in payload.traits:
        if t.source_excerpt and t.source_excerpt not in content:
            # Hallucinated quote → refuse to carry forward as direct/inferred.
            # Downgrade to 'unknown'.
            out.append(TraitCandidate(
                field_name=t.field_name,
                support_level="unknown",
                value=None,
                source_excerpt=None,
                confidence=0.0,
                rationale=(
                    "downgraded: source_excerpt not found verbatim in evidence."
                ),
            ))
            continue
        out.append(TraitCandidate(
            field_name=t.field_name,
            support_level=t.support_level,
            value=t.value,
            source_excerpt=t.source_excerpt,
            confidence=float(t.confidence),
            rationale=t.rationale,
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
# JSON fence / prose stripper (Phase 8.2F.6)
# ---------------------------------------------------------------------------


_JSON_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(?P<body>\{.*?\})\s*```",
    re.DOTALL,
)


def _strip_json_fences(text: str) -> str:
    """Return the first valid-looking JSON object substring of `text`.

    Tolerates the four most common formatting mistakes a model can
    make even when explicitly told not to:
      1. wrap in markdown fences: ```json {...} ```
      2. prefix with prose:       "Here is the result: {...}"
      3. trailing prose:          "{...} Let me know if you need …"
      4. all of the above

    On a clean response (raw `{...}`) the function is a no-op modulo
    whitespace stripping.

    NEVER raises; always returns a string. If no `{...}` substring
    is found, returns the input unchanged so downstream JSON parsing
    can fail with a useful error.
    """
    if not isinstance(text, str):
        return text  # type: ignore[unreachable]
    s = text.strip()
    # 1) markdown fence
    m = _JSON_FENCE_RE.search(s)
    if m:
        return m.group("body").strip()
    # 2 / 3) prose prefix or suffix — find first `{` and matching `}`
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1].strip()
    return s


__all__ = [
    "LLMTraitExtractor",
    "MockTraitExtractor",
    "TraitCandidate",
    "TraitExtractionResult",
    "TraitExtractor",
]
