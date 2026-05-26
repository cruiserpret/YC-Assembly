"""Full Debate & Conversations report section.

Reads the four debate-related artifacts that the pipeline already
persists to `live_runs/<run_id>/`:

  - `influence_rounds.json`  — 4 rounds of voter-level intent/bucket mechanics
  - `society_wide_debate.json` — cross-cohort argument propagation summary
  - `representative_debates.json` — text samples of cohort-level reasoning
  - `discussion.json` — session-level discussion metadata

…and produces a single structured `full_debate` block plus a matching
markdown rendering, both intended to be embedded in the downloaded
founder report so users see the complete debate transcript and not
just the aggregate summary.

This module is read-only with respect to disk: it only loads artifacts
the pipeline already wrote. It does not call the LLM, does not call
the DB, and does not mutate any of the underlying files.

Designed to degrade gracefully: if any of the four files is missing,
the corresponding subsection is omitted with an explicit `_missing`
flag rather than raising.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_DEBATE_FILES = {
    "discussion": "discussion.json",
    "influence_rounds": "influence_rounds.json",
    "society_wide_debate": "society_wide_debate.json",
    "representative_debates": "representative_debates.json",
    # discussion_transcript.json holds the full 4-groups × 4-rounds × 96-turn
    # per-utterance dialogue. Written to disk by the pipeline at simulation
    # finalize time (or by the export helper for legacy runs). Optional —
    # gracefully omitted if missing.
    "discussion_transcript": "discussion_transcript.json",
}


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def build_full_debate_section(run_dir: Path | str) -> dict[str, Any]:
    """Assemble the full_debate JSON block from artifacts in `run_dir`.

    The returned dict is structured for direct embedding under
    `founder_report.json["full_debate"]`. Missing source files yield
    subsections with `{"_missing": True}` so downstream renderers can
    handle gracefully.
    """
    run_path = Path(run_dir)
    discussion = _load(run_path / _DEBATE_FILES["discussion"])
    influence = _load(run_path / _DEBATE_FILES["influence_rounds"])
    society = _load(run_path / _DEBATE_FILES["society_wide_debate"])
    representative = _load(run_path / _DEBATE_FILES["representative_debates"])
    transcript = _load(run_path / _DEBATE_FILES["discussion_transcript"])

    out: dict[str, Any] = {
        "schema_version": "founder_report.full_debate.v2",
        "section_title": "Full Debate & Conversations",
        "discussion_session": _shape_discussion(discussion),
        "discussion_transcript": _shape_transcript(transcript),
        "influence_rounds": _shape_influence_rounds(influence),
        "society_wide_debate": _shape_society_wide_debate(society),
        "representative_debates": _shape_representative_debates(representative),
    }
    return out


def _shape_transcript(d: dict[str, Any] | None) -> dict[str, Any]:
    """Phase v2: shape the full 4-groups × 4-rounds × per-turn transcript.

    Pass-through serialization with minimal restructure: keep the
    full per-turn array so the downloaded report retains the exact
    dialogue from every persona in every round.
    """
    if d is None:
        return {"_missing": True}
    return {
        "schema_version": d.get("schema_version"),
        "group_count": d.get("group_count"),
        "groups": d.get("groups") or [],
    }


def _shape_discussion(d: dict[str, Any] | None) -> dict[str, Any]:
    if d is None:
        return {"_missing": True}
    return {
        "discussion_session_id": d.get("discussion_session_id"),
        "persona_count": d.get("persona_count"),
        "group_count": d.get("group_count"),
        "public_turn_count": d.get("public_turn_count"),
        "peer_response_turn_count": d.get("peer_response_turn_count"),
        "pre_ballot_count": d.get("pre_ballot_count"),
        "reflection_count": d.get("reflection_count"),
        "final_ballot_count": d.get("final_ballot_count"),
        "memory_atom_count": d.get("memory_atom_count"),
        "ballot_count_by_stage": d.get("ballot_count_by_stage"),
        "phase": d.get("phase"),
        "completed_at": d.get("completed_at"),
    }


def _shape_influence_rounds(d: dict[str, Any] | None) -> dict[str, Any]:
    if d is None:
        return {"_missing": True}
    rounds = d.get("rounds") or []
    return {
        "round_count": len(rounds),
        "rounds": [_shape_round(r) for r in rounds],
        "cluster_arguments": d.get("cluster_arguments"),
        "completed_at": d.get("completed_at"),
    }


def _shape_round(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "round_idx": r.get("round_idx"),
        "round_type": r.get("round_type"),
        "voters_affected": r.get("voters_affected"),
        "intent_changes": r.get("intent_changes"),
        "bucket_changes": r.get("bucket_changes"),
        "bucket_distribution": r.get("bucket_distribution"),
        "notes": r.get("notes"),
        "skeptic_transitions": r.get("skeptic_transitions"),
        # per_voter_log can be large; we include it verbatim so the
        # downloaded report contains the full voter-level transcript
        # as the user requested.
        "per_voter_log": r.get("per_voter_log") or [],
    }


def _shape_society_wide_debate(d: dict[str, Any] | None) -> dict[str, Any]:
    if d is None:
        return {"_missing": True}
    return {
        "phase": d.get("phase"),
        "mode": d.get("mode"),
        "argument_count": d.get("argument_count"),
        "argument_type_distribution": d.get("argument_type_distribution"),
        "propagation_count": d.get("propagation_count"),
        "response_type_distribution": d.get("response_type_distribution"),
        "completed_at": d.get("completed_at"),
        # Any list-of-dict items present (full arguments, propagations etc.)
        "arguments": d.get("arguments") or d.get("argument_list") or [],
        "propagations": d.get("propagations") or d.get("propagation_list") or [],
    }


def _shape_representative_debates(d: dict[str, Any] | None) -> dict[str, Any]:
    if d is None:
        return {"_missing": True}
    return {
        "phase": d.get("phase"),
        "completed_at": d.get("completed_at"),
        "notes": d.get("notes"),
        "samples": d.get("samples") or [],
    }


# ─────────────────────────────────────────────────────────────────────
# Markdown rendering
# ─────────────────────────────────────────────────────────────────────


def render_full_debate_markdown(section: dict[str, Any]) -> str:
    """Render the full_debate block as a markdown section.

    Designed to APPEND to the existing founder_report.md. Never replaces
    existing content. Missing subsections are silently skipped.
    """
    parts: list[str] = []
    parts.append("\n---\n")
    parts.append(f"# {section.get('section_title', 'Full Debate & Conversations')}\n")
    parts.append(
        "_This section surfaces the complete debate transcript across the "
        "four influence rounds, the cross-cohort argument propagation, "
        "and the representative cohort-level reasoning samples. It "
        "complements (not replaces) the aggregate debate summary "
        "earlier in this report._\n"
    )

    # 1. Discussion session metadata
    disc = section.get("discussion_session")
    if disc and not disc.get("_missing"):
        parts.append("\n## 1. Discussion session\n")
        parts.append(f"- Session ID: `{disc.get('discussion_session_id')}`")
        parts.append(f"- Persona count: {disc.get('persona_count')}")
        parts.append(f"- Group count: {disc.get('group_count')}")
        parts.append(f"- Public turns: {disc.get('public_turn_count')}")
        parts.append(f"- Peer response turns: {disc.get('peer_response_turn_count')}")
        parts.append(f"- Pre-ballots: {disc.get('pre_ballot_count')}")
        parts.append(f"- Reflections: {disc.get('reflection_count')}")
        parts.append(f"- Final ballots: {disc.get('final_ballot_count')}")
        parts.append(f"- Memory atoms recorded: {disc.get('memory_atom_count')}")
        bcs = disc.get("ballot_count_by_stage")
        if bcs:
            parts.append(f"- Ballot count by stage: `{json.dumps(bcs)}`")

    # 2. Influence rounds (the 4-round transcript)
    inf = section.get("influence_rounds")
    if inf and not inf.get("_missing"):
        parts.append(f"\n## 2. Influence rounds ({inf.get('round_count', 0)} rounds)\n")
        for r in inf.get("rounds") or []:
            parts.append(
                f"\n### Round {r.get('round_idx')} — `{r.get('round_type')}`\n"
            )
            parts.append(f"- Voters affected: {r.get('voters_affected')}")
            parts.append(f"- Intent changes: {r.get('intent_changes')}")
            parts.append(f"- Bucket changes: {r.get('bucket_changes')}")
            parts.append(
                f"- Bucket distribution: `{json.dumps(r.get('bucket_distribution') or {})}`"
            )
            if r.get("notes"):
                parts.append(f"- Notes: _{r.get('notes')}_")
            sk = r.get("skeptic_transitions")
            if sk:
                parts.append(f"- Skeptic transitions: `{json.dumps(sk)}`")
            pvl = r.get("per_voter_log") or []
            if pvl:
                parts.append(f"\n#### Per-voter transcript (n={len(pvl)})\n")
                # Determine column set from first non-empty entry
                cols = list(pvl[0].keys())
                # Render as a markdown table
                header = "| " + " | ".join(cols) + " |"
                separator = "|" + "|".join(["---"] * len(cols)) + "|"
                parts.append(header)
                parts.append(separator)
                for v in pvl:
                    row_cells = []
                    for c in cols:
                        val = v.get(c)
                        if isinstance(val, (dict, list)):
                            cell = f"`{json.dumps(val)}`"
                        elif val is None:
                            cell = ""
                        else:
                            cell = str(val).replace("|", "\\|")
                        row_cells.append(cell)
                    parts.append("| " + " | ".join(row_cells) + " |")

    # 3. Society-wide debate (cross-cohort)
    soc = section.get("society_wide_debate")
    if soc and not soc.get("_missing"):
        parts.append("\n## 3. Society-wide debate (cross-cohort)\n")
        parts.append(f"- Argument count: {soc.get('argument_count')}")
        atd = soc.get("argument_type_distribution")
        if atd:
            parts.append(f"- Argument type distribution: `{json.dumps(atd)}`")
        parts.append(f"- Propagation count: {soc.get('propagation_count')}")
        rtd = soc.get("response_type_distribution")
        if rtd:
            parts.append(f"- Response type distribution: `{json.dumps(rtd)}`")

        args = soc.get("arguments") or []
        if args:
            parts.append(f"\n### Arguments ({len(args)})\n")
            for i, a in enumerate(args):
                if isinstance(a, dict):
                    label = a.get("type") or a.get("argument_type") or "argument"
                    src = a.get("source") or a.get("from") or a.get("source_cohort") or ""
                    text = a.get("text") or a.get("argument") or a.get("summary") or ""
                    parts.append(f"- **{label}** ({src}): {text}")
                else:
                    parts.append(f"- {a}")

        props = soc.get("propagations") or []
        if props:
            parts.append(f"\n### Propagation events ({len(props)})\n")
            for p in props:
                if isinstance(p, dict):
                    src = p.get("source") or p.get("from") or ""
                    tgt = p.get("target") or p.get("to") or ""
                    response = p.get("response") or p.get("response_type") or ""
                    effect = p.get("effect_on_intent") or p.get("effect") or ""
                    parts.append(f"- {src} → {tgt}: response=`{response}` effect=`{effect}`")
                else:
                    parts.append(f"- {p}")

    # 4. Full per-turn transcript (4 groups × 4 rounds × all 96 turns)
    transcript = section.get("discussion_transcript")
    if transcript and not transcript.get("_missing"):
        groups = transcript.get("groups") or []
        parts.append(
            f"\n## 4. Full debate transcript ({len(groups)} groups × 4 rounds)\n"
        )
        parts.append(
            "_The actual public turns each persona spoke in their "
            "group, organized by group and by round. Every persona's "
            "voice for every round is shown verbatim._\n"
        )
        for g in groups:
            personas = g.get("personas") or []
            rounds = g.get("rounds") or []
            group_idx = g.get("group_index")
            # Outer <details> per group — collapsible dropdown in markdown
            # viewers. Group 0 stays open by default; the rest collapse.
            outer_open = " open" if group_idx == 0 else ""
            parts.append(
                f"\n<details{outer_open}>"
                f"\n<summary>"
                f"<strong>Group {group_idx}</strong> ({len(personas)} personas)"
                f"</summary>\n"
            )
            if personas:
                names = ", ".join(
                    p.get("display_name") or p.get("persona_id", "?")[:8]
                    for p in personas
                )
                parts.append(f"\n_Members: {names}_\n")
            for r in rounds:
                rn = r.get("round_number")
                rl = r.get("round_label")
                turns = r.get("turns") or []
                # Use <details> HTML so most markdown viewers (GitHub, VS Code,
                # Obsidian, Cursor, etc.) render this round as a collapsible
                # dropdown. Plain-text readers see the heading + content
                # inline. The first round of each group stays open by default.
                open_attr = " open" if rn == 1 else ""
                parts.append(
                    f"\n<details{open_attr}>"
                    f"\n<summary>"
                    f"<strong>Round {rn}</strong> — <code>{rl}</code> ({len(turns)} turns)"
                    f"</summary>\n"
                )
                for t in turns:
                    speaker = t.get("speaker_name") or "Unknown"
                    stance = t.get("stance") or ""
                    text = (t.get("public_text") or "").strip()
                    if not text:
                        continue
                    parts.append(
                        f"\n**{speaker}** _({stance})_:\n\n{text}\n"
                    )
                parts.append("\n</details>\n")
            # Close the outer group <details>
            parts.append("\n</details>\n")

    # 5. Representative debate samples (with text)
    rep = section.get("representative_debates")
    if rep and not rep.get("_missing"):
        samples = rep.get("samples") or []
        parts.append(f"\n## 5. Representative cohort reasoning ({len(samples)} samples)\n")
        for i, s in enumerate(samples, start=1):
            if not isinstance(s, dict):
                continue
            parts.append(f"\n### Sample {i} — `{s.get('cohort_label', '')}`\n")
            parts.append(f"- Persona ID: `{s.get('persona_id', '')}`")
            parts.append(f"- Private stance: `{s.get('private_stance', '')}`")
            top_obj = s.get("top_objection")
            if top_obj:
                if isinstance(top_obj, dict):
                    parts.append(f"- Top objection: {top_obj.get('text', '')}")
                else:
                    parts.append(f"- Top objection: {top_obj}")
            tpn = s.get("top_proof_need")
            if tpn:
                if isinstance(tpn, dict):
                    parts.append(f"- Top proof need: {tpn.get('text', '')}")
                else:
                    parts.append(f"- Top proof need: {tpn}")
            excerpt = s.get("private_reasoning_excerpt")
            if excerpt:
                parts.append(f"- Private reasoning excerpt:")
                parts.append(f"")
                parts.append(f"  > {excerpt}")

    parts.append(
        "\n_End of full debate section. This block was assembled from the "
        "pipeline's own debate artifacts; no new LLM calls were made to "
        "produce it._\n"
    )
    return "\n".join(parts) + "\n"


# ─────────────────────────────────────────────────────────────────────
# Auto-export hook: dump discussion_transcript.json from the live DB
# ─────────────────────────────────────────────────────────────────────


async def export_discussion_transcript_if_missing(
    *, sessionmaker: Any, run_dir: Path | str,
) -> Path | None:
    """Persist the full 4-groups × 4-rounds × N-turns transcript to disk
    for the current run, if it isn't already there.

    Reads discussion.json from `run_dir` to get the discussion_session_id,
    then queries the DB tables (`discussion_groups`, `discussion_turns`,
    `persona_records`) to assemble the same structure that
    `extract_persona_cards` / `representative_debates.json` follow at a
    higher level — only here we keep every public turn verbatim.

    The function is a no-op if `discussion_transcript.json` already
    exists, or if `discussion.json` is missing, or if the session has no
    groups in the DB. Errors are caught and logged; the caller does not
    need to fail the run if the transcript can't be exported.

    Returns the written path on success, None otherwise.
    """
    run_path = Path(run_dir)
    out_path = run_path / "discussion_transcript.json"
    if out_path.exists():
        return None  # already exported
    disc_path = run_path / "discussion.json"
    if not disc_path.exists():
        return None
    try:
        import uuid
        disc = json.loads(disc_path.read_text(encoding="utf-8"))
        sess_id_str = disc.get("discussion_session_id")
        if not sess_id_str:
            return None
        sess_id = uuid.UUID(sess_id_str)

        from sqlalchemy import select  # local import keeps the helper
        # importable without SQLAlchemy as a hard dep at module-load time

        # Use the project's own ORM models for safety + portability
        from assembly.models.discussion import (
            DiscussionGroup, DiscussionTurn,
        )
        from assembly.models.persona import PersonaRecord

        async with sessionmaker() as session:
            groups = (await session.execute(
                select(DiscussionGroup)
                .where(DiscussionGroup.discussion_session_id == sess_id)
                .order_by(DiscussionGroup.group_index.asc())
            )).scalars().all()
            if not groups:
                return None

            # Collect all persona ids across groups and look up display names
            all_pids: list[uuid.UUID] = []
            for g in groups:
                for pid in (g.persona_ids or []):
                    if pid:
                        all_pids.append(pid)
            persona_rows = (await session.execute(
                select(PersonaRecord).where(PersonaRecord.id.in_(all_pids))
            )).scalars().all() if all_pids else []
            pmap = {
                str(p.id): {
                    "display_name": getattr(p, "display_name", None),
                    "segment_label": getattr(p, "segment_label", None),
                }
                for p in persona_rows
            }

            transcript: dict[str, Any] = {
                "schema_version": "discussion_transcript.v1",
                "discussion_session_id": str(sess_id),
                "group_count": len(groups),
                "groups": [],
            }
            for g in groups:
                personas_in_group = [
                    {"persona_id": str(pid), **pmap.get(str(pid), {})}
                    for pid in (g.persona_ids or [])
                ]
                rounds_out = []
                # Discover distinct round numbers actually present
                round_nums_rows = (await session.execute(
                    select(DiscussionTurn.round_number)
                    .where(DiscussionTurn.discussion_group_id == g.id)
                    .distinct()
                    .order_by(DiscussionTurn.round_number.asc())
                )).all()
                round_nums = [r[0] for r in round_nums_rows]
                for rn in round_nums:
                    turn_rows = (await session.execute(
                        select(DiscussionTurn)
                        .where(DiscussionTurn.discussion_group_id == g.id)
                        .where(DiscussionTurn.round_number == rn)
                        .order_by(DiscussionTurn.turn_number.asc())
                    )).scalars().all()
                    turns_payload = []
                    for t in turn_rows:
                        sp = str(t.speaker_persona_id) if t.speaker_persona_id else None
                        turns_payload.append({
                            "turn_id": str(t.id),
                            "turn_number": t.turn_number,
                            "turn_type": t.turn_type,
                            "speaker_persona_id": sp,
                            "speaker_name":
                                pmap.get(sp, {}).get("display_name") if sp else None,
                            "speaker_segment":
                                pmap.get(sp, {}).get("segment_label") if sp else None,
                            "stance": t.stance,
                            "public_text": t.public_text,
                        })
                    label = turn_rows[0].turn_type if turn_rows else f"round_{rn}"
                    rounds_out.append({
                        "round_number": rn,
                        "round_label": label,
                        "turn_count": len(turns_payload),
                        "turns": turns_payload,
                    })
                transcript["groups"].append({
                    "group_index": g.group_index,
                    "personas": personas_in_group,
                    "rounds": rounds_out,
                })

        out_path.write_text(
            json.dumps(transcript, indent=2, default=str), encoding="utf-8",
        )
        return out_path
    except Exception:
        # Auto-export must NEVER fail the run; the section renderer
        # degrades gracefully when the file is absent.
        return None


__all__ = [
    "build_full_debate_section",
    "render_full_debate_markdown",
    "export_discussion_transcript_if_missing",
]
