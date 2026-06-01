"""Phase 15L-B — Observed Outcome Mapping Protocol.

Decides WHEN partial public outcome evidence may legitimately become an Assembly
*observed* four-bucket outcome (training-eligible), when it must stay
evidence-only, and when it must be rejected — WITHOUT inventing any proportion,
ingesting any case, approving any candidate, or applying any calibration.

It is a NEW, pure, isolated sibling of the Phase 15J factory. It COMPOSES the
existing public APIs (``MarketDistribution`` for the sum-to-100 coherence check,
``evidence_grading`` for tier strength, ``ingest.is_clean_holdout`` for the
clean-holdout discipline) and NEVER edits the official ledger schema/loader/
ingest/metrics, the manifest, the seed, or the split ledger files. A
``ProposedOutcomeMapping`` is a candidate-side PROPOSAL artifact (purpose marker
+ ``human_approved`` pinned False) that is structurally distinct from
``ObservedProportions`` and is never loaded as validation data.

The binding finding it enforces (Phase 15L-A): a buyer/action NUMERATOR over a
self-selected denominator can never, by itself, imply the receptive /
uncertain_proof_needed / skeptical_resistant proportions. Those three buckets are
mathematically UNIDENTIFIED from a buyer-only sample — they can only be MEASURED
(``direct_observed_distribution``) or IMPORTED as explicitly-labeled assumptions
(``assumption_labeled_distribution``); filling them from the numerator is
fabrication. Pure/deterministic: no LLM, no network, no DB, no forecast change.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from assembly.validation_factory.candidate_schema import CandidateCase
from assembly.validation_factory.evidence_grading import strongest_supported_tier
from assembly.validation_ledger.ingest import is_clean_holdout
from assembly.validation_ledger.schema import (
    DenominatorType,
    MarketDistribution,
    ValidationCase,
)

PROTOCOL_VERSION = "outcome_mapping_protocol.v1"

# Isolation marker — a proposal is NEVER validation data (cf. CANDIDATE_PURPOSE).
MAPPING_PROPOSAL_PURPOSE = "mapping_proposal_not_validation_data"

# The five mapping decisions the protocol classifies evidence into.
OutcomeMappingType = Literal[
    "direct_observed_distribution",
    "assumption_labeled_distribution",
    "action_anchor_only",
    "evidence_only",
    "reject",
]

MappingConfidence = Literal["high", "medium", "low"]

# Provenance stamped on any would-be observed outcome (anti-masquerade, Gate 8).
MappingProvenance = Literal[
    "measured_four_bucket",  # all four buckets independently observed
    "assumption_based_labeled",  # anchor observed, non-buyer buckets imported priors
    "buyer_anchor_only",  # only the numerator; no distribution
    "none",  # evidence-only / reject
]

# Quality of the denominator the buyer SHARE would be computed over. Only the
# first two support a measured four-bucket distribution.
DenominatorQuality = Literal[
    "fixed_external_census",
    "representative_random_sample",
    "self_selected_funnel_counted",
    "self_selected_funnel_estimated",
    "no_denominator_cumulative",
    "unknown",
]
_REPRESENTATIVE_DENOMINATORS = ("fixed_external_census", "representative_random_sample")

BucketBasis = Literal["observed", "assumption", "unmapped"]

_BUCKET_NAMES = (
    "buyer_action_positive",
    "receptive",
    "uncertain_proof_needed",
    "skeptical_resistant",
)
_NON_BUYER_BUCKETS = ("receptive", "uncertain_proof_needed", "skeptical_resistant")

# Action-signal taxonomy partitions (cf. market_calibration.action_signals).
# PAID/committed revealed actions may anchor a buyer share; FREE actions
# over-count curiosity, not commitment (Gate 4).
PAID_BUYER_SIGNALS = frozenset(
    {"purchase", "paid_signup", "kickstarter_pledge", "backer_pledge", "trial_conversion"}
)
FREE_ACTION_SIGNALS = frozenset(
    {
        "install",
        "download",
        "github_fork",
        "github_star",
        "follow",
        "waitlist_signup",
        "discord_join",
        "bookmark",
        "share",
        "traffic",
        "search_interest",
        "product_hunt_upvote",
    }
)
WITHIN_BUYER_NEGATIVE_SIGNALS = frozenset({"churn", "return", "refund"})

# Uncertainty-flag prefixes the protocol reads (the candidates use a
# "prefix: description" convention). Matched case-insensitively on the prefix.
_FLAG_SELF_SELECTED = "self_selected_denominator"
_FLAG_BUYER_ONLY = "buyer_numerator_only"
_FLAG_NO_DENOMINATOR = "cumulative_no_denominator"
_FLAG_FREE_INSTALL = "free_install_not_purchase"
_FLAG_FULFILLMENT = ("downstream_fulfillment_failure", "realized_buyers_below_pledged")
_FLAG_RETURNS = ("net_negative_buyer_signal", "returns", "refunds")
_FLAG_ESTIMATE = ("estimated_counts", "press_estimates_not_audited", "estimate")
_FLAG_ASSUMPTION = "assumption_based_mapping"

# Default anti-overfit caps (advisory; surfaced in readiness, never auto-applied).
_DEFAULT_TARGET_CASE_COUNT = 20
_DEFAULT_ASSUMPTION_CAP_FRACTION = 1.0 / 3.0
_DEFAULT_SOURCE_CONCENTRATION_CAP = 3  # e.g. <=3 Kickstarter cases


# --------------------------------------------------------------------------
# Proposal artifact (candidate-side; never loaded as a validation case)
# --------------------------------------------------------------------------


class BucketMappingRationale(BaseModel):
    """Per-bucket provenance: was this bucket OBSERVED, imported as an ASSUMPTION,
    or left UNMAPPED — and the justification + citation."""

    model_config = ConfigDict(extra="forbid")

    bucket: Literal[
        "buyer_action_positive",
        "receptive",
        "uncertain_proof_needed",
        "skeptical_resistant",
    ]
    basis: BucketBasis
    rationale: str = ""
    source_reference: str = ""


class ProposedOutcomeMapping(BaseModel):
    """A human reviewer's PROPOSED mapping of a candidate's evidence into the four
    buckets — a proposal, NOT an approval and NOT validation data.

    ``extra="forbid"`` + the ``purpose`` marker + ``human_approved`` pinned False
    guarantee it can never masquerade as a validated ``ObservedProportions`` nor
    be loaded by the ledger. ``proposed_proportions`` reuses ``MarketDistribution``
    so, IF populated, it inherits the sum-to-100 (±1.5pp) coherence check for free
    — but that check is necessary, not sufficient (a fabrication can sum to 100).
    """

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["mapping_proposal_not_validation_data"] = MAPPING_PROPOSAL_PURPOSE
    candidate_id: str
    mapping_type: OutcomeMappingType
    # Nullable: null for action_anchor_only / evidence_only / reject; required for
    # direct_observed_distribution / assumption_labeled_distribution.
    proposed_proportions: MarketDistribution | None = None
    bucket_rationales: list[BucketMappingRationale] = Field(default_factory=list)
    # The single defensible quantity each candidate actually provides.
    buyer_anchor_signal_type: str | None = None
    buyer_anchor_count: float | None = None
    buyer_anchor_direction: Literal["positive", "negative", "neutral"] | None = None
    buyer_anchor_source: str = ""
    # Denominator the buyer SHARE would be computed over.
    denominator_type: DenominatorType = "unknown"
    denominator_count: int | None = None
    denominator_quality: DenominatorQuality = "unknown"
    denominator_explanation: str = ""
    # For assumption_labeled: the explicit priors used to split the non-buyer mass.
    assumptions: list[str] = Field(default_factory=list)
    source_bias_explanation: str = ""
    # For fulfillment-failure / returns candidates: where that cohort is recorded
    # (it is a WITHIN-BUYER split, never a non-buyer bucket). The note explains it;
    # the count records the cohort OUTSIDE the four-bucket distribution. Both are
    # required (a note alone cannot prove the mass was moved) — see G5/G6.
    within_buyer_split_note: str = ""
    within_buyer_negative_count: int | None = None
    # Gate-4 attestation: a free action deliberately used as a weak buyer proxy.
    free_action_weak_proxy: bool = False
    estimate_quality: Literal[
        "audited_official", "primary_verbatim", "third_party_estimate"
    ] = "third_party_estimate"
    confidence: MappingConfidence = "low"
    uncertainty_flags: list[str] = Field(default_factory=list)
    reviewer_notes: str = ""
    training_eligibility_recommendation: Literal[
        "training", "pending", "evidence_only", "reject", "undecided"
    ] = "undecided"
    # A proposal can NEVER carry approval — approval is the factory's
    # ReviewerChecklist + an explicit human transition, not a field here.
    human_approved: Literal[False] = False
    not_approved_note: str = (
        "PROPOSAL ONLY — not human-approved until the candidate's reviewer_checklist "
        "is completed and the factory promotion gates pass."
    )
    reviewer: str = ""
    reviewed_at: str | None = None

    @model_validator(mode="after")
    def _reject_requires_reason(self) -> ProposedOutcomeMapping:
        if self.mapping_type == "reject" and not (self.reviewer_notes or "").strip():
            raise ValueError("a 'reject' mapping must record a reason in reviewer_notes")
        return self


class MappingValidationResult(BaseModel):
    """The verdict of running the protocol gates on a ProposedOutcomeMapping."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    mapping_type: OutcomeMappingType
    ok: bool
    issues: list[str] = Field(default_factory=list)  # BLOCKING
    warnings: list[str] = Field(default_factory=list)  # advisory flags
    provenance: MappingProvenance = "none"
    forced_confidence: MappingConfidence | None = None
    training_eligible: bool = False
    counts_toward_direct_observed_bar: bool = False
    counts_toward_tier1_2_evidence: bool = False
    # A mapping proposal can never establish clean holdout (G7): that requires a
    # prediction locked BEFORE the outcome (the prospective 14C+15I path).
    clean_holdout_eligible: bool = False
    gate_codes: list[str] = Field(default_factory=list)  # which gate(s) fired


