ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG BUILD_VERSION=0.4.0

LABEL \
  io.hass.name="Home Assistant Agent Gateway" \
  io.hass.description="Policy-controlled gateway for AI agents to operate Home Assistant, Proxmox VE, and restricted SSH targets" \
  io.hass.arch="${BUILD_ARCH}" \
  io.hass.type="addon" \
  io.hass.version="${BUILD_VERSION}" \
  org.opencontainers.image.source="https://github.com/sandro-defender/ha-mcp" \
  org.opencontainers.image.version="${BUILD_VERSION}"

# Shutdown budget.  At the end of the shutdown sequence s6 sends TERM to the
# remaining processes (including the CMD, which is where the gateway runs) and
# waits this long before KILL.  The default 3000 ms is too tight for uvicorn to
# drain two listeners; 10 s stays inside the 10 s default stop timeout that
# Docker and the Supervisor use, because the app exits on its own well before
# the budget is exhausted.
#
# Note: S6_CMD_RECEIVE_SIGNALS is deliberately NOT used.  s6-overlay implements
# it with `kill -s TERM -- <pid>` in its CMDSIG helper, and busybox `kill` (the
# Alpine shell builtin) rejects `--` ("invalid number '--'"), so the signal is
# never delivered and the container hangs.  Measured on the built image.
ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    S6_KILL_GRACETIME=10000

# Python runtime, privilege dropping, and build dependencies for any
# dependency without a musllinux wheel.  The build dependencies are removed in
# the same layer to keep the runtime image lean.
RUN apk add --no-cache python3 py3-pip su-exec \
    && apk add --no-cache --virtual .build-deps build-base libffi-dev openssl-dev python3-dev cargo \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /app
COPY pyproject.toml README.md CHANGELOG.md ./
COPY src ./src
RUN /opt/venv/bin/pip install --no-cache-dir .

RUN apk del .build-deps \
    && addgroup -S -g 1000 app \
    && adduser -S -D -H -u 1000 -G app app \
    && chown -R app:app /app /opt/venv

COPY run.sh /run.sh
RUN chmod 0755 /run.sh

# Startup contract.  Fail the build (instead of the user's container) if the
# S6-Overlay entrypoint chain is missing, or lost the read/execute bits that
# the kernel + /bin/sh need for `#!/bin/sh` scripts.  A non-readable script is
# what produces:
#   /bin/sh: can't open '/init': Permission denied
#   s6-overlay-suexec: fatal: child failed with exit code 2
# The AppArmor profile in apparmor.txt grants the matching rix rules; neither
# this check nor the profile chmods or replaces /init.
RUN set -eu; \
    test -x /init && test -r /init; \
    preinit="$(ls -d /package/admin/s6-overlay-*/libexec/preinit)"; \
    test -x "$preinit" && test -r "$preinit"; \
    stage0="$(dirname "$preinit")/stage0"; \
    test -x "$stage0" && test -r "$stage0"; \
    su-exec app:app /opt/venv/bin/python -c \
        "import argon2, asyncssh, cryptography, fastapi, gateway, uvicorn; print('gateway imports ok')"

# The entrypoint starts as root only to fix ownership of the Supervisor-mounted
# /data volume, then immediately drops to the unprivileged app user.
EXPOSE 8099 8098

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/opt/venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/healthz', timeout=3).read()"]

CMD ["/run.sh"]
