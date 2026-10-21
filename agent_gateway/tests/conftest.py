"""Shared fixtures for the gateway test suite.

The suite runs against the real ASGI apps (no mocking of the security layer)
with a temporary data directory, a deliberately cheap Argon2id cost, and a
mocked upstream Home Assistant.  Anything that would touch a real Home
Assistant, Proxmox host, or SSH server is out of scope here and is documented
as NOT RUN in the final report.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from helpers import ADMIN_HEADERS, PASSWORD

from gateway.api.apps import build_admin_app, build_agent_app
from gateway.runtime import Runtime
from gateway.settings import Settings


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return Settings(
        data_dir=data_dir,
        admin_allowed_ips=frozenset({"127.0.0.1", "172.30.32.2"}),
        require_ingress_user=True,
        admin_user_ids=frozenset(),
        approval_ttl_seconds=600,
    )


@pytest.fixture
def runtime(settings: Settings) -> Iterator[Runtime]:
    rt = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    rt.startup()
    try:
        yield rt
    finally:
        rt.shutdown()


@pytest.fixture
def unlocked(runtime: Runtime) -> Runtime:
    runtime.vault.initialize(PASSWORD)
    return runtime


@pytest.fixture
async def admin(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_admin_app(runtime))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway.test", headers=ADMIN_HEADERS
    ) as client:
        yield client


@pytest.fixture
async def admin_unlocked(unlocked: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_admin_app(unlocked))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway.test", headers=ADMIN_HEADERS
    ) as client:
        yield client


@pytest.fixture
def ha_payload() -> dict[str, object]:
    return {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {"friendly_name": "Kitchen Light"},
        "last_changed": "2026-10-21T10:00:00+00:00",
    }


@pytest.fixture
async def agent_api(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_agent_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        yield client
