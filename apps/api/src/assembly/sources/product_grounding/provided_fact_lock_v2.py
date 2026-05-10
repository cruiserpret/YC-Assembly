"""Phase 10B.3 — Provided-Fact Lock v2 + repair.

Highest-priority Phase 10B.3 fix: agents were re-asking facts the
brief had already provided ("is it dishwasher-safe?",  "can it hold
45 minutes?", "is it rechargeable?"). The 10B.2 lock covered price /
power / launch state / excluded features, but missed runtime,
temperature, cleaning, materials, and bundle pricing.

This module:
  * audits ballot + turn texts for any fact-category re-ask the
    fact card already covers
  * provides a soft repair that rewrites the offending sentence
    into a "Since the brief says X, I'd want proof Y" form so the
    persona's *concern* survives but the factual error is removed
"""
from __future__ import annotations

import re
from typing import Any

from assembly.sources.product_grounding.product_fact_card import (
    ProductFactCard,
)


# ---------------------------------------------------------------------------
# Per-category re-ask patterns. Each entry is (pattern, fact_category,
# canonical_anchor_phrase). The anchor phrase is what the repair will
# substitute back in via "Since the brief says <anchor>, I'd want proof
# of <derived>".
# ---------------------------------------------------------------------------


_DISHWASHER_REASK_RE = re.compile(
    r"\b(?:is\s+(?:it|this|the\s+plate)\s+dishwasher[\- ]safe|"
    r"can\s+(?:i|you)\s+(?:put|run)\s+(?:it|this|the\s+plate)\s+"
    r"in\s+the\s+dishwasher|"
    r"does\s+(?:it|this|the\s+plate)\s+go\s+in\s+the\s+dishwasher|"
    r"i'?d\s+want\s+to\s+know\s+(?:if|whether)\s+(?:it|this|the\s+plate)"
    r"\s+is\s+dishwasher[\- ]safe)\b",
    re.IGNORECASE,
)

_MICROWAVE_REASK_RE = re.compile(
    r"\b(?:is\s+(?:it|this|the\s+plate)\s+microwave[\- ]safe|"
    r"can\s+(?:i|you)\s+(?:put|microwave)\s+(?:it|this|the\s+plate)|"
    r"does\s+(?:it|this|the\s+plate)\s+go\s+in\s+the\s+microwave|"
    r"i'?d\s+want\s+to\s+know\s+(?:if|whether)\s+(?:it|this|the\s+plate)"
    r"\s+is\s+microwave[\- ]safe)\b",
    re.IGNORECASE,
)

_RUNTIME_REASK_RE = re.compile(
    r"\b(?:can\s+it\s+(?:keep|hold)\s+(?:food|drinks?)\s+warm\s+for|"
    r"how\s+long\s+(?:does|can)\s+(?:it|the\s+battery|the\s+plate)\s+"
    r"(?:keep|hold|last)|"
    r"how\s+(?:long|many\s+(?:minutes|hours))\s+does\s+it\s+stay\s+warm|"
    r"is\s+the\s+(?:runtime|battery)\s+long\s+enough|"
    r"does\s+it\s+last\s+(?:long\s+enough|a\s+full\s+meal))\b",
    re.IGNORECASE,
)

_TEMPERATURE_REASK_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+the\s+(?:warming\s+)?temperature|"
    r"how\s+(?:hot|warm)\s+(?:does|is)\s+(?:it|the\s+plate|the\s+surface)|"
    r"what\s+temperature\s+does\s+it\s+(?:hold|reach|warm\s+to)|"
    r"i'?d\s+want\s+to\s+know\s+the\s+temperature\s+range)\b",
    re.IGNORECASE,
)

_USB_C_REASK_RE = re.compile(
    r"\b(?:is\s+(?:it|this|the\s+base)\s+(?:rechargeable|usb[\- ]?c)|"
    r"how\s+does\s+(?:it|the\s+base)\s+(?:charge|get\s+power)|"
    r"does\s+(?:it|the\s+base)\s+(?:plug\s+in|charge\s+via\s+usb)|"
    r"is\s+the\s+base\s+rechargeable|"
    r"can\s+i\s+charge\s+it\s+over\s+usb)\b",
    re.IGNORECASE,
)

