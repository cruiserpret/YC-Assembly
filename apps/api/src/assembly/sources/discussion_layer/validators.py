"""Phase 9A.4 — discussion-layer validators.

  * forbidden_claim_audit       — forecast/verdict/fake-product-use
  * sensitive_inference_audit   — protected-category inference
  * detect_overcooperation      — agents converging without reason
  * classify_public_private_delta — pre/final ballot diff classifier

All universal — no LumaLoop hardcoding (the unlaunched-product scanner
takes the product name as an argument).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable


_FORECAST_PATTERNS = (
    # X% will adopt / buy / convert / prefer — true forecast
    re.compile(
        r"\b\d{1,3}\s*%\s+(?:will|would)\s+"
        r"(?:adopt|buy|convert|prefer|purchase|switch)",
        re.I,
    ),
    # X% of (market|customers|users|people) will/would/should...
    re.compile(
        r"\b\d{1,3}\s*%\s+of\s+"
        r"(?:the\s+)?(?:market|customers|users|people|buyers|consumers)\s+"
        r"(?:will|would|should|are\s+going\s+to)",
        re.I,
    ),
    # X% adoption rate / market share / conversion
    re.compile(
        r"\b\d{1,3}\s*%\s+"
        r"(?:adoption|market\s+share|conversion|churn|attach\s+rate)",
        re.I,
    ),
    re.compile(r"\bmarket\s+will\b", re.I),
    re.compile(r"\bguaranteed\s+demand\b", re.I),
    re.compile(r"\bmust\s+launch\b", re.I),
    re.compile(r"\bdo\s+not\s+launch\b", re.I),
    re.compile(r"\b(?:launch|don't\s+launch)\s+verdict\b", re.I),
    re.compile(r"\brepresentative\s+market\b", re.I),
    re.compile(r"\b(?:will|won't)\s+buy\b", re.I),
)

_FAKE_USE_TEMPLATES = (
    r"i\s+bought\s+(?:the\s+|a\s+|an\s+)?{p}",
    r"i\s+used\s+(?:the\s+|a\s+|an\s+|my\s+)?{p}",
    r"my\s+{p}",
    r"{p}\s+customer",
    r"{p}\s+user",
    r"reviewed\s+(?:the\s+|a\s+|an\s+|my\s+)?{p}",
    r"i\s+own\s+(?:a\s+|an\s+|the\s+)?{p}",
    r"when\s+i\s+wore\s+(?:the\s+|a\s+|an\s+|my\s+)?{p}",
    r"i\s+have\s+been\s+using\s+(?:the\s+|my\s+)?{p}",
)

_SENSITIVE_INFERENCE_PATTERNS = (
    re.compile(r"\bracial\b", re.I),
    re.compile(r"\bethnicity\b", re.I),
    re.compile(r"\b(?:religion|religious)\b", re.I),
    re.compile(r"\bparty\s+affiliation\b", re.I),
    re.compile(r"\bvoted\s+for\b", re.I),
    re.compile(r"\bsexual\s+orientation\b", re.I),
    re.compile(r"\blgbt\w*\b", re.I),
    re.compile(r"\btrans_\w+\b", re.I),
    re.compile(r"\bdiagnosed\s+with\b", re.I),
    re.compile(r"\bptsd\b", re.I),
    re.compile(r"\bschizophrenia\b", re.I),
    re.compile(r"\bbipolar\b", re.I),
    re.compile(r"\bmental\s+health\s+(?:diagnosis|condition)\b", re.I),
    re.compile(r"\bmedical\s+condition\b", re.I),
    re.compile(r"\bdisabled\b", re.I),
    re.compile(r"\bdisability\s+benefits\b", re.I),
    re.compile(r"\bincome\s+bracket\b", re.I),
    re.compile(r"\bhousehold\s+income\b", re.I),
    re.compile(r"\bnet\s+worth\b", re.I),
    re.compile(r"\bcredit\s+score\b", re.I),
    re.compile(r"\bimmigration\b", re.I),
    re.compile(r"\bcitizenship\b", re.I),
)


def _fake_use_patterns(product_name: str) -> list[re.Pattern[str]]:
    p = re.escape(product_name.lower())
    return [
        re.compile(t.format(p=p), re.I) for t in _FAKE_USE_TEMPLATES
    ]


_NEGATION_PREFIX_RE = re.compile(
    r"(?:\b(?:not|never|no|isn't|aren't|won't)\b[\s,:;\-]+(?:a\s+|an\s+|the\s+)?)"
    r"$",
    re.I,
)


def _is_negated(text: str, match_start: int) -> bool:
    """Return True if the match at `match_start` is preceded by a
    negation phrase like 'not a' / 'never a' / 'this is not a'."""
    if match_start <= 0:
        return False
    # Look back ~30 chars for a negation marker
    window = text[max(0, match_start - 30):match_start]
    return bool(_NEGATION_PREFIX_RE.search(window))


def forbidden_claim_audit(
    *,
    texts: Iterable[str],
    product_name: str,
) -> dict[str, object]:
    """Sweep every text for forecast / verdict / fake-product-use phrases.

    `texts` is an iterable of (label, text) tuples — label can be e.g.
    'turn:abc123' or 'ballot:xyz789' so findings are addressable. If a
    plain string iterable is passed, the index is used as the label.

    Context-aware: if the matched phrase is preceded by a negation
    ('not a launch verdict', 'never a forecast'), the match is skipped
    — those are mandatory caveat phrasings, not violations.
    """
    forecast_findings: list[dict[str, str]] = []
    fake_use_findings: list[dict[str, str]] = []
    fake_use_pats = _fake_use_patterns(product_name)
    materialized = list(texts)
    if materialized and isinstance(materialized[0], str):
        items: list[tuple[str, str]] = [
            (f"text[{i}]", t) for i, t in enumerate(materialized)
        ]
    else:
        items = list(materialized)  # type: ignore[arg-type]
    for label, text in items:
        if not text:
            continue
        for pat in _FORECAST_PATTERNS:
            m = pat.search(text)
            if m and not _is_negated(text, m.start()):
                forecast_findings.append({
                    "label": label,
                    "matched": m.group(0)[:80],
                    "pattern": pat.pattern,
                })
                break
        for pat in fake_use_pats:
            m = pat.search(text)
            if m and not _is_negated(text, m.start()):
                fake_use_findings.append({
                    "label": label,
                    "matched": m.group(0)[:80],
                    "pattern": pat.pattern,
                })
                break
    return {
        "scanner_version": "9A.4.universal",
        "fake_target_product_use_count": len(fake_use_findings),
        "forecast_or_verdict_count": len(forecast_findings),
        "any_fake_target_product_use": bool(fake_use_findings),
        "any_forecast_or_verdict": bool(forecast_findings),
        "fake_use_findings": fake_use_findings[:30],
        "forecast_findings": forecast_findings[:30],
    }


def sensitive_inference_audit(
    texts: Iterable[str],
) -> dict[str, object]:
    """Sweep every text for protected-category inference. `texts` may
    be plain strings or (label, text) tuples."""
    findings: list[dict[str, str]] = []
    materialized = list(texts)
    if materialized and isinstance(materialized[0], str):
        items: list[tuple[str, str]] = [
            (f"text[{i}]", t) for i, t in enumerate(materialized)
        ]
    else:
        items = list(materialized)  # type: ignore[arg-type]
    for label, text in items:
        if not text:
            continue
        for pat in _SENSITIVE_INFERENCE_PATTERNS:
            m = pat.search(text)
            if m:
                findings.append({
                    "label": label,
                    "matched": m.group(0)[:80],
                    "pattern": pat.pattern,
                })
                break
    return {
        "scanner_version": "9A.4.universal",
        "finding_count": len(findings),
        "any_sensitive_inference": bool(findings),
        "findings": findings[:30],
    }


def detect_overcooperation(
    *,
    pre_stances: dict[str, str],
    final_stances: dict[str, str],
    public_turn_stances: list[str],
) -> dict[str, object]:
    """Flag the signature of "everyone politely agreed" — when the
    public discussion converges to one stance AND nobody's private
    final ballot dissents.

    Returns:
      converged: bool — public turns mostly carried one stance label
      private_dissent_present: bool — at least one persona's final
                                       ballot diverges from the public
                                       majority
      flag: bool — converged AND no private dissent (the bad signature)
    """
    if not public_turn_stances:
        return {
            "converged": False,
            "private_dissent_present": False,
            "flag": False,
            "warning": "no public turn stances supplied",
        }
    counter = Counter(public_turn_stances)
    most_common, n_most = counter.most_common(1)[0]
    converge_pct = n_most / max(len(public_turn_stances), 1)
    converged = converge_pct >= 0.85
    if not final_stances:
        return {
            "converged": converged,
            "private_dissent_present": False,
            "flag": converged,
            "public_majority_stance": most_common,
            "public_majority_pct": round(converge_pct, 3),
            "warning": "no final ballots supplied",
        }
    final_counter = Counter(final_stances.values())
    final_most, n_final_most = final_counter.most_common(1)[0]
    final_converge_pct = n_final_most / max(len(final_stances), 1)
    private_dissent_present = final_converge_pct < 0.85
    return {
        "converged": converged,
        "public_majority_stance": most_common,
        "public_majority_pct": round(converge_pct, 3),
        "final_majority_stance": final_most,
        "final_majority_pct": round(final_converge_pct, 3),
        "private_dissent_present": private_dissent_present,
        "flag": converged and not private_dissent_present,
        "warning": (
            "public discussion converged AND no private dissent — likely "
            "over-cooperation; psychology should have produced friction"
            if converged and not private_dissent_present else None
        ),
    }


def classify_public_private_delta(
    *,
    pre_stance: str,
    final_stance: str,
    public_majority_stance: str | None,
    private_reasoning: str | None,
) -> str:
    """Return one of the closed-set delta labels.

    Heuristic:
      - no_change             → pre == final
      - private_acceptance    → final differs from pre AND final aligns
                                with public_majority (persona shifted
                                privately + publicly)
      - public_compliance_only → public_majority differs from final but
                                  the persona's reasoning indicates
                                  capitulation (NOT used here without
                                  public_text — kept for runner)
      - resistance            → final differs from public_majority AND
                                resists peer pressure
      - polarization          → final hardens (pre soft → final harder)
      - uncertainty_increase  → final == 'needs_more_information' and
                                pre was a stance with confidence
    """
    if pre_stance == final_stance:
        return "no_change"
    if final_stance == "needs_more_information" and pre_stance != final_stance:
        return "uncertainty_increase"
    hard_set = {"skeptical", "likely_reject"}
    soft_set = {"curious_but_unconvinced", "interested_if_proven"}
    if pre_stance in soft_set and final_stance in hard_set:
        return "polarization"
    if (
        public_majority_stance
        and final_stance == public_majority_stance
        and pre_stance != public_majority_stance
    ):
        return "private_acceptance"
    if (
        public_majority_stance
        and final_stance != public_majority_stance
        and pre_stance != public_majority_stance
    ):
        return "resistance"
    return "no_change"
