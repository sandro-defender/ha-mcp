"""Agent token generation and comparison.

Tokens are 256-bit random URL-safe strings prefixed with ``hag_``.  Only a
SHA-256 digest is stored.  A fast hash is appropriate here because the input
has full 256-bit entropy; Argon2 is used where low-entropy human passwords are
involved (the vault).  Lookups use the digest index; a constant-time compare
guards the final equality check.
"""

from __future__ import annotations

import secrets
import string

from ..utils import constant_time_equal, sha256_hex

TOKEN_PREFIX = "hag_"
TOKEN_ENTROPY_BYTES = 32
TOKEN_MIN_LENGTH = 40


def generate_agent_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def token_digest(token: str) -> str:
    return sha256_hex(token.encode("utf-8"))


def token_prefix(token: str) -> str:
    return token[:12]


def looks_like_token(token: str) -> bool:
    if not isinstance(token, str) or len(token) < TOKEN_MIN_LENGTH or not token.startswith(TOKEN_PREFIX):
        return False
    allowed = set(string.ascii_letters + string.digits + "-_")
    return all(char in allowed for char in token[len(TOKEN_PREFIX) :])


def digest_matches(stored_digest: str, candidate_token: str) -> bool:
    return constant_time_equal(stored_digest, token_digest(candidate_token))
