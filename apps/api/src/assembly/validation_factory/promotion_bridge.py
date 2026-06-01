"""Phase 15L-C — wire the 15L-B Observed Outcome Mapping Protocol INTO the 15J
candidate promotion/ingest path.

The Phase 15J factory must not promote/ingest a candidate into the official
validation ledger as an OBSERVED four-bucket case unless a reviewer-authored,
gate-passing ``ProposedOutcomeMapping`` (Phase 15L-B) justifies the distribution.

This module is purely ADDITIVE and isolated: it COMPOSES the existing 15J
primitives (``evaluate_promotion_gates``, ``build_case_payload_from_candidate``)
and the 15L-B ``validate_mapping`` — it edits NEITHER. The low-level primitives
keep their exact signatures/behavior (so every direct-call 15J test stays green);
only the CLI is rewired to the gated path here. No official-ledger schema is
changed: mapping provenance is preserved in the existing free-text fields
(``observed.observation_notes`` + ``anti_overfit.notes``) and recovered by
``case_mapping_provenance``.

Pure/deterministic: no LLM, no network, no DB, no calibration, no forecast change.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from assembly.validation_factory.candidate_factory import (
    build_case_payload_from_candidate,
    evaluate_promotion_gates,
)
from assembly.validation_factory.candidate_schema import CandidateCase, PromotionTarget
from assembly.validation_factory.candidate_store import DEFAULT_CANDIDATES_DIR
from assembly.validation_factory.outcome_mapping_protocol import (
    MappingValidationResult,
    ProposedOutcomeMapping,
    validate_mapping,
)
from assembly.validation_ledger.schema import ValidationCase

BRIDGE_VERSION = "promotion_bridge.v1"

# Only these mapping types may carry a four-bucket distribution into the ledger.
DISTRIBUTION_MAPPING_TYPES = (
    "direct_observed_distribution",
    "assumption_labeled_distribution",
)
# Targets that create an OBSERVED case and therefore require a validated mapping.
_OBSERVED_TARGETS = ("training", "holdout")

# A deterministic, parseable provenance marker embedded in the case's free-text
# fields. The official ledger schema is intentionally NOT modified; this marker
# lets readiness recover mapping_type/provenance after ingestion so an
# assumption-labeled case is never later miscounted as a measured direct-observed.
# Bounded by open/close tags so a second copy (in anti_overfit.notes) never
# pollutes the parse.
PROVENANCE_MARKER = "[15L-mapping-provenance]"
_PROVENANCE_OPEN = "[15L-mapping-provenance]"
_PROVENANCE_CLOSE = "[/15L-mapping-provenance]"
# The unique provenance token. It is STRIPPED from every interpolated free-text
# field (assumptions / reviewer / candidate_id) so a reviewer-controlled string
# can never forge a marker, and the grammar separators ';' '=' are neutralized.
_PROVENANCE_TOKEN = "15L-mapping-provenance"
# A TRUSTED marker must carry the full builder keyset; a bare injected fragment
# (e.g. just "provenance=measured_four_bucket") lacks these and is rejected.
_REQUIRED_PROVENANCE_KEYS = (
    "protocol",
    "mapping_type",
    "provenance",
    "counts_toward_direct_observed_bar",
)


def _sanitize_freetext(value: str | None) -> str:
    """Neutralize untrusted free text so it can never forge or corrupt the
    provenance marker: strip the token and the ';'/'=' grammar separators."""
    s = (value or "").replace(_PROVENANCE_TOKEN, "")
    return s.replace(";", ",").replace("=", ":")


# --------------------------------------------------------------------------
# Mapping discovery (sidecar keyed to the candidate's OWN id — closes the
# id-laundering path: a candidate's mapping is always loaded by its own id)
# --------------------------------------------------------------------------


def default_proposals_dir() -> Path:
    """validation_cases/mapping_proposals/ (sibling of the candidate store)."""
    return Path(DEFAULT_CANDIDATES_DIR).parent / "mapping_proposals"


# Scaffold / example files in the proposals dir that are not real proposals.
_NON_PROPOSAL_NAMES = {"TEMPLATE.json"}


def load_mapping_proposal(
    candidate_id: str,
    *,
    mapping_path: str | Path | None = None,
    proposals_dir: str | Path | None = None,
) -> ProposedOutcomeMapping | None:
    """Load a reviewer-authored mapping proposal. With ``mapping_path`` given, load
    exactly that file (missing -> FileNotFoundError). Otherwise auto-resolve
    ``<proposals_dir>/<candidate_id>.json`` (missing -> None, an explicit
    "no mapping" so the gate can refuse with a clear message)."""
    if mapping_path is not None:
        p = Path(mapping_path)
        if not p.exists():
            raise FileNotFoundError(f"mapping proposal not found: {p}")
        return ProposedOutcomeMapping.model_validate(json.loads(p.read_text(encoding="utf-8")))
    base = Path(proposals_dir) if proposals_dir is not None else default_proposals_dir()
    p = base / f"{candidate_id}.json"
    if p.name in _NON_PROPOSAL_NAMES or not p.exists():
        return None
    return ProposedOutcomeMapping.model_validate(json.loads(p.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# The 15L-C gate: 15J gates + the mapping requirement for observed targets
# --------------------------------------------------------------------------


def mapping_gate_issues(
    candidate: CandidateCase,
    target: PromotionTarget,
    mapping: ProposedOutcomeMapping | None,
    *,
    result: MappingValidationResult | None = None,
) -> list[str]:
    """The NEW Phase 15L-C requirement, layered on top of the 15J gates. Returns
    BLOCKING issues. Mutates nothing."""
    issues: list[str] = []

    # pending records NO observed outcome -> it must NOT carry a distribution.
    if target == "pending":
        if mapping is not None and mapping.proposed_proportions is not None:
            issues.append(
                "mapping gate (15L-B): a 'pending' target must NOT carry a four-bucket "
                "distribution mapping — pending records no observed outcome yet"
            )
        return issues

    if target not in _OBSERVED_TARGETS:
        return issues

    # training / holdout REQUIRE a reviewer-authored, gate-passing mapping.
    if mapping is None:
        issues.append(
            f"mapping gate (15L-B): target {target!r} now requires a reviewer-authored, "
            "gate-passing ProposedOutcomeMapping (a four-bucket distribution) — none "
            "supplied (place one at mapping_proposals/<candidate_id>.json or pass --mapping)"
        )
        return issues

    if mapping.candidate_id != candidate.candidate_id:
        issues.append(
            f"mapping gate (15L-B / G_candidate_required): candidate mismatch — the "
            f"proposal is for {mapping.candidate_id!r} but the candidate is "
            f"{candidate.candidate_id!r}; cannot launder one candidate's mapping past "
            "another's evidence"
        )
        return issues

    res = result if result is not None else validate_mapping(mapping, candidate)
    # Hard-fail on any validate_mapping blocking issue — no --force escape.
    if not res.ok:
        issues += [f"mapping gate (15L-B): {i}" for i in res.issues]

    if mapping.mapping_type not in DISTRIBUTION_MAPPING_TYPES:
        issues.append(
            f"mapping gate (15L-B): mapping_type {mapping.mapping_type!r} cannot produce an "
            "official observed distribution — only direct_observed_distribution or "
            "assumption_labeled_distribution may; action_anchor_only / evidence_only / "
            "reject cannot be ingested as a four-bucket observed case"
        )
    elif not res.training_eligible:
        issues.append(
            "mapping gate (15L-B): the mapping is not training-eligible "
            f"(provenance={res.provenance})"
        )

    # Reviewer-authored, not autogenerated.
    if not (mapping.reviewer or "").strip() or not (mapping.reviewed_at or "").strip():
        issues.append(
            "mapping gate (15L-B): the mapping must be reviewer-authored (set both "
            "'reviewer' and 'reviewed_at') — autogenerated mappings cannot be ingested"
        )

    # A candidate that ALSO carries its own claimed_outcome_proportions must agree
    # with the mapping (no candidate-says-X / mapping-says-Y contradiction).
    if (
        candidate.claimed_outcome_proportions is not None
        and mapping.proposed_proportions is not None
        and candidate.claimed_outcome_proportions.to_buckets()
        != mapping.proposed_proportions.to_buckets()
    ):
        issues.append(
            "mapping gate (15L-B): candidate.claimed_outcome_proportions disagrees with "
            "the mapping's proposed_proportions"
        )

    # G7: a retrospective known-outcome case can never be a clean holdout.
    if target == "holdout":
        issues.append(
            "mapping gate (15L-B / G7): a retrospective known-outcome case can NEVER be a "
            "clean holdout — promote to training, or stage as pending and lock an Assembly "
            "prediction BEFORE the outcome (Phase 14C + 15I)"
        )

    return issues


def evaluate_ingest_gates(
    candidate: CandidateCase,
    target: PromotionTarget,
    *,
    mapping: ProposedOutcomeMapping | None = None,
    existing_candidates: Sequence[CandidateCase] = (),
    existing_cases: Sequence[ValidationCase] = (),
    allow_duplicate: bool = False,
) -> list[str]:
    """The full Phase 15L-C ingest gate: the original 15J ``evaluate_promotion_gates``
    PLUS the mapping requirement. For an observed target with a distribution mapping,
    the mapping's proportions are presented (in memory only) to the 15J observed-
    discipline gate so the candidate's on-disk JSON is never mutated. Returns the
    combined BLOCKING issues."""
    cand_for_gates = candidate
    result: MappingValidationResult | None = None
    if (
        target in _OBSERVED_TARGETS
        and mapping is not None
        and mapping.proposed_proportions is not None
    ):
        result = validate_mapping(mapping, candidate)
        cand_for_gates = candidate.model_copy(
            update={"claimed_outcome_proportions": mapping.proposed_proportions}
        )
    issues = list(
        evaluate_promotion_gates(
            cand_for_gates,
            target,
            existing_candidates=existing_candidates,
            existing_cases=existing_cases,
            allow_duplicate=allow_duplicate,
        )
    )
    issues += mapping_gate_issues(candidate, target, mapping, result=result)
    return issues


# --------------------------------------------------------------------------
# Payload build — observed proportions come ONLY from a validated mapping
# --------------------------------------------------------------------------


def _iso_date(value: str | None) -> str | None:
    if not value or value == "unknown":
        return None
    return value[:10] if len(value) >= 10 else value


def _provenance_marker(mapping: ProposedOutcomeMapping, result: MappingValidationResult) -> str:
    # Only reviewer is free text inside the marker; the rest are computed / Literal.
    return (
        f"{_PROVENANCE_OPEN} protocol=outcome_mapping_protocol.v1; "
        f"mapping_type={result.mapping_type}; provenance={result.provenance}; "
        f"counts_toward_direct_observed_bar={result.counts_toward_direct_observed_bar}; "
        f"forced_confidence={result.forced_confidence}; "
        f"denominator_quality={mapping.denominator_quality}; "
        f"reviewer={_sanitize_freetext(mapping.reviewer)} {_PROVENANCE_CLOSE}"
    )


def build_case_payload_with_mapping(
    candidate: CandidateCase,
    target: PromotionTarget,
    mapping: ProposedOutcomeMapping,
    *,
    case_id: str | None = None,
    locked_at: str | None = None,
    result: MappingValidationResult | None = None,
) -> dict:
    """Build a ValidationCase payload whose OBSERVED proportions come solely from the
    validated mapping (not a candidate field) — with the real denominator, the
    forced confidence (assumption_labeled -> 'low'), the observed date wired in, and
    a parseable provenance marker so an assumption-labeled case is never later
    mistaken for a measured direct-observed one. Mutates nothing."""
    if mapping.proposed_proportions is None:
        raise ValueError(
            "build_case_payload_with_mapping requires a distribution mapping "
            "(proposed_proportions present)"
        )
    res = result if result is not None else validate_mapping(mapping, candidate)
    # Present the mapping's proportions to the existing builder (in-memory copy).
    payload = build_case_payload_from_candidate(
        candidate.model_copy(
            update={"claimed_outcome_proportions": mapping.proposed_proportions}
        ),
        target,
        case_id=case_id,
        locked_at=locked_at,
    )
    if "observed" in payload:
        b = mapping.proposed_proportions.to_buckets()
        eff_conf = res.forced_confidence or mapping.confidence
        marker = _provenance_marker(mapping, res)
        # The trusted marker is emitted FIRST, then sanitized human free text — so a
        # crafted assumptions/candidate field can neither precede nor forge it.
        safe_assumptions = (
            "; ".join(_sanitize_freetext(a) for a in mapping.assumptions)
            if mapping.assumptions
            else "none"
        )
        payload["observed"] = {
            **b,
            "denominator_type": mapping.denominator_type,
            "denominator_count": mapping.denominator_count,
            "observation_confidence": eff_conf,
            "observed_at": _iso_date(candidate.launch_or_test_date),
            "observation_notes": (
                f"{marker} {BRIDGE_VERSION}: reviewer-mapped observed outcome for "
                f"candidate {_sanitize_freetext(candidate.candidate_id)}; "
                f"assumptions: {safe_assumptions}"
            ),
        }
        # A robust second copy of the provenance marker, also FIRST in the field.
        existing_notes = payload["anti_overfit"].get("notes", "")
        payload["anti_overfit"]["notes"] = f"{marker} {existing_notes}".strip()
    return payload


# --------------------------------------------------------------------------
# Provenance recovery + ledger-level direct-observed counting
# --------------------------------------------------------------------------


def _parse_provenance_segment(blob: str) -> dict | None:
    """Parse the FIRST bounded marker in a single field. Requires a close tag and
    the full builder keyset, and uses first-occurrence-wins so an injected
    duplicate key cannot overwrite a trusted value. Returns None if untrusted."""
    start = blob.find(_PROVENANCE_OPEN)
    if start == -1:
        return None
    start += len(_PROVENANCE_OPEN)
    end = blob.find(_PROVENANCE_CLOSE, start)
    if end == -1:
        return None
    out: dict = {}
    for part in blob[start:end].split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out.setdefault(key.strip(), value.strip())
    if not all(k in out for k in _REQUIRED_PROVENANCE_KEYS):
        return None
    return out


def case_mapping_provenance(case: ValidationCase) -> dict | None:
    """Recover the trusted mapping provenance recorded at ingest, or None if the
    case was not produced by the 15L-C bridge. Each free-text field is parsed
    INDEPENDENTLY (the marker is emitted first in each), and only a complete,
    close-tag-bounded marker is accepted — a forged fragment is ignored."""
    blobs: list[str] = []
    if case.observed is not None:
        blobs.append(case.observed.observation_notes or "")
    blobs.append(case.anti_overfit.notes or "")
    for blob in blobs:
        prov = _parse_provenance_segment(blob)
        if prov is not None:
            return prov
    return None


def ledger_direct_observed_count(cases: Sequence[ValidationCase]) -> int:
    """Count ledger cases whose recorded provenance is a MEASURED direct-observed
    distribution. Assumption-labeled cases are deliberately EXCLUDED — they never
    count toward the >=20 direct-observed Phase 15E bar."""
    n = 0
    for c in cases:
        prov = case_mapping_provenance(c)
        if prov and prov.get("provenance") == "measured_four_bucket":
            n += 1
    return n
