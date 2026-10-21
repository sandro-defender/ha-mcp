// Manual smoke harness: renders the panel in jsdom against stubbed API
// responses. Not part of CI (needs the jsdom devDependency).
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";

const repo = process.env.REPO || "/home/user/ha-mcp";
const staticDir = `${repo}/agent_gateway/src/gateway/ui/static`;
const html = readFileSync(`${staticDir}/index.html`, "utf8");
const appJs = readFileSync(`${staticDir}/app.js`, "utf8");

const now = new Date().toISOString();

const statusUnlocked = {
  version: "0.1.0",
  uptime_seconds: 120,
  vault: {
    exists: true,
    unlocked: true,
    auto_unlock_enabled: false,
    auto_unlock_key_present: false,
    version: 1,
    records: 2,
    updated_at: now,
    kdf: { algorithm: "argon2id", memory_cost_kib: 65536, time_cost: 3, parallelism: 1 },
  },
  emergency_disabled: false,
  counts: { agents: 2, enabled_agents: 1, connections: 2, pending_approvals: 1 },
  listeners: { ingress_port: 8099, agent_port: 8098, agent_tls: false },
  tool_groups: [
    { id: "home_assistant", enabled: true, tools: ["ha.get_state", "ha.list_entities"] },
    { id: "proxmox", enabled: true, tools: ["proxmox.list_nodes"] },
    { id: "ssh", enabled: false, tools: ["ssh.service_status"] },
  ],
  settings: { audit_retention_days: 30, approval_ttl_seconds: 600 },
  recent_audit: [],
  security: {
    admin_allowlist_enforced: true,
    admin_user_restricted: false,
    agent_origin_allowlist: [],
    tls_verification: "enabled",
    ssh_host_key_verification: "enabled",
    arbitrary_shell: "disabled",
  },
};

const credentials = {
  items: [
    {
      id: "a".repeat(32),
      name: "HA read-only",
      kind: "home_assistant_token",
      masked: "abcd…wxyz",
      metadata: {},
      created_at: now,
      updated_at: now,
      used_by_connections: ["Home"],
    },
    {
      id: "b".repeat(32),
      name: "SSH key",
      kind: "ssh_private_key",
      masked: "private key stored",
      metadata: {},
      created_at: now,
      updated_at: now,
      used_by_connections: [],
    },
  ],
  kinds: ["ca_certificate", "generic", "home_assistant_token", "proxmox_token", "ssh_private_key"],
};

const connections = {
  items: [
    {
      id: "c".repeat(32),
      name: "Home",
      type: "home_assistant",
      config: { base_url: "http://homeassistant:8123", verify_tls: true },
      credential_id: "a".repeat(32),
      status: "unconfigured",
      last_check_at: null,
      last_check_message: null,
      created_at: now,
      updated_at: now,
    },
    {
      id: "d".repeat(32),
      name: "Edge SSH",
      type: "ssh",
      config: {
        host: "192.0.2.10",
        port: 22,
        username: "agentgw",
        allowed_services: ["nginx"],
        allowed_log_paths: ["/var/log/nginx/error.log"],
        command_templates: [],
      },
      credential_id: "b".repeat(32),
      status: "error",
      last_check_at: now,
      last_check_message: "connection refused",
      created_at: now,
      updated_at: now,
    },
  ],
};

const agents = {
  items: [
    {
      id: "e".repeat(32),
      name: "windows-claude",
      scopes: ["ha:read", "ha:write"],
      enabled: true,
      token_prefix: "hag_abcd",
      created_at: now,
      updated_at: now,
      expires_at: null,
      last_used_at: now,
      notes: "owner: ops",
      policy_rules: 2,
    },
    {
      id: "f".repeat(32),
      name: "old-agent",
      scopes: ["ha:read"],
      enabled: false,
      token_prefix: "revoked",
      created_at: now,
      updated_at: now,
      expires_at: null,
      last_used_at: null,
      notes: "",
      policy_rules: 0,
    },
  ],
};

