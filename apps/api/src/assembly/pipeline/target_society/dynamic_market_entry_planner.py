"""Phase 8.4A.2 — dynamic market-entry society planner.

Generates `StakeholderCategory` rows from generic primitives, NOT from
product-family-specific templates. The same primitive set works for
energy drinks, sunscreen, bottled water, Shopify tools, halal
financing, restaurants, or any product where direct product evidence
is thin and the market is defined by competitor / substitute /
use-case / objection / buyer-type / geography.

Primitives (Section: ARCHITECTURAL REQUIREMENT in the operator spec):
  1. Competitor users   — one category per `brief.competitors[]`
  2. Substitute users   — parsed from product_description / extra_context
  3. Use-case groups    — parsed from intended_user_or_buyer / target_market
  4. Objection groups   — universal: price / taste / health / brand / rejector
  5. Buyer-type groups  — universal: loyalist / switcher / impulse / premium
  6. Geography group    — only when brief.geography is set; SOFT modifier

This module is deterministic, never calls an LLM, never calls the
network, never writes to the DB. Drift tests enforce.
"""
from __future__ import annotations

import re
from typing import Final

from assembly.pipeline.target_society.constants import SimulationGoal
from assembly.pipeline.target_society.schemas import (
    ProductBriefInput,
    StakeholderCategory,
)


# ---------------------------------------------------------------------------
# Auto-detection: is this a market-entry brief?
# ---------------------------------------------------------------------------


_MARKET_ENTRY_TEXT_HINTS: tuple[str, ...] = (
    "unlaunched",
    "not yet launched",
    "would launch",
    "going to launch",
    "new product",
    "new brand",
    "novel product",
    "no direct product",
    "no triton",  # generic: "no <product-name>" pattern caller may put in
    "pre-launch",
    "before launch",
    "launch testing",
    "market-entry",
    "market entry",
)


def looks_like_market_entry_brief(brief: ProductBriefInput) -> bool:
    """Heuristic: True if the brief looks like an unlaunched / market-
    entry product test. Triggers when EITHER of:
      * `simulation_goal == TEST_MARKET_ENTRY` (explicit), OR
      * any of the brief's free-text fields contains a market-entry
        hint phrase ('unlaunched', 'pre-launch', 'new brand', etc.).

    Returns False for explicit launched-product simulation goals
    (TEST_TRUST_OBJECTION_BARRIERS — the Amboras pattern, where direct
    product evidence is the basis).

    Phase 8.4A.2 — the original draft also auto-triggered on
    `(competitors + TEST_PRICE)` but that heuristic was too noisy: it
    captured several existing launched-product test fixtures (water
    bottle, sunscreen-when-launched) that should stay on the classic
    template path. Auto-detection now requires explicit signal.
    """
    if brief.simulation_goal == SimulationGoal.TEST_MARKET_ENTRY:
        return True
    if brief.simulation_goal == SimulationGoal.TEST_TRUST_OBJECTION_BARRIERS:
        return False

    text_blob = " ".join(
        s for s in (
            brief.product_description,
            brief.target_market_or_society or "",
            brief.extra_context or "",
        )
    ).lower()
    if any(hint in text_blob for hint in _MARKET_ENTRY_TEXT_HINTS):
        return True
    return False


# ---------------------------------------------------------------------------
# Slugification + brief parsing
# ---------------------------------------------------------------------------


def _slugify(text: str, *, max_chars: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:max_chars] or "unknown"


# `\b` word-boundaries on the trigger keyword prevent partial-word
# matches like "es include" landing inside the middle of "substitutes
# considered". Capture group runs until the next sentence terminator.
_SUBSTITUTE_TRIGGER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:substitutes?|alternatives?|"
    r"overlaps?\s+with|share(?:s)?\s+of\s+occasion)\b"
    r"[\s:]*(?:include|including|considered\s+in\s+scope|with|are)?"
    r"[\s:]*([A-Za-z][A-Za-z0-9,\-\s/]+?)(?:\.\s|\.$|$|;)",
    re.IGNORECASE,
)
_USE_CASE_TRIGGER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:targeted\s+at|target\s+users?|intended\s+users?|"
    r"intended\s+buyers?|target\s+market)\b"
    r"[\s:]*(?:include|of|are|is)?"
    r"[\s:]*([A-Za-z][A-Za-z0-9,\-\s/]+?)(?:\.\s|\.$|$|;)",
    re.IGNORECASE,
)


