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

Optional reachability note:

- the hidden server may use a local upstream proxy such as WireProxy/WARP when
  it cannot reach the public host directly
- that changes only the hidden-to-host route; it does not change desktop or
  Android client requirements

## Backend Families

### `cpanel_runtime_bridge`

Control-plane/runtime bootstrap for hostile shared hosts. The public data path now
expects internal app-server integration instead of legacy rewrite proxying.

Implementation surface:

- PHP bootstrap/watchdog
- localhost Python broker

Primary files:

- `host/runtime/http_broker_daemon.py`
- `host/app/bridge_runtime.php`
- `host/public/api.php`
- `host/public/health.php`
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
- camouflage deploys should publish the same generated `404.html` at both the
  site slug and the root `public_html` when `TWOMAN_CAMOUFLAGE_SITE_ROOT_INDEX=true`
- camouflage deploys should also install matching `.htaccess` `ErrorDocument`
  rules so unknown Apache-served paths use that same themed 404 page

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
- `/health` is served relative to the configured Passenger base URI
- `LSAPI_CHILDREN=1` and `LSAPI_AVOID_FORK=1` are required baseline settings
- full broker traffic now expects Passenger-managed routing rather than `.htaccess` loopback proxying
- current Passenger deploy defaults use `down_wait_ms={"ctl":250,"data":250}`
- current Passenger deploy defaults keep `streaming_data_down_helper=false`
- reason: long-lived helper `data/down` streams can monopolize the public Passenger
  worker and starve helper control traffic on the audited CloudLinux host
- current deploy defaults size broker lanes as `ctl=4096/8/1ms`, `pri=32768/16/2ms`, and `bulk=262144/64/4ms`
- current public-host naming guidance is documented in
  `docs/HOST_APP_MAPPINGS.md`

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
