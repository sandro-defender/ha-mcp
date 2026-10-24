"""SSH host-key trust, command templates, and path restrictions."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from helpers import PASSWORD
from pydantic import ValidationError

from gateway.connections import CommandTemplate, ConnectionService, SSHConfig
from gateway.connectors.ssh import has_trusted_host_key, render_template_argv
from gateway.errors import ConflictError, PolicyDenied, ValidationFailure
from gateway.storage.db import Database
from gateway.vault.manager import CredentialVault


def _test_private_key() -> str:
    """Generate a throwaway ed25519 key so the SSH client can be constructed."""

    import asyncssh

    return asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh").decode()


KEY_ONE = "AAAAB3NzaC1yc2EAAAADAQABAAABgQC-one"
KEY_TWO = "AAAAB3NzaC1yc2EAAAADAQABAAABgQC-two"


def service(tmp_path: Path) -> ConnectionService:
    db = Database(tmp_path / "gateway.db")
    vault = CredentialVault(
        tmp_path / "vault.json", tmp_path / "unlock.key", kdf_memory_cost=1024, kdf_time_cost=1
    )
    vault.initialize(PASSWORD)
    return ConnectionService(db, vault, tmp_path / "known_hosts")


def test_entries_use_the_openssh_port_syntax(tmp_path: Path) -> None:
    conn = service(tmp_path)
    assert conn.known_hosts_entry("192.0.2.10", 22, "ssh-ed25519", KEY_ONE) == (
        f"192.0.2.10 ssh-ed25519 {KEY_ONE}"
    )
    assert conn.known_hosts_entry("192.0.2.10", 2222, "ssh-ed25519", KEY_ONE) == (
        f"[192.0.2.10]:2222 ssh-ed25519 {KEY_ONE}"
    )
    assert conn.known_hosts_entry("2001:db8::1", 22, "ssh-ed25519", KEY_ONE) == (
        f"[2001:db8::1] ssh-ed25519 {KEY_ONE}"
    )


def test_trusting_a_key_writes_a_0600_known_hosts_file(tmp_path: Path) -> None:
    conn = service(tmp_path)
    path = tmp_path / "known_hosts"
    assert has_trusted_host_key(path, "192.0.2.10", 22) is False
    conn.trust_host_key("192.0.2.10", 22, "ssh-ed25519", KEY_ONE)
    assert has_trusted_host_key(path, "192.0.2.10", 22) is True
    assert path.stat().st_mode & 0o077 == 0
    assert KEY_ONE in path.read_text()


def test_existing_key_is_never_silently_replaced(tmp_path: Path) -> None:
    conn = service(tmp_path)
    conn.trust_host_key("192.0.2.10", 22, "ssh-ed25519", KEY_ONE)
    # Re-trusting the same key is a no-op.
    conn.trust_host_key("192.0.2.10", 22, "ssh-ed25519", KEY_ONE)
    with pytest.raises(ConflictError):
        conn.trust_host_key("192.0.2.10", 22, "ssh-ed25519", KEY_TWO)
    conn.trust_host_key("192.0.2.10", 22, "ssh-ed25519", KEY_TWO, replace=True)
    text = (tmp_path / "known_hosts").read_text()
    assert KEY_TWO in text
    assert KEY_ONE not in text


async def test_unknown_host_blocks_ssh_before_connecting(tmp_path: Path) -> None:
    from gateway.connectors.ssh import SSHClient, SSHCredentials

    credentials = SSHCredentials(
        host="192.0.2.10",
        port=22,
        username="agentgw",
        private_key=_test_private_key(),
        passphrase=None,
        known_hosts_path=tmp_path / "known_hosts",
        connect_timeout=1,
    )
    client = SSHClient(credentials)
    with pytest.raises(Exception) as excinfo:
        # No trusted key is enrolled, so the client must refuse before dialing.
        await client._connect()
    assert getattr(excinfo.value, "code", "") == "ssh_host_key_unknown"


def test_ssh_configuration_rejects_root_and_relative_paths() -> None:
    with pytest.raises(ValidationError):
        SSHConfig(host="192.0.2.10", username="root")
    with pytest.raises(ValidationError):
        SSHConfig(host="192.0.2.10", username="agentgw", allowed_log_paths=["relative/path.log"])
    with pytest.raises(ValidationError):
        SSHConfig(host="192.0.2.10", username="agentgw", allowed_log_paths=["/var/log/../../etc/shadow"])


def test_command_templates_render_only_allowlisted_values() -> None:
    template = CommandTemplate(
        id="restart-nginx",
        argv=["sudo", "-n", "systemctl", "restart", "{unit}"],
        params={"unit": ["nginx"]},
    )
    assert render_template_argv(template, {"unit": "nginx"}) == [
        "sudo",
        "-n",
        "systemctl",
        "restart",
        "nginx",
    ]
    with pytest.raises(PolicyDenied):
        render_template_argv(template, {"unit": "sshd"})
    with pytest.raises(ValidationFailure):
        render_template_argv(template, {"extra": "x"})
    with pytest.raises(ValidationFailure):
        render_template_argv(template, {})


def test_template_parameter_values_cannot_inject_arguments() -> None:
    # Values come from an administrator allowlist and may not contain
    # whitespace, so an agent cannot split one argument into two.  Whatever the
    # value is, argv is rejoined with shlex.join before it reaches the host.
    with pytest.raises(ValidationError):
        CommandTemplate(id="bad id", argv=["uptime"])
    with pytest.raises(ValidationError):
        CommandTemplate(id="bad", argv=["uptime"], params={"flag": ["--force rm -rf /"]})

    template = CommandTemplate(id="echo", argv=["echo", "{value}"], params={"value": ["a'b"]})
    argv = render_template_argv(template, {"value": "a'b"})
    assert argv == ["echo", "a'b"]
    # Whatever the value, rejoining is loss-free: the host shell receives exactly
    # two arguments and cannot interpret the quote as syntax.
    assert shlex.split(shlex.join(argv)) == argv
    assert render_template_argv(CommandTemplate(id="ok", argv=["uptime"]), {}) == ["uptime"]


async def test_bounded_output_reads_chunked_streams_until_eof() -> None:
    from gateway.connectors.ssh import _read_bounded

    class ChunkedStream:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = list(chunks)

        async def read(self, _limit: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    result = await _read_bounded(ChunkedStream([b"ab", b"cd", b"e"]), limit=5)
    assert result.data == b"abcde"
    assert result.truncated is False

    overflow = await _read_bounded(ChunkedStream([b"ab", b"cd", b"ef"]), limit=5)
    assert overflow.data == b"abcde"
    assert overflow.truncated is True
