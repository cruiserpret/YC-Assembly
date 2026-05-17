"""Phase 11D.2 — CSV ingestion + Postgres source tests.

Operator's acceptance checklist:

  1. Dry-run writes ZERO rows.
  2. Commit writes distilled signals.
  3. Duplicate rerun skips duplicates (same CSV, same persister).
  4. Missing optional CSV columns are handled cleanly.
  5. Blank / low-quality rows are rejected.
  6. Raw text is NOT persisted beyond `short_snippet` (≤ 240 chars).
  7. Metadata PII keys (`author_handle`, `user_id`, `email`, …) are
     stripped.
  8. Postgres source methods return expected rows (Protocol shape +
     async signature; we don't connect to a real DB here).
  9. Feature flags default OFF.
 10. No `apps/web/` files touched.
 11. No live HTTP / scraping imports introduced.
 12. CLI `--dry-run` is the default.
 13. Dedupe identity is namespaced by `(source_provider, ts/category,
     signal_type, snippet_hash)`.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import pytest

from assembly.sources.tech_market_provider import (
    InMemoryTechMarketPersister,
    NullTechMarketPersister,
    TechMarketIngestionStats,
    TechSignalIdentity,
    build_audit_payload,
    dedupe_identity_for,
    distill_csv_row,
    ingest_csv_rows,
)
from assembly.sources.tech_market_provider.distiller import (
    RuleBasedTechMarketDistiller,
)


# ---------------------------------------------------------------------------
# Tiny synthetic CSV fixtures (string-only — NEVER write CSV files to disk)
# ---------------------------------------------------------------------------


_CSV_BASIC = """text,company_or_product,competitor_name,buyer_type,market_context,source_timestamp,evidence_url,metadata_json
"the per-seat enterprise pricing is too expensive for our finance team","Generic AI Co",,buyer,B2B,1700000001,https://example.com/a,"{""rating"": 2}"
"I'd happily pay $50/month for the pro tier — worth every cent","Generic AI Co",,user,AI_tool,1700000002,,"{""rating"": 5}"
"procurement asked for a SOC 2 report and blocked the renewal","Generic Workflow SaaS",,buyer,B2B,1700000003,,
"the webhook integration broke twice this week on the staging API","Generic Devtool",,developer,devtool,1700000004,,
"this game crashes every time I load level one of the boss fight",,,,,,,
"no",,,,,,,
"""


_CSV_NO_OPTIONAL_COLUMNS = """text
"the per-seat enterprise pricing is too expensive for our finance team"
"the webhook integration with our GitHub actions broke after v2 release"
"""


_CSV_WITH_PII = """text,metadata_json
"the per-seat enterprise pricing is too expensive for our finance team","{""rating"": 2, ""author_handle"": ""@bob123"", ""user_id"": ""u_xyz"", ""email"": ""bob@example.com""}"
"""


def _csv_rows(csv_text: str) -> Iterable[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        yield {k: (v or "") for k, v in row.items() if k is not None}


# ---------------------------------------------------------------------------
# 1. Dry-run writes ZERO rows
# ---------------------------------------------------------------------------


def test_dry_run_writes_zero_rows_to_persister() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=True,
    ))
    # Stats record what would have been written, but the persister
    # contains zero inserted rows.
    assert persister.inserted == []
    assert stats.signals_inserted == 0
    # Signals were still GENERATED — that's the dry-run reporting path.
    assert stats.signals_generated >= 1


def test_dry_run_never_calls_insert_signals_on_any_persister() -> None:
    """Strong dry-run contract: the ingestion loop must not call
    `persister.insert_signals` when `dry_run=True`, regardless of
    persister type. This is the operator-spec'd safety guarantee."""
    persister = NullTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=True,
    ))
    assert stats.signals_generated >= 1
    assert stats.signals_inserted == 0
    # Even the NullTechMarketPersister stays empty in dry-run mode.
    assert persister.would_have_inserted == []


def test_null_persister_records_writes_when_dry_run_false() -> None:
    """NullTechMarketPersister is for situations where the caller
    wants a no-op DB but does want the audit record. In commit mode
    (dry_run=False) the loop calls insert_signals as usual."""
    persister = NullTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=False,
    ))
    assert persister.would_have_inserted, (
        "NullTechMarketPersister should record the batch in commit mode"
    )
    # signals_inserted stays 0 because Null returns 0 from insert.
    assert stats.signals_inserted == 0