# --------------------------------------------------------------------------
# Flag / anchor helpers
# --------------------------------------------------------------------------


def _flags(*sources: object) -> list[str]:
    out: list[str] = []
    for s in sources:
        flags = getattr(s, "uncertainty_flags", None)
        if flags:
            out.extend(str(f) for f in flags)
    return out


def _has_flag(flags: Sequence[str], *prefixes: str) -> bool:
    low = [f.strip().lower() for f in flags]
    return any(f.startswith(p.lower()) for f in low for p in prefixes)


def _anchor_signal_types(candidate: CandidateCase | None) -> list[str]:
    if candidate is None:
        return []
    return [s.signal_type for s in candidate.action_signal_candidates]


def _is_free_anchor(signal_type: str | None) -> bool:
    return signal_type is not None and signal_type in FREE_ACTION_SIGNALS


def _is_paid_anchor(signal_type: str | None) -> bool:
    return signal_type is not None and signal_type in PAID_BUYER_SIGNALS


# --------------------------------------------------------------------------
# Classification — the maximal-honest mapping type for a candidate WITHOUT a
# human-supplied four-bucket mapping (claimed_outcome_proportions null).
# --------------------------------------------------------------------------


def classify_candidate(candidate: CandidateCase) -> tuple[OutcomeMappingType, list[str]]:
    """The maximal honest mapping type for a candidate as it stands. A candidate
    with null proportions can be at most ``action_anchor_only`` (solid anchor) or
    ``evidence_only`` (soft/cumulative/estimate anchor); it is NEVER auto-promoted
    to a distribution — that requires a human-supplied, gate-passing mapping.
    Returns (type, reasons)."""
    reasons: list[str] = []
    flags = _flags(candidate)
    sig_types = _anchor_signal_types(candidate)

    if candidate.claimed_outcome_proportions is not None:
        # A human already attached a four-bucket claim; this function does not
        # grade it — route through validate_mapping with an explicit proposal.
        reasons.append(
            "candidate carries claimed_outcome_proportions — grade it with "
            "validate_mapping(), not classify_candidate()"
        )
        return "assumption_labeled_distribution", reasons

    if not candidate.action_signal_candidates:
        reasons.append("no action signal — no defensible anchor; evidence-only at best")
        return "evidence_only", reasons

    # No denominator at all (cumulative free counts) -> even a buyer SHARE is
    # undefined -> evidence_only.
    if _has_flag(flags, _FLAG_NO_DENOMINATOR):
        reasons.append(
            "cumulative free action with no denominator (stars/forks): not even a "
            "buyer share is computable -> evidence_only"
        )
        return "evidence_only", reasons

    free_anchor = any(_is_free_anchor(t) for t in sig_types)
    paid_anchor = any(_is_paid_anchor(t) for t in sig_types)
    estimate = _has_flag(flags, *_FLAG_ESTIMATE)

    # Free + estimate-based anchor (e.g. free installs reported as third-party
    # estimates) -> too soft to anchor a share -> evidence_only.
    if free_anchor and not paid_anchor and (estimate or _has_flag(flags, _FLAG_FREE_INSTALL)):
        reasons.append(
            "free, estimate-based action (installs/stars) over a self-selected "
            "funnel: too soft to anchor a buyer share -> evidence_only"
        )
        return "evidence_only", reasons

    # A credible paid/counted action over a (self-selected) funnel: the anchor is
    # solid, but the non-buyer buckets are unobservable -> action_anchor_only.
    if paid_anchor or strongest_supported_tier(candidate) in (1, 2):
        reasons.append(
            "credible Tier-1/2 action anchor over a self-selected funnel: record "
            "the anchor; the non-buyer buckets are unobservable -> action_anchor_only"
        )
        return "action_anchor_only", reasons

    reasons.append("anchor too weak to classify as an action anchor -> evidence_only")
    return "evidence_only", reasons


