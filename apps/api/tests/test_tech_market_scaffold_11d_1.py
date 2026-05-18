"""Phase 11D.1 — tech-market intelligence scaffold tests.

Operator's acceptance checklist:

  1. Providers OFF by default.
  2. Fixtures load only in tests/dev — fixture provider raises
     when the enable flag is False.
  3. Signals distill correctly across all 14 signal_types.
  4. buyer_type is assigned from venue / role hints.
  5. market_context is assigned from hint or content cues.
  6. No raw / large data committed — fixture corpus stays ≤ 50.
  7. Retriever short-circuits when ANY gate is off — no DB hit.
  8. RetrievedTechSignal carries no PII / raw fields.
  9. Snippet length capped at 240 chars.
 10. Drift: nobody in api/pipeline/orchestration imports the
     retriever yet (Phase 11D.1 is scaffold-only).
 11. Phase 11D.1 introduces no live HTTP imports.
 12. Closed enums match the DB CHECK constraints (model + migration).

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from assembly.sources.tech_market_provider import (
    BUYER_TYPES,
    DistilledTechSignal,
    FixtureTechMarketSignalProvider,
    InMemoryTechMarketSignalSource,
    MARKET_CONTEXTS,
    PRODUCT_CATEGORIES,
    ProviderDisabledError,
    RetrievedTechSignal,
    RuleBasedTechMarketDistiller,
    SENTIMENT_BUCKETS,
    SIGNAL_TYPES,
    TechMarketEvidencePackage,
    TechMarketRetrievalConfig,
    TechMarketSignalProviderConfig,
    TechMarketSignalRetriever,
    TechProductBriefShape,
    TechSignalRow,
    iter_phase_11d_1_fixtures,
    total_fixture_count,
)


# ---------------------------------------------------------------------------
# 1. Feature flags default False
# ---------------------------------------------------------------------------


def test_tech_market_settings_all_default_false() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.tech_market_signals_enabled is False
    assert s.tech_market_signals_runtime_enabled is False
    assert s.tech_market_signals_persona_injection_enabled is False


def test_tech_market_settings_have_documented_defaults() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.tech_market_signals_max_per_run == 80
    assert s.tech_market_signals_min_relevance == 0.20


def test_retrieval_config_from_settings_reads_all_fields() -> None:
    class _S:
        tech_market_signals_enabled = True
        tech_market_signals_runtime_enabled = True
        tech_market_signals_persona_injection_enabled = True
        tech_market_signals_max_per_run = 50
        tech_market_signals_min_relevance = 0.30

    cfg = TechMarketRetrievalConfig.from_settings(_S())
    assert cfg.fully_enabled is True
    assert cfg.persona_injection_enabled is True
    assert cfg.max_per_run == 50
    assert cfg.min_relevance == 0.30


# ---------------------------------------------------------------------------
# 2. Fixture provider gated behind enable flag
# ---------------------------------------------------------------------------


def test_fixture_provider_refuses_when_disabled() -> None:
    p = FixtureTechMarketSignalProvider(
        config=TechMarketSignalProviderConfig(enabled=False),
    )
    with pytest.raises(ProviderDisabledError):
        list(p.load_raw_records())
    with pytest.raises(ProviderDisabledError):
        p.distill()


def test_fixture_provider_runs_when_enabled() -> None:
    p = FixtureTechMarketSignalProvider(
        config=TechMarketSignalProviderConfig(enabled=True),
    )
    signals = p.distill()
    assert signals
    for s in signals:
        assert isinstance(s, DistilledTechSignal)


# ---------------------------------------------------------------------------
# 3. Distiller covers every operator signal_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    # 14 signal types — one canonical phrase per type.
    ("urgent — we need this yesterday", "pain_urgency"),
    ("we switched to a competing tool last month", "switching_objection"),
    ("the per-seat enterprise pricing is too expensive", "pricing_objection"),
    ("the privacy disclosure was sketchy", "trust_security_concern"),
    ("the webhook integration broke twice this week", "integration_friction"),
    ("setup took an hour and the tutorial was outdated", "onboarding_friction"),
    ("opened a support ticket and nobody replied", "support_complaint"),
    ("compared to the open-source alternative, it's clunky", "competitor_comparison"),
    ("I'd gladly pay $100/year for the pro tier", "willingness_to_pay"),
    ("nice-to-have but not mission critical", "nice_to_have_risk"),
    ("this is just a feature, big-co will easily replicate it", "feature_not_company_risk"),
    ("it slotted into our daily workflow perfectly", "workflow_fit"),
    ("the docs are wrong and this is not production-ready", "developer_skepticism"),
    ("procurement asked for a SOC 2 report and blocked us", "procurement_friction"),
])
def test_distiller_classifies_each_signal_type(
    text: str, expected: str,
) -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        text,
        source_provider="test_provider",
        product_category="ai_saas",
    )
    assert len(out) == 1
    assert out[0].signal_type == expected


def test_distiller_returns_empty_for_unmatched_text() -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "the weather is nice today and the cat is sleeping",
        source_provider="test_provider",
        product_category="ai_saas",
    )
    assert out == []


def test_distiller_returns_empty_for_blank_input() -> None:
    d = RuleBasedTechMarketDistiller()
    assert d.distill("", source_provider="x") == []
    assert d.distill("   \t\n  ", source_provider="x") == []


# ---------------------------------------------------------------------------
# 4. buyer_type assignment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected_buyer", [
    ("As a YC partner I see a lot of these tools as nice-to-have rather than mission critical",
     "investor"),
    ("I'm a cofounder and the procurement vendor approval was painful",
     "founder"),
    ("the workspace admin console kept failing during onboarding setup",
     "admin"),
    ("the SDK and webhook integration broke twice this week",
     "developer"),
    ("our director of operations signed the contract but procurement raised concerns",
     "buyer"),
    ("I use this every day in my daily workflow and the docs were written for an older version",
     "user"),
])
def test_distiller_assigns_buyer_type(
    text: str, expected_buyer: str,
) -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(text, source_provider="t", product_category="ai_saas")
    assert out, f"expected a signal for {text!r}"
    assert out[0].buyer_type == expected_buyer


def test_distiller_defaults_buyer_type_to_unknown() -> None:
    """A pain-urgency signal with no role cues lands as 'unknown'."""
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "the deadline is on fire and this is critical pain right now",
        source_provider="t", product_category="ai_saas",
    )
    assert out
    assert out[0].buyer_type == "unknown"


# ---------------------------------------------------------------------------
# 5. market_context assignment
# ---------------------------------------------------------------------------


def test_distiller_uses_market_context_hint_when_provided() -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "the integration broke twice this week",
        source_provider="t",
        product_category="ai_saas",
        market_context_hint="AI_tool",
    )
    assert out[0].market_context == "AI_tool"


@pytest.mark.parametrize("text, expected_context", [
    ("our LLM inference latency is critical pain and the deadline is on fire",
     "AI_tool"),
    ("the SDK and webhook integration with GitHub broke twice",
     "devtool"),
    ("our marketplace seller listing dashboard had a support ticket nobody replied to",
     "marketplace"),
    ("the B2B enterprise plan with SSO is too expensive for our finance team",
     "B2B"),
    ("the consumer app store onboarding tutorial was hard to figure out",
     "B2C"),
    ("as a prosumer power user I'd happily pay for the pro tier",
     "prosumer"),
])
def test_distiller_infers_market_context_from_content(
    text: str, expected_context: str,
) -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        text, source_provider="t", product_category="ai_saas",
    )
    assert out
    assert out[0].market_context == expected_context


def test_distiller_defaults_market_context_to_unknown() -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "the onboarding was rough — figured it out eventually",
        source_provider="t", product_category="ai_saas",
    )
    assert out
    assert out[0].market_context == "unknown"


# ---------------------------------------------------------------------------
# 6. Snippet cap + sentiment
# ---------------------------------------------------------------------------


def test_distiller_caps_snippet_at_240_chars() -> None:
    d = RuleBasedTechMarketDistiller()
    long_text = "urgent — " + ("x" * 1000)
    out = d.distill(long_text, source_provider="t", product_category="ai_saas")
    assert out
    assert len(out[0].short_snippet) <= 240
    assert out[0].short_snippet.endswith("…")


def test_distiller_normalizes_whitespace_in_snippet() -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "urgent\n\n  need this   yesterday\t\tcritical pain",
        source_provider="t", product_category="ai_saas",
    )
    assert out
    # No tabs, no double spaces, no leading/trailing whitespace.
    snip = out[0].short_snippet
    assert "\t" not in snip
    assert "  " not in snip
    assert snip == snip.strip()


def test_distiller_sets_willingness_to_pay_positive_sentiment() -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "I'd happily pay $50 for this — it's worth every dollar",
        source_provider="t", product_category="ai_saas",
    )
    assert out
    assert out[0].signal_type == "willingness_to_pay"
    assert out[0].sentiment_bucket == "positive"


def test_distiller_sets_objection_signals_negative_sentiment() -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "the per-seat enterprise pricing is too expensive — frustrated",
        source_provider="t", product_category="ai_saas",
    )
    assert out
    assert out[0].signal_type == "pricing_objection"
    assert out[0].sentiment_bucket == "negative"


# ---------------------------------------------------------------------------
# 7. Distiller drops PII metadata keys defensively
# ---------------------------------------------------------------------------


def test_distiller_strips_pii_metadata_keys() -> None:
    d = RuleBasedTechMarketDistiller()
    out = d.distill(
        "the per-seat enterprise pricing is too expensive",
        source_provider="t",
        product_category="ai_saas",
        metadata={
            "author_handle": "@bob123",
            "user_id": "u_xyz",
            "email": "bob@example.com",
            "session_id": "s_abc",
            "rating": 2,
            "helpful_votes": 5,
        },
    )
    assert out
    md = out[0].metadata
    assert "author_handle" not in md
    assert "user_id" not in md
    assert "email" not in md
    assert "session_id" not in md
    # Non-PII metadata survives.
    assert md.get("rating") == 2
    assert md.get("helpful_votes") == 5


# ---------------------------------------------------------------------------
# 8. Fixture corpus stays small + covers every category
# ---------------------------------------------------------------------------


def test_fixture_corpus_stays_under_50_records() -> None:
    assert total_fixture_count() <= 50


def test_fixture_corpus_covers_all_six_product_categories() -> None:
    # Every fixture's metadata declares its product_category — assert
    # we see all six controlled-vocabulary labels (minus 'unknown').
    seen: set[str] = set()
    for _text, _ctx, meta in iter_phase_11d_1_fixtures():
        pc = meta.get("_assembly_internal_product_category")
        if pc:
            seen.add(pc)
    expected = {
        "ai_saas", "browser_extension", "devtool_api",
        "b2b_workflow_saas", "consumer_mobile_app", "marketplace",
    }
    assert expected <= seen, (
        f"fixture corpus missing categories: {expected - seen}"
    )


def test_fixture_corpus_distills_to_at_least_one_per_category() -> None:
    p = FixtureTechMarketSignalProvider(
        config=TechMarketSignalProviderConfig(enabled=True),
    )
    signals = p.distill()
    by_cat: dict[str, int] = {}
    for s in signals:
        by_cat[s.product_category] = by_cat.get(s.product_category, 0) + 1
    for cat in (
        "ai_saas", "browser_extension", "devtool_api",
        "b2b_workflow_saas", "consumer_mobile_app", "marketplace",
    ):
        assert by_cat.get(cat, 0) >= 1, (
            f"no distilled signals for product_category={cat}"
        )


def test_fixture_corpus_distills_multiple_signal_types() -> None:
    p = FixtureTechMarketSignalProvider(
        config=TechMarketSignalProviderConfig(enabled=True),
    )
    signals = p.distill()
    types = {s.signal_type for s in signals}
    # We're not guaranteeing all 14 — but ≥ 6 distinct types should
    # show up given the operator's spec for fixture coverage.
    assert len(types) >= 6, (
        f"fixture corpus only exercises {len(types)} signal types: {types}"
    )


# ---------------------------------------------------------------------------
# 9. Retriever feature-flag short-circuit
# ---------------------------------------------------------------------------


def _row(
    *,
    signal_type: str = "pain_urgency",
    sentiment: str = "negative",
    product_category: str = "ai_saas",
    company: str | None = "Generic AI Co",
    competitor: str | None = None,
    buyer: str = "unknown",
    market: str = "B2B",
    snippet: str = "urgent — need this yesterday",
    rel: float | None = 0.5,
) -> TechSignalRow:
    return TechSignalRow(
        source_provider="test_provider",
        source_category=None,
        product_category=product_category,
        company_or_product=company,
        competitor_name=competitor,
        signal_type=signal_type,  # type: ignore[arg-type]
        sentiment_bucket=sentiment,  # type: ignore[arg-type]
        buyer_type=buyer,  # type: ignore[arg-type]
        market_context=market,  # type: ignore[arg-type]
        theme=None,
        short_snippet=snippet,
        evidence_url=None,
        source_timestamp=None,
        relevance_score=rel,
        metadata={},
    )


def test_retriever_returns_empty_when_flags_off() -> None:
    rows = [_row(), _row(snippet="another pain", signal_type="pricing_objection")]
    src = InMemoryTechMarketSignalSource(rows)
    for cfg in (
        TechMarketRetrievalConfig(enabled=False, runtime_enabled=False),
        TechMarketRetrievalConfig(enabled=True, runtime_enabled=False),
        TechMarketRetrievalConfig(enabled=False, runtime_enabled=True),
    ):
        r = TechMarketSignalRetriever(src, config=cfg)
        assert r.is_active is False
        assert asyncio.run(r.retrieve_by_product_category("ai_saas")) == []
        assert asyncio.run(r.retrieve_by_market_context("B2B")) == []
        assert asyncio.run(r.retrieve_by_competitor(["X"])) == []
        assert asyncio.run(r.retrieve_by_signal_types(["pain_urgency"])) == []
        pkg = asyncio.run(r.retrieve_for_product_brief(
            TechProductBriefShape(product_name="Acme"),
        ))
        assert isinstance(pkg, TechMarketEvidencePackage)
        assert pkg.attempted is False
        assert pkg.signals == []


def _enabled_cfg(**overrides) -> TechMarketRetrievalConfig:
    base = {
        "enabled": True,
        "runtime_enabled": True,
        "persona_injection_enabled": False,
        "max_per_run": 80,
        "min_relevance": 0.20,
    }
    base.update(overrides)
    return TechMarketRetrievalConfig(**base)


def test_retriever_queries_source_when_both_flags_on() -> None:
    rows = [_row(product_category="ai_saas"),
            _row(product_category="devtool_api", snippet="dev tool integration broke")]
    r = TechMarketSignalRetriever(
        InMemoryTechMarketSignalSource(rows),
        config=_enabled_cfg(),
    )
    out = asyncio.run(r.retrieve_by_product_category("ai_saas"))
    assert all(s.product_category == "ai_saas" for s in out)


def test_retriever_by_market_context_filters_correctly() -> None:
    rows = [
        _row(market="B2B", snippet="enterprise SSO setup"),
        _row(market="B2C", snippet="consumer onboarding"),
    ]
    r = TechMarketSignalRetriever(
        InMemoryTechMarketSignalSource(rows),
        config=_enabled_cfg(),
    )
    out = asyncio.run(r.retrieve_by_market_context("B2B"))
    assert {s.market_context for s in out} == {"B2B"}


def test_retriever_by_competitor_matches_brand_or_competitor() -> None:
    rows = [
        _row(company="Apollo Neuro",
             snippet="apollo neuro is fine but expensive"),
        _row(competitor="Apollo Neuro",
             snippet="we considered apollo neuro before switching"),
        _row(company="Generic AI Co", snippet="unrelated entry"),
    ]
    r = TechMarketSignalRetriever(
        InMemoryTechMarketSignalSource(rows),
        config=_enabled_cfg(),
    )
    out = asyncio.run(r.retrieve_by_competitor(["Apollo Neuro"]))
    assert len(out) == 2


def test_retrieve_for_product_brief_returns_package() -> None:
    rows = [
        _row(product_category="ai_saas",
             signal_type="pain_urgency",
             snippet="urgent — LLM integration broke and finance team is on fire"),
        _row(product_category="ai_saas",
             signal_type="pricing_objection",
             snippet="too expensive per-seat enterprise plan"),
    ]
    r = TechMarketSignalRetriever(
        InMemoryTechMarketSignalSource(rows),
        config=_enabled_cfg(),
    )
    pkg = asyncio.run(r.retrieve_for_product_brief(
        TechProductBriefShape(
            product_name="Acme AI",
            description="AI tool for developers",
            product_category_hint="AI SaaS tool",
            market_context_hint="AI_tool",
            competitors=("OpenAI",),
        ),
    ))
    assert pkg.attempted is True
    assert pkg.product_category_matched == "ai_saas"
    assert pkg.signals
    assert len(pkg.distribution) >= 1


def test_retrieve_for_product_brief_caps_at_max_per_run() -> None:
    rows = [
        _row(snippet=f"urgent unique {i}", rel=0.5)
        for i in range(20)
    ]
    r = TechMarketSignalRetriever(
        InMemoryTechMarketSignalSource(rows),
        config=_enabled_cfg(max_per_run=5),
    )
    pkg = asyncio.run(r.retrieve_for_product_brief(
        TechProductBriefShape(
            product_name="X",
            product_category_hint="ai saas",
        ),
    ))
    assert len(pkg.signals) <= 5


# ---------------------------------------------------------------------------
# 10. RetrievedTechSignal carries no forbidden fields
# ---------------------------------------------------------------------------


_FORBIDDEN_PERSONA_FIELDS = {
    "author_handle", "author_id", "author_email", "author_name",
    "user_id", "user_handle", "user_name", "email", "phone",
    "ip", "ip_address", "session_id", "row_id", "id",
    "created_at", "raw_text", "raw_body", "review_body",
    "image_url", "images",
}


def test_retrieved_tech_signal_has_no_forbidden_fields() -> None:
    fields = set(RetrievedTechSignal.__dataclass_fields__)
    leaked = fields & _FORBIDDEN_PERSONA_FIELDS
    assert leaked == set(), (
        f"RetrievedTechSignal exposes forbidden fields: {sorted(leaked)}"
    )


def test_retriever_returns_only_retrieved_tech_signals() -> None:
    rows = [_row(), _row(snippet="another snippet")]
    r = TechMarketSignalRetriever(
        InMemoryTechMarketSignalSource(rows),
        config=_enabled_cfg(),
    )
    out = asyncio.run(r.retrieve_by_product_category("ai_saas"))
    for s in out:
        assert isinstance(s, RetrievedTechSignal)
        # No internal-only attribute leakage.
        for f in _FORBIDDEN_PERSONA_FIELDS:
            assert not hasattr(s, f)


# ---------------------------------------------------------------------------
# 11. Drift: no production code wires the retriever yet
# ---------------------------------------------------------------------------


def test_no_production_module_imports_tech_market_retriever() -> None:
    """Phase 11D.1 was scaffold-only — no production wiring. Phase
    11D.9 wires the retriever into a SINGLE whitelisted file
    (`pipeline/tech_market_evidence_injector.py`) for audit-only
    use. Every other production file under `api/`, `pipeline/`, or
    `orchestration/` must still refrain from importing the
    retriever directly. New files needing tech-market access must
    be added to this whitelist explicitly, with operator approval."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    live_dirs = [
        api_root / "api",
        api_root / "pipeline",
        api_root / "orchestration",
    ]
    whitelist = {
        # Phase 11D.9 — sole authorized audit-only injector. Imports
        # TechMarketSignalRetriever + PostgresTechMarketSignalSource
        # to build the `technical.tech_market_signals` audit dict.
        # Triple-flag-gated. Never touches persona prompts.
        api_root / "pipeline" / "tech_market_evidence_injector.py",
    }
    forbidden_tokens = (
        "TechMarketSignalRetriever",
        "tech_market_provider.retrieval",
    )
    for d in live_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if path in whitelist:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                assert token not in text, (
                    f"{path} imports {token!r} — tech-market "
                    f"retrieval must go through the whitelisted "
                    f"pipeline/tech_market_evidence_injector.py"
                )


