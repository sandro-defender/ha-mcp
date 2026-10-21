# Home Assistant Agent Gateway

A Home Assistant OS add-on that lets AI agents read and (with explicit human
approval) act on Home Assistant, Proxmox VE, and restricted SSH targets
**without ever receiving the underlying credentials**.

Version 0.1.0 · experimental · not security-audited.

## Install

1. In Home Assistant: **Settings → Apps → App store → ⋮ → Repositories**, add
   the URL of this repository.
2. Install **Home Assistant Agent Gateway** and review the permissions shown by
   the Supervisor.
3. Start the add-on and open **Agent Gateway** in the sidebar.
4. Create the vault master password (minimum 12 characters; there is no
   recovery path). Store it in a password manager.
5. Add a credential, then a connection, and use **Test** to verify reachability.
6. Create one agent identity per client and copy its token immediately — it is
   shown once.
7. Only if the agent runs on another machine: open the add-on **Network**
   section and publish `8098`. Leave it unpublished otherwise.

Step-by-step recovery, backup, and networking notes are in
[`docs/VAULT_RECOVERY.md`](../docs/VAULT_RECOVERY.md) and
[`docs/NETWORKING.md`](../docs/NETWORKING.md).

## Options

| Option | Default | Notes |
| --- | --- | --- |
| `log_level` | `info` | `debug`, `info`, `warning`, `error` |
| `admin_user_ids` | `[]` | Home Assistant user IDs allowed to manage the gateway; empty means any Home Assistant administrator reaching Ingress |
| `tls_certfile` / `tls_keyfile` | `""` | Enable HTTPS on the **agent** listener; both must be set and readable |
| `audit_retention_days` | `30` | 1–3650; older audit rows and decided approvals are purged |
| `approval_ttl_seconds` | `600` | 30–3600; lifetime of a pending approval |

Additional infrastructure-only environment variables (not secrets):
`HAG_DATA_DIR`, `HAG_INGRESS_PORT`, `HAG_AGENT_PORT`, `HAG_ADMIN_ALLOWED_IPS`,
`HAG_REQUIRE_INGRESS_USER`, `HAG_AGENT_ALLOWED_ORIGINS`,
`HAG_AGENT_ALLOWED_HOSTS`, `HAG_HOSTNAME`.

## Ports and data

* `8099/tcp` — management UI and API, reachable only through Ingress.
* `8098/tcp` — agent REST `/api/v1` and MCP `/mcp`; **not published** by
  default (`ports: 8098/tcp: null`).
* `/data` — encrypted vault, SQLite state, SSH known hosts, options.
* `/ssl` — mounted read-only for optional TLS certificates.

## Security posture

* Credentials are encrypted with AES-256-GCM under an Argon2id-derived key and
  are never returned to agents, logs, audit rows, or error messages.
* Authorization is deny-by-default and identical for REST and MCP.
* Every write operation needs a single-use, expiring approval bound to the exact
  operation and parameters; agents have no approval endpoint.
* Upstream TLS verification and SSH host-key verification are always on; there
  is no disable switch.
* Arbitrary SSH commands are not available; only allowlisted service checks,
  allowlisted log reads, and administrator-defined argv templates.
* Runs as an unprivileged user with a single-profile AppArmor policy.

A fully compromised Home Assistant host can inspect runtime credentials. Read
[`docs/SECURITY.md`](../docs/SECURITY.md) before enabling write access.

## Support

This is an experimental project without a support commitment. Issues are
welcome; include the add-on version, the relevant redacted log lines, and the
exact request shape (never a token).
