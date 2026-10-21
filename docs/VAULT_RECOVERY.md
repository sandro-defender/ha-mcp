# Vault format, backup, and recovery

## What is stored where

| Path | Contents | Permissions |
| --- | --- | --- |
| `/data/vault.json` | Encrypted records, KDF parameters, wrapped DEK | `0600`, directory `0700` |
| `/data/vault.json.bak` | Previous good copy written on each save | `0600` |
| `/data/unlock.key` | Auto-unlock key (only when enabled) | `0600` |
| `/data/gateway.db` | Agents, policies, connections (no secrets), approvals, audit, operations | created by SQLite |
| `/data/ssh_known_hosts` | Trusted SSH host keys | `0600` |
| `/data/options.json` | Supervisor-managed add-on options (no secrets) | Supervisor |

## Format (version 1)

```json
{
  "format": "hag-vault",
  "version": 1,
  "kdf": {"algorithm": "argon2id", "salt": "…", "memory_cost": 65536,
          "time_cost": 3, "parallelism": 1, "hash_len": 32},
  "wrapped_dek": {"nonce": "…", "ciphertext": "…"},
  "auto_unlock": {"nonce": "…", "ciphertext": "…"} | null,
  "records": {"<32 hex>": {"nonce": "…", "ciphertext": "…"}},
  "meta": {"created_at": "…", "updated_at": "…"}
}
```

* Every ciphertext is AES-256-GCM.
* The DEK is wrapped with the Argon2id-derived KEK and AAD
  `hag-vault:v1:dek`.
* Each record is encrypted with the DEK and AAD `hag-vault:v1:record:<id>`, so a
  record cannot be moved to another id without failing authentication.
* Version 0 documents (if ever encountered) are migrated on unlock.

## Normal operations

**Lock / unlock** — the panel's Settings → Vault lifecycle. The vault unlocks
into memory only; a restart leaves it locked unless auto-unlock is enabled.

**Change master password** — re-wraps the existing DEK; record ciphertexts do
not change. The old password stops working immediately.

**Rotate data key** — generates a new DEK, re-encrypts every record, rewraps it
with the current password. Requires a typed confirmation in the panel.

**Encrypted export** — `GET /api/admin/vault/export` (or the panel button)
downloads the vault JSON. It is still encrypted with the master password; keep
it away from the password.

**Restore** — `POST /api/admin/vault/import` with
`{"vault_base64": "<file bytes>", "password": "<password of the import>"}`, or
the panel's "Restore export". The current vault is replaced, auto-unlock is
cleared, and the restored vault is left **locked**. The password is validated
against the imported file before anything is written.

## Recovery scenarios

**Forgot the master password** — there is no recovery path. The KEK is derived
from the password only; nothing on disk can decrypt the records. Delete
`vault.json` (with the add-on stopped) to start over, then recreate every
credential. Downstream tokens and keys should be revoked at their source.

**Vault file corrupted** — stop the add-on and try `vault.json.bak`:

```bash
cp /data/vault.json.bak /data/vault.json   # from the host or the container shell
```

Then start the add-on and unlock. If both files are lost, restore an encrypted
export.

**Auto-unlock fails after a restore or key rotation** — expected: imports clear
auto-unlock. Unlock with the master password, then re-enable auto-unlock in
Settings if you accept the trade-off.

**Locked out of the panel** — verify you are opening it from the Home Assistant
sidebar (Ingress). Direct access to `8099` is rejected by design. If
`admin_user_ids` is set, the Home Assistant user must be in that list.

**SSH host key changed** — do not accept it blindly. Confirm the fingerprint on
the server console, then use Settings → Connections → "Host key" → replace.
Replacing requires `replace=true`, which the panel only sends after an explicit
confirmation.

## Backup checklist

1. Take an encrypted vault export and store it separately from the master
   password.
2. Back up the whole `/data` directory (Supervisor snapshots include it) so
   agents, policies, connections, and audit history survive.
3. Verify a restore into a test instance before relying on it.
4. After restoring, treat every agent token as potentially exposed and rotate
   the ones you cannot account for.
