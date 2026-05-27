"""Blind-case schema for market calibration.

Each validation case has three sections, separated for SAFETY:

  A. PreLaunchInput        — the ONLY data Assembly is allowed to see
  B. HiddenRealWorldOutcome — the post-launch truth, withheld from
                              Assembly entirely until scoring time
  C. ScoringMetadata       — bookkeeping that the scorer needs but
                              that did not exist at prediction time

This separation is enforced at three layers:

  1. Type: separate Pydantic models that don't share fields.
  2. Method: ``BlindCase.to_assembly_brief()`` returns only the
     ``PreLaunchInput`` section. ``BlindCase.read_outcome_for_scoring()``
     requires a non-None prediction artifact path before returning the
     outcome — refuses otherwise.
  3. Hash: ``compute_pre_launch_hash()`` computes a sha256 over ONLY
     the pre-launch section. The hash is what gets stored in audit
     trails so a later reviewer can prove the brief Assembly saw was
     the one declared at case definition time.

This file is data-only. No retrieval, no HTTP, no LLM calls.

Phase 12A.1 deliberately ships the schema empty — no real outcome
data lives in this repo. That stays the case until a separate phase
explicitly approves a calibration corpus contribution.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# A. PreLaunchInput — what Assembly is allowed to see
# ---------------------------------------------------------------------------


class PreLaunchInput(BaseModel):
    """Everything Assembly is allowed to read when generating its
    prediction. Field set is deliberately narrow so accidental
    contamination is type-checkable."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(
        min_length=1,
        description=(
            "Stable identifier. Used to link a prediction artifact "
            "back to the case definition."
        ),
    )
    product_name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    pre_launch_brief: dict[str, Any] = Field(
        description=(
            "The exact `product_brief` dict that the orchestrator "
            "consumes (same shape AssemblyRun.product_brief takes). "
            "Must not contain any field whose name suggests an "
            "observed outcome; validators below enforce that."
        ),
    )
    cutoff_date: date = Field(
        description=(
            "Latest date Assembly is allowed to consider in evidence. "
            "Any retrieved evidence dated after this is a forbidden "
            "leak."
        ),
    )
    forbidden_post_cutoff_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Optional explicit denylist (urls, domain prefixes, or "
            "source-name keywords). Sources matching these MUST NOT "
            "appear in Assembly's evidence pool, even if their date "
            "is unknown. Strings only — no patterns."
        ),
    )

    @field_validator("pre_launch_brief")
    @classmethod
    def _no_outcome_keys_in_brief(cls, v: dict[str, Any]) -> dict[str, Any]:
        """The brief Assembly consumes must not contain keys that
        smell like real outcome data. This is a guardrail against
        accidental contamination during corpus authoring."""
        forbidden_substrings = (
            "observed_", "post_launch", "real_world_", "outcome_",
            "ground_truth", "actual_buyers", "actual_signups",
            "actual_revenue", "actual_conversion",
        )
        bad = [
            k for k in v
            if any(s in k.lower() for s in forbidden_substrings)
        ]
        if bad:
            raise ValueError(
                "pre_launch_brief contains outcome-shaped keys "
                f"{bad!r}; these MUST live in HiddenRealWorldOutcome"
            )
        return v


# ---------------------------------------------------------------------------
# B. HiddenRealWorldOutcome — withheld from Assembly entirely
# ---------------------------------------------------------------------------


ObservedSourceType = Literal[
    "purchase_data",
    "post_launch_survey",
    "interview_panel",
    "waitlist_conversion_audit",
    "synthetic_test_fixture",  # explicitly marked when fabricated for tests
]


class HiddenRealWorldOutcome(BaseModel):
    """The post-launch truth. Lives separately so it CANNOT
    accidentally be passed to Assembly. The scorer reads it only
    after a prediction artifact already exists."""

    model_config = ConfigDict(extra="forbid")

    observed_distribution: dict[str, float] = Field(
        description=(
            "Real-world counts or percents keyed by market bucket "
            "(`buyer`, `receptive`, `uncertain`, `skeptical`). "
            "Will be normalized by the scorer."
        ),
    )
    observed_sample_size: int = Field(
        ge=1,
        description="Total real participants observed.",
    )
    observed_source_type: ObservedSourceType
    # Phase 12E.fix2 — optional. When missing, downstream code treats
    # it as "unknown" and skips the cutoff-date strictness check.
    # Pre-12E.fix2 the parser raised on missing dates which caused
    # variance harness scoring to abort after a successful pipeline.
    observed_collection_date: date | None = Field(
        default=None,
        description=(
            "When the observed data was gathered. When present, must "
            "be AFTER PreLaunchInput.cutoff_date; the scorer enforces "
            "that. When missing, rendered as 'unknown' and the "
            "strictness check is skipped."
        ),
    )
    observed_objections: list[str] = Field(
        default_factory=list,
        description=(
            "Optional list of real-world objection strings. Used "
            "for objection recall metric if present."
        ),
    )


# ---------------------------------------------------------------------------
# C. ScoringMetadata — allowed only after Assembly output exists
# ---------------------------------------------------------------------------


class ScoringMetadata(BaseModel):
    """Bookkeeping fields the scorer needs but that were not part of
    the pre-launch brief. None of these are passed to Assembly."""

    model_config = ConfigDict(extra="forbid")

    assembly_run_id: str | None = Field(
        default=None,
        description=(
            "Optional pointer to the `AssemblyRun` row whose "
            "founder_report.json the scorer should read."
        ),
    )
    prediction_artifact_path: str | None = Field(
        default=None,
        description=(
            "Optional explicit path to the founder_report.json or "
            "simulated_intent.json the scorer should read instead "
            "of fetching by run_id."
        ),
    )
    notes: str = ""


