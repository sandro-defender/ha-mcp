"""Release-notice acknowledgement and version-transition regression tests."""

from __future__ import annotations

import json

import httpx
import pytest

import gateway.releases as releases_mod
from gateway.runtime import Runtime
from gateway.settings import Settings


async def test_acknowledge_endpoint_persists_and_updates_status(
    admin: httpx.AsyncClient, runtime: Runtime
) -> None:
    """Dismissing the current notice updates both release APIs and status."""

    before = await admin.get("/api/admin/releases")
    assert before.status_code == 200
    assert before.json()["post_update_notice"]["show"] is True

    acknowledged = await admin.post("/api/admin/releases/acknowledge", json={})
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["post_update_notice"]["show"] is False
    assert acknowledged.json()["post_update_notice"]["acknowledged_version"] == releases_mod.__version__

    after = await admin.get("/api/admin/releases")
    assert after.json()["post_update_notice"]["show"] is False
    status = await admin.get("/api/admin/status")
    assert status.status_code == 200
    assert status.json()["release"]["post_update_notice"]["show"] is False

    stored = runtime.db.query_one("SELECT value FROM settings WHERE key='seen_version'")
    assert stored is not None
    assert json.loads(stored["value"]) == releases_mod.__version__


async def test_acknowledge_requires_the_csrf_header(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        "/api/admin/releases/acknowledge", json={}, headers={"X-Gateway-Request": "0"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"


def test_acknowledgement_survives_runtime_restart(settings: Settings) -> None:
    first = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    first.startup()
    try:
        assert first.releases.get_release_summary()["post_update_notice"]["show"] is True
        first.releases.acknowledge_version()
        assert first.releases.get_release_summary()["post_update_notice"]["show"] is False
    finally:
        first.shutdown()

    second = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    second.startup()
    try:
        notice = second.releases.get_release_summary()["post_update_notice"]
        assert notice["show"] is False
        assert notice["acknowledged_version"] == releases_mod.__version__
    finally:
        second.shutdown()


def test_dismissing_one_release_does_not_hide_a_new_release(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_version = releases_mod.__version__
    new_version = "999.0.0"
    runtime.releases.acknowledge_version()
    assert runtime.releases.get_release_summary()["post_update_notice"]["show"] is False

    monkeypatch.setattr(releases_mod, "__version__", new_version)
    assert runtime.releases.get_release_summary()["post_update_notice"]["show"] is True

    # An explicit old-version acknowledgement cannot dismiss the new release.
    runtime.releases.acknowledge_version(current_version)
    assert runtime.releases.get_release_summary()["post_update_notice"]["show"] is True

    runtime.releases.acknowledge_version(new_version)
    assert runtime.releases.get_release_summary()["post_update_notice"]["show"] is False
