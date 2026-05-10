"""Phase 7: aggregation hook is flag-gated.

These tests prove:
  - run_aggregation no-ops when ASSEMBLY_ENABLE_AGGREGATION=false
  - run_aggregation invokes the Phase 7 service when the flag is on
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from assembly.config import Settings
from assembly.pipeline import aggregation as aggregation_mod


@pytest.mark.asyncio
async def test_run_aggregation_noops_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_settings = Settings(enable_aggregation=False)
    monkeypatch.setattr(aggregation_mod, "get_settings", lambda: fake_settings)

    # Just calling it should return None without exploding — no provider/sm needed
    result = await aggregation_mod.run_aggregation(
        simulation_id=uuid4(),
        sessionmaker=None,
        provider=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_run_aggregation_dispatches_to_v7_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 7: when the flag is on, run_aggregation MUST dispatch to
    `service.run_aggregation_v7`. Earlier (6.5) builds no-opped here as a
    stub; that contract has changed."""
    fake_settings = Settings(enable_aggregation=True)
    monkeypatch.setattr(aggregation_mod, "get_settings", lambda: fake_settings)

    # Patch the service entry point so we don't need a real DB / provider.
    fake_v7 = AsyncMock(return_value=None)
    from assembly.pipeline.aggregation import service as service_mod
    monkeypatch.setattr(service_mod, "run_aggregation_v7", fake_v7)

    sim_id = uuid4()
    await aggregation_mod.run_aggregation(
        simulation_id=sim_id,
        sessionmaker="fake-sessionmaker",
        provider="fake-provider",
    )
    fake_v7.assert_awaited_once()
    # Verify the right simulation_id was forwarded.
    kwargs = fake_v7.await_args.kwargs
    assert kwargs["simulation_id"] == sim_id
