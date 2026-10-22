"""Agent identities, scopes, revocation, and token verification."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import uuid
from dataclasses import dataclass
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


@dataclass(frozen=True)
class AgentPrincipal:
    id: str
    name: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


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
                remaining_seconds = (parse_iso(str(expires_at)) - utcnow()).total_seconds()
                days_until_expiry = math.ceil(remaining_seconds / 86400)
                if remaining_seconds <= 0:
                    token_status = "expired"
                elif enabled and days_until_expiry <= EXPIRING_SOON_DAYS:
                    token_status = "expiring_soon"
            except ValueError:
                pass
        if not enabled and token_pref == "revoked":
            token_status = "revoked"

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

        self.get_agent(agent_id)
        dead_digest = token_digest(generate_agent_token())
        self.db.execute(
            "UPDATE agents SET enabled=0, token_hash=?, token_prefix='revoked', updated_at=? WHERE id=?",
            (dead_digest, iso(), agent_id),
        )
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
        if not row["enabled"]:
            return None
        if row["expires_at"]:
            try:
                if parse_iso(row["expires_at"]) <= utcnow():
                    return None
            except ValueError:
                return None

        now = iso()
        if row["last_used_at"] is None or (utcnow() - parse_iso(row["last_used_at"])).total_seconds() > 60:
            self.db.execute("UPDATE agents SET last_used_at=? WHERE id=?", (now, row["id"]))

        scopes = frozenset(str(scope) for scope in json.loads(row["scopes"]))
        return AgentPrincipal(id=row["id"], name=row["name"], scopes=scopes)

    def get_principal(self, agent_id: str) -> AgentPrincipal:
        row = self.db.query_one("SELECT * FROM agents WHERE id=? AND enabled=1", (agent_id,))
        if row is None:
            raise NotFoundError("agent")
        return AgentPrincipal(
            id=row["id"],
            name=row["name"],
            scopes=frozenset(str(scope) for scope in json.loads(row["scopes"])),
        )
