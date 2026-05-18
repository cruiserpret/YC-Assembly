"""Extract bucket counts from an existing Assembly founder_report
artifact.

Reads the ``intent_distribution`` block (already produced by Phase
10A.3) and folds Assembly's intent labels into the four calibration
buckets via :mod:`assembly.calibration.market_buckets`. Raw labels
are preserved alongside the bucket counts so a reviewer can audit
the mapping post-hoc.

This module is intentionally pure: file-system reads only, no LLM
calls, no DB writes. It does not modify the founder_report — the
calibration package is read-only over Assembly's artifacts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assembly.calibration.market_buckets import (
    BUCKET_NAMES,
    MarketBucket,
    map_assembly_intent_to_market_bucket,
)

logger = logging.getLogger(__name__)


@dataclass
class BucketCounts:
    """Per-bucket counts plus an audit trail.

    ``raw_labels`` is the original ``{label: count}`` dict so the
    auditor can re-derive the mapping if the bucket vocabulary
    evolves. ``warnings`` carries the unknown-label notices emitted
    by :func:`map_assembly_intent_to_market_bucket`.
    """

    buyer: int = 0
    receptive: int = 0
    uncertain: int = 0
    skeptical: int = 0
    raw_labels: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.buyer + self.receptive + self.uncertain + self.skeptical

    def as_dict(self) -> dict[MarketBucket, int]:
        return {
            "buyer": self.buyer,
            "receptive": self.receptive,
            "uncertain": self.uncertain,
            "skeptical": self.skeptical,
        }

    def as_distribution(self) -> dict[MarketBucket, float]:
        """Return as fraction (sum=1.0). Empty input → flat prior 0.25
        per bucket so downstream math doesn't divide by zero."""
        t = self.total
        if t <= 0:
            return {b: 0.25 for b in BUCKET_NAMES}
        return {
            "buyer": self.buyer / t,
            "receptive": self.receptive / t,
            "uncertain": self.uncertain / t,
            "skeptical": self.skeptical / t,
        }


def extract_bucket_counts_from_intent_distribution(
    intent_distribution: dict[str, int] | dict[str, float],
    *,
    payment_intent_explicit: bool = False,
) -> BucketCounts:
    """Convert ``{intent_label: count}`` into a :class:`BucketCounts`.

    ``payment_intent_explicit`` is forwarded into the mapper so a
    caller that has independent evidence of payment intent can lift
    waitlist signups into the ``buyer`` bucket. Default False.
    """
    counts = BucketCounts()
    for label, raw_count in (intent_distribution or {}).items():
        try:
            cnt = int(raw_count)
        except (TypeError, ValueError):
            # Refuse to silently coerce a float like 3.7 into 3.
            counts.warnings.append(
                f"non_integer_count_for_label={label!r}: {raw_count!r}"
            )
            continue
        if cnt < 0:
            counts.warnings.append(
                f"negative_count_for_label={label!r}: {cnt}"
            )
            continue
        bucket, warning = map_assembly_intent_to_market_bucket(
            label,
            payment_intent_explicit=payment_intent_explicit,
        )
        counts.raw_labels[label] = (
            counts.raw_labels.get(label, 0) + cnt
        )
        if warning:
            counts.warnings.append(warning)
        if bucket == "buyer":
            counts.buyer += cnt
        elif bucket == "receptive":
            counts.receptive += cnt
        elif bucket == "skeptical":
            counts.skeptical += cnt
        else:
            counts.uncertain += cnt
    return counts


def extract_bucket_counts_from_founder_report(
    path: str | Path,
    *,
    payment_intent_explicit: bool = False,
) -> BucketCounts:
    """Read ``founder_report.json`` and extract bucket counts from its
    intent distribution.

    Looks for the intent distribution in this priority order:
      1. ``synthetic_intent_snapshot.intent_distribution``
      2. ``intent_snapshot.intent_distribution``
      3. ``executive_summary.intent_distribution``  (legacy)
      4. top-level ``intent_distribution``           (legacy)

    Raises ``FileNotFoundError`` if the path doesn't exist and
    ``ValueError`` if no intent distribution is found in the file.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"founder_report not found: {p!s}")
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    intent_dist = _locate_intent_distribution(data)
    if intent_dist is None:
        raise ValueError(
            f"no intent_distribution block found in {p!s}; expected "
            "synthetic_intent_snapshot.intent_distribution or "
            "intent_snapshot.intent_distribution"
        )
    return extract_bucket_counts_from_intent_distribution(
        intent_dist,
        payment_intent_explicit=payment_intent_explicit,
    )


def _locate_intent_distribution(
    data: dict[str, Any],
) -> dict[str, int] | None:
    """Walk a founder_report dict and return its intent distribution
    block, or None. Tolerant to schema drift across Phase 9/10 outputs.
    """
    candidates: list[tuple[str, dict | None]] = [
        ("synthetic_intent_snapshot",
         data.get("synthetic_intent_snapshot")),
        ("intent_snapshot", data.get("intent_snapshot")),
        ("executive_summary", data.get("executive_summary")),
    ]
    for _src, block in candidates:
        if isinstance(block, dict):
            dist = block.get("intent_distribution")
            if isinstance(dist, dict) and dist:
                return dist
    # Legacy top-level
    top = data.get("intent_distribution")
    if isinstance(top, dict) and top:
        return top
    return None
