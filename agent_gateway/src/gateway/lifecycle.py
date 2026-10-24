"""Automatic add-on shutdown through the Home Assistant Supervisor.

Verified mechanism (Home Assistant developer documentation: "App
communication" and the Supervisor API endpoints reference):

* The Supervisor is reachable from inside the add-on at ``http://supervisor``
  (overridable with ``HAG_SUPERVISOR_URL`` or the ``SUPERVISOR`` environment
  variable for local test rigs).
* Every add-on receives its own token in ``SUPERVISOR_TOKEN``; it is sent as
  ``Authorization: Bearer <token>``.
* ``POST /addons/self/stop`` stops the **calling** add-on.  Self-service
  ``/addons/self/*`` endpoints are explicitly available to every add-on
  **without** ``hassio_api`` or a role, so this add-on requests no additional
  Supervisor permissions.

The token is read from the environment for the duration of one request and is
never stored, returned by an API, written to the audit log, or included in a
log message.

Inactivity policy (documented behaviour):

* **Counted** as activity: requests that pass the Ingress admin gate
  (authenticated Home Assistant users) and requests that present a valid,
  non-expired, non-revoked agent bearer token or temporary session.
* **Not counted**: ``/healthz`` and ``/readyz`` probes (including the
  Supervisor watchdog and the image health check), unauthenticated requests,
  failed or rate-limited authentication, IP-rejected traffic, static handoff
  page loads, and background maintenance.  Expired or revoked agent
  credentials therefore cannot keep the add-on alive.
* The timer starts at process start (and resets on every counted request) and
  is evaluated by a single periodic task; shutdown runs under one lock so
  duplicate timers cannot double-fire.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

from .agent_access import AgentAccessService
from .audit.service import AuditService
from .errors import ConflictError, GatewayError
from .settings import Settings
from .storage.db import SettingsStore
from .utils import iso, utcnow

logger = logging.getLogger(__name__)

MIN_TIMEOUT_MINUTES: Final = 5
MAX_TIMEOUT_MINUTES: Final = 1440
DEFAULT_TIMEOUT_MINUTES: Final = 30
TICK_INTERVAL_SECONDS: Final = 10.0
DRAIN_DEADLINE_SECONDS: Final = 5.0
STOP_REQUEST_TIMEOUT_SECONDS: Final = 5.0
#: How long a stop request may go unanswered (accepted or not) before the
#: add-on concludes it is still running and returns to the running state.
UNCONFIRMED_REVERT_SECONDS: Final = 30.0
RETRY_BASE_SECONDS: Final = 60.0
RETRY_MAX_SECONDS: Final = 600.0
DEFAULT_SUPERVISOR_URL: Final = "http://supervisor"

SHUTDOWN_REASONS: Final = frozenset({"inactivity_timeout", "manual"})

StopStatus = Literal["accepted", "unconfirmed", "rejected", "unavailable"]


@dataclass(frozen=True)
class StopOutcome:
    """Result of one Supervisor stop attempt.  Never carries the token."""

    status: StopStatus
    message: str
    http_status: int | None = None


StopCaller = Callable[[str, str], Awaitable[StopOutcome]]


async def supervisor_stop(url: str, token: str) -> StopOutcome:
    """Call ``POST {url}/addons/self/stop`` with the Supervisor token.

    Outcome classification:

    * ``accepted`` — the Supervisor answered 2xx with ``result: ok``.
    * ``unconfirmed`` — the request was (probably) sent but no complete
      response arrived (read timeout, dropped connection).  The Supervisor
      may be stopping this container right now; the caller must not treat
      this as either success or rejection.
    * ``rejected`` — the Supervisor answered with an explicit refusal.
    * ``unavailable`` — the request never reached the Supervisor.
    """

    stop_url = url.rstrip("/") + "/addons/self/stop"
    try:
        async with httpx.AsyncClient(timeout=STOP_REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.post(
                stop_url,
                headers={"Authorization": f"Bearer {token}"},
                json={},
            )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return StopOutcome("unavailable", "the Supervisor API is unreachable")
    except (httpx.ReadTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError):
        # The connection was established; the Supervisor may hold the reply
        # until this container stops, so the request cannot be classified.
        return StopOutcome("unconfirmed", "the Supervisor did not return a response in time")
    except httpx.TimeoutException:
        return StopOutcome("unconfirmed", "the Supervisor did not respond in time")

    if response.status_code in {401, 403}:
        return StopOutcome(
            "rejected",
            "the Supervisor denied the stop request",
            http_status=response.status_code,
        )
    if response.status_code >= 400:
        return StopOutcome(
            "rejected",
            f"the Supervisor refused the stop request (HTTP {response.status_code})",
            http_status=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("result") not in (None, "ok"):
        return StopOutcome(
            "rejected",
            "the Supervisor refused the stop request",
            http_status=response.status_code,
        )
    return StopOutcome(
        "accepted",
        "the Supervisor accepted the stop request",
        http_status=response.status_code,
    )


class LifecycleService:
    """Inactivity tracking, safe shutdown sequencing, and Supervisor stop."""

    def __init__(
        self,
        *,
        settings: Settings,
        settings_store: SettingsStore,
        audit: AuditService,
        agent_access: AgentAccessService,
        stop_caller: StopCaller | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.settings_store = settings_store
        self.audit = audit
        self.agent_access = agent_access
        self._stop_caller: StopCaller = stop_caller or supervisor_stop
        self._monotonic = monotonic
        self.state: Literal["running", "shutting_down"] = "running"
        now = monotonic()
        self._last_activity = now
        self._last_activity_at = iso()
        self._last_activity_kind = "startup"
        self._inflight = 0
        self._lock = asyncio.Lock()
        self._failures = 0
        self._next_attempt_at = 0.0
        self._revert_task: asyncio.Task[None] | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._warned_missing_supervisor = False

    # ------------------------------------------------------------- config

    @property
    def enabled(self) -> bool:
        return self.settings_store.get_bool("auto_shutdown_enabled")

    @property
    def timeout_minutes(self) -> int:
        raw = self.settings_store.get_int("auto_shutdown_timeout_minutes")
        if raw < MIN_TIMEOUT_MINUTES or raw > MAX_TIMEOUT_MINUTES:
            return DEFAULT_TIMEOUT_MINUTES
        return raw

    @property
    def timeout_seconds(self) -> float:
        return float(self.timeout_minutes * 60)

    @property
    def supervisor_url(self) -> str:
        env = os.environ
        return (
            env.get("HAG_SUPERVISOR_URL") or env.get("SUPERVISOR") or DEFAULT_SUPERVISOR_URL
        ).strip() or DEFAULT_SUPERVISOR_URL

    @property
    def supervisor_token(self) -> str | None:
        token = (os.environ.get("SUPERVISOR_TOKEN") or "").strip()
        return token or None

    @property
    def supervisor_available(self) -> bool:
        return self.supervisor_token is not None

    @property
    def blocks_agents(self) -> bool:
        return self.state != "running"

    # ----------------------------------------------------------- activity

    def record_activity(self, kind: str) -> None:
        """Count one legitimate authenticated request (see module docstring)."""

        if self.state != "running":
            return
        self._last_activity = self._monotonic()
        self._last_activity_at = iso()
        self._last_activity_kind = kind
        # New legitimate work restarts the failed-shutdown backoff window.
        # The failure counter resets only after a stop attempt succeeds.
        self._next_attempt_at = 0.0

    def begin_agent_request(self) -> None:
        self._inflight += 1

    def end_agent_request(self) -> None:
        self._inflight = max(0, self._inflight - 1)

    @property
    def inactivity_remaining_seconds(self) -> int | None:
        if not self.enabled or self.state != "running":
            return None
        remaining = self.timeout_seconds - (self._monotonic() - self._last_activity)
        return max(0, int(remaining))

    # ------------------------------------------------------------ status

    def status_payload(self) -> dict[str, Any]:
        last_shutdown = None
        reason = self.settings_store.get("lifecycle_last_shutdown_reason")
        at = self.settings_store.get("lifecycle_last_shutdown_at")
        result = self.settings_store.get("lifecycle_last_shutdown_result")
        if at:
            last_shutdown = {"reason": reason, "at": at, "result": result}
        return {
            "state": self.state,
            "enabled": self.enabled,
            "timeout_minutes": self.timeout_minutes,
            "timeout_range_minutes": [MIN_TIMEOUT_MINUTES, MAX_TIMEOUT_MINUTES],
            "supervisor_available": self.supervisor_available,
            "inactivity_remaining_seconds": self.inactivity_remaining_seconds,
            "last_activity_at": self._last_activity_at,
            "last_activity_kind": self._last_activity_kind,
            "inflight_agent_requests": self._inflight,
            "last_shutdown": last_shutdown,
            "failed_attempts": self._failures,
        }

    def update_settings(
        self, *, enabled: bool | None = None, timeout_minutes: int | None = None
    ) -> dict[str, Any]:
        if enabled is not None:
            self.settings_store.set("auto_shutdown_enabled", bool(enabled))
        if timeout_minutes is not None:
            if not MIN_TIMEOUT_MINUTES <= timeout_minutes <= MAX_TIMEOUT_MINUTES:
                raise GatewayError(
                    "validation_error",
                    f"inactivity timeout must be between {MIN_TIMEOUT_MINUTES} and "
                    f"{MAX_TIMEOUT_MINUTES} minutes",
                    422,
                )
            self.settings_store.set("auto_shutdown_timeout_minutes", int(timeout_minutes))
        # Panel edits win over add-on option defaults from now on.
        self.settings_store.set("auto_shutdown_customized", True)
        return self.status_payload()

    # --------------------------------------------------------- shutdown

    async def tick(self) -> None:
        """Evaluate inactivity and start the shutdown sequence if due."""

        if self.state != "running" or not self.enabled:
            return
        if self._inflight > 0:
            return
        if self._monotonic() < self._next_attempt_at:
            return
        if not self.supervisor_available:
            if not self._warned_missing_supervisor:
                self._warned_missing_supervisor = True
                logger.warning(
                    "automatic shutdown is enabled but SUPERVISOR_TOKEN is not "
                    "available; automatic shutdown is skipped until the gateway "
                    "runs under the Home Assistant Supervisor"
                )
            return
        idle_for = self._monotonic() - self._last_activity
        if idle_for >= self.timeout_seconds:
            await self.shutdown("inactivity_timeout", actor_type="system")

    async def shutdown(
        self, reason: str, *, actor_type: str = "system", actor_id: str = "system"
    ) -> dict[str, Any]:
        """Run the safe shutdown sequence and call Supervisor self-stop.

        Order (documented): reject new agent work → drain in-flight agent
        requests (bounded) → persist state → apply the temporary-session
        revocation policy → sanitized audit event → Supervisor stop → honest
        outcome handling.

        Raises :class:`ConflictError` when a shutdown is already running.
        Never claims the add-on stopped; reports what the Supervisor answered.
        """

        if reason not in SHUTDOWN_REASONS:
            reason = "manual" if actor_type == "admin" else "inactivity_timeout"
        if self.state != "running":
            raise ConflictError("the add-on is already shutting down")
        async with self._lock:
            if self.state != "running":
                raise ConflictError("the add-on is already shutting down")

            # 1) New agent sessions and operations are rejected from here on.
            self.state = "shutting_down"

            # 2) Give in-flight agent requests a bounded window to finish;
            # the listener drain (uvicorn 5 s + S6 10 s budget) covers the rest.
            deadline = self._monotonic() + DRAIN_DEADLINE_SECONDS
            while self._inflight > 0 and self._monotonic() < deadline:  # noqa: ASYNC110
                # Deadline-bounded drain polling: the loop exits when the
                # in-flight counter reaches zero OR the deadline passes.
                await asyncio.sleep(0.05)
            if self._inflight > 0:
                logger.warning(
                    "proceeding with shutdown while %d agent request(s) are still "
                    "in flight; the listener drain will finish them",
                    self._inflight,
                )

            # 3) Persist required state before talking to the Supervisor.
            now = iso(utcnow())
            self.settings_store.set("lifecycle_last_shutdown_reason", reason)
            self.settings_store.set("lifecycle_last_shutdown_at", now)
            self.settings_store.set("lifecycle_last_shutdown_result", "requested")
            self.settings_store.set("lifecycle_last_activity_at", self._last_activity_at)

            # 4) Apply the documented temporary-session revocation policy.
            revoked = 0
            if self.agent_access.revoke_on_restart():
                result = self.agent_access.revoke_all(
                    actor_id=actor_id,
                    reason=f"add-on shutdown: {reason}",
                )
                revoked = int(result.get("revoked", 0))

            # 5) Sanitized audit event (fixed reason strings, no secrets).
            audit_params = {"reason": reason, "trigger": actor_type, "revoked_sessions": revoked}
            if actor_type == "admin":
                self.audit.admin_action(
                    admin_id=actor_id,
                    action="lifecycle.shutdown",
                    status="requested",
                    params=audit_params,
                )
            else:
                self.audit.record(
                    actor_type="system",
                    actor_id=actor_id,
                    tool="lifecycle.shutdown",
                    decision="allow",
                    status="requested",
                    reason=reason,
                    params=audit_params,
                )

            # 6) Supervisor self-stop.
            token = self.supervisor_token
            if token is None:
                outcome = StopOutcome(
                    "unavailable",
                    "the gateway is not running under the Home Assistant Supervisor "
                    "(SUPERVISOR_TOKEN is missing)",
                )
            else:
                outcome = await self._stop_caller(self.supervisor_url, token)

            # 7) Honest outcome handling.
            if outcome.status in {"accepted", "unconfirmed"}:
                result_value = "accepted" if outcome.status == "accepted" else "stop_requested_unconfirmed"
                self._failures = 0
                self._next_attempt_at = 0.0
                self.settings_store.set("lifecycle_last_shutdown_result", result_value)
                log = logger.info if outcome.status == "accepted" else logger.warning
                log("shutdown %s: %s (%s)", reason, outcome.message, result_value)
                self.audit.record(
                    actor_type=actor_type,
                    actor_id=actor_id,
                    tool="lifecycle.shutdown_result",
                    decision="allow",
                    status=result_value,
                    reason=outcome.message,
                )
                self._arm_revert_watchdog(result_value)
            else:
                self._record_failed_shutdown(outcome)
            return {
                "result": outcome.status,
                "message": outcome.message,
                "state": self.state,
                "revoked_sessions": revoked,
            }

    def _record_failed_shutdown(self, outcome: StopOutcome) -> None:
        """Revert to running after a rejected/unavailable Supervisor call."""

        self.state = "running"
        self.settings_store.set("lifecycle_last_shutdown_result", outcome.status)
        self._failures += 1
        backoff = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** min(self._failures - 1, 3)))
        self._next_attempt_at = self._monotonic() + backoff
        logger.error("shutdown request failed: %s; the add-on is still running", outcome.message)
        self.audit.record(
            actor_type="system",
            actor_id="system",
            tool="lifecycle.shutdown_result",
            decision="deny",
            status="failed",
            reason=outcome.message,
            params={"outcome": outcome.status, "retry_in_seconds": int(backoff)},
        )

    def _arm_revert_watchdog(self, result_value: str) -> None:
        """Return to ``running`` if the Supervisor never stops this container.

        A stop that was accepted (or went unanswered) but left the process
        alive must not leave agents blocked forever; the revert reports the
        failure honestly instead of pretending the add-on stopped.
        """

        if self._revert_task is not None and not self._revert_task.done():
            self._revert_task.cancel()

        async def _watch() -> None:
            await asyncio.sleep(UNCONFIRMED_REVERT_SECONDS)
            if self.state != "shutting_down":
                return
            self.state = "running"
            self.settings_store.set("lifecycle_last_shutdown_result", f"not_confirmed_after_{result_value}")
            self._failures += 1
            self._next_attempt_at = self._monotonic() + min(
                RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** min(self._failures, 3)
            )
            logger.error(
                "the Supervisor did not stop the add-on after a %s stop request; "
                "returning to the running state",
                result_value,
            )
            self.audit.record(
                actor_type="system",
                actor_id="system",
                tool="lifecycle.shutdown_result",
                decision="deny",
                status="failed",
                reason="the add-on is still running after a stop request",
                params={"previous_result": result_value},
            )

        self._revert_task = asyncio.create_task(_watch(), name="lifecycle-revert")

    # -------------------------------------------------------------- loop

    async def lifecycle_loop(self, interval_seconds: float = TICK_INTERVAL_SECONDS) -> None:
        """Single periodic evaluator; cancelled with the process."""

        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.tick()
            except ConflictError:
                # A manual shutdown already holds the lock.
                continue
            except Exception:
                logger.error("lifecycle shutdown evaluation failed")

    def start_background(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self.lifecycle_loop(), name="lifecycle")

    def cancel_background(self) -> None:
        for task in (self._loop_task, self._revert_task):
            if task is not None and not task.done():
                task.cancel()
        self._loop_task = None
        self._revert_task = None


def map_stop_outcome(outcome: dict[str, Any]) -> GatewayError | None:
    """Map a shutdown outcome dict to the HTTP error for manual shutdowns."""

    result = str(outcome.get("result", ""))
    message = str(outcome.get("message", "the shutdown request failed"))
    if result == "rejected":
        return GatewayError("shutdown_rejected", message, 502)
    if result == "unavailable":
        return GatewayError("supervisor_unavailable", message, 503)
    return None
