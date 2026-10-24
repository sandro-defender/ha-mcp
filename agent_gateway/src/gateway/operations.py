"""Operation execution pipeline shared by REST and MCP.

Order of checks (all failures are audited):

1. emergency disable
2. agent still enabled and holds the tool's scope
3. tool exists and its group is enabled
4. parameters validate against the schema
5. policy evaluation (deny by default)
6. per-agent/tool rate limit
7. approval consumption (if supplied) or approval request
8. policy + agent revalidation immediately before execution
9. bounded execution with timeout
10. redaction, size check, operation result persistence, audit
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .approvals.service import ApprovalService
from .audit.service import AuditService
from .auth.agents import AgentPrincipal, AgentService
from .auth.rate_limit import SlidingWindowLimiter
from .connections import ConnectionService
from .errors import (
    ApprovalRequired,
    EmergencyDisabled,
    GatewayError,
    NotFoundError,
    OutputTooLarge,
    PolicyDenied,
    RateLimited,
)
from .policy.engine import PolicyService
from .policy.models import PolicyDecision, PolicyRequest
from .redaction import redact_value, summarize
from .storage.db import Database, SettingsStore
from .tools.registry import ToolRegistry
from .tools.spec import ToolContext, ToolSpec
from .utils import canonical_json, iso, sha256_hex

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationOutcome:
    operation_id: str
    status: str
    result: dict[str, Any] | None = None
    approval_id: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"operation_id": self.operation_id, "status": self.status}
        if self.result is not None:
            body["result"] = self.result
        if self.approval_id is not None:
            body["approval_id"] = self.approval_id
        if self.expires_at is not None:
            body["expires_at"] = self.expires_at
        return body


class OperationService:
    def __init__(
        self,
        *,
        db: Database,
        settings: SettingsStore,
        agents: AgentService,
        policies: PolicyService,
        connections: ConnectionService,
        tools: ToolRegistry,
        approvals: ApprovalService,
        audit: AuditService,
        rate_limiter: SlidingWindowLimiter,
    ) -> None:
        self.db = db
        self.settings = settings
        self.agents = agents
        self.policies = policies
        self.connections = connections
        self.tools = tools
        self.approvals = approvals
        self.audit = audit
        self.rate_limiter = rate_limiter

    # --------------------------------------------------------------- public

    async def execute(
        self,
        *,
        agent: AgentPrincipal,
        tool_name: str,
        connection_id: str,
        params: dict[str, Any],
        approval_id: str | None = None,
        request_id: str | None = None,
        source: str = "rest",
    ) -> OperationOutcome:
        request_id = request_id or uuid.uuid4().hex
        started = time.monotonic()
        if self.settings.get_bool("emergency_disabled"):
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                tool=tool_name,
                connection_id=connection_id,
                decision="deny",
                status="denied",
                reason="agent access is disabled",
                request_id=request_id,
            )
            raise EmergencyDisabled()

        try:
            spec = self.tools.get(tool_name)
        except GatewayError as exc:
            self._audit_denied(agent, tool_name, connection_id, params, str(exc.message), request_id)
            raise

        if not agent.has_scope(spec.scope):
            self._audit_denied(agent, tool_name, connection_id, params, "missing scope", request_id)
            raise PolicyDenied(f"agent lacks the {spec.scope} scope")

        # Temporary sessions are further restricted by the allowlist and the
        # operation classes chosen when the administrator issued the session.
        # These values come from the database, never from the request.
        constraint_error = self._session_constraint(agent, spec, connection_id)
        if constraint_error:
            self._audit_denied(agent, tool_name, connection_id, params, constraint_error, request_id)
            raise PolicyDenied(constraint_error)

        validated = self.tools.validate_params(spec, params)
        normalized = validated.model_dump(mode="json")
        targets = spec.targets(validated)
        params_summary = summarize(normalized)

        try:
            self.connections.get_connection(connection_id)
        except GatewayError:
            self._audit_denied(agent, tool_name, connection_id, params, "connection not found", request_id)
            raise

        decision = self._evaluate(agent, spec, connection_id, targets)
        if not decision.allowed:
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="deny",
                reason=decision.reason,
                status="denied",
                duration_ms=_elapsed_ms(started),
                error_category="policy_denied",
                request_id=request_id,
            )
            raise PolicyDenied(decision.reason)

        allowed, retry_after = self.rate_limiter.check_and_record(
            f"op:{agent.id}:{tool_name}",
            limit=decision.limits.rate_limit_per_minute,
            window_seconds=60,
        )
        if not allowed:
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="allow",
                reason="rate limited",
                status="denied",
                error_category="rate_limited",
                request_id=request_id,
            )
            raise RateLimited(retry_after)

        request_hash = self._request_hash(agent, spec, connection_id, normalized, decision)

        if approval_id:
            approval = self.approvals.consume(
                approval_id,
                agent_id=agent.id,
                tool=tool_name,
                connection_id=connection_id,
                request_hash=request_hash,
            )
            operation_id = approval["operation_id"]
            self._mark_running(operation_id)
        elif decision.require_approval:
            operation_id = self._create_operation(
                agent_id=agent.id,
                tool=tool_name,
                connection_id=connection_id,
                status="pending_approval",
                params_summary=params_summary,
            )
            approval = self.approvals.create(
                operation_id=operation_id,
                agent_id=agent.id,
                tool=tool_name,
                connection_id=connection_id,
                request_hash=request_hash,
                params_summary=params_summary,
                ttl_seconds=self.settings.get_int("approval_ttl_seconds"),
            )
            self.db.execute("UPDATE operations SET approval_id=? WHERE id=?", (approval["id"], operation_id))
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="approval_required",
                reason=decision.reason,
                approval_id=approval["id"],
                status="pending_approval",
                request_id=request_id,
            )
            raise ApprovalRequired(approval["id"], approval["expires_at"], operation_id)
        else:
            operation_id = self._create_operation(
                agent_id=agent.id,
                tool=tool_name,
                connection_id=connection_id,
                status="running",
                params_summary=params_summary,
            )
            self._mark_running(operation_id)

        # Revalidate everything immediately before the sensitive operation.
        try:
            fresh = self.agents.get_principal(agent.id)
        except GatewayError as exc:
            self._fail_operation(operation_id, "agent_disabled")
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="deny",
                reason="agent disabled before execution",
                status="denied",
                error_category="agent_disabled",
                request_id=request_id,
            )
            raise PolicyDenied("agent is no longer enabled") from exc
        if not fresh.has_scope(spec.scope):
            self._fail_operation(operation_id, "policy_denied")
            self._audit_denied(
                fresh,
                tool_name,
                connection_id,
                params,
                "agent no longer has the required scope",
                request_id,
            )
            raise PolicyDenied("agent no longer has the required scope")
        constraint_error = self._session_constraint(fresh, spec, connection_id)
        if constraint_error:
            self._fail_operation(operation_id, "policy_denied")
            self._audit_denied(agent, tool_name, connection_id, params, constraint_error, request_id)
            raise PolicyDenied(constraint_error)
        if self.settings.get_bool("emergency_disabled"):
            self._fail_operation(operation_id, "agent_access_disabled")
            raise EmergencyDisabled()

        redecision = self._evaluate(fresh, spec, connection_id, targets)
        if not redecision.allowed:
            self._fail_operation(operation_id, "policy_denied")
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="deny",
                reason="policy changed before execution",
                status="denied",
                error_category="policy_denied",
                request_id=request_id,
            )
            raise PolicyDenied("policy no longer allows this operation")
        if redecision.require_approval and not approval_id:
            # A read rule may gain require_approval while the request is in
            # flight.  The operation was created before that change, so stop
            # it and let the caller retry through the normal approval flow.
            self._fail_operation(operation_id, "approval_required")
            self._audit_denied(
                fresh,
                tool_name,
                connection_id,
                params,
                "approval became required before execution",
                request_id,
            )
            raise PolicyDenied("approval is required by the current policy; retry the operation")

        try:
            connection = self.connections.resolve(connection_id, spec.group_to_connection_type())
        except GatewayError as exc:
            self._fail_operation(operation_id, exc.code)
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="allow",
                status="failed",
                error_category=exc.code,
                request_id=request_id,
            )
            raise

        timeout = min(
            redecision.limits.timeout_seconds, self.settings.get_int("max_operation_timeout_seconds")
        )
        max_output = min(redecision.limits.max_output_bytes, self.settings.get_int("max_output_bytes"))
        context = ToolContext(
            agent=fresh,
            connection=connection,
            filters=redecision.filters,
            excludes=redecision.excludes,
            timeout_seconds=float(timeout),
            max_output_bytes=max_output,
            known_hosts_path=self.connections.known_hosts_path,
            request_id=request_id,
        )

        try:
            async with asyncio.timeout(timeout):
                result = await spec.handler(context, validated)
        except TimeoutError as exc:
            self._fail_operation(operation_id, "timeout")
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="allow",
                approval_id=approval_id,
                status="failed",
                duration_ms=_elapsed_ms(started),
                error_category="timeout",
                request_id=request_id,
            )
            raise GatewayError("timeout", "operation timed out", 504) from exc
        except GatewayError as exc:
            self._fail_operation(operation_id, exc.code)
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="allow",
                approval_id=approval_id,
                status="failed",
                duration_ms=_elapsed_ms(started),
                error_category=exc.code,
                request_id=request_id,
            )
            raise
        except Exception as exc:
            self._fail_operation(operation_id, "internal_error")
            logger.error("tool execution failed tool=%s type=%s", tool_name, type(exc).__name__)
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="allow",
                approval_id=approval_id,
                status="failed",
                duration_ms=_elapsed_ms(started),
                error_category="internal_error",
                request_id=request_id,
            )
            raise GatewayError("internal_error", "the operation failed", 500) from exc

        safe_result = redact_value(result) if isinstance(result, dict) else {"value": redact_value(result)}
        serialized = canonical_json(safe_result)
        if len(serialized.encode("utf-8")) > max_output:
            self._fail_operation(operation_id, "output_too_large")
            self.audit.record(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                connection_id=connection_id,
                tool=tool_name,
                params_summary=params_summary,
                decision="allow",
                approval_id=approval_id,
                status="failed",
                duration_ms=_elapsed_ms(started),
                error_category="output_too_large",
                request_id=request_id,
            )
            raise OutputTooLarge(max_output)

        self.db.execute(
            "UPDATE operations SET status='succeeded', result_summary=?, finished_at=? WHERE id=?",
            (serialized, iso(), operation_id),
        )
        self.audit.record(
            actor_type="agent",
            actor_id=agent.id,
            agent_id=agent.id,
            connection_id=connection_id,
            tool=tool_name,
            params_summary=params_summary,
            decision="allow",
            reason=decision.reason,
            approval_id=approval_id,
            status="succeeded",
            duration_ms=_elapsed_ms(started),
            request_id=request_id,
        )
        logger.info("operation succeeded id=%s tool=%s agent=%s", operation_id, tool_name, agent.id)
        return OperationOutcome(operation_id=operation_id, status="succeeded", result=safe_result)

    def get_operation(self, agent: AgentPrincipal, operation_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM operations WHERE id=?", (operation_id,))
        if row is None or row["agent_id"] != agent.id:
            raise NotFoundError("operation")
        result = None
        if row["result_summary"]:
            try:
                result = json.loads(row["result_summary"])
            except json.JSONDecodeError:
                result = None
        return {
            "operation_id": row["id"],
            "status": row["status"],
            "tool": row["tool"],
            "connection_id": row["connection_id"],
            "approval_id": row["approval_id"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error_code": row["error_code"],
            "result": result,
        }

    # ------------------------------------------------------------ internals

    def _session_constraint(
        self, principal: AgentPrincipal, spec: ToolSpec, connection_id: str
    ) -> str | None:
        """Return a deny reason when the session is narrower than the policy."""

        if not principal.allows_operation_class(spec.risk):
            return f"this session does not allow {spec.risk} operations"
        if not principal.allows_connection(connection_id):
            return "this session is not authorized for that connection"
        return None

    def _evaluate(
        self, agent: AgentPrincipal, spec: ToolSpec, connection_id: str, targets: dict[str, list[str]]
    ) -> PolicyDecision:
        return self.policies.evaluate(
            PolicyRequest(
                agent_id=agent.id,
                tool=spec.name,
                connection_id=connection_id,
                risk=spec.risk,
                targets=targets,
            )
        )

    def _request_hash(
        self,
        agent: AgentPrincipal,
        spec: ToolSpec,
        connection_id: str,
        normalized_params: dict[str, Any],
        decision: PolicyDecision,
    ) -> str:
        return sha256_hex(
            canonical_json(
                {
                    "agent_id": agent.id,
                    "tool": spec.name,
                    "connection_id": connection_id,
                    "params": normalized_params,
                    "policy_revision": decision.policy_revision,
                    "schema": spec.schema(),
                    "gateway": "ha-agent-gateway/1",
                }
            )
        )

    def _create_operation(
        self,
        *,
        agent_id: str,
        tool: str,
        connection_id: str,
        status: str,
        params_summary: str,
    ) -> str:
        operation_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO operations(id, agent_id, tool, connection_id, status, params_summary, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (operation_id, agent_id, tool, connection_id, status, params_summary, iso()),
        )
        return operation_id

    def _mark_running(self, operation_id: str) -> None:
        self.db.execute(
            "UPDATE operations SET status='running', started_at=? WHERE id=?",
            (iso(), operation_id),
        )

    def _fail_operation(self, operation_id: str, error_code: str) -> None:
        self.db.execute(
            "UPDATE operations SET status='failed', error_code=?, finished_at=? WHERE id=?",
            (error_code, iso(), operation_id),
        )

    def _audit_denied(
        self,
        agent: AgentPrincipal,
        tool: str,
        connection_id: str,
        params: Any,
        reason: str,
        request_id: str,
    ) -> None:
        self.audit.record(
            actor_type="agent",
            actor_id=agent.id,
            agent_id=agent.id,
            tool=tool,
            connection_id=connection_id,
            params=params,
            decision="deny",
            status="denied",
            reason=reason,
            error_category="policy_denied",
            request_id=request_id,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