const approvals = {
  items: [
    {
      id: "g".repeat(32),
      operation_id: "h".repeat(32),
      agent_id: "e".repeat(32),
      tool: "ha.call_service",
      connection_id: "c".repeat(32),
      params_summary: '{"domain":"light","service":"turn_on","data":{"entity_id":"light.kitchen"}}',
      status: "pending",
      created_at: now,
      expires_at: new Date(Date.now() + 300000).toISOString(),
      decided_at: null,
      decided_by: null,
      used_at: null,
      reason: "",
    },
  ],
  total: 1,
  limit: 100,
  offset: 0,
};

const audit = {
  items: [
    {
      id: 12,
      ts: now,
      actor_type: "agent",
      actor_id: "e".repeat(32),
      agent_id: "e".repeat(32),
      connection_id: "c".repeat(32),
      tool: "ha.get_state",
      params_summary: '{"entity_id":"light.kitchen"}',
      decision: "allow",
      reason: "",
      approval_id: null,
      status: "succeeded",
      duration_ms: 41,
      error_category: null,
      request_id: "req-1",
    },
    {
      id: 11,
      ts: now,
      actor_type: "admin",
      actor_id: "admin-user",
      agent_id: null,
      connection_id: null,
      tool: "connection.test",
      params_summary: "{}",
      decision: "test",
      reason: "connection refused",
      approval_id: null,
      status: "failed",
      duration_ms: 210,
      error_category: "connection_error",
      request_id: "req-2",
    },
  ],
  total: 2,
  limit: 8,
  offset: 0,
};

const responses = {
  status: statusUnlocked,
  connections,
  credentials,
  agents,
  approvals,
  audit,
  vault: statusUnlocked.vault,
  scopes: { scopes: [{ id: "ha:read", description: "read" }, { id: "ha:write", description: "write" }], credential_kinds: [] },
  "policy-templates": {
    templates: {
      home_assistant_read_all: { version: 1, rules: [{ id: "ha-read", effect: "allow", tools: ["ha.get_state"], connections: ["*"] }] },
      proxmox_read_all: { version: 1, rules: [] },
      ssh_read: { version: 1, rules: [] },
    },
  },
  tools: {
    groups: statusUnlocked.tool_groups,
    tools: [
      { name: "ha.get_state", group: "home_assistant", description: "Read state", risk: "read", scope: "ha:read", input_schema: {}, default_timeout_seconds: 20, default_max_output_bytes: 262144 },
      { name: "ha.call_service", group: "home_assistant", description: "Call service", risk: "write", scope: "ha:write", input_schema: {}, default_timeout_seconds: 20, default_max_output_bytes: 262144 },
    ],
  },
  settings: {
    settings: { emergency_disabled: false, auto_unlock: false, enabled_tool_groups: ["home_assistant", "proxmox", "ssh"], audit_retention_days: 30, approval_ttl_seconds: 600, max_operation_timeout_seconds: 120, max_output_bytes: 262144 },
    defaults: {},
    limits: { approval_ttl_seconds: [30, 3600], audit_retention_days: [1, 3650], max_operation_timeout_seconds: [5, 120], max_output_bytes: [1024, 1048576] },
  },
};

function lookup(url) {
  const path = url.split("api/admin/")[1]?.split("?")[0] || "";
  const key = path.replace(/\/$/, "");
  if (key.startsWith("agents/") && key.endsWith("/policy")) {
    return { document: { version: 1, rules: [] }, revision: 3 };
  }
  return responses[key];
}

const failures = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => failures.push(`jsdomError: ${error.message}`));
virtualConsole.on("error", (message) => failures.push(`console.error: ${message}`));

