"""Phase 10B.1 — verification script for the SoleNest fresh run.

Walks the J-checklist (10 criteria) + the four 10B.1 audit JSONs
and prints a green/red summary. Used to drive the operator report.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from assembly.db import get_sessionmaker
from assembly.models.assembly_run import AssemblyRun
from assembly.models.discussion import (
    DiscussionPrivateBallot, DiscussionTurn, DiscussionGroup,
    DiscussionSession,
)
from assembly.orchestration.live_founder_brief import _LIVE_RUNS_ROOT
from assembly.sources.product_grounding.caveat_leak import (
    PERSONA_FORBIDDEN_PHRASES,
)


_PRICE_RE = re.compile(
    r"\bwhat\s+(?:'s|is|does)\s+(?:the\s+)?(?:it\s+)?cost|"
    r"\bhow\s+much\s+(?:does\s+)?(?:it\s+)?cost",
    re.IGNORECASE,
)
_LAUNCH_RE = re.compile(
    r"\bis\s+(?:it|this)\s+(?:already\s+)?launched|"
    r"\bhas\s+(?:it|this)\s+launched",
    re.IGNORECASE,
)
_FAKE_USE_RE = re.compile(
    r"\b(?:i|we)\s+(?:bought|own|use|used|tried|tested)\s+"
    r"(?:the\s+|a\s+|an\s+|my\s+)?solenest\b",
    re.IGNORECASE,
)
_WRONG_CAT_RE = re.compile(
    r"\bsolenest\s+(?:is|seems|sounds)\s+(?:just\s+|like\s+|basically\s+)?"
    r"(?:a|an)\s+(?:shoe|insole|sock|footwear)\b",
    re.IGNORECASE,
)
_REPEATED_OPENERS = (
    "before i get excited",
    "i need to know",
    "until i see",
    "what would actually move me",
)


async def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_solenest_run.py <run_uuid>")
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

    # Load ballots + turns
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

    def _scan_any(rxs: list[re.Pattern[str]], texts: list[str]) -> int:
        n = 0
        for tx in texts:
            for rx in rxs:
                if rx.search(tx or ""):
                    n += 1
                    break
        return n

    checks: list[tuple[str, bool, str]] = []

    # J1 wrong-category drift
    wrong_cat_n = _scan_any([_WRONG_CAT_RE], turn_texts + ballot_texts)
    checks.append((
        "J1. agents do not mistake SoleNest for a shoe / insole / sock",
        wrong_cat_n == 0,
        f"violations={wrong_cat_n}",
    ))

    # J2 already-provided price
    price_n = _scan_any([_PRICE_RE], turn_texts + ballot_texts)
    checks.append((
        "J2. agents do not ask 'what's the price' as if unknown",
        price_n == 0,
        f"violations={price_n}",
    ))

    # J3 already-provided launch state
    launch_n = _scan_any([_LAUNCH_RE], turn_texts + ballot_texts)
    checks.append((
        "J3. agents do not ask 'is it launched' as if unknown",
        launch_n == 0,
        f"violations={launch_n}",
    ))

    # J4 repeated phrasing
    opener_hits = 0
    for opener in _REPEATED_OPENERS:
        for tx in turn_texts:
            if opener in (tx or "").lower():
                opener_hits += 1
    checks.append((
        "J4. repeated stock openers materially reduced",
        opener_hits <= 6,
        f"opener_hits={opener_hits} across {len(turn_texts)} turns",
    ))

    # J5 receptive labels are justified — check stance_calibration_quality
    cal = json.loads(
        (run_dir / "stance_calibration_quality.json").read_text(
            encoding="utf-8"
        )
    )
    checks.append((
        "J5+J6. stance calibration ran (receptive labels reviewed)",
        cal.get("ballots_reviewed", 0) > 0,
        f"reviewed={cal.get('ballots_reviewed')} corrected={cal.get('corrections_applied')}",
    ))

    # J7 no caveat leakage in ballots after repair
    leak_in_ballots = 0
    leaked_examples: list[str] = []
    for b in ballots:
        text = (b.private_reasoning or "").lower()
        for phrase in PERSONA_FORBIDDEN_PHRASES:
            if phrase in text:
                leak_in_ballots += 1
                if len(leaked_examples) < 3:
                    leaked_examples.append(
                        f"{b.ballot_stage}: …{(b.private_reasoning or '')[:120]}…"
                    )
                break
    checks.append((
        "J7. persona ballots do not contain system caveat leakage",
        leak_in_ballots == 0,
        f"leaks={leak_in_ballots}",
    ))

    # J8 fake usage of unlaunched product
    fake_n = _scan_any([_FAKE_USE_RE], turn_texts + ballot_texts)
    checks.append((
        "J8. no fake-usage claims for the unlaunched product",
        fake_n == 0,
        f"violations={fake_n}",
    ))

    # J9 report-level caveats still present
    report = json.loads(
        (run_dir / "founder_report.json").read_text(encoding="utf-8")
    )
    has_caveats = bool(report.get("caveats"))
    checks.append((
        "J9. report-level caveats remain visible",
        has_caveats,
        f"caveat_count={len(report.get('caveats') or [])}",
    ))

    # J10 grounding audit was emitted
    grounding = json.loads(
        (run_dir / "product_grounding_quality.json").read_text(encoding="utf-8")
    )
    checks.append((
        "J10. product_grounding_quality.json emitted",
        grounding.get("misunderstanding_count") is not None,
        f"misunderstanding_count={grounding.get('misunderstanding_count')}",
    ))

    # J11 diversity audit was emitted
    diversity = json.loads(
        (run_dir / "discussion_diversity_quality.json").read_text(encoding="utf-8")
    )
    checks.append((
        "J11. discussion_diversity_quality.json emitted",
        "persona_voice_diversity_score" in diversity,
        f"diversity_score={diversity.get('persona_voice_diversity_score')}",
    ))

    # J12 caveat leak audit was emitted
    leak_audit = json.loads(
        (run_dir / "persona_caveat_leak_quality.json").read_text(encoding="utf-8")
    )
    checks.append((
        "J12. persona_caveat_leak_quality.json emitted",
        "ballots_with_leak" in leak_audit,
        f"ballots_with_leak={leak_audit.get('ballots_with_leak')} "
        f"sentences_removed={leak_audit.get('sentences_removed')}",
    ))

    # Print
    print("=" * 72)
    print(f"SoleNest fresh-run verification — run_id={run_id}")
    print(f"run_dir={run_dir}")
    print("=" * 72)
    print(f"\nrun.status = {run.status}")
    print(f"run.current_stage = {run.current_stage}")
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
    print()
    if leaked_examples:
        print("Caveat-leak examples that survived:")
        for ex in leaked_examples:
            print(f"  - {ex}")
    print(
        f"\nPASS: {len(checks) - failed}/{len(checks)}  "
        f"FAIL: {failed}/{len(checks)}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
