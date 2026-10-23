# Work log — chronological history

> Append-only. Newest entries at the top. Each entry states what changed,
> how it was verified (exact commands/results), and what was **not** run.
> Runtime detail lives in the gateway Project Journal; this file is the
> repository-side mirror (`AGENTS.md` §2).

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