const dom = new JSDOM(html, {
  url: "https://ha.example/api/hassio_ingress/token/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;

window.addEventListener("error", (event) => failures.push(`window.error: ${event.message}`));
window.addEventListener("unhandledrejection", (event) => failures.push(`unhandled: ${event.reason}`));

const calls = [];
window.fetch = async (url, options = {}) => {
  calls.push(`${options.method || "GET"} ${url}`);
  const payload = lookup(String(url));
  if (payload === undefined) {
    return { ok: false, status: 404, headers: { get: () => "application/json" }, json: async () => ({ error: { code: "not_found", message: `no stub for ${url}` } }) };
  }
  return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => payload, blob: async () => ({}) };
};

window.eval(appJs);

const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));
const text = () => window.document.querySelector("#app").textContent;

async function run() {
  await tick(80);
  const report = [];
  const check = (label, condition) => {
    report.push(`${condition ? "ok  " : "FAIL"} ${label}`);
    if (!condition) failures.push(`check failed: ${label}`);
  };

  check("dashboard rendered", text().includes("Security posture"));
  check("pending approval metric", text().includes("Pending approvals"));
  check("audit table caption", window.document.querySelector("caption") !== null);
  check("aria-sort present", window.document.querySelector("[aria-sort]") !== null);

  for (const route of ["connections", "credentials", "tools", "permissions", "approvals", "audit", "settings"]) {
    const button = window.document.querySelector(`[data-route="${route}"]`);
    check(`nav ${route} exists`, Boolean(button));
    button?.click();
    await tick(80);
    check(`route ${route} rendered`, !text().includes("This section could not be loaded"));
  }

  // Connections: open the form for a new SSH connection.
  window.document.querySelector('[data-route="connections"]').click();
  await tick(60);
  check("connection test buttons", window.document.querySelectorAll("[data-test]").length === 2);
  window.document.querySelector("#add-connection").click();
  await tick(20);
  window.document.querySelector("#conn-type").value = "ssh";
  window.document.querySelector("#conn-type").dispatchEvent(new window.Event("change"));
  await tick(20);
  check("ssh form fields", window.document.querySelector("#ssh-host") !== null);
  check("ssh root hint", text().includes("Root is rejected"));

  // Credentials form validation path.
  window.document.querySelector('[data-route="credentials"]').click();
  await tick(60);
  window.document.querySelector("#credential-form").dispatchEvent(new window.Event("submit"));
  await tick(20);
  check("error summary", window.document.querySelector(".error-summary") !== null);
  check("aria-invalid set", window.document.querySelector('[aria-invalid="true"]') !== null);

  // Permissions: policy editor dialog.
  window.document.querySelector('[data-route="permissions"]').click();
  await tick(80);
  window.document.querySelector("[data-policy]").click();
  await tick(80);
  const modal = window.document.querySelector("#modal");
  check("policy dialog open", modal.getAttribute("aria-hidden") === "false");
  check("policy textarea", window.document.querySelector("#policy-json") !== null);
  window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await tick(20);
  check("escape closes dialog", modal.getAttribute("aria-hidden") === "true");

  // Approvals: approve opens confirm dialog.
  window.document.querySelector('[data-route="approvals"]').click();
  await tick(80);
  window.document.querySelector("[data-approve]").click();
  await tick(40);
  check("approve confirmation", window.document.querySelector("#modal").getAttribute("aria-hidden") === "false");
  check("approval shows params", text().includes("light.kitchen"));
  window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

  // Settings page renders vault controls.
  window.document.querySelector('[data-route="settings"]').click();
  await tick(80);
  check("vault export control", window.document.querySelector("#vault-export") !== null);
  check("rotation control", window.document.querySelector("#rotate-key") !== null);

  console.log(report.join("\n"));
  console.log(`\nfetch calls: ${calls.length}`);
  if (failures.length) {
    console.log("\nFAILURES:");
    failures.forEach((item) => console.log(` - ${item}`));
    process.exitCode = 1;
  } else {
    console.log("\nNo runtime errors captured.");
  }
}

run().catch((error) => {
  console.error("harness error:", error);
  process.exitCode = 1;
});
