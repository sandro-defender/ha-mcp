"""Ingress management API for IP access rules and connection-attempt history."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..ipaccess import IP_ATTEMPTS_LIST_MAX, IPAccessService
from ..runtime import Runtime
from .deps import get_admin, get_runtime
from .middleware import AdminPrincipal

router = APIRouter(prefix="/api/admin/ip")


def get_ip_access(runtime: Runtime = Depends(get_runtime)) -> IPAccessService:
    return runtime.ip_access


class RuleRequest(BaseModel):
    """Create or replace an IP rule."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["allow", "deny"]
    cidr: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=200)
    revoke_sessions: bool = True


class RuleUpdateRequest(BaseModel):
    """Modify an existing rule; omitted fields keep their value."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["allow", "deny"] | None = None
    cidr: str | None = Field(default=None, min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=200)


class IPSettingsRequest(BaseModel):
    """Runtime IP enforcement toggle (trusted proxies are add-on options)."""

    model_config = ConfigDict(extra="forbid")

    allowlist_mode: bool


@router.get("/rules")
def get_rules(
    ip_access: IPAccessService = Depends(get_ip_access),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return ip_access.status_payload()


@router.post("/rules", status_code=201)
def create_rule(
    payload: RuleRequest,
    ip_access: IPAccessService = Depends(get_ip_access),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = ip_access.create_rule(
        action=payload.action,
        cidr=payload.cidr,
        note=payload.note,
        created_by=admin.user_id,
        revoke_sessions=payload.revoke_sessions,
    )
    ip_access.audit.admin_action(
        admin_id=admin.user_id,
        action="ip.rules_create",
        params={"action": payload.action, "cidr": result["rule"]["cidr"]},
    )
    return result


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: str,
    payload: RuleUpdateRequest,
    ip_access: IPAccessService = Depends(get_ip_access),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    if payload.action is None and payload.cidr is None and payload.note is None:
        from ..errors import ValidationFailure

        raise ValidationFailure("no changes were provided")
    result = ip_access.update_rule(
        rule_id,
        action=payload.action,
        cidr=payload.cidr,
        note=payload.note,
    )
    ip_access.audit.admin_action(
        admin_id=admin.user_id,
        action="ip.rules_update",
        params={"id": rule_id},
    )
    return result


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: str,
    ip_access: IPAccessService = Depends(get_ip_access),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = ip_access.delete_rule(rule_id)
    ip_access.audit.admin_action(
        admin_id=admin.user_id,
        action="ip.rules_delete",
        params={"id": rule_id},
    )
    return result


@router.put("/settings")
def update_ip_settings(
    payload: IPSettingsRequest,
    ip_access: IPAccessService = Depends(get_ip_access),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    return ip_access.set_allowlist_mode(payload.allowlist_mode, actor_id=admin.user_id)


@router.get("/attempts")
def list_attempts(
    limit: int = Query(default=25, ge=1, le=IP_ATTEMPTS_LIST_MAX),
    offset: int = Query(default=0, ge=0),
    decision: str | None = None,
    ip: str | None = None,
    q: str | None = None,
    listener: str | None = None,
    ip_access: IPAccessService = Depends(get_ip_access),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return ip_access.list_attempts(
        limit=limit,
        offset=offset,
        decision=decision,
        ip=ip,
        query=q,
        listener=listener,
    )
