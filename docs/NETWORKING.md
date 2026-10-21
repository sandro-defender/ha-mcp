# Networking and client setup

## Listeners

| Port | Surface | Exposure in `config.yaml` |
| --- | --- | --- |
| `8099` | Management UI + `/api/admin` | Ingress only; the Supervisor proxies it and the app accepts only peer `172.30.32.2` |
| `8098` | Agent REST `/api/v1`, MCP `/mcp`, `/healthz` | `ports: 8098/tcp: null` → **not published** |

Publishing `8098` is a deliberate administrator action: open the add-on's
**Network** section and assign a host port. Do it only on a trusted LAN, behind
a VPN, or behind a TLS-terminating reverse proxy.

`127.0.0.1` on the Windows PC is not the Home Assistant host. Point the agent at
the Home Assistant IP address or hostname (for example
`http://homeassistant.local:8098`).

## TLS

The management listener stays plain HTTP because Ingress terminates TLS.

The agent listener serves HTTPS when both options are set:

```yaml
tls_certfile: /ssl/fullchain.pem
tls_keyfile: /ssl/privkey.pem
```

`/ssl` is mounted read-only (`map: ssl`). Certificate paths are validated at
startup and the add-on refuses to start if only one path is provided or a file
is missing. There is no "insecure" switch; this applies to the inbound listener.
Upstream TLS verification is always on (a connection with `verify_tls: false`
is rejected), with optional custom CAs stored in the vault.

## Browser clients (host and origin allowlists)

By default no browser origin is allowed, so a request carrying an `Origin`
header is rejected (`403 origin_rejected`). Server-side agents (curl, Python,
MCP stdio bridges) do not send `Origin` and work out of the box.

To allow a browser client, set in the add-on environment:

```yaml
HAG_AGENT_ALLOWED_ORIGINS: "https://agent-console.example"
HAG_AGENT_ALLOWED_HOSTS: "gateway.example"
```

Origins are matched exactly after stripping a trailing slash; `Host` may include
the port. CORS is not authentication: the bearer token is still required and
still must not be placed in a URL.

## Example requests

```bash
# health
curl -sS http://homeassistant.local:8098/healthz

# list the tools this agent may call
curl -sS http://homeassistant.local:8098/api/v1/tools \
  -H "Authorization: Bearer $HAG_TOKEN"

# read one entity
curl -sS http://homeassistant.local:8098/api/v1/operations \
  -H "Authorization: Bearer $HAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool":"ha.get_state","connection_id":"<id>","params":{"entity_id":"light.kitchen"}}'
```

A write returns `202` with `approval_id`; approve it in the panel, then repeat
the identical request with `"approval_id": "…"` added. Any change to the
parameters invalidates the approval.

MCP clients connect to `http(s)://<host>:8098/mcp` using Streamable HTTP with an
`Authorization: Bearer` header. The endpoint is stateless: each request must be
self-contained.

## Firewall guidance

* Leave `8098` unpublished unless an agent runs off-host.
* Prefer WireGuard/Tailscale over exposing the port to the LAN.
* If you must expose it on the LAN, terminate TLS (add-on option or reverse
  proxy) and restrict the source address at the firewall.
* Never expose `8098` to the public internet.
* Management (`8099`) should never be published; use the sidebar panel.
