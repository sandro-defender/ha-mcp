# Audit checklist — Prompt #2 session (target 0.5.0, 2026-10-24)

Requirement-level checklist for the complete audit/implementation pass
(changelog fix verification, lifecycle audit, automatic Supervisor shutdown,
IP access management, Ingress trust boundary, full feature audit, security
verification, documentation, release). Every row uses **PASS**, **FIXED**,
**PARTIAL**, **MISSING**, **BLOCKED**, or **NOT TESTED**, with evidence and a
verification method. `PASS` requires recorded evidence; mocked evidence and
jsdom evidence are labelled as such; live-infrastructure claims are only made
when the operation actually ran in an authorized environment. Update this file
after every phase.

## Phase 0 — repository discovery and baseline (2026-10-24)

| Item | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Branch and working tree | P0 | `arena/d6e20eec-ha-mcp` at `7d0de10` (merge of PR #10), clean tree; all work stays on this branch | PASS |
| Current PR state | P0 | `gh pr list --state all`: PRs #1–#10 all merged, **no open PR** → exactly one new PR will be opened for this task (no duplicates) | PASS |
| Version and release state | P0 | 0.4.2 synchronized across `config.yaml`, `pyproject.toml`, `__init__.py`, `Dockerfile`; `git tag --list` shows no releases | PASS |
| CI/release workflows | P0 | `.github/workflows/ci.yml` (Python+jsdom+packaging+image), `release.yml`, `addon-pin-check.yml` inspected | PASS |
| Repository instructions | P0 | `AGENTS.md` (authoritative), `AGENT.md`, `CLAUDE.md`, `docs/PROJECT_STATE.md`, `docs/NEXT_STEPS.md`, `docs/WORKLOG.md` read | PASS |
| Prior specifications | P0 | Git history is a **shallow clone (single commit `7d0de10`)** — pre-merge commit history and PR conversation bodies beyond the repo docs cannot be retrieved locally; requirements reconstructed from `README.md`, `AGENT.md`, `docs/*`, and merged-PR titles via `gh`. Limitation recorded instead of inventing requirements | PARTIAL — documented limitation |
| Baseline test status | P0 | `.venv/bin/pytest -q` → **356 passed**; `ruff check` + `ruff format --check` → pass (75 files); `mypy src` strict → pass (55 files); `node --check app.js` → pass | PASS |
| Baseline UI smoke | P0 | `tools/ui-smoke`: all five harnesses pass (`changelog_smoke`, `panel_smoke`, `panel_paths`, `access_smoke`, `agent_page_smoke`) | PASS |
| Baseline process lifecycle | P0 | `.venv/bin/python tools/startup-smoke/smoke.py` → PASS (two cycles, both listeners, Ingress/CSRF gates, SIGTERM, clean exit 0) | PASS |
| UI-polish preservation (Prompt #1) | P0 | No design-system changes made in this phase; `design-system/` untouched | PASS |
| Architecture map | P0 | Add-on config/Dockerfile/S6 `run.sh`/AppArmor; two ASGI listeners via `api/apps.py`; `AdminGateMiddleware` (Ingress trust) vs `AgentAuthMiddleware` (bearer); `Runtime` wires vault/policy/approvals/audit/journal/agent-access; SQLite schema v4 (`storage/db.py`); UI `ui/static` 12-section panel; tests `tests/*` + `tools/ui-smoke` + `tools/startup-smoke` | PASS |

## Phase 1 — changelog dismissal (2026-10-24)

| Requirement | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Reproduce the reported dismissal failure | P0 | Live reproduction against a real gateway process (temp `/data`, real HTTP): fresh boot `post_update_notice.show=true` → `POST /api/admin/releases/acknowledge` 200 → status hides notice → process restart keeps it hidden → no-CSRF 403 → no-Ingress-user 401. The main-path defect reported from the pre-0.4.2 era is **not present** in this build (fixed by merged PR #9). A residual defect WAS reproduced: with a successful acknowledge followed by a **failed status refresh**, the dashboard banner stayed visible (`tools/ui-smoke/changelog_smoke.mjs` S4 against `HEAD`'s `app.js` → exit 1) | FIXED |
| Root cause traced (UI → API → persistence) | P0 | UI renders the notice from the boot-time `state.status` snapshot (`app.js` dashboard `renderDashboard`, Updates `viewUpdates`); `POST /api/admin/releases/acknowledge` → `ReleaseService.acknowledge_version` persists installation-scoped `seen_version` in SQLite `settings`; `show = seen_version != __version__`. Residual root cause: both dismiss handlers `await refreshStatus()` *inside* `guarded()` with no fallback — a refresh failure after successful persistence kept the stale snapshot on screen and skipped navigation | FIXED |
| Fix through frontend/backend/persistence layers | P0 | Handlers now apply the acknowledge response (authoritative `post_update_notice`) to the status snapshot via `applyAcknowledgement()`, wrap the follow-up `refreshStatus()` in best-effort `try/catch`, then re-render; backend persistence unchanged (already correct); no CSS hiding, no control removal | FIXED |
| Dismiss control works desktop/mobile, keyboard activation | P0 | Native `<button>` elements (`#dismiss-banner`, `#ack-release`) → Enter/Space dispatch `click`; `withBusy` preserves a single activation; verified at jsdom level only | PARTIAL — jsdom-verified; real browser BLOCKED (no browser/Ingress in sandbox) |
| Disappears immediately after successful dismissal | P0 | jsdom S1/S2 (immediate hide + no reappear on revisit) and new S4 (status refresh failure path) | FIXED at jsdom level |
| Persists across reloads/restart | P0 | `test_acknowledge_endpoint_persists_and_updates_status`, `test_acknowledgement_survives_runtime_restart`, plus the live restart reproduction above | FIXED |
| New release can still be announced | P0 | `test_releases.py` version-transition test (old-version ack cannot dismiss new release) + jsdom S2 (genuinely new release reappears) | FIXED |
| Failed persistence handled honestly | P0 | jsdom S3: failed acknowledge → notice stays visible, server state unchanged, no unhandled rejection; 401/`ingress_required` path shows "Management session lost" and keeps the notice | FIXED |
| Correct storage scope | P1 | Installation-scoped `seen_version` in the add-on's SQLite settings (shared by administrators of one add-on), documented in `README.md`; not represented as per-user state | PASS |
| Release history reopen path | P1 | Updates & Changelog section keeps full release history; dashboard "View changelog" routes there; jsdom S1/S2 navigation checks | PASS |
| Real Ingress browser confirmation | P1 | Requires HA Ingress + real browser | BLOCKED — infrastructure |
| Regression tests | P0 | New jsdom scenario S4 (fails on pre-fix `app.js` exit 1, passes post-fix exit 18/18 checks); full suites re-run: `pytest -q` **356 passed**, all five `tools/ui-smoke` harnesses PASS, `node --check` PASS, Ruff PASS | FIXED |

## Phase 2 — startup, lifecycle, and runtime health (2026-10-24)

| Requirement | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Dockerfile/base/S6/entrypoint/permissions/users/signals audit | P0 | `ghcr.io/home-assistant/base:3.24`; `CMD ["/run.sh"]` (HA image owns `/init`); root→`su-exec app:app` drop; `S6_KILL_GRACETIME=10000`, `S6_CMD_RECEIVE_SIGNALS` deliberately unused (busybox `kill --` defect, measured on built image); SIGTERM/SIGINT → stop event → `timeout_graceful_shutdown=5` drain → `runtime.shutdown()` (vault lock + DB close); `test_startup.py`, `test_packaging.py`, startup smoke | PASS at source/process level |
| `/init` permission error reproducible? | P0 | Fixed in 0.4.0 (AppArmor `r`+`x` on the S6 chain; `docs/STARTUP_TROUBLESHOOTING.md`); build-time contract (`test -x /init && test -r /init` + preinit/stage0), `test_apparmor_permits_s6_init_entrypoint`, live HA OS confirmation recorded for 0.4.0. Not reproducible here (no Docker/AppArmor); no speculative permission changes made | PASS at source/static level; live AppArmor CI-gated |
| Observed container restart from supplied logs | P1 | No supplied logs exist in this session; repo contains no evidence of the restart's cause. Known restart mechanisms: Supervisor `watchdog` (`/healthz`) and image `HEALTHCHECK` (retries=3) — either restarts the whole add-on on probe failure; S6 does not restart the CMD. Documented as unverified rather than speculated | PARTIAL — cannot be attributed without the original logs |
| Port conflict / startup failure reporting | P0 | Live probe: occupied ingress port previously exited 3 **with a raw uvicorn multi-exception traceback**; added `_preflight_ports()` → single actionable error (`startup failed: ingress listener port N is already in use`), same exit 3, clean shutdown, no traceback. Regression: `test_preflight_rejects_an_occupied_ingress_port`, `test_preflight_passes_when_ports_are_free`; live re-probe exit 3, `Traceback` absent | FIXED |
| Config/startup failures reported distinctly | P0 | `load_settings` ConfigError → exit 2; runtime startup failure → exit 1; listener/port failure → exit 3; all logged without traceback, `/data` closed (vault locked, `gateway stopped`) | PASS — live-verified for the port-conflict path |
| Unexpected listener exit is a failure | P0 | `tests/test_main.py::test_unexpected_listener_exit_is_a_failure` → RuntimeError; restart recovery covered by startup smoke (two cycles, same `/data`) | PASS |
| Both listeners and documented purposes | P0 | Ingress management `:8099` (AdminGate, panel, `/api/admin`), agent REST+MCP `:8098` (bearer auth, `/mcp`); smoke socket probes on 18099/18098; `config.yaml` publishes 8098 only when configured (`null` default) | PASS |
| Health vs readiness distinguish process from services | P0 | Live: `/healthz` stays `200 {"status":"ok"}` while `/readyz` flips to **503** under emergency disable, with structured `checks` (database/schema, vault, agent listener, ingress listener) | PASS — live-verified |
| Logs/health/status responses do not leak secrets | P0 | `logging_utils.RedactionFilter` (rewrites messages, sanitizes exception payloads, drops uvicorn access log, quiets httpx/asyncssh); live secret scan of `/api/admin/status`, `/healthz`, `/readyz` bodies → no token/password/key shapes; `test_redaction.py` | PASS — live-verified for status/health bodies |
| Graceful shutdown on SIGTERM | P0 | Startup smoke: SIGTERM handled, exit 0, no traceback; `test_shutdown_budget_is_configured`, `test_profile_leaves_signals_unmediated` | PASS — process level |
| Container build/boot | P0 | Docker daemon unavailable locally; CI amd64 image + docker-smoke job must cover | BLOCKED locally / CI-gated |

## Phase 3 — automatic add-on shutdown through Supervisor (2026-10-24)

| Requirement | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Supervisor mechanism verified against official docs | P0 | Home Assistant developer docs ("App communication", Supervisor API endpoints reference): `POST /addons/self/stop`, authenticated with `Authorization: Bearer $SUPERVISOR_TOKEN`; **self-service `/addons/self/*` endpoints work without `hassio_api` or a role** → no new add-on permission requested (`config.yaml` unchanged on permissions; security implication: none). `httpx` client honors `trust_env=False` (no proxy env leakage); URL overridable via `HAG_SUPERVISOR_URL`/`SUPERVISOR` for test rigs only | PASS — docs-verified; not live-verified |
| Settings UI (enable/timeout/manual stop/state/warnings) | P0 | Settings section card: enable checkbox, validated timeout input (range from server), save → `PUT /api/admin/lifecycle/settings`; manual "Shut down add-on now" → confirm dialog explaining UI loss + Home Assistant START instructions → `POST /api/admin/lifecycle/shutdown`; live status rows (state, Supervisor availability, next auto-stop, last activity, last shutdown reason/time/result), explicit boot/auto-start warning. jsdom: 11 new `panel_smoke` checks (render, warning text, range validation error, PUT recorded, confirm dialog content, POST recorded) | PASS at jsdom level; real browser BLOCKED |
| Inactivity definition and timer correctness | P0 | Policy in `lifecycle.py` docstring + tests: counted = requests passing the Ingress admin gate + requests with valid non-expired/non-revoked agent credentials; NOT counted = `/healthz`+`/readyz`, unauthenticated, failed/revoked/expired auth, static page loads, background work. Single evaluation task (`lifecycle_loop`, 10 s), one shutdown lock (no duplicate timers), in-flight counter defers ticks, monotonic clock. Tests: `test_inactivity_timer_expiry`, `test_tick_is_inert_when_disabled_or_in_flight`, `test_unauthenticated_and_health_traffic_does_not_reset_timer`, `test_expired_token_does_not_reset_timer`, `test_authenticated_admin_activity_counts`, `test_failed_shutdown_backoff_for_automatic_ticks` | FIXED/TESTED |
| Safe shutdown sequence | P0 | Order implemented and asserted: state flip (rejects new agent work) → bounded in-flight drain (5 s) → persist metadata → configured temporary-session revocation policy → sanitized audit event → Supervisor stop → honest outcome handling (accepted/unconfirmed/rejected/unavailable), failed attempts revert to running with backoff; accepted-but-not-stopped containers are reverted by a 30 s watchdog instead of leaving a zombie state. Tests: drain (`test_shutdown_drains_inflight_agent_requests`, `test_shutdown_proceeds_after_drain_deadline`), duplicate (`409`), revocation on/off, persistence across restart, backoff | TESTED (mocked Supervisor) |
| New agent sessions/operations rejected during shutdown | P0 | Agent middleware503 `shutting_down` before auth (covers `/mcp`, `/api/v1`, `/agent` page, redeem) with `Retry-After`; issuance endpoint raises `ShuttingDown` 503; `/readyz` flips 503 with `checks.lifecycle`, `/healthz` stays 200 | TESTED (ASGI + process level) |
| Restart/recovery documentation | P1 | UI hint + `DOCS.md`/`README` sections (Phase 8): restart via Home Assistant add-on START; `boot: auto` means a reboot can start it again before the idle timer stops it; no self-restart claim | PASS — docs rows confirmed in Phase 8 |
| Automated tests (mocked Supervisor) | P0 | `tests/test_lifecycle.py` **23 tests**: timer, activity policy, settings validation, endpoint/auth/outcome classification via respx on the real `supervisor_stop` caller, drain, duplicate, revocation, persistence, backoff, gates, token non-leakage (responses+audit+logs), API contract | PASS — all Supervisor interactions mocked |
| Process-level integration (real HTTP both sides) | P0 | Live sandbox run: real gateway process + local mock Supervisor HTTP server: `POST /addons/self/stop` received with `Authorization: Bearer <token>`, 202 `stopping`/`confirmed:true`, agent503/healthz200/readyz503, duplicate409, no token in responses or process logs, no traceback | PASS — process level, **Supervisor mocked** |
| Live Supervisor stop (real HA OS) | P1 | Requires the user's Home Assistant OS add-on environment | BLOCKED — infrastructure; mocked tests above are not a live claim |

## Phase 4 — IP allowlist/denylist and connection attempts (2026-10-24)

| Requirement | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Interface: rules, attempts, actions, search, bounds | P0 | New **Access Control** panel page (`viewIpAccess`): enforcement card (allowlist toggle, precedence/trusted-proxy/retention detail rows, admin-lockout note), rules table with Add/Edit(prefill)/Remove(confirm) flows, attempt history with decision filter, free-text search, pagination info, per-row agent **Revoke** (confirm → existing `agents/{id}/revoke`). API: `GET/POST /api/admin/ip/rules`, `PATCH/DELETE /api/admin/ip/rules/{id}`, `PUT /api/admin/ip/settings`, `GET /api/admin/ip/attempts` (limit ≤100, offset, decision/ip/q/listener filters). Bounds: newest **5000** rows (`prune_attempts` on insert-batch + every maintenance tick). jsdom: `panel_smoke` +19 checks (render, precedence text, toggle PUT, POST/PATCH/DELETE recorded, filter query, revoke confirm + POST, pagination info) — all 5 harnesses PASS | PASS — jsdom + API + process level; real-browser UX NOT TESTED (no browser in sandbox) |
| Backend enforcement + precedence + CIDR/IPv4/IPv6 | P0 | `IPAccessMiddleware` sits **outside** `AgentAuthMiddleware` (covers REST, `/mcp`, `/agent` page, redeem, session issuance) and before policy/approval logic; health/readiness exempt so probes work from any address. Evaluation: explicit **deny always beats allow**; allowlist mode (DB setting, default **off** → pre-feature behaviour unchanged) requires a matching allow rule; unidentifiable source fails closed. CIDR via `ipaddress` (`strict=False`, bare IPs → /32, /120-mapped v6 normalised to v4 prefix); `::ffff:10.0.0.5` hits `10.0.0.5/32` rules. Tests: deny-beats-allow, allowlist on/off, v6 ranges, mapped-v6, garbage source denied, deny-before-auth (403 `ip_denied` with **and without** a valid token — never 200/401), MCP+redeem alternate routes denied & recorded, healthz exempt, allowlisted IP still needs a valid credential (401 then 200) | PASS — 28 `test_ip_access.py` tests + process-level probe |
| Trusted proxies / spoofed-header handling | P0 | Source = **socket peer** by default; `X-Forwarded-For` honoured only when the peer is inside explicitly configured `trusted_proxies` (add-on option `trusted_proxies: []` + `HAG_TRUSTED_PROXIES` env override, each entry validated as IP/CIDR at load, `ConfigError` otherwise). Chain parsed right-to-left skipping trusted hops; all-trusted chain → left-most; multiple header lines concatenate; **malformed chain fails safe to the peer**. Tests: untrusted peer header ignored (spoofed allow-IP still 403 + row records the real peer), trusted-proxy enforcement with forwarded client recorded, chain variants, malformed fallback | PASS — unit + middleware tests; no live proxy in sandbox (Docker/HA/NAT implications documented in Phase 8) |
| Administrative protections + revocation + rate limits | P0 | Rule endpoints live only on the management app behind the full Ingress admin gate (unauthenticated → 401; absent from the agent listener → 404 with a valid agent token). Rule changes audit-logged (`ip.rules_create/update/delete`, `ip.settings_update`). Creating a **deny** rule auto-revokes temporary sessions seen from that range in the last 48 h (attribution via attempt history; response `revoked_sessions`; audit event) — token death verified via immediate 401 after redeem. Repeated denials from one source rate-limited 20/60 s → **429** `rate_limited` + `Retry-After`, recorded as `rate_limited`; authenticated-allowed rows sampled ≤1/min/source (bounded); auth failures recorded from the agent gate; blocked management attempts recorded (listener `management`). Design note: IP rules never apply to the management listener (Supervisor peer gate) → an admin cannot be locked out by a deny rule | PASS — API + middleware tests + process-level hammer (403→429, `rate_limited` rows observed) |
| History integrity: no secrets, sanitised fields | P0 | Attempts expose exactly `{ts, ip, listener, category, decision, reason, agent_id}`; reasons are static policy strings; tests assert no `token`/`password`/`secret` substrings; process-level scan of the live response found no bearer/`hag_` shapes | PASS |
| Tests + process-level integration | P0 | `tests/test_ip_access.py` **28 passed**; full suite **409 passed**; Ruff/format/mypy strict clean; startup smoke PASS. Process-level (real gateway process, real sockets): baseline agent 401 → deny rule created via management listener → agent REST+MCP **403 `ip_denied`**, healthz 200, management panel still 200, history rows `ip=127.0.0.1` with `matched deny rule 127.0.0.1/32`, hammer → 429 + `rate_limited` rows, no secrets, SIGTERM exit 0, no traceback | PASS — process level with local sockets only |
| Schema version bump | P1 | `SCHEMA_VERSION` 4 → **5**: `ip_rules` + `ip_attempts` tables (SCHEMA + `_ensure_v5_tables` fail-closed guard on fresh/upgrade/current paths); `test_database_schema_is_v5` asserts tables; v2→current upgrade test still passes | PASS |

## Phase 5 — redundant authorization inside trusted Ingress

| Requirement | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Management routes/endpoint audit | P0 | Pending | NOT TESTED |
| Ingress trust mechanism verification | P0 | Pending | NOT TESTED |
| Redundant in-app authorization removed (only if safe) | P0 | Pending | NOT TESTED |
| Direct-port/spoofing bypass regression tests | P0 | Pending | NOT TESTED |
| Trust model documented | P1 | Pending | NOT TESTED |

## Phase 6 — full product audit (6A–6K)

Tracked in rows added during the phase; each of 6A–6K receives a status,
evidence, and resolution or blocker.

## Phase 7 — security and regression verification

Test matrix rows are added when the suites run.

## Phase 8 — documentation and operational guide

Documentation rows are added when files are updated.

## Phase 9 — final audit, versioning, and release

Release rows are added at final verification.

---

## Archived — previous session checklist (0.4.2, PR #9/#10 era)

## Phase 0 — baseline and scope

| Item | Priority | File/test evidence | Classification |
| --- | --- | --- | --- |
| Branch, baseline, recent commits | P0 | `arena/a2524cd0-ha-mcp` at `25c5c6b`; clean baseline inspected before edits; `origin/main` remained at that commit when rechecked | PASS |
| Existing PR/release state | P0 | `gh pr list --state all`, `gh release list`, `git tag --list`: PRs #1–#8 merged, no open PR, no listed release/tag | PASS — a new PR is required, not a duplicate |
| Repository instructions/specifications | P0 | `AGENTS.md`, `AGENT.md`, `docs/PROJECT_STATE.md`, `docs/NEXT_STEPS.md`, `docs/WORKLOG.md` read; UI-polish architecture preserved | PASS |
| Version/listener architecture | P0 | Baseline 0.4.1; management `:8099`, agent REST+MCP `:8098`; release bumped to 0.4.2 after fixes | PASS |
| Unmodified baseline | P0 | `.venv/bin/pytest -q` before edits | PASS — **329 passed** |

## Phase 1 — changelog dismissal (P0)

| Requirement | Priority | File/test evidence | Classification |
| --- | --- | --- | --- |
| Reproduce the real dismissal failure | P0 | `tools/ui-smoke/changelog_smoke.mjs` against the real `app.js` with fresh API objects; pre-fix dashboard scenario exited 1 | FIXED |
| Root cause across UI/API/persistence | P0 | `ui/static/app.js`, `api/admin.py`, `releases.py`, SQLite `settings`: acknowledgement persisted but `state.status.release` stayed stale | FIXED |
| Mouse/touch/keyboard activation and immediate disappearance | P0 | Native `<button>` controls, `withBusy`, both click handlers; jsdom S1/S2 scenarios | FIXED at jsdom level; real browser is separately BLOCKED below |
| Dismissal survives reload/restart | P0 | `agent_gateway/tests/test_releases.py` acknowledgement persistence and new-`Runtime` coverage | FIXED |
| A new release is not hidden by an old acknowledgement | P0 | `test_releases.py` version transition; jsdom S2 | FIXED |
| Failed persistence remains visible and has no unhandled rejection | P0 | jsdom S3; handlers consume the error rethrown by `guarded()` after its toast | FIXED |
| Acknowledgement scope is intentional/documented | P1 | Installation-wide `seen_version`, shared by administrators of one add-on; documented in `README.md` | PASS — not represented as per-user state |
| Release history remains intentionally viewable | P1 | Updates navigation/history retained; panel and path smoke tests | PASS |
| Real Ingress browser confirmation | P1 | Requires HA Ingress and a real browser | BLOCKED — infrastructure |

## Phase 2 — startup, container lifecycle, and health

| Requirement | Priority | File/test evidence | Classification |
| --- | --- | --- | --- |
| Add-on metadata, Dockerfile, S6 chain, users, permissions, signals | P0 | `config.yaml`, `Dockerfile`, `run.sh`, `apparmor.txt`; `test_packaging.py`, `test_startup.py` | PASS at source/static level |
| Previous `/init`/S6 read+execute failure remains fixed | P0 | `test_apparmor_permits_s6_init_entrypoint`, startup contract assertions, `docs/STARTUP_TROUBLESHOOTING.md` | PASS at source/static level |
| Real AppArmor enforcement of the S6 chain | P0 | Requires Docker/AppArmor or HA OS | BLOCKED locally; CI/live gate |
| Clean startup, health, readiness, SIGTERM, restart | P0 | `tools/startup-smoke/smoke.py`: two cycles, both listeners, schema usable, Ingress/CSRF/auth gates, clean exit 0 | PASS — process level |
| Unexpected listener exit and startup failure status | P0 | `tests/test_main.py`; occupied-port subprocess probe returned 3; `__main__.py` now propagates nonzero and still shuts down | FIXED |
| Both listeners retain separate roles | P0 | Socket probes on `18099`/`18098`; `api/apps.py`, `settings.py` | PASS |
| Health versus readiness and secret-free responses | P0 | `test_startup.py`, process probes, `runtime.py`; no credentials in responses | PASS |
| Observed restart and `tls=False` on a live install | P1 | Local restart behavior is expected; `tls=False` is correct without both certificate options (`docs/NETWORKING.md`) | PARTIAL — a user-specific HA OS restart cause cannot be inferred locally |
| Python package build | P0 | `pip wheel --no-deps --no-build-isolation ./agent_gateway` | PASS — wheel built successfully |
| Container build/boot | P0 | Docker daemon unavailable locally | BLOCKED locally / NOT TESTED in CI at this checkpoint |

## Phase 3 — product requirements

| Area | Priority | End-to-end/file/test evidence | Classification |
| --- | --- | --- | --- |
| A. Ingress panel, navigation, forms, errors, accessibility | P0 | `test_ui_assets.py`, `test_admin_api.py`; five jsdom harnesses cover all 12 sections, keyboard dialog behavior, errors, access and handoff | PASS; obsolete fallback scope IDs FIXED with a static regression |
| A. Real browser, mobile, screen reader | P1 | Requires browser/HA Ingress | BLOCKED — infrastructure |
| B. Vault KDF/AEAD, lock/unlock, persistence, corruption, masking | P0 | `vault/crypto.py`, `vault/manager.py`, `test_vault.py` | PASS for existing controls |
| B. Legacy vault migration | P0 | Synthetic v0 fixture: first unlock succeeded, second failed before fix; `test_legacy_vault_can_be_unlocked_again_after_migration` now passes | FIXED |
| C. Temporary issue → handoff → scoped use → revoke/expire/restart | P0 | `test_agent_access.py`, `test_middleware.py`; concurrent redeem, expiry, stale epoch, revoke, restart-retain/revoke | PASS with new hardening below |
| C. Temporary global-audit and connection-discovery boundaries | P0 | Both defects reproduced; broker/API/catalog regressions in `test_agent_access.py` and `test_agent_api.py` | FIXED |
| C. Restart leaves upstream credentials usable and undisclosed | P0 | Restart test unlocks the vault and decrypts the synthetic upstream token after temporary-token revocation; issuance/redeem responses checked for sentinels | PASS — mocked/local only |
| D. One deny-by-default policy path for REST/MCP | P0 | `operations.py`, `policy/engine.py`, `test_policy.py`, `test_mcp.py`; unknown/disabled operations denied | PASS with target fixes below |
| D. HA nested/broad service target authorization | P0 | Out-of-policy nested target returned 202 before fix; `test_agent_api.py` target matrix and `test_policy.py` broad-deny test now deny before network | FIXED |
| D. Fresh scope/approval revalidation before execution | P0 | Both in-flight changes reproduced execution before fix; regressions in `test_agent_api.py` now deny before network | FIXED |
| E. Parameter-bound, single-use, expiring human approvals | P0 | `approvals/service.py`, `test_approvals.py`; exact request/policy hash, atomic consumption, replay/expiry tests | PASS; newly-required-approval race FIXED above |
| F. Home Assistant REST discovery/reads/writes/errors | P1 | `connectors/home_assistant.py`, `test_connectors.py`, `test_agent_api.py` with respx; bounded fields, hard-denied domains, approvals | FIXED for target enforcement; remaining paths PASS at mocked level |
| F. Home Assistant WebSocket | P2 | No WebSocket client or tool depends on one; documented REST-only limitation | MISSING — intentional, not claimed complete |
| F. Live Home Assistant integration | P1 | Requires an authorized lab target | BLOCKED — infrastructure |
| G. Proxmox token/TLS/read-only/scoped operations | P1 | `connectors/proxmox.py`, `test_connectors.py`; no mutation tools | PASS at mocked level |
| G. LXC policy target family | P0 | Node+container policy allowed VMID 102 before fix and attempted network; regression now denies before network | FIXED |
| G. Live Proxmox integration | P1 | Requires an authorized lab target | BLOCKED — infrastructure |
| H. Restricted SSH, host keys, argv policy, bounds, no root shell | P0 | `connectors/ssh.py`, `test_ssh_hostkeys.py`; trust replacement, unknown-host refusal, templates, paths | PASS at unit level |
| H. Chunked bounded output | P0 | Short-read regression returned only `ab` before fix; chunked EOF/overflow test now passes | FIXED |
| H. Live SSH and remote symlink/race behavior | P0 | Requires an isolated SSH lab | BLOCKED — no live claim |
| I. Shared HTTP/MCP protocol and policy parity | P0 | `test_mcp.py`, `test_agent_api.py`; auth, schemas, malformed input, shared operation/approval path | PASS at protocol-test level |
| I. HTTP error/success body bounds | P0 | Error stream that raises if consumed was read before fix; `test_http_error_response_body_is_not_consumed` now passes | FIXED for errors; success streaming remains capped |
| I. Windows/third-party MCP interoperability | P0 | PowerShell/client documentation in `README.md`, `DOCS.md`, `docs/NETWORKING.md` | BLOCKED — no Windows or third-party client available |
| J. Audit, diagnostics, retention, actionable safe errors | P1 | `audit/service.py`, `redaction.py`, `runtime.py`, `test_redaction.py`, admin/readiness tests | PASS at automated level |
| J. Global audit authorization | P0 | Legacy temporary principal returned 200 before fix; persistent-only endpoint regression now returns 403 | FIXED |

## Phase 4 — security/regression and verification matrix

| Test group | Priority | Exact evidence | Classification |
| --- | --- | --- | --- |
| Missing/invalid/expired/revoked tokens and cross-scope access | P0 | `test_agent_api.py`, `test_agent_access.py`, `test_middleware.py` | PASS |
| Vault lifecycle/corruption/no plaintext disclosure | P0 | `test_vault.py`, connector sentinel checks, restart decrypt check | PASS; legacy migration FIXED |
| HA/Proxmox/SSH failure and policy denial paths | P1 | respx/mock/unit tests; assertions that denied calls make no upstream request | PASS at mocked level |
| UI/API errors, permissions, forms, navigation, changelog | P0 | pytest plus all five configured jsdom suites; 14/14 dismissal checks | PASS at jsdom/ASGI level |
| jsdom behavior in pull-request CI | P0 | `.github/workflows/ci.yml` now runs `npm ci` and all five harnesses with `REPO=github.workspace` | NOT TESTED in CI at this checkpoint; local run PASS |
| Full Python regression | P0 | `.venv/bin/pytest -q` | PASS — **354 passed in 22.89s** |
| Ruff lint/format | P0 | `(cd agent_gateway && ruff check src tests && ruff format --check src tests)` | PASS — 75 files formatted |
| Strict type checking | P0 | `(cd agent_gateway && mypy src)` (`strict = true` in pyproject) | PASS — 55 source files |
| Local process lifecycle | P0 | startup smoke command with two cycles | PASS |
| Secret/diff scan | P0 | Added-line scan across 46 changed files; no private keys/token-shaped credentials or runtime state; `git diff --check` clean | PASS |
| Docker/AppArmor/aarch64 | P0/P1 | Not available locally; amd64 CI is required, aarch64 has no current build evidence | BLOCKED |

## Phase 5 — documentation and handoff

| Item | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Product/API/tool/scope names match implementation | P1 | `README.md`, `agent_gateway/DOCS.md` compared with `tools/registry.py` and `auth/agents.py` | FIXED — stale names/deny list/start command corrected |
| Agent access, global-audit boundary, discovery, restart behavior | P1 | `docs/AGENT_ACCESS.md`, `docs/SECURITY.md` | PASS |
| Vault recovery, file permissions, migration | P1 | `docs/VAULT_RECOVERY.md`, `docs/SECURITY.md` | PASS |
| Windows/network/TLS boundaries | P1 | `README.md`, `agent_gateway/DOCS.md`, `docs/NETWORKING.md` | PASS as documentation; live client remains BLOCKED |
| Changelog/version synchronization | P1 | 0.4.2 in config/pyproject/Dockerfile/`__init__`; both changelogs byte-identical | PASS |
| Project state, worklog, next-agent handoff | P1 | `docs/PROJECT_STATE.md`, `docs/WORKLOG.md`, `docs/NEXT_STEPS.md` | PARTIAL — final PR/CI/release results still to append |

## Phase 6 — release readiness

| Item | Priority | Evidence | Classification |
| --- | --- | --- | --- |
| Local implementation and automated checks complete | P0 | Results above | PASS |
| Required container CI and real AppArmor gate | P0 | Awaiting PR CI | NOT TESTED at this checkpoint; local execution BLOCKED |
| Complete diff/version/changelog review | P0 | Manual implementation/test/docs diff, 0.4.2 in all four sources, byte-identical changelogs, no build artifacts | PASS |
| Existing PR updated or one new PR opened | P0 | No suitable open PR exists | NOT TESTED — create exactly one from this branch |
| Merge/release | P0 | Must follow required green checks | BLOCKED until CI passes; no release has been made |

## Environment boundary

No live HA OS/Supervisor, live Home Assistant/Proxmox/SSH target, third-party
MCP client, real browser, Docker daemon, or local AppArmor tooling is available
in this sandbox. Those items must not be inferred from health checks, source
review, wheel builds, respx, or jsdom. CI container results and live-lab results
are recorded separately when they actually exist.
