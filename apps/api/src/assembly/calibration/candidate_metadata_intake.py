"""Phase 12A.4 — Operator-supplied real candidate metadata intake.

Bridges between **raw operator input** (hand-typed dicts or JSON
payloads describing a real product the operator wants to consider
for blinded validation) and the **Phase 12A.3 selection framework**
(:mod:`assembly.calibration.case_candidate_selection`).

Pipeline:

  raw operator dict
    -> parse_operator_candidate_metadata        -> IntakeRecord
    -> validate_operator_candidate_metadata     -> IntakeValidationResult
    -> convert_metadata_to_case_candidate       -> CaseCandidate
    -> score_operator_candidates                -> [ScoredOperatorCandidate]
    -> summarize_operator_candidate_batch       -> dict (ranked, with rollup)

What this module is NOT:

  - It does NOT call any LLM, hit any network, or write to any DB.
  - It does NOT scrape or fetch outcome data.
  - It does NOT fabricate metadata. Anything the operator omits
    becomes ``unknown`` and surfaces as an explicit follow-up
    question on the way out.
  - It does NOT instantiate a :class:`BlindCase` (that requires
    hidden outcome data, which we deliberately do not have here).

The intake is intentionally tolerant: missing fields produce a
``"unverified"`` recommendation and a specific follow-up question
rather than a hard rejection. The exception is when a field is
present but invalid (e.g. an unrecognized ``contamination_risk``
value) — that's surfaced as a validation issue so the operator
can correct it.

Honesty rule (Phase 12A.4 spec):
  > Do not pretend we know real-world outcome quality unless the
  > operator provides evidence.

We enforce this structurally: every operator-supplied closed-vocab
value must match a known Literal; anything else becomes a warning
and the field falls back to its safest default.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, get_args

from assembly.calibration.case_candidate_selection import (
    CandidateRecommendation,
    CandidateRiskFlag,
    CaseCandidate,
    CategoryFit,
    ContaminationLevel,
    CutoffClarity,
    ObservationCountBucket,
    OutcomeQuality,
    PriorRiskLevel,
    SourceAccess,
    candidate_risk_flags,
    candidate_scorecard,
    evaluate_candidate_suitability,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Closed-vocab acceptance sets, sourced directly from Phase 12A.3 Literals
# ---------------------------------------------------------------------------


_CONTAMINATION_VALUES = frozenset(get_args(ContaminationLevel))
_PRIOR_VALUES = frozenset(get_args(PriorRiskLevel))
_OUTCOME_VALUES = frozenset(get_args(OutcomeQuality))
_CUTOFF_VALUES = frozenset(get_args(CutoffClarity))
_CATEGORY_FIT_VALUES = frozenset(get_args(CategoryFit))
_SOURCE_ACCESS_VALUES = frozenset(get_args(SourceAccess))
_OBSERVATION_BUCKET_VALUES = frozenset(get_args(ObservationCountBucket))


_ALLOWED_INTAKE_KEYS: frozenset[str] = frozenset({
    "candidate_id",
    "product_name",
    "category",
    "launch_or_cutoff_date",
    "pre_launch_sources_available",
    "outcome_sources_available",
    "estimated_observation_count",
    "contamination_risk",
    "model_prior_risk",
    "outcome_quality",
    "cutoff_clarity",
    "category_fit",
    "source_access_risk",
    "notes",
    "operator_recommendation",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class IntakeRecord:
    """Parsed operator payload, BEFORE conversion to :class:`CaseCandidate`.

    Each closed-vocab field carries a typed value if it was valid,
    or ``None`` if it was missing or invalid. ``parse_warnings``
    surfaces every coercion decision so the operator can see what
    was accepted, what was defaulted, and what got rejected.
    """

    raw_payload: dict[str, Any]
    candidate_id: str | None = None
    product_name: str | None = None
    category: str | None = None
    launch_or_cutoff_date: date | None = None
    pre_launch_sources_available: list[str] = field(default_factory=list)
    outcome_sources_available: list[str] = field(default_factory=list)
    estimated_observation_count: ObservationCountBucket | None = None
    contamination_risk: ContaminationLevel | None = None
    model_prior_risk: PriorRiskLevel | None = None
    outcome_quality: OutcomeQuality | None = None
    cutoff_clarity: CutoffClarity | None = None
    category_fit: CategoryFit | None = None
    source_access_risk: SourceAccess | None = None
    notes: str = ""
    operator_recommendation: CandidateRecommendation | None = None
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class IntakeValidationResult:
    """Result of ``validate_operator_candidate_metadata``."""

    record: IntakeRecord
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    operator_followup_questions: list[str] = field(default_factory=list)


@dataclass
class ScoredOperatorCandidate:
    """A scored operator-supplied candidate.

    Combines the underlying :class:`CaseCandidate`, the scorecard
    output, the recommendation, and the follow-up questions the
    operator should be asked before this candidate becomes a real
    validation target.
    """

    candidate_id: str
    product_name: str
    category: str
    candidate: CaseCandidate
    recommendation: CandidateRecommendation
    calibration_value: float
    raw_total: int
    risk_flags: list[CandidateRiskFlag]
    missing_required_fields: list[str] = field(default_factory=list)
    missing_optional_fields: list[str] = field(default_factory=list)
    operator_followup_questions: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. parse
# ---------------------------------------------------------------------------


def parse_operator_candidate_metadata(
    payload: dict[str, Any],
) -> IntakeRecord:
    """Parse a raw operator dict into a structured :class:`IntakeRecord`.

    Tolerant by design: invalid closed-vocab values become ``None``
    plus a parse_warning, missing keys become defaults, and unknown
    top-level keys are surfaced as warnings (not errors — the operator
    may have a typo that's worth flagging without aborting intake).
    """
    if not isinstance(payload, dict):
        return IntakeRecord(
            raw_payload={"_invalid_payload": str(payload)[:200]},
            parse_warnings=[
                f"payload_not_a_dict (got {type(payload).__name__})"
            ],
        )

    rec = IntakeRecord(raw_payload=dict(payload))

    # Unknown top-level keys — warn, do not abort
    for k in payload.keys():
        if k not in _ALLOWED_INTAKE_KEYS:
            rec.parse_warnings.append(
                f"unknown_top_level_key={k!r}: ignored. Allowed keys "
                f"are {sorted(_ALLOWED_INTAKE_KEYS)}"
            )

    rec.candidate_id = _coerce_str(payload.get("candidate_id"))
    rec.product_name = _coerce_str(payload.get("product_name"))
    rec.category = _coerce_str(payload.get("category"))
    rec.notes = _coerce_str(payload.get("notes")) or ""

    rec.launch_or_cutoff_date = _coerce_date(
        payload.get("launch_or_cutoff_date"), rec.parse_warnings,
    )
    rec.pre_launch_sources_available = _coerce_str_list(
        payload.get("pre_launch_sources_available"),
        "pre_launch_sources_available",
        rec.parse_warnings,
    )
    rec.outcome_sources_available = _coerce_str_list(
        payload.get("outcome_sources_available"),
        "outcome_sources_available",
        rec.parse_warnings,
    )

    rec.estimated_observation_count = _coerce_enum(
        payload.get("estimated_observation_count"),
        _OBSERVATION_BUCKET_VALUES,
        "estimated_observation_count",
        rec.parse_warnings,
    )  # type: ignore[assignment]
    rec.contamination_risk = _coerce_enum(
        payload.get("contamination_risk"),
        _CONTAMINATION_VALUES,
        "contamination_risk",
        rec.parse_warnings,
    )  # type: ignore[assignment]
    rec.model_prior_risk = _coerce_enum(
        payload.get("model_prior_risk"),
        _PRIOR_VALUES,
        "model_prior_risk",
        rec.parse_warnings,
    )  # type: ignore[assignment]
    rec.outcome_quality = _coerce_enum(
        payload.get("outcome_quality"),
        _OUTCOME_VALUES,
        "outcome_quality",
        rec.parse_warnings,
    )  # type: ignore[assignment]
    rec.cutoff_clarity = _coerce_enum(
        payload.get("cutoff_clarity"),
        _CUTOFF_VALUES,
        "cutoff_clarity",
        rec.parse_warnings,
    )  # type: ignore[assignment]
    rec.category_fit = _coerce_enum(
        payload.get("category_fit"),
        _CATEGORY_FIT_VALUES,
        "category_fit",
        rec.parse_warnings,
    )  # type: ignore[assignment]
    rec.source_access_risk = _coerce_enum(
        payload.get("source_access_risk"),
        _SOURCE_ACCESS_VALUES,
        "source_access_risk",
        rec.parse_warnings,
    )  # type: ignore[assignment]
    op_rec = payload.get("operator_recommendation")
    if op_rec is not None:
        rec.operator_recommendation = _coerce_enum(
            op_rec,
            frozenset(("accept", "maybe", "reject", "unverified")),
            "operator_recommendation",
            rec.parse_warnings,
        )  # type: ignore[assignment]

    return rec


def _coerce_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    return str(v)


def _coerce_str_list(
    v: Any, key: str, warnings: list[str],
) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        # Permit single-string convenience for the common case
        return [v.strip()] if v.strip() else []
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for item in v:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            else:
                warnings.append(
                    f"non_string_in_{key}: ignored {item!r}"
                )
        return out
    warnings.append(f"unsupported_type_for_{key}: ignored {v!r}")
    return []


def _coerce_date(v: Any, warnings: list[str]) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        warnings.append(
            f"unparseable_date={v!r}: expected ISO YYYY-MM-DD"
        )
        return None
    warnings.append(f"unsupported_date_type={type(v).__name__}")
    return None


def _coerce_enum(
    v: Any, allowed: frozenset[str], key: str, warnings: list[str],
) -> str | None:
    """Match an operator-supplied string to a closed-vocab value.

    Normalization is intentionally conservative: lowercase +
    whitespace strip + space → underscore. Hyphens are PRESERVED
    because some bucket values legitimately contain hyphens
    (e.g. ``"30-100"``, ``"100-500"``). If the lowercased value
    doesn't match, we try one further pass collapsing internal
    spaces to underscores. Anything else falls through to a warning.
    """
    if v is None:
        return None
    if not isinstance(v, str):
        warnings.append(
            f"non_string_for_{key}={v!r}: expected one of "
            f"{sorted(allowed)}"
        )
        return None
    base = v.strip().lower()
    if base in allowed:
        return base
    # Bucket values like "30-100" contain literal hyphens, so we
    # first try the bare lowercased value (above). If that fails,
    # try one fallback that turns spaces AND hyphens into
    # underscores — this rescues operator inputs like "Open-Data"
    # → "open_data" without breaking the bucket strings, because
    # bucket strings would have matched on the first pass.
    fallback = base.replace(" ", "_").replace("-", "_")
    if fallback in allowed:
        return fallback
    warnings.append(
        f"invalid_value_for_{key}={v!r}: expected one of "
        f"{sorted(allowed)}"
    )
    return None


# ---------------------------------------------------------------------------
# 2. validate
# ---------------------------------------------------------------------------


_REQUIRED_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "product_name",
    "category",
)


_OPTIONAL_FIELDS_FOR_FULL_SCORING: tuple[str, ...] = (
    "launch_or_cutoff_date",
    "pre_launch_sources_available",
    "outcome_sources_available",
    "estimated_observation_count",
    "contamination_risk",
    "model_prior_risk",
    "outcome_quality",
    "cutoff_clarity",
    "category_fit",
    "source_access_risk",
)


def validate_operator_candidate_metadata(
    record: IntakeRecord,
) -> IntakeValidationResult:
    """Inspect a parsed record and produce structured validation
    output: errors (the record cannot become a CaseCandidate),
    warnings (it can, but with reduced confidence), missing-field
    lists, and explicit operator follow-up questions.
    """
    errors: list[str] = []
    warnings: list[str] = list(record.parse_warnings)
    missing_required: list[str] = []
    missing_optional: list[str] = []
    questions: list[str] = []

    # Required fields
    if not record.candidate_id:
        missing_required.append("candidate_id")
        errors.append("missing_required=candidate_id")
        questions.append(
            "What is the stable candidate_id for this product? "
            "(snake_case slug used to link to a prediction artifact)"
        )
    if not record.product_name:
        missing_required.append("product_name")
        errors.append("missing_required=product_name")
        questions.append("What is the product/startup name?")
    if not record.category:
        missing_required.append("category")
        errors.append("missing_required=category")
        questions.append(
            "What is the product category? (e.g. 'AI SaaS tool', "
            "'developer tool', 'consumer mobile app', 'B2B SaaS')"
        )

    # Optional but recommended for full scoring
    if record.launch_or_cutoff_date is None:
        missing_optional.append("launch_or_cutoff_date")
        questions.append(
            "When did this product launch publicly? Please provide "
            "an ISO date (YYYY-MM-DD)."
        )
    if not record.pre_launch_sources_available:
        missing_optional.append("pre_launch_sources_available")
        questions.append(
            "What pre-launch sources are available (e.g. "
            "'product_hunt_launch_page_text', 'show_hn_thread_text', "
            "'founder_announcement_post')?"
        )
    if not record.outcome_sources_available:
        missing_optional.append("outcome_sources_available")
        questions.append(
            "What outcome sources are available for measuring "
            "real-world reactions (e.g. 'product_hunt_comments', "
            "'g2_or_capterra_review_text', 'reddit_reaction_threads')?"
        )
    if record.estimated_observation_count is None:
        missing_optional.append("estimated_observation_count")
        questions.append(
            "Approximately how many real-world reactions did this "
            "product receive? Choose: '<30', '30-100', '100-500', "
            "'500+', or 'unknown'."
        )
    if record.contamination_risk is None:
        missing_optional.append("contamination_risk")
        questions.append(
            "Is this product (or close proxies) already used to "
            "develop Assembly's evidence/signal layers? Choose: "
            "'none', 'low', 'medium', 'high'."
        )
    if record.model_prior_risk is None:
        missing_optional.append("model_prior_risk")
        questions.append(
            "How likely is it that a pretrained LLM already 'knows' "
            "this product's outcome? Choose: 'low', 'medium', 'high'."
        )
    if record.outcome_quality is None:
        missing_optional.append("outcome_quality")
        questions.append(
            "How clearly do the outcome sources let us label "
            "real-world reactions into buyer / receptive / "
            "uncertain / skeptical? Choose: 'unknown', 'weak', "
            "'medium', 'strong'."
        )
    if record.cutoff_clarity is None:
        missing_optional.append("cutoff_clarity")
        questions.append(
            "How clearly defined is the launch cutoff date? Choose: "
            "'unclear', 'approximate', 'clear'."
        )
    if record.category_fit is None:
        missing_optional.append("category_fit")
        questions.append(
            "How well does this product match a category Assembly "
            "already has evidence for? Choose: 'none', 'weak', "
            "'medium', 'strong'."
        )
    if record.source_access_risk is None:
        missing_optional.append("source_access_risk")
        questions.append(
            "How will outcome data be obtained? Choose: 'forbidden', "
            "'scraping_required', 'operator_supply', "
            "'public_no_scrape', 'open_data'."
        )

    # Surface every parse warning as part of the validation output
    warnings.extend(
        p for p in record.parse_warnings if p not in warnings
    )

    is_valid = len(errors) == 0
    return IntakeValidationResult(
        record=record,
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        missing_required=missing_required,
        missing_optional=missing_optional,
        operator_followup_questions=questions,
    )


# ---------------------------------------------------------------------------
# 3. convert
# ---------------------------------------------------------------------------


def convert_metadata_to_case_candidate(
    record: IntakeRecord,
) -> CaseCandidate:
    """Build a :class:`CaseCandidate` from a parsed record.

    Missing closed-vocab fields fall back to the safest default —
    ``"unknown"`` / ``"unclear"`` / ``"medium"`` etc. — so the
    Phase 12A.3 scoring framework computes a recommendation of
    ``"unverified"`` rather than silently treating the candidate as
    well-formed.

    A missing ``candidate_id`` falls back to
    ``"unspecified_candidate"`` and a missing ``product_name`` /
    ``category`` falls back to ``"unspecified"`` so the structural
    Phase 12A.3 contamination check still runs (it cares about the
    name's content, not its presence).
    """
    return CaseCandidate(
        candidate_id=record.candidate_id or "unspecified_candidate",
        product_name=record.product_name or "unspecified",
        category=record.category or "unspecified",
        launch_or_cutoff_date=record.launch_or_cutoff_date,
        pre_launch_sources_available=list(
            record.pre_launch_sources_available
        ),
        outcome_sources_available=list(record.outcome_sources_available),
        estimated_observation_count=(
            record.estimated_observation_count or "unknown"
        ),
        contamination_risk=record.contamination_risk or "low",
        model_prior_risk=record.model_prior_risk or "medium",
        outcome_quality=record.outcome_quality or "unknown",
        cutoff_clarity=record.cutoff_clarity or "unclear",
        category_fit=record.category_fit or "weak",
        source_access_risk=record.source_access_risk or "operator_supply",
        notes=record.notes,
        operator_recommendation=record.operator_recommendation,
    )


# ---------------------------------------------------------------------------
# 4. score (single + batch)
# ---------------------------------------------------------------------------


def _score_one(
    record: IntakeRecord,
    *,
    validation: IntakeValidationResult,
) -> ScoredOperatorCandidate:
    candidate = convert_metadata_to_case_candidate(record)
    sc = candidate_scorecard(candidate)
    rec = evaluate_candidate_suitability(candidate)
    return ScoredOperatorCandidate(
        candidate_id=candidate.candidate_id,
        product_name=candidate.product_name,
        category=candidate.category,
        candidate=candidate,
        recommendation=rec,
        calibration_value=sc["calibration_value"],
        raw_total=sc["raw_total"],
        risk_flags=list(sc["risk_flags"]),
        missing_required_fields=list(validation.missing_required),
        missing_optional_fields=list(validation.missing_optional),
        operator_followup_questions=list(
            validation.operator_followup_questions
        ),
        parse_warnings=list(record.parse_warnings),
        validation_errors=list(validation.errors),
    )


def score_operator_candidates(
    payloads: list[dict[str, Any]],
) -> list[ScoredOperatorCandidate]:
    """End-to-end: parse → validate → convert → score → return.

    The returned list preserves operator input order. Use
    :func:`summarize_operator_candidate_batch` for the ranked rollup.
    """
    out: list[ScoredOperatorCandidate] = []
    for p in payloads:
        rec = parse_operator_candidate_metadata(p)
        val = validate_operator_candidate_metadata(rec)
        out.append(_score_one(rec, validation=val))
    return out


# ---------------------------------------------------------------------------
# 5. batch summary
# ---------------------------------------------------------------------------


def summarize_operator_candidate_batch(
    scored: list[ScoredOperatorCandidate],
) -> dict:
    """Aggregate a scored batch into a deterministic rollup.

    Sort by ``calibration_value`` desc, then ``candidate_id`` asc.
    Returns:
      {
        "batch_size":                int,
        "by_recommendation":         {"accept": N, "maybe": N, ...},
        "with_validation_errors":    int,
        "with_missing_required":     int,
        "with_followup_questions":   int,
        "ranked":                    [ {candidate_id, product_name,
                                        recommendation, calibration_value,
                                        risk_flags, followup_questions} ],
      }
    """
    by_rec: dict[str, int] = {
        "accept": 0, "maybe": 0, "reject": 0, "unverified": 0,
    }
    with_errors = 0
    with_missing_required = 0
    with_questions = 0
    for s in scored:
        by_rec[s.recommendation] = by_rec.get(s.recommendation, 0) + 1
        if s.validation_errors:
            with_errors += 1
        if s.missing_required_fields:
            with_missing_required += 1
        if s.operator_followup_questions:
            with_questions += 1
    ranked = sorted(
        scored,
        key=lambda s: (-s.calibration_value, s.candidate_id),
    )
    return {
        "batch_size": len(scored),
        "by_recommendation": by_rec,
        "with_validation_errors": with_errors,
        "with_missing_required": with_missing_required,
        "with_followup_questions": with_questions,
        "ranked": [
            {
                "candidate_id": s.candidate_id,
                "product_name": s.product_name,
                "category": s.category,
                "recommendation": s.recommendation,
                "calibration_value": s.calibration_value,
                "raw_total": s.raw_total,
                "risk_flags": list(s.risk_flags),
                "missing_required_fields": list(s.missing_required_fields),
                "missing_optional_fields": list(s.missing_optional_fields),
                "operator_followup_questions": list(
                    s.operator_followup_questions
                ),
            }
            for s in ranked
        ],
    }
