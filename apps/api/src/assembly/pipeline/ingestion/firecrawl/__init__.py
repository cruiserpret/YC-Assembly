"""Phase 8.3B — Firecrawl extraction adapter.

NOT a Tavily replacement. NOT a discovery layer. Firecrawl receives a
SINGLE public URL (typically discovered earlier by Tavily) and returns
a cleaned Markdown body of that page. Intended use: pair with Tavily
so Tavily finds candidate URLs and Firecrawl extracts deeper full-page
evidence on the most-promising ones.

Public surface:

  * `FirecrawlClient`                 — per-URL extraction client.
  * `FirecrawlExtractedPage`          — typed extraction result.
  * `FirecrawlExtractionMetadata`     — closed metadata schema.
  * `assert_firecrawl_approved`       — compliance gate; call BEFORE
                                        invoking `client.extract`.
  * `build_extracted_page_from_payload` — pure parser, no HTTP / DB.
                                          Intended for tests.
  * Error types: `FirecrawlError`, `FirecrawlApiKeyMissing`,
                 `FirecrawlComplianceNotApproved`,
                 `FirecrawlBlockedPage`,
                 `FirecrawlBodyTooShort`,
                 `FirecrawlBodyRedactionFailed`,
                 `FirecrawlMetadataMalformed`.

Phase 8.3B ships the package + memo at `Status: review`. NO live
Firecrawl calls are authorized in this phase. Phase 8.3B-LIVE
(separate approval) will run the operator-flip + first targeted
extraction with hard caps + post-run audit.

Drift tests in `tests/test_no_drift_firecrawl_adapter.py` enforce:

  * `httpx` import only in `client.py`.
  * `FIRECRAWL_API_KEY` string only in `client.py` and
    `compliance_gate.py`.
  * No persona / source-record / population-graph / simulation /
    frontend writes anywhere in the package.
  * No `firecrawl` SDK import (we use direct httpx).
"""
from assembly.pipeline.ingestion.firecrawl.client import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_CHARS,
    DEFAULT_TIMEOUT_S,
    HARD_MAX_CHARS,
    TRUNCATION_MARKER,
    FirecrawlClient,
    build_extracted_page_from_payload,
)
from assembly.pipeline.ingestion.firecrawl.compliance_gate import (
    FIRECRAWL_ADAPTER_NAME,
    FIRECRAWL_DEFAULT_STATUS,
    FIRECRAWL_MEMO_PATH,
    assert_firecrawl_approved,
)
from assembly.pipeline.ingestion.firecrawl.errors import (
    FirecrawlApiKeyMissing,
    FirecrawlBlockedPage,
    FirecrawlBodyRedactionFailed,
    FirecrawlBodyTooShort,
    FirecrawlBoilerplateDominated,
    FirecrawlBotProtectionPlaceholder,
    FirecrawlComplianceNotApproved,
    FirecrawlError,
    FirecrawlMetadataMalformed,
)
from assembly.pipeline.ingestion.firecrawl.types import (
    FirecrawlExtractedPage,
    FirecrawlExtractionMetadata,
)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MIN_CHARS",
    "DEFAULT_TIMEOUT_S",
    "FIRECRAWL_ADAPTER_NAME",
    "FIRECRAWL_DEFAULT_STATUS",
    "FIRECRAWL_MEMO_PATH",
    "FirecrawlApiKeyMissing",
    "FirecrawlBlockedPage",
    "FirecrawlBodyRedactionFailed",
    "FirecrawlBodyTooShort",
    "FirecrawlBoilerplateDominated",
    "FirecrawlBotProtectionPlaceholder",
    "FirecrawlClient",
    "FirecrawlComplianceNotApproved",
    "FirecrawlError",
    "FirecrawlExtractedPage",
    "FirecrawlExtractionMetadata",
    "FirecrawlMetadataMalformed",
    "HARD_MAX_CHARS",
    "TRUNCATION_MARKER",
    "assert_firecrawl_approved",
    "build_extracted_page_from_payload",
]
