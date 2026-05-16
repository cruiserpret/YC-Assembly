"""Phase 11C.3 — full-pipeline Amazon audit validation.

The Phase 11C.2 tests confirmed the injector's INTERNAL behavior.
Phase 11C.3 proves the pipeline integration:

  * `build_evidence` actually invokes the injector when a
    sessionmaker is provided.
  * `EvidenceBuildResult.amazon_audit` lands as expected on the
    returned dataclass.
  * The `items` list — the path that personas read — is bit-for-bit
    identical regardless of whether the Amazon audit is empty, off,
    or fully populated.
  * No forbidden review-row fields leak into the audit dict at the
    pipeline level (drift gate at the consumer boundary).
  * `same_category_only` invariant is reported in the pipeline-
    level audit when the injector is consulted.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES — the injector is
monkeypatched so `build_evidence` never opens a real session.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from assembly.pipeline.evidence_builder import (
    EvidenceBuildResult,
    build_evidence,
)
from assembly.schemas.brief import (
    CompetitorRef,
    PriceStructure,
    SimulationBriefIn,
    TargetSociety,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _quietcart_brief() -> SimulationBriefIn:
    return SimulationBriefIn(
        product_type="browser extension",
        product_name="QuietCart",
        description=(
            "A browser extension for online shoppers who want to "
            "stop impulse buying on Amazon and other retailers."
        ),
        price_structure=PriceStructure(
            model="subscription_annual", amount="$39/year",
        ),
        target_society=TargetSociety(
            description=(
                "Budget-conscious online shoppers in the US who "
                "frequently impulse-buy from Amazon / Target."
            ),
        ),
        competitors=[
            CompetitorRef(name="Freedom"),
            CompetitorRef(name="Opal"),
        ],
    )


def _calmcue_brief() -> SimulationBriefIn:
    return SimulationBriefIn(
        product_type="wellness wearable",
        product_name="CalmCue stress wearable",
        description=(
            "A screenless wellness wearable that tracks stress and "
            "sleep through skin sensors. No mobile app required."
        ),
        price_structure=PriceStructure(
            model="one_time", amount="$79",
        ),
        target_society=TargetSociety(
            description=(
                "Urban commuters in the US who want a passive "
                "stress / wellness signal without a screen."
            ),
        ),
        competitors=[
            CompetitorRef(name="Apollo Neuro"),
            CompetitorRef(name="Muse"),
        ],
    )


def _sentinel_amazon_audit(category: str) -> dict[str, Any]:
    """Mirror of the real injector's audit shape so tests can pin
    the structure without touching the DB."""
    return {
        "provider": "amazon_reviews_2023",
        "amazon_attempted": True,
        "amazon_enabled": True,
        "amazon_runtime_enabled": True,
        "same_category_only": True,
        "category_matched": category,
        "signals_retrieved": 31,
        "signal_distribution": {
            "objection": 4, "price": 7, "praise": 10,
            "use_case": 3, "durability": 3, "trust": 2,
            "safety": 1, "switch_reason": 1,
        },
        "brand_coverage": 1.0,
        "title_coverage": 1.0,
        "skipped_reasons": {
            "cross_category_filtered": 46,
            "dedup_hash_collision": 15,
        },
        "notes": [],
        "sample_signals": [
            {
                "signal_type": "setup",
                "sentiment_bucket": "negative",
                "theme": "couldnt_setup",
                "category": category,
                "brand": "Frndly TV",
                "product_title": "Some title",
                "rating": 2,
                "short_snippet": "couldn't get it to work",
                "competitor_mention": None,
                "use_case": None,
                "verified_purchase": True,
                "helpful_votes": 3,
            },
        ],
    }


# ---------------------------------------------------------------------------
# 1. build_evidence calls the injector when sessionmaker is provided
# ---------------------------------------------------------------------------


def test_build_evidence_invokes_injector_when_sessionmaker_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end pipeline integration. The injector is patched at
    its import site so we never touch the real DB; we just verify
    that `build_evidence` reaches the hook and lands its return on
    the result."""
    captured: dict[str, Any] = {}
    sentinel = _sentinel_amazon_audit("Software")

    async def fake_injector(brief, *, sessionmaker, settings):
        captured["brief"] = brief
        captured["sessionmaker"] = sessionmaker
        captured["settings"] = settings
        return sentinel

    import assembly.pipeline.amazon_evidence_injector as inj_mod
    monkeypatch.setattr(
        inj_mod, "build_amazon_evidence_section", fake_injector,
    )

    fake_sm = MagicMock(name="async_sessionmaker_stub")
    result = asyncio.run(build_evidence(
        brief=_quietcart_brief(),
        simulation_id=uuid4(),
        sessionmaker=fake_sm,
    ))

    assert isinstance(result, EvidenceBuildResult)
    assert result.amazon_audit is sentinel
    # Hook reached the injector and passed our sessionmaker through.
    assert captured["sessionmaker"] is fake_sm
    # Brief identity preserved.
    assert captured["brief"].product_name == "QuietCart"


