# Work log — chronological history

> Append-only. Newest entries at the top. Each entry states what changed,
> how it was verified (exact commands/results), and what was **not** run.
> Runtime detail lives in the gateway Project Journal; this file is the
> repository-side mirror (`AGENTS.md` §2).

## 2026-10-24 — Prompt #2 Phase 2: startup/lifecycle/health audit

- Re-audited Dockerfile/base image/S6 `run.sh`/AppArmor/signals/shutdown
  budget (see checklist Phase 2 rows for the full mapping). No speculative
  permission changes: the `/init` failure chain is documented as fixed in
  0.4.0 with build-time contract checks and static AppArmor tests; not
  reproducible locally (no Docker/AppArmor daemon).
- **Fixed:** port-conflict startup reporting. Live probe showed uvicorn
  dumping a raw multi-exception traceback (bind OSError → SystemExit →
  lifespan CancelledError) despite exiting 3 correctly. Added
  `_preflight_ports()` in `__main__.py` (bind probe before starting servers):
  single actionable error line, same exit code 3, clean shutdown, no
  traceback. Regression tests `test_preflight_*` added; live re-probe
  confirms `Traceback in output: False`, `exit: 3`.
- **Live probes:** occupied-port → exit 3 clean; `/healthz` stays 200 while
  `/readyz` flips 503 under emergency disable with structured checks; status/
  health bodies secret-scanned → no password/token/key shapes; readiness
  keys are `checks/ready/status/uptime_seconds/version`.
- **Documented as unverifiable:** the container restart seen in the supplied
  (not present in this session) logs — only watchdog/HEALTHCHECK mechanisms
  are known; no cause attributed. `tls=False` is the documented correct value
  without cert options (Supervisor terminates Ingress TLS).
- Verification: `pytest -q` **358 passed** (2 new), startup smoke PASS,
  mypy strict PASS, Ruff/format PASS.

**Not run:** Docker/AppArmor image build locally (CI-gated), live HA OS boot.

## 2026-10-24 — Prompt #2 Phase 0/1: baseline + changelog dismissal verification and residual fix

**Phase 0 (baseline):** branch `arena/d6e20eec-ha-mcp` @ `7d0de10`, clean tree,
version 0.4.2, no open PR → opened **PR #11** (single PR for this task) with the
baseline comment. Shallow clone (1 commit) recorded as a history limitation.
Baseline: `pytest -q` **356 passed**; Ruff/format/mypy strict pass; five
jsdom harnesses pass; `tools/startup-smoke/smoke.py` PASS.

**Phase 1 (changelog dismissal):**
- Live reproduction against a real gateway process (temp data dir, real HTTP
  on 18199/18198): fresh boot notice `show=true` → acknowledge 200 → status
  hides it → **SIGTERM + restart keeps it hidden** → no-CSRF 403 → no
  Ingress-user 401. The headline pre-0.4.2 bug is already fixed by PR #9 in
  this build; backend persistence, scope, and version-transition logic are
  correct.
- Residual defect reproduced: both dismiss handlers awaited `refreshStatus()`
  inside `guarded()` with no fallback, so a status-refresh failure **after a
  successful acknowledgement** kept the banner visible and skipped navigation.
  Evidence: `tools/ui-smoke/changelog_smoke.mjs` new S4 scenario against the
  pre-fix `app.js` from `HEAD` → **exit 1**; fixed `app.js` → **exit 0**.
- Fix: `applyAcknowledgement()` mirrors the acknowledge response (server
  truth) into the render snapshot; follow-up status refresh is best-effort
  (`try/catch`); then re-render. No CSS hiding, no removed controls.
- Verification: changelog harness **18/18 checks**; all five harnesses PASS;
  `pytest -q` **356 passed**; `node --check` PASS; Ruff/format PASS.

**Not run (honest gaps):** real browser/keyboard/touch verification inside HA
Ingress (no browser/Ingress in sandbox) — jsdom-level only; no live HA OS.

## 2026-10-24 — Guided agent setup, AI handoff prompt, and SSH username UX

**Agent/session:** Arena coding agent, branch `arena/98324188-ha-mcp`, based on
merged PR #9 / `520c5cb`.

