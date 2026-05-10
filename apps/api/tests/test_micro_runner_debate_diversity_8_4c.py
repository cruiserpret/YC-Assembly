"""Phase 8.4C — tests for the diversity-aware debate-pair selector
added to `run_micro_simulation`.

Covers:
  1. Legacy parity: max_debate_turns=2 returns the bidirectional
     pair between states[0] and states[1] (Phase 8.2K behavior).
  2. max_debate_turns=0 returns no pairs (debate fully disabled).
  3. max_debate_turns=10 with a CORE+ADJACENT mixed audience picks
     CORE-vs-ADJACENT pairs first.
  4. No persona speaks more than `max_turns // 2` times.
  5. No self-pair (a == b).
  6. Selection is deterministic for the same input.
  7. Bidirectional pairing: each picked pair appears as both
     (a, b) and (b, a) consecutively.

NO LIVE LLM. NO DB. Pure call to the deterministic selector helper.
"""
from __future__ import annotations

from assembly.pipeline.micro_simulation.runner import (
    _select_diverse_debate_pairs,
)
from assembly.pipeline.micro_simulation.schemas import (
    MicroPersonaState, MicroRelevanceLabel, MicroStance,
)


def _make_state(
    pid: str, label: str, category: str,
) -> MicroPersonaState:
    return MicroPersonaState(
        persona_id=pid,
        display_name=pid,
        relevance_label=MicroRelevanceLabel(label),
        matched_category_key=category,
        relevance_score=30,
        supported_traits={"current_pain": "x"},
        evidence_excerpts={"current_pain": "y"},
        initial_stance=MicroStance.CURIOUS_HESITANT,
        current_stance=MicroStance.CURIOUS_HESITANT,
    )


def _audience_7_core_2_adj() -> list[MicroPersonaState]:
    return [
        _make_state("c1", "RELEVANT", "competitor_user_a"),
        _make_state("c2", "RELEVANT", "competitor_user_b"),
        _make_state("c3", "RELEVANT", "substitute_user_x"),
        _make_state("c4", "RELEVANT", "substitute_user_x"),
        _make_state("c5", "RELEVANT", "substitute_user_y"),
        _make_state("c6", "RELEVANT", "substitute_user_y"),
        _make_state("c7", "RELEVANT", "use_case_z"),
        _make_state("a1", "WEAKLY_RELEVANT", "objection_safety"),
        _make_state("a2", "WEAKLY_RELEVANT", "buyer_type_premium"),
    ]


# ---------------------------------------------------------------------------
# 1. Legacy parity: max=2
# ---------------------------------------------------------------------------


def test_max_2_returns_legacy_bidirectional_pair() -> None:
    states = _audience_7_core_2_adj()
    pairs = _select_diverse_debate_pairs(states=states, max_turns=2)
    assert pairs == [(0, 1), (1, 0)]


def test_max_1_returns_single_direction_legacy() -> None:
    states = _audience_7_core_2_adj()
    pairs = _select_diverse_debate_pairs(states=states, max_turns=1)
    assert pairs == [(0, 1)]


# ---------------------------------------------------------------------------
# 2. max=0 returns no pairs
# ---------------------------------------------------------------------------


def test_max_0_returns_no_pairs() -> None:
    states = _audience_7_core_2_adj()
    assert _select_diverse_debate_pairs(states=states, max_turns=0) == []


# ---------------------------------------------------------------------------
# 3. max=10 with mixed audience prefers CORE-vs-ADJACENT first
# ---------------------------------------------------------------------------


def test_max_10_picks_core_vs_adjacent_first() -> None:
    states = _audience_7_core_2_adj()
    pairs = _select_diverse_debate_pairs(states=states, max_turns=10)
    # First two debate turns must be a (CORE, ADJACENT) pair followed
    # by its reverse direction.
    speaker_idx, target_idx = pairs[0]
    assert states[speaker_idx].relevance_label.value == "RELEVANT"
    assert states[target_idx].relevance_label.value == "WEAKLY_RELEVANT"
    # Reverse direction follows immediately:
    assert pairs[1] == (target_idx, speaker_idx)


# ---------------------------------------------------------------------------
# 4. Speak-cap: no persona speaks more than max_turns // 2 times
# ---------------------------------------------------------------------------


def test_no_persona_speaks_more_than_cap_times() -> None:
    states = _audience_7_core_2_adj()
    pairs = _select_diverse_debate_pairs(states=states, max_turns=10)
    speak_counts: dict[int, int] = {}
    for s, _ in pairs:
        speak_counts[s] = speak_counts.get(s, 0) + 1
    cap = 10 // 2
    for idx, c in speak_counts.items():
        assert c <= cap, (
            f"persona {idx} speaks {c} times; cap is {cap}"
        )


# ---------------------------------------------------------------------------
# 5. No self-pair
# ---------------------------------------------------------------------------


def test_no_self_pairs() -> None:
    states = _audience_7_core_2_adj()
    pairs = _select_diverse_debate_pairs(states=states, max_turns=10)
    for a, b in pairs:
        assert a != b


# ---------------------------------------------------------------------------
# 6. Determinism
# ---------------------------------------------------------------------------


def test_same_input_yields_same_pairs() -> None:
    states_a = _audience_7_core_2_adj()
    states_b = _audience_7_core_2_adj()
    p_a = _select_diverse_debate_pairs(states=states_a, max_turns=10)
    p_b = _select_diverse_debate_pairs(states=states_b, max_turns=10)
    assert p_a == p_b


# ---------------------------------------------------------------------------
# 7. Bidirectional pairing structure
# ---------------------------------------------------------------------------


def test_pairs_emitted_bidirectionally_in_sequence() -> None:
    states = _audience_7_core_2_adj()
    pairs = _select_diverse_debate_pairs(states=states, max_turns=10)
    assert len(pairs) % 2 == 0
    for i in range(0, len(pairs), 2):
        a, b = pairs[i]
        assert pairs[i + 1] == (b, a), (
            f"pair {i} {pairs[i]} not followed by reverse "
            f"direction; got {pairs[i + 1]}"
        )


# ---------------------------------------------------------------------------
# 8. Tier-edge cases
# ---------------------------------------------------------------------------


def test_only_core_personas_falls_back_to_diverse_categories() -> None:
    """When every persona is CORE (no ADJACENT), Phase 1 has nothing
    to bind, so Phase 2 fills with novel-category CORE-CORE pairs."""
    states = [
        _make_state("c1", "RELEVANT", "category_a"),
        _make_state("c2", "RELEVANT", "category_b"),
        _make_state("c3", "RELEVANT", "category_c"),
        _make_state("c4", "RELEVANT", "category_d"),
    ]
    pairs = _select_diverse_debate_pairs(states=states, max_turns=4)
    assert len(pairs) == 4
    # No two pairs may share both categories (would mean two same-cat
    # personas debating).
    seen_pair_keys: set[frozenset[str]] = set()
    for a, b in pairs[::2]:  # forward direction only
        cat_a = states[a].matched_category_key
        cat_b = states[b].matched_category_key
        assert cat_a != cat_b
        seen_pair_keys.add(frozenset((cat_a, cat_b)))
