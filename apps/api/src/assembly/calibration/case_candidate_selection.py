"""Phase 12A.3 — Real blinded historical case candidate selection.

This module is a **gatekeeper**: it scores hand-authored
candidate metadata for suitability as a blinded calibration case,
and refuses (or downgrades) candidates that would compromise the
North-Star calibration objective.

What this module is NOT:
  - It does NOT scrape, fetch, or call any API.
  - It does NOT instantiate a :class:`BlindCase` (that's Phase 12A.1
    + 12A.2's surface, and requires hidden outcome data which we
    deliberately do not have yet).
  - It does NOT store any real outcome distribution.
  - It does NOT name a candidate's product without operator
    confirmation. Candidate names come from caller-supplied
    metadata; this module only scores what it is given.

Honesty rule:
  If any required field is missing or marked ``"unknown"``, the
  candidate's recommendation drops to ``"unverified"`` and the
  scorecard records a ``"unverified_metadata"`` risk flag. We do
  not pretend to score what we cannot see.

Contamination rule:
  Two contamination dimensions are tracked separately:
    - ``contamination_risk``  : was the candidate (or close proxies)
                                already used to develop Assembly's
                                evidence/signal layers? (Vivago and
                                Semble flagged at construction time.)
    - ``model_prior_risk``    : is the candidate famous enough that
                                a pretrained LLM is likely to "know"
                                the outcome regardless of the brief?
  Either being ``"high"`` causes ``"reject"``.

The output of this phase is **structured judgment**, not a curated
list of real products with verified outcomes. The actual real-case
JSONs (with hidden outcomes) come in a later phase only after the
operator approves specific candidates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


CandidateRecommendation = Literal["accept", "maybe", "reject", "unverified"]

ContaminationLevel = Literal["none", "low", "medium", "high"]
PriorRiskLevel = Literal["low", "medium", "high"]
OutcomeQuality = Literal["unknown", "weak", "medium", "strong"]
CutoffClarity = Literal["unclear", "approximate", "clear"]
CategoryFit = Literal["none", "weak", "medium", "strong"]
SourceAccess = Literal[
    "forbidden",            # licensed-and-locked or ToS-forbidden
    "scraping_required",    # only obtainable via scraping
    "operator_supply",      # operator must provide manually
    "public_no_scrape",     # public-readable but not bulk-scraped
    "open_data",            # bulk dataset or operator-licensed export
]
ObservationCountBucket = Literal[
    "unknown", "<30", "30-100", "100-500", "500+",
]

CandidateRiskFlag = Literal[
    "contaminated_in_signal_layer",
    "model_prior_too_strong",
    "weak_outcome_data",
    "insufficient_observations",
    "vague_cutoff_date",
    "outcome_only_revenue_or_funding",
    "all_promotional_comments",
    "requires_unauthorized_scraping",
    "post_launch_leak_in_brief",
    "category_mismatch",
    "source_access_forbidden",
    "unverified_metadata",
]


# Candidates known to overlap Assembly's existing signal-development
# corpora. These names are case-insensitive substring matches; any
# candidate whose ``product_name`` contains one of these triggers an
# automatic ``contaminated_in_signal_layer`` flag and contamination
# risk = "high" regardless of operator-supplied value. The list is
# intentionally small — extend only with explicit operator approval.
_KNOWN_CONTAMINATED_PRODUCTS: tuple[str, ...] = (
    "vivago",   # Phase 11D.3 / 11D.4 Vivago Product Hunt rows
    "semble",   # Phase 11D.6 / 11D.8 Semble HN devtool rows
)


# ---------------------------------------------------------------------------
# CaseCandidate
# ---------------------------------------------------------------------------


@dataclass
class CaseCandidate:
    """Metadata-only representation of a potential calibration case.

    Construction is intentionally tolerant: any field may be marked
    ``"unknown"`` (or left as a default) — :func:`evaluate_candidate_suitability`
    will surface the missing-data condition as
    ``"unverified_metadata"`` and downgrade the recommendation.

    No outcome data, no raw comments, no URLs — only the structural
    metadata needed to judge whether a candidate is worth pursuing.
    """

    candidate_id: str
    product_name: str
    category: str
    launch_or_cutoff_date: date | None = None
    pre_launch_sources_available: list[str] = field(default_factory=list)
    outcome_sources_available: list[str] = field(default_factory=list)
    estimated_observation_count: ObservationCountBucket = "unknown"
    contamination_risk: ContaminationLevel = "low"
    model_prior_risk: PriorRiskLevel = "medium"
    outcome_quality: OutcomeQuality = "unknown"
    cutoff_clarity: CutoffClarity = "approximate"
    category_fit: CategoryFit = "weak"
    source_access_risk: SourceAccess = "operator_supply"
    notes: str = ""
    operator_recommendation: CandidateRecommendation | None = None


# ---------------------------------------------------------------------------
# Risk flag detection
# ---------------------------------------------------------------------------


def candidate_risk_flags(c: CaseCandidate) -> list[CandidateRiskFlag]:
    """Return all risk flags that apply to ``c``. Pure function — no
    side effects, no IO."""
    flags: list[CandidateRiskFlag] = []

    # Hard contamination: if the product name matches a known
    # signal-development corpus, we override whatever the operator
    # set on ``contamination_risk``.
    name_lc = (c.product_name or "").lower()
    if any(needle in name_lc for needle in _KNOWN_CONTAMINATED_PRODUCTS):
        flags.append("contaminated_in_signal_layer")

    if c.contamination_risk == "high":
        if "contaminated_in_signal_layer" not in flags:
            flags.append("contaminated_in_signal_layer")

    if c.model_prior_risk == "high":
        flags.append("model_prior_too_strong")

    if c.outcome_quality in ("weak", "unknown"):
        flags.append("weak_outcome_data")

    if c.estimated_observation_count in ("<30", "unknown"):
        flags.append("insufficient_observations")

    if c.cutoff_clarity == "unclear" or c.launch_or_cutoff_date is None:
        flags.append("vague_cutoff_date")

    if c.source_access_risk == "scraping_required":
        flags.append("requires_unauthorized_scraping")

    if c.source_access_risk == "forbidden":
        flags.append("source_access_forbidden")

    if c.category_fit == "none":
        flags.append("category_mismatch")

    # "unverified_metadata" is the umbrella flag for any candidate
    # whose required structural fields are not specified.
    if _is_unverified(c):
        flags.append("unverified_metadata")

    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[CandidateRiskFlag] = []
    for f in flags:
        if f not in seen:
            out.append(f)
            seen.add(f)
    return out


def _is_unverified(c: CaseCandidate) -> bool:
    """A candidate is *unverified* when key structural metadata is
    missing — we cannot honestly score it."""
    if c.launch_or_cutoff_date is None:
        return True
    if c.outcome_quality == "unknown":
        return True
    if c.estimated_observation_count == "unknown":
        return True
    if not c.pre_launch_sources_available:
        return True
    if not c.outcome_sources_available:
        return True
    if c.cutoff_clarity == "unclear":
        return True
    return False


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


_CUTOFF_SCORE: dict[CutoffClarity, int] = {
    "unclear": 0, "approximate": 1, "clear": 2,
}
_OBS_SCORE: dict[ObservationCountBucket, int] = {
    "unknown": 0, "<30": 0, "30-100": 1, "100-500": 2, "500+": 3,
}
_OUTCOME_SCORE: dict[OutcomeQuality, int] = {
    "unknown": 0, "weak": 0, "medium": 1, "strong": 2,
}
# inverse: less contamination = more score
_CONTAM_SCORE: dict[ContaminationLevel, int] = {
    "high": 0, "medium": 1, "low": 2, "none": 2,
}
_PRIOR_SCORE: dict[PriorRiskLevel, int] = {
    "high": 0, "medium": 1, "low": 2,
}
_SOURCE_SCORE: dict[SourceAccess, int] = {
    "forbidden": 0,
    "scraping_required": 0,
    "operator_supply": 1,
    "public_no_scrape": 2,
    "open_data": 2,
}
_CATEGORY_SCORE: dict[CategoryFit, int] = {
    "none": 0, "weak": 1, "medium": 2, "strong": 3,
}


# Sum of per-dimension maxes — used to express the calibration value
# as a fraction in [0, 1] so the threshold logic is interpretable.
_MAX_TOTAL_SCORE = (
    max(_CUTOFF_SCORE.values())
    + max(_OBS_SCORE.values())
    + max(_OUTCOME_SCORE.values())
    + max(_CONTAM_SCORE.values())
    + max(_PRIOR_SCORE.values())
    + max(_SOURCE_SCORE.values())
    + max(_CATEGORY_SCORE.values())
)


def candidate_scorecard(c: CaseCandidate) -> dict:
    """Per-dimension scores plus a normalized total.

    Returns:
      {
        "dimensions": {
          "cutoff_clarity": int,
          "observation_count": int,
          "outcome_label_mappability": int,   # derived from outcome_quality
          "pre_post_separation_quality": int, # derived from cutoff_clarity
          "contamination_risk_inverse": int,
          "model_prior_risk_inverse": int,
          "source_accessibility": int,
          "category_fit": int,
        },
        "raw_total": int,
        "max_total": int,
        "calibration_value": float,   # 0.0 - 1.0
        "risk_flags": list[CandidateRiskFlag],
      }

    Deterministic. No randomness, no IO.
    """
    cutoff = _CUTOFF_SCORE[c.cutoff_clarity]
    obs = _OBS_SCORE[c.estimated_observation_count]
    outcome = _OUTCOME_SCORE[c.outcome_quality]
    contam = _CONTAM_SCORE[c.contamination_risk]
    # If the product is on the known-contaminated list, force the
    # inverse contamination score down to 0 regardless of operator
    # value. This protects the calibration corpus from a hand-curated
    # candidate that the operator may have mis-classified.
    if any(
        needle in (c.product_name or "").lower()
        for needle in _KNOWN_CONTAMINATED_PRODUCTS
    ):
        contam = 0
    prior = _PRIOR_SCORE[c.model_prior_risk]
    source = _SOURCE_SCORE[c.source_access_risk]
    cat = _CATEGORY_SCORE[c.category_fit]
    dims = {
        "cutoff_clarity": cutoff,
        "observation_count": obs,
        "outcome_label_mappability": outcome,
        "pre_post_separation_quality": cutoff,  # tied to cutoff clarity
        "contamination_risk_inverse": contam,
        "model_prior_risk_inverse": prior,
        "source_accessibility": source,
        "category_fit": cat,
    }
    raw = (
        cutoff + obs + outcome + contam + prior + source + cat
    )
    cv = raw / _MAX_TOTAL_SCORE if _MAX_TOTAL_SCORE else 0.0
    return {
        "dimensions": dims,
        "raw_total": raw,
        "max_total": _MAX_TOTAL_SCORE,
        "calibration_value": cv,
        "risk_flags": candidate_risk_flags(c),
    }


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


# Thresholds tuned for the 4 + 16 = ~16 max-score scheme. These are
# documented inline so a future tuning pass can change them with
# context.
_ACCEPT_CV_FLOOR = 0.70   # raw >= ~11 / 16
_MAYBE_CV_FLOOR = 0.45    # raw >= ~7  / 16

# Risk flags that auto-disqualify regardless of scorecard.
_AUTO_REJECT_FLAGS: frozenset[CandidateRiskFlag] = frozenset({
    "contaminated_in_signal_layer",
    "model_prior_too_strong",
    "requires_unauthorized_scraping",
    "source_access_forbidden",
    "post_launch_leak_in_brief",
})


def evaluate_candidate_suitability(
    c: CaseCandidate,
) -> CandidateRecommendation:
    """Compute a recommendation from candidate metadata.

    Priority order:
      1. ``operator_recommendation`` (if set) overrides everything.
      2. ``"unverified_metadata"`` flag → ``"unverified"``.
      3. Any flag in ``_AUTO_REJECT_FLAGS`` → ``"reject"``.
      4. Scorecard calibration_value >= 0.70 → ``"accept"``.
      5. Scorecard calibration_value >= 0.45 → ``"maybe"``.
      6. Else → ``"reject"``.
    """
    if c.operator_recommendation is not None:
        return c.operator_recommendation
    flags = set(candidate_risk_flags(c))
    if "unverified_metadata" in flags:
        return "unverified"
    if flags & _AUTO_REJECT_FLAGS:
        return "reject"
    cv = candidate_scorecard(c)["calibration_value"]
    if cv >= _ACCEPT_CV_FLOOR:
        return "accept"
    if cv >= _MAYBE_CV_FLOOR:
        return "maybe"
    return "reject"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def rank_case_candidates(
    candidates: list[CaseCandidate],
) -> list[dict]:
    """Sort candidates by descending calibration value (deterministic
    tiebreak on ``candidate_id``). Returns a list of dicts:

      {
        "candidate_id": str,
        "product_name": str,
        "category": str,
        "recommendation": CandidateRecommendation,
        "calibration_value": float,
        "raw_total": int,
        "risk_flags": list[CandidateRiskFlag],
      }

    Pure function. ``candidates`` is not mutated.
    """
    rows: list[dict] = []
    for c in candidates:
        sc = candidate_scorecard(c)
        rows.append({
            "candidate_id": c.candidate_id,
            "product_name": c.product_name,
            "category": c.category,
            "recommendation": evaluate_candidate_suitability(c),
            "calibration_value": sc["calibration_value"],
            "raw_total": sc["raw_total"],
            "risk_flags": list(sc["risk_flags"]),
        })
    # Deterministic sort: cv desc, then candidate_id asc as tiebreak.
    rows.sort(
        key=lambda r: (-r["calibration_value"], r["candidate_id"]),
    )
    return rows
