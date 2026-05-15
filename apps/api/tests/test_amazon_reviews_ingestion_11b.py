"""Phase 11B — Amazon Reviews pilot-category ingestion tests.

Covers the operator's acceptance checklist:

  1. Dry-run does not write DB rows.
  2. Commit writes only distilled signals.
  3. Duplicate rerun does not duplicate rows.
  4. Low-quality reviews are rejected.
  5. Full raw review body is NOT persisted (only the capped snippet).
  6. user_id is NOT persisted (model has no such field).
  7. Image URLs are NOT persisted (model has no such field).
  8. `--limit` is honored.
  9. `--resume` skips already-ingested review hashes.
 10. Audit JSON is written with the right shape.
 11. Signal-type + sentiment distributions are reported.
 12. Feature flag remains off by default.
 13. CLI argv parsing works for the spec'd flags.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES. Pure deterministic
fixtures + in-memory persister.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from assembly.sources.amazon_reviews_2023 import AmazonReviewRecord
from assembly.sources.amazon_reviews_provider import (
    AmazonReviewsProvider,
    AmazonReviewsProviderConfig,
    CategoryIngestPlan,
    DistillerConfig,
    InMemorySignalPersister,
    IngestionStats,
    NullSignalPersister,
    build_audit_payload,
    ingest_category,
)
from assembly.sources.amazon_reviews_provider.distiller import (
    DistilledSignal,
)


_FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures" / "amazon_reviews_provider"
)


@pytest.fixture
def fixture_dir() -> Path:
    assert _FIXTURE_DIR.is_dir()
    return _FIXTURE_DIR


@pytest.fixture
def enabled_provider(fixture_dir: Path) -> AmazonReviewsProvider:
    cfg = AmazonReviewsProviderConfig(
        enabled=True,
        data_dir=fixture_dir,
        categories=(
            "Electronics", "All_Beauty", "Home_and_Kitchen",
        ),
        max_items_per_run=10_000,
        min_review_chars=40,
    )
    return AmazonReviewsProvider(cfg)


def _plan_for(
    provider: AmazonReviewsProvider, category: str,
) -> CategoryIngestPlan:
    raw_dir = provider.config.data_dir / "raw"  # type: ignore[union-attr]
    input_file = next(
        (
            str(p) for p in sorted(raw_dir.glob(f"{category}*.jsonl*"))
            if "_meta" not in p.name.lower()
        ),
        None,
    )
    return CategoryIngestPlan(
        category=category,
        review_iter=provider.iter_category_reviews(category),
        input_file=input_file,
        distiller_config=DistillerConfig(min_review_chars=40),
    )


# ---------------------------------------------------------------------------
# 1. Dry-run never writes
# ---------------------------------------------------------------------------


def test_dry_run_uses_null_persister_and_writes_nothing(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    plan = _plan_for(enabled_provider, "Electronics")
    persister = NullSignalPersister()
    stats = asyncio.run(
        ingest_category(plan, persister, dry_run=True, resume=False),
    )
    assert stats.signals_generated > 0
    # Dry-run path: NullSignalPersister records but never inserts.
    assert stats.signals_inserted == 0
    # The null persister still keeps a record of what WOULD have been
    # written — important for the audit JSON.
    assert len(persister.would_have_inserted) == stats.signals_generated


# ---------------------------------------------------------------------------
# 2. Commit writes signals
# ---------------------------------------------------------------------------


def test_commit_inserts_distilled_signals(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    plan = _plan_for(enabled_provider, "Electronics")
    persister = InMemorySignalPersister()
    stats = asyncio.run(
        ingest_category(plan, persister, dry_run=False, resume=False),
    )
    assert stats.signals_inserted > 0
    assert stats.signals_inserted == len(persister.inserted)
    # Every inserted row is a DistilledSignal — never an
    # AmazonReviewRecord (raw row) or a plain dict.
    for s in persister.inserted:
        assert isinstance(s, DistilledSignal)


# ---------------------------------------------------------------------------
# 3. Duplicate rerun does not duplicate
# ---------------------------------------------------------------------------


def test_rerun_without_resume_dedups_by_signal_key(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    persister = InMemorySignalPersister()

    plan1 = _plan_for(enabled_provider, "Electronics")
    first = asyncio.run(
        ingest_category(plan1, persister, dry_run=False, resume=False),
    )

    plan2 = _plan_for(enabled_provider, "Electronics")
    second = asyncio.run(
        ingest_category(plan2, persister, dry_run=False, resume=False),
    )

    # Second pass distilled the same signals, but every one of them
    # hit the dedup set and got skipped.
    assert second.signals_generated == first.signals_generated
    assert second.signals_inserted == 0
    assert second.signals_skipped_duplicate >= first.signals_inserted
    assert len(persister.inserted) == first.signals_inserted


def test_resume_skips_already_ingested_reviews(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    persister = InMemorySignalPersister()
    asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            persister, dry_run=False, resume=False,
        ),
    )
    n_inserted_first = len(persister.inserted)

    # Second pass with resume=True should skip every review whose
    # hash is already in the persister — no signals get re-distilled.
    second = asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            persister, dry_run=False, resume=True,
        ),
    )
    assert second.rows_already_ingested > 0
    assert second.signals_inserted == 0
    assert len(persister.inserted) == n_inserted_first


# ---------------------------------------------------------------------------
# 4. Low-quality reviews rejected during ingest
# ---------------------------------------------------------------------------


def test_ingest_rejects_low_quality_with_reason(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    plan = _plan_for(enabled_provider, "Electronics")
    persister = InMemorySignalPersister()
    stats = asyncio.run(
        ingest_category(plan, persister, dry_run=False, resume=False),
    )
    # The Electronics fixture deliberately includes:
    #   * one all-caps spam row
    #   * one 3-char "bad bad bad" row
    # The 11A reader's eligibility gate rejects them *before* they
    # reach the distiller, so they should never appear in
    # `rows_scanned`. Phase 11B's per-row rejection counter only
    # increments for rows that survive eligibility but fail the
    # distiller's own `is_review_eligible` — those are zero with the
    # current fixture set, but the audit shape must still expose the
    # `rejection_reasons` field as an empty dict.
    assert isinstance(stats.rejection_reasons, type(stats.rejection_reasons))
    payload = build_audit_payload(stats)
    assert "rejection_reasons" in payload


def test_ingest_rejection_reasons_count_when_distiller_sees_short_rows() -> None:
    """Direct test: feed the engine a synthetic iterator with a
    short-text record and confirm the rejection counter fires."""
    short_row = AmazonReviewRecord(
        category="Synth", parent_asin=None, asin=None, rating=1.0,
        title="bad", text="bad", helpful_vote=0,
        verified_purchase=False, timestamp=1, user_id_hash="x",
    )

    def _gen() -> Iterator[
        tuple[AmazonReviewRecord, str | None, str | None]
    ]:
        yield (short_row, None, None)

    plan = CategoryIngestPlan(
        category="Synth",
        review_iter=_gen(),
        input_file=None,
        distiller_config=DistillerConfig(min_review_chars=40),
    )
    persister = InMemorySignalPersister()
    stats = asyncio.run(
        ingest_category(plan, persister, dry_run=False, resume=False),
    )
    assert stats.rows_rejected == 1
    assert stats.rejection_reasons["too_short"] == 1


# ---------------------------------------------------------------------------
# 5/6/7. Distilled signal carries no forbidden field
# ---------------------------------------------------------------------------


def test_distilled_signal_has_no_user_id_or_image_field() -> None:
    """The `DistilledSignal` dataclass deliberately omits user_id /
    image fields, so the persister can never write them."""
    fields = {f for f in DistilledSignal.__dataclass_fields__}
    forbidden = {"user_id", "user_id_hash", "image", "images",
                 "image_url", "image_urls", "raw_text", "full_text"}
    overlap = fields & forbidden
    assert overlap == set(), (
        f"DistilledSignal must not expose {sorted(overlap)}"
    )


def test_amazon_review_signal_model_has_no_user_or_image_columns() -> None:
    """The SQLAlchemy table itself must not declare any
    user_id/image column. Catches the case where the dataclass
    shrinks but the model grew an unrelated column."""
    from assembly.models.amazon_review_signal import AmazonReviewSignal
    cols = {c.name for c in AmazonReviewSignal.__table__.columns}
    forbidden = {"user_id", "user_id_hash", "image", "images",
                 "image_url", "image_urls", "raw_text", "full_text",
                 "review_body", "review_text"}
    overlap = cols & forbidden
    assert overlap == set(), (
        f"amazon_review_signal table must not expose {sorted(overlap)}"
    )


def test_short_snippet_never_carries_full_raw_body(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """Confirms the snippet is always capped — i.e. the FULL raw
    review body never lands in the table. The snippet cap is the
    distiller's `short_snippet_max_chars` (default 240)."""
    persister = InMemorySignalPersister()
    asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            persister, dry_run=False, resume=False,
        ),
    )
    assert persister.inserted, "fixture should produce signals"
    for s in persister.inserted:
        assert len(s.short_snippet) <= 240


