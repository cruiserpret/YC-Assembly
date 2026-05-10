"""Phase 10B.1 — agent grounding + discussion quality patch.

Five concerns live here:
  * product_fact_card  — author-of-record product facts injected
                         into every discussion prompt
  * grounding_validator — post-hoc misunderstanding / already-provided
                         fact / wrong-category violations
  * stance_calibrator   — post-hoc rule-based stance review
                         (Receptive / Uncertain / Resistant)
  * caveat_leak         — detect + strip system caveats that leaked
                         into persona speech ("synthetic n=24 chat",
                         "directional, not a verdict", …)
  * diversity_auditor   — repetition / repeated-opener counts +
                         persona voice diversity score
"""
from assembly.sources.product_grounding.product_fact_card import (
    AccessoryPrice,
    ProductFactCard,
    generate_product_fact_card,
    fact_card_prompt_block,
)
from assembly.sources.product_grounding.grounding_validator import (
    audit_product_grounding,
)
from assembly.sources.product_grounding.stance_calibrator import (
    calibrate_stance,
    calibrate_ballots,
)
from assembly.sources.product_grounding.caveat_leak import (
    PERSONA_FORBIDDEN_PHRASES,
    detect_caveat_leak,
    strip_caveat_leak,
    audit_ballot_caveat_leaks,
)
from assembly.sources.product_grounding.diversity_auditor import (
    audit_discussion_diversity,
)
from assembly.sources.product_grounding.price_hierarchy import (
    audit_price_hierarchy,
    audit_provided_fact_accuracy,
    repair_price_confusion,
)
from assembly.sources.product_grounding.provided_fact_lock_v2 import (
    audit_provided_fact_lock_v2,
    repair_known_fact_reask,
)
from assembly.sources.product_grounding.human_society_realism import (
    SELF_AWARENESS_PHRASES,
    audit_human_society_realism,
    detect_self_awareness_leak,
    strip_self_awareness_leak,
)
from assembly.sources.product_grounding.stance_strictness import (
    audit_stance_strictness,
    classify_stance_strictness,
)
from assembly.sources.product_grounding.report_polish import (
    build_best_fit_audience,
    build_confident_headline,
    build_evidence_flavor,
    build_hardest_to_convince,
    humanize_role,
    role_distribution_from_ballots,
)
from assembly.sources.product_grounding.negation_scope_validator import (
    audit_forbidden_features,
    audit_negation_scope,
    repair_forbidden_feature_mentions,
    repair_negation_scope_inversion,
)
from assembly.sources.product_grounding.forbidden_features import (
    ForbiddenFeature,
    expand_forbidden_tokens,
    extract_forbidden_features,
)
from assembly.sources.product_grounding.input_mechanism_validator import (
    audit_input_mechanism,
)
from assembly.sources.product_grounding.receptive_strictness_v3 import (
    audit_receptive_strictness_v3,
    classify_stance_strictness_v3,
)


__all__ = [
    "AccessoryPrice",
    "ProductFactCard",
    "generate_product_fact_card",
    "fact_card_prompt_block",
    "audit_product_grounding",
    "calibrate_stance",
    "calibrate_ballots",
    "PERSONA_FORBIDDEN_PHRASES",
    "detect_caveat_leak",
    "strip_caveat_leak",
    "audit_ballot_caveat_leaks",
    "audit_discussion_diversity",
    "audit_price_hierarchy",
    "audit_provided_fact_accuracy",
    "repair_price_confusion",
    # Phase 10B.3 ----
    "audit_provided_fact_lock_v2",
    "repair_known_fact_reask",
    "SELF_AWARENESS_PHRASES",
    "audit_human_society_realism",
    "detect_self_awareness_leak",
    "strip_self_awareness_leak",
    "audit_stance_strictness",
    "classify_stance_strictness",
    "build_best_fit_audience",
    "build_confident_headline",
    "build_evidence_flavor",
    "build_hardest_to_convince",
    "humanize_role",
    "role_distribution_from_ballots",
    # Phase 10B.4 ----
    "audit_negation_scope",
    "repair_negation_scope_inversion",
    # Phase 10B.6 ----
    "ForbiddenFeature",
    "extract_forbidden_features",
    "expand_forbidden_tokens",
    "audit_forbidden_features",
    "repair_forbidden_feature_mentions",
    "audit_input_mechanism",
    "audit_receptive_strictness_v3",
    "classify_stance_strictness_v3",
]
