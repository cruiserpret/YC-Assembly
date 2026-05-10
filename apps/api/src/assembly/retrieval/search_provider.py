"""SearchProvider abstraction + MockSearchProvider for tests."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class SearchResult:
    """One search hit. Stable across providers."""

    url: str
    title: str
    snippet: str
    rank: int
    raw: dict | None = None


class SearchProvider(ABC):
    """Async contract every search provider implements."""

    name: ClassVar[str] = "abstract"

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
    ) -> list[SearchResult]:
        """Return up to `max_results` SearchResult rows for `query`."""


# ---------------------------------------------------------------------------
# Mock provider for tests
# ---------------------------------------------------------------------------


@dataclass
class _MockRule:
    predicate: Callable[[str], bool]
    results: list[SearchResult]


class MockSearchProvider(SearchProvider):
    """Test double for SearchProvider. Pre-load with `(predicate, results)`
    tuples; the first matching predicate wins. Records every call into
    `self.calls` for assertion."""

    name: ClassVar[str] = "mock"

    def __init__(self) -> None:
        self._rules: list[_MockRule] = []
        self._default: list[SearchResult] | None = None
        self.calls: list[tuple[str, int]] = []

    def add_results_for_query(
        self,
        query_substring: str,
        results: list[SearchResult],
    ) -> None:
        self._rules.append(
            _MockRule(
                predicate=lambda q, sub=query_substring: sub.lower() in q.lower(),
                results=list(results),
            )
        )

    def add_default(self, results: list[SearchResult]) -> None:
        self._default = list(results)

    async def search(
        self, query: str, *, max_results: int = 10
    ) -> list[SearchResult]:
        self.calls.append((query, max_results))
        for rule in self._rules:
            try:
                if rule.predicate(query):
                    return rule.results[:max_results]
            except Exception:
                continue
        if self._default is not None:
            return self._default[:max_results]
        return []
