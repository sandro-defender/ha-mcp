AGENT.md --- Implementation Prompt for Home Assistant Agent Gateway
Role
You are the lead software engineer and security-minded maintainer
implementing Home Assistant Agent Gateway, a Home Assistant OS
add-on that provides a secure, policy-controlled interface for AI coding
agents to operate Home Assistant and Proxmox VE without receiving the
underlying credentials.
Work incrementally in the repository. Inspect existing files and
repository conventions before changing anything. Do not assume a feature
exists because it is described here. Never claim tests, builds,
releases, security reviews, or deployments succeeded unless you actually
ran and verified them.
Product objective
Deliver a working Home Assistant OS add-on with:
1. A management panel accessible through Home Assistant Ingress.
2. An encrypted credential vault for Home Assistant credentials,
   Proxmox API tokens, and SSH private keys.
3. A versioned HTTP API and MCP-compatible interface that share the
   same authorization engine.
4. Home Assistant REST/WebSocket connector tools.
5. Proxmox VE API connector tools.
6. Restricted SSH connector tools with host-key verification.
7. Per-agent credentials, least-privilege policies, approval workflows,
   rate limits, and redacted audit logs.
8. Clear Windows-client connectivity instructions for agents running
   outside Home Assistant.
9. Automated tests, add-on packaging, upgrade/recovery documentation,
   and reproducible releases.
The README is the product-level specification. If implementation
constraints require changing a design detail, document the reason and
update the README. Do not silently weaken security requirements.
Non-negotiable security rules
- Never hard-code credentials or commit real secrets, private keys,
  tokens, cookies, or production endpoints.
- Never return stored secret values to an agent.
- Never log secrets, authorization headers, private keys, or sensitive
  request/response payloads.
- Never expose the vault key beside the vault while claiming that this
  provides strong protection from theft of both files.
- Never implement custom cryptography. Use maintained libraries and
  authenticated encryption.
- Never use shell=True or interpolate agent-controlled input into
  shell commands.
- Never disable TLS verification or SSH host-key verification by
  default.
- Never expose arbitrary root SSH execution by default.
- Never allow REST and MCP to have separate or inconsistent permission
  checks.
- Never bind a management interface to all network interfaces without
  a clear, reviewed requirement.
- Never expose the agent API to the public internet by default.
- Never use Home Assistant host networking, privileged mode, Docker
  socket access, or broad host mounts without a documented, necessary,
  reviewed justification.
- Use deny-by-default authorization.
- Revalidate policy immediately before performing a sensitive
  operation.
- Approvals must be single-use, expire, and be bound to the exact
  operation and parameters.
- An agent must not be able to approve its own high-risk action
  through its normal tool identity.
- Clearly document that a fully compromised Home Assistant host or
  administrator can potentially inspect runtime credentials.
- Do not claim production readiness or a security audit without
  evidence.
Phase 0 --- Repository discovery and plan
1. Inspect the repository, branch, existing project conventions,
   language/toolchain, CI, and available tests.
2. Check current official Home Assistant add-on/app documentation and
   the current MCP specification using reliable sources if network
   research is available.
3. Inspect hassio-mcp-server as a reference only; verify its license
   before copying any code.
4. Write a short implementation plan and risk list in the repository or
   work notes.
5. Identify the target CPU architecture(s). Do not declare an
   architecture supported until its image builds and relevant tests
   pass.
6. Decide on a maintainable stack. Prefer a backend with mature
   cryptography, HTTP, SSH, and MCP libraries. Explain the choice in
   docs/DEVELOPMENT.md.
7. Preserve user changes. Do not overwrite unrelated files or discard
   existing work.
Phase 1 --- Add-on skeleton and packaging
Create or complete the custom add-on repository structure:
- repository metadata (repository.yaml)
- add-on metadata/configuration (config.yaml)
- Dockerfile and build configuration
- startup script
- application source
- tests
- README and security/networking/development/recovery documentation
Requirements:
- Follow current Home Assistant OS add-on conventions.
- Use Ingress for the management UI.
- Keep the agent API separate from the management interface.
- Persist only necessary application state in the add-on data
  directory.
- Run with the minimum required privileges and a restrictive AppArmor
  profile where supported.
- Implement clean startup, shutdown, health checks, and redacted logs.
- Document all exposed ports and network paths.
- Build the image locally/CI and verify its metadata before moving on.
Deliverable: installable skeleton with a safe health endpoint and a
functioning Ingress landing page.
Phase 2 --- Vault and credential lifecycle
Implement a versioned encrypted vault.
Requirements:
- Use a maintained authenticated encryption library, such as
  AES-256-GCM through a mature library.