# --------------------------------------------------------------------------
# Validation — the hard gates on a PROPOSED mapping.
#
# Enforced here in validate_mapping: G1 (denominator), G2 (self-selected /
# assumption-labeling), G3 (buyer-numerator-only), G4 (free-action != buyer),
# G5/G6 (within-buyer fulfillment/returns != skeptic), G8 (anchor masquerade /
# provenance), G9 (sum-to-100, structural via MarketDistribution), G10
# (calibration needs approval, always-on warning), G11 (estimate floor —
# BLOCKS direct_observed, downgrades others). G7 (retrospective != clean
# holdout) is surfaced here as a warning + clean_holdout_eligible=False and is
# ENFORCED by the ledger anti-leakage gate; G12 (concentration) is a SET-level
# check enforced in mapping_readiness. A training-eligible distribution mapping
# is cross-checked against its candidate (the source of truth for anchor type
# and fulfillment/returns flags) so a fabrication cannot pass by omitting its
# own incriminating metadata.
# --------------------------------------------------------------------------


def validate_mapping(
    proposed: ProposedOutcomeMapping,
    candidate: CandidateCase | None = None,
) -> MappingValidationResult:
    """Run the Phase 15L-B gates on a proposed mapping. Returns a structured
    verdict. Mutates nothing, writes nothing, invents nothing."""
    issues: list[str] = []
    warnings: list[str] = []
    gate_codes: list[str] = []
    forced_confidence: MappingConfidence | None = None

    mt = proposed.mapping_type
    props = proposed.proposed_proportions
    has_props = props is not None
    needs_props = mt in ("direct_observed_distribution", "assumption_labeled_distribution")
    must_be_null = mt in ("action_anchor_only", "evidence_only", "reject")

    # --- The candidate is the SOURCE OF TRUTH for anchor type + fulfillment/
    # returns flags. A training-eligible distribution mapping MUST be validated
    # against it, or a fabrication could simply omit its incriminating metadata
    # and pass when validated in isolation. ---
    if needs_props:
        if candidate is None:
            issues.append(
                "G2/G4/G5/G6: a training-eligible distribution mapping must be validated "
                "against its candidate (the source of truth for anchor type and "
                "fulfillment/returns flags) — candidate is required"
            )
            gate_codes.append("G_candidate_required")
        elif candidate.candidate_id != proposed.candidate_id:
            issues.append(
                f"candidate mismatch: proposal is for {proposed.candidate_id!r} but the "
                f"candidate supplied is {candidate.candidate_id!r} — cannot launder one "
                "candidate's mapping past another's evidence"
            )
            gate_codes.append("G_candidate_required")

    # Effective flags + anchor types: union of proposal + candidate, with the
    # candidate authoritative (a proposal may ADD flags but cannot HIDE the
    # candidate's). G4/G5/G6 read from this union, not the proposal's echo.
    flags = _flags(proposed, candidate)
    cand_sig_types = _anchor_signal_types(candidate)
    declared_anchor = proposed.buyer_anchor_signal_type
    effective_anchor = declared_anchor or (cand_sig_types[0] if cand_sig_types else None)
    all_anchor_types = [t for t in ([declared_anchor] + cand_sig_types) if t]

    # --- Structural: proportions presence matches the declared type ---
    if needs_props and not has_props:
        issues.append(
            f"{mt} requires proposed_proportions (a full four-bucket distribution)"
        )
        gate_codes.append("G3_buyer_numerator_only")
    if must_be_null and has_props:
        issues.append(
            f"{mt} must NOT carry proposed_proportions — it records only the anchor "
            "(or nothing); a distribution here would masquerade as observed data"
        )
        gate_codes.append("G8_anchor_masquerade")

    # --- Per-bucket basis coherence (G8 provenance) ---
    basis_by_bucket = {r.bucket: r.basis for r in proposed.bucket_rationales}
    # A mapping is an observed-outcome PROPOSAL; it can never establish clean
    # holdout (that needs a prediction locked BEFORE the outcome — G7).
    clean_holdout_eligible = False

    if mt == "direct_observed_distribution":
        _gate_direct_observed(
            proposed, basis_by_bucket, flags, issues, warnings, gate_codes
        )
        provenance: MappingProvenance = "measured_four_bucket"
        warnings.append(
            "G7: a retrospective observed mapping is TRAINING-only — never a clean "
            "holdout (a clean holdout needs a prediction locked BEFORE the outcome)"
        )
        gate_codes.append("G7_retrospective_not_holdout")
    elif mt == "assumption_labeled_distribution":
        forced_confidence = "low"
        _gate_assumption_labeled(
            proposed, basis_by_bucket, flags, all_anchor_types, issues, warnings, gate_codes
        )
        provenance = "assumption_based_labeled"
        warnings.append(
            "G7: a retrospective assumption-labeled mapping is TRAINING-only (capped, "
            "down-weighted) — never a clean holdout"
        )
        gate_codes.append("G7_retrospective_not_holdout")
    elif mt == "action_anchor_only":
        forced_confidence = _gate_action_anchor_only(
            proposed, effective_anchor, flags, issues, warnings, gate_codes
        )
        provenance = "buyer_anchor_only"
    elif mt == "evidence_only":
        forced_confidence = "low"
        provenance = "none"
    else:  # reject
        provenance = "none"

    # --- Cross-cutting gates applied whenever a distribution is present ---
    if has_props and mt != "reject":
        _gate_within_buyer_confusion(
            proposed, props, flags, issues, warnings, gate_codes
        )
        # G9 (sum-to-100) is enforced structurally by MarketDistribution; note it.
        gate_codes.append("G9_sum_to_100_structural")

    # --- G10: a proposal can never be calibration-ready on its own ---
    # human_approved is pinned False by the schema; surface the discipline.
    warnings.append(
        "G10: proposal is not calibration-ready — requires the factory "
        "ReviewerChecklist completion + an explicit human approval transition"
    )

    ok = not issues
    counts_direct = ok and mt == "direct_observed_distribution"
    counts_tier12 = ok and _counts_toward_tier1_2(mt, effective_anchor, flags)
    training_eligible = ok and mt in (
        "direct_observed_distribution",
        "assumption_labeled_distribution",
    )

    return MappingValidationResult(
        candidate_id=proposed.candidate_id,
        mapping_type=mt,
        ok=ok,
        issues=issues,
        warnings=warnings,
        provenance=provenance,
        forced_confidence=forced_confidence,
        training_eligible=training_eligible,
        counts_toward_direct_observed_bar=counts_direct,
        counts_toward_tier1_2_evidence=counts_tier12,
        clean_holdout_eligible=clean_holdout_eligible,
        gate_codes=sorted(set(gate_codes)),
    )