**Request and inspection.** The user asked for agent setup that requires no
protocol/policy knowledge and produces a prompt the AI can continue from. They
also could not find an SSH username when storing a key. Inspection showed the
backend already requires `SSHConfig.username` and the Connections form already
submits it; the UI called it only "Dedicated account". No credential schema or
migration was needed.

**Implemented.**

- Agent Access now opens with **Connect an AI agent**. It links to the existing
  Credentials/Connections forms, explains the one required Supervisor Network
  step, offers plain-language connection/read-control/lifetime choices, and
  derives the existing HA/Proxmox/SSH scopes without changing backend policy.
- A fresh install suggests the agent base address from the current HA hostname
  and listener TLS/port settings. Guided submission rejects URL userinfo,
  query/fragment and unknown paths, normalizes pasted `/agent`, `/mcp`, or
  `/api/v1`, saves changed settings through the existing endpoint, then issues
  through the existing single-use session endpoint.
- The handoff dialog provides an editable/copyable AI prompt containing the
  one-time handoff URL, MCP URL, health/tools checks, expiry, and a continuation
  instruction. It contains placeholders rather than a redeemed bearer token,
  is never sent automatically, and warns that the handoff code cannot be shown
  again. Manual link redemption remains available.
- Advanced individual-scope and access-settings forms remain in collapsed
  sections. Both guided and advanced flows now show the one-time handoff before
  refreshing the list, so a refresh failure cannot hide the issuance response.
- The SSH field is explicitly **SSH username** with `autocomplete="username"`;
  the key form explains that key and username are stored separately and points
  to the SSH connection. Documentation now describes username as non-secret
  connection metadata rather than vault secret material.

**Security/UX decisions.** Guided read/control adds `ha:write`/`ssh:write` only
for selected compatible connections; Proxmox remains read-only and every write
still requires approval. No auth, policy, TLS, host-key, or connector code was
changed. The prompt includes a live single-use handoff code by design, so every
surface warns to share it only with the intended agent before expiry.

**Verification (local).**

- `.venv/bin/pytest -q` → **356 passed in 22.00s**.
- `(cd tools/ui-smoke && npm test)` → all five harnesses passed. Agent Access
  has 30 checks covering guided read/write scope payloads, base-address
  normalization and persistence, prompt contents/copy, no redeemed bearer
  token, preserved advanced issuance/revocation, and fresh-install suggestion.
- `ruff check agent_gateway/src agent_gateway/tests`,
  `ruff format --check agent_gateway/src agent_gateway/tests` → clean (75 files).
- `(cd agent_gateway && ../.venv/bin/mypy src)` → clean (55 source files).
- JavaScript syntax checks for management, agent handoff, and the changed
  harness → clean. `git diff --check` → clean.

**Environment notes / not claimed.** The fresh branch initially lacked `.venv`
and `node_modules`; installing declared development dependencies resolved that
setup issue. No real browser, HA OS/Supervisor, third-party AI/MCP client, or
live SSH target was available, so visual layout, clipboard behavior in Ingress,
actual AI self-setup, and live SSH login remain unverified. A PR and its CI are
pending at this checkpoint; no version bump, merge, tag, release, or deployment
has occurred for this follow-up.

## 2026-10-24 — Evidence-driven audit, security hardening, and changelog dismissal (0.4.2)

**Agent/session:** Arena coding agent, branch `arena/a2524cd0-ha-mcp`.

**Baseline and scope.** Read `AGENTS.md`, `AGENT.md`, the current architecture,
packaging, tests, CI, and historical docs. Baseline `25c5c6b` / 0.4.1 was clean;
`pytest -q` before edits returned **329 passed**. PRs #1–#8 were merged, no
suitable open PR or existing release/tag was listed, and `origin/main` remained
at the baseline when rechecked. Existing UI polish and architecture were kept.

**Reproduced root causes and fixes.**

- **P0 changelog dismissal:** both views rendered from cached
  `state.status.release.post_update_notice`; `POST releases/acknowledge`
  persisted without refreshing it. Both native buttons now refresh status,
  expose busy state, and consume the already-toasted rejection from `guarded()`.
  Failed persistence leaves the notice visible; a new version is not hidden.
  The final consistency sweep also replaced obsolete dotted IDs in the agent
  form's no-status scope fallback and added a static regression.
