"""Phase 6.75 — evidence graph package.

Builds a typed, deduplicated, embeddable graph over `evidence_items` rows
that the society builder grounds traits against and the Phase 7 aggregator
will read for the final report. Phase 6.75 produces the graph and exposes
a stable `EvidenceRetriever` + Phase-7 helper service. Phase 7 itself is
deferred.

Public surface:
  - `build_evidence_graph(...)` — top-level orchestrator (idempotent).
  - `EvidenceRetriever` — graph-ranked retrieval with BM25 fallback.
  - `EvidenceGraphService` — Phase-7-friendly helper interface.

Per the standing entry conditions (O1), every LLM call inside this package
must flow through `cost_guarded_chat` / `cost_guarded_embed`. The AST drift
test in `tests/test_no_drift.py` enforces this.
"""
from __future__ import annotations

from assembly.pipeline.evidence_graph.builder import (
    EvidenceGraphResult,
    build_evidence_graph,
)
from assembly.pipeline.evidence_graph.retriever import (
    EvidenceRetriever,
    RankedEvidence,
)
from assembly.pipeline.evidence_graph.service import EvidenceGraphService

__all__ = [
    "EvidenceGraphResult",
    "EvidenceGraphService",
    "EvidenceRetriever",
    "RankedEvidence",
    "build_evidence_graph",
]
