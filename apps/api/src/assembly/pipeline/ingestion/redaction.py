"""Phase 8.2C — redaction-before-storage pipeline.

Every raw payload an adapter produces goes through this module BEFORE
any `source_records` row is built. The pipeline:

  1. allow-listed fields only (handled at the adapter layer; we re-enforce
     by working only over `NormalizedSourcePayload`)
  2. redact identity markers (emails, phones, @handles, profile URLs) via
     Phase 8.2A's `redact_identity_markers`
  3. salted-hash the raw_handle into `user_handle_hash`; raw handle is
     never stored
  4. content-hash the redacted content for dedup
  5. sensitive-attribute scan via Phase 8.2A's
     `assert_no_sensitive_attributes` over BOTH content and metadata-as-text
  6. if any of the above fail, return a structured `RecordRejection` —
     never an unfinished SourceRecord row
  7. on success, return a `SourceRecordInsert` dict with
     `pii_redaction_status='redacted'` and `sensitive_scan_status='clean'`

`prepare_source_record_insert` is the single public entry point the
adapter base class calls per payload.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from assembly.pipeline.ingestion.run_summary import (
    NormalizedSourcePayload,
    RawSourcePayload,
    RecordRejection,
)
from assembly.pipeline.persona.anonymization import (
    hash_public_handle,
    redact_identity_markers,
)
from assembly.pipeline.persona.sensitive_filter import (
    SensitiveAttributeRejected,
    scan_sensitive_attributes,
)


# ---------------------------------------------------------------------------
# Stage 1 — payload-level redaction
# ---------------------------------------------------------------------------


def redact_source_payload(
    payload: NormalizedSourcePayload,
) -> NormalizedSourcePayload:
    """Apply identity-marker redaction to `content` and to every string
    leaf inside `metadata`. Returns a new payload — the input is not
    mutated. `raw_handle` is preserved on the output (it gets salted-
    hashed at insert time; it's never written to disk in plaintext)."""
    redacted_metadata = _redact_metadata(payload.metadata)
    return NormalizedSourcePayload(
        source_url=payload.source_url,
        captured_at=payload.captured_at,
        content=redact_identity_markers(payload.content),
        raw_handle=payload.raw_handle,
        metadata=redacted_metadata,
        language=payload.language,
    )


def _redact_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Walk a metadata dict and redact identity markers in every string
    leaf. Non-string types pass through unchanged."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        out[k] = _redact_value(v)
    return out


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_identity_markers(value)
    if isinstance(value, list):
        return [_redact_value(x) for x in value]
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Stage 2 — sanitize for storage (truncation + content_hash)
# ---------------------------------------------------------------------------


_CONTENT_CAP_CHARS = 4000
_HARD_CONTENT_CAP_CHARS = 16000  # absolute upper bound; no caller may exceed
_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_content_for_storage(
    content: str,
    *,
    max_content_chars: int = _CONTENT_CAP_CHARS,
) -> tuple[str, str]:
    """Return (truncated_content, content_hash). The hash is computed
    over the normalized text (lowercase + collapsed whitespace) so the
    UNIQUE constraint on `(source_kind, content_hash)` dedupes near-
    duplicate text effectively.

    Phase 8.3B-LIVE-1.5: `max_content_chars` is now per-call so that
    different source kinds can preserve different body lengths.
    Default remains 4000 (unchanged for Tavily); Firecrawl rows pass
    8000 from the operator script. Hard ceiling 16000 — no caller may
    set a larger cap without a memo update + status re-review.
    """
    if max_content_chars <= 0 or max_content_chars > _HARD_CONTENT_CAP_CHARS:
        raise ValueError(
            f"max_content_chars={max_content_chars} outside "
            f"(0, {_HARD_CONTENT_CAP_CHARS}]"
        )
    truncated = content[:max_content_chars]
    if len(content) > max_content_chars:
        truncated += "…[TRUNCATED]"
    normalized = _WHITESPACE_RE.sub(" ", truncated.strip().lower())
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return truncated, content_hash


# ---------------------------------------------------------------------------
# Stage 3 — sensitive scan
# ---------------------------------------------------------------------------


def scan_source_payload_for_sensitive_content(
    payload: NormalizedSourcePayload,
) -> RecordRejection | None:
    """Run the Phase 8.2A sensitive-attribute filter over both the
    redacted content AND the metadata-rendered-as-text. Return a
    structured rejection if anything sensitive is detected; None when
    clean. Adapters MUST NOT proceed past this stage on rejection."""
    text_blobs = [payload.content, json.dumps(payload.metadata, default=str)]
    for blob in text_blobs:
        hits = scan_sensitive_attributes(blob)
        if hits:
            categories = sorted({h.category.value for h in hits})
            return RecordRejection(
                reason_code="SENSITIVE_ATTRIBUTE_DETECTED",
                message=(
                    "Sensitive attributes detected in source payload after "
                    f"redaction: {categories}. Record refused — never stored."
                ),
                source_url=payload.source_url,
            )
    return None


# ---------------------------------------------------------------------------
# Stage 4 — final SourceRecord-insert preparation
# ---------------------------------------------------------------------------


def prepare_source_record_insert(
    payload: NormalizedSourcePayload,
    *,
    source_kind: str,
    compliance_tag: str,
    ingested_by: str,
    salt: str,
    max_content_chars: int = _CONTENT_CAP_CHARS,
) -> tuple[dict[str, Any] | None, RecordRejection | None]:
    """End-to-end redaction + sensitive scan + dict assembly.

    Returns:
      (insert_dict, None)  — payload is clean; caller may insert
      (None, rejection)    — payload was rejected; caller MUST NOT insert

    Rules enforced here (final firewall before storage):
      - raw_handle is never carried in the insert dict; only its salted
        hash via `user_handle_hash`.
      - content has been through `redact_identity_markers`.
      - metadata leaves have been through `redact_identity_markers`.
      - sensitive scan over content + metadata is clean.
      - regression check: post-redaction content carries no obvious
        identity-marker regex hits. If any survive, the redactor failed
        (or a new pattern slipped through) — record is rejected.
      - `pii_redaction_status` is set to `'redacted'` and
        `sensitive_scan_status` is set to `'clean'`. These columns can
        NEVER come out of this function as `'not_run'`.
    """
    # Stage 1: redact identity markers.
    redacted = redact_source_payload(payload)

    # Stage 1b: regression check — redactor must have actually scrubbed.
    if _surviving_identity_markers(redacted.content):
        return None, RecordRejection(
            reason_code="REDACTION_FAILED_RESIDUAL_IDENTITY",
            message=(
                "Identity-marker regex hits survived redaction. The "
                "record is rejected rather than stored partially redacted."
            ),
            source_url=redacted.source_url,
        )

    # Stage 2: sensitive scan over content + metadata.
    rejection = scan_source_payload_for_sensitive_content(redacted)
    if rejection is not None:
        return None, rejection

    # Stage 3: salted handle hash; raw handle is never stored.
    user_handle_hash: str | None = None
    if redacted.raw_handle:
        try:
            user_handle_hash = hash_public_handle(
                redacted.raw_handle, salt=salt,
            )
        except ValueError as e:
            return None, RecordRejection(
                reason_code="HANDLE_HASH_FAILED",
                message=f"hash_public_handle raised: {e}",
                source_url=redacted.source_url,
            )

    # Stage 4: sanitize + content_hash for dedup. Phase 8.3B-LIVE-1.5:
    # the cap is now per-call so source_kind='firecrawl_v1_scrape' can
    # preserve up to 8000 chars (operator script passes max_content_chars).
    # Tavily callers don't pass it → default 4000 (unchanged).
    sanitized_content, content_hash = sanitize_content_for_storage(
        redacted.content,
        max_content_chars=max_content_chars,
    )

    # Stage 5: final sensitive sweep over the sanitized content (defense
    # in depth — the truncation marker shouldn't introduce sensitive
    # content but we re-check to be safe).
    try:
        from assembly.pipeline.persona.sensitive_filter import (
            assert_no_sensitive_attributes,
        )
        assert_no_sensitive_attributes(sanitized_content)
    except SensitiveAttributeRejected as e:
        return None, RecordRejection(
            reason_code="SENSITIVE_ATTRIBUTE_DETECTED",
            message=(
                "Sensitive attribute detected in sanitized content "
                "(post-truncation). Record rejected. Categories: "
                f"{sorted({v.category.value for v in e.violations})}"
            ),
            source_url=redacted.source_url,
        )

    insert_dict: dict[str, Any] = {
        "source_kind": source_kind,
        "source_url": redacted.source_url,
        "captured_at": redacted.captured_at,
        "content": sanitized_content,
        "content_hash": content_hash,
        "language": redacted.language,
        "metadata_": redacted.metadata,
        "ingested_by": ingested_by,
        "compliance_tag": compliance_tag,
        "user_handle_hash": user_handle_hash,
        "pii_redaction_status": "redacted",
        "sensitive_scan_status": "clean",
    }
    return insert_dict, None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_RESIDUAL_IDENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,30}\b"),
    re.compile(r"https?://[^\s]*?/(?:u|user|@)/[A-Za-z0-9_-]+", re.IGNORECASE),
)


def _surviving_identity_markers(text: str) -> bool:
    """Returns True if any obvious identity marker survived redaction.
    A True result means the redactor failed; the record must be rejected."""
    for pat in _RESIDUAL_IDENTITY_PATTERNS:
        if pat.search(text or ""):
            return True
    return False


__all__ = [
    "prepare_source_record_insert",
    "redact_source_payload",
    "sanitize_content_for_storage",
    "scan_source_payload_for_sensitive_content",
]