def _gate_direct_observed(
    proposed, basis_by_bucket, flags, issues, warnings, gate_codes
) -> None:
    # G11: a MEASURED census/sample cannot itself be a third-party estimate — that
    # is a contradiction, so it BLOCKS (downgrade to assumption_labeled instead).
    if proposed.estimate_quality == "third_party_estimate" or _has_flag(flags, *_FLAG_ESTIMATE):
        issues.append(
            "G11: a direct_observed MEASURED distribution cannot be built on third-party "
            "/ press estimates — use a primary/audited source, or downgrade to "
            "assumption_labeled_distribution"
        )
        gate_codes.append("G11_estimate_floor")
    # G1: a real, typed, representative denominator is mandatory.
    if proposed.denominator_quality not in _REPRESENTATIVE_DENOMINATORS:
        issues.append(
            "G1/G2: direct_observed_distribution requires a representative or census "
            f"denominator (got denominator_quality={proposed.denominator_quality!r}); "
            "a self-selected funnel cannot yield OBSERVED non-buyer buckets"
        )
        gate_codes.append("G1_denominator_known")
        gate_codes.append("G2_self_selected_sample")
    if proposed.denominator_type == "unknown" or not proposed.denominator_count:
        issues.append(
            "G1: direct_observed_distribution requires a known denominator_type and a "
            "positive denominator_count"
        )
        gate_codes.append("G1_denominator_known")
    # G8: a rationale for EVERY bucket, each independently OBSERVED, each cited.
    missing = [b for b in _BUCKET_NAMES if b not in basis_by_bucket]
    if missing:
        issues.append(
            "G8: direct_observed requires a bucket_rationale for every bucket; missing: "
            + ", ".join(missing)
        )
        gate_codes.append("G8_anchor_masquerade")
    for b in _BUCKET_NAMES:
        basis = basis_by_bucket.get(b)
        if basis is not None and basis != "observed":
            issues.append(
                f"G8: direct_observed requires bucket {b!r} to be 'observed' "
                f"(got basis={basis!r}); an assumption-based bucket means this is an "
                "assumption_labeled_distribution, not direct_observed"
            )
            gate_codes.append("G8_anchor_masquerade")
    missing_cite = [
        r.bucket
        for r in proposed.bucket_rationales
        if r.basis == "observed" and not (r.source_reference or "").strip()
    ]
    if missing_cite:
        issues.append(
            "G8: each observed bucket needs a source_reference; missing for: "
            + ", ".join(missing_cite)
        )
        gate_codes.append("G8_anchor_masquerade")


