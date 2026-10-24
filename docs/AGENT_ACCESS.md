# Temporary agent access (introduced in 0.4.0; hardened in 0.4.2)

This document describes the broker that issues short-lived, tightly scoped
credentials to coding agents. It explains the two credential layers, the
lifecycle, the exact HTTP/MCP endpoints, the policy guarantees, the SSH
restrictions, and what is still unverified.

Nothing here replaces the main security model: **upstream credentials never
leave the gateway**, and every operation is still authorized by the central
policy engine and, for writes, a human approval.

---

## 1. Two credential layers

| Layer | What it is | Where it lives | Who can read it |
| --- | --- | --- | --- |
| **A — upstream** | Home Assistant long-lived token, Proxmox API token/secret, SSH private key/passphrase. The SSH username is non-secret connection metadata, separate from the key. | Secret values in the encrypted vault (`/data/vault.json`, AES-256-GCM, Argon2id-derived key); connection metadata in `/data/gateway.db`; optional `/data/unlock.key` for auto-unlock | Secret values are available only to the gateway process and are never returned by APIs, MCP, audit, logs, or Agent Access. Administrators can manage non-secret connection metadata such as the SSH username. |
| **B — agent** | `hag_…` bearer token for one temporary session | SHA-256 digest in `/data/gateway.db` (`agents.token_hash`); plaintext exists only in the redeem response | The agent that redeemed the handoff code. The administrator sees only the prefix and status. |

Layer B tokens are generated with `secrets.token_urlsafe(32)` (256 bits of
entropy) and are **not derived** from upstream credentials, from the session
name, or from each other. Revoking a session overwrites its digest with a new
random digest and sets `enabled=0`, so a leaked database row cannot be replayed
as a token.

Sessions are stored in the existing `agents` table (`kind='temporary'`), use the
existing bearer authentication middleware, and are authorized by the existing
policy engine. No parallel auth mechanism was added.

---

## 2. Lifecycle

```text
admin: generate  ──►  session (pending) + single-use handoff link
                          │
agent: open /agent#code=… │  (code never sent to the server; JS strips it)
                          ▼
                      POST /agent/api/redeem  ──►  token (once) + scope summary
                          │
       agent uses REST/MCP │  (Authorization: Bearer <token>)
                          ▼
expiry / revoke / restart ──► 401 immediately, every surface
```

1. **Generate** — the recommended **Connect an AI agent** form lets an
   administrator choose connections, read-only versus read/control, and a
   lifetime; it derives the existing scopes automatically. An advanced form
   still accepts individual scopes and operation classes. The gateway creates
   the session record, server-side policy document, and handoff code.
2. **Handoff** — the response contains a link such as
   `http://homeassistant.local:8098/agent#code=hag_h_…`. The fragment holds a
   one-time **code**, never the token. Handoff codes are stored as SHA-256
   digests, expire after 15 minutes (or the session lifetime, whichever is
   shorter), are single-use, and are deleted when the session is revoked.
3. **Redeem** — the agent opens the link. The page strips the fragment from the
   address bar before sending `POST /agent/api/redeem` (JSON, same-origin
   only). The gateway consumes the code atomically and *then* mints the token,
   returning it once. Concurrent redeems of the same code produce exactly one
   success (tested).
4. **Use** — REST (`/api/v1/*`) and MCP (`/mcp`) accept the token in the
   `Authorization: Bearer` header only. Query-string tokens are rejected with
   `400 token_in_query`.
5. **Expire / revoke** — expiry is checked on every request. Revocation is
   immediate, idempotent, and also expires that session's pending approvals.
6. **Restart** — see section 5.

The guided handoff dialog also creates an editable **AI setup prompt**. It
contains the single-use handoff URL, MCP/API addresses, verification steps, and
an instruction to summarize access and ask for the user's first task. It does
not contain an upstream credential or a redeemed bearer token. The user must
review and copy it explicitly; the gateway never sends it to an AI provider.
Because the prompt carries a live handoff code until redemption, it must be
shared only with the intended agent before expiry. The prompt is shown before
the session-list refresh so a refresh failure cannot hide the issuance response.

### Restart policy

`agent_sessions_revoke_on_restart` (default **on**) makes startup:

1. revoke every temporary session,
2. expire their pending approvals,
3. purge all handoff codes,
4. increment a persisted **session epoch** (`settings.session_epoch`).

Temporary sessions carry the epoch they were created in; a mismatch makes
authentication fail even if a row were somehow left enabled. This means a
restart can never resurrect a stale token, and no upstream credential is
rotated, deleted, or re-displayed. Administrator-managed (persistent) agent
identities are unaffected. With the policy off, already-redeemed temporary sessions survive a restart,
which is a deliberate administrator choice. Unredeemed handoff codes are still
purged at startup; issue a new session if a pending link was lost.

