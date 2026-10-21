"""Shared test fixtures.

- isolated temp DB (sqlite) per test session
- FastAPI app with in-process worker disabled for API tests (runs are
  executed explicitly where needed)
- auth helper
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

# make the project root importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_tmpdir = tempfile.mkdtemp(prefix="nexusforge-test-")
os.environ["NEXUSFORGE_DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmpdir}/test.db"
os.environ["NEXUSFORGE_WORKSPACE_ROOT"] = f"{_tmpdir}/workspaces"
os.environ["NEXUSFORGE_AUDIT_LOG_PATH"] = f"{_tmpdir}/audit.jsonl"
os.environ["NEXUSFORGE_ENVIRONMENT"] = "test"
os.environ["NEXUSFORGE_LLM_PROVIDER"] = "local"
os.environ["NEXUSFORGE_SANDBOX_BACKEND"] = "process"
os.environ["NEXUSFORGE_JWT_SECRET"] = "test-secret"
os.environ["NEXUSFORGE_RATE_LIMIT_ENABLED"] = "false"
os.environ["NEXUSFORGE_DEBUG"] = "false"

from core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from api.app import create_app  # noqa: E402
from core.db import Base, dispose_engine, get_engine  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    async def _run():
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_run())
    yield
    asyncio.run(dispose_engine())


@pytest.fixture()
async def app():
    application = create_app()
    # bypass lifespan (no worker in unit API tests)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture()
async def client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client, email="user@example.com", username="user",
                    password="Password123") -> dict:
    r = await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
async def auth_headers(client):
    tok = await _register(client)
    return {"Authorization": f"Bearer {tok['access_token']}"}


@pytest.fixture()
async def second_user_headers(client):
    tok = await _register(client, email="other@example.com", username="other")
    return {"Authorization": f"Bearer {tok['access_token']}"}