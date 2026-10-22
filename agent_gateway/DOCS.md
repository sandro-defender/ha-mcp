# Home Assistant Agent Gateway — Documentation

**Home Assistant Agent Gateway** is a local permission, approval, credential, and audit gateway for external AI agents (such as Claude Desktop, Cursor, OpenAI/MCP clients, or custom automation agents running on Windows, macOS, or Linux) that need controlled access to your smart-home and homelab infrastructure.

- **Repository**: [https://github.com/sandro-defender/ha-mcp](https://github.com/sandro-defender/ha-mcp)
- **Releases & Changelog**: [https://github.com/sandro-defender/ha-mcp/releases](https://github.com/sandro-defender/ha-mcp/releases)
- **Issues & Support**: [https://github.com/sandro-defender/ha-mcp/issues](https://github.com/sandro-defender/ha-mcp/issues)
- **Security Policy**: [https://github.com/sandro-defender/ha-mcp/blob/main/SECURITY.md](https://github.com/sandro-defender/ha-mcp/blob/main/SECURITY.md)

---

## How It Works

External AI agents never receive your Home Assistant long-lived access tokens, Proxmox VE API secrets, or SSH private keys. Instead:

1. **You manage secrets and policies inside Home Assistant Ingress** (`:8099`, reachable only through the Home Assistant Supervisor proxy).
2. **External agents authenticate to the Agent API listener** (`:8098`, disabled on the host network by default until you map the port on a trusted LAN/VPN) using a scoped, revocable `hag_...` bearer token.
3. **Every tool call is evaluated against a deny-by-default policy engine**:
   - **Read-only operations** (`ha.get_state`, `ha.list_entities`, `ha.get_config`, `proxmox.*`, `ssh.service_status`, `ssh.read_log`) execute immediately when both the agent's scope and policy allow the target.
   - **Write operations** (`ha.call_service`, `ssh.run_template`) always create a **single-use, parameter-bound, time-bounded approval request** that a human administrator must approve in the Ingress UI before execution.
   - **Destructive Proxmox operations and arbitrary shell commands** do not exist in the tool registry.

---

## Installation in Home Assistant OS

1. In Home Assistant, navigate to **Settings → Add-ons → Add-on Store** (or **Settings → Apps**).
2. Open the top-right menu (`⋮`) → **Repositories** and add:
   ```text
   https://github.com/sandro-defender/ha-mcp
   ```
3. Select **Home Assistant Agent Gateway** and click **Install**.
4. Review the **Configuration** tab (options documented below), start the add-on, and click **Open Web UI** (or enable **Show in sidebar**).

---

## Add-on Configuration Options

All options in the Home Assistant **Configuration** tab are validated at startup. Invalid values cause the add-on to fail closed rather than start with weakened security.

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `log_level` | `debug \| info \| warning \| error` | `info` | Verbosity of container stdout logs. All log lines pass through a secret redaction filter. |
| `admin_user_ids` | `list(str)` | `[]` | Optional list of Home Assistant user IDs allowed to access the Ingress management UI. When empty, any Home Assistant administrator who can open the Ingress panel is permitted. |
| `tls_certfile` | `str` | `""` | Optional path to a PEM certificate chain (for example `/ssl/fullchain.pem`) to enable native TLS on the `:8098` agent listener. Must be set together with `tls_keyfile`. |
| `tls_keyfile` | `str` | `""` | Optional path to the matching PEM private key (for example `/ssl/privkey.pem`). |
| `audit_retention_days` | `int(1, 3650)` | `30` | Number of days to retain audit log events and completed approval records before automatic background purge. |
| `approval_ttl_seconds` | `int(30, 3600)` | `600` | Default expiration window (in seconds) for pending human approval requests (`600` = 10 minutes). |

### Example Add-on Configuration

```yaml
log_level: info
admin_user_ids: []
tls_certfile: ""
tls_keyfile: ""
audit_retention_days: 30
approval_ttl_seconds: 600
```

### Network Port (`8098/tcp`)

- By default, `8098/tcp` is set to `null` (disabled on the host) so the agent API is not exposed until you finishes configuring your vault and policies.
- To allow an external agent on your LAN or VPN (for example a Windows workstation running Claude Desktop or Cursor) to connect, set the host port for `8098/tcp` in the **Network** section of the add-on **Configuration** tab, and restrict access to a trusted VLAN, WireGuard, or Tailscale network.
- **Never port-forward `8098/tcp` or `8099/tcp` directly to the public internet.**

---

## First-Run Setup Walkthrough

1. **Initialize the Credential Vault (`Credentials` tab)**:
   - Choose a master password of at least **12 characters** (`Argon2id` derives a 256-bit key Encryption Key; `AES-256-GCM` encrypts each secret record).
   - Export an encrypted vault backup (`vault-backup.hag.json`) and store it alongside your password manager entry.
2. **Store Upstream Credentials (`Credentials` tab)**:
   - Add a `home_assistant_token` (Long-Lived Access Token), `proxmox_token` (`user@realm!tokenname` + secret), or `ssh_private_key`.
   - Once saved, secret values are never returned by any API or displayed again in the UI.
3. **Create & Diagnose Connections (`Connections` tab)**:
   - Create your Home Assistant, Proxmox VE, or non-root SSH connection profile.
   - For SSH targets, click **Scan Host Key** and verify the fingerprint before clicking **Trust Key**.
   - Click **Run Diagnostics** to verify transport, TLS/SSH trust, authentication, and API readiness.
4. **Create an Agent & Policy (`Agents` & `Permissions` tabs)**:
   - Create an individual identity per AI client (for example `windows-claude-desktop`).
   - Copy the `hag_...` bearer token immediately (it is shown once).
   - Assign fine-grained allow/deny rules in **Permissions** and test them with the built-in **Policy Simulator**.

---

## Connecting External Agents (Including Windows Clients)

### MCP Streamable HTTP (`POST /mcp`)

- **Endpoint**: `http://<HOME-ASSISTANT-LAN-IP>:8098/mcp` (or `https://...` when TLS is configured)
- **Protocol Version**: `2025-06-18`
- **Authentication**: `Authorization: Bearer hag_<your_agent_token>`

Example PowerShell check from a Windows agent host:

```powershell
$Headers = @{
  "Authorization"        = "Bearer hag_REPLACE_WITH_TOKEN"
  "Content-Type"         = "application/json"
  "Accept"               = "application/json, text/event-stream"
  "MCP-Protocol-Version" = "2025-06-18"
}
$Body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
Invoke-RestMethod -Method Post -Uri "http://homeassistant.local:8098/mcp" -Headers $Headers -Body $Body
```

### Agent REST API (`/api/v1/*`)

- `GET /api/v1/health` — Authenticated health check
- `GET /api/v1/connections` — List connections visible to the agent (IDs, names, types, health status only)
- `GET /api/v1/tools` — List tools permitted by the agent's scopes and enabled tool groups
- `POST /api/v1/operations` — Execute a tool or request approval (`200 OK` on immediate read execution, `202 Accepted` with `approval_id` when human approval is required)
- `GET /api/v1/approvals/{approval_id}` — Poll approval status (`pending`, `approved`, `rejected`, `expired`, `used`)

---

## Backup, Recovery, and Limitations

- **Persistent Storage**: All persistent state lives in `/data` (`gateway.db`, `vault.json`, `known_hosts`, and optional `unlock.key` with `0600` permissions). Home Assistant OS backups of this add-on include `/data`.
- **Vault Recovery**: If the master password is lost and no auto-unlock key exists, encrypted credentials in `vault.json` cannot be decrypted by design. You must re-initialize the vault and re-enter upstream tokens.
- **Intentional Limitations**:
  - No arbitrary shell execution over SSH (only allowlisted `systemctl is-active`/`status`, allowlisted log paths, and admin-defined `argv` templates).
  - No Proxmox VE write or destructive tools in v0.2.0.
  - Home Assistant domains `hassio`, `homeassistant`, `shell_command`, `command_line`, `python_script`, `recorder`, `system_log`, and `persistent_notification` are hard-blocked even if a policy rule allows `*`.
