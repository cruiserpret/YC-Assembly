"""Phase 4 — Evidence builder.

Produces the per-simulation set of `evidence_items` from:

  1. **User-supplied direct evidence** — every populated input field.
  2. **Fetched URLs** (live or snapshot, gated by C3 cutoff_date rule) — the
     product URL and any competitor URLs the user supplied.
  3. **Source-bound category-language extraction** (C1) — an LLM pass over
     the fetched pages, where every extracted phrase MUST appear verbatim
     in at least one fetched page.
  4. **Deterministic missing-evidence checklist** (C4) — a static
     `EXPECTED_EVIDENCE_BY_PRODUCT_TYPE` dict; whatever the user did not
     supply gets a `kind=missing` row. No LLM decides what is "expected".

The builder returns a list of evidence items + assumptions ready to be
persisted by the orchestrating worker. It does not write to the database.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.errors import CutoffViolationError, LLMRepairExhausted
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import (
    LLMMessage,
    LLMProvider,
    wrap_user_content_as_data,
)
from assembly.pipeline.url_fetcher import FetchedPage, FetchError, fetch_url
from assembly.retrieval.extraction_provider import ExtractedPage, ExtractionProvider
from assembly.retrieval.search_provider import SearchProvider, SearchResult
from assembly.schemas.brief import SimulationBriefIn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static expected-evidence checklist (C4)
# ---------------------------------------------------------------------------
#
# This dict is the SINGLE source of truth for "what evidence types should be
# present for a given product_type". The missing-evidence detector iterates
# this list deterministically — no LLM decides what's missing.
#
# Keys are the canonical product_type strings the user submits; "default" is
# the fallback for unknown types.

EXPECTED_EVIDENCE_BY_PRODUCT_TYPE: dict[str, list[str]] = {
    "ai_commerce_platform": [
        "competitor_page",
        "pricing_page",
        "public_review",
        "category_language",
    ],
    "ai_store_builder": [
        "competitor_page",
        "pricing_page",
        "public_review",
        "category_language",
    ],
    "saas_tool": ["competitor_page", "pricing_page", "public_review"],
    "shopify_app": ["competitor_page", "pricing_page", "public_review"],
    "dtc_product": ["competitor_page", "public_review", "pricing_page"],
    "skincare_product": ["public_review", "competitor_page", "pricing_page"],
    "agency_service": ["competitor_page", "public_review", "pricing_page"],
    "default": ["competitor_page", "pricing_page", "public_review"],
}


def expected_evidence_for(product_type: str) -> list[str]:
    """Return the deterministic checklist for `product_type`. Falls back to
    the 'default' list. Never calls an LLM."""
    key = (product_type or "").strip().lower().replace(" ", "_").replace("-", "_")
    return list(
        EXPECTED_EVIDENCE_BY_PRODUCT_TYPE.get(key, EXPECTED_EVIDENCE_BY_PRODUCT_TYPE["default"])
    )


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingEvidenceItem:
    """A draft evidence_item ready to be persisted by the worker. The id is
    pre-allocated so other pipeline stages can reference it before flush."""

    id: UUID
    simulation_id: UUID
    kind: str  # direct | analogical | missing
    source_type: str
    source_url: str | None
    content: str
    captured_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBuildResult:
    """Aggregated evidence-builder output for a simulation."""

    items: list[PendingEvidenceItem]
    fetched_pages: list[FetchedPage]
    fetch_errors: list[str]
    extracted_phrases: list[ExtractedPhrase]
    # Phase 11C.2 — audit-only side-channel. Populated when both
    # ASSEMBLY_AMAZON_REVIEWS_ENABLED and ..._RUNTIME_ENABLED are
    # true, otherwise None. Persona generation never reads this
    # field; downstream code paths can log it for observability.
    amazon_audit: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExtractedPhrase:
    phrase: str
    source_url: str
    source_excerpt: str


# ---------------------------------------------------------------------------
# Step 1 — direct user-input evidence
# ---------------------------------------------------------------------------


def _user_input_evidence(simulation_id: UUID, brief: SimulationBriefIn) -> list[PendingEvidenceItem]:
    """One direct evidence_item per populated user-input field."""
    out: list[PendingEvidenceItem] = []
    now = datetime.now(UTC)

    fields_and_text: list[tuple[str, str]] = [
        ("user_product_type", brief.product_type),
        ("user_product_name", brief.product_name),
        ("user_description", brief.description),
        ("user_target_society", brief.target_society.description),
    ]
    if brief.target_society.geography:
        fields_and_text.append(("user_target_society_geography", brief.target_society.geography))
    if brief.target_society.income_level:
        fields_and_text.append(("user_target_society_income_level", brief.target_society.income_level))
    if brief.target_society.known_segments:
        fields_and_text.append((
            "user_target_society_known_segments",
            "; ".join(brief.target_society.known_segments),
        ))
    if brief.price_structure:
        fields_and_text.append((
            "user_price_structure",
            json.dumps(brief.price_structure.model_dump()),
        ))
    if brief.competitors:
        fields_and_text.append((
            "user_competitors",
            json.dumps([c.model_dump() for c in brief.competitors]),
        ))
    if brief.product_url:
        fields_and_text.append(("user_product_url", str(brief.product_url)))
    if brief.additional_context:
        fields_and_text.append(("user_additional_context", brief.additional_context))

    for label, content in fields_and_text:
        out.append(
            PendingEvidenceItem(
                id=uuid.uuid4(),
                simulation_id=simulation_id,
                kind="direct",
                source_type="user_input",
                source_url=None,
                content=content,
                captured_at=now,
                metadata={"input_field": label},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Step 2 — fetched URLs (with C3 cutoff guard)
# ---------------------------------------------------------------------------


def _classify_url_source_type(url: str) -> str:
    """Best-effort categorization of a URL into a source_type. Conservative —
    when in doubt, returns 'competitor_page'."""
    u = url.lower()
    if "/pricing" in u or u.endswith("/pricing/") or "pricing." in u:
        return "pricing_page"
    if "/reviews" in u or "/review/" in u or u.endswith("/reviews/"):
        return "public_review"
    return "competitor_page"


async def _fetch_evidence_items(
    *,
    simulation_id: UUID,
    brief: SimulationBriefIn,
    cutoff_date: date | None,
    snapshots: dict[str, str | Path] | None,
) -> tuple[list[PendingEvidenceItem], list[FetchedPage], list[str]]:
    """Fetch the product URL and any competitor URLs.

    `snapshots` maps URL → snapshot path. Required when `cutoff_date` is set
    (per C3); if a URL is not in the snapshots dict and `cutoff_date` is set,
    we record a `kind=missing` evidence_item and continue without fetching.
    """
    items: list[PendingEvidenceItem] = []
    pages: list[FetchedPage] = []
    errors: list[str] = []
    snapshots = snapshots or {}

    urls_to_fetch: list[str] = []
    if brief.product_url:
        urls_to_fetch.append(str(brief.product_url))
    for c in brief.competitors:
        if c.url:
            urls_to_fetch.append(c.url)

    for url in urls_to_fetch:
        snapshot = snapshots.get(url)
        try:
            page = await fetch_url(
                url, cutoff_date=cutoff_date, snapshot=snapshot
            )
        except CutoffViolationError as e:
            # Per C3: refuse, but record the gap so the evidence ledger
            # reflects it honestly.
            items.append(
                PendingEvidenceItem(
                    id=uuid.uuid4(),
                    simulation_id=simulation_id,
                    kind="missing",
                    source_type=_classify_url_source_type(url),
                    source_url=url,
                    content=(
                        f"Cannot fetch {url} under evidence_cutoff_date="
                        f"{cutoff_date}. Provide a snapshot to include this "
                        "evidence; otherwise it remains missing."
                    ),
                    captured_at=None,
                    metadata={"reason": "cutoff_violation", "error": str(e)},
                )
            )
            errors.append(f"cutoff: {url}")
            continue
        except (FetchError, ValueError) as e:
            items.append(
                PendingEvidenceItem(
                    id=uuid.uuid4(),
                    simulation_id=simulation_id,
                    kind="missing",
                    source_type=_classify_url_source_type(url),
                    source_url=url,
                    content=(
                        f"Failed to fetch {url}: {type(e).__name__}: {e}. "
                        "Treated as missing evidence."
                    ),
                    captured_at=None,
                    metadata={"reason": "fetch_error", "error": str(e)},
                )
            )
            errors.append(f"fetch: {url}: {e}")
            continue

        pages.append(page)
        items.append(
            PendingEvidenceItem(
                id=uuid.uuid4(),
                simulation_id=simulation_id,
                kind="direct",
                source_type=_classify_url_source_type(url),
                source_url=url,
                content=page.text,
                captured_at=page.captured_at,
                metadata={
                    "final_url": page.final_url,
                    "status_code": page.status_code,
                    "content_type": page.content_type,
                    "truncated": page.truncated,
                    "source_kind": page.source_kind,
                    "snapshot_path": page.snapshot_path,
                },
            )
        )

    return items, pages, errors


# ---------------------------------------------------------------------------
# Step 3 — source-bound category-language extraction (C1)
# ---------------------------------------------------------------------------


_EXTRACTOR_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "evidence_extractor.md"
)


def load_evidence_extractor_prompt() -> str:
    return _EXTRACTOR_PROMPT_PATH.read_text(encoding="utf-8")


class _ExtractedPhraseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrase: str
    source_url: str
    source_excerpt: str


class _ExtractorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrases: list[_ExtractedPhraseSchema]


def _parse_extractor_json(text: str) -> _ExtractorResponse:
    """Strip code fences, parse JSON, validate against the extractor schema.
    Raises ValueError or pydantic.ValidationError on failure."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        nl = cleaned.find("\n")
        if nl != -1:
            cleaned = cleaned[nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    if not cleaned:
        raise ValueError("empty response from evidence extractor")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"extractor response was not valid JSON: {e}") from e
    return _ExtractorResponse.model_validate(data)


