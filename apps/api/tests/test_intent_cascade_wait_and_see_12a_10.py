"""Phase 12A.10 — intent cascade structural-surface tests
(post Phase 12A.10C rollback).

The runtime cascade was reverted to its pre-12A.10 shape after the
Opslane post-fix MAE regression (9.40pp → 20.14pp; see Phase 12A.10B
replay). This test file now verifies:

  - The `wait_and_see` label REMAINS in the schema, INTENT_LABELS
    tuple, bucket vocabulary, and DB CHECK constraint (structural
    surface preserved for the future intent_signal enum fix).
  - The runtime cascade does NOT emit `wait_and_see` for any input
    — the curious/needs_info branch and the catch-all both route
    back to `would_consider_if_proven`.
  - All other cascade paths (buyer / loyal / reject / waitlist /
    share / interested_if_proven) remain backward-compatible.
  - The locked Phase 12A.8 prediction artifact and Phase 12A.9
    score remain byte-identical.

All tests are pure-Python, no LLM, no DB writes.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from assembly.calibration.market_buckets import (
    ASSEMBLY_LABEL_TO_BUCKET,
    map_assembly_intent_to_market_bucket,
)
from assembly.models.intent import INTENT_LABELS
from assembly.sources.intent_layer.inference import (
    infer_simulated_intent,
)
from assembly.sources.intent_layer.schemas import (
    IntentLabel, SimulatedIntentDraft,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _psy(**overrides: float) -> dict[str, float]:
    """Neutral psychology vector; override specific traits."""
    base = {
        "openness": 0.5,
        "novelty_seeking": 0.5,
        "trust_proof_threshold": 0.5,
        "risk_tolerance": 0.5,
        "price_sensitivity": 0.5,
        "social_influence_susceptibility": 0.5,
        "category_involvement_or_expertise": 0.5,
    }
    base.update(overrides)
    return base


def _infer(
    *,
    role: str = "generic_buyer",
    psy: dict[str, float] | None = None,
    pre_stance: str = "curious_but_unconvinced",
    final_stance: str = "curious_but_unconvinced",
    corpus: str = "",
    delta: str | None = None,
) -> SimulatedIntentDraft:
    pre_ballot: dict[str, Any] = {"private_stance": pre_stance}
    final_ballot: dict[str, Any] = {"private_stance": final_stance}
    if delta:
        final_ballot["public_private_delta"] = delta
    return infer_simulated_intent(
        persona_id="p_test",
        cohort_id=None,
        normalized_role=role,
        psychology_value_map=psy or _psy(),
        pre_ballot=pre_ballot,
        final_ballot=final_ballot,
        reflection_ballot=None,
        persona_text_corpus=corpus,
        ballot_ids=[],
        discussion_turn_ids=[],
        memory_atom_ids=[],
    )


# ---------------------------------------------------------------------------
# 1. Schema / vocabulary
# ---------------------------------------------------------------------------


class TestSchemaVocab:
    def test_wait_and_see_in_intent_label_literal(self) -> None:
        from typing import get_args
        assert "wait_and_see" in get_args(IntentLabel)

    def test_wait_and_see_in_INTENT_LABELS_tuple(self) -> None:
        assert "wait_and_see" in INTENT_LABELS
        assert len(INTENT_LABELS) == 10

    def test_wait_and_see_accepted_by_pydantic_draft(self) -> None:
        d = SimulatedIntentDraft(
            persona_id="p1",
            stance_label="curious_but_unconvinced",
            simulated_intent="wait_and_see",
            intent_strength="low",
            switching_status="weakly_attached_to_alternative",
            evidence_basis="rule:wait_and_see synthetic",
            confidence="low",
            caveat="synthetic — not a real-world forecast",
        )
        assert d.simulated_intent == "wait_and_see"

    def test_unknown_intent_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SimulatedIntentDraft(
                persona_id="p1",
                stance_label="curious_but_unconvinced",
                simulated_intent="please_call_me_back",  # type: ignore[arg-type]
                intent_strength="low",
                switching_status="weakly_attached_to_alternative",
                evidence_basis="x",
                confidence="low",
                caveat="x",
            )


# ---------------------------------------------------------------------------
# 2. Bucket mapping
# ---------------------------------------------------------------------------


class TestBucketMapping:
    def test_wait_and_see_maps_to_uncertain(self) -> None:
        assert ASSEMBLY_LABEL_TO_BUCKET["wait_and_see"] == "uncertain"
        bucket, warn = map_assembly_intent_to_market_bucket("wait_and_see")
        assert bucket == "uncertain"
        assert warn is None

    def test_existing_label_mappings_backward_compatible(self) -> None:
        # spot-check 5 key mappings still hold from Phase 12A.1
        assert ASSEMBLY_LABEL_TO_BUCKET["would_buy_now"] == "buyer"
        assert ASSEMBLY_LABEL_TO_BUCKET["would_consider_if_proven"] == "receptive"
        assert ASSEMBLY_LABEL_TO_BUCKET["would_join_waitlist"] == "receptive"
        assert ASSEMBLY_LABEL_TO_BUCKET["loyal_to_current_alternative"] == "skeptical"
        assert ASSEMBLY_LABEL_TO_BUCKET["would_reject"] == "skeptical"


# ---------------------------------------------------------------------------
# 3. Cascade — existing paths must still fire
# ---------------------------------------------------------------------------


class TestExistingCascadePathsBackwardCompatible:
    def test_explicit_buy_intent_still_maps_to_would_buy_now(self) -> None:
        # interested_if_proven + buy token → would_buy_now (buyer)
        d = _infer(
            final_stance="interested_if_proven",
            corpus="I'm sold. I'd buy this today for our team.",
        )
        assert d.simulated_intent == "would_buy_now"

    def test_loyalty_token_maps_to_loyal_to_current_alternative(self) -> None:
        # soft stance + loyalty token → loyal_to_current_alternative
        d = _infer(
            final_stance="skeptical",
            corpus="I'll stick with my current setup. Already works.",
        )
        assert d.simulated_intent == "loyal_to_current_alternative"

    def test_explicit_rejection_maps_to_would_reject(self) -> None:
        d = _infer(
            final_stance="likely_reject",
            corpus="Not interested. This isn't for me.",
        )
        assert d.simulated_intent == "would_reject"

    def test_interested_if_proven_high_trust_maps_to_consider(self) -> None:
        # interested_if_proven + high trust threshold + no positive
        # tokens → would_consider_if_proven (receptive)
        d = _infer(
            final_stance="interested_if_proven",
            psy=_psy(trust_proof_threshold=0.7, novelty_seeking=0.4),
            corpus="Looks promising. Need to see real benchmarks first.",
        )
        assert d.simulated_intent == "would_consider_if_proven"

    def test_waitlist_token_maps_to_would_join_waitlist(self) -> None:
        d = _infer(
            final_stance="interested_if_proven",
            corpus="Where's the waitlist? Notify me when launched.",
        )
        assert d.simulated_intent == "would_join_waitlist"

    def test_share_token_maps_to_would_share_with_friend(self) -> None:
        d = _infer(
            final_stance="interested_if_proven",
            corpus="I'd recommend it to my running group.",
        )
        assert d.simulated_intent == "would_share_with_friend"


# ---------------------------------------------------------------------------
# 4. Cascade — NEW Phase 12A.10 behavior
# ---------------------------------------------------------------------------


class TestPostRollbackCascadeBehavior:
    """Phase 12A.10C — assert the cascade emits the pre-12A.10
    routings for ambiguous and catch-all personas. `wait_and_see`
    remains a structural label but is not emitted at runtime."""

    def test_ambiguous_curious_persona_routes_to_consider(self) -> None:
        d = _infer(
            final_stance="curious_but_unconvinced",
            psy=_psy(novelty_seeking=0.4),
            corpus=(
                "I don't know how this would fit our setup. Curious "
                "about how it handles edge cases."
            ),
        )
        assert d.simulated_intent == "would_consider_if_proven"

    def test_needs_more_information_routes_to_consider(self) -> None:
        d = _infer(
            final_stance="needs_more_information",
            corpus="Hard to tell without more context.",
        )
        assert d.simulated_intent == "would_consider_if_proven"

    def test_curious_with_high_novelty_psy_routes_to_consider(self) -> None:
        d = _infer(
            final_stance="curious_but_unconvinced",
            psy=_psy(novelty_seeking=0.75),
            corpus="Cool different approach to this problem space.",
        )
        assert d.simulated_intent == "would_consider_if_proven"

    def test_curious_with_novelty_token_routes_to_consider_or_buy(
        self,
    ) -> None:
        d = _infer(
            final_stance="curious_but_unconvinced",
            psy=_psy(novelty_seeking=0.4),
            corpus="New format. I'd try it on a side project.",
        )
        # 'i'd try' matches _BUY_NOW_TOKENS — earlier rule may fire
        assert d.simulated_intent in (
            "would_buy_now", "would_consider_if_proven",
        )

    def test_catch_all_routes_to_consider_not_wait_and_see(self) -> None:
        """Post-rollback: a skeptical persona that escapes every
        earlier rule must land on would_consider_if_proven (V0
        behavior), NOT wait_and_see."""
        d = _infer(
            role="performance_focused_buyer",
            final_stance="skeptical",
            psy=_psy(price_sensitivity=0.4),
            corpus="Hmm. Unclear.",
        )
        assert d.simulated_intent == "would_consider_if_proven", (
            f"catch-all emitted {d.simulated_intent!r}, expected "
            "would_consider_if_proven post-rollback"
        )

    def test_runtime_cascade_never_emits_wait_and_see(self) -> None:
        """Sweep a representative set of ambiguous / catch-all inputs;
        the post-rollback cascade must not produce `wait_and_see` for
        any of them. The label remains in the vocabulary for the
        future intent_signal enum fix but is unreachable at runtime."""
        ambiguous_inputs = [
            {"final_stance": "curious_but_unconvinced", "corpus": ""},
            {
                "final_stance": "curious_but_unconvinced",
                "corpus": "I don't know.",
                "psy": _psy(novelty_seeking=0.3),
            },
            {
                "final_stance": "needs_more_information",
                "corpus": "not sure",
            },
            {
                "role": "performance_focused_buyer",
                "final_stance": "skeptical",
                "psy": _psy(price_sensitivity=0.4),
                "corpus": "Hmm.",
            },
        ]
        for kw in ambiguous_inputs:
            d = _infer(**kw)
            assert d.simulated_intent != "wait_and_see", (
                f"unexpected wait_and_see for input {kw!r}: got {d!r}"
            )

    def test_consider_evidence_basis_is_audible(self) -> None:
        """The ambiguous-curious rule and the catch-all both log a
        distinct rule name; audits can still trace the decision."""
        d = _infer(
            final_stance="curious_but_unconvinced",
            psy=_psy(novelty_seeking=0.3),
            corpus="not sure",
        )
        assert d.simulated_intent == "would_consider_if_proven"
        assert "rule:would_consider_if_proven_unsure" in d.evidence_basis


# ---------------------------------------------------------------------------
# 5. End-to-end: ambiguous personas land in RECEPTIVE bucket (post-rollback)
# ---------------------------------------------------------------------------


class TestEndToEndBucketRouting:
    def test_ambiguous_persona_pipeline_lands_in_receptive_bucket(
        self,
    ) -> None:
        """Post-rollback (Phase 12A.10C): ambiguous-curious persona
        routes to would_consider_if_proven → receptive bucket. This
        is the restored pre-12A.10 behavior; the failed 12A.10 attempt
        to route here to uncertain was reverted after MAE regressed."""
        d = _infer(
            final_stance="curious_but_unconvinced",
            psy=_psy(novelty_seeking=0.4),
            corpus="hard to tell if this fits us",
        )
        assert d.simulated_intent == "would_consider_if_proven"
        bucket, _ = map_assembly_intent_to_market_bucket(
            d.simulated_intent
        )
        assert bucket == "receptive"

    def test_receptive_persona_pipeline_lands_in_receptive_bucket(
        self,
    ) -> None:
        """Cautious-but-positive (interested_if_proven + high trust)
        persona stays in receptive bucket — unchanged by rollback."""
        d = _infer(
            final_stance="interested_if_proven",
            psy=_psy(trust_proof_threshold=0.7),
            corpus="Looks useful if you can prove the false-positive rate.",
        )
        assert d.simulated_intent == "would_consider_if_proven"
        bucket, _ = map_assembly_intent_to_market_bucket(
            d.simulated_intent
        )
        assert bucket == "receptive"

    def test_buyer_persona_pipeline_lands_in_buyer_bucket(self) -> None:
        d = _infer(
            final_stance="interested_if_proven",
            corpus="I'd buy this today. shut up and take my money.",
        )
        assert d.simulated_intent == "would_buy_now"
        bucket, _ = map_assembly_intent_to_market_bucket(
            d.simulated_intent
        )
        assert bucket == "buyer"

    def test_skeptical_persona_pipeline_lands_in_skeptical_bucket(
        self,
    ) -> None:
        d = _infer(
            final_stance="skeptical",
            corpus="I'll stick with my current setup. Already works.",
        )
        assert d.simulated_intent == "loyal_to_current_alternative"
        bucket, _ = map_assembly_intent_to_market_bucket(
            d.simulated_intent
        )
        assert bucket == "skeptical"


# ---------------------------------------------------------------------------
# 6. Migration / DB constraint drift
# ---------------------------------------------------------------------------


class TestDbConstraintParity:
    def test_check_constraint_string_lists_wait_and_see(self) -> None:
        """Drift check: the SQLAlchemy CheckConstraint string must
        list wait_and_see, otherwise a live insert with the new
        label would fail."""
        from pathlib import Path
        models_src = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "models" / "intent.py"
        ).read_text(encoding="utf-8")
        # The constraint is a multi-line string concatenation;
        # we look for the literal label substring.
        assert "'wait_and_see'" in models_src

    def test_alembic_migration_exists_for_phase_12a_10(self) -> None:
        from pathlib import Path
        migrations_dir = (
            Path(__file__).resolve().parent.parent / "alembic" / "versions"
        )
        candidates = list(
            migrations_dir.glob("*phase_12a_10*wait_and_see*.py")
        )
        assert len(candidates) == 1, (
            f"expected exactly one Phase 12A.10 migration, "
            f"found {[c.name for c in candidates]}"
        )
        text = candidates[0].read_text(encoding="utf-8")
        assert "wait_and_see" in text
        assert "ck_simulated_intents_intent_label" in text


# ---------------------------------------------------------------------------
# 7. Phase 12A.9 prediction artifact must be untouched by this phase
# ---------------------------------------------------------------------------


class TestPhase12A9ArtifactIntegrity:
    def test_opslane_prediction_artifact_unchanged(self) -> None:
        """The locked Phase 12A.8 Opslane prediction artifact must
        not be mutated by Phase 12A.10. If this hash drifts, the
        original blind validation result is invalidated."""
        import hashlib
        from pathlib import Path
        artifact = Path(
            "/Users/hamza40/Desktop/Aseembly/assembly-v0/apps/api/"
            "_audit/live_runs/f8aff6fc-a75f-43ef-8cf2-f3ec09e023d9/"
            "founder_report.json"
        )
        if not artifact.exists():
            pytest.skip(
                "Opslane prediction artifact not present in this checkout"
            )
        h = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert h == (
            "efb60159ddc7c9a11bfdcc157789f427"
            "012434e5af87265c35630216b80cc095"
        ), f"Opslane prediction artifact hash drifted: {h}"
