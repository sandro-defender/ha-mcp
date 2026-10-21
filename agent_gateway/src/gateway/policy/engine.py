"""Deny-by-default authorization engine.

Rules are evaluated in one place for REST and MCP.  Deny rules without
resource constraints veto the request.  Deny rules with constraints produce
exclusion filters for list operations and veto targeted operations.  After
denies, every matching allow rule is merged: allow list = union of patterns, or
``*`` if any matching rule leaves a key unconstrained; approval is required if
any matching rule asks for it.  No matching allow rule means deny.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from pydantic import ValidationError

from ..errors import ValidationFailure
from ..redaction import summarize
from ..utils import canonical_json, match_any, sha256_hex
from .models import PolicyDecision, PolicyDocument, PolicyLimits, PolicyRequest, PolicyRule

logger = logging.getLogger(__name__)

DEFAULT_LIMITS = PolicyLimits(timeout_seconds=30, max_output_bytes=65_536, rate_limit_per_minute=60)


def _tool_matches(rule: PolicyRule, tool: str) -> bool:
    return any(fnmatch.fnmatchcase(tool, pattern) for pattern in rule.tools)


def _connection_matches(rule: PolicyRule, connection_id: str | None) -> bool:
    if not rule.connections:
        return False
    if connection_id is None:
        return "*" in rule.connections
    return any(pattern == "*" or pattern == connection_id for pattern in rule.connections)


def _targets_match(rule: PolicyRule, request: PolicyRequest) -> bool:
    """For targeted operations, every constrained target family must match."""

    saw_constraint = False
    for key, patterns in rule.constraints.items():
        values = request.targets.get(key)
        if not values:
            continue
        saw_constraint = True
        if not all(match_any(value, patterns) for value in values):
            return False
    # A rule with constraints for target families that the request does not
    # contain cannot be evaluated for this request.  For targeted operations
    # that is treated as a non-match; list operations are handled separately.
    return saw_constraint or not rule.constraints


def _targets_hit_deny(rule: PolicyRule, request: PolicyRequest) -> bool:
    for key, patterns in rule.constraints.items():
        values = request.targets.get(key)
        if values and any(match_any(value, patterns) for value in values):
            return True
    return False


def _has_targets(request: PolicyRequest) -> bool:
    return any(request.targets.values())


def _merge_limits(left: PolicyLimits, right: PolicyLimits) -> PolicyLimits:
    return PolicyLimits(
        timeout_seconds=min(left.timeout_seconds, right.timeout_seconds),
        max_output_bytes=min(left.max_output_bytes, right.max_output_bytes),
        rate_limit_per_minute=min(left.rate_limit_per_minute, right.rate_limit_per_minute),
    )


def evaluate_document(
    document: PolicyDocument,
    request: PolicyRequest,
    *,
    default_limits: PolicyLimits | None = None,
) -> PolicyDecision:
    base_limits = default_limits or DEFAULT_LIMITS
    revision = sha256_hex(canonical_json(document.model_dump(mode="json")))

    deny_rules: list[PolicyRule] = []
    allow_rules: list[PolicyRule] = []
    for rule in document.rules:
        if not _tool_matches(rule, request.tool) or not _connection_matches(rule, request.connection_id):
            continue
        if rule.effect == "deny":
            deny_rules.append(rule)
        else:
            allow_rules.append(rule)

    excludes: dict[str, set[str]] = {}
    list_operation = not _has_targets(request)
    for rule in deny_rules:
        if not rule.constraints:
            return PolicyDecision(
                allowed=False,
                reason=f"denied by rule {rule.id}",
                rule_ids=(rule.id,),
                limits=base_limits,
                policy_revision=revision,
            )
        if list_operation:
            for key, patterns in rule.constraints.items():
                excludes.setdefault(key, set()).update(patterns)
            continue
        if _targets_hit_deny(rule, request):
            return PolicyDecision(
                allowed=False,
                reason=f"denied by rule {rule.id}",
                rule_ids=(rule.id,),
                limits=base_limits,
                policy_revision=revision,
            )

    matched_allow: list[PolicyRule] = []
    for rule in allow_rules:
        if list_operation or not rule.constraints or _targets_match(rule, request):
            matched_allow.append(rule)

    if not matched_allow:
        return PolicyDecision(
            allowed=False,
            reason="no matching allow rule",
            limits=base_limits,
            excludes={key: tuple(sorted(values)) for key, values in excludes.items()},
            policy_revision=revision,
        )

    limits = base_limits
    require_approval = request.risk != "read"
    for rule in matched_allow:
        limits = _merge_limits(limits, rule.limits)
        if rule.require_approval:
            require_approval = True

    filters: dict[str, set[str]] = {}
    for key in (
        "entities",
        "domains",
        "ha_services",
        "nodes",
        "vms",
        "containers",
        "ssh_services",
        "ssh_paths",
        "ssh_templates",
    ):
        relevant = [rule for rule in matched_allow if key in rule.constraints]
        if not relevant:
            continue
        if len(relevant) != len(matched_allow):
            filters[key] = {"*"}
        else:
            union: set[str] = set()
            for rule in relevant:
                union.update(rule.constraints[key])
            filters[key] = union

    return PolicyDecision(
        allowed=True,
        reason="allowed by " + ", ".join(rule.id for rule in matched_allow),
        rule_ids=tuple(rule.id for rule in matched_allow),
        require_approval=require_approval,
        limits=limits,
        filters={key: tuple(sorted(values)) for key, values in filters.items()},
        excludes={key: tuple(sorted(values)) for key, values in excludes.items()},
        policy_revision=revision,
    )


class PolicyService:
    def __init__(self, db: Any, default_limits: PolicyLimits | None = None) -> None:
        self.db = db
        self.default_limits = default_limits or DEFAULT_LIMITS

    def get_policy(self, agent_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM policies WHERE agent_id=?", (agent_id,))
        if row is None:
            return {
                "agent_id": agent_id,
                "version": 1,
                "document": {"version": 1, "rules": []},
                "updated_at": None,
            }
        import json

        try:
            document = json.loads(row["document"])
        except json.JSONDecodeError:
            document = {"version": 1, "rules": []}
        return {
            "agent_id": agent_id,
            "version": row["version"],
            "document": document,
            "updated_at": row["updated_at"],
        }

    def set_policy(self, agent_id: str, document: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = PolicyDocument.model_validate(document)
        except ValidationError as exc:
            safe_errors = [
                {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                for error in exc.errors()
            ]
            raise ValidationFailure("policy document is invalid", {"errors": safe_errors}) from exc
        from ..utils import iso

        self.db.execute(
            "INSERT INTO policies(agent_id, version, document, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(agent_id) DO UPDATE SET version=excluded.version, document=excluded.document, "
            "updated_at=excluded.updated_at",
            (agent_id, parsed.version, canonical_json(parsed.model_dump(mode="json")), iso()),
        )
        logger.info("policy updated agent=%s rules=%d", agent_id, len(parsed.rules))
        return self.get_policy(agent_id)

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        policy = self.get_policy(request.agent_id)
        try:
            document = PolicyDocument.model_validate(policy["document"])
        except ValidationError:
            logger.warning("policy for agent %s could not be evaluated; denying", request.agent_id)
            return PolicyDecision(
                allowed=False,
                reason="policy could not be evaluated",
                limits=self.default_limits,
            )
        decision = evaluate_document(document, request, default_limits=self.default_limits)
        logger.debug(
            "policy decision agent=%s tool=%s allowed=%s rules=%s",
            request.agent_id,
            request.tool,
            decision.allowed,
            summarize(list(decision.rule_ids), max_chars=200),
        )
        return decision