def _split_list(text: str) -> list[str]:
    """Split a free-text list like 'Red Bull, Monster, Celsius and Prime'
    into ['Red Bull', 'Monster', 'Celsius', 'Prime']."""
    if not text:
        return []
    parts = re.split(r",|;|\band\b|/", text, flags=re.IGNORECASE)
    out: list[str] = []
    for p in parts:
        cleaned = re.sub(r"\s+", " ", p).strip(" .,;").strip()
        # Drop very-generic single words that aren't real items
        if (
            cleaned
            and len(cleaned) >= 3
            and cleaned.lower() not in {
                "etc", "etc.", "more", "others", "items",
                "who", "what", "when", "where", "which",
            }
        ):
            out.append(cleaned)
    # De-dup, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        k = item.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(item)
    return deduped


def parse_substitutes_from_brief(brief: ProductBriefInput) -> list[str]:
    """Extract substitute product names from the brief's free-text
    fields. Looks for trigger phrases ('substitutes include', 'overlap
    with', 'alternatives include') and pulls a comma-separated list.

    Returns an empty list when the brief has no recognizable substitute
    section — in that case, the dynamic planner falls back to general
    category use-case categories without per-substitute ones.
    """
    blob = " ".join(
        s for s in (
            brief.product_description,
            brief.extra_context or "",
            brief.target_market_or_society or "",
        ) if s
    )
    found: list[str] = []
    for m in _SUBSTITUTE_TRIGGER_RE.finditer(blob):
        chunk = m.group(1)
        for item in _split_list(chunk):
            if (
                item.lower() not in (c.lower() for c in brief.competitors)
                and item not in found
            ):
                found.append(item)
    # Cap to a sane number to avoid runaway category counts.
    return found[:8]


# A use-case phrase qualifies only if it carries a role-shaped noun
# (someone the product targets), not a generic occasion/category word.
_USE_CASE_ROLE_HINT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:user|users|buyer|buyers|consumer|consumers|drinker|drinkers|"
    r"shopper|shoppers|customer|customers|enthusiast|enthusiasts|"
    r"student|students|founder|founders|operator|operators|"
    r"merchant|merchants|owner|owners|seller|sellers|"
    r"athlete|athletes|gym-?goer|gym-?goers|"
    r"adult|adults|parent|parents|professional|professionals|"
    r"swimmer|swimmers|beachgoer|beachgoers|runner|runners|"
    r"client|clients|skeptic|skeptics|rejector|rejectors)\b",
    re.IGNORECASE,
)


def parse_use_cases_from_brief(brief: ProductBriefInput) -> list[str]:
    """Extract use-case role phrases from `intended_user_or_buyer`.

    Only `intended_user_or_buyer` is used as a use-case source — it is
    the structured "who is this for" field and reliably contains
    comma-separated buyer roles. `target_market_or_society` carries
    occasion / market language that's too noisy for a role parser
    (e.g. 'California consumers in the energy / sports / functional-
    beverage occasion' would produce 'sports' and 'occasion' as false
    use-cases).

    A phrase is accepted only if it contains a role-shaped noun
    (student / athlete / buyer / consumer / etc.).
    """
    found: list[str] = []
    src = brief.intended_user_or_buyer
    if not src:
        return found

    for item in _split_list(src):
        if (
            item not in found
            and len(item.split()) <= 5
            and _USE_CASE_ROLE_HINT_RE.search(item)
        ):
            found.append(item)
    # Trigger-phrase supplement
    for m in _USE_CASE_TRIGGER_RE.finditer(src):
        chunk = m.group(1)
        for item in _split_list(chunk):
            if (
                item not in found
                and len(item.split()) <= 5
                and _USE_CASE_ROLE_HINT_RE.search(item)
            ):
                found.append(item)
    return found[:8]


