"""Phase 8.2D — validators for the behavioral science mechanism library.

Six structural validators above the DB CHECK constraints:

  1. validate_research_source_payload          — source_type ∈ closed enum;
                                                 year sane; title non-empty.
  2. validate_mechanism_payload                — category/status ∈ closed
                                                 enums; default_strength ∈
                                                 [0, 1]; required text fields
                                                 non-empty.
  3. validate_evidence_link_payload            — support_type ∈ closed enum;
                                                 excerpt non-empty.
  4. validate_persuasion_strategy_payload      — strategy_name ∈ closed
                                                 catalog.
  5. validate_belief_rule_payload              — relation_type ∈ closed enum;
                                                 strength ∈ {none,weak,
                                                 moderate} (NEVER 'strong');
                                                 topic_a ≠ topic_b.
  6. validate_applicability_rule_payload       — mechanism_id present;
                                                 domain_label ∈ closed
                                                 catalog; applies_when is a
                                                 dict.

A separate function `validate_priors_do_not_outrank_evidence` enforces the
core product principle: mechanism priors NEVER override source-bound
trait evidence. This is called from the initializer before assembling
the persona mechanism profile.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from assembly.pipeline.behavioral_science.constants import (
    APPLICABILITY_DOMAINS,
    EVIDENCE_SUPPORT_TYPES,
    FORBIDDEN_INFERENCE_STRENGTHS,
    INFERENCE_STRENGTHS,
    MECHANISM_CATEGORIES,
    MECHANISM_STATUSES,
    PERSUASION_STRATEGIES,
    RELATION_TYPES,
    SOURCE_TYPES,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationViolation:
    rule_id: str
    field_path: str
    suggestion: str
    matched_phrase: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    violations: tuple[ValidationViolation, ...]


def _ok() -> ValidationResult:
    return ValidationResult(passed=True, violations=())


def _bad(violations: Sequence[ValidationViolation]) -> ValidationResult:
    return ValidationResult(passed=False, violations=tuple(violations))


def _require_non_empty_string(
    payload: dict[str, Any], key: str, rule_prefix: str,
    violations: list[ValidationViolation],
) -> None:
    v = payload.get(key)
    if not isinstance(v, str) or not v.strip():
        violations.append(
            ValidationViolation(
                rule_id=f"{rule_prefix}.{key}_required",
                field_path=key,
                suggestion=f"`{key}` is required and must be a non-empty string.",
            )
        )


# ---------------------------------------------------------------------------
# 1) research_sources
# ---------------------------------------------------------------------------


def validate_research_source_payload(payload: dict[str, Any]) -> ValidationResult:
    violations: list[ValidationViolation] = []
    _require_non_empty_string(payload, "title", "research_source", violations)
    source_type = payload.get("source_type")
    if source_type not in SOURCE_TYPES:
        violations.append(
            ValidationViolation(
                rule_id="research_source.source_type_unknown",
                field_path="source_type",
                matched_phrase=str(source_type),
                suggestion=(
                    f"`source_type` must be one of {list(SOURCE_TYPES)}."
                ),
            )
        )
    year = payload.get("year")
    if year is not None:
        if not isinstance(year, int) or year < 1900 or year > 2100:
            violations.append(
                ValidationViolation(
                    rule_id="research_source.year_out_of_range",
                    field_path="year",
                    matched_phrase=repr(year),
                    suggestion="`year` must be an integer in [1900, 2100].",
                )
            )
    return _ok() if not violations else _bad(violations)


# ---------------------------------------------------------------------------
# 2) behavioral_mechanisms
# ---------------------------------------------------------------------------


def validate_mechanism_payload(payload: dict[str, Any]) -> ValidationResult:
    violations: list[ValidationViolation] = []
    for k in ("name", "description", "when_to_apply", "when_not_to_apply"):
        _require_non_empty_string(payload, k, "mechanism", violations)

    category = payload.get("category")
    if category not in MECHANISM_CATEGORIES:
        violations.append(
            ValidationViolation(
                rule_id="mechanism.category_unknown",
                field_path="category",
                matched_phrase=str(category),
                suggestion=(
                    f"`category` must be one of {list(MECHANISM_CATEGORIES)}."
                ),
            )
        )

    status = payload.get("status", "active")
    if status not in MECHANISM_STATUSES:
        violations.append(
            ValidationViolation(
                rule_id="mechanism.status_unknown",
                field_path="status",
                matched_phrase=str(status),
                suggestion=(
                    f"`status` must be one of {list(MECHANISM_STATUSES)}."
                ),
            )
        )

    strength_raw = payload.get("default_strength", 0.5)
    try:
        strength = float(strength_raw)
    except (TypeError, ValueError):
        violations.append(
            ValidationViolation(
                rule_id="mechanism.default_strength_not_numeric",
                field_path="default_strength",
                matched_phrase=repr(strength_raw),
                suggestion="`default_strength` must be a number in [0, 1].",
            )
        )
        strength = 0.0
    if not (0.0 <= strength <= 1.0):
        violations.append(
            ValidationViolation(
                rule_id="mechanism.default_strength_out_of_range",
                field_path="default_strength",
                matched_phrase=repr(strength_raw),
                suggestion="`default_strength` must be in [0, 1].",
            )
        )

    return _ok() if not violations else _bad(violations)


# ---------------------------------------------------------------------------
# 3) mechanism_evidence_links
# ---------------------------------------------------------------------------


def validate_evidence_link_payload(payload: dict[str, Any]) -> ValidationResult:
    violations: list[ValidationViolation] = []
    if not payload.get("mechanism_id"):
        violations.append(
            ValidationViolation(
                rule_id="evidence_link.mechanism_id_required",
                field_path="mechanism_id",
                suggestion="`mechanism_id` is required.",
            )
        )
    if not payload.get("research_source_id"):
        violations.append(
            ValidationViolation(
                rule_id="evidence_link.research_source_id_required",
                field_path="research_source_id",
                suggestion="`research_source_id` is required.",
            )
        )
    support_type = payload.get("support_type")
    if support_type not in EVIDENCE_SUPPORT_TYPES:
        violations.append(
            ValidationViolation(
                rule_id="evidence_link.support_type_unknown",
                field_path="support_type",
                matched_phrase=str(support_type),
                suggestion=(
                    f"`support_type` must be one of {list(EVIDENCE_SUPPORT_TYPES)}."
                ),
            )
        )
    _require_non_empty_string(
        payload, "excerpt_or_summary", "evidence_link", violations,
    )
    return _ok() if not violations else _bad(violations)


# ---------------------------------------------------------------------------
# 4) persuasion_strategy_taxonomy
# ---------------------------------------------------------------------------


def validate_persuasion_strategy_payload(payload: dict[str, Any]) -> ValidationResult:
    violations: list[ValidationViolation] = []
    name = payload.get("strategy_name")
    if name not in PERSUASION_STRATEGIES:
        violations.append(
            ValidationViolation(
                rule_id="persuasion_strategy.name_unknown",
                field_path="strategy_name",
                matched_phrase=str(name),
                suggestion=(
                    f"`strategy_name` must be one of {list(PERSUASION_STRATEGIES)}."
                ),
            )
        )
    _require_non_empty_string(
        payload, "description", "persuasion_strategy", violations,
    )
    if not payload.get("research_source_id"):
        violations.append(
            ValidationViolation(
                rule_id="persuasion_strategy.research_source_id_required",
                field_path="research_source_id",
                suggestion="`research_source_id` is required.",
            )
        )
    return _ok() if not violations else _bad(violations)


# ---------------------------------------------------------------------------
# 5) belief_network_rules
# ---------------------------------------------------------------------------


def validate_belief_rule_payload(payload: dict[str, Any]) -> ValidationResult:
    violations: list[ValidationViolation] = []
    _require_non_empty_string(payload, "topic_a", "belief_rule", violations)
    _require_non_empty_string(payload, "topic_b", "belief_rule", violations)
    if (
        payload.get("topic_a")
        and payload.get("topic_b")
        and payload["topic_a"] == payload["topic_b"]
    ):
        violations.append(
            ValidationViolation(
                rule_id="belief_rule.self_pair",
                field_path="topic_b",
                matched_phrase=str(payload.get("topic_b")),
                suggestion="`topic_a` and `topic_b` must differ.",
            )
        )

    relation_type = payload.get("relation_type")
    if relation_type not in RELATION_TYPES:
        violations.append(
            ValidationViolation(
                rule_id="belief_rule.relation_type_unknown",
                field_path="relation_type",
                matched_phrase=str(relation_type),
                suggestion=(
                    f"`relation_type` must be one of {list(RELATION_TYPES)}."
                ),
            )
        )

    strength = payload.get("allowed_inference_strength")
    if strength in FORBIDDEN_INFERENCE_STRENGTHS:
        violations.append(
            ValidationViolation(
                rule_id="belief_rule.strength_strong_forbidden",
                field_path="allowed_inference_strength",
                matched_phrase=str(strength),
                suggestion=(
                    "`allowed_inference_strength='strong'` is structurally "
                    "FORBIDDEN. The strongest spillover allowed is 'moderate'. "
                    "Mechanism priors NEVER outrank source evidence."
                ),
            )
        )
    elif strength not in INFERENCE_STRENGTHS:
        violations.append(
            ValidationViolation(
                rule_id="belief_rule.strength_unknown",
                field_path="allowed_inference_strength",
                matched_phrase=str(strength),
                suggestion=(
                    f"`allowed_inference_strength` must be one of "
                    f"{list(INFERENCE_STRENGTHS)}."
                ),
            )
        )

    if not payload.get("research_source_id"):
        violations.append(
            ValidationViolation(
                rule_id="belief_rule.research_source_id_required",
                field_path="research_source_id",
                suggestion="`research_source_id` is required.",
            )
        )

    return _ok() if not violations else _bad(violations)


# ---------------------------------------------------------------------------
# 6) mechanism_applicability_rules
# ---------------------------------------------------------------------------


def validate_applicability_rule_payload(payload: dict[str, Any]) -> ValidationResult:
    violations: list[ValidationViolation] = []
    if not payload.get("mechanism_id"):
        violations.append(
            ValidationViolation(
                rule_id="applicability_rule.mechanism_id_required",
                field_path="mechanism_id",
                suggestion="`mechanism_id` is required.",
            )
        )
    domain = payload.get("domain_label")
    if domain not in APPLICABILITY_DOMAINS:
        violations.append(
            ValidationViolation(
                rule_id="applicability_rule.domain_label_unknown",
                field_path="domain_label",
                matched_phrase=str(domain),
                suggestion=(
                    f"`domain_label` must be one of {list(APPLICABILITY_DOMAINS)}."
                ),
            )
        )
    applies_when = payload.get("applies_when", {})
    if not isinstance(applies_when, dict):
        violations.append(
            ValidationViolation(
                rule_id="applicability_rule.applies_when_not_dict",
                field_path="applies_when",
                suggestion="`applies_when` must be a JSON object.",
            )
        )
    return _ok() if not violations else _bad(violations)


# ---------------------------------------------------------------------------
# Cross-cutting: priors must never outrank source-bound evidence.
# ---------------------------------------------------------------------------


def validate_priors_do_not_outrank_evidence(
    *,
    source_supported_fields: set[str],
    mechanism_overrides: dict[str, Any],
) -> ValidationResult:
    """Refuse if any mechanism prior attempts to override a field already
    backed by source evidence (support_level ∈ {direct, inferred}).

    Inputs are keyed by `field_name` (the persona-trait closed enum).
    Mechanism priors live alongside but cannot replace source-backed
    fields. Returning a violation halts initialization for that field.
    """
    violations: list[ValidationViolation] = []
    for field_name in mechanism_overrides:
        if field_name in source_supported_fields:
            violations.append(
                ValidationViolation(
                    rule_id="initializer.prior_outranks_evidence",
                    field_path=field_name,
                    suggestion=(
                        f"Mechanism prior cannot override field "
                        f"{field_name!r} — that field is already backed by "
                        "source evidence. Source evidence ALWAYS outranks "
                        "mechanism priors."
                    ),
                )
            )
    return _ok() if not violations else _bad(violations)
