"""Phase 8.5A — YouTube Data API v3 adapter.

Critical safety properties (drift-tested):

  * The YouTube key is read ONLY from the process environment via
    `os.environ.get("YOUTUBE_DATA_API_KEY")`. Never accepted via
    CLI, never written to disk, never echoed to logs, never
    embedded in audit JSON.
  * `httpx` is the ONLY HTTP transport. (Drift-tested.)
  * The drift test asserts NO unofficial-scraping libs are
    imported anywhere in this package: yt-dlp, youtube-dl,
    pytube, youtube-comment-downloader, scrapetube, requests,
    aiohttp, beautifulsoup4, bs4, selenium, playwright.
  * `YouTubeDataClient.search_videos` and
    `YouTubeDataClient.fetch_comments` REFUSE to run if the key
    is missing.
  * `redact_comment_for_audit` strips email addresses, phone
    numbers, and obvious external-link patterns before audit
    logging.
  * `looks_like_low_quality_comment` rejects: empty, ≤3 chars,
    pure emoji, all-caps spam, "first!" etc., before the comment
    can flow into ingestion.
  * Identity stored: `display_name` (public commenter handle) +
    `comment_id` (for dedup). NEVER channelId, never email,
    never any sensitive PII the API surfaces.

Phase 8.5A does NOT create source_records, does NOT create personas,
and does NOT update traits / evidence-links.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx


_YT_SEARCH_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
_YT_COMMENT_THREADS_ENDPOINT = (
    "https://www.googleapis.com/youtube/v3/commentThreads"
)
_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_MAX_VIDEOS = 3
_DEFAULT_MAX_COMMENTS_TOTAL = 50
_DEFAULT_MAX_COMMENTS_PER_VIDEO = 25


_EMAIL_RE = re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{6,}\d)\b")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


@dataclass(frozen=True)
class YouTubeAdapterConfig:
    """Per-invocation caps for the YouTube client."""
    max_videos: int = _DEFAULT_MAX_VIDEOS
    max_comments_total: int = _DEFAULT_MAX_COMMENTS_TOTAL
    max_comments_per_video: int = _DEFAULT_MAX_COMMENTS_PER_VIDEO
    timeout_s: float = _DEFAULT_TIMEOUT_S
    relevance_language: str = "en"


@dataclass(frozen=True)
class YouTubeVideoResult:
    """Public video metadata only. NO description-thumbnail bloat."""
    video_id: str
    title: str
    channel_title: str
    published_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YouTubeCommentResult:
    """Public comment text + minimal identity (display name only).

    NEVER stores channelId, email, phone, or any PII the API exposes.
    """
    video_id: str
    comment_id: str
    text: str
    display_name: str
    like_count: int = 0
    published_at: str | None = None


def is_youtube_key_present() -> bool:
    """Return True iff `YOUTUBE_DATA_API_KEY` is in the environment.

    NEVER returns or logs the value itself."""
    return bool(os.environ.get("YOUTUBE_DATA_API_KEY"))


def redact_comment_for_audit(text: str) -> str:
    """Replace emails, phone numbers, and external links with `[REDACTED]`.

    Comments are public, but the framework still treats personally-
    identifying content carefully — email/phone in a comment is a
    flag that the commenter is sharing private contact info, not
    that the framework should propagate it. URLs are dropped to
    avoid SEO + tracking-pixel propagation."""
    out = _EMAIL_RE.sub("[REDACTED-EMAIL]", text)
    out = _PHONE_RE.sub("[REDACTED-PHONE]", out)
    out = _URL_RE.sub("[REDACTED-URL]", out)
    return out


def looks_like_low_quality_comment(text: str) -> bool:
    """Reject empty, very short, pure-emoji, all-caps-spam, or
    "first!"-style comments before they can flow into ingestion."""
    s = (text or "").strip()
    if len(s) < 4:
        return True
    # All-caps spam (≥80% upper among letters, length ≥ 6)
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 6:
        upper = sum(1 for c in letters if c.isupper())
        if upper / len(letters) >= 0.8:
            return True
    # "first!" / "first comment" / etc.
    low = s.lower().strip("!. ")
    if low in {"first", "first comment", "early", "im first", "im early"}:
        return True
    # Pure emoji (no alpha-num)
    if not any(c.isalnum() for c in s):
        return True
    return False


class YouTubeDataClient:
    """Thin YouTube Data API v3 client.

    Construction does NOT make a network call and does NOT require
    the API key. Only `search_videos` / `fetch_comments` require it.
    """

    def __init__(self, config: YouTubeAdapterConfig | None = None) -> None:
        self._config = config or YouTubeAdapterConfig()

    @property
    def config(self) -> YouTubeAdapterConfig:
        return self._config

    def search_videos(
        self, *, query: str, max_results: int | None = None,
    ) -> list[YouTubeVideoResult]:
        """Return up to `max_results` video metadata records for the
        query via the official `search.list` endpoint."""
        api_key = os.environ.get("YOUTUBE_DATA_API_KEY")
        if not api_key:
            raise RuntimeError(
                "YOUTUBE_DATA_API_KEY missing from environment; "
                "YouTubeDataClient.search_videos() refuses to run."
            )
        n = min(max_results or self._config.max_videos, self._config.max_videos)
        params = {
            "key": api_key,
            "q": query,
            "part": "snippet",
            "type": "video",
            "maxResults": str(n),
            "relevanceLanguage": self._config.relevance_language,
        }
        results: list[YouTubeVideoResult] = []
        with httpx.Client(timeout=self._config.timeout_s) as client:
            resp = client.get(_YT_SEARCH_ENDPOINT, params=params)
            resp.raise_for_status()
            payload = resp.json()
            for item in (payload.get("items") or [])[:n]:
                vid = (item.get("id") or {}).get("videoId")
                snip = item.get("snippet") or {}
                if not vid:
                    continue
                results.append(YouTubeVideoResult(
                    video_id=vid,
                    title=snip.get("title", ""),
                    channel_title=snip.get("channelTitle", ""),
                    published_at=snip.get("publishedAt"),
                    extra={},
                ))
        return results

    def fetch_comments(
        self, *, video_id: str, max_comments: int | None = None,
    ) -> list[YouTubeCommentResult]:
        """Pull up to `max_comments` top-level comments for the video
        via the official `commentThreads.list` endpoint.

        Filters out low-quality / empty comments AND redacts
        emails/phones/URLs before returning."""
        api_key = os.environ.get("YOUTUBE_DATA_API_KEY")
        if not api_key:
            raise RuntimeError(
                "YOUTUBE_DATA_API_KEY missing from environment; "
                "YouTubeDataClient.fetch_comments() refuses to run."
            )
        n = min(
            max_comments or self._config.max_comments_per_video,
            self._config.max_comments_per_video,
        )
        params = {
            "key": api_key,
            "videoId": video_id,
            "part": "snippet",
            "maxResults": str(n),
            "textFormat": "plainText",
            "order": "relevance",
        }
        out: list[YouTubeCommentResult] = []
        with httpx.Client(timeout=self._config.timeout_s) as client:
            resp = client.get(_YT_COMMENT_THREADS_ENDPOINT, params=params)
            if resp.status_code in (403, 404):
                # 403 = comments disabled; 404 = video missing.
                # Both are honest "no data" and not adapter errors.
                return []
            resp.raise_for_status()
            payload = resp.json()
            for item in (payload.get("items") or [])[:n]:
                snip = (
                    ((item.get("snippet") or {}).get("topLevelComment")
                     or {}).get("snippet") or {}
                )
                cid = (item.get("id") or "")
                raw = snip.get("textDisplay", "") or ""
                if looks_like_low_quality_comment(raw):
                    continue
                out.append(YouTubeCommentResult(
                    video_id=video_id,
                    comment_id=cid,
                    text=redact_comment_for_audit(raw),
                    display_name=snip.get("authorDisplayName", ""),
                    like_count=int(snip.get("likeCount") or 0),
                    published_at=snip.get("publishedAt"),
                ))
        return out