# ---------------------------------------------------------------------------
# BlindCase — wrapper
# ---------------------------------------------------------------------------


class _OutcomeNotYetReadableError(RuntimeError):
    """Raised when ``read_outcome_for_scoring()`` is called before a
    prediction artifact exists. Surfaced so test code can match it
    by type."""


class BlindCase(BaseModel):
    """The three-section wrapper. Construction does NOT make the
    outcome readable — that requires an explicit
    ``read_outcome_for_scoring()`` call with a prediction-artifact
    path that already exists on disk."""

    model_config = ConfigDict(extra="forbid")

    pre_launch_input: PreLaunchInput
    hidden_real_world_outcome: HiddenRealWorldOutcome
    scoring_metadata: ScoringMetadata = Field(
        default_factory=ScoringMetadata,
    )

    def to_assembly_brief(self) -> dict[str, Any]:
        """Return the EXACT product_brief dict to feed to Assembly.

        This is the only sanctioned way to extract Assembly input
        from a BlindCase. The dict carries no observed-outcome
        fields; the validator on ``PreLaunchInput.pre_launch_brief``
        is the type-level guarantee.
        """
        return dict(self.pre_launch_input.pre_launch_brief)

    def compute_pre_launch_hash(self) -> str:
        """sha256 of the JSON-canonicalized fields Assembly actually
        SEES at prediction time. Specifically: ``pre_launch_brief``,
        ``cutoff_date``, and ``forbidden_post_cutoff_sources`` — but
        NOT ``case_id`` (bookkeeping) or ``product_name`` / ``category``
        (already inside the brief).

        Excluding ``case_id`` lets the pack-level audit detect a
        copy-paste case: two cases with different ``case_id``s but
        the same brief content collide on this hash. That collision
        is what ``validate_case_pack_blindness`` checks for.

        The hash is recorded in audit trails so a later reviewer can
        verify that the brief Assembly saw matches the brief
        declared in this case definition — without ever reading the
        outcome.
        """
        payload = {
            "pre_launch_brief": self.pre_launch_input.pre_launch_brief,
            "cutoff_date": self.pre_launch_input.cutoff_date.isoformat(),
            "forbidden_post_cutoff_sources": sorted(
                self.pre_launch_input.forbidden_post_cutoff_sources or []
            ),
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def read_outcome_for_scoring(
        self,
        *,
        prediction_artifact_path: str | Path,
    ) -> HiddenRealWorldOutcome:
        """Return the outcome ONLY if the prediction artifact already
        exists on disk. This forces the scorer to wait until Assembly
        has actually produced output before the outcome is readable.

        Additionally checks that
        ``hidden_real_world_outcome.observed_collection_date`` is
        strictly AFTER ``pre_launch_input.cutoff_date`` — if it
        isn't, the outcome cannot be trusted as post-launch truth.
        """
        p = Path(prediction_artifact_path)
        if not p.exists():
            raise _OutcomeNotYetReadableError(
                f"prediction artifact not found at {p!s}; refusing "
                "to disclose hidden outcome before prediction exists"
            )
        if not p.is_file():
            raise _OutcomeNotYetReadableError(
                f"prediction artifact path {p!s} exists but is not "
                "a regular file"
            )
        # Cutoff invariant.
        obs_date = self.hidden_real_world_outcome.observed_collection_date
        cutoff = self.pre_launch_input.cutoff_date
        if obs_date <= cutoff:
            raise ValueError(
                f"observed_collection_date {obs_date.isoformat()} is "
                f"NOT strictly after pre_launch_input.cutoff_date "
                f"{cutoff.isoformat()}; this would break the "
                "post-launch-truth invariant"
            )
        return self.hidden_real_world_outcome


# ---------------------------------------------------------------------------
# Blindness audit helpers
# ---------------------------------------------------------------------------


def assembly_brief_excludes_outcome_fields(
    brief: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Returns ``(ok, leaked_field_names)``. Used in tests/audits to
    confirm an arbitrary dict can safely be fed to Assembly."""
    forbidden_substrings = (
        "observed_", "post_launch", "real_world_", "outcome_",
        "ground_truth", "actual_buyers", "actual_signups",
        "actual_revenue", "actual_conversion",
    )
    leaked = [
        k for k in brief
        if any(s in k.lower() for s in forbidden_substrings)
    ]
    return (len(leaked) == 0, leaked)


def evidence_obeys_cutoff(
    evidence_dates: list[date | datetime | None],
    cutoff: date,
) -> tuple[bool, list[str]]:
    """Returns ``(ok, violation_strings)`` after scanning a list of
    evidence-dating values. None values are treated as POSSIBLE
    cutoff-violators — surfaced as a warning so the caller decides.
    """
    violations: list[str] = []
    for i, d in enumerate(evidence_dates):
        if d is None:
            violations.append(
                f"evidence[{i}].date is None — unable to verify cutoff"
            )
            continue
        as_date = d.date() if isinstance(d, datetime) else d
        if as_date > cutoff:
            violations.append(
                f"evidence[{i}].date={as_date.isoformat()} > "
                f"cutoff={cutoff.isoformat()}"
            )
    return (len(violations) == 0, violations)
