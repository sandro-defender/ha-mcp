"""Policy document schema.

Policies are explicit allow/deny rules.  Unknown fields and unknown constraint
names are rejected so that a typo cannot silently broaden access.  An empty
document denies everything.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TOOL_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_*]+)+$")
RULE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:-]{0,63}$")
CONNECTION_PATTERN = re.compile(r"^(\*|[a-f0-9]{32})$")

CONSTRAINT_KEYS = frozenset(
    {
        "entities",
        "domains",
        "ha_services",
        "nodes",
        "vms",
        "containers",
        "ssh_services",
        "ssh_paths",
        "ssh_templates",
    }
)

MAX_RULES = 100
MAX_PATTERNS_PER_KEY = 50
MAX_PATTERN_LENGTH = 128


class PolicyLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_output_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    rate_limit_per_minute: int = Field(default=60, ge=0, le=600)


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    effect: Literal["allow", "deny"]
    tools: list[str] = Field(min_length=1, max_length=50)
    connections: list[str] = Field(min_length=1, max_length=50)
    constraints: dict[str, list[str]] = Field(default_factory=dict)
    require_approval: bool = False
    limits: PolicyLimits = Field(default_factory=PolicyLimits)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not RULE_ID_PATTERN.fullmatch(value):
            raise ValueError("rule id is invalid")
        return value

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, values: list[str]) -> list[str]:
        for value in values:
            if not TOOL_PATTERN.fullmatch(value):
                raise ValueError("tool pattern is invalid")
        return values

    @field_validator("connections")
    @classmethod
    def _validate_connections(cls, values: list[str]) -> list[str]:
        for value in values:
            if not CONNECTION_PATTERN.fullmatch(value):
                raise ValueError("connection id must be '*' or a connection identifier")
        return values

    @field_validator("constraints")
    @classmethod
    def _validate_constraints(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = set(value) - CONSTRAINT_KEYS
        if unknown:
            raise ValueError("policy contains an unknown constraint")
        for key, patterns in value.items():
            if not isinstance(patterns, list) or not patterns:
                raise ValueError(f"constraint {key} must contain at least one pattern")
            if len(patterns) > MAX_PATTERNS_PER_KEY:
                raise ValueError(f"constraint {key} has too many patterns")
            for pattern in patterns:
                if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_LENGTH:
                    raise ValueError(f"constraint {key} contains an invalid pattern")
                if "\n" in pattern or "\x00" in pattern:
                    raise ValueError(f"constraint {key} contains an invalid pattern")
        return value


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    rules: list[PolicyRule] = Field(default_factory=list, max_length=MAX_RULES)

    @field_validator("rules")
    @classmethod
    def _unique_rule_ids(cls, rules: list[PolicyRule]) -> list[PolicyRule]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("policy rule ids must be unique")
        return rules


class PolicyRequest(BaseModel):
    """A normalized authorization question for one tool call."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    tool: str
    connection_id: str | None = None
    risk: Literal["read", "write", "destructive"]
    targets: dict[str, list[str]] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str
    rule_ids: tuple[str, ...] = ()
    require_approval: bool = False
    limits: PolicyLimits = Field(default_factory=PolicyLimits)
    filters: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    excludes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    policy_revision: str = ""


READ_ONLY_TEMPLATES: dict[str, dict[str, object]] = {
    "home_assistant_read_all": {
        "version": 1,
        "rules": [
            {
                "id": "ha-read",
                "effect": "allow",
                "tools": ["ha.get_state", "ha.list_entities", "ha.get_config"],
                "connections": ["*"],
            }
        ],
    },
    "proxmox_read_all": {
        "version": 1,
        "rules": [
            {
                "id": "proxmox-read",
                "effect": "allow",
                "tools": [
                    "proxmox.list_nodes",
                    "proxmox.list_vms",
                    "proxmox.list_containers",
                    "proxmox.get_vm_status",
                    "proxmox.get_task_status",
                ],
                "connections": ["*"],
            }
        ],
    },
    "ssh_read": {
        "version": 1,
        "rules": [
            {
                "id": "ssh-read",
                "effect": "allow",
                "tools": ["ssh.service_status", "ssh.read_log"],
                "connections": ["*"],
            }
        ],
    },
}
