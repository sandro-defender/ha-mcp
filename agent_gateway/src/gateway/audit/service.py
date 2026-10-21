"""Redacted audit log."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ..redaction import MASK, redact_value, summarize
from ..storage.db import Database
from ..utils import SAFE_ERROR_CODE_RE, iso, parse_iso, utcnow

logger = logging.getLogger(__name__)

MAX_PARAMS_SUMMARY = 1600
MAX_REASON = 300

# Administrative audit actions never need the credential payload itself.  These
# keys are dropped even when a caller forgets and passes the whole request
# body, so a future endpoint cannot start leaking secrets into the audit log.
ADMIN_SECRET_PARAM_KEYS = frozenset(
    {"value", "secret", "token", "token_secret", "password", "passphrase", "private_key", "vault_base64"}
)


def _safe_admin_params(params: Any) -> Any:
    if not isinstance(params, Mapping):
        return params
    return {
        key: (MASK if str(key).strip().lower() in ADMIN_SECRET_PARAM_KEYS else value)
        for key, value in params.items()
    }


class AuditService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        *,
        actor_type: str,
        actor_id: str,
        decision: str,
        status: str,
        agent_id: str | None = None,
        connection_id: str | None = None,
        tool: str | None = None,
        params: Any | None = None,
        reason: str = "",
        approval_id: str | None = None,
        duration_ms: int | None = None,
        error_category: str | None = None,
        request_id: str | None = None,
        params_summary: str | None = None,
    ) -> None:
        """Insert an audit row.  Callers must pass summaries, never secrets."""

        if actor_type not in {"agent", "admin", "system"}:
            actor_type = "system"
        summary = params_summary if params_summary is not None else summarize(params)
        safe_error = (
            error_category if error_category and SAFE_ERROR_CODE_RE.fullmatch(error_category) else None
        )
        try:
            self.db.execute(
                "INSERT INTO audit(ts, actor_type, actor_id, agent_id, connection_id, tool, params_summary, "
                "decision, reason, approval_id, status, duration_ms, error_category, request_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    iso(),
                    actor_type,
                    actor_id[:64],
                    agent_id,
                    connection_id,
                    tool,
                    summary[:MAX_PARAMS_SUMMARY],
                    decision[:32],
                    reason[:MAX_REASON],
                    approval_id,
                    status[:32],
                    duration_ms,
                    safe_error,
                    request_id,
                ),
            )
        except Exception:
            logger.error("failed to write audit event")

    def admin_action(
        self,
        *,
        admin_id: str,
        action: str,
        status: str = "ok",
        params: Any | None = None,
        reason: str = "",
    ) -> None:
        self.record(
            actor_type="admin",
            actor_id=admin_id,
            tool=action,
            decision="admin",
            status=status,
            params=_safe_admin_params(params),
            reason=reason,
        )

    def list_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        agent_id: str | None = None,
        tool: str | None = None,
        status: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id=?")
            params.append(agent_id)
        if tool:
            clauses.append("tool=?")
            params.append(tool)
        if status:
            clauses.append("status=?")
            params.append(status)
        if since:
            try:
                parse_iso(since)
            except ValueError:
                since = None
            if since:
                clauses.append("ts >= ?")
                params.append(since)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total_row = self.db.query_one(f"SELECT COUNT(*) AS count FROM audit{where}", params)
        rows = self.db.query_all(
            f"SELECT * FROM audit{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {
            "items": [
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "actor_type": row["actor_type"],
                    "actor_id": row["actor_id"],
                    "agent_id": row["agent_id"],
                    "connection_id": row["connection_id"],
                    "tool": row["tool"],
                    "params_summary": row["params_summary"],
                    "decision": row["decision"],
                    "reason": row["reason"],
                    "approval_id": row["approval_id"],
                    "status": row["status"],
                    "duration_ms": row["duration_ms"],
                    "error_category": row["error_category"],
                    "request_id": row["request_id"],
                }
                for row in rows
            ],
            "total": total_row["count"] if total_row else 0,
            "limit": limit,
            "offset": offset,
        }

    def purge_old(self, retention_days: int) -> int:
        from datetime import timedelta

        cutoff = iso(utcnow() - timedelta(days=max(1, retention_days)))
        deleted = self.db.execute_rowcount("DELETE FROM audit WHERE ts < ?", (cutoff,))
        if deleted:
            logger.info("purged %d audit events older than the retention window", deleted)
        return int(deleted)

    def safe_export(self, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return [redact_value(dict(row)) for row in rows]
