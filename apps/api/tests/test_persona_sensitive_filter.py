"""Phase 8.2A — sensitive-attribute filter tests."""
from __future__ import annotations

import pytest

from assembly.pipeline.persona.sensitive_filter import (
    SensitiveAttributeRejected,
    SensitiveCategory,
    assert_no_sensitive_attributes,
    scan_sensitive_attributes,
)


@pytest.mark.parametrize(
    "text, category",
    [
        ("Contact me at jane.doe@example.com", SensitiveCategory.CONTACT_EMAIL),
        ("Call me at (415) 555-0199 anytime", SensitiveCategory.CONTACT_PHONE),
        ("Phone: +1 415 555 0199", SensitiveCategory.CONTACT_PHONE),
        ("ZIP 94110 in San Francisco", SensitiveCategory.ZIP_CODE),
        ("Lives at 1234 Sunset Boulevard", SensitiveCategory.PRECISE_ADDRESS),
        ("Identifies as Muslim", SensitiveCategory.RELIGION),
        ("Reportedly black", SensitiveCategory.RACE_ETHNICITY),
        ("Was diagnosed with diabetes", SensitiveCategory.HEALTH),
        ("On an H1B visa", SensitiveCategory.IMMIGRATION),
        ("Household income $120,000", SensitiveCategory.INCOME),
        ("Identifies as gay", SensitiveCategory.SEXUAL_ORIENTATION),
        ("Identifies as transgender", SensitiveCategory.GENDER_IDENTITY_INFERRED),
    ],
)
def test_sensitive_phrase_detected(text: str, category: SensitiveCategory) -> None:
    hits = scan_sensitive_attributes(text)
    assert any(h.category == category for h in hits), (
        f"expected {category!s} hit on {text!r}; got {[h.category.value for h in hits]}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "agents portraying mid-volume merchants tended to resist",
        "the society seemed cautious about brand control",
        "frustrated with plugin bloat in their Shopify stack",
        "willing to switch if final pricing control was retained",
        "the supplied starter price seemed reasonable to most agents",
    ],
)
def test_safe_commerce_text_passes(text: str) -> None:
    hits = scan_sensitive_attributes(text)
    assert hits == [], f"safe text falsely flagged: {[h.category.value for h in hits]}"


def test_assert_raises_on_sensitive_text() -> None:
    with pytest.raises(SensitiveAttributeRejected):
        assert_no_sensitive_attributes("contact at jane@example.com please")


def test_assert_passes_on_clean_text() -> None:
    # No raise → passes silently
    assert_no_sensitive_attributes("the agent seemed price-sensitive about $49 starter")
    assert_no_sensitive_attributes(None)
    assert_no_sensitive_attributes("")


def test_filter_imported_by_persona_validator() -> None:
    """The validator MUST import the sensitive filter. Asserted at the
    import boundary so future refactors can't silently drop the filter
    from the validator's path."""
    from assembly.pipeline.persona import sensitive_filter as sf
    from assembly.pipeline.persona import validator as val
    # validator references either scan_sensitive_attributes or
    # SensitiveAttributeRejected (or both) — both prove the wiring.
    src = open(val.__file__).read()
    assert "scan_sensitive_attributes" in src or "assert_no_sensitive_attributes" in src
    assert sf is not None
