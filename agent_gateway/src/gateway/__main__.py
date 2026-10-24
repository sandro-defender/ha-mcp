"""Process entry point: run the management and agent listeners."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from typing import Any

import uvicorn

from .api.apps import build_admin_app, build_agent_app
from .errors import ConfigError, GatewayError
from .logging_utils import configure_logging
from .runtime import Runtime
from .settings import Settings, load_settings

logger = logging.getLogger(__name__)


class _Server(uvicorn.Server):
    """Run multiple servers in one loop; signal handling belongs to the parent."""

    def install_signal_handlers(self) -> None:
        return


def _admin_config(app: Any, settings: Settings) -> uvicorn.Config:
    # Ingress traffic must remain plain HTTP inside the container.  The
    # Supervisor terminates the browser-facing TLS session.
    return uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.ingress_port,
        log_config=None,
        access_log=False,
        server_header=False,
        date_header=False,
        proxy_headers=False,
        timeout_keep_alive=10,
        # Bound the drain: S6 gives us S6_KILL_GRACETIME (10 s) before it
        # sends KILL, and the Supervisor waits 10 s before SIGKILL.  Without a
        # bound, a stuck client keeps the process alive until it is killed.
        timeout_graceful_shutdown=5,
    )


def _agent_config(app: Any, settings: Settings) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.agent_port,
        log_config=None,
        access_log=False,
        server_header=False,
        date_header=False,
        proxy_headers=False,
        timeout_keep_alive=10,
        # Bound the drain: S6 gives us S6_KILL_GRACETIME (10 s) before it
        # sends KILL, and the Supervisor waits 10 s before SIGKILL.  Without a
        # bound, a stuck client keeps the process alive until it is killed.
        timeout_graceful_shutdown=5,
        ssl_certfile=str(settings.tls_certfile) if settings.tls_certfile else None,
        ssl_keyfile=str(settings.tls_keyfile) if settings.tls_keyfile else None,
    )


async def run(runtime: Runtime, admin_app: Any, agent_app: Any) -> None:
    settings = runtime.settings
    admin_server = _Server(_admin_config(admin_app, settings))
    agent_server = _Server(_agent_config(agent_app, settings))
    servers = [admin_server, agent_server]
    tasks = [asyncio.create_task(server.serve(), name=f"server-{server.config.port}") for server in servers]
    maintenance = asyncio.create_task(runtime.maintenance_loop(), name="maintenance")

    stop = asyncio.Event()
    listener_failed = False

    def request_stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, request_stop)

    waiters = [asyncio.create_task(stop.wait(), name="stop-event"), *tasks]
    try:
        done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            logger.info("shutdown requested")
        else:
            for task in done:
                if task.get_name() != "stop-event":
                    listener_failed = True
                    logger.error("a listener exited unexpectedly")
    finally:
        for server in servers:
            server.should_exit = True
        maintenance.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance
        await asyncio.gather(*tasks, return_exceptions=True)
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
    if listener_failed:
        raise RuntimeError("listener exited unexpectedly")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"configuration error: {exc.message}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    runtime = Runtime(settings)
    try:
        runtime.startup()
    except (GatewayError, ConfigError) as exc:
        logger.error("startup failed: %s", exc.message)
        return 1
    admin_app = build_admin_app(runtime)
    agent_app = build_agent_app(runtime)
    exit_code = 0
    try:
        asyncio.run(run(runtime, admin_app, agent_app))
    except KeyboardInterrupt:  # pragma: no cover
        pass
    except SystemExit as exc:
        # uvicorn uses SystemExit(3) for a listener startup failure.  Convert
        # it to a clean process result instead of an internal traceback.
        exit_code = exc.code if isinstance(exc.code, int) else 1
        logger.error("listener startup/runtime failed (exit code=%s)", exit_code)
    except Exception as exc:
        exit_code = 1
        logger.error("listener startup/runtime failed: %s", exc)
    finally:
        with contextlib.suppress(Exception):
            runtime.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