def _gate_assumption_labeled(
    proposed, basis_by_bucket, flags, all_anchor_types, issues, warnings, gate_codes
) -> None:
    # Explicit written assumptions are mandatory (G2 escape hatch).
    if not [a for a in proposed.assumptions if str(a).strip()]:
        issues.append(
            "G2: assumption_labeled_distribution requires explicit written "
            "assumptions (the priors used to split the non-buyer mass)"
        )
        gate_codes.append("G2_self_selected_sample")
    # A rationale for every bucket is required (no inventing four buckets 'from a
    # vibe' — the buyer anchor must be OBSERVED, the non-buyer buckets ASSUMPTION).
    missing = [b for b in _BUCKET_NAMES if b not in basis_by_bucket]
    if missing:
        issues.append(
            "G2/G8: assumption_labeled requires a bucket_rationale for every bucket; "
            "missing: " + ", ".join(missing)
        )
        gate_codes.append("G8_anchor_masquerade")
    if basis_by_bucket.get("buyer_action_positive") != "observed":
        issues.append(
            "G2/G8: assumption_labeled requires buyer_action_positive to be an OBSERVED "
            "anchor (basis='observed'); only the three non-buyer buckets are imported "
            "priors — all four cannot be assumptions"
        )
        gate_codes.append("G8_anchor_masquerade")
    # The non-buyer buckets must be labeled 'assumption', not 'observed'.
    mislabeled = [
        b for b in _NON_BUYER_BUCKETS if basis_by_bucket.get(b) == "observed"
    ]
    if mislabeled:
        issues.append(
            "G2/G8: in an assumption_labeled mapping the non-buyer buckets are "
            "imported priors, not observations; remove 'observed' basis from: "
            + ", ".join(mislabeled)
        )
        gate_codes.append("G8_anchor_masquerade")
    # A flag marking the distribution assumption-based must be present.
    if not _has_flag(flags, _FLAG_ASSUMPTION):
        warnings.append(
            "G2: add an 'assumption_based_mapping' uncertainty flag so calibration "
            "treats this as down-weighted, non-observed evidence"
        )
        gate_codes.append("G2_self_selected_sample")
    # G4: a free action (from the candidate's signals OR the proposal) may not
    # anchor a buyer percentage without an explicit weak-proxy label.
    has_free = any(_is_free_anchor(t) for t in all_anchor_types)
    has_paid = any(_is_paid_anchor(t) for t in all_anchor_types)
    if has_free and not has_paid and not proposed.free_action_weak_proxy:
        issues.append(
            "G4: a free action cannot anchor a buyer percentage without "
            "free_action_weak_proxy=true (free actions over-count curiosity, not "
            "buyer commitment)"
        )
        gate_codes.append("G4_free_action_not_buyer")


