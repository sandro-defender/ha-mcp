"""Middleware behaviour: management gate, CSRF/origin checks, body limits,
agent authentication throttling, and response headers.

These tests drive the real ASGI apps with an explicit client address so the
peer-IP check is exercised rather than bypassed by the test transport.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from helpers import ADMIN_HEADERS, PASSWORD, auth_headers

from gateway.api.apps import build_admin_app, build_agent_app
from gateway.runtime import Runtime
from gateway.settings import Settings


def admin_http(
    runtime: Runtime,
    *,
    peer: tuple[str, int] = ("127.0.0.1", 1),
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_admin_app(runtime), client=peer),
        base_url="http://gateway.test",
        headers=ADMIN_HEADERS if headers is None else headers,
    )


def agent_http(
    runtime: Runtime,
    *,
    peer: tuple[str, int] = ("127.0.0.1", 1),
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_agent_app(runtime), client=peer),
        base_url="http://gateway.test",
        headers=headers or {},
    )


# --------------------------------------------------------------- admin gate


async def test_management_api_rejects_untrusted_peers(runtime: Runtime) -> None:
    async with admin_http(runtime, peer=("192.0.2.5", 40000)) as client:
        response = await client.get("/api/admin/status")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_healthz_is_reachable_without_the_supervisor(runtime: Runtime) -> None:
    async with admin_http(runtime, peer=("192.0.2.5", 40000), headers={}) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_management_api_requires_the_ingress_user_header(runtime: Runtime) -> None:
    async with admin_http(runtime, headers={}) as client:
        response = await client.get("/api/admin/status")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ingress_required"


async def test_management_api_rejects_malformed_user_header(runtime: Runtime) -> None:
    async with admin_http(runtime, headers={"X-Remote-User-Id": "../../etc/passwd"}) as client:
        response = await client.get("/api/admin/status")
    assert response.status_code == 401


async def test_admin_allowlist_rejects_other_home_assistant_users(settings: Settings) -> None:
    restricted = replace(settings, admin_user_ids=frozenset({"allowed-user"}))
    runtime = Runtime(restricted, kdf_memory_cost=1024, kdf_time_cost=1)
    runtime.startup()
    try:
        async with admin_http(runtime, headers={"X-Remote-User-Id": "someone-else"}) as client:
            denied = await client.get("/api/admin/status")
        async with admin_http(runtime, headers={"X-Remote-User-Id": "allowed-user"}) as client:
            allowed = await client.get("/api/admin/status")
    finally:
        runtime.shutdown()
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert allowed.status_code == 200


async def test_mutations_require_the_gateway_request_header(runtime: Runtime) -> None:
    async with admin_http(runtime, headers={"X-Remote-User-Id": "admin-user"}) as client:
        missing = await client.post("/api/admin/vault/initialize", json={"password": PASSWORD})
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == "csrf_rejected"

        # Reads do not need the header.
        read = await client.get("/api/admin/status")
        assert read.status_code == 200


async def test_cross_site_fetch_metadata_is_rejected(runtime: Runtime) -> None:
    headers = {**ADMIN_HEADERS, "Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
    async with admin_http(runtime, headers=headers) as client:
        response = await client.post("/api/admin/vault/lock", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_rejected"


async def test_origin_must_match_the_request_host(runtime: Runtime) -> None:
    mismatched = {**ADMIN_HEADERS, "Origin": "https://evil.example"}
    async with admin_http(runtime, headers=mismatched) as client:
        rejected = await client.post("/api/admin/vault/lock", json={})
    assert rejected.status_code == 403

    same_origin = {**ADMIN_HEADERS, "Origin": "http://gateway.test", "Sec-Fetch-Site": "same-origin"}
    async with admin_http(runtime, headers=same_origin) as client:
        accepted = await client.post("/api/admin/vault/lock", json={})
    assert accepted.status_code == 200


async def test_oversized_management_body_is_rejected(runtime: Runtime) -> None:
    limit = runtime.settings.max_json_body_bytes
    async with admin_http(runtime) as client:
        response = await client.post(
            "/api/admin/vault/initialize",
            content=b"{" + b"a" * (limit + 1024) + b"}",
            headers={**ADMIN_HEADERS, "Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


async def test_management_responses_carry_security_headers(runtime: Runtime) -> None:
    async with admin_http(runtime) as client:
        api = await client.get("/api/admin/status")
        page = await client.get("/")
        health = await client.get("/healthz")
    for response in (api, page, health):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert response.headers["x-request-id"]
    assert api.headers.get_list("cache-control") == ["no-store"]
    assert "cache-control" not in health.headers


async def test_panel_is_served_under_a_strict_content_security_policy(runtime: Runtime) -> None:
    async with admin_http(runtime) as client:
        page = await client.get("/")
        script = await client.get("/static/app.js")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


async def test_unknown_hosts_are_rejected_when_an_allowlist_is_configured(settings: Settings) -> None:
    restricted = replace(settings, agent_allowed_hosts=frozenset({"gateway.test"}))
    runtime = Runtime(restricted, kdf_memory_cost=1024, kdf_time_cost=1)
    runtime.startup()
    try:
        _agent, token = await create_agent_for(runtime)
        async with agent_http(runtime, headers=auth_headers(token)) as client:
            allowed = await client.get("/api/v1/health")
            rejected = await client.get("/api/v1/health", headers={"Host": "attacker.example"})
    finally:
        runtime.shutdown()
    assert allowed.status_code == 200
    assert rejected.status_code == 421
    assert rejected.json()["error"]["code"] == "host_rejected"


async def create_agent_for(runtime: Runtime) -> tuple[dict[str, object], str]:
    """Create one agent directly through the service (no HTTP needed)."""

    agent, token = runtime.agents.create_agent(name="mw-agent", scopes=["ha:read"], expires_at=None, notes="")
    runtime.policies.set_policy(agent["id"], {"version": 1, "rules": []})
    return agent, token


# -------------------------------------------------------------- agent gate


async def test_duplicate_authorization_headers_are_rejected(runtime: Runtime) -> None:
    async with agent_http(runtime) as client:
        response = await client.get(
            "/api/v1/health",
            headers=[("Authorization", "Bearer one"), ("Authorization", "Bearer two")],
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_repeated_auth_failures_are_throttled(runtime: Runtime) -> None:
    statuses: list[int] = []
    async with agent_http(runtime, peer=("198.51.100.7", 1234)) as client:
        for _ in range(11):
            response = await client.get("/api/v1/health", headers=auth_headers("hag_invalid"))
            statuses.append(response.status_code)
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429
    assert response.headers["retry-after"].isdigit()


async def test_throttling_is_per_peer(runtime: Runtime) -> None:
    async with agent_http(runtime, peer=("198.51.100.8", 1234)) as noisy:
        for _ in range(11):
            await noisy.get("/api/v1/health", headers=auth_headers("hag_invalid"))
    async with agent_http(runtime, peer=("198.51.100.9", 1234)) as quiet:
        response = await quiet.get("/api/v1/health")
    assert response.status_code == 401  # still the plain auth error, not 429


async def test_oversized_mcp_body_is_rejected(runtime: Runtime) -> None:
    async with agent_http(runtime) as client:
        response = await client.post(
            "/mcp",
            content=b"{" + b"a" * (runtime.settings.mcp_max_request_bytes + 1024) + b"}",
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413


async def test_agent_responses_are_not_cached(runtime: Runtime) -> None:
    async with agent_http(runtime) as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 401
        assert response.headers.get_list("cache-control") == ["no-store"]
        assert response.headers.get_list("x-content-type-options") == ["nosniff"]
        assert response.headers["x-request-id"]


@pytest.mark.parametrize("path", ["/api/v1/health", "/mcp", "/mcp/"])
async def test_agent_paths_require_a_token(runtime: Runtime, path: str) -> None:
    async with agent_http(runtime) as client:
        response = await client.post(path, json={}) if path.startswith("/mcp") else await client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
