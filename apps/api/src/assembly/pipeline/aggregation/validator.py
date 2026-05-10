"""Output language validator.

This is the most product-defining piece in Assembly. It runs *after* the
aggregator generates each section and *before* the section is persisted to
`simulation_outputs`. If a violation is found, the section is regenerated with
the violation surfaced to the LLM.

It rejects five classes of language / structure:

  1. Numeric forecasts — `4.7%`, `$50K`, `CTR`, `CAC`, "convert at X".
     (Numbers that appear in the *input* brief are fine; this only audits the
     *output* sections.)
  2. Absolute predictive claims — "will reject", "definitely", "guaranteed".
  3. Forced verdicts — "build", "kill", "pivot", "revise" used as commands.
  4. Objective sentiment — "the market is positive", "customers want this",
     "the audience rejects", "the product is accepted". Synthetic
     interpretation must be framed subjectively.
  5. Structural — every output must include an evidence ledger with at least
     direct evidence (the user-provided brief is always direct evidence).

It is intentionally pattern-based and conservative. False negatives are
acceptable; false positives are NOT (they would block legitimate subjective
language). When in doubt, the test suite is the spec.

Phase 7 wires this into the aggregator. Phase 1 ships the validator + tests
because (a) it is the central correctness contract for the product, and
(b) every later prompt will need to be tested against it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ViolationCategory(str, Enum):
    NUMERIC_FORECAST = "numeric_forecast"
    ABSOLUTE_CLAIM = "absolute_claim"
    FORCED_VERDICT = "forced_verdict"
    OBJECTIVE_SENTIMENT = "objective_sentiment"
    STRUCTURE = "structure"
    REAL_WORLD_INSTRUCTIONS = "real_world_instructions"  # Phase 7


@dataclass(frozen=True)
class Violation:
    category: ViolationCategory
    field_path: str
    excerpt: str
    matched_phrase: str
    rule_id: str
    suggestion: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    violations: tuple[Violation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [
                {
                    "category": v.category.value,
                    "field_path": v.field_path,
                    "excerpt": v.excerpt,
                    "matched_phrase": v.matched_phrase,
                    "rule_id": v.rule_id,
                    "suggestion": v.suggestion,
                }
                for v in self.violations
            ],
        }


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    category: ViolationCategory
    pattern: re.Pattern[str]
    suggestion: str


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
#
# Notes on regex design:
# - All patterns are case-insensitive (compiled with re.IGNORECASE).
# - We anchor on word boundaries to avoid matching inside larger words.
# - Patterns capture an `excerpt_window` of context around the match for
#   debugging / LLM regeneration prompts.

_PERCENT_FORECAST = re.compile(r"\b\d+(?:\.\d+)?\s*%", re.IGNORECASE)

_DOLLAR_FORECAST = re.compile(
    r"(?<![A-Za-z])\$\s*\d+(?:[.,]\d+)?\s*(?:[KMB]\b|million\b|billion\b)?",
    re.IGNORECASE,
)

_METRIC_ACRONYMS = re.compile(
    r"\b(?:CTR|CAC|LTV|MRR|ARR|ROI|ROAS|CPA|CPC|CPM|AOV)\b",
    re.IGNORECASE,
)

_CONVERT_AT = re.compile(
    r"\bconvert(?:s|ing|ed)?\s+at\b",
    re.IGNORECASE,
)

# "will reject", "will succeed", "will fail", etc. — bare predictive future
# tense about market behavior. We allow "will need", "will require",
# "will likely" because those are softer.
_WILL_VERB = re.compile(
    r"\bwill\s+"
    r"(?:reject|accept|adopt|buy|purchase|fail|succeed|convert|"
    r"win|lose|crush|dominate|capture|reach|grow|skyrocket|tank)\b",
    re.IGNORECASE,
)

_DEFINITELY = re.compile(r"\bdefinitely\b", re.IGNORECASE)
_GUARANTEED = re.compile(r"\bguarantee(?:d|s)?\b", re.IGNORECASE)
_CERTAINLY = re.compile(r"\bcertainly\b", re.IGNORECASE)

# Forced verdicts. We block the imperative form ("Build this product",
# "Kill this idea", "We recommend killing this") but permit softer
# observations ("the product seems likely to need repositioning").
# The trailing `(?:ing|s|ed)?` captures verb forms like "killing", "pivoting".
_VERDICT_IMPERATIVE = re.compile(
    r"\b(?:should|must|need\s+to|have\s+to|recommend(?:ed|ing)?(?:\s+to)?)"
    r"\s+(?:build|kill|pivot|revise|reject|launch|abandon|scrap)(?:ing|s|ed)?\b",
    re.IGNORECASE,
)

# "Verdict: BUILD" / "Verdict: KILL" / "Final answer: KILL"
_VERDICT_LABEL = re.compile(
    r"\b(?:verdict|final\s+answer|decision|conclusion)\s*[:\-]\s*"
    r"(?:build|kill|pivot|revise|reject|abandon|scrap|launch)\b",
    re.IGNORECASE,
)

# Bare verdict words used as commands at the start of a sentence/line.
_BARE_VERDICT_COMMAND = re.compile(
    r"(?:^|[\.\!\?]\s+|\n\s*)"
    r"(?:Build|Kill|Pivot|Revise|Reject|Abandon|Scrap)\s+"
    r"(?:this|the|your|it)\b",
)

# ---------------------------------------------------------------------------
# Objective-sentiment patterns
# ---------------------------------------------------------------------------
# These catch language that presents simulated interpretation as objective fact
# about a market or audience. Subjective rewrites: "the society seemed…",
# "agents appeared…", "the strongest resistance appeared to come from…".

# "the market is positive", "market sentiment is negative", "market will be hostile"
_MARKET_OBJECTIVE = re.compile(
    r"\b(?:the\s+)?market\s+(?:is|sentiment\s+is|will\s+be|will\s+become)\s+"
    r"(?:positive|negative|good|bad|strong|weak|favorable|unfavorable|"
    r"hostile|enthusiastic|warm|cold|excited|disappointed)\b",
    re.IGNORECASE,
)

# "customers want this", "customers reject", "customer wanted X" — present/past
# tense objective claim about all customers. Subjective: "customers seemed to",
# "many customers indicated", "agents portraying customers tended to".
_CUSTOMERS_OBJECTIVE_VERB = re.compile(
    r"\bcustomers?\s+"
    r"(?:want(?:s|ed)?|need(?:s|ed)?|love(?:s|d)?|hate(?:s|d)?|"
    r"reject(?:s|ed)?|accept(?:s|ed)?|adopt(?:s|ed)?|demand(?:s|ed)?|"
    r"prefer(?:s|red)?|expect(?:s|ed)?)\b",
    re.IGNORECASE,
)

# "the audience rejects this", "audience accepts the offer". Same shape as above.
_AUDIENCE_OBJECTIVE_VERB = re.compile(
    r"\b(?:the\s+)?audience\s+"
    r"(?:want(?:s|ed)?|need(?:s|ed)?|love(?:s|d)?|hate(?:s|d)?|"
    r"reject(?:s|ed)?|accept(?:s|ed)?|adopt(?:s|ed)?)\b",
    re.IGNORECASE,
)

# "the product is accepted", "the product has been rejected". Subjective:
# "the product seemed to be accepted", "agents tended to accept".
_PRODUCT_OBJECTIVE_STATE = re.compile(
    r"\bthe\s+product\s+"
    r"(?:is\s+(?:accepted|rejected|loved|hated|popular|unpopular|adopted)|"
    r"has\s+been\s+(?:accepted|rejected|adopted))\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Phase 7 — REAL_WORLD_INSTRUCTIONS patterns.
# ---------------------------------------------------------------------------
# Phase 7 produces a SIMULATED report. It must not instruct the user to run
# real-world experiments (Meta ads, landing pages, validation campaigns).
# Subjective observations like "agents portraying X seemed receptive" are fine;
# directives like "spend $5K on Meta ads" are forbidden.

_RUN_ADS = re.compile(
    r"\brun\s+(?:Meta|Google|TikTok|Facebook|LinkedIn|Twitter|X|Reddit|YouTube|Instagram)?\s*ads?\b",
    re.IGNORECASE,
)

_AD_PLATFORM_NAMED = re.compile(
    r"\b(?:Meta|Google|TikTok|Facebook|LinkedIn|Reddit|Instagram)\s+ads?\b",
    re.IGNORECASE,
)

_AD_SPEND_DOLLAR = re.compile(
    r"\bspend\s+\$\s*\d", re.IGNORECASE,
)

_LANDING_PAGE_TEST = re.compile(
    r"\b(?:launch|build|deploy|create|set\s+up|stand\s+up|spin\s+up)\s+"
    r"(?:a\s+)?landing\s+page\b",
    re.IGNORECASE,
)

_VALIDATION_CAMPAIGN = re.compile(
    r"\b(?:run|launch|start|kick\s+off)\s+(?:a\s+)?"
    r"(?:validation|smoke[-\s]?test|MVP|fake[-\s]?door|pre[-\s]?order)\s+"
    r"(?:campaign|test|experiment|page)\b",
    re.IGNORECASE,
)

_KILL_THE_TEST = re.compile(
    r"\bkill\s+(?:the\s+)?(?:test|campaign|ad\s+set|ads|experiment)\b",
    re.IGNORECASE,
)


_RULES: tuple[_Rule, ...] = (
    _Rule(
        rule_id="num.percent_forecast",
        category=ViolationCategory.NUMERIC_FORECAST,
        pattern=_PERCENT_FORECAST,
        suggestion="Replace numeric percentages with subjective language "
        "('a substantial share', 'a small portion').",
    ),
    _Rule(
        rule_id="num.dollar_forecast",
        category=ViolationCategory.NUMERIC_FORECAST,
        pattern=_DOLLAR_FORECAST,
        suggestion="Do not predict dollar amounts. Describe price *positioning* "
        "qualitatively ('lower-end', 'premium').",
    ),
    _Rule(
        rule_id="num.metric_acronym",
        category=ViolationCategory.NUMERIC_FORECAST,
        pattern=_METRIC_ACRONYMS,
        suggestion="Do not cite CTR/CAC/LTV/etc. Assembly does not produce "
        "marketing-funnel forecasts.",
    ),
    _Rule(
        rule_id="num.convert_at",
        category=ViolationCategory.NUMERIC_FORECAST,
        pattern=_CONVERT_AT,
        suggestion="Replace 'will convert at X' with subjective language about "
        "market reaction ('the market seemed cautiously interested').",
    ),
    _Rule(
        rule_id="abs.will_verb",
        category=ViolationCategory.ABSOLUTE_CLAIM,
        pattern=_WILL_VERB,
        suggestion="Soften absolute predictions. Prefer 'appears likely to', "
        "'seemed to', 'may', 'could'.",
    ),
    _Rule(
        rule_id="abs.definitely",
        category=ViolationCategory.ABSOLUTE_CLAIM,
        pattern=_DEFINITELY,
        suggestion="Replace 'definitely' with subjective language ('appears to', "
        "'seems to').",
    ),
    _Rule(
        rule_id="abs.guaranteed",
        category=ViolationCategory.ABSOLUTE_CLAIM,
        pattern=_GUARANTEED,
        suggestion="Avoid guarantees. Assembly is a simulation, not a forecast.",
    ),
    _Rule(
        rule_id="abs.certainly",
        category=ViolationCategory.ABSOLUTE_CLAIM,
        pattern=_CERTAINLY,
        suggestion="Replace 'certainly' with 'appears to' or 'seemed to'.",
    ),
    _Rule(
        rule_id="verdict.imperative",
        category=ViolationCategory.FORCED_VERDICT,
        pattern=_VERDICT_IMPERATIVE,
        suggestion="Do not tell the user to build/kill/pivot. Describe what the "
        "market may need; let the user decide.",
    ),
    _Rule(
        rule_id="verdict.label",
        category=ViolationCategory.FORCED_VERDICT,
        pattern=_VERDICT_LABEL,
        suggestion="Remove the verdict label. Assembly does not deliver "
        "build/kill/pivot decisions.",
    ),
    _Rule(
        rule_id="verdict.bare_command",
        category=ViolationCategory.FORCED_VERDICT,
        pattern=_BARE_VERDICT_COMMAND,
        suggestion="Reframe as observation ('the product seems to need a "
        "narrower wedge') instead of imperative ('Pivot the product').",
    ),
    _Rule(
        rule_id="obj.market_state",
        category=ViolationCategory.OBJECTIVE_SENTIMENT,
        pattern=_MARKET_OBJECTIVE,
        suggestion="Use subjective sentiment language: 'the market mood seemed "
        "positive', 'the society appeared cautious', not 'the market is X'.",
    ),
    _Rule(
        rule_id="obj.customers_verb",
        category=ViolationCategory.OBJECTIVE_SENTIMENT,
        pattern=_CUSTOMERS_OBJECTIVE_VERB,
        suggestion="Reframe as simulated observation: 'customers seemed to want', "
        "'many agents indicated', not 'customers want'.",
    ),
    _Rule(
        rule_id="obj.audience_verb",
        category=ViolationCategory.OBJECTIVE_SENTIMENT,
        pattern=_AUDIENCE_OBJECTIVE_VERB,
        suggestion="Reframe as simulated observation: 'the audience appeared to "
        "reject', not 'the audience rejects'.",
    ),
    _Rule(
        rule_id="obj.product_state",
        category=ViolationCategory.OBJECTIVE_SENTIMENT,
        pattern=_PRODUCT_OBJECTIVE_STATE,
        suggestion="Reframe as simulated observation: 'the product seemed to be "
        "accepted by some agents', not 'the product is accepted'.",
    ),
    # Phase 7 — REAL_WORLD_INSTRUCTIONS rules.
    _Rule(
        rule_id="rwi.run_ads",
        category=ViolationCategory.REAL_WORLD_INSTRUCTIONS,
        pattern=_RUN_ADS,
        suggestion="Phase 7 is a simulated report, not real-world instructions. "
        "Replace 'run ads' with 'agents portraying X seemed receptive on first exposure'.",
    ),
    _Rule(
        rule_id="rwi.ad_platform_named",
        category=ViolationCategory.REAL_WORLD_INSTRUCTIONS,
        pattern=_AD_PLATFORM_NAMED,
        suggestion="Don't name ad platforms (Meta/Google/TikTok/etc.) as test channels. "
        "Describe simulated reaction, not channel-spend instructions.",
    ),
    _Rule(
        rule_id="rwi.ad_spend",
        category=ViolationCategory.REAL_WORLD_INSTRUCTIONS,
        pattern=_AD_SPEND_DOLLAR,
        suggestion="No ad-spend instructions. Replace 'spend $X on ads' with "
        "subjective language about which agents seemed most receptive.",
    ),
    _Rule(
        rule_id="rwi.landing_page",
        category=ViolationCategory.REAL_WORLD_INSTRUCTIONS,
        pattern=_LANDING_PAGE_TEST,
        suggestion="No landing-page-test instructions. Phase 7 reports the simulated "
        "society's reaction; it does not direct real-world experiments.",
    ),
    _Rule(
        rule_id="rwi.validation_campaign",
        category=ViolationCategory.REAL_WORLD_INSTRUCTIONS,
        pattern=_VALIDATION_CAMPAIGN,
        suggestion="No validation/smoke-test/MVP-campaign instructions. Describe what "
        "the simulated society seemed to need, not how to test it in market.",
    ),
    _Rule(
        rule_id="rwi.kill_the_test",
        category=ViolationCategory.REAL_WORLD_INSTRUCTIONS,
        pattern=_KILL_THE_TEST,
        suggestion="No 'kill the test' / 'kill the campaign' phrasing. Reframe as "
        "observation about simulated resistance.",
    ),
)


_EXCERPT_WINDOW = 60  # chars on each side of a match


def _excerpt(text: str, start: int, end: int) -> str:
    a = max(0, start - _EXCERPT_WINDOW)
    b = min(len(text), end + _EXCERPT_WINDOW)
    prefix = "…" if a > 0 else ""
    suffix = "…" if b < len(text) else ""
    return prefix + text[a:b].replace("\n", " ").strip() + suffix


def _walk_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Yield every (path, string) leaf in a nested dict/list/str structure."""
    out: list[tuple[str, str]] = []
    if isinstance(value, str):
        out.append((path or "<root>", value))
    elif isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}" if path else str(k)
            out.extend(_walk_strings(v, child))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            child = f"{path}[{i}]"
            out.extend(_walk_strings(v, child))
    return out


