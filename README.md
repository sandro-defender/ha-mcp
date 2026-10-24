# Home Assistant Agent Gateway

![Agent Gateway banner](docs/assets/banner.png)

> Policy-controlled gateway for AI agents to operate Home Assistant, Proxmox VE, and restricted SSH targets — without ever receiving credentials.

[![version](https://img.shields.io/badge/version-0.4.2-blue)](CHANGELOG.md)
[![architectures](https://img.shields.io/badge/arch-amd64%20%7C%20aarch64-lightgrey)](agent_gateway/config.yaml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Repository:** https://github.com/sandro-defender/ha-mcp  
**Releases & Changelog:** https://github.com/sandro-defender/ha-mcp/releases — https://github.com/sandro-defender/ha-mcp/blob/main/CHANGELOG.md  
**Issues & Support:** https://github.com/sandro-defender/ha-mcp/issues  
**Security policy:** [SECURITY.md](SECURITY.md) · [docs/SECURITY.md](docs/SECURITY.md)  
**Add-on docs:** [agent_gateway/DOCS.md](agent_gateway/DOCS.md)

---

## What this is

A **Home Assistant OS add-on** that sits between external AI coding agents (Claude Desktop, Cursor, Windsurf, custom MCP clients on Windows/macOS/Linux) and your infrastructure:

- Agents authenticate with a **per-agent scoped, revocable `hag_...` bearer token** and call **one** policy engine whether they use REST or MCP.
- **Credentials stay in an AES-256-GCM + Argon2id vault** inside `/data`. They are never returned to agents, never appear in the UI after saving, and never enter logs or error messages.
- **Deny-by-default policies, single-use approvals, and a redacted audit log** make every operation reviewable.

Project status: **0.4.2 — evidence-driven security and reliability audit.** The full Python suite, lint, strict type-check, jsdom UI harnesses, process-level startup/restart checks, and wheel build pass in this environment. Docker and AppArmor enforcement are not available locally and remain CI/live verification gates; no live Home Assistant OS, Home Assistant/Proxmox/SSH target, third-party MCP client, or real browser was used. See [Verification log](#verification-log), [`docs/STARTUP_TROUBLESHOOTING.md`](docs/STARTUP_TROUBLESHOOTING.md), [`docs/AGENT_ACCESS.md`](docs/AGENT_ACCESS.md) and [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Operational workspace — 12 dedicated sections

The Ingress management panel (vanilla JS + CSS, no build step, strict CSP, offline, responsive) is organized into **12 sections**:

| Section | What you do there |
| :--- | :--- |
| **Overview** | Post-update release banner, security warnings, health metrics, security posture, pending approvals preview, recent activity |
| **Connections** | Home Assistant / Proxmox VE / SSH profiles with latency, multi-step diagnostics, error classification & remediation, `last_ok_at`, SSH `known_hosts` enrollment |
| **Credentials** | Encrypted vault lifecycle, 5 secret kinds, masked indicators, reference tracking, encrypted export/restore |
| **Agents** | Per-agent identities, descriptions, scopes, tool allow/deny chips, token expiry status, rotation/revocation, usage counters |
| **Agent Access** | Temporary sessions for coding agents: connections, scopes, operation classes, short lifetimes, single-use handoff links, expiry, revocation, restart policy, per-session activity |
| **Tools** | 12-tool catalog, group toggles, risk/approval classification, JSON Schema inspector, MCP/REST parity |
| **Permissions** | Visual allow/deny rule builder, starter templates, raw JSON policy editor, **interactive Policy Simulator** (`POST /api/admin/policies/evaluate`) |
| **Approvals** | Single-use, expiring, parameter-bound approvals with SHA-256 binding and policy re-validation |
| **Audit Log** | Searchable, filterable SQLite WAL audit trail with redaction, retention, and detail inspection |
| **Project Journal** | Persistent AI agent work journal: project state, prioritized tasks with dependencies, chronological timeline with verification levels (claimed vs evidenced), decisions, failed approaches, test evidence, sessions, and a **Generate Handoff Prompt** action with editable preview + copy |
| **Updates & Changelog** | Installed vs latest version, on-demand cached GitHub release check, post-update notice & migration notes, Keep-a-Changelog history |
| **Settings & Diagnostics** | Theme selector (System/Light/Dark), TTL/retention bounds, readiness probes (`/readyz`), redacted diagnostic bundle export |

Light, Dark, and System themes are supported with WCAG AA contrast verification in `tests/test_ui_assets.py`.

---

## Architecture

![Architecture](docs/assets/architecture.png)

```
Windows/macOS/Linux — AI agent / MCP client
            │  HTTPS over trusted LAN or VPN
            │  Authorization: Bearer hag_…
            ▼
Home Assistant Agent Gateway add-on
  Management :8099 (Ingress only, Supervisor peer + X-Gateway-Request: 1)
  Agent      :8098 (REST /api/v1 + MCP /mcp, 12 connector tools + Project Journal, same policy engine)
  Vault → Policy → Approval → Connectors → Audit
            │
            ├── Home Assistant REST  (HASS token, TLS verified)
            ├── Proxmox VE API       (API token, TLS verified)
            └── Restricted SSH       (asyncssh, known_hosts enforced)
```

Two listeners, one `Runtime`, one `PolicyService`, one audit path — REST and MCP share the same authorization.

Supported architectures: **amd64**, **aarch64**. See `agent_gateway/config.yaml` and `agent_gateway/build.yaml`.

---

## Installation in Home Assistant OS

1. In Home Assistant go to **Settings → Add-ons → Add-on Store** (or **Settings → Apps**) → menu `⋮` → **Repositories** → add:
   ```
   https://github.com/sandro-defender/ha-mcp
   ```
2. Select **Home Assistant Agent Gateway** → **Install**.
3. Review **Configuration** (see below), **Start** the add-on, then **Open Web UI** or **Show in sidebar**.

`icon.png` (128×128) and `logo.png` (250×100) are bundled for the Store listing. Required files `CHANGELOG.md` and `DOCS.md` are present in the add-on directory.

> **Installed from the `ha-addons` aggregator instead?** That repository includes this project as a git submodule, and Home Assistant reads only the pinned commit. A new version here therefore stays invisible until the pin is bumped — that is why the store can show `0.1.0` while `main` is newer. See [`docs/ADDON_UPDATES.md`](docs/ADDON_UPDATES.md) for the one-command fix, the hourly sync workflow for `ha-addons`, and the daily pin watchdog.

---

## Add-on configuration

All options are validated at startup. Invalid values fail closed.

| Option | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `log_level` | `list(debug|info|warning|error)` | `info` | Container stdout; all lines pass through the redaction filter |
| `admin_user_ids` | `list(str)` | `[]` | When non-empty, only these HA user IDs may open the Ingress panel |
| `tls_certfile` | `str` | `""` | PEM chain for the `:8098` agent listener (e.g. `/ssl/fullchain.pem`). Pair with `tls_keyfile`. |
| `tls_keyfile` | `str` | `""` | Matching PEM private key (e.g. `/ssl/privkey.pem`) |
| `audit_retention_days` | `int(1,3650)` | `30` | Retention for audit events and completed approvals |
| `approval_ttl_seconds` | `int(30,3600)` | `600` | Lifetime for pending approval requests |

Network: `8098/tcp` is **`null` (not published)** by default; expose it on a trusted LAN/VPN only via the add-on **Network** section. `watchdog` polls `http://[HOST]:[PORT:8099]/healthz`. Only the `ssl` map is requested (read-only).

---

## First-run walkthrough

1. **Vault** (`Credentials`): create a master password (≥12 characters, Argon2id `64 MiB, t=3, p=2`, AES-256-GCM). Export an encrypted backup and store it separately from the password.
2. **Credentials**: add `home_assistant_token`, `proxmox_token` (`user@realm!tokenname` shape validated), `ssh_private_key`, or `ca_certificate`. Secrets are masked after saving.
3. **Connections** (`Connections`): create a profile, **Scan Host Key** for SSH, **Test** — the response includes `latency_ms`, step breakdown (`configuration → credential → transport → authentication → verification`), `error_category`, `remediation`, and `last_ok_at`.
4. **Agent Access (recommended)**: use **Connect an AI agent** for guided temporary access and a ready-to-paste AI setup prompt. For persistent access instead, use **Agents & Permissions** to create an identity, copy its `hag_…` token once, and configure policy rules.
5. **Agent listener**: expose `8098/tcp` on the host for your LAN/VPN only if needed. Never port-forward `8098` or `8099` to the public internet. For SSH, the private key is stored in **Credentials**, while the **SSH username** is entered separately in the SSH **Connection**.

Upgrade, backup, and recovery are documented in [`docs/VAULT_RECOVERY.md`](docs/VAULT_RECOVERY.md) and [`agent_gateway/DOCS.md`](agent_gateway/DOCS.md). The SQLite schema migrates automatically through v4 without discarding existing records (`v2` adds connection/agent diagnostic fields, `v3` adds Project Journal tables, and `v4` adds temporary-agent access). Legacy v0 vaults are migrated on unlock and re-wrapped so they can be unlocked again after restart.

---

## Connecting an external agent (Windows, macOS, Linux)

### Guided UI setup (recommended)

1. Complete **Credentials → Connections → Test** for each upstream system.
2. Publish the add-on's **Agent API** on port `8098` under **Configuration → Network**, on a trusted LAN/VPN only.
3. Open **Agent Access → Connect an AI agent**. Choose connections, **Read only** or **Read and control**, a lifetime, and the address the agent computer can reach. The UI suggests an address from the current Home Assistant hostname and derives the scopes automatically.
4. Click **Create access and AI prompt**, review the prompt, and paste it only into the intended AI agent. It contains a short-lived, single-use handoff link—not an upstream credential or redeemed bearer token. Nothing is sent to an AI automatically.
5. The agent redeems the link, receives its token once on the agent page, configures MCP, and verifies health/tools before continuing. Writes still require your approval. Advanced individual-scope and settings forms remain available but are not required.

The guided UI fills in the following technical values for you; they are documented here as a fallback.

### MCP Streamable HTTP

- **Endpoint:** `http://<HA-LAN-IP>:8098/mcp` (or `https://` with `tls_certfile`/`tls_keyfile`)
- **Protocol version:** `2025-06-18`
- **Header:** `Authorization: Bearer hag_REPLACE_WITH_TOKEN`

PowerShell probe:

```powershell
$Headers = @{
  "Authorization"        = "Bearer hag_REPLACE_WITH_TOKEN"
  "MCP-Protocol-Version" = "2025-06-18"
  "Content-Type"         = "application/json"
}
Invoke-RestMethod -Uri "http://ha.lan:8098/mcp" -Headers $Headers -Method POST -Body '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Claude Desktop / MCP client `mcp.json`:

```json
{
  "mcpServers": {
    "ha-gateway": {
      "url": "http://ha.lan:8098/mcp",
      "headers": { "Authorization": "Bearer hag_REPLACE_WITH_TOKEN" }
    }
  }
}
```

### REST API

```bash
curl -H "Authorization: Bearer hag_REPLACE_WITH_TOKEN" \
     http://ha.lan:8098/api/v1/tools

curl -H "Authorization: Bearer hag_REPLACE_WITH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"tool":"ha.get_state","connection_id":"<id>","params":{"entity_id":"light.kitchen"}}' \
     http://ha.lan:8098/api/v1/operations
```

Every REST and MCP call is authorized by the same `PolicyService` and emits a redacted audit event. See [`docs/NETWORKING.md`](docs/NETWORKING.md) for firewall/TLS guidance.

---

## Tools

| Group | Tool | Risk | Approval | Scope |
| :--- | :--- | :--- | :--- | :--- |
| **Home Assistant** | `ha.get_state` | read | — | `ha:read` |
| | `ha.list_entities` | read | — | `ha:read` |
| | `ha.get_config` | read | — | `ha:read` |
| | `ha.call_service` | write | **required** | `ha:write` |
| **Proxmox VE** | `proxmox.list_nodes` | read | — | `proxmox:read` |
| | `proxmox.list_vms` | read | — | `proxmox:read` |
| | `proxmox.list_containers` | read | — | `proxmox:read` |
| | `proxmox.get_vm_status` | read | — | `proxmox:read` |
| | `proxmox.get_task_status` | read | — | `proxmox:read` |
| **SSH** | `ssh.service_status` | read | — | `ssh:read` |
| | `ssh.read_log` | read | — | `ssh:read` |
| | `ssh.run_command_template` | write | **required** | `ssh:write` |

`hassio`, `homeassistant`, `shell_command`, `python_script`, `system_log`, and `recorder` service domains are hard-blocked. Proxmox write operations and arbitrary shell do not exist (SSH `run_command_template` is template-only with allowlisted `argv` vectors).

Agents granted the `journal:read`/`journal:write` scopes and a `journal.*` policy rule additionally get **Project Journal** access — REST under `/api/v1/journal/*` and connectionless MCP tools (`journal.read_project_state`, `journal.record_work`, `journal.record_test_run`, `journal.generate_handoff`, …) through the same scope + policy + rate-limit + audit path. Journal records are redacted, immutable, and never grant permission to execute anything. See [`docs/JOURNAL.md`](docs/JOURNAL.md) and [`AGENTS.md`](AGENTS.md).

---

## Releases, changelog, and diagnostics

- **Version is a single source of truth:** `agent_gateway/config.yaml`, `agent_gateway/pyproject.toml`, `agent_gateway/Dockerfile`, `agent_gateway/src/gateway/__init__.py`, `CHANGELOG.md`, and `agent_gateway/CHANGELOG.md` are synchronized; CI enforces this.
- **Changelog:** [`CHANGELOG.md`](CHANGELOG.md) follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
- **Release notes in HA:** the Ingress UI surfaces `GET /api/admin/releases` (local Keep-a-Changelog parsing), on-demand `POST /api/admin/releases/check` (cached GitHub check), and a post-update banner dismissed via `POST /api/admin/releases/acknowledge`. Acknowledgement is intentionally installation-scoped (shared by the administrators of one add-on), never hides a genuinely newer version, and leaves the full history available under **Updates & Changelog**.
- **Tag → GitHub Release:** pushing a tag `v0.2.0` triggers `.github/workflows/release.yml` to create the GitHub Release from the changelog section.
- **Diagnostics:** `GET /readyz`, `GET /api/admin/readiness`, `GET /api/admin/diagnostics`, and `GET /api/admin/diagnostics/export` (`gateway-diagnostics-redacted.json`, recursively redacted, `hag-diagnostics-v1` format).

---

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e "agent_gateway[dev]"

# checks (all must pass before a PR)
.venv/bin/ruff check agent_gateway/src agent_gateway/tests
.venv/bin/ruff format --check agent_gateway/src agent_gateway/tests
(cd agent_gateway && ../.venv/bin/mypy src)
.venv/bin/pytest -q                 # 356 tests
node --check agent_gateway/src/gateway/ui/static/app.js

# run the gateway locally (development only)
PYTHONPATH=agent_gateway/src HAG_DATA_DIR="$PWD/data" .venv/bin/python -m gateway
```

Branding assets are generated offline with `tools/generate_branding.py` (no remote assets; `resvg` + `Pillow`).

CI: `lint + type-check + test` and `static + jsdom UI checks` → `amd64 image build + container startup smoke`. Releases are built by the Home Assistant builder in `build.yaml` (`ghcr.io/home-assistant/base:3.24`, `amd64` + `aarch64`).

---

## Security notes

- MFA, admin allowlist (`admin_user_ids`), Ingress peer check, `X-Gateway-Request: 1` CSRF guard, fail-closed validation.
- Rate limits on agent auth and per-policy `rate_limit_per_minute`.
- TLS verification and SSH `known_hosts` enforcement cannot be disabled from the UI.
- Full threat model: [`docs/SECURITY.md`](docs/SECURITY.md) and [`SECURITY.md`](SECURITY.md). A passing test suite is not a security audit.

---

## Verification log

Current 0.4.2 audit evidence (2026-10-24). Mocked upstreams and jsdom are not live integration tests.

| Check | Result |
| :--- | :--- |
| `ruff check` / `ruff format --check` / strict `mypy src` | **pass** |
| `pytest -q` | **354 passed** |
| jsdom UI smoke (`tools/ui-smoke`: panel, paths/errors, Agent Access, agent handoff, changelog) | **pass** — five harnesses, including 14 dismissal checks and no captured runtime errors |
| Process startup smoke (`tools/startup-smoke/smoke.py`) | **pass** — two start/readiness/SIGTERM/restart cycles on throwaway data; port-conflict probe exits nonzero |
| Python wheel build (`pip wheel --no-deps --no-build-isolation ./agent_gateway`) | **pass** |
| Docker/AppArmor container startup (`tools/startup-smoke/docker-smoke.sh`, CI `image` job) | **not run locally** — no Docker daemon or AppArmor tools; required CI evidence is tracked in `docs/WORKLOG.md` |
| Live HA OS deployment / real HA/Proxmox/SSH targets / third-party MCP client E2E / real browser | **not run** — infrastructure unavailable |
| Third-party security audit, penetration test, or fuzzing | **not performed** |

---

## License

MIT — see [LICENSE](LICENSE).
