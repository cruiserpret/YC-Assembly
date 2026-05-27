"""Phase 8.5D.1E — deterministic persona-set compressor.

`compress_persona_set(...)` takes the 8.5D.1D candidate pool and
returns a smaller, non-duplicative `CompressedPersonaSet` plus the
list of rejected candidates with explicit reasons.

Universal by construction:
  * Quality scores are computed from `evidence_strength`, `confidence`,
    trait count, evidence-source count, and provider rarity bonus.
  * Within each normalized role, the strongest candidate wins; a
    second same-role candidate is admitted ONLY when its behavioral
    differential against the kept one ≥ `min_behavioral_differential`.
  * Quality gates are NEVER relaxed. A candidate that fails launch-
    state, lacks ≥2 traits, or is missing source evidence is
    rejected regardless of its role's representation.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from assembly.sources.persona_set_compressor.normalizer import (
    normalize_role_slug, normalize_role_slugs_for_candidates,
)
from assembly.sources.persona_set_compressor.schemas import (
    CompressedPersonaCandidate, CompressedPersonaSet,
    CompressionDiffSummary, CompressionPolicy, CompressionRejection,
    CompressionRejectionReason,
)


# Universal forbidden-phrase patterns for the launch-state validator —
# parameterized by product_name. Detects fake unlaunched-product use.
_FAKE_USE_TEMPLATES = (
    r"\b{p} buyer\b",
    r"\b{p} customer\b",
    r"\b{p} user\b",
    r"\b{p} reviewer\b",
    r"\b{p} loyalist\b",
    r"\bi (bought|tried|used|own|purchased|bought) {p}\b",
    r"\b{p} works (great|well|amazingly)\b",
    r"\bmy {p}\b",
    r"\brepeat purchase of {p}\b",
)


def _build_fake_use_patterns(product_name: str) -> list[re.Pattern]:
    p = re.escape(product_name.lower())
    pats: list[re.Pattern] = []
    for tmpl in _FAKE_USE_TEMPLATES:
        pats.append(re.compile(tmpl.format(p=p)))
    # also first-token match (e.g., "Stride" of "StrideShield")
    first = re.escape(product_name.split()[0].lower()) if product_name else ""
    if first and first != p:
        for tmpl in _FAKE_USE_TEMPLATES:
            pats.append(re.compile(tmpl.format(p=first)))
    return pats


_EVIDENCE_STRENGTH_SCORE = {
    "very_strong": 4, "strong": 3, "moderate": 2, "weak": 1,
}
_CONFIDENCE_SCORE = {"high": 3, "medium": 2, "low": 1}


def _quality_score(
    *,
    candidate: dict[str, Any],
    provider_pool_counts: Counter,
) -> float:
    """Deterministic quality score for one candidate."""
    es = _EVIDENCE_STRENGTH_SCORE.get(
        candidate.get("evidence_strength", "moderate"), 2,
    )
    cf = _CONFIDENCE_SCORE.get(candidate.get("confidence", "medium"), 2)
    n_traits = len(candidate.get("inferred_traits") or [])
    n_sources = len(candidate.get("source_record_ids") or [])
    # Provider rarity: rarer providers get a small bonus so
    # YouTube/Amazon candidates aren't drowned by Brave volume.
    provider = candidate.get("_source_provider_family", "unknown")
    provider_count = provider_pool_counts.get(provider, 0)
    total = sum(provider_pool_counts.values()) or 1
    provider_share = provider_count / total
    rarity_bonus = 1.0 - provider_share  # 0..1
    return round(
        es * 1.5 + cf * 1.0 + min(n_traits, 6) * 0.5
        + min(n_sources, 3) * 0.3 + rarity_bonus * 1.0,
        3,
    )


def _evidence_theme_for_candidate(
    *,
    candidate: dict[str, Any],
    source_records_by_id: dict[str, dict[str, Any]],
) -> str:
    """Pick a coarse evidence theme for the candidate.

    Priority:
      1. competitor:<X> match in any source's metadata.matched_terms
      2. substitute:<Y> match
      3. use_case:<Z> match
      4. fallback: candidate's normalized primary role
    """
    for sid in candidate.get("source_record_ids") or []:
        sr = source_records_by_id.get(sid)
        if not sr:
            continue
        terms = (sr.get("metadata") or {}).get("matched_terms") or []
        for t in terms:
            if t.startswith("competitor:") and "(wrong-context)" not in t:
                return f"competitor::{t.split(':', 1)[1].strip().lower()}"
        for t in terms:
            if t.startswith("substitute:"):
                return f"substitute::{t.split(':', 1)[1].strip().lower()}"
        for t in terms:
            if t.startswith("use_case:"):
                return f"use_case::{t.split(':', 1)[1].strip().lower()}"
    return f"role::{normalize_role_slug(candidate.get('inferred_persona_role', ''))}"


def _provider_family_for_candidate(
    *,
    candidate: dict[str, Any],
    source_records_by_id: dict[str, dict[str, Any]],
) -> str:
    """Pick the dominant provider family across the candidate's sources."""
    counts: Counter = Counter()
    for sid in candidate.get("source_record_ids") or []:
        sr = source_records_by_id.get(sid)
        if not sr:
            # If no matching planned-source row, fall back to the
            # synthetic prefix: planned::<brief>::<source_kind>::...
            if sid.startswith("planned::"):
                parts = sid.split("::")
                if len(parts) >= 3:
                    counts[parts[2]] += 1
                    continue
            counts["unknown"] += 1
            continue
        meta = sr.get("metadata") or {}
        provider = (
            meta.get("provider")
            or sr.get("source_kind")
            or "unknown"
        )
        counts[provider] += 1
    if not counts:
        return "unknown"
    return counts.most_common(1)[0][0]


