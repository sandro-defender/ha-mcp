# Changelog

All notable changes to this project are documented here. Versions follow the
add-on `config.yaml` version. This project is experimental; see
`docs/SECURITY.md` for limitations.

## [0.1.0] — 2026-10-22

First implementation. Not deployed to a live Home Assistant OS installation and
not security-audited.

### Added

**Add-on packaging**
- Home Assistant OS add-on (`agent_gateway/`) with `config.yaml`, `build.yaml`
  (`amd64`, `aarch64`, HA base image 3.24), `Dockerfile`, `run.sh`, a
  single-profile `apparmor.txt`, and repository-level `repository.yaml`.
- Ingress-only management panel on `8099`; agent API on `8098`, unpublished by
  default (`ports: 8098/tcp: null`).
- Non-root runtime (`app`, uid 1000), `HEALTHCHECK` on `/healthz`, `/ssl`
  mounted read-only, optional TLS for the agent listener.

**Credential vault**
- Versioned `hag-vault` v1 format in `/data`: AES-256-GCM per record with AAD
  bound to format and record id, Argon2id-derived KEK, wrapped DEK, atomic
  writes with a previous-good backup.
- Explicit lock/unlock, master-password change, data-key rotation, encrypted
  export/import (import leaves the vault locked), optional auto-unlock key file.
- Masked metadata only; secret values never cross an HTTP boundary.

**Agents and authorization**
- Per-agent identities with SHA-256-hashed bearer tokens, scopes, optional
  expiry, rotation, revocation, and one-time token display.
- Deny-by-default policy engine with allow/deny rules, connection scoping,
  resource constraints (entities, domains, services, nodes, VMs, containers,
  SSH services/paths/templates), per-rule limits, and policy revisions.
- One authorization path shared by REST and MCP (`OperationService`).

**Approvals and audit**
- Single-use, expiring approvals bound to agent, tool, connection, canonical
  parameters, tool schema, and policy revision; agents have no approval route.
- Append-only audit log with redaction, filters, retention purge, and an admin
  redaction self-check endpoint.

**Connectors and tools**
- Home Assistant REST (state, entity listing, safe config subset, allowlisted
  service calls; `shell_command`, `python_script`, `hassio`, `homeassistant`,
  `system_log`, and `recorder` are refused).
- Proxmox VE API reads (nodes, VMs, containers, VM status, task status).
- Restricted SSH: managed known-hosts trust workflow, service status, SFTP log
  tail with path allowlists, administrator-defined argv templates executed with
  `shlex.join` (no `shell=True`, no agent-supplied command strings).
- 12 tools, exposed identically over REST `/api/v1` and MCP `/mcp`
  (Streamable HTTP, stateless, answered on both `/mcp` and `/mcp/`), each with
  JSON Schema and risk annotations.

**Management panel**
- Vanilla-JS SPA (no build step, no CDN) served under a strict CSP: dashboard,
  connections, credentials, tools, permissions, approvals, audit, settings,
  vault setup/unlock, one-time token modal, host-key confirmation, empty/loading
  states, and keyboard-accessible modals.
- Design-system pass with an accessible token palette (WCAG AA contrast in
  light and dark), 44px targets, visible focus rings, inline SVG icons,
  focus-managed dialogs, error summaries linked to fields, `aria-sort` tables,
  live-region announcements, and `prefers-reduced-motion` handling.

**Engineering**
- Pytest suite (207 tests) covering vault crypto and lifecycle, policy
  evaluation, approvals, redaction, settings parsing, admin gate/CSRF/origin
  checks and body limits, agent auth throttling, credential and connection
  validation, Home Assistant and Proxmox VE connectors against mocked upstreams,
  the agent approval round trip, MCP JSON-RPC sessions (initialize, tools/list,
  tools/call, shared approvals), SSH host-key trust, add-on packaging metadata
  (ports unpublished, options matching the code, AppArmor single profile), and
  static UI invariants.
- Optional jsdom smoke harness (`tools/ui-smoke/`) that renders the real panel
  against a stubbed management API and walks every section and dialog. Not part
  of CI and not a visual audit.
- Ruff (lint + format) and strict mypy clean; GitHub Actions CI for lint, types,
  tests, static UI checks, and an image build.
- Documentation: `docs/SECURITY.md`, `docs/NETWORKING.md`,
  `docs/VAULT_RECOVERY.md`, `docs/DEVELOPMENT.md`, `docs/PLAN.md`, and the
  generated `design-system/agent-gateway/MASTER.md`.

### Known limitations in this release

- No Docker daemon in the development environment: the image build and add-on
  install/upgrade paths are **not verified locally**.
- No live Home Assistant, Proxmox VE, or SSH host was used; connectors are
  tested against mocks and unit-level checks only.
- MCP sessions are exercised in-process over ASGI (initialize, list, call,
  approvals) and raw JSON-RPC was also sent over a real socket with curl; no
  official MCP SDK client was run against the endpoint.
- The panel renders in jsdom, not in a real browser; layout, contrast, and
  screen-reader behaviour have not been visually reviewed.
- No security audit, penetration test, or fuzzing. A fully compromised Home
  Assistant host can inspect runtime credentials.