def _gate_action_anchor_only(
    proposed, anchor_type, flags, issues, warnings, gate_codes
) -> MappingConfidence:
    # Must actually carry an anchor.
    if proposed.buyer_anchor_count is None and proposed.buyer_anchor_signal_type is None:
        issues.append(
            "action_anchor_only must record the buyer/action anchor "
            "(signal_type + count + source)"
        )
        gate_codes.append("G3_buyer_numerator_only")
    # A free or estimate-based anchor is forced to low confidence.
    if _is_free_anchor(anchor_type) or _has_flag(flags, *_FLAG_ESTIMATE, _FLAG_FREE_INSTALL):
        warnings.append(
            "G4/G11: free or estimate-based anchor — confidence forced to 'low'; this "
            "anchor cannot assert a precise buyer share"
        )
        gate_codes.append("G4_free_action_not_buyer")
        return "low"
    return "medium"


def _gate_within_buyer_confusion(
    proposed, props, flags, issues, warnings, gate_codes
) -> None:
    """G5/G6: a within-buyer fulfillment-failure / returns cohort must never be
    folded into ANY non-buyer bucket (skeptical, uncertain, OR receptive). A note
    string alone cannot prove the cohort was moved out — require a substantive
    note AND a dedicated within_buyer_negative_count recorded OUTSIDE the
    distribution."""
    fulfillment = _has_flag(flags, *_FLAG_FULFILLMENT)
    returns = _has_flag(flags, *_FLAG_RETURNS)
    if not (fulfillment or returns):
        return
    nonbuyer_mass = (
        props.receptive + props.uncertain_proof_needed + props.skeptical_resistant
    )
    if nonbuyer_mass <= 0:
        return
    note = (proposed.within_buyer_split_note or "").strip()
    count = proposed.within_buyer_negative_count
    if len(note) < 20 or not count or count <= 0:
        kind = "fulfillment failure" if fulfillment else "returns/churn"
        issues.append(
            f"G5/G6: this candidate has a within-buyer {kind} cohort and the mapping "
            "assigns non-buyer mass. Dissatisfied / returning / undelivered BUYERS are "
            "NOT market non-buyers — record that cohort in a dedicated "
            "within_buyer_negative_count (a positive count, OUTSIDE every non-buyer "
            "bucket) AND a substantive within_buyer_split_note, or net it out of the "
            "buyer numerator. A note string alone cannot prove the mass was moved"
        )
        gate_codes.append("G5_fulfillment_not_skeptic" if fulfillment else "G6_returns_not_skeptic")


def _counts_toward_tier1_2(mt: str, anchor_type: str | None, flags: Sequence[str]) -> bool:
    """A real Tier-1/2 ACTION anchor satisfies the Phase 15E Tier-1/2 evidence
    requirement — but a soft free/estimate anchor (evidence_only) does not until a
    reviewer resolves it, and an assumption-labeled DISTRIBUTION never counts via
    its assumed buckets (only via its real anchor)."""
    if mt == "reject":
        return False
    if mt == "evidence_only":
        return False
    if _is_paid_anchor(anchor_type):
        return True
    if _is_free_anchor(anchor_type):
        # free actions are Tier-1/2 by taxonomy but only count once reviewer-resolved
        return False
    return anchor_type is not None


