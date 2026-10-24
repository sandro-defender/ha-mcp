# Project state — Home Assistant Agent Gateway

> Concise current state for the next agent/admin session. Detailed history:
> `docs/WORKLOG.md`; remaining work: `docs/NEXT_STEPS.md`; requirement-level
> evidence: `docs/AUDIT_CHECKLIST.md`. Runtime records: the gateway Project
> Journal (`docs/JOURNAL.md`).

- **Last updated:** 2026-10-24 by Arena coding agent (session
  `arena/98324188-ha-mcp`)
- **Branch:** `arena/98324188-ha-mcp`, based on `520c5cb` (merged PR #9)
- **Version:** 0.4.2 plus Unreleased guided-agent-setup UI changes; runtime/package
  versions were not bumped for this UI-only follow-up
- **PR/release:** PR #9 was merged with all required checks green. The current
  guided-setup change has no PR yet and must pass its own CI before merge.

## Purpose and architecture

Home Assistant OS add-on that lets external AI agents operate Home Assistant,
Proxmox VE, and restricted SSH targets through one deny-by-default policy engine
without receiving upstream credentials. It includes an AES-256-GCM + Argon2id
vault, scoped digest-only bearer tokens, temporary agent sessions with
single-use handoffs, parameter-bound approvals, redacted SQLite audit and
Project Journal data, and two listeners: Ingress management `:8099` and agent
REST+MCP `:8098`, sharing one `Runtime`, `PolicyService`, and audit path.

## Current guided-setup change

Agent Access now defaults to **Connect an AI agent**: users choose connections,
read-only or read/control, a lifetime, and a suggested connection address. The
UI derives the existing scopes, saves/normalizes the address, issues the same
single-use temporary session, and presents an editable/copyable AI setup prompt
with MCP details, health/tools verification, and continuation instructions. The
prompt contains only the handoff code and placeholders—not upstream credentials
or a redeemed bearer token—and is never sent automatically. Advanced scope and
settings forms remain available. Handoff display now happens before the session
list refresh so a refresh error cannot hide the one-time response.

SSH already had a required `config.username`; no backend schema change was
needed. The UI now calls it **SSH username** and the private-key credential form
explains that the username belongs to the separate SSH connection.

Verification: **356 passed in 22.00s**, all five jsdom harnesses (including 30
Agent Access checks), Ruff, format, strict mypy, and JavaScript syntax all pass.
No real browser, live HA OS, third-party AI client, or live SSH integration was
available; those remain unverified.

## Historical 0.4.2 audit result

The P0 changelog-dismissal defect and eleven additional reliability/security
defects were reproduced or directly demonstrated, fixed, and covered by
regressions:

1. stale post-update notice after successful acknowledgement;
2. nested and broad Home Assistant service targets escaping entity policy;
3. in-flight scope removal and newly required approval not stopping execution;
4. Proxmox LXC status evaluated as a QEMU `vms` target;
5. temporary sessions eligible for global `audit:read`;
6. temporary connection catalog exposing unauthorized connection metadata;
7. unbounded buffering of discarded upstream HTTP error bodies;
8. SSH short chunked reads mistaken for EOF;
9. SQLite WAL/SHM sidecars created as `0644`;
10. v0 vault migration retaining an incompatible DEK wrapper;
11. unexpected listener exit returning success; and
12. obsolete dotted scope IDs in the agent form's no-status fallback.

The exact file/test evidence and all PASS/FIXED/PARTIAL/MISSING/BLOCKED/NOT
TESTED classifications are in `docs/AUDIT_CHECKLIST.md`.

## Verified in this session

| Check | Result | Evidence level |
| --- | --- | --- |
| Unmodified baseline | 329 passed | automated_tests |
| Final Python suite at this checkpoint | **354 passed in 22.89s** | automated_tests |
| Ruff lint/format | clean; 75 files formatted | automated_tests |
| Strict mypy (`mypy src`) | clean; 55 source files | automated_tests |
| UI jsdom | all five harnesses passed, including 14 changelog-dismissal checks; wired into PR CI | automated_tests, not a real browser |
| Process startup smoke | two start/readiness/SIGTERM/restart cycles passed | automated process test |
| Occupied-port startup probe | nonzero exit (3), runtime shutdown retained | automated process test |
| Python wheel | built successfully with `pip wheel --no-deps --no-build-isolation` | build test |
| Temporary restart behavior | old token 401; epoch/revocation persisted; synthetic upstream credential still decrypted | automated_tests |

## In progress / blocked

- **PR CI and container evidence:** PR #9 passed all four required jobs and was
  merged as `520c5cb`. The current guided-setup change still needs its own PR
  CI. Local Docker/AppArmor remain unavailable in this sandbox.
- **Live deployment and integrations:** no HA OS/Supervisor, live Home
  Assistant/Proxmox/SSH target, third-party MCP client, or real browser was
  available. These remain `blocked_infrastructure`, not PASS.
- **aarch64:** declared in metadata but not built in this session; current CI
  builds amd64 only.
- **Independent security review/fuzzing:** not performed.

## Important decisions

- **Fail closed for unknown targets.** Home Assistant area/device/floor/label
  or untargeted service calls use a broad-target sentinel. Specific entity
  allow rules do not match it; entity deny constraints still veto it.
- **Fresh principal at execution.** Scope, temporary-session constraints,
  policy, limits, and tool context all use the freshly loaded principal. If a
  read rule gains `require_approval` in flight, the operation fails and must be
  retried through the normal approval flow.
- **Temporary sessions do not get global audit.** `audit:read` remains available
  to persistent administrator-managed identities only. Temporary discovery is
  filtered to authorized connection IDs without changing persistent behavior.
- **Changelog acknowledgement is installation-scoped.** It is shared by the
  add-on's administrators, hides only the acknowledged version, and leaves
  release history available.
- **No architecture redesign.** Existing UI polish, API contracts, vault
  format version, policy model, and connector architecture were preserved.

## Failed approaches / lessons

- The first jsdom acknowledgement reproduction reused the same API object
  cached by the panel and therefore produced a false pass. Fresh deep-copy API
  responses reproduced the stale-state bug and are now permanent harness
  behavior.
- Treating a failed acknowledge request as already handled by `guarded()` was
  insufficient because it rethrows; both dismissal click handlers now consume
  the terminal rejection after the toast.
- The initial wheel command failed because the development venv lacked the
  `wheel` command (`invalid command 'bdist_wheel'`). Installing the build tool
  and rerunning succeeded; this was an environment issue, not a package defect.

## Next tasks

See `docs/NEXT_STEPS.md`. Immediate sequence for this branch: final secret/diff
review, one guided-setup PR, and required CI before any merge. Live HA OS, real
browser/AI-client, and lab verification remain separate follow-up work.
