"""Phase 9A.1 — universal signal lexicons.

Lexicons are universal across products — they capture buyer-state
*shapes*, not category labels. Adding a new product domain does not
require lexicon changes.
"""
from __future__ import annotations

from typing import Final


# Each lexicon entry: (signal_type, inferred_role, keywords)
UNIVERSAL_SIGNAL_LEXICONS: Final[
    tuple[tuple[str, str, tuple[str, ...]], ...]
] = (
    (
        "price_value_signal", "price_skeptic",
        (
            "expensive", "overpriced", "cost", "price", "value",
            "cheap", "worth the money", "pricey", "pricier",
            "cheaper", "discount", "deal", "$", "ounce", "ounces",
            "cost-per", "per use", "per ounce",
        ),
    ),
    (
        "trust_proof_signal", "trust_seeker",
        (
            "test", "tested", "review", "reviews", "rating", "ratings",
            "verified", "proof", "data", "study", "miles", "hours",
            "evidence", "research", "tested by", "third-party",
            "lab tested",
        ),
    ),
    (
        "safety_visibility_signal", "safety_visibility_focused_buyer",
        (
            "safety", "safe", "dangerous", "visibility", "visible",
            "see me", "be seen", "reflective", "reflect",
            "high-vis", "high vis", "lit up", "bright",
            "brightness", "illumination", "bright light",
            "spotted by drivers", "after dark", "at night",
            "low light",
        ),
    ),
    (
        "format_preference_signal", "format_focused_buyer",
        (
            "size", "format", "compact", "pocket", "bulky",
            "lightweight", "heavy", "comfortable", "uncomfortable",
            "fit", "strap", "snap-on", "clip", "attachment",
            "form factor", "design", "small enough",
            "easy to carry", "easy to wear",
        ),
    ),
    (
        "convenience_signal", "convenience_focused_buyer",
        (
            "easy", "convenient", "hassle", "annoying", "fast",
            "quick", "charge", "charging", "battery", "rechargeable",
            "usb", "usb-c", "easy to charge", "long battery",
            "low maintenance",
        ),
    ),
    (
        "performance_signal", "performance_focused_buyer",
        (
            "performance", "powerful", "strong", "weak", "lasts",
            "lasted", "duration", "endurance", "long run",
            "long runs", "long ride", "training", "race",
            "miles per",
        ),
    ),
    (
        "objection_signal", "objection_focused_buyer",
        (
            "doesn't work", "didn't work", "wouldn't recommend",
            "not worth", "disappointed", "annoying", "broke",
            "failed", "fell off", "fell apart", "stopped working",
            "complaint", "problem", "issue", "concern",
            "would not buy",
        ),
    ),
    (
        "use_case_signal", "use_case_focused_buyer",
        (
            "running", "runner", "runners", "ran", "jog", "jogging",
            "cycling", "cyclist", "cyclists", "biking", "bike",
            "ride", "ridden", "walking", "walker", "walkers",
            "walk", "hike", "hiker", "hikers", "hiking",
            "commute", "commuter", "commuting", "commuted",
            "school", "campus", "college student", "students",
            "teen", "teens", "teenager", "kid", "kids",
            "dog", "dogs", "leash",
        ),
    ),
)
