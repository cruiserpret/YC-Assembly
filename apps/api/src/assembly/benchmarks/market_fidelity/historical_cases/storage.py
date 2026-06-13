"""Phase 17D — historical case-pack storage (audit-only; never validation data).

Packs live under ``apps/api/benchmarks/market_fidelity/historical_case_packs/`` in a
status subdir (accepted / rejected / case_study_only -> 'rejected'? no: 'case_study'
folder / candidates). Writing is opt-in (``allow_write``); dry-run writes nothing. These
files are NEVER loaded by the validation ledger. Pure filesystem.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from assembly.benchmarks.market_fidelity.historical_cases.input_bundle import InputBundle
from assembly.benchmarks.market_fidelity.historical_cases.outcome_record import OutcomeRecord
from assembly.benchmarks.market_fidelity.historical_cases.pack_builder import CasePackReport

_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

_STATUS_DIR = {
    "accepted": "accepted",
    "rejected": "rejected",
    "candidate": "candidates",
    "case_study_only": "rejected",  # case studies are kept out of the accepted accuracy set
}


def default_packs_dir() -> Path:
    """``apps/api/benchmarks/market_fidelity/historical_case_packs/`` — audit-only."""
    api_root = Path(__file__).resolve().parents[5]
    return api_root / "benchmarks" / "market_fidelity" / "historical_case_packs"


def write_case_pack(
    report: CasePackReport,
    input_bundle: InputBundle,
    outcome_record: OutcomeRecord,
    *,
    allow_write: bool = False,
    packs_dir: str | Path | None = None,
) -> Path | None:
    """Persist a pack (metadata + input bundle + outcome record + audits) under its
    status subdir ONLY when ``allow_write=True``. Refuses to overwrite (immutable)."""
    if not allow_write:
        return None
    case_id = report.pack.case_id
    # Reject anything that isn't a safe slug — case_id is interpolated into the write
    # path, so '..' / '/' / absolute paths must never reach the filesystem.
    if not _SAFE_CASE_ID.match(case_id):
        raise ValueError(f"unsafe case_id {case_id!r} (must match [A-Za-z0-9_-]+) — refusing to write")
    base = (Path(packs_dir) if packs_dir is not None else default_packs_dir()).resolve()
    sub = (base / _STATUS_DIR.get(report.pack.case_status, "candidates") / case_id).resolve()
    if not str(sub).startswith(str(base)):
        raise ValueError("resolved pack path escapes the packs dir — refusing to write")
    if sub.exists():
        raise ValueError(f"case-pack dir already exists (immutable): {sub}")
    sub.mkdir(parents=True, exist_ok=False)
    (sub / "case_pack.json").write_text(json.dumps(report.pack.model_dump(mode="json"), indent=2) + "\n")
    (sub / "input_bundle.json").write_text(json.dumps(input_bundle.model_dump(mode="json"), indent=2) + "\n")
    (sub / "outcome_record.json").write_text(json.dumps(outcome_record.model_dump(mode="json"), indent=2) + "\n")
    (sub / "source_manifest.json").write_text(json.dumps(report.source_manifest, indent=2) + "\n")
    (sub / "leakage_audit.json").write_text(json.dumps(report.leakage_audit, indent=2) + "\n")
    (sub / "eligibility_report.json").write_text(json.dumps(report.eligibility, indent=2) + "\n")
    return sub
