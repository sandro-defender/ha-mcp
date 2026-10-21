"""Validated parameter models for every tool."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils import (
    SERVICE_NAME_RE,
    bounded_json_size,
    validate_absolute_path,
    validate_entity_id,
    validate_node,
    validate_upid,
    validate_vmid,
)

ENTITY_DOMAIN_RE = SERVICE_NAME_RE


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GetStateParams(StrictModel):
    entity_id: str = Field(description="Home Assistant entity id, for example light.kitchen")

    @field_validator("entity_id")
    @classmethod
    def _entity(cls, value: str) -> str:
        try:
            return validate_entity_id(value)
        except ValueError as exc:
            raise ValueError("entity_id is invalid") from exc


class ListEntitiesParams(StrictModel):
    domain: str | None = Field(default=None, description="Optional Home Assistant domain filter")
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not ENTITY_DOMAIN_RE.fullmatch(value):
            raise ValueError("domain is invalid")
        return value


class GetConfigParams(StrictModel):
    pass


class CallServiceParams(StrictModel):
    domain: str = Field(description="Home Assistant service domain, for example light")
    service: str = Field(description="Home Assistant service name, for example turn_on")
    entity_id: str | None = Field(default=None, description="Optional target entity id")
    data: dict[str, Any] = Field(default_factory=dict, description="Service data passed to Home Assistant")

    @field_validator("domain", "service")
    @classmethod
    def _name(cls, value: str) -> str:
        if not SERVICE_NAME_RE.fullmatch(value):
            raise ValueError("service name is invalid")
        return value

    @field_validator("entity_id")
    @classmethod
    def _entity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_entity_id(value)
        except ValueError as exc:
            raise ValueError("entity_id is invalid") from exc

    @field_validator("data")
    @classmethod
    def _data(cls, value: dict[str, Any]) -> dict[str, Any]:
        if bounded_json_size(value) > 32_768:
            raise ValueError("service data is too large")
        if _depth(value) > 4:
            raise ValueError("service data is nested too deeply")
        return value


class ListNodesParams(StrictModel):
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=50, ge=1, le=100)


class ListVmsParams(StrictModel):
    node: str | None = Field(default=None, description="Optional Proxmox node filter")
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("node")
    @classmethod
    def _node(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_node(value)
        except ValueError as exc:
            raise ValueError("node is invalid") from exc


class ListContainersParams(ListVmsParams):
    pass


class GetVmStatusParams(StrictModel):
    node: str = Field(description="Proxmox node name")
    vmid: int = Field(description="VM or container id", ge=1, le=999_999_999)
    kind: Literal["qemu", "lxc"] = Field(default="qemu", description="qemu for VMs, lxc for containers")

    @field_validator("node")
    @classmethod
    def _node(cls, value: str) -> str:
        try:
            return validate_node(value)
        except ValueError as exc:
            raise ValueError("node is invalid") from exc

    @field_validator("vmid")
    @classmethod
    def _vmid(cls, value: int) -> int:
        try:
            return validate_vmid(value)
        except ValueError as exc:
            raise ValueError("vmid is invalid") from exc


class GetTaskStatusParams(StrictModel):
    node: str = Field(description="Proxmox node name")
    upid: str = Field(description="Proxmox task UPID returned by a previous operation")

    @field_validator("node")
    @classmethod
    def _node(cls, value: str) -> str:
        try:
            return validate_node(value)
        except ValueError as exc:
            raise ValueError("node is invalid") from exc

    @field_validator("upid")
    @classmethod
    def _upid(cls, value: str) -> str:
        try:
            return validate_upid(value)
        except ValueError as exc:
            raise ValueError("upid is invalid") from exc


class ServiceStatusParams(StrictModel):
    service: str = Field(description="Service name from the connection's allowlist")


class ReadLogParams(StrictModel):
    path: str = Field(description="Absolute log path from the connection's allowlist")
    lines: int = Field(default=100, ge=1, le=500)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        try:
            return validate_absolute_path(value)
        except ValueError as exc:
            raise ValueError("path is invalid") from exc


class RunTemplateParams(StrictModel):
    template_id: str = Field(description="Command template id defined on the connection")
    params: dict[str, str] = Field(default_factory=dict, description="Values for template placeholders")

    @field_validator("params")
    @classmethod
    def _params(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 10:
            raise ValueError("too many template parameters")
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str) or len(item) > 128:
                raise ValueError("template parameter is invalid")
        return value


def _depth(value: Any, level: int = 0) -> int:
    if level > 8:
        return level
    if isinstance(value, dict):
        return max([level] + [_depth(item, level + 1) for item in value.values()])
    if isinstance(value, list):
        return max([level] + [_depth(item, level + 1) for item in value])
    return level
