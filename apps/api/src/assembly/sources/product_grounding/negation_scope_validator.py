"""Phase 10B.4 — Negation-scope fact validator + soft repair.

The PantryPulse run revealed a critical inversion failure: the
brief said "tiny wide-angle camera + does not record video / does
not identify people", and personas converted that into "no camera"
and "without a camera". That is a fact inversion — the negation
applied to the BEHAVIOR of the camera, not the existence of the
camera.

This module:
  * detects inversion phrases ("no camera", "without a camera",
    "no-camera tracker", "no scanning", "without scanning") in
    persona texts whenever the Product Fact Lock asserts the
    sensor / input mechanism *exists*;
  * provides a soft repair that rewrites the offending sentence
    into still-image / privacy / workflow-friction language so
    the persona's underlying concern survives without breaking
    the fact lock.
"""
from __future__ import annotations

import re
from typing import Any

from assembly.sources.product_grounding.product_fact_card import (
    ProductFactCard,
)


# ---------------------------------------------------------------------------
# Inversion patterns
# ---------------------------------------------------------------------------


# (pattern, fact_lock_key_required_true, kind, repair_anchor_template,
#  proof_phrase)
#
# Anchor templates are PRODUCT-AGNOSTIC. The literal "{product}"
# placeholder is filled at repair time from `fact_card.product_name`.
# Anchor descriptors only reference fact-lock concepts that any
# product can have ("camera", "still images", "input mechanisms")
# rather than product-specific descriptors. Specific concrete
# input-mechanism names ("barcode/NFC scanning", "USB-C charging")
# are derived from `fact_card.input_mechanism_facts` /
# `sensing_facts` at repair time.
_CAMERA_INVERSION_PATTERNS: tuple[
    tuple[re.Pattern[str], str, str, str, str], ...
] = (
    (re.compile(
        r"\b(?:no|without\s+a|without\s+the|no[\- ]camera)"
        r"\s+(?:built[\- ]in\s+|wide[\- ]angle\s+)?camera\b",
        re.IGNORECASE,
    ), "has_camera", "no_camera",
     "the brief says {product} has a camera that captures "
     "{capture_phrase} during scan events",
     "want to know exactly how the captured images are stored, "
     "when they're deleted, and whether the visible LED + physical "
     "shutter are enforced in firmware"),
    (re.compile(
        r"\bno[\- ]camera\s+(?:tracker|inventory|device|kit|product|system)\b",
        re.IGNORECASE,
    ), "has_camera", "no_camera_compound",
     "the brief says {product} has a camera (used for "
     "{capture_phrase} during scan events)",
     "want clarity on the still-image lifecycle and the physical "
     "shutter behavior, not absence of a camera"),
    (re.compile(
        r"\b(?:does\s+not|doesn't|don't)\s+use\s+(?:a\s+)?camera\b",
        re.IGNORECASE,
    ), "has_camera", "no_camera_use",
     "the brief says {product} DOES use a camera, just for "
     "{capture_phrase} during scan events",
     "want clear documentation of when the camera fires and how "
     "the captured stills are handled"),
    (re.compile(
        r"\bno\s+visual\s+capture\b|"
        r"\bnot\s+watching\s+with\s+a\s+camera\b|"
        r"\bnot\s+(?:imaging|photographing)\s+(?:my\s+|the\s+|its\s+)?\w+\b",
        re.IGNORECASE,
    ), "has_camera", "no_visual_capture",
     "the brief says {product} captures {capture_phrase} during "
     "scan events",
     "want to understand the still-image retention + deletion controls"),
)

