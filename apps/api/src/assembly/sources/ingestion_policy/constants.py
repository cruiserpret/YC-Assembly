"""Phase 8.5C.1 — universal ingestion-policy constants.

These lists are universal — every product, every source family, every
ingestion run reuses them as-is. The dynamic per-product policy never
overrides them.
"""
from __future__ import annotations


# Closed list of safety/compliance guardrails that apply to every
# ingestion regardless of product or source. Inserted into every
# IngestionPolicy.universal_guardrails.
UNIVERSAL_GUARDRAILS: tuple[str, ...] = (
    "no raw user_id stored (always hashed via sha256/16 if "
    "retained at all)",
    "no image URLs stored in any source-record field",
    "no email / phone number / external URL retained inside "
    "source_records.content",
    "no fake-buyer language for unlaunched products (no "
    "'<Product> buyer / customer / loyalist / reviewer / tried "
    "<Product>' substrings)",
    "no duplicate source_records (content_hash unique constraint)",
    "no Amazon.com scraping (drift-tested in source adapter package)",
    "no Amazon API call (Amazon Reviews 2023 is local-dataset only)",
    "compliance_tag must be in closed framework set",
    "rollback on any scanner failure during execute (Phase 8.5C.2)",
    "DB writes require explicit operator approval (no implicit insert)",
    "captured_at uses dataset snapshot date for historical sources; "
    "every persona built from these source_records carries the "
    "historical-evidence caveat",
)


# Closed list of scanners that MUST run on every candidate before it
# can be SELECTED. Order matters — early scanners (PII, fake-buyer)
# can short-circuit later checks if they fail.
REQUIRED_SCANNERS: tuple[str, ...] = (
    "pii_scan",
    "unlaunched_fake_buyer_scan",
    "dataset_compliance_scan",
    "duplicate_check",
)


# PII regex patterns reused across the framework.
import re  # noqa: E402

EMAIL_RE = re.compile(
    r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"
)
PHONE_RE = re.compile(
    r"\b(?:\+?\d[\d\s().-]{6,}\d)\b"
)
EXTERNAL_URL_RE = re.compile(
    r"https?://\S+", re.IGNORECASE,
)
RAW_HANDLE_RE = re.compile(
    r"(?:^|\s)@[\w._-]{2,30}\b"
)
IMAGE_URL_RE = re.compile(
    r"https?://[^\s\"]+\.(?:jpg|jpeg|png|gif|webp)\b",
    re.IGNORECASE,
)


# Closed compliance-tag set (mirrors Phase 8.2A persona constants).
COMPLIANCE_TAGS_VALID: tuple[str, ...] = (
    "public_api", "public_html", "open_dataset",
    "open_aggregate", "manual_seed",
)
