"""Agent REST surface: auth, policy enforcement, approvals, and MCP parity."""

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
from gateway.errors import PolicyDenied
from gateway.runtime import Runtime
from gateway.tools.registry import ToolRegistry


async def admin_client(runtime: Runtime) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_admin_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=ADMIN_HEADERS,
    )


@pytest.fixture
async def setup(runtime: Runtime) -> dict[str, object]:
    """Unlocked vault, one Home Assistant connection, one allowlisted agent."""

    async with await admin_client(runtime) as admin:
        await initialize_vault(admin)
        credential = await create_credential(admin)
        connection = await create_connection(admin, credential_id=credential["id"])
        agent, token = await create_agent(
            admin,
            name="ha-agent",
            scopes=["ha:read", "ha:write"],
            policy=read_policy(
                tools=["ha.get_state", "ha.call_service", "ha.list_entities"],
                constraints={"entities": ["light.kitchen"], "domains": ["light"]},
            ),
        )
    return {"connection": connection, "agent": agent, "token": token}


def agent_http(runtime: Runtime, token: str | None = None, **kwargs: object) -> httpx.AsyncClient:
    headers = auth_headers(token) if token else {}
    headers.update(kwargs.pop("headers", {}))  # type: ignore[arg-type]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_agent_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=headers,
    )


# -------------------------------------------------------------------- auth


async def test_healthz_does_not_require_a_token(runtime: Runtime) -> None:
    async with agent_http(runtime) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200


async def test_missing_and_malformed_tokens_are_rejected(runtime: Runtime) -> None:
    async with agent_http(runtime) as client:
        missing = await client.get("/api/v1/health")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "unauthenticated"
        assert "www-authenticate" in missing.headers

        basic = await client.get("/api/v1/health", headers={"Authorization": "Basic abc"})
        assert basic.status_code == 401

        unknown = await client.get("/api/v1/health", headers=auth_headers("hag_does-not-exist"))
        assert unknown.status_code == 401


async def test_tokens_in_query_strings_are_rejected(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime) as client:
        response = await client.get(f"/api/v1/connections?access_token={setup['token']}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "token_in_query"


async def test_revoked_agent_cannot_authenticate(runtime: Runtime, setup: dict) -> None:
    async with await admin_client(runtime) as admin:
        await admin.post(f"/api/admin/agents/{setup['agent']['id']}/revoke", json={})
    async with agent_http(runtime, setup["token"]) as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 401


async def test_emergency_disable_blocks_agents_only(runtime: Runtime, setup: dict) -> None:
    async with await admin_client(runtime) as admin:
        await admin.put("/api/admin/settings", json={"emergency_disabled": True})
        status = await admin.get("/api/admin/status")
    assert status.status_code == 200

    async with agent_http(runtime, setup["token"]) as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_access_disabled"


async def test_browser_origin_allowlist(runtime: Runtime, setup: dict) -> None:
    # Default: no browser origins are allowed, so an Origin header is rejected.
    async with agent_http(runtime, setup["token"], headers={"Origin": "https://evil.example"}) as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_rejected"


# ---------------------------------------------------------------- catalogue


async def test_agent_tools_are_limited_by_scope_and_groups(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        response = await client.get("/api/v1/tools")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["items"]}
    # The catalogue follows scope and enabled groups; the policy still decides
    # per call, so ha.get_config is listed even though the policy omits it.
    assert names == {"ha.get_state", "ha.call_service", "ha.list_entities", "ha.get_config"}
    assert all(tool["input_schema"]["additionalProperties"] is False for tool in response.json()["items"])


async def test_agent_cannot_see_credentials_or_config(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        connections = await client.get("/api/v1/connections")
    payload = connections.json()
    assert "credential_id" not in json.dumps(payload)
    assert payload["items"][0]["name"] == "home"


async def test_rest_and_mcp_tool_catalogues_match(runtime: Runtime, setup: dict) -> None:
    principal = runtime.agents.authenticate(setup["token"])
    assert principal is not None
    registry: ToolRegistry = runtime.tools
    rest_tools = registry.public_tools(principal)
    mcp_tools = registry.mcp_tools(principal)
    assert [tool["name"] for tool in rest_tools] == [tool.name for tool in mcp_tools]
    assert [tool["input_schema"] for tool in rest_tools] == [tool.input_schema for tool in mcp_tools]
    assert all(tool.annotations is not None for tool in mcp_tools)


async def test_audit_endpoint_requires_scope(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        denied = await client.get("/api/v1/audit")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "policy_denied"


# --------------------------------------------------------------- operations


async def test_read_operation_succeeds_with_allowlisted_entity(
    runtime: Runtime, setup: dict, ha_payload: dict
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(200, json=ha_payload)
        )
        async with agent_http(runtime, setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.get_state",
                    "connection_id": setup["connection"]["id"],
                    "params": {"entity_id": "light.kitchen"},
                },
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["state"] == "on"
    assert body["operation_id"]

    async with agent_http(runtime, setup["token"]) as client:
        lookup = await client.get(f"/api/v1/operations/{body['operation_id']}")
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "succeeded"


async def test_scope_is_revalidated_immediately_before_execution(
    runtime: Runtime, setup: dict, monkeypatch: pytest.MonkeyPatch, ha_payload: dict
) -> None:
    """A scope removed while a request is in flight must stop that request."""

    principal = runtime.agents.authenticate(setup["token"])
    assert principal is not None
    original_get_principal = runtime.agents.get_principal

    def remove_read_scope(agent_id: str):
        runtime.db.execute("UPDATE agents SET scopes=? WHERE id=?", (json.dumps(["ha:write"]), agent_id))
        return original_get_principal(agent_id)

    monkeypatch.setattr(runtime.agents, "get_principal", remove_read_scope)
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(200, json=ha_payload)
        )
        with pytest.raises(PolicyDenied, match="scope"):
            await runtime.operations.execute(
                agent=principal,
                tool_name="ha.get_state",
                connection_id=setup["connection"]["id"],
                params={"entity_id": "light.kitchen"},
            )
    assert not mock.calls


async def test_approval_requirement_is_revalidated_before_execution(
    runtime: Runtime, setup: dict, monkeypatch: pytest.MonkeyPatch, ha_payload: dict
) -> None:
    """A policy that starts requiring approval mid-flight must fail closed."""

    principal = runtime.agents.authenticate(setup["token"])
    assert principal is not None
    original_evaluate = runtime.policies.evaluate
    calls = 0

    def require_approval_on_recheck(request):
        nonlocal calls
        calls += 1
        decision = original_evaluate(request)
        if calls == 2:
            return decision.model_copy(update={"require_approval": True})
        return decision

    monkeypatch.setattr(runtime.policies, "evaluate", require_approval_on_recheck)
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(200, json=ha_payload)
        )
        with pytest.raises(PolicyDenied, match="approval"):
            await runtime.operations.execute(
                agent=principal,
                tool_name="ha.get_state",
                connection_id=setup["connection"]["id"],
                params={"entity_id": "light.kitchen"},
            )
    assert not mock.calls


async def test_target_outside_policy_is_denied_before_any_network_call(runtime: Runtime, setup: dict) -> None:
    with respx.mock(assert_all_called=False) as mock:
        async with agent_http(runtime, setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.get_state",
                    "connection_id": setup["connection"]["id"],
                    "params": {"entity_id": "lock.front_door"},
                },
            )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_denied"
    assert not mock.calls


