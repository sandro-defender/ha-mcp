"""Agent-facing handoff page and status endpoint.

The page is served by the agent listener (not the Home Assistant Ingress
management listener) so a coding agent can reach it directly.  Only these
exact paths bypass bearer authentication:

* ``GET /agent`` and ``GET /agent/`` — the handoff page;
* ``GET /agent/styles.css`` and ``GET /agent/agent.js`` — its assets;
* ``POST /agent/api/redeem`` — exchange a single-use handoff code.

Every other path on this listener, including ``GET /agent/api/session``, still
requires ``Authorization: Bearer <session token>``.

The page never receives a token from the URL: the handoff code is read from the
URL fragment by the browser and posted once.  Responses are ``no-store`` and
carry ``no-referrer`` plus a strict CSP.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from ..errors import RateLimited
from ..runtime import Runtime
from .deps import get_agent, get_runtime
from .middleware import client_ip
from .schemas import AgentHandoffRedeemRequest

logger = logging.getLogger(__name__)
router = APIRouter()

AGENT_DIR = Path(__file__).resolve().parent.parent / "ui" / "agent"
AGENT_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
PAGE_HEADERS = {
    "Content-Security-Policy": AGENT_CSP,
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _file(name: str, media_type: str) -> FileResponse:
    return FileResponse(AGENT_DIR / name, media_type=media_type, headers=PAGE_HEADERS)


@router.get("/agent", include_in_schema=False)
@router.get("/agent/", include_in_schema=False)
def handoff_page() -> FileResponse:
    return _file("index.html", "text/html; charset=utf-8")


@router.get("/agent/styles.css", include_in_schema=False)
def handoff_styles() -> FileResponse:
    return _file("styles.css", "text/css; charset=utf-8")


@router.get("/agent/agent.js", include_in_schema=False)
def handoff_script() -> FileResponse:
    return _file("agent.js", "text/javascript; charset=utf-8")


@router.post("/agent/api/redeem")
def redeem(
    payload: AgentHandoffRedeemRequest,
    request: Request,
    runtime: Runtime = Depends(get_runtime),
) -> JSONResponse:
    peer = client_ip(request.scope)
    # A 256-bit single-use code is not brute-forceable, but the attempt limit
    # still bounds abuse of an unauthenticated endpoint.
    allowed, retry_after = runtime.rate_limiter.check_and_record(
        f"handoff-redeem:{peer}", limit=20, window_seconds=60
    )
    if not allowed:
        raise RateLimited(retry_after)
    result = runtime.agent_access.redeem(payload.code, peer=peer)
    return JSONResponse(result, headers=PAGE_HEADERS)


@router.get("/agent/api/session")
def session_status(runtime: Runtime = Depends(get_runtime), agent: Any = Depends(get_agent)) -> JSONResponse:
    record = runtime.agents.get_agent(agent.id)
    session = runtime.agent_access.session_public(record)
    connections = {item["id"]: item for item in runtime.connections.list_connections()}
    session["connections"] = [
        {
            "id": connection_id,
            "name": connections.get(connection_id, {}).get("name", "deleted connection"),
            "type": connections.get(connection_id, {}).get("type"),
        }
        for connection_id in record["allowed_connections"] or []
    ]
    return JSONResponse({"session": session}, headers=PAGE_HEADERS)
