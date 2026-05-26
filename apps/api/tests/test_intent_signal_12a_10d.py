"""Phase 12A.10D — explicit intent signal / uncertain-routing fix.

Tests cover:
  - the derive_intent_signal() classifier on realistic ballot text
  - the map_intent_signal_to_market_bucket() bucket mapping
  - backward compatibility (intent_signal is optional / nullable on
    SimulatedIntentDraft)
  - resistance invariants (loyal / would_reject / hard-resistant
    still map to skeptical bucket)
  - no LLM call introduced (the file imports cleanly with no
    provider/sdk side-effects)
"""
from __future__ import annotations

import pytest

from assembly.calibration.market_buckets import (
    INTENT_SIGNAL_TO_BUCKET,
    map_intent_signal_to_market_bucket,
)
from assembly.sources.intent_layer.inference import (
    derive_intent_signal,
    infer_simulated_intent,
)
from assembly.sources.intent_layer.schemas import SimulatedIntentDraft


# -----------------------------------------------------------------------
# Bucket mapping table
# -----------------------------------------------------------------------


class TestIntentSignalBucketMap:
    def test_buyer_signals_map_to_buyer(self) -> None:
        for sig in (
            "explicit_buy_or_use_now",
            "explicit_try_once",
            "explicit_waitlist_or_signup",
        ):
            b, w = map_intent_signal_to_market_bucket(sig)
            assert b == "buyer", f"{sig} mapped to {b}"
            assert w is None

    def test_receptive_signals_map_to_receptive(self) -> None:
        for sig in (
            "positive_interest_if_proven",
            "would_compare_to_current_tool",
        ):
            b, w = map_intent_signal_to_market_bucket(sig)
            assert b == "receptive", f"{sig} mapped to {b}"
            assert w is None

    def test_uncertain_signals_map_to_uncertain(self) -> None:
        for sig in (
            "curious_but_unconvinced",
            "needs_more_information",
            "neutral_information_seeking",
            "mixed_or_ambiguous",
        ):
            b, w = map_intent_signal_to_market_bucket(sig)
            assert b == "uncertain", f"{sig} mapped to {b}"
            assert w is None

    def test_skeptical_signals_map_to_skeptical(self) -> None:
        for sig in (
            "trust_blocked",
            "price_blocked",
            "competitor_loyal",
            "explicit_rejection",
            "not_target_customer",
        ):
            b, w = map_intent_signal_to_market_bucket(sig)
            assert b == "skeptical", f"{sig} mapped to {b}"
            assert w is None

    def test_off_topic_maps_to_uncertain(self) -> None:
        b, w = map_intent_signal_to_market_bucket("off_topic_or_noise")
        assert b == "uncertain"

    def test_unknown_signal_defaults_to_uncertain_with_warning(
        self,
    ) -> None:
        b, w = map_intent_signal_to_market_bucket("totally_invented_label")
        assert b == "uncertain"
        assert w is not None
        assert "unknown_intent_signal" in w

    def test_none_signal_defaults_to_uncertain_with_warning(self) -> None:
        b, w = map_intent_signal_to_market_bucket(None)
        assert b == "uncertain"
        assert w is not None
        assert "intent_signal_missing" in w

    def test_every_enum_value_has_mapping(self) -> None:
        """Every IntentSignal value declared in the schema must map
        to a real bucket — no silent fall-throughs allowed."""
        from typing import get_args
        from assembly.sources.intent_layer.schemas import IntentSignal
        for sig in get_args(IntentSignal):
            assert sig in INTENT_SIGNAL_TO_BUCKET, (
                f"IntentSignal '{sig}' missing from bucket map"
            )


# -----------------------------------------------------------------------
# Derivation classifier — uncertain category (THE FIX)
# -----------------------------------------------------------------------


def _derive(*, stance="curious_but_unconvinced", reasoning="", role="generic_buyer", psy=None, obj=None, proof=None):
    return derive_intent_signal(
        private_stance=stance,
        private_reasoning=reasoning,
        top_objection=obj,
        top_proof_need=proof,
        normalized_role=role,
        psychology_value_map=psy or {},
        cohort_objection_summary=None,
        persona_text_corpus=None,
    )


