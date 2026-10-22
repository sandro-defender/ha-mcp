# Changelog

All notable changes to **Home Assistant Agent Gateway** are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-22

### Added
- **Persistent AI Agent Work Journal & Session Handoff System** (`gateway.journal`, schema v3): eight new `journal_*` tables (projects, sessions, tasks + dependency edges, work entries, decisions, issues/failed approaches, test runs, handoffs) migrated automatically from v2 without touching existing records. Every record is project-scoped, agent/admin-attributed, timestamped, redacted before storage, and idempotency-keyed so agent retries cannot duplicate records.
- **Journal API on all surfaces**: agent REST (`/api/v1/journal/*`), admin REST (`/api/admin/journal/*`), and connectionless MCP tools (`journal.read_project_state`, `record_work`, `record_test_run`, `generate_handoff`, …) sharing one authorization path — new `journal:read`/`journal:write` scopes plus central-policy `journal.*` pseudo-tools, policy rate limits, and audit events for writes and denials. Starter templates `journal_read` / `journal_readwrite`.
- **Accuracy rules enforced server-side**: five verification levels (`claimed`, `automated_tests`, `integration_verified`, `not_tested`, `blocked_infrastructure`) stored verbatim and never upgraded; tasks can only complete with evidence levels; dependency cycles rejected and unfinished dependencies block progress (`409`); validated status transitions; append-only history with owner-scoped writes and optimistic-concurrency project state.
- **Project Journal UI (11th panel section)**: project-state overview with revision and optimistic-concurrency editor, work-status counts, active sessions (admin can end), prioritized Next steps with dependency blockers, record-work/add-task forms, filterable timeline (completed/failed/blocked/unverified, record type, search, commit) with test evidence and agent/session attribution, and a session-handoff card — **Generate Handoff Prompt**, editable preview, copy action, previous-handoff loading. Real persisted data only; loading, empty, error, and access-denied states implemented.
- **Handoff prompt generator**: bounded (≤16k chars) continuation prompt from current records — identity/purpose, branch/commit/revision, architecture/conventions, constraints, completed work with evidence levels, failures and failed-approach lessons, open bugs/security issues, focus task with acceptance criteria and blockers, prioritized next actions, recent test evidence, and a verification checklist; journal text is labelled untrusted and generation never executes anything.
- **Agent instruction set**: `AGENTS.md` (living project-wide instructions incl. the journal session lifecycle; `AGENT.md` kept as the historical bootstrap prompt), `CLAUDE.md` pointer, `docs/PROJECT_STATE.md`, `docs/WORKLOG.md`, `docs/NEXT_STEPS.md`, and `docs/JOURNAL.md` (architecture, schema, API, retention/backup, security limitations, manual verification).

### Changed
- **SQLite schema v2 → v3** (journal tables; upgrade verified by migration + restart-persistence tests and a socket-level process-restart check).
- Panel section count 10 → 11; policy editor gained Journal read-only / read-write template buttons.

### Security
- Journal writes are authorized by the same deny-by-default policy engine (REST/MCP parity via one function), rate-limited (rule limit honored, capped at 120/min), and audited; `require_approval` on a journal rule *denies* (journal records execute nothing); cross-agent modification of tasks/issues/sessions is refused; oversized bodies (`413`) and all inputs are validated server-side; a journal entry cannot approve or unlock anything.

### Known limitations
- Verified in this environment by the full suite (253 tests), the jsdom panel smoke harness (incl. journal checks), and a real-socket dev-instance E2E with process restart. Live Home Assistant OS installation and real HA/Proxmox/SSH integration were **not** run (no lab/Docker daemon here), and no third-party security audit has been performed. Journal verification levels are self-reported evidence metadata, not proof — see `docs/JOURNAL.md`. One `default` journal project is seeded; multi-project management endpoints are not exposed yet.

## [0.2.1] - 2026-10-22

