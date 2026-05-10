# Compliance memo — Tavily Search/Extract

> **Tavily is a discovery/extraction provider. Individual pages returned
> by Tavily must still be treated as public web evidence subject to
> source-level compliance, redaction, sensitive-content rejection, and
> domain-level caution. Tavily use does not automatically approve every
> source domain for production use.**
>
> Phase 8.2E ships the framework's first live adapter. The memo opens
> at `Status: review`. Local-development smoke testing requires an
> explicit operator flip to `approved`; production approval requires
> formal sign-off in Section 16.

## 1. Status

- **Status:** review
- **Approver:** _<unset — local-dev smoke test only; production sign-off pending>_
- **Last reviewed:** 2026-05-03

## 2. Source name + scope

- **Source:** Tavily Search/Extract API (commercial discovery + extraction provider).
- **Endpoints / pages accessed:**
  - `POST https://api.tavily.com/search` — query → list of public-web result hits with `url`, `title`, `content` (snippet), and optional metadata.
  - `POST https://api.tavily.com/extract` — URL list → extracted page text. Used optionally; first smoke test relies on `search` snippets only.
- **Returned domains:** open-ended. Tavily may surface results from any public domain. Each result is treated as **untrusted public web evidence** and screened by the redaction-before-storage pipeline. **Surfacing a domain via Tavily does NOT approve that domain for production use.**
- **Date window:** captured-at-time-of-call only; no historical crawl.

## 3. Access method

- **API or HTML or other:** API (official commercial provider).
- **Authentication:** `TAVILY_API_KEY` from environment only. The key is never read from a CLI flag, never logged, never written to disk, never embedded in metadata, never echoed in test fixtures. The drift test asserts the key string never appears in any module under `pipeline/ingestion/`.
- **Browser automation:** **No.**
- **Library / client:** `httpx` (sync or async; framework default async). No `tavily-python` SDK dependency required for V0 — direct JSON over HTTPS, narrowly scoped.

## 4. robots.txt status (if applicable)

- **Path:** N/A at the Tavily endpoint level (Tavily is the provider).
- **Per-result-domain robots:** Tavily's contract is that returned URLs are public. Our framework does NOT re-fetch the URL — only Tavily's returned `content` snippet (or its `extract` response) reaches us. We do not crawl beyond what Tavily returned.

## 5. Terms of Service notes

- **ToS link:** https://tavily.com/terms (provider terms).
- **ToS posture:** Tavily provides a paid commercial API; commercial use is part of the product. Compliance with PER-RESULT-DOMAIN terms remains the user's responsibility — surfaces such as Reddit, Shopify community, Product Hunt, Trustpilot, G2, Capterra each carry their own terms which we have NOT individually reviewed in 8.2E.
- **Mitigation:** We do not store login-walled, paywalled, or private content. We rely on the redaction + sensitive-attribute filter to refuse identity-bearing content. Any domain whose terms turn out to forbid downstream commercial reuse can be added to a domain deny-list before its first ingestion run.

## 6. Commercial-use notes