class TestUncertainRouting:
    @pytest.mark.parametrize("text", [
        "How does this handle hallucinations?",
        "Can you prove it works on real alerts?",
        "Interesting, but I would need to see it in production.",
        "How is this different from PagerDuty?",
        "Would need a demo first.",
        "Maybe useful, but I'm not sure this solves our workflow.",
        "What about false positives — how do you measure them?",
    ])
    def test_question_or_info_seeking_routes_to_uncertain(
        self, text: str,
    ) -> None:
        signal, basis = _derive(stance="curious_but_unconvinced", reasoning=text)
        bucket, _ = map_intent_signal_to_market_bucket(signal)
        assert bucket == "uncertain", (
            f"text={text!r} -> signal={signal}, bucket={bucket}; basis={basis}"
        )

    def test_needs_more_info_stance_with_no_positive_tokens(
        self,
    ) -> None:
        signal, _ = _derive(
            stance="needs_more_information",
            reasoning="I'd want to see how it integrates with our existing setup.",
        )
        assert signal in (
            "needs_more_information",
            "neutral_information_seeking",
            "curious_but_unconvinced",
        )
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "uncertain"


# -----------------------------------------------------------------------
# Derivation classifier — receptive category
# -----------------------------------------------------------------------


class TestReceptiveRouting:
    @pytest.mark.parametrize("text", [
        "This is useful if it works; I would consider trying it.",
        "I can see our team using this if the accuracy is good.",
        "Looks promising — could be useful for our team.",
        "This looks great if you can deliver on the promises.",
    ])
    def test_positive_interest_routes_to_receptive(self, text: str) -> None:
        signal, basis = _derive(stance="interested_if_proven", reasoning=text)
        bucket, _ = map_intent_signal_to_market_bucket(signal)
        assert bucket == "receptive", (
            f"text={text!r} -> signal={signal}, bucket={bucket}; basis={basis}"
        )

    def test_compare_to_current_without_loyalty(self) -> None:
        signal, _ = _derive(
            stance="curious_but_unconvinced",
            reasoning=(
                "I would compare this to my current tool to see "
                "which one fits our team better."
            ),
        )
        assert signal == "would_compare_to_current_tool"
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "receptive"


# -----------------------------------------------------------------------
# Derivation classifier — skeptical category (resistance preserved)
# -----------------------------------------------------------------------


class TestSkepticalRouting:
    @pytest.mark.parametrize("text,expected", [
        ("PagerDuty already does this and I'm sticking with my current solution.", "competitor_loyal"),
        ("I don't trust LLMs for alerting — too many hallucinations.", "trust_blocked"),
        ("This isn't for me; we don't have this issue.", "not_target_customer"),
        ("Won't pay $19/month for this; too expensive for what it does.", "price_blocked"),
        ("Absolutely not. This isn't for me.", "explicit_rejection"),
    ])
    def test_resistance_signals_route_to_skeptical(
        self, text: str, expected: str,
    ) -> None:
        signal, basis = _derive(stance="skeptical", reasoning=text)
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "skeptical", (
            f"text={text!r} -> signal={signal}, bucket={b}, expected={expected}; basis={basis}"
        )

    def test_likely_reject_stance_routes_to_skeptical(self) -> None:
        signal, _ = _derive(stance="likely_reject", reasoning="not for us")
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "skeptical"

    def test_loyalty_token_with_curious_stance_still_skeptical(
        self,
    ) -> None:
        """Loyalty signal in curious_but_unconvinced ballot must NOT
        get rebucketed as uncertain — must stay skeptical."""
        signal, _ = _derive(
            stance="curious_but_unconvinced",
            reasoning="What I already have works fine; I'd stick with it.",
        )
        assert signal == "competitor_loyal"
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "skeptical"


