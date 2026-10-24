"""Restricted SSH connector using AsyncSSH.

Host keys are verified against a managed known-hosts file.  Remote commands are
fixed argv vectors assembled by the gateway and serialized with
``shlex.join``; agent input is never concatenated into a command string.
Log reads use SFTP and an administrator allowlist, with symlink escape checks.
Arbitrary shell execution is not implemented.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import asyncssh

from ..errors import ConnectorError, OutputTooLarge, PolicyDenied, ValidationFailure
from ..redaction import redact_text
from ..utils import validate_absolute_path

STDERR_LIMIT = 4096
PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_-]*)\}")


@dataclass(frozen=True)
class SSHCredentials:
    host: str
    port: int
    username: str
    private_key: str
    passphrase: str | None
    known_hosts_path: Path
    connect_timeout: float = 10.0


def _import_key(private_key: str, passphrase: str | None) -> asyncssh.SSHKey:
    try:
        return asyncssh.import_private_key(private_key, passphrase=passphrase)
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise ConnectorError(
            "ssh_credential_invalid", "the SSH private key could not be loaded", status=409
        ) from exc


def _host_key_fields(key: asyncssh.SSHKey) -> dict[str, str]:
    exported = key.export_public_key("openssh").decode("ascii").strip().split()
    if len(exported) < 2:
        raise ConnectorError("ssh_scan_failed", "the SSH server did not present a usable host key")
    return {
        "key_type": exported[0],
        "key": exported[1],
        "fingerprint": key.get_fingerprint(),
        "algorithm": key.get_algorithm(),
    }


def _known_host_token(host: str, port: int) -> str:
    if port == 22:
        if ":" in host:
            return f"[{host}]"
        return host
    return f"[{host}]:{port}"


def has_trusted_host_key(known_hosts_path: Path, host: str, port: int) -> bool:
    if not known_hosts_path.is_file():
        return False
    token = _known_host_token(host, port)
    for line in known_hosts_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.split(" ")[0] == token:
            return True
    return False


async def scan_host_key(host: str, port: int, *, timeout: float = 10.0) -> dict[str, str]:
    try:
        async with asyncio.timeout(timeout):
            key = await asyncssh.get_server_host_key(host, port)
    except TimeoutError as exc:
        raise ConnectorError("ssh_timeout", "the SSH server did not respond in time", status=504) from exc
    except (OSError, asyncssh.Error) as exc:
        raise ConnectorError("ssh_scan_failed", "could not retrieve the SSH host key") from exc
    if key is None:
        raise ConnectorError("ssh_scan_failed", "the SSH server did not present a host key")
    return _host_key_fields(key)


class SSHClient:
    def __init__(self, credentials: SSHCredentials) -> None:
        self.credentials = credentials
        self._key = _import_key(credentials.private_key, credentials.passphrase)

    def _ensure_known_hosts(self) -> None:
        path = self.credentials.known_hosts_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            path.touch(mode=0o600)

    async def _connect(self) -> asyncssh.SSHClientConnection:
        self._ensure_known_hosts()
        creds = self.credentials
        if not has_trusted_host_key(creds.known_hosts_path, creds.host, creds.port):
            raise ConnectorError(
                "ssh_host_key_unknown",
                "the SSH host key is not trusted; enroll it from the management panel first",
                status=502,
            )
        try:
            async with asyncio.timeout(creds.connect_timeout + 5):
                return await asyncssh.connect(
                    creds.host,
                    creds.port,
                    username=creds.username,
                    client_keys=[self._key],
                    known_hosts=str(creds.known_hosts_path),
                    config=None,
                    connect_timeout=creds.connect_timeout,
                    login_timeout=creds.connect_timeout,
                    request_pty=False,
                    encoding=None,
                )
        except asyncssh.HostKeyNotVerifiable as exc:
            raise ConnectorError(
                "ssh_host_key_changed",
                "the SSH host key does not match the trusted host key",
                status=502,
            ) from exc
        except asyncssh.PermissionDenied as exc:
            raise ConnectorError("ssh_auth_failed", "SSH authentication failed", status=502) from exc
        except TimeoutError as exc:
            raise ConnectorError("ssh_timeout", "the SSH connection timed out", status=504) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ConnectorError(
                "ssh_unreachable", "could not establish the SSH connection", status=502
            ) from exc

    async def test(self) -> dict[str, Any]:
        conn = await self._connect()
        try:
            key = conn.get_server_host_key()
            fields = _host_key_fields(key) if key is not None else {}
            return {"reachable": True, **fields}
        finally:
            conn.close()
            await conn.wait_closed()

    async def run(
        self,
        argv: list[str],
        *,
        max_bytes: int,
        timeout: float,
        accepted_exit_codes: set[int] | None = None,
    ) -> dict[str, Any]:
        if not argv or not all(isinstance(part, str) and "\x00" not in part for part in argv):
            raise ValidationFailure("invalid command")
        command = shlex.join(argv)
        conn = await self._connect()
        try:
            async with asyncio.timeout(timeout):
                process = await conn.create_process(command, encoding=None, request_pty=False)
                stdout, stderr = await asyncio.gather(
                    _read_bounded(process.stdout, max_bytes),
                    _read_bounded(process.stderr, STDERR_LIMIT, truncate=True),
                )
                if stdout.truncated:
                    process.kill()
                await process.wait()
                exit_status = process.exit_status
        except TimeoutError as exc:
            raise ConnectorError("ssh_timeout", "the SSH command timed out", status=504) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ConnectorError(
                "ssh_command_failed", "the SSH command could not be completed", status=502
            ) from exc
        finally:
            conn.close()
            await conn.wait_closed()

        if stdout.truncated:
            raise OutputTooLarge(max_bytes)
        accepted = accepted_exit_codes if accepted_exit_codes is not None else {0}
        if exit_status not in accepted:
            raise ConnectorError(
                "ssh_command_failed",
                f"the remote command exited with status {exit_status}",
                status=502,
                details={
                    "exit_code": exit_status,
                    "stderr": redact_text(stderr.text)[:1000],
                },
            )
        return {
            "stdout": _decode(stdout.data),
            "stderr": redact_text(_decode(stderr.data)),
            "exit_code": exit_status,
        }

    async def read_log(
        self,
        *,
        path: str,
        allowed_paths: list[str],
        lines: int,
        max_bytes: int,
        timeout: float,
    ) -> dict[str, Any]:
        path = validate_absolute_path(path)
        if not allowed_paths:
            raise PolicyDenied("no log paths are configured for this connection")
        if not _requested_path_allowed(path, allowed_paths):
            raise PolicyDenied("log path is outside the configured allowlist")
        conn = await self._connect()
        try:
            async with asyncio.timeout(timeout):
                sftp = await conn.start_sftp_client()
                resolved = await sftp.realpath(path)
                if not _resolved_path_allowed(resolved, allowed_paths):
                    raise PolicyDenied("log path resolves outside the configured allowlist")
                attrs = await sftp.stat(path)
                mode = getattr(attrs, "mode", 0) or 0
                if not stat.S_ISREG(mode):
                    raise ValidationFailure("log path is not a regular file")
                size = int(attrs.size or 0)
                read_size = min(max_bytes, size)
                offset = max(0, size - read_size)
                async with sftp.open(path, "rb") as handle:
                    if offset:
                        await handle.seek(offset)
                    data = cast(bytes, await handle.read(read_size))
                sftp.exit()
        except TimeoutError as exc:
            raise ConnectorError("ssh_timeout", "reading the log timed out", status=504) from exc
        except (asyncssh.SFTPError, OSError) as exc:
            raise ConnectorError(
                "ssh_read_failed", "could not read the configured log path", status=502
            ) from exc
        except asyncssh.Error as exc:
            raise ConnectorError("ssh_command_failed", "the SSH session failed", status=502) from exc
        finally:
            conn.close()
            await conn.wait_closed()

        text = data.decode("utf-8", errors="replace")
        tail = text.splitlines()[-lines:] if lines > 0 else []
        return {
            "path": path,
            "lines": tail,
            "line_count": len(tail),
            "bytes": len(data),
            "truncated": offset > 0 or text.count("\n") + 1 > len(tail),
        }


@dataclass
class _BoundedOutput:
    data: bytes
    truncated: bool

    @property
    def text(self) -> str:
        return _decode(self.data)


async def _read_bounded(stream: Any, limit: int, *, truncate: bool = False) -> _BoundedOutput:
    """Read to EOF or the bound; a short read is not an EOF signal.

    Stdout stops at overflow so the caller can kill the process.  For stderr
    (``truncate=True``), keep draining and discarding overflow so an unread
    pipe cannot prevent the remote process from exiting.
    """

    data = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        remaining = max(0, limit - len(data))
        if remaining:
            data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
            if not truncate:
                break
    return _BoundedOutput(data=bytes(data), truncated=truncated)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _requested_path_allowed(path: str, allowed_paths: list[str]) -> bool:
    for entry in allowed_paths:
        if entry.endswith("/"):
            if path.startswith(entry) and path != entry:
                return True
        elif path == entry:
            return True
    return False


def _resolved_path_allowed(resolved: str, allowed_paths: list[str]) -> bool:
    normalized = posixpath.normpath(resolved)
    for entry in allowed_paths:
        if entry.endswith("/"):
            prefix = posixpath.normpath(entry) + "/"
            if normalized.startswith(prefix):
                return True
        elif normalized == posixpath.normpath(entry):
            return True
    return False


def render_template_argv(template: Any, params: dict[str, str]) -> list[str]:
    allowed_params: dict[str, list[str]] = template.params
    unknown = set(params) - set(allowed_params)
    if unknown:
        raise ValidationFailure("command template received an unknown parameter")
    argv: list[str] = []
    for part in template.argv:
        placeholders = PLACEHOLDER_RE.findall(part)
        if not placeholders:
            argv.append(part)
            continue
        rendered = part
        for name in placeholders:
            if name not in allowed_params:
                raise ValidationFailure("command template contains an unresolved parameter")
            if name not in params:
                raise ValidationFailure("command template parameter is required")
            value = params[name]
            if value not in allowed_params[name]:
                raise PolicyDenied("command parameter value is not allowed")
            rendered = rendered.replace("{" + name + "}", value)
        if PLACEHOLDER_RE.search(rendered):
            raise ValidationFailure("command template contains an unresolved parameter")
        argv.append(rendered)
    return argv
