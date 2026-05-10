"""Phase 8.2F — trait-extraction prompt template.

Phase 8.2F.6 hardening (this file's current revision):

  * field-level extraction TRIGGERS — when the snippet contains
    complaint / skepticism / tool-mention / role markers, the model
    MUST extract the corresponding trait (rather than defaulting to
    'unknown' too easily).
  * JSON-only output — explicit "your entire response must start with
    `{` and end with `}`; no markdown fences, no commentary, no
    leading/trailing whitespace beyond the JSON itself".
  * worked example — short illustrative valid output that the model
    can pattern-match against; reduces shape-mistakes that previously
    triggered the repair loop on every shell.
  * extra-keys forbidden — restated three times across system + user
    + the JSON-shape example.

The Pydantic schema (`_LLMExtractionPayload` in extractor.py) still
uses `extra='forbid'` — these prompt changes just reduce the rate at
which the model emits non-conforming output in the first place.
"""
from __future__ import annotations

from textwrap import dedent

from assembly.pipeline.persona.constants import (
    INFERRED_MIN_CONFIDENCE,
    PERSONA_FIELD_NAMES,
    SUPPORT_LEVELS,
)


SYSTEM_PROMPT = dedent("""
    You extract anonymous, source-grounded persona traits from short
    excerpts of public web evidence. You operate under a strict
    discipline:

    OUTPUT FORMAT — read carefully:
      • Your ENTIRE response is one JSON object. No prose, no
        commentary, no preface, no trailing summary, no markdown
        fences (no ```json), no XML, no yaml. The first character of
        your reply is `{{` and the last character is `}}`.
      • The JSON has EXACTLY one top-level key: `traits`. No other
        top-level keys. No `summary`, `notes`, `analysis`, etc.
      • Each trait object has EXACTLY these six keys:
        `field_name`, `support_level`, `value`, `source_excerpt`,
        `confidence`, `rationale`. No other keys.

    CONTENT RULES:
      • You MAY ONLY emit traits for these field names (closed set):
        {fields}.
      • support_level MUST be one of: {levels}.
      • 'direct' or 'inferred': MUST quote a `source_excerpt` taken
        verbatim from the input. The excerpt MUST appear character-
        for-character somewhere in the evidence. If you cannot quote
        verbatim, mark the trait 'unknown' and emit value=null.
      • 'inferred' confidence MUST be >= {inferred_min}. Otherwise
        emit 'unknown'.
      • For fields with NO supporting evidence in the input, emit
        support_level='unknown' and value=null. NEVER guess.

    FIELD-LEVEL EXTRACTION TRIGGERS (Phase 8.2F.6):
      You SHOULD extract the corresponding trait when the evidence
      contains the listed signals. The 'unknown' default is for
      fields with NO matching signal — not for "I'm uncertain".

      • role_or_context — extract when the evidence contains any of:
        "merchant", "store owner", "shop owner", "founder", "operator",
        "DTC", "ecommerce", "small business", "entrepreneur",
        "site owner", "shopkeeper", or other commerce-role context.
        The value should describe the persona's commerce-role context
        in their own framing (paraphrase OK; quote in source_excerpt).

      • objection_patterns — extract when the evidence contains
        complaint or frustration language: "expensive", "too much",
        "fed up", "frustrated", "broken", "ruined", "hate", "wish",
        "tired of", "stuck", "burned by", "never again", "problem
        with", "issue", "concern", "complaint", "annoying",
        "overwhelming", "bloated", "lock-in", "lock in".
        Quote the strongest complaint sentence in source_excerpt.

      • trust_triggers — extract when the evidence mentions trust /
        skepticism / risk / control / lock-in / AI / quality concerns:
        "trust", "skeptical", "skepticism", "control", "brand control",
        "transparent", "transparency", "guarantee", "proof",
        "credibility", "lock-in", "vendor lock-in", "switch back",
        "AI", "automation skepticism", "reliability", "support
        quality". Note what the persona would NEED to trust the
        product (paraphrase the threshold; quote one supporting
        sentence).

      • current_alternatives — extract when the evidence mentions
        specific tools or providers: "Shopify", "Shopify apps",
        "Shopify Magic", "WooCommerce", "BigCommerce", "Wix",
        "Squarespace", "Magento", "Klaviyo", "Oberlo", "Mailchimp",
        "Stripe", "agency", "agencies", "freelancer", "contractor",
        "custom theme", "custom site", "in-house team", "manual
        workflow". List the alternative(s) explicitly.

      • price_sensitivity / buying_constraints — extract when the
        evidence mentions price, cost, fee, subscription, tier,
        budget, "expensive", "cheap", "afford", "value for money".

      • communication_style — extract when the evidence has clear
        tonal markers (analytical, casual, technical, narrative,
        emotional). Otherwise 'unknown'.

      • interests — extract when the evidence mentions specific
        commerce / product / tool / domain interests.

      • influence_signals — almost always 'unknown' for Tavily
        snippets. Set 'direct' ONLY when the snippet contains
        explicit measured engagement metadata (follower / subscriber
        / review counts). Snippets DO NOT typically carry these.

      • geography_broad — 'unknown' unless the evidence contains an
        explicit geographic claim (country, region, state). Do NOT
        guess from language, domain, or shipping mention.

    SAFETY RULES:
      • You MUST NOT infer demographic, sensitive, or identity-
        bearing attributes (age, gender, race, ethnicity, religion,
        sexual orientation, health, immigration status, household
        income, precise address, ZIP, employer identity, real
        names, emails, phone numbers, profile URLs).
      • All `value` strings MUST be free of identity markers (no
        @handles, no real names, no emails, no phone numbers, no
        profile URLs).
      • You do NOT invent facts. Mechanism priors / general
        commerce knowledge cannot fill a field that the evidence
        does not support — that field stays 'unknown'.

    REMEMBER: every persona-field name MUST appear once in
    `traits`. Do not omit any. Do not emit duplicates. Do not emit
    field names outside the closed set.
    """).strip().format(
        fields=", ".join(repr(f) for f in PERSONA_FIELD_NAMES),
        levels=", ".join(repr(l) for l in SUPPORT_LEVELS),
        inferred_min=INFERRED_MIN_CONFIDENCE,
)


