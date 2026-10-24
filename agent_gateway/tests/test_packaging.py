"""Add-on packaging metadata and image invariants.

These checks cannot replace a real build (no Docker daemon is available in the
development environment), but they catch the mistakes that make a build fail or
silently weaken the add-on: a port that is published by default, a privileged
mode, a missing healthcheck, an AppArmor file with more than one profile, or
option defaults that disagree with the application.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from gateway.settings import Settings

ADDON_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ADDON_DIR.parent

CONFIG = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))
BUILD = yaml.safe_load((ADDON_DIR / "build.yaml").read_text(encoding="utf-8"))
REPOSITORY = yaml.safe_load((REPO_ROOT / "repository.yaml").read_text(encoding="utf-8"))
DOCKERFILE = (ADDON_DIR / "Dockerfile").read_text(encoding="utf-8")
APPARMOR = (ADDON_DIR / "apparmor.txt").read_text(encoding="utf-8")
GITIGNORE = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_required_addon_metadata_is_present() -> None:
    for key in ("name", "version", "slug", "description", "arch", "init", "ingress", "ingress_port"):
        assert key in CONFIG, key
    assert CONFIG["init"] is False  # required for Home Assistant base images
    assert CONFIG["slug"] == "ha_agent_gateway"
    assert CONFIG["ingress"] is True
    assert CONFIG["panel_title"]


def test_architectures_are_declared_consistently() -> None:
    assert set(CONFIG["arch"]) == {"amd64", "aarch64"}
    assert set(BUILD["build_from"]) == set(CONFIG["arch"])
    assert all(image.startswith("ghcr.io/home-assistant/base:") for image in BUILD["build_from"].values())


def test_agent_port_is_not_published_by_default() -> None:
    assert CONFIG["ports"]["8098/tcp"] is None
    assert CONFIG["ingress_port"] == 8099
    assert "8098/tcp" in CONFIG["ports_description"]


def test_only_the_ssl_map_is_requested_and_read_only() -> None:
    assert CONFIG["map"] == [{"type": "ssl", "read_only": True}]


def test_no_privileged_or_host_access_is_requested() -> None:
    for key in ("privileged", "host_network", "host_pid", "host_ipc", "docker_api", "full_access"):
        assert key not in CONFIG, key
    assert "hassio_api" not in CONFIG
    assert "homeassistant_api" not in CONFIG


def test_watchdog_targets_the_health_endpoint() -> None:
    assert CONFIG["watchdog"] == "http://[HOST]:[PORT:8099]/healthz"


def test_option_defaults_match_the_application() -> None:
    options = CONFIG["options"]
    defaults = Settings(data_dir=Path("/tmp/unused"))
    assert options["audit_retention_days"] == defaults.audit_retention_days == 30
    assert options["approval_ttl_seconds"] == defaults.approval_ttl_seconds == 600
    assert options["auto_shutdown_enabled"] is defaults.auto_shutdown_enabled is True
    assert options["auto_shutdown_timeout_minutes"] == defaults.auto_shutdown_timeout_minutes == 30
    assert options["tls_certfile"] == ""
    assert options["tls_keyfile"] == ""
    assert options["log_level"] == "info"
    assert options["admin_user_ids"] == []
    assert options["trusted_proxies"] == []
    assert defaults.trusted_proxies == ()


def test_every_option_has_a_schema_entry() -> None:
    assert set(CONFIG["options"]) == set(CONFIG["schema"])
    assert CONFIG["schema"]["log_level"] == "list(debug|info|warning|error)"
    assert CONFIG["schema"]["audit_retention_days"] == "int(1,3650)"
    assert CONFIG["schema"]["approval_ttl_seconds"] == "int(30,3600)"


def test_image_runs_unprivileged_and_drops_privileges() -> None:
    # The container starts as root only to fix /data ownership, then run.sh
    # re-execs the application as the app user (documented limitation #8).
    assert "adduser" in DOCKERFILE and "-u 1000" in DOCKERFILE
    assert "USER root" not in DOCKERFILE
    run_script = (ADDON_DIR / "run.sh").read_text(encoding="utf-8")
    assert 'APP_USER="${HAG_APP_USER:-app:app}"' in run_script
    assert 'exec su-exec "$APP_USER"' in run_script
    assert "EXPOSE 8099 8098" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE and "/healthz" in DOCKERFILE
    assert "BUILD_FROM" in DOCKERFILE
    assert "ENTRYPOINT" not in DOCKERFILE  # /run.sh is the documented entrypoint
    assert "--privileged" not in DOCKERFILE


def test_image_has_no_baked_secrets_or_published_credentials() -> None:
    lowered = DOCKERFILE.lower()
    for marker in ("password=", "token=", "secret=", "private_key", "authorized_keys"):
        assert marker not in lowered, marker
    assert "COPY . ." not in DOCKERFILE  # only explicit paths are copied


def test_run_script_drops_root_before_starting_the_app() -> None:
    script = (ADDON_DIR / "run.sh").read_text(encoding="utf-8")
    assert 'APP_USER="${HAG_APP_USER:-app:app}"' in script
    assert 'exec su-exec "$APP_USER"' in script
    assert "exec " in script
    assert "set -euo pipefail" in script


def test_apparmor_defines_exactly_one_profile() -> None:
    profiles = re.findall(r"(?m)^profile\s+\S+", APPARMOR)
    assert len(profiles) == 1, profiles
    assert "ha_agent_gateway" in profiles[0]
    assert "#include <tunables/global>" in APPARMOR


def test_apparmor_permits_s6_init_entrypoint() -> None:
    """Regression: `/bin/sh: can't open '/init'|'.../preinit': Permission denied`.

    The HA base image's ENTRYPOINT is the S6-Overlay v3 `/init` (PID 1, hence
    `init: false` in config.yaml so Supervisor does not add Docker's init).
    A custom apparmor.txt replaces the default profile, so it must grant both
    **read** and execute access to every S6 script: the kernel runs
    `/bin/sh <script>` for a shebang, and the shell must open the file.
    Execute-only (`ix`) rules reproduce the 0.2.x/0.3.0 failure, and a missing
    read rule on `/package/**` reproduced it one stage later at `preinit`
    (`s6-overlay-suexec: fatal: child failed with exit code 2`).

    Detailed rule coverage (scripts, binaries, shared objects) is asserted in
    tests/test_startup.py.
    """

    for rule in (
        "/init rix,",
        "/package/** rix,",
        "/command/** rix,",
        "/etc/services.d/** rix,",
        "/etc/cont-init.d/** rix,",
        "/etc/cont-finish.d/** rix,",
    ):
        assert rule in APPARMOR, rule
    # No S6 script may be execute-only.
    for line in APPARMOR.splitlines():
        stripped = line.strip()
        if not stripped.startswith("/") or not stripped.endswith(","):
            continue
        path, _, perms = stripped[:-1].partition(" ")
        if path in {"/init", "/package/**", "/command/**"}:
            assert "r" in perms, stripped


def test_container_startup_chain_is_init_then_run_sh() -> None:
    # Base image owns ENTRYPOINT /init; the Dockerfile must not override it,
    # and CMD must be the executable run.sh (chmod'd in the image, since the
    # git checkout stores it 0644).
    assert "ENTRYPOINT" not in DOCKERFILE
    assert 'CMD ["/run.sh"]' in DOCKERFILE
    assert "chmod 0755 /run.sh" in DOCKERFILE
    run_script = (ADDON_DIR / "run.sh").read_text(encoding="utf-8")
    assert run_script.startswith("#!/usr/bin/env bash\n")
    assert "\r" not in run_script  # CRLF would break the shebang on Alpine


def test_repository_metadata_is_valid() -> None:
    assert REPOSITORY["name"]
    assert REPOSITORY["url"].startswith("https://github.com/")
    assert REPOSITORY["maintainer"]


def test_runtime_requirements_mirror_pyproject_pins() -> None:
    requirements = (ADDON_DIR / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (ADDON_DIR / "pyproject.toml").read_text(encoding="utf-8")
    pins = [line.strip() for line in requirements.splitlines() if "==" in line]
    assert len(pins) == 8
    for pin in pins:
        assert pin in pyproject, pin


@pytest.mark.parametrize(
    "pattern",
    ["*.venv*", "*vault.json*", "*unlock.key*", "*.db*", "__pycache__*"],
)
def test_gitignore_excludes_local_secrets_and_state(pattern: str) -> None:
    assert pattern.strip("*") in GITIGNORE or pattern in GITIGNORE


def test_ui_assets_are_packaged() -> None:
    pyproject = (ADDON_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert 'gateway = ["ui/static/*", "ui/agent/*", "py.typed"]' in pyproject
    for name in ("index.html", "app.js", "styles.css"):
        assert (ADDON_DIR / "src" / "gateway" / "ui" / "static" / name).is_file(), name


def test_changelogs_and_versions_are_synchronized() -> None:
    from gateway import __version__

    root_changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    addon_changelog = (ADDON_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    assert root_changelog == addon_changelog
    assert CONFIG["version"] == __version__
    assert f'version = "{__version__}"' in (ADDON_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert f"ARG BUILD_VERSION={__version__}" in DOCKERFILE
    assert f"## [{__version__}]" in root_changelog
