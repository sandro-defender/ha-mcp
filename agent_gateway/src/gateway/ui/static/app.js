/* Home Assistant Agent Gateway — management panel.
 *
 * No dependencies, no build step, no remote assets: the panel is served from
 * the add-on inside Home Assistant Ingress under a strict CSP.
 *
 * Design/accessibility rules applied here (see design-system/.../MASTER.md):
 *  - inline SVG icons only, never emoji;
 *  - 44px interactive targets, visible :focus-visible rings;
 *  - dialogs are labelled, focus-trapped, Escape-dismissable, and return focus;
 *  - form errors are summarised in a focusable region and linked to fields;
 *  - tables expose aria-sort and a caption; status always has a text label;
 *  - loading, empty, and error states are explicit; reduced motion respected.
 */
"use strict";

(() => {
  const ICONS = {
    dashboard:
      '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    connections:
      '<rect x="2.5" y="4" width="19" height="6" rx="2"/><rect x="2.5" y="14" width="19" height="6" rx="2"/><circle cx="7" cy="7" r="0.9"/><circle cx="7" cy="17" r="0.9"/>',
    key: '<circle cx="8" cy="8" r="4"/><path d="M11 11l9 9"/><path d="M17.5 17.5L19.5 15.5"/><path d="M20 14l2 2"/>',
    tools:
      '<path d="M14.7 6.3a4 4 0 1 0 3 3L21 6l-3-3-3.3 3.3z"/><path d="M10.6 10.6L3 18.2V21h2.8l7.6-7.6"/>',
    shield:
      '<path d="M12 3l7 3v5.5c0 4.4-3 8-7 9.5-4-1.5-7-5.1-7-9.5V6l7-3z"/><path d="M8.8 12l2.2 2.2 4.2-4.4"/>',
    approvals: '<circle cx="12" cy="12" r="9"/><path d="M8 12.2l2.8 2.8L16 9.4"/>',
    audit:
      '<path d="M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M9 8h6M9 12h6M9 16h4"/>',
    settings:
      '<path d="M4 7h9M18 7h2M4 17h3M12 17h8"/><circle cx="15.5" cy="7" r="2"/><circle cx="9.5" cy="17" r="2"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    unlock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.4-2"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.4 6.4"/><path d="M20 4v7h-7"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash:
      '<path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>',
    pencil: '<path d="M4 20h4L20 8l-4-4L4 16v4z"/><path d="M13 7l4 4"/>',
    rotate: '<path d="M3.5 12a8.5 8.5 0 1 0 3-6.5"/><path d="M3 4v5h5"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    alert: '<path d="M12 3l9 16H3l9-16z"/><path d="M12 9v4"/><circle cx="12" cy="17" r="0.7"/>',
    check: '<path d="M4 12.5l5 5L20 6.5"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
    home: '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
    database:
      '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    network:
      '<rect x="9" y="3" width="6" height="6" rx="1.5"/><rect x="2" y="15" width="6" height="6" rx="1.5"/><rect x="16" y="15" width="6" height="6" rx="1.5"/><path d="M12 9v3M5 15v-3h14v3"/>',
    eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    journal:
      '<path d="M5 4a2 2 0 0 1 2-2h12v18H7a2 2 0 0 0-2 2z"/><path d="M5 20a2 2 0 0 1 2-2h12"/><path d="M16 2v6l-2.5-2L11 8V2"/>',
    spinner: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  };

  const NAV = [
    ["dashboard", "Overview", "dashboard"],
    ["connections", "Connections", "connections"],
    ["credentials", "Credentials", "key"],
    ["agents", "Agents", "shield"],
    ["access", "Agent Access", "key"],
    ["tools", "Tools", "tools"],
    ["permissions", "Permissions", "shield"],
    ["approvals", "Approvals", "approvals"],
    ["audit", "Audit Log", "audit"],
    ["journal", "Project Journal", "journal"],
    ["updates", "Updates & Changelog", "refresh"],
    ["settings", "Settings & Diagnostics", "settings"],
  ];

  const SUBTITLES = {
    dashboard: "System health, security posture, pending actions, and release status.",
    connections: "Home Assistant, Proxmox VE, and restricted SSH profiles with actionable diagnostics.",
    credentials: "Encrypted vault — secrets are never displayed again after saving.",
    agents: "Per-agent identities, scopes, token lifecycle, and usage insights.",
    access: "Temporary, single-use sessions for coding agents: handoff links, expiry, and revocation.",
    tools: "Tool groups and schemas — the same engine serves REST and MCP.",
    permissions: "Central policy documents, templates, and the live policy simulator.",
    approvals: "Single-use approvals bound to the exact operation, parameters, and policy revision.",
    audit: "Searchable, filterable audit trail with redaction and retention.",
    journal: "Persistent agent work history: project state, verified evidence, failed approaches, and session handoffs.",
    updates: "Installed version, release history, migration notes, and update checks.",
    settings: "Theme, limits, vault lifecycle, health checks, and diagnostic exports.",
  };

  const CONNECTION_LABELS = {
    home_assistant: "Home Assistant",
    proxmox: "Proxmox VE",
    ssh: "SSH",
  };

  const CREDENTIAL_KINDS = [
    ["home_assistant_token", "Home Assistant token"],
    ["proxmox_token", "Proxmox API token"],
    ["ssh_private_key", "SSH private key"],
    ["ca_certificate", "CA certificate"],
    ["generic", "Generic secret"],
  ];

  const state = {
    status: null,
    route: "dashboard",
    agents: [],
    connections: [],
    credentials: [],
    scopes: [],
    templates: {},
    approvals: [],
    journalState: null,
    audit: { items: [], total: 0, sort: { key: "ts", direction: "desc" } },
    refreshTimer: null,
    modalOpen: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  /* ------------------------------------------------------------- utilities */

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function icon(name, size = 18, extra = "") {
    const paths = ICONS[name];
    if (!paths) return "";
    const cls = extra ? `icon ${extra}` : "icon";
    return `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`;
  }

  function fmtDate(value) {
    if (!value) return "Not yet";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return esc(value);
    return esc(date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }));
  }

  function relTime(value) {
    if (!value) return "never";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "unknown";
    const seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
  }

  function badge(text, tone = "") {
    return `<span class="badge"${tone ? ` data-tone="${tone}"` : ""}>${esc(text)}</span>`;
  }

  function statusBadge(status) {
    const tones = {
      ok: "ok",
      succeeded: "ok",
      approved: "ok",
      enabled: "ok",
      used: "info",
      pending: "warn",
      pending_approval: "warn",
      unconfigured: "warn",
      expired: "danger",
      denied: "danger",
      failed: "danger",
      rejected: "danger",
      revoked: "danger",
      disabled: "danger",
      error: "danger",
      blocked: "warn",
      in_progress: "info",
      completed: "ok",
      todo: "",
      cancelled: "",
      open: "warn",
      resolved: "ok",
      passed: "ok",
      active: "info",
      ended: "",
      accepted: "ok",
    };
    const icons = {
      ok: "check",
      succeeded: "check",
      approved: "check",
      enabled: "check",
      denied: "alert",
      failed: "alert",
      rejected: "alert",
      error: "alert",
      expired: "alert",
      disabled: "alert",
      revoked: "alert",
      pending: "approvals",
      pending_approval: "approvals",
      unconfigured: "alert",
    };
    const tone = tones[status] || "";
    const glyph = icons[status] ? icon(icons[status], 13) : "";
    return `<span class="badge"${tone ? ` data-tone="${tone}"` : ""}>${glyph}${esc(status || "unknown")}</span>`;
  }

  function riskBadge(risk) {
    if (risk === "read") return badge("read-only", "ok");
    if (risk === "write") return badge("write · approval", "warn");
    return badge("destructive · approval", "danger");
  }

  function emptyState(title, hint, glyph = "database") {
    return `<div class="empty-state">${icon(glyph, 24)}<p><strong>${esc(title)}</strong></p>${
      hint ? `<p>${esc(hint)}</p>` : ""
    }</div>`;
  }

  function skeleton(count = 3) {
    return `<div class="skeleton-stack" aria-hidden="true">${Array.from({ length: count })
      .map(() => '<div class="skeleton"></div>')
      .join("")}</div>`;
  }

  /* ---------------------------------------------------------------- toasts */

  let toastTimer = null;

  function toast(message, tone = "ok") {
    const node = $("#toast");
    if (!node) return;
    node.dataset.tone = tone;
    node.dataset.visible = "true";
    node.innerHTML = `${icon(tone === "danger" ? "alert" : "check", 18)}<span>${esc(message)}</span>`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.dataset.visible = "false";
      node.innerHTML = "";
    }, 6000);
  }

  function announce(message) {
    const live = $("#live-region");
    if (live) live.textContent = message;
  }

  /* ------------------------------------------------------------------- api */

  class ApiError extends Error {
    constructor(message, { status, code, details } = {}) {
      super(message);
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  async function api(path, { method = "GET", body } = {}) {
    const headers = { "X-Gateway-Request": "1", Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(`api/admin/${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch {
      throw new ApiError("The gateway did not respond. Check that the add-on is running.", {
        status: 0,
        code: "network_error",
      });
    }
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const error = payload && payload.error ? payload.error : {};
      throw new ApiError(error.message || `Request failed (${response.status})`, {
        status: response.status,
        code: error.code || "request_failed",
        details: error.details || null,
      });
    }
    return payload;
  }

  function errorText(error) {
    if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
    if (error.code === "vault_locked" || error.status === 423) {
      return "The vault is locked. Unlock it to continue.";
    }
    if (error.code === "vault_not_initialized" || error.status === 409) {
      return error.message;
    }
    if (error.code === "rate_limited") return "Too many attempts. Wait a minute and try again.";
    const details = error.details?.errors;
    if (Array.isArray(details) && details.length) {
      return `${error.message}: ${details.map((item) => item.message).join("; ")}`;
    }
    return error.message;
  }

  async function guarded(work) {
    try {
      return await work();
    } catch (error) {
      if (error instanceof ApiError && (error.status === 423 || error.code === "vault_locked")) {
        toast("Vault locked. Unlock it to continue.", "danger");
        await boot();
        return undefined;
      }
      if (error instanceof ApiError && (error.status === 401 || error.code === "ingress_required")) {
        toast("Management session lost. Reload the panel from the Home Assistant sidebar.", "danger");
        return undefined;
      }
      toast(errorText(error), "danger");
      throw error;
    }
  }

  async function withBusy(button, work) {
    if (!button) return work();
    const original = button.innerHTML;
    const width = button.offsetWidth;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.style.minWidth = `${width}px`;
    button.innerHTML = `${icon("spinner", 16, "spinner")}<span>Working…</span>`;
    try {
      return await work();
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.innerHTML = original;
      button.style.minWidth = "";
    }
  }

  /* ---------------------------------------------------------------- modals */

  let lastFocused = null;

  function focusable(root) {
    return $$(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      root
    ).filter((node) => node.offsetParent !== null);
  }

  function openModal({ title, body, actions = [], onMount }) {
    const modal = $("#modal");
    const card = $(".modal-card", modal);
    lastFocused = document.activeElement;
    state.modalOpen = true;
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = body;
    const footer = $("#modal-actions");
    footer.innerHTML = "";
    const danger = [];
    const normal = [];
    actions.forEach((action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button";
      if (action.variant) button.dataset.variant = action.variant;
      button.innerHTML = `${action.icon ? icon(action.icon, 16) : ""}<span>${esc(action.label)}</span>`;
      button.addEventListener("click", () => action.onClick(button));
      (action.dangerZone ? danger : normal).push(button);
    });
    if (danger.length) {
      const zone = document.createElement("div");
      zone.className = "danger-zone";
      danger.forEach((button) => zone.appendChild(button));
      footer.appendChild(zone);
    }
    normal.forEach((button) => footer.appendChild(button));
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    if (onMount) onMount(card);
    const focusables = focusable(card);
    (focusables[0] || card).focus();
    return card;
  }

  function closeModal() {
    const modal = $("#modal");
    if (!modal || modal.classList.contains("hidden")) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    $("#modal-body").innerHTML = "";
    $("#modal-actions").innerHTML = "";
    document.body.style.overflow = "";
    state.modalOpen = false;
    if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
    lastFocused = null;
  }

  function trapFocus(event) {
    if (event.key !== "Tab" || !state.modalOpen) return;
    const card = $(".modal-card", $("#modal"));
    const items = focusable(card);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function confirmAction({ title, message, confirmLabel, variant = "primary", requireText = "", dangerZone = false }) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        closeModal();
        resolve(value);
      };
      const field = requireText
        ? `<div class="field"><label for="confirm-text">Type “${esc(requireText)}” to confirm</label>
             <input id="confirm-text" autocomplete="off" aria-describedby="confirm-text-error">
             <p class="field-error" id="confirm-text-error"></p></div>`
        : "";
      openModal({
        title,
        body: `<p>${esc(message)}</p>${field}`,
        actions: [
          { label: "Cancel", variant: "ghost", onClick: () => finish(false) },
          {
            label: confirmLabel,
            variant,
            dangerZone,
            onClick: (button) => {
              if (!requireText) {
                finish(true);
                return;
              }
              const input = $("#confirm-text");
              if (input.value !== requireText) {
                input.setAttribute("aria-invalid", "true");
                $("#confirm-text-error").textContent = `Type ${requireText} exactly to continue.`;
                input.focus();
                return;
              }
              button.disabled = true;
              finish(true);
            },
          },
        ],
        onMount: (card) => {
          if (requireText) {
            const input = $("#confirm-text", card);
            const confirm = $(".modal-footer .button:last-child", card);
            confirm.disabled = true;
            input.addEventListener("input", () => {
              confirm.disabled = input.value !== requireText;
              if (input.value === requireText) {
                input.removeAttribute("aria-invalid");
                $("#confirm-text-error").textContent = "";
              }
            });
            input.focus();
          }
        },
      });
    });
  }

  /* ----------------------------------------------------------- form errors */

  function fieldError(input, message) {
    if (!input) return;
    const errorId = `${input.id}-error`;
    let node = document.getElementById(errorId);
    if (!node) {
      node = document.createElement("p");
      node.className = "field-error";
      node.id = errorId;
      input.insertAdjacentElement("afterend", node);
    }
    node.textContent = message;
    if (message) {
      input.setAttribute("aria-invalid", "true");
      const describedBy = new Set((input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
      describedBy.add(errorId);
      input.setAttribute("aria-describedby", Array.from(describedBy).join(" "));
    } else {
      input.removeAttribute("aria-invalid");
      node.textContent = "";
    }
  }

  function clearErrors(root) {
    $$(".error-summary", root).forEach((node) => node.remove());
    $$("[aria-invalid='true']", root).forEach((node) => node.removeAttribute("aria-invalid"));
    $$(".field-error", root).forEach((node) => {
      node.textContent = "";
    });
  }

  function showErrors(root, summary, errors) {
    clearErrors(root);
    errors.forEach((error) => {
      const input = document.getElementById(error.fieldId);
      if (input) fieldError(input, error.message);
    });
    const box = document.createElement("div");
    box.className = "error-summary";
    box.setAttribute("role", "alert");
    box.setAttribute("tabindex", "-1");
    const items = errors
      .map((error) =>
        error.fieldId && document.getElementById(error.fieldId)
          ? `<li><a href="#${esc(error.fieldId)}">${esc(error.message)}</a></li>`
          : `<li>${esc(error.message)}</li>`
      )
      .join("");
    box.innerHTML = `<h4>${esc(summary)}</h4>${items ? `<ul>${items}</ul>` : ""}`;
    root.prepend(box);
    box.focus();
  }

  function attachBlurValidation(form) {
    $$("input[required], select[required], textarea[required]", form).forEach((input) => {
      input.addEventListener("blur", () => {
        if (!input.value.trim()) fieldError(input, "This field is required.");
        else if (input.getAttribute("aria-invalid") === "true" && input.value.trim()) fieldError(input, "");
      });
    });
  }

  /* -------------------------------------------------------------- app shell */

  function pendingApprovals() {
    return state.status?.counts?.pending_approvals || 0;
  }

  function shell() {
    const status = state.status;
    return `
      <a class="skip-link" href="#main-content">Skip to main content</a>
      <div class="shell">
        <aside class="sidebar">
          <div class="brand">
            <span class="brand-mark">${icon("shield", 20)}</span>
            <span>
              <span class="brand-name">Agent Gateway</span>
              <span class="brand-meta">v${esc(status.version)}</span>
            </span>
          </div>
          <nav class="nav" aria-label="Gateway sections">
            ${NAV.map(
              ([id, label, glyph]) => `
              <button type="button" class="nav-link" data-route="${id}" ${
                state.route === id ? 'aria-current="page"' : ""
              }>
                ${icon(glyph, 18)}
                <span class="nav-label">${esc(label)}</span>
                ${id === "approvals" && pendingApprovals() ? `<span class="nav-count" aria-label="${pendingApprovals()} pending approvals">${pendingApprovals()}</span>` : ""}
              </button>`
            ).join("")}
          </nav>
          <div class="sidebar-footer">
            <div class="footer-row"><span>Vault</span><strong>${status.vault.unlocked ? "unlocked" : "locked"}</strong></div>
            <div class="footer-row"><span>Agents</span><strong>${status.emergency_disabled ? "stopped" : "enabled"}</strong></div>
            <div class="footer-row"><span>Health</span><strong>${status.counts?.healthy_connections || 0}/${status.counts?.connections || 0} ok</strong></div>
            <div class="footer-row"><span>Ingress</span><span class="mono">:${esc(status.listeners.ingress_port)}</span></div>
            <div class="footer-row"><span>Agent API</span><span class="mono">:${esc(status.listeners.agent_port)}${status.listeners.agent_tls ? " \u00b7 TLS" : ""}</span></div>
            <div class="footer-row"><a class="repo-link" href="https://github.com/sandro-defender/ha-mcp" target="_blank" rel="noreferrer">GitHub</a><a class="repo-link" href="https://github.com/sandro-defender/ha-mcp/blob/main/CHANGELOG.md" target="_blank" rel="noreferrer">Changelog</a></div>
            <div class="theme-switcher" role="group" aria-label="Color theme">
              <button type="button" data-theme-choice="system" aria-pressed="${(document.documentElement.dataset.theme || "system") === "system" ? "true" : "false"}">Auto</button>
              <button type="button" data-theme-choice="light" aria-pressed="${document.documentElement.dataset.theme === "light" ? "true" : "false"}">Light</button>
              <button type="button" data-theme-choice="dark" aria-pressed="${document.documentElement.dataset.theme === "dark" ? "true" : "false"}">Dark</button>
            </div>
          </div>
        </aside>
        <main class="main" id="main-content" tabindex="-1">
          <div class="page-header">
            <div>
              <h2 id="page-title" tabindex="-1">${esc(NAV.find(([id]) => id === state.route)?.[1] || "Dashboard")}</h2>
              <p class="page-sub" id="page-sub">${esc(SUBTITLES[state.route] || "")}</p>
            </div>
            <div class="page-tools">
              <span class="updated" id="updated" aria-live="polite">Updated ${esc(
                new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
              )}</span>
              <button type="button" class="button" data-size="sm" id="refresh">${icon("refresh", 16)}<span>Refresh</span></button>
            </div>
          </div>
          <div id="section" aria-busy="false"></div>
        </main>
      </div>`;
  }

  function mountShell() {
    $("#app").innerHTML = shell();
    $$("[data-route]").forEach((button) => {
      button.addEventListener("click", () => navigate(button.dataset.route));
    });
    $("#refresh").addEventListener("click", () => loadRoute(state.route, { focus: false }));
    $$("[data-theme-choice]").forEach((button) => {
      button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
    });
    $$("[data-go-section]").forEach((button) => {
      button.addEventListener("click", () => navigate(button.dataset.goSection));
    });
  }

  function setUpdated() {
    const node = $("#updated");
    if (node) {
      node.textContent = `Updated ${new Date().toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      })}`;
    }
  }

  async function navigate(route) {
    if (!NAV.some(([id]) => id === route)) route = "dashboard";
    state.route = route;
    if (state.refreshTimer) {
      clearInterval(state.refreshTimer);
      state.refreshTimer = null;
    }
    mountShell();
    await loadRoute(route, { focus: true });
  }

  async function loadRoute(route, { focus } = {}) {
    const section = $("#section");
    if (!section) return;
    section.setAttribute("aria-busy", "true");
    section.innerHTML = skeleton(route === "dashboard" ? 4 : 3);
    announce(`Loading ${NAV.find(([id]) => id === route)?.[1] || route}`);
    try {
      if (route === "dashboard") await viewDashboard();
      else if (route === "connections") await viewConnections();
      else if (route === "credentials") await viewCredentials();
      else if (route === "agents") await viewAgents();
      else if (route === "access") await viewAgentAccess();
      else if (route === "tools") await viewTools();
      else if (route === "permissions") await viewPermissions();
      else if (route === "approvals") await viewApprovals();
      else if (route === "audit") await viewAudit();
      else if (route === "journal") await viewJournal();
      else if (route === "updates") await viewUpdates();
      else if (route === "settings") await viewSettings();
    } catch (error) {
      if (error instanceof ApiError && (error.status === 423 || error.code === "vault_locked")) {
        await boot();
        return;
      }
      section.innerHTML = `<div class="card">${emptyState(
        "This section could not be loaded",
        errorText(error),
        "alert"
      )}</div>`;
      toast(errorText(error), "danger");
    } finally {
      section.setAttribute("aria-busy", "false");
      setUpdated();
    }
    if (focus) {
      $("#page-title")?.focus();
      announce(`${NAV.find(([id]) => id === route)?.[1] || route} loaded`);
    }
  }

  async function refreshStatus() {
    state.status = await api("status");
  }

  /* -------------------------------------------------------------- dashboard */

  async function viewDashboard() {
    const audit = await api("audit?limit=8");
    const status = state.status;
    const metric = (label, value, note, glyph) => `
      <div class="card glass">
        <p class="metric-label">${esc(label)}</p>
        <p class="metric">${value}</p>
        <p class="metric-note">${note}</p>
      </div>`;

    var release = status.release || {};
    var warnings = status.warnings || [];
    var banner = "";
    if (release.post_update_notice && release.post_update_notice.show) {
      var n = release.post_update_notice;
      var h = (n.highlights||[]).map(function(x){ return '<li>' + esc(x) + '</li>'; }).join("");
      var m = (n.migration_notes||[]).map(function(x){ return '<li>' + esc(x) + '</li>'; }).join("");
      banner = '<div class="update-banner" role="status"><h3>' + icon("refresh",16) + ' What\'s New in v' + esc(n.version) + ' — restart complete</h3>' + (h ? '<ul class="release-bullets">' + h + '</ul>' : "") + (m ? '<div class="notice mt-2"><div><strong>Migration Notes</strong><ul class="release-bullets">' + m + '</ul></div></div>' : "") + '<div class="button-row mt-2"><button type="button" class="button" data-size="sm" id="dismiss-banner">' + icon("check",14) + '<span>Dismiss</span></button><button type="button" class="button" data-size="sm" data-go="updates">View changelog</button></div></div>';
    }
    var warningsBlock = "";
    if (warnings.length) {
      warningsBlock = '<section class="card glass" aria-labelledby="warnings-title"><div class="card-header"><h3 id="warnings-title">Active warnings (' + warnings.length + ')</h3>' + badge("review", warnings.some(function(w){ return w.severity==="critical"; })?"danger":"warn") + '</div><div class="warning-stack">' + warnings.map(warningCard).join("") + '</div></section>';
    }
    $("#section").innerHTML = banner + warningsBlock + `
      <div class="grid grid-4">
        ${metric(
          "Enabled agents",
          esc(status.counts.enabled_agents),
          `${esc(status.counts.agents)} identities total`,
          "shield"
        )}
        ${metric("Connections", esc(status.counts.connections), "configured profiles", "connections")}
        ${metric(
          "Pending approvals",
          esc(status.counts.pending_approvals),
          `<a href="#approvals-link" data-go="approvals">Review requests</a>`,
          "approvals"
        )}
        ${metric(
          "Vault",
          status.vault.unlocked ? `${icon("unlock", 18)} unlocked` : `${icon("lock", 18)} locked`,
          `${esc(status.vault.records)} credentials · ${esc(status.vault.kdf?.algorithm || "argon2id")}`,
          "lock"
        )}
      </div>

      <div class="grid grid-2 mt-4">
        <section class="card glass" aria-labelledby="posture-title">
          <div class="card-header">
            <h3 id="posture-title">Security posture</h3>
            ${badge("fail-closed", "ok")}
          </div>
          <dl class="detail-list">
            <div class="detail-row"><dt>${icon("shield", 15)}Agent access</dt><dd>${
              status.emergency_disabled ? badge("disabled", "danger") : badge("enabled", "ok")
            }</dd></div>
            <div class="detail-row"><dt>${icon("home", 15)}Management access</dt><dd>${badge(
              "Ingress / Supervisor only",
              "ok"
            )}</dd></div>
            <div class="detail-row"><dt>${icon("key", 15)}Administrators</dt><dd>${
              status.security.admin_user_restricted ? badge("allowlist enforced", "ok") : badge("any HA admin", "warn")
            }</dd></div>
            <div class="detail-row"><dt>${icon("network", 15)}TLS verification</dt><dd>${badge(
              "Home Assistant & Proxmox",
              "ok"
            )}</dd></div>
            <div class="detail-row"><dt>${icon("terminal", 15)}SSH host keys</dt><dd>${badge(
              "verified against trust store",
              "ok"
            )}</dd></div>
            <div class="detail-row"><dt>${icon("terminal", 15)}Arbitrary shell</dt><dd>${badge(
              "unavailable",
              "ok"
            )}</dd></div>
            <div class="detail-row"><dt>${icon("eye", 15)}Browser origins</dt><dd>${
              status.security.agent_origin_allowlist.length
                ? `<span class="mono">${esc(status.security.agent_origin_allowlist.join(", "))}</span>`
                : badge("no browser clients", "ok")
            }</dd></div>
          </dl>
          <div class="button-row mt-4">
            <button type="button" class="button" data-size="sm" id="lock-vault">${icon("lock", 16)}<span>Lock vault</span></button>
            <div class="danger-zone">
              <button type="button" class="button" data-size="sm" data-variant="${
                status.emergency_disabled ? "primary" : "danger"
              }" id="toggle-agents">
                ${icon(status.emergency_disabled ? "unlock" : "alert", 16)}
                <span>${status.emergency_disabled ? "Enable agent access" : "Emergency disable"}</span>
              </button>
            </div>
          </div>
        </section>

        <section class="card glass" aria-labelledby="groups-title">
          <div class="card-header">
            <h3 id="groups-title">Tool groups</h3>
            <span class="card-hint">disabled groups are hidden from agents</span>
          </div>
          <div class="list">
            ${status.tool_groups
              .map(
                (group) => `
              <div class="list-item">
                <div class="list-item-top">
                  <span class="list-item-title">
                    ${icon(group.id === "ssh" ? "terminal" : group.id === "proxmox" ? "network" : "connections", 16)}
                    ${esc(CONNECTION_LABELS[group.id] || group.id)}
                    ${group.enabled ? badge("enabled", "ok") : badge("disabled", "warn")}
                  </span>
                  <span class="list-item-meta">${group.tools.length} tools</span>
                </div>
              </div>`
              )
              .join("")}
          </div>
        </section>
      </div>

      <section class="card glass mt-4" aria-labelledby="activity-title">
        <div class="card-header">
          <h3 id="activity-title">Recent activity</h3>
          <span class="card-hint">${esc(audit.total)} events retained · secrets redacted</span>
        </div>
        <div id="dashboard-audit">${auditTable(audit.items, { compact: true })}</div>
      </section>`;

    $("#lock-vault").addEventListener("click", () =>
      guarded(async () => {
        await api("vault/lock", { method: "POST", body: {} });
        toast("Vault locked.");
        await boot();
      })
    );
    $("#toggle-agents").addEventListener("click", toggleAgents);
    var dismissBtn = document.getElementById("dismiss-banner");
    if (dismissBtn) dismissBtn.addEventListener("click", function(){ guarded(async function(){ await api("releases/acknowledge", {method:"POST", body:{}}); toast("Notice dismissed."); await navigate("dashboard"); }); });
    $$("[data-go]").forEach((node) =>
      node.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(node.dataset.go);
      })
    );
    bindAuditSort($("#section"), audit.items, { compact: true });
  }

  async function toggleAgents() {
    const disable = !state.status.emergency_disabled;
    if (disable) {
      const confirmed = await confirmAction({
        title: "Disable all agent access?",
        message:
          "Every agent REST and MCP request will be rejected immediately. Management access is unaffected, and you can re-enable access here.",
        confirmLabel: "Disable agent access",
        variant: "danger",
      });
      if (!confirmed) return;
    }
    await guarded(async () => {
      await api("settings", { method: "PUT", body: { emergency_disabled: disable } });
      toast(disable ? "Agent access disabled." : "Agent access enabled.");
      await refreshStatus();
      await navigate("dashboard");
    });
  }

  /* ------------------------------------------------------------------ audit */

  function auditTable(items, { compact = false } = {}) {
    if (!items || !items.length) {
      return emptyState(
        "No audit events yet",
        "Events appear after an agent calls a tool or an administrator changes configuration.",
        "audit"
      );
    }
    const columns = [
      ["ts", "Time"],
      ["actor_type", "Actor"],
      ["tool", "Operation"],
      ["decision", "Decision"],
      ["status", "Status"],
      ...(compact ? [] : [["duration_ms", "Duration"]]),
      ["params_summary", "Parameters"],
    ];
    const sorted = sortAudit(items);
    const sort = state.audit.sort;
    return `
      <div class="table-wrap" role="region" aria-label="Audit events" tabindex="0">
        <table>
          <caption>${compact ? "Most recent gateway activity" : `Matching events (${items.length} shown)`}</caption>
          <thead>
            <tr>
              ${columns
                .map(([key, label]) => {
                  const direction = sort.key === key ? (sort.direction === "asc" ? "ascending" : "descending") : "none";
                  return `<th scope="col" aria-sort="${direction}">
                    <button type="button" class="sort-button" data-sort="${key}">
                      <span>${esc(label)}</span>
                      ${
                        sort.key === key
                          ? `<span class="sort-icon">${icon(sort.direction === "asc" ? "up" : "down", 13)}</span>`
                          : ""
                      }
                    </button>
                  </th>`;
                })
                .join("")}
            </tr>
          </thead>
          <tbody>
            ${sorted
              .map(
                (event) => `
              <tr>
                <td class="nowrap">${fmtDate(event.ts)}</td>
                <td>${esc(event.actor_type)}${
                  event.agent_id ? `<div class="list-item-meta">${esc(String(event.agent_id).slice(0, 10))}</div>` : ""
                }</td>
                <td><span class="mono">${esc(event.tool || "—")}</span>${
                  event.connection_id
                    ? `<div class="list-item-meta">${esc(String(event.connection_id).slice(0, 10))}</div>`
                    : ""
                }</td>
                <td>${esc(event.decision)}${
                  event.reason
                    ? `<div class="list-item-meta truncate" title="${esc(event.reason)}">${esc(event.reason)}</div>`
                    : ""
                }</td>
                <td>${statusBadge(event.status)}${
                  event.error_category
                    ? `<div class="list-item-meta">${esc(event.error_category)}</div>`
                    : ""
                }</td>
                ${
                  compact
                    ? ""
                    : `<td class="nowrap">${
                        event.duration_ms === null || event.duration_ms === undefined
                          ? "—"
                          : `${esc(event.duration_ms)} ms`
                      }</td>`
                }
                <td><span class="mono truncate" title="${esc(event.params_summary || "")}">${esc(
                  compact ? String(event.params_summary || "").slice(0, 80) : event.params_summary || ""
                )}</span></td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>`;
  }

  function sortAudit(items) {
    const { key, direction } = state.audit.sort;
    return [...items].sort((left, right) => {
      const a = left[key] ?? "";
      const b = right[key] ?? "";
      const result =
        typeof a === "number" && typeof b === "number"
          ? a - b
          : String(a).localeCompare(String(b), undefined, { numeric: true });
      return direction === "asc" ? result : -result;
    });
  }

  function bindAuditSort(container, items, options = {}) {
    $$("[data-sort]", container).forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.sort;
        const sort = state.audit.sort;
        state.audit.sort = {
          key,
          direction: sort.key === key && sort.direction === "desc" ? "asc" : "desc",
        };
        // Replace only the table region so surrounding cards, headings, and
        // filters keep their focus order.
        const region = button.closest("[role='region']");
        if (region) {
          const replacement = document.createElement("div");
          replacement.innerHTML = auditTable(items, options);
          const next = replacement.firstElementChild;
          if (next) {
            region.replaceWith(next);
            bindAuditSort(container, items, options);
          }
        }
        $(`[data-sort="${key}"]`, container)?.focus();
      });
    });
  }

  /* ------------------------------------------------------------ connections */

  async function loadConnections() {
    const [connections, credentials] = await Promise.all([api("connections"), api("credentials")]);
    state.connections = connections.items;
    state.credentials = credentials.items;
  }

  async function viewConnections() {
    await loadConnections();
    $("#section").innerHTML = `
      <div class="button-row mb-4">
        <button type="button" class="button" data-variant="primary" id="add-connection">
          ${icon("plus", 16)}<span>Add connection</span>
        </button>
      </div>
      <div id="connection-form"></div>
      <div class="list">
        ${
          state.connections.length
            ? state.connections.map(connectionItem).join("")
            : emptyState(
                "No connections configured",
                "Add a Home Assistant, Proxmox VE, or SSH profile, test it, then grant an agent access with a policy.",
                "connections"
              )
        }
      </div>`;

    $("#add-connection").addEventListener("click", () => openConnectionForm(null));
    $$("[data-edit]", $("#section")).forEach((button) =>
      button.addEventListener("click", () =>
        openConnectionForm(state.connections.find((item) => item.id === button.dataset.edit))
      )
    );
    $$("[data-test]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => testConnection(button.dataset.test, button))
    );
    $$("[data-delete]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => deleteConnection(button.dataset.delete))
    );
    $$("[data-hostkey]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => scanHostKey(button.dataset.hostkey))
    );
  }

  function connectionItem(connection) {
    const config = connection.config || {};
    const detail =
      connection.type === "ssh"
        ? `<div class="mono">${esc(config.username)}@${esc(config.host)}:${esc(config.port || 22)}</div>
           <p class="card-hint mt-1">
             services: ${esc((config.allowed_services || []).join(", ") || "none")} ·
             logs: ${esc((config.allowed_log_paths || []).join(", ") || "none")}
           </p>`
        : `<div class="mono">${esc(config.base_url)}</div>`;
    var diag = connection.last_check_detail || {};
    var steps = Array.isArray(diag.steps) ? diag.steps : [];
    var latency = diag.latency_ms ?? connection.latency_ms ?? diag.duration_ms ?? null;
    var latencyBadge = "";
    if (latency !== null && latency !== undefined) {
      var tone = latency < 500 ? "ok" : latency < 1500 ? "warn" : "danger";
      latencyBadge = badge(String(latency) + " ms", tone);
    }
    var category = diag.error_category || diag.category || "";
    var remediation = diag.remediation || diag.hint || "";
    return `
      <div class="list-item">
        <div class="list-item-top">
          <span class="list-item-title">
            ${icon(connection.type === "ssh" ? "terminal" : connection.type === "proxmox" ? "network" : "connections", 16)}
            ${esc(connection.name)}
            ${badge(CONNECTION_LABELS[connection.type] || connection.type, "info")}
            ${statusBadge(connection.status)}
            ${latencyBadge}
          </span>
          <div class="button-row">
            <button type="button" class="button" data-size="sm" data-test="${esc(connection.id)}">
              ${icon("check", 14)}<span>Test</span>
            </button>
            ${
              connection.type === "ssh"
                ? `<button type="button" class="button" data-size="sm" data-hostkey="${esc(connection.id)}">
                     ${icon("shield", 14)}<span>Host key</span>
                   </button>`
                : ""
            }
            <button type="button" class="button" data-size="sm" data-edit="${esc(connection.id)}">
              ${icon("pencil", 14)}<span>Edit</span>
            </button>
          </div>
        </div>
        ${detail}
        ${connection.last_ok_at ? '<p class="list-item-meta">Last healthy: ' + esc(fmtDate(connection.last_ok_at)) + ' · ' + esc(relTime(connection.last_ok_at)) + '</p>' : ""}
        ${steps.length ? '<div class="diag-steps mt-2">' + steps.map(diagStepRow).join("") + '</div>' : ""}
        ${category ? '<p class="field-hint mt-1">Error: ' + esc(category) + (remediation ? ' — ' + esc(remediation) : "") + '</p>' : ""}
        <div class="button-row">
          <span class="list-item-meta">
            ${connection.last_check_at ? `Last tested ${fmtDate(connection.last_check_at)} — ${esc(connection.last_check_message || "")}` : "Never tested"}
            ${connection.host_key_trusted===false ? ' · ' + badge("host key untrusted","danger") : connection.host_key_trusted===true ? ' · ' + badge("host key trusted","ok") : ""}
          </span>
          <div class="danger-zone">
            <button type="button" class="button" data-size="sm" data-variant="danger" data-delete="${esc(connection.id)}">
              ${icon("trash", 14)}<span>Delete</span>
            </button>
          </div>
        </div>
      </div>`;
  }

  function credentialOptions(kind) {
    const options = state.credentials.filter((item) => item.kind === kind);
    return options.length
      ? options.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} (${esc(item.masked)})</option>`).join("")
      : `<option value="">No ${esc(kind)} credential — add one first</option>`;
  }

  function caOptions() {
    const options = state.credentials.filter((item) => item.kind === "ca_certificate" || item.kind === "proxmox_ca");
    return `<option value="">System CA bundle</option>${options
      .map((item) => `<option value="${esc(item.id)}">${esc(item.name)}</option>`)
      .join("")}`;
  }

  function openConnectionForm(connection) {
    const editing = Boolean(connection);
    const type = connection?.type || "home_assistant";
    const config = connection?.config || {};
    const form = `
      <section class="card glass mb-4" aria-labelledby="connection-form-title">
        <div class="card-header">
          <h3 id="connection-form-title">${editing ? `Edit ${esc(connection.name)}` : "New connection"}</h3>
          <button type="button" class="button" data-size="sm" data-variant="ghost" id="cancel-connection">
            ${icon("close", 14)}<span>Cancel</span>
          </button>
        </div>
        <form class="form" id="connection-form" novalidate>
          <div class="form-row">
            <div class="field">
              <label for="conn-name">Display name</label>
              <input id="conn-name" name="name" required maxlength="64" autocomplete="off"
                     value="${esc(connection?.name || "")}">
              <p class="field-hint">Shown to agents in connection listings.</p>
            </div>
            <div class="field">
              <label for="conn-type">Type</label>
              <select id="conn-type" name="type" ${editing ? "disabled" : ""}>
                ${Object.entries(CONNECTION_LABELS)
                  .map(([value, label]) => `<option value="${value}" ${value === type ? "selected" : ""}>${esc(label)}</option>`)
                  .join("")}
              </select>
              <p class="field-hint">${editing ? "The type cannot be changed after creation." : "Choose the API this profile talks to."}</p>
            </div>
          </div>
          <div id="connection-fields"></div>
          <div class="button-row">
            <button type="submit" class="button" data-variant="primary" id="save-connection">
              ${icon("check", 16)}<span>${editing ? "Save changes" : "Create connection"}</span>
            </button>
          </div>
          <p class="field-hint">TLS verification and SSH host-key verification are always enforced. Credentials stay in the vault and are never returned to agents.</p>
        </form>
      </section>`;
    const slot = $("#connection-form");
    slot.innerHTML = form;
    renderConnectionFields(type, config, connection);
    $("#conn-type").addEventListener("change", (event) => {
      renderConnectionFields(event.target.value, {}, null);
    });
    $("#cancel-connection").addEventListener("click", () => {
      slot.innerHTML = "";
    });
    const formEl = $("#connection-form");
    attachBlurValidation(formEl);
    formEl.addEventListener("submit", (event) => {
      event.preventDefault();
      submitConnection(formEl, connection);
    });
    $("#conn-name").focus();
  }

  function renderConnectionFields(type, config, connection) {
    const container = $("#connection-fields");
    if (!container) return;
    if (type === "ssh") {
      container.innerHTML = `
        <div class="form-row">
          <div class="field">
            <label for="ssh-host">Host</label>
            <input id="ssh-host" required autocomplete="off" placeholder="192.0.2.10" value="${esc(config.host || "")}">
          </div>
          <div class="field">
            <label for="ssh-port">Port</label>
            <input id="ssh-port" type="number" min="1" max="65535" required value="${esc(config.port || 22)}">
          </div>
        </div>
        <div class="form-row">
          <div class="field">
            <label for="ssh-username">Dedicated account</label>
            <input id="ssh-username" required autocomplete="off" placeholder="agentgw" value="${esc(config.username || "")}">
            <p class="field-hint">Root is rejected. Prefer a dedicated account with narrow sudo rules.</p>
          </div>
          <div class="field">
            <label for="ssh-credential">Private key credential</label>
            <select id="ssh-credential" required>${credentialOptions("ssh_private_key")}</select>
          </div>
        </div>
        <div class="field">
          <label for="ssh-services">Allowed services</label>
          <input id="ssh-services" autocomplete="off" placeholder="nginx, home-assistant"
                 value="${esc((config.allowed_services || []).join(", "))}">
          <p class="field-hint">Only these systemd units may be inspected with ssh.service_status.</p>
        </div>
        <div class="field">
          <label for="ssh-paths">Allowed log paths</label>
          <input id="ssh-paths" autocomplete="off" placeholder="/var/log/nginx/error.log"
                 value="${esc((config.allowed_log_paths || []).join(", "))}">
          <p class="field-hint">Absolute paths only. Reads reject traversal and symlink escapes.</p>
        </div>
        <div class="field">
          <label for="ssh-templates">Command templates (JSON)</label>
          <textarea id="ssh-templates" data-code="true" spellcheck="false"
                    aria-describedby="ssh-templates-hint">${esc(JSON.stringify(config.command_templates || [], null, 2))}</textarea>
          <p class="field-hint" id="ssh-templates-hint">Advanced. Each template is a fixed argv vector with enumerated parameters; shell strings are not accepted.</p>
        </div>`;
    } else {
      const kind = type === "home_assistant" ? "home_assistant_token" : "proxmox_token";
      container.innerHTML = `
        <div class="field">
          <label for="conn-base-url">Base URL</label>
          <input id="conn-base-url" required autocomplete="off"
                 placeholder="${type === "home_assistant" ? "http://homeassistant:8123" : "https://pve.example.lan:8006"}"
                 value="${esc(config.base_url || "")}">
          <p class="field-hint">${
            type === "home_assistant"
              ? "Use a dedicated Home Assistant user's long-lived token."
              : "Create a Proxmox API token for a dedicated, least-privilege user."
          }</p>
        </div>
        <div class="form-row">
          <div class="field">
            <label for="conn-credential">Token credential</label>
            <select id="conn-credential" required>${credentialOptions(kind)}</select>
          </div>
          <div class="field">
            <label for="conn-ca">Custom CA certificate</label>
            <select id="conn-ca">${caOptions()}</select>
            <p class="field-hint">Optional. TLS verification stays enabled.</p>
          </div>
        </div>`;
      if (connection?.credential_id) $("#conn-credential").value = connection.credential_id;
      if (config.ca_cert_secret_id) $("#conn-ca").value = config.ca_cert_secret_id;
    }
    if (connection?.credential_id && type === "ssh" && $("#ssh-credential")) {
      $("#ssh-credential").value = connection.credential_id;
    }
  }

  async function submitConnection(form, connection) {
    clearErrors(form);
    const type = connection?.type || $("#conn-type").value;
    const errors = [];
    const name = $("#conn-name").value.trim();
    if (!name) errors.push({ fieldId: "conn-name", message: "Enter a display name." });

    let config = {};
    let credentialId = "";
    if (type === "ssh") {
      const host = $("#ssh-host").value.trim();
      const port = Number($("#ssh-port").value || 22);
      const username = $("#ssh-username").value.trim();
      credentialId = $("#ssh-credential").value;
      if (!host) errors.push({ fieldId: "ssh-host", message: "Enter the SSH host." });
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        errors.push({ fieldId: "ssh-port", message: "Port must be between 1 and 65535." });
      }
      if (!username) errors.push({ fieldId: "ssh-username", message: "Enter the dedicated account name." });
      if (!credentialId) errors.push({ fieldId: "ssh-credential", message: "Select a private key credential." });
      let templates = [];
      try {
        templates = JSON.parse($("#ssh-templates").value || "[]");
      } catch {
        errors.push({ fieldId: "ssh-templates", message: "Command templates must be valid JSON." });
      }
      config = {
        host,
        port,
        username,
        allowed_services: $("#ssh-services").value.split(",").map((item) => item.trim()).filter(Boolean),
        allowed_log_paths: $("#ssh-paths").value.split(",").map((item) => item.trim()).filter(Boolean),
        command_templates: templates,
      };
    } else {
      const baseUrl = $("#conn-base-url").value.trim();
      credentialId = $("#conn-credential").value;
      try {
        const parsed = new URL(baseUrl);
        if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("scheme");
      } catch {
        errors.push({ fieldId: "conn-base-url", message: "Enter a valid http(s) URL." });
      }
      if (!credentialId) errors.push({ fieldId: "conn-credential", message: "Select a token credential." });
      config = { base_url: baseUrl, verify_tls: true };
      const ca = $("#conn-ca").value;
      if (ca) config.ca_cert_secret_id = ca;
    }

    if (errors.length) {
      showErrors(form, "The connection was not saved.", errors);
      return;
    }

    const button = $("#save-connection");
    await withBusy(button, () =>
      guarded(async () => {
        if (connection) {
          await api(`connections/${connection.id}`, {
            method: "PATCH",
            body: { name, config, credential_id: credentialId },
          });
          toast("Connection updated.");
        } else {
          await api("connections", {
            method: "POST",
            body: { name, type, config, credential_id: credentialId },
          });
          toast("Connection created.");
        }
        await navigate("connections");
      })
    ).catch((error) => {
      showErrors(form, "The connection was not saved.", [{ message: errorText(error) }]);
    });
  }

  async function testConnection(connectionId, button) {
    await withBusy(button, () =>
      guarded(async () => {
        const result = await api(`connections/${connectionId}/test`, { method: "POST", body: {} });
        if (result.ok) toast("Connection test succeeded.");
        else toast(`Connection test failed: ${result.error?.message || "unknown error"}`, "danger");
        await navigate("connections");
      })
    );
  }

  async function deleteConnection(connectionId) {
    const connection = state.connections.find((item) => item.id === connectionId);
    if (!connection) return;
    const confirmed = await confirmAction({
      title: `Delete ${connection.name}?`,
      message: "The connection profile is removed. Stored credentials are kept in the vault and must be deleted separately.",
      confirmLabel: "Delete connection",
      variant: "danger",
    });
    if (!confirmed) return;
    await guarded(async () => {
      await api(`connections/${connectionId}`, { method: "DELETE" });
      toast("Connection deleted.");
      await navigate("connections");
    });
  }

  async function scanHostKey(connectionId) {
    const result = await guarded(async () => api(`connections/${connectionId}/ssh/scan`, { method: "POST", body: {} }));
    if (!result) return;
    const changed = result.requires_replacement;
    openModal({
      title: "SSH host key",
      body: `
        <p>Compare this fingerprint with the value shown on the server console. Do not trust it blindly.</p>
        <div class="pre">${esc(result.key_type)}\n${esc(result.fingerprint)}</div>
        ${
          changed
            ? `<div class="notice" data-tone="danger">${icon("alert", 18)}<div><strong>Host key changed</strong>This key differs from the trusted key. Replacing it is a high-risk action.</div></div>`
            : ""
        }
        ${
          result.trusted && result.matches_trusted
            ? `<div class="notice" data-tone="ok">${icon("check", 18)}<div><strong>Key matches</strong>The presented key matches the trusted key.</div></div>`
            : ""
        }
        ${
          !result.trusted
            ? `<div class="notice">${icon("alert", 18)}<div><strong>Not yet trusted</strong>SSH operations fail until this key is trusted.</div></div>`
            : ""
        }`,
      actions: [
        { label: "Cancel", variant: "ghost", onClick: closeModal },
        {
          label: changed ? "Replace trusted key" : "Trust this key",
          variant: "danger",
          onClick: () =>
            guarded(async () => {
              await api(`connections/${connectionId}/ssh/trust`, {
                method: "POST",
                body: { fingerprint: result.fingerprint, replace: Boolean(changed) },
              });
              closeModal();
              toast("Host key trusted.");
              await navigate("connections");
            }),
        },
      ],
    });
  }

  /* ------------------------------------------------------------ credentials */

  async function viewCredentials() {
    const data = await api("credentials");
    state.credentials = data.items;
    $("#section").innerHTML = `
      <div class="grid grid-2">
        <section class="card glass" aria-labelledby="add-credential-title">
          <div class="card-header">
            <h3 id="add-credential-title">Add credential</h3>
            ${badge("encrypted at rest", "ok")}
          </div>
          <form class="form" id="credential-form" novalidate>
            <div class="field">
              <label for="cred-name">Name</label>
              <input id="cred-name" required maxlength="64" autocomplete="off" placeholder="Home Assistant read-only token">
              <p class="field-hint">Descriptive names make approvals easier to review.</p>
            </div>
            <div class="field">
              <label for="cred-kind">Kind</label>
              <select id="cred-kind">
                ${CREDENTIAL_KINDS.map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join("")}
              </select>
            </div>
            <div id="credential-fields"></div>
            <div class="button-row">
              <button type="submit" class="button" data-variant="primary" id="save-credential">
                ${icon("key", 16)}<span>Save credential</span>
              </button>
            </div>
            <p class="field-hint">Saved secrets are never shown again; only a masked indicator and metadata are returned.</p>
          </form>
        </section>

        <section class="card glass" aria-labelledby="vault-notes-title">
          <h3 id="vault-notes-title">How the vault protects secrets</h3>
          <ul class="security-list">
            <li>AES-256-GCM with a unique nonce per record; the record id and format version are authenticated as associated data.</li>
            <li>Argon2id derives the key-encryption key from the master password; the password itself is never written to disk.</li>
            <li>Auto-unlock stores a separate key file in <span class="code-hint">/data</span>. If both files are stolen, encryption at rest does not protect the secrets.</li>
            <li>A fully compromised Home Assistant host can inspect runtime memory. Encryption at rest is not runtime isolation.</li>
            <li>Rotating the data key re-encrypts every record; changing the password re-wraps the key.</li>
          </ul>
        </section>
      </div>

      <div class="list mt-4">
        ${
          state.credentials.length
            ? state.credentials.map(credentialItem).join("")
            : emptyState("No credentials stored", "Add a credential before creating a connection.", "key")
        }
      </div>`;

    const kindSelect = $("#cred-kind");
    const renderFields = () => {
      $("#credential-fields").innerHTML = credentialFields(kindSelect.value);
    };
    renderFields();
    kindSelect.addEventListener("change", renderFields);
    const form = $("#credential-form");
    attachBlurValidation(form);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitCredential(form, kindSelect.value);
    });
    $$("[data-rotate]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => rotateCredential(button.dataset.rotate))
    );
    $$("[data-delete-credential]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => deleteCredential(button.dataset.deleteCredential))
    );
  }

  function credentialItem(credential) {
    return `
      <div class="list-item">
        <div class="list-item-top">
          <span class="list-item-title">
            ${icon("key", 16)}${esc(credential.name)}
            ${badge(String(credential.kind).replaceAll("_", " "), "info")}
          </span>
          <div class="button-row">
            <button type="button" class="button" data-size="sm" data-rotate="${esc(credential.id)}">
              ${icon("rotate", 14)}<span>Rotate</span>
            </button>
          </div>
        </div>
        <div class="mono">${esc(credential.masked)}</div>
        <div class="button-row">
          <span class="list-item-meta">
            Updated ${fmtDate(credential.updated_at)} ·
            ${
              credential.used_by_connections.length
                ? `used by ${esc(credential.used_by_connections.join(", "))}`
                : "unused"
            }
          </span>
          <div class="danger-zone">
            <button type="button" class="button" data-size="sm" data-variant="danger"
                    data-delete-credential="${esc(credential.id)}">
              ${icon("trash", 14)}<span>Delete</span>
            </button>
          </div>
        </div>
      </div>`;
  }

  function secretField(id, label, placeholder) {
    return `<div class="field">
      <label for="${id}">${esc(label)}</label>
      <input type="password" id="${id}" autocomplete="new-password" placeholder="${esc(placeholder)}">
      <p class="field-error" id="${id}-error"></p>
    </div>`;
  }

  function credentialFields(kind) {
    if (kind === "home_assistant_token") return secretField("cred-value", "Long-lived access token", "Paste the token");
    if (kind === "proxmox_token") {
      return `<div class="field">
          <label for="cred-token-id">Token ID</label>
          <input id="cred-token-id" required autocomplete="off" placeholder="user@realm!tokenname">
          <p class="field-error" id="cred-token-id-error"></p>
        </div>
        ${secretField("cred-value", "Token secret", "Paste the secret")}`;
    }
    if (kind === "ssh_private_key") {
      return `<div class="field">
          <label for="cred-value">Private key (PEM or OpenSSH)</label>
          <textarea id="cred-value" data-code="true" spellcheck="false" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
          <p class="field-error" id="cred-value-error"></p>
        </div>
        ${secretField("cred-passphrase", "Key passphrase (optional)", "Leave empty if none")}`;
    }
    if (kind === "ca_certificate") {
      return `<div class="field">
          <label for="cred-value">CA certificate (PEM)</label>
          <textarea id="cred-value" data-code="true" spellcheck="false" placeholder="-----BEGIN CERTIFICATE-----"></textarea>
          <p class="field-error" id="cred-value-error"></p>
        </div>`;
    }
    return secretField("cred-value", "Secret value", "Paste the secret");
  }

  function collectCredential(kind) {
    const read = (id) => document.getElementById(id)?.value || "";
    const errors = [];
    let value = {};
    if (kind === "home_assistant_token") value = { token: read("cred-value") };
    else if (kind === "proxmox_token") value = { token_id: read("cred-token-id"), token_secret: read("cred-value") };
    else if (kind === "ssh_private_key") {
      value = { private_key: read("cred-value") };
      const passphrase = read("cred-passphrase");
      if (passphrase) value.passphrase = passphrase;
    } else if (kind === "ca_certificate") value = { ca_cert: read("cred-value") };
    else value = { value: read("cred-value") };

    if (kind === "proxmox_token") {
      if (!value.token_id) errors.push({ fieldId: "cred-token-id", message: "Token ID is required." });
      if (!value.token_secret) errors.push({ fieldId: "cred-value", message: "Token secret is required." });
    } else {
      const single = value.value || value.token || value.private_key || value.ca_cert;
      if (!single) errors.push({ fieldId: "cred-value", message: "A secret value is required." });
    }
    return { value, errors };
  }

  async function submitCredential(form, kind) {
    clearErrors(form);
    const errors = [];
    const name = $("#cred-name").value.trim();
    if (!name) errors.push({ fieldId: "cred-name", message: "Enter a name." });
    const collected = collectCredential(kind);
    errors.push(...collected.errors);
    if (errors.length) {
      showErrors(form, "The credential was not saved.", errors);
      return;
    }
    await withBusy($("#save-credential"), () =>
      guarded(async () => {
        await api("credentials", { method: "POST", body: { name, kind, value: collected.value } });
        toast("Credential saved.");
        await navigate("credentials");
      })
    ).catch((error) => showErrors(form, "The credential was not saved.", [{ message: errorText(error) }]));
  }

  function rotateCredential(credentialId) {
    const credential = state.credentials.find((item) => item.id === credentialId);
    if (!credential) return;
    const card = openModal({
      title: `Rotate ${credential.name}`,
      body: `<p>Replace the stored value. Update the upstream service before testing the connection again.</p>
             <form class="form" id="rotate-form" novalidate>${credentialFields(credential.kind)}</form>`,
      actions: [
        { label: "Cancel", variant: "ghost", onClick: closeModal },
        {
          label: "Rotate value",
          variant: "primary",
          onClick: (button) =>
            withBusy(button, () =>
              guarded(async () => {
                const form = $("#rotate-form", card);
                clearErrors(form);
                const collected = collectCredential(credential.kind);
                if (collected.errors.length) {
                  showErrors(form, "The credential was not rotated.", collected.errors);
                  return;
                }
                await api(`credentials/${credential.id}`, { method: "PATCH", body: { value: collected.value } });
                closeModal();
                toast("Credential rotated.");
                await navigate("credentials");
              })
            )
        },
      ],
      onMount: () => $("#cred-value")?.focus(),
    });
    void card;
  }

  async function deleteCredential(credentialId) {
    const credential = state.credentials.find((item) => item.id === credentialId);
    if (!credential) return;
    if (credential.used_by_connections.length) {
      toast(`In use by ${credential.used_by_connections.join(", ")}. Remove the connection first.`, "danger");
      return;
    }
    const confirmed = await confirmAction({
      title: `Delete ${credential.name}?`,
      message: "The encrypted value is removed from the vault and cannot be recovered from the gateway.",
      confirmLabel: "Delete credential",
      variant: "danger",
    });
    if (!confirmed) return;
    await guarded(async () => {
      await api(`credentials/${credentialId}`, { method: "DELETE" });
      toast("Credential deleted.");
      await navigate("credentials");
    });
  }

  /* ------------------------------------------------------------------ tools */

  async function viewTools() {
    const data = await api("tools");
    $("#section").innerHTML = `
      <div class="grid grid-3">
        ${data.groups
          .map(
            (group) => `
          <section class="card glass">
            <div class="card-header">
              <h3>${esc(CONNECTION_LABELS[group.id] || group.id)}</h3>
              ${group.enabled ? badge("enabled", "ok") : badge("disabled", "warn")}
            </div>
            <p class="card-hint">${group.tools.length} tools</p>
            <label class="checkbox-row mt-2">
              <input type="checkbox" data-group="${esc(group.id)}" ${group.enabled ? "checked" : ""}>
              <span>Expose this group to agents</span>
            </label>
          </section>`
          )
          .join("")}
      </div>
      <div class="button-row my-4">
        <button type="button" class="button" data-variant="primary" id="save-groups">
          ${icon("check", 16)}<span>Save tool groups</span>
        </button>
      </div>
      <section class="card glass" aria-labelledby="tools-table-title">
        <div class="card-header">
          <h3 id="tools-table-title">Registered tools</h3>
          <span class="card-hint">one authorization path for REST and MCP</span>
        </div>
        <div class="table-wrap" role="region" aria-label="Registered tools" tabindex="0">
          <table>
            <caption>Every call is authorized by the same policy engine, whatever the transport.</caption>
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">Group</th>
                <th scope="col">Risk</th>
                <th scope="col">Required scope</th>
                <th scope="col">Description</th>
              </tr>
            </thead>
            <tbody>
              ${data.tools
                .map(
                  (tool) => `
                <tr>
                  <td class="mono">${esc(tool.name)}</td>
                  <td>${esc(tool.group)}</td>
                  <td>${riskBadge(tool.risk)}</td>
                  <td class="mono">${esc(tool.scope)}</td>
                  <td>${esc(tool.description)}</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </section>`;

    $("#save-groups").addEventListener("click", (event) =>
      withBusy(event.currentTarget, () =>
        guarded(async () => {
          const groups = $$("[data-group]", $("#section"))
            .filter((box) => box.checked)
            .map((box) => box.dataset.group);
          await api("tools/groups", { method: "PUT", body: { groups } });
          toast("Tool group state saved.");
          await navigate("tools");
        })
      )
    );
  }

  /* ------------------------------------------------------------ permissions */

  async function viewPermissions() {
    const [agentData, scopeData, templateData] = await Promise.all([
      api("agents"),
      api("scopes"),
      api("policy-templates"),
    ]);
    state.agents = agentData.items;
    state.scopes = scopeData.scopes;
    state.templates = templateData.templates;

    $("#section").innerHTML = `
      <section class="card glass mb-4" aria-labelledby="create-agent-title">
        <div class="card-header">
          <h3 id="create-agent-title">Create agent identity</h3>
          ${badge("token shown once", "warn")}
        </div>
        <form class="form" id="agent-form" novalidate>
          <div class="form-row">
            <div class="field">
              <label for="agent-name">Name</label>
              <input id="agent-name" required maxlength="64" autocomplete="off" placeholder="windows-claude-agent">
            </div>
            <div class="field">
              <label for="agent-expiry">Expiry (optional)</label>
              <input id="agent-expiry" type="datetime-local">
              <p class="field-hint">Rotation is still recommended even without an expiry.</p>
            </div>
          </div>
          <fieldset class="field plain-fieldset">
            <legend class="field-label mb-2">Scopes</legend>
            <div class="chip-row" id="agent-scopes">
              ${state.scopes
                .map(
                  (scope) => `
                <label class="chip" data-selected="false">
                  <input type="checkbox" value="${esc(scope.id)}">
                  <span>${esc(scope.id)}</span>
                </label>`
                )
                .join("")}
            </div>
            <p class="field-hint mt-2">
              At least one scope is required. Scope grants are further limited by the agent's policy.
            </p>
            <p class="field-error" id="agent-scopes-error"></p>
          </fieldset>
          <div class="form-row">
            <div class="field">
              <label for="agent-template">Initial policy</label>
              <select id="agent-template">
                <option value="deny">Deny all (configure later)</option>
                <option value="home_assistant_read_all">Home Assistant read-only</option>
                <option value="proxmox_read_all">Proxmox read-only</option>
                <option value="ssh_read">SSH read-only</option>
              </select>
            </div>
            <div class="field">
              <label for="agent-notes">Notes</label>
              <input id="agent-notes" maxlength="500" autocomplete="off" placeholder="Owner, purpose, ticket">
            </div>
          </div>
          <div class="button-row">
            <button type="submit" class="button" data-variant="primary" id="create-agent">
              ${icon("plus", 16)}<span>Create agent</span>
            </button>
          </div>
        </form>
      </section>

      <div class="list">
        ${
          state.agents.length
            ? state.agents.map(agentItem).join("")
            : emptyState(
                "No agent identities",
                "Create a separate, revocable token for each agent. Never share tokens between agents.",
                "shield"
              )
        }
      </div>`;

    $$("#agent-scopes .chip").forEach((chip) => {
      const input = $("input", chip);
      const sync = () => {
        chip.dataset.selected = String(input.checked);
      };
      chip.addEventListener("click", (event) => {
        if (event.target !== input) {
          input.checked = !input.checked;
          sync();
        }
      });
      input.addEventListener("change", sync);
    });

    const form = $("#agent-form");
    attachBlurValidation(form);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      createAgent(form);
    });

    $$("[data-policy]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => openPolicyEditor(button.dataset.policy))
    );
    $$("[data-rotate-token]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => rotateAgent(button.dataset.rotateToken))
    );
    $$("[data-revoke]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => revokeAgent(button.dataset.revoke))
    );
    $$("[data-delete-agent]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => deleteAgent(button.dataset.deleteAgent))
    );

    var simBlock = document.createElement("section");
    simBlock.className = "card glass mt-4";
    var agentOptions = state.agents.map(function(a){ return '<option value="' + esc(a.id) + '">' + esc(a.name) + ' — ' + esc(a.token_prefix||"") + '</option>'; }).join("");
    var connOptions = state.connections.map(function(c){ return '<option value="' + esc(c.id) + '">' + esc(c.name) + ' — ' + esc(c.type) + '</option>'; }).join("") || '<option value="">No connections</option>';
    simBlock.innerHTML = "<div class=\"card-header\"><h3>Policy Simulator</h3><span class=\"card-hint\">dry-run an operation before live execution</span></div><form class=\"form\" id=\"sim-form\" novalidate><div class=\"form-row\"><div class=\"field\"><label for=\"sim-agent\">Agent</label><select id=\"sim-agent\">" + agentOptions + "</select></div><div class=\"field\"><label for=\"sim-connection\">Connection</label><select id=\"sim-connection\">" + connOptions + "</select></div></div><div class=\"form-row\"><div class=\"field\"><label for=\"sim-tool\">Tool</label><input id=\"sim-tool\" required placeholder=\"ha.get_state\" autocomplete=\"off\"></div><div class=\"field\"><label for=\"sim-params\">Parameters (JSON)</label><textarea id=\"sim-params\" data-code=\"true\" spellcheck=\"false\" placeholder='{&quot;entity_id&quot;:&quot;light.kitchen&quot;}'>{\"entity_id\":\"light.kitchen\"}</textarea></div></div><div class=\"button-row\"><button type=\"submit\" class=\"button\" data-variant=\"primary\" id=\"sim-run\">" + icon("check",16) + "<span>Evaluate</span></button></div></form><div id=\"sim-result\" class=\"mt-3\" aria-live=\"polite\"></div><p class=\"field-hint mt-2\">Calls <span class=\"code-hint\">POST /api/admin/policies/evaluate</span> with real scope, group, and policy evaluation. No side effects.</p>";
    $("#section").appendChild(simBlock);
    if (!state.connections.length) {
      api("connections").then(function(d){ state.connections=d.items||[]; }).catch(function(){});
    }
    document.getElementById("sim-form").addEventListener("submit", async function(e){
      e.preventDefault();
      var btn=document.getElementById("sim-run");
      await withBusy(btn, function(){ return guarded(async function(){
        var agentId=document.getElementById("sim-agent").value;
        var connectionId=document.getElementById("sim-connection").value;
        var tool=document.getElementById("sim-tool").value.trim();
        var params={};
        try { var raw=document.getElementById("sim-params").value.trim(); if(raw) params=JSON.parse(raw); } catch { toast("Parameters must be valid JSON.", "danger"); return; }
        var result=await api("policies/evaluate", {method:"POST", body:{agent_id:agentId, tool:tool, connection_id:connectionId, params:params}});
        var tone = result.decision==="allow_immediate" ? "ok" : result.decision==="require_approval" ? "warn" : "danger";
        var checks = (result.checks||[]).map(function(c){ return '<div class="diag-step"><span>' + esc(c.detail||c.check) + '</span>' + badge(c.ok?"ok":"fail", c.ok?"ok":"danger") + '</div>'; }).join("");
        document.getElementById("sim-result").innerHTML='<div class="notice" data-tone="' + tone + '">' + icon(tone==="ok"?"check":"alert",18) + '<div><strong>' + esc(result.decision) + ' — ' + esc(result.reason) + '</strong>' + (result.require_approval?'<p class="card-hint">Approval required before execution.</p>':"") + '</div></div><div class="diag-steps mt-3">' + checks + '</div><div class="detail-list mt-3"><div class="detail-row"><dt>Policy revision</dt><dd class="mono">' + esc(result.policy_revision||"") + '</dd></div><div class="detail-row"><dt>Limits</dt><dd class="mono">' + esc(String(result.limits && result.limits.timeout_seconds||"")) + 's · ' + esc(String(result.limits && result.limits.max_output_bytes||"")) + 'B</dd></div></div><pre class="pre mt-2">' + esc(JSON.stringify(result,null,2)) + '</pre>';
      }); });
    });
  }

  function agentItem(agent) {
    return `
      <div class="list-item">
        <div class="list-item-top">
          <span class="list-item-title">
            ${icon("shield", 16)}${esc(agent.name)}
            ${agent.enabled ? badge("enabled", "ok") : badge("revoked", "danger")}
            ${badge(`${agent.policy_rules} policy rules`, "info")}
          </span>
          <div class="button-row">
            <button type="button" class="button" data-size="sm" data-policy="${esc(agent.id)}">
              ${icon("settings", 14)}<span>Policy</span>
            </button>
            <button type="button" class="button" data-size="sm" data-rotate-token="${esc(agent.id)}">
              ${icon("rotate", 14)}<span>Rotate token</span>
            </button>
          </div>
        </div>
        <div class="chip-row">
          ${agent.scopes.map((scope) => `<span class="chip">${esc(scope)}</span>`).join("")}
        </div>
        <div class="button-row">
          <span class="list-item-meta">
            prefix ${esc(agent.token_prefix)} · created ${fmtDate(agent.created_at)} ·
            last used ${relTime(agent.last_used_at)}${agent.expires_at ? ` · expires ${fmtDate(agent.expires_at)}` : ""}
          </span>
          <div class="danger-zone">
            <button type="button" class="button" data-size="sm" data-variant="danger"
                    data-revoke="${esc(agent.id)}" ${agent.enabled ? "" : "disabled"}>
              ${icon("close", 14)}<span>Revoke</span>
            </button>
            <button type="button" class="button" data-size="sm" data-variant="danger"
                    data-delete-agent="${esc(agent.id)}">
              ${icon("trash", 14)}<span>Delete</span>
            </button>
          </div>
        </div>
        ${agent.notes ? `<p class="card-hint">${esc(agent.notes)}</p>` : ""}
      </div>`;
  }

  async function createAgent(form) {
    clearErrors(form);
    const name = $("#agent-name").value.trim();
    const scopes = $$("#agent-scopes input:checked").map((input) => input.value);
    const errors = [];
    if (!name) errors.push({ fieldId: "agent-name", message: "Enter an agent name." });
    if (!scopes.length) errors.push({ fieldId: "agent-scopes", message: "Select at least one scope." });
    if (errors.length) {
      showErrors(form, "The agent was not created.", errors);
      return;
    }
    const templateKey = $("#agent-template").value;
    const policy = templateKey === "deny" ? { version: 1, rules: [] } : state.templates[templateKey];
    const expiry = $("#agent-expiry").value;
    const body = {
      name,
      scopes,
      notes: $("#agent-notes").value.trim(),
      policy,
    };
    if (expiry) body.expires_at = new Date(expiry).toISOString();

    await withBusy($("#create-agent"), () =>
      guarded(async () => {
        const result = await api("agents", { method: "POST", body });
        await navigate("permissions");
        showToken(result.token, result.agent.name);
      })
    ).catch((error) => showErrors(form, "The agent was not created.", [{ message: errorText(error) }]));
  }

  function showToken(token, agentName) {
    openModal({
      title: "Agent token",
      body: `
        <div class="notice" data-tone="danger">
          ${icon("alert", 18)}
          <div><strong>Copy this token now</strong>It is not stored in the browser and cannot be shown again.</div>
        </div>
        <p>Authentication header for <strong>${esc(agentName)}</strong>:</p>
        <div class="token-box" id="token-value">${esc(token)}</div>
        <div class="button-row mt-2">
          <button type="button" class="button" data-size="sm" id="copy-token">
            ${icon("copy", 14)}<span>Copy token</span>
          </button>
        </div>
        <p class="field-hint">Send it as <span class="code-hint">Authorization: Bearer &lt;token&gt;</span>.
          Never put tokens in URLs, query strings, or logs.</p>`,
      actions: [{ label: "I stored it", variant: "primary", onClick: closeModal }],
      onMount: () => {
        $("#copy-token").addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(token);
            toast("Token copied to the clipboard.");
          } catch {
            toast("Clipboard access was denied. Select the token and copy it manually.", "danger");
          }
        });
        $("#token-value").setAttribute("tabindex", "0");
        $("#token-value").focus();
      },
    });
  }

  async function openPolicyEditor(agentId) {
    const agent = state.agents.find((item) => item.id === agentId);
    if (!agent) return;
    const policy = await guarded(async () => api(`agents/${agentId}/policy`));
    if (!policy) return;
    const card = openModal({
      title: `Policy — ${agent.name}`,
      body: `
        <p class="field-hint">Deny by default. Rules match tool patterns and connection ids;
          <span class="code-hint">"*"</span> is allowed. Every non-read tool still requires approval.</p>
        <div class="button-row" role="group" aria-label="Policy templates">
          <button type="button" class="button" data-size="sm" data-template="home_assistant_read_all">HA read-only</button>
          <button type="button" class="button" data-size="sm" data-template="proxmox_read_all">Proxmox read-only</button>
          <button type="button" class="button" data-size="sm" data-template="ssh_read">SSH read-only</button>
          <button type="button" class="button" data-size="sm" data-template="journal_read">Journal read-only</button>
          <button type="button" class="button" data-size="sm" data-template="journal_readwrite">Journal read/write</button>
          <button type="button" class="button" data-size="sm" data-template="deny">Deny all</button>
        </div>
        <form class="form" id="policy-form" novalidate>
          <div class="field">
            <label for="policy-json">Policy document (JSON)</label>
            <textarea id="policy-json" data-code="true" spellcheck="false"
                      aria-describedby="policy-json-hint">${esc(JSON.stringify(policy.document, null, 2))}</textarea>
            <p class="field-hint" id="policy-json-hint">Unknown fields and constraint names are rejected; a malformed document is not saved.</p>
            <p class="field-error" id="policy-json-error"></p>
          </div>
        </form>`,
      actions: [
        { label: "Cancel", variant: "ghost", onClick: closeModal },
        {
          label: "Save policy",
          variant: "primary",
          onClick: (button) =>
            withBusy(button, () =>
              guarded(async () => {
                const form = $("#policy-form", card);
                clearErrors(form);
                let document;
                try {
                  document = JSON.parse($("#policy-json", card).value);
                } catch {
                  showErrors(form, "The policy was not saved.", [
                    { fieldId: "policy-json", message: "The policy is not valid JSON." },
                  ]);
                  return;
                }
                try {
                  await api(`agents/${agentId}/policy`, { method: "PUT", body: { document } });
                } catch (error) {
                  showErrors(form, "The policy was not saved.", [
                    { fieldId: "policy-json", message: errorText(error) },
                  ]);
                  return;
                }
                closeModal();
                toast("Policy saved.");
                await navigate("permissions");
              })
            ),
        },
      ],
    });
    $$("[data-template]", card).forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.template;
        $("#policy-json", card).value = JSON.stringify(
          key === "deny" ? { version: 1, rules: [] } : state.templates[key],
          null,
          2
        );
        $("#policy-json", card).focus();
      });
    });
  }

  async function rotateAgent(agentId) {
    const confirmed = await confirmAction({
      title: "Rotate this agent token?",
      message: "The previous token stops working immediately. The new token is shown once.",
      confirmLabel: "Rotate token",
    });
    if (!confirmed) return;
    await guarded(async () => {
      const result = await api(`agents/${agentId}/rotate-token`, { method: "POST", body: {} });
      await navigate("permissions");
      showToken(result.token, result.agent.name);
    });
  }

  async function revokeAgent(agentId) {
    const confirmed = await confirmAction({
      title: "Revoke this agent?",
      message: "The token is destroyed and every future request from this agent is rejected. This cannot be undone.",
      confirmLabel: "Revoke agent",
      variant: "danger",
    });
    if (!confirmed) return;
    await guarded(async () => {
      await api(`agents/${agentId}/revoke`, { method: "POST", body: {} });
      toast("Agent revoked.");
      await navigate("permissions");
    });
  }

  async function deleteAgent(agentId) {
    const confirmed = await confirmAction({
      title: "Delete this agent identity?",
      message: "The identity and its policy are removed permanently. Prefer revoking if you only need to block access.",
      confirmLabel: "Delete agent",
      variant: "danger",
      requireText: "DELETE",
    });
    if (!confirmed) return;
    await guarded(async () => {
      await api(`agents/${agentId}`, { method: "DELETE" });
      toast("Agent deleted.");
      await navigate("permissions");
    });
  }

  /* -------------------------------------------------------------- approvals */

  async function viewApprovals() {
    const [pending, recent] = await Promise.all([
      api("approvals?status=pending&limit=100"),
      api("approvals?limit=50"),
    ]);
    state.approvals = pending.items;
    const decided = recent.items.filter((item) => item.status !== "pending");
    $("#section").innerHTML = `
      <div class="notice">
        ${icon("shield", 18)}
        <div>
          <strong>Single-use, expiring, parameter-bound</strong>
          An approval is bound to the agent, tool, connection, exact parameters, and policy revision.
          Agents cannot approve their own request. Review the parameters before you approve.
        </div>
      </div>

      <section class="card glass mt-4" aria-labelledby="pending-title">
        <div class="card-header">
          <h3 id="pending-title">Pending approvals (${pending.items.length})</h3>
          <button type="button" class="button" data-size="sm" id="refresh-approvals">
            ${icon("refresh", 14)}<span>Refresh</span>
          </button>
        </div>
        <div class="list" id="pending-list">
          ${
            pending.items.length
              ? pending.items.map(approvalItem).join("")
              : emptyState(
                  "No pending approvals",
                  "Policy-permitted write operations appear here for review.",
                  "approvals"
                )
          }
        </div>
      </section>

      <section class="card glass mt-4" aria-labelledby="decisions-title">
        <h3 id="decisions-title">Recent decisions</h3>
        <div class="table-wrap" role="region" aria-label="Recent approval decisions" tabindex="0">
          <table>
            <caption>Approval history is retained according to the audit retention setting.</caption>
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">Parameters</th>
                <th scope="col">Status</th>
                <th scope="col">Decided by</th>
                <th scope="col">Expires</th>
              </tr>
            </thead>
            <tbody>
              ${
                decided.length
                  ? decided
                      .map(
                        (approval) => `
                    <tr>
                      <td class="mono">${esc(approval.tool)}</td>
                      <td><span class="mono truncate" title="${esc(approval.params_summary)}">${esc(approval.params_summary)}</span></td>
                      <td>${statusBadge(approval.status)}</td>
                      <td class="mono">${esc(approval.decided_by || "—")}</td>
                      <td class="nowrap">${fmtDate(approval.expires_at)}</td>
                    </tr>`
                      )
                      .join("")
                  : `<tr><td colspan="5">${emptyState("No decisions yet", "Approved and rejected requests appear here.", "audit")}</td></tr>`
              }
            </tbody>
          </table>
        </div>
      </section>`;

    $("#refresh-approvals").addEventListener("click", () => loadRoute("approvals", { focus: false }));
    bindApprovalButtons();
    if (state.refreshTimer) clearInterval(state.refreshTimer);
    state.refreshTimer = setInterval(() => {
      if (state.route === "approvals" && !state.modalOpen) refreshPending();
    }, 15000);
  }

  function approvalItem(approval) {
    return `
      <div class="list-item">
        <div class="list-item-top">
          <span class="list-item-title">${icon("approvals", 16)}<span class="mono">${esc(approval.tool)}</span>
            ${badge("pending", "warn")}
          </span>
          <div class="button-row">
            <button type="button" class="button" data-size="sm" data-variant="primary" data-approve="${esc(approval.id)}">
              ${icon("check", 14)}<span>Approve</span>
            </button>
          </div>
        </div>
        <p class="list-item-meta">
          agent ${esc(String(approval.agent_id).slice(0, 12))} ·
          connection ${esc(String(approval.connection_id || "—").slice(0, 12))} ·
          expires ${fmtDate(approval.expires_at)}
        </p>
        <pre class="pre">${esc(approval.params_summary)}</pre>
        <div class="button-row">
          <div class="danger-zone">
            <button type="button" class="button" data-size="sm" data-variant="danger" data-reject="${esc(approval.id)}">
              ${icon("close", 14)}<span>Reject</span>
            </button>
          </div>
        </div>
      </div>`;
  }

  function bindApprovalButtons() {
    $$("[data-approve]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => decideApproval(button.dataset.approve, "approve"))
    );
    $$("[data-reject]", $("#section")).forEach((button) =>
      button.addEventListener("click", () => decideApproval(button.dataset.reject, "reject"))
    );
  }

  async function refreshPending() {
    try {
      const pending = await api("approvals?status=pending&limit=100");
      const list = $("#pending-list");
      const title = $("#pending-title");
      if (title) title.textContent = `Pending approvals (${pending.items.length})`;
      if (list) {
        list.innerHTML = pending.items.length
          ? pending.items.map(approvalItem).join("")
          : emptyState("No pending approvals", "Policy-permitted write operations appear here for review.", "approvals");
        bindApprovalButtons();
        if (pending.items.length) announce(`${pending.items.length} pending approvals`);
      }
      const nav = $(".nav-count");
      if (nav) nav.textContent = String(pending.items.length);
    } catch {
      /* A silent refresh must not interrupt the operator. */
    }
  }

  async function decideApproval(approvalId, action) {
    const approving = action === "approve";
    const confirmed = await confirmAction({
      title: approving ? "Approve this operation?" : "Reject this operation?",
      message: approving
        ? "Check the exact parameters first. The agent may execute this operation once, with these parameters, before it expires."
        : "The requesting agent is told the request was rejected.",
      confirmLabel: approving ? "Approve once" : "Reject request",
      variant: approving ? "primary" : "danger",
    });
    if (!confirmed) return;
    await guarded(async () => {
      await api(`approvals/${approvalId}/${action}`, {
        method: "POST",
        body: approving ? {} : { reason: "" },
      });
      toast(approving ? "Approved. The agent may run it once before it expires." : "Rejected.");
      await refreshStatus();
      await navigate("approvals");
    });
  }

  /* ------------------------------------------------------------------ audit */

  async function viewAudit() {
    state.audit.sort = { key: "ts", direction: "desc" };
    $("#section").innerHTML = `
      <section class="card glass" aria-labelledby="filters-title">
        <div class="card-header"><h3 id="filters-title">Filters</h3></div>
        <form class="form" id="audit-filters">
          <div class="form-row">
            <div class="field">
              <label for="audit-tool">Tool name</label>
              <input id="audit-tool" autocomplete="off" placeholder="ha.call_service">
              <p class="field-hint">Exact match. Leave empty for all tools.</p>
            </div>
            <div class="field">
              <label for="audit-status">Status</label>
              <select id="audit-status">
                <option value="">Any status</option>
                <option value="succeeded">succeeded</option>
                <option value="denied">denied</option>
                <option value="failed">failed</option>
                <option value="pending_approval">pending_approval</option>
              </select>
            </div>
          </div>
          <div class="button-row">
            <button type="submit" class="button" data-variant="primary">
              ${icon("search", 16)}<span>Apply filters</span>
            </button>
            <button type="button" class="button" data-variant="ghost" id="clear-filters">Clear</button>
          </div>
        </form>
      </section>

      <section class="card glass mt-4" aria-labelledby="events-title">
        <div class="card-header">
          <h3 id="events-title">Events <span class="card-hint" id="audit-count"></span></h3>
          <span class="card-hint">Secrets, authorization headers, and private keys are redacted; payloads are truncated.</span>
        </div>
        <div id="audit-results"></div>
      </section>`;

    const load = async () => {
      const tool = $("#audit-tool").value.trim();
      const status = $("#audit-status").value;
      const params = new URLSearchParams({ limit: "200" });
      if (tool) params.set("tool", tool);
      if (status) params.set("status", status);
      const data = await api(`audit?${params.toString()}`);
      const count = $("#audit-count");
      if (count) count.textContent = `(${data.total})`;
      const results = $("#audit-results");
      if (!results) return;
      results.innerHTML = auditTable(data.items, { compact: false });
      bindAuditSort(results, data.items, { compact: false });
    };

    $("#audit-filters").addEventListener("submit", (event) => {
      event.preventDefault();
      guarded(load);
    });
    $("#clear-filters").addEventListener("click", () => {
      $("#audit-tool").value = "";
      $("#audit-status").value = "";
      guarded(load);
    });
    await load();
  }

  /* ---------------------------------------------------------------- journal */

  function verificationBadge(level, verified) {
    if (!level) return "";
    const tone = verified ? "ok" : level === "blocked_infrastructure" ? "warn" : "";
    const glyph = verified ? icon("check", 13) : "";
    return `<span class="badge"${tone ? ` data-tone="${tone}"` : ""}>${glyph}${esc(level)}</span>`;
  }

  function journalTaskItem(task) {
    const blocked = task.blocked_by?.length
      ? `<p class="list-item-meta">${icon("alert", 14)} Blocked by ${task.blocked_by.length} unfinished task(s)</p>`
      : "";
    const criteria = task.acceptance_criteria
      ? `<p class="card-hint">Acceptance: ${esc(task.acceptance_criteria)}</p>`
      : "";
    return `<div class="list-item">
      <div class="list-item-top">
        <span class="list-item-title">${esc(task.title)}</span>
        <span>${badge(`P${task.priority}`, "info")} ${statusBadge(task.status)} ${verificationBadge(task.verification_level, task.verified)}</span>
      </div>
      ${task.description ? `<p>${esc(task.description)}</p>` : ""}
      ${criteria}${blocked}
      <p class="list-item-meta">Owner ${esc(task.owner_name || task.owner_id)} · updated ${esc(fmtDate(task.updated_at))}${
        task.commit_sha ? ` · <span class="mono">${esc(String(task.commit_sha).slice(0, 12))}</span>` : ""
      }${task.branch ? ` · <span class="mono">${esc(task.branch)}</span>` : ""}</p>
      ${task.files?.length ? `<p class="list-item-meta mono">${task.files.map((file) => esc(file)).join(" · ")}</p>` : ""}
    </div>`;
  }

  function journalTimelineItem(item) {
    let evidence = "";
    if (item.record_type === "test_run" && item.evidence) {
      evidence = `<div class="journal-evidence mono">$ ${esc(item.evidence.command)}<br>exit ${
        item.evidence.exit_code ?? "—"
      } · ${esc(item.evidence.environment || "environment not recorded")}${
        item.evidence.run_at ? ` · ran ${esc(fmtDate(item.evidence.run_at))}` : ""
      }</div>`;
    } else if (item.record_type === "decision" && item.evidence?.rationale) {
      evidence = `<p class="card-hint">Rationale: ${esc(item.evidence.rationale)}</p>`;
    } else if (item.record_type === "issue" && item.evidence) {
      evidence = `<p class="card-hint">Severity ${esc(item.evidence.severity)}${
        item.evidence.learned ? ` · Learned: ${esc(item.evidence.learned)}` : ""
      }</p>`;
    }
    const body = item.body
      ? `<p>${esc(item.body.length > 600 ? `${item.body.slice(0, 600)}…` : item.body)}</p>`
      : "";
    return `<div class="list-item">
      <div class="list-item-top">
        <span class="list-item-title">${esc(item.title)}</span>
        <span>${badge(item.record_type)} ${badge(item.kind)} ${statusBadge(item.status)} ${verificationBadge(item.verification_level, item.verified)}</span>
      </div>
      ${body}${evidence}
      <p class="list-item-meta">${esc(item.actor_name || item.actor_id)} (${esc(item.actor_type)} via ${esc(
        item.source
      )}) · ${esc(fmtDate(item.created_at))} (${esc(relTime(item.created_at))})${
        item.commit_sha ? ` · <span class="mono">${esc(String(item.commit_sha).slice(0, 12))}</span>` : ""
      }</p>
      ${item.files?.length ? `<p class="list-item-meta mono">${item.files.map((file) => esc(file)).join(" · ")}</p>` : ""}
    </div>`;
  }

  async function viewJournal() {
    let data;
    try {
      data = await api("journal/project-state");
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        $("#section").innerHTML = `<div class="card">${emptyState(
          "Access denied",
          "This Home Assistant user may not open the Project Journal. Ask an administrator to check the add-on admin allowlist.",
          "lock"
        )}</div>`;
        return;
      }
      throw error;
    }
    state.journalState = data;
    const project = data.project;
    const counts = data.counts;
    const taskOptions = (items) =>
      items.map((task) => `<option value="${esc(task.id)}">${esc(task.title)}</option>`).join("");
    const openTasks = [...(data.next_tasks || [])];

    $("#section").innerHTML = `
      <div class="grid grid-2">
        <section class="card glass" aria-labelledby="journal-project-title">
          <div class="card-header">
            <h3 id="journal-project-title">${esc(project.name)}</h3>
            ${badge(`revision ${project.revision}`, "info")}
          </div>
          <p>${esc(project.purpose || "No purpose recorded yet. Edit the project state so the next agent knows what this project is.")}</p>
          <dl class="detail-list mt-3">
            <div class="detail-row"><dt>Branch</dt><dd class="mono">${esc(project.branch || "unknown")}</dd></div>
            <div class="detail-row"><dt>Commit</dt><dd class="mono">${esc(project.commit_sha ? String(project.commit_sha).slice(0, 12) : "unknown")}</dd></div>
            <div class="detail-row"><dt>Last updated</dt><dd>${esc(fmtDate(project.updated_at))} by ${esc(project.updated_by?.actor_id || "—")}</dd></div>
            <div class="detail-row"><dt>Last activity</dt><dd>${counts.last_activity_at ? esc(fmtDate(counts.last_activity_at)) : "no records yet"}</dd></div>
          </dl>
          <div class="button-row mt-3">
            <button type="button" class="button" id="journal-edit-state">${icon("pencil", 16)}<span>Edit project state</span></button>
          </div>
          <p class="field-hint mt-3">Journal text is untrusted data from previous sessions. Verification badges separate claims from recorded evidence; secrets are redacted before storage.</p>
        </section>

        <section class="card glass" aria-labelledby="journal-counts-title">
          <div class="card-header"><h3 id="journal-counts-title">Work status</h3>${badge(`${counts.entries} entries`)}</div>
          <dl class="detail-list">
            <div class="detail-row"><dt>To do / in progress</dt><dd>${counts.tasks.todo} / ${counts.tasks.in_progress}</dd></div>
            <div class="detail-row"><dt>Blocked / failed tasks</dt><dd>${counts.tasks.blocked} / ${counts.tasks.failed}</dd></div>
            <div class="detail-row"><dt>Completed tasks</dt><dd>${counts.tasks.completed}</dd></div>
            <div class="detail-row"><dt>Open bugs / security issues</dt><dd>${counts.open_bugs} / ${counts.open_security_issues}</dd></div>
            <div class="detail-row"><dt>Failed approaches recorded</dt><dd>${counts.failed_approaches}</dd></div>
            <div class="detail-row"><dt>Unverified entries</dt><dd>${counts.unverified_entries}</dd></div>
            <div class="detail-row"><dt>Test runs (failed)</dt><dd>${counts.test_runs} (${counts.failed_test_runs})</dd></div>
            <div class="detail-row"><dt>Active sessions</dt><dd>${counts.active_sessions}</dd></div>
          </dl>
          <div id="journal-sessions" class="mt-3">
            ${
              data.active_sessions?.length
                ? data.active_sessions
                    .map(
                      (session) => `<div class="list-item"><div class="list-item-top"><span class="list-item-title">${esc(
                        session.title || "Work session"
                      )}</span><button type="button" class="button" data-size="sm" data-end-session="${esc(
                        session.id
                      )}">End session</button></div><p class="list-item-meta">${esc(
                        session.actor_name || session.actor_id
                      )} · started ${esc(fmtDate(session.started_at))}</p></div>`
                    )
                    .join("")
                : `<p class="field-hint">No active agent sessions. Agents start sessions through the journal API.</p>`
            }
          </div>
        </section>
      </div>

      <section class="card glass mt-4" aria-labelledby="journal-next-title">
        <div class="card-header"><h3 id="journal-next-title">Next steps</h3><span class="card-hint">Highest priority first (lowest P number)</span></div>
        <div class="list">
          ${
            openTasks.length
              ? openTasks.map(journalTaskItem).join("")
              : emptyState("No open tasks", "Record the next task below, or let an agent create it through the journal API.", "journal")
          }
        </div>
      </section>

      <div class="grid grid-2 mt-4">
        <section class="card glass" aria-labelledby="journal-record-title">
          <div class="card-header"><h3 id="journal-record-title">Record work</h3>${badge("attributed to you", "info")}</div>
          <form class="form" id="journal-entry-form">
            <div class="field"><label for="journal-entry-title">Title</label>
              <input id="journal-entry-title" required maxlength="200" autocomplete="off" placeholder="Verified the journal migration"></div>
            <div class="form-row">
              <div class="field"><label for="journal-entry-outcome">Outcome</label>
                <select id="journal-entry-outcome">
                  <option value="info">info</option><option value="in_progress">in_progress</option>
                  <option value="completed">completed</option><option value="failed">failed</option>
                  <option value="blocked">blocked</option>
                </select></div>
              <div class="field"><label for="journal-entry-level">Verification</label>
                <select id="journal-entry-level">
                  <option value="claimed">claimed (no evidence)</option>
                  <option value="automated_tests">automated_tests</option>
                  <option value="integration_verified">integration_verified</option>
                  <option value="not_tested">not_tested</option>
                  <option value="blocked_infrastructure">blocked_infrastructure</option>
                </select></div>
            </div>
            <div class="field"><label for="journal-entry-task">Task (optional)</label>
              <select id="journal-entry-task"><option value="">No task</option>${taskOptions(openTasks)}</select></div>
            <div class="field"><label for="journal-entry-body">Details</label>
              <textarea id="journal-entry-body" maxlength="8000" rows="4" placeholder="What happened, what evidence backs it, what remains. Never paste credentials."></textarea></div>
            <div class="button-row"><button type="submit" class="button" data-variant="primary">${icon("plus", 16)}<span>Record entry</span></button></div>
          </form>
        </section>

        <section class="card glass" aria-labelledby="journal-task-title">
          <div class="card-header"><h3 id="journal-task-title">Add task</h3></div>
          <form class="form" id="journal-task-form">
            <div class="field"><label for="journal-new-task-title">Title</label>
              <input id="journal-new-task-title" required maxlength="200" autocomplete="off" placeholder="Run the live HA OS install check"></div>
            <div class="field"><label for="journal-new-task-priority">Priority (0 = highest)</label>
              <input id="journal-new-task-priority" type="number" min="0" max="100" value="50"></div>
            <div class="field"><label for="journal-new-task-criteria">Acceptance criteria</label>
              <textarea id="journal-new-task-criteria" maxlength="4000" rows="3" placeholder="Exact checks that must pass before this counts as done"></textarea></div>
            <div class="field"><label for="journal-new-task-description">Description</label>
              <textarea id="journal-new-task-description" maxlength="8000" rows="3"></textarea></div>
            <div class="button-row"><button type="submit" class="button" data-variant="primary">${icon("plus", 16)}<span>Add task</span></button></div>
          </form>
        </section>
      </div>

      <section class="card glass mt-4" aria-labelledby="journal-timeline-title">
        <div class="card-header"><h3 id="journal-timeline-title">Activity timeline <span class="card-hint" id="journal-timeline-count"></span></h3></div>
        <form class="form" id="journal-filters">
          <div class="form-row">
            <div class="field"><label for="journal-q">Search</label>
              <input id="journal-q" autocomplete="off" placeholder="task, decision, issue, command…"></div>
            <div class="field"><label for="journal-category">Category</label>
              <select id="journal-category">
                <option value="">All work</option><option value="completed">completed</option>
                <option value="failed">failed</option><option value="blocked">blocked</option>
                <option value="unverified">unverified</option>
              </select></div>
            <div class="field"><label for="journal-type">Record type</label>
              <select id="journal-type">
                <option value="">All types</option><option value="entry">work entries</option>
                <option value="decision">decisions</option><option value="issue">issues &amp; failed approaches</option>
                <option value="test_run">test runs</option>
              </select></div>
            <div class="field"><label for="journal-commit">Commit</label>
              <input id="journal-commit" autocomplete="off" class="mono" placeholder="0123abcd…"></div>
          </div>
          <div class="button-row">
            <button type="submit" class="button" data-variant="primary">${icon("search", 16)}<span>Apply filters</span></button>
            <button type="button" class="button" data-variant="ghost" id="journal-clear-filters">Clear</button>
          </div>
        </form>
        <div id="journal-timeline" class="list mt-4" aria-live="polite"></div>
      </section>

      <section class="card glass mt-4" aria-labelledby="journal-handoff-title">
        <div class="card-header"><h3 id="journal-handoff-title">Session handoff</h3><span class="card-hint">Preview and edit before copying. Nothing is executed automatically.</span></div>
        <form class="form" id="journal-handoff-form">
          <div class="field"><label for="journal-focus-task">Focus task (optional)</label>
            <select id="journal-focus-task"><option value="">Highest-priority open task</option>${taskOptions(openTasks)}</select></div>
          <div class="button-row">
            <button type="submit" class="button" data-variant="primary" id="journal-generate">${icon("terminal", 16)}<span>Generate Handoff Prompt</span></button>
            <button type="button" class="button" id="journal-copy" disabled>${icon("copy", 16)}<span>Copy prompt</span></button>
          </div>
        </form>
        <div class="field mt-3"><label for="journal-prompt">Handoff prompt</label>
          <textarea id="journal-prompt" class="journal-prompt mono" rows="14" spellcheck="false" placeholder="Generate a handoff to preview it here. You can edit the text before copying."></textarea></div>
        <div id="journal-handoffs" class="list mt-3"></div>
      </section>`;

    const loadTimeline = async () => {
      const params = new URLSearchParams({ limit: "50" });
      const q = $("#journal-q").value.trim();
      const category = $("#journal-category").value;
      const type = $("#journal-type").value;
      const commit = $("#journal-commit").value.trim();
      if (q) params.set("q", q);
      if (category) params.set("category", category);
      if (type) params.set("record_type", type);
      if (commit) params.set("commit_sha", commit);
      const results = $("#journal-timeline");
      results.innerHTML = skeleton(2);
      const timeline = await api(`journal/timeline?${params.toString()}`);
      const count = $("#journal-timeline-count");
      if (count) count.textContent = `(${timeline.total})`;
      results.innerHTML = timeline.items.length
        ? timeline.items.map(journalTimelineItem).join("")
        : emptyState(
            "No journal records match",
            "Nothing is stored for this filter yet. Agents record work through the journal API; this panel never invents entries.",
            "journal"
          );
    };

    const loadHandoffs = async () => {
      const box = $("#journal-handoffs");
      const listing = await api("journal/handoffs?limit=10");
      box.innerHTML = listing.items.length
        ? `<p class="field-hint">Previous handoffs</p>` +
          listing.items
            .map(
              (handoff) => `<div class="list-item"><div class="list-item-top"><span class="list-item-title">${esc(
                handoff.title || "Session handoff"
              )}</span><button type="button" class="button" data-size="sm" data-load-handoff="${esc(
                handoff.id
              )}">Load</button></div><p class="list-item-meta">${esc(
                handoff.actor_name || handoff.actor_id
              )} · ${esc(fmtDate(handoff.created_at))} · based on revision ${esc(handoff.based_on_revision)}</p></div>`
            )
            .join("")
        : `<p class="field-hint">No handoffs generated yet.</p>`;
      $$("[data-load-handoff]", box).forEach((button) =>
        button.addEventListener("click", () =>
          withBusy(button, () =>
            guarded(async () => {
              const handoff = await api(`journal/handoffs/${button.dataset.loadHandoff}`);
              $("#journal-prompt").value = handoff.prompt;
              $("#journal-copy").disabled = false;
              toast("Handoff loaded. Review it before copying.");
            })
          )
        )
      );
    };

    $("#journal-filters").addEventListener("submit", (event) => {
      event.preventDefault();
      guarded(loadTimeline);
    });
    $("#journal-clear-filters").addEventListener("click", () => {
      $("#journal-q").value = "";
      $("#journal-category").value = "";
      $("#journal-type").value = "";
      $("#journal-commit").value = "";
      guarded(loadTimeline);
    });

    $("#journal-entry-form").addEventListener("submit", (event) => {
      event.preventDefault();
      guarded(async () => {
        const title = $("#journal-entry-title").value.trim();
        if (!title) {
          fieldError($("#journal-entry-title"), "A title is required.");
          return;
        }
        const taskId = $("#journal-entry-task").value;
        await api("journal/entries", {
          method: "POST",
          body: {
            title,
            outcome: $("#journal-entry-outcome").value,
            verification_level: $("#journal-entry-level").value,
            task_id: taskId || null,
            body: $("#journal-entry-body").value.trim(),
          },
        });
        toast("Work entry recorded.");
        await navigate("journal");
      });
    });

    $("#journal-task-form").addEventListener("submit", (event) => {
      event.preventDefault();
      guarded(async () => {
        const title = $("#journal-new-task-title").value.trim();
        if (!title) {
          fieldError($("#journal-new-task-title"), "A title is required.");
          return;
        }
        await api("journal/tasks", {
          method: "POST",
          body: {
            title,
            priority: Number($("#journal-new-task-priority").value || 50),
            acceptance_criteria: $("#journal-new-task-criteria").value.trim(),
            description: $("#journal-new-task-description").value.trim(),
          },
        });
        toast("Task added to Next steps.");
        await navigate("journal");
      });
    });

    $$("[data-end-session]", $("#section")).forEach((button) =>
      button.addEventListener("click", () =>
        withBusy(button, () =>
          guarded(async () => {
            const confirmed = await confirmAction({
              title: "End this agent session?",
              message: "The session is marked ended in the journal. Its records stay unchanged.",
              confirmLabel: "End session",
            });
            if (!confirmed) return;
            await api(`journal/sessions/${button.dataset.endSession}/end`, {
              method: "POST",
              body: { summary: "Ended by administrator from the panel" },
            });
            toast("Session ended.");
            await navigate("journal");
          })
        )
      )
    );

    $("#journal-edit-state").addEventListener("click", () => openJournalStateEditor(project));

    $("#journal-handoff-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const button = $("#journal-generate");
      withBusy(button, () =>
        guarded(async () => {
          const focus = $("#journal-focus-task").value;
          const handoff = await api("journal/handoffs/generate", {
            method: "POST",
            body: focus ? { focus_task_id: focus } : {},
          });
          $("#journal-prompt").value = handoff.prompt;
          $("#journal-copy").disabled = false;
          toast("Handoff generated from stored records. Review before use.");
          await loadHandoffs();
        })
      );
    });

    $("#journal-copy").addEventListener("click", (event) => {
      const button = event.currentTarget;
      withBusy(button, async () => {
        const prompt = $("#journal-prompt").value;
        if (!prompt.trim()) {
          toast("Generate or load a handoff first.", "danger");
          return;
        }
        try {
          await navigator.clipboard.writeText(prompt);
          toast("Handoff prompt copied.");
        } catch {
          $("#journal-prompt").select();
          toast("Clipboard unavailable; the prompt is selected for manual copy.", "danger");
        }
      });
    });

    await loadTimeline();
    await loadHandoffs();
  }

  function openJournalStateEditor(project) {
    openModal({
      title: "Edit project state",
      body: `<form class="form" id="journal-state-form">
        <div class="field"><label for="js-name">Project name</label>
          <input id="js-name" maxlength="200" value="${esc(project.name)}"></div>
        <div class="field"><label for="js-purpose">Purpose</label>
          <textarea id="js-purpose" maxlength="4000" rows="3">${esc(project.purpose)}</textarea></div>
        <div class="field"><label for="js-architecture">Architecture</label>
          <textarea id="js-architecture" maxlength="4000" rows="3">${esc(project.architecture)}</textarea></div>
        <div class="field"><label for="js-conventions">Conventions</label>
          <textarea id="js-conventions" maxlength="4000" rows="3">${esc(project.conventions)}</textarea></div>
        <div class="field"><label for="js-constraints">Security &amp; operating constraints</label>
          <textarea id="js-constraints" maxlength="4000" rows="3">${esc(project.constraints)}</textarea></div>
        <div class="form-row">
          <div class="field"><label for="js-branch">Branch</label>
            <input id="js-branch" maxlength="128" class="mono" value="${esc(project.branch || "")}"></div>
          <div class="field"><label for="js-commit">Commit (hex)</label>
            <input id="js-commit" maxlength="64" class="mono" value="${esc(project.commit_sha || "")}"></div>
        </div>
        <p class="field-hint">Saved with revision checking: if an agent updated the state meanwhile, this save is rejected instead of overwriting it.</p>
      </form>`,
      actions: [
        { label: "Cancel", variant: "ghost", onClick: () => closeModal() },
        {
          label: "Save project state",
          variant: "primary",
          icon: "check",
          onClick: (button) =>
            withBusy(button, () =>
              guarded(async () => {
                const branch = $("#js-branch").value.trim();
                const commit = $("#js-commit").value.trim();
                await api("journal/project-state", {
                  method: "PUT",
                  body: {
                    expected_revision: project.revision,
                    name: $("#js-name").value.trim(),
                    purpose: $("#js-purpose").value.trim(),
                    architecture: $("#js-architecture").value.trim(),
                    conventions: $("#js-conventions").value.trim(),
                    constraints_text: $("#js-constraints").value.trim(),
                    branch: branch || null,
                    commit_sha: commit || null,
                  },
                });
                closeModal();
                toast("Project state saved.");
                await navigate("journal");
              })
            ),
        },
      ],
    });
  }

  /* --------------------------------------------------------------- settings */

  async function viewSettings() {
    const [settingsData, vault] = await Promise.all([api("settings"), api("vault")]);
    const settings = settingsData.settings;
    const vaultMeta = state.status.vault;
    $("#section").innerHTML = `
      <div class="grid grid-2">
        <section class="card glass" aria-labelledby="emergency-title">
          <h3 id="emergency-title">Agent access</h3>
          <div class="notice" ${state.status.emergency_disabled ? 'data-tone="danger"' : 'data-tone="ok"'}>
            ${icon(state.status.emergency_disabled ? "alert" : "shield", 18)}
            <div>
              <strong>${state.status.emergency_disabled ? "Agent access is disabled" : "Agent access is enabled"}</strong>
              The emergency stop rejects every agent REST and MCP request immediately. Management access is unaffected.
            </div>
          </div>
          <div class="button-row mt-3">
            <button type="button" class="button" data-variant="${
              state.status.emergency_disabled ? "primary" : "danger"
            }" id="toggle-access">
              ${icon(state.status.emergency_disabled ? "unlock" : "alert", 16)}
              <span>${state.status.emergency_disabled ? "Enable agent access" : "Emergency disable agent access"}</span>
            </button>
          </div>
        </section>

        <section class="card glass" aria-labelledby="limits-title">
          <h3 id="limits-title">Limits and retention</h3>
          <form class="form" id="limits-form" novalidate>
            <div class="form-row">
              <div class="field">
                <label for="set-retention">Audit retention (days)</label>
                <input id="set-retention" type="number" min="1" max="3650" required value="${esc(settings.audit_retention_days)}">
              </div>
              <div class="field">
                <label for="set-ttl">Approval lifetime (seconds)</label>
                <input id="set-ttl" type="number" min="30" max="3600" required value="${esc(settings.approval_ttl_seconds)}">
              </div>
            </div>
            <div class="form-row">
              <div class="field">
                <label for="set-timeout">Operation timeout ceiling (s)</label>
                <input id="set-timeout" type="number" min="5" max="120" required
                       value="${esc(settings.max_operation_timeout_seconds ?? 120)}">
              </div>
              <div class="field">
                <label for="set-output">Output ceiling (bytes)</label>
                <input id="set-output" type="number" min="1024" max="1048576" required
                       value="${esc(settings.max_output_bytes ?? 262144)}">
              </div>
            </div>
            <div class="button-row">
              <button type="submit" class="button" data-variant="primary" id="save-limits">
                ${icon("check", 16)}<span>Save limits</span>
              </button>
            </div>
          </form>
        </section>
      </div>

      <div class="grid grid-2 mt-4">
        <section class="card glass" aria-labelledby="vault-title">
          <h3 id="vault-title">Vault lifecycle</h3>
          <p class="card-hint">
            Version ${esc(vault.version || "—")} · ${esc(vault.records)} records ·
            ${esc(vault.kdf?.algorithm || "argon2id")}
            (${esc(vault.kdf?.memory_cost_kib || 0)} KiB, t=${esc(vault.kdf?.time_cost || 0)})
          </p>
          <div class="button-row mt-3">
            <button type="button" class="button" data-size="sm" id="vault-lock">${icon("lock", 14)}<span>Lock now</span></button>
            <button type="button" class="button" data-size="sm" id="vault-export">${icon("copy", 14)}<span>Encrypted export</span></button>
            <button type="button" class="button" data-size="sm" id="vault-import">${icon("rotate", 14)}<span>Restore export</span></button>
          </div>
          <hr class="divider">
          <form class="form" id="password-form" novalidate>
            <div class="form-row">
              <div class="field">
                <label for="pw-current">Current master password</label>
                <input type="password" id="pw-current" autocomplete="current-password">
              </div>
              <div class="field">
                <label for="pw-new">New master password</label>
                <input type="password" id="pw-new" autocomplete="new-password">
                <p class="field-hint">Minimum 12 characters. There is no recovery path.</p>
              </div>
            </div>
            <div class="button-row">
              <button type="submit" class="button" data-size="sm" id="change-password">Change password</button>
            </div>
          </form>
          <form class="form mt-3" id="rotate-form" novalidate>
            <div class="field">
              <label for="rotate-password">Rotate the data encryption key</label>
              <input type="password" id="rotate-password" autocomplete="current-password" aria-describedby="rotate-hint">
              <p class="field-hint" id="rotate-hint">Requires the master password and re-encrypts every record.</p>
            </div>
            <div class="button-row">
              <button type="submit" class="button" data-size="sm" id="rotate-key">Rotate data key</button>
            </div>
          </form>
        </section>

        <section class="card glass" aria-labelledby="auto-title">
          <h3 id="auto-title">Auto-unlock and maintenance</h3>
          <div class="notice" ${vaultMeta.auto_unlock_enabled ? 'data-tone="danger"' : ""}>
            ${icon(vaultMeta.auto_unlock_enabled ? "alert" : "lock", 18)}
            <div>
              <strong>${vaultMeta.auto_unlock_enabled ? "Auto-unlock is on" : "Auto-unlock is off"}</strong>
              ${
                vaultMeta.auto_unlock_enabled
                  ? "A random unlock key is stored beside the vault in /data. If an attacker obtains both files, the secrets can be decrypted."
                  : "After a restart the vault stays locked until a person enters the master password."
              }
            </div>
          </div>
          <div class="button-row mt-3">
            <button type="button" class="button" data-size="sm" id="toggle-auto">
              ${icon(vaultMeta.auto_unlock_enabled ? "lock" : "unlock", 14)}
              <span>${vaultMeta.auto_unlock_enabled ? "Disable auto-unlock" : "Enable auto-unlock (reduces protection)"}</span>
            </button>
          </div>
          <hr class="divider">
          <div class="button-row">
            <button type="button" class="button" data-size="sm" id="purge">${icon("trash", 14)}<span>Purge expired records</span></button>
            <button type="button" class="button" data-size="sm" id="redaction-check">${icon("shield", 14)}<span>Test redaction</span></button>
          </div>
          <div id="redaction-result" class="mt-3" aria-live="polite"></div>
        </section>
      </div>

      <section class="card glass mt-4" aria-labelledby="diag-title">
        <div class="card-header"><h3 id="diag-title">Diagnostics &amp; Health</h3><span class="card-hint">redacted bundle — zero secrets</span></div>
        <div class="grid grid-2">
          <div>
            <h4>Readiness</h4>
            <div id="readiness-box" class="pre" aria-live="polite">Loading…</div>
            <div class="button-row mt-2">
              <button type="button" class="button" data-size="sm" id="refresh-readiness">${icon("refresh",14)}<span>Refresh</span></button><a class="button" data-size="sm" href="api/admin/readiness" target="_blank" rel="noreferrer">Open JSON</a>
            </div>
          </div>
          <div>
            <h4>Diagnostic bundle</h4>
            <p class="field-hint">Exports schema version, counts, connection health, recent warnings, and recent audit — all recursive secrets redacted.</p>
            <pre class="pre" id="diag-preview">Loading…</pre>
            <div class="button-row mt-2">
              <button type="button" class="button" data-size="sm" data-variant="primary" id="diag-export">${icon("copy",14)}<span>Download gateway-diagnostics-redacted.json</span></button><button type="button" class="button" data-size="sm" id="diag-refresh">${icon("refresh",14)}<span>Refresh preview</span></button>
            </div>
          </div>
        </div>
      </section>

      <section class="card glass mt-4" aria-labelledby="endpoints-title">
        <h3 id="endpoints-title">Network and endpoints</h3>
        <dl class="detail-list">
          <div class="detail-row"><dt>Management listener</dt>
            <dd class="mono">ingress :${esc(state.status.listeners.ingress_port)} · Supervisor only</dd></div>
          <div class="detail-row"><dt>Agent listener</dt>
            <dd class="mono">:${esc(state.status.listeners.agent_port)} ${
              state.status.listeners.agent_tls ? "TLS enabled" : "HTTP — use TLS or a VPN off-LAN"
            }</dd></div>
          <div class="detail-row"><dt>MCP endpoint</dt><dd class="mono">/mcp · Streamable HTTP</dd></div>
          <div class="detail-row"><dt>REST API</dt><dd class="mono">/api/v1</dd></div>
          <div class="detail-row"><dt>Host port</dt><dd>Not published by default in the add-on network settings</dd></div>
        </dl>
      </section>`;

    $("#toggle-access").addEventListener("click", toggleAgents);
    $("#vault-lock").addEventListener("click", () =>
      guarded(async () => {
        await api("vault/lock", { method: "POST", body: {} });
        toast("Vault locked.");
        await boot();
      })
    );
    $("#vault-export").addEventListener("click", exportVault);
    $("#vault-import").addEventListener("click", importVault);
    $("#toggle-auto").addEventListener("click", toggleAutoUnlock);
    $("#purge").addEventListener("click", () =>
      guarded(async () => {
        const result = await api("maintenance/purge", { method: "POST", body: {} });
        toast(`Purged ${result.audit_deleted} audit rows and ${result.approvals_deleted} approvals.`);
      })
    );
    $("#redaction-check").addEventListener("click", () =>
      guarded(async () => {
        const result = await api("audit/verify-redaction");
        $("#redaction-result").innerHTML = `
          <div class="pre">${esc(result.sample)}</div>
          <p class="field-hint mt-2">
            Known test secrets found after redaction: ${result.contains_known_secret ? "yes (failure)" : "no"}
          </p>`;
      })
    );

    var loadReadiness = async function(){
      try {
        var r = await api("readiness");
        var readinessBox = document.getElementById("readiness-box");
        if (readinessBox) readinessBox.textContent = JSON.stringify(r, null, 2);
      } catch (e) { var readinessBox2 = document.getElementById("readiness-box"); if (readinessBox2) readinessBox2.textContent = errorText(e); }
    };
    var loadDiag = async function(){
      try {
        var d = await api("diagnostics");
        var diagPreview = document.getElementById("diag-preview");
        if (diagPreview) diagPreview.textContent = JSON.stringify(d, null, 2).slice(0, 4000);
      } catch (e) { var diagPreview2 = document.getElementById("diag-preview"); if (diagPreview2) diagPreview2.textContent = errorText(e); }
    };
    loadReadiness();
    loadDiag();
    var refreshReadinessBtn = document.getElementById("refresh-readiness");
    if (refreshReadinessBtn) refreshReadinessBtn.addEventListener("click", loadReadiness);
    var diagRefreshBtn = document.getElementById("diag-refresh");
    if (diagRefreshBtn) diagRefreshBtn.addEventListener("click", loadDiag);
    var diagExportBtn = document.getElementById("diag-export");
    if (diagExportBtn) diagExportBtn.addEventListener("click", async function(){
      await guarded(async function(){
        var resp = await fetch("api/admin/diagnostics/export", {headers:{"X-Gateway-Request":"1"}, credentials:"same-origin"});
        if(!resp.ok) throw new ApiError("Export failed", {status:resp.status});
        var blob = await resp.blob();
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href=url; a.download="gateway-diagnostics-redacted.json";
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function(){ URL.revokeObjectURL(url); },3000);
        toast("Diagnostics bundle downloaded.");
      });
    });


    const limitsForm = $("#limits-form");
    attachBlurValidation(limitsForm);
    limitsForm.addEventListener("submit", (event) => {
      event.preventDefault();
      saveLimits(limitsForm, settingsData.limits);
    });

    const passwordForm = $("#password-form");
    passwordForm.addEventListener("submit", (event) => {
      event.preventDefault();
      changePassword(passwordForm);
    });

    const rotateForm = $("#rotate-form");
    rotateForm.addEventListener("submit", (event) => {
      event.preventDefault();
      rotateKey(rotateForm);
    });
  }


  function tokenTone(status) {
    if (status === "active") return "ok";
    if (status === "expiring_soon") return "warn";
    if (status === "expired" || status === "revoked") return "danger";
    return "";
  }

  function warningCard(w) {
    return '<div class="warning-item" data-severity="' + esc(w.severity) + '"><div><strong>' + esc(w.title) + '</strong><p class="card-hint">' + esc(w.message) + '</p></div><button type="button" class="button" data-size="sm" data-go-section="' + esc(w.section) + '">' + esc(w.action_label || "View") + '</button></div>';
  }

  function releaseSectionBlocks(sections) {
    if (!sections || !Object.keys(sections).length) return '<p class="card-hint">No additional details.</p>';
    var out = "";
    for (var k in sections) {
      var bullets = sections[k];
      if (!bullets || !bullets.length) continue;
      if (k.toLowerCase() === "unreleased") continue;
      out += '<p class="release-section-title">' + esc(k) + '</p><ul class="release-bullets">' + bullets.map(function(b){ return '<li>' + esc(b) + '</li>'; }).join("") + '</ul>';
    }
    return out;
  }

  function diagStepRow(step) {
    var tone = step.status === "ok" ? "ok" : step.status === "skipped" ? "warn" : "danger";
    var label = step.label || step.step || step.name || "step";
    return '<div class="diag-step"><span>' + esc(label) + '</span>' + badge(step.status || "unknown", tone) + '</div>';
  }

  function applyTheme(theme) {
    var allowed = {system:1, light:1, dark:1};
    var choice = allowed[theme] ? theme : "system";
    if (choice === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", choice);
    document.querySelectorAll("[data-theme-choice]").forEach(function(b){
      b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice));
    });
  }

  function initTheme() {
    if (document.documentElement.dataset.theme) applyTheme(document.documentElement.dataset.theme);
  }

  /* --------------------------------------------------------------- agents */

  async function viewAgents() {
    var data = await api("agents");
    var agents = data.items || [];
    var enriched = agents;
    try {
      var diag = await api("diagnostics");
      if (diag && diag.agents) enriched = diag.agents;
    } catch {}

    $("#section").innerHTML = '<section class="card glass" aria-labelledby="create-agent-title"><div class="card-header"><h3 id="create-agent-title">Create agent identity</h3>' + badge("token shown once", "warn") + '</div><form class="form" id="agent-form" novalidate><div class="form-row"><div class="field"><label for="agent-name">Name</label><input id="agent-name" required maxlength="64" autocomplete="off" placeholder="windows-claude-agent"></div><div class="field"><label for="agent-expiry">Expiry (optional)</label><input id="agent-expiry" type="datetime-local"><p class="field-hint">Rotation is still recommended even without an expiry.</p></div></div><div class="field"><label for="agent-description">Description</label><input id="agent-description" maxlength="500" autocomplete="off" placeholder="Owner, purpose, ticket"><p class="field-hint">Visible to reviewers when approving requests.</p></div><fieldset class="field plain-fieldset"><legend class="field-label mb-2">Scopes</legend><div class="chip-row" id="agent-scopes">' + ((state.scopes && state.scopes.length ? state.scopes : [{id:"ha.read"},{id:"ha.write"},{id:"proxmox.read"},{id:"ssh.read"},{id:"ssh.execute"}]).map(function(s){ return '<label class="chip" data-selected="false"><input type="checkbox" value="' + esc(s.id) + '"><span>' + esc(s.id) + '</span></label>'; }).join("")) + '</div><p class="field-error" id="agent-scopes-error"></p></fieldset><div class="field"><label for="agent-template">Initial policy</label><select id="agent-template"><option value="deny">Deny all (configure later)</option><option value="home_assistant_read_all">Home Assistant read-only</option><option value="proxmox_read_all">Proxmox read-only</option><option value="ssh_read">SSH read-only</option></select></div><div class="button-row"><button type="submit" class="button" data-variant="primary" id="create-agent-btn">' + icon("plus", 16) + '<span>Create agent</span></button></div></form></section><div class="list mt-4">' + (enriched.length ? enriched.map(agentCard).join("") : emptyState("No agent identities","Create a separate, revocable token for each agent. Never share tokens between agents.","shield")) + '</div>';

    try {
      var scopeData = await api("scopes");
      var templateData = await api("policy-templates");
      state.scopes = scopeData.scopes || scopeData.items || state.scopes;
      state.templates = templateData.templates || state.templates;
    } catch {}

    $$("#agent-scopes .chip").forEach(function(chip){
      var input=$("input",chip);
      var sync=function(){ chip.dataset.selected=String(input.checked); };
      chip.addEventListener("click", function(e){ if(e.target!==input){ input.checked=!input.checked; sync(); }});
      input.addEventListener("change", sync);
    });

    var form=$("#agent-form");
    attachBlurValidation(form);
    form.addEventListener("submit", function(e){ e.preventDefault(); createAgentFromAgentsPage(form); });

    $$("[data-policy]", $("#section")).forEach(function(b){ b.addEventListener("click", function(){ openPolicyEditor(b.dataset.policy); }); });
    $$("[data-rotate-token]", $("#section")).forEach(function(b){ b.addEventListener("click", function(){ rotateAgent(b.dataset.rotateToken); }); });
    $$("[data-revoke]", $("#section")).forEach(function(b){ b.addEventListener("click", function(){ revokeAgent(b.dataset.revoke); }); });
    $$("[data-delete-agent]", $("#section")).forEach(function(b){ b.addEventListener("click", function(){ deleteAgent(b.dataset.deleteAgent); }); });
    $$("[data-audit-filter]", $("#section")).forEach(function(b){ b.addEventListener("click", function(){ navigate("audit"); }); });
  }

  function agentCard(agent) {
    var usage = agent.usage || {};
    var allowed = agent.allowed_tools || [];
    var denied = agent.denied_tools || [];
    var tokenStatus = agent.token_status || (agent.enabled ? "active" : "revoked");
    var chipsAllowed = allowed.map(function(t){ return '<span class="chip" data-selected="true">' + esc(t) + '</span>'; }).join("");
    var chipsDenied = denied.map(function(t){ return '<span class="chip" data-tone="muted">' + esc(t) + '</span>'; }).join("");
    var scopesChips = (agent.scopes||[]).map(function(s){ return '<span class="chip">' + esc(s) + '</span>'; }).join("");
    return '<div class="list-item"><div class="list-item-top"><span class="list-item-title">' + icon("shield", 16) + esc(agent.name) + ' ' + (agent.enabled ? badge("enabled", "ok") : badge("revoked", "danger")) + ' ' + badge(tokenStatus, tokenTone(tokenStatus)) + ' ' + badge(String(agent.policy_rules ?? agent.rule_count ?? 0) + ' policy rules', "info") + '</span><div class="button-row"><button type="button" class="button" data-size="sm" data-policy="' + esc(agent.id) + '">' + icon("settings", 14) + '<span>Policy</span></button><button type="button" class="button" data-size="sm" data-rotate-token="' + esc(agent.id) + '">' + icon("rotate", 14) + '<span>Rotate token</span></button></div></div>' + (agent.description || agent.notes ? '<p class="card-hint">' + esc(agent.description || agent.notes) + '</p>' : "") + '<div class="chip-row">' + scopesChips + '</div>' + (allowed.length || denied.length ? '<div class="chip-row mt-2">' + chipsAllowed + chipsDenied + '</div>' : "") + '<div class="detail-list mt-2"><div class="detail-row"><dt>Token</dt><dd class="mono">' + esc(agent.token_prefix||"—") + ' · ' + esc(tokenStatus) + (agent.expires_at ? ' · expires ' + esc(fmtDate(agent.expires_at)) : "") + '</dd></div><div class="detail-row"><dt>Last used</dt><dd>' + (agent.last_used_at ? esc(relTime(agent.last_used_at)) : "never") + ' · created ' + esc(fmtDate(agent.created_at)) + '</dd></div><div class="detail-row"><dt>Usage</dt><dd>' + esc(usage.total_requests ?? 0) + ' total · ' + esc(usage.succeeded_count ?? 0) + ' ok · ' + esc(usage.denied_count ?? 0) + ' denied · ' + esc(usage.rate_limited_count ?? 0) + ' throttled</dd></div>' + (agent.last_rotated_at ? '<div class="detail-row"><dt>Last rotated</dt><dd>' + esc(fmtDate(agent.last_rotated_at)) + '</dd></div>' : "") + '</div><div class="button-row mt-2"><span class="list-item-meta">id ' + esc(String(agent.id).slice(0,12)) + ' · prefix ' + esc(agent.token_prefix||"—") + '</span><div class="button-row"><button type="button" class="button" data-size="sm" data-audit-filter="' + esc(agent.id) + '">' + icon("audit",14) + '<span>Audit</span></button><div class="danger-zone"><button type="button" class="button" data-size="sm" data-variant="danger" data-revoke="' + esc(agent.id) + '" ' + (agent.enabled?"":"disabled") + '>' + icon("close",14) + '<span>Revoke</span></button><button type="button" class="button" data-size="sm" data-variant="danger" data-delete-agent="' + esc(agent.id) + '">' + icon("trash",14) + '<span>Delete</span></button></div></div></div></div>';
  }

  async function createAgentFromAgentsPage(form) {
    clearErrors(form);
    var name=$("#agent-name").value.trim();
    var scopes=$$("#agent-scopes input:checked").map(function(i){ return i.value; });
    var errors=[];
    if(!name) errors.push({fieldId:"agent-name", message:"Enter an agent name."});
    if(!scopes.length) errors.push({fieldId:"agent-scopes", message:"Select at least one scope."});
    if(errors.length){ showErrors(form,"The agent was not created.",errors); return; }
    var templateKey=$("#agent-template").value;
    var policy={version:1,rules:[]};
    if(templateKey!=="deny" && state.templates && state.templates[templateKey]) policy=state.templates[templateKey];
    var expiry=$("#agent-expiry").value;
    var descEl = $("#agent-description");
    var body={name:name, scopes:scopes, notes:(descEl ? descEl.value.trim() : ""), description:(descEl ? descEl.value.trim() : ""), policy:policy};
    if(expiry) body.expires_at=new Date(expiry).toISOString();
    await withBusy($("#create-agent-btn"), function(){ return guarded(async function(){
      var result=await api("agents",{method:"POST", body:body});
      await navigate("agents");
      showToken(result.token, result.agent.name);
    }); }).catch(function(e){ showErrors(form,"The agent was not created.",[{message:errorText(e)}]); });
  }

  /* ----------------------------------------------------------- agent access */

  function fmtDuration(seconds) {
    if (typeof seconds !== "number" || !isFinite(seconds)) return "—";
    if (seconds <= 0) return "expired";
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    if (days) return days + "d " + hours + "h";
    if (hours) return hours + "h " + minutes + "m";
    return minutes + "m";
  }

  function accessStatusTone(status) {
    if (status === "active") return "ok";
    if (status === "pending") return "info";
    if (status === "expiring_soon") return "warn";
    return "danger";
  }

  function accessSessionCard(session) {
    var conns = (session.connections || []).map(function (c) {
      return '<span class="chip">' + esc(c.name) + (c.type ? ' <span class="mono">' + esc(c.type) + "</span>" : "") + "</span>";
    }).join("");
    var scopes = (session.scopes || []).map(function (s) {
      return '<span class="chip" data-selected="true">' + esc(s) + "</span>";
    }).join("");
    var classes = (session.operation_classes || []).map(function (c) {
      return '<span class="chip" data-tone="' + (c === "read" ? "ok" : "warn") + '">' + esc(c) + "</span>";
    }).join("");
    return '<div class="list-item"><div class="list-item-top"><span class="list-item-title">' +
      icon("key", 16) + esc(session.name) + " " + badge(session.status, accessStatusTone(session.status)) +
      (session.handoff_pending ? " " + badge("handoff pending", "info") : "") +
      '</span><div class="button-row">' +
      '<button type="button" class="button" data-size="sm" data-session-activity="' + esc(session.id) + '">' + icon("audit", 14) + "<span>Activity</span></button>" +
      '<button type="button" class="button" data-size="sm" data-variant="danger" data-session-revoke="' + esc(session.id) + '"' +
      (session.status === "revoked" ? " disabled" : "") + ">" + icon("close", 14) + "<span>Revoke</span></button>" +
      "</div></div>" +
      '<div class="chip-row mt-2">' + scopes + classes + "</div>" +
      '<div class="detail-list mt-2">' +
      '<div class="detail-row"><dt>Connections</dt><dd>' + (conns || "none") + "</dd></div>" +
      '<div class="detail-row"><dt>Lifetime</dt><dd>' + esc(fmtDuration(session.issued_ttl_seconds || session.remaining_seconds)) +
      " · remaining " + esc(fmtDuration(session.remaining_seconds)) + "</dd></div>" +
      '<div class="detail-row"><dt>Created</dt><dd>' + esc(fmtDate(session.created_at)) + " by " + esc(session.created_by || "—") + "</dd></div>" +
      '<div class="detail-row"><dt>Expires</dt><dd>' + esc(fmtDate(session.expires_at)) + "</dd></div>" +
      '<div class="detail-row"><dt>Token</dt><dd class="mono">' + esc(session.token_prefix || "—") + " · last used " + (session.last_used_at ? esc(relTime(session.last_used_at)) : "never") + "</dd></div>" +
      '<div class="detail-row"><dt>Activity</dt><dd>' + esc(session.requests_last_hour || 0) + " requests in the last hour</dd></div>" +
      (session.revoked_at ? '<div class="detail-row"><dt>Revoked</dt><dd>' + esc(fmtDate(session.revoked_at)) + " · " + esc(session.revoked_reason || "") + "</dd></div>" : "") +
      "</div></div>";
  }

  async function viewAgentAccess() {
    var data = await api("agent-access");
    var settings = data.settings || {};
    var sessions = data.sessions || [];
    var connections = data.connections || [];
    var endpoints = data.endpoints || {};
    var restart = data.restart_policy_result || {};
    var baseUrl = settings.agent_api_base_url || "";
    var minMinutes = Math.round((settings.min_ttl_seconds || 300) / 60);
    var defaultMinutes = Math.round((settings.default_ttl_seconds || 3600) / 60);
    var maxHours = Math.round((settings.max_ttl_seconds || 86400) / 3600);

    var warning = "";
    if (!baseUrl) {
      warning = '<div class="notice" data-tone="warn">' + icon("alert", 18) +
        "<div><strong>Set the agent API base URL</strong>The handoff link must point at the agent listener, not the Home Assistant Ingress address. " +
        "Configure it below, for example <span class=\"code-hint\">http://homeassistant.local:8098</span>.</div></div>";
    }

    var reachable = endpoints.listener_reachable
      ? badge("listener reachable", "ok")
      : badge("listener unreachable", "danger");

    var settingSummary = '<div class="detail-list"><div class="detail-row"><dt>Default lifetime</dt><dd>' + esc(fmtDuration(settings.default_ttl_seconds)) + "</dd></div>" +
      '<div class="detail-row"><dt>Maximum lifetime</dt><dd>' + esc(fmtDuration(settings.max_ttl_seconds)) + "</dd></div>" +
      '<div class="detail-row"><dt>Restart policy</dt><dd>' + (settings.revoke_on_restart ? "revoke temporary sessions on restart" : "retain temporary sessions on restart") + "</dd></div>" +
      '<div class="detail-row"><dt>Agent page</dt><dd class="mono">' + esc(baseUrl ? baseUrl + "/agent" : "not configured") + "</dd></div>" +
      '<div class="detail-row"><dt>MCP endpoint</dt><dd class="mono">' + esc((baseUrl || "<gateway>") + "/mcp") + "</dd></div>" +
      '<div class="detail-row"><dt>REST base</dt><dd class="mono">' + esc((baseUrl || "<gateway>") + "/api/v1") + "</dd></div></div>";

    var form = '<section class="card glass" aria-labelledby="issue-session-title"><div class="card-header"><h3 id="issue-session-title">Generate temporary access</h3>' + badge("single-use handoff", "info") + "</div>" +
      '<p class="card-hint">The agent receives the session token on the agent page, never in the link itself. The link contains a one-time code and expires quickly.</p>' +
      warning +
      '<form class="form" id="access-form" novalidate>' +
      '<div class="form-row"><div class="field"><label for="access-name">Session name</label>' +
      '<input id="access-name" required maxlength="64" autocomplete="off" placeholder="codex-session-1" value="agent-session"></div>' +
      '<div class="field"><label for="access-ttl">Lifetime (minutes)</label>' +
      '<input id="access-ttl" type="number" min="' + minMinutes + '" max="' + maxHours * 60 + '" value="' + defaultMinutes + '">' +
      '<p class="field-hint">Between ' + minMinutes + " minutes and " + maxHours + " hours.</p></div></div>" +
      '<fieldset class="field plain-fieldset"><legend class="field-label mb-2">Authorized connections</legend>' +
      '<div class="chip-row" id="access-connections">' +
      (connections.length ? connections.map(function (c) {
        return '<label class="chip" data-selected="false"><input type="checkbox" value="' + esc(c.id) + '" data-connection-type="' + esc(c.type) + '"><span>' + esc(c.name) + ' <span class="mono">' + esc(CONNECTION_LABELS[c.type] || c.type) + "</span></span></label>";
      }).join("") : '<p class="field-hint">Create a connection first.</p>') +
      '</div><p class="field-error" id="access-connections-error"></p></fieldset>' +
      '<fieldset class="field plain-fieldset"><legend class="field-label mb-2">Scopes</legend>' +
      '<div class="chip-row" id="access-scopes">' +
      (data.scopes || []).map(function (s) {
        return '<label class="chip" data-selected="false" title="' + esc(s.description || "") + '"><input type="checkbox" value="' + esc(s.id) + '"><span>' + esc(s.id) + "</span></label>";
      }).join("") +
      '</div><p class="field-hint">Scopes must match the selected connection types; the gateway rejects mismatches.</p>' +
      '<p class="field-error" id="access-scopes-error"></p></fieldset>' +
      '<fieldset class="field plain-fieldset"><legend class="field-label mb-2">Operation classes</legend><div class="chip-row" id="access-classes">' +
      '<label class="chip" data-selected="true"><input type="checkbox" value="read" checked disabled><span>read (required)</span></label>' +
      '<label class="chip" data-selected="false"><input type="checkbox" value="write"><span>write (approval required)</span></label>' +
      '</div><p class="field-hint">Destructive operations can never be granted to a temporary session.</p></fieldset>' +
      '<label class="check-row"><input type="checkbox" id="access-revoke-previous" checked> <span>Revoke all previous temporary sessions when this one is generated</span></label>' +
      '<div class="button-row"><button type="submit" class="button" data-variant="primary" id="access-generate">' + icon("key", 16) + "<span>Generate access link</span></button>" +
      '<button type="button" class="button" data-variant="danger" id="access-revoke-all" data-danger-zone="true">' + icon("close", 16) + "<span>Revoke all sessions</span></button></div>" +
      "</form></section>";

    var settingsForm = '<section class="card glass" aria-labelledby="access-settings-title"><div class="card-header"><h3 id="access-settings-title">Agent access settings</h3></div>' +
      '<form class="form" id="access-settings-form" novalidate><div class="form-row">' +
      '<div class="field"><label for="access-default-ttl">Default lifetime (minutes)</label><input id="access-default-ttl" type="number" min="' + minMinutes + '" max="' + maxHours * 60 + '" value="' + defaultMinutes + '"></div>' +
      '<div class="field"><label for="access-max-ttl">Maximum lifetime (hours)</label><input id="access-max-ttl" type="number" min="' + Math.ceil(minMinutes / 60) + '" max="' + maxHours + '" value="' + maxHours + '"></div>' +
      "</div>" +
      '<div class="field"><label for="access-base-url">Agent API base URL</label>' +
      '<input id="access-base-url" maxlength="300" autocomplete="off" placeholder="http://homeassistant.local:8098" value="' + esc(baseUrl) + '">' +
      '<p class="field-hint">Externally reachable address of the agent listener (port 8098). No path, query, or credentials. Used for handoff links.</p></div>' +
      '<label class="check-row"><input type="checkbox" id="access-restart-revoke"' + (settings.revoke_on_restart ? " checked" : "") + '> <span>Revoke every temporary session when the gateway restarts</span></label>' +
      '<p class="field-hint">Revocation never rotates or deletes upstream Home Assistant, Proxmox, or SSH credentials.</p>' +
      '<div class="button-row"><button type="submit" class="button" id="access-save-settings">Save settings</button></div></form></section>';

    $("#section").innerHTML = warning + form + settingsForm +
      '<section class="card glass" aria-labelledby="access-status-title"><div class="card-header"><h3 id="access-status-title">Listener and limits</h3>' +
      reachable + "</div>" + settingSummary +
      '<div class="detail-list mt-2"><div class="detail-row"><dt>Listener probe</dt><dd>' + esc(endpoints.detail || "not checked") +
      " <span class=\"mono\">" + esc(endpoints.listener_url || "") + "</span></dd></div>" +
      '<div class="detail-row"><dt>Last restart</dt><dd>' + esc(restart.policy || "not started") +
      (typeof restart.revoked === "number" ? " · revoked " + esc(restart.revoked) + " session(s)" : "") + "</dd></div>" +
      '<div class="detail-row"><dt>Active / pending</dt><dd>' + esc(data.active_count || 0) + " active · " + esc(data.pending_count || 0) + " awaiting handoff</dd></div></div>" +
      '<p class="field-hint">' + (endpoints.note || "") + "</p>" +
      '<div class="button-row mt-2"><button type="button" class="button" data-size="sm" id="access-recheck">' + icon("refresh", 14) + "<span>Re-check listener</span></button></div>" +
      "</section>" +
      '<section aria-labelledby="access-sessions-title"><div class="card-header"><h3 id="access-sessions-title">Sessions</h3>' + badge(String(sessions.length) + " total", "muted") + "</div>" +
      '<div class="list mt-2">' + (sessions.length ? sessions.map(accessSessionCard).join("") : emptyState("No temporary sessions", "Generate a handoff link when an agent needs temporary access. Destructive operations are never granted.", "key")) + "</div></section>";

    $$("#access-connections .chip, #access-scopes .chip, #access-classes .chip").forEach(function (chip) {
      var input = $("input", chip);
      var sync = function () {
        chip.dataset.selected = String(input.checked);
      };
      chip.addEventListener("click", function (event) {
        if (event.target !== input && !input.disabled) {
          input.checked = !input.checked;
          sync();
        }
      });
      input.addEventListener("change", sync);
    });

    var formEl = $("#access-form");
    formEl.addEventListener("submit", function (event) {
      event.preventDefault();
      issueAgentSession();
    });
    $("#access-revoke-all").addEventListener("click", revokeAllSessions);
    $("#access-recheck").addEventListener("click", function () {
      guarded(function () { return api("agent-access/reachability", { method: "POST", body: {} }); })
        .then(function () { return loadRoute("access"); });
    });
    $("#access-settings-form").addEventListener("submit", function (event) {
      event.preventDefault();
      saveAgentAccessSettings();
    });
    $$("[data-session-revoke]").forEach(function (button) {
      button.addEventListener("click", function () { revokeAgentSession(button.dataset.sessionRevoke); });
    });
    $$("[data-session-activity]").forEach(function (button) {
      button.addEventListener("click", function () { openSessionActivity(button.dataset.sessionActivity); });
    });
  }

  async function issueAgentSession() {
    var connections = $$("#access-connections input:checked").map(function (input) { return input.value; });
    var scopes = $$("#access-scopes input:checked").map(function (input) { return input.value; });
    var classes = $$("#access-classes input:checked").map(function (input) { return input.value; });
    if (classes.indexOf("read") === -1) classes.push("read");
    var name = $("#access-name").value.trim();
    var ttl = parseInt($("#access-ttl").value, 10);
    var problems = [];
    if (!name || name.length < 3) problems.push({ fieldId: "access-name", message: "Enter a session name." });
    if (!connections.length) problems.push({ fieldId: "access-connections", message: "Select at least one connection." });
    if (!scopes.length) problems.push({ fieldId: "access-scopes", message: "Select at least one scope." });
    if (!isFinite(ttl) || ttl < 5) problems.push({ fieldId: "access-ttl", message: "Enter a lifetime of at least 5 minutes." });
    if (problems.length) {
      problems.forEach(function (problem) {
        var node = $("#" + problem.fieldId + "-error");
        if (node) node.textContent = problem.message;
      });
      toast("The session was not created.", "danger");
      return;
    }
    var body = {
      name: name,
      scopes: scopes,
      connection_ids: connections,
      operation_classes: classes,
      ttl_seconds: ttl * 60,
      revoke_previous: $("#access-revoke-previous").checked,
    };
    await withBusy($("#access-generate"), function () {
      return guarded(async function () {
        var result = await api("agent-access/sessions", { method: "POST", body: body });
        await loadRoute("access");
        showHandoff(result);
      });
    }).catch(function (error) {
      toast(errorText(error), "danger");
    });
  }

  function showHandoff(result) {
    var handoff = result.handoff || {};
    var session = result.session || {};
    var url = handoff.url || "";
    var configured = /^https?:\/\//i.test(url);
    openModal({
      title: "Agent access link",
      body: '<div class="notice" data-tone="info">' + icon("key", 18) +
        "<div><strong>One-time handoff link</strong>It contains a single-use code, not the token. Open it in the agent's browser; the agent page shows the token once and confirms the session scope.</div></div>" +
        '<p class="field-hint">Session <strong>' + esc(session.name || "") + "</strong> · expires " + esc(fmtDate(session.expires_at)) + "</p>" +
        '<div class="token-box" id="handoff-value">' + esc(url || "no base URL configured") + "</div>" +
        (!configured ? '<p class="field-error">Make the handoff link absolute by setting the agent API base URL in Agent access settings. A relative link only works on the agent listener.</p>' : "") +
        '<div class="button-row mt-2">' +
        '<button type="button" class="button" data-size="sm" id="handoff-copy">' + icon("copy", 14) + "<span>Copy link</span></button>" +
        (configured ? '<a class="button" data-size="sm" href="' + esc(url) + '" target="_blank" rel="noreferrer noopener" id="handoff-open">Open agent page</a>' : "") +
        "</div>" +
        '<p class="field-hint mt-2">The link expires at ' + esc(fmtDate(handoff.expires_at)) + " and can be redeemed once. If it expires unused, generate a new session.</p>",
      actions: [{ label: "Done", variant: "primary", onClick: closeModal }],
      onMount: function () {
        var button = $("#handoff-copy");
        if (button) button.addEventListener("click", async function () {
          try { await navigator.clipboard.writeText(url); toast("Handoff link copied."); }
          catch { toast("Clipboard access was denied. Select the link and copy it manually.", "danger"); }
        });
      },
    });
  }

  async function revokeAgentSession(sessionId) {
    var ok = await confirmAction({
      title: "Revoke temporary session",
      message: "The agent token stops working immediately. The agent must request a new handoff link. Upstream credentials are not changed.",
      confirmLabel: "Revoke session",
      variant: "danger",
      dangerZone: true,
    });
    if (!ok) return;
    await guarded(function () { return api("agent-access/sessions/" + sessionId + "/revoke", { method: "POST", body: { reason: "revoked in management panel" } }); });
    toast("Session revoked.");
    await loadRoute("access");
  }

  async function revokeAllSessions() {
    var ok = await confirmAction({
      title: "Revoke every temporary session",
      message: "All handoff links and session tokens stop working immediately. Administrator-managed agent identities are unaffected.",
      confirmLabel: "Revoke all",
      variant: "danger",
      requireText: "REVOKE ALL",
      dangerZone: true,
    });
    if (!ok) return;
    var result = await guarded(function () {
      return api("agent-access/revoke-all", { method: "POST", body: { confirm: "REVOKE ALL", reason: "revoked in management panel" } });
    });
    toast("Revoked " + (result ? result.revoked : 0) + " session(s).");
    await loadRoute("access");
  }

  async function openSessionActivity(sessionId) {
    var data = await guarded(function () { return api("agent-access/sessions/" + sessionId + "/activity?limit=40"); });
    if (!data) return;
    var items = data.items || [];
    openModal({
      title: "Session activity",
      body: items.length
        ? '<div class="detail-list">' + items.map(function (item) {
            return '<div class="detail-row"><dt>' + esc(fmtDate(item.ts)) + "</dt><dd><span class=\"mono\">" + esc(item.tool) +
              "</span> · " + esc(item.decision) + " · " + esc(item.status) + (item.reason ? " · " + esc(item.reason) : "") + "</dd></div>";
          }).join("") + "</div>"
        : emptyState("No activity yet", "Requests made with this session will appear here.", "audit"),
      actions: [{ label: "Close", variant: "primary", onClick: closeModal }],
    });
  }

  async function saveAgentAccessSettings() {
    var defaultMinutes = parseInt($("#access-default-ttl").value, 10);
    var maxHours = parseInt($("#access-max-ttl").value, 10);
    var baseUrl = $("#access-base-url").value.trim();
    var body = {
      default_ttl_seconds: defaultMinutes * 60,
      max_ttl_seconds: maxHours * 3600,
      revoke_on_restart: $("#access-restart-revoke").checked,
      agent_api_base_url: baseUrl,
    };
    await withBusy($("#access-save-settings"), function () {
      return guarded(async function () {
        await api("agent-access/settings", { method: "PUT", body: body });
        toast("Agent access settings saved.");
        await loadRoute("access");
      });
    }).catch(function (error) {
      toast(errorText(error), "danger");
    });
  }

  /* -------------------------------------------------------------- updates */

  async function viewUpdates() {
    var data = await api("releases");
    var cur = data.current_release;
    var releases = data.releases || [];
    var notice = data.post_update_notice;
    var links = data.links || {};
    var bannerHtml = "";
    if (notice && notice.show) {
      var highlights = (notice.highlights||[]).map(function(h){ return '<li>' + esc(h) + '</li>'; }).join("");
      var migrations = (notice.migration_notes||[]).map(function(m){ return '<li>' + esc(m) + '</li>'; }).join("");
      bannerHtml = '<div class="update-banner" role="status"><h3>' + icon("refresh",18) + ' What\'s New in v' + esc(notice.version) + (notice.previous_version ? ' (from v' + esc(notice.previous_version) + ')' : "") + '</h3>' + (notice.date ? '<p class="card-hint">Released ' + esc(notice.date) + '</p>' : "") + (highlights ? '<ul class="release-bullets">' + highlights + '</ul>' : "") + (migrations ? '<div class="notice mt-2"><div><strong>Migration Notes</strong><ul class="release-bullets">' + migrations + '</ul></div></div>' : "") + (notice.breaking ? '<div class="notice mt-2" data-tone="danger"><div><strong>Breaking Changes</strong> Review the changelog before upgrading dependent agents.</div></div>' : "") + '<div class="button-row mt-3"><button type="button" class="button" data-variant="primary" id="ack-release">' + icon("check",14) + '<span>Dismiss notice</span></button><a class="button" data-variant="ghost" href="' + esc(links.changelog||"") + '" target="_blank" rel="noreferrer">' + icon("audit",14) + '<span>Open CHANGELOG.md</span></a></div></div>';
    }
    var curBlock = "";
    if (cur) curBlock = '<div class="notice" data-tone="ok"><div><strong>Current: v' + esc(cur.version) + (cur.date ? ' — ' + esc(cur.date) : "") + '</strong>' + releaseSectionBlocks(cur.sections) + '</div></div>';
    var releasesHtml = releases.map(function(rel){
      var summary = (rel.highlights && rel.highlights.length ? esc(rel.highlights[0].slice(0,80)) : "");
      return '<details class="list-item" ' + (rel.version===data.installed_version?"open":"") + '><summary class="list-item-top" data-role="summary-action"><span class="list-item-title">v' + esc(rel.version) + (rel.date?' — ' + esc(rel.date):"") + ' ' + (rel.is_current?badge("current","ok"):"") + '</span><span class="list-item-meta">' + (rel.breaking?badge("breaking","danger"):"") + summary + '</span></summary>' + releaseSectionBlocks(rel.sections) + '<div class="button-row mt-2"><a class="button" data-size="sm" href="' + esc(rel.release_url||"") + '" target="_blank" rel="noreferrer">View release</a></div></details>';
    }).join("");
    var linksHtml = Object.entries(links).map(function(kv){ return '<div class="detail-row"><dt>' + esc(kv[0]) + '</dt><dd><a class="mono" href="' + esc(kv[1]) + '" target="_blank" rel="noreferrer">' + esc(kv[1]) + '</a></dd></div>'; }).join("");
    $("#section").innerHTML = bannerHtml + '<div class="grid grid-2"><section class="card glass"><div class="card-header"><h3>Installed version</h3>' + (data.update_available ? badge("update available","warn") : badge("up to date","ok")) + '</div><p class="metric">v' + esc(data.installed_version) + '</p><p class="card-hint">Installed ' + (data.installed_at ? esc(fmtDate(data.installed_at)) : "unknown") + ' · schema v' + esc(String(data.schema && data.schema.version || "—")) + '</p><dl class="detail-list mt-3"><div class="detail-row"><dt>Previous version</dt><dd>' + (data.previous_version ? 'v' + esc(data.previous_version) : "—") + '</dd></div><div class="detail-row"><dt>Latest known</dt><dd>v' + esc(data.latest_version) + ' · ' + esc(data.check_status||"local_only") + '</dd></div><div class="detail-row"><dt>Checked</dt><dd>' + (data.checked_at ? esc(fmtDate(data.checked_at)) : "never") + ' · ' + esc(data.check_message||"") + '</dd></div></dl><div class="button-row mt-3"><button type="button" class="button" data-variant="primary" id="check-updates">' + icon("refresh",14) + '<span>Check for updates</span></button><a class="button" href="' + esc(links.releases||"") + '" target="_blank" rel="noreferrer">GitHub Releases</a><a class="button" href="' + esc(links.repository||"") + '" target="_blank" rel="noreferrer">Repository</a></div></section><section class="card glass"><h3>Repository links</h3><div class="list mt-3">' + linksHtml + '</div><p class="field-hint mt-3">Release checks contact GitHub only when you click “Check for updates” and are cached for 1 hour.</p></section></div><section class="card glass mt-4" aria-labelledby="history-title"><div class="card-header"><h3 id="history-title">Release history</h3><span class="card-hint">local CHANGELOG.md · structured Keep-a-Changelog</span></div>' + curBlock + '<div class="release-stack mt-4">' + releasesHtml + '</div></section>';

    var checkBtn = document.getElementById("check-updates");
    if (checkBtn) checkBtn.addEventListener("click", function(e){ withBusy(e.currentTarget, function(){ return guarded(async function(){ var fresh=await api("releases/check",{method:"POST", body:{}}); toast(fresh.update_available ? 'Update available: v' + fresh.latest_version : "You are on the latest release."); await navigate("updates"); }); }); });
    var ackBtn = document.getElementById("ack-release");
    if (ackBtn) ackBtn.addEventListener("click", function(){ guarded(async function(){ await api("releases/acknowledge",{method:"POST", body:{}}); toast("Post-update notice dismissed."); await navigate("updates"); }); });
  }

  async function saveLimits(form, limits) {
    clearErrors(form);
    const values = {
      audit_retention_days: Number($("#set-retention").value),
      approval_ttl_seconds: Number($("#set-ttl").value),
      max_operation_timeout_seconds: Number($("#set-timeout").value),
      max_output_bytes: Number($("#set-output").value),
    };
    const fieldIds = {
      audit_retention_days: "set-retention",
      approval_ttl_seconds: "set-ttl",
      max_operation_timeout_seconds: "set-timeout",
      max_output_bytes: "set-output",
    };
    const errors = [];
    Object.entries(values).forEach(([key, value]) => {
      const [min, max] = limits[key] || [0, Number.MAX_SAFE_INTEGER];
      if (!Number.isFinite(value) || value < min || value > max) {
        errors.push({ fieldId: fieldIds[key], message: `Enter a value between ${min} and ${max}.` });
      }
    });
    if (errors.length) {
      showErrors(form, "Limits were not saved.", errors);
      return;
    }
    await withBusy($("#save-limits"), () =>
      guarded(async () => {
        await api("settings", { method: "PUT", body: values });
        toast("Limits saved.");
        await navigate("settings");
      })
    ).catch((error) => showErrors(form, "Limits were not saved.", [{ message: errorText(error) }]));
  }

  async function changePassword(form) {
    clearErrors(form);
    const current = $("#pw-current").value;
    const next = $("#pw-new").value;
    const errors = [];
    if (!current) errors.push({ fieldId: "pw-current", message: "Enter the current password." });
    if (next.length < 12) errors.push({ fieldId: "pw-new", message: "Use at least 12 characters." });
    if (errors.length) {
      showErrors(form, "The password was not changed.", errors);
      return;
    }
    await withBusy($("#change-password"), () =>
      guarded(async () => {
        await api("vault/password", {
          method: "POST",
          body: { current_password: current, new_password: next },
        });
        form.reset();
        toast("Master password changed.");
      })
    ).catch((error) =>
      showErrors(form, "The password was not changed.", [{ fieldId: "pw-current", message: errorText(error) }])
    );
  }

  async function rotateKey(form) {
    clearErrors(form);
    const password = $("#rotate-password").value;
    if (!password) {
      showErrors(form, "The data key was not rotated.", [
        { fieldId: "rotate-password", message: "Enter the master password." },
      ]);
      return;
    }
    const confirmed = await confirmAction({
      title: "Rotate the data encryption key?",
      message: "Every credential is re-encrypted with a new key. Take an encrypted export first.",
      confirmLabel: "Rotate key",
      variant: "danger",
      requireText: "ROTATE",
    });
    if (!confirmed) return;
    await withBusy($("#rotate-key"), () =>
      guarded(async () => {
        await api("vault/rotate-key", { method: "POST", body: { password } });
        form.reset();
        toast("Data encryption key rotated.");
      })
    ).catch((error) =>
      showErrors(form, "The data key was not rotated.", [{ fieldId: "rotate-password", message: errorText(error) }])
    );
  }

  async function toggleAutoUnlock() {
    const enabling = !state.status.vault.auto_unlock_enabled;
    if (enabling) {
      const confirmed = await confirmAction({
        title: "Enable auto-unlock?",
        message:
          "The unlock key will be stored beside the vault in /data. If an attacker obtains both files, the secrets can be decrypted without the master password.",
        confirmLabel: "Enable auto-unlock",
        variant: "danger",
      });
      if (!confirmed) return;
    }
    await guarded(async () => {
      await api("vault/auto-unlock", { method: "POST", body: { enabled: enabling } });
      toast(enabling ? "Auto-unlock enabled." : "Auto-unlock disabled.");
      await refreshStatus();
      await navigate("settings");
    });
  }

  async function exportVault() {
    await guarded(async () => {
      const response = await fetch("api/admin/vault/export", {
        headers: { "X-Gateway-Request": "1" },
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new ApiError(payload?.error?.message || "Export failed.", {
          status: response.status,
          code: payload?.error?.code,
        });
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "agent-gateway-vault-export.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast("Encrypted export downloaded. Store it away from the master password.");
    });
  }

  function importVault() {
    openModal({
      title: "Restore an encrypted vault",
      body: `
        <div class="notice" data-tone="danger">
          ${icon("alert", 18)}
          <div><strong>This replaces the current vault</strong>The existing vault file is overwritten and left locked.
            Export the current vault first if you might need it.</div>
        </div>
        <form class="form" id="import-form" novalidate>
          <div class="field">
            <label for="import-file">Encrypted export (.json)</label>
            <input type="file" id="import-file" accept="application/json,.json">
          </div>
          <div class="field">
            <label for="import-password">Master password of the imported vault</label>
            <input type="password" id="import-password" autocomplete="current-password">
          </div>
        </form>`,
      actions: [
        { label: "Cancel", variant: "ghost", onClick: closeModal },
        {
          label: "Restore and lock",
          variant: "danger",
          onClick: (button) =>
            withBusy(button, () =>
              guarded(async () => {
                const form = $("#import-form");
                clearErrors(form);
                const file = $("#import-file").files[0];
                const password = $("#import-password").value;
                const errors = [];
                if (!file) errors.push({ fieldId: "import-file", message: "Choose an export file." });
                if (file && file.size > 5_000_000) errors.push({ fieldId: "import-file", message: "File is too large." });
                if (!password) errors.push({ fieldId: "import-password", message: "Enter the password." });
                if (errors.length) {
                  showErrors(form, "The vault was not restored.", errors);
                  return;
                }
                const bytes = new Uint8Array(await file.arrayBuffer());
                let binary = "";
                for (let index = 0; index < bytes.length; index += 8192) {
                  binary += String.fromCharCode.apply(null, bytes.subarray(index, index + 8192));
                }
                try {
                  await api("vault/import", {
                    method: "POST",
                    body: { vault_base64: btoa(binary), password },
                  });
                } catch (error) {
                  showErrors(form, "The vault was not restored.", [
                    { fieldId: "import-password", message: errorText(error) },
                  ]);
                  return;
                }
                closeModal();
                toast("Vault restored and locked. Unlock it with its master password.");
                await boot();
              })
            ),
        },
      ],
      onMount: () => $("#import-file")?.focus(),
    });
  }

  /* ------------------------------------------------------------ boot & auth */

  function authShell(inner) {
    return `
      <div class="auth-wrap">
        <main class="card glass auth-card" id="main-content" tabindex="-1">
          <div class="brand">
            <span class="brand-mark">${icon("shield", 20)}</span>
            <span>
              <span class="brand-name">Agent Gateway</span>
              <span class="brand-meta">Home Assistant add-on</span>
            </span>
          </div>
          ${inner}
        </main>
      </div>`;
  }

  function renderSetup() {
    $("#app").innerHTML = authShell(`
      <div>
        <h1>Create the credential vault</h1>
        <p class="auth-lede">Secrets for Home Assistant, Proxmox VE, and SSH are encrypted with a password only you know.</p>
      </div>
      <div class="notice">
        ${icon("lock", 18)}
        <div><strong>Choose a password you can recover</strong>
          It is never stored. Losing it means the vault cannot be decrypted. Keep it in a password manager.</div>
      </div>
      <form class="form" id="setup-form" novalidate>
        <div class="field">
          <label for="setup-password">Master password</label>
          <input type="password" id="setup-password" autocomplete="new-password" required
                 aria-describedby="setup-hint">
          <p class="field-hint" id="setup-hint">Minimum 12 characters; leading and trailing spaces are rejected.</p>
        </div>
        <div class="field">
          <label for="setup-confirm">Confirm master password</label>
          <input type="password" id="setup-confirm" autocomplete="new-password" required>
        </div>
        <div class="button-row">
          <button type="submit" class="button" data-variant="primary" id="create-vault">
            ${icon("lock", 16)}<span>Create vault</span>
          </button>
        </div>
        <p class="field-hint">Argon2id key derivation · AES-256-GCM · per-record nonces and authenticated metadata.</p>
      </form>`);
    const form = $("#setup-form");
    attachBlurValidation(form);
    $("#setup-password").focus();
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      setupVault(form);
    });
  }

  async function setupVault(form) {
    clearErrors(form);
    const password = $("#setup-password").value;
    const confirm = $("#setup-confirm").value;
    const errors = [];
    if (password.length < 12) errors.push({ fieldId: "setup-password", message: "Use at least 12 characters." });
    if (password !== confirm) errors.push({ fieldId: "setup-confirm", message: "The passwords do not match." });
    if (errors.length) {
      showErrors(form, "The vault was not created.", errors);
      return;
    }
    await withBusy($("#create-vault"), () =>
      guarded(async () => {
        await api("vault/initialize", { method: "POST", body: { password } });
        toast("Vault created and unlocked.");
        await boot();
      })
    ).catch((error) => showErrors(form, "The vault was not created.", [{ message: errorText(error) }]));
  }

  function renderUnlock() {
    const vault = state.status.vault;
    $("#app").innerHTML = authShell(`
      <div>
        <h1>Unlock the vault</h1>
        <p class="auth-lede">The credential vault is locked. Enter the master password to manage connections.</p>
      </div>
      ${
        vault.auto_unlock_enabled
          ? `<div class="notice" data-tone="danger">${icon("alert", 18)}<div><strong>Auto-unlock failed</strong>
               The unlock key is ${vault.auto_unlock_key_present ? "not valid for this vault" : "missing"}.
               Enter the master password.</div></div>`
          : ""
      }
      <form class="form" id="unlock-form" novalidate>
        <div class="field">
          <label for="unlock-password">Master password</label>
          <input type="password" id="unlock-password" autocomplete="current-password" required>
        </div>
        <div class="button-row">
          <button type="submit" class="button" data-variant="primary" id="unlock-vault">
            ${icon("unlock", 16)}<span>Unlock vault</span>
          </button>
        </div>
        <p class="field-hint">The vault stays unlocked in memory until you lock it or the add-on restarts.</p>
      </form>`);
    const form = $("#unlock-form");
    attachBlurValidation(form);
    $("#unlock-password").focus();
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      unlockVault(form);
    });
  }

  async function unlockVault(form) {
    clearErrors(form);
    const password = $("#unlock-password").value;
    if (!password) {
      showErrors(form, "Enter the master password to continue.", [
        { fieldId: "unlock-password", message: "The master password is required." },
      ]);
      return;
    }
    await withBusy($("#unlock-vault"), () =>
      guarded(async () => {
        try {
          await api("vault/unlock", { method: "POST", body: { password } });
        } catch (error) {
          const message =
            error instanceof ApiError && error.code === "vault_unlock_failed"
              ? "That password is not correct."
              : errorText(error);
          showErrors(form, "The vault was not unlocked.", [{ fieldId: "unlock-password", message }]);
          return;
        }
        await boot();
      })
    );
  }

  function renderFatal(error) {
    const denied = error instanceof ApiError && (error.status === 403 || error.status === 401);
    $("#app").innerHTML = authShell(`
      <div>
        <h1>${denied ? "Management access is restricted" : "The gateway is not reachable"}</h1>
        <p class="auth-lede">${esc(errorText(error))}</p>
      </div>
      <div class="notice" data-tone="danger">
        ${icon("alert", 18)}
        <div><strong>${denied ? "Open the panel from Home Assistant" : "Check the add-on"}</strong>
          The management API is only reachable through Home Assistant Ingress, and only the Supervisor
          proxy may connect to it.</div>
      </div>
      <div class="button-row">
        <button type="button" class="button" data-variant="primary" id="retry-boot">
          ${icon("refresh", 16)}<span>Try again</span>
        </button>
      </div>`);
    $("#retry-boot").addEventListener("click", () => boot());
  }

  async function boot() {
    if (state.refreshTimer) {
      clearInterval(state.refreshTimer);
      state.refreshTimer = null;
    }
    let status;
    try {
      status = await api("status");
    } catch (error) {
      renderFatal(error);
      return;
    }
    state.status = status;
    if (!status.vault.exists) {
      renderSetup();
      return;
    }
    if (!status.vault.unlocked) {
      renderUnlock();
      return;
    }
    if (!NAV.some(([id]) => id === state.route)) state.route = "dashboard";
    initTheme();
    mountShell();
    await loadRoute(state.route, { focus: false });
  }

  /* ---------------------------------------------------------------- wiring */

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.modalOpen) {
      event.preventDefault();
      closeModal();
    }
    trapFocus(event);
  });

  $("#modal-close").addEventListener("click", () => closeModal());

  $("#modal").addEventListener("click", (event) => {
    if (event.target === $("#modal")) closeModal();
  });

  boot();
})();
