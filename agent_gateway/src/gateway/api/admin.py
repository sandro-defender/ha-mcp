"""Management API used by the Ingress panel."""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError

from ..auth.agents import ALLOWED_SCOPES, SCOPE_DESCRIPTIONS
from ..errors import ConflictError, RateLimited, ValidationFailure, VaultError
from ..policy.models import READ_ONLY_TEMPLATES, PolicyDocument
from ..redaction import redact_text
from ..runtime import CREDENTIAL_ROTATION_REMINDER_DAYS, Runtime
from ..tools.registry import TOOL_GROUPS
from ..utils import parse_iso, utcnow
from ..vault.manager import SECRET_KINDS
from .deps import get_admin, get_runtime
from .middleware import AdminPrincipal
from .schemas import (
    AgentCreateRequest,
    AgentUpdateRequest,
    ApprovalRejectRequest,
    AutoUnlockRequest,
    ChangePasswordRequest,
    ConnectionCreateRequest,
    ConnectionUpdateRequest,
    CredentialCreateRequest,
    CredentialUpdateRequest,
    HostKeyTrustRequest,
    PasswordRequest,
    PolicySimulationRequest,
    PolicyUpdateRequest,
    ReleaseAcknowledgeRequest,
    ReleaseCheckRequest,
    SettingsUpdateRequest,
    ToolGroupsRequest,
    VaultImportRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")

REQUIRED_CREDENTIAL_FIELDS = {
    "home_assistant_token": ("token",),
    "proxmox_token": ("token_id", "token_secret"),
    "ssh_private_key": ("private_key",),
    "ssh_key_passphrase": ("passphrase",),
    "ca_certificate": ("ca_cert",),
    "proxmox_ca": ("ca_cert",),
}


def _validate_credential_value(kind: str, value: dict[str, str]) -> None:
    if kind not in SECRET_KINDS:
        raise ValidationFailure("credential kind is not supported")
    required = REQUIRED_CREDENTIAL_FIELDS.get(kind, ())
    missing = [field for field in required if not value.get(field)]
    if missing:
        raise ValidationFailure("credential value is missing required fields", {"fields": missing})
    if kind == "home_assistant_token" and value.get("value") and not value.get("token"):
        value["token"] = value["value"]
    if kind == "proxmox_token":
        token_id = value["token_id"]
        if "!" not in token_id or "@" not in token_id.split("!")[0]:
            raise ValidationFailure("Proxmox token id must use user@realm!tokenname form")


def _credential_references(runtime: Runtime, credential_id: str) -> list[str]:
    references: list[str] = []
    for row in runtime.db.query_all("SELECT * FROM connections"):
        config = json.loads(row["config"])
        if row["credential_id"] == credential_id or config.get("ca_cert_secret_id") == credential_id:
            references.append(row["name"])
    return references


@router.get("/status")
def status(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return runtime.status()


@router.get("/readiness")
def readiness(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return runtime.readiness()


@router.get("/scopes")
def scopes(admin: AdminPrincipal = Depends(get_admin)) -> dict[str, Any]:
    _ = admin
    return {
        "scopes": [
            {"id": scope, "description": SCOPE_DESCRIPTIONS.get(scope, "")}
            for scope in sorted(ALLOWED_SCOPES)
        ],
        "credential_kinds": sorted(SECRET_KINDS),
    }


# --------------------------------------------------------------------- vault


@router.get("/vault")
def vault_status(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return runtime.vault.status()


@router.post("/vault/initialize")
def vault_initialize(
    payload: PasswordRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.vault.initialize(payload.password)
    runtime.settings_store.set("auto_unlock", False)
    runtime.audit.admin_action(admin_id=admin.user_id, action="vault.initialize")
    return result


@router.post("/vault/unlock")
def vault_unlock(
    request: Request,
    payload: PasswordRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    peer = request.client.host if request.client else "unknown"
    allowed, retry_after = runtime.rate_limiter.check_and_record(
        f"vaultunlock:{admin.user_id}:{peer}", limit=5, window_seconds=300
    )
    if not allowed:
        raise RateLimited(retry_after)
    try:
        result = runtime.vault.unlock(payload.password)
    except VaultError as exc:
        runtime.audit.admin_action(
            admin_id=admin.user_id, action="vault.unlock", status="failed", reason=exc.code
        )
        raise
    runtime.audit.admin_action(admin_id=admin.user_id, action="vault.unlock")
    return result


@router.post("/vault/lock")
def vault_lock(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    result = runtime.vault.lock()
    runtime.audit.admin_action(admin_id=admin.user_id, action="vault.lock")
    return result


@router.post("/vault/password")
def vault_change_password(
    payload: ChangePasswordRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.vault.change_password(payload.current_password, payload.new_password)
    runtime.audit.admin_action(admin_id=admin.user_id, action="vault.password_changed")
    return result


@router.post("/vault/rotate-key")
def vault_rotate_key(
    payload: PasswordRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.vault.rotate_data_key(payload.password)
    runtime.audit.admin_action(admin_id=admin.user_id, action="vault.key_rotated")
    return result


@router.post("/vault/auto-unlock")
def vault_auto_unlock(
    payload: AutoUnlockRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    if payload.enabled:
        result = runtime.vault.enable_auto_unlock()
        runtime.settings_store.set("auto_unlock", True)
        runtime.audit.admin_action(
            admin_id=admin.user_id,
            action="vault.auto_unlock_enabled",
            reason="unlock key stored beside vault in /data",
        )
    else:
        result = runtime.vault.disable_auto_unlock()
        runtime.settings_store.set("auto_unlock", False)
        runtime.audit.admin_action(admin_id=admin.user_id, action="vault.auto_unlock_disabled")
    return result


@router.get("/vault/export")
def vault_export(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> Response:
    data = runtime.vault.export_encrypted()
    runtime.audit.admin_action(admin_id=admin.user_id, action="vault.export")
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="agent-gateway-vault-export.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/vault/import")
def vault_import(
    payload: VaultImportRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    try:
        data = base64.b64decode(payload.vault_base64.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValidationFailure("vault import is not valid base64") from exc
    result = runtime.vault.restore_encrypted(data, payload.password)
    runtime.settings_store.set("auto_unlock", False)
    runtime.audit.admin_action(admin_id=admin.user_id, action="vault.import")
    return result


# --------------------------------------------------------------- credentials


@router.get("/credentials")
def list_credentials(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    credentials = runtime.vault.list_secrets()
    now = utcnow()
    for credential in credentials:
        credential["used_by_connections"] = _credential_references(runtime, credential["id"])
        age_days = 0
        updated_str = credential.get("updated_at") or credential.get("created_at")
        if updated_str:
            try:
                age_days = max(0, (now - parse_iso(str(updated_str))).days)
            except ValueError:
                age_days = 0
        credential["age_days"] = age_days
        credential["rotation_recommended"] = age_days >= CREDENTIAL_ROTATION_REMINDER_DAYS
    return {"items": credentials, "kinds": sorted(SECRET_KINDS)}


@router.post("/credentials")
def create_credential(
    payload: CredentialCreateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    value = dict(payload.value)
    _validate_credential_value(payload.kind, value)
    result = runtime.vault.create_secret(
        name=payload.name, kind=payload.kind, value=value, metadata=payload.metadata
    )
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="credential.create",
        params={"name": payload.name, "kind": payload.kind},
    )
    return result


@router.patch("/credentials/{credential_id}")
def update_credential(
    credential_id: str,
    payload: CredentialUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    existing = runtime.vault.get_secret(credential_id)
    value = dict(payload.value) if payload.value is not None else None
    if value is not None:
        _validate_credential_value(existing.kind, value)
    result = runtime.vault.update_secret(
        credential_id, value=value, metadata=payload.metadata, name=payload.name
    )
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="credential.update",
        params={"name": existing.name, "rotated": value is not None},
    )
    return result


@router.delete("/credentials/{credential_id}")
def delete_credential(
    credential_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    existing = runtime.vault.get_secret(credential_id)
    references = _credential_references(runtime, credential_id)
    if references:
        raise ConflictError("credential is referenced by a connection")
    runtime.vault.delete_secret(credential_id)
    runtime.audit.admin_action(
        admin_id=admin.user_id, action="credential.delete", params={"name": existing.name}
    )
    return {"deleted": True}


# --------------------------------------------------------------- connections


@router.get("/connections")
def list_connections(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return {"items": runtime.connections.list_connections()}


@router.post("/connections")
def create_connection(
    payload: ConnectionCreateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.connections.create_connection(
        name=payload.name,
        connection_type=payload.type,
        config=payload.config,
        credential_id=payload.credential_id,
    )
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="connection.create",
        params={"name": payload.name, "type": payload.type},
    )
    return result


@router.patch("/connections/{connection_id}")
def update_connection(
    connection_id: str,
    payload: ConnectionUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.connections.update_connection(
        connection_id, name=payload.name, config=payload.config, credential_id=payload.credential_id
    )
    runtime.audit.admin_action(
        admin_id=admin.user_id, action="connection.update", params={"name": result["name"]}
    )
    return result


@router.delete("/connections/{connection_id}")
def delete_connection(
    connection_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    record = runtime.connections.get_connection(connection_id)
    runtime.connections.delete_connection(connection_id)
    runtime.audit.admin_action(
        admin_id=admin.user_id, action="connection.delete", params={"name": record.name}
    )
    return {"deleted": True}


@router.post("/connections/{connection_id}/test")
async def test_connection(
    connection_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    result = await runtime.test_connection(connection_id)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="connection.test",
        status="ok" if result.get("ok") else "failed",
        params={"connection_id": connection_id},
        reason=str(result.get("error", {}).get("code", "")) if isinstance(result.get("error"), dict) else "",
    )
    return result


@router.get("/connections/{connection_id}/ssh/hostkey")
def get_ssh_host_key(
    connection_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return runtime.get_ssh_trust(connection_id)


@router.post("/connections/{connection_id}/ssh/scan")
async def scan_ssh_host(
    connection_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    result = await runtime.scan_ssh_host(connection_id)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="connection.ssh_scan",
        params={"connection_id": connection_id, "fingerprint": result.get("fingerprint")},
    )
    return result


@router.post("/connections/{connection_id}/ssh/trust")
async def trust_ssh_host(
    connection_id: str,
    payload: HostKeyTrustRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = await runtime.trust_ssh_host_async(
        connection_id, fingerprint=payload.fingerprint, replace=payload.replace
    )
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="connection.ssh_trust",
        params={
            "connection_id": connection_id,
            "fingerprint": payload.fingerprint,
            "replace": payload.replace,
        },
    )
    return result


# -------------------------------------------------------------------- agents


@router.get("/agents")
def list_agents(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    agents = runtime.enrich_agents()
    for agent in agents:
        agent["policy_rules"] = agent["rule_count"]
    return {"items": agents}


@router.post("/agents")
def create_agent(
    payload: AgentCreateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    if payload.policy is not None:
        try:
            PolicyDocument.model_validate(payload.policy)
        except ValidationError as exc:
            raise ValidationFailure("policy document is invalid") from exc
    agent, token = runtime.agents.create_agent(
        name=payload.name, scopes=payload.scopes, expires_at=payload.expires_at, notes=payload.notes
    )
    if payload.policy is not None:
        runtime.policies.set_policy(agent["id"], payload.policy)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="agent.create",
        params={"name": agent["name"], "scopes": agent["scopes"]},
    )
    return {"agent": agent, "token": token, "token_shown_once": True}


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: str,
    payload: AgentUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.agents.update_agent(
        agent_id,
        name=payload.name,
        scopes=payload.scopes,
        enabled=payload.enabled,
        expires_at=payload.expires_at,
        clear_expiry=payload.clear_expiry,
        notes=payload.notes,
    )
    runtime.audit.admin_action(admin_id=admin.user_id, action="agent.update", params={"name": result["name"]})
    return result


@router.post("/agents/{agent_id}/revoke")
def revoke_agent(
    agent_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    result = runtime.agents.revoke_agent(agent_id)
    runtime.audit.admin_action(admin_id=admin.user_id, action="agent.revoke", params={"name": result["name"]})
    return result


@router.post("/agents/{agent_id}/rotate-token")
def rotate_agent_token(
    agent_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    agent, token = runtime.agents.rotate_token(agent_id)
    runtime.audit.admin_action(
        admin_id=admin.user_id, action="agent.rotate_token", params={"name": agent["name"]}
    )
    return {"agent": agent, "token": token, "token_shown_once": True}


@router.delete("/agents/{agent_id}")
def delete_agent(
    agent_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    agent = runtime.agents.get_agent(agent_id)
    runtime.agents.delete_agent(agent_id)
    runtime.audit.admin_action(admin_id=admin.user_id, action="agent.delete", params={"name": agent["name"]})
    return {"deleted": True}


@router.get("/agents/{agent_id}/policy")
def get_agent_policy(
    agent_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    runtime.agents.get_agent(agent_id)
    return runtime.policies.get_policy(agent_id)


@router.put("/agents/{agent_id}/policy")
def put_agent_policy(
    agent_id: str,
    payload: PolicyUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    runtime.agents.get_agent(agent_id)
    result = runtime.policies.set_policy(agent_id, payload.document)
    runtime.audit.admin_action(
        admin_id=admin.user_id, action="agent.policy_update", params={"agent_id": agent_id}
    )
    return result


@router.get("/policy-templates")
def policy_templates(admin: AdminPrincipal = Depends(get_admin)) -> dict[str, Any]:
    _ = admin
    return {"templates": READ_ONLY_TEMPLATES}


@router.post("/policies/evaluate")
def evaluate_policy_simulation(
    payload: PolicySimulationRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.simulate_policy(
        agent_id=payload.agent_id,
        tool_name=payload.tool,
        connection_id=payload.connection_id,
        params=payload.params,
    )


# --------------------------------------------------------------------- tools


@router.get("/tools")
def list_tools(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return {
        "groups": runtime.tools.groups(),
        "tools": [
            {
                "name": spec.name,
                "group": spec.group,
                "description": spec.summary,
                "risk": spec.risk,
                "scope": spec.scope,
                "input_schema": spec.schema(),
                "default_timeout_seconds": spec.default_timeout_seconds,
                "default_max_output_bytes": spec.default_max_output_bytes,
            }
            for spec in runtime.tools.all_specs()
        ],
    }


@router.put("/tools/groups")
def update_tool_groups(
    payload: ToolGroupsRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    unknown = set(payload.groups) - set(TOOL_GROUPS)
    if unknown:
        raise ValidationFailure("unknown tool group")
    runtime.settings_store.set("enabled_tool_groups", sorted(set(payload.groups)))
    runtime.audit.admin_action(
        admin_id=admin.user_id, action="tools.groups_update", params={"groups": sorted(set(payload.groups))}
    )
    return {"groups": runtime.tools.groups()}


# ----------------------------------------------------------------- approvals


@router.get("/approvals")
def list_approvals(
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    listing = runtime.approvals.list(status=status, limit=limit, offset=offset)
    agents_by_id = {a["id"]: a["name"] for a in runtime.agents.list_agents()}
    conns_by_id = {c["id"]: c for c in runtime.connections.list_connections()}
    specs_by_name = {s.name: s for s in runtime.tools.all_specs()}
    now = utcnow()
    for item in listing.get("items", []):
        item["agent_name"] = agents_by_id.get(item["agent_id"], item["agent_id"])
        conn = conns_by_id.get(item.get("connection_id") or "")
        item["connection_name"] = conn["name"] if conn else (item.get("connection_id") or "—")
        item["connection_type"] = conn["type"] if conn else None
        spec = specs_by_name.get(item["tool"])
        item["risk"] = spec.risk if spec else "write"
        item["tool_summary"] = spec.summary if spec else ""
        remaining_seconds: int | None = None
        if item["status"] == "pending" and item.get("expires_at"):
            try:
                remaining_seconds = max(0, int((parse_iso(str(item["expires_at"])) - now).total_seconds()))
            except ValueError:
                remaining_seconds = None
        item["remaining_seconds"] = remaining_seconds
    return listing


@router.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str, runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    result = runtime.approvals.approve(approval_id, admin.user_id)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="approval.approve",
        params={"approval_id": approval_id, "tool": result["tool"]},
    )
    return result


@router.post("/approvals/{approval_id}/reject")
def reject(
    approval_id: str,
    payload: ApprovalRejectRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.approvals.reject(approval_id, admin.user_id, payload.reason)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="approval.reject",
        params={"approval_id": approval_id},
        reason=payload.reason,
    )
    return result


# --------------------------------------------------------------------- audit


@router.get("/audit")
def audit(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    agent_id: str | None = Query(default=None, max_length=64),
    tool: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, max_length=32),
    since: str | None = Query(default=None, max_length=40),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.audit.list_events(
        limit=limit, offset=offset, agent_id=agent_id, tool=tool, status=status, since=since
    )


# ------------------------------------------------------------------ settings


@router.get("/settings")
def get_settings(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return {
        "settings": runtime.settings_store.all_public(),
        "defaults": runtime.settings_store.DEFAULTS,
        "limits": {
            "approval_ttl_seconds": [30, 3600],
            "audit_retention_days": [1, 3650],
            "max_operation_timeout_seconds": [5, 120],
            "max_output_bytes": [1024, 1_048_576],
        },
    }


@router.put("/settings")
def update_settings(
    payload: SettingsUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise ValidationFailure("no settings were provided")
    for key, value in changes.items():
        runtime.settings_store.set(key, value)
    if "emergency_disabled" in changes:
        runtime.audit.admin_action(
            admin_id=admin.user_id,
            action="settings.emergency_disable",
            params={"disabled": bool(changes["emergency_disabled"])},
        )
        if changes["emergency_disabled"]:
            logger.warning("agent access disabled by administrator")
    runtime.audit.admin_action(admin_id=admin.user_id, action="settings.update", params=changes)
    return runtime.settings_store.all_public()


@router.post("/maintenance/purge")
def purge(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    retention = runtime.settings_store.get_int("audit_retention_days")
    audit_deleted = runtime.audit.purge_old(retention)
    approvals_deleted = runtime.approvals.purge_old(retention)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="maintenance.purge",
        params={"audit_deleted": audit_deleted, "approvals_deleted": approvals_deleted},
    )
    return {"audit_deleted": audit_deleted, "approvals_deleted": approvals_deleted}


@router.get("/audit/verify-redaction")
def verify_redaction(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    """Utility endpoint used by the UI to show that known secret patterns are redacted."""

    _ = admin
    sample = redact_text(
        "Authorization: Bearer abcdefghijklmnop password=hunter2 PVEAPIToken=user@pve!tok=secret"
    )
    return {
        "sample": sample,
        "contains_known_secret": any(
            item in sample for item in ("hunter2", "abcdefghijklmnop", "!tok=secret")
        ),
    }


# ------------------------------------------------- releases & diagnostics


@router.get("/releases")
def get_releases(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return runtime.releases.get_release_summary()


@router.post("/releases/check")
async def check_releases(
    payload: ReleaseCheckRequest | None = None,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    req = payload or ReleaseCheckRequest()
    result = await runtime.releases.check_for_updates(force=req.force)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="releases.check",
        params={"force": req.force, "status": result["check_status"]},
    )
    return result


@router.post("/releases/acknowledge")
def acknowledge_release(
    payload: ReleaseAcknowledgeRequest | None = None,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    req = payload or ReleaseAcknowledgeRequest()
    result = runtime.releases.acknowledge_version(req.version)
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="releases.acknowledge",
        params={"version": req.version or result["installed_version"]},
    )
    return result


@router.get("/diagnostics")
def get_diagnostics(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> dict[str, Any]:
    _ = admin
    return runtime.diagnostic_bundle()


@router.get("/diagnostics/export")
def export_diagnostics(
    runtime: Runtime = Depends(get_runtime), admin: AdminPrincipal = Depends(get_admin)
) -> Response:
    bundle = runtime.diagnostic_bundle()
    runtime.audit.admin_action(admin_id=admin.user_id, action="diagnostics.export")
    payload = json.dumps(bundle, indent=2, sort_keys=True).encode("utf-8")
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="gateway-diagnostics-redacted.json"',
            "Cache-Control": "no-store",
        },
    )
