/* Agent Gateway — session handoff page.
 *
 * Deliberately dependency-free and network-minimal:
 *  - the handoff code lives in the URL fragment, which browsers never send to
 *    the server, to a referrer, or into an access log;
 *  - the fragment is stripped from the address bar before the redeem request;
 *  - the returned token is kept only in this module's memory. No cookies,
 *    localStorage, sessionStorage, or IndexedDB are used. A reload loses it.
 */
(function () {
  "use strict";

  var state = { token: null, session: null, base: window.location.origin };

  function $(id) {
    return document.getElementById(id);
  }

  function setHidden(id, hidden) {
    var node = $(id);
    if (node) node.hidden = hidden;
  }

  function announce(text) {
    var node = $("announcer");
    if (node) node.textContent = text;
  }

  function status(title, detail, tone) {
    $("status-title").textContent = title;
    $("status-detail").textContent = detail;
    $("status-card").dataset.tone = tone || "neutral";
    setHidden("status-card", false);
  }

  function formatSeconds(total) {
    if (typeof total !== "number" || !isFinite(total)) return "—";
    if (total <= 0) return "expired";
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var seconds = Math.floor(total % 60);
    if (hours) return hours + "h " + minutes + "m";
    if (minutes) return minutes + "m " + seconds + "s";
    return seconds + "s";
  }

  function formatTime(value) {
    if (!value) return "—";
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function extractCode(input) {
    var value = String(input || "").trim();
    if (!value) return "";
    var index = value.indexOf("code=");
    if (index >= 0) {
      value = value.slice(index + 5).split(/[&#]/)[0];
    }
    try {
      return decodeURIComponent(value);
    } catch (error) {
      return "";
    }
  }

  function jsonConfig() {
    return JSON.stringify(
      {
        gateway_base_url: state.base,
        api_base: state.base + "/api/v1",
        mcp_url: state.base + "/mcp",
        mcp_transport: "streamable-http",
        authorization_header: "Bearer " + (state.token || "<session token>"),
        session_id: state.session ? state.session.id : undefined,
        expires_at: state.session ? state.session.expires_at : undefined,
      },
      null,
      2
    );
  }

  function renderSession(session) {
    state.session = session;
    $("fact-name").textContent = session.name || session.id || "—";
    $("fact-expires").textContent = formatTime(session.expires_at);
    $("fact-remaining").textContent = formatSeconds(session.remaining_seconds);
    $("fact-classes").textContent = (session.operation_classes || []).join(", ") || "—";
    $("fact-scopes").textContent = (session.scopes || []).join(", ") || "—";
    var connections = session.connections || [];
    $("fact-connections").textContent = connections.length
      ? connections
          .map(function (item) {
            return item.name + (item.type ? " (" + item.type + ")" : "");
          })
          .join(", ")
      : "none";
    var badge = $("session-badge");
    badge.textContent = session.status || "active";
    badge.dataset.tone = session.status === "active" ? "ok" : "warn";
    setHidden("session-card", false);
  }

  function renderToken(payload) {
    state.token = payload.token;
    state.base = window.location.origin;
    $("token-value").textContent = payload.token;
    $("fact-base").textContent = state.base;
    $("fact-api").textContent = state.base + "/api/v1";
    $("fact-mcp").textContent = state.base + "/mcp";
    $("curl-example").textContent =
      'curl -sS \\\n  -H "Authorization: Bearer $HAG_AGENT_TOKEN" \\\n  ' +
      state.base +
      "/api/v1/health";
    setHidden("token-card", false);
    if (payload.session) renderSession(payload.session);
    status(
      "Credential ready",
      "Copy the token now. It cannot be retrieved again from this handoff link.",
      "ok"
    );
    announce("Session credential ready. Copy it now.");
  }

  function copyText(text, label) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () {
          announce(label + " copied to the clipboard.");
        },
        function () {
          announce("Copy failed. Select the text and copy it manually.");
        }
      );
      return;
    }
    announce("Clipboard access is unavailable in this browser. Select the text and copy it manually.");
  }

  function redeem(code) {
    status("Redeeming handoff…", "This link can only be used once.", "neutral");
    return fetch("/agent/api/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "omit",
      cache: "no-store",
      body: JSON.stringify({ code: code }),
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, status: response.status, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          var message =
            (result.body && result.body.error && result.body.error.message) ||
            "The handoff link is invalid, expired, or already used.";
          status("Handoff unavailable", message + " Ask the administrator for a new link.", "warn");
          setHidden("manual-card", false);
          return;
        }
        renderToken(result.body);
      })
      .catch(function () {
        status(
          "Cannot reach the gateway",
          "The handoff link could not be redeemed. Check the URL and network access, then retry.",
          "warn"
        );
        setHidden("manual-card", false);
      });
  }

  function checkStatus() {
    if (!state.token) return;
    fetch("/agent/api/session", {
      headers: { Authorization: "Bearer " + state.token },
      credentials: "omit",
      cache: "no-store",
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          var message =
            (result.body && result.body.error && result.body.error.message) ||
            "This session is no longer valid.";
          status("Session not active", message, "warn");
          return;
        }
        renderSession(result.body.session || result.body);
        status("Session status refreshed", "Values reflect the gateway's current state.", "ok");
      })
      .catch(function () {
        status("Status unavailable", "The gateway did not respond to the status check.", "warn");
      });
  }

  function init() {
    var code = extractCode(window.location.hash.replace(/^#/, ""));
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
    }

    $("copy-token").addEventListener("click", function () {
      copyText(state.token, "Session token");
    });
    $("copy-json").addEventListener("click", function () {
      copyText(jsonConfig(), "Connection JSON");
    });
    $("check-status").addEventListener("click", checkStatus);

    $("manual-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var candidate = extractCode($("manual-code").value);
      if (!candidate) {
        status("Handoff code missing", "Paste the complete handoff link.", "warn");
        return;
      }
      $("manual-code").value = "";
      redeem(candidate);
    });

    if (!code) {
      status(
        "No handoff code",
        "Open the link your gateway administrator generated. Links are single-use and expire quickly.",
        "warn"
      );
      setHidden("manual-card", false);
      return;
    }
    redeem(code);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
