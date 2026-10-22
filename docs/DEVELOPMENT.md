# Development

## Requirements

* Python 3.11+. The image installs Alpine's `python3`; the exact version is not
  pinned and was not verified here because no Docker daemon was available.
* No Node.js, bundler, or external CDN is required: the panel is plain
  ES2020 + CSS served by the app under a strict CSP.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e "agent_gateway[dev]"
```

## Checks

```bash
.venv/bin/python -m pytest            # repository root also works (pytest.ini)
.venv/bin/ruff check agent_gateway/src agent_gateway/tests
.venv/bin/ruff format --check agent_gateway/src agent_gateway/tests
(cd agent_gateway && ../.venv/bin/mypy src)
.venv/bin/python -m pytest agent_gateway/tests -q
node --check agent_gateway/src/gateway/ui/static/app.js
```

The suite uses the real ASGI applications with a temporary data directory, a
cheap Argon2id cost, and `respx` in place of real Home Assistant or Proxmox
hosts. Nothing in the suite opens a socket to a real device.

## Panel smoke harness

```bash
cd tools/ui-smoke
npm install
npm test
```

This loads the real `index.html` and `app.js` in jsdom with a stubbed management
API, walks every section, and asserts that the expected requests are sent and no
runtime error occurs. It is optional, not part of CI, and no substitute for
looking at the panel in Home Assistant.

## Running locally

```bash
HAG_DATA_DIR=/tmp/gateway-data HAG_ADMIN_ALLOWED_IPS=127.0.0.1 \
HAG_REQUIRE_INGRESS_USER=0 \
  .venv/bin/python -m gateway
```

With `HAG_REQUIRE_INGRESS_USER=0` the management app accepts a local peer and
uses a development principal. Never run that way on a real installation. The
agent listener stays on `:8098`; create an agent in the panel and use the
returned bearer token.

## End-to-end smoke test

With a development vault password you can exercise the whole flow over real
sockets. Use a throwaway data directory; do not do this against a real `/data`.

```bash
HAG_DATA_DIR=/tmp/gw-live HAG_ADMIN_ALLOWED_IPS=127.0.0.1 \
HAG_REQUIRE_INGRESS_USER=0 HAG_INGRESS_PORT=18099 HAG_AGENT_PORT=18098 \
  .venv/bin/python -m gateway
```

Then, with a fake upstream on `127.0.0.1:8123`, `curl` the management API with
`X-Remote-User-Id` and `X-Gateway-Request: 1`, create a credential, connection,
and agent, and call `/api/v1/operations`. A write returns `202` with an
`approval_id`; approve it through `/api/admin/approvals/<id>/approve` and repeat
the identical request with `approval_id` to execute it once. `POST /mcp` with
`Accept: application/json, text/event-stream` speaks JSON-RPC with the same
bearer token.

## Layout

```
agent_gateway/src/gateway/
├── __main__.py        uvicorn servers, maintenance loop, shutdown
├── runtime.py         composition root; one object shared by both apps
├── settings.py        /data/options.json + HAG_* environment
├── storage/db.py      SQLite schema, settings store
├── vault/             Argon2id KDF, AES-256-GCM envelope, vault manager
├── auth/              tokens, rate limiting, agent service
├── policy/            document schema and the evaluation engine
├── operations.py      the single execution path for REST and MCP
├── journal/           Project Journal: service, models, handoff builder, MCP tools
├── approvals/         single-use, expiring, parameter-bound approvals
├── audit/             redacted audit log
├── connectors/        Home Assistant, Proxmox VE, SSH (known hosts, argv)
├── tools/             tool specs, parameter models, registry
├── api/               admin and agent FastAPI apps, middleware, schemas
├── mcp/server.py      Streamable HTTP MCP server over the same service
└── ui/static/         management panel (no build step)
```

Tests live in `agent_gateway/tests/`; the root `pytest.ini` mirrors the
`agent_gateway/pyproject.toml` pytest settings so both invocation styles work.

## Test coverage and gaps

Covered (253 tests): vault crypto/lifecycle/rotation/export, corrupt-file
handling, policy evaluation and deny rules, approvals (single use, expiry,
binding), redaction, settings parsing, the admin gate/CSRF/origin checks, body
limits, agent auth throttling and header handling, credential and connection
validation, TLS-off rejection, Home Assistant and Proxmox VE connectors against
mocked upstreams (including error mapping and credential non-leakage), blocked
service domains, the full agent approval round trip over REST, MCP JSON-RPC
sessions (initialize, tools/list, tools/call, approval shared with REST), SSH
host-key trust, add-on packaging metadata, and static UI accessibility
invariants.

| Module | Covers |
| --- | --- |
| `test_vault.py` | envelope crypto, lifecycle, rotation, export, corruption |
| `test_policy.py` | document validation, deny-by-default, constraints |
| `test_approvals.py` | single use, expiry, parameter binding |
| `test_redaction.py` | log/audit redaction |
| `test_admin_api.py` | management routes and validation |
| `test_agent_api.py` | auth, scopes, operations, approvals |
| `test_connectors.py` | HA REST, Proxmox VE, TLS, error mapping |
| `test_middleware.py` | admin gate, CSRF, body limits, throttling, headers |
| `test_mcp.py` | JSON-RPC session, catalogue parity, tool calls |
| `test_ssh_hostkeys.py` | known-hosts trust, templates, argv joining |
| `test_settings.py` | options/env parsing and validation |
| `test_packaging.py` | add-on metadata, ports, options, Dockerfile, AppArmor |
| `test_ui_assets.py` | design tokens, contrast, focus, ARIA, CSP-safety |
| `test_journal.py` | schema v3 migration/recovery, restart persistence, task transitions & dependencies, idempotency, search/pagination, claim vs verified separation, redaction, cross-agent authorization, policy/rate-limit enforcement, handoff generation, admin/agent/MCP parity |

Not covered, and why:

* Docker image build and add-on install — no Docker daemon or Supervisor in the
  development environment.
* Live Home Assistant, Proxmox VE, or SSH calls — no lab infrastructure is
  available; connectors are tested through mocks and unit-level checks only.
* MCP protocol conformance against an external client — sessions are exercised
  in-process over ASGI, but no third-party client or real network hop was used.
* Browser rendering, screen-reader behaviour, and visual regression — the static
  checks and the jsdom harness below cover structure and behaviour, not pixels.
  Manual review is still required.

## Conventions

* Secrets never enter logs, audit rows, HTTP responses, or tests as literals
  that resemble real credentials.
* New tools are added to `tools/registry.py` (one `ToolSpec`), which feeds REST,
  MCP, policy target extraction, and the audit summary automatically.
* New authorization logic belongs in `policy/`, never in a route handler.
* The UI has no dependencies; icons are inline SVG, and all user data is
  escaped before insertion.
