Home Assistant Agent Gateway
A Home Assistant OS add-on that provides a controlled gateway for AI
coding agents and automation tools to work with Home Assistant and
Proxmox VE without placing the underlying API tokens or SSH private keys
in the agent's context.
Project status: implemented at version 0.1.0 and tested locally (unit and
integration-style tests against the real ASGI apps). It has not been installed
on a live Home Assistant OS system, has not been exercised against real Home
Assistant, Proxmox VE, or SSH targets, and has not been security-audited. This
document describes the product and, where it differs from the original plan,
the behaviour of the code in this repository. See "Implementation status" and
"Verification log" below, and `docs/SECURITY.md` for the threat model.

Goals
- Run as a dedicated Home Assistant OS add-on.
- Provide a management UI accessible from the Home Assistant sidebar
  through Ingress.
- Securely store credentials for Home Assistant, Proxmox VE, and SSH
  connections.
- Expose approved operations through an HTTP API and an MCP-compatible
  interface.
- Keep credentials out of agent prompts, tool descriptions, ordinary
  responses, logs, and error messages.
- Apply per-agent authorization, least privilege, operation
  allowlists, approvals, rate limits, and audit logging.
- Support a Windows-based AI coding agent connecting over a trusted
  LAN or VPN.
- Keep the initial release read-only by default and add sensitive
  operations only behind explicit policies.
Non-goals
- Do not expose unrestricted root shell access by default.
- Do not publish the gateway directly to the public internet.
- Do not promise protection from a fully compromised Home Assistant
  host. The running add-on must be able to use its credentials, and
  host administrators can ultimately access its runtime.
- Do not treat encryption at rest as a replacement for access control
  or runtime isolation.
- Do not allow agents to retrieve stored secret values through a
  "list", "debug", or configuration endpoint.
Implementation status (0.1.0)
Implemented and covered by tests:
- Add-on skeleton (config.yaml, build.yaml, Dockerfile, run.sh,
  apparmor.txt, repository.yaml) for amd64 and aarch64, unprivileged
  runtime, Ingress panel, agent listener unpublished by default.
- Encrypted credential vault: AES-256-GCM v1 format, Argon2id KEK,
  wrapped DEK, lock/unlock, password change, data-key rotation,
  encrypted export/import, optional auto-unlock, atomic writes with a
  previous-good backup.
- Per-agent bearer tokens (hash-only storage, scopes, expiry, rotation,
  revocation, shown once), deny-by-default policy engine, approvals that
  are single-use, expiring, and bound to the exact operation, audit log
  with redaction and retention.
- Home Assistant REST, Proxmox VE API read tools, and restricted SSH
  (managed known-hosts, service status, log tail with path allowlists,
  administrator-defined argv templates). 12 tools total, exposed
  identically over REST and MCP.
- Management panel (vanilla JS, no build step, strict CSP) with
  dashboard, connections, credentials, tools, permissions, approvals,
  audit, and settings, including the vault lifecycle.
- Pytest suite (207 tests, including add-on metadata checks), ruff, strict mypy,
  and GitHub Actions CI (lint, types, tests, static UI checks, amd64 image build).

Not implemented or not verified in this release:
- No live deployment, no real Home Assistant/Proxmox/SSH calls, no MCP
  client end-to-end session, no Docker build on the development machine,
  no security audit. `ha.get_logs` is not implemented; SSH log reads are
  the supported log path. Proxmox write operations are intentionally
  absent. Arbitrary SSH commands remain disabled by default and are
  only reachable through administrator-defined templates.

Architecture
Windows PC
  AI coding agent / MCP client / scripts
            |
            | HTTPS over trusted LAN or VPN
            | separate revocable agent credential
            v
Home Assistant Agent Gateway add-on
  - HTTP API and MCP endpoint
  - agent authentication
  - authorization/policy engine
  - approval workflow
  - encrypted credential vault
  - audit log with secret redaction
            |
            +---- Home Assistant REST / WebSocket / Supervisor APIs
            |
            +---- Proxmox VE API
            |
            +---- Restricted SSH connections
