"""Single-use, expiring, parameter-bound approvals."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..errors import ApprovalInvalid, ConflictError, NotFoundError
from ..redaction import redact_text
from ..storage.db import Database
from ..utils import expires_after, iso, parse_iso, utcnow

logger = logging.getLogger(__name__)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
USED = "used"


class ApprovalService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _public(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "operation_id": row["operation_id"],
            "agent_id": row["agent_id"],
            "tool": row["tool"],
            "connection_id": row["connection_id"],
            "params_summary": row["params_summary"],
            "status": row["status"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "decided_at": row["decided_at"],
            "decided_by": row["decided_by"],
            "used_at": row["used_at"],
            "reason": row["reason"],
        }

    def create(
        self,
        *,
        operation_id: str,
        agent_id: str,
        tool: str,
        connection_id: str | None,
        request_hash: str,
        params_summary: str,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        approval_id = uuid.uuid4().hex
        expires_at, _ = expires_after(ttl_seconds)
        self.db.execute(
            "INSERT INTO approvals(id, operation_id, agent_id, tool, connection_id, request_hash, "
            "params_summary, status, created_at, expires_at, reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                operation_id,
                agent_id,
                tool,
                connection_id,
                request_hash,
                params_summary,
                PENDING,
                iso(),
                expires_at,
                "",
            ),
        )
        logger.info("approval requested id=%s tool=%s", approval_id, tool)
        return self.get(approval_id)

    def get(self, approval_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if row is None:
            raise NotFoundError("approval")
        self._expire_if_needed(row)
        row = self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        assert row is not None
        return self._public(row)

    def _expire_if_needed(self, row: Any) -> None:
        if row["status"] == PENDING and parse_iso(row["expires_at"]) <= utcnow():
            self.db.execute(
                "UPDATE approvals SET status=? WHERE id=? AND status=?",
                (EXPIRED, row["id"], PENDING),
            )

    def list(self, *, status: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        self.expire_pending()
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.query_all(
            f"SELECT * FROM approvals{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM approvals{where}", params)
        return {
            "items": [self._public(row) for row in rows],
            "total": total["count"] if total else 0,
            "limit": limit,
            "offset": offset,
        }

    def approve(self, approval_id: str, admin_id: str) -> dict[str, Any]:
        row = self.get(approval_id)
        if row["status"] != PENDING:
            raise ConflictError("approval is not pending")
        if row["expires_at"] <= iso():
            raise ConflictError("approval has expired")
        updated = self.db.execute_rowcount(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=? AND status=?",
            (APPROVED, iso(), admin_id[:64], approval_id, PENDING),
        )
        if updated != 1:
            raise ConflictError("approval was already decided")
        logger.info("approval granted id=%s by=%s", approval_id, admin_id[:32])
        return self.get(approval_id)

    def reject(self, approval_id: str, admin_id: str, reason: str = "") -> dict[str, Any]:
        row = self.get(approval_id)
        if row["status"] != PENDING:
            raise ConflictError("approval is not pending")
        updated = self.db.execute_rowcount(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=?, reason=? WHERE id=? AND status=?",
            (REJECTED, iso(), admin_id[:64], redact_text(reason)[:300], approval_id, PENDING),
        )
        if updated != 1:
            raise ConflictError("approval was already decided")
        logger.info("approval rejected id=%s by=%s", approval_id, admin_id[:32])
        return self.get(approval_id)

    def consume(
        self,
        approval_id: str,
        *,
        agent_id: str,
        tool: str,
        connection_id: str | None,
        request_hash: str,
    ) -> dict[str, Any]:
        """Atomically consume an approval. Raises ApprovalInvalid on any mismatch."""

        row = self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if row is None:
            raise ApprovalInvalid("approval was not found")
        if row["agent_id"] != agent_id or row["tool"] != tool:
            raise ApprovalInvalid("approval is not bound to this agent and operation")
        if (row["connection_id"] or None) != (connection_id or None):
            raise ApprovalInvalid("approval is not bound to this connection")
        if row["request_hash"] != request_hash:
            raise ApprovalInvalid("approval does not match the current operation parameters or policy")
        if row["status"] != APPROVED:
            raise ApprovalInvalid("approval has not been granted or was already used")
        if row["used_at"]:
            raise ApprovalInvalid("approval has already been used")
        if parse_iso(row["expires_at"]) <= utcnow():
            self.db.execute(
                "UPDATE approvals SET status=? WHERE id=? AND status=?", (EXPIRED, approval_id, APPROVED)
            )
            raise ApprovalInvalid("approval has expired")
        updated = self.db.execute_rowcount(
            "UPDATE approvals SET status=?, used_at=? WHERE id=? AND status=? AND used_at IS NULL",
            (USED, iso(), approval_id, APPROVED),
        )
        if updated != 1:
            raise ApprovalInvalid("approval was already used")
        logger.info("approval consumed id=%s", approval_id)
        row = self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        assert row is not None
        return self._public(row)

    def expire_pending(self) -> int:
        return int(
            self.db.execute_rowcount(
                "UPDATE approvals SET status=? WHERE status=? AND expires_at <= ?",
                (EXPIRED, PENDING, iso()),
            )
        )

    def pending_count(self) -> int:
        self.expire_pending()
        row = self.db.query_one("SELECT COUNT(*) AS count FROM approvals WHERE status=?", (PENDING,))
        return int(row["count"]) if row else 0

    def purge_old(self, retention_days: int) -> int:
        from datetime import timedelta

        cutoff = iso(utcnow() - timedelta(days=max(1, retention_days)))
        return int(
            self.db.execute_rowcount(
                "DELETE FROM approvals WHERE created_at < ? AND status IN (?,?,?)",
                (cutoff, REJECTED, EXPIRED, USED),
            )
        )
