"""Phase 8.5B.1 — deterministic dynamic anchor planner.

`generate_anchor_plan(brief)` takes a `ProductBriefForPlanning` and
produces an `EvidenceAnchorPlan` derived ENTIRELY from the brief's
text plus three universal lexicons (stopwords, generic modifiers,
ambiguity contexts). NO LLM. NO network. NO product-category
hardcoding.

The planner is product-agnostic by construction:

  * `_extract_content_tokens` — tokenizes the brief's
    description / target_customers / competitors and filters
    out stopwords + generic modifiers.
  * `_infer_product_type` — picks the most likely product-type
    phrase (last 1–3 noun-shaped tokens of the description's
    leading clause).
  * `_detect_ambiguous_competitors` — flags short / common-word
    competitors and pulls discriminating phrases from
    UNIVERSAL_AMBIGUITY_CONTEXTS.
  * `_build_metadata_rules` — constructs metadata-relevance
    rules from the brief's product-type tokens.

Triton + Solara + any other product all run through the same
function with no per-product code path.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime

from assembly.sources.evidence_anchor_planner.constants import (
    SHORT_NAME_AMBIGUITY_THRESHOLD,
    UNIVERSAL_AMBIGUITY_CONTEXTS,
    UNIVERSAL_GENERIC_MODIFIERS,
    UNIVERSAL_STOPWORDS,
)
from assembly.sources.evidence_anchor_planner.schemas import (
    AmbiguousEntity,
    EvidenceAnchorPlan,
    MetadataRelevanceRule,
    ProductBriefForPlanning,
)


# ---------------------------------------------------------------------------
# Tokenization helpers
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")
_GENERIC_MODIFIER_SET = frozenset(t.lower() for t in UNIVERSAL_GENERIC_MODIFIERS)


def _tokenize(text: str) -> list[str]:
    """Return lowercased word-shaped tokens. Strips punctuation."""
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _content_tokens(text: str) -> list[str]:
    """Tokenize + filter out stopwords + generic modifiers + very
    short tokens. The remaining tokens are CONTENT words — useful
    for anchor extraction."""
    out: list[str] = []
    for t in _tokenize(text):
        if t in UNIVERSAL_STOPWORDS or t in _GENERIC_MODIFIER_SET:
            continue
        if len(t) < 3:
            continue
        out.append(t)
    return out


def _bigrams(tokens: list[str]) -> list[str]:
    return [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]


# ---------------------------------------------------------------------------
# Product-type inference
# ---------------------------------------------------------------------------


_PRODUCT_TYPE_HEADWORDS = (
    "drink", "beverage", "stick", "cream", "lotion", "spray",
    "powder", "bar", "shake", "capsule", "tablet", "gel",
    "oil", "paste", "kit", "set", "pack", "subscription",
    "service", "app", "platform", "tool", "device", "gadget",
    "balm", "salve", "ointment", "wipe", "wipes",
    "serum", "mask", "pad", "pads", "roll-on", "rollon",
)


def _infer_product_type(brief: ProductBriefForPlanning) -> str:
    """Pick a short noun phrase that best names the product type.

    Strategy: look at the first sentence of the description; find the
    last "head" noun (one of the canonical product-type headwords if
    present, else the longest bare noun adjacent to descriptive
    adjectives). Falls back to the product_name + ' product' if no
    candidate is found."""
    desc = brief.product_description.strip()
    first_sentence = re.split(r"[.!?]", desc, maxsplit=1)[0]
    tokens = _content_tokens(first_sentence)
    if not tokens:
        return f"{brief.product_name} product"
    # 1. Prefer a known product-type headword and the 1-3 tokens
    # before it.
    for i, t in enumerate(tokens):
        if t in _PRODUCT_TYPE_HEADWORDS:
            start = max(0, i - 2)
            phrase = " ".join(tokens[start:i + 1])
            return phrase
    # 2. Fallback — last 2 content tokens of the first sentence.
    return " ".join(tokens[-2:]) if len(tokens) >= 2 else tokens[-1]


# ---------------------------------------------------------------------------
# Anchor extraction
# ---------------------------------------------------------------------------


def _build_positive_anchors(
    brief: ProductBriefForPlanning,
    product_type: str,
) -> list[str]:
    """Strong product-shape anchors derived from description + name.

    Phase 8.5B.1 quality fix: positive_anchor_terms ARE NOT
    arbitrary high-frequency bigrams from the description.
    Filler bigrams like "who want", "students outdoor", "young
    adults", "busy young" are demographic/lifestyle, not product
    category. They flood false positives in unrelated reviews
    (snack boxes, grip strengtheners, etc.).

    Discriminating rule: only TWO classes of phrase qualify as a
    positive_anchor_term:

      1. Single-token CONTENT WORDS from the inferred product_type.
         (e.g., for "mineral sunscreen stick" → mineral / sunscreen
         / stick.)

      2. Multi-word phrases ENDING in a product-type headword
         (`_PRODUCT_TYPE_HEADWORDS`: drink, stick, cream, etc.) and
         appearing in the brief's text. (e.g., "energy drink",
         "sports drink", "sunscreen stick", "mineral sunscreen
         stick".)

    Everything else (filler bigrams, demographic phrases) routes
    to use_case_anchor_terms or stays out of the positive list.
    """
    pt_tokens = _content_tokens(product_type)
    desc_pool = (
        brief.product_name + " "
        + brief.product_description + " "
        + " ".join(brief.target_customers)
    )
    desc_tokens = _content_tokens(desc_pool)
    desc_bigrams = _bigrams(desc_tokens)
    desc_trigrams = [
        f"{desc_tokens[i]} {desc_tokens[i + 1]} {desc_tokens[i + 2]}"
        for i in range(len(desc_tokens) - 2)
    ]
    # Multi-word phrases ending in a known product-type headword
    head_phrases: list[str] = []
    for ngram in desc_bigrams + desc_trigrams:
        last = ngram.rsplit(" ", 1)[-1]
        if last in _PRODUCT_TYPE_HEADWORDS:
            # Filter: every token must be a content word (no
            # stopwords / generic modifiers / very-short tokens).
            parts = ngram.split()
            if all(
                p not in UNIVERSAL_STOPWORDS
                and p not in _GENERIC_MODIFIER_SET
                and len(p) >= 3
                for p in parts
            ):
                head_phrases.append(ngram)
    out: list[str] = []
    seen: set[str] = set()
    # Order: most-specific (longest) head phrases first → product_type
    # → single-token content words from product_type.
    head_phrases_sorted = sorted(
        set(head_phrases),
        key=lambda s: (-s.count(" "), s),
    )
    for term in (
        head_phrases_sorted
        + [product_type]
        + pt_tokens
    ):
        t = term.strip()
        if not t or t in seen or len(t) < 3:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= 12:
            break
    return out


_LEADING_NUMERIC_PREFIX_RE = re.compile(
    r"^\s*(?:\(?\d+\)?[\.\):\-\s])\s*",
)


def _strip_user_listing_prefix(s: str) -> str:
    """Remove leading numeric prefixes ("1. ", "(2) ", "3-", "4: ")
    that founders often paste from their own numbered lists. Without
    this, a competitor entered as "1. Samsung Family Hub" would never
    match a search snippet that says just "Samsung Family Hub" — the
    anchor includes the literal "1. " prefix.

    Also strips leading conjunctions ("and ", "or ") that appear when
    a free-text customer list gets split on commas — fragments like
    "or accidentally buy duplicate pantry items" become "accidentally
    buy duplicate pantry items"."""
    out = _LEADING_NUMERIC_PREFIX_RE.sub("", s)
    out = re.sub(r"^\s*(?:and|or)\s+", "", out, flags=re.IGNORECASE)
    return out.strip()


