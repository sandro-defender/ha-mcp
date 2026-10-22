# Project Journal — architecture, API, and operating guide

The Project Journal is the gateway's persistent work memory for AI coding
agents: sessions, tasks, work entries, technical decisions, issues (including
failed approaches), test evidence, and next-session handoffs. It lets a new
agent session retrieve trustworthy project context instead of restarting from
a blank prompt, and it keeps an unbroken distinction between what an agent
*claimed* and what is backed by recorded evidence.

The journal lives in three places that mirror each other for different
audiences:

| Layer | Where | Purpose |
| --- | --- | --- |
| Repository files | `AGENTS.md`, `docs/PROJECT_STATE.md`, `docs/WORKLOG.md`, `docs/NEXT_STEPS.md` | Human- and agent-readable state that travels with the code and is reviewable in PRs |
| Gateway database | `journal_*` tables in `/data/gateway.db` (schema v3) | Structured, queryable records written by agents/admins at runtime |
| Ingress panel | **Project Journal** section | Timeline, filters, next steps, handoff generation for administrators |

Repository files are curated summaries; the database is the detailed record.
Neither replaces `git` history, and neither grants any permission.

## Architecture

```
Agent (REST :8098 /api/v1/journal/*) ──┐
Agent (MCP  :8098 /mcp journal.*)      ├── scope → PolicyService (journal.*)
Admin (Ingress :8099 /api/admin/…) ────┘   → rate limit → JournalService
                                              → redaction → SQLite (WAL)
                                              → audit events
```

- `gateway/journal/service.py` — `JournalService` (all persistence, ownership,
  lifecycle, search/timeline) and `authorize_agent_journal` (the single
  agent-authorization path used by REST and MCP).
- `gateway/journal/models.py` — vocabulary (verification levels, statuses,
  transitions), size limits, and the Pydantic request models shared by all
  three surfaces (`extra="forbid"`; unknown fields are rejected).
- `gateway/journal/handoff.py` — bounded handoff prompt builder.
- `gateway/journal/mcp_tools.py` — MCP tool schemas and dispatch.
- `gateway/api/journal.py` — REST routers (`/api/v1/journal`,
  `/api/admin/journal`).
- UI: `viewJournal()` in `gateway/ui/static/app.js` (section 11 of the panel).

## Data model (schema v3)

All tables are created by the existing `CREATE TABLE IF NOT EXISTS` schema in
`storage/db.py`; upgrading from v1/v2 only adds tables and never touches
existing rows. Every record has a random 32-hex `id`, server timestamps, a
`project_id` (the seeded `default` project unless extended later), actor
attribution (`actor_type`/`actor_id`/`actor_name`, plus `source`:
`rest`/`mcp`/`admin`), and — where meaningful — `session_id`, `task_id`,
`commit_sha`, `branch`, and repository-relative `files` references.

| Table | Contents |
| --- | --- |
| `journal_projects` | The shared project-state document: name, purpose, architecture, conventions, constraints, branch, commit, optimistic-concurrency `revision`, last updater |
| `journal_sessions` | Agent/admin work sessions (`active` → `ended`) |
| `journal_tasks` | Tasks with priority (0 = highest), description, acceptance criteria, verification level, status, owner |
| `journal_task_dependencies` | `task_id → depends_on_task_id` edges (cycle-checked) |
| `journal_entries` | Work log: `kind` (milestone/progress/blocker/failure/note) × `outcome` (completed/failed/blocked/in_progress/info) |
| `journal_decisions` | Decision + rationale + alternatives (`accepted`/`superseded`/`rejected`) |
| `journal_issues` | `kind` bug/security/failed_approach/limitation, severity, `learned`, status open/resolved/wontfix |
| `journal_test_runs` | Command, status, exit code, environment, redacted output summary, client-reported `run_at`, server `created_at`, duration |
| `journal_handoffs` | Generated prompts with the record ids they were built from (`included`), project revision, branch/commit |

Integrity rules enforced by the service (not by clients):

- **Immutability.** Entries, decisions, issues, test runs, and handoffs cannot
  be edited or deleted through any API. Only status transitions exist: tasks
  (`todo → in_progress/blocked/cancelled`; `in_progress → blocked/completed/
  failed/cancelled`; `blocked → in_progress/todo/cancelled`; `failed →
  todo/in_progress/cancelled`; `completed`/`cancelled` terminal), issues
  (`open ↔ resolved/wontfix`), sessions (`active → ended`).
- **Completion requires evidence.** A task may only become `completed` with
  `verification_level` `automated_tests` or `integration_verified`.
- **Dependencies gate progress.** A task with unfinished dependencies (not
  `completed`/`cancelled`) cannot move to `in_progress` or `completed`;
  dependency cycles are rejected.
