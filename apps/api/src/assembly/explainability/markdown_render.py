"""Phase 12F.1 — additive markdown renderer.

Appends three sub-sections to the founder report markdown:

  ## 7. Why Assembly predicted this        (explainability panel)
  ## 8. Representative persona reasoning   (persona reasoning cards)
  ## 9. Niche signals worth investigating  (niche signals panel)

This module is INTENTIONALLY additive — the legacy
`render_intent_and_debate_report_markdown` output is preserved
verbatim by the orchestrator, and this section appends after it.

Discipline:
  * No chain-of-thought / raw_output / reasoning exposed.
  * Every claim references a structured `evidence_anchor` or
    `triggered_by` field from the input dicts.
  * Sparse / missing data is rendered as "not provided" rather than
    hidden — founder sees what's missing.
  * No fake certainty: confidence cap (0.85) and `limited_by` are
    surfaced inline.
  * No new LLM calls.
"""
from __future__ import annotations

from typing import Any


def _md_escape(text: Any) -> str:
    """Lightweight markdown safety for free text — escapes the few
    sequences that would break list/section rendering. Does NOT do
    full HTML escaping (markdown allows angle brackets, etc.)."""
    s = "" if text is None else str(text)
    # Normalize newlines; markdown lines must stay on a single line
    # when they live inside a `-` bullet.
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    # Soften triple-backticks so a quoted code fence inside a quote
    # doesn't accidentally open a fenced block.
    s = s.replace("```", "`​``")
    return s.strip()


def _fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_or_missing(value: Any, missing_label: str = "not provided") -> str:
    if value is None:
        return f"_{missing_label}_"
    if isinstance(value, str) and not value.strip():
        return f"_{missing_label}_"
    if isinstance(value, (list, dict, tuple, set)) and not value:
        return f"_{missing_label}_"
    return _md_escape(value)


