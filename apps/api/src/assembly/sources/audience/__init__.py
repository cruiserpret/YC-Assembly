"""Phase 12E — Source-Audience Population Layer.

Assembly's synthetic society must represent the actual *launch-source
audience* that will react to a product, not only the product's
target customers. Without this, the simulator over-predicts receptive
voices and under-predicts proof-seekers / industry observers /
category skeptics that dominate real public launch threads.

This module defines:
  * The 10-role audience taxonomy (target customer + 9 non-customer
    archetypes).
  * Two source profiles (`default`, `hn_show_hn`) declaring
    proportional role mixes.
  * Helper functions to look up role metadata, default buckets, and
    profile proportions.

All flags off by default — when `launch_source` is missing on a
brief, the `default` profile applies and the system behaves
identically to pre-Phase-12E.
"""
from __future__ import annotations

from assembly.sources.audience.role_taxonomy import (
    AUDIENCE_ROLES,
    AudienceRole,
    AudienceRoleSpec,
    LaunchSource,
    SOURCE_PROFILES,
    allocate_role_counts,
    get_profile,
    get_role_spec,
    is_hard_resistant_role,
    is_scorable_role,
    resolve_launch_source,
    role_locked_default_bucket,
)

__all__ = [
    "AUDIENCE_ROLES",
    "AudienceRole",
    "AudienceRoleSpec",
    "LaunchSource",
    "SOURCE_PROFILES",
    "allocate_role_counts",
    "get_profile",
    "get_role_spec",
    "is_hard_resistant_role",
    "is_scorable_role",
    "resolve_launch_source",
    "role_locked_default_bucket",
]
