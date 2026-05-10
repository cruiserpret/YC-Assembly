"""Phase 8.2A — persona validators.

Two layers of discipline above the DB CHECK constraints:

  1. Stricter than the migration: e.g. inferred-confidence ≥ 0.5 (the DB
     allows > 0; the validator tightens).
  2. Sensitive-attribute screening on `value` and `rationale` BEFORE
     persisting.

The validators return structured `ValidationViolation` objects with
`rule_id`, `field_path`, `matched_phrase` (when relevant), and a
`suggestion`. Callers either repair (Phase 8.2B+) or refuse (always
correct in 8.2A — there's nothing yet that calls these). Callers MUST NOT
silently coerce invalid data.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from assembly.pipeline.persona.constants import (
    COVERAGE_LABELS,
    INFERRED_MIN_CONFIDENCE,
    PERSONA_FIELD_NAMES,
    SOURCE_BACKED_ONLY_FIELDS,
    SUPPORT_DIRECT,
    SUPPORT_INFERRED,
    SUPPORT_LEVELS,
    SUPPORT_MISSING,
    SUPPORT_UNKNOWN,
)
from assembly.pipeline.persona.sensitive_filter import (
    SensitiveAttributeRejected,
    scan_sensitive_attributes,
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


# ---------------------------------------------------------------------------
# Persona trait payload validation
# ---------------------------------------------------------------------------


def validate_persona_trait_payload(payload: dict[str, Any]) -> ValidationResult:
    """Validate a persona-trait dict before insert. Returns structured
    violations; callers MUST refuse on `passed=False`.

    Required keys: `field_name`, `support_level`. Optional: `value`,
    `source_ids`, `confidence`, `rationale`. Anything else is rejected
    (forward-compat protection — the schema only accepts known keys).
    """
    violations: list[ValidationViolation] = []
    allowed_keys = {
        "field_name", "support_level", "value", "source_ids",
        "confidence", "rationale",
    }
    extra = set(payload.keys()) - allowed_keys
    if extra:
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.extra_keys",
                field_path="<root>",
                suggestion=(
                    f"Extra keys not allowed: {sorted(extra)}. "
                    f"Allowed: {sorted(allowed_keys)}."
                ),
            )
        )

    field_name = payload.get("field_name")
    if field_name is None:
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.field_name_required",
                field_path="field_name",
                suggestion="`field_name` is required.",
            )
        )
    elif field_name not in PERSONA_FIELD_NAMES:
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.field_name_unknown",
                field_path="field_name",
                matched_phrase=str(field_name),
                suggestion=(
                    f"Unknown field_name {field_name!r}. Allowed: "
                    f"{list(PERSONA_FIELD_NAMES)}."
                ),
            )
        )

    support_level = payload.get("support_level")
    if support_level is None:
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.support_level_required",
                field_path="support_level",
                suggestion="`support_level` is required.",
            )
        )
    elif support_level not in SUPPORT_LEVELS:
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.support_level_unknown",
                field_path="support_level",
                matched_phrase=str(support_level),
                suggestion=(
                    f"Unknown support_level {support_level!r}. Allowed: "
                    f"{list(SUPPORT_LEVELS)}."
                ),
            )
        )

    value = payload.get("value")
    source_ids = payload.get("source_ids") or []
    confidence_raw = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.confidence_not_numeric",
                field_path="confidence",
                matched_phrase=repr(confidence_raw),
                suggestion="`confidence` must be a number in [0, 1].",
            )
        )
        confidence = 0.0

    if not (0.0 <= confidence <= 1.0):
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.confidence_out_of_range",
                field_path="confidence",
                matched_phrase=repr(confidence),
                suggestion="`confidence` must be in [0, 1].",
            )
        )

    # Coerce + validate source_ids type (UUID-like).
    if not isinstance(source_ids, (list, tuple)):
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.source_ids_not_list",
                field_path="source_ids",
                suggestion="`source_ids` must be a list of UUIDs.",
            )
        )
        source_ids = []
    coerced_source_ids: list[UUID] = []
    for sid in source_ids:
        if isinstance(sid, UUID):
            coerced_source_ids.append(sid)
            continue
        try:
            coerced_source_ids.append(UUID(str(sid)))
        except (ValueError, TypeError):
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.source_id_not_uuid",
                    field_path="source_ids",
                    matched_phrase=repr(sid),
                    suggestion="Each source_ids entry must be a UUID.",
                )
            )

    # Combination rules.
    if support_level in (SUPPORT_DIRECT, SUPPORT_INFERRED):
        if not coerced_source_ids:
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.support_requires_source_ids",
                    field_path="source_ids",
                    suggestion=(
                        f"support_level={support_level!r} requires at least "
                        "one bound source_id."
                    ),
                )
            )
        if value is None or (isinstance(value, str) and not value.strip()):
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.support_requires_value",
                    field_path="value",
                    suggestion=(
                        f"support_level={support_level!r} requires a non-empty value."
                    ),
                )
            )
        if support_level == SUPPORT_DIRECT and confidence <= 0:
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.direct_requires_confidence_gt_0",
                    field_path="confidence",
                    suggestion="support_level='direct' requires confidence > 0.",
                )
            )
        if (
            support_level == SUPPORT_INFERRED
            and confidence < INFERRED_MIN_CONFIDENCE
        ):
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.inferred_confidence_too_low",
                    field_path="confidence",
                    matched_phrase=repr(confidence),
                    suggestion=(
                        "support_level='inferred' requires confidence >= "
                        f"{INFERRED_MIN_CONFIDENCE}. If the source is too "
                        "weak to support an inference, mark the trait "
                        "'unknown' instead."
                    ),
                )
            )

    if support_level == SUPPORT_UNKNOWN:
        if value not in (None, ""):
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.unknown_forbids_value",
                    field_path="value",
                    suggestion="support_level='unknown' must have value=null.",
                )
            )
        if coerced_source_ids:
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.unknown_forbids_source_ids",
                    field_path="source_ids",
                    suggestion="support_level='unknown' must have empty source_ids.",
                )
            )

    if support_level == SUPPORT_MISSING and value not in (None, ""):
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.missing_forbids_value",
                field_path="value",
                suggestion="support_level='missing' must have value=null.",
            )
        )

    # Source-backed-only fields: e.g. influence_signals MUST be either
    # 'direct' (real engagement metric counted from source metadata) or
    # 'unknown'. It can never be 'inferred' from prose — engagement is a
    # measured quantity, not an inferred one.
    if (
        field_name in SOURCE_BACKED_ONLY_FIELDS
        and support_level == SUPPORT_INFERRED
    ):
        violations.append(
            ValidationViolation(
                rule_id="persona.trait.field_must_be_source_backed",
                field_path="support_level",
                suggestion=(
                    f"field_name={field_name!r} cannot be 'inferred'. "
                    "Mark it 'direct' (when source engagement metrics are "
                    "available) or 'unknown'."
                ),
            )
        )

    # Geography honesty: even when source-backed, only broad regions allowed.
    if field_name == "geography_broad" and value:
        if _looks_too_precise_geography(str(value)):
            violations.append(
                ValidationViolation(
                    rule_id="persona.trait.geography_too_precise",
                    field_path="value",
                    matched_phrase=str(value)[:80],
                    suggestion=(
                        "geography_broad must be a broad region only. "
                        "Reject ZIP, street, address-style, or city+state-pair "
                        "values. Use tags like 'us_california', 'eu_western', "
                        "'india_metro'."
                    ),
                )
            )

    # Sensitive-attribute screening on value + rationale.
    for blob_path in ("value", "rationale"):
        text = payload.get(blob_path)
        if not text or not isinstance(text, str):
            continue
        hits = scan_sensitive_attributes(text)
        if hits:
            for h in hits:
                violations.append(
                    ValidationViolation(
                        rule_id=f"persona.trait.sensitive.{h.category.value}",
                        field_path=blob_path,
                        matched_phrase=h.matched,
                        suggestion=(
                            f"Sensitive attribute ({h.category.value}) detected "
                            "in persona trait. Forbidden by Phase 8.2A privacy "
                            "rules. Drop the value or mark the trait 'unknown'."
                        ),
                    )
                )

    return _ok() if not violations else _bad(violations)


_GEOGRAPHY_TOO_PRECISE_PATTERNS = (
    # Street-style: "123 Main St"
    __import__("re").compile(
        r"\b\d{1,6}\s+\w+\s+(?:St\.?|Street|Ave\.?|Avenue|Blvd\.?|Road|Rd\.?|"
        r"Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Place|Pl\.?|Way|Plaza)\b",
        __import__("re").IGNORECASE,
    ),
    # ZIP-like
    __import__("re").compile(r"\b\d{5}(?:-\d{4})?\b"),
    # Apartment / unit / suite — too granular
    __import__("re").compile(
        r"\b(?:apt\.?|apartment|suite|unit)\s+\#?\d", __import__("re").IGNORECASE,
    ),
)


def _looks_too_precise_geography(value: str) -> bool:
    """Reject any geography_broad value that hints at precision below
    safe broad-region level. Phase 8.2A explicitly forbids ZIP, street,
    apartment, and similar narrow markers."""
    for pat in _GEOGRAPHY_TOO_PRECISE_PATTERNS:
        if pat.search(value):
            return True
    return False


def assert_persona_trait_payload(payload: dict[str, Any]) -> None:
    """Like `validate_persona_trait_payload` but raises on failure.
    Use at insert time when there's no good in-band repair path."""
    result = validate_persona_trait_payload(payload)
    if not result.passed:
        raise SensitiveAttributeRejected(violations=[]) if any(
            v.rule_id.startswith("persona.trait.sensitive.")
            for v in result.violations
        ) else _ValidationFailed(result.violations)


