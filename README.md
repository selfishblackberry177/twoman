# Twoman

<p align="center">
  <img src="docs/assets/logo-homepage.png" alt="Twoman logo" width="160" />
</p>

Twoman is a host-preserving relay for shared cPanel hosting and managed
CloudLinux app runtimes.

Final path:

`app -> local helper -> public host broker -> hidden reverse agent -> internet`

The public host stays in the live path. The hidden server performs outbound
internet access. The local helper exposes normal HTTP and SOCKS5 proxies so
apps like Telegram and browsers can use the system.

Compatibility note:
- the live bridge path remains `/bridge/v2`
- that path name is kept for wire compatibility with existing deployments, not because this repository ships multiple public versions

## Status

This repository contains the current public implementation.

What it is good at:
- Telegram and other lighter interactive traffic
- SOCKS5 and HTTP proxy access through a localhost helper
- shared-host deployments where the public host must remain in-path
- managed-host deployments where a real Node app runtime is available

What it is not:
- a full-speed VPN replacement
- a general-purpose high-throughput tunnel on hostile shared hosting

## Repository Layout

- `twoman_protocol.py`: framed protocol and lane definitions
- `twoman_transport.py`: shared public-leg transport
- `local_client/helper.py`: local HTTP + SOCKS5 helper
- `hidden_server/agent.py`: hidden reverse agent
- `android-client/`: Android client with saved profiles, share/import profile text, proxy mode, and VPN mode
- `desktop_client/`: cross-platform desktop TUI with saved profiles, connect/disconnect, and authenticated external SOCKS sharing
- `host/node_selector/broker.js`: CloudLinux Node selector broker for managed-host deployments
- `host/runtime/http_broker_daemon.py`: asyncio broker for bridge-style cPanel deployments
- `host/app/bridge_runtime.php`: PHP bootstrap that starts and supervises the bridge broker
- `host/public/api.php`: public health/bootstrap endpoint for bridge-style deployments
- `host/twoman.htaccess`: LiteSpeed reverse-proxy rules for bridge-style deployments
- `tests/run_e2e.sh`: local smoke test
- `tests/run_e2e_node_http.sh`: local smoke test for the Node selector broker

Backend families:

- `backends/cpanel_litespeed_bridge`
- `backends/passenger_python`
- `backends/passenger_node`

Backend overview: [docs/BACKENDS.md](docs/BACKENDS.md)
Android client notes: [android-client/README.md](android-client/README.md)
Desktop client notes: [desktop_client/README.md](desktop_client/README.md)

## Architecture

Twoman uses:
- external helper lanes: `ctl` + `data`
- external agent lanes: `ctl` + `data`
- internal scheduler classes: `ctl`, `pri`, `bulk`

Key design points:
- helper downlinks are streamed HTTP/1.1 responses
- helper uplinks are bounded POST batches
- the broker assigns agent-side stream IDs and scopes helper streams by session
- public authentication uses bearer tokens in `X-Relay-Token`

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Backend Strategy

Twoman is one product with multiple public-host backend families.

Today:

- the stable fallback backend is the cPanel LiteSpeed bridge backend
- the current best managed-host backend is the CloudLinux Node selector path
- the Passenger Python backend is an experimental compatibility track
- the generic Passenger Node backend remains a proof track for hosts where it genuinely works

This is intentional. Different host classes expose different runtime models,
and Twoman does not force them into one fragile host implementation.

## Quick Start

### One-command deployment

Twoman now ships with repo-level scripts for each side:

- `scripts/deploy_host.sh`: uploads the cPanel host files, writes `host/app/config.php`, restarts the broker, and verifies health
- `scripts/deploy_hidden_server.sh`: uploads the hidden agent files, writes `config.json`, installs `systemd` units, enables the watchdog, and restarts the agent
- `scripts/start_client.sh`: writes `local_client/config.json` if needed and starts the local helper in the foreground

Manual fallback:
- [docs/MANUAL_DEPLOY.md](docs/MANUAL_DEPLOY.md)

### 1. Deploy the cPanel host

