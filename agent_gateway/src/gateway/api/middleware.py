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
from ..ipaccess import Decision, IPAccessService
from ..lifecycle import LifecycleService
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

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        lifecycle: LifecycleService | None = None,
        ip_access: IPAccessService | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.lifecycle = lifecycle
        self.ip_access = ip_access

    def _record_management_denied(self, scope: Scope, reason: str) -> None:
        """Persist a blocked management connection attempt (history only).

        Management traffic is never *allowed* by IP rules — it is gated by
        the Supervisor peer check — so only rejections are recorded here.
        """

        if self.ip_access is None:
            return
        peer = client_ip(scope)
        try:
            self.ip_access.record_attempt(
                peer,
                listener="management",
                category="management_gate",
                decision="denied",
                reason=reason,
            )
        except Exception:
            logger.debug("failed to record management attempt")

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
            self._record_management_denied(scope, "peer not in admin_allowed_ips")
            await _send_json(
                send, 403, {"error": {"code": "forbidden", "message": "management access is restricted"}}
            )
            return

        user_id = _header(scope, b"x-remote-user-id")
        display_name = _header(scope, b"x-remote-user-display-name") or user_id or ""
        if self.settings.require_ingress_user:
            if not user_id or not USER_ID_RE.fullmatch(user_id):
                self._record_management_denied(scope, "missing ingress user")
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
                self._record_management_denied(scope, "user not allowlisted")
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
                self._record_management_denied(scope, "missing request header")
                await _send_json(
                    send,
                    403,
                    {"error": {"code": "csrf_rejected", "message": "missing required request header"}},
                )
                return
            origin = _header(scope, b"origin")
            if origin:
                host = _header(scope, b"host") or ""
                forwarded_host = (_header(scope, b"x-forwarded-host") or "").split(",")[0].strip()
                if not _same_origin(origin, host) and not (
                    forwarded_host and _same_origin(origin, forwarded_host)
                ):
                    self._record_management_denied(scope, "origin rejected")
                    await _send_json(
                        send, 403, {"error": {"code": "origin_rejected", "message": "origin is not allowed"}}
                    )
                    return
            fetch_site = _header(scope, b"sec-fetch-site")
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                self._record_management_denied(scope, "cross-site request rejected")
                await _send_json(
                    send,
                    403,
                    {"error": {"code": "origin_rejected", "message": "cross-site request rejected"}},
                )
                return

        # Authenticated Ingress traffic counts as legitimate administrative
        # activity for the inactivity timer (documented in lifecycle.py);
        # rejected peers and failed CSRF/origin checks never get here.
        if self.lifecycle is not None:
            self.lifecycle.record_activity("admin")
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
    default_port = 443 if parsed.scheme == "https" else 80
    origin_netloc = parsed.hostname.lower()
    if parsed.port and parsed.port != default_port:
        origin_netloc = f"{origin_netloc}:{parsed.port}"
    host_netloc = host.strip().lower()
    if host_netloc.endswith(f":{default_port}"):
        host_netloc = host_netloc[: -len(f":{default_port}")]
    return bool(host_netloc) and origin_netloc == host_netloc