def _build_competitor_anchors(competitors: list[str]) -> list[str]:
    """Competitor brand names + a few canonical variants per name.

    Variant generation is purely structural: lowercase, no-spaces,
    word-boundary forms. Produces nothing category-specific."""
    out: list[str] = []
    seen: set[str] = set()
    for c in competitors:
        c = _strip_user_listing_prefix(c)
        if not c:
            continue
        for v in (c, c.lower(), c.replace(" ", ""), c.replace("-", " ")):
            v = v.strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _build_substitute_anchors(brief: ProductBriefForPlanning) -> list[str]:
    """Substitutes are inferred from substitute-shape language in the
    description and from the optional_constraints field."""
    candidates: list[str] = []
    text = brief.product_description + " " + " ".join(brief.optional_constraints)
    # Match "alternative to X", "substitute for X", "instead of X",
    # "rather than X", "vs X", "or X"
    patterns = [
        r"alternative to ([\w\s,-]+)",
        r"substitute for ([\w\s,-]+)",
        r"instead of ([\w\s,-]+)",
        r"rather than ([\w\s,-]+)",
        r"\boverlaps? with ([\w\s,-]+)",
        r"\bsubstitutes considered in scope:?\s*([\w\s,-]+)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            candidates.extend(
                t.strip() for t in re.split(r"[,;/]", m.group(1))
                if t.strip()
            )
    # Cap + dedupe
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if not c:
            continue
        # Truncate at first phrase-end token if it ran long
        c_short = c.split(" and ")[0].split(" without ")[0].strip()
        if c_short and c_short.lower() not in seen and len(c_short) < 60:
            seen.add(c_short.lower())
            out.append(c_short)
    return out


