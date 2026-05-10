"""Phase 10B.1 — persona-system-caveat leak detector + repair.

Detects sentences inside persona speech / private ballots that
sound like a system evaluator rather than a buyer ("synthetic n=24
chat", "directional, not a verdict", "as a synthetic persona", …)
and strips them while preserving legitimate buyer reasoning.

Used by:
  * the discussion stage as a soft post-hoc cleaner that rewrites
    persisted ballot rows
  * the report stage as an audit
  * the frontend as a defensive filter (mirrored regex set)
"""
from __future__ import annotations

import re
from typing import Any


# Phrases that should never appear inside persona speech / ballots.
# These are case-insensitive substring matches; whole sentences
# containing any of them are stripped during repair.
PERSONA_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "synthetic n=",
    "synthetic chat",
    "directional, not a verdict",
    "directional rather than a verdict",
    "directional but not a verdict",
    "not a real-world forecast",
    "not a market forecast",
    "as an ai",
    "as a synthetic persona",
    "as a synthetic agent",
    "i'm a synthetic",
    "this is a synthetic",
    "this synthetic n",
    "synthetic society",
    "this simulation",
    "this chat is",
    "n=24",
    "n=21",
    "the simulation",
    "(synthetic n",
    "synthetic-society",
    "treat as directional",
    "treating it as directional",
    "synthetic conversation",
)


_PHRASE_RES = [
    re.compile(re.escape(p), re.IGNORECASE)
    for p in PERSONA_FORBIDDEN_PHRASES
]


def detect_caveat_leak(text: str) -> list[str]:
    """Return the list of forbidden phrase matches found in `text`."""
    if not text:
        return []
    found: list[str] = []
    for r in _PHRASE_RES:
        for m in r.finditer(text):
            found.append(m.group(0))
    return found


def _split_sentences(text: str) -> list[str]:
    """Tiny sentence splitter — splits on `. `, `! `, `? `, newlines,
    AND em-dash / en-dash. Em-dashes are common in persona speech to
    join a caveat with its buyer reasoning ("directional, not a
    verdict — but at $69.99 I'd want runtime proof"); splitting at
    the dash lets us strip just the caveat half. Avoids breaking on
    URLs / decimals."""
    if not text:
        return []
    # Protect "1.5" / "Mr." style decimals + abbreviations from
    # splitting incorrectly.
    placeholder = "<<DOT>>"
    safe = re.sub(
        r"(\d)\.(\d)", lambda m: f"{m.group(1)}{placeholder}{m.group(2)}",
        text,
    )
    # Split on .!?, newline, OR em-dash/en-dash with optional surrounding
    # whitespace. The dash itself is not preserved in any segment.
    raw = re.split(
        r"(?<=[\.\?\!])\s+|\n+|\s+[—–]\s+",
        safe,
    )
    return [
        p.replace(placeholder, ".").strip()
        for p in raw
        if p.strip()
    ]


def strip_caveat_leak(text: str) -> tuple[str, list[str]]:
    """Remove sentences that contain any forbidden phrase. Returns
    `(cleaned_text, removed_sentences)`. Preserves the order +
    spacing of the surviving sentences.
    """
    if not text:
        return "", []
    sentences = _split_sentences(text)
    surviving: list[str] = []
    removed: list[str] = []
    for s in sentences:
        if any(r.search(s) for r in _PHRASE_RES):
            removed.append(s)
            continue
        surviving.append(s)
    cleaned = " ".join(surviving).strip()
    # Also strip any leading filler like "Caveat:" if it now starts
    # an empty fragment
    cleaned = re.sub(r"^\s*Caveat[:\.\s]*", "", cleaned, flags=re.IGNORECASE)
    return cleaned, removed


def audit_ballot_caveat_leaks(
    ballots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Inspect a list of ballot dicts (each with at least `persona_id`
    + `private_reasoning` + `ballot_stage`) and return an audit dict
    summarizing leakage. The caller decides whether to also rewrite
    the persisted rows."""
    total = len(ballots)
    leaked = 0
    sentences_removed = 0
    examples: list[dict[str, Any]] = []
    for b in ballots:
        text = (b.get("private_reasoning") or b.get("public_text") or "")
        if not text:
            continue
        hits = detect_caveat_leak(text)
        if not hits:
            continue
        leaked += 1
        cleaned, removed = strip_caveat_leak(text)
        sentences_removed += len(removed)
        if len(examples) < 6:
            examples.append({
                "persona_id": str(b.get("persona_id") or ""),
                "ballot_stage": b.get("ballot_stage"),
                "original_excerpt": text[:240],
                "phrases_matched": list(set(hits))[:5],
                "removed_sentences": removed[:3],
                "cleaned_excerpt": cleaned[:240],
            })
    return {
        "phase": "10b_1_persona_caveat_leak",
        "ballots_total": total,
        "ballots_with_leak": leaked,
        "sentences_removed": sentences_removed,
        "examples": examples,
        "any_leak": leaked > 0,
    }
