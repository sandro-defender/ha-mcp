"""Add-on option parsing must fail closed on bad configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.errors import ConfigError
from gateway.settings import load_settings


def options_file(tmp_path: Path, options: dict) -> Path:
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options), encoding="utf-8")
    return path


def test_defaults_require_ingress_and_supervisor_peer(tmp_path: Path) -> None:
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.admin_allowed_ips == frozenset({"172.30.32.2"})
    assert settings.require_ingress_user is True
    assert settings.admin_user_ids == frozenset()
    assert settings.tls_enabled is False
    assert settings.approval_ttl_seconds == 600


def test_rejects_unknown_log_level(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(options_file(tmp_path, {"log_level": "verbose"}), data_dir=tmp_path, env={})


def test_rejects_out_of_range_retention_and_ttl(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(options_file(tmp_path, {"audit_retention_days": 0}), data_dir=tmp_path, env={})
    with pytest.raises(ConfigError):
        load_settings(options_file(tmp_path, {"approval_ttl_seconds": 5}), data_dir=tmp_path, env={})


def test_rejects_partial_tls_configuration(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_text("not a real certificate")
    with pytest.raises(ConfigError):
        load_settings(options_file(tmp_path, {"tls_certfile": str(cert)}), data_dir=tmp_path, env={})
    missing = tmp_path / "missing.pem"
    with pytest.raises(ConfigError):
        load_settings(
            options_file(tmp_path, {"tls_certfile": str(missing), "tls_keyfile": str(missing)}),
            data_dir=tmp_path,
            env={},
        )


def test_rejects_equal_ports_and_bad_ip_lists(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(data_dir=tmp_path, env={"HAG_INGRESS_PORT": "8099", "HAG_AGENT_PORT": "8099"})
    with pytest.raises(ConfigError):
        load_settings(data_dir=tmp_path, env={"HAG_ADMIN_ALLOWED_IPS": "0.0.0.0/0"})


def test_admin_user_allowlist_and_origin_lists(tmp_path: Path) -> None:
    settings = load_settings(
        options_file(tmp_path, {"admin_user_ids": ["abc123", " def456 "]}),
        data_dir=tmp_path,
        env={
            "HAG_AGENT_ALLOWED_ORIGINS": "https://agents.example,https://other.example/",
            "HAG_AGENT_ALLOWED_HOSTS": "gateway.example",
        },
    )
    assert settings.admin_user_ids == frozenset({"abc123", "def456"})
    assert settings.agent_allowed_origins == frozenset({"https://agents.example", "https://other.example"})
    assert settings.agent_allowed_hosts == frozenset({"gateway.example"})


def test_invalid_options_file_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "options.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path, data_dir=tmp_path, env={})
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path, data_dir=tmp_path, env={})