# --------------------------------------------------------------------------
# Readiness — mapping-QUALITY-aware Phase 15E gate (Agent 4)
# --------------------------------------------------------------------------


def mapping_readiness(
    classifications: Sequence[tuple[CandidateCase, str]] = (),
    *,
    ledger_cases: Sequence[ValidationCase] = (),
    target_case_count: int = _DEFAULT_TARGET_CASE_COUNT,
    assumption_cap_fraction: float = _DEFAULT_ASSUMPTION_CAP_FRACTION,
    source_concentration_cap: int = _DEFAULT_SOURCE_CONCENTRATION_CAP,
) -> dict:
    """Mapping-quality-aware Phase 15E readiness. Only ``direct_observed`` mappings
    count toward the >=target distribution bar; assumption-labeled cases are
    capped to a minority and can never be the marginal reason a threshold is met.
    Read-only: counts only, emits no distribution, changes nothing.

    ``classifications`` is a list of (candidate, mapping_type) — the HYPOTHETICAL
    classification of candidates; nothing is ingested. Clean-holdout and Tier-1/2
    counts are judged on the LIVE ledger (real validation data)."""
    by_type: dict[str, int] = {
        "direct_observed_distribution": 0,
        "assumption_labeled_distribution": 0,
        "action_anchor_only": 0,
        "evidence_only": 0,
        "reject": 0,
    }
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    entity_clusters: dict[str, list[str]] = {}
    for cand, mt in classifications:
        by_type[mt] = by_type.get(mt, 0) + 1
        by_source[cand.source_type] = by_source.get(cand.source_type, 0) + 1
        by_category[cand.category] = by_category.get(cand.category, 0) + 1
        key = (cand.product_or_company_name or "").strip().lower().split(" ")[0]
        entity_clusters.setdefault(key, []).append(cand.candidate_id)

    n_direct = by_type["direct_observed_distribution"]
    n_assumption = by_type["assumption_labeled_distribution"]

    ledger = list(ledger_cases)
    ledger_total = len(ledger)
    clean_holdout = sum(1 for c in ledger if is_clean_holdout(c))
    tier1_2_outcome_cases = sum(
        1 for c in ledger if {s.tier for s in c.action_signals if s.tier is not None} & {1, 2}
    )

    # integer floor of (n_direct * fraction); +epsilon guards the exact-1/3 case
    # from float undershoot (e.g. 3 * (1/3) == 0.999... -> should floor to 1).
    assumption_cap = int(n_direct * assumption_cap_fraction + 1e-9)
    assumption_over_cap = n_assumption > assumption_cap

    non_independent = {k: v for k, v in entity_clusters.items() if len(v) > 1}
    source_over_cap = {
        s: n for s, n in by_source.items() if n > source_concentration_cap
    }

    # weak_mapping_warning: the >=target bar must not be met by weak mappings.
    weak_reasons: list[str] = []
    counted_total = ledger_total  # only real ledger cases exist today (0 ingested)
    if counted_total >= target_case_count and n_direct < target_case_count:
        weak_reasons.append(
            "case count would reach the target only via non-direct (weak) mappings"
        )
    if assumption_over_cap:
        weak_reasons.append(
            f"assumption_labeled cases ({n_assumption}) exceed the cap "
            f"({assumption_cap} = floor({assumption_cap_fraction:.2f} x {n_direct} direct))"
        )
    if non_independent:
        weak_reasons.append(
            "non-independent entity clusters present (same company, correlated): "
            + ", ".join(f"{k}({len(v)})" for k, v in non_independent.items())
        )
    if source_over_cap:
        weak_reasons.append(
            "source concentration over cap: "
            + ", ".join(f"{s}={n}>{source_concentration_cap}" for s, n in source_over_cap.items())
        )
    weak_mapping_warning = bool(weak_reasons)

    unmet: list[str] = []
    if n_direct < target_case_count:
        unmet.append(
            f"need >={target_case_count} DIRECT-observed-distribution cases "
            f"(have {n_direct}; assumption/anchor/evidence-only do NOT count)"
        )
    if clean_holdout < 1:
        unmet.append("need >=1 clean holdout case (have 0; obtainable only prospectively)")
    if tier1_2_outcome_cases < 1:
        unmet.append("need >=1 ledger case with Tier-1/2 action outcomes (have 0)")
    if assumption_over_cap:
        unmet.append(
            f"assumption_labeled cases exceed the minority cap ({assumption_cap})"
        )
    if weak_mapping_warning:
        unmet.append("weak_mapping_warning is set (threshold met only by weak mappings)")

    phase_15e_blocked = bool(unmet)

    # Set-level gate markers (for traceability/symmetry with validate_mapping):
    # G12 = concentration cap; G7 = clean holdout is only obtainable prospectively.
    gate_codes = ["G7_clean_holdout_prospective_only"]
    if source_over_cap or non_independent:
        gate_codes.append("G12_concentration_cap")

    return {
        "protocol_version": PROTOCOL_VERSION,
        "n_classified": len(classifications),
        "gate_codes": gate_codes,
        "mapping_type_breakdown": by_type,
        "n_direct_observed_distribution_cases": n_direct,
        "n_assumption_labeled_cases": n_assumption,
        "n_action_anchor_only_cases": by_type["action_anchor_only"],
        "n_evidence_only_cases": by_type["evidence_only"],
        "n_rejected_cases": by_type["reject"],
        "ledger_total_cases": ledger_total,
        "ledger_clean_holdout": clean_holdout,
        "ledger_tier1_2_outcome_cases": tier1_2_outcome_cases,
        "readiness_target_case_count": target_case_count,
        "direct_cases_short_of_target": max(0, target_case_count - n_direct),
        "assumption_labeled_cap": assumption_cap,
        "assumption_labeled_over_cap": assumption_over_cap,
        "source_concentration_by_source_type": by_source,
        "source_concentration_by_category": by_category,
        "source_concentration_over_cap": source_over_cap,
        "non_independent_entity_clusters": non_independent,
        "weak_mapping_warning": weak_mapping_warning,
        "weak_mapping_warning_reasons": weak_reasons,
        "phase_15e_blocked": phase_15e_blocked,
        "phase_15e_unmet_requirements": unmet,
    }