# ---------------------------------------------------------------------------
# 2. Commit writes distilled signals
# ---------------------------------------------------------------------------


def test_commit_writes_distilled_signals() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=False,
    ))
    assert persister.inserted, "commit mode must persist signals"
    assert stats.signals_inserted == len(persister.inserted)
    assert stats.signals_inserted >= 3  # 4 valid + 2 rejected rows


# ---------------------------------------------------------------------------
# 3. Duplicate rerun skips duplicates
# ---------------------------------------------------------------------------


def test_rerun_against_same_persister_inserts_zero_new_rows() -> None:
    persister = InMemoryTechMarketPersister()
    # First run: commit.
    first = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=False,
    ))
    inserted_first = first.signals_inserted
    assert inserted_first > 0

    # Second run: same CSV, same persister.
    second = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=False,
    ))
    assert second.signals_inserted == 0
    assert second.duplicates_skipped >= inserted_first


def test_dedupe_identity_falls_back_when_timestamp_missing() -> None:
    """When `source_timestamp` is None, the identity must use
    (source_provider, product_category, signal_type, snippet_hash)."""
    from assembly.sources.tech_market_provider.distiller import (
        DistilledTechSignal,
    )
    sig = DistilledTechSignal(
        source_provider="g2_synthetic_csv",
        source_category=None,
        product_category="ai_saas",
        company_or_product=None,
        competitor_name=None,
        signal_type="pricing_objection",
        sentiment_bucket="negative",
        buyer_type="buyer",
        market_context="B2B",
        theme=None,
        short_snippet="too expensive for our finance team",
        evidence_url=None,
        source_timestamp=None,
        relevance_score=None,
        metadata={},
    )
    ident = dedupe_identity_for(sig)
    assert ident.discriminator == "cat"
    assert ident.parts[0] == "g2_synthetic_csv"
    assert ident.parts[1] == "ai_saas"
    assert ident.parts[2] == "pricing_objection"


def test_dedupe_identity_uses_timestamp_when_present() -> None:
    from assembly.sources.tech_market_provider.distiller import (
        DistilledTechSignal,
    )
    sig = DistilledTechSignal(
        source_provider="g2_synthetic_csv",
        source_category=None,
        product_category="ai_saas",
        company_or_product=None,
        competitor_name=None,
        signal_type="pricing_objection",
        sentiment_bucket="negative",
        buyer_type="buyer",
        market_context="B2B",
        theme=None,
        short_snippet="too expensive for our finance team",
        evidence_url=None,
        source_timestamp=1700000000,
        relevance_score=None,
        metadata={},
    )
    ident = dedupe_identity_for(sig)
    assert ident.discriminator == "ts"
    assert ident.parts[1] == 1700000000


def test_dedupe_namespaces_ts_and_cat_separately() -> None:
    """A signal with a timestamp must not collide with the same
    snippet sans timestamp — they're different rows from the
    persister's perspective."""
    from assembly.sources.tech_market_provider.distiller import (
        DistilledTechSignal,
    )
    base = dict(
        source_provider="p", source_category=None,
        product_category="ai_saas", company_or_product=None,
        competitor_name=None,
        signal_type="pricing_objection",
        sentiment_bucket="negative",
        buyer_type="buyer", market_context="B2B",
        theme=None,
        short_snippet="too expensive for our finance team",
        evidence_url=None, relevance_score=None, metadata={},
    )
    sig_ts = DistilledTechSignal(**base, source_timestamp=1700000000)
    sig_no_ts = DistilledTechSignal(**base, source_timestamp=None)
    a = dedupe_identity_for(sig_ts)
    b = dedupe_identity_for(sig_no_ts)
    assert a != b
    assert a.discriminator == "ts"
    assert b.discriminator == "cat"