_BUNDLE_PRICE_REASK_RE = re.compile(
    r"\b(?:is\s+there\s+a\s+(?:bundle|two[\- ]pack|family\s+pack|multi[\- ]pack)|"
    r"do\s+they\s+sell\s+a\s+(?:bundle|multi[\- ]pack|two[\- ]pack)|"
    r"what(?:'s|\s+is)\s+the\s+(?:bundle|two[\- ]plate|two[\- ]pack)\s+price|"
    r"how\s+much\s+(?:does|is)\s+the\s+(?:bundle|two[\- ]pack|two[\- ]plate))\b",
    re.IGNORECASE,
)

_MATERIAL_REASK_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+(?:it|the\s+plate)\s+made\s+of|"
    r"is\s+(?:it|the\s+plate)\s+(?:ceramic|stainless\s+steel|"
    r"food[\- ]grade|bpa[\- ]free)|"
    r"what\s+material\s+is\s+(?:it|the\s+plate))\b",
    re.IGNORECASE,
)

_KIT_CONTENTS_REASK_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+(?:in|inside|included)\s+(?:the\s+)?(?:kit|bundle|box)|"
    r"what\s+do\s+i\s+get\s+for\s+\$|"
    r"what\s+comes\s+(?:in|with)\s+(?:the\s+)?(?:kit|bundle|box))\b",
    re.IGNORECASE,
)

# (pattern, category, fact_card_attr, anchor_phrase_template)
_FACT_REASK_RULES: tuple[
    tuple[re.Pattern[str], str, str, str], ...
] = (
    (_DISHWASHER_REASK_RE, "cleaning_dishwasher", "cleaning_facts",
     "the plate is described as dishwasher-safe"),
    (_MICROWAVE_REASK_RE, "cleaning_microwave", "cleaning_facts",
     "the plate is described as microwave-safe"),
    (_RUNTIME_REASK_RE, "runtime", "runtime_facts",
     "the brief says runtime is {0}"),
    (_TEMPERATURE_REASK_RE, "temperature", "temperature_facts",
     "the brief says the warming range is {0}"),
    (_USB_C_REASK_RE, "charging_usb_c", "charging_facts",
     "the brief says the base is USB-C rechargeable"),
    (_BUNDLE_PRICE_REASK_RE, "bundle_price", "bundle_price",
     "the brief lists a bundle at {0}"),
    (_MATERIAL_REASK_RE, "materials", "materials",
     "the brief says the materials are {0}"),
    (_KIT_CONTENTS_REASK_RE, "kit_contents", "kit_contents",
     "the kit contents are {0}"),
)


# ---------------------------------------------------------------------------
# Repair vocabulary — what proof phrasing should replace each re-ask
# category. Universal — never product-specific names.
# ---------------------------------------------------------------------------


_REPAIR_PROOF_HINTS: dict[str, str] = {
    "cleaning_dishwasher": (
        "want proof it survives repeated dishwasher cycles without "
        "coating damage"
    ),
    "cleaning_microwave": (
        "want proof microwaving the plate doesn't damage the coating "
        "or affect food contact"
    ),
    "runtime": (
        "want a real-food test showing pasta, rice, soup, and meat stay "
        "within a useful serving-temperature range for that whole window"
    ),
    "temperature": (
        "want proof the temperature stays consistent across the surface "
        "under realistic food load"
    ),
    "charging_usb_c": (
        "want to know charge time, battery lifespan, and whether one "
        "charge handles multiple meals"
    ),
    "bundle_price": (
        "want to know whether the multi-pack discount changes warranty "
        "or shipping versus the single unit"
    ),
    "materials": (
        "want proof of food-contact certification (FDA / LFGB) and "
        "coating durability test data"
    ),
    "kit_contents": (
        "want a clear unbox photo or an itemized list with replacement "
        "part numbers"
    ),
}


def _fact_value_for_anchor(
    fact_card: ProductFactCard, attr: str,
) -> str | None:
    """Look up the plain-text value the fact card has for the given
    attribute name. Returns None if the fact card has no value."""
    val = getattr(fact_card, attr, None)
    if val is None:
        return None
    if isinstance(val, list):
        if not val:
            return None
        return ", ".join(str(v) for v in val[:3])
    if isinstance(val, str):
        return val if val.strip() else None
    return str(val)