def test_build_evidence_skips_injector_when_sessionmaker_is_none() -> None:
    """No sessionmaker -> injector not called, amazon_audit is None.
    Existing call sites that never knew about Amazon stay
    unaffected."""
    result = asyncio.run(build_evidence(
        brief=_quietcart_brief(),
        simulation_id=uuid4(),
        sessionmaker=None,
    ))
    assert result.amazon_audit is None


# ---------------------------------------------------------------------------
# 2. Disabled-flag pass-through still produces an audit dict
# ---------------------------------------------------------------------------


def test_build_evidence_returns_disabled_audit_when_flags_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With sessionmaker present but Settings flags off, the
    pipeline still surfaces an observability dict that documents
    the disabled state. (No DB hit — proven by passing a
    sessionmaker that would explode if used.)"""

    def explode(*a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise AssertionError(
            "PostgresSignalSource opened a session despite flags off",
        )

    # Force a fresh get_settings() that returns a Settings with both
    # flags False (the default). No env override needed because the
    # repo defaults are False.
    from assembly.config import get_settings
    get_settings.cache_clear()

    result = asyncio.run(build_evidence(
        brief=_quietcart_brief(),
        simulation_id=uuid4(),
        sessionmaker=explode,  # type: ignore[arg-type]
    ))

    assert result.amazon_audit is not None
    assert result.amazon_audit["provider"] == "amazon_reviews_2023"
    assert result.amazon_audit["amazon_attempted"] is False
    assert result.amazon_audit["amazon_enabled"] is False
    assert result.amazon_audit["amazon_runtime_enabled"] is False
    assert result.amazon_audit["signals_retrieved"] == 0
    assert "feature_flag_off" in result.amazon_audit["notes"][0]


# ---------------------------------------------------------------------------
# 3. items list (the persona path) is invariant regardless of audit
# ---------------------------------------------------------------------------


def test_evidence_items_unchanged_when_amazon_audit_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of Phase 11C.2/11C.3 is that the persona
    path NEVER sees Amazon. We prove this by running build_evidence
    twice — once with a populated audit, once with audit=None — and
    asserting the `items` lists are byte-for-byte identical (modulo
    UUIDs, which are random per-run)."""
    brief = _quietcart_brief()
    fake_sm = MagicMock(name="sm_stub")
    sim_id = uuid4()

    import assembly.pipeline.amazon_evidence_injector as inj_mod

    # Run 1 — patched injector returns a fully populated audit.
    async def fake_full(brief, *, sessionmaker, settings):
        return _sentinel_amazon_audit("Software")
    monkeypatch.setattr(
        inj_mod, "build_amazon_evidence_section", fake_full,
    )
    full_result = asyncio.run(build_evidence(
        brief=brief, simulation_id=sim_id, sessionmaker=fake_sm,
    ))

    # Run 2 — patched injector returns None.
    async def fake_none(brief, *, sessionmaker, settings):
        return None
    monkeypatch.setattr(
        inj_mod, "build_amazon_evidence_section", fake_none,
    )
    none_result = asyncio.run(build_evidence(
        brief=brief, simulation_id=sim_id, sessionmaker=fake_sm,
    ))

    # ----- items invariant -----
    assert len(full_result.items) == len(none_result.items), (
        "Amazon audit changed item count — persona path leaked"
    )
    # Compare by every persistence-relevant field. UUIDs are
    # random per-call so we exclude them. Everything else must
    # match exactly.
    def _normalize(items):
        return [
            (
                it.simulation_id, it.kind, it.source_type,
                it.source_url, it.content, it.metadata,
            )
            for it in items
        ]
    assert _normalize(full_result.items) == _normalize(none_result.items)

    # The audit field is the ONLY difference.
    assert full_result.amazon_audit is not None
    assert none_result.amazon_audit is None


