"""Small shared helpers: time, canonical JSON, matching, and validation."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import re
import secrets
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\.[a-z0-9_]{1,128}$")
SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
NODE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CONNECTION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,63}$")
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,63}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
UPID_RE = re.compile(
    r"^UPID:[A-Za-z0-9._-]{1,64}:[0-9A-Fa-f]{1,16}:[0-9A-Fa-f]{1,16}:[0-9A-Fa-f]{1,16}:[A-Za-z0-9._-]{1,64}:[0-9A-Fa-f]{1,16}:[^:\s]{1,128}:[^:\s]{0,128}$"
)
SAFE_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
IP_RE = re.compile(r"^[0-9a-fA-F:.]{2,64}$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def expires_after(seconds: int) -> tuple[str, datetime]:
    when = utcnow() + timedelta(seconds=seconds)
    return iso(when), when


def canonical_json(value: Any) -> str:
    """Deterministic JSON used for hashing and equality checks."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def random_token(nbytes: int = 32, prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(nbytes)


def constant_time_equal(left: str, right: str) -> bool:
    # compare_digest over UTF-8 bytes; lengths are not secret for hashes.
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def match_any(value: str, patterns: Iterable[str]) -> bool:
    return any(pattern == "*" or fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def validate_entity_id(entity_id: str) -> str:
    if not ENTITY_ID_RE.fullmatch(entity_id):
        raise ValueError("invalid entity_id")
    return entity_id


def validate_node(node: str) -> str:
    if not NODE_NAME_RE.fullmatch(node):
        raise ValueError("invalid node name")
    return node


def validate_service(service: str) -> str:
    if not SERVICE_NAME_RE.fullmatch(service):
        raise ValueError("invalid service name")
    return service


def validate_vmid(vmid: int) -> int:
    if isinstance(vmid, bool) or not isinstance(vmid, int) or vmid < 1 or vmid > 999_999_999:
        raise ValueError("invalid VM/container id")
    return vmid


def validate_upid(upid: str) -> str:
    if not UPID_RE.fullmatch(upid):
        raise ValueError("invalid task id")
    return upid


def validate_absolute_path(path: str) -> str:
    if not path.startswith("/") or "\x00" in path or ".." in path.split("/"):
        raise ValueError("invalid absolute path")
    if any(ch in path for ch in ("\n", "\r")):
        raise ValueError("invalid path")
    return path


def bounded_json_size(value: Any) -> int:
    try:
        return len(canonical_json(value).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def safe_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def nested_get(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def redact_url(url: str) -> str:
    """Return scheme+host+path only; drop credentials, query, and fragment."""

    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{parts.path}"
