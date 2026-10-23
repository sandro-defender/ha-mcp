"""Temporary agent-access broker.

Layer A (upstream credentials) lives in :mod:`gateway.vault` and is only ever
read by connectors inside the gateway.  Layer B (agent credentials) lives here:
a temporary session is created first, and the bearer token is minted only when
the agent redeems a short-lived, single-use handoff code.  Consequently:

* no plaintext token exists in the database, in logs, or in a URL;
* the handoff link carries a code (never the credential) in the URL fragment,
  so it is not sent to the server, to a referrer, or to an access log;
* the credential is displayed exactly once, on the agent page, after redemption;
* every request validates the database row: enabled, not revoked, not expired,
  and (for temporary sessions) the restart epoch and the connection/operation
  allowlist chosen by the administrator;
* restart-based revocation never touches upstream credentials and never
  resurrects a stale token, because the epoch is persisted and checked.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
from datetime import timedelta
from typing import Any

from .approvals.service import ApprovalService
from .audit.service import AuditService
from .auth.agents import (
    ALLOWED_SCOPES,
    SCOPE_DESCRIPTIONS,
    SESSION_MAX_TTL_SECONDS,
    SESSION_MIN_TTL_SECONDS,
    AgentService,
)
from .auth.tokens import token_digest
from .connections import ConnectionService
from .errors import NotFoundError, ValidationFailure
from .journal.models import READ_TOOLS as JOURNAL_READ_TOOLS
from .journal.models import WRITE_TOOLS as JOURNAL_WRITE_TOOLS
from .policy.engine import PolicyService
from .settings import Settings
from .storage.db import Database, SettingsStore
from .tools.registry import ToolRegistry
from .utils import iso, parse_iso, utcnow

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600
DEFAULT_MAX_TTL_SECONDS = 86400
HARD_MAX_TTL_SECONDS = SESSION_MAX_TTL_SECONDS
MIN_TTL_SECONDS = SESSION_MIN_TTL_SECONDS
MAX_SESSIONS_LISTED = 100
MCP_PATH = "/mcp"
API_PREFIX = "/api/v1"
HANDOFF_PREFIX = "hag_h_"
HANDOFF_TTL_SECONDS = 900
HANDOFF_MAX_TTL_SECONDS = 3600
REDEEM_FAILURE_LIMIT = 10
REDEEM_FAILURE_WINDOW_SECONDS = 60


def _normalize_base_url(value: str) -> str:
    """Validate an administrator-supplied externally reachable gateway URL."""

    if not isinstance(value, str) or len(value) > 300:
        raise ValidationFailure("agent API base URL is invalid")
    candidate = value.strip().rstrip("/")
    if not candidate:
        return ""
    from urllib.parse import urlsplit

    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"}:
        raise ValidationFailure("agent API base URL must use http or https")
    if not parts.hostname:
        raise ValidationFailure("agent API base URL must include a host")
    if parts.username or parts.password:
        raise ValidationFailure("agent API base URL must not contain credentials")
    if parts.query or parts.fragment:
        raise ValidationFailure("agent API base URL must not contain a query or fragment")
    if parts.path not in {"", "/"}:
        raise ValidationFailure("agent API base URL must not contain a path")
    return candidate


class AgentAccessService:
    """Issue, redeem, list, and revoke temporary agent sessions.

    The service never returns an upstream secret: it only knows connection
    identifiers, names, and types.
    """

    def __init__(
        self,
        *,
        db: Database,
        settings_store: SettingsStore,
        agents: AgentService,
        policies: PolicyService,
        connections: ConnectionService,
        approvals: ApprovalService,
        audit: AuditService,
        tools: ToolRegistry,
        settings: Settings,
    ) -> None:
        self.db = db
        self.settings_store = settings_store
        self.agents = agents
        self.policies = policies
        self.connections = connections
        self.approvals = approvals
        self.audit = audit
        self.tools = tools
        self.settings = settings
        # Serializes issuance so two concurrent "generate access" requests
        # cannot interleave the revoke-previous step and leave two live
        # privileged sessions behind.
        self._issue_lock = threading.Lock()
        self._last_reachability: dict[str, Any] | None = None

    # ------------------------------------------------------------- settings

    def default_ttl_seconds(self) -> int:
        value = self.settings_store.get_int("agent_access_default_ttl_seconds")
        return min(max(value, MIN_TTL_SECONDS), self.max_ttl_seconds())

    def max_ttl_seconds(self) -> int:
        value = self.settings_store.get_int("agent_access_max_ttl_seconds")
        return min(max(value, MIN_TTL_SECONDS), HARD_MAX_TTL_SECONDS)

    def revoke_on_restart(self) -> bool:
        return self.settings_store.get_bool("agent_sessions_revoke_on_restart")

    def base_url(self) -> str:
        value = self.settings_store.get("agent_api_base_url")
        return str(value) if isinstance(value, str) else ""

    def effective_settings(self) -> dict[str, Any]:
        return {
            "default_ttl_seconds": self.default_ttl_seconds(),
            "max_ttl_seconds": self.max_ttl_seconds(),
            "min_ttl_seconds": MIN_TTL_SECONDS,
            "hard_max_ttl_seconds": HARD_MAX_TTL_SECONDS,
            "handoff_ttl_seconds": HANDOFF_TTL_SECONDS,
            "revoke_on_restart": self.revoke_on_restart(),
            "agent_api_base_url": self.base_url(),
            "mcp_path": MCP_PATH,
            "api_prefix": API_PREFIX,
            "agent_page_path": "/agent",
            "transport": "streamable-http",
            "listener": {
                "host": self.settings.hostname,
                "port": self.settings.agent_port,
                "tls": self.settings.tls_enabled,
            },
        }

    def update_settings(
        self,
        *,
        default_ttl_seconds: int | None = None,
        max_ttl_seconds: int | None = None,
        revoke_on_restart: bool | None = None,
        agent_api_base_url: str | None = None,
        actor_id: str = "",
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if max_ttl_seconds is not None:
            if not MIN_TTL_SECONDS <= max_ttl_seconds <= HARD_MAX_TTL_SECONDS:
                raise ValidationFailure(
                    f"maximum session lifetime must be between {MIN_TTL_SECONDS} and "
                    f"{HARD_MAX_TTL_SECONDS} seconds"
                )
            changes["agent_access_max_ttl_seconds"] = max_ttl_seconds
        if default_ttl_seconds is not None:
            upper = int(changes.get("agent_access_max_ttl_seconds", self.max_ttl_seconds()))
            if not MIN_TTL_SECONDS <= default_ttl_seconds <= upper:
                raise ValidationFailure(
                    f"default session lifetime must be between {MIN_TTL_SECONDS} and {upper} seconds"
                )
            changes["agent_access_default_ttl_seconds"] = default_ttl_seconds
        if revoke_on_restart is not None:
            changes["agent_sessions_revoke_on_restart"] = bool(revoke_on_restart)
        if agent_api_base_url is not None:
            changes["agent_api_base_url"] = _normalize_base_url(agent_api_base_url)
        if not changes:
            raise ValidationFailure("no agent-access settings were provided")
        for key, value in changes.items():
            self.settings_store.set(key, value)
        self.audit.admin_action(
            admin_id=actor_id or "unknown",
            action="agent_access.settings_update",
            params={key: value for key, value in changes.items() if key != "agent_api_base_url"}
            | {"agent_api_base_url_set": bool(changes.get("agent_api_base_url"))},
        )
        return self.effective_settings()

    # -------------------------------------------------------------- overview

    def session_public(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return the agent-safe projection of a temporary session."""

        remaining = record.get("remaining_seconds")
        handoff = self._active_handoff(record["id"])
        return {
            "id": record["id"],
            "name": record["name"],
            "kind": record["kind"],
            "status": record["token_status"],
            "enabled": record["enabled"],
            "token_prefix": record["token_prefix"],
            "scopes": record["scopes"],
            "operation_classes": record["operation_classes"],
            "allowed_connection_ids": record["allowed_connections"] or [],
            "created_at": record["created_at"],
            "created_by": record["created_by"],
            "expires_at": record["expires_at"],
            "remaining_seconds": remaining,
            "last_used_at": record["last_used_at"],
            "revoked_at": record.get("revoked_at"),
            "revoked_reason": record.get("revoked_reason", ""),
            "notes": record["notes"],
            "issued_ttl_seconds": record.get("issued_ttl_seconds"),
            "handoff_pending": handoff is not None,
            "handoff_expires_at": handoff["expires_at"] if handoff else None,
        }

    def _active_handoff(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT id, expires_at, created_at FROM session_handoffs "
            "WHERE session_id=? AND used_at IS NULL AND expires_at > ? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id, iso()),
        )
        if row is None:
            return None
        return {"id": row["id"], "expires_at": row["expires_at"], "created_at": row["created_at"]}

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = [self.session_public(record) for record in self.agents.list_by_kind("temporary")]
        connections = {item["id"]: item for item in self.connections.list_connections()}
        for session in sessions:
            session["connections"] = [
                {
                    "id": connection_id,
                    "name": connections.get(connection_id, {}).get("name", "deleted connection"),
                    "type": connections.get(connection_id, {}).get("type"),
                    "status": connections.get(connection_id, {}).get("status"),
                }
                for connection_id in session["allowed_connection_ids"]
            ]
            session["requests_last_hour"] = self._requests_last_hour(session["id"])
        return sessions

    def _requests_last_hour(self, agent_id: str) -> int:
        cutoff = iso(utcnow() - timedelta(hours=1))
        row = self.db.query_one(
            "SELECT COUNT(*) AS count FROM audit WHERE agent_id=? AND ts >= ?", (agent_id, cutoff)
        )
        return int(row["count"]) if row else 0

    def overview(self) -> dict[str, Any]:
        sessions = self.list_sessions()
        active = [session for session in sessions if session["status"] in {"active", "expiring_soon"}]
        return {
            "settings": self.effective_settings(),
            "sessions": sessions[:MAX_SESSIONS_LISTED],
            "active_count": len(active),
            "pending_count": sum(1 for session in sessions if session["status"] == "pending"),
            "connections": self.connections.list_connections(),
            "scopes": [
                {"id": scope, "description": SCOPE_DESCRIPTIONS.get(scope, "")}
                for scope in sorted(ALLOWED_SCOPES)
            ],
            "last_reachability": self._last_reachability,
            "security_notes": [
                "Session tokens are stored as SHA-256 digests; the plaintext token is shown once.",
                "The handoff link carries a single-use code, never the session token.",
                "Upstream credentials never leave the encrypted vault and are never sent to agents.",
                "Write operations still require a human approval bound to the exact request.",
                "Destructive operation classes cannot be granted to a temporary session.",
            ],
        }

    # --------------------------------------------------------------- issuing

    def _selected_connections(self, connection_ids: list[str]) -> list[dict[str, Any]]:
        available = {item["id"]: item for item in self.connections.list_connections()}
        selected: list[dict[str, Any]] = []
        for connection_id in sorted({str(item) for item in connection_ids}):
            item = available.get(connection_id)
            if item is None:
                raise ValidationFailure("a selected connection does not exist")
            selected.append(item)
        return selected

    def _validate_scope_selection(
        self, scopes: list[str], connections: list[dict[str, Any]], operation_classes: list[str]
    ) -> None:
        unknown = set(scopes) - ALLOWED_SCOPES
        if unknown:
            raise ValidationFailure("session contains an unknown scope")
        if not scopes:
            raise ValidationFailure("select at least one scope")
        classes = set(operation_classes)
        if not classes or classes - {"read", "write"}:
            raise ValidationFailure("only read and write operation classes can be granted")
        if "read" not in classes:
            raise ValidationFailure("the read operation class is required")
        types = {connection["type"] for connection in connections}
        by_scope = {
            "ha:read": "home_assistant",
            "ha:write": "home_assistant",
            "proxmox:read": "proxmox",
            "proxmox:write": "proxmox",
            "ssh:read": "ssh",
            "ssh:write": "ssh",
        }
        for scope in scopes:
            expected = by_scope.get(scope)
            if expected and expected not in types:
                raise ValidationFailure(f"scope {scope} requires a {expected} connection")
        if not connections and set(scopes) - {"journal:read", "journal:write"}:
            raise ValidationFailure("select at least one connection for connection-scoped access")
        if "write" in classes:
            write_scopes = {"ha:write", "proxmox:write", "ssh:write", "journal:write"}
            if not set(scopes) & write_scopes:
                raise ValidationFailure("the write operation class requires a write scope")
        if any(scope.endswith(":write") for scope in scopes) and "write" not in classes:
            raise ValidationFailure("a write scope requires the write operation class")

    def _policy_document(
        self,
        *,
        scopes: list[str],
        connections: list[dict[str, Any]],
        operation_classes: list[str],
    ) -> dict[str, Any]:
        """Build the server-side policy for a session.

        Only tools needed by the selected connections, scopes, and operation
        classes are allowed.  Every write tool keeps ``require_approval`` set,
        and the operation pipeline forces approval for non-read risk anyway.
        Journal tools are not approval-gated by design; they are evaluated with
        read risk, so the rule must not set ``require_approval``.
        """

        classes = set(operation_classes)
        scope_set = set(scopes)
        rules: list[dict[str, Any]] = []
        for connection_type, scope_prefix in (
            ("home_assistant", "ha"),
            ("proxmox", "proxmox"),
            ("ssh", "ssh"),
        ):
            selected = [c["id"] for c in connections if c["type"] == connection_type]
            if not selected:
                continue
            specs = [spec for spec in self.tools.all_specs() if spec.group == connection_type]
            read_tools = sorted(spec.name for spec in specs if spec.risk == "read")
            write_tools = sorted(spec.name for spec in specs if spec.risk == "write")
            if f"{scope_prefix}:read" in scope_set and read_tools:
                rules.append(
                    {
                        "id": f"session-{scope_prefix}-read",
                        "effect": "allow",
                        "tools": read_tools,
                        "connections": selected,
                        "constraints": {},
                        "require_approval": False,
                    }
                )
            if f"{scope_prefix}:write" in scope_set and "write" in classes and write_tools:
                rules.append(
                    {
                        "id": f"session-{scope_prefix}-write",
                        "effect": "allow",
                        "tools": write_tools,
                        "connections": selected,
                        "constraints": {},
                        "require_approval": True,
                        "limits": {"rate_limit_per_minute": 10},
                    }
                )

        journal_tools: list[str] = []
        if "journal:read" in scope_set:
            journal_tools.extend(sorted(JOURNAL_READ_TOOLS))
        if "journal:write" in scope_set and "write" in classes:
            journal_tools.extend(sorted(JOURNAL_WRITE_TOOLS))
        if journal_tools:
            rules.append(
                {
                    "id": "session-journal",
                    "effect": "allow",
                    "tools": sorted(set(journal_tools)),
                    "connections": ["*"],
                    "constraints": {},
                    "require_approval": False,
                }
            )
        return {"version": 1, "rules": rules}

    def _create_handoff(self, session_id: str, ttl_seconds: int) -> tuple[str, str]:
        code = HANDOFF_PREFIX + secrets.token_urlsafe(32)
        created = utcnow()
        lifetime = max(60, min(HANDOFF_TTL_SECONDS, ttl_seconds))
        expires_at = iso(created + timedelta(seconds=lifetime))
        self.db.execute(
            "INSERT INTO session_handoffs(id, session_id, code_hash, created_at, expires_at) "
            "VALUES(?,?,?,?,?)",
            (secrets.token_hex(16), session_id, token_digest(code), iso(created), expires_at),
        )
        return code, expires_at

    def handoff_url(self, code: str, base_url: str = "") -> str:
        base = (base_url or self.base_url()).rstrip("/")
        suffix = f"/agent#code={code}"
        return f"{base}{suffix}" if base else suffix

    def purge_handoffs(self, session_ids: list[str] | None = None) -> None:
        if session_ids:
            for session_id in session_ids:
                self.db.execute("DELETE FROM session_handoffs WHERE session_id=?", (session_id,))
        else:
            self.db.execute(
                "DELETE FROM session_handoffs "
                "WHERE session_id IN (SELECT id FROM agents WHERE kind='temporary')"
            )
        self.db.execute("DELETE FROM session_handoffs WHERE expires_at <= ?", (iso(),))

    def issue(
        self,
        *,
        name: str,
        scopes: list[str],
        connection_ids: list[str],
        operation_classes: list[str],
        ttl_seconds: int | None = None,
        revoke_previous: bool = True,
        notes: str = "",
        created_by: str,
        base_url: str = "",
    ) -> dict[str, Any]:
        """Create a temporary session and its single-use handoff link.

        The response never contains a bearer token: the agent receives it only
        after redeeming the handoff code.
        """

        connections = self._selected_connections(connection_ids)
        self._validate_scope_selection(scopes, connections, operation_classes)
        lifetime = self.default_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
        if not MIN_TTL_SECONDS <= lifetime <= self.max_ttl_seconds():
            raise ValidationFailure(
                f"session lifetime must be between {MIN_TTL_SECONDS} and {self.max_ttl_seconds()} seconds"
            )
        document = self._policy_document(
            scopes=scopes, connections=connections, operation_classes=operation_classes
        )
        if not document["rules"]:
            raise ValidationFailure("the selected scopes do not authorize any tool")

        with self._issue_lock, self.db.transaction():
            if revoke_previous:
                revoked = self.agents.revoke_all_temporary(reason="superseded by a newly issued session")
                self._expire_pending_approvals(revoked, reason="session superseded")
                self.purge_handoffs(revoked)
            record = self.agents.create_session_record(
                name=name,
                scopes=scopes,
                connection_ids=[item["id"] for item in connections],
                operation_classes=operation_classes,
                ttl_seconds=lifetime,
                created_by=created_by,
                notes=notes,
            )
            self.policies.set_policy(record["id"], document)
            code, handoff_expires_at = self._create_handoff(record["id"], lifetime)

        public = self.session_public(self.agents.get_agent(record["id"]))
        config = self.connection_config(base_url=base_url)
        self.audit.admin_action(
            admin_id=created_by,
            action="agent_session.create",
            params={
                "session_id": record["id"],
                "name": record["name"],
                "scopes": record["scopes"],
                "operation_classes": record["operation_classes"],
                "connection_ids": record["allowed_connections"],
                "ttl_seconds": lifetime,
                "revoke_previous": revoke_previous,
                "handoff_expires_at": handoff_expires_at,
            },
            status="ok",
        )
        return {
            "session": public,
            "handoff": {
                "url": self.handoff_url(code, base_url),
                "expires_at": handoff_expires_at,
                "single_use": True,
            },
            "connection_config": config,
            "policy": self.policies.get_policy(record["id"]),
            "settings": self.effective_settings(),
        }

    # -------------------------------------------------------------- redeem

    def redeem(self, code: str, *, peer: str = "") -> dict[str, Any]:
        """Exchange a handoff code for the session bearer token (single use)."""

        if not code.startswith(HANDOFF_PREFIX) or len(code) > 200:
            raise ValidationFailure("handoff code is invalid or expired")
        digest = token_digest(code)
        with self.db.transaction():
            row = self.db.query_one("SELECT * FROM session_handoffs WHERE code_hash=?", (digest,))
            if row is None or not hmac.compare_digest(str(row["code_hash"]), digest):
                raise ValidationFailure("handoff code is invalid or expired")
            if row["used_at"] is not None:
                raise ValidationFailure("handoff code is invalid or expired")
            if parse_iso(str(row["expires_at"])) <= utcnow():
                raise ValidationFailure("handoff code is invalid or expired")
            session = self.agents.get_agent(str(row["session_id"]))
            if session["kind"] != "temporary" or session["token_status"] not in {"pending"}:
                raise ValidationFailure("handoff code is invalid or expired")
            consumed = self.db.execute_rowcount(
                "UPDATE session_handoffs SET used_at=? WHERE id=? AND used_at IS NULL",
                (iso(), row["id"]),
            )
            if consumed != 1:
                raise ValidationFailure("handoff code is invalid or expired")
            token = self.agents.mint_session_token(str(row["session_id"]))

        record = self.agents.get_agent(str(row["session_id"]))
        connections = {item["id"]: item for item in self.connections.list_connections()}
        config = self.connection_config(base_url="")
        self.audit.record(
            actor_type="peer",
            actor_id=peer or "unknown",
            agent_id=record["id"],
            tool="agent_session.redeem",
            decision="allow",
            status="succeeded",
            reason="session credential delivered once",
        )
        logger.info("temporary session redeemed id=%s", record["id"])
        session = self.session_public(record)
        session["connections"] = [
            {
                "id": connection_id,
                "name": connections.get(connection_id, {}).get("name", "deleted connection"),
                "type": connections.get(connection_id, {}).get("type"),
            }
            for connection_id in record["allowed_connections"] or []
        ]
        return {
            "session": session,
            "token": token,
            "token_shown_once": True,
            "connection_config": config,
            "connections": [
                {
                    "id": connection_id,
                    "name": connections.get(connection_id, {}).get("name", "deleted connection"),
                    "type": connections.get(connection_id, {}).get("type"),
                }
                for connection_id in record["allowed_connections"] or []
            ],
        }

    # ------------------------------------------------------------ revocation

    def revoke(
        self, session_id: str, *, actor_id: str, reason: str = "revoked by administrator"
    ) -> dict[str, Any]:
        record = self.agents.get_agent(session_id)
        if record.get("kind") != "temporary":
            raise ValidationFailure("only temporary sessions can be revoked here")
        if record["token_status"] == "revoked":
            return self.session_public(record)
        with self.db.transaction():
            updated = self.agents.revoke_session(session_id, reason=reason)
            self._expire_pending_approvals([session_id], reason="session revoked")
            self.purge_handoffs([session_id])
        self.audit.admin_action(
            admin_id=actor_id,
            action="agent_session.revoke",
            params={"session_id": session_id, "name": updated["name"], "reason": reason},
        )
        return self.session_public(self.agents.get_agent(session_id))

    def revoke_all(
        self, *, actor_id: str, reason: str = "all agent sessions revoked by administrator"
    ) -> dict[str, Any]:
        with self.db.transaction():
            revoked = self.agents.revoke_all_temporary(reason=reason)
            self._expire_pending_approvals(revoked, reason="all agent sessions revoked")
            self.purge_handoffs(revoked)
        self.audit.admin_action(
            admin_id=actor_id,
            action="agent_session.revoke_all",
            params={"revoked_count": len(revoked), "session_ids": revoked, "reason": reason},
        )
        return {"revoked": len(revoked), "session_ids": revoked}

    def _expire_pending_approvals(self, agent_ids: list[str], *, reason: str) -> None:
        for agent_id in agent_ids:
            self.db.execute(
                "UPDATE approvals SET status='expired', reason=? WHERE agent_id=? AND status='pending'",
                (reason[:200], agent_id),
            )

    def activity(self, session_id: str, *, limit: int = 50) -> dict[str, Any]:
        record = self.agents.get_agent(session_id)
        if record.get("kind") != "temporary":
            raise NotFoundError("session")
        rows = self.db.query_all(
            "SELECT ts, tool, decision, status, reason, duration_ms FROM audit "
            "WHERE agent_id=? ORDER BY id DESC LIMIT ?",
            (session_id, max(1, min(limit, 200))),
        )
        return {
            "session_id": session_id,
            "items": [
                {
                    "ts": row["ts"],
                    "tool": row["tool"],
                    "decision": row["decision"],
                    "status": row["status"],
                    "reason": row["reason"],
                    "duration_ms": row["duration_ms"],
                }
                for row in rows
            ],
        }

    # -------------------------------------------------------------- restart

    def apply_startup_policy(self) -> dict[str, Any]:
        """Apply the configured restart policy before the listeners start.

        With ``agent_sessions_revoke_on_restart`` on, every temporary session is
        revoked and the session epoch is bumped.  Both are persisted, so a
        previously issued token is rejected immediately and cannot be
        resurrected by a stale row or by an in-memory cache.  Upstream
        credentials, audit records, and administrator-managed identities are
        untouched.
        """

        if not self.revoke_on_restart():
            self.purge_handoffs([])
            return {
                "policy": "retain",
                "revoked": 0,
                "session_epoch": self.agents.current_session_epoch(),
            }
        with self.db.transaction():
            revoked = self.agents.revoke_all_temporary(reason="gateway restarted")
            self._expire_pending_approvals(revoked, reason="gateway restarted")
            self.purge_handoffs([])
            epoch = self.agents.bump_session_epoch()
        self.audit.record(
            actor_type="system",
            actor_id="gateway",
            tool="agent_session.revoke_on_restart",
            decision="revoke",
            status="succeeded",
            reason=f"revoked {len(revoked)} temporary session(s) at startup",
        )
        logger.info(
            "restart policy revoked %d temporary session(s); session epoch is now %d",
            len(revoked),
            epoch,
        )
        return {"policy": "revoke_on_restart", "revoked": len(revoked), "session_epoch": epoch}

    # ------------------------------------------------------------ reachability

    def record_reachability(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._last_reachability = {"checked_at": iso(), **payload}
        return self._last_reachability

    def connection_config(self, *, base_url: str = "") -> dict[str, Any]:
        """Connection instructions.

        The credential is deliberately absent: it is delivered only on the
        agent page after redemption.
        """

        base = (base_url or self.base_url()).rstrip("/")
        placeholder = base or "http://<gateway-host>:<agent-port>"
        return {
            "gateway_base_url": base or None,
            "agent_page_url": f"{base}/agent" if base else "/agent",
            "api_base": f"{placeholder}{API_PREFIX}",
            "mcp_url": f"{placeholder}{MCP_PATH}",
            "transport": "streamable-http",
            "auth": {"header": "Authorization", "scheme": "Bearer", "token_source": "agent page"},
            "token_placeholder": "<session token from the agent page>",
            "notes": [
                "The session token is delivered once, on the agent page, after the handoff code is redeemed.",
                "Send the token in the Authorization header, never in a query string.",
                "The MCP endpoint is stateless Streamable HTTP and requires the same bearer token.",
                "Do not commit the token to a repository or paste it into shell history.",
            ],
        }

    # -------------------------------------------------------------- helpers

    def session_expiry(self, ttl_seconds: int) -> str:
        return iso(utcnow() + timedelta(seconds=ttl_seconds))

    def _remaining_seconds(self, expires_at: str | None) -> int | None:
        if not expires_at:
            return None
        try:
            return max(0, int((parse_iso(expires_at) - utcnow()).total_seconds()))
        except ValueError:
            return None


def json_list(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []
