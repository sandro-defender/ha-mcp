"""Typed errors with safe, non-secret messages.

Every error that crosses an interface boundary is expected to be one of these
types.  The ``message`` is safe to log and to return to a client; internal
details (connector response bodies, exception reprs, paths) must not be placed
in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GatewayError(Exception):
    code: str
    message: str
    status: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class ConfigError(GatewayError):
    def __init__(self, message: str) -> None:
        super().__init__("config_error", message, 500)


class NotFoundError(GatewayError):
    def __init__(self, what: str = "resource") -> None:
        super().__init__("not_found", f"{what} was not found", 404)


class ConflictError(GatewayError):
    def __init__(self, message: str) -> None:
        super().__init__("conflict", message, 409)


class ValidationFailure(GatewayError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("validation_error", message, 422, details or {})


class AuthError(GatewayError):
    def __init__(self, message: str = "authentication required", *, code: str = "unauthenticated") -> None:
        super().__init__(code, message, 401)


class RateLimited(GatewayError):
    def __init__(self, retry_after: int = 60) -> None:
        super().__init__(
            "rate_limited",
            "too many requests; retry later",
            429,
            {"retry_after_seconds": retry_after},
        )


class PolicyDenied(GatewayError):
    def __init__(self, reason: str = "no matching policy rule") -> None:
        super().__init__("policy_denied", f"operation denied: {reason}", 403)


class ApprovalRequired(GatewayError):
    def __init__(self, approval_id: str, expires_at: str, operation_id: str) -> None:
        super().__init__(
            "approval_required",
            "operation requires human approval",
            202,
            {"approval_id": approval_id, "expires_at": expires_at, "operation_id": operation_id},
        )


class ApprovalInvalid(GatewayError):
    def __init__(self, message: str = "approval is not valid for this request") -> None:
        super().__init__("approval_invalid", message, 409)


class VaultError(GatewayError):
    def __init__(self, message: str, *, code: str = "vault_error", status: int = 400) -> None:
        super().__init__(code, message, status)


class VaultLocked(VaultError):
    def __init__(self) -> None:
        super().__init__("vault is locked", code="vault_locked", status=423)


class VaultNotInitialized(VaultError):
    def __init__(self) -> None:
        super().__init__("vault has not been initialized", code="vault_not_initialized", status=409)


class ConnectorError(GatewayError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 502,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, status, details or {})


class NotConfigured(ConnectorError):
    def __init__(self, what: str = "connection") -> None:
        super().__init__("not_configured", f"{what} is not configured", status=409)


class ToolUnavailable(GatewayError):
    def __init__(self, message: str) -> None:
        super().__init__("tool_unavailable", message, 409)


class OutputTooLarge(GatewayError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            "output_too_large",
            "result exceeded the configured output limit; narrow the request",
            413,
            {"limit_bytes": limit},
        )


class OperationTimeout(GatewayError):
    def __init__(self) -> None:
        super().__init__("timeout", "operation timed out", 504)


class EmergencyDisabled(GatewayError):
    def __init__(self) -> None:
        super().__init__("agent_access_disabled", "agent access is disabled by the administrator", 503)


class ShuttingDown(GatewayError):
    def __init__(self) -> None:
        super().__init__(
            "shutting_down",
            "the gateway is shutting down and is not accepting new agent sessions or operations",
            503,
        )
