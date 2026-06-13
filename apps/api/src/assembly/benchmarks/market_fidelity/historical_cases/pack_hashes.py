"""Phase 17D — deterministic case-pack hashes (reuses the 17B canonicalize/hash-lock).

Hashes the input bundle, the outcome record, the source manifest, and the whole pack.
Any change to the evidence or the outcome changes the relevant hash. Pure stdlib.
"""
from __future__ import annotations

from pydantic import BaseModel

from assembly.benchmarks.market_fidelity.canonicalize import canonical_bytes
from assembly.benchmarks.market_fidelity.hash_lock import sha256_hex


def _payload(obj: object) -> object:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_none=True)
    return obj


def hash_obj(obj: object) -> str:
    """SHA-256 over the canonical JSON of a pydantic model or plain JSON value."""
    return sha256_hex(canonical_bytes(_payload(obj)))


def input_bundle_hash(bundle: object) -> str:
    return hash_obj(bundle)


def outcome_record_hash(outcome: object) -> str:
    return hash_obj(outcome)


def source_manifest_hash(manifest: object) -> str:
    return hash_obj(manifest)


def full_case_pack_hash(
    *, input_bundle_hash: str, outcome_record_hash: str, source_manifest_hash: str, case_id: str
) -> str:
    """A single digest binding the three component hashes + case id together."""
    return sha256_hex(canonical_bytes({
        "case_id": case_id,
        "input_bundle_hash": input_bundle_hash,
        "outcome_record_hash": outcome_record_hash,
        "source_manifest_hash": source_manifest_hash,
    }))