- **Ownership.** Reads are shared within the project (handoffs require it);
  writes to a task/issue/session are restricted to its owning agent or an
  administrator. Referencing another agent's session is rejected. The shared
  project document is writable by any authorized writer but protected by
  `expected_revision` optimistic concurrency (stale writes get `409`).
- **Idempotency.** Creates accept an `idempotency_key` (8–128 chars). A retry
  by the same actor with the same key returns the original record with
  `idempotent_replay: true` (unique index per actor).
- **Redaction & bounds.** Every text field passes through
  `gateway.redaction.redact_text` before storage; lengths are capped
  (title 200, body 8 000, command 500, output summary 4 000, ≤50 file refs,
  paths must be repository-relative without `..`). HTTP bodies are capped by
  the existing `BodyLimitMiddleware` (agent app: 2 MiB → `413`).

### Verification levels

`claimed` (agent assertion), `automated_tests`, `integration_verified`,
`not_tested`, `blocked_infrastructure`. Levels are stored verbatim and never
upgraded implicitly. A test run recorded with `claimed` is displayed as a
claim even if its status says `passed` — the gateway did not run it and will
not pretend otherwise. Responses include a derived `verified` boolean
(true only for the two evidence levels).

## Authentication and authorization

- **Agents** use the existing `hag_…` bearer token (`AgentAuthMiddleware`,
  including the emergency kill-switch). Two new scopes gate the journal:
  `journal:read` and `journal:write`.
- **Central policy.** Each journal operation is evaluated by the same
  `PolicyService` as connector tools, using a `journal.*` pseudo-tool and
  `connection_id = None` (rules must therefore use `connections: ["*"]`).
  Starter templates `journal_read` and `journal_readwrite` are available in
  Permissions. Journal records cannot execute anything, so they are evaluated
  at read risk; a rule that sets `require_approval: true` for a journal tool
  **denies** the call (fail closed) with an explanatory error. The matching
  rule's `rate_limit_per_minute` is honored, capped at 120/min per
  agent+tool.
- **Admins** use the existing Ingress gate (Supervisor peer + user header +
  `X-Gateway-Request: 1`). Admin writes bypass per-agent ownership (human
  override) and are attributed to the Home Assistant user.
- **Audit.** Every agent journal write and every denial/rate-limit emits an
  audit event (`tool = journal.*`); admin writes emit `journal.*` admin
  actions. Read successes are not audited (they would drown the log);
  handoff generation is a write and is always audited.
- A journal record is data, never authority: nothing in the journal can
  approve an operation, alter a policy, or unlock the vault.

## API reference (agent, `/api/v1/journal`)

All list endpoints accept `limit` (1–200, default 50) and `offset`, return
`{items, total, limit, offset}`, and accept `project_id` (default `default`).

| Method & path | Scope | Policy tool | Notes |
| --- | --- | --- | --- |
| `GET /project-state` | read | `journal.read_project_state` | Composed state: project doc, counts, next/blocked/completed tasks, open issues, recent decisions/tests/entries, active sessions |
| `PUT /project-state` | write | `journal.update_project_state` | Requires `expected_revision`; `409` on stale write |
| `GET /stats` | read | `journal.read_project_state` | Counts only |
| `POST /sessions`, `POST /sessions/{id}/end`, `GET /sessions` | write/write/read | `journal.start_session` / `end_session` / `list_sessions` | End is owner-only (admin override on admin API) |
| `GET /tasks`, `GET /tasks/{id}` | read | `journal.list_tasks` | Filters: `status`, `q`, `commit_sha`; order: priority, newest |
| `POST /tasks`, `PATCH /tasks/{id}` | write | `journal.record_task` / `update_task` | Transitions, evidence rule, dependency rules enforced |
| `GET /timeline` | read | `journal.list_entries` | Merged entries+decisions+issues+test runs. Filters: `record_type`, `category` (`completed`/`failed`/`blocked`/`unverified`), `verification_level`, `task_id`, `session_id`, `commit_sha`, `q`, `since`, `until`. Merged window: newest 200 per type (use per-type lists for deep history) |
| `GET /entries`, `POST /entries` | read/write | `journal.list_entries` / `record_work` | |
| `POST /decisions` | write | `journal.record_decision` | List via timeline (`record_type=decision`) |
| `POST /issues`, `PATCH /issues/{id}` | write | `journal.record_issue` / `update_issue` | |
| `GET /test-runs`, `POST /test-runs` | read/write | `journal.list_test_runs` / `record_test_run` | Filters: `task_id`, `status`, `commit_sha` |
| `POST /handoffs/generate`, `GET /handoffs`, `GET /handoffs/{id}` | write/read/read | `journal.generate_handoff` / `journal.list_entries` | List view omits the prompt body |

The admin API mirrors these under `/api/admin/journal/*` (plus `GET
/issues`), without scopes/policy (Ingress gate instead).

### MCP tools

