# Phase 11A — tiny Amazon Reviews provider fixtures

Hand-authored synthetic reviews used only by `test_amazon_reviews_provider.py`. **Not** real Amazon reviews; every row was written specifically to exercise one or more distiller rules. Total budget across all categories: ~30 review rows, far below the soft cap of 50 in the operator spec.

Layout mirrors the McAuley Lab Amazon Reviews 2023 on-disk shape so the existing Phase 8.5A reader can stream them without modification:

```
raw/
  Electronics_reviews.jsonl       — 10 reviews
  Electronics_meta.jsonl          — 5 products
  All_Beauty_reviews.jsonl        — 10 reviews
  All_Beauty_meta.jsonl           — 5 products
  Home_and_Kitchen_reviews.jsonl  — 10 reviews
  Home_and_Kitchen_meta.jsonl     — 5 products
```

Each review row carries (parent_asin, asin, rating, title, text, helpful_vote, verified_purchase, timestamp, user_id) per the 8.5A `parse_amazon_review_line` schema. Each `*_meta.jsonl` row carries (parent_asin, title, store) per the 8.5B `_read_metadata` reader.

No real ASINs, no real brand names, no real user identifiers. Brand names are obvious placeholders (`BrandX`, `AltCompany`, …) and the `user_id` field is a plain `synthetic_*` string the parser still hashes to a 16-hex stub.
