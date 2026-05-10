"""Phase 8.5A — Brave Search adapter.

Brave is treated as a DISCOVERY provider only. Snippets and URLs
returned by `BraveSearchClient.search` are CANDIDATE evidence and
must flow through the existing Phase 8.2x extraction + redaction
+ sensitive-filter + dedup pipeline before any persona ever sees
them.

Critical safety properties (drift-tested):

  * The Brave key is read ONLY from the process environment via
    `os.environ.get("BRAVE_SEARCH_API_KEY")`. Never accepted via
    CLI, never written to disk, never echoed to logs, never
    embedded in audit JSON.
  * `httpx` is the ONLY HTTP transport. No `requests`, no
    `urllib`, no `aiohttp`. (Drift-tested.)
  * `BraveSearchClient.search` REFUSES to run if the key is
    missing.
  * Hard caps: at most `max_queries × max_results_per_query`
    requests in a single client invocation; the preflight script
    further restricts to 3 × 5 for the 8.5A dry-run shape.
  * `redact_url_for_audit` removes any query-string parameter
    that looks like a tracking token before audit logging.

Phase 8.5A does NOT write to `source_records`, does NOT create
personas, and does NOT update traits / evidence-links.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


_BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_MAX_QUERIES = 3
_DEFAULT_MAX_RESULTS_PER_QUERY = 5


@dataclass(frozen=True)
class BraveAdapterConfig:
    """Per-invocation caps for the Brave client."""
    max_queries: int = _DEFAULT_MAX_QUERIES
    max_results_per_query: int = _DEFAULT_MAX_RESULTS_PER_QUERY
    timeout_s: float = _DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class BraveQueryResult:
    """Normalized Brave result. NO key fields. NO tracking params."""
    query: str
    title: str
    url: str
    domain: str
    description: str
    age: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def is_brave_key_present() -> bool:
    """Return True iff `BRAVE_SEARCH_API_KEY` is in the environment.

    NEVER returns or logs the value itself. Used by preflight scripts
    to decide whether a `--live` flag can run.
    """
    return bool(os.environ.get("BRAVE_SEARCH_API_KEY"))


def redact_url_for_audit(url: str) -> str:
    """Strip query-string params that look like tracking tokens before
    writing a URL into an audit JSON.

    Conservative: drops the entire query string. Brave returns the
    canonical URL in `url`; if a downstream pipeline ever needs the
    tracked variant, that's a new explicit decision."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return url


def build_brave_query_set(
    *,
    product_name: str,
    competitors: list[str],
    extra_terms: list[str] | None = None,
    max_queries: int = _DEFAULT_MAX_QUERIES,
) -> list[str]:
    """Compose the bounded Triton-style discovery query set.

    The exact text is product-agnostic — the caller passes the
    product name + competitor list. For Triton this produces queries
    like "Red Bull vs Monster review" / "Celsius energy drink review"
    rather than "Triton review" (which would have zero useful hits
    since Triton is unlaunched)."""
    extra_terms = extra_terms or []
    queries: list[str] = []
    # 1. competitor-vs-competitor pairs (most useful for unlaunched
    # market-entry briefs because they surface comparison content)
    for i in range(min(2, len(competitors))):
        for j in range(i + 1, len(competitors)):
            queries.append(f"{competitors[i]} vs {competitors[j]} review")
            if len(queries) >= max_queries:
                return queries[:max_queries]
    # 2. single-competitor reviews
    for c in competitors:
        queries.append(f"{c} review")
        if len(queries) >= max_queries:
            return queries[:max_queries]
    # 3. product-name + extra terms
    for term in extra_terms:
        queries.append(f"{product_name} {term}".strip())
        if len(queries) >= max_queries:
            return queries[:max_queries]
    return queries[:max_queries]


class BraveSearchClient:
    """Thin Brave Search HTTP client.

    Construction does NOT make a network call and does NOT require
    the API key. Only `search()` requires the key.
    """

    def __init__(self, config: BraveAdapterConfig | None = None) -> None:
        self._config = config or BraveAdapterConfig()

    @property
    def config(self) -> BraveAdapterConfig:
        return self._config

    def search(self, *, queries: list[str]) -> list[BraveQueryResult]:
        """Run the bounded query set against Brave Web Search.

        Refuses to run if `BRAVE_SEARCH_API_KEY` is missing.
        """
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if not api_key:
            raise RuntimeError(
                "BRAVE_SEARCH_API_KEY missing from environment; "
                "BraveSearchClient.search() refuses to run."
            )
        if len(queries) > self._config.max_queries:
            raise ValueError(
                f"queries cap exceeded: {len(queries)} > "
                f"{self._config.max_queries}"
            )

        results: list[BraveQueryResult] = []
        seen_urls: set[str] = set()
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        }
        with httpx.Client(timeout=self._config.timeout_s) as client:
            for q in queries:
                params = {
                    "q": q,
                    "count": str(self._config.max_results_per_query),
                }
                resp = client.get(
                    _BRAVE_SEARCH_ENDPOINT,
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                payload = resp.json()
                web = payload.get("web") or {}
                for r in (web.get("results") or [])[
                    :self._config.max_results_per_query
                ]:
                    url = (r.get("url") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    parts = urlsplit(url)
                    results.append(BraveQueryResult(
                        query=q,
                        title=(r.get("title") or "").strip(),
                        url=url,
                        domain=parts.netloc.lower(),
                        description=(r.get("description") or "").strip(),
                        age=r.get("age"),
                        extra={},
                    ))
        return results


def __dir__() -> list[str]:
    return [
        "BraveAdapterConfig",
        "BraveQueryResult",
        "BraveSearchClient",
        "build_brave_query_set",
        "is_brave_key_present",
        "redact_url_for_audit",
    ]
