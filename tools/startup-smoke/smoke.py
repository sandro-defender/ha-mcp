#!/usr/bin/env python3
"""Local startup smoke test for the add-on process (no Docker required).

What this exercises for real
----------------------------
* ``run.sh`` exactly as the container runs it (same script, same environment
  handling), including the interpreter lookup and the non-root exec path.
* The gateway process, both listeners, ``/healthz`` and ``/readyz``.
* The management Ingress gate (requests without Home Assistant's remote-user
  header are rejected).
* Graceful shutdown on SIGTERM and a restart against the same ``/data``.

What this does NOT exercise
---------------------------
S6-Overlay itself, Docker, AppArmor, the root -> ``app`` privilege drop, and a
live Home Assistant Supervisor.  Those are covered by
``tests/test_startup.py`` (static contract), the Docker-based CI job, and the
manual Home Assistant OS checklist in ``docs/STARTUP_TROUBLESHOOTING.md``.
This harness is deliberately explicit about that boundary: a pass here is not
proof that the add-on boots on Home Assistant OS.

Usage
-----
    python3 tools/startup-smoke/smoke.py \
        --python /path/to/venv/bin/python \
        --pythonpath /path/to/ha-mcp/agent_gateway/src

The process is started with a temporary data directory; nothing is written to
``/data``.  Exit status 0 means every check passed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADDON_DIR = REPO_ROOT / "agent_gateway"
RUN_SH = ADDON_DIR / "run.sh"

OK = "PASS"
FAIL = "FAIL"


class SmokeError(RuntimeError):
    pass


def check(condition: bool, label: str) -> None:
    print(f"  [{OK if condition else FAIL}] {label}")
    if not condition:
        raise SmokeError(label)


def http_get(port: int, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    return http_request("GET", port, path, headers=headers)


def http_request(
    method: str,
    port: int,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        return response.status, body, {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()


def wait_for_http(port: int, path: str, *, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            status, _body, _headers = http_get(port, path)
            if status == 200:
                return
            last_error = f"status {status}"
        except (OSError, http.client.HTTPException) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise SmokeError(f"{path} on port {port} did not become ready ({last_error})")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def start_gateway(
    *, python: str, pythonpath: str, data_dir: Path, ingress_port: int, agent_port: int, log_path: Path
) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "HAG_DATA_DIR": str(data_dir),
        "HAG_PYTHON": python,
        "HAG_PYTHONPATH": pythonpath,
        "HAG_INGRESS_PORT": str(ingress_port),
        "HAG_AGENT_PORT": str(agent_port),
        # Ingress is simulated: the loopback peer is trusted like the
        # Supervisor proxy (172.30.32.2) and the remote-user header is still
        # required.  Production keeps both defaults.
        "HAG_ADMIN_ALLOWED_IPS": "127.0.0.1",
        "HAG_REQUIRE_INGRESS_USER": "1",
        "PYTHONUNBUFFERED": "1",
    }
    log = log_path.open("ab")
    process = subprocess.Popen(  # noqa: S603 - fixed script path, no shell
        ["bash", str(RUN_SH)],
        cwd=str(ADDON_DIR),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    process._smoke_log = log  # type: ignore[attr-defined]
    return process


def stop_gateway(process: subprocess.Popen[bytes], *, timeout: float = 25.0) -> int:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise SmokeError("the gateway did not exit after SIGTERM") from None
    log = getattr(process, "_smoke_log", None)
    if log is not None:
        log.close()
    return exit_code


def wait_for_exit(process: subprocess.Popen[bytes], *, timeout: float = 25.0) -> int:
    return stop_gateway(process, timeout=timeout)


def run_cycle(
    *,
    label: str,
    python: str,
    pythonpath: str,
    data_dir: Path,
    ingress_port: int,
    agent_port: int,
    log_dir: Path,
    expect_clean_shutdown: bool,
) -> None:
    print(f"\n== {label}")
    log_path = log_dir / f"gateway-{label.lower().replace(' ', '-')}.log"
    process = start_gateway(
        python=python,
        pythonpath=pythonpath,
        data_dir=data_dir,
        ingress_port=ingress_port,
        agent_port=agent_port,
        log_path=log_path,
    )
    try:
        wait_for_http(ingress_port, "/healthz")
        check(True, f"management listener answers /healthz on :{ingress_port}")

        wait_for_http(agent_port, "/healthz")
        check(True, f"agent listener answers /healthz on :{agent_port}")

        status, body, _ = http_get(ingress_port, "/readyz")
        payload = json.loads(body or b"{}")
        check(status == 200 and payload.get("ready") is True, "readiness reports ready")
        check(
            payload.get("checks", {}).get("database", {}).get("ok") is True,
            "SQLite schema is usable",
        )

        status, body, _ = http_get(ingress_port, "/")
        check(status == 401, f"management panel rejects requests without an Ingress user (got {status}: {body[:120]!r})")

        ingress_headers = {"X-Remote-User-Id": "smoke-admin", "X-Gateway-Request": "1"}
        status, body, headers = http_get(ingress_port, "/", headers=ingress_headers)
        check(status == 200 and b"<html" in body.lower(), "management panel is served through Ingress")
        check("content-security-policy" in headers, "management panel sets a CSP")
        check(headers.get("cache-control") == "no-store", "management panel is not cached")

        status, body, _ = http_get(ingress_port, "/api/admin/status")
        check(status == 401, f"management API rejects requests without an Ingress user (got {status}: {body[:120]!r})")
        status, body, _ = http_request(
            "POST",
            ingress_port,
            "/api/admin/vault/lock",
            headers={"X-Remote-User-Id": "smoke-admin", "Content-Type": "application/json"},
            body=b"{}",
        )
        check(status == 403, f"unsafe requests still require the CSRF header (got {status}: {body[:120]!r})")
        status, body, _ = http_get(ingress_port, "/api/admin/status", headers=ingress_headers)
        check(status == 200 and json.loads(body)["version"], "management API answers for an Ingress user")

        status, _body, _ = http_get(agent_port, "/api/v1/health")
        check(status == 401, "agent API rejects requests without a bearer token")

        status, _body, _ = http_get(agent_port, "/mcp")
        check(status in {401, 400, 405, 406}, "MCP endpoint rejects unauthenticated requests")

        # A live request proves the event loop is not wedged.
        status, body, _ = http_get(ingress_port, "/healthz")
        check(status == 200 and json.loads(body)["status"] == "ok", "liveness stays healthy")
    finally:
        exit_code = wait_for_exit(process)

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    check("gateway started version" in log_text, "startup banner logged")
    check("shutdown requested" in log_text, "SIGTERM handled")
    check("gateway stopped" in log_text, "shutdown completed (vault locked, database closed)")
    check("Traceback" not in log_text, "no Python traceback during lifecycle")
    if expect_clean_shutdown:
        check(exit_code == 0, f"clean exit status (got {exit_code})")
    else:  # pragma: no cover - reserved for failure-path tests
        check(exit_code != 0, "expected a non-zero exit status")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", default=sys.executable, help="interpreter that runs `-m gateway`")
    parser.add_argument(
        "--pythonpath",
        default=str(ADDON_DIR / "src"),
        help="PYTHONPATH for the gateway package",
    )
    parser.add_argument("--ingress-port", type=int, default=18099)
    parser.add_argument("--agent-port", type=int, default=18098)
    parser.add_argument("--cycles", type=int, default=2, help="start/stop cycles against the same data dir")
    parser.add_argument("--keep", action="store_true", help="keep the temporary data directory")
    args = parser.parse_args()

    if not RUN_SH.is_file():
        print(f"run.sh not found at {RUN_SH}", file=sys.stderr)
        return 2
    for port in (args.ingress_port, args.agent_port):
        if not port_free(port):
            print(f"port {port} is already in use", file=sys.stderr)
            return 2

    workdir = Path(tempfile.mkdtemp(prefix="hag-startup-smoke-"))
    data_dir = workdir / "data"
    data_dir.mkdir(mode=0o700)
    log_dir = workdir / "logs"
    log_dir.mkdir()
    (data_dir / "options.json").write_text(json.dumps({"log_level": "info"}), encoding="utf-8")

    print("Home Assistant Agent Gateway — local startup smoke test")
    print(f"  add-on dir : {ADDON_DIR}")
    print(f"  data dir   : {data_dir}")
    print(f"  interpreter: {args.python}")
    print("  NOT covered: S6-Overlay, Docker, AppArmor, Supervisor, HA OS boot")

    try:
        for cycle in range(1, max(1, args.cycles) + 1):
            run_cycle(
                label=f"cycle {cycle}",
                python=args.python,
                pythonpath=args.pythonpath,
                data_dir=data_dir,
                ingress_port=args.ingress_port,
                agent_port=args.agent_port,
                log_dir=log_dir,
                expect_clean_shutdown=True,
            )
    except SmokeError as exc:
        print(f"\nRESULT: FAIL — {exc}")
        print(f"logs kept in {log_dir}")
        return 1

    if args.keep:
        print(f"\nlogs and data kept in {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    print("\nRESULT: PASS (process-level startup, health, shutdown, restart)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
