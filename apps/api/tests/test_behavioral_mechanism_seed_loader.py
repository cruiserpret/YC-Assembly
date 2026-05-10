"""Phase 8.2D — seed loader integration tests.

Asserts that:
  - `seed_all` populates every table to the seed counts
  - `seed_all` is idempotent: re-running does NOT duplicate rows
  - reading helpers (`get_mechanisms_by_category`,
    `get_belief_rules_for_topic`, `get_persuasion_strategies`) return
    the expected rows
  - the seed catalog never inserts a `'strong'` belief rule
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from assembly.db import get_sessionmaker
from assembly.pipeline.behavioral_science.mechanism_library import (
    count_seeded,
    get_belief_rules_for_topic,
    get_mechanism_by_name,
    get_mechanisms_by_category,
    get_mechanisms_by_domain,
    get_persuasion_strategies,
    seed_all,
)
from assembly.pipeline.behavioral_science.seed_data import seed_summary


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _reset_async_engine_after_each_test() -> AsyncIterator[None]:
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:  # pragma: no cover
            pass
    db._engine = None
    db._sessionmaker = None


@pytest.mark.asyncio
async def test_seed_all_populates_all_tables() -> None:
    sessionmaker = get_sessionmaker()
    expected = seed_summary()
    summary = await seed_all(sessionmaker)
    assert summary == expected
    counts = await count_seeded(sessionmaker)
    # After seeding (with a fresh DB), each table is at least the seed
    # count; pre-existing rows from prior runs may push counts higher.
    assert counts["research_sources"] >= expected["research_sources"]
    assert counts["behavioral_mechanisms"] >= expected["behavioral_mechanisms"]
    assert counts["mechanism_evidence_links"] >= expected["evidence_links"]
    assert counts["persuasion_strategies"] >= expected["persuasion_strategies"]
    assert counts["belief_network_rules"] >= expected["belief_network_rules"]
    assert counts["applicability_rules"] >= expected["applicability_rules"]


@pytest.mark.asyncio
async def test_seed_all_is_idempotent() -> None:
    sessionmaker = get_sessionmaker()
    await seed_all(sessionmaker)
    counts_first = await count_seeded(sessionmaker)
    await seed_all(sessionmaker)
    counts_second = await count_seeded(sessionmaker)
    assert counts_first == counts_second, (
        f"seed_all is not idempotent.\nfirst:  {counts_first}\n"
        f"second: {counts_second}"
    )


@pytest.mark.asyncio
async def test_get_mechanisms_by_category_returns_persuasion_set() -> None:
    sessionmaker = get_sessionmaker()
    await seed_all(sessionmaker)
    rows = await get_mechanisms_by_category(sessionmaker, "persuasion")
    names = {r.name for r in rows}
    # Expected three persuasion-category mechanisms from the seed.
    assert "strategy_personalization" in names
    assert "logical_vs_emotional_appeal_balance" in names
    assert "inquiry_before_persuasion" in names


@pytest.mark.asyncio
async def test_get_mechanism_by_name_resolves() -> None:
    sessionmaker = get_sessionmaker()
    await seed_all(sessionmaker)
    row = await get_mechanism_by_name(sessionmaker, "evidence_linking_drives_change")
    assert row is not None
    assert row.category == "evidence_processing"


@pytest.mark.asyncio
async def test_get_mechanisms_by_domain_for_commerce() -> None:
    sessionmaker = get_sessionmaker()
    await seed_all(sessionmaker)
    rows = await get_mechanisms_by_domain(sessionmaker, "commerce")
    names = {r.name for r in rows}
    # Several mechanisms have 'commerce' applicability rules in the seed.
    assert "strategy_personalization" in names
    assert "evidence_linking_drives_change" in names


@pytest.mark.asyncio
async def test_get_belief_rules_for_topic_returns_pairs() -> None:
    sessionmaker = get_sessionmaker()
    await seed_all(sessionmaker)
    rules = await get_belief_rules_for_topic(sessionmaker, "price_sensitivity")
    assert len(rules) >= 1
    # And NONE of them carry strength='strong' — the DB CHECK and the
    # seed catalog both forbid it.
    assert all(r.allowed_inference_strength != "strong" for r in rules)


@pytest.mark.asyncio
async def test_get_persuasion_strategies_returns_full_set() -> None:
    sessionmaker = get_sessionmaker()
    await seed_all(sessionmaker)
    rows = await get_persuasion_strategies(sessionmaker)
    names = {r.strategy_name for r in rows}
    expected = {
        "logical_appeal", "emotional_appeal", "credibility_appeal",
        "personal_story", "self_modeling", "foot_in_the_door",
        "task_product_information", "source_related_inquiry",
        "task_related_inquiry", "personal_related_inquiry",
        "evidence_linking", "social_proof", "authority_signal",
        "peer_conformity_signal",
    }
    assert expected.issubset(names)
