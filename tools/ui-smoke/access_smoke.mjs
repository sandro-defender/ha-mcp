import { readFileSync } from "node:fs";

const env = process.env;
import { JSDOM, VirtualConsole } from "jsdom";

const repo = env.REPO || "/home/user/ha-mcp";
const dir = `${repo}/agent_gateway/src/gateway/ui/static`;
const html = readFileSync(`${dir}/index.html`, "utf8");
const appJs = readFileSync(`${dir}/app.js`, "utf8");
const now = new Date().toISOString();

const status = {
  version: "0.4.0", uptime_seconds: 60,
  vault: { exists: true, unlocked: true, auto_unlock_enabled: false, auto_unlock_key_present: false, version: 1, records: 1, updated_at: now, kdf: {} },
  emergency_disabled: false,
  counts: { agents: 1, enabled_agents: 1, connections: 1, pending_approvals: 0 },
  listeners: { ingress_port: 8099, agent_port: 8098, agent_tls: false },
  tool_groups: [], settings: {}, recent_audit: [],
  security: { admin_allowlist_enforced: true, admin_user_restricted: false, agent_origin_allowlist: [], tls_verification: "enabled", ssh_host_key_verification: "enabled", arbitrary_shell: "disabled" },
  agent_access: { active_sessions: 1, revoke_on_restart: true, agent_api_base_url_set: true, last_restart_policy: { policy: "revoke_on_restart", revoked: 0, session_epoch: 1 } },
};

const cid = "a".repeat(32);
const session = {
  id: "b".repeat(32), name: "codex-session", kind: "temporary", status: "pending", enabled: true,
  token_prefix: "pending", scopes: ["ha:read"], operation_classes: ["read"],
  allowed_connection_ids: [cid], created_at: now, created_by: "admin-user",
  expires_at: now, remaining_seconds: 3500, last_used_at: null, revoked_at: null,
  revoked_reason: "", notes: "", issued_ttl_seconds: 3600, handoff_pending: true,
  handoff_expires_at: now, connections: [{ id: cid, name: "Home", type: "home_assistant" }],
  requests_last_hour: 0,
};

const overview = {
  settings: { default_ttl_seconds: 3600, max_ttl_seconds: 86400, min_ttl_seconds: 300, hard_max_ttl_seconds: 604800,
    handoff_ttl_seconds: 900, revoke_on_restart: true, agent_api_base_url: "http://ha.local:8098",
    mcp_path: "/mcp", api_prefix: "/api/v1", agent_page_path: "/agent", transport: "streamable-http",
    listener: { host: "0.0.0.0", port: 8098, tls: false } },
  sessions: [session], active_count: 0, pending_count: 1,
  connections: [{ id: cid, name: "Home", type: "home_assistant", status: "healthy" }],
  scopes: [{ id: "ha:read", description: "read" }],
  last_reachability: null,
  security_notes: ["note"],
  endpoints: { listener_reachable: true, listener_url: "http://127.0.0.1:8098/healthz", detail: "HTTP 200",
    external_url_configured: true, note: "probe note" },
  restart_policy_result: { policy: "revoke_on_restart", revoked: 0, session_epoch: 1 },
};

const issued = {
  session: { ...session, id: "c".repeat(32) },
  handoff: { url: "http://ha.local:8098/agent#code=hag_h_" + "z".repeat(43), expires_at: now, single_use: true },
  connection_config: { agent_page_url: "http://ha.local:8098/agent", mcp_url: "http://ha.local:8098/mcp", notes: [] },
  policy: { document: { version: 1, rules: [] } },
  settings: overview.settings,
};

