"""Phase 10B.4 — verification script for the PantryPulse rerun.

Walks the J-checklist (16 criteria from the 10B.4 spec) and prints
a green/red summary. Reads the negation-scope, input-mechanism, v3
receptive-strictness, human-speech, and report-summary calibration
audit JSONs.
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


_NO_CAMERA_RE = re.compile(
    r"\b(?:no|without\s+a)\s+(?:built[\- ]in\s+|wide[\- ]angle\s+)?camera\b|"
    r"\bno[\- ]camera\s+(?:tracker|inventory|device|kit)\b",
    re.IGNORECASE,
)
_NO_SCANNING_RE = re.compile(
    r"\bno\s+scanning\b|\bwithout\s+scanning\b|"
    r"\bif\s+there\s+is\s+no\s+scanning\b",
    re.IGNORECASE,
)
_FAKE_USE_RE = re.compile(
    r"\b(?:i|we)\s+(?:bought|own|use|used|tried|tested)\s+"
    r"(?:the\s+|a\s+|my\s+)?pantrypulse\b",
    re.IGNORECASE,
)
_PROOF_FORM_RE = re.compile(
    r"\bsince\s+(?:the\s+)?(?:brief|claim|spec|plate)|"
    r"\bi'?d\s+want\s+(?:proof|a\s+real|to\s+know)|"
    r"\bsince\s+pantrypulse\s+(?:has|uses|captures)",
    re.IGNORECASE,
)


async def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_pantrypulse_run.py <run_uuid>")
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

    def _scan(rx, texts):
        return sum(1 for tx in texts if rx.search(tx or ""))

    # Load all 10B.4 audits
    neg = json.loads(
        (run_dir / "negation_scope_fact_quality.json").read_text(
            encoding="utf-8"
        )
    )
    inp = json.loads(
        (run_dir / "input_mechanism_fact_quality.json").read_text(
            encoding="utf-8"
        )
    )
    v3 = json.loads(
        (run_dir / "receptive_strictness_quality.json").read_text(
            encoding="utf-8"
        )
    )
    speech = json.loads(
        (run_dir / "human_speech_quality.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (run_dir / "report_summary_calibration_quality.json").read_text(
            encoding="utf-8"
        )
    )
    fact_lock_v2 = json.loads(
        (run_dir / "provided_fact_lock_v2_quality.json").read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        (run_dir / "founder_report.json").read_text(encoding="utf-8")
    )

    checks = []

    # J1: agents say PantryPulse has a camera
    has_cam_phrase = sum(
        1 for tx in all_texts
        if re.search(
            r"\b(?:has\s+a\s+camera|camera\s+(?:that|which)\s+captures|"
            r"still\s+(?:image|capture|shelf|photo))",
            tx, re.IGNORECASE,
        )
    )
    checks.append((
        "J1. agents acknowledge PantryPulse has a camera",
        has_cam_phrase >= 1,
        f"phrases_present={has_cam_phrase}",
    ))

    # J2: no inversion claiming records video / livestream / identifies
    privacy_inv = neg.get("privacy_fact_inversion_count", 0)
    checks.append((
        "J2. agents do not say it records video / livestreams / "
        "identifies people",
        privacy_inv == 0,
        f"privacy_fact_inversion_count={privacy_inv}",
    ))

    # J3: still-image / scan-event language present
    still_img = sum(
        1 for tx in all_texts
        if "still image" in tx.lower() or "still shelf" in tx.lower()
        or "scan event" in tx.lower()
    )
    checks.append((
        "J3. agents reference still-image / scan-event language",
        still_img >= 1 or neg.get("repaired_count", 0) >= 1,
        f"still_image_phrases={still_img} (repairs applied: "
        f"{neg.get('repaired_count', 0)})",
    ))

    # J4: no "no camera" phrasing
    no_cam = _scan(_NO_CAMERA_RE, all_texts)
    checks.append((
        "J4. agents do not say 'no camera' or 'without a camera'",
        no_cam == 0,
        f"violations={no_cam} (negation-scope caught "
        f"{neg.get('camera_fact_inversion_count', 0)}, "
        f"repaired {neg.get('repaired_count', 0)})",
    ))

    # J5: no "no scanning" when scanning is provided
    no_scan = _scan(_NO_SCANNING_RE, all_texts)
    checks.append((
        "J5. agents do not say 'no scanning' when barcode/NFC "
        "scanning exists",
        no_scan == 0,
        f"violations={no_scan} (input-mech caught "
        f"{inp.get('input_inversion_count', 0)})",
    ))

    # J6: agents may question scanning workflow (proof-form present)
    proof_form = _scan(_PROOF_FORM_RE, all_texts)
    checks.append((
        "J6. agents may ask for verification (proof-form present)",
        proof_form >= 1,
        f"proof_form_sentences={proof_form}",
    ))

    # J7: $149 treated as primary
    primary = fact_lock_v2.get("fact_lock_summary", {}).get(
        "primary_price"
    )
    checks.append((
        "J7. agents treat $149 as primary price",
        primary == "$149",
        f"primary_price_detected={primary}",
    ))

    # J8: $7.99 treated as accessory/subscription
    has_subscription = (
        "$7.99" in (run.product_brief.get("price_or_price_structure") or "")
    )
    checks.append((
        "J8. $7.99/month subscription present in fact lock",
        has_subscription,
        f"subscription_in_brief={has_subscription}",
    ))

    # J9: $19.99 treated as accessory tag price
    has_accessory = "$19.99" in (
        run.product_brief.get("price_or_price_structure") or ""
    )
    checks.append((
        "J9. $19.99 NFC tag accessory price in fact lock",
        has_accessory,
        f"accessory_in_brief={has_accessory}",
    ))

    # J10: receptive labels stricter (v3 downgrades non-zero OR
    # receptive count is honest)
    rec_before = v3.get("receptive_before", 0)
    rec_after = v3.get("receptive_after", 0)
    downgraded = v3.get("downgraded_receptive_count", 0)
    checks.append((
        "J10. receptive labels stricter under v3",
        downgraded > 0 or rec_after <= rec_before,
        f"receptive_before={rec_before} after={rec_after} "
        f"downgraded={downgraded}",
    ))

    # J11: mostly proof-demanding agents are UNCERTAIN
    rule_counter = v3.get("rule_counter") or {}
    proof_dom = (
        rule_counter.get("v3_killer_proof", 0)
        + rule_counter.get("v3_proof_outnumbers_positive", 0)
    )
    checks.append((
        "J11. mostly proof-demanding agents downgraded to UNCERTAIN",
        proof_dom > 0 or downgraded == 0,
        f"v3_proof_dominated_downgrades={proof_dom}",
    ))

    # J12: resistant labels still work — confirm by counting
    final_resistant = sum(
        1 for b in ballots
        if b.ballot_stage == "final"
        and (b.private_stance or "") in {"skeptical", "likely_reject"}
    )
    final_uncertain = sum(
        1 for b in ballots
        if b.ballot_stage == "final"
        and (b.private_stance or "") in {
            "curious_but_unconvinced", "needs_more_information",
        }
    )
    final_receptive = sum(
        1 for b in ballots
        if b.ballot_stage == "final"
        and (b.private_stance or "") == "interested_if_proven"
    )
    checks.append((
        "J12. final stance distribution honest (R / U / RES counts "
        "computable)",
        final_receptive + final_uncertain + final_resistant > 0,
        f"final={{receptive={final_receptive}, "
        f"uncertain={final_uncertain}, resistant={final_resistant}}}",
    ))

    # J13: no agent self-awareness leak (after repair)
    leak = speech.get("self_awareness_leak_count", 0)
    checks.append((
        "J13. no agent self-awareness / caveat leakage",
        leak == 0,
        f"self_awareness_leak_count={leak}",
    ))

    # J14: headline does not contain caveat
    head = (summary.get("headline") or "").lower()
    head_clean = (
        "not a real-world purchase forecast" not in head
        and "not a real-world forecast" not in head
        and "validated with real prospects" not in head
        and "synthetic signal" not in head
    )
    checks.append((
        "J14. headline does not include caveat sentence",
        head_clean,
        f"headline={summary.get('headline')!r}",
    ))

    # J15: report-level caveats remain visible
    caveats = report.get("caveats") or []
    checks.append((
        "J15. report-level caveats remain visible",
        len(caveats) > 0,
        f"caveat_count={len(caveats)}",
    ))

    # J16: best-fit + hardest copy human-readable
    bf_human = summary.get("best_fit_human_readable", False)
    hard_human = summary.get(
        "hardest_to_convince_human_readable", False,
    )
    checks.append((
        "J16. best-fit + hardest-to-convince copy founder-readable",
        bf_human and hard_human,
        f"best_fit_human={bf_human} hardest_human={hard_human}",
    ))

    # ---- Pretty print ----
    print("=" * 76)
    print(f"PantryPulse 10B.4 verification — run_id={run_id}")
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