# ---------------------------------------------------------------------------
# Universal objection + buyer-type primitives
# ---------------------------------------------------------------------------


# Objection categories are universal across product families. Their
# inclusion_signals are domain-agnostic objection language. The scorer
# matches against persona's `objection_patterns` + `buying_constraints`
# + `interests` traits.
_UNIVERSAL_OBJECTION_PRIMITIVES: Final[tuple[dict, ...]] = (
    {
        "key_suffix": "price_sensitive",
        "display": "Price-sensitive buyer",
        "pains": [
            "too expensive", "overpriced", "cheaper alternative",
            "on a budget", "broke", "premium price",
        ],
        "objections": [
            "not worth the price", "I can find cheaper",
            "private label / generic alternative",
        ],
        "evidence_needed": [
            "explicit price complaint", "cheaper-alternative comparison",
            "budget-mention",
        ],
        "inclusion_signals": [
            "expensive", "too expensive", "cheaper", "afford",
            "budget", "broke", "premium price", "overpriced",
            "value for money",
        ],
        "exclusion_signals": ["brand marketing voice"],
        "priority": "medium",
    },
    {
        "key_suffix": "taste_or_quality_skeptic",
        "display": "Taste / quality skeptic",
        "pains": [
            "bad taste", "gross flavor", "aftertaste",
            "low-quality", "artificial",
        ],
        "objections": [
            "tastes terrible", "weird aftertaste", "chemical taste",
            "low quality compared to alternatives",
        ],
        "evidence_needed": [
            "explicit taste complaint", "quality complaint",
        ],
        "inclusion_signals": [
            "taste", "flavor", "gross", "disgusting", "bitter",
            "sweet", "sickly", "chemical", "aftertaste", "bland",
            "high-quality", "low-quality",
        ],
        "exclusion_signals": ["brand promo voice"],
        "priority": "medium",
    },
    {
        "key_suffix": "health_or_safety_skeptic",
        "display": "Health / safety skeptic",
        "pains": [
            "unhealthy ingredients", "too much sugar",
            "too much caffeine", "questionable safety",
        ],
        "objections": [
            "bad for you", "unhealthy", "ingredients I don't trust",
            "doesn't meet my dietary requirement",
        ],
        "evidence_needed": [
            "health-skepticism statement",
            "ingredient-concern statement",
        ],
        "inclusion_signals": [
            "unhealthy", "bad for you", "too much caffeine",
            "too much sugar", "natural", "clean ingredient",
            "low sugar", "zero sugar", "sugar free", "additives",
            "preservatives", "artificial",
        ],
        "exclusion_signals": ["nutritionist marketing voice"],
        "priority": "medium",
    },
    {
        "key_suffix": "brand_loyalist_or_skeptic",
        "display": "Brand loyalist / new-brand skeptic",
        "pains": [
            "trust issues with new brands",
            "loyalty inertia",
        ],
        "objections": [
            "I stick with my current brand",
            "never tried that brand",
            "skeptical of new entrants",
            "overrated", "hype",
        ],
        "evidence_needed": [
            "explicit brand-loyalty statement",
            "skepticism toward new entrant",
        ],
        "inclusion_signals": [
            "loyal to", "stick with", "stick to", "new brand",
            "never tried", "would never", "would not switch",
            "skeptical", "hype", "overrated", "gimmick",
        ],
        "exclusion_signals": ["paid affiliate voice"],
        "priority": "medium",
    },
    {
        "key_suffix": "category_rejector",
        "display": "Category rejector",
        "pains": [
            "won't buy this category at all",
            "category-level rejection",
        ],
        "objections": [
            "don't drink / use / buy this category",
            "stopped buying", "gave up", "quit",
        ],
        "evidence_needed": [
            "explicit category-rejection statement",
        ],
        "inclusion_signals": [
            "don't drink", "don't use", "won't drink", "won't use",
            "stopped drinking", "stopped using", "never drink",
            "never use", "won't touch", "gave up", "quit",
        ],
        "exclusion_signals": [],
        "priority": "medium",
    },
)


