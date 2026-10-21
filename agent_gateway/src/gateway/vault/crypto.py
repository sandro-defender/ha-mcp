"""Authenticated encryption primitives for the credential vault.

Design:

- A random 256-bit data encryption key (DEK) encrypts each secret record with
  AES-256-GCM.  Every record has a unique 96-bit nonce.  The record id and
  vault format version are authenticated as additional data, so records cannot
  be swapped between ids without detection.
- The DEK is wrapped with a key encryption key (KEK) derived from the master
  password with Argon2id.  The KEK is never stored.
- Auto-unlock optionally wraps the same DEK with a random 256-bit key stored in
  a separate file.  This trades at-rest protection for unattended restarts and
  is documented.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULT_FORMAT = "hag-vault"
VAULT_VERSION = 1
MIN_PASSWORD_LENGTH = 12
NONCE_BYTES = 12
KEY_BYTES = 32

DEFAULT_KDF_MEMORY_KIB = 65_536  # 64 MiB
DEFAULT_KDF_TIME_COST = 3
DEFAULT_KDF_PARALLELISM = 2
MIN_KDF_MEMORY_KIB = 8
MAX_KDF_MEMORY_KIB = 1_048_576
MAX_KDF_TIME_COST = 20
MAX_KDF_PARALLELISM = 16


class VaultCryptoError(Exception):
    """Raised for cryptographic failures; message is safe to surface."""


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(value: str) -> bytes:
    if not isinstance(value, str):
        raise VaultCryptoError("vault contains an invalid encoding")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise VaultCryptoError("vault contains an invalid encoding") from exc


@dataclass(frozen=True)
class KdfParams:
    salt: bytes
    memory_cost: int = DEFAULT_KDF_MEMORY_KIB
    time_cost: int = DEFAULT_KDF_TIME_COST
    parallelism: int = DEFAULT_KDF_PARALLELISM
    hash_len: int = KEY_BYTES

    @classmethod
    def create(
        cls,
        *,
        memory_cost: int = DEFAULT_KDF_MEMORY_KIB,
        time_cost: int = DEFAULT_KDF_TIME_COST,
        parallelism: int = DEFAULT_KDF_PARALLELISM,
    ) -> KdfParams:
        params = cls(
            salt=os.urandom(16),
            memory_cost=memory_cost,
            time_cost=time_cost,
            parallelism=parallelism,
        )
        params.validate()
        return params

    def validate(self) -> None:
        if not MIN_KDF_MEMORY_KIB <= self.memory_cost <= MAX_KDF_MEMORY_KIB:
            raise VaultCryptoError("vault KDF memory parameter is out of range")
        if not 1 <= self.time_cost <= MAX_KDF_TIME_COST:
            raise VaultCryptoError("vault KDF time parameter is out of range")
        if not 1 <= self.parallelism <= MAX_KDF_PARALLELISM:
            raise VaultCryptoError("vault KDF parallelism parameter is out of range")
        if self.hash_len != KEY_BYTES:
            raise VaultCryptoError("vault KDF key length is invalid")
        if len(self.salt) < 16 or len(self.salt) > 64:
            raise VaultCryptoError("vault KDF salt is invalid")

    def derive(self, password: str) -> bytes:
        self.validate()
        if not password:
            raise VaultCryptoError("master password is required")
        try:
            return hash_secret_raw(
                secret=password.encode("utf-8"),
                salt=self.salt,
                time_cost=self.time_cost,
                memory_cost=self.memory_cost,
                parallelism=self.parallelism,
                hash_len=self.hash_len,
                type=Type.ID,
            )
        except (ValueError, MemoryError) as exc:
            raise VaultCryptoError("could not derive the vault key") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": "argon2id",
            "salt": b64encode(self.salt),
            "memory_cost_kib": self.memory_cost,
            "time_cost": self.time_cost,
            "parallelism": self.parallelism,
            "hash_len": self.hash_len,
        }

    @classmethod
    def from_dict(cls, data: Any) -> KdfParams:
        if not isinstance(data, dict):
            raise VaultCryptoError("vault KDF metadata is malformed")
        if data.get("algorithm") != "argon2id":
            raise VaultCryptoError("vault uses an unsupported KDF")
        try:
            params = cls(
                salt=b64decode(data["salt"]),
                memory_cost=int(data["memory_cost_kib"]),
                time_cost=int(data["time_cost"]),
                parallelism=int(data["parallelism"]),
                hash_len=int(data["hash_len"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VaultCryptoError("vault KDF metadata is malformed") from exc
        params.validate()
        return params


def new_key() -> bytes:
    return os.urandom(KEY_BYTES)


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> dict[str, str]:
    if len(key) != KEY_BYTES:
        raise VaultCryptoError("invalid encryption key length")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {"nonce": b64encode(nonce), "ciphertext": b64encode(ciphertext)}


def decrypt(key: bytes, envelope: Any, aad: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise VaultCryptoError("invalid encryption key length")
    if not isinstance(envelope, dict):
        raise VaultCryptoError("vault envelope is malformed")
    try:
        nonce = b64decode(envelope["nonce"])
        ciphertext = b64decode(envelope["ciphertext"])
    except KeyError as exc:
        raise VaultCryptoError("vault envelope is malformed") from exc
    if len(nonce) != NONCE_BYTES or len(ciphertext) < 16:
        raise VaultCryptoError("vault envelope is malformed")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except (InvalidTag, ValueError) as exc:
        raise VaultCryptoError("vault authentication failed") from exc


def dek_aad() -> bytes:
    return f"hag-vault:v{VAULT_VERSION}:dek".encode()


def auto_unlock_aad() -> bytes:
    return f"hag-vault:v{VAULT_VERSION}:auto-unlock".encode()


def record_aad(record_id: str) -> bytes:
    return f"hag-vault:v{VAULT_VERSION}:record:{record_id}".encode()


def encode_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def decode_json(value: bytes) -> Any:
    try:
        return json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultCryptoError("vault record is malformed") from exc