- **Is the source's ToS clear about commercial / paid product use?** Yes — Tavily is a commercial product. The provider charges per request and explicitly markets to product builders.
- **Per-result-domain commercial use:** **AMBIGUOUS.** A given returned URL may carry its own commercial-use restrictions. The framework does not auto-approve per-domain commercial use — the audit panel surfaces accepted source domains so reviewers can deny-list any domain that turns out to be problematic.
- **Pricing / API tier:** Tavily free / paid tiers (subject to provider's pricing page). Phase 8.2E uses tightly capped quotas (see Section 7).

## 7. Rate limits

- **Documented limits:** subject to Tavily's pricing tier; Phase 8.2E does NOT pin to a specific tier.
- **Limits we enforce (Phase 8.2E first smoke test):**
  - max queries per run: **5**
  - max results per query: **5**
  - max accepted source_records per run: **25**
  - max content chars per record: **4000**
  - per-request timeout: **30s**
  - no extract-pass beyond the snippet returned by `search` (for V0)
- **Future raises:** any increase requires a memo update + status re-review.

## 8. What we collect

- **Fields we read from each Tavily result:**
  - `url` → `source_records.source_url`
  - `title` → metadata.title
  - `content` (snippet) → `source_records.content` (after redaction + sanitization)
  - `score` (Tavily relevance) → metadata.tavily_score
  - `published_date` (when present) → metadata.published_date
  - request-side: query string and result rank → metadata.query / metadata.result_rank
- **Fields we explicitly drop before storing:**
  - any author-name string Tavily returns
  - any avatar / profile-URL field
  - any embedded HTML
  - any binary / image data
  - any field not in the read-list above
- **Per-record content cap:** **4000 chars**, truncated with `…[TRUNCATED]` marker.

## 9. What we do NOT collect

- Login-walled or paywalled pages — rejected at normalize time when title/URL/snippet contain markers like `paywall`, `subscribe to read`, `sign in to view`, `members only`, `private`, `403`, `404`, `requires login`, `account required`, or HTTP error pages.
- Private / personal pages — rejected when the URL path looks like a profile (`/u/<name>`, `/user/<name>`, `/@<name>`).
- Identity-heavy pages — rejected post-redaction if the residual identity-marker scan still hits.
- Content that fails the sensitive-attribute filter (race, ethnicity, religion, health, immigration, income, exact addresses, phones, emails) is rejected entirely; partial storage is structurally impossible.

## 10. Identity surface

- **Identifiers in the raw response:** Tavily returns `url`, `title`, `content` (snippet) and may include author-shaped strings within `content`. `raw_handle` is **not** populated by this adapter — handles, when they appear in snippet text, are redacted to `[REDACTED_HANDLE]` by the framework's existing redactor.
- **Storage policy:** `user_handle_hash` is null for Tavily records. The redactor is the only path by which handle-shaped substrings can leave the snippet — they are replaced, not hashed, because we cannot guarantee a stable handle space across third-party domains.
- **User-facing exposure:** **None.** Source URLs are surfaced to operators in the audit panel; raw `content` is never user-facing.

## 11. Redaction-before-storage path

- **Pre-store redaction:** Phase 8.2A `redact_identity_markers` over `content` and every string leaf in `metadata`. Catches emails, phones, @handles, profile URLs.
- **`pii_redaction_status` target:** always `'redacted'` on accepted records. Records whose post-redaction content still carries identity markers are REJECTED, not partially stored.
- **`sensitive_scan_status` target:** always `'clean'` on accepted records. `assert_no_sensitive_attributes` runs over content + metadata-as-json; any sensitive attribute hit REJECTS the entire record.

## 12. Sensitive data policy

- **Forbidden attributes:** race, ethnicity, religion, sexual orientation, gender identity inferred, health, immigration status, household income, exact addresses (street/ZIP), phone numbers, email addresses, real names beyond first-name patterns the redactor catches.
- **On detection:** REJECT the entire record. Never partially store. The rejection is logged into `AdapterRunSummary.rejection_reasons` with a structured `reason_code`.

## 13. Allowed downstream use

- **Persona construction:** **NOT YET.** Phase 8.2E only writes `source_records`. Persona construction is Phase 8.2F. This memo will be revisited at that boundary.
- **Direct user-facing display:** **No.** Raw content never reaches users. Only redacted excerpts via the existing `evidence_anchor_details` audit-detail surface.
- **Aggregation / analysis only:** N/A in 8.2E — aggregation surfaces ship in 8.2F+.

## 14. Refresh / deletion policy

- **Refresh cadence:** No automatic refresh in V0. Manual re-ingest only after a per-run review.
- **Source deletion / takedown:** If a returned URL becomes unavailable (404, takedown, robots-blocked), our next ingest pass will not re-acquire it; existing rows are retained (read-only) until simulation deletion cascades.
- **Retention:** Source records retained for the lifetime of the simulations they support; deleted on simulation delete via FK CASCADE (Phase 8.2A schema).

## 15. Risks + mitigations

- **Risk:** Tavily-surfaced domain has its own commercial-use prohibition we did not catch. **Mitigation:** the operator audit panel exposes `accepted source domains` per run; any problematic domain can be added to a deny-list and a re-ingest blocked.
- **Risk:** Tavily returns paywalled / login-walled snippets. **Mitigation:** normalize-time deny-list of paywall / login markers in title / url / snippet; rejected with `reason_code=PAYWALL_OR_LOGIN_WALL`.
- **Risk:** Identity-heavy snippets slip past the redactor. **Mitigation:** post-redaction regression scan rejects records whose content still carries email / handle / profile-URL hits — same firewall the existing framework uses.
- **Risk:** API key leakage. **Mitigation:** key is read only via `os.environ.get("TAVILY_API_KEY")`. The drift test asserts no module under `pipeline/ingestion/` references the key string by any other means; tests assert the key never appears in logs / fixtures / repr / debug output.
- **Risk:** Quota over-run. **Mitigation:** hard caps in Section 7 (5 queries × 5 results, 25 accepted total per run); the adapter refuses to issue another HTTP call once the accepted-cap is reached.

## 16. Sign-off

- **Engineering owner:** _<unassigned — pending Phase 8.2F kickoff>_
- **Privacy / legal reviewer:** _<unassigned — pending review before production approval>_
- **Status:** **REVIEW.** No production approval. Local-dev smoke test only after explicit operator flip to `approved` for the dev environment, with the understanding that the flip authorizes Phase 8.2E's smoke-test run, NOT production ingestion.

---

**Memo last updated:** Phase 8.2E ship date.

**Until this memo's `Status` field reads `approved` AND the
`adapter_compliance_status` row carries populated `approver` and
`approved_at` columns, the framework REFUSES to run the Tavily
adapter — even in test mode the test must explicitly insert an
approved row to exercise the success path.**
