"""Phase 10B.3 — human-society realism + agent self-awareness leak.

Phase 10B.1 already had a caveat-leak detector that catches
"synthetic n=24 chat" / "directional, not a verdict". This module
extends it with a stricter set of self-awareness leaks that are
specific to agents talking about themselves AS agents instead of
real people in the target market.

Detected leaks:
  • "as an agent" / "as an AI"
  • "as a synthetic persona" / "synthetic persona / society / agent"
  • "in this simulation" / "in this synthetic society"
  • "n=24" / "n=21" / "n=12" (any "n=" sample-size phrasing)
  • "directional signal" / "directional rather than"
  • "not a forecast" / "not a verdict" / "real-world purchase forecast"

The detector also exposes a soft repair that removes the leaking
sentences while preserving the rest of the buyer reasoning. The
caveat-leak.py phrase set already covers most of these strings;
this module is a focused audit + report surface so the operator
gets a separate trace for Phase 10B.3 acceptance.
"""
from __future__ import annotations

import re
from typing import Any


# Phrases that mark the persona speaking AS a system evaluator
# rather than a real person in the target market. Mostly overlap
# with PERSONA_FORBIDDEN_PHRASES but tightened around "agent /
# synthetic / simulation / n=NN".
SELF_AWARENESS_PHRASES: tuple[str, ...] = (
    "as an agent",
    "as an ai",
    "as an a.i.",
    "as a synthetic",
    "as a simulated",
    "synthetic persona",
    "synthetic agent",
    "synthetic society",
    "synthetic conversation",
    "in this simulation",
    "in this synthetic",
    "in this n=",
    "in the simulation",
    "directional signal",
    "directional rather than",
    "directional, not a verdict",
    "not a real-world forecast",
    "not a real-world purchase forecast",
    "not a market forecast",
    "not a forecast",
    "not a verdict",
    "purely synthetic",
    "this is synthetic",
    "i'm a simulated",
    "i am a simulated",
    "as a model",
    "as an assistant",
    "as a language model",
)

# "n=24" style sample-size phrasing. Matches `n = 12`, `n=24`,
# `(n=24)`, `n = 100`. Not a phrase substring — uses regex.
_N_EQUALS_RE = re.compile(r"\bn\s*=\s*\d{1,4}\b", re.IGNORECASE)

_PHRASE_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(re.escape(p), re.IGNORECASE)
    for p in SELF_AWARENESS_PHRASES
)


def detect_self_awareness_leak(text: str) -> list[str]:
    """Return the list of self-awareness leak fragments found."""
    if not text:
        return []
    found: list[str] = []
    for r in _PHRASE_RES:
        for m in r.finditer(text):
            found.append(m.group(0))
    for m in _N_EQUALS_RE.finditer(text):
        found.append(m.group(0))
    return found


def _split_sentences(text: str) -> list[str]:
    """Sentence splitter (mirrors caveat_leak helper). Handles
    em-dash separators because personas often join a system caveat
    to a real concern with an em-dash."""
    if not text:
        return []
    placeholder = "<<DOT>>"
    safe = re.sub(
        r"(\d)\.(\d)", lambda m: f"{m.group(1)}{placeholder}{m.group(2)}",
        text,
    )
    raw = re.split(
        r"(?<=[\.\?\!])\s+|\n+|\s+[—–]\s+",
        safe,
    )
    return [
        p.replace(placeholder, ".").strip()
        for p in raw
        if p.strip()
    ]


def strip_self_awareness_leak(text: str) -> tuple[str, list[str]]:
    """Remove sentences containing any self-awareness leak. Returns
    `(cleaned_text, removed_sentences)`."""
    if not text:
        return "", []
    sentences = _split_sentences(text)
    surviving: list[str] = []
    removed: list[str] = []
    for s in sentences:
        if (
            any(r.search(s) for r in _PHRASE_RES)
            or _N_EQUALS_RE.search(s)
        ):
            removed.append(s)
            continue
        surviving.append(s)
    cleaned = " ".join(surviving).strip()
    return cleaned, removed


def audit_human_society_realism(
    *,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit ballot + turn texts for agent-self-awareness leaks.
    Produces the artifact written as
    `human_society_realism_quality.json`."""
    audit: dict[str, Any] = {
        "phase": "10b_3_human_society_realism",
        "turns_scanned": len(turn_texts),
        "ballots_scanned": len(ballot_texts),
        "self_awareness_leak_count": 0,
        "leaks_by_phrase": {},
        "examples": [],
        "any_leak": False,
    }
    by_phrase: dict[str, int] = {}
    examples: list[dict[str, Any]] = audit["examples"]

    def _scan(blob: dict[str, Any], origin: str) -> None:
        text = blob.get("text") or ""
        if not text:
            return
        leaks = detect_self_awareness_leak(text)
        if not leaks:
            return
        for leak in leaks:
            audit["self_awareness_leak_count"] += 1
            key = leak.lower()
            by_phrase[key] = by_phrase.get(key, 0) + 1
        if len(examples) < 12:
            examples.append({
                "origin": origin,
                "persona_id": str(blob.get("persona_id") or ""),
                "phrases": list({leak.lower() for leak in leaks})[:5],
                "excerpt": text[:240],
            })

    for t in turn_texts:
        _scan(t, "turn")
    for b in ballot_texts:
        _scan(b, "ballot")

    audit["leaks_by_phrase"] = by_phrase
    audit["any_leak"] = audit["self_awareness_leak_count"] > 0
    return audit
