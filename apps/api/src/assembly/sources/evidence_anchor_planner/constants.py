"""Phase 8.5B.1 — UNIVERSAL lexicons for the dynamic anchor planner.

These three lexicons are product-agnostic. They describe the way the
ENGLISH LANGUAGE behaves around product reviews, not the way any
specific product category behaves. A new product brief in any
domain (food, tech, fashion, fitness, beauty) reuses these as-is.

Anything product- or category-specific is inferred per-brief by
`planner.generate_anchor_plan`.
"""
from __future__ import annotations


# Universal stopwords — function words and review-platform filler
# that should NEVER count as positive signal on their own. Used to
# filter token lists during anchor extraction.
UNIVERSAL_STOPWORDS: frozenset[str] = frozenset({
    # Articles / determiners / pronouns
    "a", "an", "the", "this", "that", "these", "those",
    "i", "you", "we", "they", "he", "she", "it", "my", "our",
    "your", "their", "his", "her", "its",
    # Auxiliaries / be / have / do
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did", "doing",
    "can", "could", "should", "would", "may", "might", "must",
    "will", "shall",
    # Common prepositions / conjunctions
    "and", "or", "but", "so", "if", "as", "of", "to", "in", "on",
    "at", "by", "for", "with", "without", "from", "into", "onto",
    "than", "then", "when", "while", "where", "until", "before",
    "after", "during", "about", "between", "through",
    # Quantifiers / qualifiers (these are GENERIC modifiers, see below)
    "very", "really", "much", "many", "more", "most", "less", "least",
    "some", "any", "all", "each", "every", "both", "few", "several",
    # Discourse / emphasis filler
    "actually", "honestly", "literally", "definitely", "kinda",
    "sorta", "etc", "stuff", "things", "thing", "way", "ways",
    "lot", "lots", "tons", "bunch",
    # Review-platform filler
    "review", "reviews", "purchase", "purchased", "bought",
    "received", "received", "five", "stars", "star", "rating",
    "ratings",
    # Time
    "now", "today", "tomorrow", "yesterday", "soon", "later",
})


# Universal generic-modifier list. These terms describe a quality
# attribute — they do NOT identify a product or category. They are
# only useful when COMBINED with a brief-derived product/category
# anchor. The planner attaches this list verbatim to every plan.
UNIVERSAL_GENERIC_MODIFIERS: tuple[str, ...] = (
    # Sensory
    "taste", "tastes", "tasty", "flavor", "flavour", "flavors",
    "texture", "smell", "smells", "smelly", "scent", "scented",
    "color", "colour", "colorful",
    # Sweetness / bitterness (sensory but often product-category-relevant
    # — still treated as GENERIC unless a category anchor co-occurs)
    "sweet", "sweetness", "bitter", "bitterness", "sour", "salty",
    # Price / value
    "price", "priced", "pricey", "expensive", "cheap", "cheaper",
    "value", "worth", "affordable", "overpriced", "bargain", "deal",
    # Quality / functional
    "quality", "good", "great", "excellent", "amazing", "awesome",
    "bad", "terrible", "horrible", "awful", "poor",
    "love", "loved", "hate", "hated", "like", "liked", "disliked",
    # Convenience / form factor
    "convenient", "easy", "simple", "quick", "fast", "slow",
    "hard", "difficult", "comfortable", "uncomfortable",
    "portable", "compact", "lightweight", "heavy", "big", "small",
    "durable", "sturdy", "flimsy",
    # Generic positivity / negativity
    "perfect", "useful", "useless", "nice", "pretty", "ugly",
    "fine", "okay", "ok", "decent", "solid",
    # Fit / size (apparel-shape modifiers — generic across products)
    "fits", "fit", "sizing", "size", "small", "medium", "large",
)


# Universal ambiguity-context lexicon. When a competitor brand name
# is short / common / polysemous (e.g., "Prime", "Coach", "Apple",
# "Visa", "Reign", "Bang"), reviews mentioning the competitor often
# refer to a DIFFERENT real-world thing (Amazon Prime shipping,
# Apple Inc., the Coach handbag brand vs Coach as a sports figure,
# etc.). This lexicon maps every common cross-domain SENSE to
# discriminating phrases that signal "this is NOT the product
# category". The planner combines these with each ambiguous
# competitor at runtime to produce wrong-sense detection rules.
#
# These are GENRES of cross-domain confusion, not product-specific.
# Adding a new product (sunscreen, knife, software) reuses the
# same lexicon as-is.
UNIVERSAL_AMBIGUITY_CONTEXTS: dict[str, tuple[str, ...]] = {
    "shipping_commerce": (
        "amazon prime", "prime shipping", "prime delivery",
        "prime membership", "prime member", "prime eligible",
        "prime day", "prime two-day", "prime 2-day",
        "i have prime", "with prime", "got prime", "free prime",
        "use prime", "package arrived", "fast shipping",
        "arrived quickly", "shipping was fast", "shipping is fast",
        "fulfilled by amazon", "sold by amazon",
    ),
    "streaming_video": (
        "prime video", "amazon prime video", "watch on prime",
        "stream on prime", "available on prime", "showing on prime",
        "netflix", "hulu", "disney plus", "youtube tv",
    ),
    "tech_company_apple": (
        "iphone", "ipad", "macbook", "imac", "apple watch",
        "apple tv", "ios ", " ios,", "macos", "app store",
        "siri ", "icloud",
    ),
    "computing_microsoft": (
        "microsoft", "windows 10", "windows 11", "xbox", "surface pro",
        "office 365", "outlook", "powerpoint",
    ),
    "automobile": (
        "horsepower", "transmission", "odometer", "dealership",
        "test drive", "fuel economy", "miles per gallon",
        "engine block", "the dealer",
    ),
    "fashion_handbag_coach": (
        "handbag", "purse", "tote bag", "leather bag", "designer bag",
    ),
    "memberships_subscriptions": (
        "membership", "subscription", "auto-renew", "renewal",
        "monthly fee", "annual fee", "cancel anytime",
    ),
    "time_idioms": (
        "prime time", "in his prime", "in her prime", "in their prime",
        "past their prime", "his prime years",
    ),
    "math_idioms": (
        "prime number", "prime factor", "prime factorization",
    ),
    # Common payment-card / financial idioms (Visa, Discover, Capital One)
    "financial_card": (
        "credit card", "debit card", "visa card", "mastercard",
        "rewards card", "card declined",
    ),
    # Scientific / measurement units that collide with brand names
    # (e.g., "Celsius" as a temperature unit vs Celsius the energy
    # drink brand; "Watt" as a unit vs a brand). Universal — applies
    # to ANY product whose competitor list contains a word that's
    # also a unit name.
    "scientific_units": (
        "degrees celsius", "degrees fahrenheit", "degrees kelvin",
        "celsius temperature", "celsius scale", "in celsius",
        "below celsius", "above celsius",
        "watts ", "kilowatt", "kelvin",
    ),
}


# A small list of "potentially ambiguous" indicators. The planner
# uses these to flag a competitor token as a candidate for ambiguity
# scrutiny. Triggering this flag does NOT alone mark the entity
# ambiguous — the planner also checks for matches in the universal
# ambiguity lexicon.
SHORT_NAME_AMBIGUITY_THRESHOLD = 6  # competitor name ≤ this is suspect