### Fixed
- **Add-on startup failure `/bin/sh: can't open '/init': Permission denied`**: the custom `apparmor.txt` profile replaced the Supervisor default profile but granted no execute permission to the Home Assistant base image's S6-Overlay v3 `/init` entrypoint (or `/package/**`, `/command/**`, `/etc/cont-init.d/**`), so AppArmor denied `/init` before `run.sh` or any application code could run. Added the S6 rule block required by the Home Assistant developer documentation for custom AppArmor profiles. `init: false` and the `ghcr.io/home-assistant/base:3.24` base image were already correct and are unchanged. Regression tests assert the S6 rules and the `/init` → `CMD ["/run.sh"]` startup chain.

### Security
- Added concurrent approval-consumption test proving exactly one of 8 simultaneous attempts can consume an approved operation, and an expired-agent-token rejection test.

### Documentation
- Added `docs/IMPLEMENTATION_AUDIT.md` (capability-by-capability status, evidence, infrastructure-blocked items), documented the Class 1–5 agent operating boundaries in `docs/SECURITY.md`, and restored the root `SECURITY.md` referenced by the README.

### Known limitations
- Startup fix verified by packaging regression tests and the full test suite in this environment; no Docker daemon or Home Assistant Supervisor was available here, so container run and live HA OS installation remain to be confirmed (CI builds the amd64 image). No live Home Assistant/Proxmox/SSH integration test was run.

## [0.2.0] - 2026-10-21

### Added
- **10-Section Home Assistant Ingress UI**: Redesigned operational workspace with dedicated sections for `Overview`, `Connections`, `Credentials`, `Agents`, `Tools`, `Permissions`, `Approvals`, `Audit Log`, `Updates and Changelog`, and `Settings and Diagnostics`.
- **Light, Dark, and System Theme Selector**: Explicit theme preference persisted in gateway settings alongside automatic `prefers-color-scheme` support, WCAG AA contrast verification, and `prefers-reduced-motion` support.
- **Dedicated Agents Management Section**: Per-agent identity management with descriptions, scoped permissions, tool allowlist/denylist chips, token expiration status (`active`, `expiring_soon`, `expired`, `revoked`), token rotation, immediate revocation, usage counters, denied/rate-limited request counts, and one-click activity filtering.
- **Interactive Policy Simulator (`POST /api/admin/policies/evaluate`)**: Dry-run any combination of agent, tool, connection, and parameters to verify whether the gateway will allow immediately, require human approval, or deny (with rule and constraint attribution) before live execution.
- **Rich Connection Diagnostics**: Latency tracking (`latency_ms`), multi-step verification breakdown (`configuration`, `credential`, `transport`, `authentication`, `verification`), structured error classification (`authentication_failed`, `permission_denied`, `tls_certificate_error`, `host_key_untrusted`, `network_unreachable`, `protocol_error`), actionable remediation guidance, and persistent `last_ok_at` health tracking.
- **Updates, Release History, and Post-Update Notice**: Bundled Keep-a-Changelog parser (`GET /api/admin/releases`), cached on-demand GitHub release check (`POST /api/admin/releases/check`), and one-time post-update "What's New & Migration Notes" banner (`POST /api/admin/releases/acknowledge`).
- **Readiness & Redacted Diagnostic Exports**: Added `GET /readyz`, `GET /api/admin/readiness`, `GET /api/admin/diagnostics`, and downloadable `GET /api/admin/diagnostics/export` (`gateway-diagnostics-redacted.json`) with recursive secret redaction verification.
- **Proactive Security Warnings**: Real-time posture warnings for expiring or expired agent tokens, credentials older than 90 days since rotation, recent failed authentication attempts, rate-limited requests, and untrusted SSH host keys.
- **Official Home Assistant OS App Assets & Documentation**: Added `agent_gateway/icon.png` (128×128), `agent_gateway/logo.png` (250×100), `agent_gateway/DOCS.md`, `agent_gateway/CHANGELOG.md`, `agent_gateway/translations/en.yaml`, and original vector branding (`logo.svg`, `favicon.svg`).

