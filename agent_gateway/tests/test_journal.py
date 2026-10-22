"""Project Journal: persistence, lifecycle, authorization, handoff, redaction.

These tests run against the real ASGI apps and the real SQLite file.  Claims
made here are limited to that: no Home Assistant OS, Supervisor, or live MCP
client is involved.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from helpers import ADMIN_HEADERS, auth_headers, create_agent
from starlette.applications import Starlette

from gateway.api.apps import build_admin_app, build_agent_app
from gateway.runtime import Runtime
from gateway.settings import Settings
from gateway.storage.db import Database

JOURNAL_POLICY: dict[str, Any] = {
    "version": 1,
    "rules": [{"id": "journal-all", "effect": "allow", "tools": ["journal.*"], "connections": ["*"]}],
}
JOURNAL_SCOPES = ["journal:read", "journal:write"]
FAKE_SECRET = "hunter2-super-secret-value"


async def journal_agent(
    admin: httpx.AsyncClient,
    *,
    name: str = "journal-agent",
    scopes: list[str] | None = None,
    policy: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    return await create_agent(
        admin,
        name=name,
        scopes=scopes if scopes is not None else JOURNAL_SCOPES,
        policy=policy if policy is not None else JOURNAL_POLICY,
    )


async def post_entry(client: httpx.AsyncClient, token: str, **overrides: Any) -> httpx.Response:
    body: dict[str, Any] = {"title": "Implemented the journal API", "outcome": "info"}
    body.update(overrides)
    return await client.post("/api/v1/journal/entries", json=body, headers=auth_headers(token))


# ---------------------------------------------------------- schema & restart


def test_schema_v3_creates_journal_tables(runtime: Runtime) -> None:
    assert runtime.db.schema_info()["version"] == 3
    rows = runtime.db.query_all("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'journal_%'")
    names = {str(row["name"]) for row in rows}
    assert {
        "journal_projects",
        "journal_sessions",
        "journal_tasks",
        "journal_task_dependencies",
        "journal_entries",
        "journal_decisions",
        "journal_issues",
        "journal_test_runs",
        "journal_handoffs",
    } <= names


def test_v2_database_upgrades_to_v3_preserving_records(tmp_path: Path) -> None:
    """Simulate a pre-journal install: v2 meta, no journal tables, one agent."""

    path = tmp_path / "gateway.db"
    db = Database(path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents(id, name, token_hash, token_prefix, scopes, enabled, created_at, "
            "updated_at, expires_at, last_used_at, last_rotated_at, notes) "
            "VALUES('a'*32, 'legacy-agent', 'h'*64, 'hag_x', '[\"ha:read\"]', 1, "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL, NULL, NULL, '')"
        )
        for table in (
            "journal_handoffs",
            "journal_test_runs",
            "journal_issues",
            "journal_decisions",
            "journal_entries",
            "journal_task_dependencies",
            "journal_tasks",
            "journal_sessions",
            "journal_projects",
        ):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    db.close()

    upgraded = Database(path)
    try:
        info = upgraded.schema_info()
        assert info["version"] == 3
        assert info["migrated_from"] == 2
        agent = upgraded.query_one("SELECT name FROM agents WHERE name='legacy-agent'")
        assert agent is not None
        tables = upgraded.query_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='journal_tasks'"
        )
        assert len(tables) == 1
    finally:
        upgraded.close()


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "gateway.db"
    db = Database(path)
    db.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
    db.close()
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)


def test_journal_records_persist_across_runtime_restart(settings: Settings) -> None:
    from gateway.journal.models import TaskCreateRequest, WorkEntryRequest
    from gateway.journal.service import JournalActor

    actor = JournalActor(actor_type="agent", actor_id="agent-1", actor_name="agent-one", source="rest")
    first = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    first.startup()
    task = first.journal.create_task(actor, TaskCreateRequest(title="Persist me"))
    first.journal.record_work(
        actor,
        WorkEntryRequest(title="Wrote code", task_id=task["id"], verification_level="claimed"),
    )
    first.shutdown()

    second = Runtime(settings, kdf_memory_cost=1024, kdf_time_cost=1)
    second.startup()
    try:
        restored = second.journal.get_task(task["id"])
        assert restored["title"] == "Persist me"
        entries = second.journal.list_entries(task_id=task["id"])
        assert entries["total"] == 1
        assert entries["items"][0]["title"] == "Wrote code"
        # The raw file also survives a plain sqlite read (no in-memory state).
        raw = sqlite3.connect(str(settings.database_path))
        try:
            count = raw.execute("SELECT COUNT(*) FROM journal_tasks").fetchone()[0]
        finally:
            raw.close()
        assert count == 1
    finally:
        second.shutdown()


# ------------------------------------------------------------------- sessions


async def test_session_lifecycle_and_idempotent_start(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    body = {"title": "Journal work", "branch": "arena/test", "idempotency_key": "session-key-0001"}
    first = await agent_api.post("/api/v1/journal/sessions", json=body, headers=auth_headers(token))
    assert first.status_code == 200, first.text
    session = first.json()
    assert session["status"] == "active"
    assert session["idempotent_replay"] is False

    replay = await agent_api.post("/api/v1/journal/sessions", json=body, headers=auth_headers(token))
    assert replay.status_code == 200
    assert replay.json()["id"] == session["id"]
    assert replay.json()["idempotent_replay"] is True

    listing = await agent_api.get(
        "/api/v1/journal/sessions", params={"status": "active"}, headers=auth_headers(token)
    )
    assert listing.json()["total"] == 1

    ended = await agent_api.post(
        f"/api/v1/journal/sessions/{session['id']}/end",
        json={"summary": "Done for today"},
        headers=auth_headers(token),
    )
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert ended.json()["ended_at"]

    again = await agent_api.post(
        f"/api/v1/journal/sessions/{session['id']}/end",
        json={"summary": ""},
        headers=auth_headers(token),
    )
    assert again.status_code == 409


async def test_duplicate_entry_with_same_idempotency_key_is_not_duplicated(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient, runtime: Runtime
) -> None:
    _, token = await journal_agent(admin)
    first = await post_entry(agent_api, token, idempotency_key="entry-key-00001")
    second = await post_entry(agent_api, token, idempotency_key="entry-key-00001")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["idempotent_replay"] is True
    rows = runtime.db.query_all("SELECT id FROM journal_entries")
    assert len(rows) == 1


# ---------------------------------------------------------------------- tasks


async def test_task_status_transitions_and_completion_evidence_rule(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    created = await agent_api.post(
        "/api/v1/journal/tasks",
        json={"title": "Ship journal", "priority": 5, "acceptance_criteria": "tests pass"},
        headers=auth_headers(token),
    )
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["status"] == "todo"
    assert task["verified"] is False

    # todo -> completed is not a legal transition.
    bad = await agent_api.patch(
        f"/api/v1/journal/tasks/{task['id']}",
        json={"status": "completed", "verification_level": "automated_tests"},
        headers=auth_headers(token),
    )
    assert bad.status_code == 409

    started = await agent_api.patch(
        f"/api/v1/journal/tasks/{task['id']}",
        json={"status": "in_progress"},
        headers=auth_headers(token),
    )
    assert started.status_code == 200

    # Completion on a mere claim is refused.
    claimed = await agent_api.patch(
        f"/api/v1/journal/tasks/{task['id']}",
        json={"status": "completed"},
        headers=auth_headers(token),
    )
    assert claimed.status_code == 422
    assert "automated_tests" in claimed.text

    done = await agent_api.patch(
        f"/api/v1/journal/tasks/{task['id']}",
        json={"status": "completed", "verification_level": "automated_tests"},
        headers=auth_headers(token),
    )
    assert done.status_code == 200
    assert done.json()["verified"] is True
    assert done.json()["completed_at"]

    # Terminal states are final.
    reopened = await agent_api.patch(
        f"/api/v1/journal/tasks/{task['id']}",
        json={"status": "in_progress"},
        headers=auth_headers(token),
    )
    assert reopened.status_code == 409


async def test_task_dependencies_block_and_cycle_is_rejected(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)

    async def make_task(title: str, **kwargs: Any) -> dict[str, Any]:
        response = await agent_api.post(
            "/api/v1/journal/tasks", json={"title": title, **kwargs}, headers=auth_headers(token)
        )
        assert response.status_code == 200, response.text
        return response.json()

    prerequisite = await make_task("Schema first")
    dependent = await make_task("API second", dependencies=[prerequisite["id"]])
    assert dependent["blocked_by"] == [prerequisite["id"]]

    blocked = await agent_api.patch(
        f"/api/v1/journal/tasks/{dependent['id']}",
        json={"status": "in_progress"},
        headers=auth_headers(token),
    )
    assert blocked.status_code == 409
    assert prerequisite["id"] in blocked.text

    cycle = await agent_api.patch(
        f"/api/v1/journal/tasks/{prerequisite['id']}",
        json={"dependencies": [dependent["id"]]},
        headers=auth_headers(token),
    )
    assert cycle.status_code == 422
    assert "cycle" in cycle.text

    await agent_api.patch(
        f"/api/v1/journal/tasks/{prerequisite['id']}",
        json={"status": "in_progress"},
        headers=auth_headers(token),
    )
    await agent_api.patch(
        f"/api/v1/journal/tasks/{prerequisite['id']}",
        json={"status": "completed", "verification_level": "integration_verified"},
        headers=auth_headers(token),
    )
    unblocked = await agent_api.patch(
        f"/api/v1/journal/tasks/{dependent['id']}",
        json={"status": "in_progress"},
        headers=auth_headers(token),
    )
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["blocked_by"] == []


# -------------------------------------------------------- entries & evidence


async def test_claims_are_separated_from_verified_results(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    await post_entry(agent_api, token, title="Claimed fix", outcome="completed")
    await post_entry(
        agent_api,
        token,
        title="Verified fix",
        outcome="completed",
        verification_level="automated_tests",
    )
    run = await agent_api.post(
        "/api/v1/journal/test-runs",
        json={
            "command": "pytest -q",
            "status": "passed",
            "exit_code": 0,
            "verification_level": "claimed",
        },
        headers=auth_headers(token),
    )
    assert run.status_code == 200
    # Even a passing run is only a claim until its level says otherwise.
    assert run.json()["verified"] is False

    unverified = await agent_api.get(
        "/api/v1/journal/timeline",
        params={"category": "unverified"},
        headers=auth_headers(token),
    )
    titles = {item["title"] for item in unverified.json()["items"]}
    assert "Claimed fix" in titles
    assert "Verified fix" not in titles

    completed = await agent_api.get(
        "/api/v1/journal/timeline",
        params={"category": "completed", "record_type": "entry"},
        headers=auth_headers(token),
    )
    levels = {item["title"]: item["verification_level"] for item in completed.json()["items"]}
    assert levels == {"Claimed fix": "claimed", "Verified fix": "automated_tests"}


async def test_secrets_are_redacted_before_storage(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient, runtime: Runtime
) -> None:
    _, token = await journal_agent(admin)
    response = await agent_api.post(
        "/api/v1/journal/test-runs",
        json={
            "command": f"deploy --password={FAKE_SECRET}",
            "status": "failed",
            "exit_code": 1,
            "output_summary": (
                f"Authorization: Bearer abcdefghijklmnop token={FAKE_SECRET} "
                "-----BEGIN PRIVATE KEY-----abc-----END PRIVATE KEY-----"
            ),
        },
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert FAKE_SECRET not in json.dumps(payload)
    assert "[REDACTED]" in payload["command"]
    assert "[REDACTED]" in payload["output_summary"]

    row = runtime.db.query_one("SELECT command, output_summary FROM journal_test_runs")
    assert row is not None
    assert FAKE_SECRET not in row["command"]
    assert FAKE_SECRET not in row["output_summary"]
    assert "BEGIN PRIVATE KEY" not in row["output_summary"]


async def test_search_commit_filter_and_pagination(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    for index in range(5):
        await post_entry(
            agent_api,
            token,
            title=f"Timeline item {index}",
            body="needle-work",
            commit_sha="abcdef1234567" if index % 2 == 0 else None,
        )
    searched = await agent_api.get(
        "/api/v1/journal/timeline", params={"q": "needle-work"}, headers=auth_headers(token)
    )
    assert searched.json()["total"] == 5

    by_commit = await agent_api.get(
        "/api/v1/journal/timeline",
        params={"commit_sha": "abcdef1234567", "record_type": "entry"},
        headers=auth_headers(token),
    )
    assert by_commit.json()["total"] == 3

    page_one = await agent_api.get(
        "/api/v1/journal/entries", params={"limit": 2, "offset": 0}, headers=auth_headers(token)
    )
    page_two = await agent_api.get(
        "/api/v1/journal/entries", params={"limit": 2, "offset": 2}, headers=auth_headers(token)
    )
    assert page_one.json()["total"] == 5
    ids = {i["id"] for i in page_one.json()["items"]} | {i["id"] for i in page_two.json()["items"]}
    assert len(ids) == 4


async def test_failed_and_blocked_work_is_visible_in_timeline(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    await post_entry(agent_api, token, title="Broke the build", outcome="failed", kind="failure")
    await post_entry(agent_api, token, title="Waiting on lab", outcome="blocked", kind="blocker")
    issue = await agent_api.post(
        "/api/v1/journal/issues",
        json={
            "kind": "failed_approach",
            "title": "Tried rewriting the vault",
            "learned": "Envelope format is load-bearing; do not touch it.",
        },
        headers=auth_headers(token),
    )
    assert issue.status_code == 200

    failed = await agent_api.get(
        "/api/v1/journal/timeline", params={"category": "failed"}, headers=auth_headers(token)
    )
    failed_titles = {item["title"] for item in failed.json()["items"]}
    assert "Broke the build" in failed_titles
    assert "Tried rewriting the vault" in failed_titles

    blocked = await agent_api.get(
        "/api/v1/journal/timeline", params={"category": "blocked"}, headers=auth_headers(token)
    )
    assert "Waiting on lab" in {item["title"] for item in blocked.json()["items"]}


# ------------------------------------------------------------ project state


async def test_project_state_optimistic_concurrency_and_attribution(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    state = await agent_api.get("/api/v1/journal/project-state", headers=auth_headers(token))
    assert state.status_code == 200
    revision = state.json()["project"]["revision"]

    updated = await agent_api.put(
        "/api/v1/journal/project-state",
        json={
            "expected_revision": revision,
            "purpose": "Policy-controlled gateway",
            "branch": "arena/journal",
            "commit_sha": "0123456789abcdef",
        },
        headers=auth_headers(token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == revision + 1
    assert updated.json()["updated_by"]["actor_type"] == "agent"

    stale = await agent_api.put(
        "/api/v1/journal/project-state",
        json={"expected_revision": revision, "purpose": "stale write"},
        headers=auth_headers(token),
    )
    assert stale.status_code == 409


# ------------------------------------------------------------------ handoff


async def test_handoff_is_generated_from_real_records(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    state = await agent_api.get("/api/v1/journal/project-state", headers=auth_headers(token))
    revision = state.json()["project"]["revision"]
    await agent_api.put(
        "/api/v1/journal/project-state",
        json={
            "expected_revision": revision,
            "name": "HA Agent Gateway",
            "purpose": "Policy-controlled gateway for AI agents",
            "branch": "arena/journal",
            "commit_sha": "0123456789abcdef",
        },
        headers=auth_headers(token),
    )
    next_task = await agent_api.post(
        "/api/v1/journal/tasks",
        json={
            "title": "Wire the Journal UI",
            "priority": 1,
            "acceptance_criteria": "Panel lists real entries; pytest passes",
            "status": "in_progress",
        },
        headers=auth_headers(token),
    )
    assert next_task.status_code == 200, next_task.text
    await agent_api.post(
        "/api/v1/journal/tasks",
        json={"title": "Lab-only verification", "priority": 2, "status": "blocked"},
        headers=auth_headers(token),
    )
    await post_entry(
        agent_api,
        token,
        title="UI attempt failed",
        outcome="failed",
        kind="failure",
        verification_level="automated_tests",
    )
    await agent_api.post(
        "/api/v1/journal/issues",
        json={
            "kind": "failed_approach",
            "title": "Inline styles in panel",
            "learned": "Strict CSP drops inline styles; use utility classes.",
        },
        headers=auth_headers(token),
    )
    await agent_api.post(
        "/api/v1/journal/test-runs",
        json={
            "command": "pytest -q",
            "status": "passed",
            "exit_code": 0,
            "verification_level": "automated_tests",
            "environment": "sandbox py3.11",
        },
        headers=auth_headers(token),
    )

    generated = await agent_api.post(
        "/api/v1/journal/handoffs/generate",
        json={"focus_task_id": next_task.json()["id"], "idempotency_key": "handoff-key-0001"},
        headers=auth_headers(token),
    )
    assert generated.status_code == 200, generated.text
    handoff = generated.json()
    prompt = handoff["prompt"]
    assert "HA Agent Gateway" in prompt
    assert "arena/journal" in prompt
    assert "Wire the Journal UI" in prompt
    assert "pytest passes" in prompt  # acceptance criteria carried over
    assert "Lab-only verification" in prompt  # blocked work is not dropped
    assert "UI attempt failed" in prompt
    assert "utility classes" in prompt  # failed-approach lesson
    assert "pytest -q" in prompt
    assert "untrusted data" in prompt
    assert handoff["included"]["focus_task_id"] == next_task.json()["id"]

    fetched = await agent_api.get(f"/api/v1/journal/handoffs/{handoff['id']}", headers=auth_headers(token))
    assert fetched.json()["prompt"] == prompt

    replay = await agent_api.post(
        "/api/v1/journal/handoffs/generate",
        json={"idempotency_key": "handoff-key-0001"},
        headers=auth_headers(token),
    )
    assert replay.json()["id"] == handoff["id"]
    assert replay.json()["idempotent_replay"] is True


async def test_empty_journal_handoff_and_lists(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    timeline = await agent_api.get("/api/v1/journal/timeline", headers=auth_headers(token))
    assert timeline.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    stats = await agent_api.get("/api/v1/journal/stats", headers=auth_headers(token))
    assert stats.json()["entries"] == 0
    generated = await agent_api.post(
        "/api/v1/journal/handoffs/generate", json={}, headers=auth_headers(token)
    )
    assert generated.status_code == 200
    assert "No test runs recorded" in generated.json()["prompt"]
    assert "Nothing is verified" in generated.json()["prompt"]


# ------------------------------------------------------------- authorization


async def test_agent_without_journal_scope_is_denied(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await create_agent(admin, name="ha-only", scopes=["ha:read"], policy=JOURNAL_POLICY)
    response = await agent_api.get("/api/v1/journal/project-state", headers=auth_headers(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_denied"


async def test_agent_with_scope_but_no_policy_rule_is_denied(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin, name="no-policy", policy={"version": 1, "rules": []})
    response = await agent_api.get("/api/v1/journal/project-state", headers=auth_headers(token))
    assert response.status_code == 403
    assert "policy_denied" in response.text


async def test_read_only_agent_cannot_write(admin: httpx.AsyncClient, agent_api: httpx.AsyncClient) -> None:
    _, token = await journal_agent(admin, name="reader", scopes=["journal:read"])
    read = await agent_api.get("/api/v1/journal/project-state", headers=auth_headers(token))
    assert read.status_code == 200
    write = await post_entry(agent_api, token)
    assert write.status_code == 403


async def test_agents_cannot_modify_each_others_tasks_or_sessions(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token_a = await journal_agent(admin, name="agent-a")
    _, token_b = await journal_agent(admin, name="agent-b")
    session = await agent_api.post(
        "/api/v1/journal/sessions", json={"title": "A session"}, headers=auth_headers(token_a)
    )
    task = await agent_api.post(
        "/api/v1/journal/tasks",
        json={"title": "A task", "session_id": session.json()["id"]},
        headers=auth_headers(token_a),
    )
    assert task.status_code == 200

    # Shared project reads are fine...
    read = await agent_api.get(f"/api/v1/journal/tasks/{task.json()['id']}", headers=auth_headers(token_b))
    assert read.status_code == 200
    # ...but writes to another agent's records are not.
    patch = await agent_api.patch(
        f"/api/v1/journal/tasks/{task.json()['id']}",
        json={"status": "in_progress"},
        headers=auth_headers(token_b),
    )
    assert patch.status_code == 403
    end_theirs = await agent_api.post(
        f"/api/v1/journal/sessions/{session.json()['id']}/end",
        json={"summary": ""},
        headers=auth_headers(token_b),
    )
    assert end_theirs.status_code == 403
    use_theirs = await post_entry(agent_api, token_b, session_id=session.json()["id"])
    assert use_theirs.status_code == 422


async def test_require_approval_rule_fails_closed_for_journal(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    policy = {
        "version": 1,
        "rules": [
            {
                "id": "journal-gated",
                "effect": "allow",
                "tools": ["journal.*"],
                "connections": ["*"],
                "require_approval": True,
            }
        ],
    }
    _, token = await journal_agent(admin, name="gated", policy=policy)
    response = await post_entry(agent_api, token)
    assert response.status_code == 403
    assert "approval-gated" in response.text


async def test_journal_policy_rate_limit_is_enforced(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    policy = {
        "version": 1,
        "rules": [
            {
                "id": "journal-slow",
                "effect": "allow",
                "tools": ["journal.*"],
                "connections": ["*"],
                "limits": {"rate_limit_per_minute": 2},
            }
        ],
    }
    _, token = await journal_agent(admin, name="slow", policy=policy)
    assert (await post_entry(agent_api, token, title="one")).status_code == 200
    assert (await post_entry(agent_api, token, title="two")).status_code == 200
    third = await post_entry(agent_api, token, title="three")
    assert third.status_code == 429


async def test_journal_requires_a_bearer_token(agent_api: httpx.AsyncClient) -> None:
    response = await agent_api.get("/api/v1/journal/project-state")
    assert response.status_code == 401


async def test_journal_writes_and_denials_are_audited(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient, runtime: Runtime
) -> None:
    _, token = await journal_agent(admin)
    await post_entry(agent_api, token)
    events = runtime.audit.list_events(tool="journal.record_work")
    assert events["total"] == 1
    assert events["items"][0]["decision"] == "allow"

    _, denied_token = await journal_agent(admin, name="denied-agent", policy={"version": 1, "rules": []})
    await post_entry(agent_api, denied_token)
    denials = runtime.audit.list_events(tool="journal.record_work", status="denied")
    assert denials["total"] == 1


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {"title": "x" * 201},
        {"title": "ok", "body": "y" * 8001},
        {"title": "ok", "files": ["../secrets.txt"]},
        {"title": "ok", "files": ["/etc/passwd"]},
        {"title": "ok", "commit_sha": "not-a-commit!"},
        {"title": "ok", "verification_level": "totally_verified"},
        {"title": "ok", "unexpected_field": True},
        {"title": "ok", "idempotency_key": "short"},
        {"title": "ok", "task_id": "0" * 32},
    ],
)
async def test_journal_input_validation(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    _, token = await journal_agent(admin)
    response = await agent_api.post("/api/v1/journal/entries", json=body, headers=auth_headers(token))
    assert response.status_code == 422, response.text


async def test_oversized_http_body_is_rejected(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    huge = {"title": "big", "body": "x" * 2_200_000}
    response = await agent_api.post("/api/v1/journal/entries", json=huge, headers=auth_headers(token))
    assert response.status_code == 413


# --------------------------------------------------------------------- admin


async def test_admin_journal_flow_and_cross_agent_override(
    admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    created = await agent_api.post(
        "/api/v1/journal/tasks", json={"title": "Agent task"}, headers=auth_headers(token)
    )
    task_id = created.json()["id"]

    entry = await admin.post(
        "/api/admin/journal/entries",
        json={"title": "Admin note", "task_id": task_id, "outcome": "in_progress"},
    )
    assert entry.status_code == 200, entry.text
    assert entry.json()["actor_type"] == "admin"

    overridden = await admin.patch(f"/api/admin/journal/tasks/{task_id}", json={"status": "blocked"})
    assert overridden.status_code == 200
    assert overridden.json()["status"] == "blocked"

    timeline = await admin.get("/api/admin/journal/timeline", params={"task_id": task_id})
    assert timeline.json()["total"] == 1
    assert timeline.json()["items"][0]["actor_type"] == "admin"

    handoff = await admin.post("/api/admin/journal/handoffs/generate", json={})
    assert handoff.status_code == 200
    assert "Agent task" in handoff.json()["prompt"]


async def test_admin_journal_requires_ingress_identity(runtime: Runtime) -> None:
    transport = httpx.ASGITransport(app=build_admin_app(runtime), client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        response = await client.get("/api/admin/journal/project-state")
    assert response.status_code in (401, 403)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway.test", headers=ADMIN_HEADERS
    ) as client:
        ok = await client.get("/api/admin/journal/project-state")
    assert ok.status_code == 200


# ----------------------------------------------------------------------- MCP

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def _sse(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("no data frame in MCP response")


@asynccontextmanager
async def _mcp(runtime: Runtime, token: str) -> AsyncIterator[httpx.AsyncClient]:
    app: Starlette = build_agent_app(runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
            base_url="http://gateway.test",
            headers={**MCP_HEADERS, **auth_headers(token)},
        ) as client,
    ):
        yield client


async def _rpc(client: httpx.AsyncClient, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return _sse(response)


async def test_mcp_journal_tools_share_the_rest_authorization_path(
    runtime: Runtime, admin: httpx.AsyncClient, agent_api: httpx.AsyncClient
) -> None:
    _, token = await journal_agent(admin)
    async with _mcp(runtime, token) as client:
        await _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )
        listed = await _rpc(client, "tools/list", {})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert "journal.record_work" in names
        assert "journal.read_project_state" in names

        called = await _rpc(
            client,
            "tools/call",
            {
                "name": "journal.record_work",
                "arguments": {"title": "Recorded over MCP", "outcome": "in_progress"},
            },
        )
        result = called["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["title"] == "Recorded over MCP"

    timeline = await agent_api.get(
        "/api/v1/journal/timeline", params={"q": "Recorded over MCP"}, headers=auth_headers(token)
    )
    assert timeline.json()["total"] == 1
    assert timeline.json()["items"][0]["source"] == "mcp"


async def test_mcp_journal_tools_hidden_without_scope_and_denied_without_policy(
    runtime: Runtime, admin: httpx.AsyncClient
) -> None:
    _, plain_token = await create_agent(admin, name="plain", scopes=["ha:read"])
    async with _mcp(runtime, plain_token) as client:
        await _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )
        listed = await _rpc(client, "tools/list", {})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert not any(name.startswith("journal.") for name in names)

    _, scoped_token = await journal_agent(admin, name="scoped-no-policy", policy={"version": 1, "rules": []})
    async with _mcp(runtime, scoped_token) as client:
        await _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )
        called = await _rpc(
            client,
            "tools/call",
            {"name": "journal.record_work", "arguments": {"title": "Nope"}},
        )
        assert called["result"]["isError"] is True
        assert called["result"]["structuredContent"]["error"]["code"] == "policy_denied"