def _canonical_phrase(s: str, max_tokens: int = 6) -> str:
    """Canonicalize a free-text phrase to its first N content tokens.

    Drops stopwords + very short tokens so that excerpt-derived
    objections/behaviors (which often start with filler words) collapse
    onto the same canonical key when they describe the same dimension.
    """
    if not s:
        return ""
    tokens = re.findall(r"[a-z0-9]+", s.lower())
    stop = {
        "the", "a", "an", "of", "to", "for", "and", "or", "is", "are",
        "in", "on", "with", "by", "as", "i", "my", "me", "you", "your",
        "this", "that", "it", "be", "was", "were", "do", "does", "did",
        "no", "not", "but", "so", "if", "then",
    }
    kept = [t for t in tokens if t not in stop and len(t) >= 3]
    return " ".join(kept[:max_tokens])


def _trait_signature(candidate: dict[str, Any]) -> set[str]:
    """Coarse trait signature.

    Uses trait_NAME only, not value. The name captures the *dimension*
    being inferred (e.g., `current_alternative_competitor`,
    `preference_performance_use_case`). Excerpt-derived trait_values
    differ per snippet even when the underlying inferred dimension
    is identical — using value would make every candidate look
    artificially distinct.
    """
    out: set[str] = set()
    for t in candidate.get("inferred_traits") or []:
        name = (t.get("trait_name") or "").lower().strip()
        if name:
            out.add(name)
    return out


def _objection_signature(candidate: dict[str, Any]) -> set[str]:
    """Canonicalize objections to first 6 content tokens so that
    excerpt-derived phrasings collapse when they describe the same
    underlying objection."""
    return {
        _canonical_phrase(o, max_tokens=6)
        for o in candidate.get("inferred_objections") or []
        if (o or "").strip()
    } - {""}


def _behavior_signature(candidate: dict[str, Any]) -> set[str]:
    return {
        _canonical_phrase(b, max_tokens=6)
        for b in candidate.get("inferred_behaviors") or []
        if (b or "").strip()
    } - {""}


def _behavioral_differential(
    *,
    new_cand: dict[str, Any],
    new_theme: str,
    new_provider: str,
    new_traits: set[str],
    new_objs: set[str],
    new_behs: set[str],
    kept_meta: dict[str, Any],
) -> tuple[int, list[str]]:
    """Return (score, reasons). Score in [0, 5]. Each axis worth +1.

    Axes:
      1. Different evidence theme (competitor / substitute / use-case
         bucket).
      2. Different provider family.
      3. Trait Jaccard distance ≥ 0.5.
      4. Objection Jaccard distance ≥ 0.5.
      5. Behavior Jaccard distance ≥ 0.5.
    """
    score = 0
    reasons: list[str] = []
    if new_theme != kept_meta["theme"]:
        score += 1
        reasons.append(
            f"different evidence theme ({new_theme} vs "
            f"{kept_meta['theme']})"
        )
    if new_provider != kept_meta["provider"]:
        score += 1
        reasons.append(
            f"different provider ({new_provider} vs "
            f"{kept_meta['provider']})"
        )
    for axis_name, new_set, kept_set in (
        ("traits", new_traits, kept_meta["traits"]),
        ("objections", new_objs, kept_meta["objections"]),
        ("behaviors", new_behs, kept_meta["behaviors"]),
    ):
        union = new_set | kept_set
        inter = new_set & kept_set
        jaccard = (len(inter) / len(union)) if union else 0.0
        if jaccard <= 0.5:
            score += 1
            reasons.append(f"distinct {axis_name} (jaccard={jaccard:.2f})")
    return score, reasons