- **HA target authorization:** policy extraction ignored service-data and
  nested/broad targets. An out-of-policy nested lock target returned `202`
  before the fix. Top-level/nested/list targets are validated; area, device,
  floor, label, mixed, and untargeted calls carry a broad sentinel that cannot
  bypass specific allows or denies.
- **Execution revalidation:** a fresh principal was fetched but the stale one
  still governed scope/policy/context. In-flight scope removal and a newly
  required read approval both reached execution before the fix; both now deny
  before any upstream request.
- **Proxmox:** `kind=lxc` status was labelled `vms`; it now uses `containers`.
- **Temporary access:** global `audit:read` is persistent-agent-only at both
  issuance and the audit endpoint, and the temporary connection catalog is
  filtered to authorized IDs. Persistent-agent behavior is unchanged.
- **Vault:** synthetic v0 migration unlocked once but failed the next unlock
  because the DEK wrapper retained legacy AAD. Migration now re-wraps with v1
  AAD; a lock/unlock-again regression passes.
- **Persistence permissions:** a direct mode probe showed `gateway.db-wal` and
  `gateway.db-shm` as `0644`. Private-umask initialization plus explicit repair
  now makes the main DB and sidecars `0600`.
- **HTTP:** an upstream error body was fully read before being discarded. It is
  no longer consumed; a fail-on-read stream regression proves status mapping
  still works. Successful bodies remain streamed and capped.
- **SSH:** a short chunked read was treated as EOF (`ab` instead of `abcde`).
  The bounded reader now loops to EOF/overflow and drains truncated stderr.
- **Lifecycle:** an unexpected normal listener exit logged an error but returned
  success. It now raises/fails nonzero; occupied-port startup returned 3 and
  runtime shutdown still ran.

**Verification (local, exact results).**

- `.venv/bin/pytest -q` → **354 passed in 22.89s**.
- `(cd agent_gateway && ruff check src tests && ruff format --check src tests)`
  → clean; 75 files formatted. `mypy src` (strict config) → clean, 55 files.
- `(cd tools/ui-smoke && npm test)` → all five harnesses passed: panel,
  paths/errors, Agent Access, agent handoff, and changelog; 14/14 dismissal
  checks; no captured runtime errors. The CI UI job now installs the locked
  jsdom dependencies and runs these same five harnesses on every PR.
- `.venv/bin/python tools/startup-smoke/smoke.py --python ... --pythonpath ...
  --cycles 2` → PASS for both listeners, health/readiness, schema,
  Ingress/CSRF/auth gates, two SIGTERM/restart cycles, clean exit 0.
- `pip wheel --no-deps --no-build-isolation ./agent_gateway` → PASS after
  installing the missing `wheel` build tool in the development venv.
- Final `git diff --check`, version/changelog comparison, YAML parsing, and
  added-line secret/artifact scan → clean; 46 changed files, no runtime state
  or generated wheel/build metadata left in Git.

**Documentation.** Added `docs/AUDIT_CHECKLIST.md`; synchronized both
changelogs and all version files to 0.4.2; corrected stale API/scope/tool names,
hard-deny lists, file permissions, migration notes, and local-start commands in
README/DOCS/security/recovery/access docs; refreshed project state and next
steps. Changelog acknowledgement is documented as installation-scoped.

**Failed approaches.** The first jsdom reproduction reused the panel's cached
API object and appeared to pass before the fix; fresh deep-copy responses were
required. A failed acknowledgement was not fully handled merely because
`guarded()` showed a toast—it rethrows, so terminal rejection handling was also
required. The first wheel attempt failed only because `bdist_wheel` was absent.

**Not run / not claimed.** No local Docker daemon or AppArmor tools, HA
OS/Supervisor, live HA/Proxmox/SSH target, third-party MCP client, real browser,
or independent security audit. Container build/startup and required PR checks
remain pending at this checkpoint; merge/release has not occurred.

## 2026-10-23 — Fix Ingress panel `IndexError: 4` on `/api/admin/status` inside container (0.4.1)

**Agent/session:** Arena coding agent, branch `arena/91b312ba-ha-mcp`.

**Context (real HA OS feedback on 0.4.0):**
- User's Home Assistant OS log confirmed 0.4.0's S6-Overlay AppArmor rules
  succeeded (`legacy-services successfully started`, `gateway started
  version=0.4.0 ingress_port=8099 agent_port=8098`, `/healthz -> 200`, and
  Ingress `GET /`, `/static/styles.css`, `/static/app.js` → `200`; earlier
  `can't open '/init'` lines at the top of the log were retained journald
  history from pre-0.4.0 boots).