# Buyer-type categories are universal. Inclusion signals match generic
# buyer-shape language regardless of category.
_UNIVERSAL_BUYER_TYPE_PRIMITIVES: Final[tuple[dict, ...]] = (
    {
        "key_suffix": "switcher",
        "display": "Switcher / openness to alternatives",
        "pains": ["dissatisfaction with current", "looking for change"],
        "objections": ["why should I switch"],
        "evidence_needed": [
            "explicit switching statement", "openness to alternatives",
        ],
        "inclusion_signals": [
            "switched", "switching", "thinking of switching",
            "considering", "trying out", "tried", "open to",
            "willing to try",
        ],
        "exclusion_signals": [],
        "priority": "medium",
    },
    {
        "key_suffix": "impulse_or_convenience_buyer",
        "display": "Impulse / convenience buyer",
        "pains": ["forgot to buy", "needed quickly"],
        "objections": ["if it's not on the shelf at convenience store"],
        "evidence_needed": [
            "convenience-channel mention",
            "impulse-purchase statement",
        ],
        "inclusion_signals": [
            "convenience store", "gas station", "7-eleven",
            "7 eleven", "circle k", "impulse", "grabbed",
            "quick", "on the way", "checkout",
        ],
        "exclusion_signals": [],
        "priority": "medium",
    },
    {
        "key_suffix": "premium_or_status_buyer",
        "display": "Premium / status-driven buyer",
        "pains": ["lack of brand story", "perceived inauthenticity"],
        "objections": ["is this actually premium"],
        "evidence_needed": [
            "premium-tier preference",
            "brand-story preference",
        ],
        "inclusion_signals": [
            "willing to pay more", "premium", "luxury", "high-end",
            "boutique", "artisanal", "specialty",
            "brand story", "authentic",
        ],
        "exclusion_signals": ["extreme price-sensitivity language"],
        "priority": "medium",
    },
)


# ---------------------------------------------------------------------------
# Per-primitive category builders
# ---------------------------------------------------------------------------


def _build_competitor_user_category(
    competitor: str, *, brief: ProductBriefInput,
) -> StakeholderCategory:
    """One category per brief.competitor[]. The competitor name itself
    is the load-bearing inclusion signal — e.g. 'Red Bull' surfaces in
    persona text as 'Red Bull', 'red bull', 'redbull'."""
    slug = _slugify(competitor)
    # Phase 8.4A.3: use the unified _expand_term_variants helper so
    # multi-word competitors ('Red Bull') get hyphen / no-separator
    # variants without per-builder duplication.
    name_variants = _expand_term_variants(competitor)
    return StakeholderCategory(
        category_key=f"competitor_user_{slug}",
        display_name=f"{competitor} user / loyalist",
        description=(
            f"Evidence-grounded current users, loyalists, or recent "
            f"trial-buyers of the {competitor} brand. Sourced from "
            f"category discussion / review / forum content where the "
            f"persona explicitly mentions {competitor} use, taste, "
            f"price, or experience."
        ),
        why_relevant=(
            f"For an unlaunched product entering the same market as "
            f"{competitor}, current {competitor} users are the most "
            f"directly comparable evidence-backed voices. Their "
            f"objections, drivers, and price thresholds anchor "
            f"market-entry simulation."
        ),
        likely_pains=[
            f"frustration with {competitor}",
            f"price of {competitor}",
            f"taste of {competitor}",
        ],
        likely_objections=[
            f"loyal to {competitor}",
            "wouldn't switch from current brand",
            "new entrants are unproven",
        ],
        likely_current_alternatives=[competitor],
        evidence_needed=[
            f"explicit {competitor} mention",
            f"{competitor} use experience",
            "category use evidence",
        ],
        source_query_themes=[
            f"{competitor} review", f"{competitor} user experience",
            f"{competitor} taste", f"{competitor} price",
        ],
        # Critical: include role-noun anchor phrases so the scorer's
        # _score_role_context (which looks for 'user', 'buyer',
        # 'consumer', etc.) can fire on persona text mentioning the
        # competitor in any form.
        inclusion_signals=[
            *name_variants,
            f"{competitor} user",
            f"{competitor} buyer",
            f"{competitor} consumer",
            f"{competitor} drinker",
        ],
        exclusion_signals=[
            "paid promotion", "marketing affiliate",
        ],
        minimum_persona_target_tiny=1,
        minimum_persona_target_small=3,
        minimum_persona_target_serious=8,
        priority="high",
    )


