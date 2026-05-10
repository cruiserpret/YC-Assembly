"""Phase 10B.1 — discussion diversity auditor.

Counts repeated openers, near-duplicate turns, repeated objections,
and emits a `persona_voice_diversity_score` in [0, 1]. Audit-only —
the orchestrator does not regenerate turns based on this output.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any


REPEATED_OPENER_PATTERNS: tuple[str, ...] = (
    # Phase 10B.1
    "before i get excited",
    "before i can get excited",
    "i need to know",
    "until i see",
    "what would actually move me",
    "what would actually convince me",
    "what i'd need",
    "what i would need",
    "before i commit",
    "i'm not getting excited until",
    # Phase 10B.2 — observed in the ClosetCloud run
    "here's what's bugging me",
    "here is what's bugging me",
    "i keep circling back",
    "the thing i keep coming back to",
    "what i want pinned down",
    "i want to push back on",
    "what would actually shift me",
)


_OPENER_RES = [
    re.compile(re.escape(p), re.IGNORECASE)
    for p in REPEATED_OPENER_PATTERNS
]


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace for cheap dedupe comparison."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _shingles(text: str, k: int = 7) -> set[str]:
    """k-token shingles for near-duplicate comparison."""
    tokens = re.findall(r"[a-z0-9']+", _normalize(text))
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def audit_discussion_diversity(
    *,
    turns: list[dict[str, Any]],
    ballots: list[dict[str, Any]] | None = None,
    near_duplicate_threshold: float = 0.55,
) -> dict[str, Any]:
    """Audit a list of turns + optional ballots for repetition.

    `turns` items must carry `text` and `persona_id`. `ballots`
    items may carry `private_reasoning` + `persona_id` + `ballot_stage`.

    Returns an audit dict with the four counts, a diversity score,
    and a small list of example duplicates.
    """
    turn_texts = [
        (str(t.get("persona_id") or ""), (t.get("text") or "").strip())
        for t in turns
        if (t.get("text") or "").strip()
    ]
    ballot_texts = [
        (
            str(b.get("persona_id") or ""),
            (b.get("private_reasoning") or "").strip(),
        )
        for b in (ballots or [])
        if (b.get("private_reasoning") or "").strip()
    ]
    all_texts = turn_texts + ballot_texts

    # 1. Repeated opener counts — first 6 words of each text
    opener_counter: Counter[str] = Counter()
    opener_pattern_hits: Counter[str] = Counter()
    for _, text in all_texts:
        # First 6 words as a literal opener
        first6 = " ".join(text.split()[:6]).lower().strip(" ,.!?;:")
        if first6:
            opener_counter[first6] += 1
        # Pattern-based opener-phrase hits
        for r in _OPENER_RES:
            if r.search(text):
                opener_pattern_hits[r.pattern] += 1
    repeated_openers = [
        (k, v) for k, v in opener_counter.items() if v >= 2
    ]
    repeated_opener_phrases_count = sum(v for _, v in repeated_openers)
    repeated_opener_phrases_count += sum(opener_pattern_hits.values())

    # 2. Near-duplicate turn count via 7-token Jaccard
    turn_shingles = [
        (pid, _shingles(text)) for pid, text in turn_texts
    ]
    near_duplicate_turn_count = 0
    near_duplicate_examples: list[dict[str, Any]] = []
    for i in range(len(turn_shingles)):
        for j in range(i + 1, len(turn_shingles)):
            score = _jaccard(turn_shingles[i][1], turn_shingles[j][1])
            if score >= near_duplicate_threshold:
                near_duplicate_turn_count += 1
                if len(near_duplicate_examples) < 4:
                    near_duplicate_examples.append({
                        "persona_a": turn_shingles[i][0],
                        "persona_b": turn_shingles[j][0],
                        "jaccard": round(score, 2),
                        "excerpt_a": turn_texts[i][1][:160],
                        "excerpt_b": turn_texts[j][1][:160],
                    })

    # 3. Repeated objection count — bucket the first 8 words of any
    # ballot reasoning that contains "i need" / "until i see"
    objection_counter: Counter[str] = Counter()
    for _, text in ballot_texts:
        low = text.lower()
        if "i need" in low or "until i see" in low or "i'd need" in low:
            stem = " ".join(text.split()[:8]).lower()
            objection_counter[stem] += 1
    repeated_objection_count = sum(
        v for v in objection_counter.values() if v >= 2
    )

    # 4. Distinct angle count — heuristic: number of unique stems +
    # personas with text that doesn't match any repeated opener.
    distinct_personas = {pid for pid, _ in all_texts if pid}
    distinct_angle_count = len(distinct_personas)

    # 5. Voice diversity score — 1.0 - normalized repetition penalty.
    n = max(len(all_texts), 1)
    penalty = (
        repeated_opener_phrases_count
        + near_duplicate_turn_count * 1.5
        + repeated_objection_count
    ) / (n * 2.0)
    voice_diversity_score = max(0.0, min(1.0, 1.0 - penalty))

    return {
        "phase": "10b_1_discussion_diversity",
        "turns_scanned": len(turn_texts),
        "ballots_scanned": len(ballot_texts),
        "repeated_opening_phrases_count": repeated_opener_phrases_count,
        "repeated_openers": [
            {"opener": k, "count": v} for k, v in repeated_openers[:8]
        ],
        "repeated_opener_pattern_hits": dict(opener_pattern_hits),
        "near_duplicate_turn_count": near_duplicate_turn_count,
        "near_duplicate_examples": near_duplicate_examples,
        "repeated_objection_count": repeated_objection_count,
        "distinct_angle_count": distinct_angle_count,
        "persona_voice_diversity_score": round(voice_diversity_score, 3),
    }
