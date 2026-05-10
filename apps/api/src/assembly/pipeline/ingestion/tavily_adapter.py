"""Phase 8.2E — Tavily Search/Extract live adapter.

This is the first APPROVED live ingestion adapter in the framework. It
talks to Tavily's commercial search/extract API (and ONLY Tavily) and
normalizes results into the existing redaction-before-storage pipeline.

Critical safety properties (drift tests + tests assert each):

  * The TAVILY_API_KEY is read ONLY from the process environment via
    `os.environ.get("TAVILY_API_KEY")`. It is never accepted via CLI
    flag, never written to disk, never echoed to logs, never embedded
    in metadata, never carried into `repr()` / `__str__`. The drift
    test asserts no string `"TAVILY_API_KEY"` appears in any other
    module under `pipeline/ingestion/`.

  * `httpx` is imported here and ONLY here (the drift test enforces).

  * `fetch_live` REFUSES to run if the key is missing.

  * Results that look paywalled / login-walled / private are rejected
    at normalize time with structured `reason_code`.

  * Identity-shaped URLs (`/u/<name>`, `/user/<name>`, `/@<name>`) are
    rejected at normalize time.

  * Tavily-returned text passes through the framework's existing
    redaction-before-storage pipeline before any database write.

  * Hard caps: 5 queries × 5 results × 25 accepted total × 4000-char
    content. The base-class `ingest_live(accepted_cap=...)` enforces
    the accepted cap.

Tavily is a DISCOVERY/EXTRACTION provider. Phase 8.2E does NOT add
dedicated adapters for any specific source domain (Reddit, Shopify,
Product Hunt, Trustpilot, G2, Capterra, etc.). Pages from those
domains are allowed only when surfaced by Tavily and only after they
pass the framework's compliance + redaction + sensitive-filter +
dedup discipline.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from assembly.pipeline.ingestion.adapter_base import (
    NormalizationRejection,
    SourceAdapter,
)
from assembly.pipeline.ingestion.run_summary import (
    NormalizedSourcePayload,
    RawSourcePayload,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-result metadata schema (Pydantic) — adapter contract
# ---------------------------------------------------------------------------


class TavilyResultMetadata(BaseModel):
    """Closed metadata schema for one accepted Tavily result.

    Anything not in this schema is dropped at normalize time. The
    METADATA_SCHEMA contract surfaces shape mismatches early; the base
    class's run_summary captures rejection reasons rather than raising.

    Phase 8.2F.5 added run-tracking fields:
      - `run_purpose`           free-form operator label (e.g.
                                'phase_8_2f_5_human_signal_expansion')
      - `operator_run`          True when the row came from an operator-
                                approved live run (not a test fixture)
      - `test_fixture`          True when the row came from a test
                                fixture; the test cleanup fixture deletes
                                ONLY rows with this flag set
      - `likely_human_signal_candidate`
                                heuristic flag set at normalize time
                                indicating the result looks like a
                                discussion / forum / review surface
                                (i.e. likely to classify as
                                strong/weak persona signal under the
                                Phase 8.2F classifier).
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    result_rank: int = Field(ge=0)
    title: str | None = None
    domain: str | None = None
    tavily_score: float | None = None
    published_date: str | None = None
    # Phase 8.2F.5 additions — all default-safe so existing fixtures
    # (and existing rows) remain valid against the schema.
    run_purpose: str | None = None
    operator_run: bool = False
    test_fixture: bool = False
    likely_human_signal_candidate: bool | None = None
    # Phase 8.2F.6 — when a query is explicitly aimed at one of the
    # Phase-8.2F.7 missing stakeholder categories, the operator
    # records the target here so audits can verify coverage gain.
    target_missing_category: str | None = None
    # Phase 8.2I — when ingestion is part of a run-scoped top-up loop,
    # the operator stamps which brief the run was scoped to. Lets a
    # future audit attribute new source_records to the brief that
    # triggered them.
    target_brief: str | None = None
    # Phase 8.2I.1 — operator stamps the refinement-pass label so future
    # audits can tell which query catalog produced the row (e.g.
    # '8.2I.1' for the Amboras refined retry).
    query_refinement_version: str | None = None


