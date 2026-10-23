"""Management API: ingress gate, CSRF, vault, credentials, connections, agents."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from helpers import (
    ADMIN_HEADERS,
    PASSWORD,
    create_agent,
    create_connection,
    create_credential,
    initialize_vault,
)

from gateway.api.apps import build_admin_app
from gateway.runtime import Runtime


async def client_for(app, *, client=("127.0.0.1", 123), headers=None) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=client)
    return httpx.AsyncClient(transport=transport, base_url="http://gateway.test", headers=headers or {})


# ------------------------------------------------------------- ingress gate


async def test_healthz_is_public(runtime: Runtime) -> None:
    async with await client_for(build_admin_app(runtime)) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_missing_ingress_user_is_rejected(runtime: Runtime) -> None:
    async with await client_for(build_admin_app(runtime)) as client:
        response = await client.get("/api/admin/status")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ingress_required"


async def test_untrusted_peer_is_rejected(runtime: Runtime) -> None:
    async with await client_for(
        build_admin_app(runtime), client=("10.0.0.5", 5000), headers=ADMIN_HEADERS
    ) as client:
        response = await client.get("/api/admin/status")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_invalid_user_id_format_is_rejected(runtime: Runtime) -> None:
    headers = {"X-Remote-User-Id": "bad user id!", "X-Gateway-Request": "1"}
    async with await client_for(build_admin_app(runtime), headers=headers) as client:
        response = await client.get("/api/admin/status")
    assert response.status_code == 401


async def test_admin_allowlist_is_enforced(data_dir: Path) -> None:
    from gateway.settings import Settings

    settings = Settings(
        data_dir=data_dir,
        admin_allowed_ips=frozenset({"127.0.0.1"}),
        admin_user_ids=frozenset({"someone-else"}),
    )
    rt = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    rt.startup()
    try:
        async with await client_for(build_admin_app(rt), headers=ADMIN_HEADERS) as client:
            response = await client.get("/api/admin/status")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
    finally:
        rt.shutdown()


async def test_unsafe_requests_require_csrf_header(runtime: Runtime) -> None:
    headers = {"X-Remote-User-Id": "admin-user"}
    async with await client_for(build_admin_app(runtime), headers=headers) as client:
        response = await client.post("/api/admin/vault/lock")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"


async def test_cross_origin_and_cross_site_requests_are_rejected(runtime: Runtime) -> None:
    headers = {**ADMIN_HEADERS, "Origin": "http://evil.example"}
    async with await client_for(build_admin_app(runtime), headers=headers) as client:
        response = await client.post("/api/admin/vault/lock")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_rejected"

    headers = {**ADMIN_HEADERS, "Sec-Fetch-Site": "cross-site"}
    async with await client_for(build_admin_app(runtime), headers=headers) as client:
        response = await client.post("/api/admin/vault/lock")
    assert response.status_code == 403


async def test_index_serves_ui_with_csp(runtime: Runtime) -> None:
    async with await client_for(build_admin_app(runtime), headers=ADMIN_HEADERS) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "static/app.js" in response.text


# ------------------------------------------------------------------- vault


async def test_vault_lifecycle_over_http(admin: httpx.AsyncClient, runtime: Runtime) -> None:
    status = await admin.get("/api/admin/vault")
    assert status.json()["exists"] is False

    created = await initialize_vault(admin)
    assert created["unlocked"] is True

    locked = await admin.post("/api/admin/vault/lock", json={})
    assert locked.status_code == 200
    assert runtime.vault.unlocked is False

    wrong = await admin.post("/api/admin/vault/unlock", json={"password": "definitely-not-right"})
    assert wrong.status_code in {400, 401}
    assert wrong.json()["error"]["code"] == "vault_unlock_failed"

    right = await admin.post("/api/admin/vault/unlock", json={"password": PASSWORD})
    assert right.status_code == 200
    assert runtime.vault.unlocked is True


async def test_vault_endpoints_require_unlocked_vault(admin: httpx.AsyncClient) -> None:
    response = await admin.get("/api/admin/credentials")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "vault_not_initialized"

    await initialize_vault(admin)
    await admin.post("/api/admin/vault/lock", json={})
    locked = await admin.get("/api/admin/credentials")
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "vault_locked"


async def test_vault_password_change(admin_unlocked: httpx.AsyncClient) -> None:
    response = await admin_unlocked.post(
        "/api/admin/vault/password",
        json={"current_password": PASSWORD, "new_password": "a much better passphrase"},
    )
    assert response.status_code == 200
    assert response.json()["unlocked"] is True


async def test_vault_export_is_an_attachment_and_redacted(admin_unlocked: httpx.AsyncClient) -> None:
    await create_credential(admin_unlocked, value={"token": "plain-text-token-value"})
    response = await admin_unlocked.get("/api/admin/vault/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert b"plain-text-token-value" not in response.content
    assert json.loads(response.content)["format"] == "hag-vault"


# ------------------------------------------------------------- credentials


async def test_credential_create_and_list_never_returns_secret(
    admin_unlocked: httpx.AsyncClient,
) -> None:
    created = await create_credential(admin_unlocked, value={"token": "plain-text-token-value"})
    assert "plain-text-token-value" not in json.dumps(created)
    listing = (await admin_unlocked.get("/api/admin/credentials")).json()
    assert listing["items"][0]["masked"].endswith("alue")
    assert "plain-text-token-value" not in json.dumps(listing)


async def test_credential_validation_errors(admin_unlocked: httpx.AsyncClient) -> None:
    # Domain validation failures use HTTP 422 with a typed error code.
    missing = await admin_unlocked.post(
        "/api/admin/credentials", json={"name": "x", "kind": "proxmox_token", "value": {}}
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_error"

    bad_kind = await admin_unlocked.post(
        "/api/admin/credentials", json={"name": "x", "kind": "nope", "value": {"value": "v"}}
    )
    assert bad_kind.status_code == 422

    bad_token_id = await admin_unlocked.post(
        "/api/admin/credentials",
        json={"name": "x", "kind": "proxmox_token", "value": {"token_id": "nope", "token_secret": "s"}},
    )
    assert bad_token_id.status_code == 422


async def test_credential_rotation_and_delete_referenced_credential(
    admin_unlocked: httpx.AsyncClient,
) -> None:
    credential = await create_credential(admin_unlocked)
    updated = await admin_unlocked.patch(
        f"/api/admin/credentials/{credential['id']}",
        json={"value": {"token": "rotated-token-value"}},
    )
    assert updated.status_code == 200
    assert "rotated-token-value" not in json.dumps(updated.json())

    connection = await create_connection(admin_unlocked, credential_id=credential["id"])
    blocked = await admin_unlocked.delete(f"/api/admin/credentials/{credential['id']}")
    assert blocked.status_code == 409
    listing = (await admin_unlocked.get("/api/admin/credentials")).json()["items"][0]
    assert listing["used_by_connections"] == [connection["name"]]

    await admin_unlocked.delete(f"/api/admin/connections/{connection['id']}")
    deleted = await admin_unlocked.delete(f"/api/admin/credentials/{credential['id']}")
    assert deleted.status_code == 200


# ------------------------------------------------------------- connections


async def test_connection_requires_matching_credential_kind(
    admin_unlocked: httpx.AsyncClient,
) -> None:
    credential = await create_credential(admin_unlocked, kind="home_assistant_token")
    response = await admin_unlocked.post(
        "/api/admin/connections",
        json={
            "name": "pve",
            "type": "proxmox",
            "config": {"base_url": "https://pve.test:8006"},
            "credential_id": credential["id"],
        },
    )
    assert response.status_code == 422


async def test_tls_verification_cannot_be_disabled(admin_unlocked: httpx.AsyncClient) -> None:
    credential = await create_credential(admin_unlocked)
    response = await admin_unlocked.post(
        "/api/admin/connections",
        json={
            "name": "home",
            "type": "home_assistant",
            "config": {"base_url": "https://ha.test:8123", "verify_tls": False},
            "credential_id": credential["id"],
        },
    )
    assert response.status_code == 422
    assert "TLS" in response.text


async def test_ssh_connection_rejects_root_and_requires_allowlists(
    admin_unlocked: httpx.AsyncClient,
) -> None:
    credential = await create_credential(
        admin_unlocked,
        name="ssh key",
        kind="ssh_private_key",
        value={"private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----"},
    )
    root = await admin_unlocked.post(
        "/api/admin/connections",
        json={
            "name": "root-ssh",
            "type": "ssh",
            "config": {"host": "192.0.2.10", "username": "root"},
            "credential_id": credential["id"],
        },
    )
    assert root.status_code == 422

    ok = await admin_unlocked.post(
        "/api/admin/connections",
        json={
            "name": "ops-ssh",
            "type": "ssh",
            "config": {
                "host": "192.0.2.10",
                "username": "agentgw",
                "allowed_services": ["nginx"],
                "allowed_log_paths": ["/var/log/nginx/error.log"],
            },
            "credential_id": credential["id"],
        },
    )
    assert ok.status_code == 200, ok.text


async def test_connection_test_success_is_recorded(
    admin_unlocked: httpx.AsyncClient,
) -> None:
    credential = await create_credential(admin_unlocked)
    connection = await create_connection(admin_unlocked, credential_id=credential["id"])
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/").mock(
            return_value=httpx.Response(200, json={"message": "API running."})
        )
        response = await admin_unlocked.post(f"/api/admin/connections/{connection['id']}/test", json={})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    listing = (await admin_unlocked.get("/api/admin/connections")).json()["items"]
    assert listing[0]["status"] == "ok"


async def test_connection_test_failure_is_reported_not_raised(
    admin_unlocked: httpx.AsyncClient,
) -> None:
    credential = await create_credential(admin_unlocked)
    connection = await create_connection(admin_unlocked, credential_id=credential["id"])
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://ha.test:8123/api/").mock(side_effect=httpx.ConnectError("refused"))
        response = await admin_unlocked.post(f"/api/admin/connections/{connection['id']}/test", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"]


# ------------------------------------------------------------------ agents


async def test_agent_token_is_shown_once(admin_unlocked: httpx.AsyncClient) -> None:
    agent, token = await create_agent(admin_unlocked, name="claude")
    assert token.startswith("hag_")
    assert "token_hash" not in agent
    listing = (await admin_unlocked.get("/api/admin/agents")).json()["items"]
    assert "token" not in listing[0]
    assert listing[0]["token_prefix"] == agent["token_prefix"]

    rotated = await admin_unlocked.post(f"/api/admin/agents/{agent['id']}/rotate-token", json={})
    assert rotated.status_code == 200
    assert rotated.json()["token"] != token


async def test_agent_validation_and_revocation(admin_unlocked: httpx.AsyncClient) -> None:
    bad_scope = await admin_unlocked.post(
        "/api/admin/agents", json={"name": "x", "scopes": ["root"], "policy": {"version": 1, "rules": []}}
    )
    assert bad_scope.status_code == 422

    bad_policy = await admin_unlocked.post(
        "/api/admin/agents",
        json={"name": "y", "scopes": ["ha:read"], "policy": {"version": 1, "rules": [{"id": "!"}]}},
    )
    assert bad_policy.status_code == 422

    agent, token = await create_agent(admin_unlocked, name="revocable")
    revoked = await admin_unlocked.post(f"/api/admin/agents/{agent['id']}/revoke", json={})
    assert revoked.status_code == 200
    assert revoked.json()["enabled"] is False
    assert token


async def test_policy_update_rejects_unknown_constraints(admin_unlocked: httpx.AsyncClient) -> None:
    agent, _ = await create_agent(admin_unlocked)
    response = await admin_unlocked.put(
        f"/api/admin/agents/{agent['id']}/policy",
        json={
            "document": {
                "version": 1,
                "rules": [
                    {
                        "id": "bad",
                        "effect": "allow",
                        "tools": ["ha.*"],
                        "connections": ["*"],
                        "constraints": {"bogus": ["x"]},
                    }
                ],
            }
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- settings


async def test_settings_bounds_and_emergency_switch(admin_unlocked: httpx.AsyncClient) -> None:
    too_low = await admin_unlocked.put("/api/admin/settings", json={"approval_ttl_seconds": 5})
    assert too_low.status_code == 422 or too_low.status_code == 400

    updated = await admin_unlocked.put(
        "/api/admin/settings", json={"audit_retention_days": 7, "emergency_disabled": True}
    )
    assert updated.status_code == 200
    assert updated.json()["emergency_disabled"] is True


async def test_tool_group_updates_reject_unknown_groups(admin_unlocked: httpx.AsyncClient) -> None:
    ok = await admin_unlocked.put("/api/admin/tools/groups", json={"groups": ["home_assistant"]})
    assert ok.status_code == 200
    assert [group["id"] for group in ok.json()["groups"] if group["enabled"]] == ["home_assistant"]

    bad = await admin_unlocked.put("/api/admin/tools/groups", json={"groups": ["nope"]})
    assert bad.status_code == 422


# ------------------------------------------------------------------- audit


async def test_audit_filters_and_redaction_check(admin_unlocked: httpx.AsyncClient) -> None:
    await create_credential(admin_unlocked, name="one")
    events = (await admin_unlocked.get("/api/admin/audit?limit=10")).json()
    assert events["total"] >= 1
    assert events["items"][0]["actor_type"] == "admin"
    assert "one" in json.dumps(events)

    filtered = (await admin_unlocked.get("/api/admin/audit?tool=credential.create")).json()
    assert all(item["tool"] == "credential.create" for item in filtered["items"])

    redaction = (await admin_unlocked.get("/api/admin/audit/verify-redaction")).json()
    assert redaction["contains_known_secret"] is False


async def test_purge_reports_deleted_counts(admin_unlocked: httpx.AsyncClient) -> None:
    response = await admin_unlocked.post("/api/admin/maintenance/purge", json={})
    assert response.status_code == 200
    assert set(response.json()) == {"audit_deleted", "approvals_deleted"}


@pytest.mark.parametrize("path", ["/api/admin/status", "/api/admin/scopes"])
async def test_read_endpoints_require_admin_principal(admin: httpx.AsyncClient, path: str) -> None:
    response = await admin.get(path)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "shallow_module_path",
    [
        "/app/src/gateway/releases.py",
        "/opt/gateway/releases.py",
        "/releases.py",
    ],
)
async def test_status_and_releases_succeed_at_container_path_depths(
    admin: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shallow_module_path: str,
) -> None:
    """Regression: `/app/src/gateway/releases.py` has only 4 parents (`IndexError: 4`)."""

    import gateway.releases as releases_mod

    monkeypatch.setattr(releases_mod, "__file__", shallow_module_path)
    monkeypatch.chdir(tmp_path)

    status_resp = await admin.get("/api/admin/status")
    assert status_resp.status_code == 200, status_resp.text
    assert status_resp.json()["version"]

    releases_resp = await admin.get("/api/admin/releases")
    assert releases_resp.status_code == 200, releases_resp.text
    assert releases_resp.json()["installed_version"]

    diag_resp = await admin.get("/api/admin/diagnostics")
    assert diag_resp.status_code == 200, diag_resp.text


def test_find_changelog_path_tolerates_permission_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import gateway.releases as releases_mod

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [0.4.1] - 2026-10-23\n", encoding="utf-8")

    monkeypatch.setattr(releases_mod, "__file__", "/app/src/gateway/releases.py")
    monkeypatch.chdir(tmp_path)
    real_is_file = Path.is_file

    def guarded_is_file(self: Path) -> bool:
        if str(self).startswith("/app/"):
            raise PermissionError("simulated AppArmor denial")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    assert releases_mod.find_changelog_path() == changelog
