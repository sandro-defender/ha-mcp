import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";
const repo = process.env.REPO || "/home/user/ha-mcp";
const dir = `${repo}/agent_gateway/src/gateway/ui/agent`;
const html = readFileSync(`${dir}/index.html`, "utf8");
const js = readFileSync(`${dir}/agent.js`, "utf8");
const failures = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => failures.push("jsdomError: " + e.message));
const dom = new JSDOM(html, { url: "http://ha.local:8098/agent#code=hag_h_demo", runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
const { window } = dom;
const now = new Date(Date.now() + 3000_000).toISOString();
const calls = [];
window.fetch = async (url, options = {}) => {
  calls.push(`${options.method || "GET"} ${url}`);
  if (String(url).endsWith("/agent/api/redeem")) {
    return { ok: true, status: 200, json: async () => ({
      token: "hag_" + "T".repeat(43), token_shown_once: true,
      connection_config: {},
      connections: [{ id: "a".repeat(32), name: "Home", type: "home_assistant" }],
      session: { id: "b".repeat(32), name: "codex", status: "active", expires_at: now, remaining_seconds: 3000,
        scopes: ["ha:read"], operation_classes: ["read"], connections: [{ id: "a".repeat(32), name: "Home", type: "home_assistant" }] },
    })};
  }
  return { ok: true, status: 200, json: async () => ({ session: { status: "active", remaining_seconds: 100 } }) };
};
window.eval(js);
window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
await new Promise((r) => setTimeout(r, 120));
const text = window.document.body.textContent;
const report = [];
const check = (l, ok) => { report.push(`${ok ? "ok  " : "FAIL"} ${l}`); if (!ok) failures.push(l); };
check("redeem called", calls.some((c) => c.includes("POST /agent/api/redeem")));
check("token displayed", (window.document.querySelector("#token-value")?.textContent || "").includes("hag_T"));
check("base url shown", (window.document.querySelector("#fact-base")?.textContent || "").includes("ha.local:8098"));
check("mcp endpoint shown", (window.document.querySelector("#fact-mcp")?.textContent || "").endsWith("/mcp"));
check("warning present", text.includes("shown once"));
check("fragment stripped", !window.location.hash.includes("code="));
check("status ready message", text.includes("Credential ready"));
console.log(report.join("\n"));
if (failures.length) { console.error("FAILURES: " + failures.join("; ")); process.exit(1); }
console.log("RESULT: PASS (agent handoff page)");
