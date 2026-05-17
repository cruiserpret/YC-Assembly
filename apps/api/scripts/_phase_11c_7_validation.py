"""Phase 11C.7 — local validation harness (NOT for production).

Compares 5 retrieval modes on two reference briefs (QuietCart and
CalmCue) using a synthetic-realistic in-memory corpus. The corpus
is designed to mirror real McAuley shape: on-topic rows mixed with
genuine category-level noise (games inside Software, sponges /
bottles inside Health_and_Personal_Care). This is the validation
harness the operator asked for in the Phase-11C.7 spec.

Modes:
  A — No Amazon persona injection (baseline).
  B — 11C.5 category-only retrieval, no relevance filter.
  C — 11C.6 category retrieval + relevance filter @ 0.20.
  D — 11C.7 expanded retrieval + relevance filter @ 0.20.
  E — 11C.7 expanded retrieval + relevance filter @ 0.05.

Output:
  Per-brief comparison table with candidate-pool counts, kept count,
  final-snippet count, and example snippets from each kept bucket.

This script is NOT wired into the production pipeline. Run it with:

    uv run python scripts/_phase_11c_7_validation.py

Amazon flags do not need to be set — the script drives the
retriever directly with an in-memory source. No production DB
access. No live Amazon scraping.
"""
from __future__ import annotations

import asyncio
from typing import Any

from assembly.sources.amazon_reviews_provider import (
    AmazonSignalRetriever,
    InMemorySignalSource,
    ProductBriefShape,
    RetrievalConfig,
    SignalRow,
)
from assembly.sources.amazon_reviews_provider.relevance import (
    filter_signals_by_relevance,
)
from assembly.pipeline.amazon_evidence_injector import (
    _balanced_prompt_snippets,
    _format_prompt_snippet,
)


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------


QUIETCART = ProductBriefShape(
    product_name="QuietCart",
    description=(
        "A browser extension for online shoppers who want to stop "
        "impulse buying on Amazon and other shopping sites. Adds a "
        "delay and privacy-focused review of carts before checkout."
    ),
    category_hint="browser extension",
    competitors=("Freedom", "Opal", "Rocket Money"),
)

CALMCUE = ProductBriefShape(
    product_name="CalmCue stress wearable",
    description=(
        "A screenless wellness wearable that tracks stress and sleep "
        "via passive skin sensors. No mobile app required."
    ),
    category_hint="wellness wearable",
    competitors=("Apollo Neuro", "Muse", "Whoop"),
)


# ---------------------------------------------------------------------------
# Synthetic-realistic corpora
# ---------------------------------------------------------------------------


def _row(
    *,
    signal_type: str,
    category: str,
    brand: str | None,
    title: str,
    snippet: str,
    theme: str = "",
    sentiment: str = "negative",
    rating: int | None = 2,
    verified: bool = True,
    helpful: int = 3,
    competitor: str | None = None,
    use_case: str | None = None,
    review_hash: str | None = None,
) -> SignalRow:
    if review_hash is None:
        review_hash = f"h_{abs(hash((title, snippet, brand))) % 10_000_000}"
    return SignalRow(
        signal_type=signal_type,
        sentiment_bucket=sentiment,
        theme=theme or signal_type,
        category=category,
        brand=brand,
        product_title=title,
        asin="B" + str(abs(hash(title)) % 1_000_000_000),
        parent_asin="B" + str(abs(hash(title)) % 1_000_000_000),
        rating=rating,
        verified_purchase=verified,
        helpful_votes=helpful,
        short_snippet=snippet,
        competitor_mention=competitor,
        use_case=use_case,
        source_review_hash=review_hash,
    )


