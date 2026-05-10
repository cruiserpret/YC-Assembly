"""Phase 8.3B — Firecrawl extraction HTTP client.

Single-URL extraction client. Receives a public URL (typically
discovered earlier by Tavily) and returns a
`FirecrawlExtractedPage` whose `body_markdown` has already been
through pre-store redaction.

Critical safety properties (drift tests + extraction tests assert
each):

  * `httpx` is imported here and ONLY here in the Firecrawl package.
  * `FIRECRAWL_API_KEY` is read here via `os.environ.get(...)` and
    NOT in any other module of the package outside `compliance_gate.py`
    (which references the name in docstrings only).
  * The live path raises `FirecrawlApiKeyMissing` if the key is unset.
  * The compliance gate is NOT auto-invoked by the constructor: the
    caller is responsible for calling `assert_firecrawl_approved`
    BEFORE invoking `extract`. This separation lets tests construct
    a client without touching the DB.
  * The adapter NEVER persists any row. It returns a typed page; the
    caller routes that page through `prepare_source_record_insert`
    when persistence is desired.
  * Pre-store redaction runs inside `_build_extracted_page` BEFORE
    the page object is returned. `body_markdown` on the result is
    the redacted body. Residual identity markers cause refusal.
  * `repr()` / `__str__` never reference the API key.
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from assembly.pipeline.ingestion.firecrawl.errors import (
    FirecrawlApiKeyMissing,
    FirecrawlBlockedPage,
    FirecrawlBodyRedactionFailed,
    FirecrawlBodyTooShort,
    FirecrawlBoilerplateDominated,
    FirecrawlBotProtectionPlaceholder,
    FirecrawlError,
    FirecrawlMetadataMalformed,
)
from assembly.pipeline.ingestion.firecrawl.types import (
    FirecrawlExtractedPage,
    FirecrawlExtractionMetadata,
)
from assembly.pipeline.persona.anonymization import redact_identity_markers


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_ENV_VAR = "FIRECRAWL_API_KEY"
_BASE_URL = "https://api.firecrawl.dev"
_SCRAPE_PATH = "/v1/scrape"

DEFAULT_MAX_CHARS = 8000
DEFAULT_MIN_CHARS = 80
DEFAULT_TIMEOUT_S = 30.0
HARD_MAX_CHARS = 200_000

# Phase 8.3B-LIVE-1.5 — substantive-content floor. Pages must surface
# at least this many "sentence-shaped" lines after redaction; below
# this, the body is treated as boilerplate-dominated and refused.
DEFAULT_MIN_SUBSTANTIVE_SENTENCES = 3
DEFAULT_MAX_NAV_LINK_RATIO = 0.50

TRUNCATION_MARKER = "\n\n…[TRUNCATED]"


# ---------------------------------------------------------------------------
# Heuristics — paywall / blocked / robots-disallow detection
# ---------------------------------------------------------------------------


_PAYWALL_OR_LOGIN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpaywall\b", re.IGNORECASE),
    re.compile(r"\bsubscribe to (?:read|continue|view)\b", re.IGNORECASE),
    re.compile(r"\bsign in to (?:view|read|continue)\b", re.IGNORECASE),
    re.compile(r"\bmembers? only\b", re.IGNORECASE),
    re.compile(r"\baccount required\b", re.IGNORECASE),
    re.compile(r"\brequires login\b", re.IGNORECASE),
    re.compile(r"\blogin required\b", re.IGNORECASE),
    re.compile(r"\bpremium content\b", re.IGNORECASE),
    re.compile(
        r"\bunlock (?:full|premium) (?:article|content)\b",
        re.IGNORECASE,
    ),
)


_RESIDUAL_IDENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,30}\b"),
    re.compile(
        r"https?://[^\s]*?/(?:u|user|@)/[A-Za-z0-9_-]+",
        re.IGNORECASE,
    ),
)


# Phase 8.3B-LIVE-1.5 — bot-protection / placeholder markers. Any of
# these in the body triggers a clean refusal with reason
# `BOT_OR_PLACEHOLDER_CONTENT`. The list mirrors the operator's spec
# in the phase prompt; expanding it requires a memo update.
_BOT_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "Something went wrong. Wait a moment and try again",
    "verify you are human",
    "captcha",
    "enable JavaScript",
    "sign in to continue",
    "access denied",
    "temporarily blocked",
    "Please enable cookies",
    "Cloudflare",
    "Just a moment",
)


# Markdown-link-line regex: a line that's only a bullet/dash + a
# Markdown link (the pattern Firecrawl emits for nav menus,
# breadcrumbs, sidebar topic lists).
_MD_LINK_LINE_RE: re.Pattern[str] = re.compile(
    r"^\s*[-*•]?\s*\[[^\]]{1,200}\]\([^\)]+\)\s*$"
)
# A "Skip to" or breadcrumb-shape line (Firecrawl's accessibility shims).
_SKIP_OR_NAV_LINE_RE: re.Pattern[str] = re.compile(
    r"^\s*\[?(?:Skip to|Go to|Sign In|Home|Menu)\b", re.IGNORECASE
)


def _looks_paywalled(body: str) -> bool:
    return any(p.search(body) for p in _PAYWALL_OR_LOGIN_PATTERNS)


def _residual_identity_markers(text: str) -> bool:
    return any(p.search(text or "") for p in _RESIDUAL_IDENTITY_PATTERNS)


def _matched_bot_placeholder(body: str) -> str | None:
    """Return the first bot-placeholder marker found in `body`, or None.
    Case-insensitive substring match."""
    if not body:
        return None
    body_lower = body.lower()
    for marker in _BOT_PLACEHOLDER_MARKERS:
        if marker.lower() in body_lower:
            return marker
    return None


def _nav_link_ratio(body: str) -> tuple[float, int, int]:
    """Return (ratio, nav_link_lines, total_nonempty_lines). Ratio is
    the fraction of nonempty lines that look like markdown nav/link
    boilerplate."""
    if not body:
        return 0.0, 0, 0
    total = 0
    nav = 0
    for ln in body.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        total += 1
        if _MD_LINK_LINE_RE.match(stripped) or _SKIP_OR_NAV_LINE_RE.match(stripped):
            nav += 1
    ratio = (nav / total) if total else 0.0
    return ratio, nav, total


# Sentence-shaped line: at least 4 word-tokens, contains lower-case
# letters AND a sentence-terminator-or-clause-shape, NOT entirely
# inside a markdown link. Used to count substantive content after
# nav stripping.
_WORD_RE = re.compile(r"\b[a-zA-Z][a-zA-Z'\-]{1,}\b")
_SENTENCE_SHAPE_RE = re.compile(r"[.!?]")


def _count_substantive_sentences(body: str) -> int:
    """Count substantive sentences in `body`. A "sentence" is a
    sentence-terminator-bounded chunk that, after stripping nav/link
    lines and markdown-link syntax, has ≥ 4 word-tokens.

    Heuristic goal: distinguish bodies dominated by nav/link/menu
    lines (low count) from bodies that contain real prose (high
    count). One paragraph with 3 sentences should count as 3, not 1."""
    if not body:
        return 0
    # First, drop lines that are entirely nav/link boilerplate so
    # they cannot contribute their own sentence count even if they
    # contain a `.` (e.g. URL ending with `.com.`).
    kept_lines: list[str] = []
    for ln in body.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        if _MD_LINK_LINE_RE.match(stripped):
            continue
        if _SKIP_OR_NAV_LINE_RE.match(stripped):
            continue
        kept_lines.append(stripped)
    if not kept_lines:
        return 0
    # Strip markdown-link syntax: replace `[text](url)` with `text` so
    # word counts inside sentences like "I [hated] the experience."
    # remain accurate.
    joined = re.sub(
        r"\[([^\]]+)\]\([^)]+\)", r"\1", "\n".join(kept_lines)
    )
    # Split into sentence chunks by sentence terminators.
    sentence_chunks = re.split(r"[.!?]+", joined)
    count = 0
    for chunk in sentence_chunks:
        words = _WORD_RE.findall(chunk)
        if len(words) >= 4:
            count += 1
    return count


# ---------------------------------------------------------------------------
# FirecrawlClient
# ---------------------------------------------------------------------------


class FirecrawlClient:
    """Per-URL Firecrawl extraction client.

    Live mode requires `FIRECRAWL_API_KEY` in the environment AND a
    prior `assert_firecrawl_approved(sessionmaker)` call by the caller
    (the gate is intentionally NOT invoked here so tests can construct
    a client with an injected `http_factory` without touching the DB).

    Caps:
      * `max_chars`: per-record body cap (default 8000; hard ceiling 200000)
      * `min_chars`: per-record body floor (default 80)
      * `timeout_s`: per-request timeout (default 30s)

    Test seam:
      * `http_factory(api_key)` → object usable as `async with`. Tests
        inject a stub that returns canned payloads without making any
        real HTTP call. When `http_factory is None`, the live path
        creates a real `httpx.AsyncClient`.
    """

    NAME = "firecrawl_extract"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_factory: Any | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        min_chars: int = DEFAULT_MIN_CHARS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        only_main_content: bool = True,
        max_nav_link_ratio: float = DEFAULT_MAX_NAV_LINK_RATIO,
        min_substantive_sentences: int = DEFAULT_MIN_SUBSTANTIVE_SENTENCES,
    ) -> None:
        if max_chars > HARD_MAX_CHARS:
            raise ValueError(
                f"max_chars={max_chars} exceeds hard ceiling "
                f"{HARD_MAX_CHARS}"
            )
        if min_chars < 1:
            raise ValueError(
                f"min_chars must be >= 1, got {min_chars}"
            )
        if not 0.0 < max_nav_link_ratio <= 1.0:
            raise ValueError(
                f"max_nav_link_ratio={max_nav_link_ratio} must be in (0, 1]"
            )
        if min_substantive_sentences < 1:
            raise ValueError(
                f"min_substantive_sentences must be >= 1, "
                f"got {min_substantive_sentences}"
            )
        self._api_key = api_key
        self._http_factory = http_factory
        self._max_chars = max_chars
        self._min_chars = min_chars
        self._timeout_s = timeout_s
        # Phase 8.3B-LIVE-1.5 hardening flags
        self._only_main_content = only_main_content
        self._max_nav_link_ratio = max_nav_link_ratio
        self._min_substantive_sentences = min_substantive_sentences

    def __repr__(self) -> str:
        # Never echo the api key.
        return (
            f"<FirecrawlClient max_chars={self._max_chars} "
            f"min_chars={self._min_chars} "
            f"only_main_content={self._only_main_content}>"
        )

    @property
    def max_chars(self) -> int:
        return self._max_chars

    @property
    def min_chars(self) -> int:
        return self._min_chars

    # ---- Live path ---------------------------------------------------

    async def extract(self, url: str) -> FirecrawlExtractedPage:
        """Extract one URL via Firecrawl.

        The caller MUST have already invoked
        `assert_firecrawl_approved(sessionmaker)` before reaching this
        method; the client itself does not invoke the gate so tests can
        run without DB state.
        """
        if not isinstance(url, str) or not url.startswith(
            ("http://", "https://")
        ):
            raise FirecrawlError(
                f"Firecrawl: invalid URL shape: {url!r}"
            )
        api_key = self._api_key or os.environ.get(_ENV_VAR)
        if not api_key or not api_key.strip():
            # NEVER include the env-var value in the message.
            raise FirecrawlApiKeyMissing(
                f"{_ENV_VAR} not set; refusing to run Firecrawl live extract."
            )
        api_key = api_key.strip()

        client_cm = (
            self._http_factory(api_key)
            if self._http_factory is not None
            else httpx.AsyncClient(timeout=self._timeout_s)
        )
        captured_at = datetime.now(UTC)
        # Phase 8.3B-LIVE-1.5: send `onlyMainContent: true` so Firecrawl
        # strips nav / sidebar / breadcrumb boilerplate at the provider
        # layer. Falls back to false only if the constructor was given
        # `only_main_content=False` (test seam).
        request_body: dict[str, Any] = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": bool(self._only_main_content),
        }
        async with client_cm as client:
            try:
                resp = await client.post(
                    f"{_BASE_URL}{_SCRAPE_PATH}",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
            except httpx.HTTPError as e:
                raise FirecrawlBlockedPage(
                    url=url,
                    reason_code="HTTP_TRANSPORT",
                    message=f"transport failure: {type(e).__name__}",
                ) from e
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise FirecrawlBlockedPage(
                    url=url,
                    reason_code=f"HTTP_{resp.status_code}",
                    message="upstream returned non-2xx",
                ) from e
            try:
                payload = resp.json()
            except Exception as e:
                raise FirecrawlError(
                    f"Firecrawl: response was not valid JSON: "
                    f"{type(e).__name__}"
                ) from e

        return self._build_extracted_page(
            requested_url=url,
            payload=payload,
            captured_at=captured_at,
        )

    # ---- Pure: payload → typed page (used by both live + tests) -----

    def _build_extracted_page(
        self,
        *,
        requested_url: str,
        payload: dict[str, Any],
        captured_at: datetime,
    ) -> FirecrawlExtractedPage:
        """Pure: turn a Firecrawl scrape response payload into a
        `FirecrawlExtractedPage`. Tests inject payloads here directly
        via the public alias `build_extracted_page_from_payload`."""
        if not isinstance(payload, dict):
            raise FirecrawlError("Firecrawl: top-level response is not a dict")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise FirecrawlError(
                "Firecrawl: response.data is not a dict"
            )
        body = data.get("markdown") or data.get("content") or ""
        if not isinstance(body, str):
            raise FirecrawlError(
                "Firecrawl returned a non-string body"
            )

        meta_block = data.get("metadata") or {}
        if not isinstance(meta_block, dict):
            meta_block = {}

        # 1. Detect blocked / paywalled / robots-disallowed shapes
        # BEFORE trimming the body, so we can refuse with the right
        # reason_code regardless of body length.
        if _looks_blocked_metadata(meta_block):
            raise FirecrawlBlockedPage(
                url=requested_url,
                reason_code="ROBOTS_OR_BLOCKED",
                message=(
                    "page metadata signals robots-disallow / blocked / "
                    "non-2xx status / scrapeStatus='blocked'"
                ),
            )

        body_stripped = body.strip()
        if len(body_stripped) < self._min_chars:
            raise FirecrawlBodyTooShort(
                f"extracted body {len(body_stripped)} chars < "
                f"min_chars={self._min_chars}"
            )

        if _looks_paywalled(body_stripped):
            raise FirecrawlBlockedPage(
                url=requested_url,
                reason_code="PAYWALL_OR_LOGIN_WALL",
                message=(
                    "extracted body contains paywall / login-wall / "
                    "premium-content markers"
                ),
            )

        # Phase 8.3B-LIVE-1.5 — bot-protection placeholder refusal.
        # Runs BEFORE truncation so a placeholder body with the marker
        # past the cap is still caught.
        bot_marker = _matched_bot_placeholder(body_stripped)
        if bot_marker is not None:
            raise FirecrawlBotProtectionPlaceholder(
                url=requested_url, marker=bot_marker,
            )

        # Phase 8.3B-LIVE-1.5 — boilerplate / nav-ratio refusal. Counts
        # nav-link lines vs total nonempty lines AND substantive
        # sentences. Either threshold breach refuses the page.
        ratio, _nav_lines, _total = _nav_link_ratio(body_stripped)
        substantive_count = _count_substantive_sentences(body_stripped)
        if (
            ratio > self._max_nav_link_ratio
            or substantive_count < self._min_substantive_sentences
        ):
            raise FirecrawlBoilerplateDominated(
                url=requested_url,
                nav_link_ratio=ratio,
                substantive_sentence_count=substantive_count,
            )

        truncated = False
        if len(body_stripped) > self._max_chars:
            body_stripped = (
                body_stripped[: self._max_chars] + TRUNCATION_MARKER
            )
            truncated = True

        # 2. Pre-store redaction. ALWAYS runs. The adapter contract is
        # that `body_markdown` on the returned page is the redacted body.
        redacted = redact_identity_markers(body_stripped) or ""
        if len(redacted.strip()) < self._min_chars:
            raise FirecrawlBodyRedactionFailed(
                "post-redaction body fell below min_chars; refusing "
                "the page rather than partially returning."
            )
        if _residual_identity_markers(redacted):
            raise FirecrawlBodyRedactionFailed(
                "post-redaction body still carries identity markers; "
                "refusing the page."
            )

        # 3. Coerce metadata into the closed schema. Fields not in the
        # schema are dropped at parse time.
        try:
            metadata = FirecrawlExtractionMetadata(
                requested_url=requested_url,
                final_url=_str_or_none(
                    meta_block.get("sourceURL")
                    or meta_block.get("url")
                    or requested_url
                ),
                title=_str_or_none(meta_block.get("title")),
                source_status_code=_int_or_none(
                    meta_block.get("statusCode")
                ),
                content_type=_str_or_none(meta_block.get("contentType")),
                page_lang=_str_or_none(meta_block.get("language")),
                robots_allowed=_bool_or_none(
                    meta_block.get("robotsAllowed")
                ),
                scraped_via="firecrawl_v1_scrape",
            )
        except Exception as e:
            raise FirecrawlMetadataMalformed(
                f"FirecrawlExtractionMetadata: {type(e).__name__}: {e}"
            ) from e

        final_url = metadata.final_url or requested_url
        return FirecrawlExtractedPage(
            requested_url=requested_url,
            final_url=str(final_url),
            title=metadata.title,
            body_markdown=redacted,
            body_chars=len(redacted),
            captured_at=captured_at,
            truncated=truncated,
            metadata=metadata,
        )


def build_extracted_page_from_payload(
    *,
    client: FirecrawlClient,
    requested_url: str,
    payload: dict[str, Any],
    captured_at: datetime | None = None,
) -> FirecrawlExtractedPage:
    """Public test seam — runs the same parse + redact + audit path
    that the live `extract` runs after the HTTP call. Tests use this
    to drive the parser without instantiating any HTTP transport.

    No live HTTP call is made; no DB call is made; no environment is
    read. Suitable for unit tests.
    """
    return client._build_extracted_page(
        requested_url=requested_url,
        payload=payload,
        captured_at=captured_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# helpers — defensive coercion of upstream metadata fields
# ---------------------------------------------------------------------------


def _looks_blocked_metadata(meta: dict[str, Any]) -> bool:
    sc = meta.get("statusCode")
    if isinstance(sc, int) and (sc >= 400 or sc == 0):
        return True
    if meta.get("robotsAllowed") is False:
        return True
    if isinstance(meta.get("scrapeStatus"), str) and meta[
        "scrapeStatus"
    ].lower() in ("blocked", "robots_disallowed", "rejected"):
        return True
    if isinstance(meta.get("error"), str):
        return True
    return False


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MIN_CHARS",
    "DEFAULT_TIMEOUT_S",
    "FirecrawlClient",
    "HARD_MAX_CHARS",
    "TRUNCATION_MARKER",
    "build_extracted_page_from_payload",
]
