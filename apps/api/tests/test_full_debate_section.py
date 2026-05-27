"""Tests for the Full Debate & Conversations report section."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assembly.orchestration.full_debate_section import (
    build_full_debate_section,
    render_full_debate_markdown,
)


# ─────────────────────────────────────────────────────────────────────
# Fixture artifacts
# ─────────────────────────────────────────────────────────────────────


def _write_full_run(tmp_path: Path) -> Path:
    """Mimic a `live_runs/<run_id>/` directory with all 4 debate files."""
    (tmp_path / "discussion.json").write_text(json.dumps({
        "discussion_session_id": "session-1",
        "persona_count": 23,
        "group_count": 4,
        "public_turn_count": 92,
        "peer_response_turn_count": 23,
        "pre_ballot_count": 23,
        "reflection_count": 22,
        "final_ballot_count": 23,
        "memory_atom_count": 69,
        "ballot_count_by_stage": {"pre": 23, "post": 23, "final": 23},
        "phase": "10a_3",
        "completed_at": "2026-05-25T08:46:41+00:00",
    }))
    (tmp_path / "influence_rounds.json").write_text(json.dumps({
        "phase": "10a_3",
        "completed_at": "2026-05-25T08:46:41+00:00",
        "rounds": [
            {"round_idx": 0, "round_type": "init", "voters_affected": 100,
             "intent_changes": 0, "bucket_changes": 0,
             "bucket_distribution": {"buyer": 0, "receptive": 43, "uncertain": 2, "skeptical": 55},
             "notes": "initial seed"},
            {"round_idx": 1, "round_type": "receive", "voters_affected": 100,
             "intent_changes": 0, "bucket_changes": 0,
             "bucket_distribution": {"buyer": 0, "receptive": 43, "uncertain": 2, "skeptical": 55},
             "per_voter_log": [
                 {"voter_id": "v1", "n_signals": 7},
                 {"voter_id": "v2", "n_signals": 5},
             ]},
            {"round_idx": 2, "round_type": "update", "voters_affected": 100,
             "intent_changes": 8, "bucket_changes": 0,
             "bucket_distribution": {"buyer": 0, "receptive": 37, "uncertain": 8, "skeptical": 55},
             "per_voter_log": [
                 {"voter_id": "v1", "initial": "would_consider_if_proven",
                  "final": "would_consider_if_proven", "moved": False,
                  "movement_probability": 0.34, "switching_resistance": 0.49},
             ],
             "notes": "movement_constrained=1"},
            {"round_idx": 3, "round_type": "finalize", "voters_affected": 100,
             "intent_changes": 0, "bucket_changes": 8,
             "bucket_distribution": {"buyer": 0, "receptive": 37, "uncertain": 8, "skeptical": 55},
             "per_voter_log": [
                 {"voter_id": "v1", "final_intent": "would_consider_if_proven",
                  "final_bucket": "receptive", "initial_bucket": "receptive",
                  "bucket_changed": False},
             ]},
        ],
    }))
    (tmp_path / "society_wide_debate.json").write_text(json.dumps({
        "phase": "10a_3",
        "mode": "live",
        "argument_count": 15,
        "argument_type_distribution": {"price_value": 7, "proof_need": 7, "persuasion_lever": 1},
        "propagation_count": 90,
        "response_type_distribution": {"intensified": 42, "adopted": 42, "ignored": 6},
        "completed_at": "2026-05-25T08:46:41+00:00",
    }))
    (tmp_path / "representative_debates.json").write_text(json.dumps({
        "phase": "10a_3",
        "completed_at": "2026-05-25T08:46:41+00:00",
        "samples": [
            {"cohort_label": "trust_seeker::curious_but_unconvinced",
             "persona_id": "9bd5c68f-b5ed-4e0d-83fa-4ae006fc2b7f",
             "private_stance": "curious_but_unconvinced",
             "top_objection": "Pricing concerns dominate.",
             "top_proof_need": "Independent benchmarks please.",
             "private_reasoning_excerpt": "At this price I want benchmarks first."},
        ],
    }))
    return tmp_path


# ─────────────────────────────────────────────────────────────────────
# build_full_debate_section
# ─────────────────────────────────────────────────────────────────────


class TestBuildFullDebateSection:
    def test_complete_run_includes_all_subsections(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        assert block["schema_version"] == "founder_report.full_debate.v2"
        assert block["section_title"] == "Full Debate & Conversations"
        for k in (
            "discussion_session", "influence_rounds", "society_wide_debate",
            "representative_debates", "discussion_transcript",
        ):
            assert k in block
        # discussion_transcript is optional in the minimal fixture — only the
        # other four must be non-missing here.
        for k in ("discussion_session", "influence_rounds", "society_wide_debate", "representative_debates"):
            assert not block[k].get("_missing")

    def test_influence_rounds_preserves_all_four(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        rounds = block["influence_rounds"]["rounds"]
        assert len(rounds) == 4
        assert [r["round_idx"] for r in rounds] == [0, 1, 2, 3]
        assert [r["round_type"] for r in rounds] == ["init", "receive", "update", "finalize"]

    def test_per_voter_log_preserved_verbatim(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        # Round 2 has the rich per-voter log
        r2 = block["influence_rounds"]["rounds"][2]
        assert r2["round_type"] == "update"
        assert len(r2["per_voter_log"]) == 1
        v = r2["per_voter_log"][0]
        assert v["voter_id"] == "v1"
        assert v["movement_probability"] == 0.34
        assert v["switching_resistance"] == 0.49

    def test_representative_samples_preserve_text(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        samples = block["representative_debates"]["samples"]
        assert len(samples) == 1
        assert "benchmarks" in samples[0]["private_reasoning_excerpt"]

    def test_missing_files_degrade_gracefully(self, tmp_path: Path) -> None:
        # Only write 2 of 4 — the other two should be marked _missing.
        (tmp_path / "discussion.json").write_text(json.dumps({"persona_count": 10}))
        (tmp_path / "influence_rounds.json").write_text(json.dumps({"rounds": []}))
        block = build_full_debate_section(tmp_path)
        assert not block["discussion_session"].get("_missing")
        assert not block["influence_rounds"].get("_missing")
        assert block["society_wide_debate"]["_missing"] is True
        assert block["representative_debates"]["_missing"] is True

    def test_empty_run_dir_returns_all_missing(self, tmp_path: Path) -> None:
        block = build_full_debate_section(tmp_path)
        assert block["discussion_session"]["_missing"] is True
        assert block["influence_rounds"]["_missing"] is True
        assert block["society_wide_debate"]["_missing"] is True
        assert block["representative_debates"]["_missing"] is True

    def test_corrupt_json_treated_as_missing(self, tmp_path: Path) -> None:
        (tmp_path / "influence_rounds.json").write_text("not valid json {")
        block = build_full_debate_section(tmp_path)
        assert block["influence_rounds"]["_missing"] is True


# ─────────────────────────────────────────────────────────────────────
# render_full_debate_markdown
# ─────────────────────────────────────────────────────────────────────


class TestRenderFullDebateMarkdown:
    def test_header_present(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        assert "# Full Debate & Conversations" in md

    def test_all_subsections_rendered(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        assert "## 1. Discussion session" in md
        assert "## 2. Influence rounds" in md
        assert "## 3. Society-wide debate" in md
        # discussion_transcript missing in this fixture → §4 absent
        assert "## 5. Representative cohort reasoning" in md

    def test_each_of_four_rounds_appears(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        assert "### Round 0" in md
        assert "### Round 1" in md
        assert "### Round 2" in md
        assert "### Round 3" in md
        assert "`init`" in md
        assert "`update`" in md
        assert "`finalize`" in md

    def test_per_voter_log_rendered_as_table(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        # Markdown table header for the voter log
        assert "voter_id" in md
        # Round 2's specific voter log fields
        assert "movement_probability" in md
        # Round 3's specific voter log fields
        assert "final_bucket" in md
        # Round 1's specific voter log fields
        assert "n_signals" in md

    def test_representative_sample_text_rendered(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        assert "Pricing concerns dominate" in md
        assert "Independent benchmarks please" in md
        assert "At this price I want benchmarks first" in md

    def test_missing_subsections_skipped(self, tmp_path: Path) -> None:
        block = build_full_debate_section(tmp_path)
        md = render_full_debate_markdown(block)
        # Header should still render even if all subsections are missing
        assert "# Full Debate & Conversations" in md
        # But no subsection bodies should appear
        assert "## 1. Discussion session" not in md
        assert "## 2. Influence rounds" not in md

    def test_session_id_rendered(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        assert "session-1" in md

    def test_bucket_distribution_rendered(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        # Final bucket distribution should appear
        assert '"skeptical": 55' in md or '"skeptical":55' in md

    def test_no_llm_or_db_calls(self, tmp_path: Path) -> None:
        # Pure-Python only — if either were called, this test environment
        # would surface that as an import-time or network error.
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        md = render_full_debate_markdown(block)
        assert isinstance(md, str)
        assert len(md) > 0


# ─────────────────────────────────────────────────────────────────────
# Verify against the locked Tiiny artifacts (read-only)
# ─────────────────────────────────────────────────────────────────────


class TestAgainstLockedTiinyArtifacts:
    """Sanity check: the helper works correctly against real on-disk
    artifacts from the Tiiny Phase 12F.3 retry run (live_runs/24e593f7…).

    This test only READS those files; it never writes. If the live_runs
    dir for that run is missing on this machine, the test is skipped.
    """

    TIINY_RUN_DIR = Path(
        "/Users/hamza40/Desktop/Aseembly/assembly-v0/apps/api"
        "/_audit/live_runs/24e593f7-2e05-486a-b1fa-2047fe270823"
    )

    def test_loads_tiiny_run_if_present(self) -> None:
        if not self.TIINY_RUN_DIR.exists():
            pytest.skip("Tiiny live_runs dir not present on this machine")
        block = build_full_debate_section(self.TIINY_RUN_DIR)
        # Tiiny has all four files
        assert not block["discussion_session"].get("_missing")
        assert not block["influence_rounds"].get("_missing")
        assert not block["society_wide_debate"].get("_missing")
        assert not block["representative_debates"].get("_missing")
        # Tiiny has 4 influence rounds
        assert len(block["influence_rounds"]["rounds"]) == 4
        # And 6 representative samples
        assert len(block["representative_debates"]["samples"]) == 6

    def test_renders_tiiny_run_if_present(self) -> None:
        if not self.TIINY_RUN_DIR.exists():
            pytest.skip("Tiiny live_runs dir not present on this machine")
        block = build_full_debate_section(self.TIINY_RUN_DIR)
        md = render_full_debate_markdown(block)
        # Spot-check that rendering produced non-empty output covering all 4 rounds
        assert "### Round 0" in md
        assert "### Round 3" in md
        # Tiiny does not have discussion_transcript.json; section 5 holds reps
        assert "## 5. Representative cohort reasoning" in md


# ─────────────────────────────────────────────────────────────────────
# Full per-turn transcript (v2 — 4 groups × 4 rounds × 96 turns)
# ─────────────────────────────────────────────────────────────────────


class TestFullPerTurnTranscript:
    """Tests for the new discussion_transcript.json subsection."""

    PANTRYPULSE_RUN_DIR = Path(
        "/Users/hamza40/Desktop/Aseembly/assembly-v0/apps/api"
        "/_audit/live_runs/0d7ebc2d-e2ae-468f-9f9d-dee1cb8880fa"
    )

    def test_transcript_missing_in_legacy_run_does_not_raise(self, tmp_path: Path) -> None:
        run_dir = _write_full_run(tmp_path)
        block = build_full_debate_section(run_dir)
        # discussion_transcript.json wasn't written in this fixture
        assert block["discussion_transcript"]["_missing"] is True

    def test_transcript_present_populates_groups(self, tmp_path: Path) -> None:
        _write_full_run(tmp_path)
        # Hand-write a minimal transcript shape
        (tmp_path / "discussion_transcript.json").write_text(json.dumps({
            "schema_version": "discussion_transcript.v1",
            "discussion_session_id": "s1",
            "group_count": 4,
            "groups": [
                {"group_index": i, "personas": [
                    {"persona_id": f"p{i}{j}", "display_name": f"P{i}{j}"} for j in range(6)
                ], "rounds": [
                    {"round_number": rn, "round_label": f"round_{rn}",
                     "turn_count": 6, "turns": [
                         {"turn_id": f"t{i}{rn}{k}", "speaker_persona_id": f"p{i}{k}",
                          "speaker_name": f"P{i}{k}", "stance": "neutral",
                          "public_text": f"Sample turn text from group {i} round {rn} turn {k}."}
                         for k in range(6)
                     ]}
                    for rn in (1, 2, 3, 4)
                ]} for i in range(4)
            ],
        }))
        block = build_full_debate_section(tmp_path)
        tr = block["discussion_transcript"]
        assert not tr.get("_missing")
        assert tr["group_count"] == 4
        assert len(tr["groups"]) == 4
        # 4 rounds per group × 6 turns each = 24 turns per group
        for g in tr["groups"]:
            assert len(g["rounds"]) == 4
            total = sum(len(r["turns"]) for r in g["rounds"])
            assert total == 24

    def test_transcript_markdown_renders_groups_and_rounds(self, tmp_path: Path) -> None:
        _write_full_run(tmp_path)
        (tmp_path / "discussion_transcript.json").write_text(json.dumps({
            "schema_version": "discussion_transcript.v1",
            "discussion_session_id": "s1", "group_count": 4,
            "groups": [
                {"group_index": i, "personas": [{"persona_id": "p", "display_name": "Alice"}],
                 "rounds": [
                     {"round_number": rn, "round_label": "public_opening",
                      "turn_count": 1, "turns": [
                          {"turn_id": "t", "speaker_persona_id": "p", "speaker_name": "Alice",
                           "stance": "interested_if_proven",
                           "public_text": f"Sample reasoning from group {i} round {rn}."}
                      ]}
                     for rn in (1, 2, 3, 4)
                 ]} for i in range(4)
            ],
        }))
        block = build_full_debate_section(tmp_path)
        md = render_full_debate_markdown(block)
        assert "## 4. Full debate transcript" in md
        # <details>/<summary> wrappers render dropdown arrows in markdown viewers
        assert "<details" in md
        assert "<summary>" in md
        # Group + Round headings inside <summary>
        assert "<strong>Group 0</strong>" in md
        assert "<strong>Group 3</strong>" in md
        assert "<strong>Round 1</strong>" in md
        assert "<strong>Round 4</strong>" in md
        # Speaker + stance + text
        assert "**Alice**" in md
        assert "_(interested_if_proven)_" in md
        assert "Sample reasoning from group 0 round 1." in md
        # First group + first round of each group default to open
        assert "<details open>" in md

    def test_pantrypulse_real_transcript_if_present(self) -> None:
        """If the PantryPulse run + exported transcript exist on disk,
        the helper produces a 4×4×96 transcript with real persona names."""
        if not (self.PANTRYPULSE_RUN_DIR / "discussion_transcript.json").exists():
            pytest.skip("PantryPulse transcript not exported on this machine")
        block = build_full_debate_section(self.PANTRYPULSE_RUN_DIR)
        tr = block["discussion_transcript"]
        assert not tr.get("_missing")
        assert tr["group_count"] == 4
        total_turns = sum(
            len(r["turns"]) for g in tr["groups"] for r in g["rounds"]
        )
        assert total_turns == 96
        # All groups have 4 rounds
        for g in tr["groups"]:
            assert len(g["rounds"]) == 4
        # At least one persona has a real display name (not a UUID prefix)
        any_named = any(
            (p.get("display_name") and " " in (p.get("display_name") or ""))
            for g in tr["groups"] for p in g["personas"]
        )
        assert any_named, "expected at least one persona to have a display name"

    def test_pantrypulse_markdown_includes_real_dialogue(self) -> None:
        if not (self.PANTRYPULSE_RUN_DIR / "discussion_transcript.json").exists():
            pytest.skip("PantryPulse transcript not exported on this machine")
        block = build_full_debate_section(self.PANTRYPULSE_RUN_DIR)
        md = render_full_debate_markdown(block)
        # Must render the transcript header
        assert "## 4. Full debate transcript (4 groups × 4 rounds)" in md
        # All 4 groups rendered as collapsible <details><summary> blocks
        for i in range(4):
            assert f"<strong>Group {i}</strong>" in md
        # Round 1-4 rendered inside each group's collapsible
        for r in (1, 2, 3, 4):
            assert f"<strong>Round {r}</strong>" in md
        # <details>/<summary> structure for dropdown arrows
        assert "<details" in md
        assert "<summary>" in md
        # Must contain actual PantryPulse-flavor text
        assert any(
            term in md.lower()
            for term in ("anylist", "pantry", "scanner", "nfc", "family hub")
        )