def software_corpus() -> list[SignalRow]:
    """Synthetic Software category mirroring McAuley distribution:
    ~25% genuinely browser-extension shaped, ~75% noise (games,
    antivirus, OS software). 80 rows total."""
    rows: list[SignalRow] = []

    on_topic = [
        # browser-extension objections
        ("objection",
         "QuietBrowser Cart Saver Chrome extension v3",
         "tried installing the browser extension on Chrome and the "
         "permissions dialog never finished loading for me as an online shopper"),
        ("objection",
         "ShopDelay Privacy Browser Extension",
         "Firefox version of this browser extension keeps disabling "
         "itself every time I close my impulse-buying shopping tabs"),
        ("objection",
         "CheckoutPause browser extension for shoppers",
         "extension worked on desktop but the mobile browser app does "
         "not actually block any shopping sites for impulse purchases"),
        ("objection",
         "MindfulCart Chrome browser extension",
         "uninstalled because the browser extension blocked legitimate "
         "checkout pages on Amazon that I needed for routine groceries"),
        # browser-extension trust
        ("trust",
         "PrivacyShield browser extension for shoppers",
         "asks for full access to every shopping page I visit which "
         "feels too invasive for a privacy-focused browser extension"),
        ("trust",
         "SafeBuy browser extension privacy edition",
         "extension's privacy policy is unclear about whether it sells "
         "my checkout history to advertisers as an online shopper"),
        ("trust",
         "CartGuard browser extension premium",
         "had to give credit card info just to enable the basic "
         "impulse-blocking shopping feature which felt scammy to me"),
        ("trust",
         "SecureCart browser extension for online shoppers",
         "the browser extension flagged my own bank login as a phishing "
         "shopping site which makes me question its trust signals"),
        # setup
        ("setup",
         "OnboardEasy Chrome browser extension v2",
         "took me an hour to figure out which browser extension button "
         "actually enables the impulse-buying blocker for shopping"),
        ("setup",
         "QuickSetup browser extension for shoppers",
         "the setup wizard for this browser extension never asked me "
         "which shopping sites I wanted to block for impulse control"),
        ("setup",
         "ConfigCart browser extension for online shoppers",
         "extension installed cleanly but no obvious way to whitelist "
         "the online shopping sites I actually want to visit normally"),
        ("setup",
         "HelpfulCart browser extension privacy",
         "documentation for setting up the browser extension impulse "
         "blocker is hidden behind a paywalled support portal page"),
        # price
        ("price",
         "BudgetGuard browser extension monthly",
         "five dollars a month feels steep for a browser extension that "
         "only blocks impulse buying on a handful of shopping sites"),
        ("price",
         "FreeShield browser extension for shoppers",
         "the free tier of this browser extension only lets you block "
         "two shopping sites which is not enough for online shoppers"),
        ("price",
         "AutoBill browser extension annual",
         "had no idea the browser extension subscription would jump to "
         "yearly billing after the first month of impulse blocking"),
        ("price",
         "RefundCart browser extension cancellation",
         "cancelling the subscription left the browser extension still "
         "installed and silently watching every shopping checkout page"),
        # durability
        ("durability",
         "LongHaul browser extension for shoppers",
         "after three months the browser extension stopped updating its "
         "shopping site blocklist and now ignores most checkout pages"),
        ("durability",
         "StableBlocker browser extension",
         "browser extension breaks on every Chrome update which makes "
         "the impulse-buying shopping site blocker unreliable for me"),
    ]
    for sig_type, title, snippet in on_topic:
        rows.append(_row(
            signal_type=sig_type,
            category="Software",
            brand=title.split()[0] + "Co",
            title=title,
            snippet=snippet,
            rating=2,
            helpful=5,
        ))

    # Freedom-branded (competitor-anchored).
    freedom_snippets = [
        "switched from this browser extension to Freedom because Freedom actually blocks shopping sites during work hours of the day",
        "Freedom blocked all my shopping sites including amazon, the browser extension never reliably caught impulse purchases for me",
        "after a month I went back to Freedom because their browser extension covers more shopping sites with stricter blocking rules",
    ]
    for i, sn in enumerate(freedom_snippets):
        rows.append(_row(
            signal_type="switch_reason",
            category="Software",
            brand="Freedom",
            title=f"Freedom Distraction Blocker App {i}",
            snippet=sn,
            rating=4,
            helpful=8,
            theme=f"switched_to_freedom_{i}",
        ))

    # Noise: games, antivirus, OS software (~60 rows).
    game_titles = [
        "Bikini Bottom Bash Adventure",
        "Pirate Cove Treasure Hunters",
        "Galactic Dragon Tactics Online",
        "Lunar Farm Builder Classic Edition",
        "Mystery Manor Hidden Object Saga",
    ]
    for i, t in enumerate(game_titles):
        for j in range(4):
            sn = (
                f"this game has issue {j} with level {i} progression — "
                f"crashes during the boss fight on chapter {i*2+j}"
            )
            rows.append(_row(
                signal_type="objection",
                category="Software",
                brand=f"GameStudio{i}",
                title=f"{t} v{j}",
                snippet=sn,
                rating=1,
                helpful=1,
                theme="generic_disappointment",
                review_hash=f"h_game_{i}_{j}",
            ))
    av_titles = [
        "AntiThreat Premium Antivirus",
        "SafeNet Cloud Security Suite",
        "GuardianShield Total Protection",
        "VirusDefender Family Pack",
        "ThreatLock Enterprise Edition",
    ]
    for i, t in enumerate(av_titles):
        for j in range(4):
            sn = (
                f"this antivirus version {j} slows down boot time on my "
                f"computer model {i} significantly when running daily scans"
            )
            rows.append(_row(
                signal_type="objection",
                category="Software",
                brand=f"AntivirusCo{i}",
                title=f"{t} {j}",
                snippet=sn,
                rating=2,
                helpful=2,
                theme="generic_disappointment",
                review_hash=f"h_av_{i}_{j}",
            ))
    os_titles = [
        "Office Productivity Suite Home",
        "Photo Editor Pro Plus",
        "PDF Converter Premium License",
        "Database Manager Lite",
    ]
    for i, t in enumerate(os_titles):
        for j in range(4):
            sn = (
                f"this office software version {j} has user-interface issue "
                f"{i} that makes daily document editing tedious for me"
            )
            rows.append(_row(
                signal_type="praise",
                category="Software",
                brand=f"OfficeCo{i}",
                title=f"{t} {j}",
                snippet=sn,
                rating=4,
                helpful=0,
                theme="general_praise",
                review_hash=f"h_os_{i}_{j}",
                sentiment="positive",
            ))
    return rows


