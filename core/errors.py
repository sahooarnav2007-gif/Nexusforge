"""Central error types and API error envelope."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class NexusForgeError(Exception):
    """Base for expected, user-presentable errors."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(NexusForgeError):
    status_code = 422
    code = "validation_error"


class NotFoundError(NexusForgeError):
    status_code = 404
    code = "not_found"

    def __init__(self, message: str = "Not found", **kw: Any) -> None:
        super().__init__(message, **kw)


class UnauthorizedError(NexusForgeError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(NexusForgeError):
    status_code = 403
    code = "forbidden"


class ConflictError(NexusForgeError):
    status_code = 409
    code = "conflict"


class RateLimitedError(NexusForgeError):
    status_code = 429
    code = "rate_limited"


class LLMError(NexusForgeError):
    """LLM provider failure (timeout, malformed output, quota...)."""

    status_code = 502
    code = "llm_error"


class SandboxError(NexusForgeError):
    status_code = 500
    code = "sandbox_error"


class ToolError(NexusForgeError):
    status_code = 400
    code = "tool_error"


def error_envelope(code: str, message: str, details: dict[str, Any] | None = None,
                   request_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    if request_id:
        body["request_id"] = request_id
    return body


def register_error_handlers(app: FastAPI) -> None:
    from core.logging import get_logger, new_request_id, request_id_var

    log = get_logger("nexusforge.errors")

    @app.exception_handler(NexusForgeError)
    async def _nexusforge_error(request: Request, exc: NexusForgeError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc.code, exc.message, exc.details,
                                   request_id_var.get()),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = request_id_var.get()
        log.exception("unhandled error", extra={"path": str(request.url.path)})
        return JSONResponse(
            status_code=500,
            content=error_envelope("internal_error", "Internal server error",
                                   request_id=rid),
        )

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        return response