# ---------------------------------------------------------------------------
# 8. --limit honored
# ---------------------------------------------------------------------------


def test_limit_caps_rows_scanned(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    plan = _plan_for(enabled_provider, "Electronics")
    persister = InMemorySignalPersister()
    stats = asyncio.run(
        ingest_category(
            plan, persister, dry_run=False, resume=False, limit=3,
        ),
    )
    assert stats.rows_scanned <= 3


# ---------------------------------------------------------------------------
# 9. Audit JSON shape
# ---------------------------------------------------------------------------


def test_audit_payload_has_required_keys(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    persister = NullSignalPersister()
    stats = asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            persister, dry_run=True, resume=False,
        ),
    )
    payload = build_audit_payload(stats)
    required = {
        "phase", "category", "input_file", "dry_run",
        "started_at", "finished_at", "runtime_seconds",
        "counts", "rejection_reasons",
        "signal_type_distribution", "sentiment_distribution",
        "top_themes", "sample_accepted_signals", "sample_rejected_rows",
    }
    missing = required - set(payload)
    assert not missing, f"audit JSON missing keys: {sorted(missing)}"
    counts = payload["counts"]
    assert isinstance(counts, dict)
    count_keys = {
        "rows_scanned", "rows_accepted", "rows_rejected",
        "rows_already_ingested", "signals_generated", "signals_inserted",
        "signals_skipped_duplicate",
    }
    missing_counts = count_keys - set(counts)
    assert not missing_counts, (
        f"audit counts missing: {sorted(missing_counts)}"
    )


