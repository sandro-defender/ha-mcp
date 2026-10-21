"""Secret redaction for logs, errors, audit rows, and summaries.

This is a defence-in-depth layer.  The primary rule is still: do not pass
secrets to logging or response-building code in the first place.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

MASK = "[REDACTED]"

# Exact (case-insensitive) key names whose values are secret or credential
# material.  ``token_id``, ``public_key``, ``host_key``, and ``key_type`` are
# intentionally absent: they are public identifiers, not secrets.
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "passphrase",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "private_key",
        "privatekey",
        "ssh_private_key",
        "token",
        "bearer",
        "access_token",
        "refresh_token",
        "vault_key",
        "unlock_key",
        "dek",
        "kek",
        "credential",
        "credentials",
        "x-supervisor-token",
        "pveapitoken",
        "proxmox_secret",
    }
)

_SENSITIVE_SUFFIXES = (
    "_password",
    "_passphrase",
    "_secret",
    "_private_key",
    "_api_key",
    "_token_secret",
)

_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL),
        MASK,
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), f"Bearer {MASK}"),
    (re.compile(r"\bPVEAPIToken=[^\s,'\"]+", re.IGNORECASE), f"PVEAPIToken={MASK}"),
    (re.compile(r"\bhag_[A-Za-z0-9_-]{16,}"), MASK),
    (
        re.compile(
            r"(?i)\b(authorization|password|passphrase|secret|token|api[_-]?key|cookie|x-supervisor-token)"
            r"(\s*[:=]\s*[\"']?)([^\s\"',;}]+)"
        ),
        r"\1\2" + MASK,
    ),
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in SENSITIVE_KEYS:
        return True
    return any(lowered.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def redact_text(text: str) -> str:
    result = text
    for pattern, replacement in _TEXT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 12:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if is_sensitive_key(key):
                cleaned[key] = MASK
            else:
                cleaned[key] = redact_value(raw_value, _depth=_depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def summarize(value: Any, *, max_chars: int = 1200) -> str:
    """Return a redacted, bounded JSON summary suitable for audit storage."""

    try:
        encoded = json.dumps(redact_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        encoded = json.dumps({"_summary": "unserializable"})
    if len(encoded) > max_chars:
        encoded = encoded[: max_chars - 3] + "..."
    return encoded


def mask_secret(value: str) -> str:
    """Mask a secret for display; never returns more than four trailing chars."""

    if not value:
        return "not set"
    if len(value) <= 8:
        return "••••"
    return "••••" + value[-4:]


def redact_sequence(values: Sequence[Any]) -> list[Any]:
    return [redact_value(item) for item in values]