class _ValidationFailed(Exception):
    def __init__(self, violations: tuple[ValidationViolation, ...]) -> None:
        self.violations = violations
        super().__init__(
            f"persona_trait validation failed: "
            + "; ".join(f"{v.rule_id}@{v.field_path}" for v in violations[:5])
        )


# ---------------------------------------------------------------------------
# Persona-trait ORM validation
# ---------------------------------------------------------------------------


def validate_persona_trait_orm(trait) -> ValidationResult:
    """Validate a `models.persona.PersonaTrait` instance pre-flush. Reuses
    the payload validator over the ORM object's fields."""
    payload = {
        "field_name": getattr(trait, "field_name", None),
        "support_level": getattr(trait, "support_level", None),
        "value": getattr(trait, "value", None),
        "source_ids": list(getattr(trait, "source_ids", []) or []),
        "confidence": float(getattr(trait, "confidence", 0) or 0),
        "rationale": getattr(trait, "rationale", None),
    }
    return validate_persona_trait_payload(payload)


# ---------------------------------------------------------------------------
# User-facing persona safety
# ---------------------------------------------------------------------------


_FORBIDDEN_USER_FACING_KEYS = (
    "raw_handle",
    "handle",
    "username",
    "email",
    "phone",
    "real_name",
    "full_name",
    "given_name",
    "family_name",
    "first_name",
    "last_name",
    "photo",
    "photo_url",
    "avatar_url",
    "profile_url",
    "address",
    "street_address",
    "zip",
    "zip_code",
    "user_handle_hash",  # the hash is internal-only, never user-visible
    "ssn",
    "dob",
    "birthdate",
)


