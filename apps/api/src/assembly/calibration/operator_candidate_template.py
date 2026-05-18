"""Phase 12A.5 — Operator-facing real candidate metadata template.

This module is the **on-ramp** for the operator. It produces the
exact metadata template the operator must fill out for each real
candidate product, and validates the *shape* of what comes back
before it ever reaches the Phase 12A.4 intake layer.

Three responsibilities:

  1. Generate an empty template (one slot per candidate) the
     operator can hand-fill into JSON or YAML.
  2. Validate the *shape* of an operator-filled template — does it
     have the required keys? Does it sneakily carry a forbidden
     outcome field? Does it use deprecated field names?
  3. Render a deterministic human-readable request packet (header
     + per-slot help text + warnings) that names which fields the
     operator MUST supply, which are recommended, and the do/don't
     rules they should follow when picking candidates.

What this module is NOT:

  - It does NOT score candidates. That's Phase 12A.3 +
    :mod:`assembly.calibration.candidate_metadata_intake`.
  - It does NOT carry any real outcome data, observation counts,
    URLs, or specific product names. The 4-bucket vocabulary
    (buyer / receptive / uncertain / skeptical) is referenced only
    in operator-facing help text, never as a request for numeric
    counts at this phase.
  - It does NOT scrape, fetch, call any API, write to any DB, or
    talk to an LLM.

The shape of a filled template is identical to the dict that
:func:`assembly.calibration.candidate_metadata_intake.parse_operator_candidate_metadata`
expects. The conversion from a filled template into the intake
layer is therefore a direct hand-off — no translation needed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field lists
# ---------------------------------------------------------------------------


_REQUIRED_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "product_name",
    "category",
)


_OPTIONAL_FIELDS: tuple[str, ...] = (
    "launch_or_cutoff_date",
    "pre_launch_sources_available",
    "outcome_sources_available",
    "estimated_observation_count",
    "contamination_risk",
    "model_prior_risk",
    "outcome_quality",
    "cutoff_clarity",
    "category_fit",
    "source_access_risk",
    "notes",
)


_ALL_ALLOWED_FIELDS: frozenset[str] = frozenset(
    _REQUIRED_FIELDS + _OPTIONAL_FIELDS
)


# Fields whose presence would signal that the operator accidentally
# included real outcome data in the template. The shape validator
# rejects any of these at the template layer so they can never
# reach the intake or :class:`BlindCase` surfaces.
_FORBIDDEN_OUTCOME_SHAPED_KEYS: frozenset[str] = frozenset({
    "observed_distribution",
    "observed_sample_size",
    "observed_collection_date",
    "observed_objections",
    "observed_source_type",
    "hidden_real_world_outcome",
    "real_world_outcome",
    "ground_truth",
    "actual_buyers",
    "actual_signups",
    "actual_revenue",
    "actual_conversion",
})


# Help text per field. Operator-facing English; keeps the per-field
# guidance discoverable without forcing the operator to read the
# whole calibration design.
_HELP_TEXT: dict[str, str] = {
    # required
    "candidate_id": (
        "REQUIRED. Stable snake_case slug (e.g. "
        "'pretend_company_ai_video_launch_2024'). Used to link this "
        "candidate to a future prediction artifact."
    ),
    "product_name": (
        "REQUIRED. The real product / startup name. MUST NOT contain "
        "'Vivago' or 'Semble' (Assembly's signal layers were "
        "developed against those — they are auto-rejected as "
        "contaminated). Avoid mega-famous launches: a pretrained "
        "LLM probably already 'knows' the outcome."
    ),
    "category": (
        "REQUIRED. Product category string. Prefer one Assembly has "
        "evidence layers for: 'AI SaaS tool', 'developer tool', "
        "'B2B SaaS', 'consumer mobile app', 'consumer product'."
    ),
    # recommended
    "launch_or_cutoff_date": (
        "ISO date (YYYY-MM-DD) of the public launch. This is what "
        "Assembly's evidence cutoff will be set to — anything dated "
        "after this is a forbidden post-cutoff leak."
    ),
    "pre_launch_sources_available": (
        "List of source-type strings the operator can supply for "
        "pre-launch inputs (e.g. 'product_hunt_launch_page_text', "
        "'show_hn_thread_text', 'founder_announcement_thread'). "
        "Do NOT include URLs or raw text here — just source-type "
        "labels."
    ),
    "outcome_sources_available": (
        "List of source-type strings where real-world reactions can "
        "be observed (e.g. 'product_hunt_comments', "
        "'g2_or_capterra_review_text', 'reddit_reaction_threads'). "
        "Operator-supplied review/comment data is preferred over "
        "scraping. Public-readable sources are OK as long as no "
        "scraping is required to collect them."
    ),
    "estimated_observation_count": (
        "How many real-world reactions can be mapped into buyer / "
        "receptive / uncertain / skeptical? Pick a BUCKET (no exact "
        "count yet): '<30', '30-100', '100-500', '500+', or "
        "'unknown'. Prefer at least 30-100. Candidates with <30 "
        "observations carry an 'insufficient_observations' warning."
    ),
    "contamination_risk": (
        "Is this product (or close proxies) already used in "
        "Assembly's evidence/signal layers? Choose: 'none', 'low', "
        "'medium', 'high'. ANY answer of 'high' auto-rejects. "
        "Vivago and Semble auto-reject on product_name regardless "
        "of this field."
    ),
    "model_prior_risk": (
        "How famous is this product? Choose: 'low', 'medium', "
        "'high'. 'high' auto-rejects — a pretrained LLM probably "
        "knows the outcome, which would compromise the blinded "
        "test."
    ),
    "outcome_quality": (
        "How clearly do the outcome sources let us label real "
        "reactions into buyer / receptive / uncertain / skeptical? "
        "Choose: 'unknown', 'weak', 'medium', 'strong'. Prefer "
        "'medium' or 'strong'. Outcome data that is only revenue "
        "or funding (no user reactions) is 'weak'."
    ),
    "cutoff_clarity": (
        "How sharp is the launch cutoff? Choose: 'unclear', "
        "'approximate', 'clear'. A 'clear' cutoff means there's a "
        "specific public launch event — pre/post separation is "
        "trustworthy."
    ),
    "category_fit": (
        "How well does this product match a category Assembly "
        "already has evidence layers for? Choose: 'none', 'weak', "
        "'medium', 'strong'."
    ),
    "source_access_risk": (
        "How will outcome data be obtained? Choose: 'forbidden', "
        "'scraping_required', 'operator_supply', 'public_no_scrape', "
        "'open_data'. 'forbidden' and 'scraping_required' "
        "auto-reject — we will NEVER scrape unauthorized sources."
    ),
    "notes": (
        "Free-form notes: anything else the operator wants to "
        "record (e.g. 'comments are mostly hype, real reactions "
        "are in the linked Reddit thread')."
    ),
}


# Do/don't rules surfaced verbatim in the rendered operator request.
_DO_RULES: tuple[str, ...] = (
    "Prefer products with 30-100+ real public reactions.",
    "Prefer a clear public launch event with a known launch date.",
    "Prefer operator-supplied or public review/comment data.",
    "Keep pre-launch and post-launch information strictly "
    "separate — anything dated after the launch is a leak.",
    "Pick categories Assembly already has evidence for (AI SaaS, "
    "developer tool, B2B SaaS, consumer app, consumer product).",
)


_DONT_RULES: tuple[str, ...] = (
    "Do NOT use Vivago or Semble — Assembly's signal layers were "
    "developed against them, so they are auto-rejected as "
    "contaminated.",
    "Do NOT pick mega-famous products. If a pretrained LLM "
    "probably knows the outcome, the blinded test is meaningless.",
    "Do NOT pick candidates with no public outcome reactions "
    "(e.g. funding rounds with no user feedback).",
    "Do NOT use sources that would require unauthorized scraping. "
    "Operator-supplied exports or public-readable threads only.",
    "Do NOT include any real-world outcome distribution, "
    "observation counts, or hidden labels in the template at this "
    "phase. That data lives in a later, explicitly-authorized "
    "hidden-outcome phase.",
)


# ---------------------------------------------------------------------------
# Field-list accessors
# ---------------------------------------------------------------------------


def candidate_metadata_required_fields() -> tuple[str, ...]:
    """Return the tuple of required field names (operator MUST supply)."""
    return _REQUIRED_FIELDS


def candidate_metadata_optional_fields() -> tuple[str, ...]:
    """Return the tuple of optional / strongly-recommended field names."""
    return _OPTIONAL_FIELDS


def candidate_metadata_help_text() -> dict[str, str]:
    """Return a copy of the per-field help-text dict.

    Returned dict is a fresh shallow copy so callers can mutate it
    without affecting the module-level constants.
    """
    return dict(_HELP_TEXT)


# ---------------------------------------------------------------------------
# Empty template builder
# ---------------------------------------------------------------------------


def build_empty_operator_candidate_template(
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Return an empty operator-template dict.

    Keys appear in a stable order: required fields first, then
    recommended fields. Required fields default to the empty string
    (so the operator can see which slots need to be filled);
    list-valued fields default to empty lists; everything else
    defaults to ``None`` so the intake-layer "missing" detection
    fires cleanly.

    If ``candidate_id`` is provided, it pre-populates the
    ``candidate_id`` slot.
    """
    tpl: dict[str, Any] = {}
    tpl["candidate_id"] = candidate_id or ""
    tpl["product_name"] = ""
    tpl["category"] = ""
    tpl["launch_or_cutoff_date"] = None
    tpl["pre_launch_sources_available"] = []
    tpl["outcome_sources_available"] = []
    tpl["estimated_observation_count"] = None
    tpl["contamination_risk"] = None
    tpl["model_prior_risk"] = None
    tpl["outcome_quality"] = None
    tpl["cutoff_clarity"] = None
    tpl["category_fit"] = None
    tpl["source_access_risk"] = None
    tpl["notes"] = ""
    return tpl


