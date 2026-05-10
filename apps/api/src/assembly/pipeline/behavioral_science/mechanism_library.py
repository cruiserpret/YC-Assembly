"""Phase 8.2D — DB-backed library service.

Read + seed surface for the behavioral mechanism catalog. NO LLM calls,
NO network calls, NO persona writes. Only:

  - `seed_all`               idempotent seed loader (research_sources +
                             mechanisms + evidence_links + strategies +
                             belief_rules + applicability_rules).
  - `get_mechanism_by_name`  resolve a single mechanism by `name`.
  - `get_mechanisms_by_category`
  - `get_mechanisms_by_domain`
  - `get_belief_rules_for_topic`
  - `get_persuasion_strategies`
  - `count_seeded`           used by tests as a drift check.

`seed_all` is idempotent: re-running it does NOT duplicate rows. Existing
rows are matched by stable natural keys (research_sources.title,
behavioral_mechanisms.name, persuasion_strategy_taxonomy.strategy_name,
belief_network_rules.(topic_a, topic_b, relation_type),
mechanism_applicability_rules.(mechanism_id, domain_label),
mechanism_evidence_links.(mechanism_id, research_source_id, support_type)).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.behavioral_mechanism import (
    BehavioralMechanism,
    BeliefNetworkRule,
    MechanismApplicabilityRule,
    MechanismEvidenceLink,
    PersuasionStrategyTaxonomy,
    ResearchSource,
)
from assembly.pipeline.behavioral_science.seed_data import (
    SEED_APPLICABILITY_RULES,
    SEED_BELIEF_RULES,
    SEED_MECHANISMS,
    SEED_SOURCES,
    SEED_STRATEGIES,
    seed_summary,
)
from assembly.pipeline.behavioral_science.validator import (
    validate_applicability_rule_payload,
    validate_belief_rule_payload,
    validate_evidence_link_payload,
    validate_mechanism_payload,
    validate_persuasion_strategy_payload,
    validate_research_source_payload,
)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


class SeedValidationFailed(Exception):
    """Raised when a seed payload fails its validator. Contains the
    structured violation list so debugging is straightforward."""

    def __init__(self, where: str, violations: tuple) -> None:
        self.where = where
        self.violations = violations
        super().__init__(
            f"seed validation failed at {where}: "
            + "; ".join(
                f"{v.rule_id}@{v.field_path}" for v in violations[:5]
            )
        )


async def seed_all(sessionmaker: async_sessionmaker) -> dict[str, int]:
    """Insert all seeded behavioral-science rows. Idempotent.

    Returns a dict matching `seed_summary()` so tests can assert counts
    without re-walking the DB. Validates every payload BEFORE insert; a
    failed validator aborts seeding (no partial commits within a single
    table due to the per-table session/transaction structure).
    """
    # Validate seeds up-front — surface bugs in the seed catalog itself
    # before we touch the database.
    for s in SEED_SOURCES:
        r = validate_research_source_payload({
            "title": s.title,
            "authors": s.authors,
            "year": s.year,
            "source_type": s.source_type,
            "citation": s.citation,
            "notes": s.notes,
        })
        if not r.passed:
            raise SeedValidationFailed(f"research_source[{s.key}]", r.violations)

    for m in SEED_MECHANISMS:
        r = validate_mechanism_payload({
            "name": m.name,
            "category": m.category,
            "description": m.description,
            "when_to_apply": m.when_to_apply,
            "when_not_to_apply": m.when_not_to_apply,
            "default_strength": m.default_strength,
            "status": m.status,
        })
        if not r.passed:
            raise SeedValidationFailed(f"mechanism[{m.key}]", r.violations)

    for st in SEED_STRATEGIES:
        r = validate_persuasion_strategy_payload({
            "strategy_name": st.name,
            "description": st.description,
            "research_source_id": "00000000-0000-0000-0000-000000000001",  # validator only checks presence
        })
        if not r.passed:
            raise SeedValidationFailed(
                f"strategy[{st.name}]", r.violations,
            )

    for br in SEED_BELIEF_RULES:
        r = validate_belief_rule_payload({
            "topic_a": br.topic_a,
            "topic_b": br.topic_b,
            "relation_type": br.relation_type,
            "allowed_inference_strength": br.allowed_inference_strength,
            "research_source_id": "00000000-0000-0000-0000-000000000001",
        })
        if not r.passed:
            raise SeedValidationFailed(
                f"belief_rule[{br.topic_a}->{br.topic_b}]", r.violations,
            )

    # ---- Source rows -------------------------------------------------
    source_id_by_key: dict[str, UUID] = {}
    async with sessionmaker() as session:
        async with session.begin():
            existing_titles = {
                row.title: row.id
                for row in (
                    await session.execute(select(ResearchSource))
                ).scalars().all()
            }
            for s in SEED_SOURCES:
                if s.title in existing_titles:
                    source_id_by_key[s.key] = existing_titles[s.title]
                    continue
                row = ResearchSource(
                    title=s.title,
                    authors=s.authors,
                    year=s.year,
                    source_type=s.source_type,
                    citation=s.citation,
                    notes=s.notes,
                )
                session.add(row)
                await session.flush()
                source_id_by_key[s.key] = row.id

    # ---- Mechanism rows ----------------------------------------------
    mechanism_id_by_key: dict[str, UUID] = {}
    async with sessionmaker() as session:
        async with session.begin():
            existing_names = {
                row.name: row.id
                for row in (
                    await session.execute(select(BehavioralMechanism))
                ).scalars().all()
            }
            for m in SEED_MECHANISMS:
                if m.name in existing_names:
                    mechanism_id_by_key[m.key] = existing_names[m.name]
                    continue
                row = BehavioralMechanism(
                    name=m.name,
                    category=m.category,
                    description=m.description,
                    when_to_apply=m.when_to_apply,
                    when_not_to_apply=m.when_not_to_apply,
                    default_strength=Decimal(str(m.default_strength)),
                    status=m.status,
                )
                session.add(row)
                await session.flush()
                mechanism_id_by_key[m.key] = row.id

    # ---- Mechanism evidence links -----------------------------------
    async with sessionmaker() as session:
        async with session.begin():
            existing_keys: set[tuple[UUID, UUID, str]] = {
                (row.mechanism_id, row.research_source_id, row.support_type)
                for row in (
                    await session.execute(select(MechanismEvidenceLink))
                ).scalars().all()
            }
            for m in SEED_MECHANISMS:
                mid = mechanism_id_by_key[m.key]
                for src_key, support_type, excerpt in m.sources:
                    sid = source_id_by_key[src_key]
                    if (mid, sid, support_type) in existing_keys:
                        continue
                    payload = {
                        "mechanism_id": mid,
                        "research_source_id": sid,
                        "support_type": support_type,
                        "excerpt_or_summary": excerpt,
                    }
                    r = validate_evidence_link_payload(payload)
                    if not r.passed:
                        raise SeedValidationFailed(
                            f"evidence_link[{m.name}->{src_key}]",
                            r.violations,
                        )
                    session.add(MechanismEvidenceLink(**payload))

    # ---- Persuasion strategies --------------------------------------
    async with sessionmaker() as session:
        async with session.begin():
            existing_strategy_names = {
                row.strategy_name
                for row in (
                    await session.execute(select(PersuasionStrategyTaxonomy))
                ).scalars().all()
            }
            for st in SEED_STRATEGIES:
                if st.name in existing_strategy_names:
                    continue
                session.add(PersuasionStrategyTaxonomy(
                    strategy_name=st.name,
                    description=st.description,
                    research_source_id=source_id_by_key[st.source_key],
                    usage_notes=st.usage_notes,
                ))

    # ---- Belief network rules ---------------------------------------
    async with sessionmaker() as session:
        async with session.begin():
            existing_belief_keys: set[tuple[str, str, str]] = {
                (row.topic_a, row.topic_b, row.relation_type)
                for row in (
                    await session.execute(select(BeliefNetworkRule))
                ).scalars().all()
            }
            for br in SEED_BELIEF_RULES:
                key = (br.topic_a, br.topic_b, br.relation_type)
                if key in existing_belief_keys:
                    continue
                session.add(BeliefNetworkRule(
                    topic_a=br.topic_a,
                    topic_b=br.topic_b,
                    relation_type=br.relation_type,
                    allowed_inference_strength=br.allowed_inference_strength,
                    notes=br.notes,
                    research_source_id=source_id_by_key[br.source_key],
                ))

    # ---- Applicability rules ----------------------------------------
    async with sessionmaker() as session:
        async with session.begin():
            existing_app_keys: set[tuple[UUID, str]] = {
                (row.mechanism_id, row.domain_label)
                for row in (
                    await session.execute(select(MechanismApplicabilityRule))
                ).scalars().all()
            }
            for ar in SEED_APPLICABILITY_RULES:
                mid = mechanism_id_by_key[ar.mechanism_key]
                if (mid, ar.domain_label) in existing_app_keys:
                    continue
                payload = {
                    "mechanism_id": mid,
                    "domain_label": ar.domain_label,
                    "applies_when": ar.applies_when,
                }
                r = validate_applicability_rule_payload(payload)
                if not r.passed:
                    raise SeedValidationFailed(
                        f"applicability[{ar.mechanism_key}/{ar.domain_label}]",
                        r.violations,
                    )
                session.add(MechanismApplicabilityRule(
                    mechanism_id=mid,
                    domain_label=ar.domain_label,
                    applies_when=ar.applies_when,
                    notes=ar.notes,
                    research_source_id=(
                        source_id_by_key[ar.source_key]
                        if ar.source_key
                        else None
                    ),
                ))

    return seed_summary()


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def get_mechanism_by_name(
    sessionmaker: async_sessionmaker, name: str,
) -> BehavioralMechanism | None:
    async with sessionmaker() as session:
        return (
            await session.execute(
                select(BehavioralMechanism).where(
                    BehavioralMechanism.name == name
                )
            )
        ).scalar_one_or_none()


async def get_mechanisms_by_category(
    sessionmaker: async_sessionmaker, category: str,
) -> Sequence[BehavioralMechanism]:
    async with sessionmaker() as session:
        return (
            await session.execute(
                select(BehavioralMechanism).where(
                    BehavioralMechanism.category == category
                )
            )
        ).scalars().all()


async def get_mechanisms_by_domain(
    sessionmaker: async_sessionmaker, domain_label: str,
) -> Sequence[BehavioralMechanism]:
    """Return mechanisms that have an applicability rule for this domain."""
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(BehavioralMechanism)
                .join(
                    MechanismApplicabilityRule,
                    MechanismApplicabilityRule.mechanism_id
                    == BehavioralMechanism.id,
                )
                .where(MechanismApplicabilityRule.domain_label == domain_label)
            )
        ).scalars().all()
    return rows


async def get_belief_rules_for_topic(
    sessionmaker: async_sessionmaker, topic: str,
) -> Sequence[BeliefNetworkRule]:
    """Return belief rules where `topic` participates as either side."""
    async with sessionmaker() as session:
        return (
            await session.execute(
                select(BeliefNetworkRule).where(
                    (BeliefNetworkRule.topic_a == topic)
                    | (BeliefNetworkRule.topic_b == topic)
                )
            )
        ).scalars().all()


async def get_persuasion_strategies(
    sessionmaker: async_sessionmaker,
) -> Sequence[PersuasionStrategyTaxonomy]:
    async with sessionmaker() as session:
        return (
            await session.execute(select(PersuasionStrategyTaxonomy))
        ).scalars().all()


async def count_seeded(sessionmaker: async_sessionmaker) -> dict[str, int]:
    """Count rows per behavioral-science table — drift check used by tests."""
    out: dict[str, int] = {}
    async with sessionmaker() as session:
        out["research_sources"] = len(
            (await session.execute(select(ResearchSource))).scalars().all()
        )
        out["behavioral_mechanisms"] = len(
            (await session.execute(select(BehavioralMechanism))).scalars().all()
        )
        out["mechanism_evidence_links"] = len(
            (await session.execute(select(MechanismEvidenceLink))).scalars().all()
        )
        out["persuasion_strategies"] = len(
            (
                await session.execute(select(PersuasionStrategyTaxonomy))
            ).scalars().all()
        )
        out["belief_network_rules"] = len(
            (await session.execute(select(BeliefNetworkRule))).scalars().all()
        )
        out["applicability_rules"] = len(
            (
                await session.execute(select(MechanismApplicabilityRule))
            ).scalars().all()
        )
    return out
