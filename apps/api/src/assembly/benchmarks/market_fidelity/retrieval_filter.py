"""Phase 17C — pre-outcome evidence / retrieval leakage filter.

Assembly CAN remove leakage from its retrieval/vector/RAG layer (it controls the
evidence bundle). This filter excludes — or weights to 0 — any source that post-dates
the prediction, post-dates the outcome, or visibly contains outcome values
(postmortems: "raised $X", "final backers", "successfully funded", "failed", …). It
emits an audit report. PURE functions; it does NOT touch production retrieval — it
operates on a supplied source list and is used only by the benchmark/offline path.

NOTE: this filters RETRIEVED evidence only. It CANNOT erase outcome knowledge baked
into a base model's pretrained weights — that is measured separately by the knowledge
probe (knowledge_probe.py) and bounded by the blindness tier (blindness.py).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from assembly.benchmarks.market_fidelity.canonicalize import canonical_bytes
from assembly.benchmarks.market_fidelity.hash_lock import sha256_hex

# Phrases that strongly indicate a source reveals the realized outcome.
_OUTCOME_PATTERNS = [
    r"\braised\s*\$?\s*[\d,.]+",
    r"\bfinal(?:ly)?\s+(?:raised|funded|pledged|backers?|total)\b",
    r"\b\d[\d,.]*\s+backers\b",
    r"\bsuccessfully\s+funded\b",
    r"\bfully\s+funded\b",
    r"\bfunded\s+at\s+\d",
    r"\b(?:campaign|project)\s+(?:succeeded|failed|was\s+cancell?ed)\b",
    r"\bhow\s+it\s+ended\b",
    r"\bpost-?mortem\b",
    r"\b(?:reached|hit|surpassed|exceeded)\s+(?:its\s+)?(?:goal|target)\b",
    r"\b\d+%\s+funded\b",
    r"\bclosed\s+(?:with|at)\s+\$?\s*[\d,.]+",
    r"\b(?:brought\s+in|pulled\s+in|netted|collected)\s+\$?\s*[\d,.]+",
    r"\bended\s+with\s+[\d,.]+\s+(?:backers?|supporters?|pledg)",
    r"\bgoal\s+(?:smashed|crushed|met|beaten)\b",
]
_OUTCOME_RE = re.compile("|".join(_OUTCOME_PATTERNS), re.IGNORECASE)


def _parse_instant(value: object) -> datetime | None:
    """Parse to a tz-aware UTC datetime. Returns None for anything missing, non-ISO,
    whitespace-corrupted, or coarser than a full date (e.g. '2024' / '2024-03') — so
    the comparison fails CLOSED (an unparseable/coarse timestamp is treated as
    'unknown', hence excluded) rather than lexically slipping past the outcome."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def scan_outcome_text(text: str, flagged_outcome_values: Sequence[str] = ()) -> list[str]:
    """Best-effort content scan for outcome-revealing language / flagged values in an
    arbitrary text blob (e.g. the model-facing structured bundle fields, not just
    evidence excerpts). Returns a list of leak reasons (empty == clean). The regex is a
    heuristic — the robust guard is temporal — but this catches the obvious postmortem
    phrasings + any explicitly flagged outcome value."""
    why: list[str] = []
    if text and _OUTCOME_RE.search(text):
        why.append("text matches an outcome-reveal pattern (postmortem / final tally)")
    ntext = _norm(text)
    for fv in flagged_outcome_values:
        if fv and _norm(fv) in ntext:
            why.append("text contains a flagged outcome value")
            break
    return why


def _source_instant(src: Mapping) -> datetime | None:
    """Best available source instant (published > archived > retrieved), tz-aware UTC."""
    for k in ("published_at", "archived_at", "retrieved_at"):
        dt = _parse_instant(src.get(k))
        if dt is not None:
            return dt
    return None


def _norm(text: str) -> str:
    """Casefold + collapse whitespace, for robust flagged-value substring matching."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def filter_pre_outcome_evidence(
    *,
    case_id: str,
    prediction_timestamp: str,
    outcome_date: str,
    sources: Sequence[Mapping],
    flagged_outcome_values: Sequence[str] = (),
) -> dict:
    """Partition ``sources`` into approved (pre-outcome) vs excluded, set
    retrieval_weight=0 for excluded sources, and emit an audit report with a
    deterministic ``evidence_bundle_hash`` over the APPROVED bundle.

    A source is EXCLUDED if any of: it has no parseable timestamp; its timestamp is at/
    after the prediction_timestamp; its timestamp is at/after the outcome_date; its
    text matches an outcome-reveal pattern (best-effort heuristic — the *robust* guard
    is the temporal one); or its text contains a flagged outcome value. If either
    anchor (prediction_timestamp / outcome_date) is missing or unparseable, the filter
    FAILS CLOSED and excludes EVERY source."""
    pred_dt = _parse_instant(prediction_timestamp)
    out_dt = _parse_instant(outcome_date)
    anchors_ok = pred_dt is not None and out_dt is not None
    approved: list[str] = []
    excluded: list[str] = []
    reasons: dict[str, list[str]] = {}
    weights: dict[str, float] = {}
    approved_bundle: list[dict] = []

    flagged = [_norm(f) for f in flagged_outcome_values if f]

    for i, src in enumerate(sources):
        sid = str(src.get("id") or src.get("url") or f"source_{i}")
        why: list[str] = []
        text = str(src.get("text") or src.get("snippet") or "")

        if not anchors_ok:
            why.append("prediction_timestamp or outcome_date is missing/unparseable — failing closed")
        else:
            src_dt = _source_instant(src)
            if src_dt is None:
                why.append("no parseable source timestamp (published_at/archived_at/retrieved_at)")
            else:
                if src_dt >= pred_dt:
                    why.append(f"source time {src_dt.isoformat()} is at/after prediction_timestamp")
                if src_dt >= out_dt:
                    why.append(f"source time {src_dt.isoformat()} is at/after outcome_date")
        if text and _OUTCOME_RE.search(text):
            why.append("text matches an outcome-reveal pattern (postmortem / final tally)")
        ntext = _norm(text)
        for fv in flagged:
            if fv in ntext:
                why.append("text contains a flagged outcome value")
                break

        if why:
            excluded.append(sid)
            reasons[sid] = why
            weights[sid] = 0.0
        else:
            approved.append(sid)
            weights[sid] = float(src.get("retrieval_weight", 1.0))
            approved_bundle.append(
                {"id": sid, **{k: src.get(k) for k in ("url", "published_at", "archived_at", "retrieved_at")}}
            )

    evidence_bundle_hash = sha256_hex(canonical_bytes({"case_id": case_id, "approved": approved_bundle}))

    return {
        "case_id": case_id,
        "prediction_timestamp": prediction_timestamp,
        "outcome_date": outcome_date,
        "approved_source_ids": approved,
        "excluded_source_ids": excluded,
        "exclusion_reasons": reasons,
        "retrieval_weight_overrides": weights,
        "n_sources": len(list(sources)),
        "n_excluded": len(excluded),
        "evidence_bundle_hash": evidence_bundle_hash,
    }
