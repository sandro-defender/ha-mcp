"""Temporary agent-access broker: issuance, handoff, scope, revocation, restart.

These tests exercise the real ASGI applications and the real database.  No
upstream Home Assistant, Proxmox, or SSH server is contacted: connection
records exist only as vault-backed configuration rows and every tool call that
would reach the network is expected to fail after the authorization checks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from helpers import PASSWORD, create_connection, create_credential

from gateway.api.apps import build_admin_app, build_agent_app
from gateway.runtime import Runtime
from gateway.settings import Settings


async def _connections(runtime: Runtime, admin: httpx.AsyncClient, count: int = 1) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for index in range(count):
        credential = await create_credential(
            admin, name=f"ha-token-{index}", value={"token": f"upstream-secret-{index}"}
        )
        connection = await create_connection(
            admin,
            name=f"home-{index}",
            config={"base_url": f"http://ha-{index}.test:8123"},
            credential_id=credential["id"],
        )
        created.append(connection)
    return created


async def _issue(
    admin: httpx.AsyncClient,
    *,
    name: str = "agent-session",
    scopes: list[str] | None = None,
    connection_ids: list[str] | None = None,
    operation_classes: list[str] | None = None,
    ttl_seconds: int = 3600,
    revoke_previous: bool = True,
) -> dict[str, Any]:
    response = await admin.post(
        "/api/admin/agent-access/sessions",
        json={
            "name": name,
            "scopes": scopes or ["ha:read"],
            "connection_ids": connection_ids or [],
            "operation_classes": operation_classes or ["read"],
            "ttl_seconds": ttl_seconds,
            "revoke_previous": revoke_previous,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _code(handoff: dict[str, Any]) -> str:
    return str(handoff["url"]).split("code=", 1)[1]


@pytest.fixture
async def agent_client(runtime: Runtime):
    transport = httpx.ASGITransport(app=build_agent_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        yield client


# --------------------------------------------------------------------- issue


async def test_issue_returns_handoff_and_never_a_token(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    body = await _issue(admin_unlocked, connection_ids=[connection["id"]])

    assert "token" not in body
    assert body["session"]["status"] == "pending"
    assert body["session"]["token_prefix"] == "pending"
    assert body["handoff"]["single_use"] is True
    assert body["handoff"]["url"].startswith("/agent#code=hag_h_")
    assert body["connection_config"]["agent_page_url"].endswith("/agent")
    assert "token" not in body["connection_config"]
    rules = body["policy"]["document"]["rules"]
    assert len(rules) == 1
    assert rules[0]["require_approval"] is False


async def test_issue_requires_ingress_and_csrf(runtime: Runtime, admin_unlocked: httpx.AsyncClient) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    payload = {
        "name": "session-x",
        "scopes": ["ha:read"],
        "connection_ids": [connection["id"]],
        "operation_classes": ["read"],
    }
    transport = httpx.ASGITransport(app=build_admin_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as bare:
        # No verified Ingress user header: rejected before the endpoint runs.
        response = await bare.post(
            "/api/admin/agent-access/sessions", json=payload, headers={"X-Gateway-Request": "1"}
        )
        assert response.status_code == 401
        # Verified user but no CSRF header.
        response = await bare.post(
            "/api/admin/agent-access/sessions", json=payload, headers={"X-Remote-User-Id": "admin-user"}
        )
        assert response.status_code == 403


async def test_issue_rejects_scope_connection_mismatch(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    response = await admin_unlocked.post(
        "/api/admin/agent-access/sessions",
        json={
            "name": "session-x",
            "scopes": ["ssh:read"],
            "connection_ids": [connection["id"]],
            "operation_classes": ["read"],
        },
    )
    assert response.status_code in {400, 422}, response.text


async def test_issue_rejects_destructive_and_write_without_scope(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    response = await admin_unlocked.post(
        "/api/admin/agent-access/sessions",
        json={
            "name": "session-x",
            "scopes": ["ha:read"],
            "connection_ids": [connection["id"]],
            "operation_classes": ["read", "write"],
        },
    )
    assert response.status_code >= 400
    response = await admin_unlocked.post(
        "/api/admin/agent-access/sessions",
        json={
            "name": "session-y",
            "scopes": ["ha:read"],
            "connection_ids": [connection["id"]],
            "operation_classes": ["destructive"],
        },
    )
    assert response.status_code == 422


async def test_journal_only_session_needs_no_connection(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    body = await _issue(admin_unlocked, scopes=["journal:read"], connection_ids=[])
    assert body["session"]["allowed_connection_ids"] == []
    tools = body["policy"]["document"]["rules"][0]["tools"]
    assert "journal.read_project_state" in tools
    assert all(tool.startswith("journal.") for tool in tools)


# -------------------------------------------------------------------- redeem


async def test_handoff_redeem_delivers_token_once(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    code = _code(issued["handoff"])

    response = await agent_client.post("/agent/api/redeem", json={"code": code})
    assert response.status_code == 200, response.text
    payload = response.json()
    token = payload["token"]
    assert token.startswith("hag_")
    assert payload["token_shown_once"] is True
    assert payload["session"]["id"] == issued["session"]["id"]
    assert payload["session"]["connections"][0]["id"] == connection["id"]
    assert "upstream-secret" not in response.text

    second = await agent_client.post("/agent/api/redeem", json={"code": code})
    assert second.status_code == 422
    assert "token" not in second.json()

    health = await agent_client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
    assert health.status_code == 200


async def test_redeem_is_single_use_under_concurrency(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    code = _code(issued["handoff"])
    responses = await asyncio.gather(
        *(agent_client.post("/agent/api/redeem", json={"code": code}) for _ in range(4))
    )
    assert sum(1 for item in responses if item.status_code == 200) == 1


async def test_redeem_rejects_bad_codes(agent_client: httpx.AsyncClient) -> None:
    for code in ["hag_h_" + "x" * 43, "not-a-code", "hag_" + "a" * 43]:
        response = await agent_client.post("/agent/api/redeem", json={"code": code})
        assert response.status_code in {422, 400}


async def test_agent_page_is_unauthenticated_but_hardened(agent_client: httpx.AsyncClient) -> None:
    response = await agent_client.get("/agent")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"].startswith("no-store")
    assert "/agent/agent.js" in response.text
    assert "<script>" not in response.text

    for asset in ("/agent/agent.js", "/agent/styles.css"):
        asset_response = await agent_client.get(asset)
        assert asset_response.status_code == 200
        assert asset_response.headers["cache-control"].startswith("no-store")

    status = await agent_client.get("/agent/api/session")
    assert status.status_code == 401


async def test_agent_page_redeem_is_same_origin_only(agent_client: httpx.AsyncClient) -> None:
    response = await agent_client.post(
        "/agent/api/redeem",
        json={"code": "hag_h_" + "a" * 43},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
    response = await agent_client.post(
        "/agent/api/redeem",
        content=json.dumps({"code": "hag_h_" + "a" * 43}),
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    response = await agent_client.get("/agent/api/redeem")
    assert response.status_code == 405


# --------------------------------------------------------------- enforcement


async def test_session_is_confined_to_authorized_connection(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    allowed, other = await _connections(runtime, admin_unlocked, count=2)
    issued = await _issue(admin_unlocked, connection_ids=[allowed["id"]])
    token = (await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})).json()[
        "token"
    ]
    headers = {"Authorization": f"Bearer {token}"}

    denied = await agent_client.post(
        "/api/v1/operations",
        headers=headers,
        json={"tool": "ha.get_state", "connection_id": other["id"], "params": {"entity_id": "light.kitchen"}},
    )
    assert denied.status_code == 403
    assert "not authorized" in denied.text

    permitted = await agent_client.post(
        "/api/v1/operations",
        headers=headers,
        json={
            "tool": "ha.get_state",
            "connection_id": allowed["id"],
            "params": {"entity_id": "light.kitchen"},
        },
    )
    # Reaches the upstream client (which fails: no live Home Assistant here),
    # which proves authorization passed.
    assert permitted.status_code != 403


async def test_read_only_session_cannot_call_write_tool(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    read_only = await _issue(
        admin_unlocked,
        name="read-only",
        connection_ids=[connection["id"]],
        scopes=["ha:read"],
    )
    token = (await agent_client.post("/agent/api/redeem", json={"code": _code(read_only["handoff"])})).json()[
        "token"
    ]
    response = await agent_client.post(
        "/api/v1/operations",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "tool": "ha.call_service",
            "connection_id": connection["id"],
            "params": {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"},
        },
    )
    assert response.status_code == 403
    assert "ha:write" in response.text


def test_operation_class_gate_is_defence_in_depth(runtime: Runtime) -> None:
    from gateway.auth.agents import AgentPrincipal

    spec = runtime.tools.get_spec("ha.call_service")
    read_only = AgentPrincipal(
        id="session",
        name="session",
        scopes=frozenset({"ha:write"}),
        kind="temporary",
        operation_classes=frozenset({"read"}),
        allowed_connections=frozenset({"connection"}),
    )
    assert runtime.operations._session_constraint(read_only, spec, "connection") == (
        "this session does not allow write operations"
    )
    wrong_connection = AgentPrincipal(
        id="session",
        name="session",
        scopes=frozenset({"ha:read"}),
        kind="temporary",
        operation_classes=frozenset({"read"}),
        allowed_connections=frozenset({"other"}),
    )
    read_spec = runtime.tools.get_spec("ha.get_state")
    assert runtime.operations._session_constraint(wrong_connection, read_spec, "connection") == (
        "this session is not authorized for that connection"
    )


async def test_write_session_policy_requires_approval(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(
        admin_unlocked,
        connection_ids=[connection["id"]],
        scopes=["ha:read", "ha:write"],
        operation_classes=["read", "write"],
    )
    write_rules = [rule for rule in issued["policy"]["document"]["rules"] if rule["require_approval"]]
    assert write_rules
    assert all("ha.call_service" in rule["tools"] for rule in write_rules)


# --------------------------------------------------------------- revocation


async def test_revoke_invalidates_the_token_immediately(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    token = (await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})).json()[
        "token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await agent_client.get("/api/v1/health", headers=headers)).status_code == 200

    revoked = await admin_unlocked.post(
        f"/api/admin/agent-access/sessions/{issued['session']['id']}/revoke",
        json={"reason": "test"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["session"]["status"] == "revoked"
    assert (await agent_client.get("/api/v1/health", headers=headers)).status_code == 401

    again = await admin_unlocked.post(f"/api/admin/agent-access/sessions/{issued['session']['id']}/revoke")
    assert again.status_code == 200


async def test_revoke_all_requires_confirmation_and_clears_every_session(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    await _issue(admin_unlocked, name="one", connection_ids=[connection["id"]], revoke_previous=False)
    await _issue(admin_unlocked, name="two", connection_ids=[connection["id"]], revoke_previous=False)

    rejected = await admin_unlocked.post(
        "/api/admin/agent-access/revoke-all", json={"confirm": "yes", "reason": "test"}
    )
    assert rejected.status_code == 422

    result = await admin_unlocked.post(
        "/api/admin/agent-access/revoke-all", json={"confirm": "REVOKE ALL", "reason": "test"}
    )
    assert result.status_code == 200
    assert result.json()["revoked"] == 2
    listing = await admin_unlocked.get("/api/admin/agent-access")
    assert all(session["status"] == "revoked" for session in listing.json()["sessions"])


async def test_new_session_revokes_previous_by_default(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    first = await _issue(admin_unlocked, name="first", connection_ids=[connection["id"]])
    second = await _issue(admin_unlocked, name="second", connection_ids=[connection["id"]])
    assert second["session"]["id"] != first["session"]["id"]
    detail = await admin_unlocked.get(f"/api/admin/agent-access/sessions/{first['session']['id']}")
    assert detail.json()["session"]["status"] == "revoked"
    # The superseded handoff link cannot be redeemed any more.
    pending = await admin_unlocked.get(f"/api/admin/agent-access/sessions/{first['session']['id']}")
    assert pending.json()["session"]["handoff_pending"] is False


# ------------------------------------------------------------ restart policy


def _restart(settings: Settings) -> tuple[Runtime, httpx.AsyncClient]:
    runtime = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    runtime.startup()
    return runtime, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_agent_app(runtime)), base_url="http://gateway.test"
    )


async def test_restart_revokes_temporary_sessions_and_keeps_upstream_credentials(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    token = (await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})).json()[
        "token"
    ]
    runtime.settings_store.set("agent_sessions_revoke_on_restart", True)

    restarted, client = _restart(runtime.settings)
    try:
        assert restarted.startup_session_policy is not None
        assert restarted.startup_session_policy["revoked"] >= 1
        response = await client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert restarted.agents.current_session_epoch() >= 1
        # Upstream credentials survive and still decrypt; only the temporary
        # agent credential was revoked.  The value remains inside the vault.
        assert len(restarted.connections.list_connections()) == 1
        credential_id = restarted.connections.get_connection(connection["id"]).credential_id
        assert credential_id is not None
        restarted.vault.unlock(PASSWORD)
        assert restarted.vault.get_secret(credential_id).value["token"] == "upstream-secret-0"
    finally:
        await client.aclose()
        restarted.shutdown()


async def test_restart_policy_retain_keeps_the_session(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    runtime.settings_store.set("agent_sessions_revoke_on_restart", False)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    token = (await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})).json()[
        "token"
    ]

    restarted, client = _restart(runtime.settings)
    try:
        response = await client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
    finally:
        await client.aclose()
        restarted.shutdown()


async def test_stale_epoch_rejects_an_unrevoked_token(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    token = (await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})).json()[
        "token"
    ]
    runtime.agents.bump_session_epoch()
    response = await agent_client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_expired_session_is_rejected(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    token = (await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})).json()[
        "token"
    ]
    runtime.db.execute(
        "UPDATE agents SET expires_at='2020-01-01T00:00:00Z' WHERE id=?", (issued["session"]["id"],)
    )
    response = await agent_client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# ----------------------------------------------------------------- settings


async def test_settings_validate_and_apply(runtime: Runtime, admin_unlocked: httpx.AsyncClient) -> None:
    good = await admin_unlocked.put(
        "/api/admin/agent-access/settings",
        json={
            "default_ttl_seconds": 1800,
            "max_ttl_seconds": 7200,
            "revoke_on_restart": False,
            "agent_api_base_url": "https://gateway.example:8098",
        },
    )
    assert good.status_code == 200, good.text
    settings = good.json()
    assert settings["default_ttl_seconds"] == 1800
    assert settings["max_ttl_seconds"] == 7200
    assert settings["revoke_on_restart"] is False
    assert settings["agent_api_base_url"] == "https://gateway.example:8098"

    bad = await admin_unlocked.put(
        "/api/admin/agent-access/settings", json={"agent_api_base_url": "https://user:pw@x.test/path"}
    )
    assert bad.status_code == 422
    bad_ttl = await admin_unlocked.put("/api/admin/agent-access/settings", json={"max_ttl_seconds": 10})
    assert bad_ttl.status_code == 422


async def test_handoff_uses_configured_base_url(runtime: Runtime, admin_unlocked: httpx.AsyncClient) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    await admin_unlocked.put(
        "/api/admin/agent-access/settings",
        json={"agent_api_base_url": "http://homeassistant.local:8098"},
    )
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    assert issued["handoff"]["url"].startswith("http://homeassistant.local:8098/agent#code=")


# ------------------------------------------------------------------- audits


async def test_issuance_and_revocation_are_audited(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    await admin_unlocked.post(f"/api/admin/agent-access/sessions/{issued['session']['id']}/revoke")
    rows = runtime.db.query_all("SELECT tool FROM audit WHERE tool LIKE 'agent_session.%' ORDER BY id")
    actions = [row["tool"] for row in rows]
    assert "agent_session.create" in actions
    assert "agent_session.revoke" in actions


async def test_secret_material_never_appears_in_broker_responses(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
    redeem = await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})
    overview = await admin_unlocked.get("/api/admin/agent-access")
    for payload in (issued, redeem.json(), overview.json()):
        text = json.dumps(payload)
        assert "upstream-secret" not in text
    assert runtime.vault.exists


def test_session_handoffs_store_only_digests(runtime: Runtime, admin_unlocked: httpx.AsyncClient) -> None:
    # Synchronous wrapper kept simple: use the service directly.
    async def scenario() -> None:
        [connection] = await _connections(runtime, admin_unlocked)
        issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
        rows = runtime.db.query_all("SELECT code_hash FROM session_handoffs")
        assert rows
        code = _code(issued["handoff"])
        assert all(row["code_hash"] != code for row in rows)
        assert all(len(str(row["code_hash"])) == 64 for row in rows)

    asyncio.get_event_loop().run_until_complete(scenario())


def test_database_schema_is_v4(runtime: Runtime) -> None:
    info = runtime.db.schema_info()
    assert info["version"] == 4
    columns = {row["name"] for row in runtime.db.query_all("PRAGMA table_info(agents)")}
    assert {
        "kind",
        "allowed_connections",
        "operation_classes",
        "revoked_at",
        "revoked_reason",
        "created_by",
        "session_epoch",
        "issued_ttl_seconds",
    } <= columns


def test_no_plaintext_token_is_persisted(runtime: Runtime, admin_unlocked: httpx.AsyncClient) -> None:
    async def scenario() -> None:
        [connection] = await _connections(runtime, admin_unlocked)
        issued = await _issue(admin_unlocked, connection_ids=[connection["id"]])
        row = runtime.db.query_one(
            "SELECT token_hash, token_prefix FROM agents WHERE id=?", (issued["session"]["id"],)
        )
        assert row is not None
        assert row["token_prefix"] == "pending"
        assert len(str(row["token_hash"])) == 64

    asyncio.get_event_loop().run_until_complete(scenario())


@pytest.mark.parametrize("path", ["/agent", "/agent/", "/agent/agent.js", "/agent/styles.css"])
def test_agent_page_assets_exist_inside_the_package(path: str) -> None:
    from gateway.api.agent_page import AGENT_DIR

    name = "index.html" if path in {"/agent", "/agent/"} else path.rsplit("/", 1)[1]
    assert (AGENT_DIR / name).is_file()


async def test_issue_rejects_global_audit_scope_for_temporary_sessions(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient
) -> None:
    [connection] = await _connections(runtime, admin_unlocked)
    response = await admin_unlocked.post(
        "/api/admin/agent-access/sessions",
        json={
            "name": "audit-session",
            "scopes": ["ha:read", "audit:read"],
            "connection_ids": [connection["id"]],
            "operation_classes": ["read"],
        },
    )
    assert response.status_code == 422, response.text


async def test_session_catalog_hides_unauthorized_connections(
    runtime: Runtime, admin_unlocked: httpx.AsyncClient, agent_client: httpx.AsyncClient
) -> None:
    allowed, other = await _connections(runtime, admin_unlocked, count=2)
    issued = await _issue(admin_unlocked, connection_ids=[allowed["id"]])
    redeemed = await agent_client.post("/agent/api/redeem", json={"code": _code(issued["handoff"])})
    token = redeemed.json()["token"]

    response = await agent_client.get("/api/v1/connections", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {allowed["id"]}
    assert other["name"] not in response.text