def _expand_term_variants(substitute: str) -> list[str]:
    """For a multi-word substitute like 'pre-workout powders', return
    variants the persona text might use:

      * full phrase ('pre-workout powders')
      * lowercase ('pre-workout powders')
      * hyphen↔space swap ('pre workout powders')
      * head word with hyphen ('pre-workout')
      * head word without separator ('preworkout')   ← Phase 8.4A.3
      * head word with space ('pre workout')         ← Phase 8.4A.3

    Generic: works for 'cold brew' → ['cold brew', 'coldbrew', 'cold-brew'],
    'electrolyte drinks' → adds 'electrolyte', etc.
    """
    base = substitute.strip()
    out: list[str] = [base, base.lower()]
    # Hyphen ↔ space variants of the full phrase.
    if " " in base:
        out.append(base.replace(" ", "-").lower())
        out.append(base.replace(" ", "").lower())  # no-separator
    if "-" in base:
        out.append(base.replace("-", " ").lower())
        out.append(base.replace("-", "").lower())  # no-separator
    # Head-word + its variants. E.g. 'pre-workout powders' → 'pre-workout',
    # 'pre workout', 'preworkout'.
    tokens = base.split()
    if len(tokens) >= 2:
        head = tokens[0].lower()
        if "-" in head or len(head) >= 5:
            out.append(head)
            if "-" in head:
                out.append(head.replace("-", " "))
                out.append(head.replace("-", ""))
            elif " " in head:
                out.append(head.replace(" ", "-"))
                out.append(head.replace(" ", ""))
    return list(dict.fromkeys(out))


def _build_substitute_user_category(
    substitute: str, *, brief: ProductBriefInput,
) -> StakeholderCategory:
    slug = _slugify(substitute)
    name_variants = _expand_term_variants(substitute)
    return StakeholderCategory(
        category_key=f"substitute_user_{slug}",
        display_name=f"{substitute} user (substitute / adjacent buyer)",
        description=(
            f"Evidence-grounded users of {substitute}, a recognized "
            f"substitute or adjacent product for the brief's category. "
            f"Their use-occasion overlaps with the unlaunched product "
            f"and they are addressable as switching candidates."
        ),
        why_relevant=(
            f"Substitute users represent the share-of-occasion the "
            f"unlaunched product would compete for. Their objections "
            f"to switching anchor adjacent-relevant evidence."
        ),
        likely_pains=[
            f"limitations of {substitute}",
            f"price of {substitute}",
        ],
        likely_objections=[
            f"why switch from {substitute}",
            "category fit concerns",
        ],
        likely_current_alternatives=[substitute],
        evidence_needed=[
            f"explicit {substitute} use",
            "substitute-occasion mention",
        ],
        source_query_themes=[
            f"{substitute} review", f"{substitute} use experience",
        ],
        inclusion_signals=[
            *name_variants,
            f"{substitute} user", f"{substitute} buyer",
            f"{substitute} consumer",
        ],
        exclusion_signals=["paid promotion"],
        minimum_persona_target_tiny=1,
        minimum_persona_target_small=2,
        minimum_persona_target_serious=5,
        priority="medium",
    )


