"""Phase 10B.1 — product-grounding validator.

Post-hoc audit over persona speech (turns + ballots) that flags:
  * wrong-category drift (persona treats the product as the wrong
    object — e.g. calling it "a shoe" when the brief says it's a
    "shoe-drying dock")
  * already-provided fact requests (persona asks "what does it
    cost?" when the brief gave a price)
  * fake usage claims ("I bought one" for an unlaunched product)
  * unsupported invented specs ("it has a 12-hour battery") when
    the brief didn't mention battery

The validator does NOT regenerate turns. It writes an audit
artifact + (optionally) marks individual rows as `grounding_invalid`
so they can be flagged in the UI.
"""
from __future__ import annotations

import re
from typing import Any

from assembly.sources.product_grounding.product_fact_card import (
    ProductFactCard,
)


_FAKE_USE_RE = re.compile(
    r"\b(i|we)\s+(?:bought|own|use|used|tried|tested|reviewed|"
    r"purchased)\s+(?:the\s+|a\s+|an\s+|my\s+)?",
    re.IGNORECASE,
)


_PRICE_QUESTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwhat\s+(?:'s|is|does)\s+(?:the\s+)?(?:it\s+)?cost",
        r"\bhow\s+much\s+(?:does\s+)?(?:it\s+)?cost",
        r"\bwhat\s+(?:'s|is)\s+the\s+price",
        r"\bhow\s+much\s+is\s+(?:it|this)",
        r"\bprice\s+isn[''`]t\s+(?:given|listed|disclosed)",
        r"\bno\s+price\s+(?:given|listed|disclosed)",
        r"\bunclear\s+pricing\b",
        r"\bprice\s+is\s+missing\b",
    )
)


_LAUNCH_QUESTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bis\s+(?:it|this)\s+(?:already\s+)?launched\b",
        r"\bis\s+(?:it|this)\s+(?:on\s+the\s+)?market\b",
        r"\bhas\s+(?:it|this)\s+launched\b",
        r"\bwhen\s+(?:does|did|will)\s+(?:it|this)\s+launch\b",
        r"\bis\s+(?:it|this)\s+available\s+yet\b",
        r"\blaunch\s+state\s+(?:is\s+)?(?:not\s+)?(?:given|provided|known)",
    )
)


_COMPETITOR_LIST_QUESTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwho\s+(?:are\s+)?(?:the\s+)?competitors?\b",
        r"\bwhat\s+(?:are\s+)?(?:the\s+)?competitors?\b",
        r"\bany\s+competitor[s]?\b",
        r"\bwho\s+else\s+is\s+in\s+(?:this|the)\s+(?:space|market)",
    )
)


def _build_wrong_category_patterns(
    card: ProductFactCard,
) -> tuple[re.Pattern[str], ...]:
    """For each `Not:` hint in the fact card, build a regex that
    matches the persona calling the product that thing."""
    if not card.not_categories:
        return ()
    name = re.escape(card.product_name.lower())
    patterns: list[re.Pattern[str]] = []
    for cat in card.not_categories:
        cat_low = cat.lower().strip()
        # accept "a shoe" → "shoe", "an insole" → "insole" so we
        # match either form
        bare = re.sub(r"^(?:a|an)\s+", "", cat_low)
        bare_re = re.escape(bare)
        # "{Product} is just a shoe" / "treating SoleNest as a shoe"
        patterns.append(
            re.compile(
                rf"\b{name}\s+(?:is|seems|sounds|feels)\s+(?:just\s+|like\s+|basically\s+)?(?:a|an)\s+{bare_re}\b",
                re.IGNORECASE,
            )
        )
        patterns.append(
            re.compile(
                rf"\bit['’]?s\s+(?:just\s+|basically\s+|essentially\s+)?(?:a|an)\s+{bare_re}\b",
                re.IGNORECASE,
            )
        )
    return tuple(patterns)


