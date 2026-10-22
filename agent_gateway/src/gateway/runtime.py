"""Application composition root."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .approvals.service import ApprovalService
from .audit.service import AuditService
from .auth.agents import AgentService
from .auth.rate_limit import SlidingWindowLimiter
from .connections import ConnectionService
from .connectors.home_assistant import HARD_DENIED_SERVICE_DOMAINS, HomeAssistantClient
from .connectors.proxmox import ProxmoxClient
from .connectors.ssh import scan_host_key
from .errors import GatewayError, NotFoundError, ValidationFailure
from .operations import OperationService
from .policy.engine import PolicyService
from .policy.models import PolicyRequest
from .redaction import redact_value
from .releases import REPO_LINKS, ReleaseService
from .settings import Settings
from .storage.db import Database, SettingsStore
from .tools.registry import ToolRegistry
from .utils import iso, parse_iso, redact_url, utcnow
from .vault.manager import CredentialVault

logger = logging.getLogger(__name__)

CREDENTIAL_ROTATION_REMINDER_DAYS = 90


def _classify_connection_error(exc: GatewayError, connection_type: str) -> tuple[str, str, str]:
    """Return ``(category, failed_step, remediation)`` for a connection error."""

    code = exc.code
    details = exc.details or {}
    reason = str(details.get("reason") or "")

    if code in {"vault_not_initialized", "vault_locked"}:
        return (
            "vault_locked",
            "credential",
            "Unlock or initialize the credential vault before testing connections.",
        )
    if code == "not_found":
        return (
            "configuration_error",
            "credential",
            "Verify that the referenced credential still exists in the Credentials vault.",
        )
    if code in {"tls_configuration_error", "tls_verification_failed"} or reason == "tls_certificate_error":
        return (
            "tls_certificate_error",
            "transport",
            (
                "Verify the upstream HTTPS certificate and hostname, or attach a valid PEM CA "
                "certificate in Credentials."
            ),
        )
    if code == "ssh_host_key_unknown":
        return (
            "host_key_untrusted",
            "transport",
            "Scan and trust the SSH host key fingerprint before running tests or operations.",
        )
    if code == "ssh_host_key_mismatch":
        return (
            "host_key_untrusted",
            "transport",
            "The remote SSH host key does not match known_hosts. Verify out-of-band before replacing trust.",
        )
    if code in {"upstream_timeout", "ssh_timeout", "upstream_unreachable", "ssh_unreachable"}:
        return (
            "network_unreachable",
            "transport",
            (
                "Check that the target host/URL and port are reachable from the Home Assistant "
                "network and not blocked by a firewall."
            ),
        )
    if code == "upstream_auth_failed" and (
        reason == "permission_denied" or details.get("http_status") == 403
    ):
        return (
            "permission_denied",
            "authentication",
            "The credential authenticated, but the upstream account lacks permission for this API endpoint.",
        )
    if code in {"upstream_auth_failed", "ssh_auth_failed", "ssh_key_invalid"}:
        if connection_type == "home_assistant":
            rem = (
                "Rotate the Home Assistant Long-Lived Access Token in Credentials and verify "
                "it has not been revoked."
            )
        elif connection_type == "proxmox":
            rem = (
                "Verify the Proxmox API Token ID (user@realm!token) and secret, and ensure "
                "Privilege Separation is configured."
            )
        else:
            rem = (
                "Verify the SSH Ed25519/RSA private key, passphrase, and "
                "~/.ssh/authorized_keys for the non-root user."
            )
        return ("authentication_failed", "authentication", rem)
    if code in {
        "upstream_redirect",
        "upstream_invalid_response",
        "home_assistant_unexpected",
        "proxmox_unexpected",
        "upstream_not_found",
    }:
        return (
            "protocol_error",
            "verification",
            (
                "Check the base URL path and port (for example http://homeassistant.local:8123 "
                "or https://pve.lan:8006 without trailing API paths)."
            ),
        )
    return (
        "protocol_error",
        "verification",
        "Inspect the upstream service logs and verify connection parameters.",
    )


def _build_diagnostic_steps(
    *,
    connection_type: str,
    failed_step: str | None = None,
    error_message: str | None = None,
    detail: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    order = [
        ("configuration", "Connection profile & allowlist validation"),
        ("credential", "Vault credential & CA certificate resolution"),
        (
            "transport",
            "SSH known_hosts & TCP transport"
            if connection_type == "ssh"
            else "TCP transport & TLS certificate verification",
        ),
        ("authentication", "Upstream credential authentication"),
        ("verification", "API readiness & response verification"),
    ]
    steps: list[dict[str, str]] = []
    failed_seen = False
    for key, label in order:
        if failed_seen:
            steps.append(
                {"step": key, "label": label, "status": "skipped", "detail": "Skipped due to earlier failure"}
            )
        elif failed_step == key:
            failed_seen = True
            steps.append(
                {
                    "step": key,
                    "label": label,
                    "status": "error",
                    "detail": error_message or "Check failed",
                }
            )
        else:
            step_detail = "Verified"
            if key == "verification" and detail:
                if "message" in detail:
                    step_detail = f"Upstream message: {detail['message']}"
                elif "version" in detail:
                    step_detail = f"Proxmox VE v{detail['version']} ({detail.get('release', '')})".strip()
                elif "reachable" in detail:
                    step_detail = "Authenticated non-root SSH session verified"
            steps.append({"step": key, "label": label, "status": "ok", "detail": step_detail})
    return steps


class Runtime:
    def __init__(self, settings: Settings, *, kdf_memory_cost: int = 65_536, kdf_time_cost: int = 3) -> None:
        self.settings = settings
        self.started_at = time.monotonic()
        self.db = Database(settings.database_path)
        self.settings_store = SettingsStore(self.db)
        self.vault = CredentialVault(
            settings.vault_path,
            settings.unlock_key_path,
            kdf_memory_cost=kdf_memory_cost,
            kdf_time_cost=kdf_time_cost,
        )
        self.agents = AgentService(self.db)
        self.policies = PolicyService(self.db)
        self.connections = ConnectionService(self.db, self.vault, settings.ssh_known_hosts_path)
        self.tools = ToolRegistry(self.settings_store)
        self.approvals = ApprovalService(self.db)
        self.audit = AuditService(self.db)
        self.releases = ReleaseService(self.db)
        self.rate_limiter = SlidingWindowLimiter()
        self.operations = OperationService(
            db=self.db,
            settings=self.settings_store,
            agents=self.agents,
            policies=self.policies,
            connections=self.connections,
            tools=self.tools,
            approvals=self.approvals,
            audit=self.audit,
            rate_limiter=self.rate_limiter,
        )
        self._initialized = False

    # ------------------------------------------------------------- lifecycle

    def startup(self) -> None:
        if self._initialized:
            return
        self.db.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?,?,?)",
            ("audit_retention_days", str(self.settings.audit_retention_days), ""),
        )
        self.db.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?,?,?)",
            ("approval_ttl_seconds", str(self.settings.approval_ttl_seconds), ""),
        )
        self.releases.record_startup_version()
        self._ensure_known_hosts_file()
        self.audit.purge_old(self.settings_store.get_int("audit_retention_days"))
        self.approvals.purge_old(self.settings_store.get_int("audit_retention_days"))

        if self.settings_store.get_bool("auto_unlock") and not self.vault.unlock_with_key_file():
            logger.warning("auto-unlock is enabled but the vault could not be unlocked; unlock it manually")
        if not self.vault.exists:
            logger.info("credential vault is not initialized")
        elif not self.vault.unlocked:
            logger.info("credential vault is locked")
        else:
            logger.info("credential vault is unlocked")
        self._initialized = True
        logger.info(
            "gateway started version=%s ingress_port=%s agent_port=%s tls=%s",
            self.settings.version,
            self.settings.ingress_port,
            self.settings.agent_port,
            self.settings.tls_enabled,
        )

    def shutdown(self) -> None:
        self.vault.lock()
        self.db.close()
        logger.info("gateway stopped")

    async def maintenance_loop(self, interval_seconds: int = 21_600) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                retention = self.settings_store.get_int("audit_retention_days")
                self.audit.purge_old(retention)
                self.approvals.purge_old(retention)
                self.approvals.expire_pending()
            except Exception:
                logger.error("background maintenance failed")

    def _ensure_known_hosts_file(self) -> None:
        path = self.settings.ssh_known_hosts_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            path.touch(mode=0o600)
        else:
            try:
                path.chmod(0o600)
            except OSError:
                pass

    # ---------------------------------------------------- telemetry & health

    def agent_usage_map(self) -> dict[str, dict[str, Any]]:
        """Aggregate per-agent operation, denial, rate-limit, and approval counts."""

        rows = self.db.query_all(
            "SELECT agent_id, decision, status, tool, ts FROM audit "
            "WHERE agent_id IS NOT NULL ORDER BY id DESC"
        )
        stats: dict[str, dict[str, Any]] = {}
        for row in rows:
            aid = str(row["agent_id"])
            entry = stats.setdefault(
                aid,
                {
                    "total_requests": 0,
                    "succeeded_count": 0,
                    "denied_count": 0,
                    "rate_limited_count": 0,
                    "pending_approval_count": 0,
                    "failed_count": 0,
                    "last_tool": row["tool"],
                    "last_decision": row["decision"],
                    "last_activity_at": row["ts"],
                },
            )
            entry["total_requests"] += 1
            st = str(row["status"])
            dec = str(row["decision"])
            if st == "succeeded":
                entry["succeeded_count"] += 1
            elif st == "rate_limited" or dec == "rate_limited":
                entry["rate_limited_count"] += 1
            elif dec == "deny" or st == "denied":
                entry["denied_count"] += 1
            elif st == "pending_approval":
                entry["pending_approval_count"] += 1
            elif st == "failed":
                entry["failed_count"] += 1
        return stats

    def enrich_agents(self) -> list[dict[str, Any]]:
        """Return agents enriched with policy summaries, tool allowlists, and usage stats."""

        agents = self.agents.list_agents()
        usage_map = self.agent_usage_map()
        enriched: list[dict[str, Any]] = []
        for agent in agents:
            aid = agent["id"]
            policy = self.policies.get_policy(aid)
            rules = policy.get("document", {}).get("rules", [])
            allow_tools: list[str] = []
            deny_tools: list[str] = []
            rate_limits: list[int] = []
            for rule in rules:
                tools = [str(t) for t in rule.get("tools", [])]
                if rule.get("effect") == "allow":
                    for t in tools:
                        if t not in allow_tools:
                            allow_tools.append(t)
                    limit_rpm = (rule.get("limits") or {}).get("rate_limit_per_minute")
                    if isinstance(limit_rpm, int):
                        rate_limits.append(limit_rpm)
                elif rule.get("effect") == "deny":
                    for t in tools:
                        if t not in deny_tools:
                            deny_tools.append(t)
            usage = usage_map.get(
                aid,
                {
                    "total_requests": 0,
                    "succeeded_count": 0,
                    "denied_count": 0,
                    "rate_limited_count": 0,
                    "pending_approval_count": 0,
                    "failed_count": 0,
                    "last_tool": None,
                    "last_decision": None,
                    "last_activity_at": None,
                },
            )
            enriched.append(
                {
                    **agent,
                    "policy_Summary": {
                        "rule_count": len(rules),
                        "allowed_tools": allow_tools,
                        "denied_tools": deny_tools,
                        "rate_limit_per_minute": min(rate_limits) if rate_limits else 60,
                        "updated_at": policy.get("updated_at"),
                    },
                    "allowed_tools": allow_tools,
                    "denied_tools": deny_tools,
                    "rule_count": len(rules),
                    "rate_limit_per_minute": min(rate_limits) if rate_limits else 60,
                    "usage": usage,
                }
            )
        return enriched

    def security_warnings(self) -> list[dict[str, Any]]:
        """Compute active operational and security warnings for the administrator."""

        warnings: list[dict[str, Any]] = []
        vault_status = self.vault.status()
        if not vault_status["exists"]:
            warnings.append(
                {
                    "id": "vault-uninitialized",
                    "severity": "warning",
                    "category": "vault",
                    "title": "Credential vault is not initialized",
                    "message": (
                        "Initialize the AES-256-GCM vault with a strong master password to "
                        "store upstream credentials."
                    ),
                    "section": "credentials",
                    "action_label": "Initialize Vault",
                }
            )
        elif not vault_status["unlocked"]:
            warnings.append(
                {
                    "id": "vault-locked",
                    "severity": "warning",
                    "category": "vault",
                    "title": "Credential vault is locked",
                    "message": (
                        "Agent operations that require upstream credentials will fail closed "
                        "until the vault is unlocked."
                    ),
                    "section": "credentials",
                    "action_label": "Unlock Vault",
                }
            )

        if self.settings_store.get_bool("emergency_disabled"):
            warnings.append(
                {
                    "id": "emergency-stop-active",
                    "severity": "critical",
                    "category": "access",
                    "title": "Emergency agent kill-switch is active",
                    "message": "All agent REST and MCP requests are currently rejected with HTTP 503.",
                    "section": "overview",
                    "action_label": "Resume Agent Access",
                }
            )

        now = utcnow()
        for agent in self.agents.list_agents():
            if not agent["enabled"]:
                continue
            status = agent.get("token_status")
            days = agent.get("days_until_expiry")
            if status == "expired":
                warnings.append(
                    {
                        "id": f"agent-expired-{agent['id']}",
                        "severity": "warning",
                        "category": "agent_token",
                        "title": f"Agent token expired: {agent['name']}",
                        "message": (
                            f"Bearer token {agent['token_prefix']}… has expired. "
                            "Rotate the token or revoke the agent."
                        ),
                        "section": "agents",
                        "action_label": "Manage Agent",
                    }
                )
            elif status == "expiring_soon":
                warnings.append(
                    {
                        "id": f"agent-expiring-{agent['id']}",
                        "severity": "warning",
                        "category": "agent_token",
                        "title": f"Agent token expiring soon: {agent['name']}",
                        "message": (
                            f"Bearer token {agent['token_prefix']}… expires in {days} day(s). "
                            "Rotate before expiry to avoid downtime."
                        ),
                        "section": "agents",
                        "action_label": "Rotate Token",
                    }
                )

        if self.vault.unlocked:
            for secret in self.vault.list_secrets():
                updated_str = secret.get("updated_at") or secret.get("created_at")
                if updated_str:
                    try:
                        age_days = (now - parse_iso(str(updated_str))).days
                        if age_days >= CREDENTIAL_ROTATION_REMINDER_DAYS:
                            warnings.append(
                                {
                                    "id": f"credential-rotation-{secret['id']}",
                                    "severity": "info",
                                    "category": "credential_rotation",
                                    "title": f"Credential rotation reminder: {secret['name']}",
                                    "message": (
                                        f"Last rotated {age_days} days ago (recommended rotation "
                                        f"interval: {CREDENTIAL_ROTATION_REMINDER_DAYS} days)."
                                    ),
                                    "section": "credentials",
                                    "action_label": "Rotate Secret",
                                }
                            )
                    except ValueError:
                        pass

        cutoff_24h = iso(now - timedelta(hours=24))
        auth_fail_row = self.db.query_one(
            "SELECT COUNT(*) AS count FROM audit WHERE ts >= ? AND "
            "(tool = 'auth.failed' OR error_category = 'unauthenticated' OR reason = 'invalid_token')",
            (cutoff_24h,),
        )
        auth_failures_24h = int(auth_fail_row["count"]) if auth_fail_row else 0
        if auth_failures_24h > 0:
            warnings.append(
                {
                    "id": "recent-auth-failures",
                    "severity": "warning",
                    "category": "authentication",
                    "title": f"{auth_failures_24h} failed authentication attempt(s) in the last 24h",
                    "message": "Review the Audit Log for rejected or expired agent tokens.",
                    "section": "audit",
                    "action_label": "Inspect Audit Log",
                }
            )

        rate_limit_row = self.db.query_one(
            "SELECT COUNT(*) AS count FROM audit WHERE ts >= ? AND "
            "(status = 'rate_limited' OR decision = 'rate_limited')",
            (cutoff_24h,),
        )
        rate_limited_24h = int(rate_limit_row["count"]) if rate_limit_row else 0
        if rate_limited_24h > 0:
            warnings.append(
                {
                    "id": "recent-rate-limits",
                    "severity": "info",
                    "category": "rate_limit",
                    "title": f"{rate_limited_24h} request(s) rate-limited in the last 24h",
                    "message": (
                        "Per-agent or per-peer sliding-window rate limits throttled excessive requests."
                    ),
                    "section": "agents",
                    "action_label": "View Agents",
                }
            )

        for conn in self.connections.list_connections():
            if conn["status"] == "error":
                warnings.append(
                    {
                        "id": f"conn-error-{conn['id']}",
                        "severity": "warning",
                        "category": "connection",
                        "title": f"Connection unhealthy: {conn['name']}",
                        "message": (
                            conn.get("last_check_message") or "Last connection diagnostic check failed."
                        ),
                        "section": "connections",
                        "action_label": "Run Diagnostics",
                    }
                )
            elif conn["type"] == "ssh" and conn.get("host_key_trusted") is False:
                warnings.append(
                    {
                        "id": f"ssh-untrusted-{conn['id']}",
                        "severity": "info",
                        "category": "ssh_trust",
                        "title": f"SSH host key not yet enrolled: {conn['name']}",
                        "message": "Scan and trust the SSH host key fingerprint before running SSH tools.",
                        "section": "connections",
                        "action_label": "Enroll Host Key",
                    }
                )

        return warnings

    def readiness(self) -> dict[str, Any]:
        """Return structured readiness and liveness checks."""

        db_ok = False
        schema_info: dict[str, Any] = {}
        try:
            row = self.db.query_one("SELECT 1 AS ok")
            db_ok = bool(row and row["ok"] == 1)
            schema_info = self.db.schema_info()
        except Exception:
            db_ok = False

        vault_status = self.vault.status()
        emergency = self.settings_store.get_bool("emergency_disabled")
        ready = db_ok and not emergency
        return {
            "ready": ready,
            "status": "ready" if ready else "degraded",
            "version": self.settings.version,
            "uptime_seconds": int(time.monotonic() - self.started_at),
            "checks": {
                "database": {"ok": db_ok, "schema_version": schema_info.get("version")},
                "vault": {
                    "initialized": vault_status["exists"],
                    "unlocked": vault_status["unlocked"],
                    "auto_unlock": vault_status["auto_unlock_enabled"],
                },
                "agent_listener": {
                    "enabled": not emergency,
                    "port": self.settings.agent_port,
                    "tls_enabled": self.settings.tls_enabled,
                },
                "ingress_listener": {
                    "port": self.settings.ingress_port,
                    "require_ingress_user": self.settings.require_ingress_user,
                },
            },
        }

    # ------------------------------------------------------------- dashboard

    def status(self) -> dict[str, Any]:
        self.approvals.expire_pending()
        counts = {}
        for name, sql in {
            "agents": "SELECT COUNT(*) AS count FROM agents",
            "enabled_agents": "SELECT COUNT(*) AS count FROM agents WHERE enabled=1",
            "connections": "SELECT COUNT(*) AS count FROM connections",
            "healthy_connections": "SELECT COUNT(*) AS count FROM connections WHERE status='ok'",
            "pending_approvals": "SELECT COUNT(*) AS count FROM approvals WHERE status='pending'",
            "audit_events": "SELECT COUNT(*) AS count FROM audit",
        }.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["count"]) if row else 0

        cutoff_24h = iso(utcnow() - timedelta(hours=24))
        denied_row = self.db.query_one(
            "SELECT COUNT(*) AS count FROM audit WHERE ts >= ? AND decision='deny'",
            (cutoff_24h,),
        )
        rate_limited_row = self.db.query_one(
            "SELECT COUNT(*) AS count FROM audit WHERE ts >= ? AND "
            "(status='rate_limited' OR decision='rate_limited')",
            (cutoff_24h,),
        )
        counts["denied_24h"] = int(denied_row["count"]) if denied_row else 0
        counts["rate_limited_24h"] = int(rate_limited_row["count"]) if rate_limited_row else 0

        vault_status = self.vault.status()
        recent = self.audit.list_events(limit=10)["items"]
        release_summary = self.releases.get_release_summary()
        warnings = self.security_warnings()
        return {
            "version": self.settings.version,
            "uptime_seconds": int(time.monotonic() - self.started_at),
            "vault": vault_status,
            "emergency_disabled": self.settings_store.get_bool("emergency_disabled"),
            "counts": counts,
            "listeners": {
                "ingress_port": self.settings.ingress_port,
                "agent_port": self.settings.agent_port,
                "agent_tls": self.settings.tls_enabled,
            },
            "tool_groups": self.tools.groups(),
            "settings": self.settings_store.all_public(),
            "recent_audit": recent,
            "warnings": warnings,
            "release": {
                "installed_version": release_summary["installed_version"],
                "latest_version": release_summary["latest_version"],
                "update_available": release_summary["update_available"],
                "check_status": release_summary["check_status"],
                "checked_at": release_summary["checked_at"],
                "post_update_notice": release_summary["post_update_notice"],
                "links": release_summary["links"],
            },
            "schema": self.db.schema_info(),
            "security": {
                "admin_allowlist_enforced": True,
                "admin_user_restricted": bool(self.settings.admin_user_ids),
                "agent_origin_allowlist": sorted(self.settings.agent_allowed_origins),
                "tls_verification": "enabled",
                "ssh_host_key_verification": "enabled",
                "arbitrary_shell": "disabled",
                "warning_count": len(warnings),
            },
        }

    # ------------------------------------------------------ policy simulator

    def simulate_policy(
        self,
        *,
        agent_id: str,
        tool_name: str,
        connection_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dry-run a tool invocation through scope, group, target, and policy evaluation."""

        agent = self.agents.get_agent(agent_id)
        spec = self.tools.get_spec(tool_name)
        connection = self.connections.get_public(connection_id)
        raw_params = dict(params or {})

        checks: list[dict[str, Any]] = []
        emergency = self.settings_store.get_bool("emergency_disabled")
        checks.append(
            {
                "check": "emergency_switch",
                "ok": not emergency,
                "detail": "Emergency stop is off" if not emergency else "Emergency stop is active",
            }
        )
        agent_active = bool(agent["enabled"]) and agent.get("token_status") != "expired"
        checks.append(
            {
                "check": "agent_active",
                "ok": agent_active,
                "detail": f"Agent '{agent['name']}' is {agent.get('token_status', 'active')}",
            }
        )
        group_enabled = self.tools.is_group_enabled(spec.group)
        checks.append(
            {
                "check": "tool_group",
                "ok": group_enabled,
                "detail": f"Tool group '{spec.group}' is {'enabled' if group_enabled else 'disabled'}",
            }
        )
        has_scope = spec.scope in agent["scopes"]
        checks.append(
            {
                "check": "agent_scope",
                "ok": has_scope,
                "detail": (
                    f"Agent has required scope '{spec.scope}'"
                    if has_scope
                    else f"Agent is missing required scope '{spec.scope}'"
                ),
            }
        )
        expected_conn_type = spec.group_to_connection_type()
        conn_type_ok = connection["type"] == expected_conn_type
        checks.append(
            {
                "check": "connection_type",
                "ok": conn_type_ok,
                "detail": (
                    f"Connection '{connection['name']}' matches type '{expected_conn_type}'"
                    if conn_type_ok
                    else f"Tool requires '{expected_conn_type}' connection, got '{connection['type']}'"
                ),
            }
        )

        try:
            validated_params = spec.params_model.model_validate(raw_params)
        except ValidationError as exc:
            errors = [
                {"path": ".".join(str(part) for part in e["loc"]), "message": e["msg"]} for e in exc.errors()
            ]
            raise ValidationFailure("simulation parameters are invalid", {"errors": errors}) from exc

        targets = spec.targets(validated_params)
        hard_denied_domain = False
        if tool_name == "ha.call_service":
            domain = str(getattr(validated_params, "domain", ""))
            if domain in HARD_DENIED_SERVICE_DOMAINS:
                hard_denied_domain = True
                checks.append(
                    {
                        "check": "hard_denylist",
                        "ok": False,
                        "detail": f"Service domain '{domain}' is hard-blocked by gateway safety policy",
                    }
                )

        policy_decision = self.policies.evaluate(
            PolicyRequest(
                agent_id=agent_id,
                tool=spec.name,
                connection_id=connection_id,
                risk=spec.risk,
                targets=targets,
            )
        )
        checks.append(
            {
                "check": "policy_rules",
                "ok": policy_decision.allowed,
                "detail": policy_decision.reason,
            }
        )

        overall_allowed = (
            not emergency
            and agent_active
            and group_enabled
            and has_scope
            and conn_type_ok
            and not hard_denied_domain
            and policy_decision.allowed
        )
        if not overall_allowed:
            failed_check = next((c for c in checks if not c["ok"]), None)
            reason = failed_check["detail"] if failed_check else policy_decision.reason
            outcome = "deny"
        elif policy_decision.require_approval:
            outcome = "require_approval"
            reason = f"{policy_decision.reason}; write/sensitive risk requires single-use human approval"
        else:
            outcome = "allow_immediate"
            reason = policy_decision.reason

        return {
            "allowed": overall_allowed,
            "decision": outcome,
            "require_approval": bool(overall_allowed and policy_decision.require_approval),
            "reason": reason,
            "agent": {"id": agent["id"], "name": agent["name"]},
            "tool": {"name": spec.name, "group": spec.group, "risk": spec.risk, "scope": spec.scope},
            "connection": {
                "id": connection["id"],
                "name": connection["name"],
                "type": connection["type"],
            },
            "targets": targets,
            "filters": {k: list(v) for k, v in policy_decision.filters.items()},
            "excludes": {k: list(v) for k, v in policy_decision.excludes.items()},
            "limits": {
                "timeout_seconds": policy_decision.limits.timeout_seconds,
                "max_output_bytes": policy_decision.limits.max_output_bytes,
                "rate_limit_per_minute": policy_decision.limits.rate_limit_per_minute,
            },
            "policy_revision": policy_decision.policy_revision,
            "checks": checks,
        }

    # ------------------------------------------------------ connection tests

    async def test_connection(self, connection_id: str) -> dict[str, Any]:
        record = self.connections.get_connection(connection_id)
        detail: dict[str, Any]
        t0 = time.monotonic()
        checked_at = iso()
        try:
            if record.type == "home_assistant":
                resolved = self.connections.resolve(connection_id, "home_assistant")
                token = resolved.secret.get("token") or resolved.secret.get("value") or ""
                ca = (resolved.extra_secret or {}).get("ca_cert") or (resolved.extra_secret or {}).get(
                    "value"
                )
                async with HomeAssistantClient(str(record.config["base_url"]), token, ca_pem=ca) as client:
                    detail = await client.test()
            elif record.type == "proxmox":
                resolved = self.connections.resolve(connection_id, "proxmox")
                ca = (resolved.extra_secret or {}).get("ca_cert") or (resolved.extra_secret or {}).get(
                    "value"
                )
                async with ProxmoxClient(
                    str(record.config["base_url"]),
                    resolved.secret.get("token_id", ""),
                    resolved.secret.get("token_secret", ""),
                    ca_pem=ca,
                ) as pve_client:
                    detail = await pve_client.test()
            elif record.type == "ssh":
                from .connectors.ssh import SSHClient, SSHCredentials

                resolved = self.connections.resolve(connection_id, "ssh")
                key = resolved.secret.get("private_key") or resolved.secret.get("value") or ""
                ssh_client = SSHClient(
                    SSHCredentials(
                        host=str(record.config["host"]),
                        port=int(record.config.get("port", 22)),
                        username=str(record.config["username"]),
                        private_key=key,
                        passphrase=resolved.secret.get("passphrase"),
                        known_hosts_path=self.settings.ssh_known_hosts_path,
                        connect_timeout=float(record.config.get("connect_timeout_seconds", 10)),
                    )
                )
                detail = await ssh_client.test()
            else:
                raise ValidationFailure("unsupported connection type")
        except GatewayError as exc:
            latency_ms = max(1, int((time.monotonic() - t0) * 1000))
            category, failed_step, remediation = _classify_connection_error(exc, record.type)
            steps = _build_diagnostic_steps(
                connection_type=record.type,
                failed_step=failed_step,
                error_message=exc.message,
            )
            diag_payload = {
                "ok": False,
                "category": category,
                "code": exc.code,
                "message": exc.message,
                "latency_ms": latency_ms,
                "checked_at": checked_at,
                "remediation": remediation,
                "steps": steps,
            }
            self.connections.set_status(connection_id, "error", exc.message, detail=diag_payload)
            self.audit.record(
                actor_type="system",
                actor_id="gateway",
                tool="connection.test",
                connection_id=connection_id,
                decision="test",
                status="failed",
                duration_ms=latency_ms,
                reason=exc.code,
                error_category=category,
            )
            return {
                "ok": False,
                "status": "error",
                "category": category,
                "latency_ms": latency_ms,
                "checked_at": checked_at,
                "remediation": remediation,
                "steps": steps,
                "error": {"code": exc.code, "message": exc.message},
            }

        latency_ms = max(1, int((time.monotonic() - t0) * 1000))
        safe_detail = redact_value(detail) if isinstance(detail, dict) else {}
        steps = _build_diagnostic_steps(
            connection_type=record.type,
            failed_step=None,
            detail=safe_detail if isinstance(safe_detail, dict) else {},
        )
        remediation = "Connection verified and ready for policy-authorized operations."
        diag_payload = {
            "ok": True,
            "category": "ok",
            "latency_ms": latency_ms,
            "checked_at": checked_at,
            "detail": safe_detail,
            "remediation": remediation,
            "steps": steps,
        }
        self.connections.set_status(connection_id, "ok", "connection test succeeded", detail=diag_payload)
        self.audit.record(
            actor_type="system",
            actor_id="gateway",
            tool="connection.test",
            connection_id=connection_id,
            decision="test",
            status="succeeded",
            duration_ms=latency_ms,
        )
        return {
            "ok": True,
            "status": "ok",
            "category": "ok",
            "latency_ms": latency_ms,
            "checked_at": checked_at,
            "remediation": remediation,
            "steps": steps,
            "detail": detail,
        }

    # --------------------------------------------------- diagnostic export

    def diagnostic_bundle(self) -> dict[str, Any]:
        """Generate a redacted, support-safe diagnostic bundle with zero secrets."""

        status = self.status()
        connections_summary = []
        for conn in self.connections.list_connections():
            cfg = dict(conn.get("config") or {})
            if "base_url" in cfg and isinstance(cfg["base_url"], str):
                cfg["base_url"] = redact_url(cfg["base_url"])
            connections_summary.append(
                {
                    "id": conn["id"],
                    "name": conn["name"],
                    "type": conn["type"],
                    "status": conn["status"],
                    "host_key_trusted": conn.get("host_key_trusted"),
                    "last_check_at": conn.get("last_check_at"),
                    "last_ok_at": conn.get("last_ok_at"),
                    "last_check_message": conn.get("last_check_message"),
                    "last_check_detail": conn.get("last_check_detail"),
                    "config_summary": cfg,
                }
            )

        agents_summary = [
            {
                "id": agent["id"],
                "name": agent["name"],
                "enabled": agent["enabled"],
                "token_prefix": agent["token_prefix"],
                "token_status": agent["token_status"],
                "days_until_expiry": agent["days_until_expiry"],
                "scopes": agent["scopes"],
                "allowed_tools": agent["allowed_tools"],
                "denied_tools": agent["denied_tools"],
                "rule_count": agent["rule_count"],
                "rate_limit_per_minute": agent["rate_limit_per_minute"],
                "usage": agent["usage"],
                "created_at": agent["created_at"],
                "last_used_at": agent["last_used_at"],
                "expires_at": agent["expires_at"],
            }
            for agent in self.enrich_agents()
        ]

        credentials_summary = []
        if self.vault.unlocked:
            for item in self.vault.list_secrets():
                credentials_summary.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "kind": item["kind"],
                        "created_at": item["created_at"],
                        "updated_at": item["updated_at"],
                    }
                )

        bundle = {
            "format": "hag-diagnostics-v1",
            "generated_at": iso(),
            "repository": REPO_LINKS["repository"],
            "version": self.settings.version,
            "schema": self.db.schema_info(),
            "readiness": self.readiness(),
            "security": status["security"],
            "warnings": status["warnings"],
            "settings": status["settings"],
            "listeners": status["listeners"],
            "tool_groups": status["tool_groups"],
            "counts": status["counts"],
            "connections": connections_summary,
            "credentials": credentials_summary,
            "agents": agents_summary,
            "recent_audit": self.audit.list_events(limit=25)["items"],
        }
        redacted = redact_value(bundle)
        return redacted if isinstance(redacted, dict) else bundle

    async def scan_ssh_host(self, connection_id: str) -> dict[str, Any]:
        record = self.connections.get_connection(connection_id)
        if record.type != "ssh":
            raise ValidationFailure("connection is not an SSH connection")
        host = str(record.config["host"])
        port = int(record.config.get("port", 22))
        key = await scan_host_key(host, port)
        existing = _trusted_key_for(self.connections.known_hosts_path, host, port)
        trusted = existing is not None
        matches = existing == key["key"] if existing else None
        return {
            "host": host,
            "port": port,
            **key,
            "trusted": trusted,
            "matches_trusted": matches,
            "requires_replacement": bool(trusted and matches is False),
        }

    def trust_ssh_host(
        self, connection_id: str, *, fingerprint: str, replace: bool = False
    ) -> dict[str, Any]:
        record = self.connections.get_connection(connection_id)
        if record.type != "ssh":
            raise ValidationFailure("connection is not an SSH connection")
        return {
            "pending": True,
            "host": record.config["host"],
            "port": record.config.get("port", 22),
            "fingerprint": fingerprint,
            "replace": replace,
        }

    async def trust_ssh_host_async(
        self, connection_id: str, *, fingerprint: str, replace: bool = False
    ) -> dict[str, Any]:
        """Rescan and trust only if the fingerprint the admin confirmed still matches."""

        record = self.connections.get_connection(connection_id)
        if record.type != "ssh":
            raise ValidationFailure("connection is not an SSH connection")
        host = str(record.config["host"])
        port = int(record.config.get("port", 22))
        key = await scan_host_key(host, port)
        if key["fingerprint"] != fingerprint:
            raise ValidationFailure("the SSH host key changed while confirming; scan again")
        self.connections.trust_host_key(host, port, key["key_type"], key["key"], replace=replace)
        return {"trusted": True, **key}

    def get_ssh_trust(self, connection_id: str) -> dict[str, Any]:
        record = self.connections.get_connection(connection_id)
        if record.type != "ssh":
            raise NotFoundError("SSH connection")
        host = str(record.config["host"])
        port = int(record.config.get("port", 22))
        key = _trusted_key_for(self.connections.known_hosts_path, host, port)
        return {"host": host, "port": port, "trusted": key is not None}


def _trusted_key_for(known_hosts_path: Path, host: str, port: int) -> str | None:
    if not known_hosts_path.is_file():
        return None
    token = host if port == 22 and ":" not in host else (f"[{host}]" if port == 22 else f"[{host}]:{port}")
    for line in known_hosts_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == token:
            return parts[2]
    return None
