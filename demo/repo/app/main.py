"""FastAPI application entrypoint."""
from fastapi import FastAPI

from app.routers import users

app = FastAPI(title="User Platform", version="0.1.0")
app.include_router(users.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}