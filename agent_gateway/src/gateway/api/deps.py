"""FastAPI dependencies for both surfaces."""

from __future__ import annotations

from fastapi import Request

from ..auth.agents import AgentPrincipal
from ..errors import AuthError
from ..runtime import Runtime
from .middleware import AdminPrincipal


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:  # pragma: no cover - wiring error
        raise RuntimeError("runtime is not attached to the application")
    return runtime


def get_agent(request: Request) -> AgentPrincipal:
    agent = getattr(request.state, "agent", None)
    if not isinstance(agent, AgentPrincipal):
        raise AuthError()
    return agent


def get_admin(request: Request) -> AdminPrincipal:
    admin = getattr(request.state, "admin", None)
    if not isinstance(admin, AdminPrincipal):
        raise AuthError("management session required")
    return admin
