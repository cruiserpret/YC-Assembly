"""Phase 12F.1 — Founder Trust + Explainability tests.

Covers the 16 invariants in the Phase 12F.1 task list:

  1. Legacy briefs still validate (no required field added).
  2. New context fields are all optional.
  3. Explainability block exists in the report assembly.
  4. fields_provided / fields_missing computed correctly.
  5. confidence.limited_by always populated.
  6. Confidence score bounded [0..1] and capped at 0.85.
  7. Persona cards always carry evidence_anchor references.
  8. Persona cards do not expose raw chain-of-thought.
  9. Niche signals have evidence_anchors.
 10. one_question_for_real_customers is phrased as a question.
 11. No new LLM calls (modules import without provider).
 12. No apps/web changes (static grep).
 13. No DB migration in 12F.1.
 14. No pricing UI in 12F.1.
 15. No CompanyContext routes / tables yet.
 16. No Interview Mode yet.

All tests run without DB, without LLM, and without network.
"""
from __future__ import annotations

import importlib
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.explainability import (
    build_explainability_panel,
    build_niche_signals,
    build_persona_reasoning_cards,
    compute_confidence,
)
from assembly.explainability.confidence_score import (
    CONFIDENCE_HARD_CAP_12F1,
)
from assembly.schemas.founder_brief import FounderBriefIn


API_ROOT = Path(__file__).resolve().parents[1]   # apps/api/
REPO_ROOT = Path(__file__).resolve().parents[3]  # assembly-v0/ (git root)


# -----------------------------------------------------------------------
# Test fixtures
# -----------------------------------------------------------------------


def _legacy_brief() -> dict:
    """Pre-12F.1 brief shape. Must keep validating."""
    return {
        "product_name": "DocuSeal",
        "product_description": (
            "Open-source document signing platform for B2B teams."
        ),
        "price_or_price_structure": "$40/mo per company",
        "launch_geography": "United States",
        "target_customers": ["B2B operations teams"],
        "competitors_or_alternatives": ["DocuSign", "HelloSign"],
        "launch_state": "unlaunched",
    }


def _rich_brief() -> dict:
    """Brief with full 12F.1 context populated. Mirrors what we'd
    pass during Krumbit rerun."""
    return {
        **_legacy_brief(),
        "category_hint": "devtools_b2b",
        "launch_source": "hn_show_hn",
        "company_stage": "pre_pmf",
        "current_traction": {"users": 50, "time_in_market_months": 3},
        "retention_or_churn_signal": "5/10 trial users converted",
        "founder_hypothesis": (
            "Mid-market teams will adopt an open-source signing tool "
            "if it matches DocuSign's reliability."
        ),
        "customer_interviews": [
            {"quote": "I'd switch if migration was painless.",
             "segment": "mid_market_ops"},
            {"quote": "Audit trail matters more than UX.",
             "segment": "legal_ops"},
        ],
        "known_objections": [
            "no_audit_trail", "migration_friction",
            "security_certifications_unclear",
        ],
        "icp_segments": [
            {"label": "mid_market_ops",
             "description": "20-200 person companies signing 100+/mo docs",
             "est_size_pct": 60.0},
        ],
        "pricing_assumptions": {
            "model": "tiered",
            "tiers": [
                {"name": "Team", "price_usd": 40.0,
                 "includes": "5 seats + audit log"},
                {"name": "Business", "price_usd": 120.0,
                 "includes": "20 seats + SSO + premium support"},
            ],
        },
        "gtm_channel": "hn_show_hn",
        "competitors_with_context": [
            {"name": "DocuSign",
             "why_they_win": "incumbent + integrations",
             "why_they_lose": "expensive per-seat"},
        ],
        "current_messaging": "Open-source DocuSign alternative.",
        "decision_being_tested": (
            "Should we prioritize DocuSign migration tools or our own SSO?"
        ),
        "what_would_change_my_mind": (
            "If 2/3 of mid-market interviewees prefer SSO over migration"
        ),
    }


