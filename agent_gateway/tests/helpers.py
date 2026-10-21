"""Constants and builders shared by the test modules."""

from __future__ import annotations

from typing import Any

import httpx

PASSWORD = "correct horse battery staple"
ADMIN_USER = "admin-user"
ADMIN_HEADERS = {"X-Remote-User-Id": ADMIN_USER, "X-Gateway-Request": "1"}
AGENT_TOKEN = "test-agent-token"


async def initialize_vault(client: httpx.AsyncClient, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post("/api/admin/vault/initialize", json={"password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def create_credential(
    client: httpx.AsyncClient,
    *,
    name: str = "ha token",
    kind: str = "home_assistant_token",
    value: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/api/admin/credentials",
        json={"name": name, "kind": kind, "value": value or {"token": "super-secret-token-123"}},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def create_connection(
    client: httpx.AsyncClient,
    *,
    name: str = "home",
    connection_type: str = "home_assistant",
    config: dict[str, Any] | None = None,
    credential_id: str,
) -> dict[str, Any]:
    response = await client.post(
        "/api/admin/connections",
        json={
            "name": name,
            "type": connection_type,
            "config": config or {"base_url": "http://ha.test:8123"},
            "credential_id": credential_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def create_agent(
    client: httpx.AsyncClient,
    *,
    name: str = "test-agent",
    scopes: list[str] | None = None,
    policy: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    response = await client.post(
        "/api/admin/agents",
        json={
            "name": name,
            "scopes": scopes or ["ha:read", "ha:write"],
            "policy": policy if policy is not None else {"version": 1, "rules": []},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["agent"], payload["token"]


def read_policy(
    *,
    tools: list[str] | None = None,
    connections: list[str] | None = None,
    constraints: dict[str, list[str]] | None = None,
    effect: str = "allow",
    rule_id: str = "allow-read",
) -> dict[str, Any]:
    return {
        "version": 1,
        "rules": [
            {
                "id": rule_id,
                "effect": effect,
                "tools": tools or ["ha.*"],
                "connections": connections or ["*"],
                "constraints": constraints or {},
            }
        ],
    }


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
