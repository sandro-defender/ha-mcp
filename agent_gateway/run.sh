#!/usr/bin/env bash
#
# Add-on CMD.  The HA base image owns ENTRYPOINT /init, so S6-Overlay starts
# this script as PID 1's stage-2 child.  It runs as root only long enough to
# claim the Supervisor-mounted /data volume and then replaces itself with the
# unprivileged `app` user.
#
# The HAG_* overrides exist for the local smoke harness
# (tools/startup-smoke) and for tests.  Home Assistant Supervisor never sets
# them, so the defaults below are what runs in the add-on.
set -euo pipefail

# The application reads /data/options.json directly.  Only infrastructure
# settings are accepted through the environment; secrets are never passed here.
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

DATA_DIR="${HAG_DATA_DIR:-/data}"
PYTHON_BIN="${HAG_PYTHON:-/opt/venv/bin/python}"
PYTHON_PATH="${HAG_PYTHONPATH:-/app/src}"
APP_USER="${HAG_APP_USER:-app:app}"

export PYTHONPATH="$PYTHON_PATH"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R "$APP_USER" "$DATA_DIR"
    exec su-exec "$APP_USER" "$PYTHON_BIN" -m gateway "$@"
fi

exec "$PYTHON_BIN" -m gateway "$@"
