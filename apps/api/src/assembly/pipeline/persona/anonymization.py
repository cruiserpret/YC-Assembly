"""Phase 8.2A — anonymization utilities.

Three pieces:

  - generate_display_name(seed) — deterministic random fictional name from a
    curated pool. Same seed → same name. Different seeds → different name
    almost certainly. The pool is fictional and explicitly NOT sourced
    from any ingestion content.

  - hash_public_handle(raw_handle, salt) — salted sha256. The raw handle
    is never stored; this hash is internal-only (debug surface), never
    exposed in user-facing responses.

  - redact_identity_markers(text) — first-pass scrubber for emails, phone
    numbers, obvious @handles, and URL identity paths. NOT a complete PII
    scrubber — Phase 8.2B layers a richer redactor on top of this.

These utilities exist so future ingestion code has a stable, tested
boundary for converting raw public sources into anonymous persona
records. They do not call the network.
"""
from __future__ import annotations

import hashlib
import re
import struct
from typing import Final
from uuid import UUID

from assembly.pipeline.persona.constants import (
    DISPLAY_NAME_FIRSTS,
    DISPLAY_NAME_LAST_INITIALS,
)


# ---------------------------------------------------------------------------
# Display names
# ---------------------------------------------------------------------------


def generate_display_name(seed: str | UUID) -> str:
    """Return a fictional "First L." display name from a curated pool.

    Deterministic given the seed: same seed → same name. Different seeds
    spread across the pool. The pool is explicitly fictional — no ingestion
    content contributes to it. A test asserts that no name in the pool
    appears in any source content seeded by the test fixtures.
    """
    seed_str = str(seed)
    digest = hashlib.sha256(seed_str.encode("utf-8")).digest()
    # First 4 bytes pick the first name; next 4 bytes pick the last initial.
    first_idx, = struct.unpack_from("<I", digest, 0)
    last_idx, = struct.unpack_from("<I", digest, 4)
    first = DISPLAY_NAME_FIRSTS[first_idx % len(DISPLAY_NAME_FIRSTS)]
    last_initial = DISPLAY_NAME_LAST_INITIALS[
        last_idx % len(DISPLAY_NAME_LAST_INITIALS)
    ]
    return f"{first} {last_initial}."


# ---------------------------------------------------------------------------
# Salted handle hashing
# ---------------------------------------------------------------------------


def hash_public_handle(raw_handle: str, *, salt: str) -> str:
    """Salted SHA-256 of a public handle. Stored in
    `source_records.user_handle_hash` for source de-duplication. Never
    exposed in user-facing API responses.

    The salt rotates per ingestion run (see future ingestion code) so the
    same handle produces different hashes across runs — preventing the
    hash itself from becoming a stable cross-time identifier.
    """
    if not raw_handle:
        raise ValueError("hash_public_handle: raw_handle must not be empty")
    if not salt:
        raise ValueError("hash_public_handle: salt must not be empty")
    return hashlib.sha256((salt + "|" + raw_handle).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Identity redaction (first pass)
# ---------------------------------------------------------------------------


_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE,
)
_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)"
    r"(?:\+?\d{1,3}[\s.-]?)?"
    r"(?:\(?\d{3}\)?[\s.-]?)"
    r"\d{3}[\s.-]?\d{4}"
    r"(?!\d)"
)
# @handle — Twitter/X/Reddit-style. We strip the leading "@" + handle,
# leaving a [REDACTED_HANDLE] marker.
_HANDLE_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,30}\b")
# URLs that are profile-shaped (e.g. /u/<name>, /user/<name>, /@<name>).
_PROFILE_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s]*?/(?:u|user|@)/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


def redact_identity_markers(text: str | None) -> str:
    """Replace obvious identity markers with `[REDACTED_*]` placeholders.

    Coverage in Phase 8.2A:
      - email addresses → `[REDACTED_EMAIL]`
      - phone numbers   → `[REDACTED_PHONE]`
      - @handles        → `[REDACTED_HANDLE]`
      - profile URLs    → `[REDACTED_PROFILE_URL]`

    Phase 8.2B will add a deeper redactor (real-name detection via NER,
    address detection beyond regex). This function intentionally errs on
    the side of redacting too aggressively rather than too little.
    """
    if not text:
        return ""
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
    out = _PROFILE_URL_RE.sub("[REDACTED_PROFILE_URL]", out)
    out = _HANDLE_RE.sub("[REDACTED_HANDLE]", out)
    return out
