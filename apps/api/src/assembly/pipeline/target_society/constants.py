"""Phase 8.2G — closed enums, family templates, sensitive markers.

The planner is deterministic and works by:

  1. detecting a `ProductFamily` from the brief (commerce platform,
     consumer-packaged-good, consumer-electronics, financial-product,
     b2b-saas, or default-general)
  2. expanding the per-family baseline stakeholder template
  3. augmenting with competitor / geography / sensitivity hooks

These constants are the single source of truth for every "what
counts as commerce-shaped" / "what counts as financial-product
sensitive" decision in the package.
"""
from __future__ import annotations

import enum
from typing import Final


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------


class ProductFamily(str, enum.Enum):
    COMMERCE_PLATFORM_OR_TOOLING = "commerce_platform_or_tooling"
    CONSUMER_PACKAGED_GOOD = "consumer_packaged_good"
    CONSUMER_ELECTRONICS = "consumer_electronics"
    FINANCIAL_PRODUCT = "financial_product"
    B2B_SAAS = "b2b_saas"
    DEFAULT_GENERAL = "default_general"


class SimulationGoal(str, enum.Enum):
    TEST_PRODUCT_CONCEPT = "test_product_concept"
    TEST_PRICE = "test_price"
    TEST_POSITIONING = "test_positioning"
    TEST_COMPETITOR_REPLACEMENT = "test_competitor_replacement"
    TEST_MESSAGE = "test_message"
    TEST_TRUST_OBJECTION_BARRIERS = "test_trust_objection_barriers"
    # Phase 8.4A.2 — unlaunched-product market-entry test. The brief
    # carries no direct product evidence; relevance is anchored on
    # competitor / substitute / category / use-occasion evidence.
    # Triggers the dynamic_market_entry_planner (categories generated
    # from primitives) + the market-entry weight profile in the
    # audience-retrieval scorer.
    TEST_MARKET_ENTRY = "test_market_entry"


class WarningSeverity(str, enum.Enum):
    INFO = "info"
    CAVEAT = "caveat"
    WARNING = "warning"
    BLOCKER = "blocker"


# Warning codes
WARNING_MISSING_GEOGRAPHY: Final[str] = "missing_geography"
WARNING_MISSING_PRICE: Final[str] = "missing_price"
WARNING_MISSING_COMPETITORS: Final[str] = "missing_competitors"
WARNING_SENSITIVE_TARGETING_CAVEAT: Final[str] = "sensitive_targeting_caveat"
WARNING_THIN_EVIDENCE_RISK: Final[str] = "thin_evidence_risk"
WARNING_PUBLIC_DATA_SKEW: Final[str] = "public_data_skew"
WARNING_LLM_SIMULATION_LIMITATION: Final[str] = "llm_simulation_limitation"
WARNING_PROTECTED_ATTRIBUTE_INFERENCE_FORBIDDEN: Final[str] = (
    "protected_attribute_inference_forbidden"
)


# ---------------------------------------------------------------------------
# Sensitive / protected-attribute detection — case-insensitive substrings
# in any of the brief's text fields trigger a SENSITIVE_TARGETING_CAVEAT
# warning AND require every stakeholder category to carry a
# sensitivity_or_compliance_notes string.
# ---------------------------------------------------------------------------


SENSITIVE_TARGETING_KEYWORDS: Final[tuple[str, ...]] = (
    # religious targeting
    "halal", "kosher", "shariah", "sharia", "islamic", "religious",
    "religion", "muslim", "jewish", "christian", "buddhist", "hindu",
    # ethnic / race targeting
    "ethnic", "race-based", "racial", "minority", "diaspora",
    "first nations", "indigenous",
    # gender/sexuality targeting
    "lgbt", "lgbtq", "queer", "transgender", "gender identity",
    # health / disability
    "health condition", "diabetic", "hiv", "disability", "disabled",
    "mental health", "autism", "chronic illness",
    # immigration / citizenship
    "immigrant", "undocumented", "asylum", "refugee", "citizenship",
    # income tier (US protected-class adjacent)
    "low-income", "section 8",
)


# ---------------------------------------------------------------------------
# Family detection — keyword classifier. The first family with a
# matching keyword wins. Order matters; more-specific families come
# first.
# ---------------------------------------------------------------------------


