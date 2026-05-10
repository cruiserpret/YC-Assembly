"""Phase 10B.4 — Input-mechanism fact validator (focused audit).

The negation-scope validator already flags input-mechanism
inversions ("no scanning", "just a magnet"). This module exposes a
dedicated audit surface so the operator gets a clean
`input_mechanism_fact_quality.json` artifact separate from the
camera / privacy audit.
"""
from __future__ import annotations

from typing import Any

from assembly.sources.product_grounding.negation_scope_validator import (
    _INPUT_INVERSION_PATTERNS,
    _fact_required_true,
)
from assembly.sources.product_grounding.product_fact_card import (
    ProductFactCard,
)


def audit_input_mechanism(
    *,
    fact_card: ProductFactCard,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit ballot + turn texts for input-mechanism fact
    inversions ("no scanning", "just a magnet", "no input
    mechanism") when the Product Fact Lock asserts at least one
    input mechanism exists."""
    available_mechanisms = sorted(
        k for k, v in (fact_card.input_mechanism_facts or {}).items() if v
    )
    audit: dict[str, Any] = {
        "phase": "10b_4_input_mechanism_fact",
        "input_mechanisms_present": available_mechanisms,
        "input_mechanism_known": bool(available_mechanisms),
        "input_inversion_count": 0,
        "by_kind": {},
        "examples": [],
        "any_violations": False,
    }
    by_kind: dict[str, int] = {}
    examples: list[dict[str, Any]] = audit["examples"]

    if not available_mechanisms:
        # Nothing to defend — single-input or unknown product. Skip
        # gracefully with `any_violations=False`.
        audit["skip_reason"] = (
            "no input mechanisms parsed from the brief — nothing to "
            "defend"
        )
        return audit

    def _scan(blob: dict[str, Any], origin: str) -> None:
        text = blob.get("text") or ""
        if not text:
            return
        for rx, key, kind, _anchor, _proof in _INPUT_INVERSION_PATTERNS:
            if not _fact_required_true(fact_card, key):
                continue
            m = rx.search(text)
            if not m:
                continue
            audit["input_inversion_count"] += 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if len(examples) < 12:
                examples.append({
                    "origin": origin,
                    "kind": kind,
                    "persona_id": str(blob.get("persona_id") or ""),
                    "match": m.group(0),
                    "excerpt": text[:240],
                })

    for t in turn_texts:
        _scan(t, "turn")
    for b in ballot_texts:
        _scan(b, "ballot")

    audit["by_kind"] = by_kind
    audit["any_violations"] = audit["input_inversion_count"] > 0
    return audit
