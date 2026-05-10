# Source compliance — YouTube Data API v3

**Production status:** review (Phase 8.5A scaffolding only; no live ingestion runs yet).

## Source purpose
YouTube Data API v3 is the official Google API for YouTube. Assembly uses it to surface **public top-level comments** on review/comparison videos in the energy-drink / pre-workout / sports-drink categories. Comments are CANDIDATE evidence: they capture real consumer objections about taste, caffeine load, sugar content, price, trust, and influencer-figurehead aversion — directly useful for unlaunched-product market-entry simulation.

## Allowed use
- `search.list` (type=video) with bounded `maxResults` to find candidate review/comparison videos for operator-spec'd queries.
- `commentThreads.list` (part=snippet, order=relevance) to pull top-level comments per video, capped per Phase 8.5A preflight at 50 total comments per invocation.
- Storing public comment text + minimal commenter handle (`authorDisplayName`) for downstream redaction + sensitive-filter + dedup processing.

## Disallowed use
- NEVER use unofficial scraping (yt-dlp, youtube-dl, pytube, scrapetube, youtube-comment-downloader, raw HTML scrape, `requests`/`aiohttp`-based crawls of youtube.com). Official API only. Drift test asserts none of these libs are imported anywhere in `src/assembly/sources/youtube/`.
- NEVER make sensitive inferences about a commenter (sexual orientation, political affiliation, religion, mental health, etc.). The framework's existing sensitive-filter applies downstream.
- NEVER fan out across the YouTube graph (subscriber lists, playlist memberships, etc.) — only `search.list` and `commentThreads.list` are allowed surfaces.
- NEVER store videos themselves, video transcripts beyond comment text, or thumbnails.

## Data retained
- Video metadata: `videoId`, `title`, `channelTitle`, `publishedAt`
- Comment data: `comment_id` (for dedup), `text` (after PII redaction — see below), `authorDisplayName`, `likeCount`, `publishedAt`

## Data NOT retained
- The YouTube API key (read from environment only)
- `channelId` of the commenter
- Email addresses, phone numbers, or external URLs that appear inside the comment text — `redact_comment_for_audit` replaces these with `[REDACTED-EMAIL]` / `[REDACTED-PHONE]` / `[REDACTED-URL]` before any persistence.
- Comment thread replies (Phase 8.5A scope is top-level only)
- Any user profile picture, channel banner, or watch-history signal the API can surface

## Privacy rules
- Comments are public, but the framework treats personally-identifying content carefully: emails / phones inside a comment are a flag that the commenter is sharing private contact info, not that Assembly should propagate it.
- Low-quality comments (≤ 3 chars, pure emoji, all-caps spam, "first!" patterns) are dropped in `looks_like_low_quality_comment` before they can become CANDIDATE evidence.
- A commenter's display name is retained for audit traceability ONLY. It must never be combined with other YouTube fields to enable cross-video re-identification.

## API key handling
- `YOUTUBE_DATA_API_KEY` is read ONLY from the process environment via `os.environ.get(...)`.
- The key is NEVER passed via CLI flag, NEVER written to disk, NEVER echoed to logs, NEVER embedded in audit JSON.
- Drift test asserts the literal string `YOUTUBE_DATA_API_KEY` does not appear outside the adapter file (and config).

## Rate / cost / quota caps
- YouTube Data API v3 has a daily quota (typically 10,000 units/day, project-wide). `search.list` costs 100 units; `commentThreads.list` costs 1 unit.
- Phase 8.5A preflight cap: **3 videos × ~17 comments avg = ~50 comments total**. That's ~303 quota units worst-case (3 searches × 100 + 3 comment-thread calls × 1) — well under any single-day envelope.
- The `YouTubeAdapterConfig` dataclass is the single per-invocation cap surface.

## Persona / source-record creation
- Phase 8.5A creates **zero** `source_records`, **zero** `persona_records`, **zero** `persona_traits`, **zero** `persona_evidence_links`.
- Future ingestion (Phase 8.5B+) requires a separate operator approval gate.

## Operator notes
- Live preflight is gated behind the `--live` flag of `scripts/youtube_comments_preflight_8_5a.py`. Default invocation is dry-run.
- Per-video comment quota = 20 (Phase 8.5A bound) to keep total well under 50.