def _has_fake_use_claim(
    *,
    candidate: dict[str, Any],
    fake_pats: list[re.Pattern],
) -> list[str]:
    """Scan evidence_summary + evidence_snippets +
    hypothetical_target_product_reaction for fake-target-use claims."""
    blob_parts: list[str] = []
    blob_parts.append(candidate.get("evidence_summary") or "")
    blob_parts.append(
        candidate.get("hypothetical_target_product_reaction") or "",
    )
    for s in candidate.get("evidence_snippets") or []:
        blob_parts.append(s or "")
    blob = " ".join(blob_parts).lower()
    hits: list[str] = []
    for p in fake_pats:
        if p.search(blob):
            hits.append(p.pattern)
    return hits


def _plan_id(
    *,
    target_brief_id: str,
    product_name: str,
    candidate_ids: list[str],
    role_map: dict[str, str],
) -> str:
    payload = "|".join((
        target_brief_id, product_name,
        ",".join(sorted(candidate_ids)),
        ",".join(f"{k}->{v}" for k, v in sorted(role_map.items())),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _competitor_concentration(roles: list[str]) -> float:
    comp_counter: Counter = Counter(
        r for r in roles if r.startswith("competitor_user_")
    )
    n = len(roles)
    if n == 0 or not comp_counter:
        return 0.0
    return round(comp_counter.most_common(1)[0][1] / n, 3)


def _diversity_score_for_set(
    candidates: list[dict[str, Any]],
    target_min_unique_roles: int = 4,
) -> float:
    """Calibrated diversity score for the compressed set.

    Uses `min(unique_roles / target_min_unique_roles, 1.0)` instead
    of `unique_roles / total_candidates` so a small high-quality set
    can score well. Same blend (0.6 × uniqueness + 0.4 × balance) as
    the persona_diversity_evaluator.
    """
    if not candidates:
        return 0.0
    primary_roles = [
        c.get("normalized_primary_role") or c.get("inferred_persona_role")
        for c in candidates
    ]
    unique = {r for r in primary_roles if r}
    role_uniqueness = min(
        len(unique) / max(target_min_unique_roles, 1), 1.0,
    )
    competitor_concentration = _competitor_concentration(primary_roles)
    return round(
        0.6 * role_uniqueness + 0.4 * (1.0 - competitor_concentration), 3,
    )


def _apply_hard_cap_stratified(
    *,
    compressed: list[CompressedPersonaCandidate],
    hard_max: int,
    max_competitor_user_total_share: float = 0.60,
) -> tuple[list[CompressedPersonaCandidate], list[CompressedPersonaCandidate], dict[str, Any]]:
    """Phase 9A.2 — universal stratified hard-cap selector.

    Truncates a compressed list to at most `hard_max` candidates by
    stratifying across role / provider / theme / objection / proof
    requirement / price-value diversity axes. NEVER random-slices.

    `max_competitor_user_total_share` is the AGGREGATE cap across all
    `competitor_user_*` sub-roles combined (mirrors the persona-quality
    gate in live_quality_gates.py). The per-role 35% cap is necessary
    but not sufficient: three competitor sub-roles at 25% each pass
    the per-role check but sum to 75%, which the gate rejects. By
    enforcing the aggregate here we keep compression and gating
    aligned, so the gate fails only when retrieval really cannot
    produce enough non-competitor voices.

    Returns:
        (kept_capped, dropped_overflow, audit)
    """
    if hard_max <= 0:
        raise ValueError(f"hard_max must be > 0; got {hard_max}")
    if len(compressed) <= hard_max:
        return list(compressed), [], {
            "applied": False,
            "input_count": len(compressed),
            "hard_max": hard_max,
            "reason": "input_within_cap",
        }

    # Sort by quality_score desc; tie-break on candidate_id for
    # determinism.
    sorted_by_q = sorted(
        compressed,
        key=lambda c: (-float(c.quality_score), c.candidate_id),
    )

    # Stratified passes — universal, no per-product hardcoding.
    selected: list[CompressedPersonaCandidate] = []
    selected_ids: set[str] = set()
    seen_role: set[str] = set()
    seen_provider: set[str] = set()
    seen_theme: set[str] = set()
    seen_role_provider: set[tuple[str, str]] = set()

    def _admit(c: CompressedPersonaCandidate) -> None:
        nonlocal competitor_used
        if c.candidate_id in selected_ids:
            return
        selected.append(c)
        selected_ids.add(c.candidate_id)
        seen_role.add(c.normalized_primary_role)
        seen_provider.add(c.source_provider_family)
        seen_theme.add(c.evidence_theme)
        seen_role_provider.add(
            (c.normalized_primary_role, c.source_provider_family),
        )
        if (c.normalized_primary_role or "").startswith(
            "competitor_user_",
        ):
            competitor_used += 1

    role_counts: Counter = Counter(
        c.normalized_primary_role for c in sorted_by_q
    )
    role_caps: dict[str, int] = {
        r: max(1, int(0.35 * hard_max))  # no role > 35%
        for r in role_counts
    }
    role_used: Counter = Counter()
    # Aggregate competitor_user_* cap. floor(share × hard_max) so a
    # 60% cap at hard_max=24 leaves room for 14 competitor users.
    competitor_total_cap = max(
        1, int(max_competitor_user_total_share * hard_max),
    )
    competitor_used = 0

    def _is_competitor(c: CompressedPersonaCandidate) -> bool:
        return (c.normalized_primary_role or "").startswith(
            "competitor_user_",
        )

    def _can_admit(c: CompressedPersonaCandidate) -> bool:
        nonlocal competitor_used
        if len(selected) >= hard_max:
            return False
        if c.candidate_id in selected_ids:
            return False
        cap = role_caps.get(c.normalized_primary_role, hard_max)
        if role_used[c.normalized_primary_role] >= cap:
            return False
        # Aggregate competitor cap — keeps compression aligned with the
        # downstream persona-quality gate's competitor_user_share check.
        if _is_competitor(c) and competitor_used >= competitor_total_cap:
            return False
        return True

    # Pass 1: best candidate per distinct primary role (universal
    # role-diversity floor).
    for c in sorted_by_q:
        if len(selected) >= hard_max:
            break
        if c.normalized_primary_role in seen_role:
            continue
        if _can_admit(c):
            _admit(c)
            role_used[c.normalized_primary_role] += 1

    # Pass 2: fill underrepresented provider families.
    for c in sorted_by_q:
        if len(selected) >= hard_max:
            break
        if c.candidate_id in selected_ids:
            continue
        if c.source_provider_family in seen_provider:
            continue
        if _can_admit(c):
            _admit(c)
            role_used[c.normalized_primary_role] += 1

    # Pass 3: fill underrepresented (role, provider) pairs.
    for c in sorted_by_q:
        if len(selected) >= hard_max:
            break
        if c.candidate_id in selected_ids:
            continue
        rp = (c.normalized_primary_role, c.source_provider_family)
        if rp in seen_role_provider:
            continue
        if _can_admit(c):
            _admit(c)
            role_used[c.normalized_primary_role] += 1

    # Pass 4: fill remaining slots by quality_score, respecting the
    # 35% role cap.
    for c in sorted_by_q:
        if len(selected) >= hard_max:
            break
        if c.candidate_id in selected_ids:
            continue
        if _can_admit(c):
            _admit(c)
            role_used[c.normalized_primary_role] += 1

    # If we still have slots and the 35% cap is the only blocker,
    # relax it to 40% and try again — universal soft fallback so
    # we don't UNDERFILL the hard cap. The aggregate competitor cap
    # is NOT relaxed: relaxing it would push us into the persona-
    # quality gate's failure region and abort the run downstream
    # anyway, so we prefer to underfill honestly.
    if len(selected) < hard_max:
        for c in sorted_by_q:
            if len(selected) >= hard_max:
                break
            if c.candidate_id in selected_ids:
                continue
            relaxed_cap = max(1, int(0.40 * hard_max))
            if role_used[c.normalized_primary_role] >= relaxed_cap:
                continue
            if _is_competitor(c) and competitor_used >= competitor_total_cap:
                continue
            _admit(c)
            role_used[c.normalized_primary_role] += 1

    dropped = [
        c for c in compressed if c.candidate_id not in selected_ids
    ]
    audit = {
        "applied": True,
        "input_count": len(compressed),
        "hard_max": hard_max,
        "kept_count": len(selected),
        "dropped_count": len(dropped),
        "passes": [
            "best per distinct primary role",
            "fill underrepresented providers",
            "fill underrepresented (role, provider)",
            "quality_score fill respecting role 35% cap + competitor aggregate cap",
            "soft relax to 40% role cap if underfilled (competitor aggregate NOT relaxed)",
        ],
        "selection_rule": (
            "stratified-by (role, provider, theme); within "
            "each pass, sort by quality_score desc; per-role cap "
            f"= max(1, 35% of {hard_max}); aggregate competitor_user_* "
            f"cap = max(1, {int(max_competitor_user_total_share * 100)}% "
            f"of {hard_max}) = {competitor_total_cap}; soft-relax role "
            "cap to 40% if slots empty (competitor cap NOT relaxed)."
        ),
        "role_concentration_after_cap": (
            f"{role_used.most_common(1)[0][0]}={role_used.most_common(1)[0][1]}/{len(selected)}"
            if role_used else None
        ),
        "competitor_user_total_cap": competitor_total_cap,
        "competitor_user_total_used": competitor_used,
        "competitor_user_total_share_after_cap": (
            round(competitor_used / len(selected), 3)
            if selected else 0.0
        ),
        "max_competitor_user_total_share": max_competitor_user_total_share,
    }
    # Re-order selected by quality_score for clean audit emission.
    selected.sort(
        key=lambda c: (-float(c.quality_score), c.candidate_id),
    )
    return selected, dropped, audit


def compress_persona_set(
    *,
    candidates: list[dict[str, Any]],
    planned_source_records: list[dict[str, Any]],
    target_brief_id: str,
    product_name: str,
    launch_state: str,
    generated_for_phase: str = "8.5D.1E",
    min_traits: int = 2,
    max_target_range: tuple[int, int] = (6, 8),
    min_behavioral_differential: int = 2,
    hard_max_compressed: int | None = None,
) -> CompressedPersonaSet:
    """Pure function. Same inputs → same output (modulo `generated_at`)."""
    if launch_state not in ("unlaunched", "launched", "in_market"):
        raise ValueError(f"unexpected launch_state: {launch_state!r}")

    # Index source records for theme + provider lookup
    source_by_id: dict[str, dict[str, Any]] = {}
    for sr in planned_source_records:
        sid = sr.get("planned_source_record_id_synthetic")
        if not sid:
            continue
        source_by_id[sid] = sr

    # Compute per-candidate provider & theme + collect role distribution
    provider_pool_counts: Counter = Counter()
    enriched: list[dict[str, Any]] = []
    for c in candidates:
        provider = _provider_family_for_candidate(
            candidate=c, source_records_by_id=source_by_id,
        )
        theme = _evidence_theme_for_candidate(
            candidate=c, source_records_by_id=source_by_id,
        )
        cc = dict(c)
        cc["_source_provider_family"] = provider
        cc["_evidence_theme"] = theme
        enriched.append(cc)
        provider_pool_counts[provider] += 1

    # Normalize role slugs across the input pool
    role_map, normalization_rows = normalize_role_slugs_for_candidates(
        candidates,
    )
    for cc in enriched:
        cc["_normalized_primary_role"] = role_map.get(
            cc.get("inferred_persona_role") or "",
            normalize_role_slug(cc.get("inferred_persona_role") or ""),
        )

    # Compute quality scores + sort
    for cc in enriched:
        cc["_quality_score"] = _quality_score(
            candidate=cc, provider_pool_counts=provider_pool_counts,
        )
    enriched.sort(
        key=lambda c: (
            -c["_quality_score"], c["_normalized_primary_role"],
            c.get("candidate_id", ""),
        ),
    )

    fake_pats = _build_fake_use_patterns(product_name)

    kept: list[dict[str, Any]] = []
    kept_meta_by_role: dict[str, list[dict[str, Any]]] = {}
    rejected: list[CompressionRejection] = []

    def _reject(
        cand: dict[str, Any],
        reason: CompressionRejectionReason,
        explanation: str,
        stronger_id: str | None = None,
    ) -> None:
        rejected.append(CompressionRejection(
            candidate_id=cand.get("candidate_id", ""),
            pre_normalization_role=cand.get("inferred_persona_role") or "",
            normalized_primary_role=cand.get("_normalized_primary_role") or "",
            rejection_reason=reason,
            rejection_explanation=explanation,
            stronger_candidate_kept_id=stronger_id,
        ))

    for cand in enriched:
        # 1. Universal scope discipline
        if not cand.get("not_global_persona", False):
            _reject(
                cand, "non_brief_scoped_or_global_persona",
                "Candidate not flagged as not_global_persona=True.",
            )
            continue
        if cand.get("scope") != "brief_scoped":
            _reject(
                cand, "non_brief_scoped_or_global_persona",
                f"scope={cand.get('scope')!r} (must be 'brief_scoped').",
            )
            continue

        # 2. Evidence presence
        sids = cand.get("source_record_ids") or []
        snips = cand.get("evidence_snippets") or []
        if not sids or not snips:
            _reject(
                cand, "missing_evidence",
                "Candidate is missing source_record_ids or "
                "evidence_snippets.",
            )
            continue

        # 3. ≥ min_traits
        traits = cand.get("inferred_traits") or []
        if len(traits) < min_traits:
            _reject(
                cand, "below_min_traits",
                f"Only {len(traits)} traits (< {min_traits} required).",
            )
            continue

        # 4. Launch-state / fake-use scan
        fake_hits = _has_fake_use_claim(candidate=cand, fake_pats=fake_pats)
        if fake_hits:
            _reject(
                cand, "fake_target_product_use",
                "Candidate contains forbidden phrases implying direct "
                f"unlaunched-product use: {fake_hits[:2]}.",
            )
            continue

        # 5. Quality floor: weak evidence with low confidence is rejected
        es = _EVIDENCE_STRENGTH_SCORE.get(
            cand.get("evidence_strength", "moderate"), 2,
        )
        cf = _CONFIDENCE_SCORE.get(cand.get("confidence", "medium"), 2)
        if es <= 1 and cf <= 1:
            _reject(
                cand, "below_quality_floor",
                "Both evidence_strength and confidence are at the "
                "lowest tier.",
            )
            continue

        # 6. Same-role admission rule
        norm_role = cand["_normalized_primary_role"]
        already = kept_meta_by_role.get(norm_role) or []
        if not already:
            cand["_kept_reason"] = (
                f"first candidate for normalized role {norm_role!r}; "
                f"quality_score={cand['_quality_score']}."
            )
            kept.append(cand)
            kept_meta_by_role.setdefault(norm_role, []).append({
                "id": cand["candidate_id"],
                "theme": cand["_evidence_theme"],
                "provider": cand["_source_provider_family"],
                "traits": _trait_signature(cand),
                "objections": _objection_signature(cand),
                "behaviors": _behavior_signature(cand),
            })
            continue

        new_traits = _trait_signature(cand)
        new_objs = _objection_signature(cand)
        new_behs = _behavior_signature(cand)

        # Hard reject: same (role, theme, provider) triple as an
        # already-kept candidate is a same-voice duplicate regardless
        # of trait/objection/behavior surface differences. The
        # behavioral differential check below is a softer admission
        # gate; this triple-match check is the hard floor.
        triple_dup_id = next(
            (
                m["id"] for m in already
                if m["theme"] == cand["_evidence_theme"]
                and m["provider"] == cand["_source_provider_family"]
            ),
            None,
        )
        if triple_dup_id is not None:
            _reject(
                cand, "duplicate_role_and_theme",
                "Same (normalized_role, evidence_theme, "
                "provider_family) triple as kept candidate.",
                stronger_id=triple_dup_id,
            )
            continue

        # Compute behavioral differential against EVERY already-kept
        # member of this role; admit only if differential vs the
        # most-similar one is ≥ threshold AND quality is non-trivial.
        max_diff_against_any = -1
        best_diff_reasons: list[str] = []
        most_similar_id: str | None = None
        for kept_meta in already:
            score, reasons = _behavioral_differential(
                new_cand=cand,
                new_theme=cand["_evidence_theme"],
                new_provider=cand["_source_provider_family"],
                new_traits=new_traits, new_objs=new_objs,
                new_behs=new_behs, kept_meta=kept_meta,
            )
            # We want to ensure the new cand is sufficiently different
            # from EVERY kept member of this role. Track the MIN diff
            # across kept members → that's the bottleneck.
            if max_diff_against_any < 0 or score < max_diff_against_any:
                max_diff_against_any = score
                best_diff_reasons = reasons
                most_similar_id = kept_meta["id"]
        if max_diff_against_any >= min_behavioral_differential:
            cand["_kept_reason"] = (
                f"second+ candidate for normalized role {norm_role!r}; "
                f"behavioral_differential={max_diff_against_any} vs "
                f"most-similar kept ({most_similar_id}); "
                f"reasons: {best_diff_reasons}."
            )
            kept.append(cand)
            already.append({
                "id": cand["candidate_id"],
                "theme": cand["_evidence_theme"],
                "provider": cand["_source_provider_family"],
                "traits": new_traits,
                "objections": new_objs,
                "behaviors": new_behs,
            })
            continue

        # Reject — pick the most specific reason
        if max_diff_against_any < min_behavioral_differential:
            # Inspect axes that were equal
            kept_meta = next(
                m for m in already if m["id"] == most_similar_id
            )
            # Choose specific reason
            if cand["_evidence_theme"] == kept_meta["theme"]:
                reason: CompressionRejectionReason = (
                    "duplicate_role_and_theme"
                )
            elif new_traits and new_traits == kept_meta["traits"]:
                reason = "duplicate_role_and_traits"
            elif new_objs and new_objs == kept_meta["objections"]:
                reason = "duplicate_role_and_objections"
            elif (
                cand["_source_provider_family"]
                == kept_meta["provider"]
                and not best_diff_reasons
            ):
                reason = "duplicate_role_and_provider"
            else:
                reason = "weaker_than_kept_candidate"
            _reject(
                cand, reason,
                f"Behavioral differential {max_diff_against_any} < "
                f"{min_behavioral_differential} threshold against kept "
                f"candidate {most_similar_id}.",
                stronger_id=most_similar_id,
            )
            continue

    # Convert kept to schema
    compressed_pre_cap: list[CompressedPersonaCandidate] = []
    for cand in kept:
        # Build sanitized inferred_traits dicts (preserve audit shape)
        trait_dicts = [dict(t) for t in cand.get("inferred_traits") or []]
        compressed_pre_cap.append(CompressedPersonaCandidate(
            candidate_id=cand["candidate_id"],
            target_brief=cand.get("target_brief", target_brief_id),
            generated_for_phase=generated_for_phase,
            pre_normalization_role=cand.get("inferred_persona_role") or "",
            normalized_primary_role=cand["_normalized_primary_role"],
            secondary_persona_roles=[
                role_map.get(r, normalize_role_slug(r))
                for r in cand.get("secondary_persona_roles") or []
            ],
            role_inference_basis=list(cand.get("role_inference_basis") or []),
            segment_label=cand.get("segment_label") or "",
            source_record_ids=list(cand.get("source_record_ids") or []),
            evidence_summary=cand.get("evidence_summary") or "",
            evidence_snippets=list(cand.get("evidence_snippets") or []),
            evidence_theme=cand["_evidence_theme"],
            source_provider_family=cand["_source_provider_family"],
            inferred_traits=trait_dicts,
            inferred_preferences=list(cand.get("inferred_preferences") or []),
            inferred_objections=list(cand.get("inferred_objections") or []),
            inferred_behaviors=list(cand.get("inferred_behaviors") or []),
            hypothetical_target_product_reaction=(
                cand.get("hypothetical_target_product_reaction") or ""
            ),
            confidence=cand.get("confidence", "medium"),
            evidence_strength=cand.get("evidence_strength", "moderate"),
            quality_score=cand["_quality_score"],
            caveats=list(cand.get("caveats") or []),
            simulation_usefulness_summary=(
                cand.get("simulation_usefulness_summary") or ""
            ),
            persistence_recommendation=cand.get(
                "persistence_recommendation", "DEFER",
            ),
            kept_reason=cand["_kept_reason"],
        ))

    # Phase 9A.2 — universal hard-cap stratified selector.
    hard_cap_audit: dict[str, Any] = {"applied": False}
    if (
        hard_max_compressed is not None
        and hard_max_compressed > 0
        and len(compressed_pre_cap) > hard_max_compressed
    ):
        compressed, dropped_overflow, hard_cap_audit = (
            _apply_hard_cap_stratified(
                compressed=compressed_pre_cap,
                hard_max=hard_max_compressed,
            )
        )
        for d in dropped_overflow:
            rejected.append(CompressionRejection(
                candidate_id=d.candidate_id,
                pre_normalization_role=d.pre_normalization_role,
                normalized_primary_role=d.normalized_primary_role,
                rejection_reason="hard_cap_overflow",
                rejection_explanation=(
                    f"Dropped during stratified hard-cap selection "
                    f"(hard_max={hard_max_compressed}). The keep list "
                    "is stratified by role/provider/theme diversity, "
                    "not random sliced."
                ),
                stronger_candidate_kept_id=None,
            ))
    else:
        compressed = compressed_pre_cap

    # Diff summary
    roles_before = sorted({
        c.get("inferred_persona_role") or "" for c in candidates
    })
    roles_after = sorted({
        c.normalized_primary_role for c in compressed
    })
    role_counter_before: Counter = Counter(
        c.get("inferred_persona_role") or "" for c in candidates
    )
    role_counter_after: Counter = Counter(
        c.normalized_primary_role for c in compressed
    )
    dup_clusters_before = sum(
        1 for v in role_counter_before.values() if v >= 2
    )
    dup_clusters_after = sum(
        1 for v in role_counter_after.values() if v >= 2
    )
    providers_before = sorted({
        c.get("_source_provider_family", "unknown") for c in enriched
    })
    providers_after = sorted({
        c.source_provider_family for c in compressed
    })
    diversity_before = _diversity_score_for_set([
        {
            "normalized_primary_role": role_map.get(
                c.get("inferred_persona_role") or "",
                normalize_role_slug(c.get("inferred_persona_role") or ""),
            ),
        }
        for c in candidates
    ])
    diversity_after = _diversity_score_for_set([
        {"normalized_primary_role": c.normalized_primary_role}
        for c in compressed
    ])
    concentration_before = _competitor_concentration(
        [c.get("inferred_persona_role") or "" for c in candidates],
    )
    concentration_after = _competitor_concentration(
        [c.normalized_primary_role for c in compressed],
    )

    diff_summary = CompressionDiffSummary(
        before_count=len(candidates),
        after_count=len(compressed),
        rejected_count=len(rejected),
        roles_before=roles_before,
        roles_after=roles_after,
        duplicate_role_clusters_before=dup_clusters_before,
        duplicate_role_clusters_after=dup_clusters_after,
        provider_families_before=providers_before,
        provider_families_after=providers_after,
        diversity_score_before=diversity_before,
        diversity_score_after=diversity_after,
        competitor_concentration_before=concentration_before,
        competitor_concentration_after=concentration_after,
    )

    policy = CompressionPolicy(
        grouping_dimensions=[
            "normalized_primary_role", "evidence_theme",
            "source_provider_family", "trait_signature",
            "objection_signature", "behavior_signature",
        ],
        selection_rules=[
            "Sort all candidates by quality_score descending; tie-break "
            "on normalized_primary_role then candidate_id.",
            "Admit the highest-quality candidate per normalized "
            "primary role first.",
            "Admit a second+ candidate of the same normalized role "
            f"only when its behavioral_differential ≥ "
            f"{min_behavioral_differential} (axes: theme, provider, "
            "trait Jaccard, objection Jaccard, behavior Jaccard).",
            "Quality always beats count: target range "
            f"{max_target_range} is SOFT; output may be smaller if "
            "evidence does not support more.",
        ] + (
            [
                f"Phase 9A.2 hard-cap applied: input "
                f"{hard_cap_audit['input_count']} → kept "
                f"{hard_cap_audit['kept_count']} via stratified "
                "selector (role × provider × theme; 35% role cap)."
            ]
            if hard_cap_audit.get("applied") else []
        ),
        rejection_rules=[
            "Reject any candidate that is global / not brief-scoped.",
            "Reject any candidate missing source_record_ids or "
            "evidence_snippets.",
            f"Reject candidates with fewer than {min_traits} traits.",
            "Reject candidates that match the universal fake-target-"
            "product-use phrase scanner.",
            "Reject candidates whose evidence_strength AND confidence "
            "are both at the lowest tier.",
            "Reject duplicate same-role candidates (theme / traits / "
            "objections / provider all match a kept candidate).",
        ],
        max_target_range=max_target_range,
        quality_floor={
            "min_traits": min_traits,
            "min_evidence_strength_or_confidence_above_lowest_tier": True,
        },
        min_behavioral_differential_for_second_same_role=(
            min_behavioral_differential
        ),
    )

    rationale: list[str] = []
    if hard_cap_audit.get("applied"):
        rationale.append(
            f"Hard-cap selector applied: "
            f"{hard_cap_audit['input_count']} compressed candidates "
            f"→ stratified top-{hard_cap_audit['kept_count']} "
            f"(dropped {hard_cap_audit['dropped_count']} as "
            "hard_cap_overflow). Stratification axes: "
            "role × provider × theme; per-role cap=35%."
        )
    rationale.append(
        f"Compressed {len(candidates)} candidates → {len(compressed)} "
        f"({len(rejected)} rejected)."
    )
    if compressed:
        rationale.append(
            f"Unique normalized roles in compressed set: "
            f"{len(roles_after)} ({sorted(roles_after)})."
        )
    if compressed:
        rationale.append(
            f"Provider families represented after compression: "
            f"{providers_after}."
        )
    if normalization_rows:
        rationale.append(
            f"Role-slug normalization changed {len(normalization_rows)} "
            "distinct role(s) (apostrophe/punctuation cleanup)."
        )

    caveats = [
        "PersonaSetCompressor is deterministic — derived from the "
        "input candidate pool + universal quality rules. NO LLM, NO "
        "network, NO DB writes.",
        "Compressed candidates remain BRIEF-SCOPED + RUN-SCOPED + "
        "DRY-RUN-ONLY. None is a global persona.",
        "Universal fake-target-product-use scanner ran on every "
        "candidate's evidence_summary + evidence_snippets + "
        "hypothetical_target_product_reaction.",
        "Quality always beats count: the output range is a soft "
        "target, not a forced count.",
    ]

    return CompressedPersonaSet(
        target_brief_id=target_brief_id,
        product_name=product_name,
        launch_state=launch_state,  # type: ignore[arg-type]
        generated_for_phase=generated_for_phase,
        plan_id=_plan_id(
            target_brief_id=target_brief_id,
            product_name=product_name,
            candidate_ids=[c.get("candidate_id", "") for c in candidates],
            role_map=role_map,
        ),
        policy=policy,
        compressed_candidates=compressed,
        rejected_candidates=rejected,
        diff_summary=diff_summary,
        rationale=rationale,
        caveats=caveats,
        generated_at=datetime.now(UTC).isoformat(),
    )
