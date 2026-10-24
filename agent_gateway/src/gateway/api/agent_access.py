"""Management API for temporary agent access (the ``/agent`` page).

Everything here is administrator-only: the management app is reachable only
through Home Assistant Ingress (peer allowlist + verified remote-user header),
and unsafe methods additionally require the ``X-Gateway-Request`` CSRF header.

The issuance endpoint returns the plaintext session token exactly once.  No
other endpoint can read it back, and no endpoint ever returns upstream
credentials.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request

from ..auth.agents import SESSION_MAX_TTL_SECONDS, SESSION_MIN_TTL_SECONDS
from ..errors import RateLimited, ShuttingDown, ValidationFailure
from ..runtime import Runtime
from .deps import get_admin, get_runtime
from .middleware import AdminPrincipal, client_ip
from .schemas import (
    AgentAccessSettingsRequest,
    AgentSessionIssueRequest,
    AgentSessionRevokeAllRequest,
    AgentSessionRevokeRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/agent-access")

REVOKE_ALL_CONFIRMATION = "REVOKE ALL"


def _probe_listener(runtime: Runtime) -> dict[str, Any]:
    """Check the agent listener from inside the container.

    Only the loopback address is probed: the add-on cannot verify NAT, DNS, or
    firewall reachability for the administrator's external URL, and probing a
    user-supplied URL from the gateway would be an SSRF primitive.
    """

    scheme = "https" if runtime.settings.tls_enabled else "http"
    url = f"{scheme}://127.0.0.1:{runtime.settings.agent_port}/healthz"
    try:
        response = httpx.get(url, timeout=3.0, verify=False)  # noqa: S501 - loopback liveness probe
        reachable = response.status_code == 200 and response.json().get("status") == "ok"
        detail = f"HTTP {response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        reachable = False
        detail = type(exc).__name__
    return {
        "listener_reachable": reachable,
        "listener_url": url,
        "detail": detail,
        "external_url_configured": bool(runtime.agent_access.base_url()),
        "note": (
            "Reachability is verified on the container's agent listener. The external "
            "URL must be reachable from the machine that runs the coding agent; the "
            "gateway cannot test NAT, DNS, or firewall rules for that address."
        ),
    }


@router.get("")
def overview(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    payload = runtime.agent_access.overview()
    payload["endpoints"] = _probe_listener(runtime)
    payload["restart_policy_result"] = runtime.startup_session_policy
    return payload


@router.post("/sessions")
def issue_session(
    payload: AgentSessionIssueRequest,
    request: Request,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    # No new agent sessions while the add-on is shutting down.
    if runtime.lifecycle.blocks_agents:
        raise ShuttingDown()
    peer = client_ip(request.scope)
    allowed, retry_after = runtime.rate_limiter.check_and_record(
        f"sessionissue:{admin.user_id}:{peer}", limit=10, window_seconds=60
    )
    if not allowed:
        raise RateLimited(retry_after)
    return runtime.agent_access.issue(
        name=payload.name,
        scopes=payload.scopes,
        connection_ids=payload.connection_ids,
        operation_classes=list(payload.operation_classes),
        ttl_seconds=payload.ttl_seconds,
        revoke_previous=payload.revoke_previous,
        notes=payload.notes,
        created_by=admin.user_id,
    )


@router.get("/sessions/{session_id}")
def session_detail(
    session_id: str,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    record = runtime.agents.get_agent(session_id)
    if record.get("kind") != "temporary":
        raise ValidationFailure("not a temporary session")
    return {"session": runtime.agent_access.session_public(record)}


@router.post("/sessions/{session_id}/revoke")
def revoke_session(
    session_id: str,
    payload: AgentSessionRevokeRequest | None = None,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    reason = (payload.reason if payload else "") or "revoked by administrator"
    return {"session": runtime.agent_access.revoke(session_id, actor_id=admin.user_id, reason=reason)}


@router.post("/revoke-all")
def revoke_all(
    payload: AgentSessionRevokeAllRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    if payload.confirm.strip() != REVOKE_ALL_CONFIRMATION:
        raise ValidationFailure(f"type {REVOKE_ALL_CONFIRMATION} to confirm this action")
    result = runtime.agent_access.revoke_all(
        actor_id=admin.user_id,
        reason=payload.reason or "all agent sessions revoked by administrator",
    )
    logger.warning("administrator revoked %d temporary session(s)", result["revoked"])
    return result


@router.get("/sessions/{session_id}/activity")
def session_activity(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.agent_access.activity(session_id, limit=limit)


@router.put("/settings")
def update_settings(
    payload: AgentAccessSettingsRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    return runtime.agent_access.update_settings(
        default_ttl_seconds=payload.default_ttl_seconds,
        max_ttl_seconds=payload.max_ttl_seconds,
        revoke_on_restart=payload.revoke_on_restart,
        agent_api_base_url=payload.agent_api_base_url,
        actor_id=admin.user_id,
    )


@router.post("/reachability")
def refresh_reachability(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return runtime.agent_access.record_reachability(_probe_listener(runtime))


@router.get("/limits")
def limits(admin: AdminPrincipal = Depends(get_admin)) -> dict[str, Any]:
    _ = admin
    return {
        "min_ttl_seconds": SESSION_MIN_TTL_SECONDS,
        "hard_max_ttl_seconds": SESSION_MAX_TTL_SECONDS,
        "revoke_all_confirmation": REVOKE_ALL_CONFIRMATION,
    }
