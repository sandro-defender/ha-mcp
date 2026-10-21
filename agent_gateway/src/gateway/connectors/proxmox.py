"""Proxmox VE API connector (read-only in this release)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ..errors import ConnectorError
from ..utils import validate_node, validate_upid, validate_vmid
from .http import make_client, request_json

SAFE_NODE_FIELDS = ("node", "status", "uptime", "cpu", "mem", "maxmem", "disk", "maxdisk", "level")
SAFE_RESOURCE_FIELDS = (
    "vmid",
    "name",
    "node",
    "status",
    "type",
    "template",
    "uptime",
    "cpu",
    "mem",
    "maxmem",
    "disk",
    "maxdisk",
    "maxdisk",
)
SAFE_VM_STATUS_FIELDS = (
    "vmid",
    "name",
    "status",
    "uptime",
    "cpu",
    "mem",
    "maxmem",
    "disk",
    "maxdisk",
    "qmpstatus",
    "lock",
    "agent",
    "tags",
)
SAFE_TASK_FIELDS = ("upid", "node", "pid", "type", "id", "starttime", "endtime", "status", "exitstatus")


class ProxmoxClient:
    def __init__(self, base_url: str, token_id: str, token_secret: str, *, ca_pem: str | None = None) -> None:
        if not token_id or not token_secret:
            raise ConnectorError("credential_invalid", "Proxmox token is incomplete", status=409)
        if "!" not in token_id:
            raise ConnectorError(
                "credential_invalid", "Proxmox token id must be in user@realm!tokenid form", status=409
            )
        self.base_url = base_url.rstrip("/")
        self._token_id = token_id
        self._token_secret = token_secret
        self._ca_pem = ca_pem
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ProxmoxClient:
        self._client = make_client(ca_pem=self._ca_pem)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ProxmoxClient used outside its context")
        return self._client

    def _headers(self) -> dict[str, str]:
        # This header must never be logged or included in errors.
        return {
            "Authorization": f"PVEAPIToken={self._token_id}={self._token_secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def get(self, path: str, *, max_bytes: int, timeout: float) -> Any:
        data = await request_json(
            self.client,
            "GET",
            f"{self.base_url}/api2/json{path}",
            headers=self._headers(),
            max_bytes=max_bytes,
            timeout=timeout,
        )
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    async def test(self) -> dict[str, Any]:
        data = await self.get("/version", max_bytes=4096, timeout=10)
        if not isinstance(data, dict) or "version" not in data:
            raise ConnectorError("proxmox_unexpected", "the endpoint is not a Proxmox VE API")
        return {"reachable": True, "release": str(data.get("release", ""))[:32]}


def _pick(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: data[field] for field in fields if field in data}


async def list_nodes(
    client: ProxmoxClient,
    *,
    offset: int,
    limit: int,
    max_bytes: int,
    timeout: float,
    filters: dict[str, tuple[str, ...]],
    excludes: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    from ..utils import match_any

    data = await client.get("/nodes", max_bytes=max_bytes, timeout=timeout)
    if not isinstance(data, list):
        raise ConnectorError("proxmox_unexpected", "Proxmox returned an unexpected node list")
    items = [_pick(node, SAFE_NODE_FIELDS) for node in data if isinstance(node, dict)]
    allowed = filters.get("nodes")
    if allowed and "*" not in allowed:
        items = [item for item in items if match_any(str(item.get("node", "")), allowed)]
    denied = excludes.get("nodes")
    if denied:
        items = [item for item in items if not match_any(str(item.get("node", "")), denied)]
    items.sort(key=lambda item: str(item.get("node", "")))
    total = len(items)
    return {
        "items": items[offset : offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
    }


async def _list_resources(
    client: ProxmoxClient,
    *,
    resource_type: str,
    node: str | None,
    offset: int,
    limit: int,
    max_bytes: int,
    timeout: float,
    filters: dict[str, tuple[str, ...]],
    excludes: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    from ..utils import match_any

    data = await client.get("/cluster/resources", max_bytes=max_bytes, timeout=timeout)
    if not isinstance(data, list):
        raise ConnectorError("proxmox_unexpected", "Proxmox returned an unexpected resource list")
    items: list[dict[str, Any]] = []
    for resource in data:
        if not isinstance(resource, dict) or resource.get("type") != resource_type:
            continue
        picked = _pick(resource, SAFE_RESOURCE_FIELDS)
        if node and picked.get("node") != node:
            continue
        items.append(picked)

    allowed_nodes = filters.get("nodes")
    if allowed_nodes and "*" not in allowed_nodes:
        items = [item for item in items if match_any(str(item.get("node", "")), allowed_nodes)]
    allowed_vms = filters.get("vms" if resource_type == "qemu" else "containers")
    if allowed_vms and "*" not in allowed_vms:
        items = [item for item in items if match_any(str(item.get("vmid", "")), allowed_vms)]
    denied_nodes = excludes.get("nodes")
    if denied_nodes:
        items = [item for item in items if not match_any(str(item.get("node", "")), denied_nodes)]
    denied_vms = excludes.get("vms" if resource_type == "qemu" else "containers")
    if denied_vms:
        items = [item for item in items if not match_any(str(item.get("vmid", "")), denied_vms)]
    items.sort(key=lambda item: (str(item.get("node", "")), int(item.get("vmid", 0))))
    total = len(items)
    return {
        "items": items[offset : offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
    }


async def list_vms(client: ProxmoxClient, **kwargs: Any) -> dict[str, Any]:
    return await _list_resources(client, resource_type="qemu", **kwargs)


async def list_containers(client: ProxmoxClient, **kwargs: Any) -> dict[str, Any]:
    return await _list_resources(client, resource_type="lxc", **kwargs)


async def get_vm_status(
    client: ProxmoxClient,
    *,
    node: str,
    vmid: int,
    kind: str,
    max_bytes: int,
    timeout: float,
) -> dict[str, Any]:
    node = validate_node(node)
    vmid = validate_vmid(vmid)
    if kind not in {"qemu", "lxc"}:
        raise ConnectorError("validation_error", "VM kind must be qemu or lxc", status=422)
    data = await client.get(
        f"/nodes/{quote(node, safe='')}/{kind}/{vmid}/status/current",
        max_bytes=max_bytes,
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise ConnectorError("proxmox_unexpected", "Proxmox returned an unexpected status payload")
    return _pick(data, SAFE_VM_STATUS_FIELDS)


async def get_task_status(
    client: ProxmoxClient, *, node: str, upid: str, max_bytes: int, timeout: float
) -> dict[str, Any]:
    node = validate_node(node)
    upid = validate_upid(upid)
    data = await client.get(
        f"/nodes/{quote(node, safe='')}/tasks/{quote(upid, safe='')}/status",
        max_bytes=max_bytes,
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise ConnectorError("proxmox_unexpected", "Proxmox returned an unexpected task payload")
    return _pick(data, SAFE_TASK_FIELDS)