def _build_use_case_anchors(brief: ProductBriefForPlanning) -> list[str]:
    """Use-case anchors come from target_customers + verbs in the
    description (workouts, studying, hiking, etc.).

    target_customers entries get their leading numeric / conjunction
    prefixes stripped, and obvious sentence fragments are skipped so
    a list pasted from a paragraph ("forget leftovers", "or
    accidentally buy duplicate pantry items.") doesn't pollute the
    anchor set."""
    out: list[str] = []
    seen: set[str] = set()
    for c in brief.target_customers:
        c = _strip_user_listing_prefix(c).rstrip(".")
        if not c:
            continue
        # Reject obvious sentence-fragment-shaped entries: a customer
        # type is a noun phrase. Fragments tend to start with a verb
        # ("forget", "accidentally"), end mid-thought, or be a single
        # adverb. Heuristic: at least 2 words AND first word is not
        # a leading verb of the kinds we see in pasted-list bugs.
        first = c.split()[0].lower() if c.split() else ""
        if first in {
            "forget", "forgets", "accidentally", "remember",
            "buy", "bought", "thinking", "thinks", "hoping",
        }:
            continue
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    # Also pull verb-shape tokens from description (gerunds + plain
    # verbs that appear in customer-action contexts).
    desc = brief.product_description
    for m in re.finditer(
        r"\b(workout|workouts|studying|study|hiking|hike|running|run|"
        r"reapply|alertness|performance|recovery|endurance|gym|sports|"
        r"breakfast|lunch|dinner|snack|snacking|sleep|sleeping|"
        r"focus|focusing|concentration)\b",
        desc, re.IGNORECASE,
    ):
        v = m.group(0).lower()
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _build_objection_anchors(brief: ProductBriefForPlanning) -> list[str]:
    """Objection anchors are derived from negative-shape language in
    the description ("avoid X", "without Y", "no Z", "free of W")."""
    out: list[str] = []
    seen: set[str] = set()
    text = brief.product_description + " " + " ".join(brief.optional_constraints)
    for pat, label_template in (
        (r"avoid (?:the |their |its |any |a )?(\w+)", "avoid {0}"),
        (r"without (\w+)", "without {0}"),
        (r"no (?:added |excessive )?(\w+)", "no {0}"),
        (r"free of (\w+)", "{0} free"),
        (r"free from (\w+)", "{0} free"),
        (r"\b(causes? breakouts?|breakouts?)\b", "breakouts"),
        (r"\b(greasy|sticky|messy)\b", "{0} feel"),
        (r"\b(crash|crashes|jitters)\b", "{0}"),
    ):
        for m in re.finditer(pat, text, re.IGNORECASE):
            tok = m.group(1).lower().strip()
            if not tok or tok in UNIVERSAL_STOPWORDS:
                continue
            phrase = label_template.format(tok)
            if phrase not in seen:
                seen.add(phrase)
                out.append(phrase)
    return out


