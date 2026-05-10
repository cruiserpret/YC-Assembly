"""Phase 8.2F — `PersonaConstructionRunSummary`.

Structured run-result the worker returns to its caller. The summary is
the single source of truth for "what just happened"; every dry-run and
write-mode run produces one. Audit panels in Phase 8.2H+ will surface
these counts.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field


class SkippedShellReason(BaseModel):
    """One structured reason why a candidate shell did NOT become a persona."""

    model_config = ConfigDict(extra="forbid")

    shell_id: str
    reason_code: str
    message: str


class PersonaConstructionRunSummary(BaseModel):
    """Dry-run + write-mode result. Counts always reflect what was
    actually done (writes happened only when `wrote_personas=True`).
    """

    model_config = ConfigDict(extra="forbid")

    # Run mode
    dry_run: bool = True
    wrote_personas: bool = False

    # Source classification breakdown
    source_records_seen: int = 0
    strong_persona_signal_records: int = 0
    weak_persona_signal_records: int = 0
    context_only_records: int = 0
    rejected_records: int = 0

    # Grouping
    candidate_shells: int = 0

    # Per-shell extraction outcomes
    shells_with_extraction_attempted: int = 0
    shells_with_three_or_more_valid_traits: int = 0

    # Persona outcomes
    personas_created: int = 0
    personas_skipped: int = 0
    traits_created: int = 0
    traits_rejected: int = 0
    evidence_links_created: int = 0

    skipped_reasons: list[SkippedShellReason] = Field(default_factory=list)

    # Cost accounting (only meaningful when LLM extractor is used)
    llm_calls: int = 0
    cost_estimate_usd: float | None = None
    cost_actual_usd: float | None = None

    def reason_breakdown(self) -> Mapping[str, int]:
        """Return a {reason_code: count} map for quick reporting."""
        c: Counter[str] = Counter()
        for s in self.skipped_reasons:
            c[s.reason_code] += 1
        return dict(c)
