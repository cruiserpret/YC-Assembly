"""Phase 8.5D.1E — universal role-slug normalizer.

Same function applies to ANY role string from ANY product. NO
per-brand mapping. NO StrideShield/Triton/Solara-specific code.

Rules:
  1. lowercase
  2. strip apostrophes (so `squirrel's` and `squirrels` collapse)
  3. replace any non-alphanumeric char with a single underscore
  4. collapse runs of `_` to one
  5. trim leading/trailing `_`

The role's prefix (`competitor_user_…`, `substitute_user_…`,
`price_skeptic`, etc.) is preserved by construction — the rules
operate on the entire string and the prefix is alphanumeric.

Idempotent: `normalize(normalize(x)) == normalize(x)`.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_APOSTROPHE_CHARS = "'’‘"  # straight + curly


class RoleSlugNormalization(BaseModel):
    """Audit shape for one role-slug change."""

    model_config = ConfigDict(extra="forbid")

    original_role: str
    normalized_role: str
    normalization_reason: str | None = None
    affected_candidate_ids: list[str] = Field(default_factory=list)


def normalize_role_slug(role: str) -> str:
    """Universal slug cleanup. See module docstring for rules."""
    if role is None:
        return ""
    s = role.strip().lower()
    # Step 2: drop apostrophes BEFORE collapsing non-alnum so that
    # `squirrel's` becomes `squirrels` (not `squirrel_s`).
    for ap in _APOSTROPHE_CHARS:
        s = s.replace(ap, "")
    # Step 3 + 4: collapse non-alphanumeric runs to single `_`
    s = _NON_ALNUM_RE.sub("_", s)
    # Step 5: trim
    s = s.strip("_")
    return s


def normalize_role_slugs_for_candidates(
    candidates: list[dict],
) -> tuple[dict[str, str], list[RoleSlugNormalization]]:
    """Walk a list of persona-candidate dicts (`inferred_persona_role`
    + `secondary_persona_roles`) and return:

      * `role_map`: { original_role → normalized_role } for every
        distinct role that appeared in the input;
      * `normalizations`: list of `RoleSlugNormalization` rows for
        roles whose normalized form differs from the original
        (every "no-op" role is omitted from this list to keep audit
        focused).
    """
    distinct: set[str] = set()
    affected: dict[str, list[str]] = {}
    for c in candidates:
        cid = c.get("candidate_id") or ""
        primary = c.get("inferred_persona_role") or ""
        secondary = list(c.get("secondary_persona_roles") or [])
        for r in [primary, *secondary]:
            if not r:
                continue
            distinct.add(r)
            affected.setdefault(r, []).append(cid)
    role_map: dict[str, str] = {}
    rows: list[RoleSlugNormalization] = []
    for r in sorted(distinct):
        n = normalize_role_slug(r)
        role_map[r] = n
        if n != r:
            reason_parts: list[str] = []
            if any(ap in r for ap in _APOSTROPHE_CHARS):
                reason_parts.append("stripped_apostrophe")
            if r.lower() != r:
                reason_parts.append("lowercased")
            if _NON_ALNUM_RE.search(r.lower()):
                reason_parts.append("collapsed_non_alphanumeric")
            rows.append(RoleSlugNormalization(
                original_role=r,
                normalized_role=n,
                normalization_reason=(
                    "+".join(reason_parts) or "normalized"
                ),
                affected_candidate_ids=sorted(set(affected.get(r, []))),
            ))
    return role_map, rows