def validate_text(
    text: str,
    *,
    field_path: str = "<text>",
    skip_rules: frozenset[str] | set[str] | None = None,
    skip_categories: frozenset[ViolationCategory] | set[ViolationCategory] | None = None,
) -> list[Violation]:
    """Run all rules against a single string. Returns 0+ violations.

    `skip_rules` exempts specific rule_ids (e.g., `num.metric_acronym` in
    buyer-state contexts where a buyer naturally references ROI/MRR).
    `skip_categories` exempts whole ViolationCategory values."""
    if not text:
        return []
    skip_rules = frozenset(skip_rules or ())
    skip_categories = frozenset(skip_categories or ())

    violations: list[Violation] = []
    for rule in _RULES:
        if rule.rule_id in skip_rules:
            continue
        if rule.category in skip_categories:
            continue
        for m in rule.pattern.finditer(text):
            violations.append(
                Violation(
                    category=rule.category,
                    field_path=field_path,
                    excerpt=_excerpt(text, m.start(), m.end()),
                    matched_phrase=m.group(0).strip(),
                    rule_id=rule.rule_id,
                    suggestion=rule.suggestion,
                )
            )
    return violations


def _check_evidence_ledger_structure(sections: dict[str, Any]) -> list[Violation]:
    """Structural check: outputs must surface an evidence ledger with at least
    direct evidence. The user-provided brief is always direct evidence, so an
    empty `direct_evidence` list is a structural failure — not a stylistic one.
    """
    violations: list[Violation] = []
    ledger = sections.get("evidence_ledger")

    if ledger is None:
        violations.append(
            Violation(
                category=ViolationCategory.STRUCTURE,
                field_path="evidence_ledger",
                excerpt="<missing>",
                matched_phrase="<missing key>",
                rule_id="struct.ledger_missing",
                suggestion="Every simulation output must include `evidence_ledger` "
                "with `direct_evidence`, `analogical_evidence`, and `missing_evidence` keys.",
            )
        )
        return violations

    if not isinstance(ledger, dict):
        violations.append(
            Violation(
                category=ViolationCategory.STRUCTURE,
                field_path="evidence_ledger",
                excerpt=str(ledger)[:80],
                matched_phrase="<not a dict>",
                rule_id="struct.ledger_invalid_type",
                suggestion="`evidence_ledger` must be a dict with the three "
                "kind-keyed lists.",
            )
        )
        return violations

    for required_key in ("direct_evidence", "analogical_evidence", "missing_evidence"):
        if required_key not in ledger:
            violations.append(
                Violation(
                    category=ViolationCategory.STRUCTURE,
                    field_path=f"evidence_ledger.{required_key}",
                    excerpt="<missing>",
                    matched_phrase=f"<missing key: {required_key}>",
                    rule_id="struct.ledger_missing_key",
                    suggestion=f"`{required_key}` must be present (may be an "
                    "empty list if there is genuinely none, but must exist).",
                )
            )

    direct = ledger.get("direct_evidence", [])
    if not isinstance(direct, list) or not direct:
        violations.append(
            Violation(
                category=ViolationCategory.STRUCTURE,
                field_path="evidence_ledger.direct_evidence",
                excerpt="<empty>" if isinstance(direct, list) else str(direct)[:80],
                matched_phrase="<empty list>",
                rule_id="struct.no_direct_evidence",
                suggestion="`direct_evidence` cannot be empty: at minimum the "
                "user-provided brief is direct evidence and must appear here.",
            )
        )

    return violations


def validate_output(
    sections: dict[str, Any],
    *,
    skip_paths: tuple[str, ...] = (),
    require_ledger: bool = False,
) -> ValidationResult:
    """Validate every string leaf inside the 9 output sections.

    `skip_paths` lets callers exempt fields that are allowed to contain numbers
    (e.g. an evidence ledger that quotes a real competitor's price page).

    `require_ledger=True` runs the structural check that asserts an evidence
    ledger is present and direct_evidence is non-empty. The Phase 7 aggregator
    must call this with `require_ledger=True` on the final assembled report.
    Per-section validation during regeneration may pass `require_ledger=False`.
    """
    violations: list[Violation] = []
    for path, text in _walk_strings(sections):
        if any(path.startswith(p) for p in skip_paths):
            continue
        violations.extend(validate_text(text, field_path=path))

    if require_ledger:
        violations.extend(_check_evidence_ledger_structure(sections))

    return ValidationResult(passed=not violations, violations=tuple(violations))
