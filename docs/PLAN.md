# Implementation plan and risk register

Status: implemented in phases on branch `arena/49be020f-ha-mcp`.
This file records the plan that was followed, the decisions taken, and what
has actually been verified. It is not a security audit.

## Verified upstream requirements (checked 2026-10-21)

Sources:

- Home Assistant app configuration reference:
  <https://contributors.home-assistant.io/apps/reference/configuration/>
- Home Assistant ingress guide:
  <https://contributors.home-assistant.io/apps/guides/ingress/>
- Home Assistant app security guide:
  <https://contributors.home-assistant.io/apps/guides/security/>
- Model Context Protocol specification 2026-07-28:
  <https://modelcontextprotocol.io/specification/2026-07-28>
- MCP Python SDK 2.3.0 (PyPI, pinned in `agent_gateway/pyproject.toml`)

Key facts used:

- `config.yaml` requires `name`, `version`, `slug`, `description`, `arch`.
- Ingress uses `ingress: true`, `ingress_port`, `panel_icon`, `panel_title`,
  `panel_admin`. Ingress traffic arrives from the Supervisor at
  `172.30.32.2`; the Supervisor sets `X-Remote-User-Id`,
  `X-Remote-User-Name`, and `X-Remote-User-Display-Name` on ingress
  requests and strips client-supplied copies.
- Relative URLs are required inside an ingress panel. The panel path changes
  per installation.
- An app with `ingress_port: 0` may not publish ports between 62000 and
  65500. This project uses fixed ports instead.
- `init: false` is required for images based on the Home Assistant base
  images (s6-overlay v3).
- Protection mode is on by default; this app requests no privileged mode,
  no Docker socket, no host network, no host PIDs, no broad mounts, and no
  Supervisor API roles.
- MCP 2026-07-28 is JSON-RPC 2.0 over a stateless, self-contained Streamable
  HTTP request. The Python SDK 2.3.0 supports this era and the older
  initialize handshake.

## Stack decision

