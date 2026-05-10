"""Schemas for the intake brief — what the user submits and how we store it."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

PriceModel = Literal[
    "one_time",
    "subscription_monthly",
    "subscription_annual",
    "usage_based",
    "transaction_fee",
    "revenue_share",
    "freemium",
    "bundle",
    "custom",
]


class PriceStructure(BaseModel):
    model: PriceModel = Field(..., description="Pricing model")
    amount: str | None = Field(
        default=None,
        description="Free-text amount (e.g. '$49/mo', '5% of GMV', 'tiered'). "
        "We keep this as a string in V0 — Assembly does not emit numeric forecasts so "
        "we do not need to do arithmetic on it.",
    )
    notes: str | None = None


class TargetSociety(BaseModel):
    """The market the user wants Assembly to simulate. Free-form on purpose;
    the intake parser (Phase 4) will normalize segment splits, geographies,
    and influence clusters from this."""

    description: str = Field(..., min_length=10)
    geography: str | None = None
    income_level: str | None = None
    known_segments: list[str] = Field(default_factory=list)


class CompetitorRef(BaseModel):
    name: str
    url: str | None = None
    notes: str | None = None


class SimulationBriefIn(BaseModel):
    """Request shape for `POST /simulations`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_type: Annotated[str, Field(min_length=2, max_length=128)]
    product_name: Annotated[str, Field(min_length=1, max_length=256)]
    description: Annotated[str, Field(min_length=20)]
    price_structure: PriceStructure
    target_society: TargetSociety
    competitors: list[CompetitorRef] = Field(default_factory=list)
    product_url: HttpUrl | None = None
    additional_context: str | None = None

    # Optional: lock evidence cutoff at submit time so the worker can enforce
    # zero-leakage during backtests.
    evidence_cutoff_date: date | None = None

    @field_validator("description")
    @classmethod
    def _description_is_substantive(cls, v: str) -> str:
        if len(v.split()) < 5:
            raise ValueError("description must be more than a one-line idea")
        return v


class SimulationBriefStored(BaseModel):
    """Server-side normalized brief, mirrors the `simulation_inputs` row."""

    model_config = ConfigDict(from_attributes=True)

    product_type: str
    product_name: str
    description: str
    price_structure: dict
    target_society: dict
    competitors: list[dict]
    product_url: str | None = None
    additional_context: str | None = None


class SimulationCreated(BaseModel):
    """Response for `POST /simulations` — the run is enqueued, not done."""

    id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    created_at: datetime
