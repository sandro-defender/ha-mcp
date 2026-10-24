"""Automatic add-on shutdown: inactivity policy, Supervisor stop, UI status.

Every Supervisor interaction here is **mocked** (injected stop caller or
respx-intercepted httpx).  A live ``POST /addons/self/stop`` requires a real
Home Assistant Supervisor and is reported as blocked infrastructure, never as
a passing test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from helpers import ADMIN_HEADERS, auth_headers, create_agent

import gateway.lifecycle as lifecycle_module
from gateway.api.apps import build_admin_app, build_agent_app
from gateway.errors import ConfigError
from gateway.lifecycle import (
    MAX_TIMEOUT_MINUTES,
    MIN_TIMEOUT_MINUTES,
    StopOutcome,
    supervisor_stop,
)
from gateway.runtime import Runtime
from gateway.settings import load_settings

TOKEN = "unit-test-supervisor-token"


def options_file(tmp_path: Path, options: dict[str, Any]) -> Path:
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options), encoding="utf-8")
    return path


def admin_client(runtime: Runtime) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_admin_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=ADMIN_HEADERS,
    )


def agent_http(runtime: Runtime, token: str | None = None) -> httpx.AsyncClient:
    headers = auth_headers(token) if token else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_agent_app(runtime), client=("127.0.0.1", 1)),
        base_url="http://gateway.test",
        headers=headers,
    )


class RecordingStop:
    """Injected Supervisor stop caller; records url/token, returns an outcome."""

    def __init__(self, outcome: StopOutcome | None = None) -> None:
        self.outcome = outcome or StopOutcome("accepted", "the Supervisor accepted the stop request", 200)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, url: str, token: str) -> StopOutcome:
        self.calls.append((url, token))
        return self.outcome


@pytest.fixture
def supervisor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", TOKEN)


# --------------------------------------------------------------- config


def test_auto_shutdown_options_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(
            options_file(tmp_path, {"auto_shutdown_timeout_minutes": 4}),
            data_dir=tmp_path,
            env={},
        )
    with pytest.raises(ConfigError):
        load_settings(
            options_file(tmp_path, {"auto_shutdown_timeout_minutes": 1441}),
            data_dir=tmp_path,
            env={},
        )
    settings = load_settings(
        options_file(tmp_path, {"auto_shutdown_timeout_minutes": 45, "auto_shutdown_enabled": False}),
        data_dir=tmp_path,
        env={},
    )
    assert settings.auto_shutdown_timeout_minutes == 45
    assert settings.auto_shutdown_enabled is False


def test_option_defaults_match_lifecycle_defaults() -> None:
    from gateway.settings import Settings

    defaults = Settings(data_dir=Path("/tmp/unused"))
    assert defaults.auto_shutdown_enabled is True
    assert defaults.auto_shutdown_timeout_minutes == 30
    assert MIN_TIMEOUT_MINUTES <= defaults.auto_shutdown_timeout_minutes <= MAX_TIMEOUT_MINUTES


def test_options_seed_store_until_panel_edit(settings) -> None:
    # Fresh install: options seed the store on every startup while untouched.
    rt = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    rt.startup()
    assert rt.settings_store.get_bool("auto_shutdown_enabled") is True
    rt.settings_store.set("auto_shutdown_enabled", False)
    rt.shutdown()

    rt2 = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    rt2.startup()
    # Not customized yet → add-on options win again on the next startup.
    assert rt2.settings_store.get_bool("auto_shutdown_enabled") is True
    rt2.lifecycle.update_settings(enabled=False, timeout_minutes=45)
    rt2.shutdown()

    rt3 = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    rt3.startup()
    # Panel edit persists across restarts and beats the option defaults.
    assert rt3.settings_store.get_bool("auto_shutdown_enabled") is False
    assert rt3.settings_store.get_int("auto_shutdown_timeout_minutes") == 45
    rt3.shutdown()


def test_settings_update_validation(runtime: Runtime) -> None:
    with pytest.raises(Exception) as excinfo:
        runtime.lifecycle.update_settings(timeout_minutes=MIN_TIMEOUT_MINUTES - 1)
    assert getattr(excinfo.value, "status", None) == 422
    with pytest.raises(Exception) as excinfo:
        runtime.lifecycle.update_settings(timeout_minutes=MAX_TIMEOUT_MINUTES + 1)
    assert getattr(excinfo.value, "status", None) == 422
    payload = runtime.lifecycle.update_settings(timeout_minutes=MIN_TIMEOUT_MINUTES)
    assert payload["timeout_minutes"] == MIN_TIMEOUT_MINUTES


# ------------------------------------------------- Supervisor HTTP contract


async def test_supervisor_stop_posts_self_endpoint_with_bearer_token() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://supervisor/addons/self/stop").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )
        outcome = await supervisor_stop("http://supervisor", "raw-token-value")
    assert route.called
    request = route.calls[0].request
    assert str(request.url) == "http://supervisor/addons/self/stop"
    assert request.headers["Authorization"] == "Bearer raw-token-value"
    assert outcome.status == "accepted"
    assert "raw-token-value" not in outcome.message


async def test_supervisor_stop_outcome_classification() -> None:
    base = "http://supervisor/addons/self/stop"
    with respx.mock(assert_all_called=False) as mock:
        mock.post(base).mock(return_value=httpx.Response(403, json={"result": "error", "message": "denied"}))
        denied = await supervisor_stop("http://supervisor", TOKEN)
    assert denied.status == "rejected" and denied.http_status == 403
    assert TOKEN not in denied.message

    with respx.mock(assert_all_called=False) as mock:
        mock.post(base).mock(return_value=httpx.Response(500, text="secret-echo " + TOKEN))
        failed = await supervisor_stop("http://supervisor", TOKEN)
    assert failed.status == "rejected"
    assert TOKEN not in failed.message

    with respx.mock(assert_all_called=False) as mock:
        mock.post(base).mock(side_effect=httpx.ConnectError("connection refused"))
        unreachable = await supervisor_stop("http://supervisor", TOKEN)
    assert unreachable.status == "unavailable"

    with respx.mock(assert_all_called=False) as mock:
        mock.post(base).mock(side_effect=httpx.ReadTimeout("no reply"))
        unanswered = await supervisor_stop("http://supervisor", TOKEN)
    assert unanswered.status == "unconfirmed"

    with respx.mock(assert_all_called=False) as mock:
        mock.post(base).mock(return_value=httpx.Response(200, json={"result": "error", "message": "busy"}))
        errored = await supervisor_stop("http://supervisor", TOKEN)
    assert errored.status == "rejected"


# ------------------------------------------------------- inactivity timer


async def test_inactivity_timer_expiry(runtime: Runtime, monkeypatch, supervisor_env) -> None:
    lc = runtime.lifecycle
    stopper = RecordingStop()
    lc._stop_caller = stopper
    now = [1000.0]
    monkeypatch.setattr(lc, "_monotonic", lambda: now[0])
    lc._last_activity = now[0]
    runtime.settings_store.set("auto_shutdown_timeout_minutes", 5)

    await lc.tick()
    assert stopper.calls == [] and lc.state == "running"

    now[0] += 4 * 60
    await lc.tick()
    assert stopper.calls == [] and lc.state == "running"

    now[0] += 60
    await lc.tick()
    assert len(stopper.calls) == 1
    assert stopper.calls[0][0] == "http://supervisor"
    assert stopper.calls[0][1] == TOKEN
    assert lc.state == "shutting_down"


async def test_tick_is_inert_when_disabled_or_in_flight(
    runtime: Runtime, monkeypatch, supervisor_env
) -> None:
    lc = runtime.lifecycle
    stopper = RecordingStop()
    lc._stop_caller = stopper
    now = [1000.0]
    monkeypatch.setattr(lc, "_monotonic", lambda: now[0])
    lc._last_activity = now[0]
    runtime.settings_store.set("auto_shutdown_timeout_minutes", 5)

    runtime.settings_store.set("auto_shutdown_enabled", False)
    now[0] += 10_000
    await lc.tick()
    assert stopper.calls == [] and lc.state == "running"

    runtime.settings_store.set("auto_shutdown_enabled", True)
    lc.begin_agent_request()
    await lc.tick()
    assert stopper.calls == [] and lc.state == "running"
    lc.end_agent_request()
    await lc.tick()
    assert len(stopper.calls) == 1


async def test_unauthenticated_and_health_traffic_does_not_reset_timer(runtime: Runtime, monkeypatch) -> None:
    lc = runtime.lifecycle
    monkeypatch.setattr(lc, "_monotonic", lambda: 4242.0)
    lc._last_activity = -1000.0

    async with agent_http(runtime) as anon:
        assert (await anon.get("/healthz")).status_code == 200
        assert (await anon.get("/api/v1/health")).status_code == 401
    assert lc._last_activity == -1000.0

    # Revoked credentials are rejected and must not count as activity.
    async with admin_client(runtime) as admin:
        agent, revoked_token = await create_agent(admin, name="revoked-agent", scopes=["ha:read"])
        revoke = await admin.post(
            f"/api/admin/agents/{agent['id']}/revoke",
            json={"reason": "test"},
        )
        assert revoke.status_code == 200, revoke.text
    lc._last_activity = -1000.0  # admin traffic counted; reset before the probe
    async with agent_http(runtime, revoked_token) as client:
        assert (await client.get("/api/v1/health")).status_code == 401
    assert lc._last_activity == -1000.0

    # A valid credential that passes every gate IS activity.
    async with admin_client(runtime) as admin:
        _, valid_token = await create_agent(admin, name="valid-agent", scopes=["ha:read"])
    lc._last_activity = -1000.0
    async with agent_http(runtime, valid_token) as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert lc._last_activity == 4242.0
    assert lc._last_activity_kind == "agent"


async def test_authenticated_admin_activity_counts(runtime: Runtime, monkeypatch) -> None:
    lc = runtime.lifecycle
    monkeypatch.setattr(lc, "_monotonic", lambda: 7777.0)
    lc._last_activity = -1.0
    async with admin_client(runtime) as admin:
        assert (await admin.get("/api/admin/status")).status_code == 200
    assert lc._last_activity == 7777.0
    assert lc._last_activity_kind == "admin"

    lc._last_activity = -1.0
    async with admin_client(runtime) as admin:
        assert (await admin.get("/healthz")).status_code == 200
    assert lc._last_activity == -1.0


async def test_expired_token_does_not_reset_timer(runtime: Runtime, monkeypatch) -> None:
    lc = runtime.lifecycle
    monkeypatch.setattr(lc, "_monotonic", lambda: 9999.0)
    async with admin_client(runtime) as admin:
        response = await admin.post(
            "/api/admin/agents",
            json={
                "name": "past-agent",
                "scopes": ["ha:read"],
                "policy": {"version": 1, "rules": []},
            },
        )
        assert response.status_code == 200, response.text
        agent_id = response.json()["agent"]["id"]
        expired_token = response.json()["token"]
    # The API refuses past expirations at creation time; age the row directly
    # to simulate an identity that expired while installed.
    runtime.db.execute(
        "UPDATE agents SET expires_at=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", agent_id),
    )
    lc._last_activity = -5.0  # admin setup traffic counted; reset before the probe
    async with agent_http(runtime, expired_token) as client:
        assert (await client.get("/api/v1/health")).status_code == 401
    assert lc._last_activity == -5.0


# ------------------------------------------------- shutdown sequence


async def test_manual_shutdown_accepted_reports_stopping(
    runtime: Runtime, supervisor_env, monkeypatch
) -> None:
    stopper = RecordingStop()
    runtime.lifecycle._stop_caller = stopper
    async with admin_client(runtime) as admin:
        response = await admin.post("/api/admin/lifecycle/shutdown")
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["result"] == "stopping"
        assert body["confirmed"] is True
        assert "Home Assistant" in body["hint"] and "START" in body["hint"]
        assert TOKEN not in response.text

        # Duplicate shutdown is refused while the first is running.
        duplicate = await admin.post("/api/admin/lifecycle/shutdown")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "conflict"

        # Status is user-facing and honest.
        status = await admin.get("/api/admin/lifecycle")
        payload = status.json()
        assert payload["state"] == "shutting_down"
        assert payload["last_shutdown"]["result"] == "accepted"
        assert payload["last_shutdown"]["reason"] == "manual"
        assert payload["supervisor_available"] is True
        assert payload["timeout_range_minutes"] == [MIN_TIMEOUT_MINUTES, MAX_TIMEOUT_MINUTES]
        assert payload["enabled"] is True

        # Session issuance is rejected while shutting down.
        issued = await admin.post(
            "/api/admin/agent-access/sessions",
            json={
                "name": "during-shutdown",
                "scopes": ["ha:read"],
                "connection_ids": [],
                "operation_classes": ["read"],
            },
        )
        assert issued.status_code == 503
        assert issued.json()["error"]["code"] == "shutting_down"

    assert stopper.calls and stopper.calls[0][1] == TOKEN
    rows = runtime.db.query_all("SELECT tool, status FROM audit WHERE tool LIKE 'lifecycle%' ORDER BY id")
    statuses = [(str(r["tool"]), str(r["status"])) for r in rows]
    assert ("lifecycle.shutdown", "requested") in statuses
    assert ("lifecycle.shutdown_result", "accepted") in statuses
    stored = runtime.db.query_all("SELECT value FROM settings WHERE key='lifecycle_last_shutdown_result'")
    assert stored and json.loads(str(stored[0]["value"])) == "accepted"


async def test_manual_shutdown_rejection_reverts_and_reports(
    runtime: Runtime, supervisor_env, monkeypatch
) -> None:
    runtime.lifecycle._stop_caller = RecordingStop(
        StopOutcome("rejected", "the Supervisor denied the stop request", 403)
    )
    async with admin_client(runtime) as admin:
        response = await admin.post("/api/admin/lifecycle/shutdown")
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "shutdown_rejected"
        payload = (await admin.get("/api/admin/lifecycle")).json()
        assert payload["state"] == "running"
        assert payload["last_shutdown"]["result"] == "rejected"
        assert payload["failed_attempts"] == 1
        # A failed attempt does not block an immediate manual retry.
        runtime.lifecycle._stop_caller = RecordingStop()
        retry = await admin.post("/api/admin/lifecycle/shutdown")
        assert retry.status_code == 202

    rows = runtime.db.query_all("SELECT status FROM audit WHERE tool='lifecycle.shutdown_result'")
    statuses = [str(r["status"]) for r in rows]
    assert "failed" in statuses and "accepted" in statuses


async def test_manual_shutdown_without_supervisor_is_honest(runtime: Runtime, monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    stopper = RecordingStop()
    runtime.lifecycle._stop_caller = stopper
    async with admin_client(runtime) as admin:
        response = await admin.post("/api/admin/lifecycle/shutdown")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "supervisor_unavailable"
        payload = (await admin.get("/api/admin/lifecycle")).json()
        assert payload["state"] == "running"
        assert payload["last_shutdown"]["result"] == "unavailable"
    assert stopper.calls == []


async def test_unconfirmed_stop_is_not_claimed_as_success(runtime: Runtime, supervisor_env) -> None:
    runtime.lifecycle._stop_caller = RecordingStop(
        StopOutcome("unconfirmed", "the Supervisor did not return a response in time")
    )
    async with admin_client(runtime) as admin:
        response = await admin.post("/api/admin/lifecycle/shutdown")
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["result"] == "stop_requested"
        assert body["confirmed"] is False
        payload = (await admin.get("/api/admin/lifecycle")).json()
        assert payload["last_shutdown"]["result"] == "stop_requested_unconfirmed"


async def test_shutdown_drains_inflight_agent_requests(runtime: Runtime, supervisor_env, monkeypatch) -> None:
    lc = runtime.lifecycle
    observed: list[int] = []

    async def slow_stop(url: str, token: str) -> StopOutcome:
        observed.append(lc._inflight)
        return StopOutcome("accepted", "the Supervisor accepted the stop request", 200)

    lc._stop_caller = slow_stop
    lc.begin_agent_request()
    task = asyncio.create_task(lc.shutdown("manual", actor_type="admin", actor_id="admin"))
    await asyncio.sleep(0.15)
    assert not task.done(), "shutdown must wait for the in-flight request"
    lc.end_agent_request()
    outcome = await task
    assert outcome["result"] == "accepted"
    assert observed == [0]


async def test_shutdown_proceeds_after_drain_deadline(runtime: Runtime, supervisor_env, monkeypatch) -> None:
    lc = runtime.lifecycle
    monkeypatch.setattr(lifecycle_module, "DRAIN_DEADLINE_SECONDS", 0.1)
    stopper = RecordingStop()
    lc._stop_caller = stopper
    lc.begin_agent_request()
    try:
        outcome = await asyncio.wait_for(lc.shutdown("manual"), timeout=2.0)
    finally:
        lc.end_agent_request()
    assert outcome["result"] == "accepted"
    assert stopper.calls


async def test_shutdown_applies_session_revocation_policy(runtime: Runtime, supervisor_env) -> None:
    from helpers import create_connection, create_credential, initialize_vault

    async with admin_client(runtime) as admin:
        await initialize_vault(admin)
        credential = await create_credential(admin)
        connection = await create_connection(admin, credential_id=credential["id"])
        issued = await admin.post(
            "/api/admin/agent-access/sessions",
            json={
                "name": "revocation-session",
                "scopes": ["ha:read"],
                "connection_ids": [connection["id"]],
                "operation_classes": ["read"],
            },
        )
        assert issued.status_code == 200, issued.text
        session_id = issued.json()["session"]["id"]

    runtime.lifecycle._stop_caller = RecordingStop()
    # Default policy: revoke_on_restart=True → revocation applied at shutdown.
    assert runtime.agent_access.revoke_on_restart() is True
    await runtime.lifecycle.shutdown("manual", actor_type="admin", actor_id="admin")
    record = runtime.agents.get_agent(session_id)
    assert record["revoked_at"]

    # Policy off → sessions retained (restart policy still applies at boot).
    runtime.settings_store.set("agent_sessions_revoke_on_restart", False)
    assert runtime.agent_access.revoke_on_restart() is False
    runtime.lifecycle.state = "running"  # previous shutdown left it shutting_down
    async with admin_client(runtime) as admin:
        issued = await admin.post(
            "/api/admin/agent-access/sessions",
            json={
                "name": "retained-session",
                "scopes": ["ha:read"],
                "connection_ids": [connection["id"]],
                "operation_classes": ["read"],
            },
        )
        assert issued.status_code == 200, issued.text
        kept_id = issued.json()["session"]["id"]
    await runtime.lifecycle.shutdown("manual", actor_type="admin", actor_id="admin")
    kept = runtime.agents.get_agent(kept_id)
    assert kept["revoked_at"] is None


async def test_shutdown_metadata_survives_restart(settings) -> None:
    rt = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    rt.startup()
    rt.lifecycle._stop_caller = RecordingStop()
    import os

    previous = os.environ.get("SUPERVISOR_TOKEN")
    os.environ["SUPERVISOR_TOKEN"] = TOKEN
    try:
        await rt.lifecycle.shutdown("manual", actor_type="admin", actor_id="admin")
    finally:
        if previous is None:
            os.environ.pop("SUPERVISOR_TOKEN", None)
        else:
            os.environ["SUPERVISOR_TOKEN"] = previous
    rt.shutdown()

    rt2 = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    rt2.startup()
    payload = rt2.lifecycle.status_payload()
    assert payload["state"] == "running"
    assert payload["last_shutdown"] is not None
    assert payload["last_shutdown"]["reason"] == "manual"
    assert payload["last_shutdown"]["result"] == "accepted"
    assert payload["last_shutdown"]["at"]
    rt2.shutdown()


async def test_failed_shutdown_backoff_for_automatic_ticks(
    runtime: Runtime, monkeypatch, supervisor_env
) -> None:
    lc = runtime.lifecycle
    stopper = RecordingStop(StopOutcome("rejected", "the Supervisor denied the stop request", 403))
    lc._stop_caller = stopper
    now = [1000.0]
    monkeypatch.setattr(lc, "_monotonic", lambda: now[0])
    lc._last_activity = now[0]
    runtime.settings_store.set("auto_shutdown_timeout_minutes", 5)

    now[0] += 301
    await lc.tick()
    assert len(stopper.calls) == 1
    assert lc.state == "running"

    # Within the backoff window the automatic retry does not fire...
    now[0] += 30
    await lc.tick()
    assert len(stopper.calls) == 1

    # ...and the retry fires after it (this attempt succeeds).
    stopper.outcome = StopOutcome("accepted", "the Supervisor accepted the stop request", 200)
    now[0] += 61
    await lc.tick()
    assert len(stopper.calls) == 2
    assert lc.state == "shutting_down"


# ------------------------------------------- shutting-down gate (agent side)


async def test_shutting_down_blocks_agent_surface_but_not_health(
    runtime: Runtime,
) -> None:
    lc = runtime.lifecycle
    async with admin_client(runtime) as admin:
        _, token = await create_agent(admin, name="gate-agent", scopes=["ha:read"])

    original = lc.state
    lc.state = "shutting_down"
    try:
        async with agent_http(runtime, token) as client:
            blocked = await client.get("/api/v1/health")
            assert blocked.status_code == 503
            assert blocked.json()["error"]["code"] == "shutting_down"
            assert "retry-after" in blocked.headers

            page = await client.get("/agent")
            assert page.status_code == 503

            redeem = await client.post("/agent/api/redeem", json={"code": "x" * 40})
            assert redeem.status_code == 503

            assert (await client.get("/healthz")).status_code == 200

        # An unauthenticated agent-side request is also gated before auth.
        async with agent_http(runtime) as anon:
            assert (await anon.get("/api/v1/health")).status_code == 503

        # Readiness reports the shutdown; liveness does not.
        async with admin_client(runtime) as admin:
            ready = await admin.get("/readyz")
            assert ready.status_code == 503
            assert ready.json()["checks"]["lifecycle"]["state"] == "shutting_down"
            health = await admin.get("/healthz")
            assert health.status_code == 200
    finally:
        lc.state = original or "running"


# ------------------------------------------------------------- no leakage


async def test_supervisor_token_is_never_exposed(
    runtime: Runtime, supervisor_env, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    runtime.lifecycle._stop_caller = RecordingStop()
    async with admin_client(runtime) as admin:
        await admin.post("/api/admin/lifecycle/shutdown")
        texts = [
            (await admin.get("/api/admin/lifecycle")).text,
            (await admin.get("/api/admin/status")).text,
            (await admin.get("/api/admin/audit")).text,
            (await admin.get("/api/admin/diagnostics")).text,
        ]
    for text in texts:
        assert TOKEN not in text
    assert TOKEN not in caplog.text

    # The default HTTP caller never places the token in a message either.
    outcome = StopOutcome("rejected", "the Supervisor denied the stop request", 403)
    assert TOKEN not in outcome.message


# ------------------------------------------------------- lifecycle API


async def test_lifecycle_settings_endpoint(runtime: Runtime) -> None:
    async with admin_client(runtime) as admin:
        put = await admin.put("/api/admin/lifecycle/settings", json={"enabled": False, "timeout_minutes": 60})
        assert put.status_code == 200, put.text
        assert put.json()["enabled"] is False
        assert put.json()["timeout_minutes"] == 60

        too_small = await admin.put("/api/admin/lifecycle/settings", json={"timeout_minutes": 1})
        assert too_small.status_code == 422

        extra = await admin.put("/api/admin/lifecycle/settings", json={"nonsense": True})
        assert extra.status_code == 422

        empty = await admin.put("/api/admin/lifecycle/settings", json={})
        assert empty.status_code == 422

        status = await admin.get("/api/admin/lifecycle")
        assert status.json()["enabled"] is False
        assert status.json()["inactivity_remaining_seconds"] is None

    rows = runtime.db.query_all("SELECT tool FROM audit WHERE tool='lifecycle.settings_update'")
    assert rows