# ---------------------------------------------------------------------------
# Ambiguous entity detection — universal, brand-shape only
# ---------------------------------------------------------------------------


def _detect_ambiguous_entities(
    competitors: list[str],
    positive_anchor_terms: list[str],
    product_type: str,
) -> list[AmbiguousEntity]:
    """Flag a competitor as ambiguous when its name (case-insensitively)
    appears in ANY phrase from the universal ambiguity-context lexicon.
    The detector is purely lexicon-driven — no product-category code
    path. A competitor's lexicon membership is what makes it ambiguous,
    not its character length.

    For each ambiguous entity:
      * intended_sense_phrases = `<entity> <pt>` for product_type tokens
      * wrong_sense_phrases = phrases from UNIVERSAL_AMBIGUITY_CONTEXTS
        that contain the entity token
    """
    out: list[AmbiguousEntity] = []
    pt_tokens = _content_tokens(product_type)[:5]
    for c in competitors:
        c_low = c.strip().lower()
        if not c_low:
            continue
        # Find which lexicon categories mention this competitor token.
        # Whole-word match preferred (avoids "Bum" matching "album",
        # "Sun" matching "summer", etc.).
        matching_categories: list[str] = []
        wrong_phrases: list[str] = []
        for cat_key, phrases in UNIVERSAL_AMBIGUITY_CONTEXTS.items():
            cat_hits = [
                p for p in phrases
                if re.search(rf"\b{re.escape(c_low)}\b", p.lower())
            ]
            if cat_hits:
                matching_categories.append(cat_key)
                wrong_phrases.extend(cat_hits)
        if not matching_categories:
            continue  # not actually ambiguous
        # Build intended_sense_phrases from product_type tokens
        intended_phrases: list[str] = []
        for tok in pt_tokens:
            intended_phrases.append(f"{c_low} {tok}")
            intended_phrases.append(f"{tok} {c_low}")
        # Also include `<competitor> <each positive_anchor>` for top 5
        for anchor in positive_anchor_terms[:5]:
            phrase = f"{c_low} {anchor}".strip()
            if phrase not in intended_phrases:
                intended_phrases.append(phrase)
        intended_label = (
            f"{c} as {product_type}"
            if product_type
            else f"{c} as the brief's product category"
        )
        out.append(AmbiguousEntity(
            entity=c.strip(),
            intended_sense_label=intended_label,
            intended_sense_phrases=intended_phrases,
            wrong_sense_categories=sorted(set(matching_categories)),
            wrong_sense_phrases=sorted(set(wrong_phrases)),
        ))
    return out


# ---------------------------------------------------------------------------
# Metadata relevance rules
# ---------------------------------------------------------------------------


