"""arq worker settings.

Run with:
    uv run arq assembly.workers.WorkerSettings
"""
from __future__ import annotations

import logging

from arq.connections import RedisSettings

from assembly.config import get_settings
from assembly.logging import configure_logging
from assembly.workers.pipeline_worker import run_pipeline

logger = logging.getLogger(__name__)


async def _on_startup(ctx: dict) -> None:
    configure_logging()
    settings = get_settings()
    logger.info(
        "worker.startup redis=%s env=%s",
        settings.redis_url, settings.env,
    )


async def _on_shutdown(ctx: dict) -> None:
    logger.info("worker.shutdown")


class WorkerSettings:
    """Top-level arq settings. Loaded by `arq` CLI."""

    functions = [run_pipeline]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

    # Hard ceilings — protect against runaway jobs.
    job_timeout = 60 * 30        # 30 min max per simulation
    max_jobs = 4                  # max simulations in flight per worker process
    keep_result = 60 * 60 * 24    # results visible for 24h
    health_check_interval = 60

    on_startup = _on_startup
    on_shutdown = _on_shutdown
