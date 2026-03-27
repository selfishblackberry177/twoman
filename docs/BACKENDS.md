# Backends

Twoman supports multiple public-host backend families.

Different host classes expose different runtime surfaces. The right public-host
implementation on a LiteSpeed/cPanel account with no app runtime is not the
same as the right implementation on a Passenger/Application Manager account.

## Shared Product Goal

These backend families still target the same product shape:

- local helper on the client machine
- public host remains in the path
- hidden reverse agent on the hidden server
- final path:
  `client -> public host -> hidden server -> internet`

## Backend Families

### `cpanel_litespeed_bridge`

Stable backend for hostile shared hosts with no real app runtime.

Implementation surface:

- PHP bootstrap/watchdog
- LiteSpeed `.htaccess [P]` reverse proxy
- localhost Python broker

Primary files:

- `host/runtime/http_broker_daemon.py`
- `host/app/bridge_runtime.php`
- `host/public/api.php`
- `host/public/health.php`
- `host/twoman.htaccess`
- `scripts/deploy_host.sh`

### `cloudlinux_node_selector`

Current best managed-host backend for CloudLinux accounts that expose a real
Node.js selector runtime.

Implementation surface:

- CloudLinux Node.js selector app
- Node broker on the public host
- HTTP transport with helper `ctl` on HTTP/2 and `data` on HTTP/1.1
- reverse hidden agent

Current status:

- end-to-end tunnel is proven on the audited managed host
- browser-grade smoke tests are passing on the current profile
- raw public WebSocket upgrade is still not assumed

Primary files:

- `host/node_selector/broker.js`
- `scripts/deploy_host_node_selector.sh`
- `tests/run_e2e_node_http.sh`
- `docs/CLPERSIST_METHOD.md`

### `passenger_python`

Backend track for cPanel hosts with a real Passenger/Application Manager
surface and Python runtime.

Implementation surface:

- Passenger-managed Python WSGI app
- no PHP in the hot path

Current status:

- application registration is proven on the audited CloudLinux host
- `/bridge/v2/health` is served by the Passenger Python app
- `LSAPI_CHILDREN=1` and `LSAPI_AVOID_FORK=1` are required baseline settings
- full Twoman broker traffic is not yet production-ready on this backend

Primary files:

- `host/passenger_python/broker_app.py`
- `host/passenger_python/passenger_wsgi.py`
- `backends/passenger_python/proof_app.py`
- `scripts/deploy_host_passenger.sh`

### `passenger_node`

Backend track for Passenger-capable hosts where Node.js and WebSocket upgrade
are truly available.

Why it matters:

- best fit for long-lived bidirectional control/data channels
- highest-upside path for reducing request churn

Current status:

- generic Passenger runtime is not the preferred Node path on the audited CloudLinux host
- proof app scaffold exists for fast validation

Primary files:

- `backends/passenger_node/app.js`
- `backends/passenger_node/package.json`

## Standard Maintenance Model

The standard model is:

- one product
- multiple backend implementations
- backend-specific deployment and validation

Backends may share code where safe, but they do not need to share everything.
If a host class needs a different client or broker model, that is acceptable.
