// Smoke harness: renders the panel in jsdom against stubbed API responses.
// Run locally with npm test; the CI UI job runs the same harness.
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
  "journal/project-state": {
    project: {
      id: "default", name: "Smoke project", purpose: "Panel smoke stub",
      architecture: "", conventions: "", constraints: "",
      branch: "main", commit_sha: "0123456789abcdef", revision: 2,
      created_at: now, updated_at: now,
      updated_by: { actor_type: "agent", actor_id: "smoke-agent", session_id: null },
    },
    counts: {
      tasks: { todo: 1, in_progress: 1, blocked: 0, completed: 0, failed: 0, cancelled: 0 },
      open_issues: 0, open_security_issues: 0, open_bugs: 0, failed_approaches: 0,
      entries: 1, unverified_entries: 1, decisions: 0, test_runs: 0, failed_test_runs: 0,
      active_sessions: 0, handoffs: 0, last_activity_at: now,
    },
    next_tasks: [
      {
        id: "c".repeat(32), title: "Smoke task", description: "", status: "in_progress",
        priority: 1, acceptance_criteria: "smoke passes", verification_level: "claimed",
        verified: false, dependencies: [], blocked_by: [], owner_name: "smoke-agent",
        owner_id: "smoke", branch: "main", commit_sha: null, files: [],
        created_at: now, updated_at: now, completed_at: null,
      },
    ],
    in_progress_tasks: [], blocked_tasks: [], recent_completed_tasks: [],
    open_issues: [], recent_decisions: [], recent_test_runs: [], recent_entries: [],
    active_sessions: [],
  },
  "journal/timeline": {
    items: [
      {
        record_type: "entry", id: "d".repeat(32), title: "Smoke entry", body: "stub body",
        status: "in_progress", kind: "progress", verification_level: "claimed", verified: false,
        actor_type: "agent", actor_id: "smoke", actor_name: "smoke-agent", source: "rest",
        session_id: null, task_id: null, commit_sha: null, files: [], created_at: now,
        evidence: null,
      },
    ],
    total: 1, limit: 50, offset: 0,
  },
  "journal/handoffs": { items: [], total: 0, limit: 10, offset: 0 },
  settings: {
    settings: { emergency_disabled: false, auto_unlock: false, enabled_tool_groups: ["home_assistant", "proxmox", "ssh"], audit_retention_days: 30, approval_ttl_seconds: 600, max_operation_timeout_seconds: 120, max_output_bytes: 262144 },
    defaults: {},
    limits: { approval_ttl_seconds: [30, 3600], audit_retention_days: [1, 3650], max_operation_timeout_seconds: [5, 120], max_output_bytes: [1024, 1048576] },
  },
  lifecycle: {
    state: "running",
    enabled: true,
    timeout_minutes: 30,
    timeout_range_minutes: [5, 1440],
    supervisor_available: true,
    inactivity_remaining_seconds: 1500,
    last_activity_at: now,
    last_activity_kind: "admin",
    inflight_agent_requests: 0,
    last_shutdown: null,
    failed_attempts: 0,
  },
  "ip/rules": {
    allowlist_mode: false,
    rules: [
      {
        id: "a".repeat(32),
        action: "deny",
        cidr: "203.0.113.0/24",
        note: "scanner range",
        created_at: now,
        created_by: "admin-user",
      },
    ],
    trusted_proxies: [],
    enforcement: {
      surface: "agent",
      precedence: "deny wins over allow; allowlist mode requires an allow rule",
      forwarded_headers: "trusted proxies only; otherwise socket peer",
    },
    attempts_max_rows: 5000,
  },
  "ip/attempts": {
    items: [
      { ts: now, ip: "203.0.113.9", listener: "agent", category: "agent_auth", decision: "denied", reason: "invalid token", agent_id: null },
      { ts: now, ip: "198.51.100.4", listener: "agent", category: "agent_api", decision: "allowed", reason: "authenticated agent request", agent_id: "f".repeat(32) },
      { ts: now, ip: "10.1.2.3", listener: "management", category: "management_gate", decision: "denied", reason: "peer not in admin_allowed_ips", agent_id: null },
    ],
    total: 3,
    limit: 25,
    offset: 0,
  },
};

