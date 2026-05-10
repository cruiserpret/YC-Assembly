"""Phase 8.2F.7 — relevance audit rubric.

Closed-enum scoring fields, classification thresholds, and the curated
keyword sets that drive the deterministic auditor.

Total score is the sum of nine 0–5 sub-scores (max 45). Classification:

  highly_relevant:  36–45
  relevant:         27–35
  weakly_relevant:  18–26
  not_relevant:      0–17

These thresholds are explicit (operators can adjust if justified).
"""
from __future__ import annotations

import enum
import re
from typing import Final


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class RelevanceClassification(str, enum.Enum):
    HIGHLY_RELEVANT = "highly_relevant"
    RELEVANT = "relevant"
    WEAKLY_RELEVANT = "weakly_relevant"
    NOT_RELEVANT = "not_relevant"


# Inclusive lower bounds. Each label runs from its bound up to the next
# label's bound minus one (the topmost runs to TOTAL_MAX).
CLASSIFICATION_THRESHOLDS: Final[dict[RelevanceClassification, int]] = {
    RelevanceClassification.NOT_RELEVANT: 0,
    RelevanceClassification.WEAKLY_RELEVANT: 18,
    RelevanceClassification.RELEVANT: 27,
    RelevanceClassification.HIGHLY_RELEVANT: 36,
}


SCORE_MAX_PER_FIELD: Final[int] = 5


SCORE_FIELDS: Final[tuple[str, ...]] = (
    "role_context_score",
    "pain_point_score",
    "current_alternative_score",
    "price_budget_score",
    "trust_objection_score",
    "source_strength_score",
    "human_signal_score",
    "viewpoint_diversity_score",
    "simulation_usefulness_score",
)


TOTAL_MAX: Final[int] = SCORE_MAX_PER_FIELD * len(SCORE_FIELDS)


def classify_total_score(total: int) -> RelevanceClassification:
    """Map a total score in [0, TOTAL_MAX] to a classification."""
    if total < 0 or total > TOTAL_MAX:
        raise ValueError(
            f"total score {total} out of bounds [0, {TOTAL_MAX}]"
        )
    if total >= CLASSIFICATION_THRESHOLDS[RelevanceClassification.HIGHLY_RELEVANT]:
        return RelevanceClassification.HIGHLY_RELEVANT
    if total >= CLASSIFICATION_THRESHOLDS[RelevanceClassification.RELEVANT]:
        return RelevanceClassification.RELEVANT
    if total >= CLASSIFICATION_THRESHOLDS[RelevanceClassification.WEAKLY_RELEVANT]:
        return RelevanceClassification.WEAKLY_RELEVANT
    return RelevanceClassification.NOT_RELEVANT


# ---------------------------------------------------------------------------
# Target-context keyword sets — curated for an Amboras-style simulation.
# Lower-cased; matched as word boundaries.
# ---------------------------------------------------------------------------