def _build_use_case_category(
    use_case: str, *, brief: ProductBriefInput,
) -> StakeholderCategory:
    """A use-case category captures evidence about a specific user
    role / occasion (e.g. 'college students', 'gym-goers',
    'beachgoers', 'parents'). The use-case phrase itself is the
    inclusion signal.

    Phase 8.4A.3: variants now include hyphen / space / no-separator
    swaps via `_expand_term_variants`, plus singular forms.
    """
    slug = _slugify(use_case)
    # Hyphen / space / no-separator variants of the full phrase.
    variants = list(_expand_term_variants(use_case))
    # Add singular forms (drop trailing 's') of every variant.
    singulars: list[str] = []
    for v in variants:
        if v.endswith("s") and len(v) > 4:
            singulars.append(v[:-1])
    for s in singulars:
        if s not in variants:
            variants.append(s)
    # Pull out the role-noun-shaped tokens so the scorer's role-noun
    # extractor has something explicit to match against.
    role_anchors = []
    for v in variants:
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9'\-]+", v):
            if len(tok) >= 4:
                role_anchors.append(tok.lower())
    role_anchors = list(dict.fromkeys(role_anchors))
    return StakeholderCategory(
        category_key=f"use_case_{slug}",
        display_name=f"{use_case} (use-case / role)",
        description=(
            f"Evidence-grounded participants in the {use_case} "
            f"use-case / role for the brief's product category. The "
            f"role itself anchors relevance: someone who self-"
            f"identifies as a {use_case} member is an addressable "
            f"market-entry voice for an unlaunched product targeting "
            f"this use-case."
        ),
        why_relevant=(
            f"Use-case role evidence anchors how the product would "
            f"land in real consumption / usage occasions. Critical "
            f"for unlaunched products where direct product evidence "
            f"is absent — the role is the relevance."
        ),
        likely_pains=[
            f"unmet need in {use_case} occasion",
            "current solution insufficient",
        ],
        likely_objections=[
            "current solution works fine",
            "why change my routine",
        ],
        likely_current_alternatives=[
            "current solution in this use-case",
        ],
        evidence_needed=[
            f"explicit {use_case} role / membership",
            "use-case occasion evidence",
        ],
        source_query_themes=[
            f"{use_case} category review",
            f"{use_case} routine",
            f"{use_case} habits",
        ],
        inclusion_signals=[
            *variants,
            *role_anchors,
        ],
        exclusion_signals=["marketing voice"],
        minimum_persona_target_tiny=1,
        minimum_persona_target_small=3,
        minimum_persona_target_serious=8,
        priority="high",
    )


def _build_objection_category(
    primitive: dict,
) -> StakeholderCategory:
    suffix = primitive["key_suffix"]
    return StakeholderCategory(
        category_key=f"objection_{suffix}",
        display_name=primitive["display"],
        description=(
            f"Universal market-entry objection group: {primitive['display']}. "
            f"Captures evidence-grounded buyers whose objections are "
            f"category-level (not product-specific)."
        ),
        why_relevant=(
            f"Objection-driven buyers are addressable for unlaunched "
            f"products whose value proposition would need to overcome "
            f"the same objection to win share."
        ),
        likely_pains=list(primitive["pains"]),
        likely_objections=list(primitive["objections"]),
        likely_current_alternatives=[],
        evidence_needed=list(primitive["evidence_needed"]),
        source_query_themes=[primitive["display"].lower()],
        inclusion_signals=list(primitive["inclusion_signals"]),
        exclusion_signals=(
            list(primitive["exclusion_signals"])
            or ["marketing voice"]
        ),
        minimum_persona_target_tiny=1,
        minimum_persona_target_small=2,
        minimum_persona_target_serious=4,
        priority=primitive["priority"],
    )


def _build_buyer_type_category(
    primitive: dict,
) -> StakeholderCategory:
    suffix = primitive["key_suffix"]
    return StakeholderCategory(
        category_key=f"buyer_type_{suffix}",
        display_name=primitive["display"],
        description=(
            f"Universal market-entry buyer-type group: "
            f"{primitive['display']}."
        ),
        why_relevant=(
            f"Buyer-type signal is category-agnostic; it surfaces "
            f"how a persona approaches purchase decisions, anchoring "
            f"market-entry simulation around the same buyer-type "
            f"distribution as the brief's category."
        ),
        likely_pains=list(primitive["pains"]),
        likely_objections=list(primitive["objections"]),
        likely_current_alternatives=[],
        evidence_needed=list(primitive["evidence_needed"]),
        source_query_themes=[primitive["display"].lower()],
        inclusion_signals=list(primitive["inclusion_signals"]),
        exclusion_signals=(
            list(primitive["exclusion_signals"])
            or ["marketing voice"]
        ),
        minimum_persona_target_tiny=1,
        minimum_persona_target_small=2,
        minimum_persona_target_serious=4,
        priority=primitive["priority"],
    )


