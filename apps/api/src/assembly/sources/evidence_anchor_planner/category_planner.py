"""Phase 8.5D.1B — dynamic Amazon-category planner.

`generate_source_category_plan(brief, *, dataset_dir, available_categories,
sample_per_category)` decides which local Amazon categories to scan
deeply for a given founder brief — without any hardcoded
brief-to-category mapping.

How it works (universal, data-driven):

  1. For each available local Amazon category, stream the first N
     metadata records (default 5,000) from `meta_<Category>.jsonl`.
  2. Count how many of the brief's competitors appear in
     metadata.title + metadata.categories of those records.
  3. A category's relevance score = total competitor-name hits.
  4. Categories with relevance >= 1 are selected for deep scanning;
     the rest are excluded.
  5. The plan is fully data-driven: it does not encode "balm goes to
     Beauty & Personal Care" or "drink goes to Grocery". It uses the
     brief's own competitor list as the discovery signal.

This generalizes to any product: the brief's competitor names tell
the planner where the source evidence lives.
"""
from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from assembly.sources.evidence_anchor_planner.schemas import (
    ProductBriefForPlanning,
)


@dataclass(frozen=True)
class SourceCategoryPlan:
    """Per-category relevance + selection. Pure-data shape."""
    available_categories: tuple[str, ...]
    selected_categories: tuple[str, ...]
    excluded_categories: tuple[str, ...]
    relevance_per_category: dict[str, dict]
    selection_rule: str
    sample_per_category: int
    generated_from: str = "deterministic_competitor_metadata_scan"
    caveats: tuple[str, ...] = field(default_factory=tuple)


def _open_meta(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def generate_source_category_plan(
    brief: ProductBriefForPlanning,
    *,
    dataset_dir: Path,
    available_categories: list[str],
    sample_per_category: int = 5000,
    min_relevance_to_select: int = 1,
    product_type_tokens: tuple[str, ...] | None = None,
) -> SourceCategoryPlan:
    """Return a category plan derived from a metadata scan.

    NEVER hardcodes brief-to-category mappings. Two universal
    discovery signals are combined:

      1. The brief's `competitors` list — wherever a competitor
         brand name appears in metadata, that category is relevant.
      2. The brief's product-type tokens (`balm`, `cream`, `drink`,
         `stick`, etc.) — wherever the product-type word appears in
         metadata title, that category is also category-relevant.

    Both signals are evidence — `total_hits` aggregates them. The
    selection rule remains "any category with >= 1 evidence hit
    in the metadata sample." The product-type signal is essential
    when competitor brands appear DEEPER in the metadata file than
    the bounded sample reaches.
    """
    raw_dir = dataset_dir / "raw"
    relevance: dict[str, dict] = {}
    competitors_lower = [c.lower() for c in brief.competitors if c]
    if product_type_tokens is None:
        # Derive product-type tokens from the brief's own description
        # (universal — no per-product code path).
        from assembly.sources.evidence_anchor_planner.constants import (
            UNIVERSAL_GENERIC_MODIFIERS, UNIVERSAL_STOPWORDS,
        )
        import re as _re
        tokens: list[str] = []
        gen_set = frozenset(t.lower() for t in UNIVERSAL_GENERIC_MODIFIERS)
        for m in _re.finditer(r"[A-Za-z][A-Za-z0-9'-]*", brief.product_description):
            t = m.group(0).lower()
            if (
                len(t) >= 4
                and t not in UNIVERSAL_STOPWORDS
                and t not in gen_set
            ):
                tokens.append(t)
        # Cap at top-15 by frequency to keep signal density high
        from collections import Counter as _Counter
        product_type_tokens = tuple(
            t for t, _ in _Counter(tokens).most_common(15)
        )
    pt_lower = [t.lower() for t in product_type_tokens]

    for cat in available_categories:
        # Look for either uncompressed or .gz
        meta_candidates = [
            raw_dir / f"meta_{cat}.jsonl",
            raw_dir / f"meta_{cat}.jsonl.gz",
        ]
        meta_file = next((p for p in meta_candidates if p.is_file()), None)
        if meta_file is None:
            relevance[cat] = {
                "n_scanned": 0,
                "competitor_hits": {}, "product_type_hits": {},
                "total_hits": 0,
                "metadata_file_present": False,
            }
            continue
        comp_hits: Counter = Counter()
        pt_hits: Counter = Counter()
        n_scanned = 0
        try:
            with _open_meta(meta_file) as fh:
                for line in fh:
                    if n_scanned >= sample_per_category:
                        break
                    n_scanned += 1
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    title = (obj.get("title") or "").lower()
                    cats_str = " ".join(
                        obj.get("categories") or []
                    ).lower()
                    blob = title + " | " + cats_str
                    for comp in competitors_lower:
                        if not comp:
                            continue
                        if comp in blob:
                            comp_hits[comp] += 1
                    for tok in pt_lower:
                        if not tok:
                            continue
                        # word-boundary match for short/common tokens
                        # to avoid e.g. "bar" matching "barber"
                        if re.search(rf"\b{re.escape(tok)}\b", blob):
                            pt_hits[tok] += 1
        except OSError:
            pass
        relevance[cat] = {
            "n_scanned": n_scanned,
            "competitor_hits": dict(comp_hits),
            "product_type_hits": dict(pt_hits),
            "total_hits": int(sum(comp_hits.values()) + sum(pt_hits.values())),
            "metadata_file_present": True,
        }

    selected = tuple(
        cat for cat in available_categories
        if relevance.get(cat, {}).get("total_hits", 0)
        >= min_relevance_to_select
    )
    excluded = tuple(
        cat for cat in available_categories if cat not in selected
    )
    selection_rule = (
        f"select categories where >=1 brief competitor appears in "
        f"metadata title or categories of the first "
        f"{sample_per_category:,} records (data-driven; no hardcoded "
        "brief-to-category mapping)"
    )
    caveats: list[str] = [
        "Plan is deterministic — derived purely from competitor "
        "name hits in metadata. NO LLM. NO hardcoded brief-to-"
        "category logic.",
        "Sample is bounded; categories with no metadata hits in the "
        "first N records may be missed if the competitor-product "
        "appears later. For a more thorough scan, raise "
        "`sample_per_category`.",
    ]
    if not selected:
        caveats.append(
            "No category surfaced any brief competitor in the sampled "
            "metadata. Either the product brief's competitors are not "
            "represented in any downloaded local Amazon category, or "
            "the sample size needs to be raised."
        )
    return SourceCategoryPlan(
        available_categories=tuple(available_categories),
        selected_categories=selected,
        excluded_categories=excluded,
        relevance_per_category=relevance,
        selection_rule=selection_rule,
        sample_per_category=sample_per_category,
        caveats=tuple(caveats),
    )
