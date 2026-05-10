"""Phase 8.5C.1 — universal scanners run on every candidate before
selection.

Each scanner is a pure function over (text, planned_record, ...). No
network, no LLM. Returns a list of issues — empty list = pass.

Scanners:

  * `scan_pii(text)` — emails, phones, external URLs, raw @handles,
    image URLs.
  * `scan_unlaunched_fake_buyer(text, product_name)` — rejects any
    `<Product> buyer/customer/loyalist/reviewer/tried <Product>/
    bought <Product>/uses <Product>` substring. Critical for
    unlaunched products.
  * `scan_dataset_compliance(planned_record, source_family)` —
    verifies compliance_tag, source_url shape, no .com URLs, etc.
  * `compute_content_hash(content, source_kind)` — sha256 hex.
  * `check_duplicate_content_hash(content_hash, sessionmaker)` —
    READ-ONLY DB query to detect prior insertion of the same hash.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import func, select

from assembly.sources.ingestion_policy.constants import (
    COMPLIANCE_TAGS_VALID,
    EMAIL_RE,
    EXTERNAL_URL_RE,
    IMAGE_URL_RE,
    PHONE_RE,
    RAW_HANDLE_RE,
)


@dataclass(frozen=True)
class PIIScanResult:
    issues: list[str]
    has_email: bool
    has_phone: bool
    has_external_url: bool
    has_raw_handle: bool
    has_image_url: bool


@dataclass(frozen=True)
class UnlaunchedFakeBuyerScanResult:
    issues: list[str]
    matched_phrases: list[str]


def scan_pii(text: str) -> PIIScanResult:
    """Detect PII patterns in the raw text. Returns structured
    result; empty `issues` = pass."""
    if not text:
        return PIIScanResult(
            issues=[], has_email=False, has_phone=False,
            has_external_url=False, has_raw_handle=False,
            has_image_url=False,
        )
    has_email = bool(EMAIL_RE.search(text))
    has_phone = bool(PHONE_RE.search(text))
    has_url = bool(EXTERNAL_URL_RE.search(text))
    has_handle = bool(RAW_HANDLE_RE.search(text))
    has_image = bool(IMAGE_URL_RE.search(text))
    issues: list[str] = []
    if has_email:
        issues.append("contains an email address")
    if has_phone:
        issues.append("contains a phone-number-like sequence")
    if has_url:
        issues.append("contains an external URL")
    if has_handle:
        issues.append("contains a raw @handle")
    if has_image:
        issues.append("contains an image URL")
    return PIIScanResult(
        issues=issues,
        has_email=has_email, has_phone=has_phone,
        has_external_url=has_url, has_raw_handle=has_handle,
        has_image_url=has_image,
    )


def scan_unlaunched_fake_buyer(
    *,
    text: str,
    product_name: str,
) -> UnlaunchedFakeBuyerScanResult:
    """For unlaunched products, reject any text that fabricates a
    buying / customer / loyalty / direct-experience relationship to
    the product itself.

    Patterns checked (case-insensitive):
      * `<Product> buyer / customer / loyalist / reviewer / fan`
      * `tried <Product>` / `bought <Product>` / `purchased <Product>`
      * `uses <Product>` / `using <Product>`
      * `repeat <Product>` / `<Product> repeat purchase`
      * `<Product> changed my life` / `<Product> habit`

    These are universal anti-pretending rules — every unlaunched
    product gets the same scan."""
    if not text or not product_name:
        return UnlaunchedFakeBuyerScanResult(issues=[], matched_phrases=[])
    name = product_name.strip()
    name_first_word = name.split()[0] if name else ""
    candidates = [name]
    if name_first_word and name_first_word.lower() != name.lower():
        candidates.append(name_first_word)
    matched: list[str] = []
    txt_low = text.lower()
    for n in candidates:
        n_low = n.lower()
        for pat_template in (
            r"\b{n} buyer\b", r"\b{n} buyers\b",
            r"\b{n} customer\b", r"\b{n} customers\b",
            r"\b{n} loyalist\b", r"\b{n} loyalists\b",
            r"\b{n} reviewer\b", r"\b{n} reviewers\b",
            r"\b{n} fan\b", r"\b{n} fans\b",
            r"\btried {n}\b", r"\bbought {n}\b",
            r"\bpurchased {n}\b",
            r"\buses {n}\b", r"\busing {n}\b", r"\bused {n}\b",
            r"\brepeat {n}\b", r"\b{n} repeat purchase\b",
            r"\b{n} changed my life\b", r"\b{n} habit\b",
            r"\bstarted with {n}\b",
        ):
            pat = pat_template.format(n=re.escape(n_low))
            if re.search(pat, txt_low):
                matched.append(pat_template.format(n=n))
    issues = (
        [f"unlaunched-product fake-buyer pattern matched: {m}" for m in matched]
        if matched else []
    )
    return UnlaunchedFakeBuyerScanResult(
        issues=issues, matched_phrases=sorted(set(matched)),
    )


def scan_dataset_compliance(
    *,
    source_kind: str,
    source_url: str,
    compliance_tag: str,
    source_family: str,
) -> list[str]:
    """Verify the planned source_record fields satisfy framework
    compliance rules. Returns issue list — empty = pass."""
    issues: list[str] = []
    if compliance_tag not in COMPLIANCE_TAGS_VALID:
        issues.append(
            f"compliance_tag {compliance_tag!r} not in closed set "
            f"{COMPLIANCE_TAGS_VALID}"
        )
    if source_family.startswith("amazon_reviews_2023"):
        if compliance_tag != "open_dataset":
            issues.append(
                "amazon_reviews_2023 source MUST use "
                "compliance_tag='open_dataset'"
            )
        # Accept either `local://amazon_reviews_2023/` or
        # `local://amazon_reviews_2023_local/`. Both signal the
        # local-dataset family.
        if not source_url.startswith("local://amazon_reviews_2023"):
            issues.append(
                "amazon_reviews_2023 source_url must start with "
                "'local://amazon_reviews_2023' (synthetic prefix)"
            )
        if "amazon.com" in source_url.lower():
            issues.append(
                "source_url contains 'amazon.com' — Amazon.com "
                "scraping is forbidden"
            )
        if not source_kind.startswith("amazon_reviews_2023"):
            issues.append(
                f"source_kind {source_kind!r} does not match "
                "amazon_reviews_2023 family"
            )
    return issues


def compute_content_hash(*, content: str, source_kind: str) -> str:
    """Deterministic content hash for dedup. sha256 over a normalized
    blob = source_kind + '|' + content with whitespace collapsed.

    Same content + same source_kind → same hash. Always 64 hex chars."""
    normalized = re.sub(r"\s+", " ", (content or "").strip())
    payload = f"{source_kind}|{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def check_duplicate_content_hash(
    *,
    content_hash: str,
    sessionmaker,
) -> bool:
    """READ-ONLY DB query. Returns True iff an existing source_record
    row has this `content_hash`. Pure read; the function body uses
    only `select(...)`. No write surface."""
    # Late import — keeps the module structure clean and lets drift
    # tests scan for direct ORM construction (none here).
    from assembly.models.persona import SourceRecord
    async with sessionmaker() as session:
        result = await session.execute(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.content_hash == content_hash)
        )
        n = result.scalar_one()
    return n > 0