def validate_persona_record_safe_for_user(payload: dict[str, Any]) -> ValidationResult:
    """Assert a persona payload destined for a user-facing API response
    contains no real-identity columns. Phase 8.2A doesn't have a
    user-facing persona endpoint yet, but this validator will gate the
    response builder once Phase 8.2B/C ships it."""
    violations: list[ValidationViolation] = []
    for k in _FORBIDDEN_USER_FACING_KEYS:
        if k in payload:
            violations.append(
                ValidationViolation(
                    rule_id="persona.user_facing.forbidden_key",
                    field_path=k,
                    suggestion=(
                        f"Key {k!r} must never appear in a user-facing "
                        "persona response. Strip at the response builder."
                    ),
                )
            )
    # Also reject if any string value contains identity markers.
    for k, v in payload.items():
        if not isinstance(v, str):
            continue
        for hit in scan_sensitive_attributes(v):
            violations.append(
                ValidationViolation(
                    rule_id=f"persona.user_facing.sensitive.{hit.category.value}",
                    field_path=k,
                    matched_phrase=hit.matched,
                    suggestion=(
                        "User-facing persona data contains a sensitive "
                        "attribute. Strip or refuse the response."
                    ),
                )
            )
    return _ok() if not violations else _bad(violations)


# ---------------------------------------------------------------------------
# Population audit payload validation
# ---------------------------------------------------------------------------


