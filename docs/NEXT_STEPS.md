# Next steps — prioritized remaining work

> Read with `docs/PROJECT_STATE.md` and `docs/AUDIT_CHECKLIST.md`. Verification
> levels follow `AGENTS.md` §4. P0 blocks a production/live-release claim;
> lower numbers = do sooner.

## P0 — finish 0.4.2 PR, CI, and release evidence (in progress)

- **Task:** complete the final secret/diff review, open exactly one PR from
  `arena/a2524cd0-ha-mcp`, and require the Python, UI, packaging, and amd64
  image/startup jobs to pass. Append actual check URLs/results to
  `docs/WORKLOG.md`; merge and invoke the release workflow only after green.
- **Depends on:** GitHub Actions. No duplicate PR exists at audit time.
- **Acceptance criteria:** all required checks green on the PR head; container
  startup smoke passes with whatever AppArmor capability the runner actually
  has (do not infer enforcement if unavailable); version/changelog sync passes;
  release workflow result and tag are recorded accurately.

## P0 — install 0.4.2 on real Home Assistant OS (blocked: needs user's HA OS)

- **Task:** upgrade a test/non-production HA OS installation, open the panel
  through Ingress, dismiss the 0.4.2 notice from both Dashboard and Updates,
  reload/restart, and verify it stays dismissed while release history remains.
  Exercise vault unlock and one Agent Access session end to end.
- **Depends on:** explicit user authorization and host access. Do not deploy
  from this session without that approval.
- **Acceptance criteria:** S6 starts under Supervisor; `/healthz` and `/readyz`
  are healthy; no startup traceback; existing agents/connections/vault records
  survive; dismissal persists; a genuinely new version notice reappears; a
  temporary token is revoked by the configured restart policy while upstream
  credentials still decrypt; results are logged as `integration_verified` or
  failures are filed honestly.

## P1 — live-target integration tests (blocked: needs isolated lab)

- **Task:** against authorized lab targets, run real HA REST reads plus one
  approved service call (including nested/broad target denial), Proxmox QEMU
  and LXC reads, SSH service/log/template paths with an enrolled host key, and
  one third-party MCP client on Windows or another supported host.
- **Acceptance criteria:** each flow succeeds once and fails safely for bad
  credentials/policy/trust; no production-destructive operation; no secret in
  command logs; exact commands/results enter the worklog/runtime journal at
  `integration_verified`.

## P1 — real browser and accessibility pass (blocked: needs browser/Ingress)

- **Task:** desktop/mobile and keyboard-only pass over all 12 sections, one
  screen-reader spot check, real touch/keyboard dismissal, clipboard behavior
  inside the Ingress iframe, and the unauthenticated agent handoff page.
- **Acceptance criteria:** no CSP/console errors, sane focus order, visible
  failure states, no unhandled rejection, no browser-stored token; findings are
  fixed with regressions or recorded as open issues.

## P1 — aarch64 build evidence (blocked: needs builder)

- **Task:** build and start the add-on for aarch64 on a suitable builder or HA
  OS device. Current CI builds amd64 only; architecture metadata alone is not
  build evidence.
- **Acceptance criteria:** image build, S6 startup, health/readiness, and one
  vault/agent smoke pass on aarch64; record platform and AppArmor status.

## P2 — independent security review and fuzzing

- **Task:** third-party review of vault migration/crypto, auth/policy/approval
  races, connector target extraction, SSH path handling, redaction, and MCP
  parsing; add malformed-input/fuzz coverage where appropriate.
- **Depends on:** reviewer availability/budget.
- **Acceptance criteria:** written findings triaged; critical/high issues fixed
  with regression tests before a production-security claim.

## Intentional limitations, not silently “done”

- Home Assistant is REST-only; no WebSocket tool requires it.
- Proxmox write/power/delete and arbitrary/root SSH shell tools do not exist.
- Installation-scoped release acknowledgement is deliberate, not per-user.
- Journal verification levels are self-reported metadata; the gateway does not
  execute the recorded tests.

## Standing rules

- Never mark P0/P1 live items done from sandbox evidence.
- Never weaken TLS, host-key checking, auth, policy, approvals, or tests to get
  a pass. Keep upstream credentials out of logs, URLs, responses, and Git.
