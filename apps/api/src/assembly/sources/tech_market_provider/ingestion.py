"""Phase 11D.2 — tech-market CSV ingestion engine.

Owns:

  * `TechMarketSignalPersister` Protocol + three implementations:
      - `NullTechMarketPersister`  (dry-run; records but never writes)
      - `InMemoryTechMarketPersister`  (test-only; in-memory list +
        dedup index)
      - `PostgresTechMarketPersister` (production; lazy SQLAlchemy
        insert with batched per-row dedup against the live table).
  * `dedupe_identity_for(signal)` — pure function that computes the
    de-duplication key for a `DistilledTechSignal`.
  * `TechMarketIngestionStats` — tally + audit JSON builder.
  * `ingest_csv_rows()` — pure ingestion loop that the CLI calls.
    Takes an iterator of CSV `dict`s, distills each, dedupes within
    the batch + against the persister's "already seen" set, and
    optionally writes via the persister.

NO HTTP. NO SCRAPING. NO RAW BODY PERSISTED — the persister only
sees `DistilledTechSignal` instances, which already cap the snippet
at 240 chars and strip PII metadata keys.
"""
from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol, TYPE_CHECKING

from assembly.sources.tech_market_provider.distiller import (
    DistilledTechSignal,
    RuleBasedTechMarketDistiller,
    TechMarketSignalDistiller,
)
from assembly.sources.tech_market_provider.signal_types import (
    BUYER_TYPES,
    BuyerType,
    MARKET_CONTEXTS,
    MarketContext,
    PRODUCT_CATEGORIES,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


# ---------------------------------------------------------------------------
# Dedupe identity
# ---------------------------------------------------------------------------


# Hash first N chars of the (normalized) snippet so two near-identical
# snippets collapse. 96 mirrors the Phase-11C Amazon retriever's
# fuzzy-collision prefix.
_SNIPPET_HASH_PREFIX = 96


def _snippet_hash(snippet: str) -> str:
    """SHA-256 of the first 96 chars of the lowercase, whitespace-
    collapsed snippet. Stable across re-runs; collision-resistant for
    our scale."""
    normalized = " ".join((snippet or "").lower().split())[
        :_SNIPPET_HASH_PREFIX
    ]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TechSignalIdentity:
    """Persister-side dedupe key. Primary identity is
    `(source_provider, source_timestamp, signal_type, snippet_hash)`.
    When `source_timestamp` is unavailable, falls back to
    `(source_provider, product_category, signal_type, snippet_hash)`.

    Both shapes are namespaced under a leading discriminator so a
    timestamped-key never collides with a fallback-keyed signal from
    the same provider."""

    discriminator: str
    parts: tuple[str | int, ...]

    @classmethod
    def for_signal(cls, signal: DistilledTechSignal) -> "TechSignalIdentity":
        snippet_h = _snippet_hash(signal.short_snippet)
        if signal.source_timestamp is not None:
            return cls(
                discriminator="ts",
                parts=(
                    signal.source_provider,
                    int(signal.source_timestamp),
                    signal.signal_type,
                    snippet_h,
                ),
            )
        return cls(
            discriminator="cat",
            parts=(
                signal.source_provider,
                signal.product_category,
                signal.signal_type,
                snippet_h,
            ),
        )


def dedupe_identity_for(
    signal: DistilledTechSignal,
) -> TechSignalIdentity:
    """Public helper — see `TechSignalIdentity.for_signal`."""
    return TechSignalIdentity.for_signal(signal)


# ---------------------------------------------------------------------------
# Persister protocol + implementations
# ---------------------------------------------------------------------------


class TechMarketSignalPersister(Protocol):
    """Persister abstraction. Tests use the in-memory impl; the CLI
    in commit mode uses the Postgres impl.

    Implementations MUST:

      * Read pre-existing identities into `existing_identities()` so
        the ingestion loop can pre-filter without re-inserting
        duplicates.
      * Persist a batch via `insert_signals()` and return the count
        actually written.
      * Never see or store the raw source text. They only get
        `DistilledTechSignal` instances.
    """

    async def existing_identities(
        self, source_provider: str,
    ) -> set[TechSignalIdentity]:  # pragma: no cover - protocol
        ...

    async def insert_signals(
        self, signals: list[DistilledTechSignal],
    ) -> int:  # pragma: no cover - protocol
        ...


class NullTechMarketPersister:
    """`--dry-run` persister: records what would have been written
    but never touches a database."""

    def __init__(self) -> None:
        self.would_have_inserted: list[DistilledTechSignal] = []

    async def existing_identities(
        self, source_provider: str,
    ) -> set[TechSignalIdentity]:
        return set()

    async def insert_signals(
        self, signals: list[DistilledTechSignal],
    ) -> int:
        self.would_have_inserted.extend(signals)
        return 0


class InMemoryTechMarketPersister:
    """Test-only persister. Behaves correctly under dedup re-run:
    the second `ingest_csv_rows()` invocation against the same
    persister + the same input data inserts zero new signals."""

    def __init__(self) -> None:
        self.inserted: list[DistilledTechSignal] = []
        self._identities: set[TechSignalIdentity] = set()
        # Optional pre-population so tests can simulate "this row is
        # already in the DB from a prior run".
        self.preloaded_identities: set[TechSignalIdentity] = set()

    def preload_identities(
        self, identities: Iterable[TechSignalIdentity],
    ) -> None:
        self.preloaded_identities.update(identities)

    async def existing_identities(
        self, source_provider: str,
    ) -> set[TechSignalIdentity]:
        return {
            i for i in (self._identities | self.preloaded_identities)
            if (i.parts and i.parts[0] == source_provider)
        }

    async def insert_signals(
        self, signals: list[DistilledTechSignal],
    ) -> int:
        count = 0
        for s in signals:
            ident = TechSignalIdentity.for_signal(s)
            if ident in self._identities:
                continue
            self._identities.add(ident)
            self.inserted.append(s)
            count += 1
        return count


class PostgresTechMarketPersister:
    """Production persister — INSERT-only. Lazy SQLAlchemy imports.

    Reads the per-provider identity set once at the start of the
    batch; the ingestion loop pre-filters against that set so we
    don't issue a SELECT per row.

    `insert_signals` is a single `bulk_insert_mappings`-style INSERT.
    On conflict we silently skip — there is no `ON CONFLICT` clause
    because the dedupe key is computed in Python (the table has no
    unique index on the dedupe tuple yet; Phase 11D.3 may add one).
    """

    def __init__(
        self,
        sessionmaker: "async_sessionmaker[AsyncSession]",
    ) -> None:
        self._sm = sessionmaker

    async def existing_identities(
        self, source_provider: str,
    ) -> set[TechSignalIdentity]:
        from sqlalchemy import select
        from assembly.models.tech_market_signal import TechMarketSignal
        async with self._sm() as session:
            stmt = select(
                TechMarketSignal.source_provider,
                TechMarketSignal.source_timestamp,
                TechMarketSignal.product_category,
                TechMarketSignal.signal_type,
                TechMarketSignal.short_snippet,
            ).where(
                TechMarketSignal.source_provider == source_provider,
            )
            res = await session.execute(stmt)
            out: set[TechSignalIdentity] = set()
            for sp, ts, pc, st, snip in res.all():
                snippet_h = _snippet_hash(snip or "")
                if ts is not None:
                    out.add(TechSignalIdentity(
                        discriminator="ts",
                        parts=(sp, int(ts), st, snippet_h),
                    ))
                else:
                    out.add(TechSignalIdentity(
                        discriminator="cat",
                        parts=(sp, pc, st, snippet_h),
                    ))
            return out

    async def insert_signals(
        self, signals: list[DistilledTechSignal],
    ) -> int:
        from assembly.models.tech_market_signal import TechMarketSignal
        if not signals:
            return 0
        rows = [
            {
                "source_provider": s.source_provider,
                "source_category": s.source_category,
                "product_category": s.product_category,
                "company_or_product": s.company_or_product,
                "competitor_name": s.competitor_name,
                "signal_type": s.signal_type,
                "sentiment_bucket": s.sentiment_bucket,
                "buyer_type": s.buyer_type,
                "market_context": s.market_context,
                "theme": s.theme,
                "short_snippet": s.short_snippet,
                "evidence_url": s.evidence_url,
                "source_timestamp": s.source_timestamp,
                "relevance_score": s.relevance_score,
                "metadata_json": s.metadata or None,
            }
            for s in signals
        ]
        async with self._sm() as session:
            await session.run_sync(
                lambda sync_sess: sync_sess.bulk_insert_mappings(
                    TechMarketSignal, rows,
                ),
            )
            await session.commit()
        return len(rows)


# ---------------------------------------------------------------------------
# CSV row → distilled signal
# ---------------------------------------------------------------------------


CSV_REQUIRED_COLUMNS: tuple[str, ...] = ("text",)
CSV_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "company_or_product",
    "competitor_name",
    "buyer_type",
    "market_context",
    "source_timestamp",
    "evidence_url",
    "metadata_json",
)