def validate_population_audit_payload(payload: dict[str, Any]) -> ValidationResult:
    """Validate a PopulationConstructionAudit payload before insert.
    The audit row is the user-facing audit panel's source of truth, so
    we enforce its shape strictly."""
    violations: list[ValidationViolation] = []

    required = (
        "requested_society",
        "retrieved_persona_count",
        "final_persona_count",
        "cluster_count",
        "geography_coverage_label",
        "society_strength_label",
    )
    for k in required:
        if k not in payload:
            violations.append(
                ValidationViolation(
                    rule_id="audit.required_key_missing",
                    field_path=k,
                    suggestion=f"PopulationConstructionAudit requires `{k}`.",
                )
            )

    for k in (
        "retrieved_persona_count",
        "final_persona_count",
        "cluster_count",
        "direct_trait_count",
        "inferred_trait_count",
        "unknown_trait_count",
        "missing_trait_count",
    ):
        if k in payload and (not isinstance(payload[k], int) or payload[k] < 0):
            violations.append(
                ValidationViolation(
                    rule_id="audit.count_negative_or_non_int",
                    field_path=k,
                    matched_phrase=repr(payload[k]),
                    suggestion=f"`{k}` must be a non-negative integer.",
                )
            )

    for k in ("geography_coverage_label", "source_freshness_label", "society_strength_label"):
        if k not in payload:
            continue
        v = payload[k]
        if v is None:
            # source_freshness_label is the only nullable label.
            if k != "source_freshness_label":
                violations.append(
                    ValidationViolation(
                        rule_id="audit.label_required",
                        field_path=k,
                        suggestion=f"`{k}` must be one of {list(COVERAGE_LABELS)}.",
                    )
                )
            continue
        if v not in COVERAGE_LABELS:
            violations.append(
                ValidationViolation(
                    rule_id="audit.label_invalid",
                    field_path=k,
                    matched_phrase=str(v),
                    suggestion=(
                        f"`{k}` must be one of {list(COVERAGE_LABELS)}; got {v!r}."
                    ),
                )
            )

    # representativeness_caveats / missing_evidence_warnings must be string lists.
    for k in ("representativeness_caveats", "missing_evidence_warnings"):
        if k not in payload:
            continue
        v = payload[k]
        if not isinstance(v, (list, tuple)) or any(
            not isinstance(x, str) for x in v
        ):
            violations.append(
                ValidationViolation(
                    rule_id="audit.list_not_strings",
                    field_path=k,
                    suggestion=f"`{k}` must be a list of strings.",
                )
            )

    return _ok() if not violations else _bad(violations)
