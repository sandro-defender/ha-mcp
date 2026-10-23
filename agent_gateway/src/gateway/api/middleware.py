"""ASGI middleware for the management and agent surfaces."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..audit.service import AuditService
from ..auth.agents import AgentPrincipal, AgentService
from ..auth.rate_limit import SlidingWindowLimiter
from ..settings import Settings
from ..storage.db import SettingsStore

logger = logging.getLogger(__name__)

USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TOKEN_QUERY_KEYS = frozenset({"token", "access_token", "api_key", "apikey", "authorization", "bearer"})


@dataclass(frozen=True)
class AdminPrincipal:
    user_id: str
    display_name: str


def _json_response(
    status: int, payload: dict[str, object], extra_headers: list[tuple[bytes, bytes]] | None = None
) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return body, headers


async def _send_json(
    send: Send,
    status: int,
    payload: dict[str, object],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body, headers = _json_response(status, payload, extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str | None:
    headers = scope.get("headers") or []
    for key, value in headers:
        if key.lower() == name:
            return str(value.decode("latin-1"))
    return None


def _all_headers(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == name]


def _state(scope: Scope) -> dict[str, object]:
    state = scope.setdefault("state", {})
    if not isinstance(state, dict):
        state = {}
        scope["state"] = state
    return state


def client_ip(scope: Scope) -> str:
    client = scope.get("client")
    if not client:
        return "unknown"
    return str(client[0])


class RequestContextMiddleware:
    """Add a request id and common security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        _state(scope)["request_id"] = request_id
        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {key.lower() for key, _ in headers}
                additions = [
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                ]
                if path.startswith("/api/") or path.startswith("/mcp"):
                    additions.append((b"cache-control", b"no-store"))
                # Never duplicate a header a handler already set.
                headers.extend((key, value) for key, value in additions if key not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_length = _header(scope, b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await _send_json(
                        send,
                        413,
                        {"error": {"code": "request_too_large", "message": "request body is too large"}},
                    )
                    return
            except ValueError:
                await _send_json(
                    send, 400, {"error": {"code": "bad_request", "message": "invalid Content-Length"}}
                )
                return

        total = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except _BodyTooLarge:
            if not started:
                await _send_json(
                    send,
                    413,
                    {"error": {"code": "request_too_large", "message": "request body is too large"}},
                )


class _BodyTooLarge(Exception):
    pass


class AdminGateMiddleware:
    """Only the Supervisor's ingress proxy may reach the management app.

    The Supervisor strips client-supplied remote-user headers and sets them
    itself; the peer address check is what makes those headers trustworthy.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in {"/healthz", "/readyz"}:
            await self.app(scope, receive, send)
            return

        peer = client_ip(scope)
        if peer not in self.settings.admin_allowed_ips:
            logger.warning("rejected management request from untrusted peer")
            await _send_json(
                send, 403, {"error": {"code": "forbidden", "message": "management access is restricted"}}
            )
            return

        user_id = _header(scope, b"x-remote-user-id")
        display_name = _header(scope, b"x-remote-user-display-name") or user_id or ""
        if self.settings.require_ingress_user:
            if not user_id or not USER_ID_RE.fullmatch(user_id):
                await _send_json(
                    send,
                    401,
                    {
                        "error": {
                            "code": "ingress_required",
                            "message": "open this panel through Home Assistant Ingress",
                        }
                    },
                )
                return
            if self.settings.admin_user_ids and user_id not in self.settings.admin_user_ids:
                logger.warning("rejected management request from non-allowlisted Home Assistant user")
                await _send_json(
                    send,
                    403,
                    {
                        "error": {
                            "code": "forbidden",
                            "message": "user is not an allowed gateway administrator",
                        }
                    },
                )
                return
        else:
            user_id = user_id or "dev-admin"
            display_name = display_name or user_id

        _state(scope)["admin"] = AdminPrincipal(user_id=user_id, display_name=display_name[:64])

        if scope.get("method") in UNSAFE_METHODS and path.startswith("/api/"):
            if _header(scope, b"x-gateway-request") != "1":
                await _send_json(
                    send,
                    403,
                    {"error": {"code": "csrf_rejected", "message": "missing required request header"}},
                )
                return
            origin = _header(scope, b"origin")
            if origin:
                host = _header(scope, b"host") or ""
                if not _same_origin(origin, host):
                    await _send_json(
                        send, 403, {"error": {"code": "origin_rejected", "message": "origin is not allowed"}}
                    )
                    return
            fetch_site = _header(scope, b"sec-fetch-site")
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                await _send_json(
                    send,
                    403,
                    {"error": {"code": "origin_rejected", "message": "cross-site request rejected"}},
                )
                return

        await self.app(scope, receive, send)


def _page_guard(scope: Scope, path: str) -> tuple[int, dict[str, Any]] | None:
    """Same-origin and method checks for the unauthenticated handoff page."""

    method = str(scope.get("method", "GET")).upper()
    if path == AGENT_REDEEM_PATH:
        if method != "POST":
            return 405, {"error": {"code": "method_not_allowed", "message": "POST is required"}}
        content_type = (_header(scope, b"content-type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return 415, {
                "error": {"code": "unsupported_media_type", "message": "application/json is required"}
            }
    elif method not in {"GET", "HEAD"}:
        return 405, {"error": {"code": "method_not_allowed", "message": "GET is required"}}

    host = _header(scope, b"host") or ""
    origin = _header(scope, b"origin")
    if origin and not _same_origin(origin, host):
        return 403, {"error": {"code": "origin_rejected", "message": "origin is not allowed"}}
    fetch_site = _header(scope, b"sec-fetch-site")
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return 403, {"error": {"code": "origin_rejected", "message": "cross-site request rejected"}}
    return None


def _same_origin(origin: str, host: str) -> bool:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    origin_netloc = parsed.hostname.lower()
    if parsed.port and parsed.port not in {80, 443}:
        origin_netloc = f"{origin_netloc}:{parsed.port}"
    return origin_netloc == host.lower()


#: Exact paths on the agent listener that must work before a session token
#: exists.  Everything else (including /agent/api/session) still needs a bearer
#: token.  These are matched exactly, never by prefix.
AGENT_PAGE_PATHS = frozenset({"/agent", "/agent/", "/agent/styles.css", "/agent/agent.js"})
AGENT_REDEEM_PATH = "/agent/api/redeem"


class AgentAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        agents: AgentService,
        settings_store: SettingsStore,
        settings: Settings,
        limiter: SlidingWindowLimiter,
        audit: AuditService | None = None,
    ) -> None:
        self.app = app
        self.agents = agents
        self.settings_store = settings_store
        self.settings = settings
        self.limiter = limiter
        self.audit = audit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in {"/healthz", "/readyz"}:
            await self.app(scope, receive, send)
            return

        query = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        if any(key.lower() in TOKEN_QUERY_KEYS for key in query):
            await _send_json(
                send,
                400,
                {
                    "error": {
                        "code": "token_in_query",
                        "message": "bearer tokens must not be sent in the query string",
                    }
                },
            )
            return

        host = _header(scope, b"host") or ""
        if self.settings.agent_allowed_hosts:
            host_only = host.rsplit(":", 1)[0].lower() if host.count(":") == 1 else host.lower()
            if (
                host.lower() not in self.settings.agent_allowed_hosts
                and host_only not in self.settings.agent_allowed_hosts
            ):
                await _send_json(
                    send, 421, {"error": {"code": "host_rejected", "message": "host is not allowed"}}
                )
                return

        if path in AGENT_PAGE_PATHS or path == AGENT_REDEEM_PATH:
            # The handoff page is intentionally unauthenticated: the session
            # credential does not exist yet.  It is still same-origin only and
            # the redeem call must be a JSON POST, so a cross-site form cannot
            # consume the code.
            page_error = _page_guard(scope, path)
            if page_error is not None:
                page_status, page_body = page_error
                await _send_json(send, page_status, page_body)
                return
            await self.app(scope, receive, send)
            return

        origin = _header(scope, b"origin")
        if origin and origin.rstrip("/") not in self.settings.agent_allowed_origins:
            await _send_json(
                send,
                403,
                {"error": {"code": "origin_rejected", "message": "browser origins are not allowed"}},
            )
            return

        auth_headers = _all_headers(scope, b"authorization")
        if len(auth_headers) != 1:
            await self._auth_failure(send, scope, "missing or duplicate authorization header")
            return
        value = auth_headers[0].strip()
        if not value.lower().startswith("bearer "):
            await self._auth_failure(send, scope, "unsupported authorization scheme")
            return
        token = value[7:].strip()
        if not token:
            await self._auth_failure(send, scope, "empty bearer token")
            return

        principal = self.agents.authenticate(token)
        if principal is None:
            await self._auth_failure(send, scope, "invalid token")
            return

        if self.settings_store.get_bool("emergency_disabled"):
            await _send_json(
                send,
                503,
                {
                    "error": {
                        "code": "agent_access_disabled",
                        "message": "agent access is disabled by the administrator",
                    }
                },
            )
            return

        _state(scope)["agent"] = principal
        await self.app(scope, receive, send)

    async def _auth_failure(self, send: Send, scope: Scope, reason: str) -> None:
        peer = client_ip(scope)
        allowed, retry_after = self.limiter.check_and_record(f"authfail:{peer}", limit=10, window_seconds=60)
        logger.warning("agent authentication failed from %s (%s)", peer, reason)
        if self.audit is not None:
            try:
                self.audit.record(
                    actor_type="peer",
                    actor_id=peer,
                    tool="auth.failed",
                    decision="rate_limited" if not allowed else "deny",
                    status="rate_limited" if not allowed else "denied",
                    reason=reason,
                    error_category="unauthenticated",
                    request_id=str(_state(scope).get("request_id") or ""),
                )
            except Exception:
                logger.debug("failed to record auth failure audit event")
        if not allowed:
            await _send_json(
                send,
                429,
                {"error": {"code": "rate_limited", "message": "too many authentication failures"}},
                [(b"retry-after", str(retry_after).encode())],
            )
            return
        await _send_json(
            send,
            401,
            {"error": {"code": "unauthenticated", "message": "a valid agent bearer token is required"}},
            [(b"www-authenticate", b'Bearer error="invalid_token"')],
        )


def get_agent_from_scope(scope: Scope) -> AgentPrincipal | None:
    state = scope.get("state") or {}
    agent = state.get("agent") if isinstance(state, dict) else None
    return agent if isinstance(agent, AgentPrincipal) else None


class AccessLogMiddleware:
    """Minimal method/path logging; no query strings or headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status_holder = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
            await send(message)

        await self.app(scope, receive, send_wrapper)
        logger.info(
            "%s %s -> %s in %dms",
            scope.get("method", ""),
            scope.get("path", ""),
            status_holder["status"],
            int((time.monotonic() - started) * 1000),
        )
