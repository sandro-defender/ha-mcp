# Startup troubleshooting: S6-Overlay permission denials

This document records the startup failure reported for 0.2.1–0.3.1, the
verified chain that produces it, the fix shipped in 0.4.0, and exactly what has
and has not been proven about it.

## Symptom

The add-on stops immediately after start. The Supervisor log shows, in order:

```text
/bin/sh: can't open '/init': Permission denied
/bin/sh: can't open '/package/admin/s6-overlay-3.2.3.2/libexec/preinit': Permission denied
s6-overlay-suexec: fatal: child failed with exit code 2
```

`run.sh`, Python, the vault, and the gateway never start. Home Assistant reports
the add-on as stopped; the watchdog never gets a `/healthz` response.

## What actually runs (verified against the pinned sources)

| Step | Component | Evidence |
| --- | --- | --- |
| 1 | Supervisor creates the container with `ENTRYPOINT ["/init"]` from the HA base image `ghcr.io/home-assistant/base:3.24` | `home-assistant/docker-base` `Dockerfile` |
| 2 | The kernel sees `#!/bin/sh -e` on `/init` and starts `/bin/sh /init` | s6-overlay v3.2.3.2 `layout/rootfs-overlay/init` |
| 3 | `/init` execs `s6-overlay-suexec ' …/libexec/preinit' '' …/libexec/stage0 "$@"` | same file |
| 4 | `s6-overlay-suexec` requires PID 1, drops to the real uid, and `xmspawn`s `/package/admin/s6-overlay-3.2.3.2/libexec/preinit` as a child | `just-containers/s6-overlay-helpers` v0.1.2.2 `s6-overlay-suexec.c` |
| 5 | `preinit` (`#!/bin/sh -e`) execs `stage0` (also `#!/bin/sh -e`) | s6-overlay v3.2.3.2 `libexec/` |
| 6 | `stage0` builds `/run/s6/basedir` and execs `/run/s6/basedir/bin/init` (`#!/bin/sh`) | same |
| 7 | stage2 `rc.init` runs `/etc/cont-init.d`, `/etc/services.d`, then `CMD ["/run.sh"]` | HA base image + s6-overlay |
| 8 | `run.sh` fixes `/data` ownership as root, then `exec su-exec app:app /opt/venv/bin/python -m gateway` | this repository |

Two details matter for the diagnosis:

1. Every one of these files is a **shell script executed through its shebang**.
   The kernel does not open the file for the process; it starts the
   interpreter and passes the path. The interpreter (BusyBox `/bin/sh` on
   Alpine) then `open(2)`s the script **for reading**. AppArmor mediates that
   `open`, so `ix` (execute-only, no read) is not sufficient: the shell fails
   with `can't open … Permission denied` and exits with status 2.
2. `s6-overlay-suexec: fatal: child failed with exit code 2` is the *outer*
   error. `s6-overlay-suexec.c` prints exactly that when its `preinit` child
   exits non-zero (`strerr_dief2x(wait_estatus(wstat), "child failed with exit code ", …)`).
   So the fatal line is a consequence, not an independent S6 problem. The PID-1
   guard (`getpid() != 1` → exit 100) did not trigger, which proves the
   entrypoint chain itself was reached.

## Root cause

The custom profile in `agent_gateway/apparmor.txt` **replaces** the Supervisor
default profile for this add-on (`apparmor.txt` present → Supervisor loads it
and runs the container with `--security-opt apparmor=<add-on slug>`). The
profile granted that S6 chain execute-only or nothing at all:

- 0.2.1: no rule for `/init`, `/package/**`, `/command/**` — the first failure
  is `/bin/sh: can't open '/init'`.
- 0.3.1: `/init rix` (read fixed) but `/package/** ix` and `/command/** ix`
  (execute-only), so the failure moved one stage later to
  `/bin/sh: can't open '/package/admin/s6-overlay-3.2.3.2/libexec/preinit'`.

Both messages appear in the report because the add-on was updated between
attempts. Both files are readable and executable in the image; the denial came
from AppArmor, not from file modes, ownership, or a mounted `noexec` filesystem.

The fix is therefore a profile fix, **not** a file-mode fix: no `chmod`, no
`chown`, no replacement of `/init`, no `init: true`, no AppArmor disablement.

