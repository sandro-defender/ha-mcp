"""Vault lifecycle, crypto envelope, and export/import behaviour."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from helpers import PASSWORD

from gateway.errors import ConflictError, NotFoundError, ValidationFailure, VaultError, VaultLocked
from gateway.vault.crypto import (
    KdfParams,
    VaultCryptoError,
    b64decode,
    decrypt,
    dek_aad,
    encrypt,
    new_key,
    record_aad,
)
from gateway.vault.manager import CredentialVault, mask_record


def make_vault(tmp_path: Path) -> CredentialVault:
    return CredentialVault(
        tmp_path / "vault.json", tmp_path / "unlock.key", kdf_memory_cost=1024, kdf_time_cost=1
    )


# --------------------------------------------------------------------- crypto


def test_encrypt_decrypt_roundtrip_and_unique_nonces() -> None:
    key = new_key()
    first = encrypt(key, b"payload", record_aad("record-1"))
    second = encrypt(key, b"payload", record_aad("record-1"))
    assert first["nonce"] != second["nonce"]
    assert decrypt(key, first, record_aad("record-1")) == b"payload"


def test_associated_data_binds_ciphertext_to_record() -> None:
    key = new_key()
    envelope = encrypt(key, b"payload", record_aad("record-1"))
    with pytest.raises(VaultCryptoError):
        decrypt(key, envelope, record_aad("record-2"))


def test_wrong_key_and_tampered_ciphertext_fail_closed() -> None:
    key = new_key()
    envelope = encrypt(key, b"payload", dek_aad())
    with pytest.raises(VaultCryptoError):
        decrypt(new_key(), envelope, dek_aad())
    tampered = dict(envelope)
    raw = bytearray(b64decode(tampered["ciphertext"]))
    raw[0] ^= 0x01
    tampered["ciphertext"] = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(VaultCryptoError):
        decrypt(key, tampered, dek_aad())


def test_kdf_params_reject_weak_settings() -> None:
    with pytest.raises(VaultCryptoError):
        KdfParams.create(memory_cost=1024, time_cost=0, parallelism=2).validate()
    params = KdfParams.create(memory_cost=8192, time_cost=1, parallelism=1)
    assert params.derive("password") == params.derive("password")


# ------------------------------------------------------------------- lifecycle


def test_uninitialized_vault_rejects_secret_access(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    assert vault.status()["exists"] is False
    with pytest.raises(VaultError):
        vault.list_secrets()


def test_initialize_is_idempotent_only_once(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    status = vault.initialize(PASSWORD)
    assert status["kdf"]["algorithm"] == "argon2id"
    assert vault.unlocked is True
    with pytest.raises(ConflictError):
        vault.initialize(PASSWORD)


def test_short_or_padded_passwords_are_rejected(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    with pytest.raises(ValidationFailure):
        vault.initialize("short")
    with pytest.raises(ValidationFailure):
        vault.initialize(" padded password value ")


def test_unlock_and_lock_lifecycle(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    vault.lock()
    assert vault.unlocked is False
    with pytest.raises(VaultLocked):
        vault.list_secrets()
    with pytest.raises(VaultError):
        vault.unlock("wrong password entirely")
    assert vault.unlock(PASSWORD)["unlocked"] is True


def test_password_change_invalidates_old_password(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    vault.create_secret(name="token", kind="generic", value={"value": "secret-value"})
    vault.change_password(PASSWORD, "another strong passphrase")
    vault.lock()
    with pytest.raises(VaultError):
        vault.unlock(PASSWORD)
    vault.unlock("another strong passphrase")
    assert vault.list_secrets()[0]["name"] == "token"


def test_data_key_rotation_keeps_records_readable(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    created = vault.create_secret(name="token", kind="generic", value={"value": "secret-value"})
    before = json.loads((tmp_path / "vault.json").read_text())
    vault.rotate_data_key(PASSWORD)
    after = json.loads((tmp_path / "vault.json").read_text())
    assert before["records"][created["id"]]["ciphertext"] != after["records"][created["id"]]["ciphertext"]
    assert vault.get_secret(created["id"]).value == {"value": "secret-value"}


def test_secret_payload_never_contains_plaintext_on_disk(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    vault.create_secret(name="ha", kind="home_assistant_token", value={"token": "plain-text-token-value"})
    raw = (tmp_path / "vault.json").read_text()
    assert "plain-text-token-value" not in raw
    assert PASSWORD not in raw


def test_public_view_masks_secrets(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    record = vault.create_secret(
        name="ha", kind="home_assistant_token", value={"token": "abcdefghijklmnopqrstuvwxyz"}
    )
    assert "abcdefghijklmnopqrstuvwxyz" not in json.dumps(record)
    assert record["masked"].endswith("wxyz") or record["masked"].startswith("abcd")
    assert mask_record("ssh_private_key", {"private_key": "x"}) == "private key stored"


def test_duplicate_names_and_delete(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    created = vault.create_secret(name="token", kind="generic", value={"value": "v"})
    with pytest.raises(ConflictError):
        vault.create_secret(name="token", kind="generic", value={"value": "v"})
    vault.delete_secret(created["id"])
    assert vault.list_secrets() == []
    with pytest.raises(NotFoundError):
        vault.delete_secret(created["id"])


def test_record_metadata_update_and_rotation(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    created = vault.create_secret(name="token", kind="generic", value={"value": "old"})
    updated = vault.update_secret(created["id"], value={"value": "new"}, metadata={"owner": "ops"})
    assert updated["metadata"] == {"owner": "ops"}
    assert vault.get_secret(created["id"]).value == {"value": "new"}


def test_auto_unlock_key_file_permissions_and_disable(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    vault.enable_auto_unlock()
    key_path = tmp_path / "unlock.key"
    assert key_path.exists()
    assert key_path.stat().st_mode & 0o077 == 0
    vault.lock()
    assert vault.unlock_with_key_file() is True
    vault.disable_auto_unlock()
    assert not key_path.exists()


def test_export_import_restores_locked_vault(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    source = make_vault(tmp_path / "source")
    source.initialize(PASSWORD)
    source.create_secret(name="token", kind="generic", value={"value": "keep-me"})
    exported = source.export_encrypted()
    assert b"keep-me" not in exported

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_vault(target_dir)
    result = target.restore_encrypted(exported, PASSWORD)
    assert result["exists"] is True
    assert result["unlocked"] is False
    with pytest.raises(VaultError):
        target.list_secrets()
    target.unlock(PASSWORD)
    assert target.list_secrets()[0]["name"] == "token"


def test_import_rejects_wrong_password_and_garbage(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    source = make_vault(tmp_path / "source")
    source.initialize(PASSWORD)
    exported = source.export_encrypted()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_vault(target_dir)
    with pytest.raises(VaultError):
        target.restore_encrypted(exported, "a different strong password")
    with pytest.raises(VaultError):
        target.restore_encrypted(b"not-a-vault", PASSWORD)


def test_corrupted_vault_file_is_detected(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.initialize(PASSWORD)
    vault.create_secret(name="token", kind="generic", value={"value": "v"})
    path = tmp_path / "vault.json"
    document = json.loads(path.read_text())
    document["records"][next(iter(document["records"]))]["ciphertext"] = "AAAA"
    path.write_text(json.dumps(document))
    fresh = make_vault(tmp_path)
    with pytest.raises(VaultError):
        fresh.unlock(PASSWORD)


def test_legacy_vault_can_be_unlocked_again_after_migration(tmp_path: Path) -> None:
    """A v0 migration must re-wrap the DEK with v1 associated data."""

    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from gateway.vault.crypto import VAULT_FORMAT, b64encode, encode_json

    def legacy_encrypt(key: bytes, plaintext: bytes) -> dict[str, str]:
        nonce = os.urandom(12)
        return {
            "nonce": b64encode(nonce),
            "ciphertext": b64encode(AESGCM(key).encrypt(nonce, plaintext, None)),
        }

    kdf = KdfParams.create(memory_cost=1024, time_cost=1, parallelism=1)
    kek = kdf.derive(PASSWORD)
    dek = new_key()
    record_id = "a" * 32
    payload = {
        "name": "legacy token",
        "kind": "home_assistant_token",
        "value": {"token": "synthetic-legacy-token"},
        "metadata": {},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    document = {
        "format": VAULT_FORMAT,
        "version": 0,
        "kdf": kdf.to_dict(),
        "wrapped_dek": legacy_encrypt(kek, dek),
        "secrets": {record_id: legacy_encrypt(dek, encode_json(payload))},
        "meta": {"created_at": "2026-01-01T00:00:00Z"},
    }
    path = tmp_path / "vault.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    vault = make_vault(tmp_path)

    vault.unlock(PASSWORD)
    assert vault.get_secret(record_id).value == {"token": "synthetic-legacy-token"}
    vault.lock()
    vault.unlock(PASSWORD)
    assert vault.get_secret(record_id).value == {"token": "synthetic-legacy-token"}