def _render_explainability(panel: dict[str, Any]) -> list[str]:
    """Render the `explainability` block as markdown lines."""
    if not panel:
        return [
            "## 7. Why Assembly predicted this",
            "",
            "_Explainability panel not present in this run._",
            "",
        ]
    lines: list[str] = ["## 7. Why Assembly predicted this", ""]

    # Header — decision being tested + what would change founder mind
    decision = panel.get("decision_being_tested")
    change_mind = panel.get("what_would_change_founder_mind")
    lines += [
        "**Decision being tested:** " + _fmt_or_missing(decision),
        "",
        "**What would change the founder's mind:** "
        + _fmt_or_missing(change_mind),
        "",
    ]

    # Inputs used
    iu = panel.get("inputs_used") or {}
    provided = iu.get("fields_provided") or []
    missing = iu.get("fields_missing") or []
    n_provided = iu.get("n_provided", len(provided))
    n_total = iu.get("n_total_optional", len(provided) + len(missing))
    lines += [
        "### Inputs used",
        "",
        f"- Optional fields provided: **{n_provided} / {n_total}**",
    ]
    if provided:
        lines.append("- Provided: " + ", ".join(
            f"`{_md_escape(f)}`" for f in provided
        ))
    if missing:
        lines.append("- Missing: " + ", ".join(
            f"`{_md_escape(f)}`" for f in missing
        ))
    lines.append("")

    # Source audience profile
    sap = panel.get("source_audience_profile") or {}
    lines += [
        "### Source audience profile",
        "",
        f"- Profile used: `{_md_escape(sap.get('profile_used'))}`",
        f"- Rationale: {_fmt_or_missing(sap.get('rationale'))}",
    ]
    role_mix = sap.get("role_mix_pct") or {}
    if role_mix:
        lines.append("- Role mix (calibration-stage priors):")
        for role, pct in role_mix.items():
            lines.append(f"  - `{_md_escape(role)}`: {float(pct):.1f}%")
    lines.append("")

    # Persona composition
    pc = panel.get("persona_composition") or {}
    lines += [
        "### Persona composition",
        "",
        f"- Total personas in scorable + non-scorable pool: "
        f"**{pc.get('n_total', 'n/a')}**",
        f"- Synthetic non-customer voices (Phase 12E): "
        f"**{pc.get('n_synthetic_non_customer_voices', 0)}**",
    ]
    by_role = pc.get("by_audience_role") or {}
    if by_role:
        lines.append("- By audience role:")
        for role, n in sorted(by_role.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - `{_md_escape(role)}`: {n}")
    lines.append("")

    # Evidence snapshot
    es = panel.get("evidence_snapshot") or {}
    lines += ["### Evidence snapshot", ""]
    if not es.get("snapshot_present"):
        lines.append(
            f"- {_fmt_or_missing(es.get('note'), 'no snapshot attached')}"
        )
    else:
        lines += [
            f"- snapshot_id: `{_md_escape(es.get('evidence_snapshot_id'))}`",
            f"- raw_result_count: {es.get('raw_result_count', 'n/a')}",
            f"- accepted_evidence_count: "
            f"{es.get('accepted_evidence_count', 'n/a')}",
        ]
        by_source = es.get("by_source") or {}
        if by_source:
            lines.append("- By source:")
            for src, n in by_source.items():
                lines.append(f"  - `{_md_escape(src)}`: {n}")
    lines.append("")

    # Assumptions in play
    aip = panel.get("assumptions_in_play") or []
    lines += ["### Assumptions in play", ""]
    if not aip:
        lines.append("_No specific assumptions flagged for this run._")
    else:
        for a in aip:
            lines.append(
                f"- **{_md_escape(a.get('id'))}** — "
                f"{_md_escape(a.get('statement'))} "
                f"_(impact: {_md_escape(a.get('impact'))})_"
            )
    lines.append("")

    # Bucket explanations
    be = panel.get("bucket_explanations") or {}
    lines += ["### Bucket explanations", ""]
    if not be:
        lines.append(
            "_Bucket-level drivers / blockers not derivable from this "
            "run's intent drafts._"
        )
    else:
        for bucket in ("buyer", "receptive", "uncertain", "skeptical"):
            row = be.get(bucket) or {}
            count = row.get("count", 0)
            pct = row.get("pct", 0.0)
            drivers = row.get("top_drivers") or []
            blockers = row.get("top_blockers") or []
            anchors = row.get("evidence_anchors_sample") or []
            lines += [
                f"#### `{bucket}` — {count} persona(s) · {_fmt_pct(pct)}",
            ]
            if drivers:
                lines.append("- Top drivers:")
                for d in drivers[:5]:
                    lines.append(
                        f"  - {_md_escape(d.get('text'))} "
                        f"(raised by {d.get('raised_by_count', 0)})"
                    )
            else:
                lines.append("- Top drivers: _none recorded_")
            if blockers:
                lines.append("- Top blockers:")
                for b in blockers[:5]:
                    lines.append(
                        f"  - {_md_escape(b.get('text'))} "
                        f"(raised by {b.get('raised_by_count', 0)})"
                    )
            else:
                lines.append("- Top blockers: _none recorded_")
            if anchors:
                lines.append("- Evidence anchors (sample):")
                for a in anchors[:3]:
                    lines.append(f"  - `{_md_escape(a)}`")
            lines.append("")

    # Confidence
    conf = panel.get("confidence") or {}
    lines += ["### Confidence", ""]
    if not conf:
        lines.append("_Confidence block not present._")
    else:
        level = conf.get("level", "n/a")
        score = conf.get("score", 0.0)
        cap = conf.get("cap")
        cap_applied = conf.get("cap_applied", False)
        cap_note = (
            f" _(capped at {cap})_" if cap_applied
            else f" _(cap: {cap})_" if cap else ""
        )
        lines += [
            f"- Level: **{_md_escape(level)}**",
            f"- Score: **{float(score):.3f}**{cap_note}",
        ]
        limited_by = conf.get("limited_by") or []
        # The 12F.1 invariant: limited_by MUST be non-empty.
        if limited_by:
            lines.append("- Limited by:")
            for entry in limited_by:
                lines.append(f"  - `{_md_escape(entry)}`")
        would_increase = conf.get("would_increase_if") or []
        if would_increase:
            lines.append("- Would increase if:")
            for entry in would_increase:
                lines.append(f"  - {_md_escape(entry)}")
        breakdown = conf.get("breakdown") or {}
        if breakdown:
            lines.append("- Breakdown (per factor, weighted):")
            weights = conf.get("weights") or {}
            for factor, value in breakdown.items():
                w = weights.get(factor)
                wnote = f" · weight {w}" if w is not None else ""
                lines.append(
                    f"  - `{_md_escape(factor)}`: "
                    f"{float(value):.3f}{wnote}"
                )
    lines.append("")
    return lines


def _stance_arrow(initial: str | None, final: str | None) -> str:
    initial_s = _md_escape(initial) or "?"
    final_s = _md_escape(final) or "?"
    if initial_s == final_s:
        return f"`{initial_s}` (no change)"
    return f"`{initial_s}` → `{final_s}`"


def _render_anchored_field(label: str, field: dict[str, Any] | None) -> str:
    if not field:
        return f"- {label}: _not provided_"
    text = field.get("text") if isinstance(field, dict) else None
    anchor = field.get("evidence_anchor") if isinstance(field, dict) else None
    if not text:
        return f"- {label}: _not provided_"
    if not anchor:
        # Structural invariant: anchored fields should always carry an
        # anchor. Surface the omission rather than hide it.
        return (
            f"- {label}: {_md_escape(text)} _(evidence_anchor missing)_"
        )
    return f"- {label}: {_md_escape(text)} _(anchor: `{_md_escape(anchor)}`)_"


def _render_persona_cards(cards: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ["## 8. Representative persona reasoning", ""]
    if not cards:
        lines += [
            "_No representative persona reasoning cards produced for this "
            "run. This can happen on pre-Phase-12E runs without "
            "augmented intent drafts, or when no persona had both pre "
            "and final ballots with sourced evidence._",
            "",
        ]
        return lines

    lines.append(
        f"_Cards: {len(cards)} representative persona(s), stratified by "
        "audience role with one shifter + one sticky per role where "
        "possible. Every claim references a structured anchor._"
    )
    lines.append("")
    for i, card in enumerate(cards, start=1):
        pid = _md_escape(card.get("persona_id"))
        role = _md_escape(card.get("audience_role"))
        segment = _md_escape(card.get("segment_label"))
        is_synth = card.get("is_synthetic_non_customer_voice", False)
        synth_tag = " · _synthetic non-customer voice_" if is_synth else ""
        lines.append(
            f"### Card {i} — `{role}` / `{segment}`{synth_tag}"
        )
        lines.append(f"- persona_id: `{pid}`")
        lines.append(
            f"- Stance: {_stance_arrow(card.get('initial_stance'), card.get('final_stance'))}"
        )
        lines.append(
            f"- Final bucket: **{_md_escape(card.get('final_bucket'))}**"
        )
        lines.append(_render_anchored_field(
            "Top objection", card.get("top_objection"),
        ))
        lines.append(_render_anchored_field(
            "Top proof need", card.get("top_proof_need"),
        ))
        # What moved or failed to move them
        movement = card.get("what_moved_or_failed_to_move_them") or {}
        movement_summary = movement.get("summary")
        movement_trigger = movement.get("triggered_by_kind")
        movement_anchor = movement.get("evidence_anchor")
        if movement_summary:
            anchor_clause = (
                f" _(anchor: `{_md_escape(movement_anchor)}`)_"
                if movement_anchor else " _(anchor missing)_"
            )
            trigger_clause = (
                f" [trigger: `{_md_escape(movement_trigger)}`]"
                if movement_trigger else ""
            )
            lines.append(
                f"- What moved / failed to move them: "
                f"{_md_escape(movement_summary)}"
                f"{trigger_clause}{anchor_clause}"
            )
        else:
            lines.append("- What moved / failed to move them: _not provided_")
        lines.append(_render_anchored_field(
            "Adoption trigger", card.get("adoption_trigger"),
        ))
        lines.append(_render_anchored_field(
            "Stayed in bucket because", card.get("stayed_x_because"),
        ))
        lines.append(
            "- Confidence in this persona: "
            f"`{_md_escape(card.get('confidence_in_this_persona'))}`"
        )
        bucket_note = card.get("bucket_routing_note")
        if bucket_note:
            lines.append(
                f"- Bucket routing note: `{_md_escape(bucket_note)}`"
            )
        lines.append("")
    return lines


def _render_niche_signals(signals: dict[str, Any]) -> list[str]:
    lines: list[str] = ["## 9. Niche signals worth investigating", ""]
    if not signals:
        lines += [
            "_Niche signals panel not produced for this run._",
            "",
        ]
        return lines

    minorities = signals.get("minority_objections") or []
    unexpected = signals.get("unexpected_segments") or []
    edge_cases = signals.get("edge_case_use_cases") or []
    one_q = signals.get("one_question_for_real_customers")

    # Minority objections
    lines += ["### Minority objections", ""]
    if not minorities:
        lines.append(
            "_No minority objections cleared the threshold (1-3 raisers "
            "across ≥2 audience roles, with anchored evidence)._"
        )
    else:
        for m in minorities[:10]:
            roles = m.get("raised_by_roles") or []
            anchors = m.get("evidence_anchors") or []
            lines.append(
                "- **" + _md_escape(m.get("representative_text")) + "**"
            )
            lines.append(
                f"  - Raised by: {m.get('raised_by_count', 0)} persona(s)"
                f" across roles {', '.join(f'`{_md_escape(r)}`' for r in roles)}"
            )
            if anchors:
                lines.append(
                    f"  - Evidence anchors: "
                    + ", ".join(
                        f"`{_md_escape(a)}`" for a in anchors[:3]
                    )
                )
            else:
                lines.append("  - Evidence anchors: _missing_")
    lines.append("")

    # Unexpected segments
    lines += ["### Unexpected micro-segments", ""]
    if not unexpected:
        lines.append(
            "_No micro-segment diverges from the global bucket "
            "distribution by ≥0.25 TVD with ≥3 personas in the cohort._"
        )
    else:
        for u in unexpected[:5]:
            tvd = u.get("diverges_from_global_by_tvd", 0.0)
            lines.append(
                f"- **{_md_escape(u.get('cohort_label'))}** "
                f"(n={u.get('n_personas', 0)}, "
                f"Δ_TVD = {float(tvd):.3f})"
            )
            cd = u.get("bucket_distribution_pct") or {}
            gd = u.get("global_bucket_distribution_pct") or {}
            if cd:
                lines.append(
                    "  - Cohort distribution: "
                    + ", ".join(
                        f"{k}={float(v):.1f}%" for k, v in cd.items()
                    )
                )
            if gd:
                lines.append(
                    "  - Global distribution: "
                    + ", ".join(
                        f"{k}={float(v):.1f}%" for k, v in gd.items()
                    )
                )
            anchors = u.get("evidence_anchors") or []
            if anchors:
                lines.append(
                    "  - Evidence anchors: "
                    + ", ".join(
                        f"`{_md_escape(a)}`" for a in anchors[:3]
                    )
                )
            hint = u.get("interpretation_hint")
            if hint:
                lines.append(f"  - _{_md_escape(hint)}_")
    lines.append("")

    # Edge-case use cases
    lines += ["### Edge-case use cases", ""]
    if not edge_cases:
        lines.append(
            "_No edge-case use cases surfaced (each unique condition "
            "must come from exactly one persona with an anchored "
            "evidence_basis)._"
        )
    else:
        for ec in edge_cases[:8]:
            lines.append(
                f"- {_md_escape(ec.get('use_case'))}"
                f" _(persona `{_md_escape(ec.get('raised_by_persona_id'))}`,"
                f" anchor: `{_md_escape(ec.get('evidence_anchor'))}`)_"
            )
    lines.append("")

    # One question to ask real customers
    lines += ["### One question for real customers", ""]
    if not one_q:
        lines.append(
            "_No standout question surfaced — either all minority "
            "objections were already in your `known_objections` list, "
            "or no minority objection cleared the threshold._"
        )
    else:
        lines.append(f"> {_md_escape(one_q)}")
    lines.append("")

    caveat = signals.get("_caveat")
    if caveat:
        lines.append(f"_{_md_escape(caveat)}_")
        lines.append("")
    return lines


def render_12f1_markdown_section(
    *,
    explainability: dict[str, Any] | None,
    persona_cards: list[dict[str, Any]] | None,
    niche_signals: dict[str, Any] | None,
) -> str:
    """Render the Phase 12F.1 founder-readable section as a single
    markdown string. Intended to be appended to the existing
    `founder_report.md` AFTER the legacy renderer output.

    Inputs may be empty / None — sparse data renders as explicit
    "not provided" lines, never as silent hiding.
    """
    lines: list[str] = [
        "",
        "---",
        "",
        "# Phase 12F.1 — Trust, Reasoning & Niche Signals",
        "",
        "_The sections below are aggregations of artifacts the "
        "pipeline already produced — no additional LLM calls were "
        "made to generate them. Every claim cites a structured "
        "evidence anchor or trigger; raw LLM reasoning is never "
        "exposed._",
        "",
    ]
    lines += _render_explainability(explainability or {})
    lines += _render_persona_cards(persona_cards or [])
    lines += _render_niche_signals(niche_signals or {})
    return "\n".join(lines)
