# Source compliance — Brave Search API

**Production status:** review (Phase 8.5A scaffolding only; no live ingestion runs yet).

## Source purpose
Brave Search is a third-party commercial search API. Assembly uses it as a **discovery** provider — Brave returns title + URL + snippet metadata for candidate pages that may contain energy-drink / category / competitor / safety evidence. Brave does NOT supply persona evidence directly; URLs surfaced here are CANDIDATE evidence and must flow through the existing extraction + redaction + sensitive-filter + dedup pipeline before any persona ever sees them.

## Allowed use
- Bounded discovery queries to surface comparison content (e.g. "Red Bull vs Monster review", "Celsius energy drink review").
- Result snippets and URLs may be written into a Phase 8.5A audit JSON so the operator can review what Brave returned before approving extraction.
- Per-query domain-deduplication.

## Disallowed use
- NEVER use Brave to power production-time autocomplete or end-user-visible search.
- NEVER fan out queries unbounded — every Brave call site MUST go through `BraveSearchClient` with explicit `BraveAdapterConfig` caps.
- NEVER bypass the framework's existing extraction + redaction pipeline. A Brave URL is a CANDIDATE for extraction, not persona evidence.
- NEVER scrape Brave Search results from the consumer-facing site (search.brave.com); the official API is the only allowed surface.

## Data retained
- Query string (operator-supplied)
- Result title, canonical URL (with tracking-style query-string params stripped via `redact_url_for_audit`), domain, snippet text, optional `age` field
- Per-call timestamp and result count

## Data NOT retained
- The Brave API key (read from environment only; never logged, written, or echoed)
- Any tracking-style query-string params on result URLs
- Any session cookies or third-party fingerprints

## Privacy rules
- Brave Search results are public web metadata. Treat Phase 8.5A audit output as operator-only — do not surface to end users until extraction + sensitive-filter pass.
- If any returned URL points to an identity-shaped page (`/u/<name>`, `/user/<name>`, `/@<name>`), the existing extraction-rejection rules from Phase 8.2E apply at the next stage.

## API key handling
- `BRAVE_SEARCH_API_KEY` is read ONLY from the process environment via `os.environ.get(...)`.
- The key is NEVER passed via CLI flag, NEVER written to disk, NEVER echoed to logs, NEVER embedded in audit JSON, NEVER carried into `repr()` / `__str__`.
- Drift test asserts the literal string `BRAVE_SEARCH_API_KEY` does not appear outside the adapter file (and config) — preventing accidental key-handling sprawl.

## Rate / cost caps
- Phase 8.5A preflight cap: **3 queries × 5 results** per invocation.
- The `BraveAdapterConfig` dataclass is the single per-invocation cap surface; `BraveSearchClient.search` raises if the caller passes more queries than `max_queries`.
- Future production ingestion will set caps via environment + cost-guard, NOT raise these scaffolding caps blindly.

## Persona / source-record creation
- Phase 8.5A creates **zero** `source_records`, **zero** `persona_records`, **zero** `persona_traits`, **zero** `persona_evidence_links`.
- Future ingestion (Phase 8.5B+) requires a separate operator approval gate.

## Operator notes
- Live preflight is gated behind the `--live` flag of `scripts/brave_source_preflight_8_5a.py`. Default invocation is dry-run.
- Re-running the preflight is cheap and idempotent.