def _verify_phrase_in_pages(
    phrase: str, pages: list[FetchedPage]
) -> FetchedPage | None:
    """Return the first FetchedPage whose `text` contains `phrase` (case-insensitive),
    or None. Whitespace is normalized — multiple spaces match a single space."""
    if not phrase:
        return None
    needle = re.sub(r"\s+", " ", phrase).strip().lower()
    if not needle:
        return None
    for page in pages:
        haystack = re.sub(r"\s+", " ", page.text).lower()
        if needle in haystack:
            return page
    return None


async def extract_category_language(
    *,
    pages: list[FetchedPage],
    provider: LLMProvider,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    model: str,
    max_repair_attempts: int = 1,
    stage: str = "evidence_extractor",
) -> list[ExtractedPhrase]:
    """Run the C1 source-bound extractor over `pages`. Every returned phrase is
    verified to appear in at least one page's text; phrases that don't are
    silently dropped (not retried — empty is the correct answer if the LLM
    can't extract).

    Phase 6.6: every attempt — including repairs — flows through
    `cost_guarded_chat`, which acquires the per-simulation row lock,
    enforces the cost cap, and writes a row to `llm_call_log`.
    """
    if not pages:
        return []

    system_prompt = load_evidence_extractor_prompt()

    # Concatenate pages with explicit URL labels, fenced as data.
    blocks = []
    for p in pages:
        blocks.append(
            f"URL: {p.url}\n\n"
            + wrap_user_content_as_data(f"page:{p.url}", p.text[:20_000])
        )
    user_message = (
        "Extract category language from the following pages. Every phrase "
        "you emit must appear verbatim in at least one of the page texts.\n\n"
        + "\n\n".join(blocks)
    )

    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]

    # Phase 6.6: replace the previous `provider.structured_output(...)` with a
    # local schema-repair loop driven by `cost_guarded_chat` so every attempt
    # writes its own `llm_call_log` row. The substring-verification pass below
    # stays on the raw parsed output (drops unverified phrases — does NOT
    # trigger a repair, since "no extractable phrases" is a valid answer).
    parsed: _ExtractorResponse | None = None
    last_error: Exception | None = None
    try:
        for attempt in range(max_repair_attempts + 1):
            response = await cost_guarded_chat(
                sessionmaker=sessionmaker,
                simulation_id=simulation_id,
                stage=stage,
                messages=messages,
                provider=provider,
                model=model,
                max_tokens=2048,
                temperature=0.1,
            )
            try:
                parsed = _parse_extractor_json(response.text)
                break
            except (ValueError, ValidationError) as e:
                last_error = e
                if attempt >= max_repair_attempts:
                    raise LLMRepairExhausted(
                        f"evidence_extractor failed schema after "
                        f"{max_repair_attempts} repairs: {e}"
                    ) from e
                messages = list(messages) + [
                    LLMMessage(role="assistant", content=response.text),
                    LLMMessage(
                        role="user",
                        content=(
                            "Your previous response did not validate against "
                            "the required schema `ExtractorResponse`:\n\n"
                            f"```\n{e}\n```\n\n"
                            "Return ONLY the corrected JSON object."
                        ),
                    ),
                ]
                logger.info(
                    "evidence_extractor.repair attempt=%d simulation=%s",
                    attempt, simulation_id,
                )
    except Exception as e:
        logger.warning("evidence_extractor.failed err=%s", e)
        return []
    if parsed is None:  # pragma: no cover  defensive (loop exits via break/raise)
        return []

    # Substring-verify every phrase. Drop unverifiable ones.
    verified: list[ExtractedPhrase] = []
    rejected: list[dict] = []
    for ph in parsed.phrases:
        match = _verify_phrase_in_pages(ph.phrase, pages)
        if match is None:
            rejected.append({"phrase": ph.phrase, "claimed_url": ph.source_url})
            continue
        # Also check the claimed source_url is one of our fetched pages.
        page_urls = {p.url for p in pages} | {p.final_url for p in pages}
        if ph.source_url not in page_urls:
            rejected.append({
                "phrase": ph.phrase,
                "claimed_url": ph.source_url,
                "reason": "source_url not in fetched pages",
            })
            continue
        verified.append(
            ExtractedPhrase(
                phrase=ph.phrase,
                source_url=ph.source_url,
                source_excerpt=ph.source_excerpt[:400],
            )
        )

    if rejected:
        logger.info(
            "evidence_extractor.dropped phrases=%d reason=substring_or_url_mismatch",
            len(rejected),
        )

    return verified


