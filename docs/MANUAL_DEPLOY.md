# Manual Deployment

Use this guide when the scripted install path fails or when you want to inspect each step manually.

## 1. cPanel host

Target layout under your public base path, for example `/home/<user>/public_html/twoman`:

- `api.php`
- `health.php`
- `.htaccess`
- `app/bootstrap.php`
- `app/bridge_runtime.php`
- `app/config.php`
- `runtime/http_broker_daemon.py`
- `runtime/logs/`
- `storage/`
- `offload/`

### Files to upload

Upload these repo files to the matching host paths:

- `host/public/api.php` -> `<public>/api.php`
- `host/public/health.php` -> `<public>/health.php`
- `host/twoman.htaccess` -> `<public>/.htaccess`
- `host/app/bootstrap.php` -> `<public>/app/bootstrap.php`
- `host/app/bridge_runtime.php` -> `<public>/app/bridge_runtime.php`
- `host/runtime/http_broker_daemon.py` -> `<public>/runtime/http_broker_daemon.py`

### Create host config

Create `<public>/app/config.php` from `host/app/config.sample.php`.

Set at least:

- `storage_path`
- `public_base_path`
- `client_tokens`
- `agent_tokens`
- `bridge_local_port`
- `bridge_max_streams_per_peer_session`
- `bridge_max_open_rate_per_peer_session`
- `bridge_open_rate_window_seconds`
- `bridge_max_peer_buffered_bytes`

Minimal example:

```php
<?php

return [
    'storage_path' => '/home/USER/public_html/twoman/storage',
    'public_base_path' => '/twoman',
    'offload_relative_path' => 'offload',
    'offload_ttl_seconds' => 3600,
    'client_tokens' => ['replace-with-client-token'],
    'agent_tokens' => ['replace-with-agent-token'],
    'reverse_keys' => ['unused-public-release-placeholder'],
    'max_request_body_bytes' => 8 * 1024 * 1024,
    'poll_wait_ms' => 20000,
    'reverse_wait_ms' => 45000,
    'poll_sleep_us' => 200000,
    'job_lease_seconds' => 30,
    'bridge_local_port' => 18093,
    'bridge_session_ttl_seconds' => 300,
    'bridge_max_agent_idle_seconds' => 90,
    'bridge_max_streams_per_peer_session' => 256,
    'bridge_max_open_rate_per_peer_session' => 120,
    'bridge_open_rate_window_seconds' => 10,
    'bridge_max_peer_buffered_bytes' => 32 * 1024 * 1024,
];
```

### Start the broker manually

Open:

```text
https://your-host.example/twoman/api.php?action=health
```

with a valid `X-Relay-Token` header from either the client token or the agent token.

Expected result:

```json
{"ok":true,...}
```

If the broker is not running, `api.php?action=health` will attempt to start it.

## 2. Hidden server

Copy these files to the hidden server, for example under `/opt/twoman`:

- `twoman_protocol.py`
- `twoman_transport.py`
- `hidden_server/agent.py`
- `hidden_server/agent_watchdog.py`
- `hidden_server/install_watchdog.sh`
- `hidden_server/systemd/twoman-agent-watchdog.service`
- `hidden_server/systemd/twoman-agent-watchdog.timer`

### Create hidden-server config

Create `/opt/twoman/config.json` from `hidden_server/config.sample.json`.

Example:

```json
{
  "broker_base_url": "https://your-host.example/twoman/bridge/v2",
  "agent_token": "replace-with-agent-token",
  "http_timeout_seconds": 30,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "peer_id": "agent-main",
  "http2_enabled": {
    "ctl": true,
    "data": false
  }
}
```

### Start agent directly

```bash
python3 /opt/twoman/agent.py --config /opt/twoman/config.json
```

### Install as a service

Create `/etc/systemd/system/twoman-agent.service`:

```ini
[Unit]
Description=Twoman hidden agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/twoman
Environment=PYTHONUNBUFFERED=1
Environment=TWOMAN_TRACE=0
ExecStart=/usr/bin/python3 /opt/twoman/agent.py --config /opt/twoman/config.json
Restart=always
RestartSec=2
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now twoman-agent.service
```

### Install watchdog manually

Update `hidden_server/systemd/twoman-agent-watchdog.service` if your service name differs from `twoman-agent.service`, then:

```bash
sudo install -m 0755 /opt/twoman/agent_watchdog.py /opt/twoman/agent_watchdog.py
sudo install -m 0644 /opt/twoman/twoman-agent-watchdog.service /etc/systemd/system/twoman-agent-watchdog.service
sudo install -m 0644 /opt/twoman/twoman-agent-watchdog.timer /etc/systemd/system/twoman-agent-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now twoman-agent-watchdog.timer
sudo systemctl start twoman-agent-watchdog.service
```

## 3. Local helper

Create `local_client/config.json` from `local_client/config.sample.json`.

Example:

```json
{
  "broker_base_url": "https://your-host.example/twoman/bridge/v2",
  "client_token": "replace-with-client-token",
  "listen_host": "127.0.0.1",
  "http_listen_port": 18092,
  "socks_listen_port": 11092,
  "http_timeout_seconds": 30,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
```

Install dependency:

```bash
python3 -m pip install -r requirements.txt
```

Run helper:

```bash
python3 local_client/helper.py --config local_client/config.json
```

Stop helper:

- foreground run: `Ctrl+C`
- background run: `kill <pid>`

## 4. Verification

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

Expected result:

- the returned IP should be the hidden server

## Troubleshooting

If the host script path fails:

- verify file upload permissions in cPanel File Manager
- verify `.htaccess` proxy rules are present under the public base path
- verify `/bin/python3` exists on the host
- verify `api.php?action=health` returns `ok`

If the hidden server fails:

- `systemctl status twoman-agent.service`
- `journalctl -u twoman-agent.service -n 100 --no-pager`
- `systemctl status twoman-agent-watchdog.timer`
- `cat /proc/$(systemctl show -p MainPID --value twoman-agent.service)/limits | grep 'Max open files'`

If the local helper fails:

- verify `python3 -m pip install -r requirements.txt`
- verify the ports are free:
  - `18092`
  - `11092`
- run the helper directly in the foreground to see the error
