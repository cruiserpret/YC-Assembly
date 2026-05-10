"""Phase 8.2G — fixture briefs that exercise the four product families.

Used by the operator script + the planner-examples test to prove the
planner produces DIFFERENT stakeholder category sets for different
products (i.e. is not Amboras-only).
"""
from __future__ import annotations

from assembly.pipeline.target_society.constants import SimulationGoal
from assembly.pipeline.target_society.schemas import ProductBriefInput


AMBORAS_BRIEF: ProductBriefInput = ProductBriefInput(
    product_name="Amboras",
    product_type="ai_commerce_platform",
    product_description=(
        "Amboras is an AI commerce platform that builds and operates "
        "Shopify stores autonomously for merchants who do not want to "
        "manage plugins or hire agencies. Founders worry the AI will "
        "damage brand identity. Merchants would switch if they saw "
        "proof that they retain final control over branding and pricing."
    ),
    price_or_price_structure="$49/mo starter; performance tier later",
    competitors=["Shopify Magic", "Conversion AI Tool"],
    target_market_or_society=(
        "Shopify merchants doing $10k-$80k/month, frustrated with plugin "
        "bloat and overwhelmed by managing apps."
    ),
    geography="US/Canada",
    intended_user_or_buyer="Shopify merchant / founder",
    optional_url=None,
    extra_context="Founders worry about brand control and trust.",
    simulation_goal=SimulationGoal.TEST_TRUST_OBJECTION_BARRIERS,
)


WATER_BOTTLE_BRIEF: ProductBriefInput = ProductBriefInput(
    product_name="Aurelia Premium Water",
    product_type="bottled water",
    product_description=(
        "Aurelia is a $10 premium bottled water sold in California. "
        "Sourced from a single Sierra Nevada spring, sold in glass "
        "bottles. Targets premium-tier grocery shoppers, fitness "
        "buyers, and sustainability-conscious consumers; explicitly "
        "tests whether $10 is defensible against Aquafina, Dasani, "
        "Fiji, and tap water."
    ),
    price_or_price_structure="$10 per 500ml glass bottle",
    competitors=["Aquafina", "Dasani", "Fiji", "Liquid Death"],
    target_market_or_society=(
        "California premium-tier grocery shoppers and fitness consumers"
    ),
    geography="California",
    intended_user_or_buyer="premium-tier grocery shopper",
    optional_url=None,
    extra_context=(
        "Includes voices that explicitly reject $10 bottled water as "
        "ridiculous; sustainability-conscious buyers concerned about "
        "single-use plastic."
    ),
    simulation_goal=SimulationGoal.TEST_PRICE,
)


IPHONE_17_BRIEF: ProductBriefInput = ProductBriefInput(
    product_name="iPhone 17",
    product_type="smartphone",
    product_description=(
        "iPhone 17 — annual flagship smartphone refresh. New on-device "
        "AI camera and summarization features; premium pricing tier. "
        "Tests upgrade-cycle adoption among current iPhone owners, "
        "switching from Samsung / Android, and AI-feature skepticism."
    ),
    price_or_price_structure="$1,099 base / $1,599 Pro Max",
    competitors=["Samsung Galaxy S25", "Google Pixel 10"],
    target_market_or_society=(
        "current iPhone users, Samsung / Android switchers, tech "
        "enthusiasts, and price-sensitive smartphone buyers"
    ),
    geography=None,
    intended_user_or_buyer="smartphone buyer",
    optional_url=None,
    extra_context=(
        "Specific test concerns: AI feature skepticism, upgrade fatigue, "
        "carrier-tied upgrade pricing."
    ),
    simulation_goal=SimulationGoal.TEST_PRODUCT_CONCEPT,
)


HALAL_FINANCING_BRIEF: ProductBriefInput = ProductBriefInput(
    product_name="HalalHome",
    product_type="halal home financing",
    product_description=(
        "HalalHome is a Shariah-compliant home financing product for "
        "homebuyers seeking halal financing options in the US. "
        "Structured as ijara (lease-to-own) and murabaha (cost-plus). "
        "Targets compliance-conscious homebuyers and conventional-loan "
        "switchers; addresses skepticism about whether such products "
        "are actually compliant and economically competitive vs "
        "conventional mortgages."
    ),
    price_or_price_structure="rate equivalent to ~6.5% APR conventional",
    competitors=[
        "Guidance Residential",
        "UIF Corp",
        "Conventional 30-year mortgage",
    ],
    target_market_or_society=(
        "compliance-conscious homebuyers and real-estate investors "
        "seeking halal-compliant financing"
    ),
    geography="United States",
    intended_user_or_buyer="halal-financing-seeking homebuyer",
    optional_url=None,
    extra_context=(
        "Sensitive religious targeting; the planner must keep individual "
        "personas broad and never infer religion or regulatory eligibility "
        "for a specific persona."
    ),
    simulation_goal=SimulationGoal.TEST_TRUST_OBJECTION_BARRIERS,
)


ALL_EXAMPLES: tuple[tuple[str, ProductBriefInput], ...] = (
    ("amboras", AMBORAS_BRIEF),
    ("water_bottle_california", WATER_BOTTLE_BRIEF),
    ("iphone_17", IPHONE_17_BRIEF),
    ("halal_financing", HALAL_FINANCING_BRIEF),
)