# -----------------------------------------------------------------------
# Derivation classifier — buyer category
# -----------------------------------------------------------------------


class TestBuyerRouting:
    @pytest.mark.parametrize("text,expected_signal", [
        ("I'll try this.", "explicit_try_once"),
        ("Installing now.", "explicit_buy_or_use_now"),
        ("We need this for our team.", "explicit_buy_or_use_now"),
        ("Sign me up — let me know when it launches.", "explicit_waitlist_or_signup"),
    ])
    def test_explicit_adoption_routes_to_buyer(
        self, text: str, expected_signal: str,
    ) -> None:
        signal, basis = _derive(stance="interested_if_proven", reasoning=text)
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "buyer", (
            f"text={text!r} -> signal={signal}, bucket={b}, expected={expected_signal}; basis={basis}"
        )

    def test_buyer_does_not_over_inflate_on_soft_positive(
        self,
    ) -> None:
        """A soft positive without explicit adoption verb must NOT
        be promoted to buyer."""
        signal, _ = _derive(
            stance="interested_if_proven",
            reasoning="Looks useful. Promising direction.",
        )
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "receptive"


# -----------------------------------------------------------------------
# Catch-all (final defense): the legacy bug was here — empty/weak
# ballots dropped into receptive. The new default is uncertain.
# -----------------------------------------------------------------------


class TestCatchAllBugFix:
    def test_empty_ballot_does_not_route_to_receptive(self) -> None:
        signal, basis = _derive(stance="curious_but_unconvinced", reasoning="")
        b, _ = map_intent_signal_to_market_bucket(signal)
        # The legacy cascade would route empty + curious_but_unconvinced
        # to would_consider_if_proven -> receptive. Phase 12A.10D must
        # route to uncertain.
        assert b == "uncertain", f"empty ballot -> {b}; basis={basis}"

    def test_genuinely_neutral_ballot_routes_to_uncertain(self) -> None:
        signal, _ = _derive(
            stance="curious_but_unconvinced",
            reasoning="It depends. Hard to say without more details.",
        )
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "uncertain"


# -----------------------------------------------------------------------
# Backward compatibility — intent_signal field is optional
# -----------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_simulated_intent_draft_accepts_null_intent_signal(self) -> None:
        d = SimulatedIntentDraft(
            persona_id="p1",
            cohort_id="c1",
            stance_label="curious_but_unconvinced",
            simulated_intent="would_consider_if_proven",
            intent_strength="medium",
            switching_status="weakly_attached_to_alternative",
            confidence="medium",
            evidence_basis="legacy-style draft",
            caveat="Synthetic simulated intent.",
            # intent_signal omitted on purpose
        )
        assert d.intent_signal is None
        assert d.intent_signal_basis is None

    def test_infer_emits_intent_signal_when_text_present(self) -> None:
        d = infer_simulated_intent(
            persona_id="p1",
            cohort_id="c1",
            normalized_role="sre",
            psychology_value_map={
                "trust_proof_threshold": 0.6,
                "novelty_seeking": 0.5,
                "risk_tolerance": 0.5,
                "price_sensitivity": 0.5,
                "social_influence_susceptibility": 0.5,
                "openness": 0.5,
                "category_involvement_or_expertise": 0.5,
            },
            pre_ballot=None,
            final_ballot={
                "private_stance": "curious_but_unconvinced",
                "private_reasoning": (
                    "How does it handle false positives? "
                    "Would need a demo first."
                ),
                "top_proof_need": "Real before/after examples.",
            },
            reflection_ballot=None,
            persona_text_corpus="",
            ballot_ids=[],
            discussion_turn_ids=[],
            memory_atom_ids=[],
        )
        assert d.intent_signal is not None
        # The text contains 2 proof-question tokens → needs_more_information
        b, _ = map_intent_signal_to_market_bucket(d.intent_signal)
        assert b == "uncertain"


# -----------------------------------------------------------------------
# Resistance / buyer-inflation invariants
# -----------------------------------------------------------------------