### Changed
- **Automatic SQLite Schema Migration (v1 → v2)**: Added automatic startup migration in `storage/db.py` to add `connections.last_ok_at`, `connections.last_check_detail`, and `agents.last_rotated_at` while preserving all existing records.
- **Audit Log Failed-Auth Visibility**: `AgentAuthMiddleware` now records redacted `auth.failed` events (including rate-limit throttling) in the audit log for operator visibility.
- **Release & Version Synchronization**: Synchronized Semantic Versioning across `agent_gateway/config.yaml`, `agent_gateway/pyproject.toml`, `agent_gateway/Dockerfile`, `agent_gateway/src/gateway/__init__.py`, `CHANGELOG.md`, and `agent_gateway/CHANGELOG.md` with automated CI enforcement.

### Fixed
- **TLS Handshake Error Classification**: `connectors/http.py` now distinguishes `ssl.SSLError` / certificate verification failures (`tls_verification_failed`) from generic TCP connection failures (`upstream_unreachable`) and distinguishes HTTP `403` permission errors from HTTP `401` authentication failures.

### Security
- Enforced recursive secret redaction on exported diagnostic bundles (`hag-diagnostics-v1`) and connection diagnostic details.
- Preserved strict Content-Security-Policy (`default-src 'self'`, no inline styles or scripts) and zero browser storage (`localStorage`/`sessionStorage`/`cookies`) across all new UI views.

### Migration Notes
- Database schema migrates automatically from `v1` to `v2` at startup. No manual SQL or configuration changes are required.
- Existing encrypted `vault.json` files, `unlock.key` files, SSH `known_hosts` entries, and `hag_...` agent tokens remain 100% compatible.

## [0.1.0] - 2026-10-20

### Added
- **Home Assistant OS Add-on (`agent_gateway/`)**: `config.yaml`, `build.yaml`, `Dockerfile`, `apparmor.txt`, `run.sh`, and root `repository.yaml` targeting `amd64` and `aarch64`.
- **Two-Listener Architecture**:
  - Management listener (`:8099`) gated to the Home Assistant Supervisor Ingress peer (`172.30.32.2`), requiring `X-Remote-User-Id`, an optional administrator user ID allowlist, and same-origin + `X-Gateway-Request: 1` CSRF checks.
  - Agent listener (`:8098`, off on host network by default) serving REST `/api/v1/*` and MCP Streamable HTTP at `/mcp` (`MCP-Protocol-Version: 2025-06-18`), authenticated with per-agent `hag_...` bearer tokens stored as SHA-256 digests.
- **Credential Vault (`gateway.vault`)**: AES-256-GCM authenticated encryption with per-record 96-bit random nonces, record-ID additional authenticated data, Argon2id key derivation (`64 MiB`, `t=3`, `p=2`), encrypted export/restore, DEK rotation, and optional `0600` auto-unlock key file.
- **Deny-by-Default Policy Engine (`gateway.policy`)**: Strict Pydantic-validated policy rules with explicit deny precedence, tool wildcards, connection scoping, target constraints (`entities`, `domains`, `ha_services`, `nodes`, `vmids`, `services`, `log_paths`, `templates`), and per-rule execution limits.
- **Approval Service (`gateway.approvals`)**: Single-use, time-bounded approvals bound to `(agent_id, tool, connection_id, sha256(canonical_json(params)))` with policy and scope re-validation before execution.
- **Connectors (`gateway.connectors`)**:
  - Home Assistant REST connector (`ha.get_state`, `ha.list_entities`, `ha.get_config`, `ha.call_service`) with hard-blocked dangerous service domains (`hassio`, `homeassistant`, `shell_command`, `command_line`, `python_script`, `recorder`, `system_log`, `persistent_notification`).
  - Proxmox VE read-only connector (`proxmox.list_nodes`, `proxmox.list_vms`, `proxmox.list_containers`, `proxmox.get_vm_status`, `proxmox.get_task_status`) using `PVEAPIToken` authentication and strict field allowlists.
  - Restricted SSH connector (`ssh.service_status`, `ssh.read_log`, `ssh.run_template`) using `asyncssh` with mandatory `known_hosts` verification, non-root username enforcement, and shell-free argument allowlists.
- **Structured Redacting Audit Log (`gateway.audit`, `gateway.redaction`)**: SQLite WAL audit trail with recursive secret redaction and logging filter.
