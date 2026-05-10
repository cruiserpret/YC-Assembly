"""Phase 8.5G.1 — Tavily Search adapter (bounded discovery).

Tavily is treated as a DISCOVERY-only third provider alongside Brave
and YouTube. Snippets and URLs are CANDIDATE evidence and must flow
through the existing redaction + sensitive-filter + dedup + forbidden-
claim pipeline before any persona ever sees them.

Critical safety properties (drift-tested):

  * Tavily key is read ONLY from `os.environ.get("TAVILY_API_KEY")`.
    Never accepted via CLI, never written to disk, never echoed to
    logs, never embedded in audit JSON.
  * `httpx` is the ONLY HTTP transport.
  * `TavilySearchClient.search` REFUSES to run if the key is missing.
  * Hard caps: at most `max_queries × max_results_per_query` requests
    in a single client invocation.
  * `redact_tavily_url_for_audit` strips query-string tokens before
    audit logging.

Phase 8.5G.1 does NOT write source_records itself — the orchestrator
stages results in memory until the persona-coverage gate passes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_DEFAULT_TIMEOUT_S = 20.0
_DEFAULT_MAX_QUERIES = 8
_DEFAULT_MAX_RESULTS_PER_QUERY = 8


@dataclass(frozen=True)
class TavilyAdapterConfig:
    """Per-invocation caps for the Tavily client."""
    max_queries: int = _DEFAULT_MAX_QUERIES
    max_results_per_query: int = _DEFAULT_MAX_RESULTS_PER_QUERY
    timeout_s: float = _DEFAULT_TIMEOUT_S
    search_depth: str = "basic"  # 'basic' | 'advanced'


@dataclass(frozen=True)
class TavilyQueryResult:
    """Normalized Tavily result. NO key fields. NO tracking params."""
    query: str
    title: str
    url: str
    domain: str
    content: str
    score: float
    extra: dict[str, Any] = field(default_factory=dict)


def is_tavily_key_present() -> bool:
    """Return True iff `TAVILY_API_KEY` is in the environment.
    Never returns or logs the value itself."""
    return bool(os.environ.get("TAVILY_API_KEY"))


def redact_tavily_url_for_audit(url: str) -> str:
    """Strip query-string params before writing a URL to audit JSON."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return url


class TavilySearchClient:
    """Thin Tavily Search HTTP client.

    Construction does NOT make a network call and does NOT require
    the API key. Only `search()` requires the key.
    """

    def __init__(self, config: TavilyAdapterConfig | None = None) -> None:
        self._config = config or TavilyAdapterConfig()

    @property
    def config(self) -> TavilyAdapterConfig:
        return self._config

    def search(self, *, queries: list[str]) -> list[TavilyQueryResult]:
        """Run the bounded query set against Tavily.

        Refuses to run if `TAVILY_API_KEY` is missing.
        """
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError(
                "TAVILY_API_KEY missing from environment; "
                "TavilySearchClient.search() refuses to run."
            )
        if len(queries) > self._config.max_queries:
            raise ValueError(
                f"queries cap exceeded: {len(queries)} > "
                f"{self._config.max_queries}"
            )

        results: list[TavilyQueryResult] = []
        seen_urls: set[str] = set()
        with httpx.Client(timeout=self._config.timeout_s) as client:
            for q in queries:
                payload = {
                    "api_key": api_key,
                    "query": q,
                    "max_results": (
                        self._config.max_results_per_query
                    ),
                    "search_depth": self._config.search_depth,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                }
                try:
                    resp = client.post(
                        _TAVILY_ENDPOINT,
                        json=payload,
                    )
                    resp.raise_for_status()
                except httpx.HTTPError:
                    # Skip failing query; the orchestrator handles
                    # missing-result fallback.
                    continue
                data = resp.json()
                for r in (data.get("results") or [])[
                    :self._config.max_results_per_query
                ]:
                    url = (r.get("url") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    parts = urlsplit(url)
                    results.append(TavilyQueryResult(
                        query=q,
                        title=(r.get("title") or "").strip(),
                        url=url,
                        domain=parts.netloc.lower(),
                        content=(r.get("content") or "").strip(),
                        score=float(r.get("score") or 0.0),
                        extra={},
                    ))
        return results


def __dir__() -> list[str]:
    return [
        "TavilyAdapterConfig",
        "TavilyQueryResult",
        "TavilySearchClient",
        "is_tavily_key_present",
        "redact_tavily_url_for_audit",
    ]
