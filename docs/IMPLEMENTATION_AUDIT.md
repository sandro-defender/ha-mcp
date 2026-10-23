# Implementation Audit — 0.2.1 (2026-10-22)

Method: code inspection of `agent_gateway/src/gateway/**` plus the executed
test suite (`pytest -q`, 211 tests — see README "Verification log").
"Verified" below means a passing automated test exercises the path; mocked
upstreams (respx/ASGI transport) are **not** live-integration verification.
No Home Assistant OS/Supervisor, Docker daemon, Proxmox, or real SSH server
was available in this environment.

> **Superseded for 0.4.0.** The 0.3.1 change below fixed read access for
> `/init` only; the same denial then surfaced for
> `/package/admin/s6-overlay-*/libexec/preinit` and `stage0`. The complete root
> cause and fix (read + execute for the whole S6 chain) are documented in
> [`STARTUP_TROUBLESHOOTING.md`](STARTUP_TROUBLESHOOTING.md). The text below is
> kept as the historical record of the 0.2.1/0.3.1 investigation.

## Startup failure (`/init`: Permission denied) — root cause

`agent_gateway/config.yaml` sets `init: false` (correct: the HA base image
`ghcr.io/home-assistant/base:3.24` ships S6-Overlay v3 whose `/init` must be
PID 1; `init: false` stops Supervisor adding Docker's own init). The base
image uses the S6 `/init` startup chain and Dockerfile `CMD ["/run.sh"]`.
The custom `apparmor.txt` replaces Supervisor's default profile and must
permit the entrypoint. The 0.2.1 fix added S6 execute rules, but a live 0.3.0
installation still reported `/bin/sh: can't open '/init': Permission denied`.
That exact shell error means `/bin/sh` was opening `/init` as a script and
lacked **read** access; `/init ix` only granted inherited execution. Fix
(0.3.1): `/init rix` grants read plus inherited execute access, with the
regression assertion in `tests/test_packaging.py`
(`test_apparmor_permits_s6_init_entrypoint`). `init: false` and the base image
remain unchanged. This diagnosis is based on the reported runtime error;
container/HA OS startup still needs live confirmation (no Docker/Supervisor in
this environment).

## Capability audit

| Area | Status | Evidence (source → tests) |
|---|---|---|
| A. HA REST integration | Implemented and verified (mocked upstream) | `connectors/home_assistant.py`, `connectors/http.py` → `test_connectors.py`, `test_agent_api.py` |
| A. HA WebSocket | Missing (intentional limitation) | No WS client exists; REST only. Documented limitation, not used by any tool. |
| A. Blocked HA service domains | Implemented and verified | `tools/registry.py` → `test_blocked_service_domains_*`, approval-path test |
| B. Proxmox read-only (5 tools, token auth, TLS verify, field allowlists) | Implemented and verified (mocked) | `connectors/proxmox.py` → `test_proxmox_*` |
| B. Proxmox mutations (power/config/delete) | Missing (intentional) | No write tools registered → `test_proxmox_mutations_do_not_exist`, `test_registry_has_no_proxmox_write_tools`. Class-3/4 Proxmox ops are prohibited by absence, not merely policy. |
| C. Restricted SSH (known_hosts enforced, no root, argv templates, bounds, timeouts) | Implemented and verified (unit level) | `connectors/ssh.py` → `test_ssh_hostkeys.py`, template/injection tests. Live SSH: Blocked by infrastructure. |
| D. Credential vault (AES-256-GCM, Argon2id KEK wrap, masked UI, export/import, fail-closed) | Implemented and verified | `vault/crypto.py`, `vault/manager.py` → `test_vault.py` (17 tests) |
| D. Vault key persistence | Implemented and verified | Password-derived KEK; optional 0600 auto-unlock file (off by default). Missing/invalid key fails closed (`test_vault.py`). See `docs/VAULT_RECOVERY.md`. |
| E. Agent auth (per-agent `hag_` tokens, SHA-256 digest only, expiry, revocation, rate limiting) | Implemented and verified | `auth/agents.py`, `auth/tokens.py`, `api/middleware.py` → `test_agent_api.py`, `test_middleware.py`; expiry test added in 0.2.1 |
| F. Centralized authorization (one `PolicyService`/`OperationService` for REST+MCP+SSH tools, deny-by-default) | Implemented and verified | `policy/engine.py`, `operations.py` → `test_policy.py`, `test_mcp.py` (REST/MCP parity + shared approval) |
| G. Approvals (single-use, expiring, bound to agent/tool/connection/SHA-256 of canonical params+policy revision+schema, atomic consume, revalidation before execution) | Implemented and verified | `approvals/service.py`, `operations.py` → `test_approvals.py`; concurrent-consume test added in 0.2.1 (exactly one of 8 wins) |
| H. Audit & diagnostics (redacted SQLite WAL audit, retention, `/readyz`, redacted diagnostics export) | Implemented and verified | `audit/service.py`, `redaction.py`, `runtime.py` → `test_redaction.py`, admin audit tests |
| I. HTTP + MCP interfaces (same engine, schema validation, size/time limits, safe errors) | Implemented and verified | `api/agent.py`, `mcp/server.py` → `test_mcp.py`, `test_agent_api.py` |
| J. Management UI (11 sections, real backend status, secrets never returned, policy simulator) | Implemented and verified (static/ASGI level) | `ui/static/*`, `api/admin.py` → `test_ui_assets.py`, `test_admin_api.py`. jsdom smoke (`tools/ui-smoke`) run green incl. journal checks (2026-10-22); real-browser/HA Ingress rendering: Blocked by infrastructure. |
| K. Project Journal & handoffs (0.3.0: schema v3, sessions/tasks/entries/decisions/issues/test-runs/handoffs, verification levels, idempotency, `journal.*` policy parity REST+MCP, 11th UI section) | Implemented and verified (ASGI + socket E2E) | `journal/*`, `api/journal.py`, `mcp/server.py` → `test_journal.py` (37 tests) + journal cases in `test_ui_assets.py`; dev-instance socket E2E with process restart proved persistence/redaction/handoff. Live HA OS install of 0.3.0: Blocked by infrastructure. See `docs/JOURNAL.md`. |

## Security gaps / remediation (priority order)

1. **Fixed 0.2.1 (critical, startup/security):** AppArmor profile denied the
   S6 `/init` entrypoint — add-on could not start under Supervisor.
2. **High — unverified on real HA OS:** image builds in CI but is never run;
   add a CI smoke job that starts the container and probes `/healthz`
   (requires Docker-capable runner changes; not possible in this sandbox).
3. **Medium:** Proxmox/HA/SSH have no live integration tests; keep mocked
   status until a lab exists. Do not claim live verification.
4. **Medium:** No HA WebSocket support — acceptable scope limitation; state
   it in user docs (done in `agent_gateway/DOCS.md` known limitations).
5. **Low:** Root `SECURITY.md` was referenced by README but missing —
   restored in 0.2.1 as a pointer to `docs/SECURITY.md`.
6. **Low (accepted, documented):** bearer-token agents, admin trust, and
   host-compromise limits — see `docs/SECURITY.md` "Known limitations".

## Infrastructure-blocked validation

Live HA OS install/start via Supervisor, real HA/Proxmox/SSH connections,
aarch64 image build/run, Windows MCP client E2E, browser smoke tests.
None were fabricated; all remain open.
