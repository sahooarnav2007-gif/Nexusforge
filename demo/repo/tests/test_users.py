"""API tests for the user platform.

These tests describe the EXPECTED behavior. The profile endpoint is not
implemented yet, so this suite is currently red.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_get_user():
    resp = client.get("/users/u1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "u1"
    assert data["username"] == "alice"


def test_get_user_not_found():
    resp = client.get("/users/does-not-exist")
    assert resp.status_code == 404


def test_list_users():
    resp = client.get("/users/")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_get_user_profile():
    """The profile endpoint must return the full public profile."""
    resp = client.get("/users/u1/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "u1"
    assert data["username"] == "alice"
    assert data["email"] == "alice@example.com"
    assert "bio" in data
    assert "joined_at" in data


def test_get_user_profile_not_found():
    resp = client.get("/users/nope/profile")
    assert resp.status_code == 404