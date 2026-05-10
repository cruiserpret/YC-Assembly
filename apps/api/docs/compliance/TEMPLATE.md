# Compliance memo — TEMPLATE

> Copy this file to `apps/api/docs/compliance/<adapter_name>.md` and fill
> in the sections below. An adapter cannot run unless this memo exists
> AND `adapter_compliance_status.status='approved'` AND
> `approver` + `approved_at` are populated.

## 1. Status
- **Status:** draft | review | approved | suspended
- **Approver:** _<name + date when status flips to approved>_
- **Last reviewed:** _<YYYY-MM-DD>_

## 2. Source name + scope
- **Source:** _e.g. "Reddit Public API (read-only)"_
- **Endpoints / pages accessed:** _<closed allow-list>_
- **Date window:** _e.g. "posts captured_at <= cutoff_date, last 90 days"_

## 3. Access method
- **API or HTML or other:** _api | html | other_
- **Authentication:** _e.g. "client_id + client_secret only; no user OAuth"_
- **Browser automation:** _yes / no_ — Phase 8.2C forbids it
- **Library / client:** _e.g. "PRAW v7.7.x"_

## 4. robots.txt status (if applicable)
- **Path:** _<URL of robots.txt>_
- **Last fetched:** _<YYYY-MM-DD>_
- **Disallow rules relevant to our access:** _<verbatim>_

## 5. Terms of Service notes
- **ToS link:** _<URL>_
- **ToS excerpt that permits this access:** _<verbatim quote>_
- **ToS sections that restrict our access (and how we comply):** _<list>_

## 6. Commercial-use notes
- **Is the source's ToS clear about commercial / paid product use?** _yes / no / ambiguous_
- **If ambiguous:** _<note that human legal review is required before approval>_
- **Pricing / API tier:** _e.g. "free tier 60 req/min; paid tier required at >60 req/min"_

## 7. Rate limits
- **Documented limits:** _<value>_
- **Limit we enforce:** _<value>_ (must be ≤ documented)

## 8. What we collect
- **Fields we read:** _<closed list>_
- **Fields we explicitly drop before storing:** _<list>_
- **Per-record content cap:** _e.g. "≤ 4000 chars; truncated with marker"_

## 9. What we do NOT collect
- _<e.g. "private DMs, locked subreddits, deleted content, modmail">_
- _<e.g. "user account history beyond the post/comment we ingest">_

## 10. Identity surface
- **Identifiers in the raw response:** _<e.g. "username (string)">_
- **Storage policy:** _<e.g. "username is salted-hashed via `hash_public_handle`; raw username is never written">_
- **User-facing exposure:** _<e.g. "no username in any API response; debug-mode only shows the hash, never the raw">_

## 11. Redaction-before-storage path
- **Pre-store redaction:** _<list>_ (must include emails, phones, @handles, profile URLs at minimum)
- **`pii_redaction_status` target:** _<e.g. "always 'redacted'; on redactor failure, record REJECTED, not stored">_
- **`sensitive_scan_status` target:** _<e.g. "scanned; rejected if any sensitive attribute hit; never stored intact">_

## 12. Sensitive data policy
- **What we explicitly forbid:** sensitive attributes (race, ethnicity, religion, sexual orientation, gender identity inferred, health, immigration, income), exact addresses, ZIP codes, phone numbers, email addresses, raw real names beyond first-name patterns the redactor catches.
- **What we do on detection:** REJECT the entire record; never partially store.

## 13. Allowed downstream use
- **Persona construction:** _yes / no_
- **Direct user-facing display:** _no — never raw; only redacted excerpts via the existing `evidence_anchor_details` mechanism_
- **Aggregation / analysis only:** _yes / no_

## 14. Refresh / deletion policy
- **Refresh cadence:** _<e.g. "no automatic refresh in V0; manual re-ingest">_
- **Source deletion request:** _<e.g. "if a user deletes the original post, our next refresh job soft-deletes the source_record + cascades to persona_evidence_links">_
- **Retention:** _<e.g. "indefinite for active simulations; deleted on simulation delete via FK CASCADE">_

## 15. Risks + mitigations
- _<Risk 1>_: _<mitigation>_
- _<Risk 2>_: _<mitigation>_

## 16. Sign-off
- **Engineering owner:** _<name>_
- **Privacy / legal reviewer:** _<name + date>_
- **Status:** _approved | not approved_

---

**Template last updated:** Phase 8.2C ship date.

The framework will refuse to run any adapter whose memo is `draft` or
`review` or `suspended`, or whose memo file is missing entirely.
