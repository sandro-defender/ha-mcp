#!/usr/bin/env bash
# Container-level startup smoke test (CI only; requires a Docker daemon).
#
# It proves that S6-Overlay reaches stage 2, that the application answers
# /healthz, that the app really runs as the unprivileged user, that a restart
# works, and that SIGTERM shuts the container down cleanly.
#
# AppArmor handling has two separate concerns and they must not be confused:
#
#   1. The profile must be *valid*.  `apparmor_parser -Q -K -T` parses it
#      without touching the kernel or the cache, so a syntax error is always a
#      hard failure -- that is a bug in this repository.
#   2. The profile must be *loadable* to be enforced.  That needs root and an
#      AppArmor-enabled kernel.  When the runner cannot provide either, the
#      test falls back to Docker's default profile and says so loudly (notice
#      annotation) instead of failing, because the add-on is not at fault.
#      A green run in that mode does NOT prove the AppArmor rules are correct;
#      tests/test_startup.py covers them statically and a real HA OS install
#      is still required (see docs/STARTUP_TROUBLESHOOTING.md).
#
# Every failure prints the container state and log tail, and also emits them as
# GitHub annotations so a CI-only failure can be diagnosed outside the runner.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADDON_DIR="$REPO_ROOT/agent_gateway"
IMAGE="${IMAGE:-ha-agent-gateway:smoke}"
CONTAINER="${CONTAINER:-hag-smoke}"
INGRESS_PORT="${INGRESS_PORT:-18099}"
AGENT_PORT="${AGENT_PORT:-18098}"
PROFILE_NAME="ha_agent_gateway"
PROFILE="$ADDON_DIR/apparmor.txt"
AA_MODE="docker-default"
SECURITY_OPT=()

log() { printf '\n== %s\n' "$*"; }

# Emit a GitHub annotation (newlines must be %0A encoded).  Harmless locally:
# the line is just printed.
annotate() {
  local level="$1" title="$2" message="$3"
  message="$(printf '%s' "$message" | tr -d '\r' | head -c 6000)"
  printf '::%s title=%s::%s\n' "$level" "$title" "${message//$'\n'/%0A}"
}

container_state() {
  docker inspect -f \
    'running={{.State.Running}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} apparmor={{.AppArmorProfile}} error={{.State.Error}}' \
    "$CONTAINER" 2>&1 || echo "container $CONTAINER does not exist"
}

fail() {
  local reason="$1" details
  details="image=$IMAGE container=$CONTAINER apparmor=$AA_MODE"$'\n'"$(container_state)"$'\n'
  details+="--- docker logs (tail 60) ---"$'\n'"$(docker logs "$CONTAINER" 2>&1 | tail -60 || true)"
  printf '\nRESULT: FAIL — %s\n' "$reason" >&2
  printf '%s\n' "$details" >&2
  annotate error "Startup smoke: $reason" "$details"
  exit 1
}

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }

if [ "${SKIP_BUILD:-0}" = "1" ]; then
  log "Using prebuilt image $IMAGE (SKIP_BUILD=1)"
  docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "image $IMAGE not found" >&2; exit 2; }
else
  log "Build image"
  docker build \
    --build-arg BUILD_ARCH=amd64 \
    --build-arg BUILD_VERSION="$(awk '/^version:/ {gsub(/"/, "", $2); print $2}' "$ADDON_DIR/config.yaml")" \
    -t "$IMAGE" "$ADDON_DIR"
fi

# ---------------------------------------------------------------------------
# AppArmor: validate, then try to enforce.
# ---------------------------------------------------------------------------
log "AppArmor availability"
AA_SUDO=""
if [ "$(id -u)" != "0" ] && command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  AA_SUDO="sudo -n"
fi

if command -v apparmor_parser >/dev/null 2>&1; then
  # Parse only: no kernel load, no cache write.  A rejection here is a real
  # defect in apparmor.txt and must fail the build.
  if ! $AA_SUDO apparmor_parser -Q -K -T "$PROFILE" 2>/tmp/hag-aa-parse.log; then
    cat /tmp/hag-aa-parse.log >&2 || true
    fail "apparmor_parser rejected agent_gateway/apparmor.txt (invalid profile)"
  fi
  echo "profile parses: $PROFILE"

  if [ ! -d /sys/module/apparmor ] && [ ! -d /sys/kernel/security/apparmor ]; then
    echo "AppArmor is not enabled on this host; cannot enforce the profile."
    annotate notice "Startup smoke: AppArmor not enabled" \
      "The profile was validated but not enforced. Rules are covered statically by tests/test_startup.py; a real HA OS install is still required."
  elif $AA_SUDO apparmor_parser -r -W "$PROFILE" 2>/tmp/hag-aa-load.log; then
    AA_MODE="apparmor=$PROFILE_NAME"
    SECURITY_OPT=(--security-opt "apparmor=$PROFILE_NAME")
    echo "loaded and enforcing profile $PROFILE_NAME"
  else
    echo "could not load the profile into the kernel (needs root/CAP_MAC_ADMIN); using docker-default."
    cat /tmp/hag-aa-load.log >&2 || true
    annotate notice "Startup smoke: AppArmor not enforced" \
      "apparmor_parser could not load the profile on this runner, so the container ran under docker-default. Profile validity was checked; enforcement needs a privileged runner or a real HA OS install."
  fi
