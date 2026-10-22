"""Request models for the management and agent APIs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PasswordRequest(StrictRequest):
    password: str = Field(min_length=1, max_length=512)


class ChangePasswordRequest(StrictRequest):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


class AutoUnlockRequest(StrictRequest):
    enabled: bool


class VaultImportRequest(StrictRequest):
    vault_base64: str
    password: str = Field(min_length=1, max_length=512)


class CredentialCreateRequest(StrictRequest):
    name: str
    kind: str
    value: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class CredentialUpdateRequest(StrictRequest):
    name: str | None = None
    value: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None


class ConnectionCreateRequest(StrictRequest):
    name: str
    type: Literal["home_assistant", "proxmox", "ssh"]
    config: dict[str, Any]
    credential_id: str


class ConnectionUpdateRequest(StrictRequest):
    name: str | None = None
    config: dict[str, Any] | None = None
    credential_id: str | None = None


class HostKeyTrustRequest(StrictRequest):
    fingerprint: str = Field(min_length=8, max_length=200)
    replace: bool = False


class AgentCreateRequest(StrictRequest):
    name: str
    scopes: list[str]
    expires_at: str | None = None
    notes: str = ""
    policy: dict[str, Any] | None = None


class AgentUpdateRequest(StrictRequest):
    name: str | None = None
    scopes: list[str] | None = None
    enabled: bool | None = None
    expires_at: str | None = None
    clear_expiry: bool = False
    notes: str | None = None


class PolicyUpdateRequest(StrictRequest):
    document: dict[str, Any]


class ApprovalRejectRequest(StrictRequest):
    reason: str = Field(default="", max_length=300)


class ToolGroupsRequest(StrictRequest):
    groups: list[str]


class SettingsUpdateRequest(StrictRequest):
    emergency_disabled: bool | None = None
    audit_retention_days: int | None = Field(default=None, ge=1, le=3650)
    approval_ttl_seconds: int | None = Field(default=None, ge=30, le=3600)
    max_operation_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    max_output_bytes: int | None = Field(default=None, ge=1024, le=1_048_576)
    ui_theme: Literal["system", "light", "dark"] | None = None


class PolicySimulationRequest(StrictRequest):
    agent_id: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=3, max_length=100)
    connection_id: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class ReleaseCheckRequest(StrictRequest):
    force: bool = False


class ReleaseAcknowledgeRequest(StrictRequest):
    version: str | None = Field(default=None, max_length=32)


class OperationRequest(StrictRequest):
    tool: str = Field(min_length=3, max_length=100)
    connection_id: str = Field(min_length=32, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = Field(default=None, max_length=64)