const responses = {
  status, "audit?limit=8": { items: [], total: 0 },
  "agent-access": overview, "agent-access/sessions": issued,
  "agent-access/revoke-all": { revoked: 1, session_ids: ["b".repeat(32)] },
  "agent-access/settings": overview.settings,
  "agent-access/reachability": overview.endpoints,
};
function lookup(url) {
  const path = (url.split("api/admin/")[1] || "").split("?")[0].replace(/\/$/, "");
  if (path.startsWith("agent-access/sessions/") && path.endsWith("/revoke")) return { session };
  if (path.startsWith("agent-access/sessions/") && path.endsWith("/activity")) return { session_id: "x", items: [] };
  return responses[path] ?? responses[path + "?limit=8"];
}

const failures = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => failures.push("jsdomError: " + e.message));
vc.on("error", (m) => failures.push("console.error: " + m));
const dom = new JSDOM(html, { url: "https://ha.example/api/hassio_ingress/token/", runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
const { window } = dom;
window.addEventListener("error", (e) => failures.push("window.error: " + e.message));
window.addEventListener("unhandledrejection", (e) => failures.push("unhandled: " + e.reason));
const calls = [];
window.fetch = async (url, options = {}) => {
  calls.push(`${options.method || "GET"} ${url}`);
  const payload = lookup(String(url));
  if (payload === undefined) return { ok: false, status: 404, headers: { get: () => "application/json" }, json: async () => ({ error: { code: "not_found", message: "no stub " + url } }) };
  return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => payload };
};
window.eval(appJs);

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = () => window.document.querySelector("#app").textContent;

await tick(120);
const report = [];
const check = (label, ok) => { report.push(`${ok ? "ok  " : "FAIL"} ${label}`); if (!ok) failures.push(label); };

check("dashboard", text().includes("Security posture"));
const nav = window.document.querySelector('[data-route="access"]');
check("nav access exists", Boolean(nav));
nav.click();
await tick(150);
const body = window.document.querySelector("#section").textContent;
check("access page rendered", body.includes("Generate temporary access"));
check("no error state", !body.includes("This section could not be loaded"));
check("settings form present", Boolean(window.document.querySelector("#access-settings-form")));
check("base url shown", window.document.querySelector("#access-base-url").value === "http://ha.local:8098");
check("session row present", body.includes("codex-session"));
check("restart policy label", body.includes("revoke temporary sessions on restart"));

// Fill and submit the issue form.
window.document.querySelector("#access-name").value = "codex-session-2";
window.document.querySelectorAll("#access-connections input").forEach((i) => { i.checked = true; i.dispatchEvent(new window.Event("change")); });
window.document.querySelectorAll("#access-scopes input").forEach((i) => { i.checked = true; i.dispatchEvent(new window.Event("change")); });
window.document.querySelector("#access-form").dispatchEvent(new window.Event("submit"));
await tick(200);

const modal = window.document.querySelector("#modal");
check("modal opened", modal && !modal.classList.contains("hidden"));
check("handoff link rendered", (modal?.textContent || "").includes("#code=hag_h_"));
check("token stays on the agent page", (modal?.textContent || "").includes("not the token"));
check("POST issued", calls.some((c) => c.startsWith("POST api/admin/agent-access/sessions")));

// Revoke flow: the confirmation dialog must be explicit and then issue the POST.
window.document.querySelector("#modal-close").click();
await tick(40);
const revokeButton = window.document.querySelector("[data-session-revoke]");
check("revoke button present", Boolean(revokeButton));
revokeButton.click();
await tick(60);
const confirmText = window.document.querySelector("#modal").textContent;
check("revoke confirmation explains impact", confirmText.includes("stops working immediately"));
const confirmButton = [...window.document.querySelectorAll("#modal-actions .button")].find((b) => b.textContent.includes("Revoke session"));
check("revoke confirm button", Boolean(confirmButton));
confirmButton.click();
await tick(180);
check("revoke POST issued", calls.some((c) => c.startsWith("POST api/admin/agent-access/sessions/") && c.endsWith("/revoke")));

console.log(report.join("\n"));
if (failures.length) { console.error("\nFAILURES:\n" + failures.join("\n")); process.exit(1); }
console.log("\nRESULT: PASS (agent access page renders, issues, shows handoff)");