else
  echo "apparmor_parser is not installed; using the Docker default profile."
  annotate notice "Startup smoke: AppArmor not enforced" \
    "apparmor_parser is unavailable on this runner; profile rules are covered by tests/test_startup.py only."
fi

log "Start container ($AA_MODE)"
cleanup
if ! docker run -d --name "$CONTAINER" \
  "${SECURITY_OPT[@]}" \
  -p "127.0.0.1:${INGRESS_PORT}:8099" \
  -p "127.0.0.1:${AGENT_PORT}:8098" \
  "$IMAGE" >/dev/null; then
  fail "docker run failed"
fi

APPLIED="$(docker inspect -f '{{.AppArmorProfile}}' "$CONTAINER" 2>/dev/null || echo unknown)"
echo "docker reports apparmor=$APPLIED"
if [ "$AA_MODE" != "docker-default" ] && [ "$APPLIED" != "$PROFILE_NAME" ]; then
  fail "requested AppArmor profile $PROFILE_NAME but the container reports '${APPLIED}'"
fi

log "Wait for /healthz"
HEALTHY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${INGRESS_PORT}/healthz" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]; then
    fail "the container exited before /healthz answered"
  fi
  sleep 1
done
[ "$HEALTHY" = "1" ] || fail "/healthz did not answer within 60 s"

log "Checks"
curl -fsS --max-time 5 "http://127.0.0.1:${INGRESS_PORT}/healthz" | grep -q "\"ok\"" || fail "management /healthz payload"
curl -fsS --max-time 5 "http://127.0.0.1:${AGENT_PORT}/healthz" | grep -q "\"ok\"" || fail "agent /healthz payload"
curl -fsS --max-time 5 "http://127.0.0.1:${INGRESS_PORT}/readyz" | grep -q '"ready":true' || fail "/readyz not ready"

log "S6 chain and privilege state"
docker exec "$CONTAINER" sh -c 'test -x /init && test -r /init' || fail "/init is not readable+executable in the image"
docker exec "$CONTAINER" sh -c 'ls /package/admin/s6-overlay-*/libexec/preinit >/dev/null' || fail "preinit missing from the image"

# PID 1 must be s6-svscan (or the stage-2 shell before it hands over), never a
# bare python: that is what proves S6 actually completed its init.
PID1="$(docker inspect -f '{{.State.Pid}}' "$CONTAINER")"
COMM1="$(ps -o comm= -p "$PID1" 2>/dev/null | tr -d ' ' || true)"
echo "container pid1=$COMM1"
case "$COMM1" in
  s6-svscan|s6-svscanctl|execlineb|s6-linux-init|/init|init) ;;
  "") echo "warning: could not read PID 1 command name" ;;
  *) fail "unexpected PID 1 '$COMM1' (S6 did not take over)" ;;
esac

# Find the gateway process through /proc so this works with busybox ps.
GATEWAY_UID="$(docker exec "$CONTAINER" sh -c '
  for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    cmd=$(tr "\0" " " < "$p/cmdline" 2>/dev/null) || continue
    case "$cmd" in
      *"-m gateway"*)
        awk "/^Uid:/{print \$2; exit}" "$p/status" 2>/dev/null
        ;;
    esac
  done' | head -1)"
if [ -z "$GATEWAY_UID" ]; then
  fail "no 'python -m gateway' process found inside the container"
fi
echo "gateway uid=$GATEWAY_UID"
[ "$GATEWAY_UID" = "1000" ] || fail "the gateway process is not running as uid 1000 (got $GATEWAY_UID)"

log "Readiness after in-place restart"
docker restart "$CONTAINER" >/dev/null
READY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${INGRESS_PORT}/readyz" 2>/dev/null | grep -q '"ready":true'; then
    READY=1
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]; then
    fail "the container exited during restart"
  fi
  sleep 1
done
[ "$READY" = "1" ] || fail "not ready 60 s after restart"

log "Graceful shutdown"
docker stop --time 20 "$CONTAINER" >/dev/null || fail "docker stop failed"
EXIT_CODE="$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")"
docker logs "$CONTAINER" 2>&1 | tail -20 || true
[ "$EXIT_CODE" = "0" ] || fail "container exit code ${EXIT_CODE} after SIGTERM (expected 0)"

echo
echo "RESULT: PASS (S6 stage 2, health, readiness, uid 1000, restart, graceful shutdown; AppArmor=$AA_MODE)"
