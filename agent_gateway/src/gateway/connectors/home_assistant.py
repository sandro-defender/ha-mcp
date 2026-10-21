"""Home Assistant REST connector (read-only by default)."""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import ConnectorError, PolicyDenied, ValidationFailure
from ..redaction import redact_value
from ..utils import validate_entity_id
from .http import make_client, request_json

RAW_LIST_MAX_BYTES = 4 * 1024 * 1024
SAFE_CONFIG_FIELDS = (
    "version",
    "location_name",
    "unit_system",
    "time_zone",
    "language",
    "country",
    "currency",
    "safe_mode",
)
# These domains are refused even if a policy allowlists them.  They either
# execute arbitrary code, control the Supervisor, or mutate logs/recording.
HARD_DENIED_SERVICE_DOMAINS = frozenset(
    {
        "shell_command",
        "python_script",
        "hassio",
        "homeassistant",
        "system_log",
        "recorder",
    }
)


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, *, ca_pem: str | None = None) -> None:
        if not token:
            raise ConnectorError("credential_invalid", "Home Assistant token is empty", status=409)
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._ca_pem = ca_pem
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HomeAssistantClient:
        self._client = make_client(ca_pem=self._ca_pem)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HomeAssistantClient used outside its context")
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def get(self, path: str, *, max_bytes: int, timeout: float) -> Any:
        return await request_json(
            self.client,
            "GET",
            f"{self.base_url}{path}",
            headers=self._headers(),
            max_bytes=max_bytes,
            timeout=timeout,
        )

    async def post(self, path: str, body: dict[str, Any], *, max_bytes: int, timeout: float) -> Any:
        return await request_json(
            self.client,
            "POST",
            f"{self.base_url}{path}",
            headers=self._headers(),
            json_body=body,
            max_bytes=max_bytes,
            timeout=timeout,
        )

    async def test(self) -> dict[str, Any]:
        data = await self.get("/api/", max_bytes=4096, timeout=10)
        if not isinstance(data, dict) or data.get("message") != "API running.":
            raise ConnectorError("home_assistant_unexpected", "the endpoint is not a Home Assistant API")
        return {"reachable": True}


def _state_summary(entity: dict[str, Any]) -> dict[str, Any]:
    attributes = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    return {
        "entity_id": str(entity.get("entity_id", ""))[:128],
        "state": str(entity.get("state", ""))[:256],
        "friendly_name": str(attributes.get("friendly_name", ""))[:128] if attributes else "",
        "last_changed": str(entity.get("last_changed", ""))[:40],
        "domain": str(entity.get("entity_id", "")).split(".")[0][:64],
    }


def _allowed_by_filters(
    entity_id: str, filters: dict[str, tuple[str, ...]], excludes: dict[str, tuple[str, ...]]
) -> bool:
    from ..utils import match_any

    domain = entity_id.split(".")[0]
    entities = filters.get("entities")
    if entities and "*" not in entities and not match_any(entity_id, entities):
        return False
    domains = filters.get("domains")
    if domains and "*" not in domains and not match_any(domain, domains):
        return False
    excluded_entities = excludes.get("entities")
    if excluded_entities and match_any(entity_id, excluded_entities):
        return False
    excluded_domains = excludes.get("domains")
    return not (excluded_domains and match_any(domain, excluded_domains))


async def get_state(
    client: HomeAssistantClient,
    *,
    entity_id: str,
    filters: dict[str, tuple[str, ...]],
    excludes: dict[str, tuple[str, ...]],
    max_bytes: int,
    timeout: float,
) -> dict[str, Any]:
    entity_id = validate_entity_id(entity_id)
    if not _allowed_by_filters(entity_id, filters, excludes):
        raise PolicyDenied("entity is outside the agent's resource allowlist")
    data = await client.get(f"/api/states/{entity_id}", max_bytes=max_bytes, timeout=timeout)
    if not isinstance(data, dict) or "state" not in data:
        raise ConnectorError(
            "home_assistant_unexpected", "Home Assistant returned an unexpected state payload"
        )
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    return {
        "entity_id": entity_id,
        "state": str(data.get("state", ""))[:512],
        "last_changed": str(data.get("last_changed", ""))[:40],
        "last_updated": str(data.get("last_updated", ""))[:40],
        "attributes": redact_value(attributes),
    }


async def list_entities(
    client: HomeAssistantClient,
    *,
    domain: str | None,
    offset: int,
    limit: int,
    filters: dict[str, tuple[str, ...]],
    excludes: dict[str, tuple[str, ...]],
    max_bytes: int,
    timeout: float,
) -> dict[str, Any]:
    data = await client.get("/api/states", max_bytes=max(RAW_LIST_MAX_BYTES, max_bytes), timeout=timeout)
    if not isinstance(data, list):
        raise ConnectorError("home_assistant_unexpected", "Home Assistant returned an unexpected state list")
    items: list[dict[str, Any]] = []
    for entity in data:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entity_id", ""))
        if "." not in entity_id or len(entity_id) > 200:
            continue
        if domain and not entity_id.startswith(f"{domain}."):
            continue
        if not _allowed_by_filters(entity_id, filters, excludes):
            continue
        items.append(_state_summary(entity))
    items.sort(key=lambda item: item["entity_id"])
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
    }


async def get_config(client: HomeAssistantClient, *, max_bytes: int, timeout: float) -> dict[str, Any]:
    data = await client.get("/api/config", max_bytes=max_bytes, timeout=timeout)
    if not isinstance(data, dict):
        raise ConnectorError(
            "home_assistant_unexpected", "Home Assistant returned an unexpected config payload"
        )
    return {key: redact_value(data[key]) for key in SAFE_CONFIG_FIELDS if key in data}


async def call_service(
    client: HomeAssistantClient,
    *,
    domain: str,
    service: str,
    entity_id: str | None,
    data: dict[str, Any] | None,
    max_bytes: int,
    timeout: float,
) -> dict[str, Any]:
    if domain in HARD_DENIED_SERVICE_DOMAINS:
        raise PolicyDenied("service domain is blocked by the gateway")
    payload: dict[str, Any] = dict(data or {})
    if entity_id:
        entity_id = validate_entity_id(entity_id)
        existing = payload.get("entity_id")
        if existing is not None and existing != entity_id:
            raise ValidationFailure("entity_id appears in both the tool argument and service data")
        payload["entity_id"] = entity_id
    if len(str(payload)) > 32_768:
        raise ValidationFailure("service data is too large")
    result = await client.post(
        f"/api/services/{domain}/{service}", payload, max_bytes=max_bytes, timeout=timeout
    )
    affected = len(result) if isinstance(result, list) else 0
    return {
        "called": True,
        "domain": domain,
        "service": service,
        "entity_id": entity_id,
        "affected_entities": affected,
    }
