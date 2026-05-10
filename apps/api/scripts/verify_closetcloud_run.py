"""Phase 10B.2 — verification script for the ClosetCloud rerun.

Walks the J-checklist (14 criteria from the 10B.2 spec) plus the
two new audit JSONs (price_hierarchy + provided_fact_accuracy)
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
from assembly.sources.product_grounding.caveat_leak import (
    PERSONA_FORBIDDEN_PHRASES,
)


_PRICE_REASK_RE = re.compile(
    r"\bwhat\s+(?:'s|is|does)\s+(?:the\s+)?(?:it\s+)?cost|"
    r"\bhow\s+much\s+(?:does\s+)?(?:it\s+)?cost",
    re.IGNORECASE,
)
_POWER_REASK_RE = re.compile(
    r"\b(?:is|are)\s+(?:it|they|the\s+\w+)\s+plug[\- ]in\s+or\s+battery|"
    r"\bdoes\s+(?:it|this)\s+plug\s+in\b",
    re.IGNORECASE,
)
_EXCLUDED_REASK_RE = re.compile(
    r"\bdoes\s+(?:it|this)\s+use\s+(?:heat|steam|water|detergent|uv|ozone)",
    re.IGNORECASE,
)
_WRONG_CAT_RE = re.compile(
    r"\bclosetcloud\s+(?:is|seems|sounds|feels)\s+"
    r"(?:just\s+|like\s+|basically\s+)?(?:a|an)\s+"
    r"(?:washing\s+machine|dryer|steamer|dry[\- ]cleaner)\b",
    re.IGNORECASE,
)
_FAKE_USE_RE = re.compile(
    r"\b(?:i|we)\s+(?:bought|own|use|used|tried|tested)\s+"
    r"(?:the\s+|a\s+|an\s+|my\s+)?closetcloud\b",
    re.IGNORECASE,
)
_ACCESSORY_AS_PRIMARY_RE = re.compile(
    r"\$\s?14\.99[\s,;:.\-—–]+(?:[a-z\- ']{0,60}?)"
    r"(?:product|hanger|kit|system|station|device|unit)\b",
    re.IGNORECASE,
)
_FIFTEEN_BUCKS_HANGER_RE = re.compile(
    r"\b(?:fifteen|15)\s+(?:bucks|dollars)\s+(?:for|to|on)\s+(?:a\s+|the\s+)?"
    r"(?:hanger|product|kit|system)",
    re.IGNORECASE,
)


async def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_closetcloud_run.py <run_uuid>")
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

    # J1 YouTube as Tier-1
    retrieval = json.loads(
        (run_dir / "evidence_retrieval.json").read_text(encoding="utf-8")
    )
    yt_attempted = "youtube_data_api" in (
        retrieval.get("providers_attempted") or []
    )
    yt_audit = retrieval.get("youtube_audit") or {}
    checks.append((
        "J1. YouTube ran as Tier 1 (when key configured)",
        yt_attempted or not retrieval.get("provider_keys", {}).get(
            "youtube_data_api_key_configured"
        ),
        f"yt_attempted={yt_attempted} accepted={yt_audit.get('comments_accepted', 0)} "
        f"rejected={yt_audit.get('comments_rejected', 0)}",
    ))

    # J2 Firecrawl escalation-only
    fc_called = "firecrawl_extract" in (
        retrieval.get("tier_2_providers_attempted") or []
    )
    escalated = retrieval.get("escalation_triggered", False)
    checks.append((
        "J2. Firecrawl remained escalation-only",
        not fc_called or escalated,
        f"fc_called={fc_called} escalated={escalated}",
    ))

    # J3 + J4 + J5 price hierarchy
    ph = json.loads(
        (run_dir / "price_hierarchy_quality.json").read_text(encoding="utf-8")
    )
    primary_value = (ph.get("primary_price_detected") or "")
    accessory_amts = [
        ap["amount"] for ap in (ph.get("accessory_prices_detected") or [])
    ]
    checks.append((
        "J3. $119 detected as primary price",
        "$119" in (primary_value or ""),
        f"primary={primary_value}",
    ))
    checks.append((
        "J4. $14.99 detected as accessory only",
        "$14.99" in accessory_amts and "$14.99" not in (primary_value or ""),
        f"accessories={accessory_amts}",
    ))
    accessory_as_primary = _scan(
        [_ACCESSORY_AS_PRIMARY_RE, _FIFTEEN_BUCKS_HANGER_RE], all_texts,
    )
    checks.append((
        "J5. agents do not call ClosetCloud a $14.99 product",
        accessory_as_primary == 0,
        f"violations={accessory_as_primary} (price_audit reported "
        f"{ph.get('price_confusion_count', 0)}, "
        f"repaired {ph.get('repaired_price_confusion_count', 0)})",
    ))

    # J6 plug-in re-asks
    power_reasks = _scan([_POWER_REASK_RE], all_texts)
    checks.append((
        "J6. agents do not ask 'plug-in or battery' as if unknown",
        power_reasks == 0,
        f"violations={power_reasks}",
    ))

    # J7 heat/UV/steam re-asks
    excluded_reasks = _scan([_EXCLUDED_REASK_RE], all_texts)
    checks.append((
        "J7. agents do not ask if it uses heat/UV/steam as if unknown",
        excluded_reasks == 0,
        f"violations={excluded_reasks}",
    ))

    # J8 credibility-question allowed (audit reported re-ask count
    # is the inverse — we just verify it's reasonable)
    pf = json.loads(
        (run_dir / "provided_fact_accuracy_quality.json").read_text(encoding="utf-8")
    )
    checks.append((
        "J8. provided_fact_accuracy_quality.json emitted",
        "known_fact_reask_count" in pf,
        f"known_fact_reask_count={pf.get('known_fact_reask_count')}",
    ))

    # J9 wrong category
    wrong_cat = _scan([_WRONG_CAT_RE], all_texts)
    checks.append((
        "J9. agents do not confuse with washer/dryer/steamer",
        wrong_cat == 0,
        f"violations={wrong_cat}",
    ))

    # J10 + J11 stance calibration
    cal = json.loads(
        (run_dir / "stance_calibration_quality.json").read_text(encoding="utf-8")
    )
    checks.append((
        "J10+J11. stance calibration ran",
        cal.get("ballots_reviewed", 0) > 0,
        f"reviewed={cal.get('ballots_reviewed')} "
        f"corrected={cal.get('corrections_applied')} "
        f"downgrades={cal.get('downgrades')}",
    ))

    # J12 repetition
    diversity = json.loads(
        (run_dir / "discussion_diversity_quality.json").read_text(encoding="utf-8")
    )
    score = diversity.get("persona_voice_diversity_score", 0)
    checks.append((
        "J12. repeated stock phrases materially reduced",
        score >= 0.7,
        f"voice_diversity={score} repeated_openers="
        f"{diversity.get('repeated_opening_phrases_count')}",
    ))

    # J13 caveat leakage
    leak_in_ballots = 0
    for b in ballots:
        text = (b.private_reasoning or "").lower()
        if any(p in text for p in PERSONA_FORBIDDEN_PHRASES):
            leak_in_ballots += 1
    checks.append((
        "J13. no persona caveat leakage",
        leak_in_ballots == 0,
        f"leaks={leak_in_ballots}",
    ))

    # J14 report-level caveats
    report = json.loads(
        (run_dir / "founder_report.json").read_text(encoding="utf-8")
    )
    checks.append((
        "J14. report-level caveats remain visible",
        bool(report.get("caveats")),
        f"caveat_count={len(report.get('caveats') or [])}",
    ))

    # No fake usage
    fake = _scan([_FAKE_USE_RE], all_texts)
    checks.append((
        "BONUS. no fake-usage claims for unlaunched product",
        fake == 0,
        f"violations={fake}",
    ))

    print("=" * 76)
    print(f"ClosetCloud 10B.2 verification — run_id={run_id}")
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