class _SnapshotStub:
    """Minimal stand-in for an EvidenceSnapshot. Only the fields the
    confidence score and explainability panel read."""

    def __init__(self, raw=47, accepted=32):
        self.evidence_snapshot_id = "evsnap_test_001"
        self.snapshot_hash = "abc123"
        self.brief_hash = "def456"
        self.raw_result_count = raw
        self.accepted_evidence_count = accepted
        self.raw_evidence_items = [
            {"source": "hn"}, {"source": "hn"}, {"source": "blog"},
        ]


def _make_ctx(
    n_target=4, n_competitor=2, n_proof_seeker=2,
    include_snapshot=True, launch_source="hn_show_hn",
) -> dict:
    """Build a ctx dict resembling what live_founder_brief.py produces
    just before founder_report.json assembly."""
    drafts: list[dict] = []
    pre_dicts: dict[str, dict] = {}
    final_dicts: dict[str, dict] = {}
    persona_meta: dict[str, dict] = {}
    persona_ids: list[str] = []

    def _add(idx, audience_role, segment, intent, stance_pre, stance_final,
             objection, proof, conditions=(), rejection=None, scorable=True,
             is_synthetic=False, anchor_extra=""):
        pid = f"p{idx:02d}"
        persona_ids.append(pid)
        drafts.append({
            "persona_id": pid,
            "cohort_id": "live_cohort_0",
            "audience_role": audience_role,
            "is_synthetic_non_customer_voice": is_synthetic,
            "is_scorable": scorable,
            "default_bucket": "uncertain",
            "stance_label": "curious_but_unconvinced",
            "simulated_intent": intent,
            "intent_strength": "medium",
            "switching_status": "weakly_attached_to_alternative",
            "current_alternative": None,
            "conditions_to_buy": list(conditions),
            "reason_for_rejection": rejection,
            "proof_needed": [proof] if proof else [],
            "evidence_basis": (
                f"rule:{intent} (final=likely_accept, role={audience_role}"
                f"{anchor_extra})"
            ),
            "discussion_turn_ids": [],
            "ballot_ids": [],
            "memory_atom_ids": [],
            "confidence": "medium",
            "caveat": "test",
            "intent_signal": None,
            "intent_signal_basis": None,
        })
        pre_dicts[pid] = {
            "private_stance": stance_pre,
            "private_reasoning": "test reasoning pre",
            "top_objection": objection,
            "top_proof_need": proof,
        }
        final_dicts[pid] = {
            "private_stance": stance_final,
            "private_reasoning": "test reasoning final",
            "public_private_delta": "no_change",
            "top_objection": objection,
            "top_proof_need": proof,
        }
        persona_meta[pid] = {"segment_label": segment}

    # Target customers
    for i in range(n_target):
        _add(
            i, "target_customer_evaluator",
            f"trust_seeker_{i % 2}",
            "would_consider_if_proven" if i % 2 == 0 else "wait_and_see",
            "curious_but_unconvinced",
            "interested_if_proven" if i % 2 == 0
            else "curious_but_unconvinced",
            "pricing seems high for small teams",
            "case_study_from_similar_company",
            conditions=["3+ similar logos visible", "self-serve trial"]
                if i % 2 == 0 else [],
        )
    # Competitor users
    for i in range(n_competitor):
        idx = n_target + i
        _add(
            idx, "existing_competitor_user",
            "competitor_user_docusign",
            "loyal_to_current_alternative",
            "skeptical",
            "skeptical" if i % 2 == 0 else "needs_more_information",
            "switching cost too high",
            "migration_guide",
            rejection=(
                "current vendor already solves this"
                if i % 2 == 0 else None
            ),
            anchor_extra=" (competitor)",
        )
    # Proof seekers (locked to uncertain)
    for i in range(n_proof_seeker):
        idx = n_target + n_competitor + i
        _add(
            idx, "proof_seeker_only",
            "trust_seeker_0",
            "wait_and_see",
            "needs_more_information",
            "needs_more_information",
            "I need to see audit trail proof",
            "proof_the_audit_trail_works",
            is_synthetic=True,
            anchor_extra=" (synthetic)",
        )
    # One unique edge-case condition to test edge_case_use_cases
    drafts[0]["conditions_to_buy"].append(
        "use it for redlining only, not signatures",
    )
    cohort_persona_lists = [persona_ids]
    cohort_summaries = [{
        "cohort_label": "trust_seeker::interested_if_proven",
        "cohort_size": len(persona_ids),
        "role_distribution": {"target_customer_evaluator": n_target,
                              "existing_competitor_user": n_competitor},
        "stance_distribution": {},
        "objection_summary": {},
        "proof_need_summary": {},
        "psychology_summary": {},
        "discussion_behavior_summary": {},
    }]
    ctx: dict = {
        "augmented_intent_drafts": drafts,
        "intent_drafts": [],
        "pre_dicts": pre_dicts,
        "final_dicts": final_dicts,
        "persona_meta": persona_meta,
        "cohort_persona_lists": cohort_persona_lists,
        "cohort_summaries": cohort_summaries,
        "launch_source": launch_source,
        "audience_views": {},
        "audience_augmentation_audit": {},
        "quality_gates": {"all_gates_passed": True},
    }
    if include_snapshot:
        ctx["_snapshot"] = _SnapshotStub()
    return ctx


