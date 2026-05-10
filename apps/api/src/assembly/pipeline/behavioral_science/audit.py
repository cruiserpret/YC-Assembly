"""Phase 8.2D — `mechanism_initialization_audit` write surface.

The single blessed function that persists a `PersonaMechanismProfile`
into `mechanism_initialization_audit`. Phase 8.2H+'s UI audit panel
reads these rows.

This module DOES NOT call LLM providers, network APIs, or write persona
rows. It only writes the audit table. The drift test
`test_no_drift_behavioral_science.py::test_audit_writes_only_audit_table`
asserts the rule structurally.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.behavioral_mechanism import MechanismInitializationAudit
from assembly.pipeline.behavioral_science.initializer import (
    PersonaMechanismProfile,
)


async def write_mechanism_initialization_audit(
    sessionmaker: async_sessionmaker,
    *,
    profile: PersonaMechanismProfile,
    persona_id: UUID | None = None,
    simulation_id: UUID | None = None,
    notes: str | None = None,
) -> MechanismInitializationAudit:
    """Persist the profile as a `mechanism_initialization_audit` row.

    Returns the inserted ORM row. The function flushes inside a
    transaction so the caller gets a usable copy without keeping the
    session open.
    """
    row = MechanismInitializationAudit(
        persona_id=persona_id,
        simulation_id=simulation_id,
        applied_mechanisms=[
            {
                "mechanism_id": str(m.mechanism_id),
                "name": m.name,
                "category": m.category,
                "strength": m.strength,
                "domain_label": m.domain_label,
                "rationale": m.rationale,
            }
            for m in profile.applied_mechanisms
        ],
        skipped_mechanisms=[
            {
                "mechanism_id": (
                    str(m.mechanism_id) if m.mechanism_id is not None else None
                ),
                "name": m.name,
                "reason_code": m.reason_code,
                "reason_message": m.reason_message,
            }
            for m in profile.skipped_mechanisms
        ],
        applied_belief_rules=[
            {
                "rule_id": str(r.rule_id),
                "topic_a": r.topic_a,
                "topic_b": r.topic_b,
                "relation_type": r.relation_type,
                "allowed_inference_strength": r.allowed_inference_strength,
                "notes": r.notes,
            }
            for r in profile.applied_belief_rules
        ],
        anti_pattern_warnings=list(profile.anti_pattern_warnings),
        evidence_outranked_priors=profile.evidence_outranked_priors,
        notes=notes if notes is not None else profile.notes,
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(row)
            await session.flush()
            inserted_id = row.id

    # Re-load detached so the caller can inspect after the session closes.
    async with sessionmaker() as session:
        from sqlalchemy import select
        return (
            await session.execute(
                select(MechanismInitializationAudit).where(
                    MechanismInitializationAudit.id == inserted_id
                )
            )
        ).scalar_one()
