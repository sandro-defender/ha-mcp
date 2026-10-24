"""Ingress management API for automatic add-on shutdown (lifecycle)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..errors import GatewayError
from ..lifecycle import MAX_TIMEOUT_MINUTES, MIN_TIMEOUT_MINUTES, map_stop_outcome
from ..runtime import Runtime
from .deps import get_admin, get_runtime
from .middleware import AdminPrincipal

router = APIRouter(prefix="/api/admin/lifecycle")


class LifecycleSettingsRequest(BaseModel):
    """Validated automatic-shutdown settings update."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    timeout_minutes: int | None = Field(default=None, ge=MIN_TIMEOUT_MINUTES, le=MAX_TIMEOUT_MINUTES)


@router.get("")
def lifecycle_status(
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.lifecycle.status_payload()


@router.put("/settings")
def update_lifecycle_settings(
    payload: LifecycleSettingsRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise GatewayError("validation_error", "no lifecycle settings were provided", 422)
    result = runtime.lifecycle.update_settings(
        enabled=changes.get("enabled"),
        timeout_minutes=changes.get("timeout_minutes"),
    )
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action="lifecycle.settings_update",
        params={
            "enabled": result["enabled"],
            "timeout_minutes": result["timeout_minutes"],
        },
    )
    return result


@router.post("/shutdown")
async def shutdown_addon(
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> JSONResponse:
    """Manually stop the whole add-on through the Supervisor."""

    # A duplicate request raises ConflictError → 409 via the shared handler.
    outcome = await runtime.lifecycle.shutdown("manual", actor_type="admin", actor_id=admin.user_id)

    error = map_stop_outcome(outcome)
    if error is not None:
        raise error
    # accepted → the Supervisor is stopping this container;
    # unconfirmed → the stop was sent but not answered; neither claims the
    # add-on has already stopped.
    confirmed = outcome["result"] == "accepted"
    return JSONResponse(
        status_code=202,
        content={
            "result": "stopping" if confirmed else "stop_requested",
            "confirmed": confirmed,
            "message": outcome["message"],
            "state": outcome["state"],
            "revoked_sessions": outcome["revoked_sessions"],
            "hint": (
                "The Supervisor is stopping this add-on; the Ingress UI will become "
                "unavailable. Start it again from Home Assistant → Settings → Add-ons "
                "→ Home Assistant Agent Gateway → START."
            ),
        },
    )