# ---------------------------------------------------------------------------
# Shape validation
# ---------------------------------------------------------------------------


@dataclass
class TemplateShapeValidation:
    """Result of :func:`validate_candidate_template_shape`."""

    is_valid_shape: bool
    missing_required: list[str] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)
    forbidden_outcome_keys: list[str] = field(default_factory=list)
    empty_required: list[str] = field(default_factory=list)


def validate_candidate_template_shape(
    payload: dict[str, Any],
) -> TemplateShapeValidation:
    """Audit the structural shape of an operator-filled template.

    This is a complement to
    :func:`candidate_metadata_intake.validate_operator_candidate_metadata`,
    NOT a replacement: this layer cares about KEY SHAPE
    (presence/absence and forbidden-outcome leaks), the intake
    layer cares about VALUE SEMANTICS (enum validity, follow-up
    questions).

    The shape is invalid when any of:

      - a required key is missing
      - a required key is present but empty (operator left it blank)
      - the template carries any key from
        :data:`_FORBIDDEN_OUTCOME_SHAPED_KEYS` — outcome data must
        not appear at this phase
      - the template carries unknown top-level keys (warning only —
        does not invalidate the shape, since the intake layer
        already tolerates this)
    """
    if not isinstance(payload, dict):
        return TemplateShapeValidation(
            is_valid_shape=False,
            forbidden_outcome_keys=[],
            unknown_keys=[],
            missing_required=list(_REQUIRED_FIELDS),
            empty_required=list(_REQUIRED_FIELDS),
        )
    missing_required = [
        k for k in _REQUIRED_FIELDS if k not in payload
    ]
    forbidden_outcome = sorted(
        k for k in payload.keys() if k in _FORBIDDEN_OUTCOME_SHAPED_KEYS
    )
    unknown = sorted(
        k for k in payload.keys()
        if k not in _ALL_ALLOWED_FIELDS
        and k not in _FORBIDDEN_OUTCOME_SHAPED_KEYS
    )
    empty_required: list[str] = []
    for k in _REQUIRED_FIELDS:
        if k in payload:
            v = payload[k]
            if v is None or (isinstance(v, str) and not v.strip()):
                empty_required.append(k)
    is_valid = (
        not missing_required
        and not empty_required
        and not forbidden_outcome
    )
    return TemplateShapeValidation(
        is_valid_shape=is_valid,
        missing_required=missing_required,
        unknown_keys=unknown,
        forbidden_outcome_keys=forbidden_outcome,
        empty_required=empty_required,
    )


