"""Tool registry: one definition used by REST, MCP, policy, and audit."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from ..auth.agents import AgentPrincipal
from ..connectors import home_assistant as ha
from ..connectors import proxmox as pve
from ..connectors.ssh import SSHClient, SSHCredentials, render_template_argv
from ..errors import NotFoundError, PolicyDenied, ToolUnavailable, ValidationFailure
from ..storage.db import SettingsStore
from ..utils import validate_entity_id
from .models import (
    CallServiceParams,
    GetConfigParams,
    GetStateParams,
    GetTaskStatusParams,
    GetVmStatusParams,
    ListContainersParams,
    ListEntitiesParams,
    ListNodesParams,
    ListVmsParams,
    ReadLogParams,
    RunTemplateParams,
    ServiceStatusParams,
)
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

GROUP_HOME_ASSISTANT = "home_assistant"
GROUP_PROXMOX = "proxmox"
GROUP_SSH = "ssh"
TOOL_GROUPS = (GROUP_HOME_ASSISTANT, GROUP_PROXMOX, GROUP_SSH)


def _ca_pem(ctx: ToolContext) -> str | None:
    if not ctx.connection.extra_secret:
        return None
    return ctx.connection.extra_secret.get("ca_cert") or ctx.connection.extra_secret.get("value")


def _ha_token(ctx: ToolContext) -> str:
    token = ctx.connection.secret.get("token") or ctx.connection.secret.get("value") or ""
    if not token:
        raise ValidationFailure("Home Assistant credential is empty")
    return token


def _ha_client(ctx: ToolContext) -> ha.HomeAssistantClient:
    return ha.HomeAssistantClient(
        str(ctx.connection.record.config.get("base_url", "")),
        _ha_token(ctx),
        ca_pem=_ca_pem(ctx),
    )


def _pve_client(ctx: ToolContext) -> pve.ProxmoxClient:
    token_id = ctx.connection.secret.get("token_id", "")
    token_secret = ctx.connection.secret.get("token_secret", "")
    return pve.ProxmoxClient(
        str(ctx.connection.record.config.get("base_url", "")),
        token_id,
        token_secret,
        ca_pem=_ca_pem(ctx),
    )


def _ssh_client(ctx: ToolContext) -> SSHClient:
    config = ctx.connection.record.config
    private_key = ctx.connection.secret.get("private_key") or ctx.connection.secret.get("value") or ""
    passphrase = ctx.connection.secret.get("passphrase")
    if not private_key:
        raise ValidationFailure("SSH private key is empty")
    return SSHClient(
        SSHCredentials(
            host=str(config["host"]),
            port=int(config.get("port", 22)),
            username=str(config["username"]),
            private_key=private_key,
            passphrase=passphrase,
            known_hosts_path=ctx.known_hosts_path,
            connect_timeout=float(config.get("connect_timeout_seconds", 10)),
        )
    )


# --------------------------------------------------------------------- handlers


async def _ha_get_state(ctx: ToolContext, params: GetStateParams) -> dict[str, Any]:
    async with _ha_client(ctx) as client:
        return await ha.get_state(
            client,
            entity_id=params.entity_id,
            filters=ctx.filters,
            excludes=ctx.excludes,
            max_bytes=ctx.max_output_bytes,
            timeout=ctx.timeout_seconds,
        )


async def _ha_list_entities(ctx: ToolContext, params: ListEntitiesParams) -> dict[str, Any]:
    async with _ha_client(ctx) as client:
        return await ha.list_entities(
            client,
            domain=params.domain,
            offset=params.offset,
            limit=params.limit,
            filters=ctx.filters,
            excludes=ctx.excludes,
            max_bytes=ctx.max_output_bytes,
            timeout=ctx.timeout_seconds,
        )


async def _ha_get_config(ctx: ToolContext, params: GetConfigParams) -> dict[str, Any]:
    _ = params
    async with _ha_client(ctx) as client:
        return await ha.get_config(client, max_bytes=ctx.max_output_bytes, timeout=ctx.timeout_seconds)


async def _ha_call_service(ctx: ToolContext, params: CallServiceParams) -> dict[str, Any]:
    if not ctx.agent.has_scope("ha:write"):
        raise PolicyDenied("agent lacks the ha:write scope")
    async with _ha_client(ctx) as client:
        return await ha.call_service(
            client,
            domain=params.domain,
            service=params.service,
            entity_id=params.entity_id,
            data=params.data,
            max_bytes=ctx.max_output_bytes,
            timeout=ctx.timeout_seconds,
        )


def _raw_fetch_limit(ctx: ToolContext) -> int:
    return min(4_000_000, max(ctx.max_output_bytes * 8, 131_072))


async def _pve_list_nodes(ctx: ToolContext, params: ListNodesParams) -> dict[str, Any]:
    async with _pve_client(ctx) as client:
        return await pve.list_nodes(
            client,
            offset=params.offset,
            limit=params.limit,
            max_bytes=_raw_fetch_limit(ctx),
            timeout=ctx.timeout_seconds,
            filters=ctx.filters,
            excludes=ctx.excludes,
        )


async def _pve_list_vms(ctx: ToolContext, params: ListVmsParams) -> dict[str, Any]:
    async with _pve_client(ctx) as client:
        return await pve.list_vms(
            client,
            node=params.node,
            offset=params.offset,
            limit=params.limit,
            max_bytes=_raw_fetch_limit(ctx),
            timeout=ctx.timeout_seconds,
            filters=ctx.filters,
            excludes=ctx.excludes,
        )


async def _pve_list_containers(ctx: ToolContext, params: ListContainersParams) -> dict[str, Any]:
    async with _pve_client(ctx) as client:
        return await pve.list_containers(
            client,
            node=params.node,
            offset=params.offset,
            limit=params.limit,
            max_bytes=_raw_fetch_limit(ctx),
            timeout=ctx.timeout_seconds,
            filters=ctx.filters,
            excludes=ctx.excludes,
        )


async def _pve_get_vm_status(ctx: ToolContext, params: GetVmStatusParams) -> dict[str, Any]:
    async with _pve_client(ctx) as client:
        return await pve.get_vm_status(
            client,
            node=params.node,
            vmid=params.vmid,
            kind=params.kind,
            max_bytes=ctx.max_output_bytes,
            timeout=ctx.timeout_seconds,
        )


async def _pve_get_task_status(ctx: ToolContext, params: GetTaskStatusParams) -> dict[str, Any]:
    async with _pve_client(ctx) as client:
        return await pve.get_task_status(
            client,
            node=params.node,
            upid=params.upid,
            max_bytes=ctx.max_output_bytes,
            timeout=ctx.timeout_seconds,
        )


async def _ssh_service_status(ctx: ToolContext, params: ServiceStatusParams) -> dict[str, Any]:
    allowed = list(ctx.connection.record.config.get("allowed_services", []))
    if params.service not in allowed:
        raise PolicyDenied("service is not in the connection allowlist")
    client = _ssh_client(ctx)
    result = await client.run(
        ["systemctl", "is-active", "--", params.service],
        max_bytes=ctx.max_output_bytes,
        timeout=ctx.timeout_seconds,
        accepted_exit_codes={0, 3},
    )
    state = result["stdout"].strip()
    return {"service": params.service, "state": state, "active": result["exit_code"] == 0}


async def _ssh_read_log(ctx: ToolContext, params: ReadLogParams) -> dict[str, Any]:
    client = _ssh_client(ctx)
    return await client.read_log(
        path=params.path,
        allowed_paths=list(ctx.connection.record.config.get("allowed_log_paths", [])),
        lines=params.lines,
        max_bytes=min(ctx.max_output_bytes, 65_536),
        timeout=ctx.timeout_seconds,
    )


async def _ssh_run_template(ctx: ToolContext, params: RunTemplateParams) -> dict[str, Any]:
    config = ctx.connection.record.config
    templates = list(config.get("command_templates", []))
    template = next((item for item in templates if item.get("id") == params.template_id), None)
    if template is None:
        raise PolicyDenied("command template is not configured on this connection")
    from ..connections import CommandTemplate

    argv = render_template_argv(CommandTemplate.model_validate(template), params.params)
    client = _ssh_client(ctx)
    result = await client.run(argv, max_bytes=ctx.max_output_bytes, timeout=ctx.timeout_seconds)
    return {
        "template_id": params.template_id,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
    }


# --------------------------------------------------------------------- targets


def _ha_state_targets(params: GetStateParams) -> dict[str, list[str]]:
    return {"entities": [params.entity_id]}


def _ha_list_targets(params: ListEntitiesParams) -> dict[str, list[str]]:
    return {"domains": [params.domain]} if params.domain else {}


def _service_entity_values(raw: Any) -> list[str]:
    """Validate the entity_id forms accepted by Home Assistant service data."""

    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        values = raw
    else:
        raise ValidationFailure("service entity_id must be a string or a list of strings")
    if len(values) > 100:
        raise ValidationFailure("too many service target entities")
    try:
        return [validate_entity_id(item) for item in values]
    except ValueError as exc:
        raise ValidationFailure("service target entity_id is invalid") from exc


def _ha_service_targets(params: CallServiceParams) -> dict[str, list[str]]:
    """Extract every HA service target before policy evaluation.

    HA accepts ``entity_id`` both as a tool argument and inside service data;
    its ``target`` object can additionally name areas/devices.  Omitting those
    values would let a nested or broad target bypass an entity-constrained
    policy.  ``*`` is a request-side sentinel for an unknown/broad target: a
    wildcard allow rule may permit it, but a specific allow or deny constraint
    fails closed.
    """

    targets = {"domains": [params.domain], "ha_services": [f"{params.domain}.{params.service}"]}
    entities: list[str] = []
    if params.entity_id:
        entities.extend(_service_entity_values(params.entity_id))
    if "entity_id" in params.data:
        entities.extend(_service_entity_values(params.data["entity_id"]))

    # HA also accepts these target selectors at the top level of REST service
    # data.  An allowed entity alongside one does not enumerate the full
    # affected set, so retain the broad sentinel in mixed requests too.
    broad_target = bool({"area_id", "device_id", "floor_id", "label_id"} & set(params.data))
    target = params.data.get("target")
    if target is not None:
        if not isinstance(target, dict):
            raise ValidationFailure("service target must be an object")
        if "entity_id" in target:
            entities.extend(_service_entity_values(target["entity_id"]))
        # Area/device/other targeting can affect entities not enumerated in
        # the request, so it remains broad even when an entity is also named.
        if set(target) - {"entity_id"}:
            broad_target = True
    if not entities:
        broad_target = True
    if broad_target:
        entities.append("*")
    if entities:
        targets["entities"] = list(dict.fromkeys(entities))
    return targets


def _pve_list_vm_targets(params: ListVmsParams) -> dict[str, list[str]]:
    return {"nodes": [params.node]} if params.node else {}


def _pve_vm_targets(params: GetVmStatusParams) -> dict[str, list[str]]:
    resource_family = "vms" if params.kind == "qemu" else "containers"
    return {"nodes": [params.node], resource_family: [str(params.vmid)]}


def _pve_task_targets(params: GetTaskStatusParams) -> dict[str, list[str]]:
    return {"nodes": [params.node]}


def _ssh_service_targets(params: ServiceStatusParams) -> dict[str, list[str]]:
    return {"ssh_services": [params.service]}


def _ssh_log_targets(params: ReadLogParams) -> dict[str, list[str]]:
    return {"ssh_paths": [params.path]}


def _ssh_template_targets(params: RunTemplateParams) -> dict[str, list[str]]:
    return {"ssh_templates": [params.template_id]}


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="ha.get_state",
        group=GROUP_HOME_ASSISTANT,
        summary="Read the current state and attributes of one Home Assistant entity.",
        scope="ha:read",
        risk="read",
        params_model=GetStateParams,
        handler=_ha_get_state,
        targets=_ha_state_targets,
        default_timeout_seconds=20,
        default_max_output_bytes=32_768,
    ),
    ToolSpec(
        name="ha.list_entities",
        group=GROUP_HOME_ASSISTANT,
        summary="List Home Assistant entities with pagination and bounded output.",
        scope="ha:read",
        risk="read",
        params_model=ListEntitiesParams,
        handler=_ha_list_entities,
        targets=_ha_list_targets,
        default_timeout_seconds=25,
        default_max_output_bytes=65_536,
    ),
    ToolSpec(
        name="ha.get_config",
        group=GROUP_HOME_ASSISTANT,
        summary="Read a small allowlist of non-sensitive Home Assistant configuration fields.",
        scope="ha:read",
        risk="read",
        params_model=GetConfigParams,
        handler=_ha_get_config,
        targets=lambda _params: {},
        default_timeout_seconds=15,
        default_max_output_bytes=8_192,
    ),
    ToolSpec(
        name="ha.call_service",
        group=GROUP_HOME_ASSISTANT,
        summary=(
            "Call an allowlisted Home Assistant service. Disabled unless a policy rule allows it; "
            "always requires human approval."
        ),
        scope="ha:write",
        risk="write",
        params_model=CallServiceParams,
        handler=_ha_call_service,
        targets=_ha_service_targets,
        default_timeout_seconds=30,
        default_max_output_bytes=8_192,
        default_rate_limit_per_minute=10,
    ),
    ToolSpec(
        name="proxmox.list_nodes",
        group=GROUP_PROXMOX,
        summary="List Proxmox VE cluster nodes with safe status fields.",
        scope="proxmox:read",
        risk="read",
        params_model=ListNodesParams,
        handler=_pve_list_nodes,
        targets=lambda _params: {},
        default_timeout_seconds=20,
        default_max_output_bytes=32_768,
    ),
    ToolSpec(
        name="proxmox.list_vms",
        group=GROUP_PROXMOX,
        summary="List QEMU virtual machines in the Proxmox cluster.",
        scope="proxmox:read",
        risk="read",
        params_model=ListVmsParams,
        handler=_pve_list_vms,
        targets=_pve_list_vm_targets,
        default_timeout_seconds=25,
        default_max_output_bytes=65_536,
    ),
    ToolSpec(
        name="proxmox.list_containers",
        group=GROUP_PROXMOX,
        summary="List LXC containers in the Proxmox cluster.",
        scope="proxmox:read",
        risk="read",
        params_model=ListContainersParams,
        handler=_pve_list_containers,
        targets=_pve_list_vm_targets,
        default_timeout_seconds=25,
        default_max_output_bytes=65_536,
    ),
    ToolSpec(
        name="proxmox.get_vm_status",
        group=GROUP_PROXMOX,
        summary="Read the current status of one Proxmox VM or container.",
        scope="proxmox:read",
        risk="read",
        params_model=GetVmStatusParams,
        handler=_pve_get_vm_status,
        targets=_pve_vm_targets,
        default_timeout_seconds=20,
        default_max_output_bytes=16_384,
    ),
    ToolSpec(
        name="proxmox.get_task_status",
        group=GROUP_PROXMOX,
        summary="Read the status of a Proxmox task by UPID.",
        scope="proxmox:read",
        risk="read",
        params_model=GetTaskStatusParams,
        handler=_pve_get_task_status,
        targets=_pve_task_targets,
        default_timeout_seconds=20,
        default_max_output_bytes=16_384,
    ),
    ToolSpec(
        name="ssh.service_status",
        group=GROUP_SSH,
        summary="Check whether an allowlisted systemd service is active on an SSH host.",
        scope="ssh:read",
        risk="read",
        params_model=ServiceStatusParams,
        handler=_ssh_service_status,
        targets=_ssh_service_targets,
        default_timeout_seconds=20,
        default_max_output_bytes=8_192,
    ),
    ToolSpec(
        name="ssh.read_log",
        group=GROUP_SSH,
        summary="Read the tail of an allowlisted log file over SFTP.",
        scope="ssh:read",
        risk="read",
        params_model=ReadLogParams,
        handler=_ssh_read_log,
        targets=_ssh_log_targets,
        default_timeout_seconds=25,
        default_max_output_bytes=65_536,
    ),
    ToolSpec(
        name="ssh.run_command_template",
        group=GROUP_SSH,
        summary=(
            "Run an administrator-defined argv template on an SSH host. Disabled unless policy allows; "
            "always requires human approval."
        ),
        scope="ssh:write",
        risk="write",
        params_model=RunTemplateParams,
        handler=_ssh_run_template,
        targets=_ssh_template_targets,
        default_timeout_seconds=30,
        default_max_output_bytes=32_768,
        default_rate_limit_per_minute=10,
    ),
)


class ToolRegistry:
    def __init__(self, settings: SettingsStore, specs: tuple[ToolSpec, ...] = SPECS) -> None:
        self.settings = settings
        self._specs = {spec.name: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise RuntimeError("duplicate tool name in registry")

    def all_specs(self) -> list[ToolSpec]:
        return sorted(self._specs.values(), key=lambda spec: spec.name)

    def enabled_groups(self) -> set[str]:
        groups = self.settings.get("enabled_tool_groups")
        return {str(group) for group in groups} if isinstance(groups, list) else set()

    def is_group_enabled(self, group: str) -> bool:
        return group in self.enabled_groups()

    def get_spec(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise NotFoundError("tool")
        return spec

    def get(self, name: str) -> ToolSpec:
        spec = self.get_spec(name)
        if spec.group not in self.enabled_groups():
            raise ToolUnavailable(f"tool group {spec.group} is disabled")
        return spec

    def for_agent(self, agent: AgentPrincipal) -> list[ToolSpec]:
        enabled = self.enabled_groups()
        return [spec for spec in self.all_specs() if spec.group in enabled and agent.has_scope(spec.scope)]

    def validate_params(self, spec: ToolSpec, params: dict[str, Any]) -> Any:
        try:
            return spec.params_model.model_validate(params)
        except ValidationError as exc:
            safe_errors = [
                {"path": ".".join(str(part) for part in error["loc"]) or "params", "message": error["msg"]}
                for error in exc.errors()
            ]
            raise ValidationFailure("tool parameters are invalid", {"errors": safe_errors}) from exc

    def public_tools(self, agent: AgentPrincipal) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "group": spec.group,
                "description": spec.summary,
                "risk": spec.risk,
                "scope": spec.scope,
                "input_schema": spec.schema(),
            }
            for spec in self.for_agent(agent)
        ]

    def mcp_tools(self, agent: AgentPrincipal) -> list[Any]:
        from mcp_types import Tool, ToolAnnotations

        return [
            Tool(
                name=spec.name,
                description=spec.summary,
                input_schema=spec.schema(),
                annotations=ToolAnnotations(
                    read_only_hint=spec.risk == "read",
                    destructive_hint=spec.risk == "destructive",
                    open_world_hint=True,
                ),
            )
            for spec in self.for_agent(agent)
        ]

    def groups(self) -> list[dict[str, Any]]:
        enabled = self.enabled_groups()
        result = []
        for group in TOOL_GROUPS:
            tools = [spec.name for spec in self.all_specs() if spec.group == group]
            result.append({"id": group, "enabled": group in enabled, "tools": tools})
        return result
