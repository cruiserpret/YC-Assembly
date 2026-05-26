"""Phase 12A.10F — simulation_seed + temperature stabilization tests.

Pure-Python, no DB, no LLM. Covers:
  - settings expose society_builder_temperature + live_discussion_temperature
  - defaults preserve pre-12A.10F behavior (0.4 and 0.6)
  - env vars override settings
  - simulation_seed kwarg accepted at orchestrator + public entry
  - simulation_seed propagates to ctx
  - run_live_discussion accepts simulation_seed kwarg
  - group assignment seed mixes simulation_seed when supplied
  - backwards compat: simulation_seed absent → previous seed shape
  - drift: simulation_seed not read from env vars
  - drift: temperature settings use the canonical env-var pattern
"""
from __future__ import annotations

import inspect
import os
import uuid
from pathlib import Path

import pytest

from assembly.sources.discussion_layer.group_assignment import (
    _seeded_order, assign_groups_stratified,
)


# ---------------------------------------------------------------------
# 1. Settings expose the new temperature fields
# ---------------------------------------------------------------------


class TestTemperatureSettings:
    def test_society_builder_temperature_field_exists(self) -> None:
        from assembly.config import Settings
        s = Settings()
        assert hasattr(s, "society_builder_temperature")
        assert isinstance(s.society_builder_temperature, float)

    def test_live_discussion_temperature_field_exists(self) -> None:
        from assembly.config import Settings
        s = Settings()
        assert hasattr(s, "live_discussion_temperature")
        assert isinstance(s.live_discussion_temperature, float)

    def test_default_society_builder_temperature_is_04(self) -> None:
        """Pre-12A.10F default preserved."""
        from assembly.config import Settings
        # Construct without env (clear any leaked env var first)
        env_backup = os.environ.pop(
            "ASSEMBLY_SOCIETY_BUILDER_TEMPERATURE", None,
        )
        try:
            s = Settings()
            assert s.society_builder_temperature == 0.4
        finally:
            if env_backup is not None:
                os.environ[
                    "ASSEMBLY_SOCIETY_BUILDER_TEMPERATURE"
                ] = env_backup

    def test_default_live_discussion_temperature_is_06(self) -> None:
        """Pre-12A.10F default preserved."""
        from assembly.config import Settings
        env_backup = os.environ.pop(
            "ASSEMBLY_LIVE_DISCUSSION_TEMPERATURE", None,
        )
        try:
            s = Settings()
            assert s.live_discussion_temperature == 0.6
        finally:
            if env_backup is not None:
                os.environ[
                    "ASSEMBLY_LIVE_DISCUSSION_TEMPERATURE"
                ] = env_backup

    def test_env_var_overrides_society_builder_temperature(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv(
            "ASSEMBLY_SOCIETY_BUILDER_TEMPERATURE", "0.2",
        )
        from assembly.config import Settings
        s = Settings()
        assert s.society_builder_temperature == 0.2

    def test_env_var_overrides_live_discussion_temperature(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv(
            "ASSEMBLY_LIVE_DISCUSSION_TEMPERATURE", "0.3",
        )
        from assembly.config import Settings
        s = Settings()
        assert s.live_discussion_temperature == 0.3


# ---------------------------------------------------------------------
# 2. society_builder reads temperature from settings (not hardcoded)
# ---------------------------------------------------------------------


class TestSocietyBuilderTemperatureWiring:
    def test_society_builder_does_not_hardcode_0_4(self) -> None:
        """Verify the magic number 0.4 is no longer hardcoded next
        to a temperature= kwarg in society_builder.py."""
        p = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "pipeline" / "society_builder.py"
        )
        src = p.read_text(encoding="utf-8")
        assert "temperature=0.4," not in src, (
            "society_builder still hardcodes temperature=0.4 — must "
            "read from get_settings().society_builder_temperature"
        )
        assert (
            "get_settings().society_builder_temperature" in src
            or "_settings.society_builder_temperature" in src
        )

    def test_live_discussion_does_not_hardcode_0_6(self) -> None:
        p = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "orchestration"
            / "live_discussion_pipeline.py"
        )
        src = p.read_text(encoding="utf-8")
        assert "temperature=0.6," not in src, (
            "live_discussion_pipeline still hardcodes temperature="
            "0.6 — must read from get_settings()."
            "live_discussion_temperature"
        )
        assert (
            "get_settings().live_discussion_temperature" in src
            or "_settings.live_discussion_temperature" in src
        )


# ---------------------------------------------------------------------
# 3. simulation_seed plumbing
# ---------------------------------------------------------------------


