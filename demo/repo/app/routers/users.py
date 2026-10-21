"""User-facing API routes."""
from fastapi import APIRouter, HTTPException

from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}")
def get_user(user_id: str):
    """Return a single user."""
    try:
        return user_service.get_user(user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="user not found")


@router.get("/")
def list_users():
    """Return all users."""
    return user_service.list_users()