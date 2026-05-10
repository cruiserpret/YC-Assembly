"""Phase 8.2D — persona mechanism profile initializer.

`build_persona_mechanism_profile` is a PURE function. Given a persona's
trait dict (the support-level breakdown produced by Phase 8.2A's persona
catalog) plus a domain label and the loaded mechanism / belief catalog,
it returns a structured `PersonaMechanismProfile` describing:

  - which mechanisms apply (and why)
  - which mechanisms were skipped (and why)
  - which belief-network rules were consulted (with strength)
  - structured anti-pattern warnings — including the "demographic-only
    roleplay" refusal and any attempt by a mechanism prior to override
    a source-bound trait
  - whether source evidence outranked any conflicting mechanism prior

The initializer DOES NOT write persona rows. It DOES NOT make LLM calls.
It DOES NOT make network calls. The audit row is written separately via
`audit.write_mechanism_initialization_audit`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from assembly.models.behavioral_mechanism import (
    BehavioralMechanism,
    BeliefNetworkRule,
    MechanismApplicabilityRule,
)
from assembly.pipeline.behavioral_science.constants import (
    ANTI_PATTERN_DEMOGRAPHIC_ONLY,
    ANTI_PATTERN_FORBIDDEN_STRENGTH,
    ANTI_PATTERN_PRIOR_OUTRANKED_EVIDENCE,
    INFERENCE_STRENGTHS,
)
from assembly.pipeline.behavioral_science.validator import (
    validate_priors_do_not_outrank_evidence,
)


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonaTraitInput:
    """Compact view of a persona trait — only the fields the initializer
    consumes. Decouples the initializer from the full PersonaTrait ORM."""

    field_name: str
    support_level: str
    value: str | None
    confidence: float


@dataclass(frozen=True)
class AppliedMechanism:
    mechanism_id: UUID
    name: str
    category: str
    strength: float
    domain_label: str
    rationale: str


@dataclass(frozen=True)
class SkippedMechanism:
    mechanism_id: UUID | None
    name: str
    reason_code: str
    reason_message: str


@dataclass(frozen=True)
class AppliedBeliefRule:
    rule_id: UUID
    topic_a: str
    topic_b: str
    relation_type: str
    allowed_inference_strength: str
    notes: str | None


@dataclass(frozen=True)
class PersonaMechanismProfile:
    domain_label: str
    applied_mechanisms: tuple[AppliedMechanism, ...] = ()
    skipped_mechanisms: tuple[SkippedMechanism, ...] = ()
    applied_belief_rules: tuple[AppliedBeliefRule, ...] = ()
    anti_pattern_warnings: tuple[str, ...] = ()
    evidence_outranked_priors: bool = False
    notes: str | None = None


# ---------------------------------------------------------------------------
# Constants used by the initializer
# ---------------------------------------------------------------------------


_SOURCE_BACKED_SUPPORT_LEVELS: frozenset[str] = frozenset({"direct", "inferred"})


# ---------------------------------------------------------------------------
# Initializer
# ---------------------------------------------------------------------------


def build_persona_mechanism_profile(
    *,
    domain_label: str,
    persona_traits: Sequence[PersonaTraitInput],
    candidate_mechanisms: Sequence[BehavioralMechanism],
    applicability_rules_for_domain: Sequence[MechanismApplicabilityRule],
    belief_rules_for_topic: Sequence[BeliefNetworkRule],
    mechanism_overrides: dict[str, Any] | None = None,
    allow_demographic_only: bool = False,
) -> PersonaMechanismProfile:
    """Pure function. Returns a typed profile describing which mechanisms
    apply to this persona and which were refused.

    Inputs:
      - `domain_label`: closed-enum domain (e.g. 'commerce',
        'unsupported_demographic_only').
      - `persona_traits`: the persona's existing trait support breakdown.
      - `candidate_mechanisms`: pre-loaded mechanism catalog (caller
        decides which subset).
      - `applicability_rules_for_domain`: pre-loaded rules for `domain_label`.
      - `belief_rules_for_topic`: pre-loaded belief rules to consult.
      - `mechanism_overrides`: optional per-field hints a mechanism prior
        wants to set. The validator REFUSES to apply any override to a
        field already source-backed — that's the core "evidence outranks
        priors" guarantee.
      - `allow_demographic_only`: explicit opt-in for the experimental
        demographic-only mode. Default False — the framework refuses.

    The function never writes to the database.
    """
    applied: list[AppliedMechanism] = []
    skipped: list[SkippedMechanism] = []
    applied_rules: list[AppliedBeliefRule] = []
    warnings: list[str] = []
    evidence_outranked = False

    # --- Anti-pattern: demographic-only refusal -------------------------
    if (
        domain_label == "unsupported_demographic_only"
        and not allow_demographic_only
    ):
        warnings.append(ANTI_PATTERN_DEMOGRAPHIC_ONLY)
        # Still return a structured profile — caller decides what to do.
        return PersonaMechanismProfile(
            domain_label=domain_label,
            applied_mechanisms=(),
            skipped_mechanisms=tuple(
                SkippedMechanism(
                    mechanism_id=m.id,
                    name=m.name,
                    reason_code="DEMOGRAPHIC_ONLY_REFUSED",
                    reason_message=(
                        "domain_label='unsupported_demographic_only' and "
                        "`allow_demographic_only` is False. The framework "
                        "refuses to initialize mechanisms in this mode."
                    ),
                )
                for m in candidate_mechanisms
            ),
            applied_belief_rules=(),
            anti_pattern_warnings=tuple(warnings),
            evidence_outranked_priors=False,
            notes=(
                "Refused to initialize mechanisms in demographic-only mode. "
                "Pass allow_demographic_only=True to override (experimental)."
            ),
        )

    # --- Source-supported field set (used to refuse prior overrides) ---
    source_supported_fields = {
        t.field_name
        for t in persona_traits
        if t.support_level in _SOURCE_BACKED_SUPPORT_LEVELS and t.value
    }

    # --- Validate mechanism overrides (priors) do not outrank evidence -
    if mechanism_overrides:
        result = validate_priors_do_not_outrank_evidence(
            source_supported_fields=source_supported_fields,
            mechanism_overrides=mechanism_overrides,
        )
        if not result.passed:
            warnings.append(ANTI_PATTERN_PRIOR_OUTRANKED_EVIDENCE)
            evidence_outranked = True
            # Fall through; we still apply mechanisms whose priors did NOT
            # collide with source-bound fields.

    # --- Apply mechanisms whose applicability rule covers this domain --
    rules_by_mechanism = {
        rule.mechanism_id: rule
        for rule in applicability_rules_for_domain
    }
    for mech in candidate_mechanisms:
        rule = rules_by_mechanism.get(mech.id)
        if rule is None:
            skipped.append(
                SkippedMechanism(
                    mechanism_id=mech.id,
                    name=mech.name,
                    reason_code="NO_APPLICABILITY_RULE",
                    reason_message=(
                        f"No applicability rule for "
                        f"({mech.name}, {domain_label})."
                    ),
                )
            )
            continue

        # If the rule explicitly refuses initialization, count it as a
        # skipped mechanism with a structured reason.
        if rule.applies_when.get("refuses_initialization") is True:
            skipped.append(
                SkippedMechanism(
                    mechanism_id=mech.id,
                    name=mech.name,
                    reason_code="REFUSED_BY_APPLICABILITY_RULE",
                    reason_message=(
                        rule.notes
                        or "Applicability rule refuses initialization."
                    ),
                )
            )
            continue

        # Required-field gate.
        required = rule.applies_when.get("requires") or []
        if required:
            missing_required = [
                f for f in required if f not in source_supported_fields
            ]
            if missing_required:
                skipped.append(
                    SkippedMechanism(
                        mechanism_id=mech.id,
                        name=mech.name,
                        reason_code="REQUIRED_FIELDS_MISSING",
                        reason_message=(
                            f"Required source-supported fields missing: "
                            f"{missing_required}."
                        ),
                    )
                )
                continue

        # Optional max-strength clamp from the rule.
        clamp = rule.applies_when.get("max_strength")
        strength = float(mech.default_strength)
        if clamp in INFERENCE_STRENGTHS:
            strength = min(
                strength, _strength_to_float(clamp),
            )

        applied.append(AppliedMechanism(
            mechanism_id=mech.id,
            name=mech.name,
            category=mech.category,
            strength=strength,
            domain_label=domain_label,
            rationale=(
                rule.notes
                or f"Applied per rule for domain {domain_label!r}."
            ),
        ))

    # --- Belief rules: surface every rule the caller asked us to consult.
    # The DB CHECK already excludes 'strong'; we re-flag if a stale rule
    # somehow carries it (defense in depth).
    for br in belief_rules_for_topic:
        if br.allowed_inference_strength == "strong":  # pragma: no cover
            warnings.append(ANTI_PATTERN_FORBIDDEN_STRENGTH)
            continue
        applied_rules.append(AppliedBeliefRule(
            rule_id=br.id,
            topic_a=br.topic_a,
            topic_b=br.topic_b,
            relation_type=br.relation_type,
            allowed_inference_strength=br.allowed_inference_strength,
            notes=br.notes,
        ))

    return PersonaMechanismProfile(
        domain_label=domain_label,
        applied_mechanisms=tuple(applied),
        skipped_mechanisms=tuple(skipped),
        applied_belief_rules=tuple(applied_rules),
        anti_pattern_warnings=tuple(warnings),
        evidence_outranked_priors=evidence_outranked,
        notes=None,
    )


def _strength_to_float(label: str) -> float:
    """Map symbolic inference strength to a numeric clamp for combining
    with mechanism default_strength. Conservative mapping — 'moderate'
    caps at 0.6, never 1.0."""
    return {
        "none": 0.0,
        "weak": 0.3,
        "moderate": 0.6,
    }.get(label, 0.0)
