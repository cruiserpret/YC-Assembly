"""Phase 8.5F — universal API-key / secret scanner.

Used to gate every output file before it leaves the script. Universal:
no per-product / per-vendor patterns, just the canonical key shapes.

Patterns detected:
  * `sk-ant-…`, `sk-proj-…`, generic `sk-` followed by long alnum
  * `<UPPER_SNAKE>_API_KEY=<value>` and bare `API_KEY=<value>`
  * Provider-specific KEY=VALUE forms (ANTHROPIC_API_KEY=,
    OPENAI_API_KEY=, BRAVE_SEARCH_API_KEY=, YOUTUBE_DATA_API_KEY=,
    TAVILY_API_KEY=, FIRECRAWL_API_KEY=, JINA_API_KEY=, EXA_API_KEY=)
  * AWS access keys (AKIA…)

Returns a `SecretScanResult` with a list of `redacted_findings` (the
matched substring is replaced with `[REDACTED]` in the output text
so the caller can write a clean file).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Order matters — longer / more-specific patterns first so a
# `sk-ant-…` match isn't shadowed by a generic `sk-` match.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "anthropic_key_prefix",
        re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    ),
    (
        "openai_proj_key_prefix",
        re.compile(r"sk-proj-[A-Za-z0-9_\-]{16,}"),
    ),
    (
        "openai_legacy_key_prefix",
        # `sk-` followed by 32+ alnum, but NOT `sk-ant-` or `sk-proj-`
        re.compile(
            r"\bsk-(?!ant-|proj-)[A-Za-z0-9_\-]{32,}",
        ),
    ),
    (
        "aws_access_key_id",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ),
    (
        "named_provider_key_eq",
        re.compile(
            r"\b(ANTHROPIC|OPENAI|BRAVE_SEARCH|YOUTUBE_DATA|TAVILY|"
            r"FIRECRAWL|JINA|EXA|DATAFORSEO)_API_KEY=\S+",
        ),
    ),
    (
        "generic_api_key_eq",
        re.compile(r"\b[A-Z][A-Z0-9_]{2,}_API_KEY=\S+"),
    ),
    (
        "bare_api_key_eq",
        re.compile(r"\bAPI_KEY=\S+"),
    ),
)


@dataclass(frozen=True)
class SecretScanResult:
    """Pure-data scan result. `redacted_text` has every match replaced
    with `[REDACTED]`. `findings` lists each match's category +
    location."""
    is_clean: bool
    findings: list[dict]
    redacted_text: str


def scan_for_secrets(text: str) -> SecretScanResult:
    """Scan `text` for API-key / secret patterns. Returns
    `is_clean=True` iff zero matches.

    The `redacted_text` field is the input with every match replaced
    by the literal string `[REDACTED]`. Callers that need to write a
    file should write `redacted_text` instead of `text` — but a
    clean scan should produce a byte-equal output."""
    if not text:
        return SecretScanResult(
            is_clean=True, findings=[], redacted_text=text or "",
        )
    findings: list[dict] = []
    redacted = text
    for category, pat in _SECRET_PATTERNS:
        for m in pat.finditer(redacted):
            findings.append({
                "category": category,
                "match_preview": m.group(0)[:24] + "…",
                "start": m.start(),
                "end": m.end(),
            })
        redacted = pat.sub("[REDACTED]", redacted)
    return SecretScanResult(
        is_clean=len(findings) == 0,
        findings=findings,
        redacted_text=redacted,
    )
