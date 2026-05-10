# Source compliance — Amazon Reviews 2023 (LOCAL dataset)

**Production status:** review (Phase 8.5A scaffolding only; no live ingestion runs yet).

## Source purpose
The McAuley Lab "Amazon Reviews 2023" dataset (https://amazon-reviews-2023.github.io/) is a **local historical** corpus of public Amazon product reviews, released for research. Assembly uses it to surface category objections on energy drinks / pre-workout / sports drinks: taste, price, sugar, caffeine, crash, packaging, repeat-buy patterns, brand switching, and trust signals. It is the most comprehensive public-domain source for product-review evidence in these categories.

## Allowed use
- Local on-disk read of per-category JSONL or JSONL.gz files under `<AMAZON_REVIEWS_2023_DIR>/raw/`.
- Streaming reads (`AmazonReviewsLocalReader.iter_category`) capped per-category by `AmazonReviewsAdapterConfig.max_records_per_category`.
- Filtering by Triton-relevant search terms (`matches_search_terms`) BEFORE any record is treated as CANDIDATE evidence.
- Audit JSON written by the preflight script may contain matched-record excerpts for operator review.

## Disallowed use
- **NO Amazon API calls.** There is no Amazon API for reviews; calling Amazon Product Advertising API, MWS, SP-API, or any other Amazon service is out of scope and forbidden in this adapter.
- **NO Amazon.com scraping.** Drift test asserts the package contains no `httpx` / `requests` / `urllib` / `aiohttp` / `selenium` / `playwright` / `beautifulsoup4` / `bs4` / `scrapy` / `urllib3` imports.
- NEVER load full categories into memory; the reader is streaming.
- NEVER surface raw `user_id` to downstream callers; the user_id field is hashed (SHA-256, first 16 hex chars) at parse time. The hash is sufficient for cross-review dedup, insufficient for re-identification.
- NEVER surface review images or image URLs; the `images` field is dropped at parse time.
- NEVER use the dataset as evidence of CURRENT product attributes (caffeine load, sugar grams, etc.) — the dataset is historical (snapshot through 2023) and a 2023 review is not authoritative about a 2026 product.

## Data retained
- Per record: `category` (assigned at file-discovery time), `parent_asin`, `asin`, `rating`, `title`, `text`, `helpful_vote`, `verified_purchase`, `timestamp`, `user_id_hash`
- Per-preflight: matched-record counts per category, term-hit Counter, sample matched records (≤5 per category) for operator review

## Data NOT retained
- Raw `user_id` (always hashed before retention)
- Image URLs or any image fields
- Reviewer profile, profile photo, profile URL
- Anything outside the per-record schema enumerated above

## Privacy rules
- The dataset is public research data, but Assembly still hashes user_id to prevent any inadvertent cross-source re-identification (e.g. matching a user_id here against another future leaked corpus).
- All review text passes through the existing redaction + sensitive-filter pipeline before any persona ever sees it.
- Reviews flagged by `looks_like_low_quality_review` (too short, too long, all-caps spam) are dropped before downstream processing.

## API key handling
- N/A — there is no API key. The dataset is local files only.
- `AMAZON_REVIEWS_2023_DIR` is a filesystem path env var, not a credential. The directory itself does not contain secrets.

## Categories supported
- Specific list via `AMAZON_REVIEWS_2023_CATEGORIES=Cat1,Cat2,Cat3` (comma-separated category names matching the dataset's filename convention, e.g. `Grocery_and_Gourmet_Food`).
- Sentinel `AMAZON_REVIEWS_2023_CATEGORIES=ALL` to load every category present under the dataset directory. The adapter is **future-proofed for ALL** — `resolve_categories(...)` returns the literal `"ALL"` sentinel and `discover_category_files(...)` discovers every category at the directory level. Phase 8.5A does NOT actually ingest in `ALL` mode; that's a separate approval-gated phase.

## Rate / cost caps
- Phase 8.5A preflight cap: **1000 records per category** inspected from the FIRST matched file per category.
- The adapter NEVER fetches network resources; the only "rate limit" is local disk I/O.

## Historical-dataset caveat (must be acknowledged downstream)
- The dataset is a 2023 snapshot. Treat reviews as evidence of **historical category sentiment** — useful for objection patterns (sugar crash, flavor preferences, price sensitivity) but NOT authoritative about any 2026 product attribute.
- Any persona created from this evidence (in a future approved phase) MUST carry a "historical-evidence" caveat in their generated profile.

## Persona / source-record creation
- Phase 8.5A creates **zero** `source_records`, **zero** `persona_records`, **zero** `persona_traits`, **zero** `persona_evidence_links`.
- Future ingestion (Phase 8.5B+) requires a separate operator approval gate.

## Operator notes
- Phase 8.5A preflight `scripts/amazon_reviews_2023_preflight_8_5a.py` is safe to run any time — it is dry-run by default and only reads local files.
- If files are missing, the preflight reports the exact expected paths under `<dataset_dir>/raw/`. Operator must acquire the dataset (per the McAuley Lab distribution channel) before any ingestion phase.
