"""Connector behaviour against mocked upstreams.

Home Assistant and Proxmox VE are exercised through the agent REST surface so
the whole path (policy -> approval -> connector -> audit) runs, plus direct
unit checks for refusals that must happen before any network call.

No real Home Assistant, Proxmox VE, or SSH host is contacted: httpx is routed
through `respx`, and the address probes in each test are assertions that the
upstream was or was not called.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from helpers import (
    ADMIN_HEADERS,
    auth_headers,
    create_agent,
    create_connection,
    create_credential,
    initialize_vault,
    read_policy,
)

from gateway.api.apps import build_admin_app, build_agent_app
from gateway.connectors.home_assistant import HARD_DENIED_SERVICE_DOMAINS, call_service
from gateway.connectors.http import build_ssl_context, make_client
from gateway.connectors.proxmox import SAFE_NODE_FIELDS, SAFE_VM_STATUS_FIELDS
from gateway.errors import ConnectorError, PolicyDenied
from gateway.runtime import Runtime

HA_TOKEN = "ha-secret-token-value-123"
PVE_TOKEN_ID = "agent@pve!gateway"
PVE_TOKEN_SECRET = "pve-secret-456"


async def admin_client(runtime: Runtime) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_admin_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=ADMIN_HEADERS,
    )


def agent_client(runtime: Runtime, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_agent_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=auth_headers(token),
    )


@pytest.fixture
async def ha_setup(runtime: Runtime) -> dict[str, object]:
    """Home Assistant connection plus an agent allowed to read and write light.*."""

    async with await admin_client(runtime) as admin:
        await initialize_vault(admin)
        credential = await create_credential(admin, value={"token": HA_TOKEN})
        connection = await create_connection(admin, credential_id=credential["id"])
        agent, token = await create_agent(
            admin,
            name="connector-agent",
            scopes=["ha:read", "ha:write"],
            policy=read_policy(
                tools=["ha.*"],
                constraints={"entities": ["light.*"], "domains": ["light"]},
            ),
        )
    return {"connection": connection, "agent": agent, "token": token}


@pytest.fixture
async def pve_setup(runtime: Runtime) -> dict[str, object]:
    async with await admin_client(runtime) as admin:
        await initialize_vault(admin)
        credential = await create_credential(
            admin,
            name="pve token",
            kind="proxmox_token",
            value={"token_id": PVE_TOKEN_ID, "token_secret": PVE_TOKEN_SECRET},
        )
        connection = await create_connection(
            admin,
            name="pve",
            connection_type="proxmox",
            config={"base_url": "https://pve.test:8006", "verify_tls": True},
            credential_id=credential["id"],
        )
        agent, token = await create_agent(
            admin,
            name="pve-agent",
            scopes=["proxmox:read"],
            policy=read_policy(tools=["proxmox.*"], constraints={"nodes": ["pve1"]}),
        )
    return {"connection": connection, "agent": agent, "token": token}


# --------------------------------------------------------- TLS is always on


def test_connector_tls_verification_cannot_be_disabled() -> None:
    context = build_ssl_context(None)
    assert context.check_hostname is True
    assert context.verify_mode.name == "CERT_REQUIRED"


def test_connector_client_rejects_an_invalid_ca_instead_of_disabling_tls() -> None:
    # A bad CA raises instead of silently falling back to an unverified client.
    with pytest.raises(ConnectorError) as excinfo:
        make_client(ca_pem="-----BEGIN CERTIFICATE-----\nnot-a-cert\n-----END CERTIFICATE-----")
    assert excinfo.value.code == "tls_configuration_error"


async def test_connector_client_does_not_follow_redirects() -> None:
    client = make_client()
    try:
        assert client.follow_redirects is False
    finally:
        await client.aclose()


# ---------------------------------------------------------- Home Assistant


async def test_get_state_uses_the_stored_token_and_returns_no_secret(
    runtime: Runtime, ha_setup: dict, ha_payload: dict
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(200, json=ha_payload)
        )
        async with agent_client(runtime, ha_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.get_state",
                    "connection_id": ha_setup["connection"]["id"],
                    "params": {"entity_id": "light.kitchen"},
                },
            )
        sent = mock.calls[0].request
    assert response.status_code == 200, response.text
    assert sent.headers["authorization"] == f"Bearer {HA_TOKEN}"
    body = response.text
    assert HA_TOKEN not in body
    assert "super-secret" not in body
    assert response.json()["result"]["state"] == "on"


async def test_list_entities_applies_domain_and_entity_filters(runtime: Runtime, ha_setup: dict) -> None:
    states = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
        {"entity_id": "light.hall", "state": "off", "attributes": {}},
        {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
        {"entity_id": "sensor.power", "state": "12", "attributes": {}},
    ]
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states").mock(return_value=httpx.Response(200, json=states))
        async with agent_client(runtime, ha_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.list_entities",
                    "connection_id": ha_setup["connection"]["id"],
                    "params": {"limit": 10},
                },
            )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    ids = [item["entity_id"] for item in result["items"]]
    assert ids == ["light.hall", "light.kitchen"]
    assert result["total"] == 2
    # Attributes are summarised, not copied wholesale.
    assert set(result["items"][0]) == {"entity_id", "state", "friendly_name", "last_changed", "domain"}


async def test_get_config_returns_only_safe_fields(runtime: Runtime, ha_setup: dict) -> None:
    upstream = {
        "version": "2026.10.1",
        "location_name": "Home",
        "time_zone": "Asia/Tbilisi",
        "unit_system": {"length": "km"},
        "components": ["http", "mqtt"],
        "allowlist_external_dirs": ["/config"],
        "api_key": "should-never-appear",
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/config").mock(return_value=httpx.Response(200, json=upstream))
        async with agent_client(runtime, ha_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.get_config",
                    "connection_id": ha_setup["connection"]["id"],
                    "params": {},
                },
            )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert set(result) == {"version", "location_name", "time_zone", "unit_system"}
    assert "should-never-appear" not in response.text


@pytest.mark.parametrize(
    ("status", "code", "http_status"),
    [
        (401, "upstream_auth_failed", 502),
        (404, "upstream_not_found", 404),
        (429, "upstream_rate_limited", 503),
        (302, "upstream_redirect", 502),
        (500, "upstream_error", 502),
    ],
)
async def test_upstream_failures_are_mapped_without_leaking_the_token(
    runtime: Runtime, ha_setup: dict, status: int, code: str, http_status: int
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(status, text="upstream says no")
        )
        async with agent_client(runtime, ha_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.get_state",
                    "connection_id": ha_setup["connection"]["id"],
                    "params": {"entity_id": "light.kitchen"},
                },
            )
    assert response.status_code == http_status, response.text
    payload = response.json()
    assert payload["error"]["code"] == code
    assert HA_TOKEN not in response.text
    assert "upstream says no" not in response.text


async def test_blocked_service_domains_are_refused_before_the_network() -> None:
    assert "shell_command" in HARD_DENIED_SERVICE_DOMAINS
    for domain in sorted(HARD_DENIED_SERVICE_DOMAINS):
        with pytest.raises(PolicyDenied):
            await call_service(
                None,  # type: ignore[arg-type] - refuses before touching the client
                domain=domain,
                service="run",
                entity_id=None,
                data={},
                max_bytes=1024,
                timeout=5,
            )


async def test_connection_test_reports_success_and_sets_status(runtime: Runtime, ha_setup: dict) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/").mock(
            return_value=httpx.Response(200, json={"message": "API running."})
        )
        async with await admin_client(runtime) as admin:
            result = await admin.post(f"/api/admin/connections/{ha_setup['connection']['id']}/test", json={})
            listing = await admin.get("/api/admin/connections")
    assert result.status_code == 200, result.text
    assert result.json()["ok"] is True
    assert listing.json()["items"][0]["status"] == "ok"


async def test_connection_test_failure_is_reported_not_raised(runtime: Runtime, ha_setup: dict) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/").mock(return_value=httpx.Response(200, json={"message": "nope"}))
        async with await admin_client(runtime) as admin:
            result = await admin.post(f"/api/admin/connections/{ha_setup['connection']['id']}/test", json={})
    assert result.status_code == 200
    assert result.json()["ok"] is False
    assert result.json()["error"]["code"] == "home_assistant_unexpected"
    assert HA_TOKEN not in result.text


# ------------------------------------------------------------- Proxmox VE


async def test_proxmox_list_nodes_uses_a_token_header_and_safe_fields(
    runtime: Runtime, pve_setup: dict
) -> None:
    upstream = [
        {
            "node": "pve1",
            "status": "online",
            "uptime": 1000,
            "cpu": 0.1,
            "mem": 1024,
            "maxmem": 4096,
            "disk": 10,
            "maxdisk": 100,
            "level": "",
            "ssl_fingerprint": "AA:BB",
        },
        {"node": "pve2", "status": "offline", "cpu": 0},
    ]
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://pve.test:8006/api2/json/nodes").mock(
            return_value=httpx.Response(200, json={"data": upstream})
        )
        async with agent_client(runtime, pve_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "proxmox.list_nodes",
                    "connection_id": pve_setup["connection"]["id"],
                    "params": {},
                },
            )
        request = mock.calls[0].request
    assert response.status_code == 200, response.text
    assert request.headers["authorization"] == f"PVEAPIToken={PVE_TOKEN_ID}={PVE_TOKEN_SECRET}"
    result = response.json()["result"]
    # The agent's policy only allows node pve1.
    assert [item["node"] for item in result["items"]] == ["pve1"]
    assert set(result["items"][0]) <= set(SAFE_NODE_FIELDS)
    assert "ssl_fingerprint" not in response.text
    assert PVE_TOKEN_SECRET not in response.text


async def test_proxmox_vm_status_is_bounded(runtime: Runtime, pve_setup: dict) -> None:
    upstream = {
        "vmid": 100,
        "name": "git",
        "status": "running",
        "cpu": 0.02,
        "mem": 2048,
        "maxmem": 4096,
        "uptime": 500,
        "qmpstatus": "running",
        "secret_field": "do-not-return",
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://pve.test:8006/api2/json/nodes/pve1/qemu/100/status/current").mock(
            return_value=httpx.Response(200, json={"data": upstream})
        )
        async with agent_client(runtime, pve_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "proxmox.get_vm_status",
                    "connection_id": pve_setup["connection"]["id"],
                    "params": {"node": "pve1", "vmid": 100, "kind": "qemu"},
                },
            )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["status"] == "running"
    assert set(result) <= set(SAFE_VM_STATUS_FIELDS)
    assert "do-not-return" not in response.text


async def test_proxmox_auth_failure_does_not_leak_the_token(runtime: Runtime, pve_setup: dict) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://pve.test:8006/api2/json/nodes").mock(return_value=httpx.Response(401))
        async with agent_client(runtime, pve_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "proxmox.list_nodes",
                    "connection_id": pve_setup["connection"]["id"],
                    "params": {},
                },
            )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_auth_failed"
    assert PVE_TOKEN_SECRET not in response.text
    assert PVE_TOKEN_ID not in response.text


async def test_proxmox_unexpected_payload_is_reported(runtime: Runtime, pve_setup: dict) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://pve.test:8006/api2/json/nodes").mock(
            return_value=httpx.Response(200, json={"data": {"not": "a list"}})
        )
        async with agent_client(runtime, pve_setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "proxmox.list_nodes",
                    "connection_id": pve_setup["connection"]["id"],
                    "params": {},
                },
            )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "proxmox_unexpected"


@pytest.mark.parametrize("tool", ["proxmox.delete_vm", "proxmox.stop_vm", "proxmox.snapshot"])
async def test_proxmox_mutations_do_not_exist(runtime: Runtime, pve_setup: dict, tool: str) -> None:
    async with agent_client(runtime, pve_setup["token"]) as client:
        response = await client.post(
            "/api/v1/operations",
            json={"tool": tool, "connection_id": pve_setup["connection"]["id"], "params": {}},
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_registry_has_no_proxmox_write_tools(runtime: Runtime) -> None:
    proxmox = [spec for spec in runtime.tools.all_specs() if spec.group == "proxmox"]
    assert proxmox
    assert all(spec.risk == "read" for spec in proxmox)


async def test_audit_never_contains_upstream_credentials(runtime: Runtime, ha_setup: dict) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(
                200,
                json={"entity_id": "light.kitchen", "state": "on", "attributes": {}},
            )
        )
        async with agent_client(runtime, ha_setup["token"]) as client:
            await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.get_state",
                    "connection_id": ha_setup["connection"]["id"],
                    "params": {"entity_id": "light.kitchen"},
                },
            )
    async with await admin_client(runtime) as admin:
        audit = await admin.get("/api/admin/audit?limit=50")
    assert audit.status_code == 200
    text = json.dumps(audit.json())
    assert HA_TOKEN not in text
    assert PVE_TOKEN_SECRET not in text
    assert any(event["tool"] == "ha.get_state" for event in audit.json()["items"])


def test_connector_error_codes_are_stable() -> None:
    error = ConnectorError("upstream_timeout", "the upstream service did not respond in time", status=504)
    assert error.status == 504
    assert error.code == "upstream_timeout"
