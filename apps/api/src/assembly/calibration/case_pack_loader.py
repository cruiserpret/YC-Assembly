"""Phase 12A.2 — Load and validate blinded validation case packs.

A "case pack" is a directory (or in-memory collection) of
:class:`BlindCase` records. Each record has three sections separated
for safety: ``pre_launch_input``, ``hidden_real_world_outcome``,
``scoring_metadata``. See :mod:`assembly.calibration.blind_case_schema`
for the type-level contract.

This module is the **loader and blindness validator**. Its job is to
refuse to materialize any case that violates the blindness invariants
*before* the case enters the scoring pipeline. Scoring is owned by
:mod:`assembly.calibration.case_scoring`.

Why a separate validator surface on top of the Pydantic models:

  - Pydantic enforces the type-level contract (sections present,
    no outcome-shaped keys in the brief, etc.). The model raises on
    construction.
  - This loader layer adds a *second pass* that scans the
    surrounding context: for example, was the case loaded from a
    JSON file whose top-level keys include something we don't know
    about? Did the pack-level summary spot two cases sharing a
    ``case_id``? Did any case's ``forbidden_post_cutoff_sources``
    actually appear in the brief? Those are loader-level audits
    that don't belong on the per-model validator.

Pure-Python: no LLM calls, no DB, no HTTP. The loader writes nothing.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from assembly.calibration.blind_case_schema import (
    BlindCase,
    HiddenRealWorldOutcome,
    PreLaunchInput,
    ScoringMetadata,
    assembly_brief_excludes_outcome_fields,
)

logger = logging.getLogger(__name__)


_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "pre_launch_input",
    "hidden_real_world_outcome",
    "scoring_metadata",
})


class BlindCaseLoadError(ValueError):
    """Raised when a case fails to load OR a blindness invariant
    fails. Carries a human-readable list of violations so the
    caller can decide whether to log, surface, or reject."""

    def __init__(self, message: str, violations: list[str] | None = None):
        super().__init__(message)
        self.violations: list[str] = list(violations or [])


# ---------------------------------------------------------------------------
# Single-case loaders
# ---------------------------------------------------------------------------


def load_blind_case_from_dict(payload: dict[str, Any]) -> BlindCase:
    """Construct a :class:`BlindCase` from a plain dict.

    Refuses unknown top-level keys. Re-raises Pydantic
    ``ValidationError`` as :class:`BlindCaseLoadError` so callers
    have a single exception type to handle.
    """
    if not isinstance(payload, dict):
        raise BlindCaseLoadError(
            f"payload is not a dict (got {type(payload).__name__})"
        )
    extra = set(payload.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if extra:
        raise BlindCaseLoadError(
            "unknown top-level keys in case payload",
            violations=[f"unknown_top_level_key={k!r}" for k in sorted(extra)],
        )
    for required in ("pre_launch_input", "hidden_real_world_outcome"):
        if required not in payload:
            raise BlindCaseLoadError(
                f"missing required section: {required!r}",
                violations=[f"missing_section={required!r}"],
            )
    try:
        case = BlindCase(**payload)
    except ValidationError as e:
        raise BlindCaseLoadError(
            f"case failed schema validation: {e.errors()[0].get('msg', e)}",
            violations=[
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ],
        ) from e
    # Surface any loader-level blindness violation as an exception
    # rather than letting a "loaded" case carry a known leak.
    ok, viols = _check_loader_blindness_invariants(case)
    if not ok:
        raise BlindCaseLoadError(
            "case violates blindness invariants",
            violations=viols,
        )
    return case


def load_blind_case_from_json_path(path: str | Path) -> BlindCase:
    """Load a single case JSON file from disk and validate it."""
    p = Path(path)
    if not p.exists():
        raise BlindCaseLoadError(f"case file not found: {p!s}")
    if not p.is_file():
        raise BlindCaseLoadError(f"case path is not a regular file: {p!s}")
    try:
        with p.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as e:
        raise BlindCaseLoadError(
            f"case JSON is malformed in {p.name}: {e}"
        ) from e
    return load_blind_case_from_dict(payload)


# ---------------------------------------------------------------------------
# Directory pack loader
# ---------------------------------------------------------------------------


@dataclass
class CasePack:
    """An ordered collection of :class:`BlindCase` records keyed by
    ``case_id``. Construction guarantees:

      - every member has already passed schema + blindness validation
      - ``case_id`` is unique across the pack
      - ``pre_launch_hash`` is recorded per member so a later
        reviewer can verify what brief each case shipped with
    """

    cases: dict[str, BlindCase] = field(default_factory=dict)
    pre_launch_hashes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self):
        return iter(self.cases.values())

    def case_ids(self) -> list[str]:
        return list(self.cases.keys())


def load_case_pack_from_directory(
    directory: str | Path,
    *,
    pattern: str = "*.json",
) -> CasePack:
    """Load every ``*.json`` file in ``directory`` as a
    :class:`BlindCase`. Returns a :class:`CasePack`.

    Order: filesystem order (sorted by filename for determinism).
    """
    d = Path(directory)
    if not d.exists():
        raise BlindCaseLoadError(
            f"case-pack directory not found: {d!s}"
        )
    if not d.is_dir():
        raise BlindCaseLoadError(
            f"case-pack path is not a directory: {d!s}"
        )
    pack = CasePack()
    files = sorted(d.glob(pattern))
    for fp in files:
        try:
            case = load_blind_case_from_json_path(fp)
        except BlindCaseLoadError as e:
            # Re-raise with file context — fail fast so the caller
            # can't get half-loaded packs.
            raise BlindCaseLoadError(
                f"failed to load {fp.name}: {e}",
                violations=list(e.violations),
            ) from e
        cid = case.pre_launch_input.case_id
        if cid in pack.cases:
            raise BlindCaseLoadError(
                f"duplicate case_id in pack: {cid!r}",
                violations=[
                    f"duplicate_case_id={cid!r} "
                    f"in {fp.name} (already loaded)"
                ],
            )
        pack.cases[cid] = case
        pack.pre_launch_hashes[cid] = case.compute_pre_launch_hash()
    return pack


# ---------------------------------------------------------------------------
# Blindness validation across a whole pack
# ---------------------------------------------------------------------------


def validate_case_pack_blindness(
    pack: CasePack | Iterable[BlindCase],
) -> tuple[bool, list[str]]:
    """Re-run every blindness invariant across a pack as a single
    audit. Returns ``(ok, violations)``.

    Per-case invariants checked (defensive re-check — the loaders
    already enforce these, this is the pack-level audit surface):

      - Assembly-visible brief excludes outcome-shaped keys
      - ``observed_collection_date`` is strictly after ``cutoff_date``
      - none of the strings in ``forbidden_post_cutoff_sources``
        appear inside the pre_launch_brief (case-insensitive
        substring scan over the JSON-serialized brief)

    Pack-level invariants:

      - no duplicate ``case_id``
      - no duplicate ``pre_launch_hash`` (would indicate a literal
        copy of a brief)
    """
    cases = list(pack) if not isinstance(pack, CasePack) else list(pack.cases.values())
    violations: list[str] = []
    seen_ids: set[str] = set()
    seen_hashes: dict[str, str] = {}
    for case in cases:
        cid = case.pre_launch_input.case_id
        if cid in seen_ids:
            violations.append(f"duplicate_case_id={cid!r}")
            continue
        seen_ids.add(cid)
        # Outcome leakage into the brief
        brief = case.to_assembly_brief()
        ok, leaked = assembly_brief_excludes_outcome_fields(brief)
        if not ok:
            violations.append(
                f"case={cid!r} brief_leaks_outcome_fields={leaked!r}"
            )
        # Cutoff invariant
        obs = case.hidden_real_world_outcome.observed_collection_date
        cut = case.pre_launch_input.cutoff_date
        if obs <= cut:
            violations.append(
                f"case={cid!r} observed_collection_date"
                f"={obs.isoformat()} not strictly after "
                f"cutoff_date={cut.isoformat()}"
            )
        # Forbidden-source substring scan
        forbidden_violations = _scan_brief_for_forbidden_sources(case)
        violations.extend(
            f"case={cid!r} {msg}" for msg in forbidden_violations
        )
        # Duplicate-hash detection
        h = case.compute_pre_launch_hash()
        if h in seen_hashes:
            violations.append(
                f"duplicate_pre_launch_hash={h[:12]}… shared by "
                f"case={cid!r} and case={seen_hashes[h]!r}"
            )
        seen_hashes[h] = cid
    return (len(violations) == 0, violations)


# ---------------------------------------------------------------------------
# Pack summary
# ---------------------------------------------------------------------------


def summarize_case_pack(pack: CasePack) -> dict[str, Any]:
    """Return a human-readable summary of a loaded pack.

    Includes counts by category and by ``observed_source_type``, the
    earliest/latest cutoff date, and a list of any cases marked with
    ``observed_source_type="synthetic_test_fixture"`` so reviewers
    know which cases are not real outcomes.
    """
    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    synthetic_case_ids: list[str] = []
    cutoffs: list[date] = []
    sample_sizes: list[int] = []
    for case in pack:
        cat = case.pre_launch_input.category
        by_category[cat] = by_category.get(cat, 0) + 1
        src = case.hidden_real_world_outcome.observed_source_type
        by_source[src] = by_source.get(src, 0) + 1
        cutoffs.append(case.pre_launch_input.cutoff_date)
        sample_sizes.append(
            case.hidden_real_world_outcome.observed_sample_size
        )
        if src == "synthetic_test_fixture":
            synthetic_case_ids.append(case.pre_launch_input.case_id)
    blindness_ok, blindness_violations = validate_case_pack_blindness(pack)
    return {
        "case_count": len(pack),
        "by_category": dict(sorted(by_category.items())),
        "by_observed_source_type": dict(sorted(by_source.items())),
        "synthetic_test_fixture_case_ids": sorted(synthetic_case_ids),
        "cutoff_date_range": {
            "earliest": cutoffs[0].isoformat() if cutoffs else None,
            "latest": cutoffs[-1].isoformat() if cutoffs else None,
        } if cutoffs else None,
        "observed_sample_size_total": sum(sample_sizes),
        "blindness_ok": blindness_ok,
        "blindness_violations": blindness_violations,
        "case_ids": pack.case_ids() if isinstance(pack, CasePack) else None,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_loader_blindness_invariants(
    case: BlindCase,
) -> tuple[bool, list[str]]:
    """Per-case loader-time invariants. The Pydantic models already
    block some of these on construction; we re-check at the loader
    layer so a future schema change can't quietly disable a guard."""
    viols: list[str] = []
    brief = case.to_assembly_brief()
    ok, leaked = assembly_brief_excludes_outcome_fields(brief)
    if not ok:
        viols.append(f"brief_leaks_outcome_fields={leaked!r}")
    obs = case.hidden_real_world_outcome.observed_collection_date
    cut = case.pre_launch_input.cutoff_date
    if obs <= cut:
        viols.append(
            f"observed_collection_date={obs.isoformat()} not "
            f"strictly after cutoff_date={cut.isoformat()}"
        )
    viols.extend(_scan_brief_for_forbidden_sources(case))
    return (len(viols) == 0, viols)


def _scan_brief_for_forbidden_sources(case: BlindCase) -> list[str]:
    """Substring scan: if any string in
    ``forbidden_post_cutoff_sources`` appears inside the
    JSON-serialized brief, that's a known leak."""
    sources = case.pre_launch_input.forbidden_post_cutoff_sources or []
    if not sources:
        return []
    brief_text = json.dumps(
        case.pre_launch_input.pre_launch_brief,
        sort_keys=True,
        default=str,
    ).lower()
    viols: list[str] = []
    for src in sources:
        needle = (src or "").strip().lower()
        if needle and needle in brief_text:
            viols.append(
                f"forbidden_source={src!r} appears inside pre_launch_brief"
            )
    return viols


def _ensure_aware_date(d: date | datetime) -> date:
    """Normalize ``datetime`` to ``date`` (used by other callers if
    they want to compare across date/datetime boundaries)."""
    return d.date() if isinstance(d, datetime) else d
