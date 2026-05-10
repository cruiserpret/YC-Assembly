"""Phase 8.5D.1E — persona-set compressor + role-slug normalizer.

Universal, deterministic compression of an evidence-backed
persona-candidate pool into a smaller, non-duplicative,
brief-scoped run-scoped mini-society candidate set.

NO LLM. NO network. NO DB writes. Same inputs → same output.

Two pieces:

  1. `normalize_role_slug(slug)` — universal role-slug cleanup
     (lowercase, strip apostrophes, collapse non-alphanumeric runs
     to single underscore, trim). Preserves the role's prefix
     (`competitor_user_…`, `substitute_user_…`, etc.). Idempotent.

  2. `compress_persona_set(...)` — group candidates by normalized
     primary role, score each, select the strongest representative
     per role, admit additional same-role candidates ONLY when they
     show meaningful behavioral / provider / theme / trait /
     objection differential. Quality gates are NEVER relaxed.
"""

from assembly.sources.persona_set_compressor.compressor import (
    compress_persona_set,
)
from assembly.sources.persona_set_compressor.normalizer import (
    RoleSlugNormalization, normalize_role_slug,
    normalize_role_slugs_for_candidates,
)
from assembly.sources.persona_set_compressor.schemas import (
    CompressedPersonaCandidate, CompressedPersonaSet,
    CompressionPolicy, CompressionRejection,
)

__all__ = [
    "CompressedPersonaCandidate",
    "CompressedPersonaSet",
    "CompressionPolicy",
    "CompressionRejection",
    "RoleSlugNormalization",
    "compress_persona_set",
    "normalize_role_slug",
    "normalize_role_slugs_for_candidates",
]
