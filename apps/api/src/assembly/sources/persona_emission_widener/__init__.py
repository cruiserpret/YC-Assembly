"""Phase 9A.1 — multi-signal persona-candidate emitter.

`widen_persona_candidates(...)` takes the existing
`PersonaCandidatePlanner` output PLUS the atomic-signal stream from
`evidence_signal_extractor` and emits ADDITIONAL universal candidates
that the per-source planner missed.

Universal: every supplemental candidate is anchored to a real
`EvidenceSignal` (signal_id + source_record_synthetic_id +
evidence_excerpt) and never invented. Preserves the planner's
quality discipline (≥1 strong signal, ≥1 evidence excerpt, ≥1
inferred role, ≥2 evidence-backed traits OR a justified fallback).

Sub-segment splitting + duplicate-rejection gates apply universally:

  * Same exact (role, source, evidence_excerpt) → reject (no
    cosmetic-duplicate emission).
  * Same exact (role, source, objection) → reject second variant.
  * Per-source cap: max 3 emitted candidates per source.

NO LLM. NO network. Pure-function.
"""

from assembly.sources.persona_emission_widener.widener import (
    EmissionPolicy, WidenedCandidate, widen_persona_candidates,
)

__all__ = [
    "EmissionPolicy",
    "WidenedCandidate",
    "widen_persona_candidates",
]
