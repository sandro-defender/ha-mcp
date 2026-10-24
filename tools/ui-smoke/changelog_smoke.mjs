// Regression harness for the post-update/changelog dismissal bug.
//
// The dashboard renders the "What's New" notice from the state.status snapshot
// captured at boot. A dismiss handler that persisted the acknowledgement but
// did not refresh that snapshot left the banner visible immediately and on
// every revisit. Fresh deep copies from this stub reproduce that condition.
// Run: cd tools/ui-smoke && npm install && node changelog_smoke.mjs
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";

const repo = process.env.REPO || "/home/user/ha-mcp";
const staticDir = `${repo}/agent_gateway/src/gateway/ui/static`;
const html = readFileSync(`${staticDir}/index.html`, "utf8");
const appJs = readFileSync(`${staticDir}/app.js`, "utf8");
const now = new Date().toISOString();
const failures = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => failures.push(`jsdomError: ${error.message}`));
virtualConsole.on("error", (message) => failures.push(`console.error: ${message}`));

const server = { installedVersion: "0.4.1", previousVersion: "0.4.0", seenVersion: "0.4.0", latestVersion: "0.4.1" };
let ackFails = false;
let statusFailsAfterAck = false;
const showNotice = () => server.seenVersion !== server.installedVersion;

function releaseSummary() {
  return {
    installed_version: server.installedVersion,
    installed_at: now,
    previous_version: server.previousVersion,
    latest_version: server.latestVersion,
    update_available: false,
    check_status: "local_only",
    checked_at: null,
    check_message: "Release notes loaded from local verified CHANGELOG.md.",
    release_url: "https://github.com/sandro-defender/ha-mcp/releases/tag/v0.4.1",
    links: { repository: "https://github.com/sandro-defender/ha-mcp", changelog: "https://example/CHANGELOG.md" },
    schema: { version: 4 },
    post_update_notice: {
      show: showNotice(), version: server.installedVersion, previous_version: server.previousVersion,
      date: "2026-10-23", highlights: ["Fixed the post-update notice dismissal bug."],
      migration_notes: ["No manual configuration changes are required."], breaking: false,
      acknowledged_version: server.seenVersion, schema_version: 4,
    },
    current_release: { version: server.installedVersion, date: "2026-10-23", is_current: true, sections: {} },
    unreleased: null,
    releases: [{ version: server.installedVersion, date: "2026-10-23", is_current: true, sections: {}, highlights: ["Fixed the notice."] }],
  };
}

function statusPayload() {
  const release = releaseSummary();
  return {
    version: server.installedVersion,
    uptime_seconds: 120,
    vault: {
      exists: true, unlocked: true, auto_unlock_enabled: false, auto_unlock_key_present: false,
      version: 1, records: 2, updated_at: now,
      kdf: { algorithm: "argon2id", memory_cost_kib: 65536, time_cost: 3, parallelism: 1 },
    },
    emergency_disabled: false,
    counts: { agents: 2, enabled_agents: 1, connections: 2, pending_approvals: 0 },
    listeners: { ingress_port: 8099, agent_port: 8098, agent_tls: false },
    tool_groups: [{ id: "home_assistant", enabled: true, tools: ["ha.get_state"] }],
    settings: { audit_retention_days: 30, approval_ttl_seconds: 600 },
    recent_audit: [],
    security: {
      admin_allowlist_enforced: true, admin_user_restricted: false, agent_origin_allowlist: [],
      tls_verification: "enabled", ssh_host_key_verification: "enabled", arbitrary_shell: "disabled",
    },
    release: {
      installed_version: release.installed_version, latest_version: release.latest_version,
      update_available: false, check_status: "local_only", checked_at: null,
      post_update_notice: release.post_update_notice, links: release.links,
    },
  };
}

