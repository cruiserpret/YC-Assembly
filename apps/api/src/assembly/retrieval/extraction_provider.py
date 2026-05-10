"""ExtractionProvider abstraction + httpx fallback + Mock for tests."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

from assembly.llm.errors import CutoffViolationError


@dataclass(frozen=True)
class ExtractedPage:
    """One extracted page. Same shape regardless of provider."""

    url: str
    final_url: str
    title: str | None
    text: str
    captured_at: datetime
    truncated: bool
    source_kind: str  # "live" | "snapshot" | "extracted"
    snapshot_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ExtractionProvider(ABC):
    """Async contract every page-extraction provider implements."""

    name: ClassVar[str] = "abstract"

    @abstractmethod
    async def extract(
        self,
        url: str,
        *,
        cutoff_date: date | None = None,
        snapshot: str | Path | None = None,
    ) -> ExtractedPage:
        """Extract plain text for `url`. Honors the same cutoff-date rule as
        url_fetcher: if `cutoff_date` is set, refuse live extraction and
        require a snapshot."""


# ---------------------------------------------------------------------------
# httpx fallback — works without any external API key
# ---------------------------------------------------------------------------


class HttpxExtractionProvider(ExtractionProvider):
    """Extraction via the existing `pipeline.url_fetcher.fetch_url` (httpx +
    BeautifulSoup). Free, no key required, less robust than Firecrawl/Jina
    on JS-heavy pages but fine for most static content."""

    name: ClassVar[str] = "httpx"

    async def extract(
        self,
        url: str,
        *,
        cutoff_date: date | None = None,
        snapshot: str | Path | None = None,
    ) -> ExtractedPage:
        from assembly.pipeline.url_fetcher import fetch_url  # local import — avoid cycle

        page = await fetch_url(url, cutoff_date=cutoff_date, snapshot=snapshot)
        return ExtractedPage(
            url=page.url,
            final_url=page.final_url,
            title=None,
            text=page.text,
            captured_at=page.captured_at,
            truncated=page.truncated,
            source_kind=page.source_kind,
            snapshot_path=page.snapshot_path,
            metadata={
                "status_code": page.status_code,
                "content_type": page.content_type,
            },
        )


# ---------------------------------------------------------------------------
# Mock for tests
# ---------------------------------------------------------------------------


@dataclass
class _MockRule:
    predicate: Callable[[str], bool]
    page: ExtractedPage


class MockExtractionProvider(ExtractionProvider):
    """Test double. Pre-load with `(predicate, page)` tuples; first match wins.
    Records calls for assertion."""

    name: ClassVar[str] = "mock"

    def __init__(self) -> None:
        self._rules: list[_MockRule] = []
        self._default: ExtractedPage | None = None
        self.calls: list[str] = []

    def add_page(self, url_substring: str, page: ExtractedPage) -> None:
        self._rules.append(
            _MockRule(
                predicate=lambda u, sub=url_substring: sub.lower() in u.lower(),
                page=page,
            )
        )

    def add_default(self, page: ExtractedPage) -> None:
        self._default = page

    async def extract(
        self,
        url: str,
        *,
        cutoff_date: date | None = None,
        snapshot: str | Path | None = None,
    ) -> ExtractedPage:
        self.calls.append(url)

        # Honor the same cutoff rule the real providers do.
        if cutoff_date is not None and snapshot is None:
            raise CutoffViolationError(
                f"refusing to live-extract {url!r}: simulation has "
                f"evidence_cutoff_date={cutoff_date} but no snapshot was provided."
            )

        for rule in self._rules:
            try:
                if rule.predicate(url):
                    return rule.page
            except Exception:
                continue
        if self._default is not None:
            return self._default
        raise ValueError(
            f"MockExtractionProvider has no matching page for url={url!r}. "
            f"Registered rules: {len(self._rules)} + default={self._default is not None}"
        )


def make_extracted_page(
    *,
    url: str,
    text: str,
    title: str | None = None,
    truncated: bool = False,
    source_kind: str = "extracted",
    metadata: dict[str, Any] | None = None,
) -> ExtractedPage:
    """Constructor sugar for tests."""
    return ExtractedPage(
        url=url,
        final_url=url,
        title=title,
        text=text,
        captured_at=datetime.now(UTC),
        truncated=truncated,
        source_kind=source_kind,
        snapshot_path=None,
        metadata=metadata or {},
    )