# Privacy-fact inversion: agents collapsing the privacy guarantees
# into "no camera". Example: "no face recognition" → don't read that
# as "no camera".
_PRIVACY_OVER_INVERSION_PATTERNS: tuple[
    tuple[re.Pattern[str], str, str, str, str], ...
] = (
    (re.compile(
        r"\bno\s+face\s+recognition\s+because\s+(?:there\s+is|"
        r"there's)\s+no\s+camera\b|"
        r"\bno\s+camera\s+so\s+no\s+face\s+recognition\b",
        re.IGNORECASE,
    ), "has_camera", "privacy_collapse_to_no_camera",
     "the brief says the camera EXISTS but explicitly does NOT do "
     "facial recognition — the no-face-recognition guarantee is a "
     "behavioral bound, not the absence of a camera",
     "want proof of how face recognition is prevented in firmware"),
)


_INPUT_INVERSION_PATTERNS: tuple[
    tuple[re.Pattern[str], str, str, str, str], ...
] = (
    (re.compile(
        r"\bno\s+scanning\b|"
        r"\bwithout\s+scanning\b|"
        r"\bif\s+there\s+is\s+no\s+scanning\b|"
        r"\bif\s+there's\s+no\s+scanning\b",
        re.IGNORECASE,
    ), "has_barcode_scanning|has_nfc_scanning|has_qr_scanning|has_rfid_scanning",
     "no_scanning",
     "the brief says {product} has {input_mechanisms_phrase}",
     "want to know whether that input workflow is faster than "
     "manual logging in a typical real-world session"),
    (re.compile(
        r"\bjust\s+a\s+magnetic\s+(?:note\s*pad|notepad|magnet|"
        r"sticky\s+note)\b|"
        r"\b(?:just|only)\s+a\s+magnet(?:\s+and\s+(?:a\s+)?promise)?\b",
        re.IGNORECASE,
    ), "has_barcode_scanning|has_nfc_scanning|has_qr_scanning|has_rfid_scanning",
     "magnet_dismissal",
     "the brief says {product} pairs the form factor with "
     "{input_mechanisms_phrase} — it's not just hardware around a "
     "free habit",
     "want a real demo of the input workflow versus the user's "
     "current habit"),
    (re.compile(
        r"\bno\s+input\s+mechanism\b|"
        r"\bno\s+way\s+to\s+(?:capture|input|record)\s+items\b",
        re.IGNORECASE,
    ), "has_barcode_scanning|has_nfc_scanning|has_manual_app_entry|has_voice_input|has_rfid_scanning|has_qr_scanning|has_bluetooth_input",
     "no_input_mechanism",
     "the brief lists explicit input mechanisms ("
     "{input_mechanisms_phrase})",
     "want to know whether those mechanisms feel low-friction in a "
     "real-world session"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> list[str]:
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


_INPUT_MECHANISM_HUMAN_LABEL: dict[str, str] = {
    "has_barcode_scanning": "barcode scanning",
    "has_nfc_scanning": "NFC scanning",
    "has_reusable_nfc_tags": "reusable NFC tags",
    "has_rfid_scanning": "RFID scanning",
    "has_qr_scanning": "QR-code scanning",
    "has_voice_input": "voice input",
    "has_manual_app_entry": "manual app entry",
    "has_bluetooth_input": "a Bluetooth-paired input device",
}


def _input_mechanisms_phrase(fact_card: ProductFactCard) -> str:
    """Build a comma-separated, human-readable list of the input
    mechanisms the fact card asserts the product has. Universal —
    no per-product names baked in."""
    inputs = fact_card.input_mechanism_facts or {}
    labels: list[str] = []
    for key in (
        "has_barcode_scanning",
        "has_nfc_scanning",
        "has_reusable_nfc_tags",
        "has_rfid_scanning",
        "has_qr_scanning",
        "has_voice_input",
        "has_manual_app_entry",
        "has_bluetooth_input",
    ):
        if inputs.get(key) is True:
            labels.append(_INPUT_MECHANISM_HUMAN_LABEL[key])
    if not labels:
        return "the input mechanisms listed in the brief"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + ", and " + labels[-1]


def _capture_phrase(fact_card: ProductFactCard) -> str:
    """Build a description of WHAT the camera captures, derived
    from the sensing facts. Universal — no PantryPulse-specific
    'shelf/label' descriptor unless the brief itself surfaces
    that vocabulary."""
    sensing = fact_card.sensing_facts or {}
    if sensing.get("captures_still_images") is True:
        return "still images"
    if sensing.get("has_camera") is True:
        return "images"
    return "images"


def _format_anchor(
    template: str, fact_card: ProductFactCard,
) -> str:
    """Substitute the dynamic placeholders in an anchor template
    using fact-card data. Safe-formats so missing keys don't crash."""
    product = fact_card.product_name or "the product"
    return template.format(
        product=product,
        capture_phrase=_capture_phrase(fact_card),
        input_mechanisms_phrase=_input_mechanisms_phrase(fact_card),
    )


def _check_forbidden_feature_in_sentence(
    sentence: str,
    canonical: str,
    tokens: tuple[str, ...],
) -> tuple[bool, str | None]:
    """Return (is_positive_mention, matched_token).

    A "positive mention" means the sentence is using the feature as
    if it existed — not denying or comparing it. We treat the
    mention as POSITIVE unless the surrounding ~40-char left
    context contains a negation word ("no", "not", "doesn't",
    "without", "instead of", "rather than") that scopes over it.

    Universal — no product-specific carve-outs.
    """
    low = sentence.lower()
    for tok in tokens:
        tok_low = tok.lower()
        # Word-boundary search. For multi-word tokens we use a
        # simple substring with a sanity check on the surrounding
        # chars.
        idx = 0
        while True:
            pos = low.find(tok_low, idx)
            if pos < 0:
                break
            # Boundary check on the right side: next char (if any)
            # must be non-alphanumeric.
            end = pos + len(tok_low)
            right = low[end:end + 1]
            if right and right.isalnum():
                idx = end
                continue
            # Boundary check on the left side: prev char (if any)
            # must be non-alphanumeric.
            left_char = low[pos - 1:pos] if pos > 0 else ""
            if left_char and left_char.isalnum():
                idx = end
                continue
            # Inspect the left context for a negation that scopes
            # over the token. We look back up to 40 chars.
            ctx_start = max(0, pos - 40)
            ctx = low[ctx_start:pos]
            negation_terms = (
                "no ", "not ", "n't ", "without ", "instead of ",
                "rather than ", "no-", "don't ", "doesn't ",
                "isn't ", "aren't ", "lacks ", "lack of ",
            )
            if any(neg in ctx for neg in negation_terms):
                idx = end
                continue
            # Also skip if the token sits inside a known forbidden-
            # feature description sentence the founder wrote
            # verbatim (the brief itself is the canonical source —
            # we never flag the source sentence).
            return True, tok
        # done with this token
    return False, None


def audit_forbidden_features(
    *,
    fact_card: ProductFactCard,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Phase 10B.6 — flag agent text that mentions any explicitly
    forbidden feature as if it existed. Universal — runs against
    whatever `forbidden_features` the brief parser extracted.

    Returns an audit dict suitable for
    `forbidden_features_quality.json`."""
    from assembly.sources.product_grounding.forbidden_features import (
        expand_forbidden_tokens,
    )

    audit: dict[str, Any] = {
        "phase": "10b_6_forbidden_features",
        "forbidden_features": [
            {
                "canonical_name": ff.canonical_name,
                "match_kind": ff.match_kind,
                "source_sentence": ff.source_sentence,
            }
            for ff in (fact_card.forbidden_features or [])
        ],
        "forbidden_feature_count": len(fact_card.forbidden_features or []),
        "positive_mention_count": 0,
        "by_feature": {},
        "examples_before_after": [],
        "examples": [],
        "repaired_count": 0,
        "unrepaired_count": 0,
    }

    if not fact_card.forbidden_features:
        audit["any_violations"] = False
        audit["pass"] = True
        return audit

    # Tokenize each forbidden feature once.
    feature_tokens: list[tuple[Any, tuple[str, ...], str]] = []
    forbidden_source_sentences: set[str] = set()
    for ff in fact_card.forbidden_features:
        feature_tokens.append(
            (ff, expand_forbidden_tokens(ff), ff.canonical_name),
        )
        forbidden_source_sentences.add(
            (ff.source_sentence or "").strip().lower(),
        )

    by_feature: dict[str, int] = {}
    examples: list[dict[str, Any]] = audit["examples"]

    def _scan(blob: dict[str, Any], origin: str) -> None:
        text = blob.get("text") or ""
        if not text:
            return
        # Split into sentences so we can apply per-sentence negation
        # scope detection.
        from assembly.sources.product_grounding.forbidden_features import (
            _split_sentences,
        )
        for sent in _split_sentences(text):
            sent_low_stripped = sent.strip().lower()
            # Don't flag the verbatim brief sentence itself, if it
            # somehow ended up in the agent text.
            if sent_low_stripped in forbidden_source_sentences:
                continue
            for ff, tokens, canonical in feature_tokens:
                hit, tok = _check_forbidden_feature_in_sentence(
                    sent, canonical, tokens,
                )
                if not hit:
                    continue
                audit["positive_mention_count"] += 1
                by_feature[canonical] = by_feature.get(canonical, 0) + 1
                if len(examples) < 16:
                    examples.append({
                        "origin": origin,
                        "feature": canonical,
                        "matched_token": tok,
                        "persona_id": str(blob.get("persona_id") or ""),
                        "match_kind": ff.match_kind,
                        "excerpt": sent[:240],
                    })

    for t in turn_texts:
        _scan(t, "turn")
    for b in ballot_texts:
        _scan(b, "ballot")

    audit["by_feature"] = by_feature
    audit["unrepaired_count"] = audit["positive_mention_count"]
    audit["any_violations"] = audit["positive_mention_count"] > 0
    audit["pass"] = audit["unrepaired_count"] == 0
    return audit


def repair_forbidden_feature_mentions(
    text: str,
    fact_card: ProductFactCard,
) -> tuple[str, int, list[dict[str, Any]]]:
    """Strip sentences that positively mention an explicitly
    forbidden feature, replacing them with a verification-form
    sentence anchored on the brief's source-sentence.

    Returns (cleaned_text, repair_count, repair_examples).

    Universal — operates on whatever forbidden_features the fact
    card carries.
    """
    if not text or not fact_card.forbidden_features:
        return text, 0, []
    from assembly.sources.product_grounding.forbidden_features import (
        _split_sentences,
        expand_forbidden_tokens,
    )

    feature_tokens: list[tuple[Any, tuple[str, ...], str]] = [
        (ff, expand_forbidden_tokens(ff), ff.canonical_name)
        for ff in fact_card.forbidden_features
    ]
    forbidden_source_sentences = {
        (ff.source_sentence or "").strip().lower()
        for ff in fact_card.forbidden_features
    }

    sentences = _split_sentences(text)
    out_sentences: list[str] = []
    repair_count = 0
    examples: list[dict[str, Any]] = []

    product_name = fact_card.product_name or "the product"

    for s in sentences:
        sent_low = s.strip().lower()
        if sent_low in forbidden_source_sentences:
            out_sentences.append(s)
            continue
        replaced = False
        for ff, tokens, canonical in feature_tokens:
            hit, tok = _check_forbidden_feature_in_sentence(
                s, canonical, tokens,
            )
            if not hit:
                continue
            new_sentence = (
                f"Since the brief says {product_name} does not have "
                f"{canonical}, I'd want to understand the alternative "
                "mechanism the brief affirms instead."
            )
            out_sentences.append(new_sentence)
            repair_count += 1
            if len(examples) < 6:
                examples.append({
                    "feature": canonical,
                    "matched_token": tok,
                    "before": s.strip(),
                    "after": new_sentence,
                })
            replaced = True
            break
        if not replaced:
            out_sentences.append(s)
    return " ".join(out_sentences).strip(), repair_count, examples


def _fact_required_true(
    fact_card: ProductFactCard, key_or_keys: str,
) -> bool:
    """Returns True if the fact lock has at least one of the listed
    keys set to True. The `key_or_keys` arg can be a pipe-joined list
    ("has_barcode_scanning|has_nfc_scanning") so the input-mechanism
    inversion fires when ANY of those mechanisms is present."""
    keys = key_or_keys.split("|")
    sensing = fact_card.sensing_facts or {}
    inputs = fact_card.input_mechanism_facts or {}
    for k in keys:
        if sensing.get(k) is True or inputs.get(k) is True:
            return True
    return False


# ---------------------------------------------------------------------------
# Camera + privacy inversion
# ---------------------------------------------------------------------------


def audit_negation_scope(
    *,
    fact_card: ProductFactCard,
    turn_texts: list[dict[str, Any]],
    ballot_texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Detect camera / privacy / scanning fact inversions across
    ballot + turn texts."""
    audit: dict[str, Any] = {
        "phase": "10b_4_negation_scope_fact",
        "camera_fact_inversion_count": 0,
        "privacy_fact_inversion_count": 0,
        "scanning_fact_inversion_count": 0,
        "by_kind": {},
        "examples_before_after": [],
        "examples": [],
        "repaired_count": 0,
        "unrepaired_count": 0,
    }
    by_kind: dict[str, int] = {}
    examples: list[dict[str, Any]] = audit["examples"]

    def _scan(blob: dict[str, Any], origin: str) -> None:
        text = blob.get("text") or ""
        if not text:
            return
        for rules, audit_key in (
            (_CAMERA_INVERSION_PATTERNS, "camera_fact_inversion_count"),
            (_PRIVACY_OVER_INVERSION_PATTERNS, "privacy_fact_inversion_count"),
            (_INPUT_INVERSION_PATTERNS, "scanning_fact_inversion_count"),
        ):
            for rx, key, kind, _anchor, _proof in rules:
                if not _fact_required_true(fact_card, key):
                    continue
                m = rx.search(text)
                if not m:
                    continue
                audit[audit_key] += 1
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
    audit["unrepaired_count"] = (
        audit["camera_fact_inversion_count"]
        + audit["privacy_fact_inversion_count"]
        + audit["scanning_fact_inversion_count"]
    )
    audit["any_violations"] = audit["unrepaired_count"] > 0
    return audit


def repair_negation_scope_inversion(
    text: str,
    fact_card: ProductFactCard,
) -> tuple[str, int, list[dict[str, Any]]]:
    """Rewrite inversion sentences into still-image / privacy /
    workflow-friction wording. Returns
    (cleaned_text, repair_count, repair_examples).
    Sentences without an inversion pass through unchanged."""
    if not text:
        return "", 0, []
    sentences = _split_sentences(text)
    out_sentences: list[str] = []
    repair_count = 0
    examples: list[dict[str, Any]] = []
    all_rules = (
        _CAMERA_INVERSION_PATTERNS
        + _PRIVACY_OVER_INVERSION_PATTERNS
        + _INPUT_INVERSION_PATTERNS
    )
    for s in sentences:
        replaced = False
        for rx, key, kind, anchor_template, proof in all_rules:
            if not rx.search(s):
                continue
            if not _fact_required_true(fact_card, key):
                continue
            anchor = _format_anchor(anchor_template, fact_card)
            new_sentence = f"Since {anchor}, I'd {proof}."
            out_sentences.append(new_sentence)
            repair_count += 1
            if len(examples) < 6:
                examples.append({
                    "kind": kind,
                    "before": s.strip(),
                    "after": new_sentence,
                })
            replaced = True
            break
        if not replaced:
            out_sentences.append(s)
    return " ".join(out_sentences).strip(), repair_count, examples
