"""MCP exposure of the Project Journal.

Journal tools are connectionless, so they cannot ride the connector
``ToolSpec``/``OperationService`` path (which requires a vault-backed
connection).  Instead, MCP calls dispatch here and go through exactly the
same scope check, central policy evaluation, rate limit, audit, validation,
and service methods as the REST routes — one authorization path per journal
operation, two transports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from ..errors import ValidationFailure
from .models import (
    READ_TOOLS,
    TOOL_END_SESSION,
    TOOL_HANDOFF,
    TOOL_LIST_ENTRIES,
    TOOL_LIST_TASKS,
    TOOL_READ_STATE,
    TOOL_RECORD_DECISION,
    TOOL_RECORD_ISSUE,
    TOOL_RECORD_TASK,
    TOOL_RECORD_TEST_RUN,
    TOOL_RECORD_WORK,
    TOOL_START_SESSION,
    TOOL_UPDATE_STATE,
    TOOL_UPDATE_TASK,
    DecisionRequest,
    HandoffGenerateRequest,
    IssueRequest,
    ProjectStateUpdateRequest,
    SessionEndRequest,
    SessionStartRequest,
    TaskCreateRequest,
    TaskUpdateRequest,
    TestRunRequest,
    WorkEntryRequest,
)
from .service import JournalActor, authorize_agent_journal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..auth.agents import AgentPrincipal
    from ..runtime import Runtime

_DESCRIPTIONS = {
    TOOL_READ_STATE: (
        "Read the current Project Journal state: purpose, branch/commit, prioritized open tasks, "
        "blocked work, open issues, recent decisions and test evidence, with verification levels."
    ),
    TOOL_LIST_TASKS: "List Project Journal tasks (filter by status or search text).",
    TOOL_LIST_ENTRIES: (
        "Search the Project Journal timeline (work entries, decisions, issues, test runs). "
        "Use category completed/failed/blocked/unverified to filter by outcome and evidence."
    ),
    TOOL_START_SESSION: "Start a Project Journal work session for this agent. Idempotent with a key.",
    TOOL_END_SESSION: "End your own Project Journal session with a summary.",
    TOOL_RECORD_TASK: "Create a Project Journal task with priority, acceptance criteria, and dependencies.",
    TOOL_UPDATE_TASK: (
        "Update one of your Project Journal tasks. Status transitions are validated; completion "
        "requires verification_level automated_tests or integration_verified."
    ),
    TOOL_RECORD_WORK: (
        "Record meaningful work: milestone, progress, blocker, failure, or note. "
        "State the honest verification_level; a claim is stored as a claim."
    ),
    TOOL_RECORD_DECISION: "Record a technical decision with rationale and alternatives considered.",
    TOOL_RECORD_ISSUE: (
        "Record a bug, security issue, failed approach (with what was learned), or limitation."
    ),
    TOOL_RECORD_TEST_RUN: (
        "Record a real test run: exact command, status, exit code, environment, and sanitized "
        "output summary. Never invent results; use status not_run/blocked when applicable."
    ),
    TOOL_UPDATE_STATE: (
        "Update the shared project state (purpose, architecture, conventions, constraints, "
        "branch, commit). Requires expected_revision for optimistic concurrency."
    ),
    TOOL_HANDOFF: (
        "Generate and persist a bounded next-session handoff prompt from current journal records."
    ),
}

# Request models per write tool, validated identically to REST.
_WRITE_MODELS: dict[str, type[BaseModel]] = {
    TOOL_START_SESSION: SessionStartRequest,
    TOOL_RECORD_TASK: TaskCreateRequest,
    TOOL_RECORD_WORK: WorkEntryRequest,
    TOOL_RECORD_DECISION: DecisionRequest,
    TOOL_RECORD_ISSUE: IssueRequest,
    TOOL_RECORD_TEST_RUN: TestRunRequest,
    TOOL_UPDATE_STATE: ProjectStateUpdateRequest,
    TOOL_HANDOFF: HandoffGenerateRequest,
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _model_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    return schema


def _with_field(schema: dict[str, Any], name: str, definition: dict[str, Any]) -> dict[str, Any]:
    properties = {name: definition, **schema.get("properties", {})}
    required = [name, *schema.get("required", [])]
    return _schema(properties, required)


def journal_tool_schemas() -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(name, description, input_schema)`` for every journal tool."""

    project = {"type": "string", "description": "Journal project id (default: default)."}
    tools: list[tuple[str, str, dict[str, Any]]] = [
        (TOOL_READ_STATE, _DESCRIPTIONS[TOOL_READ_STATE], _schema({"project_id": project})),
        (
            TOOL_LIST_TASKS,
            _DESCRIPTIONS[TOOL_LIST_TASKS],
            _schema(
                {
                    "project_id": project,
                    "status": {"type": "string"},
                    "q": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "offset": {"type": "integer", "minimum": 0},
                }
            ),
        ),
        (
            TOOL_LIST_ENTRIES,
            _DESCRIPTIONS[TOOL_LIST_ENTRIES],
            _schema(
                {
                    "project_id": project,
                    "record_type": {"type": "string", "enum": ["entry", "decision", "issue", "test_run"]},
                    "category": {
                        "type": "string",
                        "enum": ["completed", "failed", "blocked", "unverified"],
                    },
                    "verification_level": {"type": "string"},
                    "task_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "commit_sha": {"type": "string"},
                    "q": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 200},
                }
            ),
        ),
        (
            TOOL_END_SESSION,
            _DESCRIPTIONS[TOOL_END_SESSION],
            _with_field(_model_schema(SessionEndRequest), "session_id", {"type": "string"}),
        ),
        (
            TOOL_UPDATE_TASK,
            _DESCRIPTIONS[TOOL_UPDATE_TASK],
            _with_field(_model_schema(TaskUpdateRequest), "task_id", {"type": "string"}),
        ),
    ]
    for name, model in _WRITE_MODELS.items():
        tools.append((name, _DESCRIPTIONS[name], _model_schema(model)))
    return sorted(tools, key=lambda item: item[0])


