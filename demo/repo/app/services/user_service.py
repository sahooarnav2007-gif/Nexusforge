"""User data access.

The in-memory store stands in for a database. `get_user` returns the raw
record dict; higher-level functions build model objects on top of it.
"""
from __future__ import annotations

USERS: dict[str, dict] = {
    "u1": {
        "user_id": "u1",
        "username": "alice",
        "email": "alice@example.com",
        "bio": "Platform engineer. Coffee first, code second.",
        "joined_at": "2024-01-15",
    },
    "u2": {
        "user_id": "u2",
        "username": "bob",
        "email": "bob@example.com",
        "bio": "Backend developer. I like small PRs.",
        "joined_at": "2024-03-02",
    },
}


def get_user(user_id: str) -> dict | None:
    """Return the raw user record for `user_id`, or None if unknown."""
    return USERS.get(user_id)


def list_users() -> list[dict]:
    """Return all user records."""
    return [dict(record) for record in USERS.values()]


def get_user_profile(user_id: str) -> "UserProfile":  # noqa: F821
    """Return the public profile for a user.

    Contract:
        - Returns a UserProfile built from the stored user record
        - Raises KeyError if the user does not exist
        - Uses get_user(user_id)
    """
    from app.models import UserProfile

    user = get_user(user_id)
    if user is None:
        raise KeyError(user_id)
    # TODO(finished? no): only two fields mapped so far
    return UserProfile(
        user_id=user["user_id"],
        username=user["username"],
    )