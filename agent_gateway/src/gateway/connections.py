"""Connection profiles and credential resolution.

Connection profiles contain administrative configuration only.  Secret
material is referenced by credential id and read from the encrypted vault at
operation time.  Agent-facing listings never expose hostnames or configuration.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .connectors.ssh import has_trusted_host_key
from .errors import ConflictError, NotFoundError, ValidationFailure
from .redaction import redact_value
from .storage.db import Database
from .utils import CONNECTION_NAME_RE, iso, validate_absolute_path
from .vault.manager import CredentialVault

logger = logging.getLogger(__name__)

ConnectionType = Literal["home_assistant", "proxmox", "ssh"]
CONNECTION_TYPES = frozenset({"home_assistant", "proxmox", "ssh"})
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,64}$")
TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")

CREDENTIAL_KIND_BY_TYPE = {
    "home_assistant": "home_assistant_token",
    "proxmox": "proxmox_token",
    "ssh": "ssh_private_key",
}


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("base_url is invalid")
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"}:
        raise ValueError("base_url must use http or https")
    if not parts.hostname:
        raise ValueError("base_url must include a host")
    if parts.username or parts.password:
        raise ValueError("base_url must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    if parts.path not in {"", "/"} and not parts.path.startswith("/"):
        raise ValueError("base_url path is invalid")
    return value.rstrip("/")


class HomeAssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    verify_tls: bool = True
    ca_cert_secret_id: str | None = None

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _validate_base_url(value)


class ProxmoxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    verify_tls: bool = True
    ca_cert_secret_id: str | None = None

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _validate_base_url(value)


class CommandTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str = Field(default="", max_length=200)
    argv: list[str] = Field(min_length=1, max_length=20)
    params: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not TEMPLATE_ID_RE.fullmatch(value):
            raise ValueError("command template id is invalid")
        return value

    @field_validator("argv")
    @classmethod
    def _argv(cls, values: list[str]) -> list[str]:
        if not all(isinstance(value, str) and 0 < len(value) <= 256 for value in values):
            raise ValueError("command template arguments are invalid")
        if any("\x00" in value or "\n" in value for value in values):
            raise ValueError("command template arguments are invalid")
        return values

    @field_validator("params")
    @classmethod
    def _params(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(value) > 10:
            raise ValueError("command template has too many parameters")
        for name, allowed in value.items():
            if not TEMPLATE_ID_RE.fullmatch(name):
                raise ValueError("command template parameter name is invalid")
            if not allowed or len(allowed) > 50:
                raise ValueError("command template parameter allowlist is invalid")
            for item in allowed:
                if not isinstance(item, str) or not item or len(item) > 128:
                    raise ValueError("command template parameter allowlist is invalid")
                if any(char in item for char in ("\x00", "\n", "\r", " ")):
                    raise ValueError("command template parameter values must not contain whitespace")
        return value


class SSHConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str
    allowed_services: list[str] = Field(default_factory=list, max_length=50)
    allowed_log_paths: list[str] = Field(default_factory=list, max_length=20)
    command_templates: list[CommandTemplate] = Field(default_factory=list, max_length=10)
    connect_timeout_seconds: int = Field(default=10, ge=3, le=30)

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        value = value.strip()
        if not HOST_RE.fullmatch(value):
            raise ValueError("SSH host is invalid")
        return value

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("SSH username must be a dedicated lower-case account name")
        if value == "root":
            raise ValueError("root SSH accounts are not supported; use a dedicated restricted account")
        return value

    @field_validator("allowed_services")
    @classmethod
    def _services(cls, values: list[str]) -> list[str]:
        for value in values:
            if not SERVICE_NAME_RE.fullmatch(value):
                raise ValueError("allowed service name is invalid")
        return list(dict.fromkeys(values))

    @field_validator("allowed_log_paths")
    @classmethod
    def _paths(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            validate_absolute_path(value)
            normalized.append(value)
        return list(dict.fromkeys(normalized))

    @field_validator("command_templates")
    @classmethod
    def _unique_templates(cls, values: list[CommandTemplate]) -> list[CommandTemplate]:
        ids = [value.id for value in values]
        if len(ids) != len(set(ids)):
            raise ValueError("command template ids must be unique")
        return values


CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "home_assistant": HomeAssistantConfig,
    "proxmox": ProxmoxConfig,
    "ssh": SSHConfig,
}


@dataclass(frozen=True)
class ConnectionRecord:
    id: str
    name: str
    type: str
    config: dict[str, Any]
    credential_id: str | None
    status: str
    last_check_at: str | None
    last_ok_at: str | None
    last_check_message: str | None
    last_check_detail: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ResolvedConnection:
    record: ConnectionRecord
    secret: dict[str, str]
    extra_secret: dict[str, str] | None

    def public_admin(self) -> dict[str, Any]:
        return {
            "id": self.record.id,
            "name": self.record.name,
            "type": self.record.type,
            "config": self.record.config,
            "credential_id": self.record.credential_id,
            "status": self.record.status,
            "last_check_at": self.record.last_check_at,
            "last_ok_at": self.record.last_ok_at,
            "last_check_message": self.record.last_check_message,
            "last_check_detail": self.record.last_check_detail,
            "created_at": self.record.created_at,
            "updated_at": self.record.updated_at,
        }


class ConnectionService:
    def __init__(self, db: Database, vault: CredentialVault, known_hosts_path: Path) -> None:
        self.db = db
        self.vault = vault
        self.known_hosts_path = known_hosts_path

    # ------------------------------------------------------------- CRUD

    def _row(self, row: sqlite3.Row) -> ConnectionRecord:
        keys = set(row.keys())
        raw_detail = row["last_check_detail"] if "last_check_detail" in keys else "{}"
        try:
            parsed_detail = json.loads(raw_detail) if raw_detail else {}
            if not isinstance(parsed_detail, dict):
                parsed_detail = {}
        except (TypeError, json.JSONDecodeError):
            parsed_detail = {}
        return ConnectionRecord(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            config=json.loads(row["config"]),
            credential_id=row["credential_id"],
            status=row["status"],
            last_check_at=row["last_check_at"],
            last_ok_at=row["last_ok_at"] if "last_ok_at" in keys else None,
            last_check_message=row["last_check_message"],
            last_check_detail=parsed_detail,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _validate(self, connection_type: str, config: dict[str, Any]) -> dict[str, Any]:
        if connection_type not in CONNECTION_TYPES:
            raise ValidationFailure("connection type is not supported")
        model = CONFIG_MODELS[connection_type]
        try:
            parsed = model.model_validate(config)
        except ValidationError as exc:
            errors = [
                {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                for error in exc.errors()
            ]
            raise ValidationFailure("connection configuration is invalid", {"errors": errors}) from exc
        data = parsed.model_dump(mode="json")
        if data.get("verify_tls") is False:
            raise ValidationFailure("TLS verification cannot be disabled")
        return data

    def _validate_credential(self, connection_type: str, credential_id: str | None) -> None:
        if not credential_id:
            raise ValidationFailure("a credential is required for this connection")
        secret = self.vault.get_secret(credential_id)
        expected = CREDENTIAL_KIND_BY_TYPE[connection_type]
        if secret.kind != expected:
            raise ValidationFailure(f"credential kind must be {expected} for this connection")

    def create_connection(
        self,
        *,
        name: str,
        connection_type: str,
        config: dict[str, Any],
        credential_id: str | None,
    ) -> dict[str, Any]:
        if not CONNECTION_NAME_RE.fullmatch(name):
            raise ValidationFailure("connection name is invalid")
        validated = self._validate(connection_type, config)
        self._validate_credential(connection_type, credential_id)
        now = iso()
        connection_id = uuid.uuid4().hex
        try:
            self.db.execute(
                "INSERT INTO connections(id, name, type, config, credential_id, status, created_at, "
                "updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    connection_id,
                    name,
                    connection_type,
                    json.dumps(validated),
                    credential_id,
                    "unconfigured",
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("a connection with that name already exists") from exc
        logger.info("connection created id=%s type=%s", connection_id, connection_type)
        return self._public(self.get_connection(connection_id))

    def list_connections(self) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM connections ORDER BY name COLLATE NOCASE")
        return [self._public(self._row(row)) for row in rows]

    def _public(self, record: ConnectionRecord) -> dict[str, Any]:
        host_key_trusted: bool | None = None
        if record.type == "ssh":
            host = str(record.config.get("host") or "")
            port = int(record.config.get("port") or 22)
            host_key_trusted = has_trusted_host_key(self.known_hosts_path, host, port) if host else False
        return {
            "id": record.id,
            "name": record.name,
            "type": record.type,
            "config": record.config,
            "credential_id": record.credential_id,
            "status": record.status,
            "last_check_at": record.last_check_at,
            "last_ok_at": record.last_ok_at,
            "last_check_message": record.last_check_message,
            "last_check_detail": record.last_check_detail,
            "host_key_trusted": host_key_trusted,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def get_connection(self, connection_id: str) -> ConnectionRecord:
        row = self.db.query_one("SELECT * FROM connections WHERE id=?", (connection_id,))
        if row is None:
            raise NotFoundError("connection")
        return self._row(row)

    def get_public(self, connection_id: str) -> dict[str, Any]:
        return self._public(self.get_connection(connection_id))

    def update_connection(
        self,
        connection_id: str,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        credential_id: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_connection(connection_id)
        new_name = name or current.name
        if not CONNECTION_NAME_RE.fullmatch(new_name):
            raise ValidationFailure("connection name is invalid")
        new_config = self._validate(current.type, config if config is not None else current.config)
        new_credential = credential_id if credential_id is not None else current.credential_id
        self._validate_credential(current.type, new_credential)
        try:
            self.db.execute(
                "UPDATE connections SET name=?, config=?, credential_id=?, status='unconfigured', "
                "last_check_at=NULL, last_check_message=NULL, last_check_detail='{}', "
                "updated_at=? WHERE id=?",
                (new_name, json.dumps(new_config), new_credential, iso(), connection_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("a connection with that name already exists") from exc
        return self.get_public(connection_id)

    def delete_connection(self, connection_id: str) -> None:
        self.get_connection(connection_id)
        self.db.execute("DELETE FROM connections WHERE id=?", (connection_id,))
        self._remove_known_host_entry_for(connection_id)
        logger.info("connection deleted id=%s", connection_id)

    def set_status(
        self,
        connection_id: str,
        status: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        now = iso()
        safe_detail = redact_value(detail or {})
        detail_json = json.dumps(safe_detail if isinstance(safe_detail, dict) else {})
        if status == "ok":
            self.db.execute(
                "UPDATE connections SET status=?, last_check_at=?, last_ok_at=?, "
                "last_check_message=?, last_check_detail=? WHERE id=?",
                (status, now, now, message[:300], detail_json, connection_id),
            )
        else:
            self.db.execute(
                "UPDATE connections SET status=?, last_check_at=?, "
                "last_check_message=?, last_check_detail=? WHERE id=?",
                (status, now, message[:300], detail_json, connection_id),
            )

    # -------------------------------------------------------- resolution

    def resolve(self, connection_id: str, expected_type: str) -> ResolvedConnection:
        record = self.get_connection(connection_id)
        if record.type != expected_type:
            raise ValidationFailure(f"connection is not a {expected_type} connection")
        if not record.credential_id:
            raise ValidationFailure("connection has no credential")
        secret = self.vault.get_secret(record.credential_id)
        extra = None
        ca_id = record.config.get("ca_cert_secret_id")
        if ca_id:
            extra = self.vault.get_secret(ca_id).value
        return ResolvedConnection(record=record, secret=secret.value, extra_secret=extra)

    def agent_visible(self) -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "name": record.name,
                "type": record.type,
                "status": record.status,
            }
            for record in (
                self._row(row) for row in self.db.query_all("SELECT * FROM connections ORDER BY name")
            )
        ]

    def connections_for_type(self, connection_type: str) -> list[ConnectionRecord]:
        rows = self.db.query_all("SELECT * FROM connections WHERE type=? ORDER BY name", (connection_type,))
        return [self._row(row) for row in rows]

    # ------------------------------------------------------- known hosts

    def known_hosts_entry(self, host: str, port: int, key_type: str, key_b64: str) -> str:
        host_part = host
        try:
            address = ipaddress.ip_address(host)
            if address.version == 6:
                host_part = f"[{host}]"
        except ValueError:
            pass
        if port != 22:
            host_part = f"[{host}]:{port}" if not host_part.startswith("[") else f"{host_part}:{port}"
        return f"{host_part} {key_type} {key_b64}"

    def trust_host_key(
        self, host: str, port: int, key_type: str, key_b64: str, *, replace: bool = False
    ) -> None:
        entry = self.known_hosts_entry(host, port, key_type, key_b64)
        existing: list[str] = []
        if self.known_hosts_path.exists():
            existing = [
                line.strip()
                for line in self.known_hosts_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
        same_host = [line for line in existing if line.split(" ")[0] == entry.split(" ")[0]]
        if same_host and not replace:
            if entry in same_host:
                return
            raise ConflictError(
                "a different host key is already trusted for this host; explicit replacement required"
            )
        updated = [line for line in existing if line.split(" ")[0] != entry.split(" ")[0]] + [entry]
        self.known_hosts_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.known_hosts_path.with_name(self.known_hosts_path.name + ".tmp")
        temp.write_text("\n".join(updated) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(self.known_hosts_path)
        logger.info("SSH host key trusted host=%s type=%s", host, key_type)

    def _remove_known_host_entry_for(self, connection_id: str) -> None:
        # Known-host entries are intentionally retained when a connection is
        # deleted: the trust decision belongs to the administrator and keeping
        # it avoids silent re-enrollment.  This hook exists for clarity.
        _ = connection_id

    def known_hosts_path_for_test(self) -> Path:
        return self.known_hosts_path