Two interfaces
1. Management UI: served through Home Assistant Ingress. Use Home
   Assistant authentication for the management surface. Do not expose
   the management UI on the agent API port unless there is a specific,
   reviewed reason.
2. Agent API: a dedicated port for authenticated HTTP and MCP
   requests. Bind to the add-on's intended network interface only as
   required by Home Assistant's add-on networking model. Document the
   actual reachable address and firewall rules.
127.0.0.1 is relative to the machine/container where it is used. If
the add-on runs on Home Assistant OS and the coding agent runs on
Windows, Windows localhost does not refer to Home Assistant. The
Windows agent must connect to a reachable Home Assistant address over a
trusted network, or use an optional local Windows bridge that forwards
requests securely.
Use HTTPS where traffic crosses an untrusted network. On a trusted LAN,
do not assume the network is safe; authenticate every agent request.
Prefer a VPN for remote access. Never expose the agent port to the
public internet by default.
Management panel
The sidebar panel should include:
- Dashboard: connection health, enabled integrations, active
  agents, recent operations, and security status.
- Connections: configure Home Assistant, Proxmox API, and SSH
  connection profiles.
- Credentials: import or rotate credentials; display masked
  metadata only after saving.
- Tools: enable/disable supported tool groups.
- Permissions: policies by agent, connection, operation, resource,
  and command.
- Approvals: pending high-impact requests with clear target and
  parameters.
- Audit: searchable event history with secrets and sensitive
  headers redacted.
- Settings: listener/network settings, vault state, backups,
  retention, and emergency disable.
Use clear status labels and explain when a feature is disabled or not
configured. Never show a saved secret again by default.
Credential storage
Requirements
- Do not put secrets directly in normal add-on options, YAML
  configuration, source code, frontend bundles, URLs, or logs.
- Store encrypted vault data in persistent add-on storage, such as
  /data, using a versioned format.
- Use authenticated encryption (for example, AES-256-GCM via a
  reputable, maintained library) with unique nonces and appropriate
  key handling.
- Keep the vault encryption key separate from the encrypted vault
  file.
- Support a master-password unlock or an external secret-provider
  integration. If unattended restarts are supported, clearly explain
  the trade-off: a key stored beside the vault provides limited
  protection if both files are obtained.
- Prefer a memory-hard password KDF such as Argon2id, with parameters
  documented and benchmarked for the target hardware, if deriving a
  key from a master password.
- Never invent a custom cryptographic algorithm.
- Clear references to plaintext secrets as soon as practical,
  recognizing that garbage-collected runtimes cannot guarantee secure
  memory erasure.
- Provide encrypted export/import and a documented recovery procedure.
- Rotate/revoke credentials without requiring unrelated configuration
  to change.
- Keep backups encrypted and ensure backup recovery works before
  release.
Runtime handling
Decrypt credentials only inside the backend when a permitted operation
needs them. Never return credential material to the agent. Do not place
secrets in command-line arguments or environment variables unless the
specific mechanism has been reviewed and there is no safer option. Avoid
subprocess shell strings; use argument arrays and fixed executable
paths.
The application cannot prevent a host administrator or a fully
compromised Home Assistant host from inspecting runtime memory or files.
State this limitation plainly.
Connections
Home Assistant
Support only the API scopes required for implemented tools. The add-on
may use a configured Home Assistant token or a documented
Supervisor-provided mechanism where appropriate. Do not silently assume
that Supervisor access implies least privilege.
Initial tools should include:
- ha.get_state
- ha.list_entities (with pagination and limits)
- ha.get_config only for safe, explicitly supported fields
- ha.get_logs only if supported through a documented endpoint and
  output is bounded/redacted
- ha.call_service only when enabled by policy and restricted by
  domain/service allowlists
Avoid broad file-system access. If configuration-file inspection is
implemented later, allowlist exact directories and prevent path
traversal and symlink escapes.
Proxmox VE API
- Use a dedicated Proxmox user and API token with the minimum required
  privileges.