function createPanel() {
  const dom = new JSDOM(html, {
    url: "https://ha.example/api/hassio_ingress/token/", runScripts: "outside-only",
    pretendToBeVisual: true, virtualConsole,
  });
  const { window } = dom;
  window.addEventListener("error", (event) => failures.push(`window.error: ${event.message}`));
  window.addEventListener("unhandledrejection", (event) => failures.push(`unhandled: ${event.reason}`));
  const calls = [];
  window.fetch = async (url, options = {}) => {
    const key = (String(url).split("api/admin/")[1]?.split("?")[0] || "").replace(/\/$/, "");
    calls.push(`${options.method || "GET"} ${key}`);
    if (key === "releases/acknowledge") {
      if (ackFails) {
        return { ok: false, status: 500, headers: { get: () => "application/json" }, json: async () => ({ error: { code: "internal_error", message: "synthetic persistence failure" }, request_id: "req-ack" }) };
      }
      server.seenVersion = server.installedVersion;
      return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => releaseSummary() };
    }
    let payload;
    if (key === "status") {
      if (statusFailsAfterAck && calls.some((call) => call.includes("releases/acknowledge"))) {
        return { ok: false, status: 500, headers: { get: () => "application/json" }, json: async () => ({ error: { code: "internal_error", message: "synthetic status refresh failure" }, request_id: "req-status" }) };
      }
      payload = statusPayload();
    }
    else if (key === "releases") payload = releaseSummary();
    else if (key.startsWith("audit")) payload = { items: [], total: 0, limit: 8, offset: 0 };
    else return { ok: false, status: 404, headers: { get: () => "application/json" }, json: async () => ({ error: { code: "not_found", message: `no stub for ${key}` } }) };
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => JSON.parse(JSON.stringify(payload)) };
  };
  window.eval(appJs);
  return { window, calls };
}

const tick = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));
const noticeVisible = (window) => window.document.querySelector("#app").textContent.includes("What's New in v");
function check(label, condition) {
  console.log(`${condition ? "ok  " : "FAIL"} ${label}`);
  if (!condition) failures.push(`check failed: ${label}`);
}

async function run() {
  {
    const { window, calls } = createPanel();
    await tick(140);
    check("S1 notice visible on dashboard before dismiss", noticeVisible(window));
    const button = window.document.getElementById("dismiss-banner");
    check("S1 dashboard dismiss control exists", Boolean(button));
    button?.click();
    await tick(180);
    check("S1 notice hidden immediately after dashboard dismiss", !noticeVisible(window));
    check("S1 acknowledge request sent", calls.some((call) => call.includes("releases/acknowledge")));
    window.document.querySelector('[data-route="connections"]').click();
    await tick(90);
    window.document.querySelector('[data-route="dashboard"]').click();
    await tick(140);
    check("S1 notice does not reappear on dashboard revisit", !noticeVisible(window));
  }
  {
    server.installedVersion = "0.5.0";
    server.seenVersion = "0.4.1";
    const { window, calls } = createPanel();
    await tick(140);
    check("S2 notice visible for a genuinely new release", noticeVisible(window));
    window.document.querySelector('[data-route="updates"]').click();
    await tick(160);
    check("S2 notice visible in Updates section", noticeVisible(window));
    window.document.getElementById("ack-release")?.click();
    await tick(180);
    check("S2 notice hidden after Updates dismiss", !noticeVisible(window));
    check("S2 acknowledge request sent", calls.some((call) => call.includes("releases/acknowledge")));
    window.document.querySelector('[data-route="dashboard"]').click();
    await tick(160);
    check("S2 dashboard clean after Updates dismiss", !noticeVisible(window));
  }
  {
    server.installedVersion = "0.6.0";
    server.seenVersion = "0.5.0";
    ackFails = true;
    const { window, calls } = createPanel();
    await tick(140);
    check("S3 notice visible before failed dismiss", noticeVisible(window));
    window.document.getElementById("dismiss-banner")?.click();
    await tick(180);
    check("S3 notice still visible after failed acknowledge", noticeVisible(window));
    check("S3 no acknowledge persisted server-side", server.seenVersion !== server.installedVersion);
    check("S3 acknowledge request attempted", calls.some((call) => call.includes("releases/acknowledge")));
  }
  {
    server.installedVersion = "0.7.0";
    server.seenVersion = "0.6.0";
    ackFails = false;
    statusFailsAfterAck = true;
    const { window, calls } = createPanel();
    await tick(140);
    check("S4 notice visible before dismiss when status refresh will fail", noticeVisible(window));
    const unhandledBefore = failures.filter((item) => item.startsWith("unhandled")).length;
    window.document.getElementById("dismiss-banner")?.click();
    await tick(220);
    check("S4 notice hidden immediately after acknowledged persistence despite status refresh failure", !noticeVisible(window));
    check("S4 acknowledged server-side", server.seenVersion === server.installedVersion);
    check("S4 no unhandled rejection introduced", failures.filter((item) => item.startsWith("unhandled")).length === unhandledBefore);
    statusFailsAfterAck = false;
  }
  if (failures.length) {
    console.log("\nFAILURES:");
    failures.forEach((item) => console.log(` - ${item}`));
    process.exitCode = 1;
  } else console.log("\nAll changelog dismissal checks passed.");
}
run().catch((error) => { console.error("harness error:", error); process.exitCode = 1; });
