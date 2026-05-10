"""Phase 8.2A — anonymization utility tests."""
from __future__ import annotations

import re
from uuid import UUID, uuid4

import pytest

from assembly.pipeline.persona.anonymization import (
    generate_display_name,
    hash_public_handle,
    redact_identity_markers,
)
from assembly.pipeline.persona.constants import (
    DISPLAY_NAME_FIRSTS,
    DISPLAY_NAME_LAST_INITIALS,
)


# ---------------------------------------------------------------------------
# generate_display_name
# ---------------------------------------------------------------------------


def test_generate_display_name_deterministic_per_seed() -> None:
    seed = uuid4()
    a = generate_display_name(seed)
    b = generate_display_name(seed)
    assert a == b


def test_generate_display_name_varies_across_seeds() -> None:
    names = {generate_display_name(uuid4()) for _ in range(20)}
    # ≥10 distinct names from 20 random UUIDs is a soft signal that the
    # pool is wide enough; the Birthday-paradox is fine here.
    assert len(names) >= 10


def test_generate_display_name_format() -> None:
    name = generate_display_name(uuid4())
    # "First L." — at least one space, ends with single letter + dot.
    assert re.match(r"^[A-Z][a-z]+ [A-Z]\.$", name), name


def test_generate_display_name_pool_is_curated() -> None:
    """The pool must NOT include strings that look like ingestion content
    (e.g. real handles or real-name phrases). The pool entries are short
    fictional first names + single-letter last initials by construction.
    Test asserts the pool is structured."""
    for first in DISPLAY_NAME_FIRSTS:
        assert first.isalpha()
        assert first[0].isupper()
    for last in DISPLAY_NAME_LAST_INITIALS:
        assert len(last) == 1
        assert last.isalpha()
        assert last.isupper()


def test_generate_display_name_does_not_use_handle_as_name() -> None:
    """The name is drawn from the curated pool, not from the seed text.
    Even if a seed string contains an obvious handle, the output is
    pool-only."""
    # Seed contains an @handle — output must not include it.
    name = generate_display_name("seed-with-handle:@johnsmithreal")
    assert "@" not in name
    assert "johnsmithreal" not in name.lower()
    # And the first-name portion is in our pool.
    first = name.split()[0]
    assert first in DISPLAY_NAME_FIRSTS


# ---------------------------------------------------------------------------
# hash_public_handle
# ---------------------------------------------------------------------------


def test_hash_public_handle_uses_salt() -> None:
    h1 = hash_public_handle("merchant_jane", salt="salt-1")
    h2 = hash_public_handle("merchant_jane", salt="salt-2")
    assert h1 != h2


def test_hash_public_handle_deterministic_with_salt() -> None:
    a = hash_public_handle("merchant_jane", salt="salt-1")
    b = hash_public_handle("merchant_jane", salt="salt-1")
    assert a == b


def test_hash_public_handle_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError):
        hash_public_handle("", salt="salt-1")
    with pytest.raises(ValueError):
        hash_public_handle("merchant_jane", salt="")


def test_hash_public_handle_outputs_64_hex_chars() -> None:
    h = hash_public_handle("merchant_jane", salt="salt-1")
    assert len(h) == 64
    assert re.match(r"^[0-9a-f]{64}$", h)


# ---------------------------------------------------------------------------
# redact_identity_markers
# ---------------------------------------------------------------------------


def test_redact_email() -> None:
    out = redact_identity_markers("Contact jane@example.com tomorrow")
    assert "[REDACTED_EMAIL]" in out
    assert "@example.com" not in out


def test_redact_phone() -> None:
    out = redact_identity_markers("Call me at (415) 555-0199")
    assert "[REDACTED_PHONE]" in out


def test_redact_handle() -> None:
    out = redact_identity_markers("posted by @merchant_jane on Reddit")
    assert "[REDACTED_HANDLE]" in out
    assert "@merchant_jane" not in out


def test_redact_profile_url() -> None:
    out = redact_identity_markers("see https://reddit.com/u/merchant_jane post")
    assert "[REDACTED_PROFILE_URL]" in out
    assert "merchant_jane" not in out


def test_redact_returns_empty_for_none() -> None:
    assert redact_identity_markers(None) == ""
    assert redact_identity_markers("") == ""


def test_redact_passes_clean_commerce_text() -> None:
    text = "agents framed Shopify Magic as a free, native baseline"
    assert redact_identity_markers(text) == text
