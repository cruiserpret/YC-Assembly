from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from assembly import __version__
from assembly.api import simulations
from assembly.config import get_settings
from assembly.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log = get_logger("assembly.main")
    settings = get_settings()
    log.info("assembly.starting", env=settings.env, version=__version__)

    # Phase 6.5: open the arq pool so POST /simulations can enqueue jobs.
    # If Redis is unreachable (tests, dev without docker compose), we log
    # and continue — the route falls back to a no-op enqueue.
    app.state.arq_pool = None
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        app.state.arq_pool = await create_pool(
            RedisSettings.from_dsn(settings.redis_url)
        )
        log.info("assembly.arq_pool_ready", redis=settings.redis_url)
    except Exception as e:
        log.warning(
            "assembly.arq_pool_unavailable", err=str(e),
            hint="POST /simulations will persist briefs but not enqueue jobs",
        )

    yield

    if app.state.arq_pool is not None:
        await app.state.arq_pool.close()
    log.info("assembly.stopping")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Assembly API",
        version=__version__,
        description="Synthetic market-reaction infrastructure.",
        lifespan=lifespan,
    )

    # CORS: exact-match origins from settings PLUS a regex that covers
    # every Vercel-generated URL for this project (production alias,
    # per-branch previews, per-SHA immutable builds, team-scoped
    # previews). The regex is opt-in via the
    # ASSEMBLY_CORS_ALLOW_REGEX env var so non-Vercel deployments
    # don't accidentally inherit a broad allowlist.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_origin_regex=settings.cors_allow_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(simulations.router, prefix="/simulations", tags=["simulations"])
    # Phase 7 — GET /simulations/{id}/report
    from assembly.api import reports as reports_router
    app.include_router(reports_router.router)
    # Phase 10A — POST /assembly/runs + GET /assembly/runs/{id}/...
    from assembly.api import assembly_runs as assembly_runs_router
    app.include_router(
        assembly_runs_router.router,
        prefix="/assembly",
        tags=["assembly_runs"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
