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
