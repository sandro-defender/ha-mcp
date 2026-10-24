"""Process lifecycle tests for the two-listener entry point."""

from __future__ import annotations

import pytest

import gateway.__main__ as main_module
from gateway.runtime import Runtime


async def test_unexpected_listener_exit_is_a_failure(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listener that exits without a stop request must not look like success."""

    class FakeServer:
        def __init__(self, config) -> None:
            self.config = config
            self.should_exit = False

        async def serve(self) -> None:
            return None

    monkeypatch.setattr(main_module, "_Server", FakeServer)
    with pytest.raises(RuntimeError, match="listener exited unexpectedly"):
        await main_module.run(runtime, object(), object())


def test_preflight_rejects_an_occupied_ingress_port(settings, caplog) -> None:
    """A taken listener port fails fast with exit code 3 and one error line."""

    import socket

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", settings.ingress_port))
    blocker.listen(1)
    try:
        conflict = settings.with_overrides(ingress_port=settings.ingress_port)
        with pytest.raises(SystemExit) as excinfo:
            main_module._preflight_ports(conflict)
        assert excinfo.value.code == 3
    finally:
        blocker.close()
    assert any("already in use" in record.message for record in caplog.records), (
        "port conflict must be reported with an actionable error line"
    )


def test_preflight_passes_when_ports_are_free(settings) -> None:
    main_module._preflight_ports(settings)
