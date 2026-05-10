"""Phase 8.2K — output audit / forbidden-language scanner.

The scanner runs over every text leaf the harness emits — round
reasoning, objection text, debate arguments, and the final summary
text. If ANY forbidden language appears, the round is flagged
`output_audit_passed=False` and the runner enters its repair / fail
path.

Forbidden categories:
  * forecast / verdict ("will succeed", "will fail", "guaranteed")
  * market reaction language ("market reaction is positive")
  * adoption / conversion percentages ("X% would adopt")
  * build / kill / pivot recommendations
  * "tiny_ready" claims
  * "representative of target market" claims
  * society-as-singular framing ("the Amboras society thinks")

The scanner ALSO validates that the runner's final summary contains
the three mandatory caveat markers (sample-size, coverage-thinness,
MICRO-TEST label).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from assembly.pipeline.micro_simulation.schemas import (
    MicroDebateTurn,
    MicroRoundResult,
    MicroSimulationOutputAudit,
    MicroTrace,
)


# ---------------------------------------------------------------------------
# Forbidden-language patterns
# ---------------------------------------------------------------------------


_FORECAST_VERDICT_RE: re.Pattern[str] = re.compile(
    r"\b(?:"
    r"will\s+(?:succeed|fail|dominate|win|lose|outperform|crush)|"
    r"guaranteed\s+(?:to|win|success)|"
    r"market\s+success\s+probability|"
    r"forecast(?:s|ing)?\s+(?:revenue|sales|adoption|conversion)|"
    r"predict(?:s|ed|ion)?\s+(?:revenue|sales|adoption|conversion|"
    r"market\s+share)|"
    r"verdict\s*[:=]"
    r")",
    re.IGNORECASE,
)

_BUILD_KILL_PIVOT_RE: re.Pattern[str] = re.compile(
    r"\b(?:should|recommend|let'?s)\s+(?:build|kill|pivot|launch)\s+"
    r"(?:it|this|the\s+product)\b",
    re.IGNORECASE,
)

_ADOPTION_PERCENT_RE: re.Pattern[str] = re.compile(
    r"\b\d{1,3}\s*%\s+(?:of\s+)?"
    r"(?:merchants?|users?|buyers?|customers?|founders?|operators?|"
    r"shoppers?|consumers?|personas?)\s+"
    r"(?:would|will|are|do|adopt|convert|switch|reject)\b",
    re.IGNORECASE,
)

_MARKET_REACTION_RE: re.Pattern[str] = re.compile(
    r"\bmarket\s+(?:reaction|response|sentiment)\s+is\s+"
    r"(?:positive|negative|mixed|favorable|unfavorable)\b",
    re.IGNORECASE,
)

_TINY_READY_CLAIM_RE: re.Pattern[str] = re.compile(
    r"\btiny[_\s]?ready\b\s*(?:=|is|:)\s*(?:true|yes)",
    re.IGNORECASE,
)

_REPRESENTATIVE_CLAIM_RE: re.Pattern[str] = re.compile(
    r"\brepresentative\s+of\s+(?:the\s+)?(?:target\s+)?market\b|"
    r"\brepresents?\s+the\s+(?:target\s+)?market\b|"
    r"\bspeaks?\s+for\s+the\s+market\b",
    re.IGNORECASE,
)

# Singular-society framing — e.g. "the Amboras society thinks", "the
# society believes", "the population concluded".
_SOCIETY_AS_SINGULAR_RE: re.Pattern[str] = re.compile(
    r"\bthe\s+(?:[A-Za-z]+\s+)?(?:society|population|audience|cohort)\s+"
    r"(?:thinks?|believes?|feels?|wants?|concludes?|decides?)\b",
    re.IGNORECASE,
)


_FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_FORECAST_VERDICT_RE, "forecast/verdict language"),
    (_BUILD_KILL_PIVOT_RE, "build/kill/pivot recommendation"),
    (_ADOPTION_PERCENT_RE, "adoption/conversion percentage claim"),
    (_MARKET_REACTION_RE, "market-reaction framing"),
    (_TINY_READY_CLAIM_RE, "tiny_ready claim"),
    (_REPRESENTATIVE_CLAIM_RE, "representative-of-market claim"),
    (_SOCIETY_AS_SINGULAR_RE, "society-as-singular framing"),
)


def scan_text_for_forbidden_claims(text: str) -> list[str]:
    """Return a list of human-readable category labels for any
    forbidden-language match in `text`."""
    out: list[str] = []
    if not text:
        return out
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            out.append(label)
    return out


# ---------------------------------------------------------------------------
# Mandatory-caveat detection
# ---------------------------------------------------------------------------


SAMPLE_SIZE_CAVEAT_MARKERS: tuple[str, ...] = (
    "sample size", "sample-size", "n=", "small sample",
    "tiny sample",
)
COVERAGE_THINNESS_MARKERS: tuple[str, ...] = (
    "coverage thinness", "coverage-thinness",
    "coverage is thin", "thin coverage",
    "stakeholder coverage thin",
)
MICRO_TEST_MARKERS: tuple[str, ...] = (
    "MICRO-TEST", "micro-test", "MICRO_TEST",
    "mechanical micro-test", "MICRO TEST",
)


def has_marker(text: str, markers: tuple[str, ...]) -> bool:
    text_lower = text.lower()
    return any(m.lower() in text_lower for m in markers)


# ---------------------------------------------------------------------------
# Round / trace audit
# ---------------------------------------------------------------------------


def audit_round_result(round_result: MicroRoundResult) -> list[str]:
    """Scan a single round's reasoning + objections + citations.
    Returns a list of forbidden-claim labels found."""
    found: list[str] = []
    found.extend(scan_text_for_forbidden_claims(round_result.reasoning))
    for obj in round_result.objections:
        found.extend(scan_text_for_forbidden_claims(obj))
    for cit in round_result.evidence_citations:
        found.extend(scan_text_for_forbidden_claims(cit))
    return found


def audit_debate_turn(turn: MicroDebateTurn) -> list[str]:
    return scan_text_for_forbidden_claims(turn.argument)


def audit_full_trace_and_summary(
    *,
    trace: MicroTrace,
    summary_text: str,
    persona_count: int,
) -> MicroSimulationOutputAudit:
    """End-to-end audit. Returns a MicroSimulationOutputAudit.

    Required-caveat markers MUST appear in the summary_text:
      * a sample-size phrase (`n=N`, "sample size", etc.)
      * a coverage-thinness phrase
      * the literal `MICRO-TEST` label
    """
    forbidden_found: list[str] = []
    rounds_failing: list[str] = []
    for r in trace.rounds:
        if not r.output_audit_passed:
            rounds_failing.append(f"{r.persona_id}/{r.round_kind.value}")
        forbidden_found.extend(audit_round_result(r))
    for t in trace.debate_turns:
        if not t.output_audit_passed:
            rounds_failing.append(
                f"debate:{t.speaker_persona_id}->{t.target_persona_id}"
            )
        forbidden_found.extend(audit_debate_turn(t))
    forbidden_found.extend(scan_text_for_forbidden_claims(summary_text))

    sample_size_present = has_marker(summary_text, SAMPLE_SIZE_CAVEAT_MARKERS)
    coverage_present = has_marker(summary_text, COVERAGE_THINNESS_MARKERS)
    micro_label_present = has_marker(summary_text, MICRO_TEST_MARKERS)

    caveats: list[str] = []
    if sample_size_present:
        caveats.append(f"sample-size caveat present (n={persona_count})")
    if coverage_present:
        caveats.append("coverage-thinness caveat present")
    if micro_label_present:
        caveats.append("MICRO-TEST label present")

    return MicroSimulationOutputAudit(
        forbidden_claims_found=sorted(set(forbidden_found)),
        rounds_failing_audit=rounds_failing,
        caveats_emitted=caveats,
        sample_size_caveat_present=sample_size_present,
        coverage_thinness_caveat_present=coverage_present,
        micro_test_label_present=micro_label_present,
    )