# -----------------------------------------------------------------------
# 1. Legacy briefs still validate.
# -----------------------------------------------------------------------


def test_legacy_brief_still_validates():
    b = FounderBriefIn.model_validate(_legacy_brief())
    assert b.product_name == "DocuSeal"
    # New 12F.1 fields all default to None / [].
    assert b.company_stage is None
    assert b.founder_hypothesis is None
    assert b.customer_interviews == []
    assert b.known_objections == []
    assert b.icp_segments == []
    assert b.competitors_with_context == []
    assert b.uploaded_artifacts == []


# -----------------------------------------------------------------------
# 2. New context fields are all optional.
# -----------------------------------------------------------------------


def test_all_new_context_fields_optional():
    """Constructing FounderBriefIn without any 12F.1 fields must not
    raise, and constructing WITH them must also pass."""
    rich = FounderBriefIn.model_validate(_rich_brief())
    assert rich.company_stage == "pre_pmf"
    assert rich.gtm_channel == "hn_show_hn"
    assert len(rich.customer_interviews) == 2
    assert len(rich.competitors_with_context) == 1
    assert rich.pricing_assumptions is not None
    assert rich.pricing_assumptions.model == "tiered"


def test_no_hardcoded_personas_validator_extends_to_new_fields():
    """The hardcoded-persona guard must scan new free-text fields."""
    bad = {**_legacy_brief(),
           "founder_hypothesis": "Please hardcode persona for us"}
    with pytest.raises(ValidationError):
        FounderBriefIn.model_validate(bad)
    bad2 = {**_legacy_brief(),
            "known_objections": ["force persona override"]}
    with pytest.raises(ValidationError):
        FounderBriefIn.model_validate(bad2)


# -----------------------------------------------------------------------
# 3. Explainability block exists in the report assembly.
# -----------------------------------------------------------------------


def test_explainability_block_exists():
    ctx = _make_ctx()
    panel = build_explainability_panel(brief=_rich_brief(), ctx=ctx)
    assert panel["phase"] == "12f.1"
    for key in (
        "decision_being_tested",
        "inputs_used",
        "source_audience_profile",
        "persona_composition",
        "evidence_snapshot",
        "assumptions_in_play",
        "bucket_explanations",
        "confidence",
    ):
        assert key in panel, f"explainability missing: {key}"