class TestInvariants:
    def test_no_buyer_inflation_from_neutral_text(self) -> None:
        """A neutral information-seeking ballot must NEVER produce a
        buyer-bucket signal even if the stance is interested_if_proven."""
        signal, _ = _derive(
            stance="interested_if_proven",
            reasoning="Interesting. How does it work?",
        )
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b != "buyer"

    def test_no_skeptical_erasure_from_loyalty(self) -> None:
        signal, _ = _derive(
            stance="curious_but_unconvinced",
            reasoning="I prefer my current setup; no reason to change.",
        )
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "skeptical"

    def test_legacy_intent_label_still_maps_separately(self) -> None:
        """The legacy `map_assembly_intent_to_market_bucket` still
        exists and routes loyal_to_current_alternative to skeptical —
        this remains the fallback path for runs without intent_signal."""
        from assembly.calibration import (
            map_assembly_intent_to_market_bucket,
        )
        b, _ = map_assembly_intent_to_market_bucket(
            "loyal_to_current_alternative",
        )
        assert b == "skeptical"


# -----------------------------------------------------------------------
# No-LLM invariant — confirm the new code path imports cleanly without
# pulling any provider/sdk module.
# -----------------------------------------------------------------------


class TestNoLLMSurface:
    def test_module_has_no_anthropic_or_openai_imports(self) -> None:
        import importlib
        m = importlib.import_module(
            "assembly.sources.intent_layer.inference"
        )
        # Walk module source-attribute names; ensure no provider client
        # was accidentally pulled in.
        forbidden = ("anthropic", "openai", "AsyncAnthropic", "AsyncOpenAI")
        for name in dir(m):
            obj = getattr(m, name)
            mod = getattr(obj, "__module__", "") or ""
            for f in forbidden:
                assert f not in mod.lower(), (
                    f"forbidden import {f} found via {name}"
                )


# -----------------------------------------------------------------------
# Phase 12A.10D anti-overfitting cleanup — vertical token-pack isolation
# -----------------------------------------------------------------------


