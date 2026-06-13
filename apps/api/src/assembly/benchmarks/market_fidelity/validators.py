"""Phase 17B — validation + leakage / live-call guards for the harness.

Composes the schema validator, the disabled-provider guard, and a pre-outcome
leakage check (search-assisted sources must predate the lock). Pure; no network.
"""
from __future__ import annotations

from datetime import UTC, datetime

from assembly.benchmarks.market_fidelity.providers import assert_live_calls_disabled
from assembly.benchmarks.market_fidelity.schema import BenchmarkPrediction, validate_prediction


def _parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string to a tz-aware UTC datetime (naive -> assumed UTC).
    Returns None if missing/unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)

# Modes that DO NOT and CANNOT make a paid/live provider call in Phase 17B.
SAFE_MODES = ("manual_output", "dry_run", "naive")


def validate_prediction_payload(payload: dict) -> BenchmarkPrediction:
    """Schema-validate a prediction (raises on bucket-sum / range / missing fields)."""
    return validate_prediction(payload)


def assert_mode_is_offline(mode: str) -> None:
    """Refuse any mode that would imply a live provider call in Phase 17B."""
    assert_live_calls_disabled()
    if mode == "future_provider_call":
        raise RuntimeError(
            "mode 'future_provider_call' is NOT runnable in Phase 17B — live "
            "provider baselines arrive in Phase 17B-L behind an explicit flag + cost gate"
        )
    if mode not in SAFE_MODES:
        raise RuntimeError(f"unknown/unsupported lock mode {mode!r} (allowed: {SAFE_MODES})")


def check_no_post_lock_sources(input_bundle: dict, locked_at: str) -> list[str]:
    """Leakage guard: every search-assisted source must have been retrieved STRICTLY
    BEFORE the lock instant. Compares full tz-aware datetimes (not calendar dates), so
    a same-day-but-later or timezone-shifted source is caught. Returns a list of
    leakage issues (empty == clean). A missing/unparseable ``retrieved_at`` (or an
    unparseable ``locked_at``) is itself flagged — it cannot be verified clean.
    Sources live under ``input_bundle['sources']`` as objects with ``retrieved_at``."""
    issues: list[str] = []
    lock_dt = _parse_utc(locked_at)
    if lock_dt is None:
        return ["locked_at is missing or unparseable — cannot verify pre-outcome leakage"]
    for s in input_bundle.get("sources", []) or []:
        if not isinstance(s, dict):
            continue
        ident = s.get("url") or s.get("id") or "?"
        src_dt = _parse_utc(s.get("retrieved_at"))
        if src_dt is None:
            issues.append(
                f"source {ident} has a missing/unparseable retrieved_at — cannot verify "
                "it predates the lock"
            )
            continue
        if src_dt >= lock_dt:
            issues.append(
                f"source {ident} retrieved_at {s.get('retrieved_at')} is AT/AFTER lock "
                f"{locked_at} — post-lock evidence is leakage"
            )
    return issues
