"""Phase 6.5 — POST /simulations enqueues + GET /simulations/{id}/status.

Integration-marked. Requires Postgres + Redis up:
    docker compose up -d
    cd apps/api && uv run alembic upgrade head
    uv run pytest -m integration tests/test_pipeline_route.py -v

Uses httpx.AsyncClient + ASGITransport (NOT TestClient) so the async DB
session and the test live on the same event loop. Mixing TestClient (sync)
with async DB causes "Event loop is closed" during connection cleanup.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest

from assembly.main import create_app

pytestmark = pytest.mark.integration


_BRIEF = {
    "product_type": "ai_commerce_platform",
    "product_name": "Amboras",
    "description": (
        "Amboras is an AI commerce platform that builds and operates Shopify "
        "stores autonomously. Founders worry about brand identity. "
        "Merchants would switch with proof of control."
    ),
    "price_structure": {"model": "subscription_monthly", "amount": "$49/mo"},
    "target_society": {
        "description": "Shopify merchants doing $10k-$80k/month overwhelmed by plugins.",
        "geography": "US/Canada",
        "known_segments": ["mid-volume", "premium"],
    },
    "competitors": [{"name": "Shopify Magic"}],
}


@pytest.fixture(autouse=True)
async def _reset_async_engine_after_each_test():
    """Dispose the module-cached AsyncEngine after each test so connections
    don't leak across event loops. Without this, asyncpg tries to clean up
    on a closed loop and raises 'Event loop is closed'."""
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:  # pragma: no cover  defensive
            pass
    db._engine = None
    db._sessionmaker = None


@pytest.fixture
async def client_with_fake_pool() -> AsyncIterator[tuple[httpx.AsyncClient, AsyncMock]]:
    """Build the app, override arq_pool with a fake."""
    app = create_app()
    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        app.state.arq_pool = fake_pool
        yield client, fake_pool


@pytest.mark.asyncio
async def test_post_simulations_returns_202(client_with_fake_pool) -> None:
    client, _ = client_with_fake_pool
    r = await client.post("/simulations", json=_BRIEF)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert "id" in body


@pytest.mark.asyncio
async def test_post_simulations_enqueues_arq_job(client_with_fake_pool) -> None:
    client, fake_pool = client_with_fake_pool
    r = await client.post("/simulations", json=_BRIEF)
    assert r.status_code == 202
    sim_id = r.json()["id"]
    fake_pool.enqueue_job.assert_awaited_once()
    args, _kwargs = fake_pool.enqueue_job.call_args
    assert args[0] == "run_pipeline"
    assert args[1] == sim_id


@pytest.mark.asyncio
async def test_post_simulations_no_pool_does_not_500(client_with_fake_pool) -> None:
    client, _ = client_with_fake_pool
    # Drop the pool to simulate Redis unavailable
    client._transport.app.state.arq_pool = None
    r = await client.post("/simulations", json=_BRIEF)
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_get_status_returns_progress_field(client_with_fake_pool) -> None:
    client, _ = client_with_fake_pool
    r = await client.post("/simulations", json=_BRIEF)
    sim_id = r.json()["id"]
    r2 = await client.get(f"/simulations/{sim_id}/status")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "pending"
    assert "progress" in body
    assert "failed_stage" in body
    assert body["progress"]["stage"] == "pending"


@pytest.mark.asyncio
async def test_raw_state_endpoint_404_by_default(client_with_fake_pool) -> None:
    """ASSEMBLY_EXPOSE_RAW_STATE=false → 404."""
    client, _ = client_with_fake_pool
    r = await client.post("/simulations", json=_BRIEF)
    sim_id = r.json()["id"]
    r2 = await client.get(f"/simulations/{sim_id}/raw-state")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_raw_state_endpoint_works_when_flag_on(
    client_with_fake_pool, monkeypatch
) -> None:
    """ASSEMBLY_EXPOSE_RAW_STATE=true → 200 with empty rounds list."""
    from assembly.api import simulations as sim_routes
    from assembly.config import Settings

    fake = Settings(expose_raw_state=True)
    monkeypatch.setattr(sim_routes, "get_settings", lambda: fake)

    client, _ = client_with_fake_pool
    r = await client.post("/simulations", json=_BRIEF)
    sim_id = r.json()["id"]
    r2 = await client.get(f"/simulations/{sim_id}/raw-state")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["id"] == sim_id
    assert body["rounds"] == []
