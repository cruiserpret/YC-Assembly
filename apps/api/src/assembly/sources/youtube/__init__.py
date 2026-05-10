"""Phase 8.5A — YouTube Data API v3 adapter scaffold.

Comments harvested via this adapter are CANDIDATE evidence — they
must flow through the existing redaction + sensitive-filter +
dedup discipline before any persona ever sees them.

ONLY the official YouTube Data API v3 is used. Unofficial scraping
(yt-dlp, youtube-comment-downloader, raw HTML scrape) is forbidden
and drift-tested.

Compliance memo: ../../../../docs/source_compliance/youtube_data_api.md
"""

from assembly.sources.youtube.adapter import (
    YouTubeAdapterConfig,
    YouTubeCommentResult,
    YouTubeDataClient,
    YouTubeVideoResult,
    is_youtube_key_present,
    looks_like_low_quality_comment,
    redact_comment_for_audit,
)

__all__ = [
    "YouTubeAdapterConfig",
    "YouTubeCommentResult",
    "YouTubeDataClient",
    "YouTubeVideoResult",
    "is_youtube_key_present",
    "looks_like_low_quality_comment",
    "redact_comment_for_audit",
]
