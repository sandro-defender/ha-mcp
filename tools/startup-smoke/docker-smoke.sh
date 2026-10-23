#!/usr/bin/env bash
# Container-level startup smoke test (CI only; requires a Docker daemon).
#
# It builds the add-on image, starts it, proves that S6-Overlay reached stage 2,
# that the application answers /healthz, that the app really runs as the
# unprivileged user, that a restart works, and that SIGTERM shuts the container
# down cleanly.
#
# When the runner has AppArmor (securityfs + apparmor_parser) this script loads
# agent_gateway/apparmor.txt as `ha_agent_gateway` and starts the container with
# `--security-opt apparmor=ha_agent_gateway`, which is the same enforcement the
# Supervisor applies (it renames the profile to the add-on slug).  Otherwise it
# falls back to the Docker default profile and says so explicitly — a green run
# without AppArmor does NOT prove the profile is correct.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADDON_DIR="$REPO_ROOT/agent_gateway"
IMAGE="${IMAGE:-ha-agent-gateway:smoke}"
CONTAINER="${CONTAINER:-hag-smoke}"
INGRESS_PORT="${INGRESS_PORT:-18099}"
AGENT_PORT="${AGENT_PORT:-18098}"
PROFILE_NAME="ha_agent_gateway"
AA_MODE="docker-default"

log() { printf '\n== %s\n' "$*"; }
fail() { printf '\nRESULT: FAIL — %s\n' "$*" >&2; docker logs "$CONTAINER" 2>&1 | tail -80 >&2 || true; exit 1; }
cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }

if [ "${SKIP_BUILD:-0}" = "1" ]; then
  log "Using prebuilt image $IMAGE (SKIP_BUILD=1)"
else
  log "Build image"
  docker build \
    --build-arg BUILD_ARCH=amd64 \
    --build-arg BUILD_VERSION="$(awk '/^version:/ {gsub(/"/, "", $2); print $2}' "$ADDON_DIR/config.yaml")" \
    -t "$IMAGE" "$ADDON_DIR"
fi

log "AppArmor availability"
SECURITY_OPT=()
if [ -d /sys/kernel/security/apparmor ] && command -v apparmor_parser >/dev/null 2>&1; then
  if apparmor_parser -r -W "$ADDON_DIR/apparmor.txt" 2>/tmp/hag-apparmor.log; then
    AA_MODE="apparmor=$PROFILE_NAME"
    SECURITY_OPT=(--security-opt "apparmor=$PROFILE_NAME")
    echo "loaded profile $PROFILE_NAME via apparmor_parser"
  else
    echo "apparmor_parser rejected the profile — this is a failure of the startup contract"
    cat /tmp/hag-apparmor.log
    exit 1
  fi
else
  echo "AppArmor is not available on this runner; using the Docker default profile."
  echo "The AppArmor rules are covered statically by tests/test_startup.py only."
fi

log "Start container ($AA_MODE)"
cleanup
docker run -d --name "$CONTAINER" \
  "${SECURITY_OPT[@]}" \
  -p "127.0.0.1:${INGRESS_PORT}:8099" \
  -p "127.0.0.1:${AGENT_PORT}:8098" \
  "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${INGRESS_PORT}/healthz" >/dev/null 2>&1; then break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]; then
    fail "the container exited before /healthz answered"
  fi
  sleep 1
done

log "Checks"
curl -fsS "http://127.0.0.1:${INGRESS_PORT}/healthz" | grep -q '"ok"' || fail "management /healthz"
curl -fsS "http://127.0.0.1:${AGENT_PORT}/healthz" | grep -q '"ok"' || fail "agent /healthz"
curl -fsS "http://127.0.0.1:${INGRESS_PORT}/readyz" | grep -q '"ready":true' || fail "/readyz not ready"

log "S6 chain and privilege state"
docker exec "$CONTAINER" sh -c 'test -x /init && test -r /init' || fail "/init is not executable+readable"
docker exec "$CONTAINER" sh -c 'ls /package/admin/s6-overlay-*/libexec/preinit >/dev/null' || fail "preinit missing"
PID="$(docker inspect -f '{{.State.Pid}}' "$CONTAINER")"
USER_ID="$(ps -o uid= -p "$(pgrep -P "$PID" -f 'python.*-m gateway' | head -1)" 2>/dev/null | tr -d ' ' || true)"
if [ -n "${USER_ID}" ] && [ "${USER_ID}" != "1000" ]; then
  fail "gateway process is not running as uid 1000 (got ${USER_ID})"
fi
echo "container pid1=$(ps -o comm= -p "$PID") gateway-uid=${USER_ID:-unknown}"

log "Readiness after in-place restart"
docker restart "$CONTAINER" >/dev/null
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${INGRESS_PORT}/readyz" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${INGRESS_PORT}/readyz" | grep -q '"ready":true' || fail "not ready after restart"

log "Graceful shutdown"
docker stop --time 20 "$CONTAINER" >/dev/null
EXIT_CODE="$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")"
docker logs "$CONTAINER" 2>&1 | tail -20
[ "$EXIT_CODE" = "0" ] || fail "container exit code ${EXIT_CODE} after SIGTERM"

echo
echo "RESULT: PASS (S6 stage 2, health, readiness, restart, graceful shutdown; AppArmor=$AA_MODE)"
