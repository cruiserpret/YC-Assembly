"""Phase 9A.4 — source-grounded persona memory atoms.

Every memory atom MUST cite a real origin row (origin_type +
origin_ref_id + origin_excerpt). Atoms are immutable: superseding a
memory creates a new atom with `invalidated_by_id` pointing back.
Cross-persona leakage is forbidden — retrieval scope is per-persona.

Retrieval formula (V1):
  score = recency_weight + importance_weight + relevance_weight

  recency_weight    = 1 / (1 + recency_index)        (newer atoms boost)
  importance_weight = importance_score / 10           (1..10 → 0.1..1.0)
  relevance_weight  = lexical_overlap(query, memory)

No embeddings in V1 — keyword-overlap is enough for the discussion
prompts to surface psychologically relevant atoms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class MemoryAtomDraft:
    """Pre-persistence shape of a memory atom. The orchestrator
    converts these into PersonaMemoryAtom rows."""

    persona_id: str
    run_scope_id: str
    memory_type: str          # 'evidence' | 'trait' | 'psychology' | ...
    origin_type: str          # 'source_record' | 'persona_trait' | ...
    origin_ref_id: str        # UUID string of the origin row
    origin_excerpt: str       # raw excerpt — REQUIRED, non-empty
    memory_text: str          # the persona-relative summary
    importance_score: int     # 1..10
    recency_index: int = 0
    relevance_tags: tuple[str, ...] = ()


_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _overlap(query_tokens: set[str], memory_tokens: set[str]) -> float:
    if not memory_tokens or not query_tokens:
        return 0.0
    inter = query_tokens & memory_tokens
    return len(inter) / max(len(memory_tokens), 1)


def rank_memory_atoms(
    *,
    atoms: Iterable[Any],
    query: str,
    top_k: int = 6,
) -> list[Any]:
    """Score each atom by recency × importance × relevance and return
    the top-K. `atoms` is an iterable of objects with attributes:
    `recency_index`, `importance_score`, `memory_text`, `origin_excerpt`,
    `relevance_tags`. Works for both `PersonaMemoryAtom` ORM rows and
    `MemoryAtomDraft` dataclasses (uses getattr)."""
    qtok = _tokens(query)
    scored: list[tuple[float, Any]] = []
    for a in atoms:
        recency = float(getattr(a, "recency_index", 0) or 0)
        recency_w = 1.0 / (1.0 + recency)
        importance = float(getattr(a, "importance_score", 5) or 5)
        importance_w = importance / 10.0
        text = " ".join((
            (getattr(a, "memory_text", "") or ""),
            (getattr(a, "origin_excerpt", "") or ""),
            " ".join(getattr(a, "relevance_tags", []) or []),
        ))
        rel = _overlap(qtok, _tokens(text))
        score = 0.4 * recency_w + 0.3 * importance_w + 0.3 * rel
        scored.append((score, a))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [a for _, a in scored[:top_k]]


def build_seed_memory_atoms(
    *,
    persona_id: str,
    run_scope_id: str,
    persona_traits: list[dict[str, Any]],
    psychology_traits: list[dict[str, Any]],
    evidence_links: list[dict[str, Any]],
    prior_simulation_responses: list[dict[str, Any]],
) -> list[MemoryAtomDraft]:
    """Synthesize the persona's seed memory bag from existing rows.

    This is the only place where memory atoms are MINTED. Every atom
    cites a real origin row. No fabrication.

    Required dict shapes:
      persona_traits:  {trait_id, field_name, value, rationale, confidence}
      psychology_traits: {trait_id, trait_name, value_label, value_numeric,
                          confidence, evidence_basis}
      evidence_links:  {link_id, source_record_id, excerpt, contribution_field}
      prior_simulation_responses: {response_id, reasoning, stance, round_type}

    Top-N rules:
      - up to 4 PersonaTrait atoms (highest confidence first)
      - up to 4 PersonaPsychologyTrait atoms (highest |value-0.5| first)
      - up to 4 PersonaEvidenceLink atoms (longest excerpts first)
      - up to 4 prior simulation atoms (latest rounds first)
    """
    out: list[MemoryAtomDraft] = []

    # --- PersonaTrait atoms
    pt_sorted = sorted(
        persona_traits,
        key=lambda t: -float(t.get("confidence") or 0.0),
    )[:4]
    for t in pt_sorted:
        excerpt = (t.get("rationale") or t.get("value") or "").strip()
        if not excerpt:
            continue
        importance = max(
            1, min(10, int(round(float(t.get("confidence") or 0.5) * 10)))
        )
        out.append(MemoryAtomDraft(
            persona_id=persona_id,
            run_scope_id=run_scope_id,
            memory_type="trait",
            origin_type="persona_trait",
            origin_ref_id=str(t["trait_id"]),
            origin_excerpt=excerpt[:500],
            memory_text=(
                f"My '{t.get('field_name')}' trait: "
                f"{(t.get('value') or '').strip()}"
            )[:500],
            importance_score=importance,
            relevance_tags=tuple(filter(None, [
                t.get("field_name"),
            ])),
        ))

    # --- PersonaPsychologyTrait atoms (most extreme first)
    psy_sorted = sorted(
        psychology_traits,
        key=lambda t: -abs(float(t.get("value_numeric") or 0.5) - 0.5),
    )[:4]
    for t in psy_sorted:
        evidence_basis = (
            t.get("evidence_basis") or t.get("caveat") or ""
        ).strip()
        if not evidence_basis:
            continue
        importance = max(
            1, min(10, int(round(
                abs(float(t.get("value_numeric") or 0.5) - 0.5) * 20,
            ))) or 5,
        )
        out.append(MemoryAtomDraft(
            persona_id=persona_id,
            run_scope_id=run_scope_id,
            memory_type="psychology",
            origin_type="persona_psychology_trait",
            origin_ref_id=str(t["trait_id"]),
            origin_excerpt=evidence_basis[:500],
            memory_text=(
                f"My {t.get('trait_name')} is {t.get('value_label')} "
                f"(value={t.get('value_numeric')})."
            )[:500],
            importance_score=max(2, importance),
            relevance_tags=("psychology", t.get("trait_name") or ""),
        ))

    # --- PersonaEvidenceLink atoms (longest first)
    ev_sorted = sorted(
        evidence_links,
        key=lambda l: -len(l.get("excerpt") or ""),
    )[:4]
    for ev in ev_sorted:
        excerpt = (ev.get("excerpt") or "").strip()
        if not excerpt:
            continue
        out.append(MemoryAtomDraft(
            persona_id=persona_id,
            run_scope_id=run_scope_id,
            memory_type="evidence",
            origin_type="persona_evidence_link",
            origin_ref_id=str(ev["link_id"]),
            origin_excerpt=excerpt[:500],
            memory_text=(
                f"Evidence I'm anchored to "
                f"({ev.get('contribution_field') or 'context'}): {excerpt}"
            )[:500],
            importance_score=6,
            relevance_tags=tuple(filter(None, [
                ev.get("contribution_field"),
            ])),
        ))

    # --- prior simulation responses (most recent / final-stance first)
    sim_sorted = sorted(
        prior_simulation_responses,
        key=lambda r: 1 if r.get("round_type") == "final_stance" else 0,
        reverse=True,
    )[:4]
    for r in sim_sorted:
        reasoning = (r.get("reasoning") or "").strip()
        if not reasoning:
            continue
        out.append(MemoryAtomDraft(
            persona_id=persona_id,
            run_scope_id=run_scope_id,
            memory_type="prior_simulation",
            origin_type="agent_response",
            origin_ref_id=str(r["response_id"]),
            origin_excerpt=reasoning[:500],
            memory_text=(
                f"In the prior simulation ({r.get('round_type')}), "
                f"I said: {reasoning[:280]}"
            )[:500],
            importance_score=7,
            relevance_tags=("prior_simulation", r.get("round_type") or ""),
        ))

    return out
