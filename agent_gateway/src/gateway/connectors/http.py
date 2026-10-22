"""Bounded HTTP helper for connectors.

- TLS verification is always on; an optional custom CA can be supplied.
- Redirects are not followed.
- Response bodies are streamed and capped.
- Error messages never include response bodies, headers, or tokens.
"""

from __future__ import annotations

import json
import ssl
from typing import Any

import httpx

from .. import __version__
from ..errors import ConnectorError, OutputTooLarge

ERROR_BODY_LIMIT = 2048
DEFAULT_CONNECT_TIMEOUT = 5.0


def build_ssl_context(ca_pem: str | None) -> ssl.SSLContext | bool:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if ca_pem:
        try:
            context.load_verify_locations(cadata=ca_pem)
        except (ssl.SSLError, ValueError) as exc:
            raise ConnectorError(
                "tls_configuration_error",
                "the configured CA certificate could not be loaded",
                status=409,
            ) from exc
    return context


def make_client(*, ca_pem: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=build_ssl_context(ca_pem),
        follow_redirects=False,
        timeout=httpx.Timeout(connect=DEFAULT_CONNECT_TIMEOUT, read=30.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        headers={"User-Agent": f"ha-agent-gateway/{__version__}"},
    )


def _status_error(status: int) -> ConnectorError:
    if status == 401:
        return ConnectorError(
            "upstream_auth_failed",
            "upstream rejected the gateway credential",
            status=502,
            details={"http_status": 401, "reason": "authentication_failed"},
        )
    if status == 403:
        return ConnectorError(
            "upstream_auth_failed",
            "upstream rejected the gateway credential (insufficient permissions)",
            status=502,
            details={"http_status": 403, "reason": "permission_denied"},
        )
    if status == 404:
        return ConnectorError(
            "upstream_not_found", "the requested resource was not found upstream", status=404
        )
    if status == 429:
        return ConnectorError(
            "upstream_rate_limited", "the upstream service is rate limiting requests", status=503
        )
    if 300 <= status < 400:
        return ConnectorError(
            "upstream_redirect", "the upstream service returned an unexpected redirect", status=502
        )
    if 400 <= status < 500:
        return ConnectorError("upstream_rejected", "the upstream service rejected the request", status=502)
    return ConnectorError("upstream_error", "the upstream service is unavailable", status=502)


def _is_tls_connect_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    visited = 0
    while current is not None and visited < 6:
        visited += 1
        if isinstance(current, ssl.SSLError):
            return True
        text = str(current).upper()
        if "CERTIFICATE_VERIFY_FAILED" in text or "SSL:" in text or "TLS" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    max_bytes: int,
    timeout: float = 30.0,
) -> Any:
    try:
        request = client.build_request(
            method,
            url,
            headers=headers,
            json=json_body,
            timeout=httpx.Timeout(connect=DEFAULT_CONNECT_TIMEOUT, read=timeout, write=timeout, pool=5.0),
        )
        response = await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        raise ConnectorError(
            "upstream_timeout", "the upstream service did not respond in time", status=504
        ) from exc
    except httpx.ConnectError as exc:
        if _is_tls_connect_error(exc):
            raise ConnectorError(
                "tls_verification_failed",
                "TLS certificate verification failed for the upstream service",
                status=502,
                details={"reason": "tls_certificate_error"},
            ) from exc
        raise ConnectorError(
            "upstream_unreachable", "could not connect to the upstream service", status=502
        ) from exc
    except httpx.HTTPError as exc:
        raise ConnectorError("upstream_error", "the upstream request failed", status=502) from exc

    try:
        if response.status_code >= 300:
            try:
                await response.aread()
            except httpx.HTTPError:
                pass
            raise _status_error(response.status_code)

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise OutputTooLarge(max_bytes)
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        await response.aclose()

    if not payload:
        return {}
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorError(
            "upstream_invalid_response", "the upstream service returned invalid JSON"
        ) from exc
