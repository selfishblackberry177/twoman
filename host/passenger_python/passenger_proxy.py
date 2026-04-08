#!/usr/bin/env python3

import contextlib
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))


def _absolute_path(value, default_path):
    candidate = str(value or "").strip()
    if not candidate:
        candidate = default_path
    return os.path.abspath(candidate)


CONFIG_PATH = _absolute_path(os.environ.get("TWOMAN_CONFIG_PATH"), os.path.join(CURRENT_DIR, "config.json"))
RUNTIME_DIR = _absolute_path(os.environ.get("TWOMAN_PASSENGER_RUNTIME_DIR"), os.path.join(CURRENT_DIR, "runtime"))
SOCKET_PATH = _absolute_path(os.environ.get("TWOMAN_PASSENGER_UNIX_SOCKET"), os.path.join(RUNTIME_DIR, "broker.sock"))
PID_PATH = _absolute_path(os.environ.get("TWOMAN_PASSENGER_DAEMON_PID"), os.path.join(RUNTIME_DIR, "broker.pid"))
LOCK_PATH = _absolute_path(os.environ.get("TWOMAN_PASSENGER_DAEMON_LOCK"), os.path.join(RUNTIME_DIR, "broker.lock"))
START_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("TWOMAN_PASSENGER_DAEMON_START_TIMEOUT_SECONDS", "8")))
CONNECT_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("TWOMAN_PASSENGER_PROXY_TIMEOUT_SECONDS", "35")))
DAEMON_HEALTH_CACHE_SECONDS = max(
    0.0,
    float(os.environ.get("TWOMAN_PASSENGER_DAEMON_HEALTH_CACHE_SECONDS", "2.0")),
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
LAST_DAEMON_CHECK_AT = 0.0
LAST_DAEMON_CHECK_PID = 0


def _allowed_observer_tokens():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return set()
    allowed = set()
    for key in ("client_tokens", "agent_tokens"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for entry in values:
            token = str(entry or "").strip()
            if token:
                allowed.add(token)
    return allowed


def _extract_bearer_token(environ):
    authorization = str(environ.get("HTTP_AUTHORIZATION", "") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _observer_path(environ):
    path = str(environ.get("PATH_INFO", "") or "/")
    normalized = "/" + path.lstrip("/")
    return (
        normalized.endswith("/health")
        or normalized.endswith("/pid")
        or normalized.endswith("/connect-probe")
        or normalized.endswith("/stream")
        or normalized.endswith("/upload_probe")
    )


def _probe_bearer_token():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return ""
    for key in ("client_tokens", "agent_tokens"):
        values = payload.get(key)
        if isinstance(values, list):
            for item in values:
                token = str(item or "").strip()
                if token:
                    return token
    return ""


def daemon_script_path():
    candidates = [
        os.environ.get("TWOMAN_PASSENGER_DAEMON_SCRIPT", "").strip(),
        os.path.join(CURRENT_DIR, "http_broker_daemon.py"),
        os.path.join(os.path.dirname(CURRENT_DIR), "runtime", "http_broker_daemon.py"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(CURRENT_DIR, "http_broker_daemon.py"))


def ensure_runtime_dir():
    os.makedirs(RUNTIME_DIR, exist_ok=True)


def read_pid():
    try:
        with open(PID_PATH, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return 0
    return int(value) if value.isdigit() else 0


def write_pid(pid):
    ensure_runtime_dir()
    with open(PID_PATH, "w", encoding="utf-8") as handle:
        handle.write(str(int(pid)))


def process_is_alive(pid):
    pid = int(pid)
    if pid < 2:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_pid(pid):
    pid = int(pid)
    if pid < 2:
        return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 1.5
    while time.time() < deadline:
        if not process_is_alive(pid):
            return
        time.sleep(0.1)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


def ping_daemon(timeout_seconds=1.5):
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        token = _probe_bearer_token()
        headers = [
            b"GET /health HTTP/1.1",
            b"Host: localhost",
        ]
        if token:
            headers.append(("Authorization: Bearer %s" % token).encode("ascii", "ignore"))
        headers.extend([b"Connection: close", b"", b""])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout_seconds)
            client.connect(SOCKET_PATH)
            client.sendall(b"\r\n".join(headers))
            chunks = []
            while True:
                chunk = client.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
        payload = b"".join(chunks)
        if b"200 OK" not in payload:
            return False
        return b"\"ok\": true" in payload or b"\"ok\":true" in payload
    except OSError:
        return False


def ensure_daemon_running(perform_healthcheck=True):
    global LAST_DAEMON_CHECK_AT, LAST_DAEMON_CHECK_PID
    ensure_runtime_dir()
    with open(LOCK_PATH, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        pid = read_pid()
        now_monotonic = time.monotonic()
        if process_is_alive(pid) and os.path.exists(SOCKET_PATH):
            if not perform_healthcheck:
                LAST_DAEMON_CHECK_PID = pid
                LAST_DAEMON_CHECK_AT = now_monotonic
                return
            if (
                pid == LAST_DAEMON_CHECK_PID
                and DAEMON_HEALTH_CACHE_SECONDS > 0.0
                and (now_monotonic - LAST_DAEMON_CHECK_AT) <= DAEMON_HEALTH_CACHE_SECONDS
            ):
                return
            if ping_daemon():
                LAST_DAEMON_CHECK_PID = pid
                LAST_DAEMON_CHECK_AT = now_monotonic
                return
        if process_is_alive(pid):
            stop_pid(pid)
        LAST_DAEMON_CHECK_PID = 0
        LAST_DAEMON_CHECK_AT = 0.0
        with contextlib.suppress(OSError):
            os.remove(SOCKET_PATH)
        command = [
            sys.executable,
            daemon_script_path(),
            "--unix-socket",
            SOCKET_PATH,
            "--config",
            CONFIG_PATH,
        ]
        python_path_entries = []
        for candidate in (
            CURRENT_DIR,
            os.path.dirname(CURRENT_DIR),
            os.getcwd(),
            os.environ.get("PYTHONPATH", ""),
        ):
            if not candidate:
                continue
            if candidate not in python_path_entries:
                python_path_entries.append(candidate)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(python_path_entries)
        with open(os.path.join(RUNTIME_DIR, "broker_stderr.log"), "ab", buffering=0) as devnull:
            process = subprocess.Popen(
                command,
                cwd=CURRENT_DIR,
                stdin=devnull,
                stdout=devnull,
                stderr=devnull,
                close_fds=True,
                start_new_session=True,
                env=environment,
            )
        write_pid(process.pid)
        deadline = time.time() + START_TIMEOUT_SECONDS
        while time.time() < deadline:
            if ping_daemon():
                LAST_DAEMON_CHECK_PID = process.pid
                LAST_DAEMON_CHECK_AT = time.monotonic()
                return
            if process.poll() is not None:
                raise RuntimeError("Passenger broker daemon exited during startup")
            time.sleep(0.1)
        raise RuntimeError("Passenger broker daemon did not become healthy")


def _connect_daemon_client():
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(CONNECT_TIMEOUT_SECONDS)
    client.connect(SOCKET_PATH)
    return client


def request_path_from_environ(environ):
    path = environ.get("PATH_INFO", "") or "/"
    query = environ.get("QUERY_STRING", "")
    if query:
        return "%s?%s" % (path, query)
    return path


def request_headers_from_environ(environ):
    headers = []
    for key, value in sorted(environ.items()):
        if not key.startswith("HTTP_"):
            continue
        name = key[5:].replace("_", "-")
        if name.lower() in HOP_BY_HOP_HEADERS:
            continue
        headers.append((name, str(value)))
    content_type = environ.get("CONTENT_TYPE")
    if content_type:
        headers.append(("Content-Type", str(content_type)))
    content_length = environ.get("CONTENT_LENGTH")
    if content_length:
        headers.append(("Content-Length", str(content_length)))
    headers.append(("Host", "localhost"))
    headers.append(("Connection", "close"))
    return headers


def request_body_from_environ(environ):
    length_text = str(environ.get("CONTENT_LENGTH", "") or "").strip()
    if not length_text:
        return b""
    try:
        length = max(0, int(length_text))
    except ValueError:
        return b""
    if length <= 0:
        return b""
    return environ["wsgi.input"].read(length)


def send_request_to_daemon(environ):
    ensure_daemon_running(perform_healthcheck=_observer_path(environ))
    method = str(environ.get("REQUEST_METHOD", "GET")).upper()
    raw_path = request_path_from_environ(environ)
    body = request_body_from_environ(environ)
    try:
        client = _connect_daemon_client()
    except OSError:
        ensure_daemon_running(perform_healthcheck=True)
        client = _connect_daemon_client()
    header_lines = ["%s: %s" % (name, value) for name, value in request_headers_from_environ(environ)]
    payload = (
        ("%s %s HTTP/1.1\r\n" % (method, raw_path)).encode("iso-8859-1")
        + ("\r\n".join(header_lines) + "\r\n\r\n").encode("iso-8859-1")
        + body
    )
    client.sendall(payload)
    with contextlib.suppress(OSError):
        client.shutdown(socket.SHUT_WR)
    if method == "GET" and "/down" in raw_path:
        # Long-poll and chunked down lanes may legitimately wait for frames before
        # sending response headers, so keep the socket blocking after connect.
        client.settimeout(None)
    return client, client.makefile("rb")


def parse_response_headers(response_file):
    status_line = response_file.readline().decode("iso-8859-1").strip()
    if not status_line.startswith("HTTP/"):
        raise RuntimeError("invalid daemon response")
    parts = status_line.split(" ", 2)
    status = "%s %s" % (parts[1], parts[2] if len(parts) > 2 else "OK")
    headers = []
    header_map = {}
    while True:
        line = response_file.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("iso-8859-1")
        name, value = decoded.split(":", 1)
        normalized_name = name.strip()
        normalized_value = value.strip()
        header_map[normalized_name.lower()] = normalized_value
        if normalized_name.lower() not in HOP_BY_HOP_HEADERS:
            headers.append((normalized_name, normalized_value))
    return status, headers, header_map


class ProxyBodyIterator(object):
    def __init__(self, client, response_file, header_map):
        self.client = client
        self.response_file = response_file
        self.remaining = None
        self.chunked = "chunked" in str(header_map.get("transfer-encoding", "")).lower()
        content_length = str(header_map.get("content-length", "")).strip()
        if content_length.isdigit():
            self.remaining = int(content_length)
        self.done = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.done:
            raise StopIteration
        if self.chunked:
            return self._next_chunk()
        if self.remaining is not None:
            return self._next_sized()
        return self._next_unsized()

    def close(self):
        with contextlib.suppress(Exception):
            self.response_file.close()
        with contextlib.suppress(Exception):
            self.client.close()
        self.done = True

    def _next_unsized(self):
        chunk = self.response_file.read(8192)
        if not chunk:
            self.close()
            raise StopIteration
        return chunk

    def _next_sized(self):
        if self.remaining <= 0:
            self.close()
            raise StopIteration
        chunk = self.response_file.read(min(8192, self.remaining))
        if not chunk:
            self.close()
            raise StopIteration
        self.remaining -= len(chunk)
        if self.remaining <= 0:
            self.done = True
        return chunk

    def _next_chunk(self):
        while True:
            line = self.response_file.readline()
            if not line:
                self.close()
                raise StopIteration
            size_text = line.split(b";", 1)[0].strip()
            if not size_text:
                continue
            size = int(size_text, 16)
            if size == 0:
                while True:
                    trailer = self.response_file.readline()
                    if not trailer or trailer in (b"\r\n", b"\n"):
                        break
                self.close()
                raise StopIteration
            payload = self.response_file.read(size)
            self.response_file.read(2)
            return payload


def _camouflage_html(status_code):
    """Generate a Persian HTML error page for unauthenticated probes."""
    candidate_paths = []
    explicit_path = str(os.environ.get("TWOMAN_CAMOUFLAGE_404_PATH", "")).strip()
    if explicit_path:
        candidate_paths.append(os.path.abspath(explicit_path))
    candidate_paths.extend(
        [
            os.path.join(RUNTIME_DIR, "camouflage_404.html"),
            os.path.join(CURRENT_DIR, "runtime", "camouflage_404.html"),
            os.path.join(CURRENT_DIR, "camouflage_404.html"),
            os.path.join(os.path.expanduser("~"), "public_html", "404.html"),
        ]
    )
    seen_paths = set()
    for candidate_path in candidate_paths:
        normalized_path = os.path.abspath(candidate_path)
        if normalized_path in seen_paths:
            continue
        seen_paths.add(normalized_path)
        try:
            if os.path.exists(normalized_path):
                with open(normalized_path, "rb") as handle:
                    return handle.read()
        except Exception:
            continue

    status_text = {403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 502: "Bad Gateway"}.get(status_code, "Error")
    html = (
        '<!doctype html>'
        '<html lang="fa" dir="rtl">'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>%d - %s</title>'
        '<style>'
        'body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;'
        'font-family:Vazirmatn,Tahoma,sans-serif;background:linear-gradient(135deg,#0F2027,#203A43,#2C5364);color:#dfe6e9}'
        '.c{text-align:center;padding:60px 40px;max-width:500px}'
        'h1{font-size:72px;margin:0 0 10px;opacity:.15}'
        'p{font-size:18px;opacity:.7;line-height:1.8}'
        'a{color:#00b894;text-decoration:none}'
        '</style></head>'
        '<body><div class="c">'
        '<h1>%d</h1>'
        '<p>\u0635\u0641\u062d\u0647\u200c\u0627\u06cc \u06a9\u0647 \u0628\u0647 \u062f\u0646\u0628\u0627\u0644 \u0622\u0646 \u0647\u0633\u062a\u06cc\u062f \u062f\u0631 \u062f\u0633\u062a\u0631\u0633 \u0646\u06cc\u0633\u062a \u06cc\u0627 \u062f\u0633\u062a\u0631\u0633\u06cc \u0634\u0645\u0627 \u0645\u062d\u062f\u0648\u062f \u0634\u062f\u0647 \u0627\u0633\u062a.</p>'
        '<p><a href="/">\u0628\u0627\u0632\u06af\u0634\u062a \u0628\u0647 \u0635\u0641\u062d\u0647 \u0627\u0635\u0644\u06cc</a></p>'
        '</div></body></html>'
    ) % (status_code, status_text, status_code)
    return html.encode("utf-8")


def application(environ, start_response):
    try:
        path = str(environ.get("PATH_INFO", "") or "/")
        if _observer_path(environ) or path == "/dump-log":
            token = _extract_bearer_token(environ)
            if token not in _allowed_observer_tokens():
                body = _camouflage_html(403)
                start_response(
                    "403 Forbidden",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(body))),
                        ("Cache-Control", "no-store"),
                    ],
                )
                return [body]

        if path == "/dump-log":
            log_path = os.path.join(os.path.dirname(CURRENT_DIR), "logs", "http-broker.log")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            else:
                body = b"Log not found"
            start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
            return [body]

        client, response_file = send_request_to_daemon(environ)
        status, headers, header_map = parse_response_headers(response_file)
        iterator = ProxyBodyIterator(client, response_file, header_map)
        start_response(status, headers)
        return iterator
    except Exception as error:
        body = _camouflage_html(502)
        start_response(
            "502 Bad Gateway",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]