def wellness_corpus() -> list[SignalRow]:
    """Synthetic Health_and_Personal_Care category mirroring McAuley
    distribution: ~25% wearable-shaped, ~75% noise (sponges, bottles,
    supplements). 80 rows total."""
    rows: list[SignalRow] = []

    on_topic = [
        # durability — wearable failure
        ("durability",
         "Heart-rate stress sensor wristband v3",
         "wristband stress sensor stopped tracking my heart rate after "
         "about six weeks of normal sleep wearable use for me"),
        ("durability",
         "Wellness wearable haptic wristband Pro",
         "haptic feedback motor on this wearable wristband stress device "
         "failed during the first month of sleep tracking for me"),
        ("durability",
         "SleepGuard wearable wristband sensor",
         "the wearable wristband band material cracked at the sensor "
         "mount within a few weeks of light stress and sleep wear"),
        ("durability",
         "BatteryLife wearable stress wristband",
         "battery on my wearable stress wristband barely lasts a day with "
         "continuous sleep heart-rate sensor tracking enabled all day"),
        # trust
        ("trust",
         "PrivacyWear wellness wearable sensor",
         "worried about how my continuous heart rate and sleep stress data "
         "from this wearable sensor wristband is being shared with others"),
        ("trust",
         "SecureSensor wearable wristband",
         "no clear disclosure about which cloud receives the wearable "
         "wristband stress and sleep sensor heart-rate telemetry data"),
        ("trust",
         "SafeSleep wearable haptic wristband",
         "wellness wearable app required social-network login to view "
         "stress and sleep wristband heart-rate sensor data history online"),
        ("trust",
         "DataGuard stress sensor wearable",
         "after returning the wearable wristband stress device the app "
         "still shows my heart-rate sleep sensor history online today"),
        # setup
        ("setup",
         "OnboardEasy wearable wristband v2",
         "pairing the wearable wristband stress sensor to my phone took "
         "an hour because the heart-rate sleep setup kept timing out"),
        ("setup",
         "QuickPair wearable stress wristband",
         "no instructions came with the wearable stress wristband and "
         "the heart-rate sleep sensor onboarding video was outdated"),
        ("setup",
         "CalibrateNow wearable wristband sensor",
         "wearable wristband stress sensor would not calibrate until I "
         "let the heart-rate sleep app run unattended overnight in bed"),
        ("setup",
         "HelpfulWear wearable wristband Pro",
         "setup process for the wearable stress wristband heart-rate "
         "sensor was clearly written for a much older sleep app build"),
        # price
        ("price",
         "BudgetWear wearable wristband monthly",
         "wearable wristband stress sensor itself was reasonably priced "
         "but the heart-rate sleep tracking subscription doubled the cost"),
        ("price",
         "FreeSensor wearable stress wristband",
         "did not realize the wearable stress wristband required a "
         "monthly heart-rate sensor sleep tracking subscription to use"),
        ("price",
         "ValueSensor wearable wristband sleep",
         "wearable wristband stress device was expensive and the "
         "heart-rate sleep sensor still misreads basic exercise data"),
        ("price",
         "RefundSensor wearable wristband Pro",
         "refund process for the wearable stress wristband heart-rate "
         "sleep sensor was slow and required multiple support emails"),
        # safety
        ("safety",
         "WarmSkin wearable wristband sensor",
         "wearable wristband stress sensor heats up uncomfortably "
         "during long sleep tracking sessions overnight which worries me"),
        ("safety",
         "SkinSafe wearable stress wristband",
         "developed a rash where the wearable wristband stress sensor "
         "contacts my skin during overnight sleep heart-rate monitoring"),
    ]
    for sig_type, title, snippet in on_topic:
        rows.append(_row(
            signal_type=sig_type,
            category="Health_and_Personal_Care",
            brand=title.split()[0] + "Co",
            title=title,
            snippet=snippet,
            rating=2,
            helpful=5,
        ))

    # Apollo Neuro (competitor-anchored).
    apollo_snippets = [
        "switched to Apollo Neuro because its haptic wearable wristband actually responds to my stress and sleep heart-rate sensor cues",
        "Apollo Neuro wearable wristband haptic stress device outperformed this product on real-time sleep heart-rate tracking metrics",
        "after a month I bought an Apollo Neuro wearable wristband because the haptic stress sleep heart-rate response was faster than the rest",
    ]
    for i, sn in enumerate(apollo_snippets):
        rows.append(_row(
            signal_type="switch_reason",
            category="Health_and_Personal_Care",
            brand="Apollo Neuro",
            title=f"Apollo Neuro wearable haptic stress band {i}",
            snippet=sn,
            rating=4,
            helpful=6,
            theme=f"switched_to_apollo_{i}",
        ))

    # Noise: sponges, bottles, beauty, supplements (~60 rows).
    sponge_titles = [
        "Premium Loofah Bath Sponge",
        "Exfoliating Body Sponge Pack",
        "Konjac Facial Cleansing Sponge",
        "Bath Body Brush with Sponge Head",
        "Reusable Silicone Body Sponge",
    ]
    for i, t in enumerate(sponge_titles):
        for j in range(4):
            sn = (
                f"this {t.lower()} version {j} falls apart after the first "
                f"{i+2} showers I used it and feels rough on my bath skin"
            )
            rows.append(_row(
                signal_type="objection",
                category="Health_and_Personal_Care",
                brand=f"SpongeCo{i}",
                title=f"{t} {j}",
                snippet=sn,
                rating=2,
                helpful=0,
                theme="generic_disappointment",
                review_hash=f"h_sponge_{i}_{j}",
            ))
    bottle_titles = [
        "Plastic Water Bottle 32oz",
        "Stainless Steel Insulated Bottle",
        "Glass Water Bottle with Sleeve",
        "Sport Squeeze Water Bottle",
    ]
    for i, t in enumerate(bottle_titles):
        for j in range(4):
            sn = (
                f"this water bottle {j} has a good seal and works well for my "
                f"daily routine of carrying drinks in my work backpack at office"
            )
            rows.append(_row(
                signal_type="praise",
                category="Health_and_Personal_Care",
                brand=f"BottleCo{i}",
                title=f"{t} {j}",
                snippet=sn,
                rating=5,
                helpful=0,
                theme="general_praise",
                review_hash=f"h_bottle_{i}_{j}",
                sentiment="positive",
            ))
    sup_titles = [
        "Daily Multivitamin Capsules 60 count",
        "Magnesium Glycinate Sleep Aid",
        "Vitamin D3 Supplement Drops",
        "Probiotic Daily Capsules",
    ]
    for i, t in enumerate(sup_titles):
        for j in range(4):
            sn = (
                f"this {t.lower()} version {j} tastes fine but I am not sure "
                f"if it has actually improved my daily energy level after week {i+1}"
            )
            rows.append(_row(
                signal_type="objection",
                category="Health_and_Personal_Care",
                brand=f"SupCo{i}",
                title=f"{t} {j}",
                snippet=sn,
                rating=3,
                helpful=1,
                theme="proof_need",
                review_hash=f"h_sup_{i}_{j}",
            ))
    return rows