def _build_geography_category(
    geography: str,
) -> StakeholderCategory:
    """Geography is a SOFT modifier in market-entry mode — its
    minimums are 0 so failure to find geography-specific evidence
    doesn't block readiness. Inclusion is additive."""
    slug = _slugify(geography)
    return StakeholderCategory(
        category_key=f"geography_{slug}",
        display_name=f"{geography} regional buyer (soft modifier)",
        description=(
            f"OPTIONAL geography overlay. In market-entry mode this "
            f"category is a bonus signal, not a hard requirement — "
            f"non-{geography} category evidence still counts as "
            f"relevant with a regional caveat. Direct {geography} "
            f"evidence raises a persona's score additively."
        ),
        why_relevant=(
            f"Local-market evidence strengthens market-entry signals "
            f"but is rare in public-web snippet pools. Treating it as "
            f"a soft bonus prevents geography from gating out "
            f"otherwise-strong category personas."
        ),
        likely_pains=[
            "regional availability concerns",
        ],
        likely_objections=[
            f"not available in {geography}",
        ],
        likely_current_alternatives=[],
        evidence_needed=[
            f"{geography}-mention",
        ],
        source_query_themes=[
            f"{geography} category buyer",
        ],
        inclusion_signals=[
            geography, geography.lower(),
        ],
        exclusion_signals=["non-regional marketing voice"],
        minimum_persona_target_tiny=0,  # SOFT — never blocks readiness
        minimum_persona_target_small=0,
        minimum_persona_target_serious=0,
        priority="low",
    )


# ---------------------------------------------------------------------------
# Top-level dynamic generator
# ---------------------------------------------------------------------------


def build_dynamic_market_entry_categories(
    brief: ProductBriefInput,
) -> list[StakeholderCategory]:
    """Generate stakeholder categories from generic primitives.

    Output structure:
      * one `competitor_user_<slug>` per brief.competitor (high pri)
      * one `substitute_user_<slug>` per parsed substitute (medium)
      * one `use_case_<slug>` per parsed use-case role (high pri)
      * 5 universal objection categories (medium)
      * 3 universal buyer-type categories (medium)
      * one `geography_<slug>` if brief.geography set (low, SOFT)

    The exact category count is determined by the brief shape — there
    is NO product-family-specific code path. Sunscreen briefs, energy-
    drink briefs, Shopify-tool briefs all flow through the same
    generator and get the right shape because their competitors,
    substitutes, and use-cases differ.
    """
    categories: list[StakeholderCategory] = []

    # 1. Competitor users (one per brief.competitor)
    for competitor in brief.competitors[:8]:
        categories.append(
            _build_competitor_user_category(competitor, brief=brief)
        )

    # 2. Substitute users (parsed from brief text)
    for substitute in parse_substitutes_from_brief(brief):
        categories.append(
            _build_substitute_user_category(substitute, brief=brief)
        )

    # 3. Use-case groups (parsed from intended_user / target_market)
    for use_case in parse_use_cases_from_brief(brief):
        categories.append(
            _build_use_case_category(use_case, brief=brief)
        )

    # 4. Objection groups (universal)
    for primitive in _UNIVERSAL_OBJECTION_PRIMITIVES:
        categories.append(_build_objection_category(primitive))

    # 5. Buyer-type groups (universal)
    for primitive in _UNIVERSAL_BUYER_TYPE_PRIMITIVES:
        categories.append(_build_buyer_type_category(primitive))

    # 6. Geography (soft modifier; only when brief.geography is set)
    if brief.geography and brief.geography.strip():
        categories.append(
            _build_geography_category(brief.geography)
        )

    return categories


__all__ = [
    "build_dynamic_market_entry_categories",
    "looks_like_market_entry_brief",
    "parse_substitutes_from_brief",
    "parse_use_cases_from_brief",
]