function lookup(url) {
  const path = url.split("api/admin/")[1]?.split("?")[0] || "";
  const key = path.replace(/\/$/, "");
  if (key.startsWith("agents/") && key.endsWith("/policy")) {
    return { document: { version: 1, rules: [] }, revision: 3 };
  }
  if (key === "journal/handoffs/generate") {
    return { id: "e".repeat(32), title: "Smoke handoff", prompt: "# Continuation prompt\nSmoke stub prompt.", based_on_revision: 2, created_at: now };
  }
  if (key.startsWith("journal/handoffs/")) {
    return { id: "e".repeat(32), title: "Smoke handoff", prompt: "# Continuation prompt\nLoaded stub prompt.", based_on_revision: 2, created_at: now };
  }
  if (key === "lifecycle/settings") {
    return { state: "running", enabled: true, timeout_minutes: 45, timeout_range_minutes: [5, 1440], supervisor_available: true, inactivity_remaining_seconds: null, last_activity_at: now, last_activity_kind: "admin", inflight_agent_requests: 0, last_shutdown: null, failed_attempts: 0 };
  }
  if (key === "lifecycle/shutdown") {
    return { result: "stopping", confirmed: true, message: "the Supervisor accepted the stop request", state: "shutting_down", revoked_sessions: 0, hint: "Start it again from Home Assistant." };
  }
  if (key === "ip/settings") {
    return { allowlist_mode: true };
  }
  if (key.startsWith("ip/rules/")) {
    return {
      rule: { id: "a".repeat(32), action: "deny", cidr: "203.0.113.0/24", note: "edited", created_at: now, created_by: "admin-user" },
      deleted: "stub",
      revoked_sessions: [],
    };
  }
  if (key.startsWith("agents/") && key.endsWith("/revoke")) {
    return {};
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

  for (const route of ["connections", "credentials", "tools", "permissions", "approvals", "ipaccess", "audit", "journal", "settings"]) {
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

  // Project Journal: state, timeline, handoff generation.
  window.document.querySelector('[data-route="journal"]').click();
  await tick(100);
  check("journal project rendered", text().includes("Smoke project"));
  check("journal next step", text().includes("Smoke task"));
  check("journal timeline item", text().includes("Smoke entry"));
  check("journal verification badge", text().includes("claimed"));
  window.document.querySelector("#journal-handoff-form").dispatchEvent(new window.Event("submit"));
  await tick(80);
  check("journal handoff generated", window.document.querySelector("#journal-prompt").value.includes("Smoke stub prompt"));

  // Settings page renders vault controls.
  window.document.querySelector('[data-route="settings"]').click();
  await tick(80);
  check("vault export control", window.document.querySelector("#vault-export") !== null);
  check("rotation control", window.document.querySelector("#rotate-key") !== null);

  // Automatic shutdown card: status, warning, restart instructions, actions.
  check("lifecycle card rendered", text().includes("Automatic shutdown"));
  check("lifecycle UI warning", text().includes("Ingress UI becomes unavailable"));
  check("lifecycle supervisor status", text().includes("Supervisor API"));
  check("lifecycle restart instructions", text().includes("Home Assistant → Settings → Add-ons"));
  check("lifecycle manual control", window.document.querySelector("#shutdown-now") !== null);
  check("lifecycle boot warning", text().includes("start on boot"));

  // Timeout validation is enforced in the form before any request.
  window.document.querySelector("#lc-timeout").value = "1";
  window.document.querySelector("#lifecycle-form").dispatchEvent(new window.Event("submit"));
  await tick(30);
  check(
    "lifecycle timeout rejected",
    window.document.querySelector(".error-summary") !== null &&
      text().includes("between 5 and 1440")
  );

  // A valid save persists through the API.
  window.document.querySelector("#lc-timeout").value = "45";
  window.document.querySelector("#lifecycle-form").dispatchEvent(new window.Event("submit"));
  await tick(80);
  check(
    "lifecycle settings PUT sent",
    calls.some((call) => call.includes("PUT") && call.includes("api/admin/lifecycle/settings"))
  );

  // Manual shutdown requires confirmation and then calls the API.
  window.document.querySelector("#shutdown-now").click();
  await tick(40);
  const shutdownModal = window.document.querySelector("#modal");
  check("shutdown confirmation dialog", shutdownModal.getAttribute("aria-hidden") === "false");
  check(
    "shutdown dialog explains UI loss",
    shutdownModal.textContent.includes("Ingress UI becomes unavailable")
  );
  shutdownModal.querySelector(".modal-footer .button:last-child").click();
  await tick(80);
  check(
    "lifecycle shutdown POST sent",
    calls.some((call) => call.includes("POST") && call.includes("api/admin/lifecycle/shutdown"))
  );

  // Access Control page: enforcement card, rules CRUD, attempt history.
  window.document.querySelector('[data-route="ipaccess"]').click();
  await tick(80);
  check("ipaccess route rendered", !text().includes("This section could not be loaded"));
  check("ip precedence documented", text().includes("Deny always beats allow."));
  check("ip allowlist toggle", window.document.querySelector("#ip-allowlist") !== null);
  check("ip trusted proxy note", text().includes("socket peer address is always used"));
  check("ip rules table", text().includes("203.0.113.0/24"));
  check(
    "ip attempts rows rendered",
    window.document.querySelectorAll("#ip-attempts tbody tr").length === 3
  );
  check(
    "ip decision filter options",
    window.document.querySelector('#ip-decision option[value="rate_limited"]') !== null
  );
  check("ip attempt management row", text().includes("management_gate"));
  check("ip agent revoke button", window.document.querySelector("[data-ip-revoke]") !== null);
  check("ip pagination info", text().includes("1–3 of 3"));

  // Enforcement save issues a PUT with the toggle state.
  window.document.querySelector("#ip-allowlist").checked = true;
  window.document.querySelector("#ip-settings-form").dispatchEvent(new window.Event("submit"));
  await tick(60);
  check(
    "ip settings PUT sent",
    calls.some((call) => call.includes("PUT") && call.includes("api/admin/ip/settings"))
  );

  // Rule create posts the form values.
  window.document.querySelector("#ip-rule-cidr").value = "198.51.100.0/24";
  window.document.querySelector("#ip-rule-form").dispatchEvent(new window.Event("submit"));
  await tick(80);
  check(
    "ip rule POST sent",
    calls.some((call) => call.includes("POST") && call.includes("api/admin/ip/rules"))
  );

  // Editing loads the rule into the form and saves via PATCH.
  window.document.querySelector("[data-ip-edit]").click();
  await tick(30);
  check(
    "ip rule edit prefills form",
    window.document.querySelector("#ip-rule-cidr").value === "203.0.113.0/24"
  );
  window.document.querySelector("#ip-rule-form").dispatchEvent(new window.Event("submit"));
  await tick(60);
  check(
    "ip rule PATCH sent",
    calls.some((call) => call.includes("PATCH") && call.includes("api/admin/ip/rules/"))
  );

  // Remove asks for confirmation and issues DELETE.
  window.document.querySelector("[data-ip-remove]").click();
  await tick(40);
  const ipModal = window.document.querySelector("#modal");
  check("ip remove confirmation dialog", ipModal.getAttribute("aria-hidden") === "false");
  ipModal.querySelector(".modal-footer .button:last-child").click();
  await tick(80);
  check(
    "ip rule DELETE sent",
    calls.some((call) => call.includes("DELETE") && call.includes("api/admin/ip/rules/"))
  );

  // Attempt filters pass the decision to the API.
  window.document.querySelector("#ip-decision").value = "denied";
  window.document.querySelector("#ip-attempt-filters").dispatchEvent(new window.Event("submit"));
  await tick(60);
  check(
    "ip attempts filter sent",
    calls.some((call) => call.includes("ip/attempts") && call.includes("decision=denied"))
  );

  // Revoking an agent from the history asks first, then calls the API.
  window.document.querySelector("[data-ip-revoke]").click();
  await tick(40);
  const revokeModal = window.document.querySelector("#modal");
  check("ip revoke confirmation dialog", revokeModal.getAttribute("aria-hidden") === "false");
  revokeModal.querySelector(".modal-footer .button:last-child").click();
  await tick(80);
  check(
    "ip agent revoke POST sent",
    calls.some((call) => call.includes("POST") && call.includes("revoke"))
  );

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