class TestVerticalTokenIsolation:
    """The general token bank must NOT contain devtool/SRE-specific
    phrases. Devtool-flavored phrases must live ONLY in the
    devtools_b2b vertical pack and be gated behind the
    ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS env var (default off)."""

    DEVTOOL_FLAVORED_TOKENS = (
        "i'd pilot", "spin it up", "kick the tires",
        "scratches a real itch", "real pain point", "fits how my team",
        "matches where my team", "maps to how my team",
        "intrigued enough", "leaning in",
    )

    def test_general_pack_excludes_devtool_phrases(self) -> None:
        from assembly.sources.intent_layer.inference import (
            _POSITIVE_INTEREST_TOKENS_GENERAL,
        )
        general = set(_POSITIVE_INTEREST_TOKENS_GENERAL)
        for tok in self.DEVTOOL_FLAVORED_TOKENS:
            assert tok not in general, (
                f"devtool-flavored token {tok!r} leaked into general "
                "pack — would force devtool vocabulary on consumer / "
                "non-tech briefs"
            )

    def test_devtools_b2b_pack_isolated(self) -> None:
        from assembly.sources.intent_layer.inference import (
            _POSITIVE_INTEREST_TOKENS_DEVTOOLS_B2B,
        )
        devtools = set(_POSITIVE_INTEREST_TOKENS_DEVTOOLS_B2B)
        for tok in self.DEVTOOL_FLAVORED_TOKENS:
            assert tok in devtools, (
                f"expected devtool-flavored token {tok!r} in devtools_b2b "
                "pack but it was missing"
            )

    def test_general_is_default(self, monkeypatch) -> None:
        from assembly.sources.intent_layer.inference import (
            _resolve_vertical_token_pack,
        )
        monkeypatch.delenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", raising=False,
        )
        assert _resolve_vertical_token_pack() == "general"

    def test_devtools_b2b_must_be_opt_in(self, monkeypatch) -> None:
        from assembly.sources.intent_layer.inference import (
            _active_positive_interest_tokens,
            _POSITIVE_INTEREST_TOKENS_GENERAL,
        )
        # Default (no env var): tokens equal the general pack only.
        monkeypatch.delenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", raising=False,
        )
        active = _active_positive_interest_tokens()
        assert tuple(active) == tuple(_POSITIVE_INTEREST_TOKENS_GENERAL)

    def test_devtools_b2b_opt_in_adds_devtool_phrases(
        self, monkeypatch,
    ) -> None:
        from assembly.sources.intent_layer.inference import (
            _active_positive_interest_tokens,
        )
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", "devtools_b2b",
        )
        active = set(_active_positive_interest_tokens())
        for tok in self.DEVTOOL_FLAVORED_TOKENS:
            assert tok in active, (
                f"devtool token {tok!r} should be in active set when "
                "vertical pack is devtools_b2b"
            )

    def test_invalid_vertical_falls_back_to_general(
        self, monkeypatch,
    ) -> None:
        from assembly.sources.intent_layer.inference import (
            _resolve_vertical_token_pack,
        )
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS",
            "nonexistent_vertical_xyz",
        )
        assert _resolve_vertical_token_pack() == "general"

    def test_devtool_phrase_routes_to_uncertain_under_general(
        self, monkeypatch,
    ) -> None:
        """Under general-only routing, the phrase 'I'd pilot it' must
        NOT trigger a positive_interest signal. This guarantees we
        don't silently use devtool vocab on consumer briefs."""
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", "general",
        )
        signal, _ = _derive(
            stance="curious_but_unconvinced",
            reasoning="I'd pilot this on my team.",
        )
        # Under general-only, "i'd pilot" is NOT a recognized
        # positive-interest token, so the ballot falls through to
        # the catch-all uncertain bucket.
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "uncertain", (
            f"general-only routing should NOT promote 'I'd pilot' to "
            f"receptive, got bucket={b} signal={signal}"
        )

    def test_devtool_phrase_routes_to_receptive_under_devtools_b2b(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", "devtools_b2b",
        )
        signal, _ = _derive(
            stance="curious_but_unconvinced",
            reasoning="I'd pilot this on my team.",
        )
        b, _ = map_intent_signal_to_market_bucket(signal)
        assert b == "receptive", (
            f"devtools_b2b routing SHOULD promote 'I'd pilot' to "
            f"receptive, got bucket={b} signal={signal}"
        )


# -----------------------------------------------------------------------
# Phase 12A.10D anti-overfitting cleanup — routing flag (downstream)
# -----------------------------------------------------------------------


class TestRoutingFlag:
    def test_routing_flag_default_off(self, monkeypatch) -> None:
        from assembly.sources.intent_layer.inference import (
            is_intent_signal_routing_enabled,
        )
        monkeypatch.delenv(
            "ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED", raising=False,
        )
        assert is_intent_signal_routing_enabled() is False

    def test_routing_flag_truthy_values(self, monkeypatch) -> None:
        from assembly.sources.intent_layer.inference import (
            is_intent_signal_routing_enabled,
        )
        for v in ("true", "1", "yes", "on", "TRUE", "True"):
            monkeypatch.setenv(
                "ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED", v,
            )
            assert is_intent_signal_routing_enabled() is True, (
                f"value {v!r} should be truthy"
            )

    def test_routing_flag_falsy_values(self, monkeypatch) -> None:
        from assembly.sources.intent_layer.inference import (
            is_intent_signal_routing_enabled,
        )
        for v in ("false", "0", "no", "off", "", "garbage"):
            monkeypatch.setenv(
                "ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED", v,
            )
            assert is_intent_signal_routing_enabled() is False, (
                f"value {v!r} should be falsy"
            )

    def test_pick_market_bucket_falls_back_when_routing_off(
        self,
    ) -> None:
        from assembly.calibration.market_buckets import (
            pick_market_bucket,
        )
        # Routing off: must use intent_label even if signal is present.
        bucket, _ = pick_market_bucket(
            intent_signal="curious_but_unconvinced",  # would be uncertain
            intent_label="would_consider_if_proven",  # legacy receptive
            intent_signal_routing_enabled=False,
        )
        assert bucket == "receptive"

    def test_pick_market_bucket_uses_signal_when_routing_on(
        self,
    ) -> None:
        from assembly.calibration.market_buckets import (
            pick_market_bucket,
        )
        bucket, _ = pick_market_bucket(
            intent_signal="curious_but_unconvinced",
            intent_label="would_consider_if_proven",
            intent_signal_routing_enabled=True,
        )
        assert bucket == "uncertain"

    def test_pick_market_bucket_signal_missing_uses_label(
        self,
    ) -> None:
        from assembly.calibration.market_buckets import (
            pick_market_bucket,
        )
        bucket, _ = pick_market_bucket(
            intent_signal=None,
            intent_label="loyal_to_current_alternative",
            intent_signal_routing_enabled=True,
        )
        assert bucket == "skeptical"

    def test_pick_market_bucket_both_missing_defaults_uncertain(
        self,
    ) -> None:
        from assembly.calibration.market_buckets import (
            pick_market_bucket,
        )
        bucket, warn = pick_market_bucket(
            intent_signal=None,
            intent_label=None,
            intent_signal_routing_enabled=True,
        )
        assert bucket == "uncertain"
        assert warn is not None