def visible_journal_tools(agent: AgentPrincipal) -> list[tuple[str, str, dict[str, Any]]]:
    result = []
    for name, description, schema in journal_tool_schemas():
        scope = "journal:read" if name in READ_TOOLS else "journal:write"
        if agent.has_scope(scope):
            result.append((name, description, schema))
    return result


def _validate(model: type[BaseModel], arguments: dict[str, Any]) -> Any:
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        safe_errors = [
            {"path": ".".join(str(part) for part in error["loc"]) or "params", "message": error["msg"]}
            for error in exc.errors()
        ]
        raise ValidationFailure("journal tool parameters are invalid", {"errors": safe_errors}) from exc


def _int_arg(arguments: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationFailure(f"{name} must be an integer between {low} and {high}")
    return value


def _str_arg(arguments: dict[str, Any], name: str, *, required: bool = False) -> str | None:
    value = arguments.get(name)
    if value is None:
        if required:
            raise ValidationFailure(f"{name} is required")
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise ValidationFailure(f"{name} must be a string")
    return value


_READ_ARG_KEYS = {
    TOOL_READ_STATE: {"project_id"},
    TOOL_LIST_TASKS: {"project_id", "status", "q", "limit", "offset"},
    TOOL_LIST_ENTRIES: {
        "project_id",
        "record_type",
        "category",
        "verification_level",
        "task_id",
        "session_id",
        "commit_sha",
        "q",
        "limit",
        "offset",
    },
}


def call_journal_tool(
    runtime: Runtime, agent: AgentPrincipal, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Authorize and execute one MCP journal call. Returns the service result."""

    authorize_agent_journal(
        policies=runtime.policies,
        limiter=runtime.rate_limiter,
        audit=runtime.audit,
        agent=agent,
        tool=name,
    )
    actor = JournalActor(actor_type="agent", actor_id=agent.id, actor_name=agent.name, source="mcp")
    journal = runtime.journal
    project_id = _str_arg(arguments, "project_id") or "default"
    result: dict[str, Any]

    if name in READ_TOOLS:
        unknown = set(arguments) - _READ_ARG_KEYS.get(name, set())
        if unknown:
            raise ValidationFailure("journal tool parameters are invalid", {"unknown": sorted(unknown)})
        if name == TOOL_READ_STATE:
            return journal.project_state(project_id)
        if name == TOOL_LIST_TASKS:
            return journal.list_tasks(
                project_id=project_id,
                status=_str_arg(arguments, "status"),
                q=_str_arg(arguments, "q"),
                limit=_int_arg(arguments, "limit", 50, 1, 200),
                offset=_int_arg(arguments, "offset", 0, 0, 100_000),
            )
        if name == TOOL_LIST_ENTRIES:
            return journal.timeline(
                project_id=project_id,
                record_type=_str_arg(arguments, "record_type"),
                category=_str_arg(arguments, "category"),
                verification_level=_str_arg(arguments, "verification_level"),
                task_id=_str_arg(arguments, "task_id"),
                session_id=_str_arg(arguments, "session_id"),
                commit_sha=_str_arg(arguments, "commit_sha"),
                q=_str_arg(arguments, "q"),
                limit=_int_arg(arguments, "limit", 50, 1, 200),
                offset=_int_arg(arguments, "offset", 0, 0, 200),
            )
        raise ValidationFailure("journal tool is not available over MCP; use the REST API")

    if name == TOOL_END_SESSION:
        session_id = _str_arg(arguments, "session_id", required=True)
        assert session_id is not None
        req = _validate(SessionEndRequest, {"summary": arguments.get("summary", "")})
        result = journal.end_session(actor, session_id, req)
    elif name == TOOL_UPDATE_TASK:
        task_id = _str_arg(arguments, "task_id", required=True)
        assert task_id is not None
        rest = {key: value for key, value in arguments.items() if key != "task_id"}
        result = journal.update_task(actor, task_id, _validate(TaskUpdateRequest, rest))
    elif name in _WRITE_MODELS:
        req = _validate(_WRITE_MODELS[name], arguments)
        if name == TOOL_START_SESSION:
            result = journal.start_session(actor, req)
        elif name == TOOL_RECORD_TASK:
            result = journal.create_task(actor, req)
        elif name == TOOL_RECORD_WORK:
            result = journal.record_work(actor, req)
        elif name == TOOL_RECORD_DECISION:
            result = journal.record_decision(actor, req)
        elif name == TOOL_RECORD_ISSUE:
            result = journal.record_issue(actor, req)
        elif name == TOOL_RECORD_TEST_RUN:
            result = journal.record_test_run(actor, req)
        elif name == TOOL_UPDATE_STATE:
            result = journal.update_project_state(actor, req)
        else:  # TOOL_HANDOFF
            result = journal.generate_handoff(actor, req)
    else:
        raise ValidationFailure("unknown journal tool")

    runtime.audit.record(
        actor_type="agent",
        actor_id=agent.id,
        agent_id=agent.id,
        tool=name,
        decision="allow",
        status="ok",
        params={
            "record_id": result.get("id"),
            "project_id": result.get("project_id", project_id),
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "source": "mcp",
        },
    )
    return result