# Reject text bodies shorter than this — they don't carry enough
# information to be a useful buyer-language signal.
_MIN_TEXT_LEN = 30


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _parse_optional_market_context(
    raw: str | None,
) -> MarketContext | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in MARKET_CONTEXTS:
        return s  # type: ignore[return-value]
    # Case-insensitive lookup for friendly input.
    for v in MARKET_CONTEXTS:
        if v.lower() == s.lower():
            return v
    return None


def _parse_optional_metadata(raw: str | None) -> dict | None:
    """metadata_json column accepts a JSON object string. Anything
    else returns {} so the distiller still records SOMETHING but
    doesn't crash the run."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    import json
    try:
        parsed = json.loads(s)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


@dataclass
class CSVRowParseResult:
    """Outcome of a single CSV row → distilled signal pipeline."""

    accepted: bool
    rejection_reason: str | None
    signal: DistilledTechSignal | None


def _override_buyer_type(
    signal: DistilledTechSignal,
    buyer_type_raw: str | None,
) -> DistilledTechSignal:
    """Per-row CSV column may override the distiller's inferred
    buyer_type. Unknown / blank values fall back to whatever the
    distiller chose."""
    if not buyer_type_raw:
        return signal
    s = buyer_type_raw.strip().lower()
    if not s:
        return signal
    for v in BUYER_TYPES:
        if v.lower() == s:
            return DistilledTechSignal(
                source_provider=signal.source_provider,
                source_category=signal.source_category,
                product_category=signal.product_category,
                company_or_product=signal.company_or_product,
                competitor_name=signal.competitor_name,
                signal_type=signal.signal_type,
                sentiment_bucket=signal.sentiment_bucket,
                buyer_type=v,  # type: ignore[arg-type]
                market_context=signal.market_context,
                theme=signal.theme,
                short_snippet=signal.short_snippet,
                evidence_url=signal.evidence_url,
                source_timestamp=signal.source_timestamp,
                relevance_score=signal.relevance_score,
                metadata=dict(signal.metadata),
            )
    return signal


def distill_csv_row(
    row: dict[str, str],
    *,
    distiller: TechMarketSignalDistiller,
    source_provider: str,
    source_category: str | None,
    product_category: str,
    market_context_hint_default: MarketContext | None,
) -> CSVRowParseResult:
    """Pure function: take one CSV row dict + CLI defaults, return
    `CSVRowParseResult`. No I/O.
    """
    text = (row.get("text") or "").strip()
    if not text:
        return CSVRowParseResult(False, "blank_text", None)
    if len(text) < _MIN_TEXT_LEN:
        return CSVRowParseResult(False, "text_too_short", None)

    company = (row.get("company_or_product") or "").strip() or None
    competitor = (row.get("competitor_name") or "").strip() or None
    buyer_raw = (row.get("buyer_type") or "").strip() or None
    market_ctx_raw = _parse_optional_market_context(
        row.get("market_context"),
    )
    market_ctx_hint = market_ctx_raw or market_context_hint_default
    ts = _parse_optional_int(row.get("source_timestamp"))
    url = (row.get("evidence_url") or "").strip() or None
    metadata = _parse_optional_metadata(row.get("metadata_json")) or {}

    distilled = distiller.distill(
        text,
        source_provider=source_provider,
        source_category=source_category,
        product_category=product_category,
        company_or_product=company,
        competitor_name=competitor,
        market_context_hint=market_ctx_hint,
        evidence_url=url,
        source_timestamp=ts,
        metadata=metadata,
    )
    if not distilled:
        return CSVRowParseResult(False, "no_signal_classified", None)
    # The rule-based distiller emits ≤ 1 signal per text.
    signal = _override_buyer_type(distilled[0], buyer_raw)
    return CSVRowParseResult(True, None, signal)


# ---------------------------------------------------------------------------
# Stats + audit
# ---------------------------------------------------------------------------


@dataclass
class TechMarketIngestionStats:
    """Tally for one CSV ingestion run. Drives the audit JSON."""

    source_provider: str
    product_category: str
    csv_path: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    rows_scanned: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0

    rejection_reasons: Counter[str] = field(default_factory=Counter)

    signals_generated: int = 0
    signals_inserted: int = 0
    duplicates_skipped: int = 0

    signal_type_distribution: Counter[str] = field(default_factory=Counter)
    buyer_type_distribution: Counter[str] = field(default_factory=Counter)
    market_context_distribution: Counter[str] = field(
        default_factory=Counter,
    )

    sample_accepted_signals: list[DistilledTechSignal] = field(
        default_factory=list,
    )
    sample_rejected_rows: list[dict[str, str | int | None]] = field(
        default_factory=list,
    )

    dry_run: bool = True

    def record_rejection(
        self, row: dict[str, str], reason: str,
    ) -> None:
        self.rows_rejected += 1
        self.rejection_reasons[reason] += 1
        if len(self.sample_rejected_rows) < 5:
            self.sample_rejected_rows.append(
                {
                    "reason": reason,
                    "first_chars": (row.get("text") or "")[:80],
                    "company_or_product": row.get("company_or_product"),
                },
            )

    def record_accepted(self, signal: DistilledTechSignal) -> None:
        self.rows_accepted += 1
        self.signals_generated += 1
        self.signal_type_distribution[signal.signal_type] += 1
        self.buyer_type_distribution[signal.buyer_type] += 1
        self.market_context_distribution[signal.market_context] += 1
        if len(self.sample_accepted_signals) < 8:
            self.sample_accepted_signals.append(signal)

    def record_inserted(self, n: int) -> None:
        self.signals_inserted += n

    def record_duplicate(self, n: int = 1) -> None:
        self.duplicates_skipped += n

    @property
    def runtime_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return round(end - self.started_at, 3)


def build_audit_payload(
    stats: TechMarketIngestionStats,
) -> dict[str, object]:
    return {
        "phase": "11D.2",
        "source_provider": stats.source_provider,
        "product_category": stats.product_category,
        "csv_path": stats.csv_path,
        "dry_run": stats.dry_run,
        "started_at": stats.started_at,
        "finished_at": stats.finished_at,
        "runtime_seconds": stats.runtime_seconds,
        "counts": {
            "rows_scanned": stats.rows_scanned,
            "rows_accepted": stats.rows_accepted,
            "rows_rejected": stats.rows_rejected,
            "signals_generated": stats.signals_generated,
            "signals_inserted": stats.signals_inserted,
            "duplicates_skipped": stats.duplicates_skipped,
        },
        "rejection_reasons": dict(stats.rejection_reasons),
        "signal_type_distribution": dict(stats.signal_type_distribution),
        "buyer_type_distribution": dict(stats.buyer_type_distribution),
        "market_context_distribution": dict(
            stats.market_context_distribution,
        ),
        "sample_accepted_signals": [
            {
                "signal_type": s.signal_type,
                "sentiment_bucket": s.sentiment_bucket,
                "buyer_type": s.buyer_type,
                "market_context": s.market_context,
                "product_category": s.product_category,
                "company_or_product": s.company_or_product,
                "competitor_name": s.competitor_name,
                "theme": s.theme,
                "short_snippet": s.short_snippet,
                "evidence_url": s.evidence_url,
                "source_timestamp": s.source_timestamp,
            }
            for s in stats.sample_accepted_signals
        ],
        "sample_rejected_rows": stats.sample_rejected_rows,
        "safety_notes": [
            "raw text never persisted — only short_snippet ≤ 240 chars",
            "metadata PII keys stripped by distiller",
            "no live HTTP, no scraping verbs invoked",
            "ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED gates production "
            "writes elsewhere; ingestion CLI is a dev/local tool",
        ],
    }


# ---------------------------------------------------------------------------
# Ingestion loop (pure — async only for persister I/O)
# ---------------------------------------------------------------------------


async def ingest_csv_rows(
    rows: Iterable[dict[str, str]],
    *,
    persister: TechMarketSignalPersister,
    distiller: TechMarketSignalDistiller | None = None,
    source_provider: str,
    source_category: str | None,
    product_category: str,
    market_context_hint: MarketContext | None = None,
    dry_run: bool = True,
    limit: int | None = None,
    csv_path: str | None = None,
) -> TechMarketIngestionStats:
    """Drive the per-row pipeline and return populated stats.

    The function is responsible for:

      * Calling `distill_csv_row` per row.
      * Tracking rows-scanned / accepted / rejected + rejection
        reasons.
      * Computing the dedupe identity for each accepted signal and
        skipping duplicates (both within the batch AND against the
        persister's prior-state set).
      * Calling `persister.insert_signals` in commit mode.
      * Producing the full `TechMarketIngestionStats` for audit JSON.

    Pure orchestration — no file I/O (the caller opens the CSV), no
    network I/O.
    """
    if distiller is None:
        distiller = RuleBasedTechMarketDistiller()
    if product_category not in PRODUCT_CATEGORIES:
        # Allow free-form labels but warn the operator via the audit;
        # we don't reject — Phase 11D.1 left product_category as a
        # controlled vocabulary "soft" constraint.
        pass

    stats = TechMarketIngestionStats(
        source_provider=source_provider,
        product_category=product_category,
        csv_path=csv_path,
        dry_run=dry_run,
    )

    existing: set[TechSignalIdentity] = await persister.existing_identities(
        source_provider=source_provider,
    )

    accepted_signals: list[DistilledTechSignal] = []
    seen_in_batch: set[TechSignalIdentity] = set()

    for row in rows:
        if limit is not None and stats.rows_scanned >= limit:
            break
        stats.rows_scanned += 1
        result = distill_csv_row(
            row,
            distiller=distiller,
            source_provider=source_provider,
            source_category=source_category,
            product_category=product_category,
            market_context_hint_default=market_context_hint,
        )
        if not result.accepted or result.signal is None:
            stats.record_rejection(
                row, result.rejection_reason or "unknown",
            )
            continue
        signal = result.signal
        ident = TechSignalIdentity.for_signal(signal)
        if ident in existing or ident in seen_in_batch:
            stats.record_duplicate()
            continue
        seen_in_batch.add(ident)
        stats.record_accepted(signal)
        accepted_signals.append(signal)

    # Hard dry-run contract: when dry_run=True, persister.insert_signals
    # is NEVER called — regardless of persister type. This guarantees
    # "dry-run writes zero rows" even if the caller passes a real
    # DB-backed persister by mistake.
    if not dry_run and accepted_signals:
        inserted = await persister.insert_signals(accepted_signals)
        stats.record_inserted(inserted)

    stats.finished_at = time.time()
    return stats


__all__ = [
    "CSV_OPTIONAL_COLUMNS",
    "CSV_REQUIRED_COLUMNS",
    "CSVRowParseResult",
    "InMemoryTechMarketPersister",
    "NullTechMarketPersister",
    "PostgresTechMarketPersister",
    "TechMarketIngestionStats",
    "TechMarketSignalPersister",
    "TechSignalIdentity",
    "build_audit_payload",
    "dedupe_identity_for",
    "distill_csv_row",
    "ingest_csv_rows",
]