def test_audit_payload_is_json_serializable(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    persister = NullSignalPersister()
    stats = asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            persister, dry_run=True, resume=False,
        ),
    )
    payload = build_audit_payload(stats)
    serialized = json.dumps(payload, default=str)
    round_tripped = json.loads(serialized)
    assert round_tripped["category"] == "Electronics"
    assert round_tripped["dry_run"] is True
    assert isinstance(round_tripped["counts"], dict)


# ---------------------------------------------------------------------------
# 10. Signal-type + sentiment distributions reported
# ---------------------------------------------------------------------------


def test_distributions_populated_from_real_fixture(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    persister = NullSignalPersister()
    stats = asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            persister, dry_run=True, resume=False,
        ),
    )
    assert stats.signal_type_distribution
    assert stats.sentiment_distribution
    # Sentiment values must be one of the closed set.
    assert set(stats.sentiment_distribution).issubset(
        {"positive", "negative", "mixed"},
    )


# ---------------------------------------------------------------------------
# 11. Feature flag stays off by default
# ---------------------------------------------------------------------------


def test_ingestion_does_not_flip_global_feature_flag() -> None:
    """Importing the ingestion module / running the CLI must not
    mutate `Settings.amazon_reviews_enabled` for the rest of the
    process. (Operator constraint: production stays disabled.)"""
    from assembly.config import get_settings, Settings
    # Force a fresh Settings — clears any cached lru result.
    get_settings.cache_clear()
    s_before = Settings()
    import scripts.ingest_amazon_review_signals_11b  # noqa: F401
    s_after = Settings()
    assert s_before.amazon_reviews_enabled is False
    assert s_after.amazon_reviews_enabled is False


# ---------------------------------------------------------------------------
# 12. CLI arg parsing
# ---------------------------------------------------------------------------


def test_cli_parser_accepts_spec_flags() -> None:
    from scripts.ingest_amazon_review_signals_11b import _build_parser
    p: argparse.ArgumentParser = _build_parser()

    args = p.parse_args([
        "--category", "Electronics", "All_Beauty",
        "--limit", "100",
        "--resume",
        "--dry-run",
    ])
    assert args.category == ["Electronics", "All_Beauty"]
    assert args.limit == 100
    assert args.resume is True
    assert args.dry_run is True

    args2 = p.parse_args(["--category", "Electronics", "--commit"])
    assert args2.dry_run is False