# ---------------------------------------------------------------------------
# Paywall / login / private heuristics
# ---------------------------------------------------------------------------


_PAYWALL_OR_LOGIN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpaywall\b", re.IGNORECASE),
    re.compile(r"\bsubscribe to (?:read|continue|view)\b", re.IGNORECASE),
    re.compile(r"\bsign in to (?:view|read|continue)\b", re.IGNORECASE),
    re.compile(r"\bmembers? only\b", re.IGNORECASE),
    re.compile(r"\bprivate (?:page|content|profile)\b", re.IGNORECASE),
    re.compile(r"\baccount required\b", re.IGNORECASE),
    re.compile(r"\brequires login\b", re.IGNORECASE),
    re.compile(r"\b(?:403 forbidden|404 not found|401 unauthorized|"
               r"429 too many requests|page not found)\b", re.IGNORECASE),
    re.compile(r"\blogin required\b", re.IGNORECASE),
    re.compile(r"\bpremium content\b", re.IGNORECASE),
    re.compile(r"\bunlock (?:full|premium) (?:article|content)\b",
               re.IGNORECASE),
)


_PROFILE_URL_PATH_RE: re.Pattern[str] = re.compile(
    # /u/<handle>, /user/<handle>, /users/<handle>, /profile(s)/<handle>
    # OR /@<handle> (Twitter/Mastodon/YouTube-style segment)
    r"/(?:u|user|users|profiles?)/[A-Za-z0-9_.-]+|/@[A-Za-z0-9_.-]+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Phase 8.2F.5 — likely-human-signal heuristic (URL/title/snippet shape)
# ---------------------------------------------------------------------------


# Domains that overwhelmingly host public discussion / forum / review /
# Q&A surfaces. Surfacing one of these is a positive (but not sole)
# signal that the snippet is more likely to read as first-person buyer
# voice than a marketing page. Phase 8.2F's classifier still has the
# final say — this is only a metadata HINT we set at normalize time.
_HUMAN_SIGNAL_DOMAINS: tuple[str, ...] = (
    "community.shopify.com",
    "reddit.com",
    "old.reddit.com",
    "ycombinator.com",       # news.ycombinator.com
    "news.ycombinator.com",
    "news.combinator.com",
    "stackexchange.com",
    "stackoverflow.com",
    "quora.com",
    "discourse.org",
    "forum.gettogether.community",
    "indiehackers.com",
    "lemmy.world",
    "mastodon.social",
)
_HUMAN_SIGNAL_PATH_RE: re.Pattern[str] = re.compile(
    r"/(?:r/|threads?/|topic/|comments?/|reviews?/|forums?/|"
    r"discussion/|community/|posts?/|c/|board/)",
    re.IGNORECASE,
)
_MARKETING_DOMAIN_HINTS: tuple[str, ...] = (
    "agency", "studio", "consulting", "design", "expert",
)
_MARKETING_PATH_RE: re.Pattern[str] = re.compile(
    r"/(?:pricing|features|about|services?|solutions?|product[s]?|"
    r"signup|register|contact|demo|case-stud(?:y|ies))",
    re.IGNORECASE,
)
_FIRST_PERSON_LITE_RE: re.Pattern[str] = re.compile(
    r"\b(?:I'?m|I'?ve|my|we'?re|we'?ve|our store|"
    r"as a (?:founder|merchant|operator|owner|seller))\b",
    re.IGNORECASE,
)


def _looks_like_human_signal_candidate(
    *, url: str | None, title: str | None, snippet: str | None,
) -> bool:
    """Return True if the URL / title / snippet looks like a public
    discussion / forum / review surface that may carry first-person
    buyer voice. Used to set
    `metadata.likely_human_signal_candidate` at normalize time.

    The heuristic is intentionally lenient; the Phase 8.2F classifier
    is the authority on whether the record actually classifies as
    strong/weak persona signal.
    """
    domain = (_extract_domain(url) or "").lower()
    path = ""
    try:
        from urllib.parse import urlparse
        path = urlparse(url or "").path or ""
    except Exception:
        path = ""

    score = 0
    if any(domain == d or domain.endswith("." + d) for d in _HUMAN_SIGNAL_DOMAINS):
        score += 3
    if _HUMAN_SIGNAL_PATH_RE.search(path):
        score += 2
    snippet_str = snippet or ""
    if _FIRST_PERSON_LITE_RE.search(snippet_str):
        score += 2
    if any(h in domain for h in _MARKETING_DOMAIN_HINTS):
        score -= 2
    if _MARKETING_PATH_RE.search(path):
        score -= 2
    return score >= 2


# ---------------------------------------------------------------------------
# Phase 8.2F.5 — human-signal-focused query catalog
# ---------------------------------------------------------------------------


# These queries deliberately target review / forum / comment / discussion
# surfaces. Tavily may or may not honour `site:` perfectly — the intent
# is preserved via combined keywords.
HUMAN_SIGNAL_QUERIES: tuple[str, ...] = (
    "site:community.shopify.com Shopify merchants plugin bloat complaints",
    "site:reddit.com/r/shopify Shopify merchants plugin bloat complaints",
    "site:reddit.com/r/ecommerce Shopify merchants agency cost complaints",
    "site:reddit.com/r/shopify AI store builder concerns Shopify merchants",
    "site:reddit.com/r/ecommerce ecommerce founders brand control automation concerns",
    "site:reddit.com/r/dropship Shopify apps pricing complaints merchants",
    "site:reddit.com/r/smallbusiness website agency cost complaints ecommerce",
    "Shopify community AI store builder concerns merchants",
    "Shopify merchants too many apps complaints forum",
    "DTC founder Shopify automation trust concerns discussion",
)


# Phase 8.2F.6 broader expansion — 15 queries, each tagged with the
# stakeholder category it's aimed at. The Phase-8.2F.7 audit found
# these three categories with zero coverage:
#   - dtc_founder_brand_control
#   - freelancer_using_merchant
#   - lock_in_worried_operator
# plus underrepresented: ai_skeptical_operator, nontechnical_founder,
# agency_dependent_merchant, app_heavy_user.
#
# Mapping: query string → target_missing_category. The category value
# matches `pipeline/persona_relevance/rubric.StakeholderCategory`
# values so post-run audits can roll up coverage delta.
BROADER_HUMAN_SIGNAL_QUERIES: dict[str, str] = {
    # Brand control / DTC founders (3)
    "DTC founder brand control Shopify concerns":
        "dtc_founder_brand_control",
    "ecommerce founder brand control website builder complaints":
        "dtc_founder_brand_control",
    "Shopify merchants brand control custom theme concerns":
        "dtc_founder_brand_control",

    # Freelancer / agency alternatives (3)
    "Shopify merchant freelancer website complaints":
        "freelancer_using_merchant",
    "ecommerce founder hired freelancer Shopify problems":
        "freelancer_using_merchant",
    "small business website agency cost ecommerce complaints":
        "agency_dependent_merchant",

    # Lock-in / trust / AI skepticism (3)
    "Shopify merchant lock-in concerns":
        "lock_in_worried_operator",
    "ecommerce platform lock-in complaints merchant":
        "lock_in_worried_operator",
    "AI website builder trust concerns ecommerce founder":
        "ai_skeptical_operator",

    # Nontechnical operators (2)
    "non technical founder Shopify store setup frustration":
        "nontechnical_founder",
    "small business owner ecommerce website too complicated":
        "nontechnical_founder",

    # App fatigue / pricing (3)
    "Shopify apps too expensive merchant complaints":
        "app_heavy_user",
    "Shopify app subscription fatigue ecommerce":
        "app_heavy_user",
    "too many Shopify apps slow store complaints":
        "app_heavy_user",

    # Automation skepticism (1)
    "Shopify automation AI skepticism merchants":
        "ai_skeptical_operator",
}


def _looks_paywalled_or_login_walled(*, title: str, url: str, snippet: str) -> bool:
    blob = f"{title}\n{url}\n{snippet}"
    return any(p.search(blob) for p in _PAYWALL_OR_LOGIN_PATTERNS)


def _looks_identity_url(url: str) -> bool:
    if not url:
        return False
    return bool(_PROFILE_URL_PATH_RE.search(url))


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    m = re.match(r"https?://([^/]+)/?", url, re.IGNORECASE)
    if not m:
        return None
    host = m.group(1).lower()
    # Strip leading "www."
    if host.startswith("www."):
        host = host[4:]
    return host


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class TavilyApiKeyMissing(RuntimeError):
    """Raised when the live path is invoked without TAVILY_API_KEY in env."""


class TavilySearchExtractAdapter(SourceAdapter):
    """Tavily search adapter. Live mode is gated by compliance approval
    AND the presence of TAVILY_API_KEY in the environment. The mocked
    path is exercised by tests via `fetch_mocked`.

    Caps (Phase 8.2E baseline):
      max_queries:           5
      max_results_per_query: 5
      max_accepted:         25
      max_content_chars:  4000
      timeout_seconds:      30

    Phase 8.2F.5 introduces a `MAX_QUERIES_EXPANSION` ceiling (10) that
    operator-approved expansion runs may opt into via the
    `for_human_signal_expansion` factory. The smoke-test default
    remains MAX_QUERIES=5.
    """

    NAME: ClassVar[str] = "tavily_search_extract"
    SOURCE_KIND: ClassVar[str] = "tavily_search_extract"
    COMPLIANCE_TAG: ClassVar[str] = "public_api"
    MEMO_PATH: ClassVar[str] = "apps/api/docs/compliance/tavily_search_extract.md"
    METADATA_SCHEMA: ClassVar[type[BaseModel]] = TavilyResultMetadata

    BASE_URL: ClassVar[str] = "https://api.tavily.com"
    MAX_QUERIES: ClassVar[int] = 5
    # 8.2F.6 broader-expansion ceiling was 15; 8.2I.1 raises to 30 to
    # accommodate the Amboras refined retry (6 categories × 5 queries).
    MAX_QUERIES_EXPANSION: ClassVar[int] = 30
    MAX_RESULTS_PER_QUERY: ClassVar[int] = 5
    MAX_RESULTS_PER_QUERY_EXPANSION: ClassVar[int] = 10
    MAX_ACCEPTED: ClassVar[int] = 25
    # 8.2F.6 broader-expansion ceiling was 100; 8.2I.1 raises to 120 to
    # match the refined-retry plan's max_accepted target.
    MAX_ACCEPTED_EXPANSION: ClassVar[int] = 120
    MAX_CONTENT_CHARS: ClassVar[int] = 4000
    TIMEOUT_SECONDS: ClassVar[float] = 30.0
    ENV_VAR: ClassVar[str] = "TAVILY_API_KEY"

    # Default Phase-8.2E smoke-test query set. Operators may pass a
    # different list; the cap still applies.
    DEFAULT_QUERIES: ClassVar[tuple[str, ...]] = (
        "Shopify merchants plugin bloat complaints",
        "DTC founders agency cost store design complaints",
        "Shopify merchants AI store builder concerns",
        "ecommerce founders brand control automation concerns",
        "Shopify store automation pricing concerns",
    )

    def __init__(
        self,
        *,
        queries: Sequence[str] | None = None,
        mocked_payloads: Sequence[RawSourcePayload] | None = None,
        http_client_factory=None,
        run_purpose: str | None = None,
        operator_run: bool = False,
        test_fixture: bool = False,
        max_queries: int | None = None,
        max_results_per_query: int | None = None,
        max_accepted: int | None = None,
        query_to_category: dict[str, str] | None = None,
        target_brief: str | None = None,
        query_refinement_version: str | None = None,
    ) -> None:
        super().__init__()
        q = list(queries) if queries is not None else list(self.DEFAULT_QUERIES)
        # Resolve the per-instance cap. Default = class MAX_QUERIES.
        # Operator-approved expansion runs raise to MAX_QUERIES_EXPANSION
        # via the factory, never beyond.
        cap_max_queries = (
            max_queries
            if max_queries is not None
            else self.MAX_QUERIES
        )
        if cap_max_queries > self.MAX_QUERIES_EXPANSION:
            raise ValueError(
                f"max_queries={cap_max_queries} exceeds hard ceiling "
                f"MAX_QUERIES_EXPANSION={self.MAX_QUERIES_EXPANSION}"
            )
        if len(q) > cap_max_queries:
            raise ValueError(
                f"queries exceeds max_queries={cap_max_queries}: {len(q)}"
            )
        self._queries: list[str] = q
        self._mocked_payloads: list[RawSourcePayload] | None = (
            list(mocked_payloads) if mocked_payloads is not None else None
        )
        # Test seam — lets unit tests inject an httpx.AsyncClient stub
        # without touching real httpx network code paths.
        self._client_factory = http_client_factory
        # Per-instance caps (operator_run + expansion factory bumps these).
        self._max_results_per_query = (
            max_results_per_query
            if max_results_per_query is not None
            else self.MAX_RESULTS_PER_QUERY
        )
        if self._max_results_per_query > self.MAX_RESULTS_PER_QUERY_EXPANSION:
            raise ValueError(
                f"max_results_per_query={self._max_results_per_query} exceeds "
                f"hard ceiling MAX_RESULTS_PER_QUERY_EXPANSION="
                f"{self.MAX_RESULTS_PER_QUERY_EXPANSION}"
            )
        self._max_accepted_default = (
            max_accepted
            if max_accepted is not None
            else self.MAX_ACCEPTED
        )
        if self._max_accepted_default > self.MAX_ACCEPTED_EXPANSION:
            raise ValueError(
                f"max_accepted={self._max_accepted_default} exceeds "
                f"hard ceiling MAX_ACCEPTED_EXPANSION={self.MAX_ACCEPTED_EXPANSION}"
            )
        # Run-tracking flags written into metadata of every accepted row.
        self._run_purpose = run_purpose
        self._operator_run = bool(operator_run)
        self._test_fixture = bool(test_fixture)
        # Phase 8.2F.6 — optional query → target_missing_category map
        # used to tag results with the stakeholder category the query
        # was aimed at.
        self._query_to_category: dict[str, str] = (
            dict(query_to_category) if query_to_category else {}
        )
        # Phase 8.2I — operator-supplied label for the brief this
        # run-scoped ingest is targeting (e.g. "amboras").
        self._target_brief: str | None = target_brief
        # Phase 8.2I.1 — refinement-pass label.
        self._query_refinement_version: str | None = query_refinement_version

    @property
    def max_results_per_query(self) -> int:
        return self._max_results_per_query

    @property
    def max_accepted(self) -> int:
        return self._max_accepted_default

    @classmethod
    def for_human_signal_expansion(
        cls,
        *,
        queries: Sequence[str] | None = None,
    ) -> "TavilySearchExtractAdapter":
        """Phase 8.2F.5 factory: human-signal-focused queries + tier
        caps **10 queries × 10 results × 75 accepted**. Sets
        `operator_run=True` and `run_purpose='phase_8_2f_5_human_signal_expansion'`
        on every accepted row's metadata so future test cleanup never
        touches them."""
        return cls(
            queries=list(queries) if queries is not None else list(HUMAN_SIGNAL_QUERIES),
            run_purpose="phase_8_2f_5_human_signal_expansion",
            operator_run=True,
            test_fixture=False,
            max_queries=10,
            max_results_per_query=10,
            max_accepted=75,
        )

    @classmethod
    def for_broader_human_signal_expansion(
        cls,
        *,
        queries: dict[str, str] | None = None,
    ) -> "TavilySearchExtractAdapter":
        """Phase 8.2F.6 factory: broader human-signal queries targeting
        the missing stakeholder categories surfaced by the Phase
        8.2F.7 audit. Tier caps **15 queries × 10 results × 100
        accepted**.

        `queries` is a mapping `query_string -> target_missing_category`.
        The category value is propagated into every accepted row's
        metadata (`target_missing_category`).
        """
        catalog = (
            dict(queries) if queries is not None
            else dict(BROADER_HUMAN_SIGNAL_QUERIES)
        )
        return cls(
            queries=list(catalog.keys()),
            query_to_category=catalog,
            run_purpose="phase_8_2f_6_broader_human_signal_expansion",
            operator_run=True,
            test_fixture=False,
            max_queries=15,
            max_results_per_query=10,
            max_accepted=100,
        )

    # ---- repr / str: never expose the key -----------------------------

    def __repr__(self) -> str:  # pragma: no cover  trivial
        return f"<{type(self).__name__} queries={len(self._queries)}>"

    # ---- Mocked path: tests use this ---------------------------------

    def fetch_mocked(self) -> Sequence[RawSourcePayload]:
        if self._mocked_payloads is not None:
            return list(self._mocked_payloads)
        return list(_default_mocked_payloads())

    # ---- Live path: real Tavily call ---------------------------------

    async def fetch_live(self) -> Sequence[RawSourcePayload]:
        api_key = os.environ.get(self.ENV_VAR)
        if not api_key:
            # NEVER include the env-var value in the message — even on
            # failure, only the variable NAME is allowed in logs.
            raise TavilyApiKeyMissing(
                f"{self.ENV_VAR} not set; refusing to run Tavily live fetch."
            )
        # Defensive: never let a leading/trailing whitespace key sneak in.
        api_key = api_key.strip()

        captured_at = datetime.now(UTC)
        payloads: list[RawSourcePayload] = []
        client_cm = (
            self._client_factory(api_key)
            if self._client_factory is not None
            else httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS)
        )

        async with client_cm as client:
            for q in self._queries:
                try:
                    results = await self._tavily_search(
                        client=client, api_key=api_key, query=q,
                    )
                except Exception as e:  # pragma: no cover  defensive
                    # Log without the key. Use type+message only.
                    logger.warning(
                        "tavily search failed for query=%r: %s",
                        q, type(e).__name__,
                    )
                    continue
                target_category = self._query_to_category.get(q)
                for rank, r in enumerate(results[: self._max_results_per_query]):
                    p = _result_to_payload(
                        result=r,
                        query=q,
                        rank=rank,
                        captured_at=captured_at,
                        target_missing_category=target_category,
                    )
                    if p is not None:
                        payloads.append(p)
        return payloads

    async def _tavily_search(
        self, *, client, api_key: str, query: str,
    ) -> list[dict[str, Any]]:
        """One Tavily /search call. Returns the `results` list.

        - api_key is passed in the request body per Tavily's docs; we
          never log the request headers / body verbatim.
        """
        body = {
            "api_key": api_key,
            "query": query,
            "max_results": self._max_results_per_query,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        resp = await client.post(f"{self.BASE_URL}/search", json=body)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not isinstance(results, list):
            return []
        return [r for r in results if isinstance(r, dict)]

    # ---- Normalize ---------------------------------------------------

    def normalize_payload(
        self, raw: RawSourcePayload,
    ) -> NormalizedSourcePayload:
        # Phase 8.2F.5: merge in run-tracking flags + likely-human-signal
        # heuristic BEFORE validating against METADATA_SCHEMA. The schema
        # uses extra='forbid'; any field a fixture sets that we also
        # carry here would collide, so we use the fixture's value when
        # already present (tests can opt-in to test_fixture=true).
        raw_md = dict(raw.metadata or {})
        # Heuristic: only set the human-signal hint if the fixture/result
        # didn't pre-set it. Operators may override via raw metadata.
        if "likely_human_signal_candidate" not in raw_md:
            raw_md["likely_human_signal_candidate"] = (
                _looks_like_human_signal_candidate(
                    url=raw.source_url,
                    title=raw_md.get("title"),
                    snippet=raw.content,
                )
            )
        # Run-tracking flags: prefer per-instance config, but allow the
        # raw metadata to override (e.g. tests injecting fixtures).
        if "run_purpose" not in raw_md:
            raw_md["run_purpose"] = self._run_purpose
        if "operator_run" not in raw_md:
            raw_md["operator_run"] = self._operator_run
        if "test_fixture" not in raw_md:
            raw_md["test_fixture"] = self._test_fixture
        if "target_brief" not in raw_md:
            raw_md["target_brief"] = self._target_brief
        if "query_refinement_version" not in raw_md:
            raw_md["query_refinement_version"] = self._query_refinement_version

        # Validate metadata schema. Any structural violation rejects the
        # record cleanly via NormalizationRejection.
        try:
            md = self.METADATA_SCHEMA.model_validate(raw_md)
        except Exception as e:
            raise NormalizationRejection(
                "METADATA_SCHEMA_MISMATCH",
                f"{type(e).__name__}: {e}",
            )

        title = md.title or ""
        url = raw.source_url or ""
        snippet = raw.content or ""

        # Reject identity-shaped profile URLs FIRST. URL shape is a
        # deterministic signal; the paywall heuristic below is softer
        # and could trip on incidental phrases in the snippet.
        if _looks_identity_url(url):
            raise NormalizationRejection(
                "IDENTITY_URL_REJECTED",
                f"URL path looks like a personal profile: {url[:80]}",
            )

        # Reject paywalled / login-walled / error-page snippets.
        if _looks_paywalled_or_login_walled(
            title=title, url=url, snippet=snippet,
        ):
            raise NormalizationRejection(
                "PAYWALL_OR_LOGIN_WALL",
                "Result text or URL signals paywalled / login-walled / "
                "error / private content.",
            )

        # Reject empty / near-empty snippets.
        if len(snippet.strip()) < 24:
            raise NormalizationRejection(
                "SNIPPET_TOO_SHORT",
                f"Snippet length {len(snippet.strip())} < 24 chars; rejecting.",
            )

        # Pre-truncate. The redaction pipeline truncates again to its
        # 4000-char cap; we mirror it here so identity scans run over a
        # bounded string.
        content = snippet[: self.MAX_CONTENT_CHARS]

        # Drop adapter-internal language detection: surface "en" only if
        # Tavily explicitly set the field; otherwise leave None.
        return NormalizedSourcePayload(
            source_url=url or None,
            captured_at=raw.captured_at,
            content=content,
            raw_handle=None,  # Tavily does not surface handles directly
            metadata=md.model_dump(mode="json"),
            language="en",
        )


# ---------------------------------------------------------------------------
# Helpers — Tavily result → RawSourcePayload
# ---------------------------------------------------------------------------


def _result_to_payload(
    *,
    result: dict[str, Any],
    query: str,
    rank: int,
    captured_at: datetime,
    target_missing_category: str | None = None,
) -> RawSourcePayload | None:
    url = result.get("url")
    if not isinstance(url, str) or not url:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    title = result.get("title")
    snippet = result.get("content")
    if not isinstance(snippet, str) or not snippet:
        return None
    domain = _extract_domain(url)
    score = result.get("score")
    if not isinstance(score, (int, float)):
        score = None
    published = result.get("published_date")
    if not isinstance(published, str):
        published = None
    metadata: dict[str, Any] = {
        "query": query,
        "result_rank": rank,
        "title": title if isinstance(title, str) else None,
        "domain": domain,
        "tavily_score": float(score) if score is not None else None,
        "published_date": published,
    }
    if target_missing_category is not None:
        metadata["target_missing_category"] = target_missing_category
    return RawSourcePayload(
        source_url=url,
        captured_at=captured_at,
        content=snippet,
        raw_handle=None,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Default mocked fixtures (used when tests don't inject their own)
# ---------------------------------------------------------------------------


def _default_mocked_payloads() -> list[RawSourcePayload]:
    """Hand-crafted Tavily-shaped fixtures for tests. No real handles,
    real names, real emails, real phones, or real addresses."""
    base = datetime.now(UTC) - timedelta(days=2)
    return [
        RawSourcePayload(
            source_url="https://community.example.test/topic/12345",
            captured_at=base,
            content=(
                "Plugin overload is the daily cost mid-volume merchants "
                "describe most often; consolidation tools that retain "
                "merchant control are the recurring ask in public threads."
            ),
            raw_handle=None,
            metadata={
                "query": "Shopify merchants plugin bloat complaints",
                "result_rank": 0,
                "title": "Public discussion: plugin consolidation",
                "domain": "community.example.test",
                "tavily_score": 0.84,
                "published_date": None,
            },
        ),
        RawSourcePayload(
            source_url="https://blog.example.test/founder-cost-of-agencies",
            captured_at=base,
            content=(
                "Public blog excerpt: founders describe agency-design "
                "cost as a recurring monthly burden; many seek tooling "
                "that lets them retain final-pixel control."
            ),
            raw_handle=None,
            metadata={
                "query": "DTC founders agency cost store design complaints",
                "result_rank": 0,
                "title": "Founder cost of agencies",
                "domain": "blog.example.test",
                "tavily_score": 0.78,
                "published_date": "2025-09-12",
            },
        ),
    ]