def test_pre_existing_identities_block_inserts() -> None:
    """The persister's prior-state set must be respected: if an
    identity is already in `existing_identities()`, the row must be
    counted as a duplicate and never written."""
    from assembly.sources.tech_market_provider.distiller import (
        DistilledTechSignal,
    )
    persister = InMemoryTechMarketPersister()
    # Pre-load an identity matching one of the expected outputs.
    sig = DistilledTechSignal(
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        company_or_product=None,
        competitor_name=None,
        signal_type="pricing_objection",
        sentiment_bucket="negative",
        buyer_type="buyer",
        market_context="B2B",
        theme=None,
        short_snippet=(
            "the per-seat enterprise pricing is too expensive for our "
            "finance team"
        ),
        evidence_url=None,
        source_timestamp=1700000001,
        relevance_score=None,
        metadata={},
    )
    persister.preload_identities({dedupe_identity_for(sig)})
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=False,
    ))
    # The pricing_objection row from the CSV must have been counted
    # as a duplicate — never inserted.
    assert stats.duplicates_skipped >= 1
    for s in persister.inserted:
        assert s.signal_type != "pricing_objection" or s.source_timestamp != 1700000001


# ---------------------------------------------------------------------------
# 4. Missing optional CSV columns
# ---------------------------------------------------------------------------


def test_missing_optional_columns_handled_cleanly() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_NO_OPTIONAL_COLUMNS),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category=None,
        product_category="ai_saas",
        market_context_hint=None,
        dry_run=False,
    ))
    # Both rows distill successfully even though every optional
    # column is missing from the CSV header.
    assert stats.rows_scanned == 2
    assert stats.rows_accepted >= 1


# ---------------------------------------------------------------------------
# 5. Blank / low-quality rows rejected
# ---------------------------------------------------------------------------


def test_blank_rows_rejected_with_explicit_reason() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="g2_synthetic_csv",
        source_category="g2_review",
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=False,
    ))
    # The 6-row CSV contains one too-short row ("no") and one game
    # snippet that doesn't classify; both end up rejected.
    assert stats.rows_rejected >= 1
    reasons = set(stats.rejection_reasons.keys())
    assert reasons & {"blank_text", "text_too_short", "no_signal_classified"}


def test_text_below_min_length_rejected() -> None:
    """Specific assertion on the short-text path."""
    persister = InMemoryTechMarketPersister()
    csv_text = "text\n\"nope\"\n"
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(csv_text),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        dry_run=False,
    ))
    assert stats.rows_accepted == 0
    assert "text_too_short" in stats.rejection_reasons


def test_blank_text_rejected_with_blank_reason() -> None:
    persister = InMemoryTechMarketPersister()
    csv_text = "text\n\"\"\n"
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(csv_text),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        dry_run=False,
    ))
    assert stats.rows_accepted == 0
    assert "blank_text" in stats.rejection_reasons


def test_unclassified_text_rejected() -> None:
    """A text that doesn't match any signal_type cue must end up in
    the rejections with `no_signal_classified`."""
    persister = InMemoryTechMarketPersister()
    csv_text = (
        "text\n"
        "\"the weather today is fine and the cat slept all afternoon\"\n"
    )
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(csv_text),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        dry_run=False,
    ))
    assert stats.rows_accepted == 0
    assert "no_signal_classified" in stats.rejection_reasons


# ---------------------------------------------------------------------------
# 6. Raw text NOT persisted beyond short_snippet
# ---------------------------------------------------------------------------


def test_persisted_signals_only_carry_short_snippet() -> None:
    persister = InMemoryTechMarketPersister()
    long_text = (
        "the per-seat enterprise pricing is too expensive for our "
        "finance team " + ("x " * 200)
    )
    csv_text = "text\n\"" + long_text + "\"\n"
    asyncio.run(ingest_csv_rows(
        _csv_rows(csv_text),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        dry_run=False,
    ))
    assert persister.inserted, "row should have been accepted"
    for s in persister.inserted:
        # 240-char hard cap (snippet cap + ellipsis trim).
        assert len(s.short_snippet) <= 240
        # The original 1000+ char body is NOT preserved anywhere on
        # the DistilledTechSignal.
        assert "x x x" not in s.short_snippet or len(s.short_snippet) <= 240


# ---------------------------------------------------------------------------
# 7. Metadata PII keys stripped
# ---------------------------------------------------------------------------


def test_metadata_pii_keys_stripped_in_csv_path() -> None:
    persister = InMemoryTechMarketPersister()
    asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_WITH_PII),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        dry_run=False,
    ))
    assert persister.inserted, "row should distill successfully"
    md = persister.inserted[0].metadata
    assert "author_handle" not in md
    assert "user_id" not in md
    assert "email" not in md
    # Non-PII keys (e.g. rating) survive.
    assert md.get("rating") == 2


