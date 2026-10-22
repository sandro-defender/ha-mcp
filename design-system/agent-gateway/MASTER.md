# Design System Master: Home Assistant Agent Gateway

Generated and refined with the **UI UX Pro Max** workflow (`nextlevelbuilder/ui-ux-pro-max-skill`, `--design-system --density 8 -p "Agent Gateway"`) for a security-critical Home Assistant OS Ingress application.

## 1. Product Aesthetic & Direction

- **Archetype**: Native Home Assistant Operational Control Plane & Security Boundary
- **Tone**: Technical, calm, trustworthy, compact, and explicit about risk and authority
- **Theme Support**:
  - **System (`data-theme="system"`)**: Follows OS/Home Assistant `prefers-color-scheme`
  - **Light (`data-theme="light"`)**: Crisp slate-white surfaces (`#F8FAFC` / `#FFFFFF`) with high-contrast ink (`#0F172A`)
  - **Dark (`data-theme="dark"`)**: Deep slate surfaces (`#0B1120` / `#0F172A`) with luminous sky-cyan accents (`#38BDF8`)
- **Typography**: System UI sans-serif (`system-ui`, `-apple-system`, `Segoe UI`, `Roboto`, `Inter`) paired with tabular monospace (`ui-monospace`, `SFMono-Regular`, `Menlo`, `Consolas`) for entity IDs, SHA-256 digests, fingerprints, ports, and JSON schemas.

## 2. Color Tokens & WCAG AA Contrast Invariants

All token pairs are verified in `agent_gateway/tests/test_ui_assets.py` for WCAG AA compliance ($\ge 4.5:1$ text contrast, $\ge 3.0:1$ border/focus contrast):

| Token | Light Mode | Dark Mode | Purpose |
| :--- | :--- | :--- | :--- |
| `--color-bg` | `#f8fafc` | `#0b1120` | Primary application canvas |
| `--color-surface` | `#ffffff` | `#0f172a` | Cards, tables, and modal surfaces |
| `--color-surface-muted` | `#f1f5f9` | `#1e293b` | Secondary headers, code blocks, filter bars |
| `--color-text` | `#0f172a` | `#f8fafc` | Primary body and heading text |
| `--color-text-muted` | `#475569` | `#94a3b8` | Secondary metadata and timestamps |
| `--color-border` | `#64748b` | `#64748b` | Hairline borders and control outlines |
| `--color-accent` | `#005a9e` | `#38bdf8` | Primary interactive accent and focus ring |
| `--color-accent-ink` | `#ffffff` | `#06121e` | Text on primary accent buttons |
| `--color-success` | `#15803d` | `#4ade80` | Verified connections, allowed reads, active tokens |
| `--color-warning` | `#b45309` | `#fbbf24` | Pending human approvals, expiring tokens, rotation reminders |
| `--color-danger` | `#b91c1c` | `#f87171` | Policy denials, auth failures, emergency stop, revocation |
| `--color-focus` | `#005a9e` | `#38bdf8` | `:focus-visible` keyboard outline |

## 3. 10-Section Information Architecture

The sidebar navigation organizes the gateway into 10 dedicated operational workspaces:

1. **Overview (`#overview` / `#dashboard`)**: Post-update release banner, proactive security warnings, health telemetry cards, security posture checklist, pending approval queue preview, recent activity, and GitHub links.
2. **Connections (`#connections`)**: Home Assistant, Proxmox VE, and non-root SSH targets with multi-step connection diagnostics, latency tracking, error classification, `last_ok_at` timestamps, and SSH `known_hosts` enrollment.
3. **Credentials (`#credentials`)**: AES-256-GCM + Argon2id vault lifecycle, encrypted backup/restore, write-only secret storage, connection reference tracking, and 90-day rotation reminders.
4. **Agents (`#agents`)**: Individual agent identities, descriptions, scoped permissions, allowed/denied tool patterns, token expiry warnings, token rotation, immediate revocation, and per-agent request/denial/rate-limit counters.
5. **Tools (`#tools`)**: Tool group toggles, 12-tool catalog with risk & approval classification, JSON Schema inspector, and copy-ready MCP JSON-RPC, `curl`, and Windows PowerShell snippets.
6. **Permissions (`#permissions`)**: Visual allow/deny policy rule builder, starter templates, raw JSON policy editor, and an interactive **Policy Simulator** (`POST /api/admin/policies/evaluate`).
7. **Approvals (`#approvals`)**: Pending human approval queue with requester identity, target connection, risk badge, redacted parameter summary, SHA-256 request hash, expiration countdown, and single-use Approve / Reject controls.
8. **Audit Log (`#audit`)**: Searchable, filterable, paginated SQLite WAL audit log with actor/tool/status filters, detail inspection modal, redaction verification, and JSON export.
9. **Updates and Changelog (`#updates`)**: Installed vs. latest version status, on-demand cached GitHub release check, post-update notice & migration notes, structured Keep-a-Changelog release history, and repository links.
10. **Settings and Diagnostics (`#settings`)**: Theme selector (`System`, `Light`, `Dark`), TTL/retention/timeout bounds, readiness probes (`/readyz`), and redacted diagnostic bundle export (`gateway-diagnostics-redacted.json`).
