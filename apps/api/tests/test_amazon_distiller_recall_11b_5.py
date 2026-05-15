"""Phase 11B.5 — distiller recall tests for trust / setup / support.

The Phase 11A rule library only caught one or two phrasings per
signal type for trust/setup/support, missing the bulk of real
buyer-language complaints. Phase 11B.5 broadens those patterns.

This test file proves:
  1. The new patterns fire on common alternate phrasings the real
     dataset reviews use.
  2. Each new rule emits the right signal_type and theme.
  3. False positives are bounded — generic complaints / price
     complaints / praise should NOT silently classify as setup,
     support, or trust.
  4. Existing distiller behavior is unchanged (every Phase 11A
     test still passes; that's verified separately by the regression
     run).
  5. Drift: no HTTP imports added; no live-runtime integration.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

import inspect
import re
from collections.abc import Iterable

import pytest

from assembly.sources.amazon_reviews_2023 import AmazonReviewRecord
from assembly.sources.amazon_reviews_provider import (
    distill_review_signals,
)
from assembly.sources.amazon_reviews_provider import (
    distiller as distiller_mod,
)


_NEUTRAL_FILLER = (
    " Writing this review to share my honest experience here for "
    "future buyers to consider before deciding."
)
# Pad each test phrase with neutral filler text (NOT whitespace) so
# the distiller's eligibility check — which calls `text.strip()`
# before measuring length — can't strip the padding away and reject
# the row as `too_short`. The filler words above match no rule.


def _rec(text: str, *, rating: float = 2.0) -> AmazonReviewRecord:
    """One-line review constructor for table-driven tests."""
    return AmazonReviewRecord(
        category="Test", parent_asin="P1", asin="P1A",
        rating=rating, title="title",
        text=text + _NEUTRAL_FILLER,
        helpful_vote=0, verified_purchase=True, timestamp=1,
        user_id_hash="x",
    )


def _signal_types(text: str, *, rating: float = 2.0) -> set[str]:
    return {s.signal_type for s in distill_review_signals(_rec(text, rating=rating))}


def _themes(text: str, *, rating: float = 2.0) -> set[str]:
    return {s.theme for s in distill_review_signals(_rec(text, rating=rating)) if s.theme}


# ---------------------------------------------------------------------------
# 1. SETUP — broadened patterns
# ---------------------------------------------------------------------------


_SETUP_POSITIVE_PHRASINGS = [
    # took forever / hours / days
    "It took me forever to install this thing.",
    "Took us hours to set up the configuration.",
    "Took days to figure out how to pair it with my phone.",
    "Took all weekend to get it to work properly.",
    "It took ages to configure the settings the way I wanted.",
    # couldn't get working
    "I couldn't get it to work no matter what I tried.",
    "Couldn't figure out how to install this on Windows 11.",
    "I couldn't pair it with my router after multiple resets.",
    "Couldn't set it up despite following the manual.",
    # nightmare wording
    "Installation was a nightmare from start to finish.",
    "The setup process was painful — total disaster.",
    "Activation was brutal and took multiple support calls.",
    # setup failed
    "Activation failed three times before I gave up.",
    "Setup failed silently and never completed.",
    "Pairing wouldn't work even after a factory reset.",
    "Sign-up never completed, just spun forever.",
]


@pytest.mark.parametrize("text", _SETUP_POSITIVE_PHRASINGS)
def test_setup_recall_broadened(text: str) -> None:
    """Each phrasing in the corpus must produce at least one
    `setup`-typed signal."""
    types = _signal_types(text)
    assert "setup" in types, (
        f"setup rule did not fire for: {text!r} (got types: {types})"
    )


# ---------------------------------------------------------------------------
# 2. SUPPORT — broadened patterns
# ---------------------------------------------------------------------------


_SUPPORT_POSITIVE_PHRASINGS = [
    "Customer service is useless and never replied.",
    "Support is terrible and unhelpful.",
    "Their support was a joke — nothing got resolved.",
    "Customer care is garbage at this company.",
    "Got no response from support after a week.",
    "No reply from customer service for ten days.",
    "No help from the seller — they ghosted me.",
    "Seller refused to help with the broken unit.",
    "The manufacturer wouldn't honor the warranty.",
    "Vendor refused to refund or replace.",
    "I called them five times and got nowhere.",
    "Emailed support multiple times without resolution.",
    "Contacted them several times — no help.",
    "Warranty was denied without explanation.",
    "Return process was a nightmare and they refused.",
    "Refund was rejected on a clearly defective unit.",
]


@pytest.mark.parametrize("text", _SUPPORT_POSITIVE_PHRASINGS)
def test_support_recall_broadened(text: str) -> None:
    types = _signal_types(text)
    assert "support" in types, (
        f"support rule did not fire for: {text!r} (got types: {types})"
    )


# ---------------------------------------------------------------------------
# 3. TRUST — broadened patterns
# ---------------------------------------------------------------------------


_TRUST_POSITIVE_PHRASINGS = [
    "I don't trust this brand anymore after this experience.",
    "I do not trust this company.",
    "Wouldn't trust this seller again.",
    "Would never trust this manufacturer.",
    "This feels scammy from start to finish.",
    "Looks sketchy to me.",
    "Seems fishy honestly.",
    "This whole thing feels like a scam.",
    "Definitely a counterfeit, the logo is wrong.",
    "Appears to be a knockoff of the real brand.",
    "Clearly a fake — packaging is all wrong.",
    "Not authentic at all, returned immediately.",
    "Misleading listing — color is completely different.",
    "Not as advertised; the size is half what they show.",
    "Classic bait and switch on the bundle contents.",
    "False advertising on the battery life.",
    "Fake reviews everywhere — too many five-star fluff.",
    "Reviews seem fake or paid for, suspicious.",
]


@pytest.mark.parametrize("text", _TRUST_POSITIVE_PHRASINGS)
def test_trust_recall_broadened(text: str) -> None:
    types = _signal_types(text)
    assert "trust" in types, (
        f"trust rule did not fire for: {text!r} (got types: {types})"
    )


# ---------------------------------------------------------------------------
# 4. FALSE POSITIVES — generic complaints must NOT silently become
#    setup/support/trust just because they share a noun.
# ---------------------------------------------------------------------------


def test_feels_cheap_stays_durability_not_trust() -> None:
    """`feels cheap` is a build-quality complaint, not a trust
    signal. It must produce `durability` only."""
    types = _signal_types("This feels cheap and flimsy.")
    assert "durability" in types
    assert "trust" not in types


def test_broke_after_week_stays_durability_not_setup() -> None:
    """`broke after a week` is durability, not setup."""
    types = _signal_types("Broke after a week of normal use.")
    assert "durability" in types
    assert "setup" not in types


def test_too_expensive_stays_price_not_trust() -> None:
    """Price complaint must not leak into trust."""
    types = _signal_types("This is way too expensive for what you get.")
    assert "price" in types
    assert "trust" not in types


def test_returned_it_does_not_silently_fire_support() -> None:
    """A bare `returned it` is a return-reason signal, not a
    support signal. Phase 11B.5 support patterns require an
    explicit seller/support actor."""
    types = _signal_types("I returned it after two days.")
    assert "return_reason" in types
    assert "support" not in types


def test_positive_trust_phrase_does_not_fire_distrust() -> None:
    """`I would trust this brand` is praise, NOT distrust. Our
    pattern requires don't/never/wouldn't before `trust`."""
    types = _signal_types(
        "I would trust this brand with my eyes closed — top quality.",
    )
    assert "trust" not in types


def test_setup_does_not_fire_on_generic_failure() -> None:
    """`doesn't work` alone fires the generic_objection rule (and
    durability if combined with `stopped working`), but must NOT
    fire setup unless paired with installation/configuration verbs."""
    types = _signal_types("This product just doesn't work at all.")
    assert "setup" not in types
    # but should fire objection (catch-all)
    assert "objection" in types


def test_called_them_once_does_not_fire_support() -> None:
    """The `repeated_contact_attempts` rule requires a count or
    explicit repeat marker. A single call must not classify as
    support."""
    types = _signal_types(
        "I called them once and got a friendly response right away.",
    )
    assert "support" not in types


def test_install_was_easy_does_not_fire_setup() -> None:
    """Positive setup mentions must not fire the setup rule."""
    types = _signal_types(
        "Installation was a breeze and configuration took five minutes.",
    )
    assert "setup" not in types


def test_not_a_knockoff_is_praise_not_trust_concern() -> None:
    """Bare-word `knockoff` / `counterfeit` / `fake` appearing in
    PRAISE contexts (`not a knockoff`, `not a cheap counterfeit`)
    must NOT fire the counterfeit-trust rule. Caught a real false
    positive on Industrial_and_Scientific + Health_and_Personal_Care
    runs during 11B.5 dry-runs."""
    for praise_text in [
        "Solid and heavy duty, not a cheap knockoff part. Would recommend.",
        "These Duracells are real — not a knockoff like some sellers ship.",
        "Heavy, solid build — definitely not a fake.",
        "Genuine product, not a counterfeit.",
    ]:
        types = _signal_types(praise_text, rating=5.0)
        assert "trust" not in types, (
            f"counterfeit rule false-fired on praise: {praise_text!r}"
        )


def test_real_counterfeit_claim_still_fires() -> None:
    """Recall check: legitimate counterfeit-claim phrasings must
    keep working after the false-positive guard."""
    for negative_text in [
        "Definitely a counterfeit, the logo is wrong.",
        "Appears to be a knockoff of the real brand.",
        "I got a fake — packaging is all wrong.",
        "They sent me a counterfeit instead of the real product.",
        "Obviously fake, the materials are nothing like the original.",
        "This is a complete knockoff — avoid.",
    ]:
        types = _signal_types(negative_text, rating=1.0)
        assert "trust" in types, (
            f"counterfeit rule missed a real complaint: {negative_text!r}"
        )


def test_real_amazon_phrasing_from_software_review() -> None:
    """End-to-end: the exact phrasing I observed in real Software
    reviews must now produce trust + (optionally) support signals."""
    text = (
        "I don't trust this company anymore. Their support is "
        "useless and I called them four times with no resolution. "
        "Feels like a scam at this point."
    )
    sigs = distill_review_signals(_rec(text, rating=1.0))
    types = {s.signal_type for s in sigs}
    assert "trust" in types
    assert "support" in types


def test_real_amazon_phrasing_from_industrial_review() -> None:
    text = (
        "Took me forever to install this glove dispenser. The "
        "screws supplied are wrong. Couldn't figure out how to "
        "mount it without modifying the bracket."
    )
    sigs = distill_review_signals(_rec(text, rating=2.0))
    types = {s.signal_type for s in sigs}
    assert "setup" in types


# ---------------------------------------------------------------------------
# 5. Themes are stable + descriptive for new rules
# ---------------------------------------------------------------------------


def test_setup_themes_are_present_and_specific() -> None:
    """Each new setup phrasing should produce one of the new
    setup themes so downstream rollups can break them out."""
    expected_themes = {
        "setup_time_excessive": "It took me forever to install this.",
        "couldnt_setup": "I couldn't get it to work.",
        "setup_nightmare": "Installation was a nightmare.",
        "setup_failed": "Activation failed three times.",
    }
    for theme, text in expected_themes.items():
        themes = _themes(text)
        assert theme in themes, (
            f"expected theme={theme!r} from {text!r}; got {themes}"
        )


def test_support_themes_are_present_and_specific() -> None:
    expected_themes = {
        "support_useless": "Support is useless.",
        "support_no_response": "No response from customer service.",
        "seller_uncooperative": "Seller refused to refund.",
        "repeated_contact_attempts": "I called them five times.",
        "warranty_or_return_denied": "Warranty was denied.",
    }
    for theme, text in expected_themes.items():
        themes = _themes(text)
        assert theme in themes, (
            f"expected theme={theme!r} from {text!r}; got {themes}"
        )


def test_trust_themes_are_present_and_specific() -> None:
    expected_themes = {
        "explicit_distrust": "I don't trust this brand.",
        "scam_suspicion": "This feels scammy.",
        "counterfeit_concern": "Definitely a counterfeit.",
        "misleading_listing": "Not as advertised.",
        "fake_reviews_suspicion": "Fake reviews everywhere.",
    }
    for theme, text in expected_themes.items():
        themes = _themes(text)
        assert theme in themes, (
            f"expected theme={theme!r} from {text!r}; got {themes}"
        )


# ---------------------------------------------------------------------------
# 6. Drift — no HTTP imports added by the patch
# ---------------------------------------------------------------------------


def test_distiller_still_has_no_http_imports() -> None:
    src = inspect.getsource(distiller_mod)
    forbidden = ("requests", "httpx", "aiohttp", "selenium",
                 "playwright", "scrapy", "bs4", "beautifulsoup4")
    for token in forbidden:
        pattern = re.compile(
            rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
            re.MULTILINE,
        )
        assert pattern.search(src) is None, (
            f"distiller imports forbidden module {token!r} after 11B.5"
        )


# ---------------------------------------------------------------------------
# 7. Per-review signal cap still respected after rule expansion
# ---------------------------------------------------------------------------


def test_one_signal_per_type_per_review_invariant_holds() -> None:
    """A review that triggers multiple setup rules (took forever +
    couldn't figure out + nightmare) must still emit only ONE
    setup signal — the first rule that fires wins."""
    text = (
        "It took me forever to install. I couldn't get it to work. "
        "The installation process was a nightmare. Activation failed."
    )
    sigs = distill_review_signals(_rec(text, rating=1.0))
    setup_sigs = [s for s in sigs if s.signal_type == "setup"]
    assert len(setup_sigs) == 1, (
        f"expected exactly 1 setup signal, got {len(setup_sigs)}"
    )


def test_one_signal_per_type_support_invariant() -> None:
    text = (
        "Support is useless. No help from the seller. Manufacturer "
        "wouldn't honor the warranty. Called them five times. "
        "Refund was denied."
    )
    sigs = distill_review_signals(_rec(text, rating=1.0))
    support_sigs = [s for s in sigs if s.signal_type == "support"]
    assert len(support_sigs) == 1


def test_one_signal_per_type_trust_invariant() -> None:
    text = (
        "I don't trust this brand. Feels scammy. Definitely a "
        "counterfeit. Not as advertised. Fake reviews everywhere."
    )
    sigs = distill_review_signals(_rec(text, rating=1.0))
    trust_sigs = [s for s in sigs if s.signal_type == "trust"]
    assert len(trust_sigs) == 1


# ---------------------------------------------------------------------------
# 8. Feature flag invariant (carry-forward from 11A)
# ---------------------------------------------------------------------------


def test_feature_flag_default_unchanged_by_11b_5() -> None:
    """ASSEMBLY_AMAZON_REVIEWS_ENABLED must remain False by default
    — the distiller patch must not flip any switch."""
    from assembly.config import Settings
    s = Settings()
    assert s.amazon_reviews_enabled is False