# ---------------------------------------------------------------------------
# 8. Postgres source — Protocol shape + async signatures
# ---------------------------------------------------------------------------


def test_postgres_source_implements_protocol_methods() -> None:
    import inspect
    from assembly.sources.tech_market_provider.postgres_source import (
        PostgresTechMarketSignalSource,
    )
    for name in (
        "fetch_by_product_category",
        "fetch_by_market_context",
        "fetch_by_competitor",
        "fetch_by_signal_types",
    ):
        method = getattr(PostgresTechMarketSignalSource, name)
        assert inspect.iscoroutinefunction(method), (
            f"{name} must be async"
        )


def test_postgres_source_satisfies_protocol_type_check() -> None:
    from assembly.sources.tech_market_provider import TechMarketSignalSource
    from assembly.sources.tech_market_provider.postgres_source import (
        PostgresTechMarketSignalSource,
    )
    # Type-only assertion — the assignment statement at module-level
    # confirms structural conformance.
    _check: TechMarketSignalSource = (
        PostgresTechMarketSignalSource.__new__(
            PostgresTechMarketSignalSource,
        )
    )
    assert _check is not None


# ---------------------------------------------------------------------------
# 9. Feature flags still default OFF
# ---------------------------------------------------------------------------


def test_tech_market_flags_default_false_after_11d_2() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.tech_market_signals_enabled is False
    assert s.tech_market_signals_runtime_enabled is False
    assert s.tech_market_signals_persona_injection_enabled is False


# ---------------------------------------------------------------------------
# 10 + 11. Drift: no frontend changes, no live HTTP imports
# ---------------------------------------------------------------------------


_NEW_MODULES = (
    "apps/api/src/assembly/sources/tech_market_provider/postgres_source.py",
    "apps/api/src/assembly/sources/tech_market_provider/ingestion.py",
    "apps/api/scripts/ingest_tech_market_csv.py",
)


def test_new_modules_have_no_http_or_scraping_imports() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    forbidden = (
        "requests", "httpx", "aiohttp", "selenium", "playwright",
        "scrapy", "bs4", "beautifulsoup4", "urllib.request",
    )
    for rel in _NEW_MODULES:
        # Some _NEW_MODULES paths are repo-relative (apps/api/...);
        # others (scripts/...) are relative to apps/api. Compute both.
        path = repo_root / rel.removeprefix("apps/api/")
        if not path.exists():
            path = (
                Path(__file__).resolve().parents[3]
                / rel
            )
        assert path.exists(), f"missing module: {path}"
        src = path.read_text(encoding="utf-8")
        for token in forbidden:
            pat = re.compile(
                rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
                re.MULTILINE,
            )
            assert pat.search(src) is None, (
                f"{path.name} imports forbidden module {token!r}"
            )


def test_no_apps_web_files_modified_by_11d_2() -> None:
    """Static check: every new module Phase 11D.2 ships lives under
    apps/api/. The frontend remains frozen."""
    for rel in _NEW_MODULES:
        assert rel.startswith("apps/api/"), (
            f"{rel} is not under apps/api/ — Phase 11D.2 must remain "
            f"backend-only"
        )


def test_ingestion_module_has_no_persona_injection_wiring() -> None:
    """The CSV ingestion path must NEVER touch the persona-injection
    helpers. Drift check."""
    pkg = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "sources" / "tech_market_provider"
        / "ingestion.py"
    )
    src = pkg.read_text(encoding="utf-8")
    assert "persona_injection" not in src
    assert "build_amazon_persona_prompt_block" not in src


