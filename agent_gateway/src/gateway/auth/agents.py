"""Agent identities, scopes, revocation, and token verification."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..errors import ConflictError, NotFoundError, ValidationFailure
from ..storage.db import Database
from ..utils import AGENT_NAME_RE, iso, parse_iso, utcnow
from .tokens import digest_matches, generate_agent_token, looks_like_token, token_digest, token_prefix

logger = logging.getLogger(__name__)

EXPIRING_SOON_DAYS = 7

ALLOWED_SCOPES = frozenset(
    {
        "ha:read",
        "ha:write",
        "proxmox:read",
        "proxmox:write",
        "ssh:read",
        "ssh:write",
        "audit:read",
        "journal:read",
        "journal:write",
    }
)

SCOPE_DESCRIPTIONS = {
    "ha:read": "Read Home Assistant entity state and configuration summaries",
    "ha:write": "Call Home Assistant services (policy and approval still apply)",
    "proxmox:read": "Read Proxmox nodes, VMs, containers, and task status",
    "proxmox:write": "Reserved for approval-gated Proxmox write tools (none in this release)",
    "ssh:read": "Run allowlisted service checks and read allowlisted logs over SSH",
    "ssh:write": "Run administrator-defined command templates over SSH",
    "audit:read": "Read the gateway audit log (administrator agents only)",
    "journal:read": "Read the Project Journal: project state, tasks, work history, test evidence",
    "journal:write": (
        "Write to the Project Journal: sessions, tasks, work entries, decisions, issues, "
        "test runs, handoffs (policy rules still apply; records are redacted and immutable)"
    ),
}


ALL_OPERATION_CLASSES = frozenset({"read", "write", "destructive"})
#: Classes an administrator may grant to a temporary session.  Destructive
#: operations are never issuable through the temporary-access broker: the
#: policy engine still has to allow the tool, and no destructive tool exists in
#: this release, but the session layer refuses the class outright.
TEMPORARY_OPERATION_CLASSES = frozenset({"read", "write"})
SESSION_MIN_TTL_SECONDS = 300
SESSION_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_EPOCH_KEY = "session_epoch"


@dataclass(frozen=True)
class AgentPrincipal:
    """Server-derived identity for one request.

    ``kind`` distinguishes long-lived administrator-managed identities
    (``persistent``) from gateway-issued temporary sessions (``temporary``).
    A temporary session carries its own connection allowlist and operation
    classes, so a token can never be used outside the scope it was issued for.
    Both values come from the database row, never from the request.
    """

    id: str
    name: str
    scopes: frozenset[str]
    kind: str = "persistent"
    operation_classes: frozenset[str] = ALL_OPERATION_CLASSES
    allowed_connections: frozenset[str] | None = None
    expires_at: str | None = None
    session_epoch: int = 0

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def allows_operation_class(self, risk: str) -> bool:
        return risk in self.operation_classes

    def allows_connection(self, connection_id: str) -> bool:
        # Persistent identities keep the historical behaviour: policy decides.
        if self.allowed_connections is None:
            return True
        return connection_id in self.allowed_connections


class AgentService:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ helpers

    def _row_to_public(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        enabled = bool(row["enabled"])
        expires_at = row["expires_at"]
        token_pref = row["token_prefix"]
        last_rotated_at = row["last_rotated_at"] if "last_rotated_at" in keys else row["created_at"]

        token_status = "active" if enabled else "revoked"
        days_until_expiry: int | None = None
        if expires_at:
            try:
                seconds_left = (parse_iso(str(expires_at)) - utcnow()).total_seconds()
                days_until_expiry = math.ceil(seconds_left / 86400)
                if seconds_left <= 0:
                    token_status = "expired"
                elif enabled and days_until_expiry <= EXPIRING_SOON_DAYS:
                    token_status = "expiring_soon"
            except ValueError:
                pass
        if not enabled and token_pref == "revoked":
            token_status = "revoked"
        if row["token_prefix"] == "pending" and token_status in {"active", "expiring_soon"}:
            token_status = "pending"
        if "revoked_at" in keys and row["revoked_at"]:
            token_status = "revoked"
        elif "kind" in keys and row["kind"] == "temporary" and token_status == "expired":
            token_status = "expired"

        remaining_seconds: int | None = None
        if expires_at:
            try:
                remaining_seconds = max(0, int((parse_iso(str(expires_at)) - utcnow()).total_seconds()))
            except ValueError:
                remaining_seconds = None

        kind = row["kind"] if "kind" in keys else "persistent"
        raw_connections = row["allowed_connections"] if "allowed_connections" in keys else None
        try:
            allowed_connections = None if raw_connections is None else json.loads(raw_connections)
        except (json.JSONDecodeError, TypeError):
            allowed_connections = None
        raw_classes = row["operation_classes"] if "operation_classes" in keys else None
        try:
            operation_classes = (
                sorted(json.loads(raw_classes)) if raw_classes else sorted(ALL_OPERATION_CLASSES)
            )
        except (json.JSONDecodeError, TypeError):
            operation_classes = sorted(ALL_OPERATION_CLASSES)

        return {
            "id": row["id"],
            "name": row["name"],
            "scopes": sorted(json.loads(row["scopes"])),
            "enabled": enabled,
            "token_prefix": token_pref,
            "token_status": token_status,
            "days_until_expiry": days_until_expiry,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_rotated_at": last_rotated_at or row["created_at"],
            "expires_at": expires_at,
            "last_used_at": row["last_used_at"],
            "notes": row["notes"],
            "kind": kind,
            "operation_classes": operation_classes,
            "allowed_connections": allowed_connections,
            "revoked_at": row["revoked_at"] if "revoked_at" in keys else None,
            "revoked_reason": row["revoked_reason"] if "revoked_reason" in keys else "",
            "created_by": row["created_by"] if "created_by" in keys else "",
            "issued_ttl_seconds": row["issued_ttl_seconds"] if "issued_ttl_seconds" in keys else None,
            "remaining_seconds": remaining_seconds,
        }

    def _validate_scopes(self, scopes: list[str]) -> list[str]:
        if not isinstance(scopes, list) or not scopes:
            raise ValidationFailure("at least one scope is required")
        unknown = set(scopes) - ALLOWED_SCOPES
        if unknown:
            raise ValidationFailure("agent contains an unknown scope")
        return sorted(set(scopes))

    def _validate_expiry(self, expires_at: str | None) -> str | None:
        if expires_at in (None, ""):
            return None
        try:
            parsed = parse_iso(expires_at)
        except ValueError as exc:
            raise ValidationFailure("expires_at must be an ISO-8601 timestamp") from exc
        if parsed <= utcnow():
            raise ValidationFailure("expires_at must be in the future")
        return iso(parsed)

    def create_agent(
        self,
        *,
        name: str,
        scopes: list[str],
        expires_at: str | None = None,
        notes: str = "",
    ) -> tuple[dict[str, Any], str]:
        if not AGENT_NAME_RE.fullmatch(name):
            raise ValidationFailure("agent name is invalid")
        valid_scopes = self._validate_scopes(scopes)
        expiry = self._validate_expiry(expires_at)
        if len(notes) > 500:
            raise ValidationFailure("agent notes are too long")
        token = generate_agent_token()
        now = iso()
        agent_id = uuid.uuid4().hex
        try:
            self.db.execute(
                "INSERT INTO agents(id, name, token_hash, token_prefix, scopes, enabled, created_at, "
                "updated_at, expires_at, last_used_at, last_rotated_at, notes) "
                "VALUES(?,?,?,?,?,1,?,?,?,NULL,?,?)",
                (
                    agent_id,
                    name,
                    token_digest(token),
                    token_prefix(token),
                    json.dumps(valid_scopes),
                    now,
                    now,
                    expiry,
                    now,
                    notes,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("an agent with that name already exists") from exc
        row = self.db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
        assert row is not None
        logger.info("agent created id=%s scopes=%s", agent_id, ",".join(valid_scopes))
        return self._row_to_public(row), token

    def list_agents(self) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM agents ORDER BY name COLLATE NOCASE")
        return [self._row_to_public(row) for row in rows]

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
        if row is None:
            raise NotFoundError("agent")
        return self._row_to_public(row)

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        scopes: list[str] | None = None,
        enabled: bool | None = None,
        expires_at: str | None = None,
        clear_expiry: bool = False,
        notes: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_agent(agent_id)
        if current.get("kind") == "temporary":
            raise ValidationFailure("temporary sessions cannot be edited; revoke it and issue a new session")
        new_name = name if name is not None else current["name"]
        if not AGENT_NAME_RE.fullmatch(new_name):
            raise ValidationFailure("agent name is invalid")
        new_scopes = self._validate_scopes(scopes) if scopes is not None else current["scopes"]
        new_expiry = (
            None
            if clear_expiry
            else (self._validate_expiry(expires_at) if expires_at else current["expires_at"])
        )
        new_enabled = current["enabled"] if enabled is None else bool(enabled)
        new_notes = current["notes"] if notes is None else notes
        if len(new_notes) > 500:
            raise ValidationFailure("agent notes are too long")
        try:
            self.db.execute(
                "UPDATE agents SET name=?, scopes=?, enabled=?, expires_at=?, notes=?, updated_at=? "
                "WHERE id=?",
                (
                    new_name,
                    json.dumps(new_scopes),
                    int(new_enabled),
                    new_expiry,
                    new_notes,
                    iso(),
                    agent_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("an agent with that name already exists") from exc
        return self.get_agent(agent_id)

    def revoke_agent(self, agent_id: str) -> dict[str, Any]:
        """Disable the agent and replace its token digest so the old token dies."""

        record = self.get_agent(agent_id)
        dead_digest = token_digest(generate_agent_token())
        now = iso()
        self.db.execute(
            "UPDATE agents SET enabled=0, token_hash=?, token_prefix='revoked', updated_at=?, "
            "revoked_at=COALESCE(revoked_at, ?), revoked_reason=CASE WHEN revoked_reason='' "
            "THEN ? ELSE revoked_reason END WHERE id=?",
            (dead_digest, now, now, "revoked by administrator", agent_id),
        )
        _ = record
        logger.info("agent revoked id=%s", agent_id)
        return self.get_agent(agent_id)

    def rotate_token(
        self,
        agent_id: str,
        *,
        expires_at: str | None = None,
        reenable: bool = True,
    ) -> tuple[dict[str, Any], str]:
        current = self.get_agent(agent_id)
        if current.get("kind") == "temporary":
            raise ValidationFailure("temporary sessions cannot be rotated in place; issue a new session")
        new_expiry = self._validate_expiry(expires_at) if expires_at is not None else current["expires_at"]
        token = generate_agent_token()
        now = iso()
        new_enabled = 1 if reenable else int(bool(current["enabled"]))
        self.db.execute(
            "UPDATE agents SET token_hash=?, token_prefix=?, enabled=?, expires_at=?, "
            "last_rotated_at=?, updated_at=? WHERE id=?",
            (
                token_digest(token),
                token_prefix(token),
                new_enabled,
                new_expiry,
                now,
                now,
                agent_id,
            ),
        )
        logger.info("agent token rotated id=%s", agent_id)
        return self.get_agent(agent_id), token

    def delete_agent(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        self.db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        logger.info("agent deleted id=%s", agent_id)

    # ------------------------------------------------------- authentication

    def current_session_epoch(self) -> int:
        row = self.db.query_one("SELECT value FROM settings WHERE key=?", (SESSION_EPOCH_KEY,))
        if row is None:
            return 0
        try:
            return int(json.loads(row["value"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            try:
                return int(str(row["value"]))
            except (TypeError, ValueError):
                return 0

    def bump_session_epoch(self) -> int:
        epoch = self.current_session_epoch() + 1
        self.db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (SESSION_EPOCH_KEY, json.dumps(epoch), iso()),
        )
        return epoch

    def _principal_from_row(self, row: sqlite3.Row) -> AgentPrincipal | None:
        """Validate a row and derive the server-side principal.

        Returns ``None`` for disabled, revoked, expired, or stale-epoch rows
        without revealing which condition applied.
        """

        if not row["enabled"]:
            return None
        keys = set(row.keys())
        if "revoked_at" in keys and row["revoked_at"]:
            return None
        expires_at = row["expires_at"]
        if expires_at:
            try:
                if parse_iso(str(expires_at)) <= utcnow():
                    return None
            except ValueError:
                return None
        kind = str(row["kind"]) if "kind" in keys else "persistent"
        epoch = int(row["session_epoch"]) if "session_epoch" in keys else 0
        if kind == "temporary" and epoch != self.current_session_epoch():
            # A restart policy revoked (or invalidated) this session; never
            # resurrect it from a stale record.
            return None
        try:
            scopes = frozenset(str(scope) for scope in json.loads(row["scopes"]))
        except (json.JSONDecodeError, TypeError):
            return None
        raw_classes = row["operation_classes"] if "operation_classes" in keys else None
        try:
            classes = (
                frozenset(str(item) for item in json.loads(raw_classes))
                if raw_classes
                else ALL_OPERATION_CLASSES
            )
        except (json.JSONDecodeError, TypeError):
            return None
        raw_connections = row["allowed_connections"] if "allowed_connections" in keys else None
        try:
            connections = (
                None
                if raw_connections is None
                else frozenset(str(item) for item in json.loads(raw_connections))
            )
        except (json.JSONDecodeError, TypeError):
            return None
        if kind == "temporary" and connections is None:
            # A temporary session without an explicit allowlist would be
            # unrestricted; fail closed instead.
            return None
        return AgentPrincipal(
            id=str(row["id"]),
            name=str(row["name"]),
            scopes=scopes,
            kind=kind,
            operation_classes=classes,
            allowed_connections=connections,
            expires_at=str(expires_at) if expires_at else None,
            session_epoch=epoch,
        )

    def authenticate(self, token: str) -> AgentPrincipal | None:
        """Resolve a bearer token to a principal.

        Returns ``None`` for missing, malformed, disabled, expired, revoked, or
        unknown tokens without revealing which condition applied.
        """

        if not looks_like_token(token):
            return None
        digest = token_digest(token)
        row = self.db.query_one("SELECT * FROM agents WHERE token_hash=?", (digest,))
        if row is None:
            return None
        if not digest_matches(row["token_hash"], token):
            return None

        now = iso()
        if row["last_used_at"] is None or (utcnow() - parse_iso(row["last_used_at"])).total_seconds() > 60:
            self.db.execute("UPDATE agents SET last_used_at=? WHERE id=?", (now, row["id"]))

        return self._principal_from_row(row)

    def get_principal(self, agent_id: str) -> AgentPrincipal:
        row = self.db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
        if row is None:
            raise NotFoundError("agent")
        principal = self._principal_from_row(row)
        if principal is None:
            raise NotFoundError("agent")
        return principal

    # ---------------------------------------------------- temporary sessions

    def create_session_record(
        self,
        *,
        name: str,
        scopes: list[str],
        connection_ids: list[str],
        operation_classes: list[str],
        ttl_seconds: int,
        created_by: str,
        notes: str = "",
        now: Any | None = None,
    ) -> dict[str, Any]:
        """Insert one temporary session row and return ``(public, token)``.

        The operator-facing policy document is written by the caller in the
        same database transaction, so a session never exists without an
        allow-by-default-nothing policy.
        """

        if not AGENT_NAME_RE.fullmatch(name):
            raise ValidationFailure("session name is invalid")
        valid_scopes = self._validate_scopes(scopes)
        valid_classes = sorted(set(operation_classes))
        if not valid_classes or set(valid_classes) - TEMPORARY_OPERATION_CLASSES:
            raise ValidationFailure("session operation classes must be a subset of read/write")
        if "read" not in valid_classes:
            raise ValidationFailure("a temporary session must include the read operation class")
        if not SESSION_MIN_TTL_SECONDS <= ttl_seconds <= SESSION_MAX_TTL_SECONDS:
            raise ValidationFailure("session lifetime is outside the allowed range")
        connections = sorted({str(item) for item in connection_ids})
        if len(connections) > 50:
            raise ValidationFailure("too many connections authorized for one session")
        if len(notes) > 500:
            raise ValidationFailure("session notes are too long")

        issued_at = now or utcnow()
        agent_id = uuid.uuid4().hex
        expires_at = iso(issued_at + timedelta(seconds=ttl_seconds))
        # The session has no usable credential until the agent redeems its
        # single-use handoff code.  This placeholder digest can never match a
        # real token (the preimage is discarded immediately).
        placeholder = token_digest(generate_agent_token())
        epoch = self.current_session_epoch()
        payload = (
            agent_id,
            name,
            placeholder,
            "pending",
            json.dumps(valid_scopes),
            iso(issued_at),
            iso(issued_at),
            expires_at,
            iso(issued_at),
            notes,
            "temporary",
            json.dumps(connections),
            json.dumps(valid_classes),
            created_by,
            epoch,
            ttl_seconds,
        )
        try:
            self.db.execute(
                "INSERT INTO agents(id, name, token_hash, token_prefix, scopes, enabled, created_at, "
                "updated_at, expires_at, last_used_at, last_rotated_at, notes, kind, "
                "allowed_connections, operation_classes, created_by, session_epoch, issued_ttl_seconds) "
                "VALUES(?,?,?,?,?,1,?,?,?,NULL,?,?,?,?,?,?,?,?)",
                payload,
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("a session with that name already exists") from exc
        row = self.db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
        assert row is not None
        logger.info(
            "temporary session created id=%s scopes=%s connections=%d classes=%s ttl=%ds by=%s",
            agent_id,
            ",".join(valid_scopes),
            len(connections),
            ",".join(valid_classes),
            ttl_seconds,
            created_by,
        )
        return self._row_to_public(row)

    def mint_session_token(self, agent_id: str) -> str:
        """Create the bearer token for a pending session; returned once.

        Called by the broker after a handoff code has been atomically consumed,
        so the plaintext token exists only in that HTTP response.
        """

        row = self.db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
        if row is None:
            raise NotFoundError("session")
        if row["kind"] != "temporary":
            raise ValidationFailure("only temporary sessions can be activated")
        if not row["enabled"] or row["revoked_at"]:
            raise ValidationFailure("session is no longer active")
        if row["token_prefix"] != "pending":
            raise ValidationFailure("session credential has already been issued")
        token = generate_agent_token()
        now = iso()
        self.db.execute(
            "UPDATE agents SET token_hash=?, token_prefix=?, updated_at=?, last_rotated_at=? "
            "WHERE id=? AND token_prefix='pending'",
            (token_digest(token), token_prefix(token), now, now, agent_id),
        )
        self.db.execute("UPDATE agents SET last_used_at=NULL WHERE id=?", (agent_id,))
        logger.info("temporary session credential issued id=%s", agent_id)
        return token

    def revoke_session(self, agent_id: str, *, reason: str) -> dict[str, Any]:
        record = self.get_agent(agent_id)
        if record.get("kind") != "temporary":
            raise ValidationFailure("only temporary sessions can be revoked this way")
        dead_digest = token_digest(generate_agent_token())
        now = iso()
        self.db.execute(
            "UPDATE agents SET enabled=0, token_hash=?, token_prefix='revoked', updated_at=?, "
            "revoked_at=COALESCE(revoked_at, ?), revoked_reason=? WHERE id=?",
            (dead_digest, now, now, reason[:200], agent_id),
        )
        logger.info("temporary session revoked id=%s reason=%s", agent_id, reason)
        return self.get_agent(agent_id)

    def revoke_all_temporary(self, *, reason: str) -> list[str]:
        """Revoke every temporary session; returns the affected ids."""

        rows = self.db.query_all(
            "SELECT id FROM agents WHERE kind='temporary' AND (revoked_at IS NULL OR enabled=1)"
        )
        ids = [str(row["id"]) for row in rows]
        if not ids:
            return []
        now = iso()
        for agent_id in ids:
            dead_digest = token_digest(generate_agent_token())
            self.db.execute(
                "UPDATE agents SET enabled=0, token_hash=?, token_prefix='revoked', updated_at=?, "
                "revoked_at=COALESCE(revoked_at, ?), revoked_reason=? WHERE id=?",
                (dead_digest, now, now, reason[:200], agent_id),
            )
        logger.info("revoked %d temporary session(s) reason=%s", len(ids), reason)
        return ids

    def list_by_kind(self, kind: str) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM agents WHERE kind=? ORDER BY created_at DESC", (kind,))
        return [self._row_to_public(row) for row in rows]
