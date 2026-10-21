"""Runtime settings loaded from add-on options and environment variables.

Home Assistant writes add-on options to ``/data/options.json``.  Secrets are
not accepted in options; they belong in the encrypted vault.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import __version__
from .errors import ConfigError

LOG_LEVELS = {"debug", "info", "warning", "error"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    version: str = __version__
    log_level: str = "info"
    ingress_port: int = 8099
    agent_port: int = 8098
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None
    admin_allowed_ips: frozenset[str] = frozenset({"172.30.32.2"})
    admin_user_ids: frozenset[str] = frozenset()
    agent_allowed_origins: frozenset[str] = frozenset()
    agent_allowed_hosts: frozenset[str] = frozenset()
    require_ingress_user: bool = True
    max_json_body_bytes: int = 1_048_576
    mcp_max_request_bytes: int = 2_097_152
    audit_retention_days: int = 30
    approval_ttl_seconds: int = 600
    max_operation_timeout_seconds: int = 120
    max_output_bytes: int = 262_144
    hostname: str = "agent-gateway"
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def vault_path(self) -> Path:
        return self.data_dir / "vault.json"

    @property
    def unlock_key_path(self) -> Path:
        return self.data_dir / "unlock.key"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "gateway.db"

    @property
    def ssh_known_hosts_path(self) -> Path:
        return self.data_dir / "ssh_known_hosts"

    @property
    def tls_enabled(self) -> bool:
        return self.tls_certfile is not None

    def with_overrides(self, **kwargs: Any) -> Settings:
        return replace(self, **kwargs)


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _parse_port(value: Any, name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigError(f"{name} must be between 1 and 65535")
    return port


def _parse_ip_list(value: str) -> frozenset[str]:
    entries = {item.strip() for item in value.split(",") if item.strip()}
    for entry in entries:
        try:
            ipaddress.ip_address(entry)
        except ValueError as exc:
            # ASGI test client uses the literal host name "testclient"; allow
            # only explicit, non-wildcard host names for development setups.
            if not entry.replace("-", "").replace(".", "").isalnum():
                raise ConfigError("admin allowed IP list contains an invalid entry") from exc
    return frozenset(entries)


def load_settings(
    options_path: Path | None = None,
    env: dict[str, str] | None = None,
    *,
    data_dir: Path | None = None,
) -> Settings:
    environ = os.environ if env is None else env
    resolved_data_dir = data_dir or Path(environ.get("HAG_DATA_DIR", "/data"))
    options_file = options_path or resolved_data_dir / "options.json"

    options: dict[str, Any] = {}
    if options_file.exists():
        try:
            raw = json.loads(options_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError("could not read add-on options") from exc
        if not isinstance(raw, dict):
            raise ConfigError("add-on options must be a JSON object")
        options = raw

    log_level = str(options.get("log_level", "info")).lower()
    if log_level not in LOG_LEVELS:
        raise ConfigError("log_level must be one of debug, info, warning, error")

    ingress_port = _parse_port(environ.get("HAG_INGRESS_PORT", 8099), "ingress port")
    agent_port = _parse_port(environ.get("HAG_AGENT_PORT", 8098), "agent port")
    if ingress_port == agent_port:
        raise ConfigError("ingress and agent ports must differ")

    cert = options.get("tls_certfile") or environ.get("HAG_TLS_CERTFILE") or None
    key = options.get("tls_keyfile") or environ.get("HAG_TLS_KEYFILE") or None
    if bool(cert) != bool(key):
        raise ConfigError("tls_certfile and tls_keyfile must be set together")
    cert_path = Path(str(cert)) if cert else None
    key_path = Path(str(key)) if key else None
    if cert_path is not None and (not cert_path.is_file() or not key_path or not key_path.is_file()):
        raise ConfigError("configured TLS certificate or key file is missing")

    allowed_ips_raw = environ.get("HAG_ADMIN_ALLOWED_IPS", "172.30.32.2")
    allowed_ips = _parse_ip_list(allowed_ips_raw)

    user_ids_raw = options.get("admin_user_ids") or []
    if not isinstance(user_ids_raw, list) or not all(isinstance(item, str) for item in user_ids_raw):
        raise ConfigError("admin_user_ids must be a list of Home Assistant user IDs")
    admin_user_ids = frozenset(item.strip() for item in user_ids_raw if item.strip())

    require_ingress_user = _parse_bool(environ.get("HAG_REQUIRE_INGRESS_USER", "1"), True)
    agent_origins = frozenset(
        item.strip().rstrip("/")
        for item in environ.get("HAG_AGENT_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    )
    agent_hosts = frozenset(
        item.strip().lower() for item in environ.get("HAG_AGENT_ALLOWED_HOSTS", "").split(",") if item.strip()
    )

    retention = int(options.get("audit_retention_days", 30))
    if not 1 <= retention <= 3650:
        raise ConfigError("audit_retention_days must be between 1 and 3650")
    approval_ttl = int(options.get("approval_ttl_seconds", 600))
    if not 30 <= approval_ttl <= 3600:
        raise ConfigError("approval_ttl_seconds must be between 30 and 3600")

    data_dir_path = resolved_data_dir
    if not data_dir_path.exists():
        try:
            data_dir_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise ConfigError("data directory is not writable") from exc

    return Settings(
        data_dir=data_dir_path,
        log_level=log_level,
        ingress_port=ingress_port,
        agent_port=agent_port,
        tls_certfile=cert_path,
        tls_keyfile=key_path,
        admin_allowed_ips=allowed_ips,
        admin_user_ids=admin_user_ids,
        agent_allowed_origins=agent_origins,
        agent_allowed_hosts=agent_hosts,
        require_ingress_user=require_ingress_user,
        audit_retention_days=retention,
        approval_ttl_seconds=approval_ttl,
        hostname=environ.get("HAG_HOSTNAME", "agent-gateway"),
    )
