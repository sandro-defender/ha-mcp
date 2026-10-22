# Next steps — prioritized remaining work

> Read with `docs/PROJECT_STATE.md`. Update priorities/statuses at the end of
> each session. `P0` blocks a production claim; lower numbers = do sooner.
> Verification levels per `AGENTS.md` §4.

## P0 — verify 0.3.0 on real Home Assistant OS (blocked: needs user's HA OS)

- **Task:** Install/update the add-on to 0.3.0 from this repository on the
  real HA OS host; confirm startup under the Supervisor (0.2.1 AppArmor
  `/init` fix + 0.3.0 code), open the panel via Ingress, and confirm the
  existing `/data` migrates v2 → v3 in place.
- **Depends on:** user authorization + access to the HA OS host. Do **not**
  deploy without explicit approval.
- **Acceptance criteria:**
  - Add-on starts; `/healthz` ok; add-on log shows schema migration to v3
    with agents/connections/credentials intact.
  - Panel → **Project Journal** renders (empty state on first run is
    correct); edit project state; create a task; all persist across an
    add-on restart.
  - An agent with `journal:read/write` + `journal_readwrite` template can
    `GET /api/v1/journal/project-state` and record an entry; an agent
    without the rule gets `403`.
  - Result recorded in `docs/WORKLOG.md` and the runtime journal at level
    `integration_verified` (or failures recorded honestly).

## P1 — live-target integration tests

- **Task:** Against lab (never production-destructive) targets: real HA REST
  reads + one approved `ha.call_service`; real Proxmox read tools; SSH
  `service_status`/`read_log` with enrolled host key; one MCP client
  (e.g. Claude Desktop) end-to-end incl. `journal.*` tools.
- **Depends on:** P0 (or any reachable test HA/PVE/SSH hosts).
- **Acceptance criteria:** each flow executed once successfully and once in
  its main failure mode (bad token, unknown host key, policy deny); commands
  and results recorded in the journal (`journal.record_test_run`,
  `integration_verified`); gaps filed as journal issues.

## P1 — browser and accessibility pass on the real panel

- **Task:** Manual pass of all 11 sections in a real browser inside HA
  Ingress (desktop + mobile widths), keyboard-only navigation, one
  screen-reader spot check of the Journal timeline/handoff flow.
- **Depends on:** P0.
- **Acceptance criteria:** no CSP console errors; focus order sane; handoff
  copy works in the Ingress iframe (clipboard permission fallback shows if
  blocked); findings recorded as issues or fixed with tests.

## P2 — journal follow-ups (only if real usage demands them)

- MCP parity for `journal.list_test_runs` / `journal.list_sessions`
  (currently REST-only; timeline covers runs).
- Multi-project management endpoints (schema already keys by `project_id`;
  only the seeded `default` project is reachable today).
- Optional: export journal (JSON/Markdown) from the panel; retention policy
  for handoffs if the table grows; deep (>200/type) merged-timeline paging
  via keyset cursors.
- **Acceptance criteria (each):** failing test first where practical, same
  authorization path, docs in `docs/JOURNAL.md` updated in the same PR.

## P2 — container-run smoke in CI

- **Task:** CI job that runs the built image and probes `/healthz` +
  a journal write/read round-trip (today CI only builds `linux/amd64`).
- **Depends on:** Docker-capable runner configuration (repo owner decision).
- **Acceptance criteria:** job green on PRs; failure output includes
  redacted container logs.

## P3 — independent security review

- **Task:** Third-party review of vault, auth/policy/approvals, journal
  authorization, and redaction; track findings as journal `security` issues.
- **Depends on:** reviewer availability/budget (owner decision).
- **Acceptance criteria:** written findings triaged; criticals fixed with
  regression tests before any "production-ready" claim.

## Standing rules for whoever picks these up

- Follow `AGENTS.md` §2 (journal lifecycle) and §3 (constraints).
- Never mark P0/P1 done from sandbox evidence — they require real
  infrastructure by definition (`blocked_infrastructure` until run).
