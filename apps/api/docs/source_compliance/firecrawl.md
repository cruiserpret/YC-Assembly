# Compliance memo — Firecrawl Extraction (Phase 8.3B)

> **Firecrawl is a per-URL extraction provider. It accepts a public URL
> (typically discovered earlier via Tavily) and returns a cleaned
> Markdown body of that page. Firecrawl use does NOT automatically
> approve every source domain for production use — each returned page
> remains untrusted public-web evidence subject to redaction,
> sensitive-content rejection, and per-domain caution.**
>
> Phase 8.3B ships the adapter skeleton + this memo. The memo opens at
> `Status: review`. **No live Firecrawl calls are authorized in this
> phase.** Local development and tests must use a mock transport
> (injected `http_factory`); the live path raises
> `FirecrawlComplianceNotApproved` until the operator explicitly flips
> `adapter_compliance_status.status='approved'` for
> `adapter_name='firecrawl_extract'` AND populates `approver` +
> `approved_at`.

## 1. Status

- **Status:** review
- **Approver:** _<unset — Phase 8.3B preparation only; live use NOT authorized>_
- **Last reviewed:** 2026-05-03

## 2. Source name + scope

- **Source:** Firecrawl Scrape API (commercial extraction provider).
- **Endpoint accessed (closed allow-list):**
  - `POST https://api.firecrawl.dev/v1/scrape` — given a single URL, returns the cleaned page body in Markdown plus a small metadata block.
- **Returned domains:** open-ended. Firecrawl returns the body of whatever URL we hand it. Each returned page is treated as **untrusted public-web evidence** and screened by redaction + sensitive-attribute filter pre-storage. **Surfacing a domain via Firecrawl does NOT approve that domain for production use.**
- **Date window:** captured-at-time-of-call only; no historical / archival crawl.
- **Request shape we send:** `{"url": "<single absolute http(s) URL>", "formats": ["markdown"], "onlyMainContent": true}`. No batch endpoints, no crawl endpoints, no map endpoints, no extraction-with-prompts variants. Phase 8.3B-LIVE-1.5 added `onlyMainContent: true` so Firecrawl strips navigation / sidebar / breadcrumb boilerplate at the provider layer; this preserves request-shape parsimony (one URL, one format, one extraction-quality flag).

## 3. Access method

- **API or HTML or other:** API (official commercial provider).
- **Authentication:** `FIRECRAWL_API_KEY` from environment ONLY (`os.environ.get("FIRECRAWL_API_KEY")`). Never read from CLI flags. Never written to disk. Never embedded in metadata. Never echoed to logs. Never included in error messages. Never serialized into `repr()` / `__str__`. Drift test asserts the string `FIRECRAWL_API_KEY` does not appear anywhere in the Firecrawl package outside `client.py` and `compliance_gate.py`.
- **Browser automation:** **No.** No headless browser, no Playwright, no Selenium.
- **Library / client:** `httpx` only — direct JSON over HTTPS. The official `firecrawl` Python SDK is **forbidden** package-wide (drift-tested).

## 4. robots.txt status

- **Path:** N/A at the Firecrawl endpoint level. Firecrawl is the provider.
- **Per-result-domain robots:** Firecrawl's documented contract is that the service respects per-target robots.txt by default. The adapter additionally inspects the response metadata (`robotsAllowed` / status code / blocked indicators) and refuses the page with `FirecrawlBlockedPage(reason_code="ROBOTS_OR_BLOCKED")` if Firecrawl signals the target was disallowed.
- **Our independent check:** the adapter does **not** re-fetch robots.txt or attempt to bypass disallow signals. The adapter trusts Firecrawl's robots-handling at the provider layer and rejects on any blocked-shape response.

## 5. Terms of Service notes

- **ToS link:** https://www.firecrawl.dev/terms (provider terms).
- **ToS posture:** Firecrawl is a paid commercial API explicitly licensed for AI / data-extraction product use cases.
- **Per-result-domain commercial use:** **AMBIGUOUS.** A given returned URL may carry its own commercial-use restrictions. The framework does NOT auto-approve per-domain commercial use — accepted source domains are surfaced in the operator audit panel (Phase 8.2I+); any domain whose terms turn out to forbid downstream commercial reuse can be added to a domain deny-list before its first ingestion run.
- **Pricing / API tier:** Firecrawl free / paid tiers, subject to provider's pricing page. Phase 8.3B authorizes ZERO live calls; Phase 8.3B-LIVE (separate approval) will pin a tier + caps before any spend occurs.

## 6. Commercial-use notes

- **Is the source's ToS clear about commercial / paid product use?** Yes — Firecrawl is a commercial product marketed to AI / data-builder customers.
- **Per-result-domain commercial use:** **AMBIGUOUS — caller's responsibility.** The same posture as Tavily: surfacing a page does not approve its content's per-domain commercial reuse.
- **Pricing / API tier (when 8.3B-LIVE is approved):** TBD per a separate cost memo; Phase 8.3B does NOT pin a tier.

