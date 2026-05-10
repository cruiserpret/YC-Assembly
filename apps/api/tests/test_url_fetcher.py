"""Tests for url_fetcher: cutoff-date guard (C3) and snapshot read."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from assembly.llm.errors import CutoffViolationError
from assembly.pipeline.url_fetcher import fetch_url


@pytest.mark.asyncio
async def test_cutoff_date_blocks_live_fetch_without_snapshot() -> None:
    """C3: with cutoff_date set and no snapshot, must refuse."""
    with pytest.raises(CutoffViolationError):
        await fetch_url(
            "https://example.com/pricing",
            cutoff_date=date(2026, 2, 1),
            snapshot=None,
        )


@pytest.mark.asyncio
async def test_cutoff_date_with_snapshot_reads_snapshot(tmp_path: Path) -> None:
    """C3: with cutoff_date AND snapshot, the snapshot is read; no live fetch."""
    snap = tmp_path / "competitor.html"
    snap.write_text(
        "<html><body><h1>Pricing</h1><p>Plus plan: Custom</p></body></html>",
        encoding="utf-8",
    )
    page = await fetch_url(
        "https://example.com/pricing",
        cutoff_date=date(2026, 2, 1),
        snapshot=snap,
    )
    assert page.source_kind == "snapshot"
    assert page.snapshot_path == str(snap)
    assert "Pricing" in page.text
    assert "Plus plan" in page.text


@pytest.mark.asyncio
async def test_snapshot_extracts_text_from_html(tmp_path: Path) -> None:
    snap = tmp_path / "page.html"
    snap.write_text(
        "<html><head><title>T</title>"
        "<script>var x = 1;</script></head>"
        "<body><h1>Hello world</h1>"
        "<style>.a{}</style>"
        "<p>Some content here.</p></body></html>",
        encoding="utf-8",
    )
    page = await fetch_url("https://x.test/", snapshot=snap)
    assert "Hello world" in page.text
    assert "Some content here" in page.text
    # script/style stripped
    assert "var x = 1" not in page.text
    assert ".a{" not in page.text


@pytest.mark.asyncio
async def test_unsupported_scheme_rejected() -> None:
    with pytest.raises(ValueError):
        await fetch_url("ftp://example.com/pricing")


@pytest.mark.asyncio
async def test_missing_snapshot_file_raises() -> None:
    """If a snapshot path is given but the file is missing, FetchError."""
    from assembly.pipeline.url_fetcher import FetchError

    with pytest.raises(FetchError):
        await fetch_url(
            "https://example.com/x",
            cutoff_date=date(2026, 2, 1),
            snapshot="/nonexistent/path/to/snapshot.html",
        )
