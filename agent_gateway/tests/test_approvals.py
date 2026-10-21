"""Approval lifecycle: single use, expiry, and parameter binding."""

from __future__ import annotations

from datetime import timedelta

import pytest
from helpers import PASSWORD  # noqa: F401  (shared fixture constant)

from gateway.errors import ApprovalInvalid, ConflictError
from gateway.runtime import Runtime
from gateway.utils import iso, utcnow

REQUEST_HASH = "a" * 64
CONNECTION_ID = "c" * 32


def make_approval(runtime: Runtime, *, agent_id: str = "agent-1", ttl: int = 600) -> dict:
    return runtime.approvals.create(
        operation_id="op-1",
        agent_id=agent_id,
        tool="ha.call_service",
        connection_id=CONNECTION_ID,
        request_hash=REQUEST_HASH,
        params_summary="domain=light service=turn_on",
        ttl_seconds=ttl,
    )


def consume(runtime: Runtime, approval_id: str, **overrides: object) -> dict:
    kwargs: dict[str, object] = {
        "agent_id": "agent-1",
        "tool": "ha.call_service",
        "connection_id": CONNECTION_ID,
        "request_hash": REQUEST_HASH,
    }
    kwargs.update(overrides)
    return runtime.approvals.consume(approval_id, **kwargs)  # type: ignore[arg-type]


def test_pending_approval_cannot_be_consumed(runtime: Runtime) -> None:
    approval = make_approval(runtime)
    assert approval["status"] == "pending"
    with pytest.raises(ApprovalInvalid):
        consume(runtime, approval["id"])


def test_approved_approval_is_single_use(runtime: Runtime) -> None:
    approval = make_approval(runtime)
    runtime.approvals.approve(approval["id"], "admin-user")
    used = consume(runtime, approval["id"])
    assert used["status"] == "used"
    assert used["used_at"]
    with pytest.raises(ApprovalInvalid):
        consume(runtime, approval["id"])
    with pytest.raises(ConflictError):
        runtime.approvals.approve(approval["id"], "admin-user")


def test_approval_is_bound_to_parameters(runtime: Runtime) -> None:
    approval = make_approval(runtime)
    runtime.approvals.approve(approval["id"], "admin-user")
    with pytest.raises(ApprovalInvalid, match="parameters"):
        consume(runtime, approval["id"], request_hash="b" * 64)


def test_approval_is_bound_to_agent_tool_and_connection(runtime: Runtime) -> None:
    approval = make_approval(runtime)
    runtime.approvals.approve(approval["id"], "admin-user")
    with pytest.raises(ApprovalInvalid):
        consume(runtime, approval["id"], agent_id="agent-2")
    with pytest.raises(ApprovalInvalid):
        consume(runtime, approval["id"], tool="ha.list_entities")
    with pytest.raises(ApprovalInvalid):
        consume(runtime, approval["id"], connection_id="d" * 32)
    # The failed attempts must not have consumed the approval.
    assert consume(runtime, approval["id"])["status"] == "used"


def test_expired_approval_is_rejected_and_marked(runtime: Runtime) -> None:
    approval = make_approval(runtime, ttl=60)
    runtime.approvals.approve(approval["id"], "admin-user")
    expired_at = iso(utcnow() - timedelta(seconds=5))
    runtime.db.execute("UPDATE approvals SET expires_at=? WHERE id=?", (expired_at, approval["id"]))
    with pytest.raises(ApprovalInvalid, match="expired"):
        consume(runtime, approval["id"])
    assert runtime.approvals.get(approval["id"])["status"] == "expired"


def test_rejected_approval_cannot_be_used(runtime: Runtime) -> None:
    approval = make_approval(runtime)
    rejected = runtime.approvals.reject(approval["id"], "admin-user", "not needed")
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "not needed"
    with pytest.raises(ApprovalInvalid):
        consume(runtime, approval["id"])


def test_unknown_approval_id_is_rejected(runtime: Runtime) -> None:
    with pytest.raises(ApprovalInvalid, match="not found"):
        consume(runtime, "does-not-exist")


def test_expire_pending_updates_status_and_count(runtime: Runtime) -> None:
    first = make_approval(runtime)
    second = make_approval(runtime)
    runtime.db.execute(
        "UPDATE approvals SET expires_at=? WHERE id=?",
        (iso(utcnow() - timedelta(minutes=1)), first["id"]),
    )
    assert runtime.approvals.expire_pending() == 1
    assert runtime.approvals.get(first["id"])["status"] == "expired"
    assert runtime.approvals.get(second["id"])["status"] == "pending"
    assert runtime.approvals.pending_count() == 1


def test_reason_length_is_bounded_by_the_api_schema() -> None:
    from pydantic import ValidationError

    from gateway.api.schemas import ApprovalRejectRequest

    with pytest.raises(ValidationError):
        ApprovalRejectRequest(reason="x" * 301)