- Keep token ID and secret in the vault.
- Verify TLS certificates by default; do not make "skip TLS
  verification" a normal UI option.
- Set request timeouts and response-size limits.
- Check authorization in the gateway before sending requests, even if
  Proxmox also enforces permissions.
- Initial tools should include:
  - proxmox.list_nodes
  - proxmox.list_vms
  - proxmox.list_containers
  - proxmox.get_vm_status
  - proxmox.get_task_status
- VM start/stop, storage changes, backup deletion, node operations,
  and other state-changing operations should be disabled by default
  and require explicit policies and approval.
SSH
- Use a maintained SSH library with host-key verification.
- Store private keys encrypted in the vault and decrypt only for the
  connection lifetime where practical.
- Require a known-hosts/host-key trust workflow; never blindly accept
  unknown host keys in production.
- Use dedicated restricted server accounts, not root, wherever
  possible.
- Start with predefined operations such as fetching a bounded set of
  logs or checking a known service status.
- Do not expose arbitrary shell execution in the default policy.
- If an administrator deliberately enables arbitrary commands, show a
  high-risk warning and require explicit approval. The policy engine
  must not be bypassable through alternate endpoints.
- Do not forward SSH-agent access to the agent process itself.
HTTP API and MCP
Provide a versioned HTTP API and MCP-compatible tools backed by the same
policy engine. Do not implement two separate authorization systems.
Suggested routes (final names may be adjusted during implementation):
- GET /api/v1/health
- GET /api/v1/connections
- GET /api/v1/tools
- POST /api/v1/operations
- GET /api/v1/operations/{id}
- GET /api/v1/audit (admin only)
- MCP endpoint at a documented path, for example /mcp, using a
  supported MCP transport and protocol version
The health route must not disclose secrets, internal paths, or sensitive
host details. Connection listings should expose only safe metadata such
as display name, type, and status.
Agent authentication
- Require a separate, revocable token per agent.
- Store only a hash of agent bearer tokens when feasible; show a newly
  created token only once.
- Provide token scopes and expiry/rotation where practical.
- Compare secrets safely using maintained library functions.
- Rate-limit authentication failures and sensitive operations.
- Do not put tokens in query strings.
- Redact Authorization, cookies, API tokens, private keys, and known
  secret patterns from logs and responses.
- Enforce authorization for every operation, regardless of whether it
  arrived over REST or MCP.
- Consider origin/host validation and CSRF defenses for browser-facing
  routes. CORS is not authentication.
- Disable unnecessary endpoints and methods.
Authorization and approvals
Use deny-by-default policy evaluation. Policies should support:
- agent identity
- connection profile
- tool/operation
- resource identifiers (entity, VM, node, container)
- permitted service domains and service names
- permitted SSH command templates and arguments
- read versus write operations
- timeouts, rate limits, and output-size limits
- approval requirements
Default policy:
  Operation                           Default
  Read HA entity state                Allow if the agent is authorized
  List Proxmox nodes/VMs/containers   Allow if the agent is authorized
  Read bounded, approved logs         Allow only for configured sources
  Call HA services                    Disabled until allowlisted
  Start/stop VM or container          Approval required
  Restart HA or Proxmox node          Approval required
  Delete VM, storage, or backup       Disabled
  Arbitrary SSH shell                 Disabled
  Read arbitrary files                Disabled
Approvals must bind to the exact action and parameters, expire, and be
single-use. Do not let an agent approve its own high-risk operation
through the same tool identity. Revalidate policy immediately before
execution.
Audit logging
Record at least:
- timestamp
- agent identity
- operation/tool name
- connection profile
- sanitized parameters or parameter summary
- policy decision and approval reference
- success/failure and duration
- safe error category
Do not record secrets, authorization headers, full sensitive payloads,
private keys, or unbounded command output. Apply retention limits and
provide a way to clear logs according to the product's retention policy.
Add-on packaging and hardening
- Build a proper Home Assistant OS add-on repository with a
  repository-level repository.yaml, add-on config.yaml,
  Dockerfile, startup script, source, and documentation.
