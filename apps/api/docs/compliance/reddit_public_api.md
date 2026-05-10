# Compliance memo — Reddit Public API

> **Reddit Public API is a CANDIDATE source ONLY. No live ingestion is
> approved. No API credentials should be requested or stored yet.
> Commercial-use approval and human sign-off are required before this
> adapter's status can flip from `draft` to `approved`.**
>
> Phase 8.2C ships the framework + the mocked adapter only. Phase 8.2D
> will not ship until this memo's status is `approved`.

## 1. Status

- **Status:** draft
- **Approver:** _<unset — no human has signed this off>_
- **Last reviewed:** 2026-05-03

## 2. Source name + scope

- **Source:** Reddit Public API (read-only, official; would use PRAW or equivalent if approved)
- **Subreddits / endpoints under consideration:** commerce-relevant public subreddits — `r/shopify`, `r/ecommerce`, `r/dropship`, `r/Entrepreneur`, `r/SideProject`, `r/devtools`, `r/SaaS`, `r/smallbusiness`. **No private or locked subreddits.**
- **Date window proposal:** top + hot posts from the last 90 days.

## 3. Access method

- **API or HTML or other:** API (official). HTML scraping is explicitly out of scope.
- **Authentication (proposed):** read-only `script` app — `client_id + client_secret` only; no user-context OAuth; no user impersonation.
- **Browser automation:** **No.** Forbidden by the framework's drift test.
- **Library / client (proposed):** PRAW (Python Reddit API Wrapper). **Not yet imported into this codebase.**

## 4. robots.txt status

- **Path:** https://www.reddit.com/robots.txt
- **Last fetched:** _<not yet — we have not made any live requests in 8.2C>_
- **Disallow rules relevant to our access:** _<TBD on first review pass — must be checked before status flips>_

## 5. Terms of Service notes

- **ToS link:** https://www.redditinc.com/policies/data-api-terms
- **ToS review status:** **NOT YET COMPLETED.** Reddit's data-API terms shifted significantly in mid-2023 and have evolved since. A line-by-line review is required before status can flip.
- **Specific concerns to resolve in review:**
  - whether commercial / paid-product use is permitted under the free tier
  - whether downstream aggregate use ("synthetic society reports for paying customers") is permitted
  - whether storage of post/comment text in our DB beyond the API-cache window is permitted
  - whether the data-API terms cover our specific subreddits or have endpoint-level carve-outs

## 6. Commercial-use notes

- **Is the source's ToS clear about commercial / paid product use?** **AMBIGUOUS / NEEDS REVIEW.**
- **Action required:** human legal review before flipping status to `approved`. The fact that Reddit's API is publicly documented does not by itself authorize commercial product use.
- **Pricing / API tier:** Reddit's developer-platform tiers shifted in 2023+. The free tier exists; rate / volume limits and commercial-use carveouts MUST be re-verified at approval time.

## 7. Rate limits

- **Documented limits (subject to re-verification):** historically 60 req/min on the free tier.
- **Limit we would enforce if approved:** ≤ 30 req/min (half the documented limit) — defensive headroom.
- **Daily ceiling proposal:** ≤ 200 records on first run, scaling only after a second compliance review.

## 8. What we would collect (if approved)

- **Fields we'd read:** post.title, post.selftext, post.score, post.created_utc, post.num_comments, comment.body, comment.score, comment.created_utc, post.author.name (raw username — see Section 10), post.subreddit.display_name.
- **Fields we'd explicitly drop before storing:** post.url if it points to identity-bearing pages, post.author_flair_text, comment.author_flair_text, anything not in the read-list.
- **Per-record content cap:** 4000 chars (truncated with `…[TRUNCATED]` marker).

## 9. What we would NOT collect

- Private DMs, locked subreddits, deleted content, modmail, removed comments.
- User account history beyond the single post/comment we ingest.
- Any field marked private or removed by Reddit.
- Subreddit modlogs.
- AutoModerator-only contributions.

