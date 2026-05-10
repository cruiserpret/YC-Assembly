"""Closed enums shared by migration, ORM, validator, and (later) the UI.

The migration mirrors these in DB CHECK constraints. Python validators
re-check them so failures surface earlier with structured violations.
"""
from __future__ import annotations

from typing import Final

# -- Persona trait support level -------------------------------------------
SUPPORT_DIRECT: Final[str] = "direct"
SUPPORT_INFERRED: Final[str] = "inferred"
SUPPORT_UNKNOWN: Final[str] = "unknown"
SUPPORT_MISSING: Final[str] = "missing"

SUPPORT_LEVELS: Final[tuple[str, ...]] = (
    SUPPORT_DIRECT, SUPPORT_INFERRED, SUPPORT_UNKNOWN, SUPPORT_MISSING,
)

# -- Source compliance tags -----------------------------------------------
COMPLIANCE_TAGS: Final[tuple[str, ...]] = (
    "public_api",
    "public_html",
    "open_dataset",
    "open_aggregate",
    "manual_seed",
)

# -- Allowed persona trait field names. Closed set. -----------------------
PERSONA_FIELD_NAMES: Final[tuple[str, ...]] = (
    "interests",
    "role_or_context",
    "buying_constraints",
    "trust_triggers",
    "current_alternatives",
    "communication_style",
    "influence_signals",
    "price_sensitivity",
    "objection_patterns",
    "geography_broad",
)

# Fields that, when populated by inference, REQUIRE source-engagement
# metadata (e.g. count-of-public-engagements). The validator enforces
# this against `influence_signals` specifically.
SOURCE_BACKED_ONLY_FIELDS: Final[tuple[str, ...]] = (
    "influence_signals",
)

# -- Persona graph closed enums -------------------------------------------
EDGE_TYPES: Final[tuple[str, ...]] = (
    "similar_to",
    "influences",
    "shares_segment",
    "shared_source",
    "bridge_to",
)

EDGE_BASIS: Final[tuple[str, ...]] = (
    "embedding_cosine",
    "shared_source",
    "deterministic",
    "inferred",
)

# -- Coverage labels -------------------------------------------------------
COVERAGE_LABELS: Final[tuple[str, ...]] = ("thin", "moderate", "strong")

# -- Inferred trait minimum confidence (Python-only; DB requires > 0). ----
INFERRED_MIN_CONFIDENCE: Final[float] = 0.5

# -- Anonymization ---------------------------------------------------------
DISPLAY_NAME_FIRSTS: Final[tuple[str, ...]] = (
    "Avery", "Blair", "Casey", "Drew", "Ellis", "Finley", "Gray", "Harper",
    "Indigo", "Jules", "Kerry", "Logan", "Morgan", "Nico", "Oakley",
    "Parker", "Quinn", "Reese", "Sage", "Tate", "Umber", "Vesper",
    "Wren", "Xael", "Yarrow", "Zev",
    "Maya", "Jordan", "Riley", "Skyler", "Phoenix", "Rowan", "Emerson",
    "Lennon", "Marlowe", "Sutton", "Tatum", "Winslow",
)
DISPLAY_NAME_LAST_INITIALS: Final[tuple[str, ...]] = tuple("ABCDEFGHJKLMNPRSTVW")
