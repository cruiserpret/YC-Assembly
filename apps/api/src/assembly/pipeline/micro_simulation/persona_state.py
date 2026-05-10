"""Phase 8.2K — load `MicroPersonaState` from a `PersonaMatch`.

DB-aware: pulls PersonaTrait + PersonaEvidenceLink + SourceRecord
rows for the matched persona. Pure-function from there:

  * filters traits to direct / inferred only
  * binds evidence excerpts to the corresponding trait field
  * refuses to construct state from a `not_relevant` or
    `weakly_relevant` persona unless `include_weakly_relevant=True`
  * deterministically derives `initial_stance` from the persona's
    objection_patterns / trust_triggers / price_sensitivity content
"""
from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.persona import (
    PersonaEvidenceLink,
    PersonaRecord,
    PersonaTrait,
    SourceRecord,
)
from assembly.pipeline.audience_retrieval.schemas import PersonaMatch
from assembly.pipeline.micro_simulation.schemas import (
    MicroPersonaState,
    MicroRelevanceLabel,
    MicroStance,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification


_RELEVANT_LABELS: frozenset[str] = frozenset({
    RelevanceClassification.RELEVANT.value,
    RelevanceClassification.HIGHLY_RELEVANT.value,
})


class MicroPersonaStateLoadError(Exception):
    """Raised when the loader refuses to construct state."""


async def load_micro_persona_state(
    *,
    sessionmaker: async_sessionmaker,
    persona_match: PersonaMatch,
    include_weakly_relevant: bool = False,
) -> MicroPersonaState:
    """Load + bind a single persona's state.

    Refuses to construct state when:
      - the match's classification is not RELEVANT/HIGHLY_RELEVANT
        (unless include_weakly_relevant=True)
      - the persona has zero direct/inferred traits (cannot anchor)
    """
    if persona_match.classification.value not in _RELEVANT_LABELS:
        if not include_weakly_relevant:
            raise MicroPersonaStateLoadError(
                f"persona {persona_match.persona_id} classified "
                f"{persona_match.classification.value}; refusing to load "
                "without include_weakly_relevant=True."
            )
        if persona_match.classification.value != RelevanceClassification.WEAKLY_RELEVANT.value:
            raise MicroPersonaStateLoadError(
                f"persona {persona_match.persona_id} classified "
                f"{persona_match.classification.value}; not even weakly_relevant."
            )

    persona_uuid = UUID(persona_match.persona_id)
    async with sessionmaker() as session:
        traits = (await session.execute(
            select(PersonaTrait).where(PersonaTrait.persona_id == persona_uuid)
        )).scalars().all()
        links = (await session.execute(
            select(PersonaEvidenceLink).where(
                PersonaEvidenceLink.persona_id == persona_uuid
            )
        )).scalars().all()

    supported_traits: dict[str, str] = {}
    for t in traits:
        if t.support_level in ("direct", "inferred") and t.value:
            supported_traits[t.field_name] = t.value
    if not supported_traits:
        raise MicroPersonaStateLoadError(
            f"persona {persona_match.persona_id} has 0 direct/inferred "
            "traits; cannot anchor a micro-state."
        )

    # First excerpt per trait field — short, source-bound.
    evidence_excerpts: dict[str, str] = {}
    seen_fields: set[str] = set()
    for ln in links:
        if ln.contribution_field in seen_fields:
            continue
        seen_fields.add(ln.contribution_field)
        evidence_excerpts[ln.contribution_field] = (ln.excerpt or "")[:300]

    label = (
        MicroRelevanceLabel.HIGHLY_RELEVANT
        if persona_match.classification.value == RelevanceClassification.HIGHLY_RELEVANT.value
        else MicroRelevanceLabel.RELEVANT
        if persona_match.classification.value == RelevanceClassification.RELEVANT.value
        else MicroRelevanceLabel.WEAKLY_RELEVANT
    )

    initial_stance = _derive_initial_stance(supported_traits)

    caveats: list[str] = []
    if label is MicroRelevanceLabel.WEAKLY_RELEVANT:
        caveats.append(
            "below relevant threshold; included for mechanical breadth only"
        )

    return MicroPersonaState(
        persona_id=persona_match.persona_id,
        display_name=persona_match.display_name,
        relevance_label=label,
        matched_category_key=persona_match.matched_category_key,
        relevance_score=persona_match.relevance_score,
        supported_traits=supported_traits,
        evidence_excerpts=evidence_excerpts,
        initial_stance=initial_stance,
        current_stance=initial_stance,
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Deterministic baseline-stance heuristic
# ---------------------------------------------------------------------------


def _derive_initial_stance(traits: dict[str, str]) -> MicroStance:
    """Map the persona's source-bound traits to one of the closed
    stances. Deterministic — no LLM. Used by the baseline round."""
    blob = " | ".join(f"{k}:{v}" for k, v in traits.items()).lower()

    # Strongest signal: explicit hostility / distrust language.
    if any(
        marker in blob for marker in (
            "rip-off", "ridiculous", "would not recommend", "fed up",
            "burned by", "scam", "lock-in",
        )
    ):
        return MicroStance.RESISTANT

    # Next: explicit skepticism markers.
    if any(
        marker in blob for marker in (
            "skeptical", "skepticism", "don't trust", "concerned",
            "worried", "broken", "expensive", "overpriced", "bloat",
        )
    ):
        return MicroStance.SKEPTICAL

    # Confused: explicit overwhelm / complexity language.
    if any(
        marker in blob for marker in (
            "overwhelming", "overwhelmed", "too complicated",
            "frustrating", "frustrated",
        )
    ):
        return MicroStance.CONFUSED

    # Mildly-interested: explicit mild positive markers.
    if any(
        marker in blob for marker in (
            "would consider", "open to", "willing to try",
        )
    ):
        return MicroStance.MILDLY_INTERESTED

    # Default: curious-hesitant — neutral starting point.
    return MicroStance.CURIOUS_HESITANT
