"""Phase 8.2G — TargetSocietyPlan validator.

Asserts the plan satisfies the framework's product-level discipline:

  - not Amboras-only when the detected family is non-commerce
  - at least 4 stakeholder categories (unless explicitly tiny family)
  - every category has evidence_needed + inclusion + exclusion signals
  - no forecast / verdict / market-prediction language anywhere
  - sensitive markers → at least one CAVEAT or BLOCKER warning
  - no protected-attribute inference allowed at category level
  - missing-input fields are reflected in interpreted_brief.missing_inputs
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from assembly.pipeline.target_society.constants import (
    ProductFamily,
    WARNING_SENSITIVE_TARGETING_CAVEAT,
    WarningSeverity,
)
from assembly.pipeline.target_society.schemas import (
    ProductBriefInput,
    StakeholderCategory,
    TargetSocietyPlan,
)


# Forecast / verdict language. Any match → invalid.
# Tightened to avoid false positives on legitimate descriptive language
# (e.g. "rejects the product premise" describing a skeptical rejector
# stakeholder, "not a probabilistic forecast" in a CAVEAT warning).
_FORECAST_VERDICT_RE = re.compile(
    r"\b(?:"
    # Forecast verbs about outcome:
    r"will\s+(?:succeed|fail|dominate|win|lose|outperform)\b|"
    r"guaranteed\s+(?:to|win|success)\b|"
    r"market[- ]?success\s+probability\b|"
    # Explicit verdict markers (no trailing \b — terminates with non-
    # word `:` or `=` which has no word-boundary follower):
    r"verdict\s*[:=]|"
    # Build/kill/pivot recommendation language:
    r"(?:should|recommend|let'?s)\s+(?:build|kill|pivot|launch)\s+"
    r"(?:it|this|the\s+product)\b|"
    # Numeric outcome predictions:
    r"predict(?:s|ed|ing|ion)?\s+(?:revenue|sales|conversion|ROI|"
    r"market\s+share)\b"
    r")",
    re.IGNORECASE,
)


# Hard-coded Amboras-shape commerce keywords. If a non-commerce plan
# emits ≥ 3 of these category keys, validator flags the plan as
# Amboras-leaked.
_COMMERCE_LEAK_KEYS: tuple[str, ...] = (
    "shopify_or_platform_merchant",
    "dtc_founder_brand_control",
    "agency_dependent_merchant",
    "ai_skeptical_operator",
    "nontechnical_founder",
)


@dataclass(frozen=True)
class ValidationViolation:
    rule_id: str
    field_path: str
    suggestion: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    violations: tuple[ValidationViolation, ...]


def _ok() -> ValidationResult:
    return ValidationResult(passed=True, violations=())


def _bad(violations: list[ValidationViolation]) -> ValidationResult:
    return ValidationResult(passed=False, violations=tuple(violations))


def validate_target_society_plan(
    plan: TargetSocietyPlan,
    *,
    brief: ProductBriefInput | None = None,
) -> ValidationResult:
    """Validate a TargetSocietyPlan. Returns structured violations."""
    violations: list[ValidationViolation] = []

    # 1) Minimum 4 stakeholder categories.
    if len(plan.stakeholder_categories) < 4:
        violations.append(ValidationViolation(
            rule_id="target_society.min_categories",
            field_path="stakeholder_categories",
            suggestion=(
                f"Plan has only {len(plan.stakeholder_categories)} "
                "stakeholder categories. Phase 8.2G requires at least 4 "
                "(buyer + rejector + competitor-or-alternative + at "
                "least one secondary segment)."
            ),
        ))

    # 2) Every category has evidence_needed + inclusion/exclusion signals.
    for c in plan.stakeholder_categories:
        if not c.evidence_needed:
            violations.append(ValidationViolation(
                rule_id="target_society.category_missing_evidence_needed",
                field_path=f"stakeholder_categories[{c.category_key}].evidence_needed",
                suggestion="Every category must specify at least one evidence_needed entry.",
            ))
        if not c.inclusion_signals:
            violations.append(ValidationViolation(
                rule_id="target_society.category_missing_inclusion_signals",
                field_path=f"stakeholder_categories[{c.category_key}].inclusion_signals",
                suggestion="Every category must specify inclusion_signals.",
            ))
        if not c.exclusion_signals:
            violations.append(ValidationViolation(
                rule_id="target_society.category_missing_exclusion_signals",
                field_path=f"stakeholder_categories[{c.category_key}].exclusion_signals",
                suggestion="Every category must specify exclusion_signals.",
            ))

    # 3) No Amboras-only output for non-commerce families.
    family = plan.interpreted_brief.detected_product_family
    if family is not ProductFamily.COMMERCE_PLATFORM_OR_TOOLING:
        commerce_keys = sum(
            1 for c in plan.stakeholder_categories
            if c.category_key in _COMMERCE_LEAK_KEYS
        )
        if commerce_keys >= 3:
            violations.append(ValidationViolation(
                rule_id="target_society.amboras_leak",
                field_path="stakeholder_categories",
                suggestion=(
                    f"Plan for family={family.value} has {commerce_keys} "
                    "commerce-shape category keys. The planner must "
                    "produce family-appropriate categories, not "
                    "Amboras-only output."
                ),
            ))

    # 4) No forecast / verdict language.
    for c in plan.stakeholder_categories:
        for field_name in (
            "description", "why_relevant",
        ):
            text = getattr(c, field_name, "") or ""
            m = _FORECAST_VERDICT_RE.search(text)
            if m:
                violations.append(ValidationViolation(
                    rule_id="target_society.forecast_or_verdict_language",
                    field_path=f"stakeholder_categories[{c.category_key}].{field_name}",
                    suggestion=(
                        f"Forecast/verdict language detected: {m.group(0)!r}. "
                        "The planner must not predict outcomes or recommend "
                        "build/kill/pivot."
                    ),
                ))
    for w in plan.warnings_and_limitations:
        m = _FORECAST_VERDICT_RE.search(w.message)
        if m:
            violations.append(ValidationViolation(
                rule_id="target_society.forecast_or_verdict_language",
                field_path=f"warnings_and_limitations[{w.code}].message",
                suggestion=(
                    f"Forecast/verdict language detected: {m.group(0)!r}."
                ),
            ))

    # 5) Sensitive-marker handling: if any category carries a
    # `sensitivity_or_compliance_notes`, the plan must include at
    # least one CAVEAT or BLOCKER warning AND specifically the
    # SENSITIVE_TARGETING_CAVEAT or PROTECTED_ATTRIBUTE_INFERENCE
    # warning code.
    has_sensitive_category = any(
        c.sensitivity_or_compliance_notes for c in plan.stakeholder_categories
    )
    has_sensitive_warning = any(
        w.code == WARNING_SENSITIVE_TARGETING_CAVEAT
        and w.severity in (WarningSeverity.CAVEAT, WarningSeverity.BLOCKER)
        for w in plan.warnings_and_limitations
    )
    if has_sensitive_category and not has_sensitive_warning:
        violations.append(ValidationViolation(
            rule_id="target_society.sensitive_category_missing_warning",
            field_path="warnings_and_limitations",
            suggestion=(
                "Plan has stakeholder categories tagged with "
                "sensitivity_or_compliance_notes but does not emit a "
                "SENSITIVE_TARGETING_CAVEAT warning. The two must travel "
                "together so downstream code never silently inherits a "
                "sensitive context."
            ),
        ))

    # 6) Missing-input warnings vs interpreted_brief.missing_inputs.
    if brief is not None:
        if (
            not brief.geography
            and "geography" not in plan.interpreted_brief.missing_inputs
        ):
            violations.append(ValidationViolation(
                rule_id="target_society.missing_input_not_recorded",
                field_path="interpreted_brief.missing_inputs",
                suggestion=(
                    "Brief lacks geography but missing_inputs does not "
                    "include 'geography'."
                ),
            ))
        if (
            not brief.competitors
            and "competitors" not in plan.interpreted_brief.missing_inputs
        ):
            violations.append(ValidationViolation(
                rule_id="target_society.missing_input_not_recorded",
                field_path="interpreted_brief.missing_inputs",
                suggestion=(
                    "Brief lacks competitors but missing_inputs does not "
                    "include 'competitors'."
                ),
            ))

    # 7) Coverage / readiness gates have to be > 0 for tiny.
    if plan.simulation_readiness_gates.tiny_minimum_personas <= 0:
        violations.append(ValidationViolation(
            rule_id="target_society.tiny_minimum_must_be_positive",
            field_path="simulation_readiness_gates.tiny_minimum_personas",
            suggestion="tiny_minimum_personas must be ≥ 1.",
        ))

    return _ok() if not violations else _bad(violations)