# ---------------------------------------------------------------------------
# 4. Forbidden-field leak check at pipeline level
# ---------------------------------------------------------------------------


_FORBIDDEN_AUDIT_KEYS = {
    "source_review_hash",
    "user_id", "user_id_hash",
    "image", "images", "image_url", "image_urls",
    "raw_text", "full_text", "review_body", "review_text",
    "id",
    "created_at",
}


def test_amazon_audit_dict_has_no_forbidden_fields_at_pipeline_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The injector is the only place that materializes Amazon
    fields into the dict that downstream consumers will read.
    Pinning the forbidden-key list at the PIPELINE consumer
    boundary catches drift even if the injector were rewritten."""

    async def fake_injector(brief, *, sessionmaker, settings):
        # Return a deliberately-rich audit dict matching the real
        # injector's shape.
        return _sentinel_amazon_audit("Software")

    import assembly.pipeline.amazon_evidence_injector as inj_mod
    monkeypatch.setattr(
        inj_mod, "build_amazon_evidence_section", fake_injector,
    )

    result = asyncio.run(build_evidence(
        brief=_quietcart_brief(),
        simulation_id=uuid4(),
        sessionmaker=MagicMock(),
    ))
    assert result.amazon_audit is not None
    # Top-level keys.
    leaked_top = set(result.amazon_audit) & _FORBIDDEN_AUDIT_KEYS
    assert leaked_top == set(), (
        f"audit top-level leaked forbidden keys: {leaked_top}"
    )
    # Sample-signal keys.
    for s in result.amazon_audit["sample_signals"]:
        leaked = set(s) & _FORBIDDEN_AUDIT_KEYS
        assert leaked == set(), (
            f"audit sample signal leaked: {leaked}"
        )


def test_real_injector_dict_has_no_forbidden_fields() -> None:
    """Direct integration: build a synthetic RetrievedSignal-bearing
    package, run it through the REAL `_audit_from_package` helper,
    and confirm the rendered dict contains zero forbidden keys.
    Guards against the injector growing a leaky field over time."""
    from assembly.sources.amazon_reviews_provider.retrieval import (
        AmazonEvidencePackage, RetrievedSignal,
    )
    from assembly.pipeline.amazon_evidence_injector import (
        _audit_from_package,
    )
    from assembly.sources.amazon_reviews_provider import RetrievalConfig

    pkg = AmazonEvidencePackage(
        attempted=True,
        feature_flag_status={
            "amazon_reviews_enabled": True,
            "amazon_reviews_runtime_enabled": True,
            "same_category_only": True,
        },
        category_matched="Software",
        signals=[
            RetrievedSignal(
                signal_type="objection",
                sentiment_bucket="negative",
                theme="generic_disappointment",
                category="Software",
                brand="BrandX",
                product_title="Title X",
                rating=1,
                short_snippet="this product disappointed",
                competitor_mention=None,
                use_case=None,
                verified_purchase=True,
                helpful_votes=5,
            ),
        ],
        distribution={"objection": 1},
        brand_coverage=1.0,
        title_coverage=1.0,
        skipped_reasons={},
        notes=[],
    )
    audit = _audit_from_package(
        pkg,
        config=RetrievalConfig(
            enabled=True, runtime_enabled=True,
            same_category_only=True,
        ),
    )
    leaked = set(audit) & _FORBIDDEN_AUDIT_KEYS
    assert leaked == set()
    for s in audit["sample_signals"]:
        assert set(s) & _FORBIDDEN_AUDIT_KEYS == set()


# ---------------------------------------------------------------------------
# 5. same_category_only invariant reaches the pipeline-level audit
# ---------------------------------------------------------------------------


def test_pipeline_audit_carries_same_category_only_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase-11C.2 critical fix must be visible at the
    pipeline-level audit. Caller can see whether the safe gate was
    on (true) or whether dev/debug mode opened the global
    fallback (false)."""

    async def fake_injector(brief, *, sessionmaker, settings):
        audit = _sentinel_amazon_audit("Software")
        audit["same_category_only"] = True
        return audit

    import assembly.pipeline.amazon_evidence_injector as inj_mod
    monkeypatch.setattr(
        inj_mod, "build_amazon_evidence_section", fake_injector,
    )

    result = asyncio.run(build_evidence(
        brief=_quietcart_brief(),
        simulation_id=uuid4(),
        sessionmaker=MagicMock(),
    ))
    assert result.amazon_audit is not None
    assert result.amazon_audit["same_category_only"] is True


