"""Deny-by-default policy engine behaviour shared by REST and MCP."""

from __future__ import annotations

import pytest
from helpers import read_policy
from pydantic import ValidationError

from gateway.errors import ValidationFailure
from gateway.policy.engine import evaluate_document
from gateway.policy.models import PolicyDocument, PolicyRequest
from gateway.runtime import Runtime


def request(
    tool: str = "ha.get_state",
    *,
    risk: str = "read",
    targets: dict[str, list[str]] | None = None,
    connection_id: str = "a" * 32,
) -> PolicyRequest:
    return PolicyRequest(
        agent_id="agent-1",
        tool=tool,
        connection_id=connection_id,
        risk=risk,  # type: ignore[arg-type]
        targets=targets or {},
    )


def evaluate(document: dict, req: PolicyRequest):
    return evaluate_document(PolicyDocument.model_validate(document), req)


def test_empty_document_denies_everything() -> None:
    decision = evaluate({"version": 1, "rules": []}, request())
    assert decision.allowed is False
    assert "no matching allow rule" in decision.reason


def test_allow_rule_permits_targeted_read() -> None:
    document = read_policy(constraints={"entities": ["light.kitchen"]})
    decision = evaluate(document, request(targets={"entities": ["light.kitchen"]}))
    assert decision.allowed is True
    assert decision.require_approval is False
    assert decision.policy_revision


def test_target_outside_constraint_is_denied() -> None:
    document = read_policy(constraints={"entities": ["light.kitchen"]})
    decision = evaluate(document, request(targets={"entities": ["light.bedroom"]}))
    assert decision.allowed is False


def test_write_risk_always_requires_approval() -> None:
    document = read_policy(tools=["ha.call_service"], constraints={"domains": ["light"]})
    decision = evaluate(
        document,
        request(
            "ha.call_service",
            risk="write",
            targets={"domains": ["light"], "ha_services": ["light.turn_on"]},
        ),
    )
    assert decision.allowed is True
    assert decision.require_approval is True


def test_explicit_deny_rule_wins_over_allow() -> None:
    document = {
        "version": 1,
        "rules": [
            {"id": "allow", "effect": "allow", "tools": ["ha.*"], "connections": ["*"]},
            {
                "id": "deny-lock",
                "effect": "deny",
                "tools": ["ha.call_service"],
                "connections": ["*"],
                "constraints": {"domains": ["lock"]},
            },
        ],
    }
    allowed = evaluate(document, request("ha.call_service", risk="write", targets={"domains": ["light"]}))
    denied = evaluate(document, request("ha.call_service", risk="write", targets={"domains": ["lock"]}))
    assert allowed.allowed is True
    assert denied.allowed is False
    assert "deny-lock" in denied.reason


def test_deny_rule_without_constraints_vetoes_matching_tool() -> None:
    document = {
        "version": 1,
        "rules": [
            {"id": "allow", "effect": "allow", "tools": ["ha.*"], "connections": ["*"]},
            {"id": "no-shell", "effect": "deny", "tools": ["ha.call_service"], "connections": ["*"]},
        ],
    }
    decision = evaluate(
        document,
        request("ha.call_service", risk="write", targets={"domains": ["light"]}),
    )
    assert decision.allowed is False


def test_connection_scoping() -> None:
    document = read_policy(connections=["b" * 32])
    assert evaluate(document, request()).allowed is False
    assert evaluate(document, request(connection_id="b" * 32)).allowed is True


def test_tool_pattern_matching_is_literal_not_prefix() -> None:
    document = read_policy(tools=["ha.get_*"])
    assert evaluate(document, request("ha.get_state", targets={})).allowed is True
    assert evaluate(document, request("ha.call_service", risk="write")).allowed is False


def test_list_operation_is_limited_by_excludes() -> None:
    document = {
        "version": 1,
        "rules": [
            {
                "id": "allow-list",
                "effect": "allow",
                "tools": ["ha.list_entities"],
                "connections": ["*"],
                "constraints": {"domains": ["light"]},
            },
            {
                "id": "hide-camera",
                "effect": "deny",
                "tools": ["ha.list_entities"],
                "connections": ["*"],
                "constraints": {"domains": ["camera"]},
            },
        ],
    }
    decision = evaluate(document, request("ha.list_entities", targets={}))
    assert decision.allowed is True
    assert "camera" in decision.excludes.get("domains", ())
    assert "light" in decision.filters.get("domains", ())


def test_unsupported_policy_fields_and_constraints_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PolicyDocument.model_validate(
            {
                "version": 1,
                "rules": [
                    {
                        "id": "x",
                        "effect": "allow",
                        "tools": ["ha.*"],
                        "connections": ["*"],
                        "constraints": {"secrets": ["*"]},
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        PolicyDocument.model_validate(
            {
                "version": 1,
                "rules": [
                    {"id": "x", "effect": "allow", "tools": ["ha.*"], "connections": ["*"], "unknown": True}
                ],
            }
        )
    with pytest.raises(ValidationError):
        PolicyDocument.model_validate({"version": 2, "rules": []})


def test_limits_are_merged_with_the_most_restrictive_values() -> None:
    document = {
        "version": 1,
        "rules": [
            {
                "id": "r1",
                "effect": "allow",
                "tools": ["ha.*"],
                "connections": ["*"],
                "limits": {"timeout_seconds": 10, "max_output_bytes": 4096, "rate_limit_per_minute": 5},
            }
        ],
    }
    decision = evaluate(document, request())
    assert decision.limits.timeout_seconds == 10
    assert decision.limits.max_output_bytes == 4096
    assert decision.limits.rate_limit_per_minute == 5


def test_policy_service_stores_and_rejects_malformed_documents(runtime: Runtime) -> None:
    agent, _ = runtime.agents.create_agent(name="policy-agent", scopes=["ha:read"])
    stored = runtime.policies.set_policy(agent["id"], read_policy())
    assert stored["document"]["rules"][0]["id"] == "allow-read"
    with pytest.raises(ValidationFailure):
        runtime.policies.set_policy(agent["id"], {"version": 1, "rules": [{"id": "bad"}]})
    # A rejected update must not replace the previously stored policy.
    assert runtime.policies.get_policy(agent["id"])["document"]["rules"][0]["id"] == "allow-read"


def test_agents_without_policy_are_denied(runtime: Runtime) -> None:
    agent, _ = runtime.agents.create_agent(name="no-policy", scopes=["ha:read"])
    policy = runtime.policies.get_policy(agent["id"])
    assert policy["document"]["rules"] == []
    decision = runtime.policies.evaluate(
        PolicyRequest(
            agent_id=agent["id"], tool="ha.get_state", connection_id="c" * 32, risk="read", targets={}
        )
    )
    assert decision.allowed is False