# ---------------------------------------------------------------------------
# 12. Drift: no live HTTP imports in the tech_market_provider module
# ---------------------------------------------------------------------------


def test_tech_market_provider_has_no_http_imports() -> None:
    pkg_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "sources" / "tech_market_provider"
    )
    forbidden = (
        "requests", "httpx", "aiohttp", "selenium",
        "playwright", "scrapy", "bs4", "beautifulsoup4",
        "urllib.request",
    )
    for path in pkg_dir.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for token in forbidden:
            pat = re.compile(
                rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
                re.MULTILINE,
            )
            assert pat.search(src) is None, (
                f"{path.name} imports forbidden module {token!r}"
            )


# ---------------------------------------------------------------------------
# 13. Closed enums match model + migration
# ---------------------------------------------------------------------------


def test_signal_types_match_model_definition() -> None:
    from assembly.models.tech_market_signal import (
        SIGNAL_TYPES as MODEL_SIGNAL_TYPES,
    )
    assert set(SIGNAL_TYPES) == set(MODEL_SIGNAL_TYPES)


def test_sentiment_buckets_match_model_definition() -> None:
    from assembly.models.tech_market_signal import (
        SENTIMENT_BUCKETS as MODEL_BUCKETS,
    )
    assert set(SENTIMENT_BUCKETS) == set(MODEL_BUCKETS)


def test_buyer_types_match_model_definition() -> None:
    from assembly.models.tech_market_signal import (
        BUYER_TYPES as MODEL_BUYER_TYPES,
    )
    assert set(BUYER_TYPES) == set(MODEL_BUYER_TYPES)


def test_market_contexts_match_model_definition() -> None:
    from assembly.models.tech_market_signal import (
        MARKET_CONTEXTS as MODEL_MARKET_CONTEXTS,
    )
    assert set(MARKET_CONTEXTS) == set(MODEL_MARKET_CONTEXTS)


def test_enums_match_alembic_migration() -> None:
    """Closed enums in `signal_types` must each appear in AT LEAST
    ONE alembic migration that declares the CHECK constraint values.
    Phase 11D.1's migration (0015) shipped the original 14 signal
    types; Phase 11D.5's migration (0016) widens the constraint to
    add `feature_inquiry`. We scan every migration so new additions
    chain cleanly without breaking this test.

    A drift here would surface as a CHECK violation the first time
    we tried to write."""
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions"
    )
    all_migration_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in migrations_dir.glob("*.py")
    )
    for st in SIGNAL_TYPES:
        assert f'"{st}"' in all_migration_text or f"'{st}'" in all_migration_text, (
            f"no migration declares signal_type {st!r}"
        )
    # buyer_type + market_context values still only live in the
    # original 11D.1 migration (no subsequent additions), so we
    # check that one explicitly to ensure they didn't drift.
    text_0015 = (
        migrations_dir
        / "20260517_0015_phase_11_d_1_tech_market_signal.py"
    ).read_text(encoding="utf-8")
    for bt in BUYER_TYPES:
        assert f'"{bt}"' in text_0015 or f"'{bt}'" in text_0015, (
            f"migration missing buyer_type {bt!r}"
        )
    for mc in MARKET_CONTEXTS:
        assert f'"{mc}"' in text_0015 or f"'{mc}'" in text_0015, (
            f"migration missing market_context {mc!r}"
        )