@pytest.mark.parametrize(
    "service_data",
    [
        {"entity_id": "lock.front_door"},
        {"target": {"entity_id": "lock.front_door"}},
        {"target": {"area_id": "kitchen"}},
        {"entity_id": "light.kitchen", "area_id": "kitchen"},
    ],
)
async def test_service_data_entity_is_policy_checked(
    runtime: Runtime, setup: dict, service_data: dict
) -> None:
    """Nested or broad service targets must not bypass entity constraints."""

    with respx.mock(assert_all_called=False) as mock:
        async with agent_http(runtime, setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.call_service",
                    "connection_id": setup["connection"]["id"],
                    "params": {
                        "domain": "light",
                        "service": "turn_on",
                        "data": service_data,
                    },
                },
            )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "policy_denied"
    assert not mock.calls


async def test_allowed_service_data_entity_still_reaches_approval(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        response = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {
                    "domain": "light",
                    "service": "turn_on",
                    "data": {"entity_id": ["light.kitchen"]},
                },
            },
        )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending_approval"


async def test_untargeted_service_call_fails_closed_with_entity_policy(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        response = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {"domain": "light", "service": "turn_on"},
            },
        )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "policy_denied"


async def test_missing_scope_is_denied(runtime: Runtime, setup: dict) -> None:
    async with await admin_client(runtime) as admin:
        _, token = await create_agent(
            admin,
            name="read-only",
            scopes=["ha:read"],
            policy=read_policy(),
        )
    async with agent_http(runtime, token) as client:
        response = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {"domain": "light", "service": "turn_on"},
            },
        )
    assert response.status_code == 403
    assert "ha:write" in response.json()["error"]["message"]