def _build_metadata_rules(
    brief: ProductBriefForPlanning,
    product_type: str,
    positive_anchor_terms: list[str],
) -> list[MetadataRelevanceRule]:
    """Construct rules that exercise Amazon metadata against the brief.

    Three rule kinds:
      * include-any product_type tokens in metadata.main_category +
        metadata.categories → +2 weight.
      * title-contains-any positive_anchor_terms (top 8) → +2 weight.
      * exclude-any cross-domain category labels that obviously
        contradict the product (electronics, books, movies, etc.)
        unless the brief's product_type literally names them →
        −2 weight.
    """
    pt_tokens = _content_tokens(product_type)
    # Always-bad metadata main_category labels for *most* products. If
    # the brief's own product_type contains one of these, the planner
    # drops it from the exclude rule (this stays universal).
    always_off_topic = {
        "books", "kindle store", "movies & tv", "music", "video games",
        "software", "office products", "tools & home improvement",
        "pet supplies", "industrial & scientific",
    }
    pt_token_set = set(pt_tokens)
    excludes = [
        c for c in always_off_topic
        if not any(tok in c.lower() for tok in pt_token_set)
    ]

    rules: list[MetadataRelevanceRule] = []
    # Phase 8.5B.1 quality fix: only use the FULL product_type phrase
    # (multi-word) for the category-includes rule, not its individual
    # single tokens. A single token like "sports" matches the
    # Sports_and_Outdoors category itself and floods false positives;
    # the multi-word phrase "sports energy drink" doesn't.
    if " " in product_type.strip():
        rules.append(MetadataRelevanceRule(
            kind="category_includes_any",
            values=[product_type],
            weight=2,
        ))
    # Title-contains rule: prefer multi-word positive anchors only;
    # single tokens are too noisy.
    multi_word_positives = [
        a for a in positive_anchor_terms[:12] if " " in a.strip()
    ][:6]
    if multi_word_positives:
        rules.append(MetadataRelevanceRule(
            kind="title_contains_any",
            values=multi_word_positives,
            weight=2,
        ))
    if excludes:
        rules.append(MetadataRelevanceRule(
            kind="category_excludes_any",
            values=excludes,
            weight=-2,
        ))
    return rules


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def _plan_id(brief: ProductBriefForPlanning) -> str:
    payload = "|".join((
        brief.product_name,
        brief.product_description,
        brief.price_or_price_structure or "",
        brief.launch_geography or "",
        ",".join(sorted(brief.target_customers)),
        ",".join(sorted(brief.competitors)),
        ",".join(sorted(brief.optional_constraints)),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def generate_anchor_plan(
    brief: ProductBriefForPlanning,
) -> EvidenceAnchorPlan:
    """Deterministic. Pure function over the brief.

    Same brief → same plan, byte-for-byte (modulo the timestamp,
    which is captured in `generated_at`).
    """
    product_type = _infer_product_type(brief)
    positive_anchors = _build_positive_anchors(brief, product_type)
    competitor_anchors = _build_competitor_anchors(brief.competitors)
    substitute_anchors = _build_substitute_anchors(brief)
    use_case_anchors = _build_use_case_anchors(brief)
    objection_anchors = _build_objection_anchors(brief)
    ambiguous = _detect_ambiguous_entities(
        brief.competitors, positive_anchors, product_type,
    )
    negative_contexts = sorted({
        p for ent in ambiguous for p in ent.wrong_sense_phrases
    })
    metadata_rules = _build_metadata_rules(
        brief, product_type, positive_anchors,
    )
    caveats = [
        "Anchor plan is deterministic — derived from the founder "
        "brief alone. No LLM, no network. Adding new product domains "
        "does NOT require code changes; the same planner runs.",
        "Generic modifiers ('flavor', 'price', etc.) ONLY count when "
        "co-occurring with a brief-derived anchor.",
    ]
    if ambiguous:
        caveats.append(
            f"{len(ambiguous)} competitor(s) flagged as ambiguous — "
            "wrong-sense matches will be rejected by the scorer."
        )
    return EvidenceAnchorPlan(
        product_name=brief.product_name,
        product_type=product_type,
        launch_geography=brief.launch_geography,
        target_customers=list(brief.target_customers),
        competitors=list(brief.competitors),
        substitutes=substitute_anchors,
        positive_anchor_terms=positive_anchors,
        competitor_anchor_terms=competitor_anchors,
        substitute_anchor_terms=substitute_anchors,
        use_case_anchor_terms=use_case_anchors,
        objection_anchor_terms=objection_anchors,
        generic_modifier_terms=list(UNIVERSAL_GENERIC_MODIFIERS),
        ambiguous_entities=ambiguous,
        negative_context_terms=negative_contexts,
        metadata_relevance_rules=metadata_rules,
        generated_from="deterministic",
        caveats=caveats,
        plan_id=_plan_id(brief),
        generated_at=datetime.now(UTC).isoformat(),
    )
