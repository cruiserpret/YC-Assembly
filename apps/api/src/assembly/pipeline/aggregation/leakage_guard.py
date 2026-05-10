"""Phase 7 quality gate — factual-claim leakage detector.

The validator catches forbidden language ("the market is", "spend $X on Meta
ads"). The claim_validator catches every emitted `factual_claim` that lacks
a verbatim source_excerpt. But neither catches a third class of issue:

  *Unbound factual claims that smuggle into a `summary` string.*

For example, a section's `summary` could say:

  "Shopify Magic costs $0 and includes generative AI for ad copy."

That sentence makes two factual claims about a real product (cost, feature)
without binding either to an evidence_items row. It would not appear in
`factual_claims`, so claim_validator never sees it. The validator's
forbidden-language rules don't fire because the sentence is grammatical
subjective-looking prose.

This module patrols that gap. It scans every text leaf in a parsed section
for sentences that:

  - mention a real-world entity (competitor name from the brief), AND
  - contain a strong factual signal (dollar amount, "free", "includes",
    "supports", "offers", "costs", numeric percentage, etc.), AND
  - lack a subjective qualifier ("seemed", "appeared", "agents framed",
    "tended to", "in this simulation", "the supplied evidence does not",
    etc.).

Sentences satisfying all three are flagged. The synthesis layer treats
them as a repair-trigger — the LLM is told to either move the claim into
`factual_claims` (with a verbatim `source_excerpt`) or rephrase it
subjectively. This is the structural backstop that keeps factual-looking
prose from sneaking into `summary` text without evidence binding.

Subjective interpretation ("the society seemed cautious") never fires
this rule — by design, the user's Phase 7 rule.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


# Strong factual signals that, when paired with a competitor name and not
# softened by a subjective qualifier, indicate an unbound factual claim.
_FACTUAL_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\s*\d", re.IGNORECASE),                                  # $X pricing
    re.compile(r"\b\d+(?:\.\d+)?\s*%", re.IGNORECASE),                      # \d% rates
    re.compile(r"\bcosts?\s+\$", re.IGNORECASE),
    re.compile(r"\bpriced?\s+at\s+\$", re.IGNORECASE),
    re.compile(r"\bfor\s+free\b", re.IGNORECASE),
    re.compile(r"\bis\s+free\b", re.IGNORECASE),
    re.compile(r"\bfree\s+(?:tier|plan|version)\b", re.IGNORECASE),
    re.compile(r"\b(?:offers|provides|includes|features|supports)\s+\w+", re.IGNORECASE),
    re.compile(r"\b(?:has|have)\s+\d+\+?\s+\w+", re.IGNORECASE),             # has 1000+ users
    re.compile(r"\b\d+\+?\s+(?:reviews|customers|users|merchants)\b", re.IGNORECASE),
    re.compile(r"\baccording\s+to\s+(?:reviews?|customers?|users?)\b", re.IGNORECASE),
    re.compile(r"\b(?:reviewers?|customers?|users?)\s+(?:said|reported|wrote)\b", re.IGNORECASE),
)


# Subjective qualifiers that, when present in the same sentence, soften
# any factual signal into simulated interpretation. Order doesn't matter —
# any match disqualifies the sentence.
_SUBJECTIVE_QUALIFIERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bagents?\s+(?:treated|framed|seemed|appeared|portrayed|portraying|reached)", re.IGNORECASE),
    re.compile(r"\bagents?\s+(?:in\s+the\s+simulation|portraying|who)", re.IGNORECASE),
    re.compile(r"\bthe\s+(?:society|simulation|agents?)\s+(?:seemed|appeared|tended|treated)", re.IGNORECASE),
    re.compile(r"\bin\s+(?:the|this)\s+simulat", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+simulated\s+society", re.IGNORECASE),
    re.compile(r"\b(?:seemed|appeared|tended)\s+to\b", re.IGNORECASE),
    re.compile(r"\b(?:simulated|imagined)\s+(?:society|reaction|response)", re.IGNORECASE),
    re.compile(r"\bthe\s+supplied\s+(?:evidence|data|brief)\b", re.IGNORECASE),
    re.compile(r"\bthe\s+brief['']?s?\b", re.IGNORECASE),
    re.compile(r"\b(?:no|did\s+not|didn['']?t)\s+(?:competitor|external|published|public)\b", re.IGNORECASE),
    re.compile(r"\b(?:reconstructed|inferred|derived)\s+from\b", re.IGNORECASE),
    re.compile(r"\bnot\s+(?:available|present|supplied|surfaced)\b", re.IGNORECASE),
    # "supplied X" usually refers to the user's own input (e.g. "the
    # supplied starter price"), which is not a factual claim about a
    # competitor.
    re.compile(r"\bthe\s+supplied\b", re.IGNORECASE),
)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class LeakageHit:
    """One suspected unbound factual claim."""

    field_path: str
    sentence: str
    competitor: str
    factual_pattern: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_for_unbound_factual_claims(
    parsed: BaseModel,
    *,
    competitor_names: Iterable[str],
) -> list[LeakageHit]:
    """Walk every string leaf in `parsed` and return suspected unbound
    factual claims. Sentences that pair a competitor mention with a strong
    factual signal AND lack a subjective qualifier are flagged.

    `competitor_names` is the closed set of names from the brief +
    competitor_evidence; we only flag mentions of names the report is
    actually allowed to reference. (Anchors from outside this list are
    already caught by the existing anchor-resolution check.)
    """
    names = [n for n in competitor_names if n and len(n.strip()) >= 2]
    if not names:
        return []
    name_pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in names) + r")\b",
        re.IGNORECASE,
    )

    hits: list[LeakageHit] = []
    for path, text in _walk_string_leaves(parsed):
        if not text:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            comp_match = name_pattern.search(sentence)
            if comp_match is None:
                continue
            # Subjective qualifier in the same sentence → not a leak.
            if any(q.search(sentence) for q in _SUBJECTIVE_QUALIFIERS):
                continue
            for fp in _FACTUAL_SIGNAL_PATTERNS:
                m = fp.search(sentence)
                if m is None:
                    continue
                hits.append(
                    LeakageHit(
                        field_path=path,
                        sentence=sentence.strip()[:300],
                        competitor=comp_match.group(0),
                        factual_pattern=m.group(0),
                    )
                )
                break  # one hit per sentence is enough
    return hits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk_string_leaves(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Yield (field_path, string_value) pairs over Pydantic / dict / list."""
    out: list[tuple[str, str]] = []
    if isinstance(value, BaseModel):
        return _walk_string_leaves(value.model_dump(mode="json"), path)
    if isinstance(value, str):
        # Skip UUID-like strings.
        if not _is_uuid_like(value):
            out.append((path or "<root>", value))
    elif isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}" if path else str(k)
            # Skip the source_excerpt + factual_claims subtree — those are
            # the claim-validator's domain. We only patrol summary-style
            # text.
            if k in {"source_excerpt", "factual_claims"}:
                continue
            out.extend(_walk_string_leaves(v, child))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.extend(_walk_string_leaves(item, f"{path}[{i}]"))
    return out


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_uuid_like(value: str) -> bool:
    return bool(_UUID_RE.match(value.strip()))
