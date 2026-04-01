#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$ROOT/tests/tmp-passenger-proxy"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR" "$ROOT/tests/certs" "$TMP_DIR/runtime"

cat > "$TMP_DIR/broker-config.json" <<'JSON'
{
  "client_tokens": ["test-client-token"],
  "agent_tokens": ["test-agent-token"],
  "base_uri": "/api/v1/telemetry",
  "binary_media_type": "image/webp",
  "route_template": "/{lane}/{direction}",
  "health_template": "/health",
  "down_wait_ms": {
    "ctl": 100,
    "data": 100
  },
  "streaming_ctl_down_helper": false,
  "streaming_data_down_helper": false,
  "peer_ttl_seconds": 90,
  "stream_ttl_seconds": 300,
  "max_lane_bytes": 16777216,
  "max_peer_buffered_bytes": 33554432
}
JSON

cat > "$TMP_DIR/agent.json" <<'JSON'
{
  "broker_base_url": "http://127.0.0.1:18096/api/v1/telemetry",
  "agent_token": "test-agent-token",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "/{lane}/{direction}",
  "health_template": "/health",
  "peer_id": "agent-passenger-proxy-test",
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
JSON

cat > "$TMP_DIR/helper.json" <<'JSON'
{
  "broker_base_url": "http://127.0.0.1:18096/api/v1/telemetry",
  "client_token": "test-client-token",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "/{lane}/{direction}",
  "health_template": "/health",
  "peer_id": "helper-passenger-proxy-test",
  "listen_host": "127.0.0.1",
  "http_listen_port": 0,
  "socks_listen_port": 0,
  "listen_state_path": "helper-listen-state.json",
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
JSON

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$ROOT/tests/certs/localhost-key.pem" \
  -out "$ROOT/tests/certs/localhost.pem" \
  -subj "/CN=localhost" \
  -days 1 >/dev/null 2>&1

cleanup() {
  local exit_code="${1:-0}"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  sleep 0.5
  for pid in "${PIDS[@]:-}"; do
    kill -9 "$pid" >/dev/null 2>&1 || true
  done
  for pid in "${PIDS[@]:-}"; do
    wait "$pid" >/dev/null 2>&1 || true
  done
  if [ "$exit_code" -ne 0 ]; then
    for file in passenger-proxy.log agent.log helper.log origin.log tls.log; do
      if [ -f "$TMP_DIR/$file" ]; then
        echo "== $file ==" >&2
        cat "$TMP_DIR/$file" >&2
      fi
    done
  fi
  rm -rf "$TMP_DIR" "$ROOT/tests/certs" >/dev/null 2>&1 || true
}

trap 'cleanup $?' EXIT
PIDS=()

python3 - <<'PY' >"$TMP_DIR/passenger-proxy.log" 2>&1 &
import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

os.chdir("/home/shahab/dev/hobby/mintm")
os.environ["TWOMAN_CONFIG_PATH"] = "tests/tmp-passenger-proxy/broker-config.json"
os.environ["TWOMAN_PASSENGER_UNIX_SOCKET"] = "tests/tmp-passenger-proxy/runtime/broker.sock"
os.environ["TWOMAN_PASSENGER_DAEMON_PID"] = "tests/tmp-passenger-proxy/runtime/broker.pid"
os.environ["TWOMAN_PASSENGER_DAEMON_LOCK"] = "tests/tmp-passenger-proxy/runtime/broker.lock"
os.environ["TWOMAN_PASSENGER_DAEMON_SCRIPT"] = "host/runtime/http_broker_daemon.py"

from host.passenger_python.passenger_proxy import application


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


with make_server(
    "127.0.0.1",
    18096,
    application,
    server_class=ThreadingWSGIServer,
    handler_class=QuietHandler,
) as server:
    server.serve_forever()
PY
PIDS+=($!)

python3 "$ROOT/tests/origin_server.py" >"$TMP_DIR/origin.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/tests/tls_origin_server.py" >"$TMP_DIR/tls.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/hidden_server/agent.py" --config "$TMP_DIR/agent.json" >"$TMP_DIR/agent.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/local_client/helper.py" --config "$TMP_DIR/helper.json" >"$TMP_DIR/helper.log" 2>&1 &
PIDS+=($!)

wait_for_port() {
  local host="$1"
  local port="$2"
  local label="$3"
  for _ in $(seq 1 60); do
    if python3 - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

sock = socket.socket()
sock.settimeout(0.2)
try:
    sock.connect((sys.argv[1], int(sys.argv[2])))
finally:
    sock.close()
PY
    then
      return 0
    fi
    sleep 0.2
  done
  echo "Timed out waiting for $label on $host:$port" >&2
  return 1
}

wait_for_listen_state() {
  local path="$1"
  for _ in $(seq 1 60); do
    if python3 - "$path" <<'PY' >/dev/null 2>&1
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert int(payload["http_port"]) > 0
assert int(payload["socks_port"]) > 0
PY
    then
      return 0
    fi
    sleep 0.2
  done
  echo "Timed out waiting for helper listen state: $path" >&2
  return 1
}

read_listen_port() {
  local path="$1"
  local key="$2"
  python3 - "$path" "$key" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print(int(payload[sys.argv[2]]))
PY
}

wait_for_port 127.0.0.1 18096 passenger-proxy
wait_for_port 127.0.0.1 19090 origin
wait_for_port 127.0.0.1 19443 tls-origin
wait_for_listen_state "$TMP_DIR/helper-listen-state.json"
HELPER_HTTP_PORT="$(read_listen_port "$TMP_DIR/helper-listen-state.json" http_port)"
HELPER_SOCKS_PORT="$(read_listen_port "$TMP_DIR/helper-listen-state.json" socks_port)"
wait_for_port 127.0.0.1 "$HELPER_HTTP_PORT" http-helper
wait_for_port 127.0.0.1 "$HELPER_SOCKS_PORT" socks-helper

curl --fail --silent --show-error \
  -H "Authorization: Bearer test-client-token" \
  "http://127.0.0.1:18096/api/v1/telemetry/health" \
  > "$TMP_DIR/health.json"

python3 - "$TMP_DIR/health.json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
stats = payload["stats"]
assert stats["log_paths"]["runtime"].endswith("http-broker.log"), stats
assert stats["log_paths"]["events"].endswith("http-broker-events.ndjson"), stats
PY

curl --fail --silent --show-error \
  --socks5-hostname "127.0.0.1:${HELPER_SOCKS_PORT}" \
  "http://127.0.0.1:19090/socks-test?via=socks" \
  > "$TMP_DIR/socks.json"

python3 - "$TMP_DIR/socks.json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["path"] == "/socks-test?via=socks", payload
assert payload["method"] == "GET", payload
PY

curl --fail --silent --show-error --insecure \
  --proxy "http://127.0.0.1:${HELPER_HTTP_PORT}" \
  "https://127.0.0.1:19443/secure-test?via=http" \
  > "$TMP_DIR/http.txt"

grep -q 'secure:/secure-test?via=http' "$TMP_DIR/http.txt"

echo "TWOMAN PASSENGER PROXY E2E OK"
