"""Product Intelligence Object — the structured output of the intake parser.

Every derived field carries a `Provenance` so the Critic's C2 rule
("provenance for derived parser fields") is enforceable both by Pydantic
(structural) and by `verify_provenance()` (substring traceability).

The three provenance kinds are:

  - `verbatim`     — value is a literal substring of one of the user's input
                      fields (description, target_society, etc.). Source field
                      and excerpt MUST be set.
  - `paraphrase`   — value is a rewording of a specific user substring. Same
                      metadata as verbatim, plus a different `value`.
  - `assumption`   — value is NOT in the user's input. `assumption_rationale`
                      MUST be set. The intake-parser pipeline emits a paired
                      `evidence_item` of `kind=missing` for each assumption.

Empty lists are valid. The parser MUST NOT invent items — if a derived field
cannot be sourced, omit it.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProvenanceKind(str, Enum):
    VERBATIM = "verbatim"
    PARAPHRASE = "paraphrase"
    ASSUMPTION = "assumption"


class SourceField(str, Enum):
    """Which user-supplied input field a verbatim/paraphrase value came from.
    Always one of the canonical input fields — never a fabricated source."""

    PRODUCT_TYPE = "user_product_type"
    PRODUCT_NAME = "user_product_name"
    DESCRIPTION = "user_description"
    PRICE_STRUCTURE = "user_price_structure"
    TARGET_SOCIETY = "user_target_society"
    COMPETITORS = "user_competitors"
    PRODUCT_URL = "user_product_url"
    ADDITIONAL_CONTEXT = "user_additional_context"


class DerivedString(BaseModel):
    """A string-valued derived field with provenance metadata. The intake
    parser is required to emit one of these (not a bare string) for every
    derived value."""

    model_config = ConfigDict(extra="forbid")

    value: str
    provenance: ProvenanceKind

    # Required when provenance ∈ {verbatim, paraphrase}
    source_field: SourceField | None = None
    source_excerpt: str | None = None
    source_offset_start: int | None = None
    source_offset_end: int | None = None

    # Required when provenance == assumption
    assumption_rationale: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "DerivedString":
        if self.provenance in (ProvenanceKind.VERBATIM, ProvenanceKind.PARAPHRASE):
            if self.source_field is None:
                raise ValueError(
                    f"provenance={self.provenance.value} requires source_field"
                )
            if not self.source_excerpt:
                raise ValueError(
                    f"provenance={self.provenance.value} requires source_excerpt"
                )
            if self.assumption_rationale is not None:
                raise ValueError(
                    "assumption_rationale must be null for non-assumption provenance"
                )
        if self.provenance == ProvenanceKind.ASSUMPTION:
            if not self.assumption_rationale:
                raise ValueError(
                    "provenance=assumption requires assumption_rationale"
                )
            if self.source_field is not None or self.source_excerpt is not None:
                raise ValueError(
                    "source_field/source_excerpt must be null for assumption provenance"
                )
        return self


PromiseType = Literal["emotional", "functional", "status", "mixed"]


class ClaimWithProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: DerivedString
    promise_type: PromiseType


class ProductIntelligenceObject(BaseModel):
    """Output of the Phase 4 intake parser.

    All list fields default to `[]`. The parser MUST return empty when the
    user's input does not support a given field — never fabricate items.
    """

    model_config = ConfigDict(extra="forbid")

    # Echoes / normalizations of user input
    product_type: DerivedString
    product_name: DerivedString
    description_normalized: DerivedString
    price_summary: DerivedString
    target_society_summary: DerivedString

    # Derived structures — empty when not supportable from input
    buyer_roles: list[DerivedString] = Field(default_factory=list)
    current_alternatives: list[DerivedString] = Field(default_factory=list)
    claims: list[ClaimWithProvenance] = Field(default_factory=list)
    trust_risks: list[DerivedString] = Field(default_factory=list)
    objections: list[DerivedString] = Field(default_factory=list)
    switching_triggers: list[DerivedString] = Field(default_factory=list)

    novelty_type: DerivedString | None = None

    emotional_promises: list[DerivedString] = Field(default_factory=list)
    functional_promises: list[DerivedString] = Field(default_factory=list)
    status_promises: list[DerivedString] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Substring verification — runs after Pydantic validation. Required by C2.
# ---------------------------------------------------------------------------


def _walk_derived(pio: ProductIntelligenceObject) -> list[tuple[str, DerivedString]]:
    """Collect (field_path, DerivedString) for every DerivedString in the PIO."""
    out: list[tuple[str, DerivedString]] = []
    out.append(("product_type", pio.product_type))
    out.append(("product_name", pio.product_name))
    out.append(("description_normalized", pio.description_normalized))
    out.append(("price_summary", pio.price_summary))
    out.append(("target_society_summary", pio.target_society_summary))
    if pio.novelty_type is not None:
        out.append(("novelty_type", pio.novelty_type))

    list_fields = [
        ("buyer_roles", pio.buyer_roles),
        ("current_alternatives", pio.current_alternatives),
        ("trust_risks", pio.trust_risks),
        ("objections", pio.objections),
        ("switching_triggers", pio.switching_triggers),
        ("emotional_promises", pio.emotional_promises),
        ("functional_promises", pio.functional_promises),
        ("status_promises", pio.status_promises),
    ]
    for label, lst in list_fields:
        for i, item in enumerate(lst):
            out.append((f"{label}[{i}]", item))

    for i, claim in enumerate(pio.claims):
        out.append((f"claims[{i}].text", claim.text))

    return out


class ProvenanceError(BaseModel):
    """One failure in `verify_provenance`. Suitable for echo back to the LLM."""

    field_path: str
    rule: str
    message: str


def verify_provenance(
    pio: ProductIntelligenceObject,
    *,
    sources: dict[SourceField, str],
) -> list[ProvenanceError]:
    """Verify every derived field's provenance against the actual user inputs.

    For verbatim and paraphrase fields, `source_excerpt` MUST appear (case-
    insensitively) inside the corresponding source field text. For assumption
    fields, `assumption_rationale` MUST be set (already enforced by the
    Pydantic model_validator).

    Returns 0+ errors. Caller (the intake_parser repair loop) feeds these back
    to the LLM verbatim for correction.
    """
    errors: list[ProvenanceError] = []

    for field_path, ds in _walk_derived(pio):
        prov = ds.provenance

        if prov == ProvenanceKind.ASSUMPTION:
            # Already structurally validated by Pydantic; nothing to substring-check.
            if not ds.assumption_rationale:
                errors.append(
                    ProvenanceError(
                        field_path=field_path,
                        rule="assumption.requires_rationale",
                        message="kind=assumption requires assumption_rationale",
                    )
                )
            continue

        # verbatim / paraphrase
        sf = ds.source_field
        if sf is None:
            errors.append(
                ProvenanceError(
                    field_path=field_path,
                    rule="provenance.requires_source_field",
                    message=f"kind={prov.value} requires source_field",
                )
            )
            continue

        source_text = sources.get(sf)
        if source_text is None:
            errors.append(
                ProvenanceError(
                    field_path=field_path,
                    rule="provenance.unknown_source_field",
                    message=f"source_field={sf.value} has no source text "
                    "(was it populated by the user?)",
                )
            )
            continue

        excerpt = (ds.source_excerpt or "").strip()
        if not excerpt:
            errors.append(
                ProvenanceError(
                    field_path=field_path,
                    rule="provenance.empty_excerpt",
                    message=f"kind={prov.value} requires non-empty source_excerpt",
                )
            )
            continue

        if excerpt.lower() not in source_text.lower():
            errors.append(
                ProvenanceError(
                    field_path=field_path,
                    rule="provenance.excerpt_not_in_source",
                    message=(
                        f"source_excerpt {excerpt!r} not found in "
                        f"{sf.value} text (case-insensitive). "
                        "Either fix the excerpt or change provenance to assumption."
                    ),
                )
            )
            continue

        # If offsets supplied, verify they match the excerpt.
        if (
            ds.source_offset_start is not None
            and ds.source_offset_end is not None
        ):
            actual = source_text[
                ds.source_offset_start : ds.source_offset_end
            ]
            if actual.lower() != excerpt.lower():
                errors.append(
                    ProvenanceError(
                        field_path=field_path,
                        rule="provenance.offset_mismatch",
                        message=(
                            f"source_offset_start/end "
                            f"({ds.source_offset_start}:{ds.source_offset_end}) "
                            f"yields {actual!r} but source_excerpt is {excerpt!r}"
                        ),
                    )
                )

    return errors