## Fix in 0.4.0

`agent_gateway/apparmor.txt` now grants **read + inherited execute** (`rix`) to
every S6 script location:

- `/init rix`
- `/package/** rix` (preinit, stage0, s6-linux-init skeleton)
- `/command/** rix`
- `/etc/s6-overlay/** rix`
- `/etc/services.d/** rix`, `/etc/cont-init.d/** rix`, `/etc/cont-finish.d/** rix`
- `/run/{s6,s6-rc*,service}/** rwkix`

Additional hardening in the same profile, each rule justified in a comment:

- `/bin/**`, `/sbin/**`, `/usr/bin/**`, `/usr/sbin/**`, `/usr/libexec/**`
  are `mrix`/`mr` because Alpine applets are symlinks to `/bin/busybox` and
  AppArmor mediates the resolved path.
- `/opt/venv/** mr` and `/opt/venv/bin/** mrix` cover the virtualenv and its
  native extensions (which are `mmap`ed with `PROT_EXEC`).
- `k` (file locking) on `/data` and `/tmp` covers SQLite WAL locks.
- Writes remain limited to `/data`, `/tmp`, `/run`, `/dev`; the profile grants
  no `mount`, `ptrace`, `raw`, `sys_admin`, or `sys_module` capability, and no
  arbitrary root shell.

The Dockerfile now asserts the chain at build time, so a future base image
change fails the build instead of the user's container:

```dockerfile
RUN test -x /init && test -r /init; \
    preinit="$(ls -d /package/admin/s6-overlay-*/libexec/preinit)"; \
    test -x "$preinit" && test -r "$preinit"; \
    test -x "$(dirname "$preinit")/stage0" …
```

## How to verify

### On Home Assistant OS (after updating the add-on)

1. Supervisor → add-on → **Log** should show the S6 banner and
   `gateway starting` and no `can't open` lines.
2. The add-on stays **started**; **Open Web UI** loads the panel through
   Ingress.
3. `GET /healthz` (through Ingress) returns `{"status": "ok"}`.
4. Stop the add-on, confirm a clean shutdown in the log, start again, and
   confirm the panel and vault behave as expected.

If it still fails, collect (do **not** disable AppArmor to “test”):

- the complete add-on log from the first line,
- Supervisor → System → Log for that container,
- `docker inspect <container>` security options if you have host shell access,
- whether `/data` is mounted and writable,
- the base-image digest actually pulled.

### Static + process tests in this repository

```bash
cd agent_gateway
python -m pytest -q tests/test_startup.py tests/test_packaging.py
python tools/startup-smoke/smoke.py --python "$(command -v python3)" \
    --pythonpath ../agent_gateway/src
```

`tests/test_startup.py` parses `apparmor.txt` with a small AppArmor glob
matcher and asserts that every S6 script path the base image executes is
matched by a rule that includes both `r` and `x`, that no catch-all `/**`
rule exists, that no rule grants `w` to a system tree, and that the Dockerfile,
`run.sh`, `config.yaml`, and health checks keep the documented contract.
`tools/startup-smoke/smoke.py` starts the real `run.sh` and the real gateway
process, probes both listeners, checks the Ingress gate, sends `SIGTERM`, and
restarts against the same data directory.

### Docker / CI

`.github/workflows/ci.yml` builds the real amd64 image and runs
`tools/startup-smoke/docker-smoke.sh`. On a runner with
`/sys/kernel/security/apparmor` and `apparmor_parser`, the script loads
`agent_gateway/apparmor.txt` and starts the container with
`--security-opt apparmor=ha_agent_gateway`, i.e. the same enforcement the
Supervisor applies (Supervisor renames the profile to the add-on slug but keeps
the rules). Otherwise it falls back to the Docker default profile and prints
that it did so — a green run without AppArmor does **not** prove the profile.

## What the container run under the real profile found

The container smoke test builds the image, loads `agent_gateway/apparmor.txt`
into the kernel with `apparmor_parser`, starts the container with
`--security-opt apparmor=ha_agent_gateway` (the same enforcement the Supervisor
applies after renaming the profile to the add-on slug), and drives the whole
lifecycle. It found four defects that static review had missed. All four are
fixed in 0.4.0 and the job is green.