FAMILY_DETECTION_KEYWORDS: Final[dict[ProductFamily, tuple[str, ...]]] = {
    ProductFamily.COMMERCE_PLATFORM_OR_TOOLING: (
        "shopify", "ecommerce platform", "commerce platform", "store builder",
        "ai commerce", "store builder", "merchant tooling", "checkout",
        "subscription commerce", "headless commerce", "dtc platform",
        "ecommerce app", "shopify app", "shopify plugin",
    ),
    ProductFamily.CONSUMER_ELECTRONICS: (
        "iphone", "smartphone", "android phone", "laptop", "tablet",
        "wearable", "smartwatch", "earbuds", "headphones", "tv",
        "gaming console", "consumer electronics", "device upgrade",
        "phone carrier",
    ),
    ProductFamily.FINANCIAL_PRODUCT: (
        "loan", "mortgage", "halal financing", "credit card", "investing",
        "investment product", "insurance", "savings account",
        "lending", "fintech", "crypto", "trading platform", "wealth",
        "retirement", "401k", "ira", "robo-advisor",
    ),
    ProductFamily.B2B_SAAS: (
        "b2b saas", "enterprise saas", "developer tool", "api platform",
        "ci/cd", "devops", "observability", "iam", "compliance saas",
        "data platform", "team collaboration", "knowledge management",
    ),
    ProductFamily.CONSUMER_PACKAGED_GOOD: (
        "bottled water", "snack", "supplement", "vitamin", "beverage",
        "energy drink", "soft drink", "coffee", "tea", "cereal",
        "packaged food", "personal care", "skincare", "haircare",
        "cosmetic", "household goods", "cleaning product",
        "premium water", "sparkling water",
    ),
}


# ---------------------------------------------------------------------------
# Per-family baseline stakeholder templates.
#
# Each entry is a list of dicts; the planner instantiates each as a
# StakeholderCategory after applying brief-specific overrides.
# Keep these LITERAL — the planner copies them verbatim, then injects
# competitor / geography hooks.
# ---------------------------------------------------------------------------


_HIGH = "high"
_MEDIUM = "medium"
_LOW = "low"


