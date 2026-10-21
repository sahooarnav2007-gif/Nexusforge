"""Authentication endpoints: register, login, refresh, logout, me."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import CurrentUser, DbSession
from core.audit import audit
from core.config import get_settings
from core.errors import ConflictError, RateLimitedError, UnauthorizedError
from core.models import RefreshToken, User
from core.schemas import (
    LoginRequest,
    MeUpdate,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from core.security import (
    _token_hash,
    create_refresh_token,
    hash_password,
    issue_access_token,
    rate_limit_key,
    rate_limiter,
    rotate_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$abcdefghijklmnopqrstuv"


def _auth_guard(request: Request, key: str) -> None:
    s = get_settings()
    ok, limit, window = rate_limiter.allow(
        f"auth:{key}", s.auth_rate_limit_requests, s.auth_rate_limit_window_seconds
    )
    if not ok:
        audit("auth_rate_limited", detail={"key": key})
        raise RateLimitedError(f"Too many authentication attempts (limit {limit}/{window}s)")


def _ttl(exp: datetime) -> int:
    return int(exp.timestamp() - datetime.now(timezone.utc).timestamp())


@router.post("/register", response_model=TokenPair, status_code=201)
async def register(body: RegisterRequest, request: Request, db: DbSession):
    _auth_guard(request, rate_limit_key(request))
    email = body.email.lower()
    existing = (
        await db.execute(
            select(User).where((User.email == email) | (User.username == body.username))
        )
    ).scalars().all()
    for u in existing:
        if u.email == email:
            raise ConflictError("Email already registered")
        if u.username == body.username:
            raise ConflictError("Username already taken")
    user = User(
        email=email,
        username=body.username,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    access, exp = issue_access_token(user)
    refresh = await create_refresh_token(db, user.id)
    audit("user_registered", user_id=user.id)
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=_ttl(exp))


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, request: Request, db: DbSession):
    _auth_guard(request, rate_limit_key(request))
    user = (
        await db.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    # constant-shape failure: always run a hash verify to blunt timing attacks
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise UnauthorizedError("Invalid email or password")
    if not verify_password(body.password, user.password_hash):
        audit("login_failed", user_id=user.id)
        raise UnauthorizedError("Invalid email or password")
    if not user.is_active:
        raise UnauthorizedError("Account deactivated")
    access, exp = issue_access_token(user)
    refresh = await create_refresh_token(db, user.id)
    audit("login", user_id=user.id)
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=_ttl(exp))


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: DbSession):
    user_id, new_refresh = await rotate_refresh_token(db, body.refresh_token)
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or deactivated")
    access, exp = issue_access_token(user)
    audit("token_refreshed", user_id=user_id)
    return TokenPair(access_token=access, refresh_token=new_refresh, expires_in=_ttl(exp))


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


@router.post("/logout")
async def logout(body: LogoutRequest, db: DbSession, user: CurrentUser):
    if body.refresh_token:
        row = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == _token_hash(body.refresh_token)
                )
            )
        ).scalar_one_or_none()
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
    audit("logout", user_id=user.id)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut(
        id=user.id, email=user.email, username=user.username,
        is_active=user.is_active, created_at=user.created_at,
    )


@router.patch("/me", response_model=UserOut)
async def update_me(body: MeUpdate, user: CurrentUser, db: DbSession):
    if body.username is not None and body.username != user.username:
        taken = (
            await db.execute(select(User).where(User.username == body.username))
        ).scalar_one_or_none()
        if taken is not None and taken.id != user.id:
            raise ConflictError("Username already taken")
        user.username = body.username
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        audit("password_changed", user_id=user.id)
    return UserOut(
        id=user.id, email=user.email, username=user.username,
        is_active=user.is_active, created_at=user.created_at,
    )