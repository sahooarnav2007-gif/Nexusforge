"""Pydantic models for the user platform."""
from pydantic import BaseModel


class User(BaseModel):
    user_id: str
    username: str
    email: str


class UserProfile(BaseModel):
    """Public profile view of a user."""

    user_id: str
    username: str
    email: str
    bio: str
    joined_at: str