- Support target architectures relevant to the user's HA hardware;
  declare only architectures actually built and tested.
- Use a maintained minimal base image and pin dependencies/releases
  reproducibly where practical.
- Run as a non-root user if the Home Assistant add-on environment and
  required integrations permit it.
- Use a restrictive AppArmor profile and the minimum required add-on
  privileges.
- Do not use host networking, privileged mode, Docker socket access,
  or broad host mounts unless essential and explicitly justified.
- Persist only required application data.
- Set resource limits where supported and handle shutdown/restart
  cleanly.
- Do not expose debug mode in production.
- Provide health checks and useful but redacted logs.
Testing and release criteria
Automated tests
- Vault encrypt/decrypt, wrong password, corruption, version
  migration, backup/restore, rotation.
- Authentication, token revocation, expired tokens, permission
  denials, rate limits.
- REST and MCP authorization parity.
- Approval expiration, replay prevention, and parameter binding.
- Host-key verification and TLS verification.
- Path traversal and symlink escape attempts for any file features.
- Secret redaction in success results, errors, traces, and audit logs.
- Response-size and timeout enforcement.
- Malformed input and protocol-version handling.
Integration tests
- Home Assistant read-only API operations against a test instance.
- Proxmox API operations against a test/lab environment.
- SSH against a test server with host-key verification.
- Home Assistant OS add-on install, restart, persistence, Ingress UI,
  and upgrade.
- Windows client connectivity over the documented network path.
Release gate
Do not call the project production-ready until tests pass, dependencies
are reviewed, the security limitations are documented, and the installer
has been tested on the declared Home Assistant OS architectures. Never
claim a security audit unless one actually occurred.
Installation plan
Once a working release and repository exist:
1. In Home Assistant, open Settings → Apps → App store → ⋮ →
   Repositories (labels can vary by Home Assistant version).
2. Add the published custom add-on repository URL.
3. Install Agent Gateway and review its permissions/configuration.
4. Start the add-on and open its sidebar panel.
5. Set the vault master password and store recovery information safely.
6. Add Home Assistant and Proxmox connections. Start with read-only
   permissions.
7. Verify connection status and run safe test operations.
8. Create a separate revocable token for the Windows agent.
9. Configure the Windows agent to connect to the Home Assistant host
   over a trusted LAN or VPN, using HTTPS where appropriate.
10. Test denied operations, token revocation, audit redaction, and
    restart/restore before enabling writes.
Repository layout (actual)
repository.yaml
README.md
AGENT.md
CHANGELOG.md
design-system/agent-gateway/MASTER.md   generated design tokens and rules
docs/
  PLAN.md            verified upstream requirements and decisions
  SECURITY.md        threat model, controls, limitations
  NETWORKING.md      listeners, TLS, client setup, firewall guidance
  VAULT_RECOVERY.md  vault format, backup, recovery procedures
  DEVELOPMENT.md     local setup, checks, coverage gaps
agent_gateway/                       the add-on
  config.yaml
  Dockerfile
  build.yaml
  run.sh
  apparmor.txt
  requirements.txt
  pyproject.toml
  src/gateway/
    __main__.py        both uvicorn servers, maintenance loop
    runtime.py         composition root shared by admin and agent apps
    settings.py        options.json + HAG_* environment
    storage/db.py      SQLite schema and settings store
    vault/             crypto envelope and vault manager
    auth/              tokens, rate limiting, agent service
    policy/            document schema and evaluation engine
    approvals/         single-use approvals
    audit/             redacted audit log
    operations.py      one execution path for REST and MCP
    connectors/        home_assistant.py, proxmox.py, ssh.py, http.py
    tools/             specs, parameter models, registry
    api/               admin + agent apps, middleware, schemas
    mcp/server.py      stateless Streamable HTTP MCP endpoint
    ui/static/         index.html, styles.css, app.js
  tests/                             pytest suite
