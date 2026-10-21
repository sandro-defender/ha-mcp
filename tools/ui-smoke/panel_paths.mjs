// Manual smoke harness #2: setup / unlock / denial / write paths in jsdom.
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";

const repo = process.env.REPO || "/home/user/ha-mcp";
const staticDir = `${repo}/agent_gateway/src/gateway/ui/static`;
const html = readFileSync(`${staticDir}/index.html`, "utf8");
const appJs = readFileSync(`${staticDir}/app.js`, "utf8");
const now = new Date().toISOString();

const failures = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => failures.push(`jsdomError: ${e.message}`));

const dom = new JSDOM(html, {
  url: "https://ha.example/api/hassio_ingress/token/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;
window.addEventListener("error", (e) => failures.push(`window.error: ${e.message}`));
window.addEventListener("unhandledrejection", (e) => failures.push(`unhandled: ${e.reason}`));

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = () => window.document.querySelector("#app").textContent;
const check = (label, condition) => {
  if (!condition) failures.push(`check failed: ${label}`);
  console.log(`${condition ? "ok  " : "FAIL"} ${label}`);
};

const baseStatus = (overrides) => ({
  version: "0.1.0",
  uptime_seconds: 1,
  vault: { exists: true, unlocked: true, auto_unlock_enabled: false, auto_unlock_key_present: false, version: 1, records: 0, kdf: { algorithm: "argon2id", memory_cost_kib: 65536, time_cost: 3 } },
  emergency_disabled: false,
  counts: { agents: 0, enabled_agents: 0, connections: 0, pending_approvals: 0 },
  listeners: { ingress_port: 8099, agent_port: 8098, agent_tls: false },
  tool_groups: [{ id: "home_assistant", enabled: true, tools: [] }],
  settings: {},
  recent_audit: [],
  security: { admin_user_restricted: true, agent_origin_allowlist: [], tls_verification: "enabled", ssh_host_key_verification: "enabled", arbitrary_shell: "disabled" },
  ...overrides,
});

let statusPayload;
const posts = [];
window.fetch = async (url, options = {}) => {
  const path = String(url).split("api/admin/")[1]?.split("?")[0] || "";
  const method = options.method || "GET";
  if (method !== "GET") posts.push({ method, path, body: options.body ? JSON.parse(options.body) : null });
  const respond = (status, body) => ({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
  });
  if (path === "status") return respond(200, statusPayload);
  if (path === "vault/initialize") {
    statusPayload = baseStatus({});
    return respond(200, { created: true });
  }
  if (path === "vault/unlock") {
    const body = JSON.parse(options.body || "{}");
    if (body.password === "correct horse battery") {
      statusPayload = baseStatus({});
      return respond(200, { unlocked: true });
    }
    return respond(401, { error: { code: "vault_unlock_failed", message: "the password is incorrect" } });
  }
  if (path === "approvals/" + "g".repeat(32) + "/approve") return respond(200, { status: "approved" });
  if (path.startsWith("agents/") && path.endsWith("/policy")) {
    if (method === "PUT") {
      const body = JSON.parse(options.body || "{}");
      if (!body.document || !Array.isArray(body.document.rules)) {
        return respond(422, { error: { code: "validation_error", message: "policy document is invalid" } });
      }
      return respond(200, { revision: 4 });
    }
    return respond(200, { document: { version: 1, rules: [] }, revision: 3 });
  }
  if (path === "approvals") {
    return respond(200, {
      items: [
        {
          id: "g".repeat(32),
          operation_id: "h".repeat(32),
          agent_id: "e".repeat(32),
          tool: "ha.call_service",
          connection_id: "c".repeat(32),
          params_summary: '{"domain":"light"}',
          status: "pending",
          created_at: now,
          expires_at: new Date(Date.now() + 60000).toISOString(),
          decided_at: null,
          decided_by: null,
          used_at: null,
          reason: "",
        },
      ],
      total: 1,
    });
  }
  if (path === "agents") {
    return respond(200, {
      items: [
        {
          id: "e".repeat(32),
          name: "agent-one",
          scopes: ["ha:read"],
          enabled: true,
          token_prefix: "hag_1",
          created_at: now,
          updated_at: now,
          expires_at: null,
          last_used_at: null,
          notes: "",
          policy_rules: 0,
        },
      ],
    });
  }
  if (path === "scopes") return respond(200, { scopes: [{ id: "ha:read", description: "read" }] });
  if (path === "policy-templates") return respond(200, { templates: {} });
  if (path === "audit") return respond(200, { items: [], total: 0 });
  return respond(200, { items: [] });
};

async function scenarioSetup() {
  statusPayload = baseStatus({
    vault: { exists: false, unlocked: false, auto_unlock_enabled: false, auto_unlock_key_present: false, version: null, records: 0, kdf: { algorithm: "argon2id", memory_cost_kib: 65536, time_cost: 3 } },
  });
  window.eval(appJs);
  await tick(80);
  check("setup screen shown", text().includes("Create the credential vault"));

  window.document.querySelector("#setup-password").value = "short";
  window.document.querySelector("#setup-confirm").value = "short";
  window.document.querySelector("#setup-form").dispatchEvent(new window.Event("submit"));
  await tick(30);
  check("short password rejected client-side", window.document.querySelector(".error-summary") !== null);
  check("no initialize call for invalid password", !posts.some((p) => p.path === "vault/initialize"));

  window.document.querySelector("#setup-password").value = "correct horse battery";
  window.document.querySelector("#setup-confirm").value = "correct horse battery";
  window.document.querySelector("#setup-form").dispatchEvent(new window.Event("submit"));
  await tick(120);
  check("initialize posted", posts.some((p) => p.path === "vault/initialize"));
  check("shell after setup", text().includes("Security posture"));
}

async function scenarioUnlock() {
  statusPayload = baseStatus({
    vault: { exists: true, unlocked: false, auto_unlock_enabled: true, auto_unlock_key_present: true, version: 1, records: 1, kdf: { algorithm: "argon2id", memory_cost_kib: 65536, time_cost: 3 } },
  });
  window.eval(`(${(() => {
    // reset module state between scenarios is not possible; reload the page instead
    return "true;";
  })()})`);
  window.location.reload?.();
}

async function main() {
  await scenarioSetup();

  // Continue in the same document: lock the vault and reload the panel script.
  statusPayload = baseStatus({
    vault: { exists: true, unlocked: false, auto_unlock_enabled: true, auto_unlock_key_present: false, version: 1, records: 1, kdf: { algorithm: "argon2id", memory_cost_kib: 65536, time_cost: 3 } },
  });
  window.document.querySelector("#app").innerHTML = "";
  window.eval(appJs);
  await tick(80);
  check("unlock screen shown", text().includes("Unlock the vault"));
  check("auto-unlock warning", text().includes("Auto-unlock failed"));

  window.document.querySelector("#unlock-password").value = "wrong";
  window.document.querySelector("#unlock-form").dispatchEvent(new window.Event("submit"));
  await tick(80);
  check("wrong password message", text().includes("not correct") || text().includes("incorrect"));

  window.document.querySelector("#unlock-password").value = "correct horse battery";
  window.document.querySelector("#unlock-form").dispatchEvent(new window.Event("submit"));
  await tick(120);
  check("dashboard after unlock", text().includes("Security posture"));

  // Approvals: approve → confirm → POST.
  window.document.querySelector('[data-route="approvals"]').click();
  await tick(80);
  window.document.querySelector("[data-approve]").click();
  await tick(40);
  const confirm = window.document.querySelectorAll(".modal-footer .button");
  confirm[confirm.length - 1].click();
  await tick(120);
  check(
    "approve posted",
    posts.some((p) => p.method === "POST" && p.path === `approvals/${"g".repeat(32)}/approve`)
  );

  // Policy editor: invalid JSON is caught client-side, valid JSON posts.
  window.document.querySelector('[data-route="permissions"]').click();
  await tick(80);
  window.document.querySelector("[data-policy]").click();
  await tick(80);
  window.document.querySelector("#policy-json").value = "{not json";
  const footer = window.document.querySelectorAll(".modal-footer .button");
  footer[footer.length - 1].click();
  await tick(60);
  check("invalid JSON blocked", !posts.some((p) => p.method === "PUT" && p.path.endsWith("/policy")));
  check("invalid JSON message", window.document.querySelector(".error-summary") !== null);

  window.document.querySelector("#policy-json").value = JSON.stringify({ version: 1, rules: [] });
  const footer2 = window.document.querySelectorAll(".modal-footer .button");
  footer2[footer2.length - 1].click();
  await tick(120);
  check("policy saved", posts.some((p) => p.method === "PUT" && p.path.endsWith("/policy")));

  console.log(`\nrequests: ${posts.map((p) => `${p.method} ${p.path}`).join(", ")}`);
  if (failures.length) {
    console.log("\nFAILURES:");
    failures.forEach((item) => console.log(` - ${item}`));
    process.exitCode = 1;
  } else {
    console.log("\nNo runtime errors captured.");
  }
}

main().catch((error) => {
  console.error("harness error:", error);
  process.exitCode = 1;
});