## 10. Identity surface

- **Identifiers in the raw response:** `post.author.name` / `comment.author.name` (string username).
- **Storage policy if approved:** username is salted-hashed via `hash_public_handle(raw_handle, salt=current_run_salt)` and stored ONLY in `source_records.user_handle_hash`. **The raw username is never written to disk.** Salt rotates per ingestion run so the hash is not a stable cross-time identifier.
- **User-facing exposure:** **None.** No username — raw or hashed — appears in any API response. Debug-mode-only access surfaces the hash for support/triage.

## 11. Redaction-before-storage path

- **Pre-store redaction:** `redact_identity_markers` (Phase 8.2A) over `content` and every string leaf in `metadata`. Catches emails, phones, @handles, profile URLs.
- **`pii_redaction_status` target:** always `'redacted'` on accepted records. If the redactor's regression check finds surviving identity markers, the record is REJECTED, not partially stored.
- **`sensitive_scan_status` target:** always `'clean'` on accepted records. `assert_no_sensitive_attributes` runs over content + metadata; any sensitive attribute hit REJECTS the entire record.

## 12. Sensitive data policy

- **Forbidden attributes:** race, ethnicity, religion, sexual orientation, inferred gender identity, health, immigration status, household income, exact addresses (street/ZIP), phone numbers, email addresses, real names beyond first-name patterns the redactor catches.
- **On detection:** REJECT the entire record. Never partially store. Increment the `rejection_reasons` counter in the run summary.

## 13. Allowed downstream use (if approved)

- **Persona construction:** yes — Phase 8.2C+ persona construction worker turns `source_records` into anonymous `persona_records` via `cost_guarded_chat`-driven trait extraction with full validator enforcement.
- **Direct user-facing display:** **No.** Raw content never reaches users. Only redacted excerpts via the `evidence_anchor_details` audit-detail mechanism (Phase 8 frontend).
- **Aggregation / analysis only:** yes — the Population Construction Audit panel surfaces `source_kind_counts` and trait-support breakdowns.

## 14. Refresh / deletion policy

- **Refresh cadence:** No automatic refresh in V0. Manual re-ingest only after compliance re-review.
- **Source deletion request:** A deleted Reddit post or removed comment, on next refresh, would soft-delete the corresponding `source_record` (and FK CASCADE through `persona_evidence_links` and downstream).
- **Retention:** Source records retained for the lifetime of the simulations they support; deleted on simulation delete via FK CASCADE (Phase 8.2A schema).

## 15. Risks + mitigations

- **Risk:** Reddit's commercial-use stance shifts again. **Mitigation:** status can be flipped to `'suspended'` immediately; the framework refuses ingestion with a structured `ADAPTER_SUSPENDED` error.
- **Risk:** Public usernames coincide with real-name handles. **Mitigation:** salted hash + redaction layer + no user-facing surfacing of the hash. Random `display_name` generation in persona-construction is decoupled from any source content.
- **Risk:** Rate-limit overrun after API tier change. **Mitigation:** enforced limit is 50% of the documented limit; the adapter's run summary records throttle events.
- **Risk:** Subreddit-specific ToS variations. **Mitigation:** subreddit allow-list is closed and re-reviewed per subreddit before adding.

## 16. Sign-off

- **Engineering owner:** _<unassigned — pending Phase 8.2D kickoff>_
- **Privacy / legal reviewer:** _<unassigned — pending review>_
- **Status:** **NOT APPROVED.** No live Reddit ingestion authorized. Phase 8.2C uses the mocked adapter only.

---

**Memo last updated:** Phase 8.2C ship date.

**Until this memo's `Status` field reads `approved` AND the
`adapter_compliance_status` row carries populated `approver` and
`approved_at` columns, the framework REFUSES to run the Reddit
adapter — even in test mode the test must explicitly insert an
approved row to exercise the success path.**