The Agent Access page shows the result of the last startup
(`policy`, `revoked`, `session_epoch`).

---

## 3. Connection details (verified paths)

| Item | Value |
| --- | --- |
| Agent listener | `http://<host>:8098` (or `https://` when `agent_tls` is configured) |
| Handoff page | `GET /agent` |
| Redeem | `POST /agent/api/redeem` with `{"code": "hag_h_…"}` |
| Session status | `GET /agent/api/session` (requires the bearer token) |
| REST base | `/api/v1` |
| Health | `GET /api/v1/health` |
| Connections | `GET /api/v1/connections` |
| Tools | `GET /api/v1/tools` |
| Operations | `POST /api/v1/operations` |
| Approval status | `GET /api/v1/approvals/{approval_id}` |
| Journal | `/api/v1/journal/*` (needs `journal:*` scopes **and** matching policy rules) |
| MCP | `POST /mcp` (and `/mcp/`), Streamable HTTP, same bearer token |
| Auth header | `Authorization: Bearer <token>` |

Example (replace host and token):

```bash
curl -sS -H "Authorization: Bearer $HAG_AGENT_TOKEN" \
  http://homeassistant.local:8098/api/v1/health
```

```jsonc
// Generic MCP client entry — the gateway does not invent a client dialect.
{
  "url": "http://homeassistant.local:8098/mcp",
  "headers": { "Authorization": "Bearer <session token>" }
}
```

The Agent Access page generates this JSON from the actual configuration; it
contains only the gateway token and URL. It never contains upstream secrets.

> `pageofha://agent` is **not** a browser URL. It is not used anywhere in this
> release; a custom URI scheme could only be added as an optional future
> enhancement.

---

## 4. Effective permissions

A temporary session is **narrower** than a persistent agent identity:

- `allowed_connections` — exactly the connections chosen at issuance. A call
  against any other connection is denied before the connector runs, and
  `GET /api/v1/connections` hides unauthorized connection metadata.
- `operation_classes` — `read` is mandatory; `write` is optional; `destructive`
  **cannot** be granted (the request is rejected).
- `scopes` — must match the selected connection types (`ha:*` requires a Home
  Assistant connection, `proxmox:*` a Proxmox connection, `ssh:*` an SSH
  connection). Journal scopes are connectionless. Global `audit:read` cannot be
  issued to a temporary session; it is reserved for persistent
  administrator-managed identities and enforced again at the audit endpoint.
- **Policy** — a server-generated document containing only the tools that the
  chosen scopes/classes need. Write rules carry `require_approval: true` plus a
  10 requests/minute limit. The document is stored in the existing `policies`
  table and cannot be edited through the session API (the regular policy editor
  still applies to administrators, but it cannot *widen* the session
  allowlists).
- **Approvals** — every non-read tool call still creates a single-use approval
  bound to the exact tool, connection, parameters, and policy revision. A
  temporary session never bypasses that.
- **Fail closed** — missing/ambiguous session data (bad JSON, empty allowlist,
  stale epoch, unknown token) denies access.

The server derives all of this from the database row. Client-supplied roles,
scopes, or connection ids in a request body are never trusted; a mismatching
`connection_id` is denied.

---

## 5. Security properties and threat model

| Threat | Mitigation |
| --- | --- |
| Token theft / replay | 256-bit token, digest-only storage, `no-store` responses, no URLs/query/history, immediate revocation, expiry |
| Revoked or stale token | `enabled=0` + digest overwrite + epoch check; startup revocation persisted |
| Cross-session access | Per-session connection allowlist, filtered connection catalog, and policy; IDs are server-derived |
| Privilege escalation via parameters | Per-tool pydantic schemas, `additionalProperties: false`, target constraints, hard-blocked HA domains, approval hash binds parameters |
| Upstream secret discovery | Secrets stay in the vault; tool results are redacted; agent API never returns credentials; tests grep responses for sentinel secrets |
| Bypassing the gateway | Agents have no route to HA/Proxmox/SSH; the gateway is the only holder of upstream credentials |
| Unauthorized session creation | Ingress-only admin app, verified `X-Remote-User-Id`, peer allowlist, CSRF header, optional admin allowlist, rate limit |
| Session fixation / reuse | Handoff codes are single-use, hashed, 15-minute TTL, purged on revoke |
| Leakage in logs/errors/audit | Access log records method/path/status only; audit records tool/decision, no headers/bodies; errors are structured and redacted |
| MCP/HTTP inconsistency | Both surfaces call the same operation pipeline and policy engine; tests assert parity |
| Approval bypass | Approvals are bound to the request hash and consumed once; session revocation expires pending approvals |
| SSH injection / target manipulation | Fixed `argv` (no shell), allowlisted executables/paths, host-key verification, timeouts and output caps |
| Post-restart reuse | Epoch bump + persisted revocation; no in-memory cache |
| DoS / oversized responses | Body size limits, output caps, per-tool rate limits, redeem attempt limit (20/min per peer), auth-failure limit (10/min) |
| Unauthenticated agent page | The page shows no data until a valid code is redeemed; `/agent/api/session` requires bearer auth; CSP `default-src 'none'`, `frame-ancestors 'none'`, `no-referrer`, `no-store` |
| CSRF on admin actions | `X-Gateway-Request: 1` required on unsafe `/api/admin/*`; same-origin checks; typed confirmation for revoke-all |
| Browser storage of secrets | No cookies/localStorage/sessionStorage/IndexedDB; the token lives in page memory only and is lost on reload |

