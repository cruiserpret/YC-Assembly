"""Phase 12E — Source-Audience Population Layer.

The 10-role audience taxonomy declares the kinds of voices that
realistically populate a public-launch reaction corpus (HN thread,
PH launch, App Store reviews, etc.). The legacy persona pipeline
generates only target-customer evaluator voices; this taxonomy adds
the 9 non-customer archetypes that dominate real launch audiences.

Roles are STRICTLY GENERAL — they describe market-population
categories, not product-specific labels. The same `proof_seeker_only`
role applies to a B2B SaaS launch, a consumer subscription launch,
and a marketplace launch.

Two source profiles in v1:
  * `default` — legacy-compatible target-customer-heavy mix.
  * `hn_show_hn` — Show HN / Launch HN audience with substantial
    non-customer commentary. PROPORTIONS ARE WEAK PRIORS, to be
    calibrated as more labeled cases land.

Other source profiles (Product Hunt, App Store, Reddit, G2/Capterra)
are deferred to Phase 12E.next per operator scope decision.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AudienceRole = Literal[
    "target_customer_evaluator",
    "existing_competitor_user",
    "proof_seeker_only",
    "industry_observer",
    "technical_or_legal_explainer",
    "meta_commenter",
    "category_skeptic",
    "incumbent_defender",
    "casual_bystander",
    "off_topic_noise_candidate",
    # Phase 12E.5O — Product Hunt-specific roles. Apply to other
    # source profiles only with explicit per-profile weights; the
    # legacy HN profiles keep them at 0.0 for byte-for-byte stability.
    "shallow_positive_commenter",
    "founder_network_supporter",
    "early_adopter",
]

# Phase 12E.5C — `hn_show_hn_v2` added as an OPT-IN profile. The
# legacy `hn_show_hn` remains the default for any brief that supplied
# launch_source="hn_show_hn" prior to v2 calibration; v2 must be
# requested explicitly until it has paid-confirmation validation
# support on ≥2 products.
#
# Phase 12E.5O — `product_hunt_v1` added as an OPT-IN profile, plus
# the `product_hunt` alias that resolves to v1. PH has a materially
# different audience shape than HN: more makers/operators, more
# early-adopter language, more shallow praise, more launch-supportive
# comments, and less deep technical skepticism.
LaunchSource = Literal[
    "default",
    "hn_show_hn",
    "hn_show_hn_v2",
    "product_hunt_v1",
    "product_hunt",
]

# Market-bucket vocabulary (4-bucket calibration view used by
# calibration.market_buckets). Mirrored here as a string set so this
# module has no runtime dependency on the calibration layer.
_MARKET_BUCKETS: tuple[str, ...] = (
    "buyer", "receptive", "uncertain", "skeptical",
)


class AudienceRoleSpec(BaseModel):
    """Declarative spec for one audience role.

    `allowed_buckets` is the *only* set of buckets a persona/voter
    with this role may end up in after the intent cascade + 100-voter
    overlay. If the role's allowed_buckets is a single-element set,
    the role is "bucket-locked" — independent of ballot text, the
    persona ends in that bucket.

    `scorable` controls whether the role contributes to the founder
    report's scorable-market-reaction distribution. Roles with
    `scorable=False` are reported separately under
    `noise_meta_estimate` so the founder can see how much of the
    launch reaction is non-evaluative.
    """

    model_config = ConfigDict(extra="forbid")

    role: AudienceRole
    default_bucket: str  # one of _MARKET_BUCKETS
    allowed_buckets: frozenset[str]
    is_hard_resistant: bool = False
    is_scorable: bool = True
    typical_objections: tuple[str, ...] = ()
    typical_proof_needs: tuple[str, ...] = ()
    founder_report_section: str = "target_market"
    graph_influence_note: str = ""


AUDIENCE_ROLES: dict[AudienceRole, AudienceRoleSpec] = {
    "target_customer_evaluator": AudienceRoleSpec(
        role="target_customer_evaluator",
        default_bucket="receptive",
        allowed_buckets=frozenset(_MARKET_BUCKETS),
        is_hard_resistant=False,
        is_scorable=True,
        typical_objections=(
            "tool_doesnt_fit_workflow",
            "roi_unclear",
            "switching_cost",
        ),
        typical_proof_needs=(
            "demo_on_real_data",
            "pricing_clarity",
            "integration_proof",
        ),
        founder_report_section="target_market",
        graph_influence_note=(
            "high influence on other evaluators in same segment"
        ),
    ),
    "existing_competitor_user": AudienceRoleSpec(
        role="existing_competitor_user",
        default_bucket="skeptical",
        allowed_buckets=frozenset({"receptive", "uncertain", "skeptical"}),
        # Note: NOT bucket-locked. A competitor user CAN become
        # receptive with a strong switch trigger; just NOT buyer
        # without explicit adoption verb.
        is_hard_resistant=False,
        is_scorable=True,
        typical_objections=(
            "switching_cost",
            "sunk_training_investment",
            "missing_parity_features",
        ),
        typical_proof_needs=(
            "side_by_side_comparison",
            "migration_guide",
            "feature_parity_proof",
        ),
        founder_report_section="competitor_pull",
        graph_influence_note=(
            "high influence on target evaluators considering switch"
        ),
    ),
    "proof_seeker_only": AudienceRoleSpec(
        role="proof_seeker_only",
        default_bucket="uncertain",
        allowed_buckets=frozenset({"uncertain"}),  # LOCKED
        is_hard_resistant=False,  # not skeptical, just asking
        is_scorable=True,
        typical_objections=(),  # asks questions, doesn't object
        typical_proof_needs=(
            "the_proof_itself",
        ),
        founder_report_section="open_questions_from_audience",
        graph_influence_note="low influence (asks, doesn't argue)",
    ),
    "industry_observer": AudienceRoleSpec(
        role="industry_observer",
        default_bucket="uncertain",
        allowed_buckets=frozenset({"uncertain"}),
        # Industry observers DO comment within an evaluative frame but
        # are not in-market. Bucket-locked to uncertain to prevent the
        # synthetic simulator from inflating receptive by mis-routing
        # observers.
        is_hard_resistant=False,
        is_scorable=True,
        typical_objections=(
            "category_context_concerns",
        ),
        typical_proof_needs=(
            "category_level_evidence",
        ),
        founder_report_section="industry_context",
        graph_influence_note=(
            "medium influence — shapes the discussion context"
        ),
    ),
    "technical_or_legal_explainer": AudienceRoleSpec(
        role="technical_or_legal_explainer",
        default_bucket="uncertain",
        allowed_buckets=frozenset({"uncertain"}),
        is_hard_resistant=False,
        is_scorable=False,
        # Explainers describe how the category works (e.g., "here's
        # how ESIGN compliance is structured"). NOT product
        # evaluators. Excluded from scorable by default.
        typical_objections=(),
        typical_proof_needs=(),
        founder_report_section="category_knowledge_replies",
        graph_influence_note=(
            "medium influence on proof_seekers + target evaluators"
        ),
    ),
    "meta_commenter": AudienceRoleSpec(
        role="meta_commenter",
        default_bucket="uncertain",  # technically "noise" — see scorable=False
        allowed_buckets=frozenset({"uncertain"}),
        is_hard_resistant=False,
        is_scorable=False,
        # Meta-commenters react to business model, OSS philosophy,
        # pricing strategy framing, aesthetics — NOT adoption signal.
        typical_objections=("business_model_philosophy",),
        typical_proof_needs=(),
        founder_report_section="meta_discussion",
        graph_influence_note="very low influence on adoption",
    ),
    "category_skeptic": AudienceRoleSpec(
        role="category_skeptic",
        default_bucket="skeptical",
        allowed_buckets=frozenset({"skeptical"}),  # LOCKED
        is_hard_resistant=True,
        is_scorable=True,
        typical_objections=(
            "category_doesnt_solve_real_problem",
            "no_value_in_this_category",
        ),
        typical_proof_needs=(
            "proof_the_category_itself_works",
        ),
        founder_report_section="category_level_resistance",
        graph_influence_note=(
            "high resistance amplifier in skeptic clusters"
        ),
    ),
    "incumbent_defender": AudienceRoleSpec(
        role="incumbent_defender",
        default_bucket="skeptical",
        allowed_buckets=frozenset({"skeptical"}),  # LOCKED
        is_hard_resistant=True,
        is_scorable=True,
        typical_objections=(
            "incumbent_already_solves_this",
            "incumbent_workflow_is_fine",
        ),
        typical_proof_needs=(
            "reason_to_switch_outweighing_status_quo",
        ),
        founder_report_section="incumbent_defense",
        graph_influence_note=(
            "high influence on existing_competitor_user voices"
        ),
    ),
    "casual_bystander": AudienceRoleSpec(
        role="casual_bystander",
        default_bucket="uncertain",
        allowed_buckets=frozenset({"uncertain"}),
        is_hard_resistant=False,
        is_scorable=False,
        # Casual readers: curiosity questions, no real evaluation.
        # Default off the scorable distribution; the founder can see
        # them in noise_meta_estimate.
        typical_objections=(),
        typical_proof_needs=(),
        founder_report_section="casual_readers",
        graph_influence_note="near-zero influence on adoption",
    ),
    "off_topic_noise_candidate": AudienceRoleSpec(
        role="off_topic_noise_candidate",
        default_bucket="uncertain",
        allowed_buckets=frozenset({"uncertain"}),
        is_hard_resistant=False,
        is_scorable=False,
        typical_objections=(),
        typical_proof_needs=(),
        founder_report_section="off_topic",
        graph_influence_note="zero influence",
    ),
    # Phase 12E.5O — Product Hunt-flavored roles.
    "shallow_positive_commenter": AudienceRoleSpec(
        role="shallow_positive_commenter",
        default_bucket="receptive",
        allowed_buckets=frozenset({"receptive"}),
        # Captures the PH "looks great / nice / love this" pattern.
        # Bucket-LOCKED to receptive so this role can never be
        # mis-routed into buyer (no real adoption signal in the
        # comment text), and never into skeptical (it's positively
        # toned). Scorable because labelers map "looks great" to
        # receptive in the same way.
        is_hard_resistant=False,
        is_scorable=True,
        typical_objections=(),
        typical_proof_needs=(),
        founder_report_section="shallow_positive_reactions",
        graph_influence_note=(
            "low influence — positive but non-evaluative"
        ),
    ),
    "founder_network_supporter": AudienceRoleSpec(
        role="founder_network_supporter",
        default_bucket="uncertain",
        allowed_buckets=frozenset({"uncertain"}),
        # Friend / maker-network launch support. Treated as noise/meta
        # because the comment reflects relational support, not market
        # signal. Excluded from scorable. Distinct from
        # off_topic_noise_candidate because the supporter IS engaging
        # with the launch, just not evaluating the product.
        is_hard_resistant=False,
        is_scorable=False,
        typical_objections=(),
        typical_proof_needs=(),
        founder_report_section="network_support",
        graph_influence_note=(
            "near-zero influence on adoption decisions"
        ),
    ),
    "early_adopter": AudienceRoleSpec(
        role="early_adopter",
        default_bucket="receptive",
        allowed_buckets=frozenset({"buyer", "receptive", "uncertain"}),
        # Captures the "would try / on the waitlist / signing up
        # tonight" voices. Bucket-flexible: an early adopter with
        # explicit adoption verbs is routed to buyer; "would try if
        # X" is receptive; "interested but waiting for v2" is
        # uncertain.
        is_hard_resistant=False,
        is_scorable=True,
        typical_objections=(),
        typical_proof_needs=(
            "ease_of_trial",
            "low_risk_to_try",
        ),
        founder_report_section="early_adopter_signal",
        graph_influence_note=(
            "high influence on shallow_positive_commenter cluster"
        ),
    ),
}


# Source profiles — weak priors, calibrate per validation case.
SOURCE_PROFILES: dict[LaunchSource, dict[AudienceRole, float]] = {
    "default": {
        # Legacy-compatible: target-customer-heavy. Behaves like
        # pre-Phase-12E when launch_source is missing from the brief.
        "target_customer_evaluator": 0.70,
        "existing_competitor_user": 0.20,
        "proof_seeker_only": 0.05,
        "category_skeptic": 0.05,
        # Other roles weight 0 under default.
        "industry_observer": 0.0,
        "technical_or_legal_explainer": 0.0,
        "meta_commenter": 0.0,
        "incumbent_defender": 0.0,
        "casual_bystander": 0.0,
        "off_topic_noise_candidate": 0.0,
        # Phase 12E.5O — PH-flavored roles, zero-weighted in default
        # so this profile remains legacy-compatible byte-for-byte
        # behavior at non-PH sources.
        "shallow_positive_commenter": 0.0,
        "founder_network_supporter": 0.0,
        "early_adopter": 0.0,
    },
    "hn_show_hn": {
        # Weak prior — HN / Show HN typically has a long tail of
        # non-customer commenters. Calibrate as more cases land.
        "target_customer_evaluator": 0.22,
        "existing_competitor_user": 0.15,
        "proof_seeker_only": 0.12,
        "industry_observer": 0.18,
        "technical_or_legal_explainer": 0.08,
        "meta_commenter": 0.08,
        "category_skeptic": 0.06,
        "incumbent_defender": 0.05,
        "casual_bystander": 0.04,
        "off_topic_noise_candidate": 0.02,
        # Phase 12E.5O — zero in v1 to preserve byte-for-byte
        # stability of the pre-12E.5O HN baseline.
        "shallow_positive_commenter": 0.0,
        "founder_network_supporter": 0.0,
        "early_adopter": 0.0,
    },
    # Phase 12E.5C — `hn_show_hn_v2` is the offline-recalibrated
    # profile from Phase 12E.5B. It is OPT-IN: briefs must request
    # `launch_source="hn_show_hn_v2"` explicitly. Do not promote to
    # default until paid confirmation lands on ≥2 products.
    #
    # Calibration source: 12E.5B grid search across DocuSeal + Opslane
    # saved Phase 12E artifacts, scored against the corrected DocuSeal
    # QA labels and raw Opslane labels via the Phase 12E.5A market-
    # fidelity scoring methodology. Offline projection improvements:
    #   - DocuSeal source-audience MAE 13.98pp → 9.93pp (−4.05pp)
    #   - Opslane source-audience MAE  10.58pp → 5.89pp (−4.69pp)
    #   - worst-case MAE 13.98pp → 9.93pp
    # Largest single role-weight delta: industry_observer 18% → 8%
    # (the diagnosed source of uncertain over-injection on Opslane).
    "hn_show_hn_v2": {
        "target_customer_evaluator": 0.2000,
        "existing_competitor_user": 0.1875,
        "proof_seeker_only": 0.1200,
        "industry_observer": 0.0800,
        "technical_or_legal_explainer": 0.1000,
        "meta_commenter": 0.1000,
        "category_skeptic": 0.0750,
        "incumbent_defender": 0.0625,
        "casual_bystander": 0.0500,
        "off_topic_noise_candidate": 0.0250,
        # Phase 12E.5O — zero in v2 to preserve the calibrated
        # weights from 12E.5B. PH-flavored roles only apply when
        # `product_hunt_v1` is requested explicitly.
        "shallow_positive_commenter": 0.0,
        "founder_network_supporter": 0.0,
        "early_adopter": 0.0,
    },
    # Phase 12E.5O — `product_hunt_v1` is the OPT-IN PH audience
    # profile. PH differs from HN: more makers/operators, more
    # shallow praise, more "congrats on launch", more founder-network
    # support, more early-adopter "would try" language, and less deep
    # technical skepticism. Weights are FIRST-VERSION weak priors,
    # not tuned to any specific product — calibrate as PH validation
    # cases land. Promotion path: must reach paid-confirmation
    # validation support on ≥2 PH products before becoming default
    # for PH-sourced briefs.
    #
    # Three new role types appear at non-zero weight only in this
    # profile:
    #   - shallow_positive_commenter (0.09): the "looks great!" pattern
    #   - founder_network_supporter (0.07): friend / maker-network
    #     launch support, non-evaluative
    #   - early_adopter (0.04): explicit "would try / waitlist / sign
    #     up" voices, bucket-flexible across buyer/receptive/uncertain
    "product_hunt_v1": {
        "target_customer_evaluator": 0.2200,
        "existing_competitor_user": 0.1100,
        "proof_seeker_only": 0.0700,
        "industry_observer": 0.0500,
        "technical_or_legal_explainer": 0.0400,
        "meta_commenter": 0.1200,
        "category_skeptic": 0.0300,
        "incumbent_defender": 0.0250,
        "casual_bystander": 0.0900,
        "off_topic_noise_candidate": 0.0450,
        "shallow_positive_commenter": 0.0900,
        "founder_network_supporter": 0.0700,
        "early_adopter": 0.0400,
    },
}


# --- Public API ------------------------------------------------------


def get_role_spec(role: str | None) -> AudienceRoleSpec | None:
    """Return the spec for `role` or None if role is unknown / None."""
    if not role:
        return None
    return AUDIENCE_ROLES.get(role)


def get_profile(
    launch_source: str | None,
) -> dict[AudienceRole, float]:
    """Return the proportional role mix for `launch_source`.

    Unknown / missing → default profile.
    """
    src = resolve_launch_source(launch_source)
    return SOURCE_PROFILES[src]


def resolve_launch_source(value: str | None) -> LaunchSource:
    """Normalize an external string to a known LaunchSource. Unknown
    falls back to `default`.

    Phase 12E.5O — `product_hunt` resolves to `product_hunt_v1` so
    operators can request the friendly name from briefs/GTM channel
    while the registry keeps a versioned key.
    """
    if value is None:
        return "default"
    v = str(value).strip().lower()
    if v == "product_hunt":
        return "product_hunt_v1"
    if v in SOURCE_PROFILES:
        return v  # type: ignore[return-value]
    return "default"


def is_scorable_role(role: str | None) -> bool:
    """Default scorability for a role. Reports may override per
    operator preference, but this is the default."""
    spec = get_role_spec(role)
    if spec is None:
        # Legacy / missing role → treat as scorable target customer.
        return True
    return spec.is_scorable


def is_hard_resistant_role(role: str | None) -> bool:
    """Roles that are hard-resistant by definition."""
    spec = get_role_spec(role)
    return bool(spec and spec.is_hard_resistant)


def role_locked_default_bucket(role: str | None) -> str | None:
    """Return the locked default bucket if a role's allowed_buckets
    is a single value, else None (caller is free to route within
    allowed_buckets)."""
    spec = get_role_spec(role)
    if not spec:
        return None
    if len(spec.allowed_buckets) == 1:
        return next(iter(spec.allowed_buckets))
    return None


def allocate_role_counts(
    launch_source: str | None,
    n_personas: int,
) -> dict[AudienceRole, int]:
    """Given a launch_source profile and a target persona count, return
    the integer number of personas per role.

    Uses largest-remainder-method allocation so totals sum to
    `n_personas` exactly. Roles with profile weight 0 get 0.
    """
    profile = get_profile(launch_source)
    if n_personas <= 0:
        return {role: 0 for role in profile}

    raw = {role: weight * n_personas for role, weight in profile.items()}
    floors = {role: int(v) for role, v in raw.items()}
    remainder_total = n_personas - sum(floors.values())
    # Sort by fractional remainder desc; distribute the leftover
    by_frac = sorted(
        raw.items(),
        key=lambda kv: -(kv[1] - int(kv[1])),
    )
    out = dict(floors)
    i = 0
    while remainder_total > 0 and i < len(by_frac) * 4:
        role, _ = by_frac[i % len(by_frac)]
        # Only add to roles with non-zero base weight; preserves the
        # default profile's intent that zero-weight roles stay at 0.
        if profile[role] > 0:
            out[role] += 1
            remainder_total -= 1
        i += 1
    return out