COMMERCE_PLATFORM_TEMPLATE: Final[tuple[dict, ...]] = (
    {
        "category_key": "shopify_or_platform_merchant",
        "display_name": "Platform merchant (Shopify-style)",
        "description": (
            "Operators of an existing online store who would adopt or "
            "reject the product based on commerce-tooling fit."
        ),
        "why_relevant": (
            "Commerce-platform tooling targets active store operators "
            "first; their voice is the most direct test of fit."
        ),
        "likely_pains": [
            "plugin bloat", "app fatigue", "store-setup friction",
            "high recurring fees",
        ],
        "likely_objections": [
            "lock-in", "loss of brand control", "AI quality",
            "migration cost",
        ],
        "likely_current_alternatives": [
            "native platform features", "marketplace apps",
            "in-house developer", "agency",
        ],
        "evidence_needed": [
            "first-person merchant complaints",
            "review/forum discussion of platform pain",
            "explicit price-sensitivity quotes",
        ],
        "source_query_themes": [
            "merchant plugin complaints", "merchant pricing complaints",
            "store-builder review", "platform tool fatigue",
        ],
        "inclusion_signals": [
            "self-described merchant / store owner",
            "$X/month tier complaint",
            "explicit alternative tool mentioned",
        ],
        "exclusion_signals": [
            "agency-marketing voice", "tool-vendor pitch",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 4,
        "minimum_persona_target_serious": 12,
        "priority": _HIGH,
    },
    {
        "category_key": "dtc_founder_brand_control",
        "display_name": "DTC founder with brand-control concerns",
        "description": (
            "Founders running direct-to-consumer brands who weight "
            "brand identity and pixel-control above raw automation."
        ),
        "why_relevant": (
            "Brand-control objection is the dominant reason DTC founders "
            "reject AI-driven commerce tooling."
        ),
        "likely_pains": [
            "loss of brand identity", "agency redesign cost",
            "inconsistent on-brand output",
        ],
        "likely_objections": [
            "AI breaks brand rules", "uncanny generic output",
            "no final pixel control",
        ],
        "likely_current_alternatives": [
            "agency", "freelancer", "in-house designer",
            "custom theme",
        ],
        "evidence_needed": [
            "first-person founder voice on brand control",
            "agency-cost complaints",
        ],
        "source_query_themes": [
            "DTC founder brand control",
            "ecommerce founder brand identity concerns",
        ],
        "inclusion_signals": [
            "self-described founder", "explicit brand-control language",
        ],
        "exclusion_signals": [
            "agency promoting their service",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 3,
        "minimum_persona_target_serious": 8,
        "priority": _HIGH,
    },
    {
        "category_key": "agency_dependent_merchant",
        "display_name": "Agency-dependent merchant",
        "description": (
            "Merchants currently spending on agency or freelancer "
            "support; AI tooling competes with that spend."
        ),
        "why_relevant": (
            "Their willingness-to-switch and switching cost determine "
            "the addressable market for agency-replacement tooling."
        ),
        "likely_pains": [
            "monthly retainer", "slow turnaround",
            "redesign-cost inflation",
        ],
        "likely_objections": [
            "agency relationship value", "trust gap with AI",
            "service-level concerns",
        ],
        "likely_current_alternatives": [
            "boutique agency", "freelancer", "fiverr / upwork",
        ],
        "evidence_needed": [
            "first-person quotes about agency cost",
            "thread comparing agency vs in-house tools",
        ],
        "source_query_themes": [
            "merchant agency cost", "freelancer cost ecommerce",
        ],
        "inclusion_signals": [
            "explicit agency / freelancer mention",
            "monthly retainer complaint",
        ],
        "exclusion_signals": [
            "agency self-promotion",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 3,
        "minimum_persona_target_serious": 6,
        "priority": _MEDIUM,
    },
    {
        "category_key": "ai_skeptical_operator",
        "display_name": "AI-skeptical operator",
        "description": (
            "Operators who explicitly distrust or doubt AI-driven "
            "automation in commerce."
        ),
        "why_relevant": (
            "AI-skepticism is the dominant trust barrier; the simulation "
            "needs voices that test the product's reassurance message."
        ),
        "likely_pains": [
            "broken AI output", "AI hallucinations on product copy",
        ],
        "likely_objections": [
            "lack of trust in AI", "no proof of reliability",
        ],
        "likely_current_alternatives": [
            "manual workflow", "human assistant", "in-house team",
        ],
        "evidence_needed": [
            "skepticism quotes", "broken-AI anecdotes",
        ],
        "source_query_themes": [
            "AI skepticism merchant", "AI store builder distrust",
        ],
        "inclusion_signals": [
            "self-described skeptic", "AI-broke-my-store anecdote",
        ],
        "exclusion_signals": [
            "AI-vendor pitch",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 5,
        "priority": _MEDIUM,
    },
    {
        "category_key": "nontechnical_founder",
        "display_name": "Nontechnical founder",
        "description": (
            "Founders without engineering background; sensitive to "
            "setup-complexity and dependency on developers."
        ),
        "why_relevant": (
            "Nontechnical-founder usability is a structural buy/no-buy "
            "factor for any commerce-tooling product."
        ),
        "likely_pains": [
            "store setup friction", "code complexity",
        ],
        "likely_objections": [
            "I'm not technical enough",
            "I don't want to manage developers",
        ],
        "likely_current_alternatives": [
            "no-code template", "agency for setup",
            "buying a done-for-you store",
        ],
        "evidence_needed": [
            "explicit non-technical-founder voice",
        ],
        "source_query_themes": [
            "non-technical founder Shopify",
            "small business owner ecommerce overwhelmed",
        ],
        "inclusion_signals": [
            "self-described non-technical / non-coder",
        ],
        "exclusion_signals": [
            "developer-targeted content",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
)


CONSUMER_PACKAGED_GOOD_TEMPLATE: Final[tuple[dict, ...]] = (
    {
        "category_key": "mass_market_grocery_buyer",
        "display_name": "Mass-market grocery buyer",
        "description": (
            "Shoppers who buy this category at supermarket / convenience "
            "channels at the everyday price point."
        ),
        "why_relevant": (
            "Largest-volume buyer segment for any consumer-packaged-good "
            "category; their reaction defines the bulk-of-market story."
        ),
        "likely_pains": [
            "price spikes", "out-of-stock at preferred channel",
            "inconvenient packaging size",
        ],
        "likely_objections": [
            "too expensive vs current brand",
            "no clear reason to switch",
        ],
        "likely_current_alternatives": [
            "incumbent mass-market brand", "store private label",
        ],
        "evidence_needed": [
            "shopper review / complaint",
            "price-comparison comment",
        ],
        "source_query_themes": [
            "shopper review", "grocery price comparison",
            "channel availability complaint",
        ],
        "inclusion_signals": [
            "self-described regular shopper",
            "specific incumbent brand mention",
        ],
        "exclusion_signals": [
            "brand marketing voice",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 4,
        "minimum_persona_target_serious": 10,
        "priority": _HIGH,
    },
    {
        "category_key": "premium_buyer",
        "display_name": "Premium / luxury buyer",
        "description": (
            "Shoppers willing to pay above-category price for status, "
            "story, or perceived quality."
        ),
        "why_relevant": (
            "Premium pricing tests require a voice that explicitly "
            "values story / brand / quality over price."
        ),
        "likely_pains": [
            "lack of brand story", "perceived inauthenticity",
        ],
        "likely_objections": [
            "is this actually different",
            "can I get the same at lower price",
        ],
        "likely_current_alternatives": [
            "incumbent premium brand", "boutique alternative",
        ],
        "evidence_needed": [
            "premium-tier review", "brand-story discussion",
        ],
        "source_query_themes": [
            "premium brand review", "luxury alternative comparison",
        ],
        "inclusion_signals": [
            "willing to pay more",
            "specific premium brand mention",
        ],
        "exclusion_signals": [
            "extreme price-sensitivity language",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 3,
        "minimum_persona_target_serious": 6,
        "priority": _HIGH,
    },
    {
        "category_key": "price_sensitive_buyer",
        "display_name": "Price-sensitive buyer",
        "description": (
            "Shoppers who explicitly weight price first; reject premium "
            "pricing without strong rationale."
        ),
        "why_relevant": (
            "Direct test for the product's price proposition; identifies "
            "rejection segments and substitution risk."
        ),
        "likely_pains": [
            "too expensive", "smaller pack costs more",
            "private label is cheaper",
        ],
        "likely_objections": [
            "not worth $X",
            "I can get the store brand cheaper",
        ],
        "likely_current_alternatives": [
            "store private label", "tap water / generic alternative",
            "bulk pack",
        ],
        "evidence_needed": [
            "explicit price complaint",
            "private-label comparison",
        ],
        "source_query_themes": [
            "too expensive review", "private label vs brand",
        ],
        "inclusion_signals": [
            "explicit price comparison",
        ],
        "exclusion_signals": [
            "premium-tier framing",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 3,
        "minimum_persona_target_serious": 6,
        "priority": _HIGH,
    },
    {
        "category_key": "sustainability_conscious_buyer",
        "display_name": "Sustainability-conscious buyer",
        "description": (
            "Shoppers who weight environmental impact, packaging, or "
            "supply-chain ethics in purchase decisions."
        ),
        "why_relevant": (
            "For consumer goods (especially water / beverages / packaging-"
            "heavy categories), sustainability is a real switching driver."
        ),
        "likely_pains": [
            "single-use plastic", "long supply chains",
        ],
        "likely_objections": [
            "single-use plastic concerns",
            "carbon footprint of bottling",
        ],
        "likely_current_alternatives": [
            "reusable bottle / filter", "boxed alternative",
        ],
        "evidence_needed": [
            "sustainability complaint", "packaging review",
        ],
        "source_query_themes": [
            "single-use plastic review",
            "sustainability complaint beverage",
        ],
        "inclusion_signals": [
            "explicit sustainability framing",
        ],
        "exclusion_signals": [
            "indifferent-to-sustainability framing",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
    {
        "category_key": "fitness_lifestyle_buyer",
        "display_name": "Fitness / lifestyle buyer",
        "description": (
            "Shoppers who buy this category as part of a fitness, gym, "
            "or wellness routine."
        ),
        "why_relevant": (
            "Lifestyle-aligned buyers are the second wedge for premium "
            "beverage / supplement / wellness products."
        ),
        "likely_pains": [
            "not enough hydration on the go",
            "supplement gaps",
        ],
        "likely_objections": [
            "doesn't fit my routine",
            "another bottle to carry",
        ],
        "likely_current_alternatives": [
            "sports drink", "electrolyte tablet", "filtered water",
        ],
        "evidence_needed": [
            "fitness-routine review",
        ],
        "source_query_themes": [
            "gym water bottle review",
            "fitness hydration product complaint",
        ],
        "inclusion_signals": [
            "explicit gym / training context",
        ],
        "exclusion_signals": [
            "casual non-fitness framing only",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
    {
        "category_key": "skeptical_rejector",
        "display_name": "Skeptical rejector",
        "description": (
            "Shoppers who explicitly reject the product premise (e.g. "
            "$10 bottled water as ridiculous)."
        ),
        "why_relevant": (
            "Rejector voice is essential for any premium-pricing test; "
            "without it the simulation only models receptive segments."
        ),
        "likely_pains": [
            "perceived rip-off", "marketing fatigue",
        ],
        "likely_objections": [
            "no product is worth $X",
            "this is just water in a bottle",
        ],
        "likely_current_alternatives": [
            "tap water", "store brand", "DIY alternative",
        ],
        "evidence_needed": [
            "explicit rejection / outrage thread",
        ],
        "source_query_themes": [
            "ridiculous price product review",
            "rip-off premium product complaint",
        ],
        "inclusion_signals": [
            "explicit rejection of category premium",
        ],
        "exclusion_signals": [
            "premium-buyer framing",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
)


CONSUMER_ELECTRONICS_TEMPLATE: Final[tuple[dict, ...]] = (
    {
        "category_key": "current_product_user",
        "display_name": "Current-product user",
        "description": (
            "Existing users of the same brand/line considering an upgrade "
            "or replacement."
        ),
        "why_relevant": (
            "Upgrade-cycle adoption is the dominant near-term driver of "
            "consumer-electronics revenue; their voice tests the upgrade "
            "story."
        ),
        "likely_pains": [
            "battery life", "storage",
            "feature gap vs latest model",
        ],
        "likely_objections": [
            "current device still works",
            "incremental upgrade not worth it",
        ],
        "likely_current_alternatives": [
            "keep existing device",
            "buy refurbished previous model",
        ],
        "evidence_needed": [
            "upgrade complaint thread",
            "model-vs-model comparison",
        ],
        "source_query_themes": [
            "phone upgrade review",
            "should I upgrade smartphone",
        ],
        "inclusion_signals": [
            "explicit current-model ownership",
        ],
        "exclusion_signals": [
            "competitor-fan voice only",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 4,
        "minimum_persona_target_serious": 10,
        "priority": _HIGH,
    },
    {
        "category_key": "competitor_user",
        "display_name": "Competitor-platform user",
        "description": (
            "Users of competing brands; switch decision is the test."
        ),
        "why_relevant": (
            "Competitor-user voice is required to test market-share "
            "claims and switching cost."
        ),
        "likely_pains": [
            "ecosystem lock-in",
            "different OS learning curve",
        ],
        "likely_objections": [
            "I'm tied to my ecosystem",
            "other brand has feature X I rely on",
        ],
        "likely_current_alternatives": [
            "stay with current brand",
            "minor model upgrade",
        ],
        "evidence_needed": [
            "explicit competitor-brand discussion",
            "switching-cost thread",
        ],
        "source_query_themes": [
            "switch from competitor to product",
            "competitor vs product comparison",
        ],
        "inclusion_signals": [
            "self-described competitor user",
        ],
        "exclusion_signals": [
            "tech-press review only",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 3,
        "minimum_persona_target_serious": 8,
        "priority": _HIGH,
    },
    {
        "category_key": "upgrade_fatigued_buyer",
        "display_name": "Upgrade-fatigued buyer",
        "description": (
            "Buyers explicitly dissatisfied with annual / frequent "
            "upgrade pressure; resistant to new-model marketing."
        ),
        "why_relevant": (
            "Upgrade-fatigue is the dominant rejection driver in mature "
            "consumer-electronics categories."
        ),
        "likely_pains": [
            "incremental change",
            "perceived planned obsolescence",
        ],
        "likely_objections": [
            "nothing new in this version",
            "I'll wait two more years",
        ],
        "likely_current_alternatives": [
            "keep current device longer",
            "buy refurbished",
        ],
        "evidence_needed": [
            "upgrade fatigue thread",
            "planned-obsolescence complaint",
        ],
        "source_query_themes": [
            "upgrade fatigue smartphone",
            "no reason to upgrade phone",
        ],
        "inclusion_signals": [
            "explicit fatigue language",
        ],
        "exclusion_signals": [
            "every-year-upgrader voice",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 5,
        "priority": _MEDIUM,
    },
    {
        "category_key": "tech_enthusiast",
        "display_name": "Tech enthusiast",
        "description": (
            "Buyers who track specs, benchmark, and discuss new device "
            "features in detail."
        ),
        "why_relevant": (
            "Spec-driven buyers test the technical story and produce "
            "the loudest early-adopter signals."
        ),
        "likely_pains": [
            "feature parity vs competitor",
            "benchmark gap",
        ],
        "likely_objections": [
            "spec X is worse than competitor Y",
        ],
        "likely_current_alternatives": [
            "competitor flagship",
            "previous generation",
        ],
        "evidence_needed": [
            "benchmark / spec discussion",
        ],
        "source_query_themes": [
            "smartphone spec comparison forum",
        ],
        "inclusion_signals": [
            "explicit spec language",
        ],
        "exclusion_signals": [
            "casual buyer voice",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
    {
        "category_key": "ai_feature_skeptic",
        "display_name": "AI-feature skeptic",
        "description": (
            "Buyers who specifically distrust AI features (camera AI, "
            "summarization, on-device assistants)."
        ),
        "why_relevant": (
            "AI-feature skepticism is the dominant 2025-era trust barrier "
            "for any flagship consumer-electronics launch."
        ),
        "likely_pains": [
            "AI hallucinations",
            "privacy of on-device AI",
        ],
        "likely_objections": [
            "AI features are gimmicks",
            "AI is a privacy risk",
        ],
        "likely_current_alternatives": [
            "non-AI workflows",
        ],
        "evidence_needed": [
            "AI-feature complaint thread",
        ],
        "source_query_themes": [
            "smartphone AI feature skepticism",
        ],
        "inclusion_signals": [
            "explicit AI distrust",
        ],
        "exclusion_signals": [
            "AI-fan voice only",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
    {
        "category_key": "price_sensitive_phone_buyer",
        "display_name": "Price-sensitive electronics buyer",
        "description": (
            "Buyers who reject flagship pricing or buy mid-range "
            "alternatives instead."
        ),
        "why_relevant": (
            "Price ceiling testing requires explicit rejector voice."
        ),
        "likely_pains": [
            "flagship-tier pricing",
            "carrier upgrade cost",
        ],
        "likely_objections": [
            "not worth $X for incremental upgrade",
        ],
        "likely_current_alternatives": [
            "mid-range competitor",
            "previous-generation refurbished",
        ],
        "evidence_needed": [
            "explicit price-rejection thread",
        ],
        "source_query_themes": [
            "phone too expensive",
            "mid-range vs flagship comparison",
        ],
        "inclusion_signals": [
            "explicit price-tier rejection",
        ],
        "exclusion_signals": [
            "flagship-buyer framing",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
)


FINANCIAL_PRODUCT_TEMPLATE: Final[tuple[dict, ...]] = (
    {
        "category_key": "compliance_conscious_buyer",
        "display_name": "Compliance-conscious buyer",
        "description": (
            "Buyers who weight regulatory / compliance / religious-rule "
            "alignment as a primary purchase factor. KEEP BROAD; never "
            "infer protected attributes for individual personas."
        ),
        "why_relevant": (
            "Financial products often have explicit compliance angles "
            "(halal, kosher, esg, regulated-market). Compliance-conscious "
            "voice is required when the product targets that segment."
        ),
        "likely_pains": [
            "lack of certified options",
            "unclear compliance claims",
        ],
        "likely_objections": [
            "is this actually compliant",
            "who certifies the product",
        ],
        "likely_current_alternatives": [
            "specialist product from existing provider",
            "no product (DIY workaround)",
        ],
        "evidence_needed": [
            "public discussion of compliance options",
            "regulator / certifier mention",
        ],
        "source_query_themes": [
            "compliance options discussion forum",
            "regulator review of product type",
        ],
        "inclusion_signals": [
            "self-described compliance interest",
        ],
        "exclusion_signals": [
            "private / individual sensitive attribute claim",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 3,
        "minimum_persona_target_serious": 6,
        "priority": _HIGH,
        "_sensitivity_default": True,
    },
    {
        "category_key": "conventional_product_switcher",
        "display_name": "Conventional-product switcher",
        "description": (
            "Buyers currently on a conventional / non-specialist version "
            "of the same product type, considering switching."
        ),
        "why_relevant": (
            "Their switching cost and trust gap define the addressable "
            "market for the specialist product."
        ),
        "likely_pains": [
            "conventional product friction",
            "lack of feature X",
        ],
        "likely_objections": [
            "switching cost",
            "I trust the bigger provider",
        ],
        "likely_current_alternatives": [
            "incumbent conventional product",
        ],
        "evidence_needed": [
            "switching-cost complaint thread",
        ],
        "source_query_themes": [
            "switching financial product complaint",
        ],
        "inclusion_signals": [
            "explicit switching consideration",
        ],
        "exclusion_signals": [
            "no signal of switching intent",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 5,
        "priority": _HIGH,
    },
    {
        "category_key": "skeptical_borrower_or_investor",
        "display_name": "Skeptical borrower / investor",
        "description": (
            "Buyers explicitly distrustful of new financial products, "
            "fintechs, or non-traditional providers."
        ),
        "why_relevant": (
            "Trust gap is the largest barrier to adoption in financial "
            "products; skeptical voice is essential."
        ),
        "likely_pains": [
            "fear of scam",
            "regulator gap",
        ],
        "likely_objections": [
            "this looks too good",
            "what happens if the provider fails",
        ],
        "likely_current_alternatives": [
            "established bank / institution",
        ],
        "evidence_needed": [
            "skepticism / fraud-fear thread",
        ],
        "source_query_themes": [
            "fintech distrust",
            "is this product legit forum",
        ],
        "inclusion_signals": [
            "explicit skepticism",
        ],
        "exclusion_signals": [
            "vendor-promotional voice",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 5,
        "priority": _HIGH,
    },
    {
        "category_key": "real_estate_or_wealth_buyer",
        "display_name": "Real-estate / wealth-product buyer",
        "description": (
            "Buyers in adjacent wealth / real-estate / asset categories; "
            "relevant when the product is a financing / investing / "
            "real-estate product."
        ),
        "why_relevant": (
            "Adjacent wealth-product voice is needed when the financial "
            "product is real-estate-shaped (mortgages, halal financing, "
            "REIT-style products)."
        ),
        "likely_pains": [
            "complex application process",
            "long approval time",
        ],
        "likely_objections": [
            "rate vs incumbent",
        ],
        "likely_current_alternatives": [
            "conventional mortgage",
            "rental",
        ],
        "evidence_needed": [
            "homebuyer / investor public discussion",
        ],
        "source_query_themes": [
            "homebuyer mortgage forum",
            "investor financing forum",
        ],
        "inclusion_signals": [
            "self-described homebuyer / investor",
        ],
        "exclusion_signals": [
            "broker self-promotion",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
    {
        "category_key": "regulated_market_buyer",
        "display_name": "Regulated-market / region-specific buyer",
        "description": (
            "Buyers in a specific regulatory geography (US, UK, EU, "
            "Gulf, India, etc.). KEEP BROAD; do not infer "
            "country-of-residence for individual personas without "
            "explicit source claim."
        ),
        "why_relevant": (
            "Financial-product regulatory context is geography-specific; "
            "a region-specific voice anchors the simulation."
        ),
        "likely_pains": [
            "regional regulatory friction",
        ],
        "likely_objections": [
            "is this product available in my region",
        ],
        "likely_current_alternatives": [
            "regional incumbent provider",
        ],
        "evidence_needed": [
            "regional public discussion",
        ],
        "source_query_themes": [
            "<region> financial product discussion",
        ],
        "inclusion_signals": [
            "explicit regional mention",
        ],
        "exclusion_signals": [
            "non-regional generic content",
        ],
        "minimum_persona_target_tiny": 0,
        "minimum_persona_target_small": 1,
        "minimum_persona_target_serious": 3,
        "priority": _MEDIUM,
    },
)


B2B_SAAS_TEMPLATE: Final[tuple[dict, ...]] = (
    {
        "category_key": "decision_maker_buyer",
        "display_name": "Decision-maker / budget owner",
        "description": (
            "Person with budget authority over the purchase; weighs "
            "cost, ROI, vendor risk."
        ),
        "why_relevant": (
            "B2B SaaS buy decisions are gated by decision-maker buy-in."
        ),
        "likely_pains": [
            "vendor sprawl", "budget overrun",
        ],
        "likely_objections": [
            "another vendor on the stack",
            "ROI unclear",
        ],
        "likely_current_alternatives": [
            "incumbent vendor",
            "in-house build",
        ],
        "evidence_needed": [
            "review-site decision-maker review",
            "RFP / procurement thread",
        ],
        "source_query_themes": [
            "saas vendor evaluation review",
            "budget owner saas concerns",
        ],
        "inclusion_signals": [
            "self-described decision-maker",
        ],
        "exclusion_signals": [
            "end-user-only voice",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 5,
        "priority": _HIGH,
    },
    {
        "category_key": "end_user_buyer",
        "display_name": "End-user / daily operator",
        "description": (
            "Person who actually uses the product day-to-day; voice tests "
            "usability, friction, retention."
        ),
        "why_relevant": (
            "B2B SaaS usage adoption is gated by end-user friction."
        ),
        "likely_pains": [
            "onboarding friction",
            "feature complexity",
        ],
        "likely_objections": [
            "too hard to use",
        ],
        "likely_current_alternatives": [
            "spreadsheet workflow",
            "incumbent tool",
        ],
        "evidence_needed": [
            "end-user review",
        ],
        "source_query_themes": [
            "saas usability complaint",
        ],
        "inclusion_signals": [
            "self-described daily user",
        ],
        "exclusion_signals": [
            "buyer-only voice",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _HIGH,
    },
    {
        "category_key": "it_or_security_gatekeeper",
        "display_name": "IT / security gatekeeper",
        "description": (
            "Person who owns security / compliance review for new "
            "vendors; can block adoption."
        ),
        "why_relevant": (
            "B2B SaaS adoption is often blocked by security review; "
            "their voice tests trust / compliance story."
        ),
        "likely_pains": [
            "vendor risk", "data residency", "SOC2 gap",
        ],
        "likely_objections": [
            "vendor not approved",
            "data location concern",
        ],
        "likely_current_alternatives": [
            "approved vendor list",
        ],
        "evidence_needed": [
            "vendor review thread",
        ],
        "source_query_themes": [
            "vendor security review concerns",
        ],
        "inclusion_signals": [
            "self-described it / security",
        ],
        "exclusion_signals": [
            "buyer-only voice",
        ],
        "minimum_persona_target_tiny": 0,
        "minimum_persona_target_small": 1,
        "minimum_persona_target_serious": 3,
        "priority": _MEDIUM,
    },
    {
        "category_key": "saas_skeptic",
        "display_name": "B2B SaaS skeptic",
        "description": (
            "Buyers who push back on adding more SaaS to the stack."
        ),
        "why_relevant": (
            "Stack-fatigue is a real barrier; skeptical voice is needed."
        ),
        "likely_pains": [
            "saas sprawl",
        ],
        "likely_objections": [
            "we already have too many tools",
        ],
        "likely_current_alternatives": [
            "consolidation onto incumbent",
        ],
        "evidence_needed": [
            "saas-fatigue thread",
        ],
        "source_query_themes": [
            "saas sprawl complaint",
        ],
        "inclusion_signals": [
            "explicit fatigue language",
        ],
        "exclusion_signals": [
            "saas-fan voice only",
        ],
        "minimum_persona_target_tiny": 0,
        "minimum_persona_target_small": 1,
        "minimum_persona_target_serious": 3,
        "priority": _MEDIUM,
    },
)


# Generic fallback — produces a coarse buyer/skeptic/competitor-user
# triple plus a price-sensitive segment. Used when no family classifier
# matches.
DEFAULT_GENERAL_TEMPLATE: Final[tuple[dict, ...]] = (
    {
        "category_key": "primary_target_buyer",
        "display_name": "Primary target buyer",
        "description": (
            "The audience the brief explicitly identifies as the "
            "intended buyer."
        ),
        "why_relevant": (
            "Buyer-voice is required for any product simulation."
        ),
        "likely_pains": ["pain inferred from product context"],
        "likely_objections": [
            "no clear reason to switch",
            "unclear value vs incumbent",
        ],
        "likely_current_alternatives": ["existing alternative"],
        "evidence_needed": [
            "first-person buyer voice",
            "review / forum / comment",
        ],
        "source_query_themes": [
            "<product type> buyer review",
            "<product type> complaint",
        ],
        "inclusion_signals": [
            "self-described buyer / user",
        ],
        "exclusion_signals": [
            "vendor / marketing voice",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 3,
        "minimum_persona_target_serious": 8,
        "priority": _HIGH,
    },
    {
        "category_key": "skeptical_rejector",
        "display_name": "Skeptical rejector",
        "description": (
            "Audience that rejects the product premise or value claim."
        ),
        "why_relevant": (
            "Rejector voice is required for any product simulation; "
            "without it the simulation only models receptive segments."
        ),
        "likely_pains": [
            "perceived rip-off",
            "unclear value",
        ],
        "likely_objections": [
            "not worth the price",
            "I don't trust the claim",
        ],
        "likely_current_alternatives": [
            "do nothing", "existing alternative",
        ],
        "evidence_needed": [
            "rejection thread",
            "outrage review",
        ],
        "source_query_themes": [
            "ridiculous price product review",
            "scam / rip-off complaint",
        ],
        "inclusion_signals": [
            "explicit rejection language",
        ],
        "exclusion_signals": [
            "buyer framing",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
    {
        "category_key": "competitor_or_alternative_user",
        "display_name": "Competitor / alternative user",
        "description": (
            "Audience currently using a competing product or alternative."
        ),
        "why_relevant": (
            "Switching-cost and competitor-relative-strength testing."
        ),
        "likely_pains": [
            "limitation of current alternative",
        ],
        "likely_objections": [
            "switching cost",
        ],
        "likely_current_alternatives": [
            "named competitor",
        ],
        "evidence_needed": [
            "competitor comparison thread",
        ],
        "source_query_themes": [
            "<product> vs <competitor> review",
        ],
        "inclusion_signals": [
            "self-described competitor user",
        ],
        "exclusion_signals": [
            "no competitor signal",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
    {
        "category_key": "price_sensitive_buyer",
        "display_name": "Price-sensitive buyer",
        "description": (
            "Audience that weights price first and rejects above-tier "
            "pricing."
        ),
        "why_relevant": (
            "Pricing tests require an explicit price-sensitive voice."
        ),
        "likely_pains": [
            "too expensive",
        ],
        "likely_objections": [
            "not worth $X",
        ],
        "likely_current_alternatives": [
            "cheaper alternative", "do nothing",
        ],
        "evidence_needed": [
            "price-complaint thread",
        ],
        "source_query_themes": [
            "<product> too expensive",
        ],
        "inclusion_signals": [
            "explicit price language",
        ],
        "exclusion_signals": [
            "premium-tier framing",
        ],
        "minimum_persona_target_tiny": 1,
        "minimum_persona_target_small": 2,
        "minimum_persona_target_serious": 4,
        "priority": _MEDIUM,
    },
)


FAMILY_TEMPLATES: Final[dict[ProductFamily, tuple[dict, ...]]] = {
    ProductFamily.COMMERCE_PLATFORM_OR_TOOLING: COMMERCE_PLATFORM_TEMPLATE,
    ProductFamily.CONSUMER_PACKAGED_GOOD: CONSUMER_PACKAGED_GOOD_TEMPLATE,
    ProductFamily.CONSUMER_ELECTRONICS: CONSUMER_ELECTRONICS_TEMPLATE,
    ProductFamily.FINANCIAL_PRODUCT: FINANCIAL_PRODUCT_TEMPLATE,
    ProductFamily.B2B_SAAS: B2B_SAAS_TEMPLATE,
    ProductFamily.DEFAULT_GENERAL: DEFAULT_GENERAL_TEMPLATE,
}