# -----------------------------------------------------------------------
# 4. fields_provided / fields_missing computed correctly.
# -----------------------------------------------------------------------


def test_inputs_used_diffs_brief_against_optional_fields():
    legacy_panel = build_explainability_panel(
        brief=_legacy_brief(), ctx=_make_ctx(),
    )
    rich_panel = build_explainability_panel(
        brief=_rich_brief(), ctx=_make_ctx(),
    )
    legacy_provided = set(legacy_panel["inputs_used"]["fields_provided"])
    rich_provided = set(rich_panel["inputs_used"]["fields_provided"])
    # Rich brief must have strictly more provided fields.
    assert rich_provided > legacy_provided
    # Specific 12F.1 fields must appear in rich but not legacy.
    for f in (
        "company_stage", "founder_hypothesis", "customer_interviews",
        "pricing_assumptions", "gtm_channel", "competitors_with_context",
        "decision_being_tested",
    ):
        assert f in rich_provided
        assert f not in legacy_provided
        # And conversely must be in legacy.fields_missing.
        assert f in legacy_panel["inputs_used"]["fields_missing"]
    # Counts must be consistent.
    assert (
        rich_panel["inputs_used"]["n_provided"]
        > legacy_panel["inputs_used"]["n_provided"]
    )


# -----------------------------------------------------------------------
# 5. confidence.limited_by always populated.
# -----------------------------------------------------------------------


def test_confidence_limited_by_never_empty():
    # Empty / minimal brief case
    minimal = compute_confidence(
        brief={}, ctx={}, launch_source=None,
    )
    assert isinstance(minimal["limited_by"], list)
    assert len(minimal["limited_by"]) >= 1
    # Maximally-populated brief case
    maxed = compute_confidence(
        brief=_rich_brief(), ctx=_make_ctx(), launch_source="hn_show_hn",
    )
    assert len(maxed["limited_by"]) >= 1
    # The cap itself is always a limiter once it bites.
    if maxed["cap_applied"]:
        assert any(
            "cap_at_0.85" in l for l in maxed["limited_by"]
        )


# -----------------------------------------------------------------------
# 6. Confidence score bounded [0..1] and capped at 0.85.
# -----------------------------------------------------------------------


def test_confidence_score_bounded_and_capped():
    for brief, ls in [
        ({}, None),
        (_legacy_brief(), None),
        (_rich_brief(), "hn_show_hn"),
        (_rich_brief(), "default"),
    ]:
        result = compute_confidence(
            brief=brief, ctx=_make_ctx(), launch_source=ls,
        )
        assert 0.0 <= result["score"] <= 1.0
        assert 0.0 <= result["score_raw"] <= 1.0
        assert result["score"] <= CONFIDENCE_HARD_CAP_12F1
        assert result["cap"] == CONFIDENCE_HARD_CAP_12F1
        # Level not "high" unless evidence supports it. Since the cap
        # is 0.85, the level CAN be "high" only when raw_score and
        # capped_score both clear 0.75. Confirm that doesn't happen
        # spuriously.
        if result["level"] == "high":
            assert result["score"] >= 0.75
            # AND it must still respect the cap.
            assert result["score"] <= CONFIDENCE_HARD_CAP_12F1


def test_confidence_breakdown_per_factor_bounded():
    result = compute_confidence(
        brief=_rich_brief(), ctx=_make_ctx(), launch_source="hn_show_hn",
    )
    breakdown = result["breakdown"]
    assert set(breakdown.keys()) == set(result["weights"].keys())
    for k, v in breakdown.items():
        assert 0.0 <= v <= 1.0, f"{k}: {v} out of bounds"


def test_confidence_high_requires_evidence_support():
    """Empty brief should never produce 'high'. Even maxed-out brief
    is capped at 0.85 → still 'high' only if it clears 0.75 BUT the
    actual level we observe today is 'medium' or below."""
    empty = compute_confidence(brief={}, ctx={}, launch_source=None)
    assert empty["level"] != "high"


