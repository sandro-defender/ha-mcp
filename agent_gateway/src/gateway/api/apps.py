"""ASGI application factories for the two listeners."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from ..mcp.server import MCPEndpoint
from ..runtime import Runtime
from .admin import router as admin_router
from .agent import router as agent_router
from .errors import register_exception_handlers
from .middleware import (
    AccessLogMiddleware,
    AdminGateMiddleware,
    AgentAuthMiddleware,
    BodyLimitMiddleware,
    RequestContextMiddleware,
)

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
)


def _base_app(runtime: Runtime, title: str) -> FastAPI:
    app = FastAPI(
        title=title,
        version=runtime.settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=None,
    )
    app.state.runtime = runtime
    register_exception_handlers(app)
    return app


def build_admin_app(runtime: Runtime) -> FastAPI:
    app = _base_app(runtime, "Home Assistant Agent Gateway - Management")
    # Last added is outermost: request context -> access log -> body limit -> admin gate.
    app.add_middleware(AdminGateMiddleware, settings=runtime.settings)
    app.add_middleware(BodyLimitMiddleware, max_bytes=runtime.settings.max_json_body_bytes)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(admin_router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        payload = runtime.readiness()
        return JSONResponse(status_code=200 if payload["ready"] else 503, content=payload)

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        index_file = UI_DIR / "index.html"
        if not index_file.is_file():  # pragma: no cover - packaging error
            return HTMLResponse("<h1>Agent Gateway UI is missing</h1>", status_code=500)
        return HTMLResponse(
            index_file.read_text(encoding="utf-8"),
            headers={
                "Content-Security-Policy": CSP,
                "Cache-Control": "no-store",
            },
        )

    @app.get("/favicon.ico", include_in_schema=False, response_model=None)
    def favicon() -> Response:
        svg_file = UI_DIR / "favicon.svg"
        if svg_file.is_file():
            return FileResponse(svg_file, media_type="image/svg+xml")
        return Response(status_code=204)

    if UI_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

    return app


def build_agent_app(runtime: Runtime) -> FastAPI:
    mcp_endpoint = MCPEndpoint(runtime)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp_endpoint.manager.run():
            logger.info("MCP endpoint ready at /mcp")
            yield

    app = _base_app(runtime, "Home Assistant Agent Gateway - Agent API")
    app.router.lifespan_context = lifespan
    app.add_middleware(
        AgentAuthMiddleware,
        agents=runtime.agents,
        settings_store=runtime.settings_store,
        settings=runtime.settings,
        limiter=runtime.rate_limiter,
        audit=runtime.audit,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=runtime.settings.mcp_max_request_bytes)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(agent_router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        payload = runtime.readiness()
        return JSONResponse(status_code=200 if payload["ready"] else 503, content=payload)

    # Serve the MCP endpoint at both `/mcp` and `/mcp/`.  A Starlette `Mount`
    # would answer `/mcp` with a 307 redirect to `/mcp/`, which is harmless for
    # httpx but awkward for MCP clients that post JSON-RPC without following
    # redirects, so the same ASGI app is routed explicitly on both paths.
    app.router.routes.append(
        Route("/mcp", endpoint=mcp_endpoint, methods=None, name="mcp", include_in_schema=False)
    )
    app.router.routes.append(
        Route("/mcp/", endpoint=mcp_endpoint, methods=None, name="mcp-slash", include_in_schema=False)
    )
    return app


def dev_apps(runtime: Runtime) -> tuple[Any, Any]:
    """Convenience for tools and tests."""

    return build_admin_app(runtime), build_agent_app(runtime)
