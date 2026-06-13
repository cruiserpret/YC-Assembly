"""Phase 17B — input-bundle + prediction hash-locking (commit-then-reveal).

A benchmark prediction is committed by hashing BOTH the frozen input bundle (the
identical evidence every method sees) AND the locked prediction payload, before
the outcome is knowable. The digest is deterministic (canonical JSON + SHA-256),
so the lock self-reproduces and cannot be edited after the fact. Pure stdlib.
"""
from __future__ import annotations

import hashlib

from assembly.benchmarks.market_fidelity.canonicalize import canonical_bytes
from assembly.benchmarks.market_fidelity.schema import BENCHMARK_SCHEMA_VERSION


def sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def input_bundle_hash(input_bundle: dict) -> str:
    """SHA-256 over the canonical bytes of the FROZEN shared evidence/input bundle.
    Every method in a case must be locked against the SAME input_bundle_hash."""
    return sha256_hex(canonical_bytes(input_bundle))


def compute_prediction_hash(
    *,
    method_id: str,
    method_version: str,
    input_bundle_hash: str,
    prediction_payload: dict,
    locked_at: str,
    benchmark_schema_version: str = BENCHMARK_SCHEMA_VERSION,
) -> str:
    """Deterministic prediction hash. ``locked_at`` is an explicit part of the lock
    payload (not a hidden wall-clock), so the hash reproduces exactly from the same
    inputs. Changing any field — the prediction, the input bundle, the model
    id/version, or locked_at — changes the hash."""
    payload = {
        "benchmark_schema_version": benchmark_schema_version,
        "method_id": method_id,
        "method_version": method_version,
        "input_bundle_hash": input_bundle_hash,
        "prediction_payload": prediction_payload,
        "locked_at": locked_at,
    }
    return sha256_hex(canonical_bytes(payload))
