"""Versioned encrypted credential vault with an explicit unlock lifecycle."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import (
    ConflictError,
    NotFoundError,
    ValidationFailure,
    VaultError,
    VaultLocked,
    VaultNotInitialized,
)
from ..redaction import mask_secret
from ..utils import SECRET_NAME_RE, iso
from .crypto import (
    VAULT_FORMAT,
    VAULT_VERSION,
    KdfParams,
    VaultCryptoError,
    auto_unlock_aad,
    b64decode,
    decode_json,
    decrypt,
    dek_aad,
    encode_json,
    encrypt,
    new_key,
    record_aad,
)

logger = logging.getLogger(__name__)

SECRET_KINDS = frozenset(
    {
        "home_assistant_token",
        "proxmox_token",
        "proxmox_ca",
        "ca_certificate",
        "ssh_private_key",
        "ssh_key_passphrase",
        "generic",
    }
)

MAX_VALUE_BYTES = 65_536
MAX_METADATA_BYTES = 4_096


@dataclass(frozen=True)
class SecretRecord:
    id: str
    name: str
    kind: str
    value: dict[str, str] = field(repr=False)
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "masked": mask_record(self.kind, self.value),
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def mask_record(kind: str, value: dict[str, str]) -> str:
    if kind == "ssh_private_key":
        return "private key stored"
    if kind == "ssh_key_passphrase":
        return "passphrase stored"
    if kind in {"proxmox_ca", "ca_certificate"}:
        return "CA certificate stored"
    primary = value.get("token") or value.get("value") or next(iter(value.values()), "")
    return mask_secret(primary)


class CredentialVault:
    """File-backed, AES-256-GCM encrypted vault.

    The vault file holds only ciphertext and non-secret metadata.  The DEK is
    held in memory only while unlocked.  ``lock()`` overwrites the in-memory
    copy on a best-effort basis; Python cannot guarantee that no copy remains
    in freed memory.
    """

    def __init__(
        self,
        path: Path,
        unlock_key_path: Path,
        *,
        kdf_memory_cost: int = 65_536,
        kdf_time_cost: int = 3,
        kdf_parallelism: int = 2,
    ) -> None:
        self.path = path
        self.unlock_key_path = unlock_key_path
        self._kdf_defaults = {
            "memory_cost": kdf_memory_cost,
            "time_cost": kdf_time_cost,
            "parallelism": kdf_parallelism,
        }
        self._dek: bytearray | None = None
        self._document: dict[str, Any] | None = None
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- status

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def unlocked(self) -> bool:
        return self._dek is not None

    def status(self) -> dict[str, Any]:
        with self._lock:
            document = self._document if self._document is not None else self._safe_read()
            record_count = len(document.get("records", {})) if document else 0
            return {
                "exists": self.exists,
                "unlocked": self.unlocked,
                "auto_unlock_enabled": bool(document and document.get("auto_unlock")),
                "auto_unlock_key_present": self.unlock_key_path.is_file(),
                "version": document.get("version") if document else None,
                "records": record_count,
                "updated_at": (document or {}).get("meta", {}).get("updated_at"),
                "kdf": {
                    "algorithm": "argon2id",
                    "memory_cost_kib": self._kdf_defaults["memory_cost"],
                    "time_cost": self._kdf_defaults["time_cost"],
                    "parallelism": self._kdf_defaults["parallelism"],
                },
            }

    # ------------------------------------------------------------ lifecycle

    def initialize(self, password: str) -> dict[str, Any]:
        self._validate_password(password)
        with self._lock:
            if self.exists:
                raise ConflictError("vault already exists")
            kdf = KdfParams.create(**self._kdf_defaults)
            kek = kdf.derive(password)
            dek = new_key()
            now = iso()
            document: dict[str, Any] = {
                "format": VAULT_FORMAT,
                "version": VAULT_VERSION,
                "kdf": kdf.to_dict(),
                "wrapped_dek": encrypt(kek, dek, dek_aad()),
                "auto_unlock": None,
                "records": {},
                "meta": {"created_at": now, "updated_at": now, "revision": 1},
            }
            self._write_document(document)
            self._dek = bytearray(dek)
            self._document = document
            logger.info("credential vault initialized")
            return self.status()

    def unlock(self, password: str) -> dict[str, Any]:
        with self._lock:
            if not self.exists:
                raise VaultNotInitialized()
            document = self._read_document()
            kdf = KdfParams.from_dict(document.get("kdf"))
            kek = kdf.derive(password)
            legacy = document.get("version") == 0
            try:
                wrapped = document.get("wrapped_dek")
                dek = decrypt_legacy(kek, wrapped) if legacy else decrypt(kek, wrapped, dek_aad())
            except VaultCryptoError as exc:
                raise VaultError(
                    "invalid master password or corrupted vault",
                    code="vault_unlock_failed",
                    status=401,
                ) from exc
            if len(dek) != 32:
                raise VaultError("vault contains an invalid key", code="vault_corrupt", status=500)

            # Verify every record before declaring the vault unlocked.  A
            # corrupted or swapped record makes the vault fail closed.
            self._verify_records(document, dek, legacy=legacy)

            if legacy:
                document = self._migrate_legacy(document, dek)
                self._write_document(document)

            self._dek = bytearray(dek)
            self._document = document
            logger.info("credential vault unlocked")
            return self.status()

    def unlock_with_key_file(self) -> bool:
        """Unlock using the auto-unlock key, if enabled. Returns True on success."""

        with self._lock:
            if not self.exists or not self.unlock_key_path.is_file():
                return False
            document = self._read_document()
            wrapped = document.get("auto_unlock")
            if not wrapped:
                return False
            try:
                key = self.unlock_key_path.read_bytes()
            except OSError:
                logger.warning("auto-unlock key could not be read")
                return False
            if len(key) != 32:
                logger.warning("auto-unlock key has an invalid length")
                return False
            try:
                dek = decrypt(key, wrapped, auto_unlock_aad())
                self._verify_records(document, dek, legacy=False)
            except VaultCryptoError:
                logger.warning("auto-unlock failed; vault remains locked")
                return False
            self._dek = bytearray(dek)
            self._document = document
            logger.info("credential vault unlocked with auto-unlock key")
            return True

    def lock(self) -> dict[str, Any]:
        with self._lock:
            if self._dek is not None:
                for index in range(len(self._dek)):
                    self._dek[index] = 0
            self._dek = None
            self._document = None
            logger.info("credential vault locked")
            return self.status()

    def change_password(self, current_password: str, new_password: str) -> dict[str, Any]:
        self._validate_password(new_password)
        with self._lock:
            document = self._require_document()
            dek = self._require_dek()
            if not self._password_matches(document, current_password, dek):
                raise VaultError(
                    "current master password is incorrect", code="vault_unlock_failed", status=401
                )

            kdf = KdfParams.create(**self._kdf_defaults)
            kek = kdf.derive(new_password)
            document["kdf"] = kdf.to_dict()
            document["wrapped_dek"] = encrypt(kek, bytes(dek), dek_aad())
            if document.get("auto_unlock"):
                auto_key = self._read_unlock_key()
                document["auto_unlock"] = encrypt(auto_key, bytes(dek), auto_unlock_aad())
            self._touch(document)
            self._write_document(document)
            self._document = document
            return self.status()

    def rotate_data_key(self, password: str) -> dict[str, Any]:
        """Re-encrypt all records with a fresh DEK, keeping the same password."""

        with self._lock:
            document = self._require_document()
            old_dek = self._require_dek()
            if not self._password_matches(document, password, old_dek):
                raise VaultError("master password is incorrect", code="vault_unlock_failed", status=401)

            kdf = KdfParams.from_dict(document["kdf"])
            kek = kdf.derive(password)
            new_dek = new_key()
            records: dict[str, Any] = {}
            for record_id in document.get("records", {}):
                plaintext = decrypt(bytes(old_dek), document["records"][record_id], record_aad(record_id))
                records[record_id] = encrypt(new_dek, plaintext, record_aad(record_id))
            document["records"] = records
            document["wrapped_dek"] = encrypt(kek, new_dek, dek_aad())
            if document.get("auto_unlock"):
                auto_key = self._read_unlock_key()
                document["auto_unlock"] = encrypt(auto_key, new_dek, auto_unlock_aad())
            self._touch(document)
            self._write_document(document)
            if self._dek is not None:
                for index in range(len(self._dek)):
                    self._dek[index] = 0
            self._dek = bytearray(new_dek)
            self._document = document
            logger.info("vault data encryption key rotated")
            return self.status()

    # ------------------------------------------------------- auto-unlock

    def enable_auto_unlock(self) -> dict[str, Any]:
        with self._lock:
            document = self._require_document()
            dek = self._require_dek()
            key = new_key()
            self._write_unlock_key(key)
            doc_copy = json.loads(json.dumps(document))
            doc_copy["auto_unlock"] = encrypt(key, bytes(dek), auto_unlock_aad())
            self._touch(doc_copy)
            try:
                self._write_document(doc_copy)
            except Exception:
                with_suppress_unlink(self.unlock_key_path)
                raise
            self._document = doc_copy
            logger.warning(
                "auto-unlock enabled: the unlock key is stored beside the vault; "
                "theft of both files defeats at-rest encryption"
            )
            return self.status()

    def disable_auto_unlock(self) -> dict[str, Any]:
        with self._lock:
            document = self._require_document()
            document["auto_unlock"] = None
            self._touch(document)
            self._write_document(document)
            self._document = document
            with_suppress_unlink(self.unlock_key_path)
            logger.info("auto-unlock disabled")
            return self.status()

    # ---------------------------------------------------------- records

    def create_secret(
        self,
        *,
        name: str,
        kind: str,
        value: dict[str, str],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_record_input(name, kind, value, metadata)
        with self._lock:
            document = self._require_document()
            dek = self._require_dek()
            if any(
                self._decrypt_record(document, dek, record_id).name == name
                for record_id in document.get("records", {})
            ):
                raise ConflictError("a credential with that name already exists")
            record_id = uuid.uuid4().hex
            now = iso()
            payload = {
                "name": name,
                "kind": kind,
                "value": value,
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
            }
            document["records"][record_id] = encrypt(bytes(dek), encode_json(payload), record_aad(record_id))
            self._touch(document)
            self._write_document(document)
            self._document = document
            return SecretRecord(
                id=record_id,
                name=name,
                kind=kind,
                value=value,
                metadata=metadata or {},
                created_at=now,
                updated_at=now,
            ).public()

    def update_secret(
        self,
        record_id: str,
        *,
        value: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            document = self._require_document()
            dek = self._require_dek()
            record = self._get_record(document, dek, record_id)
            new_name = name or record.name
            new_value = value if value is not None else record.value
            new_metadata = metadata if metadata is not None else record.metadata
            self._validate_record_input(new_name, record.kind, new_value, new_metadata)
            for other_id in document["records"]:
                if other_id != record_id and self._decrypt_record(document, dek, other_id).name == new_name:
                    raise ConflictError("a credential with that name already exists")
            updated_at = iso()
            payload = {
                "name": new_name,
                "kind": record.kind,
                "value": new_value,
                "metadata": new_metadata,
                "created_at": record.created_at,
                "updated_at": updated_at,
            }
            updated = SecretRecord(
                id=record_id,
                name=new_name,
                kind=record.kind,
                value=new_value,
                metadata=new_metadata,
                created_at=record.created_at,
                updated_at=updated_at,
            )
            document["records"][record_id] = encrypt(bytes(dek), encode_json(payload), record_aad(record_id))
            self._touch(document)
            self._write_document(document)
            self._document = document
            return updated.public()

    def delete_secret(self, record_id: str) -> None:
        with self._lock:
            document = self._require_document()
            if record_id not in document.get("records", {}):
                raise NotFoundError("credential")
            del document["records"][record_id]
            self._touch(document)
            self._write_document(document)
            self._document = document
            logger.info("credential deleted id=%s", record_id)

    def list_secrets(self) -> list[dict[str, Any]]:
        with self._lock:
            document = self._require_document()
            dek = self._require_dek()
            records = [self._get_record(document, dek, rid).public() for rid in document.get("records", {})]
            return sorted(records, key=lambda item: item["name"].lower())

    def get_secret(self, record_id: str) -> SecretRecord:
        """Internal use only. Never return this object across an interface."""

        with self._lock:
            document = self._require_document()
            dek = self._require_dek()
            return self._get_record(document, dek, record_id)

    def find_secret_ids_by_kind(self, kind: str) -> list[str]:
        with self._lock:
            document = self._require_document()
            dek = self._require_dek()
            return [
                rid
                for rid in document.get("records", {})
                if self._decrypt_record(document, dek, rid).kind == kind
            ]

    # ------------------------------------------------------ export/import

    def export_encrypted(self) -> bytes:
        self._require_document()
        if not self.exists:
            raise VaultNotInitialized()
        return self.path.read_bytes()

    def restore_encrypted(self, data: bytes, password: str) -> dict[str, Any]:
        """Validate an encrypted export, replace the vault with it, and lock."""

        if len(data) > 5_000_000:
            raise ValidationFailure("vault import is too large")
        document = self._parse_and_validate(data)
        kdf = KdfParams.from_dict(document.get("kdf"))
        kek = kdf.derive(password)
        legacy = document.get("version") == 0
        try:
            dek = (
                decrypt_legacy(kek, document.get("wrapped_dek"))
                if legacy
                else decrypt(kek, document.get("wrapped_dek"), dek_aad())
            )
        except VaultCryptoError as exc:
            raise VaultError(
                "invalid password for the imported vault", code="vault_unlock_failed", status=401
            ) from exc
        self._verify_records(document, dek, legacy=legacy)
        if legacy:
            document = self._migrate_legacy(document, dek)
        # Auto-unlock depends on a key file that is not part of the export.
        document["auto_unlock"] = None
        self._touch(document)
        self._write_document(document)
        self.lock()
        logger.info("vault restored from encrypted import; vault is locked")
        return self.status()

    # ------------------------------------------------------------ internals

    def _validate_password(self, password: str) -> None:
        if not isinstance(password, str) or len(password) < 12:
            raise ValidationFailure("master password must be at least 12 characters")
        if password.strip() != password or not password.strip():
            raise ValidationFailure("master password must not have leading or trailing whitespace")

    def _validate_record_input(
        self,
        name: str,
        kind: str,
        value: dict[str, str],
        metadata: dict[str, Any] | None,
    ) -> None:
        if not isinstance(name, str) or not SECRET_NAME_RE.fullmatch(name):
            raise ValidationFailure("credential name is invalid")
        if kind not in SECRET_KINDS:
            raise ValidationFailure("credential kind is not supported")
        if not isinstance(value, dict) or not value:
            raise ValidationFailure("credential value is required")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise ValidationFailure("credential values must be strings")
        if len(encode_json(value)) > MAX_VALUE_BYTES:
            raise ValidationFailure("credential value is too large")
        if metadata is not None and (
            not isinstance(metadata, dict) or len(encode_json(metadata)) > MAX_METADATA_BYTES
        ):
            raise ValidationFailure("credential metadata is too large")
        if len(name) > 64:
            raise ValidationFailure("credential name is too long")

    def _require_document(self) -> dict[str, Any]:
        if not self.exists:
            raise VaultNotInitialized()
        if self._document is None or self._dek is None:
            raise VaultLocked()
        return self._document

    def _require_dek(self) -> bytearray:
        if self._dek is None:
            raise VaultLocked()
        return self._dek

    def _get_record(self, document: dict[str, Any], dek: bytearray, record_id: str) -> SecretRecord:
        if record_id not in document.get("records", {}):
            raise NotFoundError("credential")
        return self._decrypt_record(document, dek, record_id)

    def _decrypt_record(self, document: dict[str, Any], dek: bytearray, record_id: str) -> SecretRecord:
        try:
            plaintext = decrypt(bytes(dek), document["records"][record_id], record_aad(record_id))
            payload = decode_json(plaintext)
        except (VaultCryptoError, KeyError) as exc:
            raise VaultError("vault record is corrupted", code="vault_corrupt", status=500) from exc
        if not isinstance(payload, dict):
            raise VaultError("vault record is malformed", code="vault_corrupt", status=500)
        try:
            return SecretRecord(
                id=record_id,
                name=str(payload["name"]),
                kind=str(payload["kind"]),
                value={str(k): str(v) for k, v in payload["value"].items()},
                metadata=payload.get("metadata") or {},
                created_at=str(payload["created_at"]),
                updated_at=str(payload["updated_at"]),
            )
        except (KeyError, AttributeError, TypeError) as exc:
            raise VaultError("vault record is malformed", code="vault_corrupt", status=500) from exc

    def _verify_records(self, document: dict[str, Any], dek: bytes, *, legacy: bool) -> None:
        records = document.get("records", {}) if not legacy else document.get("secrets", {})
        if not isinstance(records, dict):
            raise VaultError("vault record set is malformed", code="vault_corrupt", status=500)
        for record_id, envelope in records.items():
            try:
                if legacy:
                    payload = decode_json(decrypt_legacy(dek, envelope))
                else:
                    payload = decode_json(decrypt(dek, envelope, record_aad(record_id)))
            except VaultCryptoError as exc:
                raise VaultError(
                    "vault is corrupted or has been tampered with", code="vault_corrupt", status=500
                ) from exc
            if not isinstance(payload, dict) or not {"name", "kind", "value"}.issubset(payload):
                raise VaultError("vault record is malformed", code="vault_corrupt", status=500)

    def _migrate_legacy(self, document: dict[str, Any], dek: bytes) -> dict[str, Any]:
        kdf = KdfParams.from_dict(document.get("kdf"))
        # The KEK is not available here; migration keeps the existing wrapped
        # DEK, which is safe because the legacy wrap and the v1 wrap both
        # authenticate the same key only after the unlock above succeeded.
        migrated: dict[str, Any] = {
            "format": VAULT_FORMAT,
            "version": VAULT_VERSION,
            "kdf": kdf.to_dict(),
            "wrapped_dek": document.get("wrapped_dek"),
            "auto_unlock": None,
            "records": {},
            "meta": {
                "created_at": document.get("meta", {}).get("created_at", iso()),
                "updated_at": iso(),
                "revision": 1,
                "migrated_from": 0,
            },
        }
        for record_id, envelope in document.get("secrets", {}).items():
            payload = decode_json(decrypt_legacy(dek, envelope))
            migrated["records"][record_id] = encrypt(dek, encode_json(payload), record_aad(record_id))
        logger.info("credential vault migrated from format version 0 to %s", VAULT_VERSION)
        return migrated

    def _password_matches(self, document: dict[str, Any], password: str, dek: bytearray) -> bool:
        try:
            kdf = KdfParams.from_dict(document["kdf"])
            candidate = decrypt(kdf.derive(password), document["wrapped_dek"], dek_aad())
        except VaultCryptoError:
            return False
        import hmac

        return hmac.compare_digest(candidate, bytes(dek))

    def _read_unlock_key(self) -> bytes:
        try:
            key = self.unlock_key_path.read_bytes()
        except OSError as exc:
            raise VaultError(
                "auto-unlock key is missing", code="auto_unlock_unavailable", status=409
            ) from exc
        if len(key) != 32:
            raise VaultError("auto-unlock key is invalid", code="auto_unlock_unavailable", status=409)
        return key

    def _touch(self, document: dict[str, Any]) -> None:
        now = iso()
        meta = document.setdefault("meta", {})
        meta.setdefault("created_at", now)
        meta["updated_at"] = now
        meta["revision"] = int(meta.get("revision", 0)) + 1

    def _write_unlock_key(self, key: bytes) -> None:
        self.unlock_key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".unlock-", dir=self.unlock_key_path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.unlock_key_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _write_document(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        serialized = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=".vault-", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                try:
                    backup_path = self.path.with_name(self.path.name + ".bak")
                    shutil.copyfile(self.path, backup_path)
                    os.chmod(backup_path, 0o600)
                except OSError:
                    logger.warning("could not write vault backup copy")
            os.replace(temp_name, self.path)
            try:
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _safe_read(self) -> dict[str, Any] | None:
        if not self.exists:
            return None
        try:
            return self._read_document()
        except VaultError:
            return None

    def _read_document(self) -> dict[str, Any]:
        try:
            data = self.path.read_bytes()
        except OSError as exc:
            raise VaultError("vault file could not be read", code="vault_unreadable", status=500) from exc
        return self._parse_and_validate(data)

    def _parse_and_validate(self, data: bytes) -> dict[str, Any]:
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultError("vault file is corrupted", code="vault_corrupt", status=500) from exc
        if not isinstance(document, dict) or document.get("format") != VAULT_FORMAT:
            raise VaultError("vault file format is not recognized", code="vault_corrupt", status=500)
        version = document.get("version")
        if version not in (0, VAULT_VERSION):
            raise VaultError(
                "vault format version is not supported by this release",
                code="vault_version_unsupported",
                status=409,
            )
        if not isinstance(document.get("kdf"), dict) or not isinstance(document.get("wrapped_dek"), dict):
            raise VaultError("vault file is incomplete", code="vault_corrupt", status=500)
        records_key = "secrets" if version == 0 else "records"
        if not isinstance(document.get(records_key), dict):
            raise VaultError("vault file is incomplete", code="vault_corrupt", status=500)
        return document


def decrypt_legacy(key: bytes, envelope: Any) -> bytes:
    """Decrypt a version-0 record/wrap, which did not use AAD."""

    if not isinstance(envelope, dict):
        raise VaultCryptoError("vault envelope is malformed")
    try:
        nonce = b64decode(envelope["nonce"])
        ciphertext = b64decode(envelope["ciphertext"])
    except (KeyError, VaultCryptoError) as exc:
        raise VaultCryptoError("vault envelope is malformed") from exc
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise VaultCryptoError("vault authentication failed") from exc


def with_suppress_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("could not remove auto-unlock key file")


__all__ = ["SECRET_KINDS", "CredentialVault", "SecretRecord"]