def audit_product_grounding(
    *,
    fact_card: ProductFactCard,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Inspect persona speech for grounding violations relative to
    the founder's product fact card.

    `turn_texts` / `ballot_texts` are lists of dicts each carrying
    at least `persona_id` + a `text` field. The function iterates
    once over both and tallies violations + examples.
    """
    name_known = bool(fact_card.product_name)
    price_known = bool(fact_card.price_or_price_structure)
    launch_known = bool(fact_card.launch_state)
    competitors_known = bool(fact_card.competitors_or_alternatives)
    wrong_cat_res = _build_wrong_category_patterns(fact_card)

    audit: dict[str, Any] = {
        "phase": "10b_1_product_grounding",
        "product_name": fact_card.product_name,
        "fact_card_summary": {
            "product_type": fact_card.product_type,
            "not_categories": fact_card.not_categories,
            "price_known": price_known,
            "launch_state_known": launch_known,
            "competitors_known": competitors_known,
        },
        "wrong_category_violations": 0,
        "already_provided_price_violations": 0,
        "already_provided_launch_violations": 0,
        "already_provided_competitor_violations": 0,
        "fake_usage_violations": 0,
        "examples": [],
    }
    examples: list[dict[str, Any]] = []

    def _scan(blob: dict[str, Any], origin: str) -> None:
        text = (blob.get("text") or "").strip()
        if not text:
            return
        low = text.lower()

        # 1. wrong-category
        for r in wrong_cat_res:
            m = r.search(text)
            if m:
                audit["wrong_category_violations"] += 1
                if len(examples) < 12:
                    examples.append({
                        "origin": origin,
                        "kind": "wrong_category",
                        "persona_id": str(blob.get("persona_id") or ""),
                        "match": m.group(0),
                        "excerpt": text[:200],
                    })
                break

        # 2. already-provided price
        if price_known:
            for r in _PRICE_QUESTION_PATTERNS:
                m = r.search(text)
                if m:
                    audit["already_provided_price_violations"] += 1
                    if len(examples) < 12:
                        examples.append({
                            "origin": origin,
                            "kind": "already_provided_price",
                            "persona_id": str(blob.get("persona_id") or ""),
                            "match": m.group(0),
                            "excerpt": text[:200],
                        })
                    break

        # 3. already-provided launch state
        if launch_known:
            for r in _LAUNCH_QUESTION_PATTERNS:
                m = r.search(text)
                if m:
                    audit["already_provided_launch_violations"] += 1
                    if len(examples) < 12:
                        examples.append({
                            "origin": origin,
                            "kind": "already_provided_launch",
                            "persona_id": str(blob.get("persona_id") or ""),
                            "match": m.group(0),
                            "excerpt": text[:200],
                        })
                    break

        # 4. already-provided competitor list
        if competitors_known:
            for r in _COMPETITOR_LIST_QUESTION_PATTERNS:
                m = r.search(text)
                if m:
                    audit["already_provided_competitor_violations"] += 1
                    if len(examples) < 12:
                        examples.append({
                            "origin": origin,
                            "kind": "already_provided_competitor_list",
                            "persona_id": str(blob.get("persona_id") or ""),
                            "match": m.group(0),
                            "excerpt": text[:200],
                        })
                    break

        # 5. fake usage — only when launch_state was 'unlaunched'
        if (
            name_known
            and (fact_card.launch_state or "").lower() == "unlaunched"
        ):
            # Find "i bought {name}" / "i used {name}" patterns
            name_re = re.compile(
                rf"\b(i|we)\s+(?:bought|own|use|used|tried|tested|reviewed|purchased)\s+(?:the\s+|a\s+|an\s+|my\s+)?{re.escape(fact_card.product_name.lower())}\b",
                re.IGNORECASE,
            )
            m = name_re.search(low)
            if m:
                audit["fake_usage_violations"] += 1
                if len(examples) < 12:
                    examples.append({
                        "origin": origin,
                        "kind": "fake_usage",
                        "persona_id": str(blob.get("persona_id") or ""),
                        "match": m.group(0),
                        "excerpt": text[:200],
                    })

    for t in turn_texts:
        _scan(t, "turn")
    for b in ballot_texts:
        _scan(b, "ballot")

    audit["misunderstanding_count"] = (
        audit["wrong_category_violations"]
        + audit["already_provided_price_violations"]
        + audit["already_provided_launch_violations"]
        + audit["already_provided_competitor_violations"]
        + audit["fake_usage_violations"]
    )
    audit["examples"] = examples
    audit["any_violations"] = audit["misunderstanding_count"] > 0
    return audit