pytest.ini                           lets `pytest` run from the repo root
tools/ui-smoke/                      optional jsdom panel smoke harness
.github/workflows/ci.yml             lint, types, tests, UI, image build

The repository layout originally suggested an `app/` tree and docs such as
`NETWORKING.md` and `VAULT_RECOVERY.md`; both exist now, with a `src/`
package layout because the project is installed as a Python distribution.

Related project to evaluate
Review
hassio-mcp-server
as a reference for a Home Assistant OS add-on exposing MCP tools. Reuse
code only after reviewing its license, dependencies, permissions, and
threat model. Do not assume broad API access meets this project's
least-privilege requirements.
Definition of done
- Installs and starts on a declared Home Assistant OS architecture.
- Sidebar management UI works through Ingress.
- Vault remains encrypted on disk and survives restart.
- Credentials never appear in agent-visible output or logs.
- HA, Proxmox API, and restricted SSH connectors have tests.
- REST and MCP requests share one authorization/policy implementation.
- Agent tokens are revocable and scoped.
- High-risk actions require explicit approval or are disabled.
- Windows connectivity instructions are accurate and tested.
- Documentation covers install, upgrades, backups, recovery,
  networking, threat model, and limitations.

Verification log (2026-10-22)
Environment: Python 3.11.2 virtual environment; Node.js 22; no Docker daemon.

Ran and passed:
- pip install -e "agent_gateway[dev]"
- ruff check agent_gateway/src agent_gateway/tests        All checks passed
- ruff format --check agent_gateway/src agent_gateway/tests  60 files formatted
- mypy src (strict)                                     no issues in 45 files
- pytest agent_gateway/tests                            207 passed
- node --check agent_gateway/src/gateway/ui/static/app.js
- ASGI smoke test: /healthz 200; management API without Ingress user 401;
  with Ingress headers 200; vault initialize/unlock; agent API 401 without a
  token.
- MCP session over ASGI: initialize, tools/list identical to the REST catalogue,
  tools/call reading through the mocked connector, pending-approval flow, and a
  write completed over REST using the approval created over MCP.
- Panel render in jsdom (optional harness in tools/ui-smoke): every section,
  the vault setup and unlock screens, dialogs, approvals, credential and policy
  validation, with no JavaScript runtime errors.
- Live process run (uvicorn, real TCP sockets, development settings on
  127.0.0.1:18099/18098): vault initialization, credential and connection
  creation, connection test, REST read, a write that returned 202 pending
  approval, agent approve attempt rejected with 404, admin approval, retry
  succeeded, audit rows written, and MCP initialize + tools/list over HTTP with
  curl as an independent client. The upstream was a local stand-in server, not
  a real Home Assistant. After shutdown `/data/vault.json` contained no
  plaintext secret.

Not run, and why:
- Docker image build and add-on install/restart/upgrade — no Docker daemon or
  Home Assistant OS instance available here.
- Live Home Assistant, Proxmox VE, and SSH integration — no lab infrastructure;
  connectors are covered by mocked HTTP, one local stand-in HTTP server, and
  unit-level SSH trust tests.
- MCP with an official SDK client — raw JSON-RPC was verified over a real
  socket with curl, but no SDK client was run against the endpoint.
- Real-browser rendering, screen-reader traversal, and visual regression — the
  panel is covered by static invariants and a jsdom smoke run, not by a browser;
  manual review is still required.
- aarch64 build — declared in build.yaml but not built here.

Before production use:
1. Build and install the add-on on amd64 and aarch64 Home Assistant OS, verify
   Ingress, restart persistence, snapshot/restore, and upgrade.
2. Exercise real Home Assistant, Proxmox VE, and SSH targets, including host-key
   rotation and TLS with a custom CA.
3. Run an independent security review of the auth, policy, approval, and vault
   code paths, plus a dependency audit.
4. Add monitoring for repeated auth failures and approval volume, and document
   an operational runbook.
5. Confirm the Windows-agent connectivity path on the real network.
