"""Project Journal APIs for agents (REST) and the Ingress panel (admin).

Agent routes are authorized per call by
:func:`gateway.journal.service.authorize_agent_journal`: scope check, central
policy engine evaluation against a ``journal.*`` pseudo-tool, policy rate
limit, and an audit event for denials and for every write.  Admin routes use
the existing Ingress gate and audit administrative actions.  Both surfaces
call the same :class:`JournalService`, so validation, redaction, ownership,
and lifecycle rules cannot diverge.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from ..auth.agents import AgentPrincipal
from ..journal.models import (
    DEFAULT_PROJECT_ID,
    TOOL_END_SESSION,
    TOOL_HANDOFF,
    TOOL_LIST_ENTRIES,
    TOOL_LIST_SESSIONS,
    TOOL_LIST_TASKS,
    TOOL_LIST_TEST_RUNS,
    TOOL_READ_STATE,
    TOOL_RECORD_DECISION,
    TOOL_RECORD_ISSUE,
    TOOL_RECORD_TASK,
    TOOL_RECORD_TEST_RUN,
    TOOL_RECORD_WORK,
    TOOL_START_SESSION,
    TOOL_UPDATE_ISSUE,
    TOOL_UPDATE_STATE,
    TOOL_UPDATE_TASK,
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
from ..journal.service import JournalActor, authorize_agent_journal
from ..runtime import Runtime
from .deps import get_admin, get_agent, get_runtime
from .middleware import AdminPrincipal

agent_router = APIRouter(prefix="/api/v1/journal", tags=["journal"])
admin_router = APIRouter(prefix="/api/admin/journal", tags=["journal-admin"])

Category = Literal["completed", "failed", "blocked", "unverified"]
RecordType = Literal["entry", "decision", "issue", "test_run"]


def _agent_actor(agent: AgentPrincipal, source: str = "rest") -> JournalActor:
    return JournalActor(actor_type="agent", actor_id=agent.id, actor_name=agent.name, source=source)


def _admin_actor(admin: AdminPrincipal) -> JournalActor:
    return JournalActor(
        actor_type="admin", actor_id=admin.user_id, actor_name=admin.display_name, source="admin"
    )


def _authorize(request: Request, runtime: Runtime, agent: AgentPrincipal, tool: str) -> None:
    authorize_agent_journal(
        policies=runtime.policies,
        limiter=runtime.rate_limiter,
        audit=runtime.audit,
        agent=agent,
        tool=tool,
        request_id=getattr(request.state, "request_id", None),
    )


def _audit_agent_write(
    request: Request, runtime: Runtime, agent: AgentPrincipal, tool: str, result: dict[str, Any]
) -> None:
    runtime.audit.record(
        actor_type="agent",
        actor_id=agent.id,
        agent_id=agent.id,
        tool=tool,
        decision="allow",
        status="ok",
        params={
            "record_id": result.get("id"),
            "project_id": result.get("project_id", DEFAULT_PROJECT_ID),
            "idempotent_replay": bool(result.get("idempotent_replay")),
        },
        request_id=getattr(request.state, "request_id", None),
    )


def _audit_admin_write(runtime: Runtime, admin: AdminPrincipal, action: str, result: dict[str, Any]) -> None:
    runtime.audit.admin_action(
        admin_id=admin.user_id,
        action=action,
        params={
            "record_id": result.get("id"),
            "project_id": result.get("project_id", DEFAULT_PROJECT_ID),
            "idempotent_replay": bool(result.get("idempotent_replay")),
        },
    )


# ------------------------------------------------------------- agent routes


@agent_router.get("/project-state")
def agent_project_state(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_READ_STATE)
    return runtime.journal.project_state(project_id)


@agent_router.put("/project-state")
def agent_update_project_state(
    request: Request,
    payload: ProjectStateUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_UPDATE_STATE)
    result = runtime.journal.update_project_state(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_UPDATE_STATE, result)
    return result


@agent_router.get("/stats")
def agent_stats(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_READ_STATE)
    return runtime.journal.stats(project_id)


@agent_router.post("/sessions")
def agent_start_session(
    request: Request,
    payload: SessionStartRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_START_SESSION)
    result = runtime.journal.start_session(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_START_SESSION, result)
    return result


@agent_router.post("/sessions/{session_id}/end")
def agent_end_session(
    request: Request,
    session_id: str,
    payload: SessionEndRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_END_SESSION)
    result = runtime.journal.end_session(_agent_actor(agent), session_id, payload)
    _audit_agent_write(request, runtime, agent, TOOL_END_SESSION, result)
    return result


@agent_router.get("/sessions")
def agent_list_sessions(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    status: Literal["active", "ended"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_SESSIONS)
    return runtime.journal.list_sessions(project_id=project_id, status=status, limit=limit, offset=offset)


@agent_router.get("/tasks")
def agent_list_tasks(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=200),
    commit_sha: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_TASKS)
    return runtime.journal.list_tasks(
        project_id=project_id, status=status, q=q, commit_sha=commit_sha, limit=limit, offset=offset
    )


@agent_router.get("/tasks/{task_id}")
def agent_get_task(
    request: Request,
    task_id: str,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_TASKS)
    return runtime.journal.get_task(task_id)


@agent_router.post("/tasks")
def agent_create_task(
    request: Request,
    payload: TaskCreateRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_RECORD_TASK)
    result = runtime.journal.create_task(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_RECORD_TASK, result)
    return result


@agent_router.patch("/tasks/{task_id}")
def agent_update_task(
    request: Request,
    task_id: str,
    payload: TaskUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_UPDATE_TASK)
    result = runtime.journal.update_task(_agent_actor(agent), task_id, payload)
    _audit_agent_write(request, runtime, agent, TOOL_UPDATE_TASK, result)
    return result


@agent_router.get("/timeline")
def agent_timeline(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    record_type: RecordType | None = Query(default=None),
    category: Category | None = Query(default=None),
    verification_level: str | None = Query(default=None, max_length=32),
    task_id: str | None = Query(default=None, max_length=64),
    session_id: str | None = Query(default=None, max_length=64),
    commit_sha: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    since: str | None = Query(default=None, max_length=40),
    until: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=200),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_ENTRIES)
    return runtime.journal.timeline(
        project_id=project_id,
        record_type=record_type,
        category=category,
        verification_level=verification_level,
        task_id=task_id,
        session_id=session_id,
        commit_sha=commit_sha,
        q=q,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@agent_router.get("/entries")
def agent_list_entries(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    task_id: str | None = Query(default=None, max_length=64),
    outcome: str | None = Query(default=None, max_length=32),
    verification_level: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_ENTRIES)
    return runtime.journal.list_entries(
        project_id=project_id,
        task_id=task_id,
        outcome=outcome,
        verification_level=verification_level,
        q=q,
        limit=limit,
        offset=offset,
    )


@agent_router.post("/entries")
def agent_record_work(
    request: Request,
    payload: WorkEntryRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_RECORD_WORK)
    result = runtime.journal.record_work(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_RECORD_WORK, result)
    return result


@agent_router.post("/decisions")
def agent_record_decision(
    request: Request,
    payload: DecisionRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_RECORD_DECISION)
    result = runtime.journal.record_decision(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_RECORD_DECISION, result)
    return result


@agent_router.post("/issues")
def agent_record_issue(
    request: Request,
    payload: IssueRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_RECORD_ISSUE)
    result = runtime.journal.record_issue(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_RECORD_ISSUE, result)
    return result


@agent_router.patch("/issues/{issue_id}")
def agent_update_issue(
    request: Request,
    issue_id: str,
    payload: IssueUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_UPDATE_ISSUE)
    result = runtime.journal.update_issue(_agent_actor(agent), issue_id, payload)
    _audit_agent_write(request, runtime, agent, TOOL_UPDATE_ISSUE, result)
    return result


@agent_router.get("/test-runs")
def agent_list_test_runs(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    task_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    commit_sha: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_TEST_RUNS)
    return runtime.journal.list_test_runs(
        project_id=project_id,
        task_id=task_id,
        status=status,
        commit_sha=commit_sha,
        limit=limit,
        offset=offset,
    )


@agent_router.post("/test-runs")
def agent_record_test_run(
    request: Request,
    payload: TestRunRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_RECORD_TEST_RUN)
    result = runtime.journal.record_test_run(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_RECORD_TEST_RUN, result)
    return result


@agent_router.post("/handoffs/generate")
def agent_generate_handoff(
    request: Request,
    payload: HandoffGenerateRequest,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_HANDOFF)
    result = runtime.journal.generate_handoff(_agent_actor(agent), payload)
    _audit_agent_write(request, runtime, agent, TOOL_HANDOFF, result)
    return result


@agent_router.get("/handoffs")
def agent_list_handoffs(
    request: Request,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_ENTRIES)
    return runtime.journal.list_handoffs(project_id=project_id, limit=limit, offset=offset)


@agent_router.get("/handoffs/{handoff_id}")
def agent_get_handoff(
    request: Request,
    handoff_id: str,
    runtime: Runtime = Depends(get_runtime),
    agent: AgentPrincipal = Depends(get_agent),
) -> dict[str, Any]:
    _authorize(request, runtime, agent, TOOL_LIST_ENTRIES)
    return runtime.journal.get_handoff(handoff_id)


# ------------------------------------------------------------- admin routes


@admin_router.get("/project-state")
def admin_project_state(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.project_state(project_id)


@admin_router.put("/project-state")
def admin_update_project_state(
    payload: ProjectStateUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.update_project_state(_admin_actor(admin), payload)
    _audit_admin_write(runtime, admin, "journal.project_state_update", result)
    return result


@admin_router.get("/stats")
def admin_stats(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.stats(project_id)


@admin_router.get("/timeline")
def admin_timeline(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    record_type: RecordType | None = Query(default=None),
    category: Category | None = Query(default=None),
    verification_level: str | None = Query(default=None, max_length=32),
    task_id: str | None = Query(default=None, max_length=64),
    session_id: str | None = Query(default=None, max_length=64),
    actor_id: str | None = Query(default=None, max_length=64),
    commit_sha: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    since: str | None = Query(default=None, max_length=40),
    until: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=200),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.timeline(
        project_id=project_id,
        record_type=record_type,
        category=category,
        verification_level=verification_level,
        task_id=task_id,
        session_id=session_id,
        actor_id=actor_id,
        commit_sha=commit_sha,
        q=q,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@admin_router.get("/tasks")
def admin_list_tasks(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.list_tasks(project_id=project_id, status=status, q=q, limit=limit, offset=offset)


@admin_router.post("/tasks")
def admin_create_task(
    payload: TaskCreateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.create_task(_admin_actor(admin), payload)
    _audit_admin_write(runtime, admin, "journal.task_create", result)
    return result


@admin_router.patch("/tasks/{task_id}")
def admin_update_task(
    task_id: str,
    payload: TaskUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.update_task(_admin_actor(admin), task_id, payload)
    _audit_admin_write(runtime, admin, "journal.task_update", result)
    return result


@admin_router.get("/entries")
def admin_list_entries(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    task_id: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.list_entries(
        project_id=project_id, task_id=task_id, q=q, limit=limit, offset=offset
    )


@admin_router.post("/entries")
def admin_record_work(
    payload: WorkEntryRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.record_work(_admin_actor(admin), payload)
    _audit_admin_write(runtime, admin, "journal.entry_create", result)
    return result


@admin_router.post("/decisions")
def admin_record_decision(
    payload: DecisionRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.record_decision(_admin_actor(admin), payload)
    _audit_admin_write(runtime, admin, "journal.decision_create", result)
    return result


@admin_router.get("/issues")
def admin_list_issues(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.list_issues(
        project_id=project_id, status=status, kind=kind, limit=limit, offset=offset
    )


@admin_router.post("/issues")
def admin_record_issue(
    payload: IssueRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.record_issue(_admin_actor(admin), payload)
    _audit_admin_write(runtime, admin, "journal.issue_create", result)
    return result


@admin_router.patch("/issues/{issue_id}")
def admin_update_issue(
    issue_id: str,
    payload: IssueUpdateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.update_issue(_admin_actor(admin), issue_id, payload)
    _audit_admin_write(runtime, admin, "journal.issue_update", result)
    return result


@admin_router.post("/test-runs")
def admin_record_test_run(
    payload: TestRunRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.record_test_run(_admin_actor(admin), payload)
    _audit_admin_write(runtime, admin, "journal.test_run_create", result)
    return result


@admin_router.get("/sessions")
def admin_list_sessions(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    status: Literal["active", "ended"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.list_sessions(project_id=project_id, status=status, limit=limit, offset=offset)


@admin_router.post("/sessions/{session_id}/end")
def admin_end_session(
    session_id: str,
    payload: SessionEndRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.end_session(_admin_actor(admin), session_id, payload)
    _audit_admin_write(runtime, admin, "journal.session_end", result)
    return result


@admin_router.post("/handoffs/generate")
def admin_generate_handoff(
    payload: HandoffGenerateRequest,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    result = runtime.journal.generate_handoff(_admin_actor(admin), payload)
    _audit_admin_write(runtime, admin, "journal.handoff_generate", result)
    return result


@admin_router.get("/handoffs")
def admin_list_handoffs(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, max_length=64),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.list_handoffs(project_id=project_id, limit=limit, offset=offset)


@admin_router.get("/handoffs/{handoff_id}")
def admin_get_handoff(
    handoff_id: str,
    runtime: Runtime = Depends(get_runtime),
    admin: AdminPrincipal = Depends(get_admin),
) -> dict[str, Any]:
    _ = admin
    return runtime.journal.get_handoff(handoff_id)