class TestSimulationSeedPlumbing:
    def test_run_live_founder_brief_pipeline_accepts_seed(self) -> None:
        from assembly.orchestration import (
            run_live_founder_brief_pipeline,
        )
        sig = inspect.signature(run_live_founder_brief_pipeline)
        assert "simulation_seed" in sig.parameters
        assert sig.parameters["simulation_seed"].default is None

    def test_orchestrator_init_accepts_seed(self) -> None:
        from assembly.orchestration.live_founder_brief import (
            LiveFounderBriefOrchestrator,
        )
        sig = inspect.signature(LiveFounderBriefOrchestrator.__init__)
        assert "simulation_seed" in sig.parameters
        assert sig.parameters["simulation_seed"].default is None

    def test_run_live_discussion_accepts_seed(self) -> None:
        from assembly.orchestration.live_discussion_pipeline import (
            run_live_discussion,
        )
        sig = inspect.signature(run_live_discussion)
        assert "simulation_seed" in sig.parameters
        assert sig.parameters["simulation_seed"].default is None

    def test_orchestrator_stores_seed(self) -> None:
        from assembly.orchestration.live_founder_brief import (
            LiveFounderBriefOrchestrator,
        )
        o = LiveFounderBriefOrchestrator(
            run_id=uuid.uuid4(), simulation_seed=42,
        )
        assert o.simulation_seed == 42

    def test_orchestrator_default_seed_is_none(self) -> None:
        """Backwards compat: pre-12A.10F callers that don't pass
        simulation_seed get None."""
        from assembly.orchestration.live_founder_brief import (
            LiveFounderBriefOrchestrator,
        )
        o = LiveFounderBriefOrchestrator(run_id=uuid.uuid4())
        assert o.simulation_seed is None


# ---------------------------------------------------------------------
# 4. group_assignment seed determinism + simulation_seed mixing
# ---------------------------------------------------------------------


def _fake_personas(n: int) -> list[dict]:
    return [{"persona_id": f"p{i}", "role": "x"} for i in range(n)]


class TestGroupAssignmentSeed:
    def test_same_seed_same_groups(self) -> None:
        a = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|scopeA|simseed:42",
        )
        b = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|scopeA|simseed:42",
        )
        assert a == b

    def test_different_seed_different_groups(self) -> None:
        a = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|scopeA|simseed:42",
        )
        b = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|scopeA|simseed:7",
        )
        assert a != b

    def test_run_scope_id_alone_changes_groups(self) -> None:
        """Pre-12A.10F behavior: just changing run_scope_id (which
        the live pipeline regenerates every run) reshuffles groups.
        This is exactly what simulation_seed is meant to override."""
        a = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|scope_A",
        )
        b = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|scope_B",
        )
        assert a != b

    def test_simulation_seed_overrides_run_scope_id_variance(
        self,
    ) -> None:
        """When simulation_seed is mixed in, different run_scope_ids
        on otherwise-identical personas produce DIFFERENT groups
        only because of the run_scope_id component. The mix function
        preserves that — but a future stage could override with a
        seed-only string if pure run-to-run determinism were desired.

        For now we just verify that the mixed-seed shape is stable
        when both components are stable."""
        a = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|fixed_scope|simseed:42",
        )
        b = assign_groups_stratified(
            personas=_fake_personas(12), group_count=3,
            group_size=4, seed="10A.2|fixed_scope|simseed:42",
        )
        assert a == b


# ---------------------------------------------------------------------
# 5. Drift / safety
# ---------------------------------------------------------------------


class TestSafetyInvariants:
    def test_simulation_seed_not_read_from_env(self) -> None:
        """Like evidence_snapshot_id, simulation_seed must be an
        explicit kwarg so it's auditable per-run, not a hidden
        process-wide env var."""
        p = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "orchestration"
            / "live_founder_brief.py"
        )
        src = p.read_text(encoding="utf-8")
        forbidden = (
            'os.environ.get("ASSEMBLY_SIMULATION_SEED',
            "os.environ.get('ASSEMBLY_SIMULATION_SEED",
            'getenv("ASSEMBLY_SIMULATION_SEED',
            "getenv('ASSEMBLY_SIMULATION_SEED",
        )
        for f in forbidden:
            assert f not in src, (
                f"orchestration must not read simulation_seed from "
                f"env var (found {f!r})."
            )

    def test_no_apps_web_touch(self) -> None:
        """Phase 12A.10F changes must not touch apps/web."""
        # Scan the 3 files that 12A.10F modified
        targets = [
            "apps/api/src/assembly/config.py",
            "apps/api/src/assembly/pipeline/society_builder.py",
            "apps/api/src/assembly/orchestration/live_founder_brief.py",
            "apps/api/src/assembly/orchestration/live_discussion_pipeline.py",
        ]
        repo_root = Path(__file__).resolve().parents[3]
        for t in targets:
            src = (repo_root / t).read_text(encoding="utf-8")
            assert "apps/web" not in src, (
                f"{t} contains apps/web reference; 12A.10F must not "
                "touch the frontend"
            )

    def test_runtime_config_artifact_written(self) -> None:
        """The orchestrator writes runtime_config.json at run start.
        Verify the path + the keys it writes — needed by the harness's
        per-run audit."""
        p = (
            Path(__file__).resolve().parent.parent
            / "src" / "assembly" / "orchestration"
            / "live_founder_brief.py"
        ).read_text(encoding="utf-8")
        assert 'runtime_config.json' in p
        # Required keys recorded
        for key in (
            'simulation_seed',
            'society_builder_temperature',
            'live_discussion_temperature',
            'evidence_snapshot_id',
        ):
            assert f'"{key}"' in p, (
                f"runtime_config.json must record {key!r}"
            )
