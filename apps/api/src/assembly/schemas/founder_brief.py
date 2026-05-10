"""Phase 10A — founder-input request schema for POST /assembly/runs.

Frontend-facing contract. `extra="forbid"` discipline blocks rogue
fields from sneaking in (e.g. attempts to hardcode personas / cohort
roles).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LaunchState = Literal["launched", "unlaunched"]
ReportDepth = Literal["fast_demo", "standard", "deep"]
RunMode = Literal["fixture_demo", "live_founder_brief"]


class FounderBriefIn(BaseModel):
    """Founder-input contract. Required vs optional fields per spec.

    Forbidden fields (validators reject):
      personas, persona_roles, cohorts, manual_segments — Assembly
      decides these dynamically. The brief should guide the run, not
      hardcode the society.
    """

    model_config = ConfigDict(extra="forbid")

    # Required
    product_name: str = Field(min_length=1, max_length=128)
    product_description: str = Field(min_length=10, max_length=4000)
    # Phase 10B.3+: bumped from 128 → 1000. Multi-tier briefs
    # (primary + bundle + subscription + accessory) routinely run
    # past the old limit. The 10B.2 price-hierarchy parser already
    # handles long multi-line price strings; the schema was the only
    # remaining choke point.
    price_or_price_structure: str = Field(min_length=1, max_length=1000)
    # Some launch geographies are described with multiple regions
    # ("Austin TX, Denver CO, and Portland OR metros"). 256 covers
    # that without giving up validation entirely.
    launch_geography: str = Field(min_length=1, max_length=256)
    target_customers: list[str] = Field(min_length=1, max_length=20)
    competitors_or_alternatives: list[str] = Field(
        default_factory=list, max_length=20,
    )
    launch_state: LaunchState
    optional_context: str | None = Field(default=None, max_length=4000)

    # Optional
    product_url: str | None = Field(default=None, max_length=400)
    category_hint: str | None = Field(default=None, max_length=128)
    constraints: list[str] = Field(
        default_factory=list, max_length=20,
    )
    report_depth: ReportDepth = "fast_demo"
    max_budget_usd: float | None = Field(default=None, ge=0.0, le=200.0)
    preferred_society_size: int | None = Field(
        default=None, ge=10, le=500,
    )

    @model_validator(mode="after")
    def _no_hardcoded_personas(self) -> FounderBriefIn:
        """Reject any attempt to smuggle in persona / cohort
        hardcoding via the constraints / category_hint / context
        fields. Universal — never product-specific."""
        forbidden_phrases = (
            "force persona", "hardcode persona", "manual cohort",
            "force cohort", "use these personas", "ignore safety",
        )
        for f in (
            self.optional_context, self.category_hint,
        ):
            if not f:
                continue
            lower = f.lower()
            for phrase in forbidden_phrases:
                if phrase in lower:
                    raise ValueError(
                        f"Founder input may not request hardcoded "
                        f"personas/cohorts ('{phrase}' rejected); "
                        "Assembly decides personas dynamically."
                    )
        for c in self.constraints:
            lower = (c or "").lower()
            for phrase in forbidden_phrases:
                if phrase in lower:
                    raise ValueError(
                        f"Founder constraint may not request hardcoded "
                        f"personas/cohorts ('{phrase}' rejected)."
                    )
        return self


class CreateAssemblyRunRequest(BaseModel):
    """Outer wrapper for POST /assembly/runs."""

    model_config = ConfigDict(extra="forbid")

    mode: RunMode = "fixture_demo"
    brief: FounderBriefIn


class CreateAssemblyRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    mode: RunMode
    current_stage: str
    estimated_steps: int
    artifact_manifest: dict[str, str] = Field(default_factory=dict)
    caveat: str = (
        "Assembly produces synthetic-society simulations and simulated "
        "intent — never real-world purchase forecasts or launch verdicts."
    )