def _word_re(*tokens: str) -> re.Pattern[str]:
    """Compile a case-insensitive word-boundary alternation."""
    escaped = [re.escape(t) for t in tokens]
    return re.compile(
        r"(?<![A-Za-z0-9])(?:" + "|".join(escaped) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


# 1) Role / context
ROLE_KEYWORDS_RE: Final[re.Pattern[str]] = _word_re(
    "shopify merchant", "shopify store owner", "store owner", "merchant",
    "founder", "dtc", "ecommerce", "e-commerce", "operator",
    "small business owner", "small business", "smb", "online store",
    "online seller", "seller", "entrepreneur", "shop owner",
    "agency client", "shopkeeper", "store admin", "site owner",
    "ecommerce entrepreneur",
)

# 2) Pain points (plugin/app/agency/brand/automation/AI)
PAIN_KEYWORDS_RE: Final[re.Pattern[str]] = _word_re(
    "plugin bloat", "plugin fatigue", "too many apps", "too many plugins",
    "plugin stack", "app sprawl", "app fatigue", "plugin overload",
    "agency cost", "agency fee", "agency pricing", "agency retainer",
    "expensive agency", "agencies", "freelancer cost",
    "brand control", "brand identity", "brand consistency",
    "store setup", "store build", "build a store", "store build friction",
    "trust", "trust issues", "automation skepticism", "ai skepticism",
    "skeptical of ai", "lock-in", "lock in", "vendor lock-in",
    "switching cost", "automation",
    "redesigns", "redesign cost",
    "plugins broken", "plugin broke", "broke my checkout",
    "expensive", "overpriced", "burned by", "fed up", "frustrated",
)

# 3) Current alternatives
ALTERNATIVE_KEYWORDS_RE: Final[re.Pattern[str]] = _word_re(
    "shopify", "shopify magic", "shopify apps", "shopify app",
    "wordpress", "woocommerce", "bigcommerce", "wix", "squarespace",
    "magento", "etsy", "amazon",
    "klaviyo", "oberlo", "mailchimp", "stripe",
    "agency", "agencies", "freelancer", "freelancers",
    "custom theme", "custom themes", "custom site",
    "off-the-shelf theme", "marketplace theme",
    "contractor", "in-house team",
)

# 4) Price / budget
PRICE_KEYWORDS_RE: Final[re.Pattern[str]] = _word_re(
    "price", "pricing", "expensive", "cheap", "cost", "costly",
    "afford", "affordable", "budget", "monthly fee", "annual fee",
    "subscription", "tier", "starter tier", "plan", "month", "/mo",
    "$", "dollars", "fee", "fees",
    "willing to pay", "willingness to pay", "pay for",
    "saving money", "save money", "value for money",
    "free", "freemium", "trial", "discount",
)

# 5) Trust / objection
TRUST_OBJECTION_KEYWORDS_RE: Final[re.Pattern[str]] = _word_re(
    "trust", "trustworthy", "credibility", "guarantee",
    "skeptical", "skepticism", "doubt", "worried", "worry", "concerned",
    "concern", "objection", "object",
    "control", "lose control", "retain control", "final control",
    "transparency", "transparent",
    "proof", "evidence", "case study", "testimonial",
    "lock-in", "lock in", "switch back",
    "data privacy", "privacy",
    "broken", "ruined", "damaged",
)

# 6) Source-strength scoring uses counts of direct/inferred traits +
# evidence_links — no keyword list needed.

# 7) Human-signal score — uses metadata.likely_human_signal_candidate.

# 8) Viewpoint-diversity — fingerprint-based; computed at audit time.

# 9) Simulation usefulness — composite predicate.


# ---------------------------------------------------------------------------
# Stakeholder categories — for missing-segment detection.
# Each category is defined by a predicate over a persona's normalized
# trait values. The auditor checks every persona against every category;
# categories with zero matching personas are flagged as "missing".
# ---------------------------------------------------------------------------


@enum.unique
class StakeholderCategory(str, enum.Enum):
    SHOPIFY_MERCHANT_PLUGIN_FATIGUE = "shopify_merchant_plugin_fatigue"
    DTC_FOUNDER_BRAND_CONTROL = "dtc_founder_brand_control"
    AGENCY_DEPENDENT_MERCHANT = "agency_dependent_merchant"
    AI_SKEPTICAL_OPERATOR = "ai_skeptical_operator"
    PREMIUM_CUSTOM_BUYER = "premium_custom_buyer"
    PRICE_SENSITIVE_SMB = "price_sensitive_smb"
    NONTECHNICAL_FOUNDER = "nontechnical_founder"
    FREELANCER_USING_MERCHANT = "freelancer_using_merchant"
    APP_HEAVY_USER = "app_heavy_user"
    LOCK_IN_WORRIED_OPERATOR = "lock_in_worried_operator"


STAKEHOLDER_CATEGORIES: Final[tuple[StakeholderCategory, ...]] = tuple(
    StakeholderCategory
)


# Per-category required predicates. Keyed by the role + pain that the
# category requires. The auditor matches a persona to a category iff
# the persona's source-backed trait values contain the listed
# substrings (case-insensitive).
STAKEHOLDER_REQUIREMENTS: Final[dict[StakeholderCategory, dict[str, tuple[str, ...]]]] = {
    StakeholderCategory.SHOPIFY_MERCHANT_PLUGIN_FATIGUE: {
        "role_keywords": ("shopify", "merchant", "store owner"),
        "pain_keywords": ("plugin", "app", "too many"),
    },
    StakeholderCategory.DTC_FOUNDER_BRAND_CONTROL: {
        "role_keywords": ("dtc", "founder"),
        "pain_keywords": ("brand", "control", "identity"),
    },
    StakeholderCategory.AGENCY_DEPENDENT_MERCHANT: {
        "role_keywords": (
            "merchant", "store owner", "founder", "shop", "site owner",
        ),
        "pain_keywords": ("agency", "agencies", "freelancer", "boutique"),
    },
    StakeholderCategory.AI_SKEPTICAL_OPERATOR: {
        "role_keywords": (
            "merchant", "operator", "founder", "store owner", "shop",
        ),
        "pain_keywords": (
            "ai skepticism", "skeptical of ai", "ai", "automation",
        ),
    },
    StakeholderCategory.PREMIUM_CUSTOM_BUYER: {
        "role_keywords": ("merchant", "owner", "founder", "shop"),
        "pain_keywords": (
            "custom theme", "custom site", "custom build", "premium",
            "agency", "boutique",
        ),
    },
    StakeholderCategory.PRICE_SENSITIVE_SMB: {
        "role_keywords": ("small business", "smb", "merchant", "shop"),
        "pain_keywords": (
            "price", "expensive", "cost", "afford", "budget", "fee",
        ),
    },
    StakeholderCategory.NONTECHNICAL_FOUNDER: {
        "role_keywords": ("founder", "owner", "entrepreneur"),
        "pain_keywords": (
            "nontechnical", "non-technical", "no developer", "no dev",
            "set up", "setup", "code", "technical",
        ),
    },
    StakeholderCategory.FREELANCER_USING_MERCHANT: {
        "role_keywords": ("merchant", "owner", "founder"),
        "pain_keywords": ("freelancer", "freelance", "contractor"),
    },
    StakeholderCategory.APP_HEAVY_USER: {
        "role_keywords": ("merchant", "store owner", "shop"),
        "pain_keywords": (
            "many apps", "too many plugins", "app stack", "plugin stack",
            "klaviyo", "oberlo", "shopify apps",
        ),
    },
    StakeholderCategory.LOCK_IN_WORRIED_OPERATOR: {
        "role_keywords": ("merchant", "founder", "operator"),
        "pain_keywords": ("lock-in", "lock in", "vendor", "switch back"),
    },
}