```bash
export TWOMAN_CPANEL_BASE_URL='https://your-host.example:2083'
export TWOMAN_CPANEL_USERNAME='cpanel-user'
export TWOMAN_CPANEL_PASSWORD='cpanel-password'
export TWOMAN_CPANEL_HOME='/home/cpanel-user'
export TWOMAN_PUBLIC_ORIGIN='https://your-host.example'
export TWOMAN_PUBLIC_BASE_PATH='/twoman'
export TWOMAN_CLIENT_TOKEN='replace-with-client-token'
export TWOMAN_AGENT_TOKEN='replace-with-agent-token'

./scripts/deploy_host.sh
```

### 2. Deploy the hidden server

```bash
export TWOMAN_SERVER_HOST='<hidden-server-host>'
export TWOMAN_SERVER_USER='root'
export TWOMAN_SERVER_PASSWORD='server-password'
export TWOMAN_SERVER_DIR='/opt/twoman'
export TWOMAN_BROKER_BASE_URL='https://your-host.example/twoman/bridge/v2'
export TWOMAN_AGENT_TOKEN='replace-with-agent-token'
export TWOMAN_AGENT_PEER_ID='agent-main'

./scripts/deploy_hidden_server.sh
```

### 3. Start the local helper

```bash
export TWOMAN_BROKER_BASE_URL='https://your-host.example/twoman/bridge/v2'
export TWOMAN_CLIENT_TOKEN='replace-with-client-token'
./scripts/start_client.sh
```

This starts the helper in the foreground. `Ctrl+C` stops it cleanly.
By default, the helper writes rotating client logs to `local_client/logs/helper.log`.
Override that location with `TWOMAN_LOG_PATH=/path/to/helper.log`.

Default helper ports:
- HTTP proxy: `127.0.0.1:18092`
- SOCKS5 proxy: `127.0.0.1:11092`

## Requirements

- cPanel host with LiteSpeed `.htaccess` reverse proxy support to `127.0.0.1`
- Python 3 on the host for `host/runtime/http_broker_daemon.py`
- Python 3.9+ recommended for helper and hidden agent
- `curl` for `scripts/deploy_host.sh`
- `ssh` and `scp` for `scripts/deploy_hidden_server.sh`
- `sshpass` only if you use password-based hidden-server deploys
- `pip install -r requirements.txt`

### 4. Verify

Bridge health:

```bash
curl -H 'X-Relay-Token: YOUR_CLIENT_TOKEN' \
  'https://your-host.example/twoman/api.php?action=health'
```

SOCKS egress:

```bash
curl --socks5-hostname 127.0.0.1:11092 https://api.ipify.org
```

HTTP egress:

```bash
curl --proxy http://127.0.0.1:18092 https://api.ipify.org
```

Expected result: the origin IP should be the hidden server, not the local client.

## Development

Run the local smoke test:

```bash
tests/run_e2e.sh
```

Enable verbose tracing temporarily:

```bash
TWOMAN_TRACE=1 python3 hidden_server/agent.py --config hidden_server/config.json
```

Tracing is off by default to avoid log growth on production hosts.

Client crash and runtime logs:

- `scripts/start_client.sh` writes a rotating helper log to `local_client/logs/helper.log`
- `TWOMAN_LOG_PATH` overrides the helper log location
- uncaught exceptions and Python fault dumps are appended to the same helper log

## Operational Notes

- LiteSpeed reverse proxying to `127.0.0.1` is the core shared-host trick.
- The broker is the hot-path component on the cPanel host. PHP is only bootstrap/supervision.
- Browser workloads are materially heavier than Telegram or one-shot `curl` probes.
- SOCKS is generally the better app-facing surface than the HTTP proxy for real-world use.
- The broker now enforces per-session safety limits for concurrent streams, open-rate bursts, and queued bytes.
- The hidden agent watchdog restarts the service before file descriptors or `CLOSE-WAIT` sockets can pile up into an outage.

## Security

- Do not commit real `client_token` or `agent_token` values.
- Do not commit `host/app/config.php`.
- Do not commit runtime data under `host/storage/`.
- Rotate tokens if they have ever been shared.
