"""Phase 8.3B — Firecrawl-specific error types.

All exceptions raised by the adapter inherit from `FirecrawlError`.
Each carries a structured `reason_code` (where applicable) so that
upstream callers can decide whether to skip-and-continue or fail-fast.
"""
from __future__ import annotations


class FirecrawlError(Exception):
    """Base for all Firecrawl-adapter failures."""


class FirecrawlApiKeyMissing(FirecrawlError):
    """Live path invoked without the Firecrawl API key in the
    environment.

    The error message references only the env-var NAME, never the
    value. The adapter will not echo the (non-existent or invalid)
    key in any log line or error trace.
    """


class FirecrawlComplianceNotApproved(FirecrawlError):
    """`adapter_compliance_status` row is not yet `'approved'` for
    `adapter_name='firecrawl_extract'`. Wraps the underlying
    `ComplianceError` so the Firecrawl boundary is preserved."""


class FirecrawlBlockedPage(FirecrawlError):
    """Page rejected: robots.txt disallow, 4xx / 5xx status, paywall
    / login-wall heuristic, or Firecrawl's `scrapeStatus='blocked'`
    shape."""

    def __init__(
        self, *, url: str, reason_code: str, message: str,
    ) -> None:
        self.url = url
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {url}: {message}")


class FirecrawlBodyTooShort(FirecrawlError):
    """Extracted body is below the per-record `min_chars` floor.

    Treated as an extraction-quality failure; the page is refused
    rather than persisted as a stub.
    """


class FirecrawlBodyRedactionFailed(FirecrawlError):
    """Post-redaction body either fell below `min_chars` or still
    carries residual identity markers. The page is refused — never
    partially returned."""


class FirecrawlMetadataMalformed(FirecrawlError):
    """Firecrawl response metadata could not be coerced into the
    closed `FirecrawlExtractionMetadata` schema."""


class FirecrawlBotProtectionPlaceholder(FirecrawlError):
    """Phase 8.3B-LIVE-1.5: page body contains dominant bot-protection
    or placeholder markers (`Something went wrong. Wait a moment and
    try again.` / `verify you are human` / `captcha` /
    `enable JavaScript` / `sign in to continue` / `access denied` /
    `temporarily blocked`). The page is refused, never partially
    returned. Reason code: `BOT_OR_PLACEHOLDER_CONTENT`.
    """

    def __init__(self, *, url: str, marker: str) -> None:
        self.url = url
        self.marker = marker
        super().__init__(
            f"BOT_OR_PLACEHOLDER_CONTENT: {url}: matched marker "
            f"{marker!r}"
        )


class FirecrawlBoilerplateDominated(FirecrawlError):
    """Phase 8.3B-LIVE-1.5: page body is dominated by navigation /
    sidebar / link / breadcrumb boilerplate; substantive sentence
    count is below the floor. The page is refused.
    Reason code: `BOILERPLATE_DOMINATED`.
    """

    def __init__(
        self,
        *,
        url: str,
        nav_link_ratio: float,
        substantive_sentence_count: int,
    ) -> None:
        self.url = url
        self.nav_link_ratio = nav_link_ratio
        self.substantive_sentence_count = substantive_sentence_count
        super().__init__(
            f"BOILERPLATE_DOMINATED: {url}: "
            f"nav_link_ratio={nav_link_ratio:.2f}, "
            f"substantive_sentences={substantive_sentence_count}"
        )


__all__ = [
    "FirecrawlApiKeyMissing",
    "FirecrawlBlockedPage",
    "FirecrawlBodyRedactionFailed",
    "FirecrawlBodyTooShort",
    "FirecrawlBoilerplateDominated",
    "FirecrawlBotProtectionPlaceholder",
    "FirecrawlComplianceNotApproved",
    "FirecrawlError",
    "FirecrawlMetadataMalformed",
]
