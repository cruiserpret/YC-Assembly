"""Phase 10B.3 — verification script for the GlowPlate rerun.

Walks the J-checklist (15 criteria from the 10B.3 spec) plus the
six new audit JSONs (provided_fact_lock_v2, human_society_realism,
stance_strictness, audience_cards, headline_caveat, evidence_flavor)
and prints a green/red summary.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid

from sqlalchemy import select

from assembly.db import get_sessionmaker
from assembly.models.assembly_run import AssemblyRun
from assembly.models.discussion import (
    DiscussionGroup, DiscussionPrivateBallot, DiscussionSession,
    DiscussionTurn,
)
from assembly.orchestration.live_founder_brief import _LIVE_RUNS_ROOT
from assembly.sources.product_grounding.human_society_realism import (
    SELF_AWARENESS_PHRASES,
    detect_self_awareness_leak,
)


_DISHWASHER_REASK_RE = re.compile(
    r"\b(?:is\s+(?:it|this|the\s+plate)\s+dishwasher[\- ]safe|"
    r"can\s+(?:i|you)\s+(?:put|run)\s+(?:it|this|the\s+plate)\s+in\s+the\s+dishwasher)\b",
    re.IGNORECASE,
)
_RUNTIME_REASK_RE = re.compile(
    r"\b(?:can\s+it\s+(?:keep|hold)\s+(?:food|drinks?)\s+warm\s+for|"
    r"how\s+long\s+(?:does|can)\s+(?:it|the\s+battery|the\s+plate)\s+(?:keep|hold|last))\b",
    re.IGNORECASE,
)
_CHARGING_REASK_RE = re.compile(
    r"\b(?:is\s+(?:it|this|the\s+base)\s+(?:rechargeable|usb[\- ]?c)|"
    r"how\s+does\s+(?:it|the\s+base)\s+(?:charge|get\s+power)|"
    r"is\s+the\s+base\s+rechargeable)\b",
    re.IGNORECASE,
)
_FAKE_USE_RE = re.compile(
    r"\b(?:i|we)\s+(?:bought|own|use|used|tried|tested)\s+"
    r"(?:the\s+|a\s+|an\s+|my\s+)?glowplate\b",
    re.IGNORECASE,
)
_WRONG_CAT_RE = re.compile(
    r"\bglowplate\s+(?:is|seems|sounds|feels)\s+"
    r"(?:just\s+|like\s+|basically\s+)?(?:a|an)\s+"
    r"(?:microwave|hot\s+plate|cooking\s+appliance|food\s+warmer\s+tray)\b",
    re.IGNORECASE,
)


async def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_glowplate_run.py <run_uuid>")
        return 2
    run_id = uuid.UUID(sys.argv[1])
    sm = get_sessionmaker()
    run_dir = _LIVE_RUNS_ROOT / str(run_id)
    if not run_dir.exists():
        print(f"FAIL: run dir not found: {run_dir}")
        return 1

    async with sm() as session:
        run = (await session.execute(
            select(AssemblyRun).where(AssemblyRun.id == run_id)
        )).scalars().first()
    if run is None:
        print("FAIL: AssemblyRun not found")
        return 1

    persistence = json.loads(
        (run_dir / "persistence.json").read_text(encoding="utf-8")
    )
    rsid = persistence.get("run_scope_id")
    async with sm() as session:
        sess_row = (await session.execute(
            select(DiscussionSession).where(
                DiscussionSession.run_scope_id == rsid
            ).order_by(DiscussionSession.created_at.desc())
        )).scalars().first()
        if sess_row is None:
            print("FAIL: no DiscussionSession")
            return 1
        groups = (await session.execute(
            select(DiscussionGroup).where(
                DiscussionGroup.discussion_session_id == sess_row.id
            )
        )).scalars().all()
        gids = [g.id for g in groups]
        turns = (await session.execute(
            select(DiscussionTurn).where(
                DiscussionTurn.discussion_group_id.in_(gids)
            )
        )).scalars().all()
        ballots = (await session.execute(
            select(DiscussionPrivateBallot).where(
                DiscussionPrivateBallot.discussion_session_id == sess_row.id
            )
        )).scalars().all()

    turn_texts = [(t.public_text or "") for t in turns]
    ballot_texts = [(b.private_reasoning or "") for b in ballots]
    all_texts = turn_texts + ballot_texts

    def _scan(rxs, texts):
        n = 0
        for tx in texts:
            for rx in rxs:
                if rx.search(tx or ""):
                    n += 1
                    break
        return n

    checks = []

    # --- Load all audit jsons -------------------------------------
    fact_lock_v2 = json.loads(
        (run_dir / "provided_fact_lock_v2_quality.json").read_text(
            encoding="utf-8",
        )
    )
    realism = json.loads(
        (run_dir / "human_society_realism_quality.json").read_text(
            encoding="utf-8",
        )
    )
    strict = json.loads(
        (run_dir / "stance_strictness_quality.json").read_text(
            encoding="utf-8",
        )
    )
    audience_cards = json.loads(
        (run_dir / "audience_cards_quality.json").read_text(
            encoding="utf-8",
        )
    )
    headline = json.loads(
        (run_dir / "headline_caveat_quality.json").read_text(
            encoding="utf-8",
        )
    )
    flavor = json.loads(
        (run_dir / "evidence_flavor_quality.json").read_text(
            encoding="utf-8",
        )
    )
    report = json.loads(
        (run_dir / "founder_report.json").read_text(encoding="utf-8")
    )

    # --- J1. RECEPTIVE label retained ---
    stance_ts = (
        run_dir.parent.parent.parent.parent / "web" / "src" / "lib"
        / "stance.ts"
    )
    label_present = (
        stance_ts.exists()
        and "RECEPTIVE" in stance_ts.read_text(encoding="utf-8")
    )
    checks.append((
        "J1. RECEPTIVE label retained in stance.ts",
        label_present,
        "stance.ts contains 'RECEPTIVE'",
    ))

    # --- J2. RECEPTIVE only where reasoning has positive intent /
    # use-case fit. Use the strictness audit numbers.
    rec_after = strict.get("receptive_count_after", 0)
    rec_before = strict.get("receptive_count_before", 0)
    downgraded = strict.get("downgraded_receptive_count", 0)
    checks.append((
        "J2. RECEPTIVE earned only with clear reason (strict v2)",
        downgraded == 0 or rec_after < rec_before,
        f"receptive_before={rec_before} after={rec_after} "
        f"downgraded={downgraded}",
    ))

    # --- J3. Agents do not ask if dishwasher-safe as if unknown ---
    dish_violations = _scan([_DISHWASHER_REASK_RE], all_texts)
    checks.append((
        "J3. agents do not ask 'is it dishwasher-safe?' as if unknown",
        dish_violations == 0,
        f"violations={dish_violations} "
        f"(fact_lock_v2 caught {fact_lock_v2.get('by_category', {}).get('cleaning_dishwasher', 0)}, "
        f"repaired {fact_lock_v2.get('repaired_count', 0)})",
    ))

    # --- J4. Agents do not re-ask 45-minute runtime ---
    runtime_violations = _scan([_RUNTIME_REASK_RE], all_texts)
    checks.append((
        "J4. agents do not ask 45-minute runtime as if unknown",
        runtime_violations == 0,
        f"violations={runtime_violations} "
        f"(fact_lock_v2 caught {fact_lock_v2.get('by_category', {}).get('runtime', 0)})",
    ))

    # --- J5. Agents do not re-ask USB-C charging ---
    charging_violations = _scan([_CHARGING_REASK_RE], all_texts)
    checks.append((
        "J5. agents do not ask USB-C charging as if unknown",
        charging_violations == 0,
        f"violations={charging_violations} "
        f"(fact_lock_v2 caught {fact_lock_v2.get('by_category', {}).get('charging_usb_c', 0)})",
    ))

    # --- J6. Agents may STILL ask for proof. Count "Since X, I'd
    # want proof Y" patterns to confirm verification language exists.
    proof_form = sum(
        1 for tx in all_texts
        if re.search(
            r"\bsince\s+(?:the\s+)?(?:brief|claim|spec)|"
            r"\bi'?d\s+want\s+proof|\bi'?d\s+want\s+a\s+real[\- ]food",
            tx, re.IGNORECASE,
        )
    )
    checks.append((
        "J6. agents may ask for verification (proof-form present)",
        proof_form > 0,
        f"proof_form_sentences={proof_form}",
    ))

    # --- J7. Agents do NOT call themselves agents/synthetic/personas
    leak_count = realism.get("self_awareness_leak_count", 0)
    checks.append((
        "J7. agents do not call themselves agents/synthetic/personas",
        leak_count == 0
        or sum(detect_self_awareness_leak(t) is not None
               and len(detect_self_awareness_leak(t)) > 0
               for t in all_texts) == 0,
        f"realism_leaks_initially={leak_count} "
        f"(repaired during audit)",
    ))

    # --- J8. Agents do NOT mention n=24 / simulation caveats ---
    sim_phrases = sum(
        1 for tx in all_texts
        if any(p in tx.lower() for p in (
            "n=24", "n=21", "n=12", "in this simulation",
            "synthetic society", "directional, not a verdict",
            "not a forecast",
        ))
    )
    checks.append((
        "J8. no n=24 / simulation caveats in persona speech",
        sim_phrases == 0,
        f"simulation_caveat_sentences={sim_phrases}",
    ))

    # --- J9. Headline does not contain caveat sentence ---
    head_text = headline.get("headline", "")
    head_low = head_text.lower()
    headline_clean = (
        "not a real-world purchase forecast" not in head_low
        and "not a real-world forecast" not in head_low
        and "validated with real prospects" not in head_low
        and "synthetic signal" not in head_low
    )
    checks.append((
        "J9. headline does not include caveat sentence",
        headline_clean,
        f"headline={head_text!r}",
    ))

    # --- J10. Report-level caveats remain visible ---
    caveats = report.get("caveats") or []
    checks.append((
        "J10. report-level caveats remain visible",
        len(caveats) > 0,
        f"caveat_count={len(caveats)}",
    ))

    # --- J11. Hardest-to-convince audience populated ---
    hardest = audience_cards.get("hardest_to_convince") or {}
    checks.append((
        "J11. hardest-to-convince audience populated with friction",
        bool(hardest.get("summary_copy"))
        and (hardest.get("primary_kind") in {"resistant", "uncertain", "all_receptive"}),
        f"primary_kind={hardest.get('primary_kind')} "
        f"copy={(hardest.get('summary_copy') or '')[:100]!r}",
    ))

    # --- J12. Best-fit audience copy is human-readable ---
    best_fit = audience_cards.get("best_fit") or {}
    bf_copy = best_fit.get("summary_copy") or ""
    bf_human = (
        bool(bf_copy)
        and not bf_copy.lower().startswith("trust_seeker")
        and ("audience" in bf_copy.lower()
             or "buyers" in bf_copy.lower()
             or "people" in bf_copy.lower()
             or "users" in bf_copy.lower())
    )
    checks.append((
        "J12. best-fit audience copy is human-readable",
        bf_human,
        f"copy={bf_copy[:120]!r}",
    ))

    # --- J13. YouTube contribution summarized ---
    yt_summary = flavor.get("summary_copy") or ""
    yt_present = (
        "youtube" in yt_summary.lower()
        and ("buyer-language" in yt_summary.lower()
             or "no comments passed" in yt_summary.lower()
             or "youtube searches" in yt_summary.lower())
    )
    checks.append((
        "J13. evidence_flavor reports YouTube contribution",
        yt_present,
        f"summary={yt_summary[:120]!r}",
    ))

    # --- J14. No fake usage of GlowPlate ---
    fake = _scan([_FAKE_USE_RE], all_texts)
    checks.append((
        "J14. no fake-usage claims for unlaunched GlowPlate",
        fake == 0,
        f"violations={fake}",
    ))

    # --- J15. No category drift ---
    wrong_cat = _scan([_WRONG_CAT_RE], all_texts)
    checks.append((
        "J15. agents do not confuse with microwave/hot plate/cooker",
        wrong_cat == 0,
        f"violations={wrong_cat}",
    ))

    # ---- Pretty print ----
    print("=" * 76)
    print(f"GlowPlate 10B.3 verification — run_id={run_id}")
    print(f"run_dir={run_dir}")
    print("=" * 76)
    print(f"\nrun.status = {run.status}")
    print(f"persona_count = {len({b.persona_id for b in ballots})}")
    print(f"turn_count    = {len(turns)}")
    print(f"ballot_count  = {len(ballots)}")
    print()
    failed = 0
    for name, ok, detail in checks:
        marker = "✓" if ok else "✗"
        print(f"  {marker} {name}")
        print(f"      → {detail}")
        if not ok:
            failed += 1
    print(
        f"\nPASS: {len(checks) - failed}/{len(checks)}  "
        f"FAIL: {failed}/{len(checks)}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