def _split_sentences(text: str) -> list[str]:
    """Sentence splitter — same shape as the caveat-leak helper."""
    if not text:
        return []
    placeholder = "<<DOT>>"
    safe = re.sub(
        r"(\d)\.(\d)", lambda m: f"{m.group(1)}{placeholder}{m.group(2)}",
        text,
    )
    raw = re.split(r"(?<=[\.\?\!])\s+|\n+", safe)
    return [
        p.replace(placeholder, ".").strip()
        for p in raw
        if p.strip()
    ]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit_provided_fact_lock_v2(
    *,
    fact_card: ProductFactCard,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Detect known-fact re-asks across the v2 fact-lock surface
    (cleaning, runtime, temperature, charging, bundle price,
    materials, kit contents)."""
    audit: dict[str, Any] = {
        "phase": "10b_3_provided_fact_lock_v2",
        "fact_lock_summary": {
            "primary_price": fact_card.primary_price,
            "bundle_price": fact_card.bundle_price,
            "runtime_known": bool(fact_card.runtime_facts),
            "temperature_known": bool(fact_card.temperature_facts),
            "cleaning_known": bool(fact_card.cleaning_facts),
            "materials_known": bool(fact_card.materials),
            "charging_known": bool(fact_card.charging_facts),
            "kit_contents_known": bool(fact_card.kit_contents),
            "excluded_known": bool(fact_card.excluded_features),
            "launch_state_known": bool(fact_card.launch_state),
            "competitors_known": bool(
                fact_card.competitors_or_alternatives
            ),
        },
        "known_fact_reask_count": 0,
        "fact_categories_violated": [],
        "by_category": {},
        "repair_examples": [],
        "examples": [],
        "repaired_count": 0,
        "unrepaired_count": 0,
    }

    by_category: dict[str, int] = {}
    examples: list[dict[str, Any]] = audit["examples"]
    violated: list[str] = audit["fact_categories_violated"]

    def _scan(blob: dict[str, Any], origin: str) -> None:
        text = blob.get("text") or ""
        if not text:
            return
        for rx, category, attr, _anchor in _FACT_REASK_RULES:
            # Only fire if the fact card *has* a value to defend.
            if _fact_value_for_anchor(fact_card, attr) is None:
                continue
            m = rx.search(text)
            if not m:
                continue
            audit["known_fact_reask_count"] += 1
            by_category[category] = by_category.get(category, 0) + 1
            if category not in violated:
                violated.append(category)
            if len(examples) < 12:
                examples.append({
                    "origin": origin,
                    "kind": category,
                    "persona_id": str(blob.get("persona_id") or ""),
                    "match": m.group(0),
                    "excerpt": text[:240],
                })

    for t in turn_texts:
        _scan(t, "turn")
    for b in ballot_texts:
        _scan(b, "ballot")

    audit["by_category"] = by_category
    audit["unrepaired_count"] = audit["known_fact_reask_count"]
    audit["any_violations"] = audit["known_fact_reask_count"] > 0
    return audit


# ---------------------------------------------------------------------------
# Soft repair — rewrites a single text by replacing each fact-reask
# sentence with a "Since the brief says X, I'd want proof Y" form.
# ---------------------------------------------------------------------------


def repair_known_fact_reask(
    text: str,
    fact_card: ProductFactCard,
) -> tuple[str, int, list[dict[str, Any]]]:
    """Rewrite known-fact re-ask sentences. Returns
    `(repaired_text, repair_count, repair_examples)`.

    For each sentence that matches a re-ask pattern AND the fact
    card has a known value for that category, the sentence is
    replaced with a verification-form sentence. Sentences that
    don't match are kept unchanged.
    """
    if not text:
        return "", 0, []
    sentences = _split_sentences(text)
    out_sentences: list[str] = []
    repair_count = 0
    examples: list[dict[str, Any]] = []
    for s in sentences:
        replaced = False
        for rx, category, attr, anchor_template in _FACT_REASK_RULES:
            if not rx.search(s):
                continue
            value = _fact_value_for_anchor(fact_card, attr)
            if value is None:
                continue
            if "{0}" in anchor_template:
                anchor = anchor_template.format(value)
            else:
                anchor = anchor_template
            proof = _REPAIR_PROOF_HINTS.get(category, "want proof it holds up")
            new_sentence = (
                f"Since {anchor}, I'd {proof}."
            )
            out_sentences.append(new_sentence)
            repair_count += 1
            if len(examples) < 6:
                examples.append({
                    "category": category,
                    "before": s.strip(),
                    "after": new_sentence,
                })
            replaced = True
            break
        if not replaced:
            out_sentences.append(s)
    return " ".join(out_sentences).strip(), repair_count, examples