# -----------------------------------------------------------------------
# 7. Persona cards always carry evidence_anchor references.
# -----------------------------------------------------------------------


def test_persona_cards_carry_evidence_anchors():
    cards = build_persona_reasoning_cards(ctx=_make_ctx(), n=8)
    assert len(cards) >= 1
    for c in cards:
        # what_moved_or_failed_to_move_them is required and must
        # carry an evidence_anchor.
        m = c["what_moved_or_failed_to_move_them"]
        assert m["evidence_anchor"], (
            f"card for {c['persona_id']} missing evidence_anchor"
        )
        # Optional fields, when present, must carry an anchor.
        for opt in ("top_objection", "top_proof_need",
                    "adoption_trigger", "stayed_x_because"):
            if c.get(opt) is not None:
                assert "evidence_anchor" in c[opt]
                assert c[opt]["evidence_anchor"]


# -----------------------------------------------------------------------
# 8. Persona cards do not expose raw chain-of-thought.
# -----------------------------------------------------------------------


def test_persona_cards_do_not_expose_chain_of_thought():
    """Cards must not contain raw LLM reasoning text — only
    structured artifacts. Sentinel: no field named 'reasoning',
    'private_reasoning', 'raw_output', 'thinking'."""
    cards = build_persona_reasoning_cards(ctx=_make_ctx(), n=8)
    forbidden_keys = {
        "reasoning", "private_reasoning", "raw_output",
        "thinking", "chain_of_thought", "llm_response",
    }

    def _scan(obj, path="") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in forbidden_keys, (
                    f"forbidden key {k!r} at {path}"
                )
                _scan(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _scan(v, f"{path}[{i}]")
    _scan(cards)


# -----------------------------------------------------------------------
# 9. Niche signals have evidence_anchors.
# -----------------------------------------------------------------------


def test_niche_signals_cite_anchors():
    ctx = _make_ctx(n_target=4, n_competitor=2, n_proof_seeker=2)
    # Inject a minority objection raised by exactly 2 personas across
    # 2 different roles → must surface as a minority objection. With
    # n_target=4, indices p00-p03 are target_customer_evaluator and
    # p04-p05 are existing_competitor_user.
    ctx["final_dicts"]["p00"]["top_objection"] = (
        "Audit trail export to S3 is missing"
    )
    ctx["final_dicts"]["p04"]["top_objection"] = (
        "Audit trail export to S3 is missing"
    )
    out = build_niche_signals(brief=_rich_brief(), ctx=ctx)
    assert isinstance(out["minority_objections"], list)
    found = False
    for entry in out["minority_objections"]:
        if "audit trail" in entry["cluster_id"]:
            found = True
            assert entry["evidence_anchors"], "anchors required"
            assert len(entry["raised_by_roles"]) >= 2
    assert found, "Did not surface the seeded minority objection"
    # Every minority objection entry MUST have anchors.
    for entry in out["minority_objections"]:
        assert entry["evidence_anchors"]
    # Edge-case use cases also cite anchors.
    for ec in out["edge_case_use_cases"]:
        assert ec["evidence_anchor"]


# -----------------------------------------------------------------------
# 10. one_question_for_real_customers is phrased as a question.
# -----------------------------------------------------------------------


def test_one_question_is_a_question():
    ctx = _make_ctx(n_target=4, n_competitor=2, n_proof_seeker=2)
    # Inject a minority objection across 2 roles so the one_question
    # logic has material to pick from. p00 = target_customer_evaluator;
    # p04 = existing_competitor_user.
    ctx["final_dicts"]["p00"]["top_objection"] = (
        "no compliance certification visible"
    )
    ctx["final_dicts"]["p04"]["top_objection"] = (
        "no compliance certification visible"
    )
    out = build_niche_signals(brief=_rich_brief(), ctx=ctx)
    q = out["one_question_for_real_customers"]
    if q is not None:
        assert q.endswith("?"), f"Not phrased as question: {q!r}"
        # Even when phrased as a question, must not be a verdict.
        for forbidden in (
            "you should", "we recommend", "the answer is",
            "this means you must",
        ):
            assert forbidden not in q.lower()


def test_one_question_skips_known_objections():
    """If the founder already listed a known_objection, the niche
    signal should NOT echo it back."""
    ctx = _make_ctx(n_target=4, n_competitor=2, n_proof_seeker=2)
    # p00 = target_customer_evaluator; p04 = existing_competitor_user.
    ctx["final_dicts"]["p00"]["top_objection"] = "migration_friction"
    ctx["final_dicts"]["p04"]["top_objection"] = "migration_friction"
    rich = _rich_brief()  # known_objections includes "migration_friction"
    out = build_niche_signals(brief=rich, ctx=ctx)
    q = out["one_question_for_real_customers"]
    if q is not None:
        assert "migration_friction" not in q.lower()


# -----------------------------------------------------------------------
# 11. No new LLM calls (the explainability module imports without
# requiring an LLM provider).
# -----------------------------------------------------------------------


def test_explainability_module_does_not_import_llm_provider():
    """Importing the explainability package in a fresh interpreter
    must not pull in any LLM provider SDK, the LLM router, or
    HTTP-touching modules. Uses subprocess to bypass sys.modules
    pollution from sibling tests."""
    script = (
        "import sys\n"
        "import importlib\n"
        "importlib.import_module('assembly.explainability')\n"
        "forbidden = ('anthropic', 'openai',\n"
        "             'assembly.llm.provider', 'assembly.llm.cost_guard')\n"
        "leaks = [m for m in sys.modules for n in forbidden if n in m]\n"
        "if leaks:\n"
        "    print('LEAKS:', leaks)\n"
        "    sys.exit(1)\n"
    )
    result = subprocess.run(
        ["python", "-c", script],
        cwd=str(API_ROOT),
        capture_output=True, text=True, timeout=30,
        env={**__import__('os').environ, "PYTHONPATH": str(API_ROOT / "src")},
    )
    assert result.returncode == 0, (
        f"explainability leaked LLM imports:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_no_provider_calls_in_explainability_source():
    """Static grep: no provider.chat / structured_output / completions
    call sites inside assembly/explainability/."""
    root = API_ROOT / "src" / "assembly" / "explainability"
    assert root.exists()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in (
            "provider.chat(",
            "provider.structured_output(",
            ".completions.create(",
            ".messages.create(",
            "with_cost_guard(",
        ):
            assert needle not in text, (
                f"{path.name}: forbidden LLM-call needle {needle!r}"
            )


# -----------------------------------------------------------------------
# 12. No apps/web changes (static guard).
# -----------------------------------------------------------------------


def test_no_apps_web_changes_in_phase_12f1():
    """Phase 12F.1 must not modify anything under apps/web. We check
    git for uncommitted changes to that directory at test time."""
    apps_web = REPO_ROOT / "apps" / "web"
    if not apps_web.exists():
        pytest.skip("apps/web directory not present in this checkout")
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "apps/web"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
        changes = (result.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available in this env")
    if changes:
        raise AssertionError(
            f"apps/web has uncommitted changes during Phase 12F.1:\n"
            f"{changes}\nPhase 12F.1 must not touch apps/web."
        )


# -----------------------------------------------------------------------
# 13. No DB migration in 12F.1.
# -----------------------------------------------------------------------


def test_no_new_db_migration_in_12f1():
    """Static grep: no alembic revision file mentions 'phase_12f' or
    'company_context' or 'explainability'."""
    versions_dir = API_ROOT / "alembic" / "versions"
    if not versions_dir.exists():
        pytest.skip("alembic versions dir not present")
    forbidden = ("phase_12f", "company_context", "explainability_panel")
    for f in versions_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in forbidden:
            assert needle not in text, (
                f"{f.name} should not exist yet in 12F.1: contains "
                f"{needle!r}"
            )


# -----------------------------------------------------------------------
# 14. No pricing UI in 12F.1.
# -----------------------------------------------------------------------


def test_no_pricing_ui_in_12f1():
    """Static grep: no pricing-tier UI / route / settings entry
    introduced by 12F.1."""
    root = API_ROOT / "src"
    forbidden_route_patterns = (
        "phase_12f_pricing", "BetaTierPricing", "ProTierPricing",
        "compute_recommended_pricing",
    )
    hits: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in forbidden_route_patterns:
            if needle in text:
                hits.append(f"{path}: {needle}")
    assert not hits, f"pricing logic should not exist yet: {hits}"


# -----------------------------------------------------------------------
# 15. No CompanyContext routes / tables yet.
# -----------------------------------------------------------------------


def test_no_company_context_module_yet():
    """The CompanyContext object is deferred to 12F.2; assert no
    model / route file has been created."""
    models_dir = API_ROOT / "src" / "assembly" / "models"
    for f in models_dir.glob("*.py"):
        # Must NOT have a model file called company_context.py.
        assert f.name != "company_context.py", (
            "company_context model is deferred to 12F.2"
        )
    api_dir = API_ROOT / "src" / "assembly" / "api"
    if api_dir.exists():
        for f in api_dir.glob("*.py"):
            assert f.name != "companies.py", (
                "companies API is deferred to 12F.2"
            )


# -----------------------------------------------------------------------
# 16. No Interview Mode yet.
# -----------------------------------------------------------------------


def test_no_interview_mode_yet():
    """Interview mode is deferred to 12F.3; assert no module exists
    and no brief field accepts interview_mode."""
    orch = API_ROOT / "src" / "assembly" / "orchestration"
    for f in orch.glob("*.py"):
        assert f.name != "interview_mode.py", (
            "interview_mode is deferred to 12F.3"
        )
    # Brief schema must reject `interview_mode` as an extra field
    # (because `extra="forbid"` is set).
    with pytest.raises(ValidationError):
        FounderBriefIn.model_validate({
            **_legacy_brief(),
            "interview_mode": {"enabled": True},
        })


# -----------------------------------------------------------------------
# Additional end-to-end smoke: the three blocks compose into a JSON-
# serializable structure (covers the wire-in step indirectly).
# -----------------------------------------------------------------------


def test_blocks_compose_and_are_json_serializable():
    import json
    ctx = _make_ctx()
    composed = {
        "explainability": build_explainability_panel(
            brief=_rich_brief(), ctx=ctx,
        ),
        "persona_reasoning_cards": build_persona_reasoning_cards(
            ctx=ctx, n=8,
        ),
        "niche_signals": build_niche_signals(
            brief=_rich_brief(), ctx=ctx,
        ),
    }
    serialized = json.dumps(composed, default=str)
    assert len(serialized) > 100
    # Spot-check that the source profile + role mix made it through.
    parsed = json.loads(serialized)
    assert (
        parsed["explainability"]["source_audience_profile"]["profile_used"]
        == "hn_show_hn"
    )


def test_legacy_only_ctx_does_not_crash_builders():
    """Pre-12E runs may not have augmented_intent_drafts in ctx.
    Builders must degrade gracefully, not crash."""
    ctx = {
        "intent_drafts": [],
        "pre_dicts": {},
        "final_dicts": {},
        "persona_meta": {},
        "cohort_persona_lists": [],
        "cohort_summaries": [],
    }
    panel = build_explainability_panel(brief=_legacy_brief(), ctx=ctx)
    cards = build_persona_reasoning_cards(ctx=ctx, n=4)
    signals = build_niche_signals(brief=_legacy_brief(), ctx=ctx)
    assert panel["phase"] == "12f.1"
    assert isinstance(cards, list)
    assert isinstance(signals["minority_objections"], list)
