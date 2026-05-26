"""Phase 10A — founder-input request schema for POST /assembly/runs.

Frontend-facing contract. `extra="forbid"` discipline blocks rogue
fields from sneaking in (e.g. attempts to hardcode personas / cohort
roles).

Phase 12F.1 adds a set of OPTIONAL context fields that founders can
supply to enrich downstream explainability / confidence / niche-signal
artifacts. None of these fields affect persona generation directly in
12F.1 — they are surfaced into the report and feed the confidence
score. CompanyContext persistence (Task 2 of 12F design) is deferred
to 12F.2. Uploaded artifacts are stored as metadata-only in 12F.1
(no blob storage / OCR yet).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LaunchState = Literal["launched", "unlaunched"]
ReportDepth = Literal["fast_demo", "standard", "deep"]
RunMode = Literal["fixture_demo", "live_founder_brief"]
# Phase 12E — source-audience profile selector. Optional brief
# field; when missing, the system uses the `default` profile, which
# preserves pre-Phase-12E target-customer-heavy behavior. Other
# source profiles (app_store_reviews, reddit_launch, g2_capterra)
# are deferred to a future phase.
#
# Phase 12E.5C — `hn_show_hn_v2` added as an OPT-IN profile. Legacy
# briefs continue to use `hn_show_hn` unchanged; v2 must be requested
# explicitly. Will not be promoted to default without paid confirmation
# validation support across ≥2 products.
#
# Phase 12E.5O — `product_hunt_v1` added as an OPT-IN profile, plus
# `product_hunt` as the friendly alias that resolves to v1 in the
# augmenter. Promotion to PH-default requires paid-confirmation
# support on ≥2 PH products.
LaunchSource = Literal[
    "default",
    "hn_show_hn",
    "hn_show_hn_v2",
    "product_hunt_v1",
    "product_hunt",
]

# Phase 12F.1 — advanced context vocabularies. All closed-set enums so
# the founder UI can render proper selectors and the confidence-score
# logic can interpret values without free-text parsing.
CompanyStage = Literal[
    "idea", "prototype", "pre_pmf", "post_pmf", "growth",
]
# Mirrors Phase 12E `launch_source` but founder-facing label. The
# orchestrator maps `gtm_channel` → `launch_source` when both are
# absent. Unknown channels stub to the `default` profile (legacy-
# compat target-customer-heavy mix) without raising.
GTMChannel = Literal[
    "hn_show_hn",
    "product_hunt",
    "reddit_launch",
    "cold_outbound",
    "paid_ads",
    "content_seo",
    "referral",
    "community",
    "other",
]
PricingModel = Literal[
    "free", "freemium", "one_time", "subscription",
    "usage_based", "tiered", "enterprise_contract", "other",
]
ArtifactKind = Literal[
    "screenshot", "landing_page_snapshot", "deck",
    "customer_survey_csv", "interview_notes", "other",
]


# --- Phase 12F.1 sub-models -------------------------------------------


class TractionInfo(BaseModel):
    """Optional sub-shape describing current product traction. Empty
    object is allowed (means "founder didn't provide traction info").
    """

    model_config = ConfigDict(extra="forbid")

    users: int | None = Field(default=None, ge=0)
    revenue_usd_mrr: float | None = Field(default=None, ge=0.0)
    time_in_market_months: int | None = Field(default=None, ge=0)


class CustomerInterview(BaseModel):
    """A real founder-supplied customer quote / interview note. Phase
    12F.1 surfaces these in the report and confidence-score; persona
    generation does NOT consume them yet (deferred to 12F.2)."""

    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1, max_length=2000)
    segment: str | None = Field(default=None, max_length=128)
    source: str | None = Field(default=None, max_length=256)


class ICPSegment(BaseModel):
    """Structured ICP segment. Augments — does NOT replace — the
    legacy flat `target_customers: list[str]` field."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    est_size_pct: float | None = Field(default=None, ge=0.0, le=100.0)


class PricingTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    price_usd: float | None = Field(default=None, ge=0.0)
    includes: str | None = Field(default=None, max_length=500)


class PricingInfo(BaseModel):
    """Structured pricing. When present, confidence-score awards full
    pricing_specificity. When absent or only `price_or_price_structure`
    free-text is set, specificity is partial."""

    model_config = ConfigDict(extra="forbid")

    model: PricingModel
    tiers: list[PricingTier] = Field(default_factory=list, max_length=10)


class CompetitorContext(BaseModel):
    """Strict superset of the legacy flat competitor string. Allows
    founders to declare WHY they think a competitor wins or loses."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    why_they_win: str | None = Field(default=None, max_length=500)
    why_they_lose: str | None = Field(default=None, max_length=500)


class ArtifactRef(BaseModel):
    """Metadata-only artifact reference. Phase 12F.1 stores URI + hash
    only — blob storage, OCR, vision processing are deferred to V0.1.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    uri: str = Field(min_length=1, max_length=2000)
    sha256: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=128)


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
    # Phase 12E — select the source-audience profile used to inject
    # non-customer voices proportionally. Missing → `default`
    # (legacy-compatible target-customer-heavy behavior).
    launch_source: LaunchSource | None = None

    # --- Phase 12F.1 advanced context fields (all optional) ---------
    # None of these change persona generation in 12F.1; they feed the
    # report + confidence score. Schema-only; backwards-compatible.
    company_stage: CompanyStage | None = None
    current_traction: TractionInfo | None = None
    retention_or_churn_signal: str | None = Field(
        default=None, max_length=500,
    )
    founder_hypothesis: str | None = Field(default=None, max_length=2000)
    customer_interviews: list[CustomerInterview] = Field(
        default_factory=list, max_length=30,
    )
    known_objections: list[str] = Field(
        default_factory=list, max_length=20,
    )
    icp_segments: list[ICPSegment] = Field(
        default_factory=list, max_length=10,
    )
    pricing_assumptions: PricingInfo | None = None
    gtm_channel: GTMChannel | None = None
    competitors_with_context: list[CompetitorContext] = Field(
        default_factory=list, max_length=15,
    )
    current_messaging: str | None = Field(default=None, max_length=2000)
    decision_being_tested: str | None = Field(
        default=None, max_length=500,
    )
    what_would_change_my_mind: str | None = Field(
        default=None, max_length=500,
    )
    # Metadata-only in 12F.1; blob storage deferred.
    uploaded_artifacts: list[ArtifactRef] = Field(
        default_factory=list, max_length=10,
    )

    @model_validator(mode="after")
    def _no_hardcoded_personas(self) -> FounderBriefIn:
        """Reject any attempt to smuggle in persona / cohort
        hardcoding via free-text fields. Universal — never product-
        specific. Phase 12F.1 extends coverage to the new context
        free-text fields."""
        forbidden_phrases = (
            "force persona", "hardcode persona", "manual cohort",
            "force cohort", "use these personas", "ignore safety",
        )
        text_fields: list[str | None] = [
            self.optional_context,
            self.category_hint,
            # Phase 12F.1 — extend coverage:
            self.founder_hypothesis,
            self.current_messaging,
            self.retention_or_churn_signal,
            self.decision_being_tested,
            self.what_would_change_my_mind,
        ]
        for f in text_fields:
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
        list_text_sources: list[str] = list(self.constraints) + list(
            self.known_objections
        )
        for seg in self.icp_segments:
            list_text_sources.append(seg.description)
        for ci in self.customer_interviews:
            list_text_sources.append(ci.quote)
        for cc in self.competitors_with_context:
            for v in (cc.why_they_win, cc.why_they_lose):
                if v:
                    list_text_sources.append(v)
        for c in list_text_sources:
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
