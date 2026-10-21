"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.deps import rate_limiter  # noqa: F401  (imported for side-effect-free access)
from api.routers import auth, projects, system, tasks
from core.config import get_settings
from core.db import dispose_engine
from core.errors import register_error_handlers
from core.events import event_bus
from core.logging import get_logger, setup_logging
from core.metrics import HTTP_DURATION, HTTP_REQUESTS
from core.queue import get_queue

log = get_logger("nexusforge.app")


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(level="DEBUG" if s.debug else "INFO",
                  json_output=not s.is_sqlite or True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from services.agent.worker import agent_worker

        # schema bootstrap: idempotent create_all (SQLite dev / Postgres prod).
        # Alembic is the path for evolving an existing schema; create_all
        # guarantees a fresh checkout works with zero manual steps.
        from core.db import Base, get_engine

        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        await event_bus.start()
        queue = get_queue()
        await queue.start()
        worker_task = await agent_worker.start(queue)
        log.info("nexusforge started", extra={
            "env": s.environment, "llm": s.llm_provider, "sandbox": s.sandbox_backend,
        })
        yield
        await agent_worker.stop()
        await queue.stop()
        await event_bus.stop()
        await dispose_engine()
        log.info("nexusforge stopped")

    app = FastAPI(
        title="NexusForge API",
        version="1.0.0",
        description="Autonomous AI software-engineering platform",
        lifespan=lifespan,
        docs_url="/api/docs" if s.is_production else "/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json" if s.is_production else "/openapi.json",
    )

    # --- CORS (locked to the web origin) ---
    origins = [o.strip() for o in s.web_origin.split(",") if o.strip()]
    allow_all = origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allow_all else origins,
        allow_credentials=not allow_all,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

    # --- secure headers ---
    @app.middleware("http")
    async def _secure_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        if s.is_production:
            response.headers.setdefault("Strict-Transport-Security",
                                        "max-age=31536000; includeSubDomains")
        return response

    # --- request metrics ---
    @app.middleware("http")
    async def _metrics(request, call_next):
        import time

        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            path = request.url.path
            if path.startswith("/api") or path.startswith("/auth") or \
               path.startswith("/projects") or path.startswith("/tasks"):
                HTTP_REQUESTS.labels(request.method, path, status).inc()
                HTTP_DURATION.labels(request.method, path).observe(
                    time.perf_counter() - start
                )
        return response

    register_error_handlers(app)

    app.include_router(auth.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(system.router)
    app.include_router(system.router, prefix="/api")

    @app.get("/", include_in_schema=False)
    async def root():
        return JSONResponse({
            "name": "NexusForge",
            "docs": "/docs" if not s.is_production else "/api/docs",
            "health": "/health",
        })

    return app