def test_product_categories_constant_has_expected_labels() -> None:
    expected = {
        "ai_saas", "browser_extension", "devtool_api",
        "b2b_workflow_saas", "consumer_mobile_app", "marketplace",
        "unknown",
    }
    assert expected <= set(PRODUCT_CATEGORIES)


# ---------------------------------------------------------------------------
# 14. Model is registered on Base.metadata
# ---------------------------------------------------------------------------


def test_tech_market_signal_model_registered_on_base() -> None:
    from assembly.db import Base
    from assembly.models.tech_market_signal import TechMarketSignal
    assert "tech_market_signal" in Base.metadata.tables
    assert (
        TechMarketSignal.__tablename__
        in Base.metadata.tables
    )


# ---------------------------------------------------------------------------
# 15. Provider Protocol is satisfied by fixture provider
# ---------------------------------------------------------------------------


def test_fixture_provider_satisfies_protocol_shape() -> None:
    from assembly.sources.tech_market_provider import (
        TechMarketSignalProvider,
    )
    p = FixtureTechMarketSignalProvider(
        config=TechMarketSignalProviderConfig(enabled=True),
    )
    # Structural check — fixture provider exposes `name`,
    # `load_raw_records`, `distill`.
    assert hasattr(p, "name")
    assert callable(getattr(p, "load_raw_records"))
    assert callable(getattr(p, "distill"))
    # And the fixture path mentions the expected provider name in
    # every distilled signal.
    for s in p.distill():
        assert s.source_provider


# ---------------------------------------------------------------------------
# 16. No source enters persona prompts (yet)
# ---------------------------------------------------------------------------


def test_persona_pipeline_does_not_import_tech_market_provider() -> None:
    """Phase 11D.1 was scaffold-only. Phase 11D.9 wires a SINGLE
    audit-only injector (`pipeline/tech_market_evidence_injector.py`)
    that imports the provider — every other production file under
    `pipeline/` or `orchestration/` must remain unwired so persona
    prompts can never silently grow a tech-market dependency."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    live_dirs = [
        api_root / "pipeline",
        api_root / "orchestration",
    ]
    whitelist = {
        api_root / "pipeline" / "tech_market_evidence_injector.py",
    }
    for d in live_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if path in whitelist:
                continue
            text = path.read_text(encoding="utf-8")
            assert "tech_market_provider" not in text, (
                f"{path} imports tech_market_provider — must go "
                f"through the whitelisted "
                f"pipeline/tech_market_evidence_injector.py"
            )
