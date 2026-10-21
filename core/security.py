"""Authentication & authorization primitives.

- Argon2id password hashing (never plaintext).
- Short-lived JWT access tokens + rotating, single-use refresh tokens
  (stored hashed at rest).
- Sliding-window in-memory rate limiter (per user/IP).
"""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import UnauthorizedError
from core.models import RefreshToken, User


def _ph() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        memory_cost=s.argon2_memory_cost,
        time_cost=s.argon2_time_cost,
        parallelism=s.argon2_parallelism,
    )


def hash_password(password: str) -> str:
    return _ph().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph().verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, Argon2Error):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_access_token(user: User) -> tuple[str, datetime]:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.access_token_ttl_minutes)
    payload = {
        "sub": user.id,
        "email": user.email,
        "username": user.username,
        "type": "access",
        "exp": exp,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
    return token, exp


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("Access token expired") from e
    except jwt.InvalidTokenError as e:
        raise UnauthorizedError("Invalid access token") from e
    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type")
    return payload


async def create_refresh_token(session: AsyncSession, user_id: str) -> str:
    s = get_settings()
    raw = secrets.token_urlsafe(48)
    row = RefreshToken(
        user_id=user_id,
        token_hash=_token_hash(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=s.refresh_token_ttl_days),
    )
    session.add(row)
    await session.flush()
    return raw


async def rotate_refresh_token(
    session: AsyncSession, raw: str
) -> tuple[str, str]:
    """Single-use rotation: the presented token is revoked, a new one issued."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == _token_hash(raw))
        )
    ).scalar_one_or_none()
    if row is None:
        raise UnauthorizedError("Invalid refresh token")
    if row.revoked_at is not None:
        # reuse detection: revoke the whole family (all tokens for the user)
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == row.user_id)
            .where(RefreshToken.revoked_at.is_(None))
        )
        for t in (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == row.user_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalars():
            t.revoked_at = now
        raise UnauthorizedError("Refresh token reuse detected; session revoked")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires < now:
        raise UnauthorizedError("Refresh token expired")
    row.revoked_at = now
    await session.flush()
    new_raw = await create_refresh_token(session, row.user_id)
    return row.user_id, new_raw


# ---------------------------------------------------------------------------
# Rate limiting (sliding window, in-memory)
# ---------------------------------------------------------------------------


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        now = time.monotonic()
        dq = self._hits[key]
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        if len(dq) >= limit:
            return False, limit, window_seconds
        dq.append(now)
        return True, limit, window_seconds

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


rate_limiter = RateLimiter()


def rate_limit_key(request: Request, user_id: str | None = None) -> str:
    if user_id:
        return f"user:{user_id}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"