"""Phase 8.2F — source classifier tests (pure, no DB)."""
from __future__ import annotations

import pytest

from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
    classify_source_record,
)


# ---------------------------------------------------------------------------
# Strong persona signal
# ---------------------------------------------------------------------------


def test_first_person_complaint_is_strong_persona_signal() -> None:
    text = (
        "I'm a Shopify merchant doing about $30k/month and I switched away "
        "from BigCommerce last year. My plugin stack is overwhelming and I "
        "wish there was a tool that would consolidate them without taking "
        "away my brand control. I'm frustrated paying $400 a month for plugins."
    )
    r = classify_source_record(
        content=text,
        source_url="https://reddit.example.test/r/shopify/comments/aaa",
    )
    assert r.classification == SourceClassification.STRONG_PERSONA_SIGNAL


def test_review_text_is_strong_persona_signal() -> None:
    text = (
        "2 out of 5 stars. I would not recommend this tool. I tried using "
        "it for three months on my store and the AI broke my checkout flow "
        "twice. Pros and cons: pros are the price; cons are I cannot trust "
        "it with my brand."
    )
    r = classify_source_record(
        content=text,
        source_url="https://reviews.example.test/saas/some-product/all",
    )
    assert r.classification == SourceClassification.STRONG_PERSONA_SIGNAL


def test_founder_quote_is_strong_persona_signal() -> None:
    text = (
        "As a founder running a $20k/month DTC store I've been burned by "
        "agencies twice. I'm fed up with paying for redesigns that don't move "
        "conversion. I'd switch to AI tooling but only if I retain final "
        "control over branding."
    )
    r = classify_source_record(
        content=text,
        source_url="https://forum.example.test/threads/12345",
    )
    assert r.classification == SourceClassification.STRONG_PERSONA_SIGNAL


# ---------------------------------------------------------------------------
# Context-only signal
# ---------------------------------------------------------------------------


def test_generic_blog_article_is_context_only() -> None:
    text = (
        "In this article we'll explore the top 10 best Shopify SEO plugins "
        "for 2025. Subscribe to our newsletter to read more. Trusted by "
        "5,000 merchants worldwide. In this guide we cover the best tools, "
        "pricing tips, and a strategy for store owners. Read more in our "
        "complete guide to Shopify automation."
    )
    r = classify_source_record(
        content=text,
        source_url="https://blog.example.test/best-shopify-seo-plugins-2025",
    )
    assert r.classification == SourceClassification.CONTEXT_ONLY


def test_pricing_page_is_context_only() -> None:
    text = (
        "Our platform offers four pricing tiers — Starter $29/mo, Growth "
        "$99/mo, Pro $299/mo, Enterprise custom. Trusted by leading "
        "ecommerce brands. Get started today, request a demo, or schedule "
        "a call. We help merchants automate their store at scale."
    )
    r = classify_source_record(
        content=text,
        source_url="https://example.test/pricing",
    )
    assert r.classification == SourceClassification.CONTEXT_ONLY


def test_landing_page_is_context_only() -> None:
    text = (
        "Trusted by 1,200+ merchants. We help you launch your Shopify store "
        "in under a week. Get started today with our agency. Our clients "
        "include some of the fastest-growing DTC brands. Book a free "
        "consultation to see how we can help your business grow."
    )
    r = classify_source_record(
        content=text,
        source_url="https://agency.example.test/services/shopify",
    )
    assert r.classification == SourceClassification.CONTEXT_ONLY


def test_short_content_is_context_only() -> None:
    text = "Short snippet about plugins."
    r = classify_source_record(
        content=text,
        source_url="https://example.test/some/short/page",
    )
    assert r.classification == SourceClassification.CONTEXT_ONLY


# ---------------------------------------------------------------------------
# Sensitive / identity rejection
# ---------------------------------------------------------------------------


def test_sensitive_content_is_rejected() -> None:
    """Even when the content otherwise looks like first-person speech,
    a sensitive-attribute hit triggers full rejection."""
    text = (
        "I'm a Shopify merchant doing $30k/month and I described my "
        "black ethnicity as part of my persona. My plugin stack is overwhelming."
    )
    r = classify_source_record(
        content=text,
        source_url="https://reddit.example.test/r/shopify/comments/zzz",
    )
    assert r.classification == SourceClassification.REJECT_FOR_SENSITIVE_OR_IDENTITY_RISK


def test_identity_residual_text_is_rejected() -> None:
    """Residual identity shape — a very crude regex catch — rejects the
    record."""
    text = (
        "Smith Jones's store has been struggling with plugin bloat for "
        "months and the team is fed up with paying for redesigns. "
        "Smith Jones's brand is what matters most."
    )
    r = classify_source_record(
        content=text,
        source_url="https://forum.example.test/threads/zzz",
    )
    assert r.classification == SourceClassification.REJECT_FOR_SENSITIVE_OR_IDENTITY_RISK


# ---------------------------------------------------------------------------
# URL shape integration
# ---------------------------------------------------------------------------


def test_blog_url_path_pushes_to_context_even_with_some_first_person() -> None:
    text = (
        "I'd like to walk you through our agency's process. We help merchants "
        "launch faster. Trusted by hundreds of clients. Read more in our "
        "guide to growing a DTC brand. Subscribe to our blog for more tips."
    )
    r = classify_source_record(
        content=text,
        source_url="https://agency.example.test/blog/how-to-launch-fast",
    )
    assert r.classification == SourceClassification.CONTEXT_ONLY


def test_user_handle_hash_bumps_persona_score() -> None:
    """A record with a salted handle hash present indicates the source
    knows the author identity (e.g. an approved handle-bearing API).
    The classifier nudges toward persona on those records."""
    text = (
        "I switched to a new platform last quarter because the plugin "
        "stack was eating my margins. My store is doing better now and I "
        "wish I had switched earlier. The migration was painful but worth it."
    )
    r = classify_source_record(
        content=text,
        source_url="https://forum.example.test/threads/some-thread",
        user_handle_hash="abc123def456",
    )
    assert r.classification == SourceClassification.STRONG_PERSONA_SIGNAL
    assert "user_handle_hash present (+2 persona)" in r.rationale
