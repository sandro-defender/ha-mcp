ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG BUILD_VERSION=0.3.1

LABEL \
  io.hass.name="Home Assistant Agent Gateway" \
  io.hass.description="Policy-controlled gateway for AI agents to operate Home Assistant, Proxmox VE, and restricted SSH targets" \
  io.hass.arch="${BUILD_ARCH}" \
  io.hass.type="addon" \
  io.hass.version="${BUILD_VERSION}" \
  org.opencontainers.image.source="https://github.com/sandro-defender/ha-mcp" \
  org.opencontainers.image.version="${BUILD_VERSION}"

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

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

# The entrypoint starts as root only to fix ownership of the Supervisor-mounted
# /data volume, then immediately drops to the unprivileged app user.
EXPOSE 8099 8098

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/opt/venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/healthz', timeout=3).read()"]

CMD ["/run.sh"]