- Opening the Ingress panel immediately failed on `GET /api/admin/status` with
  `ERROR gateway.api.errors unhandled request error type=IndexError
  path=/api/admin/status` (`[exception IndexError: 4]`), causing the UI to
  render *"The gateway is not reachable / the request could not be completed"*.

**Root cause & changes:**
- Inside the container `run.sh` sets `PYTHONPATH=/app/src`, so
  `gateway.releases.__file__` resolves to `/app/src/gateway/releases.py`, whose
  `Path.parents` sequence has length 4 (`0..3`). `find_changelog_path()`
  indexed `here.parents[4]` directly (which worked on deeper host/CI checkout
  paths with ≥5 parents, and was never hit by `docker-smoke.sh` because it only
  probed `/healthz` and `/readyz`).
- `agent_gateway/src/gateway/releases.py`: replaced fixed `here.parents[4]` /
  `here.parents[3]` indexing with `itertools.islice(here.parents, 5)` (skipping
  filesystem root) and `OSError` guards around `Path.is_file()`.
- `agent_gateway/src/gateway/api/middleware.py`: `AdminGateMiddleware` now
  accepts `Origin` matching `X-Forwarded-Host` (set by Home Assistant Core's
  Ingress proxy behind reverse proxies / Cloudflare Tunnel / Nabu Casa) in
  addition to `Host`, and normalizes default `:80`/`:443` ports while keeping
  the Supervisor peer IP gate, `X-Remote-User-Id`, `X-Gateway-Request: 1`, and
  `Sec-Fetch-Site` enforcement intact.
- `agent_gateway/src/gateway/ui/static/app.js`: `renderFatal()` now
  distinguishes HTTP 5xx internal errors (with `request_id` and add-on log
  pointer) from 401/403 Ingress restrictions and network unreachability.
- `agent_gateway/Dockerfile`, `agent_gateway/apparmor.txt`,
  `tools/startup-smoke/docker-smoke.sh`: added `/app/ r,` and `/opt/venv/ r,`,
  build-time `PYTHONPATH=/app/src` `find_changelog_path()` + `Runtime.status()`
  verification, and in-container Ingress management endpoint probes.
- Synchronized version `0.4.0` → `0.4.1` across all 6 sources (`CHANGELOG.md`
  and `agent_gateway/CHANGELOG.md` verified byte-identical in
  `tests/test_packaging.py`).

**Verified (sandbox: Python 3.11.2, Node 22.22.3):**
- Direct reproduction before fix (`__file__ = "/app/src/gateway/releases.py"`) →
  `IndexError(4)`; after fix → `200 OK` on `/api/admin/status`,
  `/api/admin/releases`, `/api/admin/diagnostics` across `/app/src/gateway/releases.py`,
  `/opt/gateway/releases.py`, and `/releases.py`.
- `.venv/bin/ruff check agent_gateway/src agent_gateway/tests` → clean.
- `.venv/bin/ruff format --check agent_gateway/src agent_gateway/tests` → 73 files formatted.
- `(cd agent_gateway && ../.venv/bin/mypy src)` → clean (55 files, `--strict`).
- `.venv/bin/pytest -q` → **329 passed**.
- `node --check` on `app.js` and `agent.js` → clean; `bash -n` on `run.sh` and
  `docker-smoke.sh` → clean.
- `(cd tools/ui-smoke && npm test)` → all 4 jsdom harnesses PASS (including new
  HTTP 500 boot error + retry recovery assertions).
- `.venv/bin/python tools/startup-smoke/smoke.py` → PASS (2 start/stop cycles).

**Not run:** Docker build / `docker-smoke.sh` (no Docker daemon in sandbox;
runs in GitHub Actions `image` job) and live Home Assistant OS 0.4.1 upgrade.

## 2026-10-23 — S6 startup root cause + temporary agent access broker (0.4.0)

