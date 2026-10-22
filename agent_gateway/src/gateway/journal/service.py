"""Project Journal service: persistence, ownership, and lifecycle rules.

Design invariants (enforced here, on the server, for every surface):

* Records are scoped to a project and attributed to the agent or admin that
  created them.  Work records are immutable once written; only task, issue,
  and session *status* can change, through validated transitions, and only by
  the owning agent or an administrator.  There is no delete operation.
* Every free-text field is redacted before storage so credentials an agent
  pastes into a work log or test output never persist.
* A claim is stored as a claim.  ``verification_level`` is never upgraded
  implicitly, and a task cannot be completed on an unverified claim.
* Retried creates with the same ``idempotency_key`` return the original
  record instead of duplicating it.
* A journal record never grants permission to execute anything.  Agent
  authorization (scope + central policy engine + rate limit + audit) happens
  in :func:`authorize_agent_journal` before any of these methods run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..errors import ConflictError, NotFoundError, PolicyDenied, RateLimited, ValidationFailure
from ..policy.models import PolicyRequest
from ..redaction import redact_text
from ..storage.db import Database
from ..utils import iso, parse_iso
from .models import (
    DEFAULT_PROJECT_ID,
    ISSUE_TRANSITIONS,
    OPEN_TASK_STATUSES,
    READ_TOOLS,
    TASK_TRANSITIONS,
    UNVERIFIED_LEVELS,
    VERIFIED_LEVELS,
    DecisionRequest,
    HandoffGenerateRequest,
    IssueRequest,
    IssueUpdateRequest,
    ProjectStateUpdateRequest,
    SessionEndRequest,
    SessionStartRequest,
    TaskCreateRequest,
    TaskUpdateRequest,
    TestRunRequest,
    WorkEntryRequest,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..audit.service import AuditService
    from ..auth.agents import AgentPrincipal
    from ..auth.rate_limit import SlidingWindowLimiter
    from ..policy.engine import PolicyService
    from ..policy.models import PolicyDecision

logger = logging.getLogger(__name__)

#: Hard ceiling for journal calls per agent+tool per minute, however generous
#: the matching policy rule is.  Journal writes are low-volume by design.
JOURNAL_MAX_PER_MINUTE = 120

DEFAULT_PROJECT_NAME = "Default project"


@dataclass(frozen=True)
class JournalActor:
    actor_type: str  # "agent" | "admin"
    actor_id: str
    actor_name: str
    source: str  # "rest" | "mcp" | "admin"

    @property
    def is_admin(self) -> bool:
        return self.actor_type == "admin"


def _clean(value: str) -> str:
    return redact_text(value)


def _clean_opt(value: str | None) -> str | None:
    return redact_text(value) if value is not None else None


def _files_json(files: list[str]) -> str:
    return json.dumps(files)


def _files(row: sqlite3.Row) -> list[str]:
    try:
        value = json.loads(row["files"])
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _check_pagination(limit: int, offset: int) -> None:
    if not 1 <= limit <= 200:
        raise ValidationFailure("limit must be between 1 and 200")
    if not 0 <= offset <= 100_000:
        raise ValidationFailure("offset is out of range")


def _check_since_until(since: str | None, until: str | None) -> None:
    for value in (since, until):
        if value:
            try:
                parse_iso(value)
            except ValueError as exc:
                raise ValidationFailure("since/until must be ISO-8601 timestamps") from exc


class JournalService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_default_project()

    # ---------------------------------------------------------------- project

    def _ensure_default_project(self) -> None:
        now = iso()
        self.db.execute(
            "INSERT OR IGNORE INTO journal_projects(id, name, created_at, updated_at) VALUES(?,?,?,?)",
            (DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME, now, now),
        )

    def _project_row(self, project_id: str) -> sqlite3.Row:
        row = self.db.query_one("SELECT * FROM journal_projects WHERE id=?", (project_id,))
        if row is None:
            raise NotFoundError("journal project")
        return row

    @staticmethod
    def _project_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "purpose": row["purpose"],
            "architecture": row["architecture"],
            "conventions": row["conventions"],
            "constraints": row["constraints_text"],
            "branch": row["branch"],
            "commit_sha": row["commit_sha"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "updated_by": {
                "actor_type": row["updated_by_type"],
                "actor_id": row["updated_by_id"],
                "session_id": row["updated_session_id"],
            },
        }

    def project_state(self, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        """Concise current state: the landing read for a new agent session."""

        project = self._project_public(self._project_row(project_id))
        open_tasks = self.list_tasks(project_id=project_id, status=None, limit=50, offset=0)["items"]
        return {
            "project": project,
            "counts": self.stats(project_id),
            "next_tasks": [t for t in open_tasks if t["status"] in OPEN_TASK_STATUSES][:20],
            "in_progress_tasks": [t for t in open_tasks if t["status"] == "in_progress"][:20],
            "blocked_tasks": [t for t in open_tasks if t["status"] == "blocked"][:20],
            "recent_completed_tasks": self.list_tasks(
                project_id=project_id, status="completed", limit=10, offset=0
            )["items"],
            "open_issues": self.list_issues(project_id=project_id, status="open", limit=20, offset=0)[
                "items"
            ],
            "recent_decisions": self.list_decisions(project_id=project_id, limit=10, offset=0)["items"],
            "recent_test_runs": self.list_test_runs(project_id=project_id, limit=10, offset=0)["items"],
            "recent_entries": self.list_entries(project_id=project_id, limit=10, offset=0)["items"],
            "active_sessions": self.list_sessions(project_id=project_id, status="active", limit=10, offset=0)[
                "items"
            ],
        }

    def update_project_state(self, actor: JournalActor, req: ProjectStateUpdateRequest) -> dict[str, Any]:
        """Optimistic-concurrency update of the shared project document."""

        self._project_row(req.project_id)
        if req.session_id:
            self._require_session(actor, req.project_id, req.session_id)
        updates = {
            "name": _clean_opt(req.name),
            "purpose": _clean_opt(req.purpose),
            "architecture": _clean_opt(req.architecture),
            "conventions": _clean_opt(req.conventions),
            "constraints_text": _clean_opt(req.constraints_text),
        }
        assignments = [f"{key}=?" for key, value in updates.items() if value is not None]
        params: list[Any] = [value for value in updates.values() if value is not None]
        if req.branch is not None:
            assignments.append("branch=?")
            params.append(req.branch)
        if req.commit_sha is not None:
            assignments.append("commit_sha=?")
            params.append(req.commit_sha)
        assignments.extend(
            [
                "revision=revision+1",
                "updated_at=?",
                "updated_by_type=?",
                "updated_by_id=?",
                "updated_session_id=?",
            ]
        )
        params.extend(
            [iso(), actor.actor_type, actor.actor_id, req.session_id, req.project_id, req.expected_revision]
        )
        changed = self.db.execute_rowcount(
            f"UPDATE journal_projects SET {', '.join(assignments)} WHERE id=? AND revision=?", params
        )
        if changed == 0:
            current = self._project_row(req.project_id)
            raise ConflictError(
                f"project state was changed by someone else (current revision {current['revision']}); "
                "reload it and retry"
            )
        return self._project_public(self._project_row(req.project_id))

    def stats(self, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        self._project_row(project_id)

        def count(sql: str, params: list[Any]) -> int:
            row = self.db.query_one(sql, params)
            return int(row["count"]) if row else 0

        task_counts = {
            status: count(
                "SELECT COUNT(*) AS count FROM journal_tasks WHERE project_id=? AND status=?",
                [project_id, status],
            )
            for status in ("todo", "in_progress", "blocked", "completed", "failed", "cancelled")
        }
        last = self.db.query_one(
            "SELECT MAX(ts) AS ts FROM ("
            " SELECT created_at AS ts FROM journal_entries WHERE project_id=?1"
            " UNION ALL SELECT created_at FROM journal_test_runs WHERE project_id=?1"
            " UNION ALL SELECT updated_at FROM journal_tasks WHERE project_id=?1"
            " UNION ALL SELECT created_at FROM journal_handoffs WHERE project_id=?1)",
            (project_id,),
        )
        return {
            "tasks": task_counts,
            "open_issues": count(
                "SELECT COUNT(*) AS count FROM journal_issues WHERE project_id=? AND status='open'",
                [project_id],
            ),
            "open_security_issues": count(
                "SELECT COUNT(*) AS count FROM journal_issues "
                "WHERE project_id=? AND status='open' AND kind='security'",
                [project_id],
            ),
            "open_bugs": count(
                "SELECT COUNT(*) AS count FROM journal_issues "
                "WHERE project_id=? AND status='open' AND kind='bug'",
                [project_id],
            ),
            "failed_approaches": count(
                "SELECT COUNT(*) AS count FROM journal_issues WHERE project_id=? AND kind='failed_approach'",
                [project_id],
            ),
            "entries": count(
                "SELECT COUNT(*) AS count FROM journal_entries WHERE project_id=?", [project_id]
            ),
            "unverified_entries": count(
                "SELECT COUNT(*) AS count FROM journal_entries "
                "WHERE project_id=? AND verification_level IN "
                "('claimed','not_tested','blocked_infrastructure')",
                [project_id],
            ),
            "decisions": count(
                "SELECT COUNT(*) AS count FROM journal_decisions WHERE project_id=?", [project_id]
            ),
            "test_runs": count(
                "SELECT COUNT(*) AS count FROM journal_test_runs WHERE project_id=?", [project_id]
            ),
            "failed_test_runs": count(
                "SELECT COUNT(*) AS count FROM journal_test_runs "
                "WHERE project_id=? AND status IN ('failed','error')",
                [project_id],
            ),
            "active_sessions": count(
                "SELECT COUNT(*) AS count FROM journal_sessions WHERE project_id=? AND status='active'",
                [project_id],
            ),
            "handoffs": count(
                "SELECT COUNT(*) AS count FROM journal_handoffs WHERE project_id=?", [project_id]
            ),
            "last_activity_at": last["ts"] if last else None,
        }

    # ------------------------------------------------------------------ refs

    def _require_session(self, actor: JournalActor, project_id: str, session_id: str) -> sqlite3.Row:
        row = self.db.query_one(
            "SELECT * FROM journal_sessions WHERE id=? AND project_id=?", (session_id, project_id)
        )
        if row is None:
            raise ValidationFailure("session_id does not reference a session in this project")
        if not actor.is_admin and (row["actor_type"], row["actor_id"]) != (actor.actor_type, actor.actor_id):
            raise ValidationFailure("session_id references another agent's session")
        return row

    def _require_task_ref(self, project_id: str, task_id: str | None) -> sqlite3.Row | None:
        if task_id is None:
            return None
        row = self.db.query_one(
            "SELECT * FROM journal_tasks WHERE id=? AND project_id=?", (task_id, project_id)
        )
        if row is None:
            raise ValidationFailure("task_id does not reference a task in this project")
        return row

    @staticmethod
    def _require_owner(actor: JournalActor, owner_type: str, owner_id: str, what: str) -> None:
        if actor.is_admin:
            return
        if (owner_type, owner_id) != (actor.actor_type, actor.actor_id):
            raise PolicyDenied(f"this {what} belongs to a different agent")

    def _insert(
        self,
        *,
        table: str,
        owner_columns: tuple[str, str],
        actor: JournalActor,
        idempotency_key: str | None,
        sql: str,
        params: list[Any],
        record_id: str,
    ) -> tuple[sqlite3.Row, bool]:
        """Insert a record, returning ``(row, replayed)`` for idempotent retries."""

        try:
            self.db.execute(sql, params)
        except sqlite3.IntegrityError:
            if idempotency_key:
                type_col, id_col = owner_columns
                existing = self.db.query_one(
                    f"SELECT * FROM {table} WHERE {type_col}=? AND {id_col}=? AND idempotency_key=?",
                    (actor.actor_type, actor.actor_id, idempotency_key),
                )
                if existing is not None:
                    return existing, True
            raise ValidationFailure("the record could not be stored (check referenced ids)") from None
        row = self.db.query_one(f"SELECT * FROM {table} WHERE id=?", (record_id,))
        assert row is not None
        return row, False

    # --------------------------------------------------------------- sessions

    @staticmethod
    def _session_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "actor_name": row["actor_name"],
            "title": row["title"],
            "branch": row["branch"],
            "commit_sha": row["commit_sha"],
            "status": row["status"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "summary": row["summary"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def start_session(self, actor: JournalActor, req: SessionStartRequest) -> dict[str, Any]:
        self._project_row(req.project_id)
        record_id = uuid.uuid4().hex
        now = iso()
        row, replayed = self._insert(
            table="journal_sessions",
            owner_columns=("actor_type", "actor_id"),
            actor=actor,
            idempotency_key=req.idempotency_key,
            sql=(
                "INSERT INTO journal_sessions(id, project_id, actor_type, actor_id, actor_name, title, "
                "branch, commit_sha, status, started_at, summary, idempotency_key, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,'active',?,'',?,?,?)"
            ),
            params=[
                record_id,
                req.project_id,
                actor.actor_type,
                actor.actor_id,
                actor.actor_name,
                _clean(req.title),
                req.branch,
                req.commit_sha,
                now,
                req.idempotency_key,
                now,
                now,
            ],
            record_id=record_id,
        )
        result = self._session_public(row)
        result["idempotent_replay"] = replayed
        return result

    def end_session(self, actor: JournalActor, session_id: str, req: SessionEndRequest) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM journal_sessions WHERE id=?", (session_id,))
        if row is None:
            raise NotFoundError("journal session")
        self._require_owner(actor, row["actor_type"], row["actor_id"], "session")
        if row["status"] != "active":
            raise ConflictError("session has already ended")
        now = iso()
        self.db.execute(
            "UPDATE journal_sessions SET status='ended', ended_at=?, summary=?, updated_at=? WHERE id=?",
            (now, _clean(req.summary), now, session_id),
        )
        ended = self.db.query_one("SELECT * FROM journal_sessions WHERE id=?", (session_id,))
        assert ended is not None
        return self._session_public(ended)

    def list_sessions(
        self,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        status: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _check_pagination(limit, offset)
        self._project_row(project_id)
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if status:
            clauses.append("status=?")
            params.append(status)
        if actor_id:
            clauses.append("actor_id=?")
            params.append(actor_id)
        where = " WHERE " + " AND ".join(clauses)
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM journal_sessions{where}", params)
        rows = self.db.query_all(
            f"SELECT * FROM journal_sessions{where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {
            "items": [self._session_public(row) for row in rows],
            "total": int(total["count"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }

    # ------------------------------------------------------------------ tasks

    def _task_dependencies(self, task_id: str) -> list[str]:
        rows = self.db.query_all(
            "SELECT depends_on_task_id FROM journal_task_dependencies "
            "WHERE task_id=? ORDER BY depends_on_task_id",
            (task_id,),
        )
        return [str(row["depends_on_task_id"]) for row in rows]

    def _blocked_by(self, task_id: str) -> list[str]:
        rows = self.db.query_all(
            "SELECT d.depends_on_task_id AS dep FROM journal_task_dependencies d "
            "JOIN journal_tasks t ON t.id = d.depends_on_task_id "
            "WHERE d.task_id=? AND t.status NOT IN ('completed','cancelled')",
            (task_id,),
        )
        return [str(row["dep"]) for row in rows]

    def _task_public(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "session_id": row["session_id"],
            "owner_type": row["owner_type"],
            "owner_id": row["owner_id"],
            "owner_name": row["owner_name"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
            "acceptance_criteria": row["acceptance_criteria"],
            "verification_level": row["verification_level"],
            "verified": row["verification_level"] in VERIFIED_LEVELS,
            "dependencies": self._task_dependencies(row["id"]),
            "blocked_by": self._blocked_by(row["id"]),
            "branch": row["branch"],
            "commit_sha": row["commit_sha"],
            "files": _files(row),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def _check_dependencies(self, project_id: str, task_id: str, dependencies: list[str]) -> None:
        if task_id in dependencies:
            raise ValidationFailure("a task cannot depend on itself")
        for dep in dependencies:
            self._require_task_ref(project_id, dep)
        edges: dict[str, set[str]] = {}
        for row in self.db.query_all("SELECT task_id, depends_on_task_id FROM journal_task_dependencies"):
            edges.setdefault(str(row["task_id"]), set()).add(str(row["depends_on_task_id"]))
        edges[task_id] = set(dependencies)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for nxt in edges.get(node, set()):
                if visit(nxt):
                    return True
            visiting.discard(node)
            visited.add(node)
            return False

        if visit(task_id):
            raise ValidationFailure("task dependencies would form a cycle")

    @staticmethod
    def _check_completion(status: str, verification_level: str) -> None:
        if status == "completed" and verification_level not in VERIFIED_LEVELS:
            raise ValidationFailure(
                "a task can only be completed with verification_level 'automated_tests' or "
                "'integration_verified'; record the test evidence first"
            )

    def _check_unblocked(self, task_id: str, dependencies: list[str], status: str) -> None:
        if status not in {"in_progress", "completed"}:
            return
        blockers = []
        for dep in dependencies:
            row = self.db.query_one("SELECT status FROM journal_tasks WHERE id=?", (dep,))
            if row is not None and row["status"] not in ("completed", "cancelled"):
                blockers.append(dep)
        if blockers:
            raise ConflictError(
                f"task is blocked by {len(blockers)} unfinished dependenc"
                f"{'y' if len(blockers) == 1 else 'ies'}: {', '.join(blockers)}"
            )

    def create_task(self, actor: JournalActor, req: TaskCreateRequest) -> dict[str, Any]:
        self._project_row(req.project_id)
        if req.session_id:
            self._require_session(actor, req.project_id, req.session_id)
        record_id = uuid.uuid4().hex
        self._check_dependencies(req.project_id, record_id, req.dependencies)
        self._check_completion(req.status, req.verification_level)
        self._check_unblocked(record_id, req.dependencies, req.status)
        now = iso()
        row, replayed = self._insert(
            table="journal_tasks",
            owner_columns=("owner_type", "owner_id"),
            actor=actor,
            idempotency_key=req.idempotency_key,
            sql=(
                "INSERT INTO journal_tasks(id, project_id, session_id, owner_type, owner_id, owner_name, "
                "title, description, status, priority, acceptance_criteria, verification_level, branch, "
                "commit_sha, files, created_at, updated_at, completed_at, idempotency_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            params=[
                record_id,
                req.project_id,
                req.session_id,
                actor.actor_type,
                actor.actor_id,
                actor.actor_name,
                _clean(req.title),
                _clean(req.description),
                req.status,
                req.priority,
                _clean(req.acceptance_criteria),
                req.verification_level,
                req.branch,
                req.commit_sha,
                _files_json(req.files),
                now,
                now,
                now if req.status == "completed" else None,
                req.idempotency_key,
            ],
            record_id=record_id,
        )
        if not replayed:
            with self.db.transaction() as conn:
                for dep in req.dependencies:
                    conn.execute(
                        "INSERT OR IGNORE INTO journal_task_dependencies(task_id, depends_on_task_id) "
                        "VALUES(?,?)",
                        (row["id"], dep),
                    )
        result = self._task_public(row)
        result["idempotent_replay"] = replayed
        return result

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM journal_tasks WHERE id=?", (task_id,))
        if row is None:
            raise NotFoundError("journal task")
        return self._task_public(row)

    def update_task(self, actor: JournalActor, task_id: str, req: TaskUpdateRequest) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM journal_tasks WHERE id=?", (task_id,))
        if row is None:
            raise NotFoundError("journal task")
        self._require_owner(actor, row["owner_type"], row["owner_id"], "task")
        current_status = str(row["status"])
        new_status = req.status or current_status
        if req.status is not None and req.status != current_status:
            allowed = TASK_TRANSITIONS.get(current_status, frozenset())
            if req.status not in allowed:
                raise ConflictError(
                    f"task status cannot move from {current_status} to {req.status}"
                    + ("; terminal states are final" if not allowed else "")
                )
        new_level = req.verification_level or str(row["verification_level"])
        self._check_completion(new_status, new_level)
        dependencies = req.dependencies if req.dependencies is not None else self._task_dependencies(task_id)
        if req.dependencies is not None:
            self._check_dependencies(str(row["project_id"]), task_id, dependencies)
        if new_status != current_status:
            self._check_unblocked(task_id, dependencies, new_status)

        fields: dict[str, Any] = {
            "title": _clean_opt(req.title),
            "description": _clean_opt(req.description),
            "priority": req.priority,
            "acceptance_criteria": _clean_opt(req.acceptance_criteria),
            "verification_level": req.verification_level,
            "branch": req.branch,
            "commit_sha": req.commit_sha,
            "files": _files_json(req.files) if req.files is not None else None,
        }
        assignments = [f"{key}=?" for key, value in fields.items() if value is not None]
        params: list[Any] = [value for value in fields.values() if value is not None]
        if req.status is not None:
            assignments.append("status=?")
            params.append(new_status)
            if new_status == "completed":
                assignments.append("completed_at=?")
                params.append(iso())
        assignments.append("updated_at=?")
        params.extend([iso(), task_id])
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE journal_tasks SET {', '.join(assignments)} WHERE id=?", params)
            if req.dependencies is not None:
                conn.execute("DELETE FROM journal_task_dependencies WHERE task_id=?", (task_id,))
                for dep in dependencies:
                    conn.execute(
                        "INSERT OR IGNORE INTO journal_task_dependencies(task_id, depends_on_task_id) "
                        "VALUES(?,?)",
                        (task_id, dep),
                    )
        return self.get_task(task_id)

    def list_tasks(
        self,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        status: str | None = None,
        q: str | None = None,
        session_id: str | None = None,
        owner_id: str | None = None,
        commit_sha: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _check_pagination(limit, offset)
        self._project_row(project_id)
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if status:
            clauses.append("status=?")
            params.append(status)
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        if owner_id:
            clauses.append("owner_id=?")
            params.append(owner_id)
        if commit_sha:
            clauses.append("commit_sha=?")
            params.append(commit_sha)
        if q:
            clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            params.extend([_like(q), _like(q)])
        where = " WHERE " + " AND ".join(clauses)
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM journal_tasks{where}", params)
        rows = self.db.query_all(
            f"SELECT * FROM journal_tasks{where} "
            "ORDER BY priority ASC, created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {
            "items": [self._task_public(row) for row in rows],
            "total": int(total["count"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }

    # ------------------------------------------------------------- work logs

    @staticmethod
    def _entry_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "session_id": row["session_id"],
            "task_id": row["task_id"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "actor_name": row["actor_name"],
            "source": row["source"],
            "kind": row["kind"],
            "title": row["title"],
            "body": row["body"],
            "outcome": row["outcome"],
            "verification_level": row["verification_level"],
            "verified": row["verification_level"] in VERIFIED_LEVELS,
            "files": _files(row),
            "commit_sha": row["commit_sha"],
            "branch": row["branch"],
            "created_at": row["created_at"],
        }

    def record_work(self, actor: JournalActor, req: WorkEntryRequest) -> dict[str, Any]:
        self._project_row(req.project_id)
        if req.session_id:
            self._require_session(actor, req.project_id, req.session_id)
        self._require_task_ref(req.project_id, req.task_id)
        record_id = uuid.uuid4().hex
        row, replayed = self._insert(
            table="journal_entries",
            owner_columns=("actor_type", "actor_id"),
            actor=actor,
            idempotency_key=req.idempotency_key,
            sql=(
                "INSERT INTO journal_entries(id, project_id, session_id, task_id, actor_type, actor_id, "
                "actor_name, source, kind, title, body, outcome, verification_level, files, commit_sha, "
                "branch, created_at, idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            params=[
                record_id,
                req.project_id,
                req.session_id,
                req.task_id,
                actor.actor_type,
                actor.actor_id,
                actor.actor_name,
                actor.source,
                req.kind,
                _clean(req.title),
                _clean(req.body),
                req.outcome,
                req.verification_level,
                _files_json(req.files),
                req.commit_sha,
                req.branch,
                iso(),
                req.idempotency_key,
            ],
            record_id=record_id,
        )
        result = self._entry_public(row)
        result["idempotent_replay"] = replayed
        return result

    def list_entries(
        self,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        task_id: str | None = None,
        session_id: str | None = None,
        actor_id: str | None = None,
        kind: str | None = None,
        outcome: str | None = None,
        verification_level: str | None = None,
        commit_sha: str | None = None,
        q: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _check_pagination(limit, offset)
        _check_since_until(since, until)
        self._project_row(project_id)
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        for column, value in (
            ("task_id", task_id),
            ("session_id", session_id),
            ("actor_id", actor_id),
            ("kind", kind),
            ("outcome", outcome),
            ("verification_level", verification_level),
            ("commit_sha", commit_sha),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if until:
            clauses.append("created_at <= ?")
            params.append(until)
        if q:
            clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
            params.extend([_like(q), _like(q)])
        where = " WHERE " + " AND ".join(clauses)
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM journal_entries{where}", params)
        rows = self.db.query_all(
            f"SELECT * FROM journal_entries{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {
            "items": [self._entry_public(row) for row in rows],
            "total": int(total["count"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }

    # -------------------------------------------------------------- decisions

    @staticmethod
    def _decision_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "session_id": row["session_id"],
            "task_id": row["task_id"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "actor_name": row["actor_name"],
            "source": row["source"],
            "title": row["title"],
            "decision": row["decision"],
            "rationale": row["rationale"],
            "alternatives": row["alternatives"],
            "status": row["status"],
            "files": _files(row),
            "commit_sha": row["commit_sha"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def record_decision(self, actor: JournalActor, req: DecisionRequest) -> dict[str, Any]:
        self._project_row(req.project_id)
        if req.session_id:
            self._require_session(actor, req.project_id, req.session_id)
        self._require_task_ref(req.project_id, req.task_id)
        record_id = uuid.uuid4().hex
        now = iso()
        row, replayed = self._insert(
            table="journal_decisions",
            owner_columns=("actor_type", "actor_id"),
            actor=actor,
            idempotency_key=req.idempotency_key,
            sql=(
                "INSERT INTO journal_decisions(id, project_id, session_id, task_id, actor_type, actor_id, "
                "actor_name, source, title, decision, rationale, alternatives, status, files, commit_sha, "
                "created_at, updated_at, idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            params=[
                record_id,
                req.project_id,
                req.session_id,
                req.task_id,
                actor.actor_type,
                actor.actor_id,
                actor.actor_name,
                actor.source,
                _clean(req.title),
                _clean(req.decision),
                _clean(req.rationale),
                _clean(req.alternatives),
                req.status,
                _files_json(req.files),
                req.commit_sha,
                now,
                now,
                req.idempotency_key,
            ],
            record_id=record_id,
        )
        result = self._decision_public(row)
        result["idempotent_replay"] = replayed
        return result

    def list_decisions(
        self,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        task_id: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _check_pagination(limit, offset)
        self._project_row(project_id)
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if task_id:
            clauses.append("task_id=?")
            params.append(task_id)
        if q:
            clauses.append(
                "(title LIKE ? ESCAPE '\\' OR decision LIKE ? ESCAPE '\\' OR rationale LIKE ? ESCAPE '\\')"
            )
            params.extend([_like(q), _like(q), _like(q)])
        where = " WHERE " + " AND ".join(clauses)
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM journal_decisions{where}", params)
        rows = self.db.query_all(
            f"SELECT * FROM journal_decisions{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {
            "items": [self._decision_public(row) for row in rows],
            "total": int(total["count"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }

    # ----------------------------------------------------------------- issues

    @staticmethod
    def _issue_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "session_id": row["session_id"],
            "task_id": row["task_id"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "actor_name": row["actor_name"],
            "source": row["source"],
            "kind": row["kind"],
            "title": row["title"],
            "body": row["body"],
            "learned": row["learned"],
            "severity": row["severity"],
            "status": row["status"],
            "verification_level": row["verification_level"],
            "verified": row["verification_level"] in VERIFIED_LEVELS,
            "files": _files(row),
            "commit_sha": row["commit_sha"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "resolved_at": row["resolved_at"],
        }

    def record_issue(self, actor: JournalActor, req: IssueRequest) -> dict[str, Any]:
        self._project_row(req.project_id)
        if req.session_id:
            self._require_session(actor, req.project_id, req.session_id)
        self._require_task_ref(req.project_id, req.task_id)
        record_id = uuid.uuid4().hex
        now = iso()
        row, replayed = self._insert(
            table="journal_issues",
            owner_columns=("actor_type", "actor_id"),
            actor=actor,
            idempotency_key=req.idempotency_key,
            sql=(
                "INSERT INTO journal_issues(id, project_id, session_id, task_id, actor_type, actor_id, "
                "actor_name, source, kind, title, body, learned, severity, status, verification_level, "
                "files, commit_sha, created_at, updated_at, idempotency_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?,?,?,?)"
            ),
            params=[
                record_id,
                req.project_id,
                req.session_id,
                req.task_id,
                actor.actor_type,
                actor.actor_id,
                actor.actor_name,
                actor.source,
                req.kind,
                _clean(req.title),
                _clean(req.body),
                _clean(req.learned),
                req.severity,
                req.verification_level,
                _files_json(req.files),
                req.commit_sha,
                now,
                now,
                req.idempotency_key,
            ],
            record_id=record_id,
        )
        result = self._issue_public(row)
        result["idempotent_replay"] = replayed
        return result

    def update_issue(self, actor: JournalActor, issue_id: str, req: IssueUpdateRequest) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM journal_issues WHERE id=?", (issue_id,))
        if row is None:
            raise NotFoundError("journal issue")
        self._require_owner(actor, row["actor_type"], row["actor_id"], "issue")
        current = str(row["status"])
        if req.status != current and req.status not in ISSUE_TRANSITIONS.get(current, frozenset()):
            raise ConflictError(f"issue status cannot move from {current} to {req.status}")
        now = iso()
        self.db.execute(
            "UPDATE journal_issues SET status=?, resolved_at=?, updated_at=? WHERE id=?",
            (req.status, now if req.status == "resolved" else None, now, issue_id),
        )
        updated = self.db.query_one("SELECT * FROM journal_issues WHERE id=?", (issue_id,))
        assert updated is not None
        return self._issue_public(updated)

    def list_issues(
        self,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        status: str | None = None,
        kind: str | None = None,
        task_id: str | None = None,
        verification_level: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _check_pagination(limit, offset)
        self._project_row(project_id)
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        for column, value in (
            ("status", status),
            ("kind", kind),
            ("task_id", task_id),
            ("verification_level", verification_level),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if q:
            clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
            params.extend([_like(q), _like(q)])
        where = " WHERE " + " AND ".join(clauses)
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM journal_issues{where}", params)
        rows = self.db.query_all(
            f"SELECT * FROM journal_issues{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {
            "items": [self._issue_public(row) for row in rows],
            "total": int(total["count"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }

    # ------------------------------------------------------------- test runs

    @staticmethod
    def _test_run_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "session_id": row["session_id"],
            "task_id": row["task_id"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "actor_name": row["actor_name"],
            "source": row["source"],
            "command": row["command"],
            "status": row["status"],
            "exit_code": row["exit_code"],
            "verification_level": row["verification_level"],
            "verified": row["verification_level"] in VERIFIED_LEVELS,
            "output_summary": row["output_summary"],
            "environment": row["environment"],
            "commit_sha": row["commit_sha"],
            "run_at": row["run_at"],
            "duration_seconds": row["duration_seconds"],
            "created_at": row["created_at"],
        }

    def record_test_run(self, actor: JournalActor, req: TestRunRequest) -> dict[str, Any]:
        self._project_row(req.project_id)
        if req.session_id:
            self._require_session(actor, req.project_id, req.session_id)
        self._require_task_ref(req.project_id, req.task_id)
        record_id = uuid.uuid4().hex
        row, replayed = self._insert(
            table="journal_test_runs",
            owner_columns=("actor_type", "actor_id"),
            actor=actor,
            idempotency_key=req.idempotency_key,
            sql=(
                "INSERT INTO journal_test_runs(id, project_id, session_id, task_id, actor_type, actor_id, "
                "actor_name, source, command, status, exit_code, verification_level, output_summary, "
                "environment, commit_sha, run_at, duration_seconds, created_at, idempotency_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            params=[
                record_id,
                req.project_id,
                req.session_id,
                req.task_id,
                actor.actor_type,
                actor.actor_id,
                actor.actor_name,
                actor.source,
                _clean(req.command),
                req.status,
                req.exit_code,
                req.verification_level,
                _clean(req.output_summary),
                _clean(req.environment),
                req.commit_sha,
                req.run_at,
                req.duration_seconds,
                iso(),
                req.idempotency_key,
            ],
            record_id=record_id,
        )
        result = self._test_run_public(row)
        result["idempotent_replay"] = replayed
        return result

    def list_test_runs(
        self,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        task_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        commit_sha: str | None = None,
        verification_level: str | None = None,
        q: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _check_pagination(limit, offset)
        _check_since_until(since, until)
        self._project_row(project_id)
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        for column, value in (
            ("task_id", task_id),
            ("session_id", session_id),
            ("status", status),
            ("commit_sha", commit_sha),
            ("verification_level", verification_level),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if until:
            clauses.append("created_at <= ?")
            params.append(until)
        if q:
            clauses.append("(command LIKE ? ESCAPE '\\' OR output_summary LIKE ? ESCAPE '\\')")
            params.extend([_like(q), _like(q)])
        where = " WHERE " + " AND ".join(clauses)
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM journal_test_runs{where}", params)
        rows = self.db.query_all(
            f"SELECT * FROM journal_test_runs{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {
            "items": [self._test_run_public(row) for row in rows],
            "total": int(total["count"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }

    # --------------------------------------------------------------- timeline

    def timeline(
        self,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        record_type: str | None = None,
        category: str | None = None,
        verification_level: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        actor_id: str | None = None,
        commit_sha: str | None = None,
        q: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Chronological merge of entries, decisions, issues, and test runs.

        ``category`` maps onto each record type's own outcome vocabulary:
        ``completed`` (entry outcome / passed run / resolved issue),
        ``failed`` (entry outcome / failed run / failed approach),
        ``blocked`` (entry outcome / blocked run / open issue), and
        ``unverified`` (verification level is a claim, untested, or blocked by
        unavailable infrastructure).  Decisions have no outcome and only
        appear when no category or verification filter is set.  The merged
        view serves the most recent 200 records per type; the per-type list
        endpoints provide deep pagination.
        """

        _check_pagination(limit, offset)
        if offset + limit > 200:
            raise ValidationFailure(
                "the merged timeline serves the most recent 200 records per type; "
                "narrow the filters or use the per-type lists for deep pagination"
            )
        _check_since_until(since, until)
        self._project_row(project_id)
        fetch = limit + offset
        items: list[dict[str, Any]] = []
        total = 0

        def wanted(name: str) -> bool:
            return record_type in (None, name)

        # Verification filtering happens in SQL per level so totals stay exact.
        levels: list[str | None] = []
        if verification_level is not None:
            levels.append(verification_level)
        elif category == "unverified":
            levels.extend(sorted(UNVERIFIED_LEVELS))
        else:
            levels.append(None)

        if wanted("entry"):
            outcome = category if category in ("completed", "failed", "blocked") else None
            matched: list[dict[str, Any]] = []
            for level in levels:
                listing = self.list_entries(
                    project_id=project_id,
                    task_id=task_id,
                    session_id=session_id,
                    actor_id=actor_id,
                    outcome=outcome,
                    verification_level=level,
                    commit_sha=commit_sha,
                    q=q,
                    since=since,
                    until=until,
                    limit=fetch,
                    offset=0,
                )
                total += int(listing["total"])
                matched.extend(listing["items"])
            items.extend(
                {
                    "record_type": "entry",
                    "id": e["id"],
                    "title": e["title"],
                    "body": e["body"],
                    "status": e["outcome"],
                    "kind": e["kind"],
                    "verification_level": e["verification_level"],
                    "verified": e["verified"],
                    "actor_type": e["actor_type"],
                    "actor_id": e["actor_id"],
                    "actor_name": e["actor_name"],
                    "source": e["source"],
                    "session_id": e["session_id"],
                    "task_id": e["task_id"],
                    "commit_sha": e["commit_sha"],
                    "files": e["files"],
                    "created_at": e["created_at"],
                    "evidence": None,
                }
                for e in matched
            )
        if wanted("decision") and category is None and verification_level is None:
            listing = self.list_decisions(project_id=project_id, task_id=task_id, q=q, limit=fetch, offset=0)
            total += int(listing["total"])
            items.extend(
                {
                    "record_type": "decision",
                    "id": d["id"],
                    "title": d["title"],
                    "body": d["decision"],
                    "status": d["status"],
                    "kind": "decision",
                    "verification_level": None,
                    "verified": False,
                    "actor_type": d["actor_type"],
                    "actor_id": d["actor_id"],
                    "actor_name": d["actor_name"],
                    "source": d["source"],
                    "session_id": d["session_id"],
                    "task_id": d["task_id"],
                    "commit_sha": d["commit_sha"],
                    "files": d["files"],
                    "created_at": d["created_at"],
                    "evidence": {"rationale": d["rationale"], "alternatives": d["alternatives"]},
                }
                for d in listing["items"]
            )
        if wanted("issue"):
            status = {"completed": "resolved", "blocked": "open"}.get(category or "")
            kind = "failed_approach" if category == "failed" else None
            matched_issues: list[dict[str, Any]] = []
            for level in levels:
                listing = self.list_issues(
                    project_id=project_id,
                    status=status,
                    kind=kind,
                    task_id=task_id,
                    verification_level=level,
                    q=q,
                    limit=fetch,
                    offset=0,
                )
                total += int(listing["total"])
                matched_issues.extend(listing["items"])
            items.extend(
                {
                    "record_type": "issue",
                    "id": i["id"],
                    "title": i["title"],
                    "body": i["body"],
                    "status": i["status"],
                    "kind": i["kind"],
                    "verification_level": i["verification_level"],
                    "verified": i["verified"],
                    "actor_type": i["actor_type"],
                    "actor_id": i["actor_id"],
                    "actor_name": i["actor_name"],
                    "source": i["source"],
                    "session_id": i["session_id"],
                    "task_id": i["task_id"],
                    "commit_sha": i["commit_sha"],
                    "files": i["files"],
                    "created_at": i["created_at"],
                    "evidence": {"severity": i["severity"], "learned": i["learned"]},
                }
                for i in matched_issues
            )
        if wanted("test_run"):
            status = {"completed": "passed", "failed": "failed", "blocked": "blocked"}.get(category or "")
            matched_runs: list[dict[str, Any]] = []
            for level in levels:
                listing = self.list_test_runs(
                    project_id=project_id,
                    task_id=task_id,
                    session_id=session_id,
                    status=status,
                    commit_sha=commit_sha,
                    verification_level=level,
                    q=q,
                    since=since,
                    until=until,
                    limit=fetch,
                    offset=0,
                )
                total += int(listing["total"])
                matched_runs.extend(listing["items"])
            items.extend(
                {
                    "record_type": "test_run",
                    "id": t["id"],
                    "title": t["command"],
                    "body": t["output_summary"],
                    "status": t["status"],
                    "kind": "test_run",
                    "verification_level": t["verification_level"],
                    "verified": t["verified"],
                    "actor_type": t["actor_type"],
                    "actor_id": t["actor_id"],
                    "actor_name": t["actor_name"],
                    "source": t["source"],
                    "session_id": t["session_id"],
                    "task_id": t["task_id"],
                    "commit_sha": t["commit_sha"],
                    "files": [],
                    "created_at": t["created_at"],
                    "evidence": {
                        "command": t["command"],
                        "exit_code": t["exit_code"],
                        "environment": t["environment"],
                        "run_at": t["run_at"],
                        "duration_seconds": t["duration_seconds"],
                    },
                }
                for t in matched_runs
            )

        items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return {"items": items[offset : offset + limit], "total": total, "limit": limit, "offset": offset}

    # --------------------------------------------------------------- handoffs

    @staticmethod
    def _handoff_public(row: sqlite3.Row) -> dict[str, Any]:
        try:
            included = json.loads(row["included"])
        except (json.JSONDecodeError, TypeError):
            included = {}
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "session_id": row["session_id"],
            "focus_task_id": row["focus_task_id"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "actor_name": row["actor_name"],
            "source": row["source"],
            "title": row["title"],
            "prompt": row["prompt"],
            "branch": row["branch"],
            "commit_sha": row["commit_sha"],
            "based_on_revision": row["based_on_revision"],
            "included": included if isinstance(included, dict) else {},
            "created_at": row["created_at"],
        }

    def generate_handoff(self, actor: JournalActor, req: HandoffGenerateRequest) -> dict[str, Any]:
        from .handoff import build_handoff_prompt

        project_row = self._project_row(req.project_id)
        if req.session_id:
            self._require_session(actor, req.project_id, req.session_id)
        focus = self._require_task_ref(req.project_id, req.focus_task_id)
        prompt, included = build_handoff_prompt(self, project_row, focus, req.max_entries)
        record_id = uuid.uuid4().hex
        title = _clean(req.title) or included.get("title", "Session handoff")
        row, replayed = self._insert(
            table="journal_handoffs",
            owner_columns=("actor_type", "actor_id"),
            actor=actor,
            idempotency_key=req.idempotency_key,
            sql=(
                "INSERT INTO journal_handoffs(id, project_id, session_id, focus_task_id, actor_type, "
                "actor_id, actor_name, source, title, prompt, branch, commit_sha, based_on_revision, "
                "included, created_at, idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            params=[
                record_id,
                req.project_id,
                req.session_id,
                req.focus_task_id,
                actor.actor_type,
                actor.actor_id,
                actor.actor_name,
                actor.source,
                title,
                prompt,
                project_row["branch"],
                project_row["commit_sha"],
                int(project_row["revision"]),
                json.dumps(included),
                iso(),
                req.idempotency_key,
            ],
            record_id=record_id,
        )
        result = self._handoff_public(row)
        result["idempotent_replay"] = replayed
        return result

    def get_handoff(self, handoff_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM journal_handoffs WHERE id=?", (handoff_id,))
        if row is None:
            raise NotFoundError("journal handoff")
        return self._handoff_public(row)

    def list_handoffs(
        self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        _check_pagination(limit, offset)
        self._project_row(project_id)
        total = self.db.query_one(
            "SELECT COUNT(*) AS count FROM journal_handoffs WHERE project_id=?", (project_id,)
        )
        rows = self.db.query_all(
            "SELECT * FROM journal_handoffs WHERE project_id=? ORDER BY created_at DESC, id DESC "
            "LIMIT ? OFFSET ?",
            (project_id, limit, offset),
        )
        items = []
        for row in rows:
            public = self._handoff_public(row)
            public.pop("prompt", None)  # list view stays small; fetch one for the full prompt
            items.append(public)
        return {
            "items": items,
            "total": int(total["count"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }


# ---------------------------------------------------------- agent authorization


def authorize_agent_journal(
    *,
    policies: PolicyService,
    limiter: SlidingWindowLimiter,
    audit: AuditService,
    agent: AgentPrincipal,
    tool: str,
    request_id: str | None = None,
) -> PolicyDecision:
    """Authorize one agent journal call: scope, central policy, rate limit.

    Journal records cannot execute anything, so they are not approval-gated:
    they are evaluated with read risk, and a policy rule that explicitly sets
    ``require_approval`` for a journal tool *denies* the call (fail closed)
    rather than being silently ignored.  The matching rule's rate limit is
    honored, capped at :data:`JOURNAL_MAX_PER_MINUTE`.
    """

    scope = "journal:read" if tool in READ_TOOLS else "journal:write"

    def deny(reason: str, *, status: str = "denied") -> None:
        audit.record(
            actor_type="agent",
            actor_id=agent.id,
            agent_id=agent.id,
            tool=tool,
            decision="deny",
            status=status,
            reason=reason,
            request_id=request_id,
        )

    if not agent.has_scope(scope):
        deny(f"agent lacks the {scope} scope")
        raise PolicyDenied(f"agent lacks the {scope} scope")
    decision = policies.evaluate(
        PolicyRequest(agent_id=agent.id, tool=tool, connection_id=None, risk="read", targets={})
    )
    if not decision.allowed:
        deny(decision.reason)
        raise PolicyDenied(decision.reason)
    if decision.require_approval:
        deny("journal operations are not approval-gated")
        raise PolicyDenied(
            "journal operations cannot be approval-gated; remove require_approval "
            "from the matching policy rule"
        )
    limit = decision.limits.rate_limit_per_minute
    effective = limit if 0 < limit < JOURNAL_MAX_PER_MINUTE else JOURNAL_MAX_PER_MINUTE
    allowed, retry_after = limiter.check_and_record(
        f"journal:{agent.id}:{tool}", limit=effective, window_seconds=60
    )
    if not allowed:
        deny("journal rate limit exceeded", status="rate_limited")
        raise RateLimited(retry_after)
    return decision
