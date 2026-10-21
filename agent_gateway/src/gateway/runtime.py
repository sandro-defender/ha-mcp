"""Application composition root."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from .approvals.service import ApprovalService
from .audit.service import AuditService
from .auth.agents import AgentService
from .auth.rate_limit import SlidingWindowLimiter
from .connections import ConnectionService
from .connectors.home_assistant import HomeAssistantClient
from .connectors.proxmox import ProxmoxClient
from .connectors.ssh import scan_host_key
from .errors import GatewayError, NotFoundError, ValidationFailure
from .operations import OperationService
from .policy.engine import PolicyService
from .settings import Settings
from .storage.db import Database, SettingsStore
from .tools.registry import ToolRegistry
from .vault.manager import CredentialVault

logger = logging.getLogger(__name__)


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

    # ------------------------------------------------------------- dashboard

    def status(self) -> dict[str, Any]:
        counts = {}
        for name, sql in {
            "agents": "SELECT COUNT(*) AS count FROM agents",
            "enabled_agents": "SELECT COUNT(*) AS count FROM agents WHERE enabled=1",
            "connections": "SELECT COUNT(*) AS count FROM connections",
            "pending_approvals": "SELECT COUNT(*) AS count FROM approvals WHERE status='pending'",
        }.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["count"]) if row else 0
        vault_status = self.vault.status()
        recent = self.audit.list_events(limit=10)["items"]
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
            "security": {
                "admin_allowlist_enforced": True,
                "admin_user_restricted": bool(self.settings.admin_user_ids),
                "agent_origin_allowlist": sorted(self.settings.agent_allowed_origins),
                "tls_verification": "enabled",
                "ssh_host_key_verification": "enabled",
                "arbitrary_shell": "disabled",
            },
        }

    # ------------------------------------------------------ connection tests

    async def test_connection(self, connection_id: str) -> dict[str, Any]:
        record = self.connections.get_connection(connection_id)
        detail: dict[str, Any]
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
            self.connections.set_status(connection_id, "error", exc.message)
            self.audit.record(
                actor_type="system",
                actor_id="gateway",
                tool="connection.test",
                connection_id=connection_id,
                decision="test",
                status="failed",
                reason=exc.code,
                error_category=exc.code,
            )
            return {"ok": False, "status": "error", "error": {"code": exc.code, "message": exc.message}}
        self.connections.set_status(connection_id, "ok", "connection test succeeded")
        self.audit.record(
            actor_type="system",
            actor_id="gateway",
            tool="connection.test",
            connection_id=connection_id,
            decision="test",
            status="succeeded",
        )
        return {"ok": True, "status": "ok", "detail": detail}

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