Agents with journal scopes see `journal.*` tools in `tools/list`:
`journal.read_project_state`, `journal.list_tasks`, `journal.list_entries`,
`journal.start_session`, `journal.end_session`, `journal.record_task`,
`journal.update_task`, `journal.record_work`, `journal.record_decision`,
`journal.record_issue`, `journal.record_test_run`,
`journal.update_project_state`, `journal.generate_handoff`. They take no
`connection_id`, validate with the same request models, and pass the same
authorization function. `journal.list_test_runs` and `journal.list_sessions`
are REST-only in this release (the timeline covers test runs).

### Example (REST)

```bash
TOKEN="hag_REPLACE_WITH_TOKEN"; BASE="http://ha.lan:8098/api/v1/journal"
curl -H "Authorization: Bearer $TOKEN" "$BASE/project-state"
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"Journal session","idempotency_key":"sess-2026-10-22-a1"}' \
  "$BASE/sessions"
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"command":"pytest -q","status":"passed","exit_code":0,
       "verification_level":"automated_tests","environment":"py3.11 sandbox",
       "output_summary":"253 passed","idempotency_key":"run-2026-10-22-01"}' \
  "$BASE/test-runs"
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' "$BASE/handoffs/generate"
```

Granting access: create/extend an agent with `journal:read` (+
`journal:write`) scopes and apply the `journal_read` / `journal_readwrite`
policy template in **Permissions**. Deny-by-default still applies: scope
without a rule, or a rule without the scope, is denied.

## Handoff generation

`build_handoff_prompt` assembles a bounded (≤16 000 chars) Markdown prompt
from *current stored records only*: project identity/purpose, branch/commit
and journal revision, architecture/conventions, constraints, recently
completed tasks (with levels), failed tasks/entries and failed-approach
lessons, open bugs/security issues/limitations, recent decisions, the focus
task (explicit `focus_task_id` or the highest-priority open task) with
acceptance criteria and dependency blockers, prioritized next actions, recent
test evidence (command, exit, environment, level), and the required
verification checklist. It explicitly tells the next agent that journal text
is untrusted data and that `[claimed]` is not evidence. The persisted record
stores which record ids were included. In the panel, the prompt is shown in
an editable textarea with a copy action; generating or copying never
executes anything.

## Retention and backup

Journal rows live in `/data/gateway.db` with everything else and are
therefore included in Home Assistant add-on backups. There is **no automatic
journal purge** and no delete API: history is retained until an administrator
removes the database (the audit-log retention setting does not apply to the
journal). Practical consequences: the database grows with use (records are
small and bounded); restores bring the journal back with the rest of `/data`;
losing `/data` loses the journal even if the vault export survives — the
vault export contains credentials only, not journal records. The repository
files (`docs/WORKLOG.md` etc.) are the off-device, human-curated mirror.

## Security notes and limitations

- Any agent with `journal:read` can read the whole project journal, including
  other agents' entries. That sharing is the point of handoffs — do not grant
  journal scopes to agents that must not see project history, and never write
  secrets into records (redaction mitigates accidents; it is pattern-based
  and cannot recognize every secret shape).
- Verification levels are self-reported. The gateway records *who* claimed
  *what*, *when*, with which command/exit/output; it does not execute tests
  and cannot prove a run happened. Cross-check against `git` and CI.
- History integrity is API-level (no edit/delete routes, owner-scoped status
  changes, audit events). A host administrator with `/data` access can still
  rewrite SQLite directly — same trust boundary as the audit log.
- One project (`default`) is seeded. The schema keys everything by
  `project_id`, but project creation/management endpoints are not exposed in
  this release.
- `AGENTS.md` is only auto-read by agents that support it. Others need
  configuration (e.g. `CLAUDE.md` points Claude Code to it) or the generated
  handoff prompt pasted into the session.

## Manual verification procedure

1. Build/run the gateway locally (`docs/DEVELOPMENT.md`), initialize the
   vault, create an agent with `journal:read` + `journal:write` and the
   `journal_readwrite` template.
2. `GET /api/v1/journal/project-state` with the token → expect the default
   project with zero counts; the panel's Project Journal section shows empty
   states (no sample data).
3. Start a session, create two tasks (second depends on first), confirm the
   second cannot start (`409`), complete the first with
   `verification_level: claimed` (expect `422`), then with
   `automated_tests` (expect `200`).
4. Record a test run whose output contains `password=…`; confirm the stored
   record shows `[REDACTED]` in the panel timeline.
5. Restart the add-on/process; confirm records are still listed (persistence
   is also covered by `tests/test_journal.py`).
6. Generate a handoff in the panel; confirm it names the blocked/open tasks,
   shows test evidence with levels, can be edited, and copies to the
   clipboard.
7. Remove the agent's journal policy rule; confirm journal calls now return
   `403 policy_denied` while non-journal tools are unaffected.