class IPAccessMiddleware:
    """Backend IP eligibility for the agent listener, evaluated before auth.

    Runs outside :class:`AgentAuthMiddleware`, so denied sources are blocked
    before token validation, handoff redemption, REST calls, and MCP
    execution — and the denial is recorded even when the request would also
    have failed authentication.  Health/readiness probes are exempt (they
    expose nothing) so container checks keep working from any address.
    """

    def __init__(self, app: ASGIApp, *, ip_access: IPAccessService) -> None:
        self.app = app
        self.ip_access = ip_access

    @staticmethod
    def _category(path: str) -> str:
        if path.startswith("/mcp"):
            return "mcp"
        if path.startswith("/agent/api/redeem"):
            return "handoff_redeem"
        if path.startswith("/agent"):
            return "agent_page"
        if path.startswith("/api/"):
            return "agent_api"
        return "other"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in {"/healthz", "/readyz"}:
            await self.app(scope, receive, send)
            return

        forwarded = [
            value.decode("latin-1") for name, value in scope.get("headers", []) if name == b"x-forwarded-for"
        ]
        peer = client_ip(scope)
        source_ip = self.ip_access.resolve_source_ip(None if peer == "unknown" else peer, forwarded)
        scope["gateway_source_ip"] = source_ip

        evaluation = self.ip_access.evaluate(source_ip)
        if evaluation.decision == "allowed":
            await self.app(scope, receive, send)
            return

        allowed_by_rate, retry_after = self.ip_access.limiter.check_and_record(
            f"ipdeny:{source_ip}",
            limit=20,
            window_seconds=60,
        )
        decision: Decision = "denied" if allowed_by_rate else "rate_limited"
        try:
            self.ip_access.record_attempt(
                source_ip,
                listener="agent",
                category=self._category(path),
                decision=decision,
                reason=evaluation.reason,
            )
        except Exception:
            logger.debug("failed to record IP attempt")
        if self.ip_access.audit is not None:
            try:
                self.ip_access.audit.record(
                    actor_type="peer",
                    actor_id=source_ip,
                    tool="ip.access_denied",
                    decision="rate_limited" if not allowed_by_rate else "deny",
                    status="rate_limited" if not allowed_by_rate else "denied",
                    reason=evaluation.reason,
                    error_category="forbidden",
                    request_id=str(_state(scope).get("request_id") or ""),
                )
            except Exception:
                logger.debug("failed to record IP denial audit event")

        request_id = str(_state(scope).get("request_id") or "")
        if not allowed_by_rate:
            await _send_json(
                send,
                429,
                {
                    "error": {
                        "code": "rate_limited",
                        "message": "too many blocked connection attempts",
                        "request_id": request_id,
                    }
                },
                [(b"retry-after", str(retry_after).encode())],
            )
            return
        await _send_json(
            send,
            403,
            {
                "error": {
                    "code": "ip_denied",
                    "message": "this source address is not permitted by IP access rules",
                    "request_id": request_id,
                }
            },
        )


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
        lifecycle: LifecycleService | None = None,
        ip_access: IPAccessService | None = None,
    ) -> None:
        self.app = app
        self.agents = agents
        self.settings_store = settings_store
        self.settings = settings
        self.limiter = limiter
        self.audit = audit
        self.lifecycle = lifecycle
        self.ip_access = ip_access

    def _source_ip(self, scope: Scope) -> str:
        resolved = scope.get("gateway_source_ip")
        if isinstance(resolved, str) and resolved:
            return resolved
        return client_ip(scope)

    def _record_denied(self, scope: Scope, reason: str) -> None:
        if self.ip_access is None:
            return
        try:
            self.ip_access.record_attempt(
                self._source_ip(scope),
                listener="agent",
                category="agent_auth",
                decision="denied",
                reason=reason,
            )
        except Exception:
            logger.debug("failed to record denied attempt")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in {"/healthz", "/readyz"}:
            await self.app(scope, receive, send)
            return

        # Shutdown gate: reject every new agent session, redeem, REST call,
        # and MCP request while the add-on is stopping.  Health/readiness stay
        # reachable so the watchdog keeps seeing a live process.
        if self.lifecycle is not None and self.lifecycle.blocks_agents:
            await _send_json(
                send,
                503,
                {
                    "error": {
                        "code": "shutting_down",
                        "message": "the gateway is shutting down",
                    }
                },
                [(b"retry-after", b"60")],
            )
            return

        query = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        if any(key.lower() in TOKEN_QUERY_KEYS for key in query):
            self._record_denied(scope, "bearer token in query string")
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
                self._record_denied(scope, "host not allowed")
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
            self._record_denied(scope, "browser origin not allowed")
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
            self._record_denied(scope, "agent access disabled by administrator")
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

        # A valid, non-expired, non-revoked credential that passed every gate
        # is legitimate agent activity for the inactivity timer; invalid or
        # expired tokens were rejected above and never reset it.
        if self.lifecycle is not None:
            self.lifecycle.record_activity("agent")
            self.lifecycle.begin_agent_request()
        if self.ip_access is not None:
            # Sampled (<=1/min/source) so an active agent cannot flood the
            # history; the row carries the authenticated identity.
            try:
                self.ip_access.record_attempt(
                    self._source_ip(scope),
                    listener="agent",
                    category=IPAccessMiddleware._category(scope.get("path", "")),
                    decision="allowed",
                    reason="authenticated agent request",
                    agent_id=principal.id,
                    sample=True,
                )
            except Exception:
                logger.debug("failed to record allowed IP attempt")
        try:
            _state(scope)["agent"] = principal
            await self.app(scope, receive, send)
        finally:
            if self.lifecycle is not None:
                self.lifecycle.end_agent_request()

    async def _auth_failure(self, send: Send, scope: Scope, reason: str) -> None:
        peer = client_ip(scope)
        allowed, retry_after = self.limiter.check_and_record(f"authfail:{peer}", limit=10, window_seconds=60)
        logger.warning("agent authentication failed from %s (%s)", peer, reason)
        if self.ip_access is not None:
            try:
                self.ip_access.record_attempt(
                    self._source_ip(scope),
                    listener="agent",
                    category="agent_auth",
                    decision="denied",
                    reason=reason,
                )
            except Exception:
                logger.debug("failed to record auth failure attempt")
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