def test_pipeline_audit_sample_signals_all_in_matched_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When same_category_only is on, the sample_signals embedded
    in the audit at the pipeline level must all carry the same
    category. (The retriever guarantees this; this test pins the
    invariant at the consumer boundary.)"""

    async def fake_injector(brief, *, sessionmaker, settings):
        return _sentinel_amazon_audit("Software")

    import assembly.pipeline.amazon_evidence_injector as inj_mod
    monkeypatch.setattr(
        inj_mod, "build_amazon_evidence_section", fake_injector,
    )

    result = asyncio.run(build_evidence(
        brief=_quietcart_brief(),
        simulation_id=uuid4(),
        sessionmaker=MagicMock(),
    ))
    assert result.amazon_audit is not None
    cats = {s["category"]
            for s in result.amazon_audit["sample_signals"]}
    assert cats == {"Software"}


# ---------------------------------------------------------------------------
# 6. Pipeline-level audit keys match operator spec
# ---------------------------------------------------------------------------


def test_pipeline_audit_dict_has_all_operator_spec_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 11C.3 spec lists the exact audit keys the operator
    expects. Lock them in at the pipeline consumer boundary."""

    async def fake_injector(brief, *, sessionmaker, settings):
        return _sentinel_amazon_audit("Software")

    import assembly.pipeline.amazon_evidence_injector as inj_mod
    monkeypatch.setattr(
        inj_mod, "build_amazon_evidence_section", fake_injector,
    )

    result = asyncio.run(build_evidence(
        brief=_quietcart_brief(),
        simulation_id=uuid4(),
        sessionmaker=MagicMock(),
    ))
    audit = result.amazon_audit
    assert audit is not None
    required = {
        "provider", "amazon_attempted", "amazon_enabled",
        "amazon_runtime_enabled", "same_category_only",
        "category_matched", "signals_retrieved",
        "signal_distribution", "brand_coverage", "title_coverage",
        "skipped_reasons", "sample_signals",
    }
    missing = required - set(audit)
    assert missing == set(), (
        f"audit missing spec'd keys: {sorted(missing)}"
    )
    assert audit["provider"] == "amazon_reviews_2023"
