# Security model

This document describes what the gateway actually enforces in this repository,
where each control lives, and what it does **not** protect against. It is a
design description, not a security audit. No third-party audit has been
performed.

## Trust boundaries

```
 Home Assistant Supervisor (trusted)
   │  Ingress proxy, peer 172.30.32.2, strips inbound X-Remote-* headers
   ▼
 Management app :8099 ── admin gate ── vault ── policy engine ── connectors
                              │
 Agent client (semi-trusted)  │
   │  Bearer token, peer unknown              │
   ▼                                          ▼
 Agent app :8098 ── token auth ───────────────┴──► HA REST, PVE API, SSH
```

| Principal | Authenticates with | Reaches |
| --- | --- | --- |
| Home Assistant administrator | Ingress headers injected by the Supervisor | Management UI/API on `:8099` only |
| AI agent / script | `Authorization: Bearer hag_…` | Agent REST `/api/v1` and MCP `/mcp` on `:8098` |
| Gateway process | — | Upstream credentials, decrypted only per operation |

The two surfaces share one `Runtime` object, one `PolicyService`, one audit
service, and one vault. There is no second authorization path: MCP tool calls
and REST operations both go through `OperationService.execute`
(`src/gateway/operations.py`).

## Enforced controls

| Control | Implementation |
| --- | --- |
| Management app is unreachable except via Ingress | `AdminGateMiddleware` requires peer `172.30.32.2` (configurable via `HAG_ADMIN_ALLOWED_IPS`) and a well-formed `X-Remote-User-Id`; optional `admin_user_ids` allowlist |
| CSRF / cross-site protection | Unsafe `/api/` requests need `X-Gateway-Request: 1`; `Origin` must match `Host`; `Sec-Fetch-Site: cross-site` is rejected |
| Agent authentication | 256-bit random token, stored as a SHA-256 digest only, shown once, prefix retained for display |
| Token handling | Missing, duplicate, or non-Bearer headers rejected; tokens in query strings rejected with `400 token_in_query`; auth failures rate-limited per peer |
| Deny by default | A policy document with no matching allow rule denies; unknown fields, unknown constraints, and malformed documents are rejected and the previous policy is kept |
| Least privilege | Scopes (`ha:read`, `ha:write`, `proxmox:read`, `ssh:read`, `ssh:write`, `audit:read`) and per-rule target filters (`entities`, `domains`, `nodes`, `vms`, `ssh_paths`, …) |
| Approvals | Required for every non-read tool; bound to agent, tool, connection, canonicalized parameters, policy revision, and tool schema; single use; expiring |
| Revalidation | Agent state, emergency switch, and policy are re-evaluated immediately before the sensitive call |
| Credential isolation | `CredentialVault` never returns secret values over HTTP; agent routes expose only name/type/status |
| Redaction | `redact_value`/`redact_text` applied to tool results, audit parameters, and admin action parameters; admin audit drops credential payload keys outright |
| TLS verification | `verify_tls: false` is rejected with `422`; custom CAs are supported through a vault-stored CA secret |
| SSH host keys | Trust is required before connecting; keys are written to a `0600` known-hosts file; replacing a trusted key requires `replace=true` and an explicit UI confirmation |
| No shell strings | SSH templates are argv vectors with enumerated parameter allowlists, joined with `shlex.join`; no `shell=True`, no agent string interpolation |
| Blocked HA domains | `shell_command`, `python_script`, `hassio`, `homeassistant`, `system_log`, `recorder` are refused even if a policy allows them |
| Response bounds | Per-call timeouts, output byte ceilings, and body-size limits; oversized results fail the operation instead of truncating silently |
| Emergency stop | `emergency_disabled` rejects every agent request with `503`; management access is unaffected |

## Credential handling

* Vault file: `/data/vault.json`, format `hag-vault` version 1.
* AES-256-GCM per record, unique 96-bit nonce, AAD bound to format version and
  record id; the DEK is wrapped with an Argon2id-derived KEK.
* Argon2id defaults: 64 MiB, time cost 3, parallelism 2 (lowered only in tests).
* Auto-unlock stores `data/unlock.key` (0600) next to the vault. This trades
  at-rest protection for unattended restarts and is off by default.
* Secrets are only decrypted inside the operation handler that needs them and
  are never written to logs, audit rows, error messages, or HTTP responses.

## Known limitations

1. **A fully compromised Home Assistant host can read runtime memory** and
   therefore the decrypted credentials and the master password while the vault
   is unlocked. Encryption at rest is not runtime isolation.
2. **Administrators are trusted.** Anyone who can open the panel can change
   policies, approve operations, replace SSH host keys, enable auto-unlock, or
   disable the gateway. Use `admin_user_ids` to limit who that is.
3. **A peer that can reach `:8098` can attempt authentication.** Keep the port
   unpublished, use a VPN or TLS terminator, and rely on token secrecy.
4. **Agent tokens are bearer credentials.** Anyone who obtains one can act as
   that agent until it is revoked. Rotate on suspicion.
5. **Approvals are only as good as the reviewer.** The panel shows the exact
   canonical parameters, but the human must read them.
6. **The gateway trusts the Home Assistant API and the Proxmox API to enforce
   their own permissions**; it applies its own policy first but does not model
   every upstream ACL.
7. **No SSRF allowlist on connection URLs.** An administrator can point a
   connection at any reachable host. This is an admin-level decision, not an
   agent capability.
8. **Not hardened against local root.** `run.sh` starts as root only to fix
   `/data` ownership and immediately drops to uid 1000.
9. **No security audit, no penetration test, no fuzzing.** Treat this as
   experimental software.

## Reporting

This is a personal project. If you find a problem, open an issue without
including secrets, tokens, or host details.