# ---------------------------------------------------------------------------
# Rendered operator request
# ---------------------------------------------------------------------------


def render_operator_candidate_request(
    num_candidates: int = 2,
) -> str:
    """Build a deterministic, human-readable operator request packet.

    The packet is plain text with a stable structure:

      1. Header (one block per Phase 12A.5 do/don't rules).
      2. Required + recommended field listing with help text inline.
      3. Per-slot empty template (1..N).
      4. Footer reminder of the strict no-outcome rule.

    Determinism: the output is byte-identical across multiple calls
    with the same ``num_candidates``. This lets the operator hash
    the request packet for an audit trail.

    ``num_candidates`` must be a positive integer (default 2, since
    Phase 12A.5 explicitly targets 2-3 real candidates).
    """
    if not isinstance(num_candidates, int) or num_candidates < 1:
        raise ValueError(
            f"num_candidates must be a positive int, got {num_candidates!r}"
        )
    lines: list[str] = []
    lines.append("# Assembly calibration — real candidate metadata request")
    lines.append("")
    lines.append("## What you're being asked to do")
    lines.append("")
    lines.append(
        f"Supply metadata for {num_candidates} real product/startup "
        "candidate(s) that Assembly may run a blinded calibration "
        "test against. **Do not include any real-world outcome data "
        "in this template** — that comes in a later, explicitly-"
        "authorized phase."
    )
    lines.append("")
    lines.append("## Do")
    for r in _DO_RULES:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("## Don't")
    for r in _DONT_RULES:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("## Required fields")
    for k in _REQUIRED_FIELDS:
        lines.append(f"  - **`{k}`** — {_HELP_TEXT[k]}")
    lines.append("")
    lines.append("## Recommended fields")
    for k in _OPTIONAL_FIELDS:
        lines.append(f"  - `{k}` — {_HELP_TEXT[k]}")
    lines.append("")
    lines.append("## Candidate slots")
    for i in range(1, num_candidates + 1):
        lines.append("")
        lines.append(f"### Candidate {i}")
        lines.append("")
        lines.append("```json")
        lines.append("{")
        keys = list(_REQUIRED_FIELDS) + list(_OPTIONAL_FIELDS)
        for j, k in enumerate(keys):
            default = _placeholder_for_field(k)
            comma = "," if j < len(keys) - 1 else ""
            lines.append(f"  {_json_string(k)}: {default}{comma}")
        lines.append("}")
        lines.append("```")
    lines.append("")
    lines.append("## Reminder")
    lines.append("")
    lines.append(
        "This template MUST NOT carry any of: "
        "`observed_distribution`, `observed_sample_size`, "
        "`observed_collection_date`, `observed_objections`, "
        "`hidden_real_world_outcome`, `real_world_outcome`, "
        "`ground_truth`, `actual_buyers`, `actual_signups`, "
        "`actual_revenue`, `actual_conversion`. The shape validator "
        "rejects any of those at the template layer so they can "
        "never reach the prediction pipeline."
    )
    return "\n".join(lines) + "\n"


def _placeholder_for_field(k: str) -> str:
    """JSON-literal placeholder for the rendered empty template.

    Strings → ``""``, lists → ``[]``, everything else → ``null``.
    """
    if k in ("candidate_id", "product_name", "category", "notes"):
        return '""'
    if k in (
        "pre_launch_sources_available", "outcome_sources_available",
    ):
        return "[]"
    return "null"


def _json_string(s: str) -> str:
    """Minimal JSON string escape sufficient for our field names."""
    return '"' + s + '"'
