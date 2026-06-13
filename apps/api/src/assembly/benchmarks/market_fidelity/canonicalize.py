"""Phase 17B — deterministic canonical JSON for hash-locking.

The same logical object must always serialize to identical bytes so a prediction
hash is reproducible and an immutable lock is verifiable. We use sorted keys,
compact separators, UTF-8, no NaN/Inf, and FIXED float formatting (rounded to a
stable precision) — an RFC-8785-style canonicalization sufficient for committing
forecasts. Pure stdlib.
"""
from __future__ import annotations

import json

# Fixed precision so float repr differences never change the hash.
FLOAT_PRECISION = 6


def _normalize(obj: object) -> object:
    """Recursively round floats to FLOAT_PRECISION and pass everything else through
    unchanged, so the canonical bytes are stable across runs/platforms. Rejects
    NaN/Inf implicitly (json.dumps allow_nan=False)."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        # round to fixed precision; normalize -0.0 to 0.0
        r = round(obj, FLOAT_PRECISION)
        return 0.0 if r == 0.0 else r
    if isinstance(obj, dict):
        return {str(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    return obj


def canonical_json(obj: object) -> str:
    """Deterministic JSON string: sorted keys, compact, fixed float precision."""
    return json.dumps(
        _normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(obj: object) -> bytes:
    return canonical_json(obj).encode("utf-8")
