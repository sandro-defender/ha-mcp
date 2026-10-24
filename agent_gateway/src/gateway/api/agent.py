"""Agent-facing REST API (versioned)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..auth.agents import AgentPrincipal
from ..errors import ApprovalRequired, PolicyDenied, ValidationFailure
from ..runtime import Runtime
from .deps import get_agent, get_runtime
from .schemas import OperationRequest

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health(
    request: Request, runtime: Runtime = Depends(get_runtime), agent: AgentPrincipal = Depends(get_agent)
) -> dict[str, Any]:
    _ = agent
    return {
        "status": "ok",
        "version": runtime.settings.version,
        "vault": "unlocked" if runtime.vault.unlocked else "locked",
        "emergency_disabled": runtime.settings_store.get_bool("emergency_disabled"),
        "request_id": getattr(request.state, "request_id", None),
    }


@router.get("/connections")
def connections(
    runtime: Runtime = Depends(get_runtime), agent: AgentPrincipal = Depends(get_agent)
) -> dict[str, Any]:
    return {"items": runtime.connections.agent_visible(agent.allowed_connections)}


@router.get("/tools")
def tools(
    runtime: Runtime = Depends(get_runtime), agent: AgentPrincipal = Depends(get_agent)
) -> dict[str, Any]:
    return {
        "items": runtime.tools.public_tools(agent),
        "requires_connection_id": True,
        "notes": (
            "Write and destructive tools always require human approval. "
            "The first call returns status=pending_approval; a human approves it in the Home Assistant "
            "panel, then retry the identical call with approval_id."
        ),
    }


@router.post("/operations")
async def create_operation(
    payload: OperationRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> Any:
    try:
        outcome = await runtime.operations.execute(
            agent=agent,
            tool_name=payload.tool,
            connection_id=payload.connection_id,
            params=payload.params,
            approval_id=payload.approval_id,
            source="rest",
        )
    except ApprovalRequired as exc:
        return JSONResponse(
            status_code=202,
            content={
                "operation_id": exc.details.get("operation_id"),
                "status": "pending_approval",
                "approval_id": exc.details.get("approval_id"),
                "expires_at": exc.details.get("expires_at"),
                "next_action": (
                    "A human must approve this request in the Home Assistant panel, then retry the same "
                    "call with approval_id."
                ),
            },
        )
    return outcome.to_dict()


@router.get("/operations/{operation_id}")
def get_operation(
    operation_id: str,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    return runtime.operations.get_operation(agent, operation_id)


@router.get("/approvals/{approval_id}")
def get_approval(
    approval_id: str,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    approval = runtime.approvals.get(approval_id)
    if approval["agent_id"] != agent.id:
        raise ValidationFailure("approval was not found for this agent")
    return {
        "approval_id": approval["id"],
        "operation_id": approval["operation_id"],
        "status": approval["status"],
        "tool": approval["tool"],
        "expires_at": approval["expires_at"],
    }


@router.get("/audit")
def audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    if agent.kind != "persistent" or not agent.has_scope("audit:read"):
        raise PolicyDenied("global audit access requires a persistent agent with the audit:read scope")
    return runtime.audit.list_events(limit=limit, offset=offset)
