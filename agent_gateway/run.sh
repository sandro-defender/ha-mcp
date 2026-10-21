#!/usr/bin/env bash
set -euo pipefail

# The application reads /data/options.json directly.  Only infrastructure
# settings are accepted through the environment; secrets are never passed here.
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/app/src

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R app:app /data
    exec su-exec app:app /opt/venv/bin/python -m gateway "$@"
fi

exec /opt/venv/bin/python -m gateway "$@"
