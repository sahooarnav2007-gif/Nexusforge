"""System endpoints: health, readiness, metrics."""
from __future__ import annotations

import shutil

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from core.config import get_settings
from core.db import get_engine
from core.metrics import metrics_body

router = APIRouter(tags=["system"])

VERSION = "1.0.0"


@router.get("/health")
async def health():
    s = get_settings()
    return {
        "status": "ok",
        "version": VERSION,
        "environment": s.environment,
        "llm_provider": s.llm_provider,
        "sandbox_backend": s.sandbox_backend,
    }


@router.get("/ready")
async def ready():
    s = get_settings()
    checks: dict[str, str] = {}
    try:
        from sqlalchemy import text

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
    checks["queue"] = "redis" if s.redis_url else "in-process"
    if s.sandbox_backend in ("auto", "docker"):
        checks["sandbox"] = "docker" if shutil.which("docker") else "process-fallback"
    else:
        checks["sandbox"] = s.sandbox_backend
    ok = checks["database"] == "ok"
    return {"status": "ok" if ok else "degraded", "checks": checks}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    return PlainTextResponse(metrics_body(),
                             media_type="text/plain; version=0.0.4; charset=utf-8")