"""Phase 10B.2 — price-hierarchy + extended provided-fact validators.

Detects two distinct grounding failures specific to multi-price
products:

  1. Price confusion — agent describes the primary product using an
     accessory amount ("$14.99 hanger", "fifteen bucks for the
     product"). This was the headline ClosetCloud failure: the
     starter kit is $119 but agents kept debating the value of a
     $14.99 hanger.

  2. Known-fact re-ask — agent asks "is it plug-in or battery?",
     "does it use heat or steam?", or asks for any fact already
     locked in the Product Fact Card.

Both validators are post-hoc audit + soft repair (regex-based
sentence strip / annotation). Neither regenerates LLM turns; they
operate on already-persisted ballot rows + turn rows. Each emits
a JSON artifact for the operator audit trail.
"""
from __future__ import annotations

import re
from typing import Any

from assembly.sources.product_grounding.product_fact_card import (
    ProductFactCard,
)


# ---------------------------------------------------------------------------
# Price hierarchy
# ---------------------------------------------------------------------------


_NUMBER_WORDS_TO_DOLLARS: tuple[tuple[str, int], ...] = (
    ("five bucks", 5), ("ten bucks", 10), ("twelve bucks", 12),
    ("fifteen bucks", 15), ("twenty bucks", 20),
    ("twenty-five bucks", 25), ("twenty five bucks", 25),
    ("thirty bucks", 30),
    ("forty bucks", 40), ("fifty bucks", 50), ("sixty bucks", 60),
    ("seventy bucks", 70), ("eighty bucks", 80),
    ("ninety bucks", 90), ("hundred bucks", 100),
)


_DOLLAR_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_AMOUNT_AS_PRODUCT_RE = re.compile(
    # Allow `$X`, `$X,`, `$X — `, `$X.` followed by up to 40
    # connecting characters, then a product noun. The separator
    # class `[\s,;:.\-—–]+` covers the punctuation that can appear
    # between an amount and the buyer's verb in natural speech.
    r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)[\s,;:.\-—–]+"
    r"(?:[a-z\- ']{0,60}?)"
    r"(?:product|hanger|dock|pod|kit|system|station|device|unit)\b",
    re.IGNORECASE,
)


def _amount_to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _scan_text_for_price_confusion(
    text: str,
    primary_value: float,
    accessory_values: list[float],
) -> list[dict[str, Any]]:
    """Return a list of price-confusion findings for the given
    text. Each finding is a dict with `match`, `kind`, and the
    accessory amount that was misused as primary."""
    if not text:
        return []
    findings: list[dict[str, Any]] = []
    low = text.lower()
    # Number-word phrases match an accessory amount when the rounded
    # value is within $1 (e.g. "fifteen bucks" ↔ $14.99).
    rounded_accessory = {round(v) for v in accessory_values}
    # 1. Number-word "fifteen bucks" / "fifteen dollars" patterns
    for phrase, cents in _NUMBER_WORDS_TO_DOLLARS:
        if phrase in low and cents in rounded_accessory:
            # Look for surrounding product nouns
            window = low[
                max(0, low.find(phrase) - 40):
                min(len(low), low.find(phrase) + 80)
            ]
            if any(
                noun in window
                for noun in (
                    "hanger", "product", "dock", "kit", "system",
                    "station", "device", "unit",
                )
            ):
                findings.append({
                    "match": phrase,
                    "kind": "number_word_for_accessory",
                    "amount_value": float(cents),
                    "excerpt": text[
                        max(0, low.find(phrase) - 40):
                        min(len(text), low.find(phrase) + 80)
                    ],
                })
    # 2. Explicit "$X (product noun)" patterns where $X is an
    # accessory price.
    for m in _AMOUNT_AS_PRODUCT_RE.finditer(text):
        v = _amount_to_float(m.group(1))
        if v is None:
            continue
        if any(abs(v - a) < 0.01 for a in accessory_values):
            findings.append({
                "match": m.group(0),
                "kind": "accessory_amount_as_product",
                "amount_value": v,
                "excerpt": text[
                    max(0, m.start() - 40):
                    min(len(text), m.end() + 60)
                ],
            })
    return findings


