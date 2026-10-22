"""Project Journal vocabulary, limits, and validated request models.

These models are the single input contract for the admin API, the agent REST
API, and the MCP journal tools, so every surface validates identically.
Unknown fields are rejected (``extra="forbid"``), free text is length-capped,
and every stored string is redacted by the service layer before it reaches
SQLite.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils import parse_iso

# ---------------------------------------------------------------- vocabulary

#: How well a claim is backed.  ``claimed`` is an agent statement only; it is
#: never upgraded silently and never counts as verification.
VerificationLevel = Literal[
    "claimed",
    "automated_tests",
    "integration_verified",
    "not_tested",
    "blocked_infrastructure",
]
VERIFICATION_LEVELS: tuple[str, ...] = (
    "claimed",
    "automated_tests",
    "integration_verified",
    "not_tested",
    "blocked_infrastructure",
)
#: Levels that count as verified evidence for completion and UI filters.
VERIFIED_LEVELS: frozenset[str] = frozenset({"automated_tests", "integration_verified"})
#: Levels the UI/API surface as "unverified".
UNVERIFIED_LEVELS: frozenset[str] = frozenset({"claimed", "not_tested", "blocked_infrastructure"})

TaskStatus = Literal["todo", "in_progress", "blocked", "completed", "failed", "cancelled"]
TASK_STATUSES: tuple[str, ...] = ("todo", "in_progress", "blocked", "completed", "failed", "cancelled")
#: Allowed task status transitions.  ``completed`` and ``cancelled`` are
#: terminal: history is not rewritten, follow-up work is a new task.
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "todo": frozenset({"in_progress", "blocked", "cancelled"}),
    "in_progress": frozenset({"blocked", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"in_progress", "todo", "cancelled"}),
    "failed": frozenset({"todo", "in_progress", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}
OPEN_TASK_STATUSES: frozenset[str] = frozenset({"todo", "in_progress", "blocked"})

EntryKind = Literal["milestone", "progress", "blocker", "failure", "note"]
EntryOutcome = Literal["completed", "failed", "blocked", "in_progress", "info"]
IssueKind = Literal["bug", "security", "failed_approach", "limitation"]
IssueStatus = Literal["open", "resolved", "wontfix"]
ISSUE_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"resolved", "wontfix"}),
    "resolved": frozenset({"open"}),
    "wontfix": frozenset({"open"}),
}
Severity = Literal["low", "medium", "high", "critical"]
DecisionStatus = Literal["accepted", "superseded", "rejected"]
TestRunStatus = Literal["passed", "failed", "error", "not_run", "blocked"]
SessionStatus = Literal["active", "ended"]

RECORD_TYPES: tuple[str, ...] = ("entry", "decision", "issue", "test_run", "task", "handoff", "session")

# Pseudo-tool names evaluated through the central PolicyService.  They follow
# the policy TOOL_PATTERN so administrators can allow e.g. ``journal.*`` or
# only ``journal.read_project_state`` in a normal policy document.
TOOL_READ_STATE = "journal.read_project_state"
TOOL_LIST_TASKS = "journal.list_tasks"
TOOL_LIST_ENTRIES = "journal.list_entries"
TOOL_LIST_TEST_RUNS = "journal.list_test_runs"
TOOL_LIST_SESSIONS = "journal.list_sessions"
TOOL_START_SESSION = "journal.start_session"
TOOL_END_SESSION = "journal.end_session"
TOOL_RECORD_TASK = "journal.record_task"
TOOL_UPDATE_TASK = "journal.update_task"
TOOL_RECORD_WORK = "journal.record_work"
TOOL_RECORD_DECISION = "journal.record_decision"
TOOL_RECORD_ISSUE = "journal.record_issue"
TOOL_UPDATE_ISSUE = "journal.update_issue"
TOOL_RECORD_TEST_RUN = "journal.record_test_run"
TOOL_UPDATE_STATE = "journal.update_project_state"
TOOL_HANDOFF = "journal.generate_handoff"

READ_TOOLS: frozenset[str] = frozenset(
    {TOOL_READ_STATE, TOOL_LIST_TASKS, TOOL_LIST_ENTRIES, TOOL_LIST_TEST_RUNS, TOOL_LIST_SESSIONS}
)
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        TOOL_START_SESSION,
        TOOL_END_SESSION,
        TOOL_RECORD_TASK,
        TOOL_UPDATE_TASK,
        TOOL_RECORD_WORK,
        TOOL_RECORD_DECISION,
        TOOL_RECORD_ISSUE,
        TOOL_UPDATE_ISSUE,
        TOOL_RECORD_TEST_RUN,
        TOOL_UPDATE_STATE,
        TOOL_HANDOFF,
    }
)

DEFAULT_PROJECT_ID = "default"

# --------------------------------------------------------------------- limits

MAX_TITLE = 200
MAX_BODY = 8_000
MAX_LONG_TEXT = 4_000
MAX_COMMAND = 500
MAX_OUTPUT_SUMMARY = 4_000
MAX_FILES = 50
MAX_FILE_PATH = 300
MAX_DEPENDENCIES = 50
MAX_LIMIT = 200
MAX_HANDOFF_CHARS = 16_000
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_file_refs(values: list[str]) -> list[str]:
    """File references are repository-relative paths, never traversals."""

    if len(values) > MAX_FILES:
        raise ValueError("too many file references")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or len(value) > MAX_FILE_PATH:
            raise ValueError("file reference is invalid")
        if value.startswith("/") or "\\" in value or "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("file reference must be a repository-relative path")
        if ".." in value.split("/"):
            raise ValueError("file reference must not contain '..'")
        cleaned.append(value)
    return cleaned


def _commit(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if not COMMIT_RE.fullmatch(value):
        raise ValueError("commit must be a hexadecimal revision of 7-64 characters")
    return value.lower()


def _branch(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if not BRANCH_RE.fullmatch(value) or ".." in value or value.endswith("/"):
        raise ValueError("branch name is invalid")
    return value


def _idempotency(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if not IDEMPOTENCY_RE.fullmatch(value):
        raise ValueError("idempotency_key must be 8-128 characters of [A-Za-z0-9._:-]")
    return value


def _iso_or_none(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    try:
        parse_iso(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    return value


def _id_ref(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if len(value) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("identifier is invalid")
    return value


def _project_id(value: str) -> str:
    if not PROJECT_ID_RE.fullmatch(value):
        raise ValueError("project_id is invalid")
    return value


class StrictJournalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStartRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    title: str = Field(default="", max_length=MAX_TITLE)
    branch: str | None = None
    commit_sha: str | None = None
    idempotency_key: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_branch = field_validator("branch")(_branch)
    _check_commit = field_validator("commit_sha")(_commit)
    _check_key = field_validator("idempotency_key")(_idempotency)


class SessionEndRequest(StrictJournalRequest):
    summary: str = Field(default="", max_length=MAX_LONG_TEXT)


class ProjectStateUpdateRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=MAX_TITLE)
    purpose: str | None = Field(default=None, max_length=MAX_LONG_TEXT)
    architecture: str | None = Field(default=None, max_length=MAX_LONG_TEXT)
    conventions: str | None = Field(default=None, max_length=MAX_LONG_TEXT)
    constraints_text: str | None = Field(default=None, max_length=MAX_LONG_TEXT)
    branch: str | None = None
    commit_sha: str | None = None
    session_id: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_branch = field_validator("branch")(_branch)
    _check_commit = field_validator("commit_sha")(_commit)
    _check_session = field_validator("session_id")(_id_ref)


class TaskCreateRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    session_id: str | None = None
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str = Field(default="", max_length=MAX_BODY)
    status: TaskStatus = "todo"
    priority: int = Field(default=50, ge=0, le=100)
    acceptance_criteria: str = Field(default="", max_length=MAX_LONG_TEXT)
    verification_level: VerificationLevel = "claimed"
    dependencies: list[str] = Field(default_factory=list, max_length=MAX_DEPENDENCIES)
    branch: str | None = None
    commit_sha: str | None = None
    files: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_session = field_validator("session_id")(_id_ref)
    _check_branch = field_validator("branch")(_branch)
    _check_commit = field_validator("commit_sha")(_commit)
    _check_key = field_validator("idempotency_key")(_idempotency)
    _check_files = field_validator("files")(validate_file_refs)

    @field_validator("dependencies")
    @classmethod
    def _check_deps(cls, values: list[str]) -> list[str]:
        for value in values:
            _id_ref(value)
        return list(dict.fromkeys(values))


class TaskUpdateRequest(StrictJournalRequest):
    status: TaskStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_BODY)
    priority: int | None = Field(default=None, ge=0, le=100)
    acceptance_criteria: str | None = Field(default=None, max_length=MAX_LONG_TEXT)
    verification_level: VerificationLevel | None = None
    dependencies: list[str] | None = Field(default=None, max_length=MAX_DEPENDENCIES)
    branch: str | None = None
    commit_sha: str | None = None
    files: list[str] | None = None

    _check_branch = field_validator("branch")(_branch)
    _check_commit = field_validator("commit_sha")(_commit)

    @field_validator("dependencies")
    @classmethod
    def _check_deps(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        for value in values:
            _id_ref(value)
        return list(dict.fromkeys(values))

    @field_validator("files")
    @classmethod
    def _check_files(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return validate_file_refs(values)


class WorkEntryRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    session_id: str | None = None
    task_id: str | None = None
    kind: EntryKind = "note"
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    body: str = Field(default="", max_length=MAX_BODY)
    outcome: EntryOutcome = "info"
    verification_level: VerificationLevel = "claimed"
    files: list[str] = Field(default_factory=list)
    commit_sha: str | None = None
    branch: str | None = None
    idempotency_key: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_session = field_validator("session_id")(_id_ref)
    _check_task = field_validator("task_id")(_id_ref)
    _check_branch = field_validator("branch")(_branch)
    _check_commit = field_validator("commit_sha")(_commit)
    _check_key = field_validator("idempotency_key")(_idempotency)
    _check_files = field_validator("files")(validate_file_refs)


class DecisionRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    session_id: str | None = None
    task_id: str | None = None
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    decision: str = Field(min_length=1, max_length=MAX_LONG_TEXT)
    rationale: str = Field(default="", max_length=MAX_BODY)
    alternatives: str = Field(default="", max_length=MAX_LONG_TEXT)
    status: DecisionStatus = "accepted"
    files: list[str] = Field(default_factory=list)
    commit_sha: str | None = None
    idempotency_key: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_session = field_validator("session_id")(_id_ref)
    _check_task = field_validator("task_id")(_id_ref)
    _check_commit = field_validator("commit_sha")(_commit)
    _check_key = field_validator("idempotency_key")(_idempotency)
    _check_files = field_validator("files")(validate_file_refs)


class IssueRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    session_id: str | None = None
    task_id: str | None = None
    kind: IssueKind = "bug"
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    body: str = Field(default="", max_length=MAX_BODY)
    learned: str = Field(default="", max_length=MAX_LONG_TEXT)
    severity: Severity = "medium"
    verification_level: VerificationLevel = "claimed"
    files: list[str] = Field(default_factory=list)
    commit_sha: str | None = None
    idempotency_key: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_session = field_validator("session_id")(_id_ref)
    _check_task = field_validator("task_id")(_id_ref)
    _check_commit = field_validator("commit_sha")(_commit)
    _check_key = field_validator("idempotency_key")(_idempotency)
    _check_files = field_validator("files")(validate_file_refs)


class IssueUpdateRequest(StrictJournalRequest):
    status: IssueStatus


class TestRunRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    session_id: str | None = None
    task_id: str | None = None
    command: str = Field(min_length=1, max_length=MAX_COMMAND)
    status: TestRunStatus = "passed"
    exit_code: int | None = Field(default=None, ge=-1, le=255)
    verification_level: VerificationLevel = "claimed"
    output_summary: str = Field(default="", max_length=MAX_OUTPUT_SUMMARY)
    environment: str = Field(default="", max_length=MAX_TITLE)
    commit_sha: str | None = None
    run_at: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0, le=86_400)
    idempotency_key: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_session = field_validator("session_id")(_id_ref)
    _check_task = field_validator("task_id")(_id_ref)
    _check_commit = field_validator("commit_sha")(_commit)
    _check_key = field_validator("idempotency_key")(_idempotency)
    _check_run_at = field_validator("run_at")(_iso_or_none)


class HandoffGenerateRequest(StrictJournalRequest):
    project_id: str = DEFAULT_PROJECT_ID
    session_id: str | None = None
    focus_task_id: str | None = None
    title: str = Field(default="", max_length=MAX_TITLE)
    max_entries: int = Field(default=20, ge=1, le=100)
    idempotency_key: str | None = None

    _check_project = field_validator("project_id")(_project_id)
    _check_session = field_validator("session_id")(_id_ref)
    _check_task = field_validator("focus_task_id")(_id_ref)
    _check_key = field_validator("idempotency_key")(_idempotency)