### SSH

The preferred and implemented architecture is **gateway-held SSH credentials**:
the gateway holds the key, and agents ask for a bounded action
(`ssh.service_status`, `ssh.read_log`, `ssh.run_command_template`). The command
templates are administrator-defined `argv` arrays — no shell interpolation — and
are constrained by host, executable, arguments, timeout, and output size.
The gateway does **not** pretend that a wrapped upstream SSH credential is
temporary, and it does not create ephemeral Unix accounts or install keys.
Genuinely ephemeral SSH access would require a supported upstream mechanism
(short-lived certificates, an SSH CA, or an external broker) and is not
implemented. Creating accounts, editing `sshd_config`, enabling root login, or
installing keys on production hosts requires explicit administrator approval
and a documented rollback; nothing in this release does it automatically.

---

## 6. What the pages show

**Agent Access (management panel, Ingress `/` → "Agent Access")**

- endpoint/reachability summary (loopback listener probe, configured base URL,
  MCP and REST paths, TLS state),
- default/maximum lifetime, restart policy, and last startup result,
- active and pending sessions with id, name, status, scopes, operation classes,
  connections, created/expiry/remaining lifetime, token prefix, last use, and
  request count for the last hour,
- controls: generate, revoke one, revoke all (typed confirmation), per-session
  activity, settings,
- copyable handoff link and connection instructions derived from real values.

**Handoff page (`/agent`, agent listener)**

- one-time token, gateway base URL, REST and MCP endpoints, authorization
  header format, expiry/remaining lifetime, scopes, operation classes, and
  connection names/types,
- copy buttons and a curl example, a status check, and a manual link input for
  when the fragment was lost,
- an explicit warning that the token is shown once and is not stored by the
  page.

Neither page ever displays an upstream credential, another session's data, or
a token after it has been consumed.

---

## 7. Recovery and operations

- **Lost handoff link** — generate a new session (this revokes previous
  temporary sessions by default). An unused link cannot be re-displayed.
- **Lost token** — revoke the session; the agent needs a new handoff.
- **Suspected leak** — "Revoke all sessions"; check the audit log; rotate the
  upstream credentials in the vault if you believe they were exposed (the
  broker never exposes them, so this is normally unnecessary).
- **Restart** — with the default policy, every temporary session is revoked at
  startup; agents re-request access. Upstream credentials are untouched.
- **Agent page unreachable** — check that port 8098 is mapped in the add-on
  configuration, that the URL uses the agent listener (not Ingress), and that
  `agent_api_base_url` matches the address the agent can reach. External
  NAT/DNS/firewall reachability cannot be verified from inside the container.
- **All requests 401** — token expired/revoked, or the epoch changed after a
  restart. Issue a new session.

---

## 8. Verification status

Current 0.4.2 audit evidence:

- full pytest suite (**354 passed**), including issuance, single-use and
  concurrent redemption, scope/connection confinement, filtered discovery,
  global-audit denial, expiry, revocation, stale-epoch rejection, and restart
  behavior with successful upstream-vault decryption afterward;
- `ruff check`, `ruff format --check`, and strict `mypy src`;
- process-level `tools/startup-smoke/smoke.py` (both listeners, health,
  readiness, two SIGTERM/restart cycles);
- five jsdom harnesses, including Agent Access, handoff, and changelog states;
- a Python wheel build.

Not run locally:

- Docker image build/boot or real AppArmor enforcement (no local daemon/tools;
  the GitHub Actions image job is the required gate),
- Home Assistant OS / Supervisor installation,
- live Home Assistant, Proxmox, or SSH calls,
- a third-party MCP client or a real browser session (jsdom only).