**1. `/run.sh` had no rule.** The first run reached S6 stage 2 — so the read fix
was correct — and then stopped with:

```text
/run/s6/basedir/scripts/rc.init: line 105: /run.sh: Permission denied
```

`/run.sh` is the add-on `CMD`, executed by the stage-2 `rc.init`. The profile
covered `/init`, `/package/**`, `/command/**` and the `/run/` **tree**, but
`/run.sh` is a *sibling* of that directory. Fixed with `/run.sh rix`, and
`/run.sh` is now part of the static startup contract.

**2. `ps` could not read `/proc`.**

```text
ps: can't open '/proc': Permission denied
```

`/proc/**` does not match the `/proc/` directory itself (the same reason
`/data/` needs its own rule). Besides breaking every in-container diagnostic,
the shutdown path enumerates `/proc`; without it the container stopped all
services and then never exited. Fixed with `/proc/ r` and `/sys/ r`.

**3. Signal delivery inside the container was denied.** Measured directly:

| Direction | Result |
| --- | --- |
| `kill -TERM <app pid>` from inside the container | `Operation not permitted` |
| `kill -TERM <app pid>` from the host | app logs `shutdown requested`, exits cleanly |

The app was never the problem. Two independent causes:

- **`CAP_KILL` was missing.** The profile lists capabilities, so unlisted ones
  are denied; the app runs as uid 1000 while the supervisor stays root, and
  signalling across uids needs `CAP_KILL`. Fixed with `capability kill`.
- **Signal rules.** A `signal (send) set=(...)`/`signal (receive)` pair (and the
  example profile's `peer=` form) did not authorise delivery, so those rules
  are gone. Without signal rules AppArmor does not mediate signals, exactly as
  Docker's default profile behaves. Every process in the container is one trust
  domain, so nothing is lost at the boundary this profile defends.

**4. `S6_CMD_RECEIVE_SIGNALS=1` is unusable on Alpine.** s6-overlay implements
that switch in its `CMDSIG` helper:

```sh
kill -s "${0##*/SIG}" -- "$pid"
```

busybox `kill` (the Alpine shell builtin) rejects `--` with
`invalid number '--'`, so the diverted signal is never delivered and the
container hangs instead of stopping. It is therefore **not** used. Instead the
image sets `S6_KILL_GRACETIME=10000` so the TERM that s6 sends to the remaining
processes at the end of shutdown has time to drain both listeners, and the app
bounds that drain with `timeout_graceful_shutdown=5`.

### What the green job proves

- The image builds, and `/init`, `preinit`, `stage0` and `/run.sh` are readable
  and executable.
- S6 completes stage 2 under the **enforced** profile, the gateway starts as
  uid 1000, `/healthz` and `/readyz` answer on both listeners.
- `docker restart` comes back ready, and `docker stop` exits **0** (no SIGKILL)
  — i.e. the app receives TERM, drains, and the container shuts down cleanly.
- `tests/test_startup.py` (31 checks) covers the same contract statically.

A run where the runner cannot load the profile reports a `notice` annotation and
is **not** evidence that the rules are correct.

## What remains unverified

- No Home Assistant OS host or Supervisor was available. The image was built and
  booted under real Docker and a real AppArmor kernel by CI, but not by the
  Supervisor, so Supervisor-side behaviour (profile install/rename,
  `security_opt`, `init: false`, watchdog) is verified from source only.
- The rule set is a defensive superset of the documented HA example (which
  grants `ix` only and compensates with a broad `file,` rule); it was not
  minimised against a particular Supervisor version.
- The reported failure itself was not reproduced: this container's *first*
  failure was the `/run.sh` denial, which is the same class of defect one stage
  later. The read-denial diagnosis for `/init` and `libexec/preinit` remains
  inferred from the pinned sources plus the exact error strings.

## Related files

- `agent_gateway/apparmor.txt` — profile with per-rule justification.
- `agent_gateway/Dockerfile` — build-time chain assertions.
- `agent_gateway/run.sh` — entrypoint; drops to `app` before Python.
- `agent_gateway/tests/test_startup.py` — static startup contract.
- `tools/startup-smoke/` — process-level and container-level smoke harnesses.
- `.github/workflows/ci.yml` — image build + startup smoke job.