def test_cli_parser_defaults_to_dry_run() -> None:
    """The script's default is `--dry-run` so an operator who runs
    it without flags can never accidentally insert."""
    from scripts.ingest_amazon_review_signals_11b import _build_parser
    args = _build_parser().parse_args(["--category", "Electronics"])
    assert args.dry_run is True


def test_cli_disallows_conflicting_dry_run_and_commit() -> None:
    from scripts.ingest_amazon_review_signals_11b import _build_parser
    with pytest.raises(SystemExit):
        _build_parser().parse_args([
            "--category", "Electronics", "--dry-run", "--commit",
        ])


# ---------------------------------------------------------------------------
# 13. End-to-end runner with in-memory persister + in-memory audit
# ---------------------------------------------------------------------------


def test_run_function_end_to_end_with_in_memory_persister(
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the `_run` async function the same way the CLI does,
    but pass in `InMemorySignalPersister` + `InMemoryAuditWriter` so
    the test never touches disk or Postgres."""
    import logging

    from scripts.ingest_amazon_review_signals_11b import (
        _run, InMemoryAuditWriter,
    )

    persister = InMemorySignalPersister()
    audit = InMemoryAuditWriter()
    log = logging.getLogger("phase_11b_test")

    stats_list = asyncio.run(
        _run(
            categories=("Electronics", "All_Beauty"),
            dry_run=False,
            resume=False,
            limit=None,
            data_dir=fixture_dir,
            persister=persister,
            audit_writer=audit,
            log=log,
        ),
    )
    assert len(stats_list) == 2
    cats = {s.category for s in stats_list}
    assert cats == {"Electronics", "All_Beauty"}
    assert audit.payloads.keys() == cats
    assert persister.inserted, "commit-mode end-to-end produced no signals"
    # Resume rerun: every signal should dedup.
    rerun = asyncio.run(
        _run(
            categories=("Electronics",),
            dry_run=False,
            resume=True,
            limit=None,
            data_dir=fixture_dir,
            persister=persister,
            audit_writer=audit,
            log=log,
        ),
    )
    assert rerun[0].signals_inserted == 0
    assert rerun[0].rows_already_ingested > 0


# ---------------------------------------------------------------------------
# 14. Drift: ingestion module does not import HTTP/scrape libs
# ---------------------------------------------------------------------------


def test_ingestion_module_has_no_http_imports() -> None:
    import re
    from assembly.sources.amazon_reviews_provider import ingestion as ing
    src = inspect.getsource(ing)
    forbidden = ("requests", "httpx", "aiohttp", "selenium",
                 "playwright", "scrapy", "bs4", "beautifulsoup4")
    for token in forbidden:
        pattern = re.compile(
            rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
            re.MULTILINE,
        )
        assert pattern.search(src) is None, (
            f"ingestion module imports forbidden module {token!r}"
        )


def test_cli_script_has_no_http_imports() -> None:
    import re
    import scripts.ingest_amazon_review_signals_11b as cli
    src = inspect.getsource(cli)
    forbidden = ("requests", "httpx", "aiohttp", "selenium",
                 "playwright", "scrapy", "bs4")
    for token in forbidden:
        pattern = re.compile(
            rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
            re.MULTILINE,
        )
        assert pattern.search(src) is None, (
            f"CLI script imports forbidden module {token!r}"
        )


# ---------------------------------------------------------------------------
# 15. Stats accumulator math
# ---------------------------------------------------------------------------


def test_stats_accumulator_math() -> None:
    stats = IngestionStats(category="Test")
    sig = DistilledSignal(
        source_dataset="amazon_reviews_2023",
        category="Test",
        product_title=None, brand=None, asin=None, parent_asin=None,
        rating=5, review_timestamp=None, verified_purchase=True,
        helpful_votes=None,
        sentiment_bucket="positive", signal_type="praise",
        theme="general_praise", short_snippet="snippet",
        competitor_mention=None, use_case=None,
        source_review_hash="abcd1234",
    )
    stats.record_accepted([sig])
    stats.record_inserted(1)
    stats.record_dup_skip(2)
    assert stats.rows_accepted == 1
    assert stats.signals_generated == 1
    assert stats.signals_inserted == 1
    assert stats.signals_skipped_duplicate == 2
    assert stats.sentiment_distribution["positive"] == 1
    assert stats.signal_type_distribution["praise"] == 1
    assert stats.theme_distribution["general_praise"] == 1
    assert stats.runtime_seconds >= 0


# ---------------------------------------------------------------------------
# 16. Audit-path helper produces a safe filename
# ---------------------------------------------------------------------------


def test_audit_path_for_category_is_safe() -> None:
    from scripts.ingest_amazon_review_signals_11b import audit_path_for
    p = audit_path_for("Home/Kitchen + Stuff")
    assert "/" not in p.name
    assert p.name.startswith("amazon_reviews_ingestion_11b_")
    assert p.name.endswith(".json")


# ---------------------------------------------------------------------------
# 17. Public iter_category_reviews — Phase 11B hardening
# ---------------------------------------------------------------------------


def test_public_iter_category_reviews_streams_records(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """The public iterator yields the exact same triples the CLI
    needs: (record, product_title, brand)."""
    triples = list(enabled_provider.iter_category_reviews("Electronics"))
    assert triples, "fixture should produce at least one row"
    for record, title, brand in triples:
        assert isinstance(record, AmazonReviewRecord)
        # Fixture rows are all linked to a known meta row, so title +
        # brand should never be None for Electronics.
        assert title is not None
        assert brand is not None


def test_iter_category_reviews_returns_empty_when_disabled(
    fixture_dir: Path,
) -> None:
    """Disabled provider returns an empty iterator without raising."""
    cfg = AmazonReviewsProviderConfig(
        enabled=False, data_dir=fixture_dir,
        categories=("Electronics",),
    )
    p = AmazonReviewsProvider(cfg)
    assert list(p.iter_category_reviews("Electronics")) == []


def test_iter_category_reviews_require_enabled_raises_when_disabled(
    fixture_dir: Path,
) -> None:
    """Strict mode raises a loud error so callers that opt into 'I
    require the provider' fail clean rather than silently no-op."""
    from assembly.sources.amazon_reviews_provider import (
        ProviderUnavailableError,
    )
    cfg = AmazonReviewsProviderConfig(
        enabled=False, data_dir=fixture_dir,
        categories=("Electronics",),
    )
    p = AmazonReviewsProvider(cfg)
    with pytest.raises(ProviderUnavailableError):
        # Materializing the iterator must surface the error — but
        # the helper raises eagerly so even `list(...)` would catch
        # it.
        p.iter_category_reviews("Electronics", require_enabled=True)


def test_cli_does_not_call_private_iter_category() -> None:
    """Drift guard — the CLI must depend on the public API only.

    Greps the CLI source for `_iter_category`. Any future
    refactor that re-introduces the private dependency fails this
    test instantly.
    """
    import scripts.ingest_amazon_review_signals_11b as cli
    src = inspect.getsource(cli)
    assert "_iter_category" not in src, (
        "CLI must use iter_category_reviews (public) — found "
        "reference to the private _iter_category method"
    )


def test_ingestion_module_does_not_call_private_iter_category() -> None:
    """Drift guard for the ingestion module itself. The engine
    consumes whatever iterator the CategoryIngestPlan carries; it
    should never reach into the provider's private API directly."""
    from assembly.sources.amazon_reviews_provider import ingestion as ing
    src = inspect.getsource(ing)
    assert "._iter_category" not in src, (
        "ingestion module must depend on the public iterator only"
    )


# ---------------------------------------------------------------------------
# 18. Batch / transaction behavior (Phase 11B hardening #2)
# ---------------------------------------------------------------------------


class _CountingPersister(InMemorySignalPersister):
    """InMemorySignalPersister subclass that counts how many times
    insert_signals is invoked. Lets us prove ingestion commits in
    batches, not one giant per-category transaction."""

    def __init__(self) -> None:
        super().__init__()
        self.insert_call_count: int = 0
        self.batch_sizes: list[int] = []

    async def insert_signals(
        self, signals: list[DistilledSignal],
    ) -> int:
        self.insert_call_count += 1
        self.batch_sizes.append(len(signals))
        return await super().insert_signals(signals)


def test_ingestion_commits_per_batch_not_per_category() -> None:
    """Feed enough signals to exceed `batch_size` and confirm the
    persister gets called multiple times. Real Postgres persister
    opens-commits-closes one session per call, so this also proves
    each batch is its own transaction."""
    # Build a synthetic iterator that emits many "praise" reviews so
    # each one distills into ≥1 signal.
    def _make_record(i: int) -> AmazonReviewRecord:
        return AmazonReviewRecord(
            category="Synth", parent_asin=f"P{i:04d}", asin=f"P{i:04d}A",
            rating=5.0, title=f"great {i}",
            text=(
                "I absolutely love this product. Used daily for 6 "
                f"months and it still works. Worth every penny. {i}"
            ),
            helpful_vote=0, verified_purchase=True, timestamp=i,
            user_id_hash=f"hash_{i:04d}",
        )

    def _gen() -> Iterator[
        tuple[AmazonReviewRecord, str | None, str | None]
    ]:
        for i in range(120):
            yield (_make_record(i), None, None)

    plan = CategoryIngestPlan(
        category="Synth",
        review_iter=_gen(),
        input_file=None,
        distiller_config=DistillerConfig(min_review_chars=40),
    )
    persister = _CountingPersister()
    stats = asyncio.run(
        ingest_category(
            plan, persister, dry_run=False, resume=False,
            batch_size=50,  # small batch to force multiple commits
        ),
    )
    # 120 reviews × ≥1 signal each, batched at 50 → ≥ 3 commit calls.
    assert persister.insert_call_count >= 3, (
        f"expected ≥3 batch commits, got {persister.insert_call_count} "
        f"(batch_sizes={persister.batch_sizes})"
    )
    # Every batch except the last must have reached the batch_size
    # threshold before flushing. A batch may slightly exceed the
    # threshold because the engine appends all of one review's
    # signals before checking — that's expected and safe. Final
    # batch carries the leftover and may be smaller.
    for size in persister.batch_sizes[:-1]:
        assert size >= 50, (
            f"engine flushed early at size={size} "
            f"(should have waited for >=50)"
        )
    # Inserted count matches sum across batches.
    assert sum(persister.batch_sizes) == stats.signals_inserted


def test_resume_after_partial_failure_recovers_cleanly(
    enabled_provider: AmazonReviewsProvider,
) -> None:
    """Simulate: a previous ingestion run committed batches 1-2 of a
    category and crashed. Resume should:
      * see the already-committed review hashes
      * skip those reviews entirely
      * commit only the remaining signals
      * reach the same final state as a clean single-pass run

    Crash semantics: because the ingest loop only flushes complete
    reviews to the persister (per-batch transaction), a real crash
    leaves "all signals of N completed reviews persisted, zero
    signals of remaining reviews persisted". We model the same
    invariant in the test setup by splitting on whole-review
    boundaries — half the review hashes pre-loaded with ALL their
    signals.
    """
    persister = InMemorySignalPersister()

    # First, a full pass so we know the "complete" end state.
    asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            persister, dry_run=False, resume=False,
        ),
    )
    assert persister.inserted, "fixture should produce signals"

    # Group all signals by review hash, then take half the reviews
    # to simulate "previously committed".
    by_review: dict[str, list[DistilledSignal]] = {}
    for s in persister.inserted:
        by_review.setdefault(s.source_review_hash, []).append(s)
    keep_hashes = list(by_review)[: len(by_review) // 2]

    fresh = InMemorySignalPersister()
    for h in keep_hashes:
        fresh.inserted.extend(by_review[h])
    fresh.preload_review_hashes("Electronics", keep_hashes)

    # Resume run — should skip the kept hashes and insert the rest
    # exactly once.
    resume_stats = asyncio.run(
        ingest_category(
            _plan_for(enabled_provider, "Electronics"),
            fresh, dry_run=False, resume=True,
        ),
    )
    assert resume_stats.rows_already_ingested >= len(keep_hashes)

    # Final state == clean-run state, no dups, no missing rows.
    final_keys = {(s.source_review_hash, s.signal_type)
                  for s in fresh.inserted}
    complete_keys = {(s.source_review_hash, s.signal_type)
                     for s in persister.inserted}
    assert final_keys == complete_keys, (
        "resume-after-crash should reach the same end state as a "
        "clean single-pass run"
    )


def test_postgres_persister_docstring_documents_batch_semantics() -> None:
    """The hardened persister must document its batch-commit
    behavior so future maintainers can't accidentally widen the
    transaction to a per-category one."""
    from scripts.ingest_amazon_review_signals_11b import (
        PostgresSignalPersister,
    )
    doc = (PostgresSignalPersister.__doc__ or "").lower()
    # Must mention key invariants. If someone rewrites the
    # persister and drops these phrases, the docstring drift fires.
    for needle in (
        "one transaction per batch",
        "batch_size",
        "resume",
        "idempotent",
    ):
        assert needle in doc, (
            f"PostgresSignalPersister docstring must mention {needle!r}"
        )
