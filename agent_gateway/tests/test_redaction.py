"""Redaction, masking, and audit summaries must never leak credentials."""

from __future__ import annotations

import json

from helpers import PASSWORD  # noqa: F401

from gateway.redaction import is_sensitive_key, mask_secret, redact_text, redact_value, summarize
from gateway.runtime import Runtime
from gateway.utils import canonical_json

SECRETS = (
    "hunter2-password",
    "abcdefghijklmnop-token",
    "user@pve!token=proxmox-secret",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature",
)


def test_authorization_headers_are_redacted() -> None:
    text = "Authorization: Bearer abcdefghijklmnop-token"
    redacted = redact_text(text)
    assert "abcdefghijklmnop-token" not in redacted
    assert "REDACTED" in redacted.upper()


def test_key_value_secrets_are_redacted() -> None:
    text = "password=hunter2-password token: abcdefghijklmnop-token"
    redacted = redact_text(text)
    assert "hunter2-password" not in redacted
    assert "abcdefghijklmnop-token" not in redacted


def test_pem_private_key_blocks_are_removed() -> None:
    text = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3NzaC1yc2E\n-----END OPENSSH PRIVATE KEY-----"
    redacted = redact_text(text)
    assert "AAAAB3NzaC1yc2E" not in redacted


def test_proxmox_token_pairs_are_redacted() -> None:
    text = "PVEAPIToken=user@pve!token=proxmox-secret"
    redacted = redact_text(text)
    assert "proxmox-secret" not in redacted


def test_nested_structures_are_redacted_recursively() -> None:
    payload = {
        "connection": {"name": "home", "token": "abcdefghijklmnop-token"},
        "items": [{"password": "hunter2-password"}, {"header": "Bearer abcdefghijklmnop-token"}],
        "safe": "visible",
    }
    redacted = redact_value(payload)
    serialized = canonical_json(redacted)
    assert "abcdefghijklmnop-token" not in serialized
    assert "hunter2-password" not in serialized
    assert redacted["safe"] == "visible"


def test_sensitive_keys_are_detected_case_insensitively() -> None:
    for key in ("token", "Password", "api_key", "private_key", "Authorization", "client_secret"):
        assert is_sensitive_key(key) is True
    assert is_sensitive_key("entity_id") is False


def test_summaries_are_bounded_and_redacted() -> None:
    summary = summarize({"token": "abcdefghijklmnop-token", "note": "x" * 5000}, max_chars=200)
    assert len(summary) <= 240
    assert "abcdefghijklmnop-token" not in summary


def test_mask_secret_keeps_only_edges() -> None:
    masked = mask_secret("abcdefghijklmnopqrstuvwxyz")
    assert "abcdefghijklmnop" not in masked
    assert "wxyz" in masked or masked.endswith("…")


def test_audit_records_redact_parameters(runtime: Runtime) -> None:
    runtime.audit.record(
        actor_type="agent",
        actor_id="agent-1",
        agent_id="agent-1",
        tool="ha.call_service",
        connection_id="c" * 32,
        params_summary=summarize({"token": "abcdefghijklmnop-token"}),
        decision="allow",
        status="succeeded",
    )
    items = runtime.audit.list_events(limit=10)["items"]
    assert items
    serialized = json.dumps(items)
    assert "abcdefghijklmnop-token" not in serialized


def test_admin_actions_do_not_store_secret_values(runtime: Runtime) -> None:
    runtime.audit.admin_action(
        admin_id="admin-user",
        action="credential.create",
        params={"name": "ha", "kind": "home_assistant_token", "value": "abcdefghijklmnop-token"},
    )
    events = runtime.audit.list_events(limit=10)["items"]
    assert "abcdefghijklmnop-token" not in json.dumps(events)
