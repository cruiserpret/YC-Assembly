"""Phase 17D — historical-case INPUT BUNDLE (pre-outcome evidence only).

The input bundle is the ONLY thing shown to Raw(model) and Assembly(model). It must
contain strictly pre-outcome evidence — no final outcome numbers, no postmortems, no
"raised $X / failed / succeeded / final backers". Enforcement of those rules lives in
``leakage_audit.py`` (which reuses the 17C ``retrieval_filter``); this module is the
schema + light per-item validation. Pure data; no model, no network.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PreOutcomeStatus = Literal["verified_pre_outcome", "uncertain", "rejected"]


class EvidenceItem(BaseModel):
    """One pre-outcome source in the bundle. ``content_hash`` commits the excerpt;
    timestamps drive the leakage audit (published > archived > accessed)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str = ""
    url: str | None = None
    archive_url: str | None = None
    publisher: str = ""
    published_at: str | None = None
    archived_at: str | None = None
    accessed_at: str | None = None
    source_type: str = "unknown"
    source_text_excerpt: str = ""
    content_hash: str | None = None
    pre_outcome_status: PreOutcomeStatus = "uncertain"
    leakage_flags: list[str] = Field(default_factory=list)

    def best_timestamp(self) -> str | None:
        return self.published_at or self.archived_at or self.accessed_at


class InputBundle(BaseModel):
    """The frozen pre-outcome evidence bundle for one historical case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    prediction_timestamp: str
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    evidence_summary: str = ""
    product_description: str = ""
    target_customer: str = ""
    ask_pre_outcome: str = ""  # price / pledge / subscription / ask known pre-outcome
    channel_context: str = ""  # launch / channel context known pre-outcome
    traction_signals_pre_outcome: str = ""  # ONLY traction known before the outcome
    uncertainty_notes: str = ""
    excluded_evidence_summary: str = ""

    def sources_for_filter(self) -> list[dict]:
        """Project evidence items into the source shape the 17C retrieval_filter reads."""
        return [
            {
                "id": e.source_id,
                "url": e.url or e.archive_url,
                "published_at": e.published_at,
                "archived_at": e.archived_at,
                "retrieved_at": e.accessed_at,
                "text": e.source_text_excerpt,
            }
            for e in self.evidence_items
        ]
