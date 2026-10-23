# Project state — Home Assistant Agent Gateway

> Concise current state for the next agent/admin session. Update this file at
> the end of every work session (see `AGENTS.md` §2). Detailed history:
> `docs/WORKLOG.md`. Prioritized remaining work: `docs/NEXT_STEPS.md`.
> Runtime records: the gateway Project Journal (`docs/JOURNAL.md`).

- **Last updated:** 2026-10-23 by Arena coding agent (session
  `arena/09933ac1-ha-mcp`, PR #7)
- **Branch:** `arena/09933ac1-ha-mcp` · 0.4.0 changes committed (`87c4b6d`)
  and pushed; Docker build and HA OS verification still pending.
- **Version:** 0.4.0 (synced across config/pyproject/Dockerfile/`__init__`/
  both CHANGELOGs; not yet released)

## Purpose and architecture

Home Assistant OS add-on that lets external AI agents operate Home
Assistant, Proxmox VE, and restricted SSH targets through one
deny-by-default policy engine without receiving credentials: AES-256-GCM +
Argon2id vault, per-agent hashed `hag_…` tokens and scopes, single-use
parameter-bound approvals, redacted SQLite audit, two listeners (Ingress
management `:8099`, agent REST+MCP `:8098`) sharing one `Runtime`,
`PolicyService`, and audit path. 0.3.0 adds the **Project Journal**
(schema v3 `journal_*` tables, `gateway/journal/`, REST + MCP `journal.*`
tools via the central policy engine, 11th panel section) so agents can
persist sessions/tasks/decisions/issues/test evidence and generate
next-session handoff prompts. 0.4.0 adds the temporary agent access broker
(schema v4 `session_handoffs`, `/agent` handoff page, 12th panel section,
restart revocation) and the S6 AppArmor startup fix; see
`docs/AGENT_ACCESS.md` and `docs/STARTUP_TROUBLESHOOTING.md`. Full detail:
`README.md`, `docs/JOURNAL.md`, `docs/SECURITY.md`.

## Completed and verified (this branch)

| Item | Evidence | Level |
| --- | --- | --- |
| Baseline before changes | `pytest -q` → 211 passed on `6857797` | automated_tests |
| Journal schema v3 + v2→v3 migration preserving records; newer-schema rejection | `tests/test_journal.py` | automated_tests |
| Persistence across restarts | pytest (new `Runtime` on same data dir + raw sqlite read) **and** real process restart of a socket-level dev instance (`/tmp/gw-journal-demo`, schema_version 3, rows intact) | automated_tests |
| Task lifecycle: transitions, completion-requires-evidence, dependency blocking + cycle rejection | pytest + socket E2E (`409`/`422` paths) | automated_tests |
| Sessions, idempotent creates (no duplicates on retry) | pytest + socket E2E | automated_tests |
| Claim vs verified separation; timeline categories incl. failed/blocked/unverified; search/commit filter/pagination | pytest | automated_tests |
| Secret redaction pre-storage (password/Bearer/PEM probes absent from DB rows) | pytest + socket E2E + direct sqlite check (0 leaked rows) | automated_tests |
| Authorization: scope, policy deny-by-default, `require_approval` fail-closed, rate limit, cross-agent ownership, admin override, audit of writes/denials | pytest | automated_tests |
| Handoff generated from real records (focus task, acceptance criteria, blocked work, failed-approach lesson, test evidence, untrusted-data warning), persisted + idempotent | pytest + socket E2E (2 927-char prompt, re-fetched after restart) | automated_tests |
| MCP journal tools: list gating by scope, call without `connection_id`, REST/MCP same records (`source=mcp`), policy denial over MCP | pytest (in-process ASGI MCP session) | automated_tests |
| Panel Project Journal section (state, next steps, timeline, handoff generate → editable → copy, empty/denied states) | `test_ui_assets.py` journal cases + jsdom `tools/ui-smoke` (all checks ok incl. 5 journal checks) | automated_tests |
| Full regression | `pytest -q` → **253 passed**; `ruff check`, `ruff format --check`, `mypy --strict` (52 files), `node --check app.js` all clean | automated_tests |

Exact commands and environment: see `docs/WORKLOG.md` (2026-10-22 entry).

## In progress / blocked

- **Live deployment verification — blocked (infrastructure).** No Home
  Assistant OS/Supervisor, Proxmox, SSH lab, or Docker daemon exists in the
  agent sandbox. The 0.4.0 S6 AppArmor read+execute fix, the schema-v3→v4
  migration and the agent access broker are verified by source-level checks,
  automated tests and a process-level smoke run only — **not** on a real
  install. Must be confirmed on the user's HA OS before making
  production-readiness claims (`docs/STARTUP_TROUBLESHOOTING.md`,
  `docs/AGENT_ACCESS.md`).
- Nothing else is half-finished on this branch; 0.4.0 is committed and the PR
  is open.

## Known bugs and unresolved security issues

- No journal-specific bugs found by the test suite. Known systemic items
  (pre-existing, unchanged): no third-party security audit/pen test/fuzzing;
  no live HA/Proxmox/SSH integration tests; the container startup smoke
  (`tools/startup-smoke/docker-smoke.sh`) runs in CI's `image` job but has not
  been executed in this sandbox (no Docker daemon); HA WebSocket intentionally
  absent.
- Journal limitations (documented in `docs/JOURNAL.md`): verification levels
  are self-reported (gateway records claims + metadata; it does not run the
  tests); any `journal:read` agent sees the whole project journal; redaction
  is pattern-based; merged timeline serves the newest 200 records per type
  (per-type lists paginate fully); one seeded `default` project
  (multi-project endpoints not exposed); `journal.list_test_runs` /
  `journal.list_sessions` are REST-only over MCP (timeline covers runs).

## Important decisions

1. **Journal authorization via `journal.*` pseudo-tools in the central
   policy engine** (`connection_id=None`, read-risk evaluation). Rationale:
   one authorization path, per-agent rules/templates/rate limits for free.
   Journal writes execute nothing, so they are not approval-gated; a rule
   with `require_approval: true` *denies* (fail closed) instead of being
   ignored. Alternatives considered: scope-only checks (rejected — bypasses
   the policy engine the spec mandates) and full `OperationService` tool
   specs (rejected — connector tools require vault-backed connections).
2. **Append-only history.** No edit/delete routes for records; only
   validated status transitions, owner-scoped (admins override). Corrections
   are new entries. Project state is the one shared mutable document,
   protected by optimistic-concurrency `revision`.
3. **Completion requires evidence.** `completed` demands
   `automated_tests`/`integration_verified`; the server rejects
   claim-only completion.
4. **MCP journal tools dispatch outside `OperationService`** but through the
   identical `authorize_agent_journal` + `JournalService`, keeping REST/MCP
   parity without faking a connection.

## Failed approaches (this session)

- **Parallel edits to the same file raced** in the agent tooling: of several
  batched edits to `db.py`/`apps.py`/`service.py`, only the last took effect
  (55 test failures, missing imports/schema bump). Lesson: serialize
  same-file edits and grep-verify after every batch; use single asserted
  patch scripts for multi-site changes. Recovered with zero data loss
  (also recorded as a failed-approach entry in the E2E dev journal).
- `mypy --strict` rejected `list[str]` assigned to an annotated
  `list[str | None]` (invariance) in timeline level filtering; fixed by
  building the list via `append`/`extend`.

## Environment limitations (test context)

Sandbox: Python 3.11.2 venv, Node 22, no Docker daemon, no Supervisor, no
live HA/Proxmox/SSH targets, no real browser (jsdom only), outbound network
restricted to package registries/GitHub. Integration-level claims above are
therefore `automated_tests`, never `integration_verified`.

## Next tasks

See `docs/NEXT_STEPS.md` (prioritized, with dependencies and acceptance
criteria). Highest priority: install 0.4.0 on real HA OS, verify the S6
read+execute startup fix, confirm the v3→v4 migration on the existing `/data`,
and exercise one Agent Access session end to end.