**Agent/session:** Arena coding agent, branch `arena/09933ac1-ha-mcp` (PR #7).

**Startup.** Chain: Supervisor → `/init` → `s6-overlay-suexec` → `preinit` /
`stage0` → `rc.init` → `/run.sh`. Every S6 entry is a `#!/bin/sh` script, so
`/bin/sh` must **read** it for the kernel's shebang execution; the custom
profile replaced the Supervisor default without covering the whole chain,
producing `can't open …: Permission denied` and the downstream
`s6-overlay-suexec … exit code 2`. The profile now grants `rix` to `/init`,
`/package/**`, `/command/**`, `/etc/s6-overlay/**`, the cont-init/services
trees and `/run/s6`; `mr` for libs/venv; writes only under `/data`, `/tmp`,
`/run`, `/dev`. The Dockerfile fails the build if the chain is unreadable, and
`run.sh` keeps the root-prepare → `su-exec app:app` handoff.

**Broker.** Schema v4 (additive `agents` columns + digest-only
`session_handoffs`). Issue → single-use handoff (`hag_h_…`, URL fragment
only) → `/agent` redeem → `hag_` session token (SHA-256 at rest) →
REST/MCP through the same policy path. Sessions carry connection allowlists,
read/write classes (destructive rejected), generated policy and expiry; the
restart policy revokes temporary sessions, expires approvals, purges handoffs
and bumps a persisted epoch without touching upstream secrets. New 12th panel
section **Agent Access**.

**Verified:** `pytest -q` → 323 passed; `ruff check` clean; `ruff format
--check` 73 files; `mypy src` success; `tools/startup-smoke/smoke.py` PASS
(2 cycles, SIGTERM exit 0, restart); four jsdom harnesses via `npm test`
exit 0; `node --check` on both JS bundles.

**Not run:** Docker/OCI image build, real AppArmor enforcement, HA OS install,
live HA/Proxmox/SSH, real browser. The root cause is statically inferred from
the pinned s6-overlay 3.2.3.2 and Supervisor sources; there was no local
reproduction. `tools/startup-smoke/docker-smoke.sh` is the gate on a
Docker-capable host.

## 2026-10-22 — `/init` AppArmor read permission follow-up (0.3.1)

**Agent/session:** Arena coding agent, branch `arena/100263ca-ha-mcp`.

**What changed**
- Follow-up to the user's report that version 0.3.0 still logs
  `/bin/sh: can't open '/init': Permission denied`: changed AppArmor rule from
  `/init ix` to `/init rix`. The shell error indicates `/bin/sh` is trying to
  read `/init` as a script; execute-only permission is insufficient for that
  launch path. Kept `init: false` and the HA base image unchanged.
- Updated the packaging regression test and troubleshooting docs; bumped all
  add-on/runtime version declarations from 0.3.0 to 0.3.1 so Supervisor can
  offer the corrected package.

**Verified**
- `python -m compileall -q agent_gateway/tests/test_packaging.py` → passed.
- Version-sync and AppArmor-source assertion script → passed (all four runtime
  version declarations are 0.3.1; `/init rix` is present in profile + test).
- `git diff --check` → passed.
- `pytest -q agent_gateway/tests/test_packaging.py` → not run: `pytest` is not
  installed in this sandbox (`/bin/bash: pytest: command not found`).
- Live add-on/HA OS startup → not run; no Supervisor or Docker daemon is
  available. The fix still needs confirmation on the user's Home Assistant.

## 2026-10-22 — Persistent Project Journal & session handoffs (0.3.0)

**Agent/session:** Arena coding agent, branch `arena/8c62810b-ha-mcp`,
feature commit `4af61b0` on `main`@`6857797`.

**What changed**
- Schema v3: `journal_projects/sessions/tasks/task_dependencies/entries/
  decisions/issues/test_runs/handoffs` in `storage/db.py` (auto-migration
  adds tables only; `_ensure_v3_tables` guard fails closed on partial DBs).
- `gateway/journal/` (`service.py`, `models.py`, `handoff.py`,
  `mcp_tools.py`): immutable redacted records, verification levels,
  idempotency keys, task transitions + dependency/cycle rules,
  completion-requires-evidence, owner-scoped writes, optimistic-concurrency
  project state, merged timeline with category/search/commit/date filters,
  bounded handoff generation. `authorize_agent_journal` = scope → central
  `PolicyService` (`journal.*`, read-risk; `require_approval` denies) →
  rate limit (rule honored, ≤120/min) → audit.
- APIs: `api/journal.py` (agent `/api/v1/journal/*`, admin
  `/api/admin/journal/*`); MCP `journal.*` tools in `mcp/server.py` via the
  same authorization function; scopes `journal:read`/`journal:write`;
  templates `journal_read`/`journal_readwrite`.
- UI: 11th panel section **Project Journal** (state overview + editor,
  counts, sessions, next steps, record/task forms, filterable timeline,
  handoff generate/preview/edit/copy) reusing the existing design system;
  policy-editor journal template buttons; jsdom smoke stubs/checks.
- Docs/instructions: `AGENTS.md` (new; `AGENT.md` preserved as historical
  prompt), `CLAUDE.md`, `docs/JOURNAL.md`, this file set; README/CHANGELOGs
  (×2, identical), `DOCS.md`, `docs/SECURITY.md`, `docs/DEVELOPMENT.md`,
  `docs/IMPLEMENTATION_AUDIT.md`, design-system master; version 0.2.1→0.3.0
  synced in all six locations.

**Verified (exact results, sandbox: Python 3.11.2, Node 22)**
- Baseline on `6857797`: `pytest -q` → **211 passed**.
- After changes: `pytest -q` → **253 passed** (37 new in
  `tests/test_journal.py`, 5 new in `tests/test_ui_assets.py`);
  `ruff check` clean; `ruff format --check` clean (68 files);
  `(cd agent_gateway && mypy src)` strict clean (52 files);
  `node --check app.js` ok.
- `(cd tools/ui-smoke && npm install && npm test)` → all checks ok,
  including journal render/next-step/timeline/badge/handoff-generate;
  "No runtime errors captured" (both harnesses).
- Socket-level E2E (throwaway `HAG_DATA_DIR=/tmp/gw-journal-demo`,
  `HAG_REQUIRE_INGRESS_USER=0`, ports 18098/18099): created agent with
  journal scopes+policy; session start was idempotent; dependency blocked
  task start (`409`); claim-only completion rejected (`422`); test run with
  a planted `password=` probe stored redacted; task completed with
  `automated_tests`; handoff (2 927 chars) contained focus-task acceptance
  criteria, blocked work, failed-approach lesson, test evidence,
  untrusted-data warning. **Process restarted** on the same data dir:
  `schema_version=3`, all rows intact (1 session, 2 tasks, 1 issue,
  1 decision, 1 test run, 1 handoff), 0 rows containing the planted probe;
  a *different* read-only agent re-read state + handoff; admin timeline
  returned the 3 activity records. Panel HTML/JS served with
  `viewJournal` present.

**Failed approaches / lessons**
- Batched parallel edits to the same file raced in the tooling (only the
  last edit survived → 55 test failures from missing imports/schema bump).
  Recovered by auditing every batch with `grep` and switching to single
  asserted patch scripts; serialize same-file edits permanently.
- `mypy --strict` invariance on `list[str | None]` in timeline filtering —
  fixed by constructing via `append`/`extend`.

**Not run (honest gaps)**
- Live Home Assistant OS install/upgrade, real HA/Proxmox/SSH targets,
  third-party MCP client, real browser/screen reader, Docker image run,
  security audit — no such infrastructure in the sandbox
  (`blocked_infrastructure`; see `docs/NEXT_STEPS.md` P0/P1).

## Pre-journal history (summarized from `CHANGELOG.md`; not re-verified this session beyond the current test suite)

- **0.2.1 (2026-10-22)** — Fixed add-on startup failure
  `/bin/sh: can't open '/init': Permission denied` (custom AppArmor profile
  lacked S6-Overlay `/init` execute rules); added concurrent-approval and
  expired-token regression tests; `docs/IMPLEMENTATION_AUDIT.md` and
  Class 1–5 operating boundaries. Live HA OS confirmation still pending.
- **0.2.0 (2026-10-21)** — 10-section Ingress UI redesign (themes, Agents
  section, Policy Simulator, connection diagnostics, releases/update
  checks, readiness + redacted diagnostics, security warnings); schema
  v1→v2; failed-auth audit visibility; version-sync enforcement.
- **0.1.0 (2026-10-20)** — Initial add-on: two-listener architecture,
  AES-256-GCM/Argon2id vault, deny-by-default policy engine, single-use
  approvals, HA/Proxmox/SSH connectors (12 tools), REST + MCP over one
  operation path, redacted audit log, packaging for amd64/aarch64.