- Python 3.11+ (`python3` from Alpine on `ghcr.io/home-assistant/base:3.24`;
  the image's exact Python version was not verified here — no Docker daemon).
- FastAPI/Starlette for the management (Ingress) and agent HTTP surfaces.
- MCP Python SDK for the MCP endpoint. The same tool registry and operation
  service back both REST and MCP.
- `cryptography` AES-256-GCM for the vault; `argon2-cffi` Argon2id for the
  master-password KDF. No custom cryptography.
- `asyncssh` for SSH and SFTP; `httpx` for Home Assistant and Proxmox HTTP.
- SQLite (Python standard library) for agents, policies, approvals, audit,
  operations, and settings.
- Vanilla HTML/CSS/JS for the Ingress panel: small, auditable, no build
  toolchain, no secrets in browser storage.

Rationale is expanded in `docs/DEVELOPMENT.md`.

## Phases

| Phase | Scope | State |
|---|---|---|
| 0 | Repository discovery, upstream research, plan, risk register | Done |
| 1 | Add-on skeleton, packaging, health, logging, AppArmor, CI | Implemented; image build not run locally (no Docker daemon) |
| 2 | Encrypted vault, unlock lifecycle, credentials API/UI | Implemented and unit-tested |
| 3 | Agent tokens, scopes, rate limits, policy engine | Implemented and unit-tested |
| 4 | Home Assistant connector | Implemented with mocked HTTP tests; no live HA instance tested |
| 5 | Proxmox API connector (read-only) | Implemented with mocked HTTP tests; no live Proxmox tested |
| 6 | Restricted SSH/SFTP connector, host-key trust | Implemented; tested against an in-process AsyncSSH server |
| 7 | REST API + MCP endpoint, shared authorization | Implemented; REST/MCP parity tested |
| 8 | Approvals and audit | Implemented; expiry, replay, binding tested |
| 9 | Ingress management UI | Implemented; JS syntax-checked, no browser E2E test |
| 10 | Windows connectivity and docs | Documented; no Windows/HA OS hardware test |
| 11 | Quality gates | Tests, Ruff, mypy run locally; Docker builds are CI-only |

## Security boundaries

1. Two ASGI apps, two listeners:
   - management app on `8099` (not published; Supervisor ingress only),
   - agent app on `8098` (published only after the operator enables the host
     port mapping; disabled by default).
2. Management requests must originate from the Supervisor address and carry
   a trusted ingress user header. State-changing management requests also
   require a custom header and same-origin `Origin` when present.
3. Agent requests require `Authorization: Bearer <token>`. Query-string
   tokens are ignored and rejected.
4. The vault is unlocked only when a human supplies the master password, or
   when the operator deliberately enables auto-unlock. The auto-unlock key
   lives in a separate file in `/data`; theft of both files defeats vault
   confidentiality at rest. This is documented, not hidden.
5. Secrets are decrypted inside the backend only for the lifetime of an
   operation. They are never placed in agent responses, tool schemas, logs,
   audit rows, URLs, or browser storage.
6. Every tool call is evaluated by one policy service. Deny rules win; no
   matching rule means deny. Write and destructive tools always require an
   approval, even if a policy rule sets `require_approval: false`.
7. Approvals are single-use, expire, and hash-bind the agent, tool,
   connection, canonical parameters, and policy revision. Approval is a
   management-realm action; an agent token cannot reach it.
8. Policy is re-evaluated immediately before execution.

## Risk register

| Risk | Mitigation | Residual risk |
|---|---|---|
| Master password brute force | Argon2id, per-vault salt, documented parameters | Weak user passwords remain weak; no password quality enforcement beyond length |
| Theft of `/data` | AES-256-GCM; key file separate | Auto-unlock key in `/data` allows offline decryption if both files are taken. Host admin can read runtime memory. |
| Token theft from agent host | Per-agent revocation, scopes, rate limits, audit | A stolen live token can act until revoked |
| Prompt injection tricks agent into destructive action | Writes disabled by default; approval required; resource allowlists; bounded outputs | A human can still approve a bad action; approval UI shows exact parameters but reviewer error is possible |
| SSRF through connection base URLs | Connection creation is management-only; URL is explicit admin input; no agent-controlled URL | An admin can point a connection at an unintended internal service |
| TLS interception / wrong Proxmox host | Certificate verification on by default; custom CA supported; no "skip verification" switch | Compromised CA or host |
| SSH host impersonation | Managed trust store; unknown/changed keys rejected; explicit fingerprint enrollment | TOFU-style first enrollment depends on the human verifying the fingerprint |
| Agent API exposed to LAN | Host port disabled by default; bearer auth; origin checks; docs recommend VPN/TLS | Plain HTTP if the operator enables the port without a TLS terminator |
| Log leakage | Structured minimal logs, redaction filter, no request headers/bodies, audit summary truncation | Output values from HA/Proxmox may contain user data; treat audit as sensitive |
| MCP session/credential confusion | Stateless MCP, bearer checked on every request, tool calls re-authenticate | SDK protocol edge cases; only tested with the pinned SDK |
| SQLite write contention | WAL, short transactions, lock; single process | Not designed for multi-replica deployment |
| Add-on image supply chain | Pinned direct dependencies, official base image, CI build, no build secrets | Transitive dependencies are not hash-pinned in this release |

## What is intentionally not implemented

- No Home Assistant restart/stop tools.
- No Proxmox delete, backup deletion, storage mutation, node restart, or
  VM power operations. The connector is read-only in this release.
- No arbitrary SSH shell or command string. Only allowlisted service checks,
  allowlisted log reads over SFTP, and administrator-defined argv templates.
- No Home Assistant log reader: there is no least-privilege documented REST
  endpoint available to this add-on without broad Supervisor API access.
- No Home Assistant configuration-file access.
- No Windows bridge process. The documented Windows path is a direct
  connection to the Home Assistant host over LAN/VPN or a TLS reverse proxy.
- No OAuth authorization server for MCP; static per-agent bearer tokens are
  required. MCP clients that cannot send a custom `Authorization` header are
  out of scope.

## Verification status

Local verification performed in this environment (2026-10-22):

- `pytest agent_gateway/tests` — 207 tests passing, covering vault crypto and
  lifecycle, policy evaluation, approvals, redaction, settings parsing, admin
  gate/CSRF/origin checks and body limits, agent auth throttling, credential and
  connection validation, Home Assistant and Proxmox VE connectors against mocked
  upstreams, the agent approval round trip, MCP JSON-RPC sessions, SSH host-key
  trust, and static UI accessibility invariants.
- `ruff check agent_gateway/src agent_gateway/tests` — all checks passed;
  `ruff format --check` — 60 files formatted.
- `mypy src` (strict) — no issues in 45 source files.
- `node --check` on the panel JavaScript.
- ASGI smoke test against both apps: `/healthz`, Ingress gate (401 without the
  Supervisor header), vault initialize/unlock, agent 401 without a token.
- Optional jsdom render of the management panel against a stubbed API
  (`tools/ui-smoke/`), covering all sections, the setup/unlock screens, dialogs,
  approvals, and policy validation. Not a browser and not a visual audit.
- Live process run with uvicorn on loopback ports: vault setup, credential and
  connection creation, connection test, REST read, the 202 -> approve -> retry
  write flow, agent approve attempt rejected (404), audit writes, and MCP
  initialize/tools/list over HTTP. The upstream Home Assistant was a local
  stand-in server; no real device was contacted.

Behavioural notes recorded during implementation:

- Domain validation failures use HTTP `422` with code `validation_error`;
  an uninitialized vault returns `409 vault_not_initialized`, a locked vault
  returns `423 vault_locked`.
- The agent tool catalogue follows scopes and enabled tool groups; per-call
  authorization is still decided by the policy, so a listed tool can be denied.
- SSH log reads use SFTP with an allowlist; `ssh.read_log` is the only log path
  in this release (`ha.get_logs` is not implemented).
- The management panel uses system font stacks rather than the design-system's
  Google Fonts, because Ingress is served offline under a strict CSP.

Not verified here:

- Docker image build and runtime (`docker` is not installed in this
  sandbox). CI builds `linux/amd64`; `aarch64` is declared but not built.
- Installation on a real Home Assistant OS machine, Ingress rendering,
  Supervisor headers, and watchdog behavior.
- Live Home Assistant, Proxmox VE, or external SSH servers. (A local stand-in
  HTTP server was used for one end-to-end read and write.)
- Windows client connectivity.
- A real browser, screen reader, or visual comparison. The panel is verified by
  static assertions and a jsdom smoke run only.
- An external MCP client. The endpoint is exercised in-process.
- A security audit. None has been performed.
