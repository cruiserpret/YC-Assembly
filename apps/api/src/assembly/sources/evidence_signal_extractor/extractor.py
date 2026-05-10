"""Phase 9A.1 — universal evidence-signal extractor.

`extract_evidence_signals(...)` takes one accepted evidence item and
emits a list of atomic `EvidenceSignal`s. Pure function. No LLM.

Signals come from THREE universal sources (all derived at runtime,
no per-product templates):

  1. Competitor-mention signal: any `brief.competitors` token whose
     name (or first-word fallback) appears in text → emits a
     `competitor_usage_signal` with `inferred_role=competitor_user_<X>`.

  2. Substitute-mention signal: any `anchor_plan.substitute_anchor_terms`
     whose phrase appears in text → emits a `substitute_usage_signal`
     with `inferred_role=substitute_user_<Y>`.

  3. Universal-lexicon signals: each lexicon in
     `UNIVERSAL_SIGNAL_LEXICONS` (price/value, trust/proof,
     safety/visibility, format, convenience, performance, objection,
     use-case) is matched as substrings → emits the corresponding
     `signal_type` and `inferred_role`.

A single source can produce 1-N atomic signals, where N is bounded
by the count of distinct (role × signal_type) pairs that appear
with evidence.

NO LLM. NO network. Same inputs → same output.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from assembly.sources.evidence_signal_extractor.constants import (
    UNIVERSAL_SIGNAL_LEXICONS,
)
from assembly.sources.evidence_signal_extractor.schemas import (
    EvidenceSignal,
)


def _slug(s: str) -> str:
    if not s:
        return ""
    s2 = s.lower().strip().replace("'", "")
    return re.sub(r"[^\w]+", "_", s2).strip("_")


def _excerpt_around_match(text: str, match: re.Match) -> str:
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 120)
    return text[start:end].strip()


def _short_id(*parts: str) -> str:
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _detect_subsegment(text: str) -> str | None:
    """Detect a coarse buyer subsegment from universal language cues.
    Returns one of: 'parent_buying_for_kids', 'commuter', 'runner',
    'cyclist', 'dog_walker', 'student', or None.

    Universal: phrases below are demographic/context cues, NOT
    product-specific roles. Adding a new product domain doesn't
    change this function."""
    low = (text or "").lower()
    pairs = (
        ("parent_buying_for_kids", (
            "for my kid", "for my child", "for my teen",
            "for my son", "for my daughter", "buying for",
            "as a parent", "for kids", "for teens",
            "as a mom", "as a dad",
        )),
        ("commuter", (
            "commute", "commuter", "commuting", "to work",
            "morning commute", "evening commute",
        )),
        ("runner", (
            "long run", "long runs", "marathon", "half-marathon",
            "trail run", "ultra runner", "ultra-runner",
            "training run", "race day", "running club",
        )),
        ("cyclist", (
            "cycling", "cyclist", "cycle", "biking",
            "bike commute", "road biker", "mountain biker",
        )),
        ("dog_walker", (
            "walking the dog", "walking my dog", "dog walk",
            "dog walker", "dog walks", "leash",
        )),
        ("student", (
            "campus", "student", "students", "college", "university",
            "dorm", "school night",
        )),
    )
    for label, keys in pairs:
        for k in keys:
            if k in low:
                return label
    return None


def extract_evidence_signals(
    *,
    evidence_item: dict[str, Any],
    competitors: list[str],
    substitutes: list[str],
    use_case_terms: list[str] | None = None,
    objection_terms: list[str] | None = None,
    max_signals_per_item: int = 6,
) -> list[EvidenceSignal]:
    """Extract atomic signals from one accepted evidence item.

    `evidence_item` shape: any dict with at least these keys:
      * `provider` (str)
      * `planned_source_record_id_synthetic` (str) OR `url`
      * `snippet` (str) OR `content_preview` (str)
      * `matched_terms` (list[str], optional)
    """
    text = (
        evidence_item.get("snippet")
        or evidence_item.get("content_preview")
        or evidence_item.get("content")
        or ""
    ).strip()
    if not text or len(text) < 30:
        return []

    sid = (
        evidence_item.get("planned_source_record_id_synthetic")
        or evidence_item.get("url")
        or _short_id(text[:80])
    )
    provider = evidence_item.get("provider") or "unknown"
    domain = evidence_item.get("domain")
    url = evidence_item.get("url")
    low = text.lower()
    out: list[EvidenceSignal] = []
    seen_keys: set[str] = set()

    def _add(
        *,
        signal_type: str,
        inferred_role: str,
        excerpt: str,
        reason: str,
        competitor_or_sub: str | None = None,
        use_case: str | None = None,
        objection: str | None = None,
        trust_proof: str | None = None,
        price_value: str | None = None,
        confidence: str = "medium",
    ) -> None:
        key = f"{signal_type}::{inferred_role}::{excerpt[:60].lower()}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        if len(out) >= max_signals_per_item:
            return
        subseg = _detect_subsegment(excerpt) or _detect_subsegment(text)
        out.append(EvidenceSignal(
            signal_id=_short_id(sid, signal_type, inferred_role, excerpt[:40]),
            source_record_synthetic_id=str(sid),
            provider=provider,
            source_url=url,
            domain=domain,
            signal_type=signal_type,  # type: ignore[arg-type]
            inferred_role=inferred_role,
            inferred_subsegment=subseg,
            competitor_or_substitute_context=competitor_or_sub,
            use_case_context=use_case,
            objection_pattern=objection,
            trust_or_proof_requirement=trust_proof,
            price_or_value_signal=price_value,
            behavior_context=subseg,
            evidence_excerpt=excerpt[:300],
            confidence=confidence,  # type: ignore[arg-type]
            reason_for_signal=reason,
        ))

    # 1. Competitor-mention signals
    # Phase 10B.3+ hotfix: each competitor gets ONE canonical slug
    # used for BOTH the full-name match and the first-word fallback.
    # Without this, "Samsung Family Hub refrigerator" matched in some
    # snippets and "Samsung" alone matched in others, producing the
    # distinct slugs `competitor_user_samsung_family_hub_refrigerator`
    # AND `competitor_user_samsung` for the same competitor — which
    # then bypassed the competitor-share quality gate by counting
    # the same Samsung pool as two roles.
    for c in competitors:
        c_low = c.lower()
        canonical_slug = _slug(c)  # one slug per competitor
        m = re.search(rf"\b{re.escape(c_low)}\b", low)
        if m is not None:
            _add(
                signal_type="competitor_usage_signal",
                inferred_role=f"competitor_user_{canonical_slug}",
                excerpt=_excerpt_around_match(text, m),
                reason=f"text contains competitor name {c!r}",
                competitor_or_sub=c,
                confidence="high",
            )
            continue
        # First-word fallback (e.g., "noxgear" of "Noxgear Tracer2")
        first = c.split()[0]
        if (
            len(first) >= 4
            and first.lower() not in ("the", "and", "for", "all")
        ):
            m = re.search(rf"\b{re.escape(first.lower())}\b", low)
            if m is not None:
                _add(
                    signal_type="competitor_usage_signal",
                    inferred_role=f"competitor_user_{canonical_slug}",
                    excerpt=_excerpt_around_match(text, m),
                    reason=(
                        f"text contains competitor first-word "
                        f"{first!r} (fallback for {c!r})"
                    ),
                    competitor_or_sub=c,
                    confidence="medium",
                )

    # 2. Substitute-mention signals
    for s in substitutes:
        s_low = s.lower()
        m = re.search(rf"\b{re.escape(s_low)}\b", low)
        if m is not None:
            slug = _slug(s)
            _add(
                signal_type="substitute_usage_signal",
                inferred_role=f"substitute_user_{slug}",
                excerpt=_excerpt_around_match(text, m),
                reason=f"text contains substitute term {s!r}",
                competitor_or_sub=s,
                confidence="medium",
            )

    # 3. Universal-lexicon signals
    for sig_type, role, kws in UNIVERSAL_SIGNAL_LEXICONS:
        for kw in kws:
            m = re.search(rf"\b{re.escape(kw)}\b", low)
            if m is None:
                continue
            ex = _excerpt_around_match(text, m)
            kwargs: dict[str, Any] = {
                "signal_type": sig_type,
                "inferred_role": role,
                "excerpt": ex,
                "reason": (
                    f"universal lexicon hit {kw!r} → {sig_type}"
                ),
                "confidence": "medium",
            }
            if sig_type == "objection_signal":
                kwargs["objection"] = kw
            elif sig_type == "trust_proof_signal":
                kwargs["trust_proof"] = kw
            elif sig_type == "price_value_signal":
                kwargs["price_value"] = kw
            elif sig_type == "use_case_signal":
                kwargs["use_case"] = kw
            _add(**kwargs)
            break  # one hit per lexicon is enough — keep signals atomic

    return out