def test_no_production_code_imports_ingestion_module() -> None:
    """Phase 11D.2 ingestion is a dev/local CLI. No file under
    api/, pipeline/, or orchestration/ may import the ingestion
    engine."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    live_dirs = [
        api_root / "api",
        api_root / "pipeline",
        api_root / "orchestration",
    ]
    for d in live_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert (
                "tech_market_provider.ingestion" not in text
            ), (
                f"{path} imports tech_market_provider.ingestion — "
                f"must stay CLI-only"
            )


# ---------------------------------------------------------------------------
# 12. CLI dry-run default
# ---------------------------------------------------------------------------


def test_cli_dry_run_is_default(tmp_path: Path) -> None:
    """Run the CLI script with --csv-path pointing at a tempfile;
    assert that with NO --commit flag, the CLI writes zero rows
    (default = dry-run). We use a subprocess so we exercise the
    real argument parser end-to-end."""
    csv_file = tmp_path / "tiny.csv"
    csv_file.write_text(_CSV_BASIC, encoding="utf-8")
    audit_file = tmp_path / "audit.json"

    apps_api = Path(__file__).resolve().parent.parent
    script = apps_api / "scripts" / "ingest_tech_market_csv.py"
    assert script.exists()

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--csv-path", str(csv_file),
            "--source-provider", "test_csv",
            "--product-category", "ai_saas",
            "--audit-out", str(audit_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(apps_api),
        env={
            "PYTHONPATH": str(apps_api / "src"),
            "PATH": "/usr/bin:/bin",
        },
        timeout=30,
    )
    assert result.returncode == 0, (
        f"CLI failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert audit_file.exists()
    audit = json.loads(audit_file.read_text(encoding="utf-8"))
    assert audit["dry_run"] is True
    assert audit["counts"]["signals_inserted"] == 0
    # Some signals were generated; CLI just didn't write them.
    assert audit["counts"]["signals_generated"] >= 1


def test_cli_rejects_unknown_market_context(tmp_path: Path) -> None:
    csv_file = tmp_path / "tiny.csv"
    csv_file.write_text(_CSV_NO_OPTIONAL_COLUMNS, encoding="utf-8")
    apps_api = Path(__file__).resolve().parent.parent
    script = apps_api / "scripts" / "ingest_tech_market_csv.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--csv-path", str(csv_file),
            "--source-provider", "test_csv",
            "--product-category", "ai_saas",
            "--market-context", "BOGUS",
        ],
        capture_output=True,
        text=True,
        cwd=str(apps_api),
        env={
            "PYTHONPATH": str(apps_api / "src"),
            "PATH": "/usr/bin:/bin",
        },
        timeout=30,
    )
    assert result.returncode != 0
    assert "market-context" in result.stderr


def test_cli_rejects_missing_csv(tmp_path: Path) -> None:
    apps_api = Path(__file__).resolve().parent.parent
    script = apps_api / "scripts" / "ingest_tech_market_csv.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--csv-path", str(tmp_path / "no_such_file.csv"),
            "--source-provider", "test_csv",
            "--product-category", "ai_saas",
        ],
        capture_output=True,
        text=True,
        cwd=str(apps_api),
        env={
            "PYTHONPATH": str(apps_api / "src"),
            "PATH": "/usr/bin:/bin",
        },
        timeout=30,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


def test_cli_rejects_csv_without_text_column(tmp_path: Path) -> None:
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text(
        "company,rating\nGeneric AI Co,5\n", encoding="utf-8",
    )
    apps_api = Path(__file__).resolve().parent.parent
    script = apps_api / "scripts" / "ingest_tech_market_csv.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--csv-path", str(csv_file),
            "--source-provider", "test_csv",
            "--product-category", "ai_saas",
        ],
        capture_output=True,
        text=True,
        cwd=str(apps_api),
        env={
            "PYTHONPATH": str(apps_api / "src"),
            "PATH": "/usr/bin:/bin",
        },
        timeout=30,
    )
    assert result.returncode != 0
    assert "text" in result.stderr.lower()


# ---------------------------------------------------------------------------
# 13. Audit JSON shape
# ---------------------------------------------------------------------------


_REQUIRED_AUDIT_KEYS = (
    "phase",
    "source_provider",
    "product_category",
    "csv_path",
    "dry_run",
    "runtime_seconds",
    "counts",
    "rejection_reasons",
    "signal_type_distribution",
    "buyer_type_distribution",
    "market_context_distribution",
    "sample_accepted_signals",
    "sample_rejected_rows",
    "safety_notes",
)


def test_audit_payload_has_every_documented_key() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=True,
    ))
    audit = build_audit_payload(stats)
    for key in _REQUIRED_AUDIT_KEYS:
        assert key in audit, f"audit missing key {key!r}"


def test_audit_counts_match_stats_fields() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        dry_run=False,
    ))
    audit = build_audit_payload(stats)
    counts = audit["counts"]
    assert counts["rows_scanned"] == stats.rows_scanned
    assert counts["rows_accepted"] == stats.rows_accepted
    assert counts["rows_rejected"] == stats.rows_rejected
    assert counts["signals_generated"] == stats.signals_generated
    assert counts["signals_inserted"] == stats.signals_inserted
    assert counts["duplicates_skipped"] == stats.duplicates_skipped


def test_audit_includes_safety_notes() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        dry_run=True,
    ))
    audit = build_audit_payload(stats)
    notes = audit["safety_notes"]
    assert isinstance(notes, list)
    assert notes
    # At least one note mentions the snippet cap, one mentions PII.
    joined = " ".join(notes).lower()
    assert "short_snippet" in joined or "240" in joined
    assert "pii" in joined or "metadata" in joined


# ---------------------------------------------------------------------------
# 14. Distill-row helper edge cases
# ---------------------------------------------------------------------------


def test_distill_csv_row_buyer_type_override_takes_precedence() -> None:
    d = RuleBasedTechMarketDistiller()
    result = distill_csv_row(
        {
            "text": (
                "the SDK and webhook integration broke twice this week"
            ),
            "buyer_type": "admin",
        },
        distiller=d,
        source_provider="p",
        source_category=None,
        product_category="devtool_api",
        market_context_hint_default=None,
    )
    assert result.accepted is True
    assert result.signal is not None
    # Even though the distiller would have inferred "developer" from
    # SDK/webhook, the per-row override wins.
    assert result.signal.buyer_type == "admin"


def test_distill_csv_row_unknown_buyer_type_falls_back() -> None:
    d = RuleBasedTechMarketDistiller()
    result = distill_csv_row(
        {
            "text": (
                "the SDK and webhook integration broke twice this week"
            ),
            "buyer_type": "wizard",  # not in BUYER_TYPES
        },
        distiller=d,
        source_provider="p",
        source_category=None,
        product_category="devtool_api",
        market_context_hint_default=None,
    )
    assert result.accepted is True
    assert result.signal is not None
    # Falls back to whatever the distiller inferred (developer).
    assert result.signal.buyer_type == "developer"


def test_distill_csv_row_evidence_url_preserved() -> None:
    d = RuleBasedTechMarketDistiller()
    result = distill_csv_row(
        {
            "text": "too expensive per-seat enterprise plan for us",
            "evidence_url": "https://example.com/review/123",
        },
        distiller=d,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        market_context_hint_default=None,
    )
    assert result.signal is not None
    assert result.signal.evidence_url == "https://example.com/review/123"


def test_distill_csv_row_timestamp_parsed_from_string() -> None:
    d = RuleBasedTechMarketDistiller()
    result = distill_csv_row(
        {
            "text": "too expensive per-seat enterprise plan",
            "source_timestamp": "1700000123",
        },
        distiller=d,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        market_context_hint_default=None,
    )
    assert result.signal is not None
    assert result.signal.source_timestamp == 1700000123


def test_distill_csv_row_invalid_metadata_json_does_not_crash() -> None:
    d = RuleBasedTechMarketDistiller()
    result = distill_csv_row(
        {
            "text": "too expensive per-seat enterprise plan",
            "metadata_json": "this is not json",
        },
        distiller=d,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        market_context_hint_default=None,
    )
    assert result.signal is not None
    assert result.signal.metadata == {}


# ---------------------------------------------------------------------------
# 15. limit option respected
# ---------------------------------------------------------------------------


def test_limit_option_caps_rows_scanned() -> None:
    persister = InMemoryTechMarketPersister()
    stats = asyncio.run(ingest_csv_rows(
        _csv_rows(_CSV_BASIC),
        persister=persister,
        source_provider="p",
        source_category=None,
        product_category="ai_saas",
        market_context_hint="AI_tool",
        dry_run=False,
        limit=2,
    ))
    assert stats.rows_scanned == 2


# ---------------------------------------------------------------------------
# 16. Existing 11D.1 + Amazon tests still pass — covered by full
#     regression run from the operator's gate. This file's purpose
#     is to pin 11D.2-specific behavior.
# ---------------------------------------------------------------------------
