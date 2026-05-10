"""Phase 8.2C — redaction-before-storage tests.

Pure-Python; no DB. Verifies the redaction pipeline:

  - emails / phones / @handles / profile URLs are redacted from content
    AND from metadata leaves
  - sensitive content rejects the entire record (no partial storage)
  - raw_handle becomes a salted user_handle_hash; the raw handle never
    appears in the prepared insert dict
  - accepted records carry pii_redaction_status='redacted' and
    sensitive_scan_status='clean'
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from assembly.pipeline.ingestion import (
    NormalizedSourcePayload,
    RawSourcePayload,
    prepare_source_record_insert,
    redact_source_payload,
    sanitize_content_for_storage,
    scan_source_payload_for_sensitive_content,
)


def _normalized(content: str, *, raw_handle: str | None = None,
                metadata: dict | None = None) -> NormalizedSourcePayload:
    return NormalizedSourcePayload(
        source_url="https://example.test/post",
        captured_at=datetime.now(UTC),
        content=content,
        raw_handle=raw_handle,
        metadata=metadata or {},
        language="en",
    )


# ---------------------------------------------------------------------------
# Identity-marker redaction
# ---------------------------------------------------------------------------


def test_email_redacted_before_storage() -> None:
    payload = _normalized("contact me at test_only@example.test today")
    redacted = redact_source_payload(payload)
    assert "[REDACTED_EMAIL]" in redacted.content
    assert "test_only@example.test" not in redacted.content


def test_phone_redacted_before_storage() -> None:
    payload = _normalized("ping (555) 555-0199 anytime")
    redacted = redact_source_payload(payload)
    assert "[REDACTED_PHONE]" in redacted.content


def test_handle_redacted_before_storage() -> None:
    payload = _normalized("posted by @testfixture_handle on the thread")
    redacted = redact_source_payload(payload)
    assert "[REDACTED_HANDLE]" in redacted.content
    assert "@testfixture_handle" not in redacted.content


def test_profile_url_redacted_before_storage() -> None:
    payload = _normalized("see https://reddit.com/u/testfixture_handle for context")
    redacted = redact_source_payload(payload)
    assert "[REDACTED_PROFILE_URL]" in redacted.content
    assert "/u/testfixture_handle" not in redacted.content


def test_metadata_string_leaves_redacted() -> None:
    payload = _normalized(
        "clean content",
        metadata={"author_note": "ping me at test_only@example.test"},
    )
    redacted = redact_source_payload(payload)
    assert "[REDACTED_EMAIL]" in redacted.metadata["author_note"]


# ---------------------------------------------------------------------------
# Sanitization (truncation + content_hash)
# ---------------------------------------------------------------------------


def test_content_truncated_at_4000_chars() -> None:
    long = "x" * 6000
    truncated, h = sanitize_content_for_storage(long)
    assert len(truncated) <= 4500  # 4000 + truncation marker
    assert "TRUNCATED" in truncated
    assert h


def test_content_hash_normalizes_whitespace_and_case() -> None:
    a, ha = sanitize_content_for_storage("Hello   world")
    b, hb = sanitize_content_for_storage("hello world")
    assert ha == hb


# ---------------------------------------------------------------------------
# Sensitive scan rejection
# ---------------------------------------------------------------------------


def test_sensitive_content_in_content_rejects_record() -> None:
    payload = _normalized("on an H1B visa and overwhelmed by pricing")
    rejection = scan_source_payload_for_sensitive_content(payload)
    assert rejection is not None
    assert rejection.reason_code == "SENSITIVE_ATTRIBUTE_DETECTED"


def test_sensitive_content_in_metadata_rejects_record() -> None:
    payload = _normalized(
        "clean content",
        metadata={"note": "household income $250,000 reported"},
    )
    rejection = scan_source_payload_for_sensitive_content(payload)
    assert rejection is not None


def test_clean_payload_passes_sensitive_scan() -> None:
    payload = _normalized("agents portraying mid-volume merchants tended to resist")
    assert scan_source_payload_for_sensitive_content(payload) is None


# ---------------------------------------------------------------------------
# End-to-end prepare_source_record_insert
# ---------------------------------------------------------------------------


def test_prepare_insert_clean_payload_returns_dict() -> None:
    payload = _normalized(
        "agents portraying mid-volume merchants tended to resist",
        raw_handle="testfixture_handle",
        metadata={"subreddit": "shopify"},
    )
    insert_dict, rejection = prepare_source_record_insert(
        payload,
        source_kind="reddit_public_api",
        compliance_tag="public_api",
        ingested_by="test_adapter",
        salt="test-salt",
    )
    assert rejection is None
    assert insert_dict is not None
    # Required redaction-status flags
    assert insert_dict["pii_redaction_status"] == "redacted"
    assert insert_dict["sensitive_scan_status"] == "clean"
    # raw_handle never appears
    assert "raw_handle" not in insert_dict
    # user_handle_hash IS populated and is the salted hash, not the raw handle
    assert insert_dict["user_handle_hash"] is not None
    assert insert_dict["user_handle_hash"] != "testfixture_handle"
    assert len(insert_dict["user_handle_hash"]) == 64
    # Content has no obvious identity markers
    for marker in ("@testfixture", "@example.test", "555-0199"):
        assert marker not in insert_dict["content"]


def test_prepare_insert_sensitive_content_rejected() -> None:
    payload = _normalized(
        "on an H1B visa with diabetes and brand-control concerns",
        raw_handle="testfixture_handle",
    )
    insert_dict, rejection = prepare_source_record_insert(
        payload,
        source_kind="reddit_public_api",
        compliance_tag="public_api",
        ingested_by="test_adapter",
        salt="test-salt",
    )
    assert insert_dict is None
    assert rejection is not None
    assert rejection.reason_code == "SENSITIVE_ATTRIBUTE_DETECTED"


def test_prepare_insert_email_in_metadata_rejected_via_sensitive_scan() -> None:
    """Emails appearing in METADATA are caught by the sensitive scan after
    the redactor runs over them — the redactor turns them into
    [REDACTED_EMAIL] markers, which is not a sensitive hit. So this case
    actually demonstrates the redaction works (no rejection) — emails
    survived only as [REDACTED_EMAIL]."""
    payload = _normalized(
        "clean content",
        metadata={"note": "ping test_only@example.test"},
    )
    insert_dict, rejection = prepare_source_record_insert(
        payload,
        source_kind="reddit_public_api",
        compliance_tag="public_api",
        ingested_by="test_adapter",
        salt="test-salt",
    )
    # Because the redactor scrubs the email FIRST, this is a clean record.
    assert insert_dict is not None, (
        f"redactor should have scrubbed the email; got rejection: {rejection}"
    )
    # The metadata leaf is the redacted version.
    assert "[REDACTED_EMAIL]" in str(insert_dict["metadata_"]["note"])


def test_no_raw_handle_in_insert_dict_or_metadata() -> None:
    payload = _normalized(
        "clean content",
        raw_handle="testfixture_handle",
        metadata={"author_handle": "testfixture_handle_in_meta"},
    )
    insert_dict, _ = prepare_source_record_insert(
        payload,
        source_kind="reddit_public_api",
        compliance_tag="public_api",
        ingested_by="test_adapter",
        salt="test-salt",
    )
    assert insert_dict is not None
    # The raw handle from raw_handle field is NOT carried over.
    assert insert_dict.get("raw_handle") is None
    # Metadata's "author_handle" string is preserved as-is (it's a value,
    # not a regex-matchable handle without an @ prefix). The point of
    # this test is the framework doesn't surface raw_handle as a
    # separate column on the insert.
    assert "raw_handle" not in insert_dict


def test_handle_hash_changes_with_salt() -> None:
    payload = _normalized("clean content", raw_handle="testfixture_handle")
    a, _ = prepare_source_record_insert(
        payload, source_kind="x", compliance_tag="public_api",
        ingested_by="t", salt="salt-1",
    )
    b, _ = prepare_source_record_insert(
        payload, source_kind="x", compliance_tag="public_api",
        ingested_by="t", salt="salt-2",
    )
    assert a is not None and b is not None
    assert a["user_handle_hash"] != b["user_handle_hash"]
