"""Phase 12A.10E — evidence snapshot system tests.

Pure-Python, no DB. Covers:
  - brief hashing (raw + normalized stability)
  - snapshot create / save / load roundtrip
  - tamper-check on snapshot_hash
  - brief-match check (loose normalized vs strict raw)
  - orchestration plumbing: kwarg accepted at every layer
  - backwards compat: no snapshot_id behaves identically to today
  - drift: snapshot id must be an explicit kwarg, not an env var
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.calibration import (
    EvidenceSnapshot,
    build_snapshot_from_pipeline_ctx,
    check_brief_matches_snapshot,
    compute_normalized_brief_hash,
    compute_raw_brief_hash,
    load_snapshot,
    normalize_brief,
    save_snapshot,
    snapshots_dir,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


def _example_brief() -> dict:
    """Shape matches the operator brief contract used by the harness."""
    return {
        "product_name": "Acme Widget",
        "product_description": "Widgets for makers.",
        "price_or_price_structure": "$10/mo",
        "launch_geography": "US",
        "target_customers": ["makers", "tinkerers"],
        "competitors_or_alternatives": [
            "OtherWidget", "manual widget construction",
        ],
        "constraints": [],
        "launch_state": "newly_launched",
        "report_depth": "standard",
        "category_hint": "consumer hardware",
        "optional_context": "early prototype",
    }


def _example_accepted() -> list[dict]:
    return [
        {"url": "https://a.example/1", "snippet": "Loves widgets",
         "provider": "tavily", "score": 0.9},
        {"url": "https://b.example/2", "snippet": "Hates widgets",
         "provider": "firecrawl", "score": 0.8},
    ]


def _example_retrieval_audit() -> dict:
    return {
        "providers_configured": ["tavily", "firecrawl"],
        "providers_attempted": ["tavily", "firecrawl"],
        "providers_skipped": [],
        "provider_skip_reasons": {},
        "per_provider_query_count": {"tavily": 8, "firecrawl": 8},
        "per_provider_raw_count": {"tavily": 20, "firecrawl": 4},
        "raw_result_count": 24,
        "tier_1_raw_count": 24,
        "tier_2_raw_count": 0,
        "any_retrieval_provider_configured": True,
    }


def _example_quality_audit() -> dict:
    return {
        "raw_count": 24,
        "accepted_count": 2,
        "rejected_count": 22,
        "rejection_counts": {"empty_snippet": 5, "dup_url": 17},
    }


@pytest.fixture
def isolated_snapshots_dir(tmp_path, monkeypatch):
    """Redirect the on-disk snapshots dir to tmp so tests never
    touch the real audit tree."""
    from assembly.calibration import evidence_snapshots as es_mod
    isolated = tmp_path / "evidence_snapshots"
    monkeypatch.setattr(
        es_mod, "_AUDIT_SNAPSHOTS_DIR", isolated,
    )
    yield isolated


# ---------------------------------------------------------------------
# 1. Brief hashing
# ---------------------------------------------------------------------


class TestBriefHashing:
    def test_raw_brief_hash_is_stable(self) -> None:
        b = _example_brief()
        h1 = compute_raw_brief_hash(b)
        h2 = compute_raw_brief_hash(b)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_raw_brief_hash_changes_with_value(self) -> None:
        b1 = _example_brief()
        b2 = _example_brief()
        b2["product_name"] = "Different Name"
        assert compute_raw_brief_hash(b1) != compute_raw_brief_hash(b2)

    def test_normalized_hash_stable_across_key_reorder(self) -> None:
        b1 = _example_brief()
        b2 = {k: b1[k] for k in reversed(list(b1.keys()))}
        assert (
            compute_normalized_brief_hash(b1)
            == compute_normalized_brief_hash(b2)
        )

    def test_normalized_hash_stable_across_whitespace(self) -> None:
        b1 = _example_brief()
        b2 = _example_brief()
        b2["product_name"] = "  Acme   Widget  "
        b2["product_description"] = "Widgets   for makers."
        assert (
            compute_normalized_brief_hash(b1)
            == compute_normalized_brief_hash(b2)
        )

    def test_normalized_hash_stable_across_case(self) -> None:
        b1 = _example_brief()
        b2 = _example_brief()
        b2["product_name"] = "ACME WIDGET"
        assert (
            compute_normalized_brief_hash(b1)
            == compute_normalized_brief_hash(b2)
        )

    def test_normalized_hash_stable_across_list_order(self) -> None:
        b1 = _example_brief()
        b2 = _example_brief()
        b2["target_customers"] = list(
            reversed(b2["target_customers"])
        )
        assert (
            compute_normalized_brief_hash(b1)
            == compute_normalized_brief_hash(b2)
        )

    def test_normalized_hash_distinguishes_meaningful_diff(self) -> None:
        b1 = _example_brief()
        b2 = _example_brief()
        b2["product_description"] = "Widgets for grown-up makers."
        assert (
            compute_normalized_brief_hash(b1)
            != compute_normalized_brief_hash(b2)
        )

    def test_normalize_brief_only_keeps_canonical_keys(self) -> None:
        b = _example_brief()
        b["extra_marketing_field"] = "ignore me"
        norm = normalize_brief(b)
        assert "extra_marketing_field" not in norm


# ---------------------------------------------------------------------
# 2. Snapshot construction
# ---------------------------------------------------------------------


class TestSnapshotConstruction:
    def test_build_snapshot_basic_fields(self) -> None:
        b = _example_brief()
        snap = build_snapshot_from_pipeline_ctx(
            brief=b,
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
            raw_evidence=[{"url": "x"}, {"url": "y"}],
            anchor_plan={"positive_anchor_terms": ["widget"]},
            simulator_version="12a_10e_v1",
        )
        assert snap.product_name == "Acme Widget"
        assert snap.category_hint == "consumer hardware"
        assert snap.launch_state == "newly_launched"
        assert snap.source == "live_retrieval"
        assert snap.status == "active"
        assert snap.simulator_version == "12a_10e_v1"
        assert snap.accepted_evidence_count == 2
        assert len(snap.accepted_evidence_items) == 2
        assert snap.brief_hash == compute_raw_brief_hash(b)
        assert (
            snap.normalized_brief_hash
            == compute_normalized_brief_hash(b)
        )

    def test_snapshot_id_format(self) -> None:
        snap = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        assert snap.evidence_snapshot_id.startswith("evsnap_")
        parts = snap.evidence_snapshot_id.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 8  # 8-char brief hash prefix
        assert len(parts[2]) == 6  # 6-char random hex

    def test_snapshot_hash_changes_with_content(self) -> None:
        snap1 = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        snap2 = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted() + [
                {"url": "https://c.example", "snippet": "extra item"},
            ],
        )
        # Different evidence sets → different snapshot_hash
        assert snap1.snapshot_hash != snap2.snapshot_hash

    def test_calibration_status_not_set_by_snapshot(self) -> None:
        """Per the spec — a snapshot is NOT a validated prediction.
        It must not carry a calibration_status field that could be
        misread as 'validated'."""
        snap = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        # The model should not expose calibration_status. Run-level
        # validation lives elsewhere (Phase 12A.10E task 2 / future
        # outcome_observations table).
        assert not hasattr(snap, "calibration_status")


# ---------------------------------------------------------------------
# 3. Save / load roundtrip
# ---------------------------------------------------------------------


class TestSnapshotPersistence:
    def test_save_then_load_roundtrip(
        self, isolated_snapshots_dir,
    ) -> None:
        snap = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
            raw_evidence=[{"url": "raw1"}, {"url": "raw2"}],
            anchor_plan={"positive_anchor_terms": ["widget"]},
        )
        path = save_snapshot(snap)
        assert path.exists()
        loaded = load_snapshot(snap.evidence_snapshot_id)
        assert loaded.snapshot_hash == snap.snapshot_hash
        assert loaded.evidence_snapshot_id == snap.evidence_snapshot_id
        assert loaded.accepted_evidence_items == snap.accepted_evidence_items
        assert loaded.raw_evidence_items == snap.raw_evidence_items
        assert loaded.product_name == "Acme Widget"

    def test_load_missing_snapshot_raises(
        self, isolated_snapshots_dir,
    ) -> None:
        with pytest.raises(FileNotFoundError) as ei:
            load_snapshot("evsnap_doesnotexist_000000")
        assert "evsnap_doesnotexist_000000" in str(ei.value)

    def test_load_tampered_snapshot_raises(
        self, isolated_snapshots_dir,
    ) -> None:
        snap = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        path = save_snapshot(snap)
        # Tamper with the file: alter an evidence item
        d = json.loads(path.read_text())
        d["accepted_evidence_items"][0]["snippet"] = "tampered"
        path.write_text(json.dumps(d))
        with pytest.raises(ValueError) as ei:
            load_snapshot(snap.evidence_snapshot_id)
        assert "tamper" in str(ei.value).lower()

    def test_overwrite_with_different_hash_refused(
        self, isolated_snapshots_dir,
    ) -> None:
        snap1 = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        save_snapshot(snap1)
        # Construct a different snapshot with the SAME id
        snap2_dict = snap1.model_dump()
        snap2_dict["accepted_evidence_items"].append(
            {"url": "https://extra", "snippet": "new"}
        )
        # Note: this gives a different snapshot_hash but same id
        from assembly.calibration.evidence_snapshots import (
            _compute_snapshot_hash,
        )
        snap2_dict["snapshot_hash"] = _compute_snapshot_hash(snap2_dict)
        snap2 = EvidenceSnapshot(**snap2_dict)
        assert snap2.evidence_snapshot_id == snap1.evidence_snapshot_id
        assert snap2.snapshot_hash != snap1.snapshot_hash
        with pytest.raises(ValueError) as ei:
            save_snapshot(snap2)
        assert "refusing to overwrite" in str(ei.value)

    def test_idempotent_save_with_same_hash(
        self, isolated_snapshots_dir,
    ) -> None:
        snap = build_snapshot_from_pipeline_ctx(
            brief=_example_brief(),
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        path1 = save_snapshot(snap)
        path2 = save_snapshot(snap)  # same content
        assert path1 == path2  # no error


# ---------------------------------------------------------------------
# 4. Brief matching
# ---------------------------------------------------------------------


class TestBriefMatching:
    def test_exact_brief_matches(
        self, isolated_snapshots_dir,
    ) -> None:
        b = _example_brief()
        snap = build_snapshot_from_pipeline_ctx(
            brief=b,
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        ok, reason = check_brief_matches_snapshot(b, snap)
        assert ok, reason

    def test_cosmetic_edit_matches_loose_mode(
        self, isolated_snapshots_dir,
    ) -> None:
        b = _example_brief()
        snap = build_snapshot_from_pipeline_ctx(
            brief=b,
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        b2 = _example_brief()
        b2["product_name"] = "  acme WIDGET  "  # whitespace + case only
        ok, reason = check_brief_matches_snapshot(
            b2, snap, require_exact=False,
        )
        assert ok, reason

    def test_cosmetic_edit_rejected_in_strict_mode(
        self, isolated_snapshots_dir,
    ) -> None:
        b = _example_brief()
        snap = build_snapshot_from_pipeline_ctx(
            brief=b,
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        b2 = _example_brief()
        b2["product_name"] = "  acme WIDGET  "
        ok, reason = check_brief_matches_snapshot(
            b2, snap, require_exact=True,
        )
        assert not ok
        assert "raw_brief_hash mismatch" in reason

    def test_meaningful_change_rejected(
        self, isolated_snapshots_dir,
    ) -> None:
        b = _example_brief()
        snap = build_snapshot_from_pipeline_ctx(
            brief=b,
            retrieval_audit=_example_retrieval_audit(),
            quality_audit=_example_quality_audit(),
            accepted_evidence=_example_accepted(),
        )
        b2 = _example_brief()
        b2["competitors_or_alternatives"] = ["Totally Different Tool"]
        ok, reason = check_brief_matches_snapshot(b2, snap)
        assert not ok
        assert "normalized_brief_hash mismatch" in reason


# ---------------------------------------------------------------------
# 5. Orchestration plumbing (signature + sentinel propagation)
# ---------------------------------------------------------------------


class TestOrchestrationPlumbing:
    """Verifies the snapshot kwarg is accepted at every public surface
    AND that it propagates to ctx via the orchestrator. Does NOT
    actually run the pipeline (no LLM, no network)."""

    def test_run_live_founder_brief_pipeline_accepts_snapshot_kwarg(
        self,
    ) -> None:
        import inspect
        from assembly.orchestration import (
            run_live_founder_brief_pipeline,
        )
        sig = inspect.signature(run_live_founder_brief_pipeline)
        assert "evidence_snapshot_id" in sig.parameters

    def test_orchestrator_init_accepts_snapshot_kwarg(self) -> None:
        import inspect
        from assembly.orchestration.live_founder_brief import (
            LiveFounderBriefOrchestrator,
        )
        sig = inspect.signature(LiveFounderBriefOrchestrator.__init__)
        assert "evidence_snapshot_id" in sig.parameters

    def test_orchestrator_stores_snapshot_id(self) -> None:
        """Constructing the orchestrator with a snapshot id stashes it
        on the instance (without running anything)."""
        import uuid
        from assembly.orchestration.live_founder_brief import (
            LiveFounderBriefOrchestrator,
        )
        rid = uuid.uuid4()
        o = LiveFounderBriefOrchestrator(
            run_id=rid,
            evidence_snapshot_id="evsnap_test1234_abcdef",
        )
        assert o.evidence_snapshot_id == "evsnap_test1234_abcdef"

    def test_orchestrator_default_snapshot_id_is_none(self) -> None:
        """Backwards compat: existing callers (API endpoint) that
        don't pass evidence_snapshot_id get None."""
        import uuid
        from assembly.orchestration.live_founder_brief import (
            LiveFounderBriefOrchestrator,
        )
        o = LiveFounderBriefOrchestrator(run_id=uuid.uuid4())
        assert o.evidence_snapshot_id is None


