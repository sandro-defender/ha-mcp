"""Backend-enforced IP allowlist/denylist, source resolution, attempt history.

Covers rule semantics (deny beats allow, CIDR v4/v6, IPv4-mapped normalisation),
middleware enforcement before authentication and on alternate routes (MCP),
trusted-proxy X-Forwarded-For handling including spoofing attempts, rate-limited
denials, connection-attempt history (sanitisation, pagination, bounded
retention), automatic temporary-session revocation on deny, and the management
gate's own denial history.  No real network egress is involved: the ASGI
transport sets the socket peer.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from helpers import ADMIN_HEADERS, auth_headers, create_agent

from gateway.api.apps import build_admin_app, build_agent_app
from gateway.errors import ConfigError, ConflictError, NotFoundError, ValidationFailure
from gateway.ipaccess import (
    IP_ATTEMPTS_MAX_ROWS,
    normalize_ip,
    normalize_network,
)
from gateway.runtime import Runtime
from gateway.settings import load_settings

# ------------------------------------------------------------------ helpers


def admin_client(runtime: Runtime, *, client: tuple[str, int] = ("127.0.0.1", 123)) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=build_admin_app(runtime), client=client)
    return httpx.AsyncClient(transport=transport, base_url="http://gateway.test", headers=ADMIN_HEADERS)


def agent_client(
    runtime: Runtime,
    *,
    client: tuple[str, int] = ("127.0.0.1", 123),
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=build_agent_app(runtime), client=client)
    return httpx.AsyncClient(transport=transport, base_url="http://gateway.test", headers=headers or {})


# ----------------------------------------------------------- rule semantics


def test_ip_normalization_and_parsing() -> None:
    assert normalize_ip(" 10.0.0.5 ") == "10.0.0.5"
    assert normalize_ip("::ffff:10.0.0.5") == "10.0.0.5"
    assert normalize_ip("not-an-ip") is None
    assert normalize_ip("fe80::1%eth0") is None
    assert normalize_network("10.0.0.5") == "10.0.0.5/32"
    assert normalize_network("::ffff:10.0.0.5/120") == "10.0.0.0/24"
    with pytest.raises(ValidationFailure):
        normalize_network("definitely-not-a-cidr")
    with pytest.raises(ValidationFailure):
        normalize_network("")


def test_deny_beats_broader_allow(runtime: Runtime) -> None:
    ip = runtime.ip_access
    ip.create_rule(action="allow", cidr="0.0.0.0/0", created_by="admin", revoke_sessions=False)
    ip.create_rule(action="deny", cidr="10.0.0.5", created_by="admin", revoke_sessions=False)

    denied = ip.evaluate("10.0.0.5")
    assert denied.decision == "denied"
    assert denied.rule_cidr == "10.0.0.5/32"

    allowed = ip.evaluate("10.0.0.6")
    assert allowed.decision == "allowed"
    assert allowed.rule_cidr == "0.0.0.0/0"

    # IPv4-mapped form hits the same deny rule.
    assert ip.evaluate("::ffff:10.0.0.5").decision == "denied"


def test_ipv6_rules_and_mapped_addresses(runtime: Runtime) -> None:
    ip = runtime.ip_access
    ip.create_rule(action="deny", cidr="2001:db8::/32", created_by="admin", revoke_sessions=False)
    assert ip.evaluate("2001:db8::9").decision == "denied"
    assert ip.evaluate("2001:db9::9").decision == "allowed"


def test_allowlist_mode_requires_matching_allow(runtime: Runtime) -> None:
    ip = runtime.ip_access
    # Default: no rules, mode off → allowed (pre-feature behaviour).
    assert ip.evaluate("203.0.113.7").decision == "allowed"

    ip.set_allowlist_mode(True, actor_id="admin")
    assert ip.allowlist_mode is True
    assert ip.evaluate("203.0.113.7").decision == "denied"

    ip.create_rule(action="allow", cidr="203.0.113.0/24", created_by="admin", revoke_sessions=False)
    evaluation = ip.evaluate("203.0.113.7")
    assert evaluation.decision == "allowed"
    assert evaluation.rule_cidr == "203.0.113.0/24"

    # Deny still wins inside an allowed range.
    ip.create_rule(action="deny", cidr="203.0.113.9", created_by="admin", revoke_sessions=False)
    assert ip.evaluate("203.0.113.9").decision == "denied"

    ip.set_allowlist_mode(False, actor_id="admin")
    assert ip.evaluate("198.51.100.1").decision == "allowed"


def test_unknown_evaluation_source_is_denied(runtime: Runtime) -> None:
    # Cannot be identified → fail closed rather than pass.
    assert runtime.ip_access.evaluate("garbage-address").decision == "denied"


# ----------------------------------------------------------- admin API CRUD


async def test_rule_validation_conflicts_and_crud(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        bad = await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "banana"})
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "validation_error"

        first = await admin.post(
            "/api/admin/ip/rules", json={"action": "deny", "cidr": "192.0.2.10", "note": "scanner"}
        )
        assert first.status_code == 201, first.text
        rule_id = first.json()["rule"]["id"]
        assert first.json()["rule"]["cidr"] == "192.0.2.10/32"

        duplicate = await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "192.0.2.10"})
        assert duplicate.status_code == 409

        listing = await admin.get("/api/admin/ip/rules")
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["allowlist_mode"] is False
        assert any(rule["id"] == rule_id for rule in payload["rules"])
        assert payload["trusted_proxies"] == []

        patched = await admin.patch(f"/api/admin/ip/rules/{rule_id}", json={"note": "updated"})
        assert patched.status_code == 200
        assert patched.json()["rule"]["note"] == "updated"

        toggled = await admin.put("/api/admin/ip/settings", json={"allowlist_mode": True})
        assert toggled.status_code == 200
        assert toggled.json()["allowlist_mode"] is True
        assert (await admin.get("/api/admin/ip/rules")).json()["allowlist_mode"] is True

        missing = await admin.patch("/api/admin/ip/rules/does-not-exist", json={"note": "x"})
        assert missing.status_code == 404

        deleted = await admin.delete(f"/api/admin/ip/rules/{rule_id}")
        assert deleted.status_code == 200
        assert not any(
            rule["id"] == rule_id for rule in (await admin.get("/api/admin/ip/rules")).json()["rules"]
        )

        empty_patch = await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "192.0.2.11"})
        no_change = await admin.patch(f"/api/admin/ip/rules/{empty_patch.json()['rule']['id']}", json={})
        assert no_change.status_code == 422


async def test_ip_admin_requires_ingress_headers(runtime: Runtime) -> None:
    transport = httpx.ASGITransport(app=build_admin_app(runtime), client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as anon:
        response = await anon.get("/api/admin/ip/rules")
        assert response.status_code == 401


async def test_ip_routes_absent_on_agent_port(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        _agent, token = await create_agent(admin)
    async with agent_client(runtime) as agent:
        response = await agent.get("/api/admin/ip/rules", headers=auth_headers(token))
    # The route does not exist on the agent listener at all.
    assert response.status_code == 404


async def test_management_listener_not_subject_to_ip_rules(runtime: Runtime) -> None:
    # An administrator cannot lock the panel out with IP rules: the
    # management listener keeps its own Supervisor-peer gate.
    async with admin_client(runtime) as admin:
        denied = await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "127.0.0.1"})
        assert denied.status_code == 201
        still_ok = await admin.get("/api/admin/ip/rules")
        assert still_ok.status_code == 200


# ------------------------------------------------------ enforcement surface


async def test_denied_source_blocked_before_authentication(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        _agent, token = await create_agent(admin)
        assert (
            await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "127.0.0.1"})
        ).status_code == 201

    async with agent_client(runtime) as agent:
        unauthenticated = await agent.get("/api/v1/health")
        authenticated = await agent.get("/api/v1/health", headers=auth_headers(token))

    # Network eligibility is evaluated before credentials: 403 ip_denied for
    # both, never a 401/200.
    for response in (unauthenticated, authenticated):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "ip_denied"

    attempts = runtime.ip_access.list_attempts(listener="agent")
    denied_rows = [row for row in attempts["items"] if row["decision"] == "denied"]
    assert denied_rows
    assert all(row["ip"] == "127.0.0.1" for row in denied_rows)
    assert {row["category"] for row in denied_rows} <= {"agent_api", "agent_auth"}


async def test_mcp_and_page_routes_denied_and_recorded(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        assert (
            await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "127.0.0.1"})
        ).status_code == 201

    async with agent_client(runtime) as agent:
        mcp = await agent.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        redeem = await agent.post("/agent/api/redeem", json={"code": "nope"})

    # Alternate routes are covered even where later auth/app logic would
    # also have failed.
    assert mcp.status_code == 403
    assert mcp.json()["error"]["code"] == "ip_denied"
    assert redeem.status_code == 403

    categories = {row["category"] for row in runtime.ip_access.list_attempts(listener="agent")["items"]}
    assert "mcp" in categories
    assert "handoff_redeem" in categories


async def test_health_and_readiness_exempt_from_ip_policy(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        assert (
            await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "127.0.0.1"})
        ).status_code == 201
    async with agent_client(runtime) as agent:
        health = await agent.get("/healthz")
        ready = await agent.get("/readyz")
    assert health.status_code == 200
    assert ready.status_code in (200, 503)  # lifecycle/readiness state, never 403


async def test_allowlisted_ip_still_requires_valid_credentials(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        _agent, token = await create_agent(admin)
        assert (await admin.put("/api/admin/ip/settings", json={"allowlist_mode": True})).status_code == 200
        assert (
            await admin.post("/api/admin/ip/rules", json={"action": "allow", "cidr": "127.0.0.1"})
        ).status_code == 201

    async with agent_client(runtime) as agent:
        without_token = await agent.get("/api/v1/health")
        with_token = await agent.get("/api/v1/health", headers=auth_headers(token))

    # Network eligibility ≠ identity: the allowed source still needs a
    # valid gateway credential.
    assert without_token.status_code == 401
    assert without_token.json()["error"]["code"] == "unauthenticated"
    assert with_token.status_code == 200


async def test_denied_attempts_rate_limited_to_429(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        assert (
            await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "127.0.0.1"})
        ).status_code == 201

    statuses: list[int] = []
    async with agent_client(runtime) as agent:
        for _ in range(21):
            response = await agent.get("/api/v1/health")
            statuses.append(response.status_code)
            if response.status_code == 429:
                assert "retry-after" in response.headers
                assert response.json()["error"]["code"] == "rate_limited"

    assert statuses.count(403) == 20
    assert statuses[-1] == 429

    decisions = [row["decision"] for row in runtime.ip_access.list_attempts(listener="agent")["items"]]
    assert "rate_limited" in decisions


# ------------------------------------------------- source address resolution


def test_spoofed_forwarded_header_ignored_from_untrusted_peer(runtime: Runtime) -> None:
    ip = runtime.ip_access
    # No trusted proxies configured: header claims never matter.
    assert ip.resolve_source_ip("10.9.9.9", ["8.8.8.8"]) == "10.9.9.9"
    assert ip.resolve_source_ip("10.9.9.9", []) == "10.9.9.9"
    assert ip.resolve_source_ip(None, ["8.8.8.8"]) == "unknown"


async def test_untrusted_peer_cannot_spoof_its_way_past_deny(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        # Allowlist mode on; only the spoofed address has an allow rule.
        await admin.put("/api/admin/ip/settings", json={"allowlist_mode": True})
        await admin.post("/api/admin/ip/rules", json={"action": "allow", "cidr": "8.8.8.8"})

    async with agent_client(
        runtime,
        client=("10.9.9.9", 5555),
        headers={"X-Forwarded-For": "8.8.8.8"},
    ) as agent:
        response = await agent.get("/api/v1/health")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ip_denied"
    row = runtime.ip_access.list_attempts(listener="agent")["items"][0]
    assert row["ip"] == "10.9.9.9"  # socket peer, not the spoofed header


def test_trusted_proxy_chain_parsing(tmp_path: Path) -> None:
    from gateway.settings import Settings

    settings = Settings(
        data_dir=tmp_path,
        admin_allowed_ips=frozenset({"127.0.0.1"}),
        trusted_proxies=("192.168.0.0/16", "172.18.0.0/16"),
    )
    runtime = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    runtime.startup()
    try:
        ip = runtime.ip_access
        # Peer outside trusted proxies → header ignored.
        assert ip.resolve_source_ip("203.0.113.4", ["8.8.8.8"]) == "203.0.113.4"
        # Trusted peer: right-most untrusted hop is the client.
        assert ip.resolve_source_ip("172.18.0.3", ["8.8.8.8, 192.168.1.5"]) == "8.8.8.8"
        # Chain of only trusted hops → left-most entry is the client.
        assert ip.resolve_source_ip("172.18.0.3", ["192.168.1.5, 172.18.0.3"]) == "192.168.1.5"
        # Multiple header lines concatenate left to right.
        assert ip.resolve_source_ip("172.18.0.3", ["8.8.8.8", "192.168.1.5"]) == "8.8.8.8"
        # Malformed chain → fail safe to the socket peer.
        assert ip.resolve_source_ip("172.18.0.3", ["garbage", "8.8.8.8"]) == "172.18.0.3"
        assert ip.resolve_source_ip("172.18.0.3", []) == "172.18.0.3"
        # Header order with spaces / casing normalises.
        assert ip.resolve_source_ip("172.18.0.3", [" 8.8.8.8 , 192.168.9.9 "]) == "8.8.8.8"
    finally:
        runtime.shutdown()


async def test_trusted_proxy_forwarded_client_is_enforced(tmp_path: Path) -> None:
    from gateway.settings import Settings

    settings = Settings(
        data_dir=tmp_path,
        admin_allowed_ips=frozenset({"127.0.0.1"}),
        trusted_proxies=("172.18.0.0/16",),
    )
    runtime = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    runtime.startup()
    try:
        async with admin_client(runtime) as admin:
            assert (
                await admin.post("/api/admin/ip/rules", json={"action": "deny", "cidr": "203.0.113.5"})
            ).status_code == 201

        # Requests arriving via the trusted proxy frontend: the forwarded
        # client is what gets evaluated.
        async with agent_client(
            runtime,
            client=("172.18.0.9", 4444),
            headers={"X-Forwarded-For": "203.0.113.5, 172.18.0.9"},
        ) as agent:
            via_proxy = await agent.get("/api/v1/health")
        async with agent_client(
            runtime,
            client=("172.18.0.9", 4444),
            headers={"X-Forwarded-For": "198.51.100.9, 172.18.0.9"},
        ) as agent:
            other_client = await agent.get("/api/v1/health")

        assert via_proxy.status_code == 403
        assert other_client.status_code == 401  # IP allowed → auth decides
        denied_rows = [
            row
            for row in runtime.ip_access.list_attempts(listener="agent")["items"]
            if row["ip"] == "203.0.113.5"
        ]
        assert denied_rows and denied_rows[0]["decision"] == "denied"
    finally:
        runtime.shutdown()


# ------------------------------------------------------- attempt history


async def test_management_denied_attempts_recorded(runtime: Runtime) -> None:
    transport = httpx.ASGITransport(app=build_admin_app(runtime), client=("10.9.9.9", 12345))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway.test", headers=ADMIN_HEADERS
    ) as stranger:
        response = await stranger.get("/api/admin/ip/rules")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"

    rows = runtime.ip_access.list_attempts(listener="management")["items"]
    assert rows
    assert rows[0]["ip"] == "10.9.9.9"
    assert rows[0]["decision"] == "denied"
    assert rows[0]["reason"] == "peer not in admin_allowed_ips"


async def test_authenticated_agent_attempt_carries_identity(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        agent_record, token = await create_agent(admin)
    async with agent_client(runtime) as agent:
        response = await agent.get("/api/v1/health", headers=auth_headers(token))
    assert response.status_code == 200

    allowed_rows = [
        row
        for row in runtime.ip_access.list_attempts(listener="agent")["items"]
        if row["decision"] == "allowed"
    ]
    assert allowed_rows
    assert allowed_rows[0]["agent_id"] == agent_record["id"]
    assert allowed_rows[0]["category"] == "agent_api"


async def test_auth_failure_recorded_as_denied_attempt(runtime: Runtime) -> None:
    async with agent_client(runtime) as agent:
        response = await agent.get("/api/v1/health", headers=auth_headers("hag_invalid_token"))
    assert response.status_code == 401

    rows = [
        row
        for row in runtime.ip_access.list_attempts(listener="agent")["items"]
        if row["category"] == "agent_auth"
    ]
    assert rows
    assert rows[0]["decision"] == "denied"
    assert rows[0]["reason"] == "invalid token"


async def test_attempts_pagination_filters_and_sanitisation(runtime: Runtime) -> None:
    ip = runtime.ip_access
    ip.record_attempt("192.0.2.1", listener="agent", category="agent_auth", decision="denied", reason="r1")
    ip.record_attempt("192.0.2.2", listener="agent", category="agent_api", decision="allowed", reason="r2")
    ip.record_attempt(
        "192.0.2.3", listener="management", category="management_gate", decision="denied", reason="r3"
    )

    async with admin_client(runtime) as admin:
        page = await admin.get("/api/admin/ip/attempts?limit=2")
        assert page.status_code == 200
        body = page.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert body["limit"] == 2

        offset = await admin.get("/api/admin/ip/attempts?limit=2&offset=2")
        assert len(offset.json()["items"]) == 1

        denied = await admin.get("/api/admin/ip/attempts?decision=denied")
        assert denied.json()["total"] == 2

        by_ip = await admin.get("/api/admin/ip/attempts?ip=192.0.2.2")
        assert by_ip.json()["total"] == 1
        assert by_ip.json()["items"][0]["ip"] == "192.0.2.2"

        search = await admin.get("/api/admin/ip/attempts?q=r3")
        assert search.json()["total"] == 1

        bad_filter = await admin.get("/api/admin/ip/attempts?decision=whatever")
        assert bad_filter.status_code == 422
        bad_ip = await admin.get("/api/admin/ip/attempts?ip=nope")
        assert bad_ip.status_code == 422

    # History rows expose only the documented, secret-free fields.
    allowed_keys = {"ts", "ip", "listener", "category", "decision", "reason", "agent_id"}
    for item in body["items"]:
        assert set(item) == allowed_keys
        blob = json.dumps(item).lower()
        assert "token" not in blob
        assert "password" not in blob
        assert "secret" not in blob


def test_attempt_history_bounded_retention(runtime: Runtime) -> None:
    ip = runtime.ip_access
    assert IP_ATTEMPTS_MAX_ROWS > 0
    for index in range(30):
        ip.record_attempt(
            f"192.0.2.{index}",
            listener="agent",
            category="agent_auth",
            decision="denied",
            reason="bulk",
        )
    pruned_to = 10
    removed = ip.prune_attempts(max_rows=pruned_to)
    assert removed == 20
    assert ip.list_attempts(limit=100)["total"] == pruned_to


async def test_sampled_allowed_records_do_not_flood_history(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        _agent, token = await create_agent(admin)
    async with agent_client(runtime) as agent:
        for _ in range(5):
            assert (await agent.get("/api/v1/health", headers=auth_headers(token))).status_code == 200
    allowed_rows = [
        row
        for row in runtime.ip_access.list_attempts(listener="agent")["items"]
        if row["decision"] == "allowed"
    ]
    # Sampled to at most one stored row per source per minute.
    assert len(allowed_rows) == 1


# ------------------------------------------------- revocation on deny rule


async def test_deny_rule_revokes_recent_temporary_sessions(runtime: Runtime) -> None:
    from helpers import create_connection, create_credential, initialize_vault

    async with admin_client(runtime) as admin:
        await initialize_vault(admin)
        credential = await create_credential(admin)
        connection = await create_connection(admin, credential_id=credential["id"])
        issued = await admin.post(
            "/api/admin/agent-access/sessions",
            json={
                "name": "ip-revoke-session",
                "scopes": ["ha:read"],
                "connection_ids": [connection["id"]],
                "operation_classes": ["read"],
            },
        )
        assert issued.status_code == 200, issued.text
        session = issued.json()["session"]
        code = str(issued.json()["handoff"]["url"]).split("code=", 1)[1]

    # Redeem the handoff to obtain the session credential while it is valid.
    async with agent_client(runtime) as agent:
        redeemed = await agent.post("/agent/api/redeem", json={"code": code})
        assert redeemed.status_code == 200, redeemed.text
        session_token = redeemed.json()["token"]
        healthy = await agent.get("/api/v1/health", headers=auth_headers(session_token))
        assert healthy.status_code == 200

    # Attribute the session to the soon-to-be-denied source.
    runtime.ip_access.record_attempt(
        "198.51.100.77",
        listener="agent",
        category="agent_api",
        decision="allowed",
        reason="authenticated agent request",
        agent_id=session["id"],
    )

    async with admin_client(runtime) as admin:
        created = await admin.post(
            "/api/admin/ip/rules",
            json={"action": "deny", "cidr": "198.51.100.0/24", "note": "compromised range"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["revoked_sessions"] == [session["id"]]

        detail = await admin.get(f"/api/admin/agent-access/sessions/{session['id']}")
        assert detail.status_code == 200
        row = detail.json()["session"]
        assert row["revoked_at"]
        assert row["status"] == "revoked"

    # Immediate effect: the revoked session credential no longer authenticates.
    async with agent_client(runtime) as agent:
        rejected = await agent.get("/api/v1/health", headers=auth_headers(session_token))
    assert rejected.status_code == 401


# ------------------------------------------------------- settings plumbing


def test_trusted_proxies_options_and_env(tmp_path: Path) -> None:
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"trusted_proxies": ["172.18.0.0/16"]}), encoding="utf-8")
    settings = load_settings(options, data_dir=tmp_path, env={})
    assert settings.trusted_proxies == ("172.18.0.0/16",)

    env_settings = load_settings(data_dir=tmp_path, env={"HAG_TRUSTED_PROXIES": "10.0.0.1, 10.0.0.0/8"})
    assert env_settings.trusted_proxies == ("10.0.0.1", "10.0.0.0/8")


def test_trusted_proxies_invalid_entries_rejected(tmp_path: Path) -> None:
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"trusted_proxies": ["not-a-range"]}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(options, data_dir=tmp_path, env={})


# ------------------------------------------------------------- error shapes


def test_service_errors_have_gateway_codes(runtime: Runtime) -> None:
    ip = runtime.ip_access
    with pytest.raises(ValidationFailure):
        ip.create_rule(action="nope", cidr="10.0.0.1", created_by="admin")
    ip.create_rule(action="deny", cidr="10.0.0.1", created_by="admin", revoke_sessions=False)
    with pytest.raises(ConflictError):
        ip.create_rule(action="deny", cidr="10.0.0.1", created_by="admin", revoke_sessions=False)
    with pytest.raises(NotFoundError):
        ip.get_rule("missing")