## 7. Rate limits

- **Documented limits:** subject to Firecrawl's pricing tier. Phase 8.3B does NOT pin to a specific tier.
- **Limits we will enforce on first live use (proposed for 8.3B-LIVE):**
  - **Max URLs per run: 25.** Operator-approved expansion runs may raise this to a documented hard ceiling (e.g. 75) only via a follow-on memo update + status re-review.
  - **Max body chars per record: 8000** (default), hard ceiling 200,000 chars per record.
  - **Min body chars per record: 80** (rejects empty or boilerplate-only bodies).
  - **Per-request timeout: 30 seconds.**
  - **No batch / crawl endpoints.** One URL per call; if a run needs many URLs, the adapter loops one-at-a-time.
  - **Phase 8.3B-LIVE-1.5: per-source-kind persistence cap.** `source_kind='firecrawl_v1_scrape'` rows preserve up to **8000 chars** in storage (the operator script passes `max_content_chars=8000` to `prepare_source_record_insert`). The hard ceiling on the persistence cap is 16,000 chars; Tavily callers do not pass this parameter and remain at the default 4000-char persistence cap.
- **Future raises:** any increase requires a memo update + status re-review.

## 8. What we collect

Fields read from each Firecrawl response and persisted (after redaction):

- `data.markdown` (or fallback `data.content`) → `source_records.content` (after pre-store redaction; capped at `max_chars`).
- `data.metadata.sourceURL` → `source_records.source_url` + `metadata.final_url`.
- `data.metadata.title` → `metadata.title`.
- `data.metadata.statusCode` → `metadata.source_status_code`.
- `data.metadata.contentType` → `metadata.content_type`.
- `data.metadata.language` → `metadata.page_lang`.
- `data.metadata.robotsAllowed` → `metadata.robots_allowed`.
- Adapter-internal: `metadata.scraped_via='firecrawl_v1_scrape'`, `metadata.requested_url`.

The closed metadata schema is `FirecrawlExtractionMetadata` (`extra='forbid'`); any field outside the list above is dropped at parse time.

## 9. What we do NOT collect

- Login-walled, paywalled, or private content — rejected at extraction time when the body or metadata signals one of: paywall markers (`subscribe to read`, `members only`, `account required`), HTTP 401/403/404/429, robots-disallow, or Firecrawl's `scrapeStatus='blocked'` shape.
- **Phase 8.3B-LIVE-1.5: bot-protection / placeholder pages** — rejected at extraction time with reason code `BOT_OR_PLACEHOLDER_CONTENT` when the body contains any of: *"Something went wrong. Wait a moment and try again."*, *"verify you are human"*, *"captcha"*, *"enable JavaScript"*, *"sign in to continue"*, *"access denied"*, *"temporarily blocked"*, *"Please enable cookies"*, *"Cloudflare"*, *"Just a moment"*. The body never reaches the persistence layer.
- **Phase 8.3B-LIVE-1.5: boilerplate-dominated pages** — rejected at extraction time with reason code `BOILERPLATE_DOMINATED` when the markdown-link-line ratio exceeds 0.50 OR the substantive-sentence count is below 3. Catches pages where Firecrawl returned mostly nav menus, breadcrumbs, sidebar topic-link lists, or empty-template scaffolding.
- Identity-bearing pages — rejected post-redaction if residual identity markers (emails, @handles, profile URLs) survive the redactor pass.
- Sensitive-attribute content (race, ethnicity, religion, sexual orientation, gender identity inferred, health, immigration, income, exact addresses, phones, emails) — rejected entirely; partial storage is structurally impossible.
- Author-name strings, avatar URLs, profile-URL fields, embedded HTML, binary / image data, JS-rendered scripts, raw HTTP headers, cookies, session tokens, any field outside Section 8.
- Multi-page crawl results — Firecrawl's `/v1/crawl` endpoint is **out of scope**; only `/v1/scrape` is authorized in 8.3B.
- Any URL that has not been first surfaced through an authorized discovery layer (Tavily today, Brave when added, Reddit when added). The operator script that drives 8.3B-LIVE refuses to scrape a URL that did not appear in a recent compliance-approved discovery run.

## 10. Identity surface

- **Identifiers in the raw response:** Firecrawl returns the page body, which may contain author names, @handles, profile URLs, emails, phones, etc. The body is treated as untrusted text.
- **Storage policy:** `user_handle_hash` is **null** for Firecrawl records — Firecrawl does not surface a structured handle field, so we do not synthesize one. Any handle-shaped substring inside the body is replaced (not hashed) by the framework's existing redactor.
- **User-facing exposure:** **None.** Source URLs and redacted excerpts may be surfaced to operators in the audit panel; raw body is never user-facing.

## 11. Redaction-before-storage path