# ---------------------------------------------------------------------
# 6. Drift / safety
# ---------------------------------------------------------------------


class TestSafetyInvariants:
    def test_snapshot_id_not_read_from_environment(self) -> None:
        """Snapshot ids must be explicit kwargs. The orchestration
        code must NOT read ASSEMBLY_EVIDENCE_SNAPSHOT_ID or any other
        env var — that would make snapshot usage invisible to the
        audit trail."""
        from pathlib import Path
        src = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "orchestration"
            / "live_founder_brief.py"
        ).read_text(encoding="utf-8")
        # Refuse any env-var read patterns for snapshot id
        forbidden = (
            "os.environ.get(\"ASSEMBLY_EVIDENCE_SNAPSHOT_ID",
            "os.environ.get('ASSEMBLY_EVIDENCE_SNAPSHOT_ID",
            "getenv(\"ASSEMBLY_EVIDENCE_SNAPSHOT_ID",
            "getenv('ASSEMBLY_EVIDENCE_SNAPSHOT_ID",
        )
        for f in forbidden:
            assert f not in src, (
                f"orchestration must not read snapshot id from env "
                f"var (found {f!r}). Snapshot id must be an explicit "
                "kwarg."
            )

    def test_snapshot_module_does_not_write_to_apps_web(self) -> None:
        """No apps/web touchpoints from the snapshot module."""
        import assembly.calibration.evidence_snapshots as es_mod
        src = Path(es_mod.__file__).read_text(encoding="utf-8")
        assert "apps/web" not in src
        assert "frontend" not in src.lower()

    def test_calibration_package_exports_snapshot_symbols(self) -> None:
        from assembly import calibration as cal
        for name in (
            "EvidenceSnapshot", "build_snapshot_from_pipeline_ctx",
            "check_brief_matches_snapshot", "compute_normalized_brief_hash",
            "compute_raw_brief_hash", "load_snapshot", "normalize_brief",
            "save_snapshot", "snapshots_dir",
        ):
            assert hasattr(cal, name), f"calibration.{name} missing"
            assert name in cal.__all__
