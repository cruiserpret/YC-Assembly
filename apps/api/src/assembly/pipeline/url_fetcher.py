"""Lightweight URL fetcher with zero-leakage cutoff-date guard (C3).

Behavior:

  - http(s) only; reject other schemes.
  - 10s timeout, 1 MB max body.
  - No JS rendering — we read what `httpx.AsyncClient.get` returns.
  - Plain-text extraction via BeautifulSoup (`html.parser`, no lxml dep).
  - **Zero-leakage rule**: if `cutoff_date` is set, refuse live fetches.
    The caller must pass either a pre-captured snapshot file path or a known
    archive URL. If neither is available, raise `CutoffViolationError` so
    the evidence builder can record the gap as `kind=missing` and continue.

This module never calls an LLM; it only fetches and extracts text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from assembly.llm.errors import CutoffViolationError

logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_S: float = 10.0
DEFAULT_MAX_BYTES: int = 1_000_000
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Snapshot scheme: snapshot files can also be referenced via `file://` URIs
# or absolute filesystem paths. The url_fetcher accepts both.
SNAPSHOT_SCHEMES: frozenset[str] = frozenset({"file"})


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    captured_at: datetime
    status_code: int
    content_type: str
    text: str  # extracted plain text
    truncated: bool
    source_kind: str  # "live" | "snapshot"
    snapshot_path: str | None = None
    extra: dict = field(default_factory=dict)


class FetchError(Exception):
    """Wrapping HTTP / IO / size errors so callers don't leak httpx into the pipeline."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_url(
    url: str,
    *,
    cutoff_date: date | None = None,
    snapshot: str | Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = "AssemblyEvidenceBot/0.1 (+contact:assembly@example.com)",
) -> FetchedPage:
    """Fetch a URL or read a snapshot. Honors the zero-leakage cutoff rule.

    Args:
      url: the canonical URL we want evidence from.
      cutoff_date: if set, live fetches are refused. A snapshot is required.
      snapshot: optional path to a pre-captured copy of the URL's content.
                Accepts `Path`, absolute string path, or `file://` URI.
                Required when `cutoff_date` is set.
      timeout_s: socket timeout for live fetches.
      max_bytes: hard cap on body size; truncated bodies set `truncated=True`.

    Raises:
      ValueError: bad scheme.
      CutoffViolationError: cutoff_date set but no snapshot provided.
      FetchError: any HTTP / IO failure, or response too large to handle.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")

    if cutoff_date is not None and snapshot is None:
        raise CutoffViolationError(
            f"refusing to live-fetch {url!r}: simulation has "
            f"evidence_cutoff_date={cutoff_date} but no snapshot was provided. "
            "Use evidence_builder's missing-evidence path or supply a snapshot."
        )

    if snapshot is not None:
        return _read_snapshot(url=url, snapshot=snapshot, max_bytes=max_bytes)

    return await _fetch_live(
        url=url,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        user_agent=user_agent,
    )


# ---------------------------------------------------------------------------
# Live fetch
# ---------------------------------------------------------------------------


async def _fetch_live(
    *, url: str, timeout_s: float, max_bytes: int, user_agent: str
) -> FetchedPage:
    headers = {"User-Agent": user_agent, "Accept": "text/html,*/*;q=0.5"}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout_s,
            limits=httpx.Limits(max_connections=4),
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"http error fetching {url!r}: {e}") from e

    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    body_bytes = response.content[:max_bytes]
    truncated = len(response.content) > max_bytes

    text = _extract_text(body_bytes, content_type)

    return FetchedPage(
        url=url,
        final_url=str(response.url),
        captured_at=datetime.now(UTC),
        status_code=response.status_code,
        content_type=content_type,
        text=text,
        truncated=truncated,
        source_kind="live",
    )


# ---------------------------------------------------------------------------
# Snapshot read
# ---------------------------------------------------------------------------


def _read_snapshot(
    *, url: str, snapshot: str | Path, max_bytes: int
) -> FetchedPage:
    if isinstance(snapshot, str) and snapshot.startswith("file://"):
        path = Path(urlparse(snapshot).path)
    else:
        path = Path(snapshot)

    if not path.is_file():
        raise FetchError(f"snapshot not found: {path}")

    body_bytes = path.read_bytes()[:max_bytes]
    truncated = len(path.read_bytes()) > max_bytes

    # Best-effort content type from suffix.
    suffix = path.suffix.lower()
    content_type = {
        ".html": "text/html",
        ".htm": "text/html",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(suffix, "application/octet-stream")

    text = _extract_text(body_bytes, content_type)

    return FetchedPage(
        url=url,
        final_url=url,
        captured_at=datetime.now(UTC),
        status_code=200,
        content_type=content_type,
        text=text,
        truncated=truncated,
        source_kind="snapshot",
        snapshot_path=str(path),
    )


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def _extract_text(body: bytes, content_type: str) -> str:
    """Return plain text. For html, strip tags via BeautifulSoup; for non-html,
    decode as UTF-8 with replace."""
    decoded = body.decode("utf-8", errors="replace")

    if "html" in content_type.lower():
        try:
            from bs4 import BeautifulSoup  # type: ignore[import-not-found]
        except ImportError:
            # bs4 is in pyproject deps; if it's missing in dev, return raw.
            logger.warning("bs4 not installed; returning raw HTML as text")
            return decoded

        soup = BeautifulSoup(decoded, "html.parser")
        # Drop script/style/nav/footer to reduce noise.
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        return _collapse_whitespace(text)

    return _collapse_whitespace(decoded)


def _collapse_whitespace(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
