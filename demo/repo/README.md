# User Platform (demo repository)

A small FastAPI service for managing users.

## Status

**Incomplete.** The user profile API (`GET /users/{user_id}/profile`) is
half-finished: the service function only maps two of the five profile fields
and the router endpoint does not exist yet. The test suite in `tests/`
describes the expected behavior and is currently failing.

## Layout

- `app/main.py` — FastAPI app
- `app/models.py` — pydantic models (`User`, `UserProfile`)
- `app/services/user_service.py` — data access (in-memory store)
- `app/routers/users.py` — `/users` routes
- `tests/test_users.py` — API tests (expected behavior)

## Run

```bash
pip install -r requirements.txt
pytest -q
```