# ---------------------------------------------------------------------------
# Helpers shared across modes
# ---------------------------------------------------------------------------


def _enabled_cfg() -> RetrievalConfig:
    return RetrievalConfig(
        enabled=True,
        runtime_enabled=True,
        same_category_only=True,
        persona_injection_enabled=True,
        max_signals_per_run=80,
        max_signals_per_category=40,
        max_signals_per_competitor=20,
        max_signals_per_brand=8,
        max_signals_per_theme=10,
    )


def _final_block(signals: list) -> str:
    picked = _balanced_prompt_snippets(signals)
    body = "\n".join(_format_prompt_snippet(s) for s in picked)
    return body, picked


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------


async def mode_a_no_injection(brief, corpus):
    return {
        "mode": "A — no Amazon injection",
        "candidate_pool_size": 0,
        "kept": 0,
        "final_snippets": 0,
        "block": "(no Amazon block)",
        "final_distribution": {},
    }


async def mode_b_category_only_no_filter(brief, corpus):
    """11C.5 path: retrieve_for_product_brief, threshold=0 (no filter)."""
    r = AmazonSignalRetriever(InMemorySignalSource(corpus), config=_enabled_cfg())
    pkg = await r.retrieve_for_product_brief(brief)
    body, picked = _final_block(list(pkg.signals))
    dist: dict[str, int] = {}
    for s in picked:
        dist[s.signal_type] = dist.get(s.signal_type, 0) + 1
    return {
        "mode": "B — 11C.5 category-only, no filter",
        "candidate_pool_size": len(pkg.signals),
        "kept": len(pkg.signals),
        "final_snippets": len(picked),
        "block": body,
        "final_distribution": dist,
    }