# --------------------------------------------------------------------------
# Protocol wrapper (named structure) + template
# --------------------------------------------------------------------------


class OutcomeMappingProtocol(BaseModel):
    """The protocol's configuration + entry points. Immutable defaults encode the
    anti-overfit caps; methods delegate to the module-level functions so the rules
    live in one place. Construct ``OutcomeMappingProtocol()`` for the defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = PROTOCOL_VERSION
    target_case_count: int = _DEFAULT_TARGET_CASE_COUNT
    assumption_cap_fraction: float = _DEFAULT_ASSUMPTION_CAP_FRACTION
    source_concentration_cap: int = _DEFAULT_SOURCE_CONCENTRATION_CAP

    def classify(self, candidate: CandidateCase) -> tuple[OutcomeMappingType, list[str]]:
        return classify_candidate(candidate)

    def validate(
        self, proposed: ProposedOutcomeMapping, candidate: CandidateCase | None = None
    ) -> MappingValidationResult:
        return validate_mapping(proposed, candidate)

    def readiness(
        self,
        classifications: Sequence[tuple[CandidateCase, str]] = (),
        *,
        ledger_cases: Sequence[ValidationCase] = (),
    ) -> dict:
        return mapping_readiness(
            classifications,
            ledger_cases=ledger_cases,
            target_case_count=self.target_case_count,
            assumption_cap_fraction=self.assumption_cap_fraction,
            source_concentration_cap=self.source_concentration_cap,
        )


def mapping_proposal_template(candidate: CandidateCase | None = None) -> dict:
    """A blank, human-fillable proposal template (a dict; the CLI prints it).
    Pre-fills candidate_id + a known anchor from the candidate when given. Carries
    the isolation marker and human_approved=False so it can never be mistaken for
    validation data."""
    anchor_type = anchor_count = anchor_dir = anchor_src = None
    if candidate is not None and candidate.action_signal_candidates:
        s = candidate.action_signal_candidates[0]
        anchor_type, anchor_count = s.signal_type, s.count
        anchor_dir, anchor_src = s.direction, s.source_reference
    return {
        "purpose": MAPPING_PROPOSAL_PURPOSE,
        "candidate_id": candidate.candidate_id if candidate else "<candidate_id>",
        "mapping_type": "<one of: direct_observed_distribution | assumption_labeled_distribution | action_anchor_only | evidence_only | reject>",
        "proposed_proportions": None,  # null unless direct/assumption (must sum ~100)
        "bucket_rationales": [
            {"bucket": b, "basis": "<observed|assumption|unmapped>", "rationale": "", "source_reference": ""}
            for b in _BUCKET_NAMES
        ],
        "buyer_anchor_signal_type": anchor_type,
        "buyer_anchor_count": anchor_count,
        "buyer_anchor_direction": anchor_dir,
        "buyer_anchor_source": anchor_src or "",
        "denominator_type": "unknown",
        "denominator_count": None,
        "denominator_quality": "unknown",
        "denominator_explanation": "",
        "assumptions": [],
        "source_bias_explanation": "",
        "within_buyer_split_note": "",
        "within_buyer_negative_count": None,
        "free_action_weak_proxy": False,
        "estimate_quality": "third_party_estimate",
        "confidence": "low",
        "uncertainty_flags": list(candidate.uncertainty_flags) if candidate else [],
        "reviewer_notes": "",
        "training_eligibility_recommendation": "undecided",
        "human_approved": False,
        "reviewer": "",
        "reviewed_at": None,
    }