# -----------------------------------------------------------------------
# Anti-overfitting invariants — general routing must keep working on
# non-devtool examples even when devtools_b2b is OFF.
# -----------------------------------------------------------------------


class TestGeneralOnlyRoutingStillWorks:
    """Even with devtools_b2b OFF, the general path must still route
    the canonical examples from the user spec correctly."""

    def test_uncertain_examples_general_only(self, monkeypatch) -> None:
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", "general",
        )
        for text in (
            "How does this handle errors?",
            "Can you prove it works on real data?",
            "Interesting, but I'd need to see it in production.",
            "Maybe useful, but I'm not sure.",
            "Would need a demo first.",
        ):
            signal, _ = _derive(
                stance="curious_but_unconvinced", reasoning=text,
            )
            b, _ = map_intent_signal_to_market_bucket(signal)
            assert b == "uncertain", (
                f"general-only uncertain example failed: text={text!r}, "
                f"signal={signal}, bucket={b}"
            )

    def test_receptive_examples_general_only(self, monkeypatch) -> None:
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", "general",
        )
        for text in (
            "This is useful if it works; I would consider trying it.",
            "Looks promising — could be useful for our team.",
            "I'd compare this against what we use.",
        ):
            signal, _ = _derive(
                stance="interested_if_proven", reasoning=text,
            )
            b, _ = map_intent_signal_to_market_bucket(signal)
            assert b == "receptive", (
                f"general-only receptive example failed: text={text!r}, "
                f"signal={signal}, bucket={b}"
            )

    def test_skeptical_examples_general_only(self, monkeypatch) -> None:
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", "general",
        )
        for text in (
            "I don't trust this for my workflow.",
            "Too expensive for what it offers.",
            "Won't pay for this.",
            "Not for me.",
        ):
            signal, _ = _derive(
                stance="skeptical", reasoning=text,
            )
            b, _ = map_intent_signal_to_market_bucket(signal)
            assert b == "skeptical", (
                f"general-only skeptical example failed: text={text!r}, "
                f"signal={signal}, bucket={b}"
            )

    def test_buyer_examples_general_only(self, monkeypatch) -> None:
        monkeypatch.setenv(
            "ASSEMBLY_INTENT_SIGNAL_VERTICAL_TOKENS", "general",
        )
        for text in (
            "I'll try this.",
            "Sign me up.",
            "We need this.",
        ):
            signal, _ = _derive(
                stance="interested_if_proven", reasoning=text,
            )
            b, _ = map_intent_signal_to_market_bucket(signal)
            assert b == "buyer", (
                f"general-only buyer example failed: text={text!r}, "
                f"signal={signal}, bucket={b}"
            )
