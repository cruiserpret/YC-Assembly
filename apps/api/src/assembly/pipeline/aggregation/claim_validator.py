"""Phase 6.75 — claim-to-source binding validator.

Every Phase 7 claim that lands in `claims` rows must satisfy:
  1. `source_evidence_id` references a real `evidence_items` row (FK already
     enforces this; we re-check defensively here).
  2. `source_excerpt` appears verbatim (whitespace-normalized + lowercased)
     in the bound evidence's `content` (or in `metadata.source_excerpt` when
     content is empty).
  3. `basis` matches the source evidence's `kind`:
       - basis='direct'    → kind='direct'
       - basis='analogical' → kind='analogical'
     A direct claim cannot be backed by an analogical source.
  4. For `claim_type='contradiction'`, the bound source must have at least
     one outbound `contradicts` edge in `evidence_edges`. No orphan
     contradiction claims.

Per Correction 4, an inferred-only edge cannot back a final-report claim.
The validator does NOT inspect inferred edges; it inspects the claim's
direct binding to evidence_items. Inferred edges are visible at retrieval
time but never become the claim's source on their own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.evidence import EvidenceItem
from assembly.models.evidence_edge import EvidenceEdge


_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


@dataclass(frozen=True)
class ClaimViolation:
    rule_id: str
    detail: str


@dataclass(frozen=True)
class ClaimValidationResult:
    passed: bool
    violations: tuple[ClaimViolation, ...]


async def validate_claim(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    text: str,
    source_evidence_id: UUID,
    source_excerpt: str,
    claim_type: str,
    basis: str,
) -> ClaimValidationResult:
    """Run all four rules. Returns a structured result; caller decides
    whether to insert or reject."""
    violations: list[ClaimViolation] = []

    async with sessionmaker() as session:
        evidence = await session.get(EvidenceItem, source_evidence_id)
        if evidence is None or evidence.simulation_id != simulation_id:
            violations.append(
                ClaimViolation(
                    rule_id="claim.source_missing",
                    detail=f"source_evidence_id {source_evidence_id} not found in simulation {simulation_id}",
                )
            )
            # Without a source, no point checking the other rules.
            return ClaimValidationResult(passed=False, violations=tuple(violations))

        # Rule 2 — substring check on source_excerpt.
        haystack = _norm(evidence.content or "")
        if not haystack:
            haystack = _norm((evidence.metadata_ or {}).get("source_excerpt") or "")
        needle = _norm(source_excerpt)
        if needle and needle not in haystack:
            violations.append(
                ClaimViolation(
                    rule_id="claim.excerpt_not_in_source",
                    detail=(
                        f"source_excerpt does not appear in evidence "
                        f"{source_evidence_id} content"
                    ),
                )
            )

        # Rule 3 — basis must match source kind.
        if basis == "direct" and evidence.kind != "direct":
            violations.append(
                ClaimViolation(
                    rule_id="claim.basis_mismatch",
                    detail=(
                        f"basis='direct' but source evidence kind="
                        f"'{evidence.kind}' (must be 'direct')"
                    ),
                )
            )
        if basis == "analogical" and evidence.kind != "analogical":
            violations.append(
                ClaimViolation(
                    rule_id="claim.basis_mismatch",
                    detail=(
                        f"basis='analogical' but source evidence kind="
                        f"'{evidence.kind}' (must be 'analogical')"
                    ),
                )
            )

        # Rule 4 — orphan contradictions.
        if claim_type == "contradiction":
            edges = (
                await session.execute(
                    select(EvidenceEdge)
                    .where(EvidenceEdge.simulation_id == simulation_id)
                    .where(EvidenceEdge.source_evidence_id == source_evidence_id)
                    .where(EvidenceEdge.edge_type == "contradicts")
                )
            ).scalars().all()
            if not edges:
                violations.append(
                    ClaimViolation(
                        rule_id="claim.contradiction_orphan",
                        detail=(
                            "claim_type='contradiction' requires at least one "
                            "outbound 'contradicts' edge from the source evidence"
                        ),
                    )
                )

    return ClaimValidationResult(
        passed=not violations, violations=tuple(violations)
    )