- **Pre-store redaction (Phase 8.2A `redact_identity_markers`):** runs over the body inside the adapter's `_build_extracted_page` BEFORE the page object is returned. The contract: `FirecrawlExtractedPage.body_markdown` is the redacted body, never the raw upstream payload. Drift / extraction tests assert this.
- **Residual-marker scan:** post-redaction body is scanned for surviving identity markers; any hit raises `FirecrawlBodyRedactionFailed` and the page is **refused** — not partially returned.
- **`pii_redaction_status` target:** always `'redacted'` on accepted records (when the page is later persisted by an ingestion run).
- **`sensitive_scan_status` target:** the existing Phase 8.2C sensitive-attribute filter runs at persistence time via `prepare_source_record_insert`; any sensitive attribute hit REJECTS the entire record before any DB write.

## 12. Sensitive data policy

- **Forbidden attributes:** race, ethnicity, religion, sexual orientation, gender identity inferred, health, immigration status, household income, exact addresses (street/ZIP), phone numbers, email addresses, real names beyond first-name patterns the redactor catches.
- **On detection:** REJECT the entire record at the persistence layer (existing Phase 8.2C `prepare_source_record_insert` discipline). Never partially store. The rejection is logged into the run summary's `rejection_reasons` with a structured `reason_code`.
- **Adapter-side enforcement:** the adapter itself refuses to return a page whose post-redaction body still carries identity markers (Section 11). The persistence layer's sensitive-attribute scan is the second line of defense.

## 13. Allowed downstream use

- **Persona construction:** **NOT YET authorized in Phase 8.3B.** The adapter package ships in 8.3B; persona-construction inputs that reference Firecrawl-derived `source_records` only become legitimate in a follow-on phase (8.3B-LIVE) gated by operator approval.
- **Direct user-facing display:** **No.** Raw body never reaches users. Only redacted excerpts via the existing `evidence_anchor_details` audit-detail surface.
- **Aggregation / analysis only:** N/A in 8.3B — no live extraction runs in this phase.

## 14. Refresh / deletion policy

- **Refresh cadence:** No automatic refresh. Manual re-extraction only after a per-run review in 8.3B-LIVE+.
- **Source deletion / takedown:** if a previously-extracted URL becomes unavailable (404, takedown, robots-blocked), our next extraction attempt will not re-acquire it; existing rows are retained (read-only) until simulation deletion cascades.
- **Retention:** source records retained for the lifetime of the simulations they support; deleted on simulation delete via FK CASCADE (Phase 8.2A schema).

## 15. Risks + mitigations

- **Risk:** Firecrawl-extracted page has its own commercial-use prohibition we did not catch upstream. **Mitigation:** the operator audit panel exposes accepted source domains per run; any problematic domain can be added to a deny-list and a re-extraction blocked.
- **Risk:** Firecrawl returns a paywalled / login-walled body that slips past Firecrawl's own filtering. **Mitigation:** adapter-side blocked-page detection inspects status code + robotsAllowed + body markers (`subscribe to read`, `members only`, etc.) and raises `FirecrawlBlockedPage` cleanly.
- **Risk:** Identity-heavy bodies slip past the redactor. **Mitigation:** post-redaction residual-marker scan; failing pages are refused, not partially returned.
- **Risk:** API key leakage. **Mitigation:** key is read only via `os.environ.get("FIRECRAWL_API_KEY")`. The drift test asserts the string `FIRECRAWL_API_KEY` does not appear in any module under the Firecrawl package outside `client.py` and `compliance_gate.py`. No `repr()`, no logging, no metadata embed.
- **Risk:** Compliance gate is bypassed by accidentally calling the live path before approval. **Mitigation:** `assert_firecrawl_approved` raises `FirecrawlComplianceNotApproved` until DB row + memo are both in place. The operator script for 8.3B-LIVE (which does not exist yet) MUST call this gate before invoking `FirecrawlClient.extract`.
- **Risk:** Quota over-run on first live use. **Mitigation:** per-run hard caps (Section 7); the run loop refuses to issue another HTTP call once `MAX_URLS` is reached. First live run is restricted to a small targeted URL list, not a sweep.
- **Risk:** Crawl-endpoint accidentally invoked. **Mitigation:** only `/v1/scrape` is wired into the client. The drift test asserts no string literal containing `/v1/crawl` or `/v1/map` exists in the package.

## 16. Sign-off

- **Engineering owner:** _<unassigned — pending Phase 8.3B-LIVE kickoff>_
- **Privacy / legal reviewer:** _<unassigned — pending review before production approval>_
- **Status:** **REVIEW.** No live use authorized. The adapter ships in `'review'` state; Phase 8.3B-LIVE (separate phase, separate approval) will run the operator-flip + first targeted extraction with hard caps + post-run audit.

---

**Memo last updated:** Phase 8.3B ship date.

**Until this memo's `Status` field reads `approved` AND the
`adapter_compliance_status` row carries populated `approver` +
`approved_at` columns for `adapter_name='firecrawl_extract'`, the
framework REFUSES to run the Firecrawl adapter — even in test mode the
test must explicitly insert an approved row to exercise the live-gate
success path.**