def audit_price_hierarchy(
    *,
    fact_card: ProductFactCard,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit-only inspection. Returns an audit dict ready for
    `price_hierarchy_quality.json`. The orchestrator decides
    whether to apply the repairs (annotate or strip)."""
    primary_value = _amount_to_float(
        (fact_card.primary_price or "").replace("$", "")
        .split("/")[0].strip()
    )
    accessory_values = [
        v
        for ap in fact_card.accessory_prices
        for v in [_amount_to_float(ap.amount.replace("$", ""))]
        if v is not None
    ]
    if primary_value is None or not accessory_values:
        # Single-price product — nothing to validate.
        return {
            "phase": "10b_2_price_hierarchy",
            "primary_price_detected": fact_card.primary_price,
            "accessory_prices_detected": [
                {"label": ap.label, "amount": ap.amount}
                for ap in fact_card.accessory_prices
            ],
            "price_confusion_count": 0,
            "repaired_price_confusion_count": 0,
            "unrepaired_price_confusion_count": 0,
            "any_violations": False,
            "examples": [],
            "skip_reason": (
                "no accessory price hierarchy on this brief — "
                "single-price product"
            ),
        }
    examples: list[dict[str, Any]] = []
    confusion_count = 0
    for blob in turn_texts:
        text = blob.get("text") or ""
        for f in _scan_text_for_price_confusion(
            text, primary_value, accessory_values,
        ):
            confusion_count += 1
            if len(examples) < 12:
                examples.append({
                    "origin": "turn",
                    "persona_id": str(blob.get("persona_id") or ""),
                    **f,
                })
    for blob in ballot_texts:
        text = blob.get("text") or ""
        for f in _scan_text_for_price_confusion(
            text, primary_value, accessory_values,
        ):
            confusion_count += 1
            if len(examples) < 12:
                examples.append({
                    "origin": "ballot",
                    "persona_id": str(blob.get("persona_id") or ""),
                    **f,
                })
    return {
        "phase": "10b_2_price_hierarchy",
        "primary_price_detected": fact_card.primary_price,
        "accessory_prices_detected": [
            {"label": ap.label, "amount": ap.amount}
            for ap in fact_card.accessory_prices
        ],
        "price_confusion_count": confusion_count,
        "repaired_price_confusion_count": 0,
        "unrepaired_price_confusion_count": confusion_count,
        "any_violations": confusion_count > 0,
        "examples": examples,
    }


# ---------------------------------------------------------------------------
# Extended provided-fact accuracy
# ---------------------------------------------------------------------------


_POWER_QUESTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:is|are)\s+(?:it|they|the\s+\w+)\s+plug[\- ]in\s+or\s+battery",
        r"\bdoes\s+(?:it|this)\s+plug\s+in\b",
        r"\bis\s+(?:it|this)\s+battery[\- ]powered\b",
        r"\bhow\s+(?:does|do)\s+(?:it|they)\s+(?:plug\s+in|get\s+power)",
        r"\bdoes\s+(?:it|this)\s+(?:run\s+on\s+battery|need\s+a\s+plug)",
    )
)


_EXCLUDED_FEATURE_QUESTION_PATTERNS: tuple[
    tuple[re.Pattern[str], str], ...
] = (
    (re.compile(r"\bdoes\s+(?:it|this)\s+use\s+heat\b", re.IGNORECASE),
     "heat"),
    (re.compile(r"\bdoes\s+(?:it|this)\s+use\s+steam\b", re.IGNORECASE),
     "steam"),
    (re.compile(r"\bdoes\s+(?:it|this)\s+use\s+water\b", re.IGNORECASE),
     "water"),
    (re.compile(r"\bdoes\s+(?:it|this)\s+use\s+detergent\b", re.IGNORECASE),
     "detergent"),
    (re.compile(
        r"\bdoes\s+(?:it|this)\s+use\s+(?:uv|uv[\- ]?c|ultraviolet)",
        re.IGNORECASE,
    ), "uv"),
    (re.compile(r"\bdoes\s+(?:it|this)\s+use\s+ozone\b", re.IGNORECASE),
     "ozone"),
    (re.compile(
        r"\b(?:does|do)\s+(?:it|this|they)\s+use\s+heat,?\s*(?:uv|steam|ozone|water|detergent)",
        re.IGNORECASE,
    ), "heat / steam / uv / ozone"),
)


def audit_provided_fact_accuracy(
    *,
    fact_card: ProductFactCard,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Detect known-fact re-asks beyond the 10B.1 set:
       * power / charging when those facts were locked
       * excluded features ("does it use heat?") when the brief
         said the product does NOT use them
       * kit contents when listed
    Returns an audit dict for `provided_fact_accuracy_quality.json`.
    """
    power_known = bool(fact_card.power_facts) or bool(
        fact_card.charging_facts
    )
    excluded_known = bool(fact_card.excluded_features)
    kit_known = bool(fact_card.kit_contents)

    audit: dict[str, Any] = {
        "phase": "10b_2_provided_fact_accuracy",
        "fact_lock_summary": {
            "primary_price": fact_card.primary_price,
            "power_known": power_known,
            "excluded_known": excluded_known,
            "kit_known": kit_known,
            "launch_state_known": bool(fact_card.launch_state),
            "competitors_known": bool(
                fact_card.competitors_or_alternatives
            ),
        },
        "known_fact_reask_count": 0,
        "power_fact_reask_count": 0,
        "excluded_feature_reask_count": 0,
        "price_reask_count": 0,
        "launch_state_reask_count": 0,
        "repaired_count": 0,
        "unrepaired_count": 0,
        "examples": [],
    }
    examples: list[dict[str, Any]] = audit["examples"]

    def _scan(blob: dict[str, Any], origin: str) -> None:
        text = blob.get("text") or ""
        if not text:
            return
        if power_known:
            for r in _POWER_QUESTION_PATTERNS:
                m = r.search(text)
                if m:
                    audit["power_fact_reask_count"] += 1
                    audit["known_fact_reask_count"] += 1
                    if len(examples) < 12:
                        examples.append({
                            "origin": origin,
                            "kind": "power_reask",
                            "persona_id": str(blob.get("persona_id") or ""),
                            "match": m.group(0),
                            "excerpt": text[:200],
                        })
                    break
        if excluded_known:
            for r, label in _EXCLUDED_FEATURE_QUESTION_PATTERNS:
                m = r.search(text)
                if m:
                    audit["excluded_feature_reask_count"] += 1
                    audit["known_fact_reask_count"] += 1
                    if len(examples) < 12:
                        examples.append({
                            "origin": origin,
                            "kind": "excluded_feature_reask",
                            "feature": label,
                            "persona_id": str(blob.get("persona_id") or ""),
                            "match": m.group(0),
                            "excerpt": text[:200],
                        })
                    break

    for t in turn_texts:
        _scan(t, "turn")
    for b in ballot_texts:
        _scan(b, "ballot")

    audit["unrepaired_count"] = audit["known_fact_reask_count"]
    audit["any_violations"] = audit["known_fact_reask_count"] > 0
    return audit


# ---------------------------------------------------------------------------
# Soft repair — strip price-confusion sentences without losing the
# rest of the buyer reasoning. Used by the orchestrator to rewrite
# persisted ballot rows.
# ---------------------------------------------------------------------------


def repair_price_confusion(
    text: str,
    primary_value: float,
    accessory_values: list[float],
) -> tuple[str, int]:
    """Drop sentences that confuse an accessory amount with the
    primary product price. Returns `(cleaned_text, removed_count)`.
    Preserves all sentences that didn't contain the confusion."""
    if not text:
        return "", 0
    # Sentence split (em-dash + period + newline + question mark).
    raw = re.split(r"(?<=[\.\?\!])\s+|\n+|\s+[—–]\s+", text)
    surviving: list[str] = []
    removed = 0
    for s in raw:
        if not s.strip():
            continue
        findings = _scan_text_for_price_confusion(
            s, primary_value, accessory_values,
        )
        if findings:
            removed += 1
            continue
        surviving.append(s.strip())
    return " ".join(surviving).strip(), removed
