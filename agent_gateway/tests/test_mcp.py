"""MCP transport: JSON-RPC over the stateless Streamable HTTP endpoint.

The MCP server is mounted inside the agent app, so these tests also prove that
the same bearer authentication, scope filtering, policy engine, approvals, and
connector code used by REST answers MCP requests. Responses are SSE frames
(`json_response=False`), which is what the pinned SDK emits.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
from starlette.applications import Starlette

from gateway.api.apps import build_admin_app, build_agent_app
from gateway.runtime import Runtime

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def sse_payload(response: httpx.Response) -> dict[str, object]:
    """Extract the single JSON-RPC object from an SSE response."""

    assert response.status_code == 200, response.text
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"no data frame in response: {response.text[:200]}")


@asynccontextmanager
async def mcp_session(runtime: Runtime, token: str | None) -> AsyncIterator[httpx.AsyncClient]:
    app: Starlette = build_agent_app(runtime)
    headers = dict(MCP_HEADERS)
    if token:
        headers.update(auth_headers(token))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
            base_url="http://gateway.test",
            headers=headers,
        ) as client,
    ):
        yield client


async def rpc(
    client: httpx.AsyncClient, method: str, params: dict[str, object] | None = None, request_id: int = 1
) -> dict[str, object]:
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )
    return sse_payload(response)


async def initialize(client: httpx.AsyncClient) -> dict[str, object]:
    return await rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0.0"},
        },
    )


async def admin_client(runtime: Runtime) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_admin_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=ADMIN_HEADERS,
    )


@pytest.fixture
async def setup(runtime: Runtime) -> dict[str, object]:
    async with await admin_client(runtime) as admin:
        await initialize_vault(admin)
        credential = await create_credential(admin, value={"token": "mcp-upstream-token"})
        connection = await create_connection(admin, credential_id=credential["id"])
        agent, token = await create_agent(
            admin,
            name="mcp-agent",
            scopes=["ha:read", "ha:write"],
            policy=read_policy(
                tools=["ha.get_state", "ha.list_entities", "ha.call_service"],
                constraints={"entities": ["light.kitchen"], "domains": ["light"]},
            ),
        )
    return {"connection": connection, "agent": agent, "token": token}


# ------------------------------------------------------------------ protocol


async def test_initialize_describes_the_gateway(runtime: Runtime, setup: dict) -> None:
    async with mcp_session(runtime, setup["token"]) as client:
        result = await initialize(client)
    payload = result["result"]
    assert payload["serverInfo"]["name"] == "ha-agent-gateway"
    assert "approval" in payload["instructions"].lower()
    assert payload["capabilities"]["tools"] == {"listChanged": False}


async def test_tools_list_matches_the_rest_catalogue(runtime: Runtime, setup: dict) -> None:
    async with mcp_session(runtime, setup["token"]) as client:
        await initialize(client)
        listed = await rpc(client, "tools/list")
    names = [tool["name"] for tool in listed["result"]["tools"]]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_agent_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=auth_headers(setup["token"]),
    ) as rest:
        rest_list = await rest.get("/api/v1/tools")
    rest_names = [tool["name"] for tool in rest_list.json()["items"]]
    assert names == rest_names
    assert "ha.get_state" in names


async def test_every_mcp_tool_schema_requires_a_connection_id(runtime: Runtime, setup: dict) -> None:
    async with mcp_session(runtime, setup["token"]) as client:
        await initialize(client)
        listed = await rpc(client, "tools/list")
    for tool in listed["result"]["tools"]:
        schema = tool["inputSchema"]
        assert schema["additionalProperties"] is False
        assert "connection_id" in schema["properties"]
        assert "connection_id" in schema["required"]
        assert "approval_id" in schema["properties"]
        assert tool["annotations"] is not None


async def test_scopes_hide_tools_from_the_mcp_catalogue(runtime: Runtime, setup: dict) -> None:
    async with await admin_client(runtime) as admin:
        _agent, read_only = await create_agent(
            admin,
            name="mcp-read-only",
            scopes=["ha:read"],
            policy=read_policy(tools=["ha.*"]),
        )
    async with mcp_session(runtime, read_only) as client:
        await initialize(client)
        listed = await rpc(client, "tools/list")
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "ha.get_state" in names
    assert "ha.call_service" not in names


# ------------------------------------------------------------------- calls


async def test_tool_call_reads_state_through_the_shared_operation_path(
    runtime: Runtime, setup: dict, ha_payload: dict
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(200, json=ha_payload)
        )
        async with mcp_session(runtime, setup["token"]) as client:
            await initialize(client)
            result = await rpc(
                client,
                "tools/call",
                {
                    "name": "ha.get_state",
                    "arguments": {
                        "connection_id": setup["connection"]["id"],
                        "entity_id": "light.kitchen",
                    },
                },
            )
        calls = list(mock.calls)
    assert result["result"]["isError"] is False
    structured = result["result"]["structuredContent"]
    assert structured["status"] == "succeeded"
    assert structured["result"]["state"] == "on"
    assert len(calls) == 1
    assert calls[0].request.headers["authorization"] == "Bearer mcp-upstream-token"


async def test_tool_call_requires_a_connection_id(runtime: Runtime, setup: dict) -> None:
    async with mcp_session(runtime, setup["token"]) as client:
        await initialize(client)
        result = await rpc(client, "tools/call", {"name": "ha.get_state", "arguments": {}})
    assert result["result"]["isError"] is True
    assert result["result"]["structuredContent"]["error"]["code"] == "validation_error"


async def test_policy_denial_is_returned_as_a_tool_error(runtime: Runtime, setup: dict) -> None:
    with respx.mock(assert_all_called=False) as mock:
        async with mcp_session(runtime, setup["token"]) as client:
            await initialize(client)
            result = await rpc(
                client,
                "tools/call",
                {
                    "name": "ha.get_state",
                    "arguments": {
                        "connection_id": setup["connection"]["id"],
                        "entity_id": "lock.front_door",
                    },
                },
            )
    assert result["result"]["isError"] is True
    assert result["result"]["structuredContent"]["error"]["code"] == "policy_denied"
    assert not mock.calls


async def test_write_tool_returns_a_pending_approval_and_never_executes(
    runtime: Runtime, setup: dict
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        async with mcp_session(runtime, setup["token"]) as client:
            await initialize(client)
            result = await rpc(
                client,
                "tools/call",
                {
                    "name": "ha.call_service",
                    "arguments": {
                        "connection_id": setup["connection"]["id"],
                        "domain": "light",
                        "service": "turn_on",
                        "entity_id": "light.kitchen",
                    },
                },
            )
    payload = result["result"]["structuredContent"]
    assert result["result"]["isError"] is False
    assert payload["status"] == "pending_approval"
    assert payload["approval_id"]
    assert "next_action" in payload
    assert not mock.calls

    # The approval is real: it is visible in the management API.
    async with await admin_client(runtime) as admin:
        pending = await admin.get("/api/admin/approvals?status=pending")
    assert pending.json()["items"][0]["id"] == payload["approval_id"]


async def test_mcp_rejects_missing_tokens(runtime: Runtime, setup: dict) -> None:
    async with mcp_session(runtime, None) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_mcp_honours_the_emergency_stop(runtime: Runtime, setup: dict) -> None:
    async with mcp_session(runtime, setup["token"]) as client:
        await initialize(client)
        async with await admin_client(runtime) as admin:
            await admin.put("/api/admin/settings", json={"emergency_disabled": True})
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_access_disabled"


async def test_endpoint_answers_both_mcp_paths(runtime: Runtime, setup: dict) -> None:
    async with mcp_session(runtime, setup["token"]) as client:
        for path in ("/mcp", "/mcp/"):
            response = await client.post(
                path,
                json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            )
            assert response.status_code == 200, path
            assert "result" in sse_payload(response)


async def test_rest_and_mcp_share_the_approval(runtime: Runtime, setup: dict) -> None:
    """A write call started over MCP can be completed over REST once approved."""

    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://ha.test:8123/api/services/light/turn_on").mock(
            return_value=httpx.Response(200, json=[{"entity_id": "light.kitchen"}])
        )
        async with mcp_session(runtime, setup["token"]) as client:
            await initialize(client)
            pending = await rpc(
                client,
                "tools/call",
                {
                    "name": "ha.call_service",
                    "arguments": {
                        "connection_id": setup["connection"]["id"],
                        "domain": "light",
                        "service": "turn_on",
                        "entity_id": "light.kitchen",
                    },
                },
            )
        approval_id = pending["result"]["structuredContent"]["approval_id"]

        async with await admin_client(runtime) as admin:
            approved = await admin.post(f"/api/admin/approvals/{approval_id}/approve", json={})
            assert approved.json()["status"] == "approved"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_agent_app(runtime), client=("127.0.0.1", 1)),
            base_url="http://gateway.test",
            headers=auth_headers(setup["token"]),
        ) as rest:
            executed = await rest.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.call_service",
                    "connection_id": setup["connection"]["id"],
                    "params": {
                        "domain": "light",
                        "service": "turn_on",
                        "entity_id": "light.kitchen",
                    },
                    "approval_id": approval_id,
                },
            )
        calls = list(mock.calls)
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "succeeded"
    assert len(calls) == 1
