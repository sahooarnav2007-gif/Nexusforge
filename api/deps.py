"""FastAPI dependencies: authentication & authorization."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import audit
from core.config import get_settings
from core.db import get_db
from core.errors import ForbiddenError, NotFoundError, RateLimitedError, UnauthorizedError
from core.models import Project, Task, TaskRun, User
from core.security import decode_access_token, rate_limiter, rate_limit_key

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    s = get_settings()
    if s.rate_limit_enabled:
        key = rate_limit_key(request)
        ok, limit, window = rate_limiter.allow(key, s.rate_limit_requests,
                                                s.rate_limit_window_seconds)
        if not ok:
            raise RateLimitedError(
                f"Rate limit exceeded ({limit} requests / {window}s)"
            )
    if credentials is None:
        raise UnauthorizedError("Missing Authorization header")
    payload = decode_access_token(credentials.credentials)
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or deactivated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_owned_project(db: AsyncSession, user: User, project_id: str) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        # 404 (not 403) so we don't leak the existence of foreign projects
        raise NotFoundError("Project not found")
    return project


async def get_owned_task(db: AsyncSession, user: User, task_id: str) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise NotFoundError("Task not found")
    project = await db.get(Project, task.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Task not found")
    return task


async def get_owned_run(db: AsyncSession, user: User, run_id: str) -> TaskRun:
    run = await db.get(TaskRun, run_id)
    if run is None:
        raise NotFoundError("Run not found")
    project = await db.get(Project, run.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Run not found")
    return run


def require_rate_limit(request: Request, key: str, limit: int, window: int) -> None:
    ok, _, _ = rate_limiter.allow(key, limit, window)
    if not ok:
        audit("rate_limited", detail={"key": key, "limit": limit, "window": window})
        raise RateLimitedError(f"Too many requests (limit {limit}/{window}s)")