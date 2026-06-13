"""Phase 17C — open-weight blind-backtest AUDIT records (audit-only).

One immutable record per (case, baseline) capturing the full blindness/contamination
provenance: model metadata, blindness tier, contamination checks, the retrieval-filter
report, the knowledge-probe report, hashes, and the public-claim eligibility. Stored
UNDER ``apps/api/benchmarks/market_fidelity/backtest_audits/`` — NOT ``validation_cases/``
— and NEVER loaded as a validation case. Pure data + filesystem; observed-free.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

AUDIT_PURPOSE = "benchmark_backtest_audit_not_validation_data"


class BacktestAuditRecord(BaseModel):
    """Immutable blind-backtest audit record. ``extra='forbid'``; observed-free;
    ``frozen=True`` so the observed-free / purpose guarantees can't be mutated after
    construction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: Literal["benchmark_backtest_audit_not_validation_data"] = AUDIT_PURPOSE
    case_id: str
    baseline_record_id: str
    paired_record_id: str | None = None
    model_metadata: dict
    blindness_tier: int
    contamination_checks: dict
    retrieval_filter_report: dict | None = None
    knowledge_probe_report: dict | None = None
    input_bundle_hash: str
    prediction_hash: str | None = None
    eligible_for_public_claim: bool
    reasons_if_not_eligible: list[str] = []
    # The outcome is NEVER written here at audit/lock time.
    observed: None = None


def default_audits_dir() -> Path:
    """``apps/api/benchmarks/market_fidelity/backtest_audits/`` — audit-only; absent
    from validation_cases/manifest.json."""
    api_root = Path(__file__).resolve().parents[4]
    return api_root / "benchmarks" / "market_fidelity" / "backtest_audits"


def _filename(record: BacktestAuditRecord) -> str:
    def safe(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)
    return f"{safe(record.case_id)}__{safe(record.baseline_record_id)}.json"


def write_audit_record(
    record: BacktestAuditRecord, *, allow_write: bool = False, audits_dir: str | Path | None = None
) -> Path | None:
    """Persist ONLY when ``allow_write=True`` (default writes nothing). Writes solely
    under the audit dir; refuses to overwrite (records are immutable)."""
    if not allow_write:
        return None
    base = Path(audits_dir) if audits_dir is not None else default_audits_dir()
    base.mkdir(parents=True, exist_ok=True)
    out = base / _filename(record)
    if out.exists():
        raise ValueError(f"audit record already exists (immutable): {out.name}")
    out.write_text(json.dumps(record.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    return out


def load_audit_records(audits_dir: str | Path | None = None) -> list[BacktestAuditRecord]:
    base = Path(audits_dir) if audits_dir is not None else default_audits_dir()
    if not base.exists():
        return []
    return [
        BacktestAuditRecord.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(base.glob("*.json"))
    ]
