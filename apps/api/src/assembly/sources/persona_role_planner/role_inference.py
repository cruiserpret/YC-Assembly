"""Phase 8.5D.1 — universal role-inference lexicons + helper.

Roles are INFERRED from evidence — not drawn from a hardcoded
product-category list. The lexicons here describe UNIVERSAL English-
language patterns that surface across consumer-product reviews:
safety / price / flavor / performance / health / convenience /
substitute. They are domain-independent. A new product brief in
any category reuses these as-is; product-specific roles
(`competitor_user_<brand>`, `substitute_user_<thing>`) are derived
per-call from the founder brief + the source's own metadata-derived
matched terms.
"""
from __future__ import annotations

from typing import Any


# Universal pattern lexicons. Same terms apply to ANY product brief.
# Roles emit only when ≥1 keyword is present AND the source supplies
# corroborating context (the planner enforces this).
UNIVERSAL_ROLE_LEXICONS: dict[str, tuple[str, ...]] = {
    "safety_skeptic": (
        "heart racing", "blood pressure", "tingling",
        "side effect", "side effects", "allergic reaction",
        "made me sick", "got sick", "felt sick",
        "doctor", "warning", "recall", "stacking",
        "too strong", "overdose", "headache",
    ),
    "price_skeptic": (
        "$", "expensive", "pricey", "overpriced", "cheap",
        "value", "worth the price", "not worth", "ridiculous price",
        "bargain",
    ),
    "flavor_focused_buyer": (
        "flavor", "flavour", "taste", "tastes",
        "smell", "smells", "scent", "aftertaste", "delicious",
        "yummy", "syrupy",
    ),
    "performance_use_case_buyer": (
        "workout", "workouts", "gym", "endurance", "performance",
        "athletic", "fitness",
        "before the gym", "training", "race", "athlete",
    ),
    "health_conscious_buyer": (
        "no sugar", "zero sugar", "sugar-free", "sugar free",
        "low sugar", "natural", "clean ingredients",
        "organic", "non-gmo", "non gmo", "no preservatives",
        "vitamin", "vitamins", "minerals",
    ),
    "convenience_focused_buyer": (
        "easy to mix", "quick", "convenient", "on the go",
        "portable", "single serving", "ready to drink",
        "ready-to-drink", "compact",
    ),
    "category_rejecter": (
        "not for me", "wouldn't buy again", "won't buy again",
        "did not work", "didn't work", "stop drinking",
        "regret", "disappointed", "complete failure",
    ),
    "behavior_dose_self_modulator": (
        "cut the scoop", "cut my dose", "halved the scoop",
        "deducting a star", "smaller serving", "use less",
    ),
}


def infer_persona_roles_from_evidence(
    *,
    text: str,
    metadata: dict[str, Any],
    competitor_brief_list: list[str],
    substitute_brief_list: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """Return (roles, evidence_basis_by_role).

    Roles are derived ONLY where corroborating evidence is present.
    Sources of role signal (in priority order):

      1. `metadata.persona_value_roles` — from prior 8.5C.1 anchor
         scorer (already evidence-tied).
      2. `metadata.additional_persona_roles_unlocked_by_full` — from
         8.5C.4 full-text companions.
      3. Brief-derived competitor brand mentions in text →
         `competitor_user_<slug>`.
      4. Brief-derived substitute term mentions in text →
         `substitute_user_<slug>`.
      5. Universal-lexicon pattern hits in text → role from
         `UNIVERSAL_ROLE_LEXICONS`.

    Evidence basis is a per-role list of short string excerpts from
    text/metadata that justify each role.
    """
    text_low = (text or "").lower()
    md_persona_roles = list(metadata.get("persona_value_roles") or [])
    md_extended_roles = list(
        metadata.get("additional_persona_roles_unlocked_by_full") or []
    )

    roles: list[str] = []
    evidence_basis: dict[str, list[str]] = {}

    def _add_role(role: str, snippet: str) -> None:
        if role not in roles:
            roles.append(role)
            evidence_basis.setdefault(role, [])
        evidence_basis[role].append(snippet[:200])

    # 1. Pre-existing role signals from upstream phases (already
    # evidence-tied).
    for r in md_persona_roles:
        _add_role(r, f"metadata.persona_value_roles inherited: {r}")
    for r in md_extended_roles:
        _add_role(
            r,
            f"metadata.additional_persona_roles_unlocked_by_full: {r}",
        )

    # 2. Competitor brand mentions (brief-derived, not hardcoded list)
    for c in competitor_brief_list:
        if not c:
            continue
        c_low = c.lower()
        if c_low in text_low:
            slug = c_low.replace(" ", "_").replace("-", "_")
            role = f"competitor_user_{slug}"
            # Pull a 200-char snippet around the match
            idx = text_low.find(c_low)
            snippet = text[max(0, idx - 60):idx + 140]
            _add_role(role, f"competitor mention: ...{snippet.strip()}...")

    # 3. Substitute term mentions (brief-derived)
    for s in substitute_brief_list:
        if not s:
            continue
        s_low = s.lower()
        if s_low in text_low:
            slug = s_low.replace(" ", "_").replace("-", "_")
            role = f"substitute_user_{slug}"
            idx = text_low.find(s_low)
            snippet = text[max(0, idx - 60):idx + 140]
            _add_role(role, f"substitute mention: ...{snippet.strip()}...")

    # 4. Universal-lexicon pattern hits
    for role, lex in UNIVERSAL_ROLE_LEXICONS.items():
        for term in lex:
            if term.lower() in text_low:
                idx = text_low.find(term.lower())
                snippet = text[max(0, idx - 40):idx + 120]
                _add_role(
                    role,
                    f"universal lexicon hit '{term}': ...{snippet.strip()}...",
                )
                break  # one hit per role from the universal lex

    return sorted(set(roles)), evidence_basis
