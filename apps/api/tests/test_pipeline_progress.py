"""Tests for the Phase 6.5 Progress schema + status transitions."""
from __future__ import annotations

import pytest

from assembly.pipeline.progress import (
    IllegalStatusTransition,
    Progress,
    _validate_transition,
)


def test_progress_default_stage_is_pending() -> None:
    p = Progress()
    assert p.stage == "pending"
    assert p.total_rounds == 7


def test_progress_with_update_is_pure() -> None:
    p1 = Progress(stage="parsing")
    p2 = p1.with_update(stage="evidence_building", evidence_items_collected=3)
    assert p1.stage == "parsing"
    assert p2.stage == "evidence_building"
    assert p2.evidence_items_collected == 3
    assert p2.last_updated_at >= p1.last_updated_at


def test_progress_extra_fields_forbidden() -> None:
    with pytest.raises(Exception):
        Progress(stage="parsing", bogus_field=42)


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def test_pending_to_parsing_allowed() -> None:
    _validate_transition("pending", "parsing")


def test_pending_to_simulating_blocked() -> None:
    with pytest.raises(IllegalStatusTransition):
        _validate_transition("pending", "simulating")


def test_failed_can_resume_to_any_stage() -> None:
    """A failed simulation can be re-enqueued and resume from any stage."""
    for target in ("parsing", "evidence_building", "society_building", "simulating"):
        _validate_transition("failed", target)  # should not raise


def test_terminal_reported_blocks_further_transitions() -> None:
    with pytest.raises(IllegalStatusTransition):
        _validate_transition("reported", "simulating")


def test_unknown_target_status_blocked() -> None:
    with pytest.raises(IllegalStatusTransition):
        _validate_transition("pending", "magically_done")


def test_idempotent_self_transition_allowed() -> None:
    _validate_transition("simulating", "simulating")  # no-op
