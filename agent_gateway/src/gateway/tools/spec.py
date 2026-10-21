"""Tool definitions shared by the REST API and the MCP server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from ..auth.agents import AgentPrincipal
from ..connections import ResolvedConnection

Risk = Literal["read", "write", "destructive"]


@dataclass(frozen=True)
class ToolContext:
    agent: AgentPrincipal
    connection: ResolvedConnection
    filters: dict[str, tuple[str, ...]]
    excludes: dict[str, tuple[str, ...]]
    timeout_seconds: float
    max_output_bytes: int
    known_hosts_path: Path
    request_id: str

    def filter_values(self, key: str) -> tuple[str, ...]:
        return self.filters.get(key, ())

    def exclude_values(self, key: str) -> tuple[str, ...]:
        return self.excludes.get(key, ())


ToolHandler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
TargetExtractor = Callable[[Any], dict[str, list[str]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: str
    summary: str
    scope: str
    risk: Risk
    params_model: type[BaseModel]
    handler: ToolHandler
    targets: TargetExtractor = field(default=lambda _params: {})
    default_timeout_seconds: float = 30.0
    default_max_output_bytes: int = 65_536
    default_rate_limit_per_minute: int = 60
    supports_connection: bool = True

    def group_to_connection_type(self) -> str:
        return {
            "home_assistant": "home_assistant",
            "proxmox": "proxmox",
            "ssh": "ssh",
        }[self.group]

    def schema(self) -> dict[str, Any]:
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        return schema
