"""Safe HTTP error rendering."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..errors import GatewayError

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or "")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        headers = {}
        if exc.code == "rate_limited":
            headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 60))
        if exc.status == 401:
            headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.to_dict(), "request_id": _request_id(request)},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            errors.append({"path": location[:120], "message": str(error.get("msg", "invalid value"))[:200]})
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request is invalid",
                    "details": {"errors": errors},
                },
                "request_id": _request_id(request),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "request_failed"
        message = "the requested resource was not found" if exc.status_code == 404 else "request failed"
        if exc.status_code == 405:
            code, message = "method_not_allowed", "method is not allowed"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}, "request_id": _request_id(request)},
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled request error type=%s path=%s", type(exc).__name__, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "the request could not be completed"},
                "request_id": _request_id(request),
            },
        )