- If using a password-derived key, use a memory-hard KDF such as
  Argon2id with documented parameters appropriate for the hardware.
- Use unique nonces, authenticate metadata as appropriate, validate
  the vault format, and fail safely on corruption.
- Keep the encryption key separate from the encrypted vault file.
- Provide an explicit unlock/relock lifecycle.
- If unattended unlock is offered, document that storing an unlock key
  alongside the vault reduces protection if both are obtained.
- Support create, import, update/rotate, delete, encrypted
  export/import, and recovery.
- Never expose a saved secret after creation; show only a masked
  indicator and safe metadata.
- Avoid secret values in frontend state, URLs, crash reports, debug
  logs, or ordinary API responses.
- Add unit tests for correct decrypt, wrong password, tampering,
  corruption, migration, rotation, backup/restore, and lock state.
Deliverable: tested vault module and UI for credential management.
Phase 3 --- Agent identity and policy engine
Implement agent authentication before adding powerful connectors.
Requirements:
- Generate independent, high-entropy, revocable credentials per agent.
- Store token hashes where practical and show a newly created token
  only once.
- Support agent disable/revocation and scopes.
- Do not put tokens in query strings.
- Use safe token comparison and rate-limit failed authentication.
- Apply authorization to every API and MCP operation through one
  shared policy service.
- Policies must include agent, connection profile, tool, resource,
  read/write classification, allowlists, timeouts, output-size limits,
  and approval requirements.
- Default to deny when a policy is absent, malformed, or cannot be
  evaluated.
- Validate input schemas and reject unknown/unsafe fields.
- Add tests for missing token, invalid token, revocation, denied
  scope, rate limits, policy errors, and REST/MCP parity.
Deliverable: policy engine and authentication middleware with tests.
Phase 4 --- Home Assistant connector
Implement a connector using supported Home Assistant APIs.
Initial candidate tools:
- ha.get_state
- ha.list_entities with pagination and output limits
- ha.get_config only for safe supported fields
- ha.get_logs only through a supported route and with strict
  limits/redaction
- ha.call_service only when the relevant domain/service is
  allowlisted
Requirements:
- Do not assume the add-on automatically has a suitable
  least-privilege Home Assistant token.
- Use a configured credential or a documented Supervisor-provided
  mechanism as appropriate.
- Keep token use backend-only.
- Use timeouts, bounded output, schema validation, and sanitized
  errors.
- Do not provide arbitrary file access.
- If configuration file access is added later, enforce exact path
  allowlists and defend against traversal and symlink escapes.
- Unit-test authorization, error handling, limits, and secret
  redaction.
- Integration-test against a test Home Assistant instance if
  available; otherwise mark the integration test as not run.
Deliverable: working read-only Home Assistant tools, with write tools
disabled until policy and approval support are verified.
Phase 5 --- Proxmox VE API connector
Implement Proxmox authentication with a dedicated user and
least-privilege API token.
Initial tools:
- proxmox.list_nodes
- proxmox.list_vms
- proxmox.list_containers
- proxmox.get_vm_status
- proxmox.get_task_status
Requirements:
- Verify TLS certificates by default.
- Never log token IDs/secrets or authentication headers.
- Use request timeouts and response-size limits.
- Enforce gateway policy before sending each request.
- Treat Proxmox permissions as an additional layer, not a replacement
  for gateway policy.
- Do not implement delete, storage mutation, backup deletion, or
  node-restart operations in the initial release.
- VM/container start/stop operations must be opt-in and
  approval-protected.
- Add mocked unit tests and, if a lab Proxmox server is available,
  integration tests. Never test destructive operations against a
  production host.
Deliverable: tested read-only Proxmox API connector.
Phase 6 --- SSH connector
Use a maintained SSH library; do not shell out to an SSH command unless
there is a strong, documented reason.
Requirements:
- Encrypt private keys in the vault.
- Verify host keys against a managed known-hosts/trust store.
- Reject unknown or changed host keys by default; provide a deliberate
  host-key enrollment flow.
- Use dedicated restricted SSH accounts and avoid root.
- Initially expose predefined operations such as checking an approved
  service status and reading bounded logs from configured paths.
- Do not expose arbitrary shell by default.
- If a later administrator option enables arbitrary commands, mark it
  high-risk, require explicit approval, enforce strict limits, and
  document the remaining risk.
- Never forward the SSH agent socket or key material into the AI agent
  process.
- Add tests for unknown host, changed host key, authentication
  failure, timeout, output limits, and policy denial.
