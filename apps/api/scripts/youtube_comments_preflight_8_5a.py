"""Phase 8.5A — YouTube Data API preflight (operator-only).

Default: NO live API call. Just reports key presence and the
intended target-video query set. Pass `--live` to actually run
up to 3 video searches and pull up to 50 comments total, redact
PII patterns for audit, and write the audit JSON.

NEVER prints the API key. NEVER writes the API key into the
audit JSON. NEVER stores channel IDs, emails, phone numbers, or
external URLs from comment text.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from assembly.sources.youtube import (
    YouTubeAdapterConfig, YouTubeDataClient,
    is_youtube_key_present,
)


TRITON_VIDEO_QUERIES = [
    "Red Bull vs Monster review",
    "Celsius energy drink review",
    "Prime energy drink review",
    "best energy drink for gym",
    "energy drink for studying",
    "pre-workout vs energy drink",
]


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 8.5A — YouTube Data API preflight.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help=(
            "Run live YouTube queries. Caps: 3 videos total, 50 "
            "comments total. Default is dry-run."
        ),
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "youtube_preflight_8_5a.json"

    key_present = is_youtube_key_present()

    print("=" * 72)
    print("Phase 8.5A — YouTube Data API PREFLIGHT")
    print("=" * 72)
    print(f"YOUTUBE_DATA_API_KEY present: {key_present}")
    print(f"intended target-video queries (top 6): {TRITON_VIDEO_QUERIES}")
    print(f"caps: 3 videos total, 50 comments total")
    print(f"mode: {'LIVE' if args.live else 'DRY-RUN'}")

    summary: dict = {
        "phase": "8_5a_youtube_preflight",
        "completed_at": datetime.now(UTC).isoformat(),
        "key_present": key_present,
        "mode": "live" if args.live else "dry_run",
        "intended_queries": TRITON_VIDEO_QUERIES,
        "max_videos": 3,
        "max_comments_total": 50,
        "live_videos": [],
        "live_comments": [],
        "compliance_note": (
            "Comments are public but treated carefully. NO channelId, "
            "NO email, NO phone, NO external URL is stored. Phase "
            "8.5A does NOT write source_records. Only the official "
            "YouTube Data API v3 is used; unofficial scraping libs "
            "(yt-dlp, pytube, scrapetube, etc.) are forbidden."
        ),
    }

    if args.live:
        if not key_present:
            print("ERROR: --live requested but YOUTUBE_DATA_API_KEY missing.")
            out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return 2
        client = YouTubeDataClient(YouTubeAdapterConfig(
            max_videos=3, max_comments_total=50, max_comments_per_video=20,
        ))
        videos: list = []
        comments_collected: list = []
        # Use only the top 3 queries (one video per query to avoid
        # quota burn).
        for q in TRITON_VIDEO_QUERIES[:3]:
            if len(videos) >= 3:
                break
            try:
                vlist = client.search_videos(query=q, max_results=1)
            except Exception as e:
                print(f"ERROR searching '{q}': {type(e).__name__}: {e}")
                continue
            if not vlist:
                continue
            v = vlist[0]
            videos.append(v)
            print(f"  video: {v.video_id} | {v.title[:80]}")
            remaining = 50 - len(comments_collected)
            if remaining <= 0:
                break
            try:
                comments = client.fetch_comments(
                    video_id=v.video_id,
                    max_comments=min(20, remaining),
                )
            except Exception as e:
                print(
                    f"ERROR fetching comments for {v.video_id}: "
                    f"{type(e).__name__}: {e}"
                )
                continue
            comments_collected.extend(comments)
            print(f"    → {len(comments)} comments retained after filtering")

        summary["live_videos"] = [
            {
                "video_id": v.video_id,
                "title": v.title,
                "channel_title": v.channel_title,
                "published_at": v.published_at,
            }
            for v in videos
        ]
        summary["live_comments"] = [
            {
                "video_id": c.video_id,
                "comment_id": c.comment_id,
                "text_redacted": c.text[:600],
                "display_name": c.display_name,
                "like_count": c.like_count,
                "published_at": c.published_at,
            }
            for c in comments_collected
        ]

    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