# Worked example — the model can pattern-match this for the JSON shape.
# Phrased as if from a Shopify-merchant snippet so it is contextually
# similar to most production inputs.
_WORKED_EXAMPLE_JSON = dedent("""
    {
      "traits": [
        {
          "field_name": "role_or_context",
          "support_level": "direct",
          "value": "Shopify merchant running a mid-volume DTC store",
          "source_excerpt": "I'm a Shopify merchant doing about $30k/month",
          "confidence": 0.9,
          "rationale": "Persona self-describes their commerce role and scale."
        },
        {
          "field_name": "objection_patterns",
          "support_level": "direct",
          "value": "Frustrated by plugin bloat and cumulative monthly fees",
          "source_excerpt": "my plugin stack is overwhelming and I'm fed up",
          "confidence": 0.85,
          "rationale": "Explicit complaint about plugin overload."
        },
        {
          "field_name": "current_alternatives",
          "support_level": "direct",
          "value": "Klaviyo for email; Oberlo for product listings",
          "source_excerpt": "I've installed Oberlo and Klaviyo",
          "confidence": 0.95,
          "rationale": "Concrete tools the persona is currently using."
        },
        {
          "field_name": "price_sensitivity",
          "support_level": "inferred",
          "value": "High; cumulative monthly fees flagged as a concern",
          "source_excerpt": "Shopify is also not cheap with the basic 30$ monthly fee",
          "confidence": 0.7,
          "rationale": "Persona signals price-sensitivity through fee framing."
        },
        {
          "field_name": "trust_triggers",
          "support_level": "unknown",
          "value": null,
          "source_excerpt": null,
          "confidence": 0.0,
          "rationale": "Snippet does not mention trust thresholds."
        },
        {
          "field_name": "buying_constraints",
          "support_level": "unknown",
          "value": null,
          "source_excerpt": null,
          "confidence": 0.0,
          "rationale": "No explicit buying constraint in evidence."
        },
        {
          "field_name": "communication_style",
          "support_level": "unknown",
          "value": null,
          "source_excerpt": null,
          "confidence": 0.0,
          "rationale": "Tone could be casual but no strong marker; safer as unknown."
        },
        {
          "field_name": "interests",
          "support_level": "inferred",
          "value": "Ecommerce store management and product listings",
          "source_excerpt": "manage product listings and email marketing",
          "confidence": 0.6,
          "rationale": "Inferred from listed concerns and tool mentions."
        },
        {
          "field_name": "influence_signals",
          "support_level": "unknown",
          "value": null,
          "source_excerpt": null,
          "confidence": 0.0,
          "rationale": "No explicit follower/subscriber counts in evidence."
        },
        {
          "field_name": "geography_broad",
          "support_level": "unknown",
          "value": null,
          "source_excerpt": null,
          "confidence": 0.0,
          "rationale": "No explicit geographic claim."
        }
      ]
    }
    """).strip()


def build_user_prompt(*, aggregated_content: str) -> str:
    """Compose the user-side prompt for one shell. The aggregated
    content is the source-side material the extractor must quote
    verbatim from."""
    return dedent("""
        Below is the aggregated public-web evidence for ONE candidate
        persona shell. Each `### record N` block is a separate source
        record; you may quote any record's content verbatim.

        ---begin-evidence---
        {content}
        ---end-evidence---

        Emit a JSON object with EXACTLY one top-level key, `traits`,
        whose value is a list of trait objects. Every persona field
        name from the closed set MUST appear exactly once.

        Each trait object has EXACTLY these six keys (no others):
          - field_name        (one of the closed set)
          - support_level     (one of the closed set)
          - value             (string or null)
          - source_excerpt    (string verbatim from the evidence, or null)
          - confidence        (float 0..1)
          - rationale         (one short sentence; never identity-bearing)

        Apply the field-level extraction triggers from the system
        message: where the evidence contains complaint / skepticism /
        role / tool-mention markers, EXTRACT the corresponding trait
        rather than defaulting to 'unknown'. Reserve 'unknown' for
        fields with NO matching signal in the evidence.

        Here is a SHAPE-ONLY example of valid output (this example is
        about a different persona; do NOT copy values verbatim — use
        only its STRUCTURE):

        {example}

        Now emit the JSON for the actual evidence above. Your reply
        starts with `{{` and ends with `}}`. No markdown fences. No
        commentary. No additional keys.
        """).strip().format(
            content=aggregated_content[:6000],
            example=_WORKED_EXAMPLE_JSON,
        )
