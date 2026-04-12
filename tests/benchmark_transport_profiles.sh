#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$ROOT/tests/tmp-benchmark-profiles"
BENCHMARK_BYTES="${TWOMAN_BENCHMARK_BYTES:-5242880}"
BENCHMARK_ATTEMPTS="${TWOMAN_BENCHMARK_ATTEMPTS:-6}"
BENCHMARK_SAMPLES="${TWOMAN_BENCHMARK_SAMPLES:-3}"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR" "$ROOT/tests/certs"

if [ ! -d "$ROOT/host/node_selector/node_modules/ws" ]; then
  (
    cd "$ROOT/host/node_selector"
    npm ci >/dev/null
  )
fi

cat > "$TMP_DIR/broker-config.json" <<'JSON'
{
  "client_tokens": ["test-client-token"],
  "agent_tokens": ["test-agent-token"],
  "binary_media_type": "image/webp",
  "peer_ttl_seconds": 90,
  "stream_ttl_seconds": 300,
  "max_lane_bytes": 16777216,
  "max_peer_buffered_bytes": 33554432,
  "base_uri": "/api/v1/telemetry",
  "websocket_public_enabled": true
}
JSON

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$ROOT/tests/certs/localhost-key.pem" \
  -out "$ROOT/tests/certs/localhost.pem" \
  -subj "/CN=localhost" \
  -days 1 >/dev/null 2>&1

cleanup() {
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
  rm -rf "$TMP_DIR" "$ROOT/tests/certs" >/dev/null 2>&1 || true
}
trap cleanup EXIT
PIDS=()

PORT=18095 TWOMAN_TRACE=0 TWOMAN_DEBUG_STATS=0 TWOMAN_CONFIG_PATH="$TMP_DIR/broker-config.json" node "$ROOT/host/node_selector/broker.js" \
  >"$TMP_DIR/broker.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/tests/origin_server.py" >"$TMP_DIR/origin.log" 2>&1 &
PIDS+=($!)

wait_for_port() {
  local host="$1"
  local port="$2"
  for _ in $(seq 1 50); do
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
  return 1
}

wait_for_listen_state() {
  local path="$1"
  for _ in $(seq 1 50); do
    if python3 - "$path" <<'PY' >/dev/null 2>&1
import json, sys
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
  return 1
}

read_listen_port() {
  local path="$1"
  local key="$2"
  python3 - "$path" "$key" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print(int(payload[sys.argv[2]]))
PY
}

wait_for_port 127.0.0.1 18095
wait_for_port 127.0.0.1 19090

run_profile() {
  local profile="$1"
  local profile_dir="$TMP_DIR/$profile"
  local helper_listen_state="$profile_dir/helper-listen-state.json"
  mkdir -p "$profile_dir"

  cat > "$profile_dir/agent.json" <<JSON
{
  "transport": "http",
  "transport_profile": "${profile}",
  "broker_base_url": "http://127.0.0.1:18095/api/v1/telemetry",
  "agent_token": "test-agent-token",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "/{lane}/{direction}",
  "health_template": "/health",
  "peer_id": "agent-${profile}",
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": true
}
JSON

  cat > "$profile_dir/helper.json" <<JSON
{
  "transport": "http",
  "transport_profile": "${profile}",
  "broker_base_url": "http://127.0.0.1:18095/api/v1/telemetry",
  "client_token": "test-client-token",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "/{lane}/{direction}",
  "health_template": "/health",
  "peer_id": "helper-${profile}",
  "listen_host": "127.0.0.1",
  "http_listen_port": 0,
  "socks_listen_port": 0,
  "listen_state_path": "${helper_listen_state}",
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": true
}
JSON

  python3 "$ROOT/hidden_server/agent.py" --config "$profile_dir/agent.json" >"$profile_dir/agent.log" 2>&1 &
  local agent_pid=$!
  python3 "$ROOT/local_client/helper.py" --config "$profile_dir/helper.json" >"$profile_dir/helper.log" 2>&1 &
  local helper_pid=$!
  PIDS+=("$agent_pid" "$helper_pid")

  wait_for_listen_state "$helper_listen_state"
  local helper_http_port
  helper_http_port="$(read_listen_port "$helper_listen_state" http_port)"
  wait_for_port 127.0.0.1 "$helper_http_port"

  python3 - "$profile" "$helper_http_port" "$BENCHMARK_BYTES" "$BENCHMARK_ATTEMPTS" "$BENCHMARK_SAMPLES" <<'PY'
import statistics
import subprocess
import sys
import time

profile = sys.argv[1]
port = int(sys.argv[2])
blob_bytes = int(sys.argv[3])
attempt_limit = int(sys.argv[4])
sample_target = int(sys.argv[5])
times = []
speeds = []
failures = 0
for attempt in range(attempt_limit):
    try:
        output = subprocess.check_output(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--max-time",
                "30",
                "--proxy",
                f"http://127.0.0.1:{port}",
                "--write-out",
                "%{time_total} %{speed_download}",
                f"http://127.0.0.1:19090/blob?bytes={blob_bytes}",
            ],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
    except subprocess.CalledProcessError as exc:
        failures += 1
        if attempt + 1 >= attempt_limit:
            raise
        time.sleep(0.5)
        continue
    time_total, speed_download = output.split()
    times.append(float(time_total))
    speeds.append(float(speed_download))
    if len(times) >= sample_target:
        break
if len(times) < sample_target:
    raise SystemExit(f"{profile} insufficient successful samples: {len(times)}/{sample_target}")
print(
    f"{profile} avg_time={statistics.mean(times):0.3f}s "
    f"avg_speed={statistics.mean(speeds)/1024/1024:0.2f}MiB/s "
    f"best_speed={max(speeds)/1024/1024:0.2f}MiB/s "
    f"failures={failures}"
)
PY

  kill "$helper_pid" "$agent_pid" >/dev/null 2>&1 || true
  wait "$helper_pid" >/dev/null 2>&1 || true
  wait "$agent_pid" >/dev/null 2>&1 || true
}

run_profile managed_host_http
run_profile managed_host_ws