# ---------------------------------------------------------------------------
# Step 4 — deterministic missing-evidence checklist (C4)
# ---------------------------------------------------------------------------


def _detect_missing_evidence(
    *,
    simulation_id: UUID,
    product_type: str,
    items_so_far: list[PendingEvidenceItem],
) -> list[PendingEvidenceItem]:
    """Iterate the static EXPECTED list and emit `kind=missing` items for any
    expected source_type not represented by a non-missing direct/analogical
    item. NO LLM in this loop."""
    expected = expected_evidence_for(product_type)
    present_types = {
        i.source_type
        for i in items_so_far
        if i.kind in ("direct", "analogical")
    }

    out: list[PendingEvidenceItem] = []
    for source_type in expected:
        if source_type in present_types:
            continue
        out.append(
            PendingEvidenceItem(
                id=uuid.uuid4(),
                simulation_id=simulation_id,
                kind="missing",
                source_type=source_type,
                source_url=None,
                content=(
                    f"Expected evidence of type {source_type!r} for "
                    f"product_type={product_type!r} per "
                    "EXPECTED_EVIDENCE_BY_PRODUCT_TYPE, but the brief did "
                    "not supply this and no fetched page provided it."
                ),
                captured_at=None,
                metadata={"reason": "expected_but_absent", "expected_for": product_type},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 5.5 — Real-world retrieval (opt-in)
# ---------------------------------------------------------------------------


def _derive_retrieval_queries(brief: SimulationBriefIn) -> list[tuple[str, str]]:
    """Build a small set of retrieval queries from the brief. Returns
    `[(query, expected_source_type), ...]` so the caller can label retrieved
    evidence appropriately. Deterministic — no LLM here.
    """
    queries: list[tuple[str, str]] = []

    # Competitor-page queries — one per named competitor.
    for c in brief.competitors:
        queries.append((f"{c.name} pricing", "pricing_page"))
        queries.append((f"{c.name} reviews", "public_review"))

    # Category-language queries from product_type.
    queries.append((f"{brief.product_type} reviews", "public_review"))
    queries.append((f"{brief.product_type} category language", "category_language"))

    # Buyer pain queries derived from target_society description.
    society_short = brief.target_society.description.split(",")[0].strip()
    if society_short:
        queries.append((f"{society_short} complaints {brief.product_type}", "public_review"))

    return queries


def _classify_retrieved_source_type(
    expected: str, url: str
) -> tuple[str, str]:
    """Decide (source_type, kind) for a retrieved page. Direct evidence when
    the URL clearly maps to a competitor's own domain; analogical otherwise."""
    u = url.lower()
    if "/pricing" in u or "pricing." in u:
        return ("pricing_page", "direct")
    if "/reviews" in u or "/review/" in u:
        return ("public_review", "direct")
    # If the expected type was a "competitor_page" we trust the search
    # routing; otherwise mark as analogical.
    if expected in ("competitor_page", "pricing_page", "public_review"):
        return (expected, "direct")
    return ("category_language", "analogical")


async def _retrieve_evidence(
    *,
    simulation_id: UUID,
    brief: SimulationBriefIn,
    search: SearchProvider,
    extractor: ExtractionProvider,
    cutoff_date: date | None,
    snapshots: dict[str, str | Path] | None,
    max_per_query: int = 3,
) -> tuple[list[PendingEvidenceItem], list[ExtractedPage], list[str]]:
    """Run search + extraction. Always honors `cutoff_date` (the extractor's
    own guard plus our own pre-check). Returns evidence items + pages +
    error strings."""
    items: list[PendingEvidenceItem] = []
    pages: list[ExtractedPage] = []
    errors: list[str] = []
    snapshots = snapshots or {}

    queries = _derive_retrieval_queries(brief)

    for query, expected in queries:
        try:
            results: list[SearchResult] = await search.search(
                query, max_results=max_per_query
            )
        except Exception as e:
            errors.append(f"search:{query}: {type(e).__name__}: {e}")
            results = []

        if not results:
            # Record the gap as missing evidence. Deterministic — no LLM.
            items.append(
                PendingEvidenceItem(
                    id=uuid.uuid4(),
                    simulation_id=simulation_id,
                    kind="missing",
                    source_type=expected,
                    source_url=None,
                    content=(
                        f"Retrieval query {query!r} returned no results. "
                        f"Treated as missing {expected} evidence."
                    ),
                    captured_at=None,
                    metadata={"reason": "no_search_results", "query": query},
                )
            )
            continue

        for r in results:
            if not r.url:
                continue
            snapshot = snapshots.get(r.url)
            try:
                page = await extractor.extract(
                    r.url, cutoff_date=cutoff_date, snapshot=snapshot
                )
            except CutoffViolationError as e:
                items.append(
                    PendingEvidenceItem(
                        id=uuid.uuid4(),
                        simulation_id=simulation_id,
                        kind="missing",
                        source_type=expected,
                        source_url=r.url,
                        content=(
                            f"Cannot extract {r.url} under "
                            f"evidence_cutoff_date={cutoff_date}. Provide a "
                            "snapshot to include this evidence."
                        ),
                        captured_at=None,
                        metadata={
                            "reason": "cutoff_violation",
                            "error": str(e),
                            "search_query": query,
                            "search_rank": r.rank,
                        },
                    )
                )
                errors.append(f"cutoff: {r.url}")
                continue
            except Exception as e:
                errors.append(f"extract:{r.url}: {type(e).__name__}: {e}")
                continue

            pages.append(page)
            source_type, kind = _classify_retrieved_source_type(expected, r.url)
            items.append(
                PendingEvidenceItem(
                    id=uuid.uuid4(),
                    simulation_id=simulation_id,
                    kind=kind,
                    source_type=source_type,
                    source_url=r.url,
                    content=page.text,
                    captured_at=page.captured_at,
                    metadata={
                        "search_query": query,
                        "search_rank": r.rank,
                        "title": page.title,
                        "search_snippet": r.snippet,
                        "source_kind": page.source_kind,
                        "snapshot_path": page.snapshot_path,
                    },
                )
            )

    return items, pages, errors


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


async def build_evidence(
    brief: SimulationBriefIn,
    *,
    simulation_id: UUID,
    sessionmaker: async_sessionmaker | None = None,
    provider: LLMProvider | None = None,
    extractor_model: str | None = None,
    snapshots: dict[str, str | Path] | None = None,
    cutoff_date: date | None = None,
    search_provider: SearchProvider | None = None,
    extraction_provider: ExtractionProvider | None = None,
) -> EvidenceBuildResult:
    """Build the full evidence set for a simulation.

    `provider` and `extractor_model` are optional — if either is missing, the
    category-language extraction step (C1) is skipped and the analogical
    evidence pool stays empty. Direct/missing evidence is always produced.

    `search_provider` and `extraction_provider` are optional — when BOTH are
    provided, Phase-5.5 retrieval runs after the user-supplied URL fetches.
    Retrieval honors `cutoff_date` exactly the same way url_fetcher does.
    """
    items: list[PendingEvidenceItem] = []
    pages: list[FetchedPage] = []
    extracted_pages: list[ExtractedPage] = []
    errors: list[str] = []

    # Step 1: user-supplied direct evidence.
    items.extend(_user_input_evidence(simulation_id, brief))

    # Step 2: fetched URL evidence (with C3 cutoff guard inside fetch_url).
    fetched_items, pages, fetch_errs = await _fetch_evidence_items(
        simulation_id=simulation_id,
        brief=brief,
        cutoff_date=cutoff_date,
        snapshots=snapshots,
    )
    items.extend(fetched_items)
    errors.extend(fetch_errs)

    # Step 2.5 (Phase 5.5): real-world retrieval when both providers given.
    if search_provider is not None and extraction_provider is not None:
        retrieval_items, extracted_pages, retrieval_errs = await _retrieve_evidence(
            simulation_id=simulation_id,
            brief=brief,
            search=search_provider,
            extractor=extraction_provider,
            cutoff_date=cutoff_date,
            snapshots=snapshots,
        )
        items.extend(retrieval_items)
        errors.extend(retrieval_errs)

    # Step 3: source-bound category-language extraction (C1).
    # Run on user-fetched pages AND on retrieval-extracted pages.
    extracted: list[ExtractedPhrase] = []
    if provider is not None and extractor_model is not None:
        # Translate retrieval ExtractedPage → FetchedPage shape for
        # extract_category_language. Same fields it reads (url, text, etc.).
        all_pages_for_extraction = list(pages)
        for ep in extracted_pages:
            all_pages_for_extraction.append(
                FetchedPage(
                    url=ep.url,
                    final_url=ep.final_url,
                    captured_at=ep.captured_at,
                    status_code=200,
                    content_type="text/markdown" if ep.metadata.get("provider") in ("firecrawl", "jina") else "text/html",
                    text=ep.text,
                    truncated=ep.truncated,
                    source_kind=ep.source_kind,
                    snapshot_path=ep.snapshot_path,
                )
            )
        # Phase 6.6: extractor LLM call now flows through cost_guarded_chat,
        # which requires a sessionmaker. Skip the extractor stage if the
        # caller didn't supply one (some standalone tests don't).
        if all_pages_for_extraction and sessionmaker is not None:
            extracted = await extract_category_language(
                pages=all_pages_for_extraction,
                provider=provider,
                sessionmaker=sessionmaker,
                model=extractor_model,
                simulation_id=simulation_id,
            )
            for ph in extracted:
                items.append(
                    PendingEvidenceItem(
                        id=uuid.uuid4(),
                        simulation_id=simulation_id,
                        kind="analogical",
                        source_type="category_language",
                        source_url=ph.source_url,
                        content=ph.phrase,
                        captured_at=datetime.now(UTC),
                        metadata={
                            "source_excerpt": ph.source_excerpt,
                            "extracted_by": "evidence_extractor",
                        },
                    )
                )

    # Step 4: deterministic missing-evidence checklist (C4).
    items.extend(
        _detect_missing_evidence(
            simulation_id=simulation_id,
            product_type=brief.product_type,
            items_so_far=items,
        )
    )

    # Step 5 (Phase 11C.2): Amazon-evidence AUDIT side-channel.
    # Double-flag-gated, read-only. Never adds rows to `items` —
    # persona generation sees zero Amazon evidence. The injector
    # returns a small audit dict (even when disabled) that
    # downstream code can log for observability. Lazy-import keeps
    # build_evidence cheap when Amazon is off.
    amazon_audit: dict[str, Any] | None = None
    if sessionmaker is not None:
        from assembly.config import get_settings
        from assembly.pipeline.amazon_evidence_injector import (
            build_amazon_evidence_section,
        )
        amazon_audit = await build_amazon_evidence_section(
            brief,
            sessionmaker=sessionmaker,
            settings=get_settings(),
        )

    return EvidenceBuildResult(
        items=items,
        fetched_pages=pages,
        fetch_errors=errors,
        extracted_phrases=extracted,
        amazon_audit=amazon_audit,
    )


__all__ = [
    "EvidenceBuildResult",
    "EXPECTED_EVIDENCE_BY_PRODUCT_TYPE",
    "ExtractedPhrase",
    "PendingEvidenceItem",
    "build_evidence",
    "expected_evidence_for",
    "extract_category_language",
    "load_evidence_extractor_prompt",
]