async def test_write_operation_requires_approval_then_executes_once(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        pending = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"},
            },
        )
    assert pending.status_code == 202
    body = pending.json()
    assert body["status"] == "pending_approval"
    approval_id = body["approval_id"]
    assert approval_id and body["expires_at"]

    # The agent can read the approval but cannot approve it: there is no route.
    async with agent_http(runtime, setup["token"]) as client:
        check = await client.get(f"/api/v1/approvals/{approval_id}")
        assert check.json()["status"] == "pending"
        approve_attempt = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={})
        assert approve_attempt.status_code == 404

    async with await admin_client(runtime) as admin:
        approved = await admin.post(f"/api/admin/approvals/{approval_id}/approve", json={})
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://ha.test:8123/api/services/light/turn_on").mock(
            return_value=httpx.Response(200, json=[{"entity_id": "light.kitchen"}])
        )
        async with agent_http(runtime, setup["token"]) as client:
            executed = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.call_service",
                    "connection_id": setup["connection"]["id"],
                    "params": {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"},
                    "approval_id": approval_id,
                },
            )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "succeeded"
    assert executed.json()["result"]["affected_entities"] == 1

    # Single use: replaying the same approval must fail.
    async with agent_http(runtime, setup["token"]) as client:
        replay = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"},
                "approval_id": approval_id,
            },
        )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "approval_invalid"


async def test_approval_does_not_authorize_different_parameters(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        pending = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"},
            },
        )
    approval_id = pending.json()["approval_id"]
    async with await admin_client(runtime) as admin:
        await admin.post(f"/api/admin/approvals/{approval_id}/approve", json={})

    with respx.mock(assert_all_called=False) as mock:
        async with agent_http(runtime, setup["token"]) as client:
            response = await client.post(
                "/api/v1/operations",
                json={
                    "tool": "ha.call_service",
                    "connection_id": setup["connection"]["id"],
                    "params": {"domain": "light", "service": "turn_off", "entity_id": "light.kitchen"},
                    "approval_id": approval_id,
                },
            )
    assert response.status_code == 409
    assert not mock.calls


async def test_blocked_service_domain_is_denied_even_with_approval(runtime: Runtime, setup: dict) -> None:
    async with await admin_client(runtime) as admin:
        _, token = await create_agent(
            admin,
            name="shell-agent",
            scopes=["ha:write"],
            policy=read_policy(tools=["ha.call_service"], constraints={}),
        )
    async with agent_http(runtime, token) as client:
        pending = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {"domain": "shell_command", "service": "reboot_everything"},
            },
        )
    assert pending.status_code == 202
    async with await admin_client(runtime) as admin:
        await admin.post(f"/api/admin/approvals/{pending.json()['approval_id']}/approve", json={})

    async with agent_http(runtime, token) as client:
        response = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.call_service",
                "connection_id": setup["connection"]["id"],
                "params": {"domain": "shell_command", "service": "reboot_everything"},
                "approval_id": pending.json()["approval_id"],
            },
        )
    assert response.status_code == 403
    assert "blocked" in response.json()["error"]["message"]


async def test_invalid_parameters_are_rejected_with_safe_details(runtime: Runtime, setup: dict) -> None:
    async with agent_http(runtime, setup["token"]) as client:
        response = await client.post(
            "/api/v1/operations",
            json={
                "tool": "ha.get_state",
                "connection_id": setup["connection"]["id"],
                "params": {"entity_id": "not an entity"},
            },
        )
    assert response.status_code == 422
    assert "not an entity" not in response.text


async def test_unknown_operation_is_not_found_for_other_agents(runtime: Runtime, setup: dict) -> None:
    async with await admin_client(runtime) as admin:
        _, other = await create_agent(admin, name="other", scopes=["ha:read"], policy=read_policy())
    async with agent_http(runtime, other) as client:
        response = await client.get("/api/v1/operations/deadbeef")
    assert response.status_code == 404


async def test_expired_token_is_rejected(runtime: Runtime, setup: dict) -> None:
    """An expiry in the past (written directly, as time passing would produce)
    must fail authentication exactly like a revoked token."""
    from datetime import timedelta

    from gateway.utils import iso, utcnow

    runtime.db.execute(
        "UPDATE agents SET expires_at=? WHERE id=?",
        (iso(utcnow() - timedelta(minutes=1)), setup["agent"]["id"]),
    )
    async with agent_http(runtime, str(setup["token"])) as client:
        response = await client.get("/api/v1/tools")
    assert response.status_code == 401
    assert runtime.agents.authenticate(setup["token"]) is None


async def test_temporary_agent_cannot_read_global_audit(runtime: Runtime, setup: dict) -> None:
    """Legacy or manually altered temporary rows still fail closed at the API."""

    async with await admin_client(runtime) as admin:
        agent, token = await create_agent(admin, name="legacy-audit", scopes=["audit:read"])
    runtime.db.execute(
        "UPDATE agents SET kind='temporary', allowed_connections='[]', operation_classes='[\"read\"]', "
        "session_epoch=? WHERE id=?",
        (runtime.agents.current_session_epoch(), agent["id"]),
    )
    async with agent_http(runtime, token) as client:
        response = await client.get("/api/v1/audit")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_denied"
