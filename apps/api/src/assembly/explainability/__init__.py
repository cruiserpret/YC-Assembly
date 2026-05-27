"""Phase 12F.1 — Founder Trust + Explainability layer.

Pure aggregation over artifacts the pipeline already produces. ZERO
new LLM calls, zero DB migration, zero `apps/web` changes in 12F.1.

Public surfaces:

  * `build_explainability_panel(...)`  — top-level "Why Assembly
    predicted this" block for founder_report.json.
  * `build_persona_reasoning_cards(...)` — N representative persona
    cards with sourced reasoning artifacts (no chain-of-thought).
  * `build_niche_signals(...)` — minority objections, unexpected
    micro-segments, edge-case use cases, one question to ask real
    customers.
  * `compute_confidence(...)` — rule-based confidence score with
    `limited_by` always populated, capped at 0.85 in 12F.1.

Anti-fake-certainty discipline lives in every builder:
  - Cards without an evidence_anchor / triggered_by are dropped.
  - The confidence score never reaches `high` in 12F.1 (cap 0.85).
  - `limited_by` is never empty.
  - `one_question_for_real_customers` is always phrased as a question.
"""
from __future__ import annotations

from assembly.explainability.confidence_score import compute_confidence
from assembly.explainability.markdown_render import (
    render_12f1_markdown_section,
)
from assembly.explainability.niche_signals import build_niche_signals
from assembly.explainability.panel_builder import build_explainability_panel
from assembly.explainability.persona_cards import (
    build_persona_reasoning_cards,
)

__all__ = [
    "build_explainability_panel",
    "build_niche_signals",
    "build_persona_reasoning_cards",
    "compute_confidence",
    "render_12f1_markdown_section",
]
