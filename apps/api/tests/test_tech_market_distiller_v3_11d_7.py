"""Phase 11D.7 — distiller v3 regression tests (HN/devtool language).

Phase 11D.6's Semble HN dry-run revealed the v2 distiller's
Product-Hunt-specific blind spots:

  * developer_skepticism / benchmark methodology never fired
    (5 missed rows on Semble)
  * "Wouldn't / Could you / Shouldn't" question forms missed
  * "wastes tokens", "falls apart", "biggest challenge" missed
  * "agent does not trust" / "falls back to grep" / "part of the
    harness" workflow patterns missed
  * Bare "Setup" / "API" triggered false positives

This file pins the v3 fixes against the real Semble HN corpus AND
re-verifies the Vivago corpus doesn't regress. The operator
deliberately folded methodology skepticism into developer_skepticism
(rather than adding a new signal_type) so no schema migration is
required in this phase.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from assembly.sources.tech_market_provider import (
    RuleBasedTechMarketDistiller,
)


_distiller = RuleBasedTechMarketDistiller()


def _classify(text: str) -> str | None:
    out = _distiller.distill(
        text,
        source_provider="phase_11d_7_test",
        product_category="devtool_api",
        market_context_hint="devtool",
    )
    if not out:
        return None
    return out[0].signal_type


# ---------------------------------------------------------------------------
# 1. developer_skepticism — HN methodology + tech-choice skepticism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Wouldn't NDCG/token results vary wildly depending on the agent's query "
    "and the number of returned items?",
    "Is the benchmark measuring one-shot retrieval accuracy, or coding agent "
    "response accuracy?",
    "How do you measure token use without the agent, prompt, and tools?",
    "Grep does not need tokens, so what is 98% fewer than zero?",
    "Even so, take a look at the NDCG numbers for grep. It is not pretty.",
])
def test_methodology_skepticism_classifies_as_developer_skepticism(
    text: str,
) -> None:
    """Operator chose to fold methodology skepticism into the
    existing developer_skepticism signal_type rather than introduce
    a new signal_type (which would have required a schema
    migration)."""
    assert _classify(text) == "developer_skepticism"


def test_python_vs_go_rust_classifies_as_developer_skepticism() -> None:
    """HN row 28: tech-choice skepticism about implementation
    language. Should hit developer_skepticism, not feature_inquiry,
    because the dominant signal is dev skepticism about a tech
    choice ('would surely be faster')."""
    text = (
        "I am very curious to give it a spin, but why write a CLI in "
        "Python? It would surely be faster and more portable with Go "
        "or Rust."
    )
    assert _classify(text) == "developer_skepticism"


def test_existing_dev_skepticism_patterns_still_classify() -> None:
    """Phase 11D.1 patterns must not regress."""
    for text in (
        "this feels like prototype quality, not production ready",
        "the docs are wrong and the SDK behaves like a black box",
        "had to ask stack overflow because the docs are wrong",
    ):
        assert _classify(text) == "developer_skepticism", text


# ---------------------------------------------------------------------------
# 2. pain_urgency — HN implicit-pain wording
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "all token savings are lost because the model does not trust results",
    "the agent wastes tokens retrying every failed call",
    "RTK falls apart running a tool it does not support",
    "the biggest challenge was getting the agent to prefer the new tool",
])
def test_hn_implicit_pain_classifies_as_pain_urgency(text: str) -> None:
    assert _classify(text) == "pain_urgency"


# ---------------------------------------------------------------------------
# 3. workflow_fit — HN/devtool adoption-friction patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Agents do not trust the results of the other tools and will retry or "
    "reread.",
    "The biggest challenge is to get the agent to prefer to use the tool "
    "over bash tools.",
    "Shouldn't it be a part of the harness at least for a local codebase?",
    "I am playing with a custom harness for Claude Code on my local "
    "codebase harness.",
    "Setup hooks. Hooks are how your harness forces compliance with your "
    "own rules.",
])
def test_hn_adoption_friction_classifies_as_workflow_fit(text: str) -> None:
    """The text-3 example has 'biggest challenge' (pain) AND 'prefer
    to use the tool' (workflow). pain_urgency wins because it's
    earlier in rule order, which matches founder-utility ranking."""
    sig = _classify(text)
    # Either pain_urgency (operator's spec allows it for some) or
    # workflow_fit. Just ensure it's one of those, not feature_inquiry
    # or competitor_comparison.
    assert sig in {"workflow_fit", "pain_urgency"}, (
        f"expected workflow_fit/pain_urgency, got {sig!r}"
    )


def test_part_of_the_harness_wins_over_feature_inquiry() -> None:
    """HN row 8 has 'Shouldn't it' (feature_inquiry stem) AND 'part
    of the harness' (workflow_fit). workflow_fit must win — it's
    the more useful founder signal."""
    text = (
        "Shouldn't it be a part of the harness at least for a local "
        "codebase? I wonder how many harnesses are doing that already."
    )
    assert _classify(text) == "workflow_fit"


def test_agent_does_not_trust_classifies_as_workflow_or_pain() -> None:
    """HN row 1 mentions Claude Code AND has 'agent does not trust
    results / retry / reread'. The adoption-friction signal is more
    useful than the competitor signal — workflow_fit OR pain_urgency
    must win, NOT competitor_comparison."""
    text = (
        "I have explored RTK and various LSP implementations and find "
        "that the models do not trust results in other forms and will "
        "continually retry or reread. All token savings are lost "
        "because the model does not trust the results of the other "
        "tools."
    )
    sig = _classify(text)
    assert sig in {"workflow_fit", "pain_urgency"}, (
        f"expected workflow_fit/pain_urgency, got {sig!r}"
    )


# ---------------------------------------------------------------------------
# 4. feature_inquiry — HN question forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Would this replace something like codebase-memory-mcp?",
    "Could you add fff to the benchmarks?",
    "Should this be configurable through a flag?",
    "Wouldn't it make sense to ship a default config?",
    "Does this work well for non-coding documents as well?",
    "How many indexes does it maintain in parallel?",
])
def test_hn_question_forms_classify_as_feature_inquiry(text: str) -> None:
    """v2 only matched 'Can/Does/Is/How/What' interrogatives. v3
    adds Would/Could/Should/Shouldn't/Wouldn't + 'does this' /
    'how many'."""
    assert _classify(text) == "feature_inquiry"


def test_does_this_with_api_docs_does_not_classify_as_integration() -> None:
    """HN row 18 false positive in 11D.6: bare 'API' fired
    integration_friction on a feature-inquiry question about API
    docs. v3 tightens integration_friction to require breakage
    language and the question lands as feature_inquiry."""
    text = (
        "Does this work well for non-coding documents as well? Say "
        "API docs or AI memory files?"
    )
    assert _classify(text) == "feature_inquiry"


# ---------------------------------------------------------------------------
# 5. onboarding_friction TIGHTENED — bare "setup" no longer fires
# ---------------------------------------------------------------------------


def test_setup_hooks_no_longer_classifies_as_onboarding_friction() -> None:
    """HN row 15 false positive in 11D.6: 'Setup hooks. Hooks are
    how your harness forces compliance...' fired onboarding_friction
    on the bare word 'Setup'. v3 requires explicit setup-pain
    language; bare 'Setup' as a noun no longer matches."""
    text = (
        "Setup hooks. Hooks are how your harness forces compliance "
        "with your own rules."
    )
    assert _classify(text) != "onboarding_friction"


def test_real_setup_pain_still_classifies_as_onboarding_friction() -> None:
    """The tightening must not regress real setup-friction language.
    Texts here deliberately avoid the bare word 'painful' (which is
    in pain_urgency at position 3 of the rule order) so this test
    isolates the onboarding-pattern behavior."""
    for text in (
        "setup took an hour and the tutorial was outdated",
        "the setup was confusing and the docs were wrong",
        "install failed twice and the docs were outdated",
        "hard to set up on a Mac",
        "the workspace admin console kept failing during onboarding "
        "setup",
    ):
        assert _classify(text) == "onboarding_friction", text


# ---------------------------------------------------------------------------
# 6. integration_friction TIGHTENED — bare "API" no longer fires
# ---------------------------------------------------------------------------


def test_api_docs_no_longer_classifies_as_integration_friction() -> None:
    """HN row 18: 'API docs' fired integration_friction on the bare
    word 'API'. v3 requires breakage language."""
    text = "Does this work well for non-coding documents as well? Say API docs or AI memory files?"
    assert _classify(text) != "integration_friction"


def test_real_integration_pain_still_classifies() -> None:
    """The tightening must not regress real integration-friction
    language. Texts here deliberately avoid bare 'painful' (which
    fires pain_urgency at position 3) so this test isolates the
    integration-pattern behavior."""
    for text in (
        "the webhook integration broke twice this week",
        "the API kept failing and the SDK won't connect",
        "OAuth integration was broken for the second day",
        "the SDK and webhook integration broke",
    ):
        assert _classify(text) == "integration_friction", text


# ---------------------------------------------------------------------------
# 7. competitor_comparison — methodology comments no longer mis-fire
# ---------------------------------------------------------------------------


def test_methodology_with_instead_of_classifies_as_dev_skepticism() -> None:
    """HN row 23 false positive in 11D.6: 'Wouldn't NDCG/token results
    vary wildly depending on the agent's query and the number of
    returned items? Agents often run grep -m 5 with different
    queries, instead of one big grep for all items.' fired
    competitor_comparison on 'instead of'. v3 promotes
    developer_skepticism above competitor_comparison so the
    methodology signal wins."""
    text = (
        "Wouldn't NDCG/token results vary wildly depending on the "
        "agent's query and the number of returned items? Agents "
        "often run grep -m 5 with different queries, instead of one "
        "big grep for all items."
    )
    assert _classify(text) == "developer_skepticism"


def test_real_competitor_comparison_still_classifies() -> None:
    """The reordering must not regress brand-named competitor calls."""
    for text in (
        "We all build the Claude Code equivalent of the area we are "
        "evicted by.",
        "I am playing with PI for Claude Code because that is what is "
        "provided.",
        "I'd love to compare this against Sora and Runway.",
        "Better than grep obviously, but how does this compare to "
        "existing LSPs?",
    ):
        assert _classify(text) == "competitor_comparison", text


# ---------------------------------------------------------------------------
# 8. End-to-end Vivago corpus — no regression
# ---------------------------------------------------------------------------


_VIVAGO_COMMENTS = [
    "I remember Vivago making the Top 3 two years ago. Great to see the "
    "team back with such a massive evolution. The transition from a pure "
    "generation tool to an AI Video Agent with a structured workflow is "
    "amazing product design.",
    "As a user, I am truly impressed by the seamless experience of "
    "generating captivating AI videos with stunning enhancements and "
    "intuitive prompt optimization.",
    "VIVA is seriously impressive. It makes creating videos so simple and "
    "fast, and the quality is top-notch. I have used other tools before, "
    "but Viva really stands out as a strong competitor to Sora.",
    "I fed it my profile pic and a one-line prompt, and it generated a "
    "narrative video where I was the hero. Considering this was produced "
    "with a single still and a low-effort prompt, I was impressed given "
    "all my failed attempts to get Sora to do my bidding. If you have been "
    "burned by stitching together 4-second clips that do not cohere, "
    "Vivago feels built for narrative.",
    "I have been tracking the AI leaderboard and noticed HiDream-O1-Image "
    "at number one for a while.",
    "Solid work. The 9-frame storyboard preview before render looks like "
    "the smartest bit. After I approve those keyframes, is the 1-minute "
    "render locked to them?",
    "Does the AI director workflow let you iterate? If you get keyframes "
    "back and want to explore a different angle on the same story, can "
    "you guide it without starting from scratch?",
    "The coherence across frames looks solid. How much of that comes from "
    "HiDream-O1 itself versus your planning layer?",
    "So wait, I just give it a photo and a sentence and it makes a whole "
    "video? That seems too good to be true. What is the catch here? Does "
    "it actually look good or is it one of those things where the demo "
    "videos are cherry-picked?",
    "Can I use my own character designs, or does the swarm create "
    "everything from scratch? Love this approach.",
    "How does the agent handle brand logos and fonts across keyframes?",
    "Skipping the prompting loop is the right problem. Most people quit AI "
    "video after 20 failed attempts. What does the agent use as input?",
    "The storyboard/keyframe approval flow feels like the right place to "
    "build trust before the expensive render. Can a team keep a reusable "
    "story or brand bible across videos — character rules, proof points, "
    "rejected styles, pacing examples — and have the director show which "
    "assets or notes guided a scene?",
    "No prompting? How does that work?",
    "Got it. That makes sense. We all build the Claude Code equivalent of "
    "the area we are evicted by.",
    "Really impressed by how smooth the video generation looks. The "
    "enhancement feature sounds super useful for creators. How long does "
    "an average video take to generate?",
    "As a social media strategist, the biggest bottleneck in video "
    "production is always maintaining visual consistency across scenes, "
    "especially when trying to tell a unified story. How does the agent "
    "handle custom brand guidelines or strict color palettes during the "
    "initial character invention phase?",
]


def test_vivago_corpus_v3_yield_does_not_regress() -> None:
    """Vivago yield must stay at ≥ 75%. v2 was 82% (14/17) — v3
    must hit at least 75% (the operator-spec'd floor)."""
    accepted = sum(
        1 for c in _VIVAGO_COMMENTS
        if _classify(c) is not None
    )
    ratio = accepted / len(_VIVAGO_COMMENTS)
    assert ratio >= 0.75, (
        f"Vivago yield regressed: {ratio:.2%} ({accepted}/"
        f"{len(_VIVAGO_COMMENTS)}) — must stay ≥ 75%"
    )


def test_vivago_signal_type_coverage_preserved() -> None:
    """The 6 signal types we had at end-of-11D.5 must all still fire
    on the Vivago corpus."""
    types: set[str] = set()
    for c in _VIVAGO_COMMENTS:
        sig = _classify(c)
        if sig is not None:
            types.add(sig)
    expected = {
        "pain_urgency",
        "workflow_fit",
        "trust_security_concern",
        "competitor_comparison",
        "switching_objection",
        "feature_inquiry",
    }
    missing = expected - types
    assert not missing, (
        f"Vivago lost signal-type coverage: missing {missing}"
    )


def test_vivago_row_1_still_correctly_rejected() -> None:
    """The Phase 11D.4 fix that rejected row 1 (bare-workflow praise)
    must still hold under v3."""
    row_1 = _VIVAGO_COMMENTS[0]
    assert _classify(row_1) is None


# ---------------------------------------------------------------------------
# 9. End-to-end Semble corpus — v3 must hit ≥ 60% with ≤ 10% FP
# ---------------------------------------------------------------------------


_SEMBLE_COMMENTS = [
    "I'd be interested in seeing actual agent benchmarks, for example "
    "Claude Code or Copilot CLI with grep removed and this tool used "
    "instead. I have explored RTK and various LSP implementations and "
    "find that the models are so heavily RL'd with grep that they do not "
    "trust results in other forms and will continually retry or reread. "
    "All token savings are lost because the model does not trust the "
    "results of the other tools.",
    "I just put something in my global CLAUDE.md asking it to use the "
    "LSP instead of grep and have never had this issue since.",
    "My question would have been this: LSP solved this, no?",
    "Codex CLI is quite happy running RTK, at least with GPT 5.5 xhigh. "
    "One thing that irks me is that when it does not support a CLI flag "
    "of find, it gives an error message rather than sending the full "
    "output of the command instead. Then the agent wastes tokens "
    "retrying, or worse, does not even try.",
    "I forced Claude to have a global memory for RTK and my own AI "
    "memory system, GuardRails, which it happily uses. Otherwise it "
    "always uses RTK unless RTK falls apart running a tool it does not "
    "support.",
    "We are also interested in doing actual agent benchmarks. It is on "
    "the roadmap together with optimization of the prompt and "
    "descriptions so that models have an easier time using it.",
    "Better than grep obviously, but how does this compare to existing "
    "LSPs?",
    "Shouldn't it be a part of the harness at least for a local "
    "codebase? I wonder how many harnesses are doing that already.",
    "I am playing with PI as a custom harness for Claude Code because "
    "that is what is provided to me. I will try this.",
    "I also like the index feature from maki.sh. Source code has a lot "
    "of structure, so using a real parser instead of grepping and "
    "reading files can potentially save a lot of tokens.",
    "How does this compare with colgrep?",
    "Semantic code search seems like a useful tool for a human too, "
    "not just for agents.",
    "Would this replace something like codebase-memory-mcp or improve "
    "when both are being used?",
    "This looks great. I built a tool in the same space, and I found "
    "that the biggest challenge was often to get the agent to prefer "
    "to use the tool over bash tools.",
    "Setup hooks. Hooks are how your harness forces compliance with "
    "your own rules.",
    "How does it compare to context-mode or serina that are both well "
    "established now?",
    "Could you add fff to the benchmarks?",
    "Does this work well for non-coding documents as well? Say API "
    "docs or AI memory files?",
    "This is something we are actively investigating. We recently added "
    "a flag, --include-text-files, which, when set, also makes Semble "
    "index regular documents like markdown, text, and JSON.",
    "Is the benchmark measuring one-shot retrieval accuracy, or coding "
    "agent response accuracy?",
    "The benchmark currently only measures retrieval accuracy. We are "
    "interested in measuring it end to end and also optimizing the "
    "prompt and tools for this.",
    "Two follow-ups: how do you compare accuracy? By checking if the "
    "answer is in any of the returned grep, BM25, or Semble snippets? "
    "Also, how do you measure token use without the agent, prompt, "
    "and tools?",
    "Wouldn't NDCG/token results vary wildly depending on the agent's "
    "query and the number of returned items? Agents often run grep "
    "-m 5 with different queries, instead of one big grep for all "
    "items.",
    "The same holds for Semble: the agent can fire off many different "
    "Semble queries with different k or parameters. The point is that "
    "you need fewer Semble queries to achieve the same outcome compared "
    "to grep plus readfile calls.",
    "Grep does not need tokens, so what is 98% fewer than zero?",
    "You need readfile to do something with those tokens. Grep only "
    "gives you the matching lines, not the context.",
    "Even so, take a look at the NDCG numbers for grep. It is not pretty.",
    "I am very curious to give it a spin, but why write a CLI in Python? "
    "It would surely be faster and more portable with Go or Rust.",
    "Perhaps Python is their main language, since they seem to be ML "
    "people, which would make that most likely.",
]


def test_semble_corpus_v3_yield_meets_60_percent() -> None:
    """The operator-spec'd Phase 11D.7 target: Semble must hit
    ≥ 60% yield (up from 45% in 11D.6)."""
    accepted = sum(
        1 for c in _SEMBLE_COMMENTS
        if _classify(c) is not None
    )
    ratio = accepted / len(_SEMBLE_COMMENTS)
    assert ratio >= 0.60, (
        f"Semble yield: {ratio:.2%} ({accepted}/{len(_SEMBLE_COMMENTS)}) "
        f"— below operator-spec'd 60% floor"
    )


def test_semble_corpus_developer_skepticism_now_appears() -> None:
    """In 11D.6 developer_skepticism fired ZERO times on Semble
    despite the corpus being rich in methodology/benchmark
    skepticism. v3 must surface it."""
    types: set[str] = set()
    for c in _SEMBLE_COMMENTS:
        sig = _classify(c)
        if sig is not None:
            types.add(sig)
    assert "developer_skepticism" in types, (
        f"developer_skepticism still missing on Semble — got {types}"
    )


def test_semble_corpus_workflow_fit_now_appears() -> None:
    """In 11D.6 workflow_fit fired ZERO times on Semble despite
    adoption-friction language being all over the corpus."""
    types: set[str] = set()
    for c in _SEMBLE_COMMENTS:
        sig = _classify(c)
        if sig is not None:
            types.add(sig)
    assert "workflow_fit" in types, (
        f"workflow_fit still missing on Semble — got {types}"
    )


# ---------------------------------------------------------------------------
# 10. Schema parity — no new signal types in 11D.7
# ---------------------------------------------------------------------------


def test_signal_types_unchanged_in_11d_7() -> None:
    """Phase 11D.7 deliberately folds methodology skepticism into
    developer_skepticism so no schema migration is needed. The
    15-value vocabulary from 11D.5 must remain unchanged."""
    from assembly.sources.tech_market_provider.signal_types import (
        SIGNAL_TYPES,
    )
    from assembly.models.tech_market_signal import (
        SIGNAL_TYPES as MODEL_SIGNAL_TYPES,
    )
    assert set(SIGNAL_TYPES) == set(MODEL_SIGNAL_TYPES)
    assert len(SIGNAL_TYPES) == 15
    # Verify no new value sneaked in.
    expected = {
        "pain_urgency", "switching_objection", "pricing_objection",
        "trust_security_concern", "integration_friction",
        "onboarding_friction", "support_complaint",
        "competitor_comparison", "willingness_to_pay",
        "nice_to_have_risk", "feature_not_company_risk",
        "workflow_fit", "developer_skepticism",
        "procurement_friction", "feature_inquiry",
    }
    assert set(SIGNAL_TYPES) == expected


# ---------------------------------------------------------------------------
# 11. Drift: no apps/web changes, no scraping imports, flags off
# ---------------------------------------------------------------------------


def test_distiller_v3_has_no_http_or_scraping_imports() -> None:
    pkg = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "sources" / "tech_market_provider"
        / "distiller.py"
    )
    src = pkg.read_text(encoding="utf-8")
    import re as _re
    forbidden = (
        "requests", "httpx", "aiohttp", "selenium", "playwright",
        "scrapy", "bs4", "beautifulsoup4", "urllib.request",
    )
    for token in forbidden:
        pat = _re.compile(
            rf"^\s*(?:import|from)\s+{_re.escape(token)}\b",
            _re.MULTILINE,
        )
        assert pat.search(src) is None, (
            f"distiller.py imports forbidden module {token!r}"
        )


def test_tech_market_flags_still_default_false_after_11d_7() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.tech_market_signals_enabled is False
    assert s.tech_market_signals_runtime_enabled is False
    assert s.tech_market_signals_persona_injection_enabled is False