async def mode_c_category_only_filter(brief, corpus, threshold=0.20):
    """11C.6 path: retrieve_for_product_brief, then relevance filter."""
    r = AmazonSignalRetriever(InMemorySignalSource(corpus), config=_enabled_cfg())
    pkg = await r.retrieve_for_product_brief(brief)
    kept, _rej = filter_signals_by_relevance(
        list(pkg.signals), brief=brief, min_score=threshold,
    )
    body, picked = _final_block(kept)
    dist: dict[str, int] = {}
    for s in picked:
        dist[s.signal_type] = dist.get(s.signal_type, 0) + 1
    return {
        "mode": f"C — 11C.6 category-only + filter @ {threshold}",
        "candidate_pool_size": len(pkg.signals),
        "kept": len(kept),
        "final_snippets": len(picked),
        "block": body,
        "final_distribution": dist,
    }


async def mode_d_expanded_filter(brief, corpus, threshold=0.20):
    """11C.7 path: expanded retrieval + relevance filter at threshold."""
    r = AmazonSignalRetriever(InMemorySignalSource(corpus), config=_enabled_cfg())
    candidates, stats, _ = await r.retrieve_candidate_pool_for_persona(brief)
    kept, rej = filter_signals_by_relevance(
        list(candidates), brief=brief, min_score=threshold,
    )
    body, picked = _final_block(kept)
    dist: dict[str, int] = {}
    for s in picked:
        dist[s.signal_type] = dist.get(s.signal_type, 0) + 1
    return {
        "mode": f"D — 11C.7 expanded + filter @ {threshold}",
        "candidate_pool_size": (
            stats.category_candidates
            + stats.title_keyword_candidates
            + stats.competitor_brand_candidates
            + stats.signal_type_candidates
        ),
        "candidates_after_dedupe": stats.candidates_after_dedupe,
        "category_candidates": stats.category_candidates,
        "title_keyword_candidates": stats.title_keyword_candidates,
        "competitor_brand_candidates": stats.competitor_brand_candidates,
        "signal_type_candidates": stats.signal_type_candidates,
        "title_keywords_used": stats.title_keywords_used,
        "matched_brands": stats.matched_brands_or_competitors,
        "kept": len(kept),
        "rejected": len(rej),
        "final_snippets": len(picked),
        "block": body,
        "final_distribution": dist,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def run_brief(name: str, brief, corpus) -> None:
    print(f"\n{'=' * 78}")
    print(f"BRIEF: {name}")
    print(f"{'=' * 78}")
    print(f"Corpus size: {len(corpus)} rows in category="
          f"{corpus[0].category if corpus else '(empty)'}")

    results = [
        await mode_a_no_injection(brief, corpus),
        await mode_b_category_only_no_filter(brief, corpus),
        await mode_c_category_only_filter(brief, corpus, threshold=0.20),
        await mode_d_expanded_filter(brief, corpus, threshold=0.20),
        await mode_d_expanded_filter(brief, corpus, threshold=0.05),
    ]
    # Rename mode E.
    results[4]["mode"] = "E — 11C.7 expanded + filter @ 0.05"

    print()
    print(f"{'mode':<46} {'pool':>5} {'kept':>5} {'final':>5}")
    print("-" * 78)
    for r in results:
        print(
            f"{r['mode']:<46} "
            f"{r.get('candidate_pool_size', 0):>5} "
            f"{r.get('kept', 0):>5} "
            f"{r['final_snippets']:>5}"
        )

    for r in results:
        if r["final_snippets"] == 0:
            continue
        print(f"\n  -- {r['mode']} -----------------------------")
        if "title_keywords_used" in r:
            print(f"     title_keywords_used: {r['title_keywords_used']}")
            print(f"     matched_brands: {r['matched_brands']}")
            print(f"     pool: category={r['category_candidates']}, "
                  f"title_kw={r['title_keyword_candidates']}, "
                  f"comp/brand={r['competitor_brand_candidates']}, "
                  f"sig_type={r['signal_type_candidates']}; "
                  f"after_dedupe={r['candidates_after_dedupe']}")
        print(f"     final_distribution: {r['final_distribution']}")
        print(f"     block:")
        for line in r["block"].splitlines():
            print(f"       {line}")


async def main() -> None:
    await run_brief("QuietCart (Software)", QUIETCART, software_corpus())
    await run_brief(
        "CalmCue stress wearable (Health_and_Personal_Care)",
        CALMCUE, wellness_corpus(),
    )


if __name__ == "__main__":
    asyncio.run(main())