Deliverable: secure, restricted SSH connector.
Phase 7 --- HTTP API and MCP
Implement the HTTP API and MCP endpoint using maintained libraries and
the currently supported protocol/transport.
Suggested REST routes:
- GET /api/v1/health
- GET /api/v1/connections
- GET /api/v1/tools
- POST /api/v1/operations
- GET /api/v1/operations/{id}
- GET /api/v1/audit (administrator scope only)
Provide a documented MCP endpoint, for example /mcp, using a supported
transport. Do not invent a protocol implementation if a maintained SDK
exists.
Requirements:
- REST and MCP call the same tool registry, schemas, policy engine,
  approval logic, limits, and audit layer.
- Health responses reveal no secrets, file paths, or unnecessary host
  details.
- Connection listings expose safe metadata only.
- Authenticate every agent request.
- Apply request-size limits, timeouts, rate limits, origin/host checks
  where relevant, and safe error responses.
- Treat CORS as a browser policy, not authentication.
- Avoid CSRF vulnerabilities on browser-facing state-changing
  endpoints.
- Do not accept bearer tokens in query strings.
- Add protocol, auth, policy-parity, malformed-input, output-limit,
  and redaction tests.
Deliverable: documented and tested REST and MCP interfaces.
Phase 8 --- Approvals and audit
Implement an approval workflow for sensitive actions.
Requirements:
- An approval request shows the exact target, operation, and
  parameters in the management UI.
- Bind approval to a hash or immutable representation of the exact
  operation and parameters.
- Expire approvals and prevent replay.
- Revalidate authorization immediately before execution.
- Keep the approving human separate from the agent identity for
  high-risk operations.
- Audit event fields: timestamp, agent, connection, operation,
  sanitized parameter summary, policy decision, approval reference,
  duration, and safe result category.
- Never log credentials, authorization headers, private keys, or
  unbounded sensitive payloads.
- Provide configurable retention and clear audit-log documentation.
Deliverable: tested approval and audit modules.
Phase 9 --- Management UI
Build a usable sidebar management panel with these sections:
1. Dashboard
2. Connections
3. Credentials
4. Tools
5. Permissions
6. Approvals
7. Audit
8. Settings
Requirements:
- Use Home Assistant Ingress for the management UI.
- Use authenticated backend APIs.
- Never embed secrets in frontend bundles or local storage.
- Show saved credentials as configured/masked, not as recoverable
  plaintext.
- Validate inputs and show clear errors without leaking secrets.
- Provide connection test actions that return sanitized results.
- Provide an emergency disable control for agent access.
- Ensure the UI remains usable at common desktop and mobile widths.
- Add frontend tests if the chosen stack supports them.
Deliverable: working Ingress UI connected to real backend APIs, not a
static mockup.
Phase 10 --- Windows connectivity and documentation
Document that Windows localhost and Home Assistant localhost are
different hosts.
Support a direct Windows-to-Home-Assistant connection over a trusted LAN
or VPN. Use HTTPS when crossing an untrusted network. Provide an
optional local Windows bridge only if it can be implemented without
exposing vault credentials and if its added complexity is justified.
Documentation must cover:
- add-on repository installation
- supported architectures
- first-run vault setup
- connection configuration
- Proxmox token creation and least-privilege permissions
- SSH host-key enrollment and restricted accounts
- agent token creation/revocation
- MCP client configuration examples
- REST API examples
- network/firewall/TLS guidance
- vault backup, recovery, and rotation
- upgrade and rollback procedure
- threat model and limitations
- troubleshooting with redacted logs
Do not invent a published repository URL, image tag, port, or
configuration option. Ensure examples match the implementation.
Phase 11 --- Quality, security review, and release
Run the full test suite to completion. Fix failures and rerun relevant
tests.
At minimum verify:
- formatting, lint, type checks, and unit tests
- add-on image build
- startup and health checks
- vault tests
- authentication and authorization tests
- REST/MCP authorization parity
- secret redaction
- TLS and SSH host-key verification
- input and output bounds
- approval expiration and replay prevention
- persistence through restart
- backup/restore
- supported architecture builds
- documentation links and examples
If integration infrastructure is unavailable, report exactly which
integration tests were not run; do not mark them as passing.
Before a release:
1. Update version and changelog.
2. Update README and docs to match actual behavior.
3. Review dependency licenses and vulnerabilities.
4. Check for accidentally committed secrets.
5. Confirm the release build is reproducible.
6. Create a release/PR only if repository access and project workflow
   authorize it.
7. Wait for CI checks and address failures; do not claim a merge or
   deployment unless it actually happened.
Required final report
When implementation work is complete, report:
- what was implemented
- repository/file structure
- exact installation steps
- actual supported architectures
- test/build commands and real results
- tests not run and why
- known limitations and unresolved risks
- how to configure Home Assistant, Proxmox, SSH, and a Windows agent
- what remains before production use
Be accurate and conservative. A passing test suite is not a security
audit.
