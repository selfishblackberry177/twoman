#!/usr/bin/env python3

import argparse
import asyncio
import contextlib
import faulthandler
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import signal
import socket
import struct
import sys
import threading
import time
import urllib.parse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from twoman_protocol import (
    Frame,
    FLAG_DATA_BULK,
    FRAME_DATA,
    FRAME_DNS_FAIL,
    FRAME_DNS_QUERY,
    FRAME_DNS_RESPONSE,
    FRAME_FIN,
    FRAME_OPEN,
    FRAME_OPEN_FAIL,
    FRAME_OPEN_OK,
    FRAME_RST,
    FRAME_WINDOW,
    LANE_BULK,
    LANE_CTL,
    LANE_PRI,
    MODE_TCP,
    make_error_payload,
    make_open_payload,
    parse_error_payload,
    random_peer_id,
)
from runtime_diagnostics import DurableEventRecorder, event_log_path, event_log_settings, runtime_log_settings
from twoman_dns import (
    DNS_TYPE_AAAA,
    DNS_TYPE_HTTPS,
    dns_query_cache_key,
    dns_question_type,
    dns_transaction_id,
    expire_dns_cache,
    format_error_summary,
    make_dns_query_frame_payload,
    synthesize_empty_dns_response,
    vpn_dns_proxy_ip,
    vpn_dns_servers,
    vpn_filter_aaaa,
    with_dns_transaction_id,
)
from twoman_transport import create_transport


INITIAL_WINDOW = 256 * 1024
PRI_LIMIT = 64 * 1024
READ_CHUNK = 16 * 1024
WINDOW_FLUSH_BYTES = 16 * 1024
WINDOW_FLUSH_DELAY = 0.005
SMALL_WRITE_BYTES = 8 * 1024
MAX_RECV_REORDER_BYTES = 1024 * 1024
DNS_QUERY_TIMEOUT = max(0.5, float(os.environ.get("TWOMAN_DNS_QUERY_TIMEOUT", "2.5")))
DNS_CACHE_TTL_SECONDS = max(1.0, float(os.environ.get("TWOMAN_DNS_CACHE_TTL_SECONDS", "20.0")))
DNS_CACHE_MAX_ENTRIES = max(32, int(os.environ.get("TWOMAN_DNS_CACHE_MAX_ENTRIES", "256")))
DNS_MAX_INFLIGHT = max(1, int(os.environ.get("TWOMAN_DNS_MAX_INFLIGHT", "8")))
SHUTDOWN_STREAM_RESET_GRACE_SECONDS = max(
    0.0,
    float(os.environ.get("TWOMAN_SHUTDOWN_STREAM_RESET_GRACE_SECONDS", "0.2")),
)

TRACE_ENABLED = os.environ.get("TWOMAN_TRACE", "").strip().lower() in ("1", "true", "yes", "on", "debug", "verbose")
LOGGER = logging.getLogger("twoman.helper")
RUNTIME_LOG_PATH = ""
EVENT_LOG_PATH = ""
FAULT_LOG_HANDLE = None
PID_FILE_PATH = ""
LISTEN_STATE_PATH = ""
EVENT_RECORDER = None
ACTIVE_LOOP = None
ACTIVE_STOP_EVENT = None
ACTIVE_LOOP_LOCK = threading.Lock()


def trace(message):
    if not TRACE_ENABLED:
        return
    if LOGGER.handlers:
        LOGGER.debug(message)
        return
    sys.stderr.write("[helper] %s\n" % message)
    sys.stderr.flush()


def record_event(kind, **fields):
    if EVENT_RECORDER is None:
        return
    try:
        EVENT_RECORDER.record(kind, component="helper", **fields)
    except Exception:
        LOGGER.exception("helper event log write failed kind=%s", kind)


def resolve_log_path(config_path, config):
    env_log_path = os.environ.get("TWOMAN_LOG_PATH", "").strip()
    if env_log_path:
        return os.path.abspath(env_log_path)
    configured_log_path = str(config.get("log_path", "")).strip()
    if configured_log_path:
        if os.path.isabs(configured_log_path):
            return configured_log_path
        return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(config_path)), configured_log_path))
    env_log_dir = os.environ.get("TWOMAN_LOG_DIR", "").strip()
    if env_log_dir:
        log_dir = os.path.abspath(env_log_dir)
    else:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(config_path)), "logs")
    return os.path.join(log_dir, "helper.log")


def configure_runtime_logging(config_path, config):
    global RUNTIME_LOG_PATH, FAULT_LOG_HANDLE
    if LOGGER.handlers:
        return
    RUNTIME_LOG_PATH = resolve_log_path(config_path, config)
    settings = runtime_log_settings(config)
    log_dir = os.path.dirname(RUNTIME_LOG_PATH)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    logger_level = logging.DEBUG if TRACE_ENABLED else logging.INFO
    LOGGER.setLevel(logger_level)
    LOGGER.propagate = False

    file_handler = RotatingFileHandler(
        RUNTIME_LOG_PATH,
        maxBytes=settings["max_bytes"],
        backupCount=settings["backup_count"],
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(file_handler)

    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.DEBUG if TRACE_ENABLED else logging.WARNING)
        console_handler.setFormatter(logging.Formatter("[helper] %(levelname)s %(message)s"))
        LOGGER.addHandler(console_handler)

    FAULT_LOG_HANDLE = open(RUNTIME_LOG_PATH, "a", encoding="utf-8")
    faulthandler.enable(FAULT_LOG_HANDLE, all_threads=True)
    sys.excepthook = log_unhandled_exception
    threading.excepthook = log_thread_exception
    LOGGER.info(
        "helper logging initialized log_path=%s max_bytes=%s backup_count=%s",
        RUNTIME_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


def configure_event_logging(config_path, config):
    global EVENT_RECORDER, EVENT_LOG_PATH
    if EVENT_RECORDER is not None:
        return
    EVENT_LOG_PATH = event_log_path(config_path, config, "helper-events.ndjson")
    settings = event_log_settings(config)
    EVENT_RECORDER = DurableEventRecorder(
        EVENT_LOG_PATH,
        max_bytes=settings["max_bytes"],
        backup_count=settings["backup_count"],
        recent_limit=settings["recent_limit"],
    )
    LOGGER.info(
        "helper event logging initialized event_log_path=%s max_bytes=%s backup_count=%s",
        EVENT_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


def configure_pid_file(config_path, config):
    global PID_FILE_PATH
    configured_pid_file = str(config.get("pid_file", "")).strip()
    if not configured_pid_file:
        PID_FILE_PATH = ""
        return
    if os.path.isabs(configured_pid_file):
        PID_FILE_PATH = configured_pid_file
    else:
        PID_FILE_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(config_path)), configured_pid_file))
    pid_dir = os.path.dirname(PID_FILE_PATH)
    if pid_dir:
        os.makedirs(pid_dir, exist_ok=True)


def configure_listen_state_path(config_path, config):
    global LISTEN_STATE_PATH
    configured_path = str(config.get("listen_state_path", "")).strip()
    if not configured_path:
        LISTEN_STATE_PATH = ""
        return
    if os.path.isabs(configured_path):
        LISTEN_STATE_PATH = configured_path
    else:
        LISTEN_STATE_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(config_path)), configured_path))
    state_dir = os.path.dirname(LISTEN_STATE_PATH)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)


def write_pid_file():
    if not PID_FILE_PATH:
        return
    with open(PID_FILE_PATH, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))


def remove_pid_file():
    if not PID_FILE_PATH:
        return
    with contextlib.suppress(OSError):
        os.remove(PID_FILE_PATH)


def write_listen_state(payload):
    if not LISTEN_STATE_PATH:
        return
    tmp_path = LISTEN_STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, LISTEN_STATE_PATH)


def remove_listen_state_file():
    if not LISTEN_STATE_PATH:
        return
    with contextlib.suppress(OSError):
        os.remove(LISTEN_STATE_PATH)


def register_runtime_control(loop, stop_event):
    global ACTIVE_LOOP, ACTIVE_STOP_EVENT
    with ACTIVE_LOOP_LOCK:
        ACTIVE_LOOP = loop
        ACTIVE_STOP_EVENT = stop_event


def clear_runtime_control(loop=None):
    global ACTIVE_LOOP, ACTIVE_STOP_EVENT
    with ACTIVE_LOOP_LOCK:
        if loop is not None and ACTIVE_LOOP is not loop:
            return
        ACTIVE_LOOP = None
        ACTIVE_STOP_EVENT = None


def request_stop():
    with ACTIVE_LOOP_LOCK:
        loop = ACTIVE_LOOP
        stop_event = ACTIVE_STOP_EVENT
    if loop is None or stop_event is None:
        return False
    if loop.is_closed():
        clear_runtime_control(loop)
        return False

    def signal_stop():
        if not stop_event.is_set():
            stop_event.set()

    try:
        loop.call_soon_threadsafe(signal_stop)
        return True
    except RuntimeError:
        clear_runtime_control(loop)
        return False


def bound_port(server):
    sockets = getattr(server, "sockets", None) or []
    if not sockets:
        raise RuntimeError("server sockets are unavailable")
    return int(sockets[0].getsockname()[1])


async def start_bound_servers(hosts, requested_port, handler):
    servers = []
    active_port = None
    for listen_host in hosts:
        bind_port = requested_port if active_port is None else active_port
        server = await asyncio.start_server(handler, listen_host, bind_port)
        if active_port is None:
            active_port = bound_port(server)
        servers.append(server)
    return servers, int(active_port or requested_port)


def log_unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    LOGGER.critical("unhandled helper exception", exc_info=(exc_type, exc_value, exc_traceback))


def log_thread_exception(args):
    if is_benign_network_error(getattr(args, "exc_value", None)):
        return
    LOGGER.critical(
        "unhandled helper thread exception thread=%s",
        getattr(args.thread, "name", "unknown"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def log_asyncio_exception(loop, context):
    del loop
    exception = context.get("exception")
    message = context.get("message", "asyncio loop exception")
    if exception is None:
        LOGGER.error("asyncio loop exception message=%s", message)
        return
    if is_benign_network_error(exception):
        return
    LOGGER.error("asyncio loop exception message=%s", message, exc_info=(type(exception), exception, exception.__traceback__))


def is_benign_network_error(error):
    if error is None:
        return False
    if isinstance(error, asyncio.CancelledError):
        return True
    if isinstance(error, asyncio.IncompleteReadError):
        return True
    if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    if isinstance(error, OSError) and getattr(error, "winerror", None) in {64, 995, 10053, 10054}:
        return True
    nested = getattr(error, "__cause__", None) or getattr(error, "__context__", None)
    if nested is not None and nested is not error:
        return is_benign_network_error(nested)
    return False


async def close_writer_quietly(writer):
    if writer.is_closing():
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    with contextlib.suppress(Exception):
        writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


def handle_shutdown_signal(signum, _frame):
    signal_name = getattr(signal.Signals(signum), "name", str(signum))
    LOGGER.warning("helper received shutdown signal=%s", signal_name)
    raise KeyboardInterrupt()


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if "broker_base_url" not in config and "broker_v2_base_url" in config:
        config["broker_base_url"] = config["broker_v2_base_url"]
    return config


def recv_until_headers(data):
    return b"\r\n\r\n" in data


def parse_request_headers(data):
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    request_line = lines[0]
    headers = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip()] = value.strip()
    return request_line, headers, rest


def target_from_request(request_line, headers):
    method, target, _version = request_line.split(" ", 2)
    if method.upper() == "CONNECT":
        host, port = target.rsplit(":", 1)
        return host, int(port), ""

    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme and parsed.hostname:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host_header = headers.get("Host", "")
        if host_header and not authority_matches(host, port, host_header):
            raise RuntimeError("absolute-form request target does not match Host header")
        rebuilt = "%s %s HTTP/1.1\r\n" % (method, path)
        return host, port, rebuilt

    host_header = headers.get("Host", "")
    if ":" in host_header:
        host, port_text = host_header.rsplit(":", 1)
        port = int(port_text)
    else:
        host = host_header
        port = 80
    rebuilt = "%s %s HTTP/1.1\r\n" % (method, target)
    return host, port, rebuilt


def rebuild_http_request(request_line, headers, rest):
    host, port, rebuilt_request_line = target_from_request(request_line, headers)
    outgoing_headers = []
    for name, value in headers.items():
        lower_name = name.lower()
        if lower_name in {"proxy-connection", "connection"}:
            continue
        outgoing_headers.append("%s: %s\r\n" % (name, value))
    outgoing_headers.append("Connection: close\r\n")
    payload = rebuilt_request_line + "".join(outgoing_headers) + "\r\n"
    return host, port, payload.encode("iso-8859-1") + rest


def parse_socks_target(address_type, data, offset=0):
    if address_type == 1:
        host = socket.inet_ntoa(data[offset : offset + 4])
        offset += 4
    elif address_type == 3:
        host_length = data[offset]
        offset += 1
        host = data[offset : offset + host_length].decode("utf-8")
        offset += host_length
    elif address_type == 4:
        host = str(ipaddress.IPv6Address(data[offset : offset + 16]))
        offset += 16
    else:
        raise RuntimeError("unsupported socks address type")
    port = struct.unpack("!H", data[offset : offset + 2])[0]
    offset += 2
    return host, port, offset


def encode_socks_address(host, port):
    try:
        ip = ipaddress.ip_address(host)
        if isinstance(ip, ipaddress.IPv4Address):
            return b"\x01" + socket.inet_aton(str(ip)) + struct.pack("!H", int(port))
        return b"\x04" + ip.packed + struct.pack("!H", int(port))
    except ValueError:
        encoded_host = host.encode("utf-8")
        if len(encoded_host) > 255:
            raise RuntimeError("socks host too long")
        return b"\x03" + bytes([len(encoded_host)]) + encoded_host + struct.pack("!H", int(port))


def parse_socks_udp_packet(packet):
    if len(packet) < 4:
        raise RuntimeError("short socks udp packet")
    if packet[0:2] != b"\x00\x00":
        raise RuntimeError("unsupported socks udp reserved bytes")
    fragment = packet[2]
    if fragment != 0:
        raise RuntimeError("fragmented socks udp is unsupported")
    address_type = packet[3]
    host, port, offset = parse_socks_target(address_type, packet, 4)
    return host, port, packet[offset:]


def build_socks_udp_packet(host, port, payload):
    return b"\x00\x00\x00" + encode_socks_address(host, port) + payload


async def query_dns_transport(runtime, target_host, payload):
    request_id = runtime.allocate_dns_request_id()
    loop = asyncio.get_running_loop()
    response_future = loop.create_future()
    runtime.dns_requests[request_id] = response_future
    try:
        trace("dns query send request=%s target=%s bytes=%s" % (request_id, target_host, len(payload)))
        record_event(
            "dns_query_sent",
            request_id=request_id,
            target_host=target_host,
            payload_bytes=len(payload),
        )
        await runtime.transport.send_frame(
            LANE_PRI,
            Frame(
                FRAME_DNS_QUERY,
                stream_id=request_id,
                payload=make_dns_query_frame_payload(target_host, payload),
            ),
        )
        return await asyncio.wait_for(response_future, timeout=runtime.dns_query_timeout)
    finally:
        current = runtime.dns_requests.get(request_id)
        if current is response_future:
            runtime.dns_requests.pop(request_id, None)


async def resolve_dns_query(runtime, target_host, payload):
    cache_key = dns_query_cache_key(payload)
    transaction_id = dns_transaction_id(payload)
    question_type = dns_question_type(payload)
    if vpn_filter_aaaa(runtime.config) and question_type in {DNS_TYPE_AAAA, DNS_TYPE_HTTPS}:
        query_type = "AAAA" if question_type == DNS_TYPE_AAAA else "HTTPS"
        trace("dns synthetic nodata type=%s bytes=%s" % (query_type, len(payload)))
        record_event("dns_query_synthesized", query_type=query_type, reason="ipv4-preferred")
        return with_dns_transaction_id(synthesize_empty_dns_response(payload), transaction_id)
    owner = False
    query_task = None
    now_monotonic = time.monotonic()
    async with runtime.dns_cache_lock:
        expire_dns_cache(runtime.dns_cache, now_monotonic, runtime.dns_cache_max_entries)
        cache_entry = runtime.dns_cache.get(cache_key)
        if cache_entry is not None:
            trace("dns cache hit bytes=%s" % len(payload))
            return with_dns_transaction_id(cache_entry["response"], transaction_id)
        query_task = runtime.dns_inflight.get(cache_key)
        if query_task is None:
            query_task = asyncio.create_task(_resolve_dns_query_uncached(runtime, target_host, payload))
            runtime.dns_inflight[cache_key] = query_task
            owner = True
    try:
        response = await asyncio.shield(query_task)
    finally:
        if owner:
            async with runtime.dns_cache_lock:
                if runtime.dns_inflight.get(cache_key) is query_task:
                    runtime.dns_inflight.pop(cache_key, None)
    return with_dns_transaction_id(response, transaction_id)


async def _resolve_dns_query_uncached(runtime, target_host, payload):
    async with runtime.dns_semaphore:
        response = await query_dns_transport(runtime, target_host, payload)
    async with runtime.dns_cache_lock:
        expire_dns_cache(runtime.dns_cache, time.monotonic(), runtime.dns_cache_max_entries)
        runtime.dns_cache[dns_query_cache_key(payload)] = {
            "expires_at": time.monotonic() + runtime.dns_cache_ttl_seconds,
            "response": response,
        }
    trace("dns relay target=%s bytes=%s via=protocol" % (target_host, len(response)))
    return response


class SocksUdpAssociation(asyncio.DatagramProtocol):
    def __init__(self, runtime):
        self.runtime = runtime
        self.transport = None
        self.client_addr = None
        self.tasks = set()

    @classmethod
    async def create(cls, runtime):
        loop = asyncio.get_running_loop()
        protocol = cls(runtime)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=("127.0.0.1", 0),
        )
        protocol.transport = transport
        return protocol

    def sockname(self):
        if self.transport is None:
            raise RuntimeError("udp association is not ready")
        return self.transport.get_extra_info("sockname")

    def close(self):
        for task in list(self.tasks):
            task.cancel()
        self.tasks.clear()
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    def datagram_received(self, data, addr):
        if self.client_addr is None:
            self.client_addr = addr
        elif addr != self.client_addr:
            trace("udp association ignoring packet from unexpected addr=%s" % (addr,))
            return
        task = asyncio.create_task(self._handle_datagram(data, addr))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _handle_datagram(self, data, addr):
        try:
            target_host, target_port, payload = parse_socks_udp_packet(data)
        except Exception as error:
            LOGGER.debug("invalid socks udp packet error=%s", error)
            return
        if target_port != 53:
            trace("drop udp packet host=%s port=%s bytes=%s" % (target_host, target_port, len(payload)))
            return
        if not payload:
            return
        last_error = None
        try:
            response = await resolve_dns_query(self.runtime, target_host, payload)
            packet = build_socks_udp_packet(target_host, target_port, response)
            self.transport.sendto(packet, addr)
            return
        except Exception as error:
            last_error = error
        LOGGER.warning(
            "dns udp relay failed target=%s qtype=%s bytes=%s error=%s",
            target_host,
            dns_question_type(payload),
            len(payload),
            format_error_summary(last_error),
        )


def authority_matches(expected_host, expected_port, header_value):
    expected_host = normalize_authority_host(expected_host)
    actual_host, actual_port = split_authority_header(header_value, expected_port)
    return expected_host == actual_host and int(expected_port) == int(actual_port)


def normalize_authority_host(value):
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return text.lower()


def split_authority_header(value, default_port):
    text = str(value or "").strip()
    if text.startswith("[") and "]" in text:
        host, _, remainder = text[1:].partition("]")
        if remainder.startswith(":"):
            return normalize_authority_host(host), int(remainder[1:])
        return normalize_authority_host(host), int(default_port)
    if text.count(":") == 1:
        host, port_text = text.rsplit(":", 1)
        return normalize_authority_host(host), int(port_text)
    return normalize_authority_host(text), int(default_port)


async def read_connect_preamble(reader, timeout_seconds, max_bytes):
    try:
        return await asyncio.wait_for(reader.read(int(max_bytes)), timeout=max(0.1, float(timeout_seconds)))
    except asyncio.TimeoutError:
        return b""


def extract_tls_server_name(payload):
    if len(payload) < 5 or payload[0] != 22:
        return ""
    record_length = struct.unpack("!H", payload[3:5])[0]
    record = payload[5:5 + record_length]
    if len(record) < 4 or record[0] != 1:
        return ""
    body_length = int.from_bytes(record[1:4], "big")
    body = record[4:4 + body_length]
    if len(body) < 34:
        return ""
    index = 34
    if index >= len(body):
        return ""
    session_id_length = body[index]
    index += 1 + session_id_length
    if index + 2 > len(body):
        return ""
    cipher_suites_length = struct.unpack("!H", body[index:index + 2])[0]
    index += 2 + cipher_suites_length
    if index >= len(body):
        return ""
    compression_methods_length = body[index]
    index += 1 + compression_methods_length
    if index + 2 > len(body):
        return ""
    extensions_length = struct.unpack("!H", body[index:index + 2])[0]
    index += 2
    end = min(len(body), index + extensions_length)
    while index + 4 <= end:
        extension_type, extension_size = struct.unpack("!HH", body[index:index + 4])
        index += 4
        extension_data = body[index:index + extension_size]
        index += extension_size
        if extension_type != 0 or len(extension_data) < 5:
            continue
        list_length = struct.unpack("!H", extension_data[:2])[0]
        names_end = min(len(extension_data), 2 + list_length)
        name_index = 2
        while name_index + 3 <= names_end:
            name_type = extension_data[name_index]
            name_length = struct.unpack("!H", extension_data[name_index + 1:name_index + 3])[0]
            name_index += 3
            name_value = extension_data[name_index:name_index + name_length]
            name_index += name_length
            if name_type == 0 and len(name_value) == name_length:
                return name_value.decode("idna")
    return ""


class ProxyStream(object):
    def __init__(self, helper, stream_id, target_host, target_port):
        self.helper = helper
        self.stream_id = int(stream_id)
        self.target_host = target_host
        self.target_port = int(target_port)
        self.open_event = asyncio.Event()
        self.recv_queue = asyncio.Queue()
        self.open_failed = ""
        self.send_credit = INITIAL_WINDOW
        self.send_credit_event = asyncio.Event()
        self.send_credit_event.set()
        self.send_offset = 0
        self.recv_offset = 0
        self.fin_offset = None
        self.closed = False
        self.pending_window = 0
        self.window_flush_task = None
        self.recv_frame_count = 0
        self.recv_data_bytes = 0
        self.local_write_bytes = 0
        self.local_write_count = 0
        self.recv_pending = {}
        self.recv_pending_bytes = 0

    async def open(self):
        trace("open stream=%s target=%s:%s" % (self.stream_id, self.target_host, self.target_port))
        record_event(
            "stream_open_requested",
            stream_id=self.stream_id,
            target_host=self.target_host,
            target_port=self.target_port,
        )
        frame = Frame(
            FRAME_OPEN,
            stream_id=self.stream_id,
            payload=make_open_payload(self.target_host, self.target_port, MODE_TCP),
        )
        await self.helper.transport.send_frame(LANE_CTL, frame)
        await asyncio.wait_for(self.open_event.wait(), timeout=30)
        if self.open_failed:
            trace("open failed stream=%s error=%s" % (self.stream_id, self.open_failed))
            record_event(
                "stream_open_failed",
                stream_id=self.stream_id,
                target_host=self.target_host,
                target_port=self.target_port,
                error=self.open_failed,
            )
            raise RuntimeError(self.open_failed)
        trace("open ok stream=%s" % self.stream_id)
        record_event(
            "stream_open_ok",
            stream_id=self.stream_id,
            target_host=self.target_host,
            target_port=self.target_port,
        )

    async def on_frame(self, frame):
        if frame.type_id == FRAME_OPEN_OK:
            trace("recv OPEN_OK stream=%s" % self.stream_id)
            self.open_event.set()
            return
        if frame.type_id == FRAME_OPEN_FAIL:
            self.open_failed = parse_error_payload(frame.payload)
            trace("recv OPEN_FAIL stream=%s error=%s" % (self.stream_id, self.open_failed))
            record_event("stream_open_fail_frame", stream_id=self.stream_id, error=self.open_failed)
            self.open_event.set()
            await self.recv_queue.put(None)
            return
        if frame.type_id == FRAME_WINDOW:
            self.send_credit += int(frame.offset)
            self.send_credit_event.set()
            return
        if frame.type_id == FRAME_DATA:
            raw_offset = int(frame.offset)
            payload = frame.payload
            if frame.offset < self.recv_offset:
                delta = self.recv_offset - frame.offset
                if delta >= len(payload):
                    trace("drop duplicate DATA stream=%s frame_offset=%s bytes=%s recv_offset=%s" % (self.stream_id, raw_offset, len(frame.payload), self.recv_offset))
                    return
                payload = payload[delta:]
            if raw_offset > self.recv_offset:
                await self._buffer_out_of_order_data(raw_offset, payload)
                return
            await self._accept_in_order_data(raw_offset, payload)
            await self._flush_pending_data()
            return
        if frame.type_id in (FRAME_FIN, FRAME_RST):
            if frame.type_id == FRAME_RST and frame.payload:
                self.open_failed = parse_error_payload(frame.payload)
                trace("recv RST stream=%s error=%s" % (self.stream_id, self.open_failed))
                record_event("stream_reset", stream_id=self.stream_id, error=self.open_failed)
                await self.recv_queue.put(None)
                return
            self.fin_offset = int(frame.offset)
            trace("recv FIN stream=%s fin_offset=%s recv_offset=%s" % (self.stream_id, self.fin_offset, self.recv_offset))
            record_event(
                "stream_fin_received",
                stream_id=self.stream_id,
                fin_offset=self.fin_offset,
                recv_offset=self.recv_offset,
            )
            if self.recv_offset >= self.fin_offset:
                await self.recv_queue.put(None)

    async def _accept_in_order_data(self, raw_offset, payload):
        self.recv_frame_count += 1
        self.recv_data_bytes += len(payload)
        self.recv_offset += len(payload)
        trace(
            "recv DATA stream=%s frame_offset=%s accepted=%s recv_offset=%s frames=%s data_bytes=%s queue=%s pending=%s/%s" % (
                self.stream_id,
                raw_offset,
                len(payload),
                self.recv_offset,
                self.recv_frame_count,
                self.recv_data_bytes,
                self.recv_queue.qsize() + 1,
                len(self.recv_pending),
                self.recv_pending_bytes,
            )
        )
        await self.recv_queue.put(payload)
        if self.fin_offset is not None and self.recv_offset >= self.fin_offset:
            await self.recv_queue.put(None)

    async def _buffer_out_of_order_data(self, raw_offset, payload):
        end_offset = raw_offset + len(payload)
        if end_offset <= self.recv_offset:
            trace("drop late DATA stream=%s frame_offset=%s bytes=%s recv_offset=%s" % (self.stream_id, raw_offset, len(payload), self.recv_offset))
            return
        if raw_offset in self.recv_pending:
            existing = self.recv_pending[raw_offset]
            if len(existing) >= len(payload):
                trace("drop duplicate pending DATA stream=%s frame_offset=%s bytes=%s recv_offset=%s" % (self.stream_id, raw_offset, len(payload), self.recv_offset))
                return
            self.recv_pending_bytes -= len(existing)
        elif self.recv_pending_bytes + len(payload) > MAX_RECV_REORDER_BYTES:
            trace(
                "reorder buffer overflow stream=%s frame_offset=%s bytes=%s recv_offset=%s pending=%s" % (
                    self.stream_id,
                    raw_offset,
                    len(payload),
                    self.recv_offset,
                    self.recv_pending_bytes,
                )
            )
            await self.reset("reorder buffer overflow")
            return
        self.recv_pending[raw_offset] = payload
        self.recv_pending_bytes += len(payload)
        trace(
            "buffer out-of-order DATA stream=%s frame_offset=%s bytes=%s recv_offset=%s pending=%s/%s" % (
                self.stream_id,
                raw_offset,
                len(payload),
                self.recv_offset,
                len(self.recv_pending),
                self.recv_pending_bytes,
            )
        )

    async def _flush_pending_data(self):
        while True:
            payload = self.recv_pending.pop(self.recv_offset, None)
            if payload is None:
                return
            self.recv_pending_bytes -= len(payload)
            trace(
                "flush pending DATA stream=%s frame_offset=%s bytes=%s pending=%s/%s" % (
                    self.stream_id,
                    self.recv_offset,
                    len(payload),
                    len(self.recv_pending),
                    self.recv_pending_bytes,
                )
            )
            await self._accept_in_order_data(self.recv_offset, payload)

    async def send_data(self, payload):
        view = memoryview(payload)
        while view and not self.closed:
            if self.send_credit <= 0:
                self.send_credit_event.clear()
                await self.send_credit_event.wait()
            chunk_len = min(len(view), READ_CHUNK, self.send_credit)
            chunk = bytes(view[:chunk_len])
            lane = self._data_lane(len(chunk))
            trace("send DATA stream=%s lane=%s offset=%s bytes=%s" % (self.stream_id, lane, self.send_offset, len(chunk)))
            flags = FLAG_DATA_BULK if lane == LANE_BULK else 0
            frame = Frame(FRAME_DATA, stream_id=self.stream_id, offset=self.send_offset, payload=chunk, flags=flags)
            await self.helper.transport.send_frame(lane, frame)
            self.send_offset += len(chunk)
            self.send_credit -= len(chunk)
            view = view[chunk_len:]

    async def grant_window(self, length):
        if length <= 0 or self.closed:
            return
        self.pending_window += int(length)
        if self.pending_window >= WINDOW_FLUSH_BYTES:
            await self.flush_window()
            return
        if self.window_flush_task is None or self.window_flush_task.done():
            self.window_flush_task = asyncio.create_task(self._flush_window_later())

    async def flush_window(self):
        if self.pending_window <= 0 or self.closed:
            return
        value = self.pending_window
        self.pending_window = 0
        await self.helper.transport.send_frame(
            LANE_CTL,
            Frame(FRAME_WINDOW, stream_id=self.stream_id, offset=int(value)),
        )

    async def _flush_window_later(self):
        await asyncio.sleep(WINDOW_FLUSH_DELAY)
        await self.flush_window()

    async def finish(self):
        if self.closed:
            return
        await self.flush_window()
        await self.helper.transport.send_frame(LANE_CTL, Frame(FRAME_FIN, stream_id=self.stream_id, offset=self.send_offset))
        self.closed = True

    async def reset(self, message):
        if self.closed:
            return
        if self.window_flush_task is not None and not self.window_flush_task.done():
            self.window_flush_task.cancel()
        await self.helper.transport.send_frame(
            LANE_CTL,
            Frame(FRAME_RST, stream_id=self.stream_id, payload=make_error_payload(message)),
        )
        self.closed = True

    def _data_lane(self, chunk_len):
        # Keep early bytes on the priority lane so interactive traffic is not
        # immediately pushed onto the bulk path.
        if self.send_offset < PRI_LIMIT and (self.send_offset + int(chunk_len)) <= PRI_LIMIT:
            return LANE_PRI
        return LANE_BULK


class HelperRuntime(object):
    def __init__(self, config):
        self.config = config
        self.streams = {}
        seed = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
        self.next_stream_id = max(1, seed | 1)
        self.peer_id = config.get("peer_id") or random_peer_id()
        self.transport = create_transport(config, "helper", self.peer_id, self.on_frame)
        self.transport.event_handler = self._record_transport_event
        self.dns_query_timeout = max(0.5, float(config.get("vpn_dns_query_timeout_seconds", DNS_QUERY_TIMEOUT)))
        self.dns_cache_ttl_seconds = max(1.0, float(config.get("vpn_dns_cache_ttl_seconds", DNS_CACHE_TTL_SECONDS)))
        self.dns_cache_max_entries = max(32, int(config.get("vpn_dns_cache_max_entries", DNS_CACHE_MAX_ENTRIES)))
        self.dns_semaphore = asyncio.Semaphore(max(1, int(config.get("vpn_dns_max_inflight", DNS_MAX_INFLIGHT))))
        self.dns_cache = {}
        self.dns_inflight = {}
        self.dns_requests = {}
        self.dns_cache_lock = asyncio.Lock()
        dns_seed = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFE
        self.next_dns_request_id = dns_seed if dns_seed > 0 else 2
        self.active_connection_tasks = set()
        self.active_connection_writers = set()

    async def start(self):
        await self.transport.start()

    async def stop(self):
        await self.shutdown_active_connections()
        await self._reset_active_streams_for_shutdown()
        self._fail_pending_dns_requests("helper stopping")
        await self.transport.stop()

    async def on_frame(self, frame, _lane):
        if frame.type_id in (FRAME_DNS_RESPONSE, FRAME_DNS_FAIL):
            future = self.dns_requests.get(frame.stream_id)
            if future is None or future.done():
                return
            if frame.type_id == FRAME_DNS_RESPONSE:
                trace("dns query recv request=%s bytes=%s" % (frame.stream_id, len(frame.payload)))
                record_event("dns_query_response", request_id=frame.stream_id, payload_bytes=len(frame.payload))
                future.set_result(frame.payload)
            else:
                error_text = parse_error_payload(frame.payload) or "dns query failed"
                trace("dns query fail request=%s error=%s" % (frame.stream_id, error_text))
                record_event("dns_query_fail", request_id=frame.stream_id, error=error_text)
                future.set_exception(RuntimeError(error_text))
            return
        stream = self.streams.get(frame.stream_id)
        if stream is None:
            return
        await stream.on_frame(frame)

    def allocate_dns_request_id(self):
        request_id = int(self.next_dns_request_id) & 0xFFFFFFFF
        if request_id <= 0:
            request_id = 2
        start = request_id
        while request_id in self.dns_requests:
            request_id += 2
            if request_id > 0xFFFFFFFF:
                request_id = 2
            if request_id == start:
                raise RuntimeError("no available dns request ids")
        next_request_id = request_id + 2
        self.next_dns_request_id = 2 if next_request_id > 0xFFFFFFFF else next_request_id
        return request_id

    def new_stream(self, target_host, target_port):
        stream_id = self.next_stream_id
        self.next_stream_id += 2
        stream = ProxyStream(self, stream_id, target_host, target_port)
        self.streams[stream_id] = stream
        return stream

    async def release_stream(self, stream_id):
        self.streams.pop(stream_id, None)
        record_event("stream_released", stream_id=stream_id)

    def _fail_pending_dns_requests(self, message):
        for request_id, future in list(self.dns_requests.items()):
            if future.done():
                continue
            future.set_exception(RuntimeError(message))
            record_event("dns_query_cancelled", request_id=request_id, reason=message)
        self.dns_requests.clear()

    def register_connection(self, task, writer):
        if task is not None:
            self.active_connection_tasks.add(task)
        if writer is not None:
            self.active_connection_writers.add(writer)

    def unregister_connection(self, task, writer):
        if task is not None:
            self.active_connection_tasks.discard(task)
        if writer is not None:
            self.active_connection_writers.discard(writer)

    def _record_transport_event(self, event):
        record_event(**event)

    async def shutdown_active_connections(self):
        active_writers = list(self.active_connection_writers)
        active_tasks = [task for task in self.active_connection_tasks if task is not asyncio.current_task()]
        if not active_writers and not active_tasks:
            return
        record_event(
            "helper_shutdown_connections",
            writer_count=len(active_writers),
            task_count=len(active_tasks),
        )
        for writer in active_writers:
            with contextlib.suppress(Exception):
                writer.close()
        for task in active_tasks:
            task.cancel()

        async def _wait_writer_closed(writer):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)

        async def _wait_task_cancelled(task):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, timeout=1.0)

        await asyncio.gather(
            *[_wait_writer_closed(writer) for writer in active_writers],
            *[_wait_task_cancelled(task) for task in active_tasks],
            return_exceptions=True,
        )

    async def _reset_active_streams_for_shutdown(self):
        active_streams = [stream for stream in self.streams.values() if not stream.closed]
        if not active_streams:
            return
        grace_seconds = max(
            0.0,
            float(
                self.config.get(
                    "shutdown_stream_reset_grace_seconds",
                    SHUTDOWN_STREAM_RESET_GRACE_SECONDS,
                )
            ),
        )
        record_event(
            "helper_shutdown_reset_pending",
            stream_count=len(active_streams),
            grace_seconds=grace_seconds,
        )
        for stream in active_streams:
            with contextlib.suppress(Exception):
                await stream.reset("helper shutdown")
        if grace_seconds > 0:
            await asyncio.sleep(grace_seconds)


async def relay_stream(runtime, stream, reader, writer, initial_payload=b"", connected_response=None, open_stream=True):
    failure = None
    child_tasks = []

    async def wait_for_drain(write_start, write_end):
        drain_task = asyncio.create_task(writer.drain())
        try:
            while True:
                try:
                    await asyncio.wait_for(asyncio.shield(drain_task), timeout=5.0)
                    return
                except asyncio.TimeoutError:
                    trace(
                        "remote drain waiting stream=%s local_range=%s-%s recv_offset=%s queue=%s" % (
                            stream.stream_id,
                            write_start,
                            write_end,
                            stream.recv_offset,
                            stream.recv_queue.qsize(),
                        )
                    )
        finally:
            if not drain_task.done():
                drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await drain_task

    try:
        if open_stream:
            await stream.open()
        if connected_response:
            writer.write(connected_response)
            await writer.drain()
        if initial_payload:
            await stream.send_data(initial_payload)

        async def local_to_remote():
            while True:
                data = await reader.read(READ_CHUNK)
                if not data:
                    trace("local EOF stream=%s send_offset=%s" % (stream.stream_id, stream.send_offset))
                    record_event("local_eof", stream_id=stream.stream_id, send_offset=stream.send_offset)
                    await stream.finish()
                    return
                trace("local read stream=%s bytes=%s" % (stream.stream_id, len(data)))
                await stream.send_data(data)

        async def remote_to_local():
            while True:
                payload = await stream.recv_queue.get()
                if payload is None:
                    if stream.open_failed and connected_response:
                        raise RuntimeError(stream.open_failed)
                    trace(
                        "remote EOF stream=%s recv_offset=%s local_written=%s local_writes=%s" % (
                            stream.stream_id,
                            stream.recv_offset,
                            stream.local_write_bytes,
                            stream.local_write_count,
                        )
                    )
                    record_event(
                        "remote_eof",
                        stream_id=stream.stream_id,
                        recv_offset=stream.recv_offset,
                        local_written=stream.local_write_bytes,
                        local_writes=stream.local_write_count,
                        error=stream.open_failed,
                    )
                    with contextlib.suppress(Exception):
                        writer.write_eof()
                    return
                write_start = stream.local_write_bytes
                write_end = write_start + len(payload)
                trace(
                    "remote write start stream=%s bytes=%s local_range=%s-%s recv_offset=%s queue=%s" % (
                        stream.stream_id,
                        len(payload),
                        write_start,
                        write_end,
                        stream.recv_offset,
                        stream.recv_queue.qsize(),
                    )
                )
                writer.write(payload)
                await wait_for_drain(write_start, write_end)
                stream.local_write_bytes = write_end
                stream.local_write_count += 1
                trace(
                    "remote write done stream=%s bytes=%s local_written=%s writes=%s queue=%s" % (
                        stream.stream_id,
                        len(payload),
                        stream.local_write_bytes,
                        stream.local_write_count,
                        stream.recv_queue.qsize(),
                    )
                )
                await stream.grant_window(len(payload))

        child_tasks = [asyncio.create_task(local_to_remote()), asyncio.create_task(remote_to_local())]
        done, pending = await asyncio.wait(child_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(asyncio.CancelledError):
                error = task.exception()
                if error:
                    raise error
    except asyncio.CancelledError as error:
        failure = error
        raise
    except Exception as error:
        failure = error
        raise
    finally:
        if child_tasks:
            for task in child_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*child_tasks, return_exceptions=True)
        if failure is not None:
            with contextlib.suppress(Exception):
                reason = "relay cancelled" if isinstance(failure, asyncio.CancelledError) else str(failure)
                await stream.reset(reason)
        elif not stream.closed:
            with contextlib.suppress(Exception):
                await stream.finish()
        await runtime.release_stream(stream.stream_id)
        await close_writer_quietly(writer)


async def handle_http(runtime, reader, writer):
    try:
        buffer = b""
        while not recv_until_headers(buffer) and len(buffer) < 65536:
            chunk = await reader.read(4096)
            if not chunk:
                await close_writer_quietly(writer)
                return
            buffer += chunk
        request_line, headers, rest = parse_request_headers(buffer)
        method = request_line.split(" ", 1)[0].upper()
        if method == "CONNECT":
            host, port, _ = target_from_request(request_line, headers)
            connected_response = None
            initial_payload = b""
            if runtime.config.get("enforce_connect_sni", True):
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                initial_payload = await read_connect_preamble(
                    reader,
                    timeout_seconds=float(runtime.config.get("connect_sni_timeout_seconds", 1.0)),
                    max_bytes=int(runtime.config.get("connect_sni_probe_bytes", 4096)),
                )
                server_name = extract_tls_server_name(initial_payload)
                if server_name and normalize_authority_host(server_name) != normalize_authority_host(host):
                    raise RuntimeError("TLS SNI does not match CONNECT host")
            else:
                connected_response = b"HTTP/1.1 200 Connection Established\r\n\r\n"
        else:
            host, port, initial_payload = rebuild_http_request(request_line, headers, rest)
            connected_response = None
        trace("http request method=%s host=%s port=%s connect=%s" % (method, host, port, method == "CONNECT"))
        stream = runtime.new_stream(host, port)
        await relay_stream(runtime, stream, reader, writer, initial_payload=initial_payload, connected_response=connected_response)
    except Exception as error:
        if is_benign_network_error(error):
            await close_writer_quietly(writer)
            return
        LOGGER.warning("http proxy request failed error=%s", error)
        body = str(error).encode("utf-8", errors="replace")
        try:
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Type: text/plain\r\nContent-Length: "
                + str(len(body)).encode("ascii")
                + b"\r\n\r\n"
                + body
            )
            await writer.drain()
        except Exception as write_error:
            if not is_benign_network_error(write_error):
                LOGGER.warning("http proxy error response failed error=%s", write_error)
        await close_writer_quietly(writer)


async def handle_socks(runtime, reader, writer):
    stream = None
    udp_association = None
    try:
        peer = writer.get_extra_info("peername")
        try:
            version, method_count = (await reader.readexactly(2))
        except asyncio.IncompleteReadError as error:
            if not error.partial:
                await close_writer_quietly(writer)
                return
            raise
        if version != 5:
            raise RuntimeError("unsupported socks version")
        methods = await reader.readexactly(method_count)
        if b"\x00" not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            await close_writer_quietly(writer)
            return
        writer.write(b"\x05\x00")
        await writer.drain()
        header = await reader.readexactly(4)
        version, command, _reserved, address_type = header
        if version != 5:
            raise RuntimeError("unsupported socks version")
        if address_type == 1:
            address_data = await reader.readexactly(4 + 2)
        elif address_type == 3:
            host_length = (await reader.readexactly(1))[0]
            address_data = bytes([host_length]) + await reader.readexactly(host_length + 2)
        elif address_type == 4:
            address_data = await reader.readexactly(16 + 2)
        else:
            raise RuntimeError("unsupported socks address type")
        host, port, _offset = parse_socks_target(address_type, address_data)
        if command == 3:
            udp_association = await SocksUdpAssociation.create(runtime)
            bind_host, bind_port = udp_association.sockname()
            LOGGER.info("socks udp accept peer=%s bind=%s:%s target=%s:%s", peer, bind_host, bind_port, host, port)
            writer.write(b"\x05\x00\x00" + encode_socks_address(bind_host, bind_port))
            await writer.drain()
            trace("socks udp association bind=%s:%s target=%s:%s" % (bind_host, bind_port, host, port))
            await reader.read()
            await close_writer_quietly(writer)
            return
        if command != 1:
            raise RuntimeError("unsupported socks command")
        LOGGER.info("socks accept peer=%s host=%s port=%s", peer, host, port)
        stream = runtime.new_stream(host, port)
        await stream.open()
        writer.write(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", 0))
        await writer.drain()
        await relay_stream(runtime, stream, reader, writer, open_stream=False)
    except Exception as error:
        if not is_benign_network_error(error):
            LOGGER.warning("socks proxy request failed error=%s", error)
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.reset("socks failure")
            await runtime.release_stream(stream.stream_id)
        await close_writer_quietly(writer)
    finally:
        if udp_association is not None:
            udp_association.close()


async def handle_client_connection(runtime, handler, reader, writer):
    task = asyncio.current_task()
    runtime.register_connection(task, writer)
    try:
        await handler(runtime, reader, writer)
    finally:
        runtime.unregister_connection(task, writer)


async def main_async(config):
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(log_asyncio_exception)
    stop_event = asyncio.Event()
    register_runtime_control(loop, stop_event)
    runtime = HelperRuntime(config)
    configured_http_hosts = config.get("http_listen_hosts") or config.get("listen_hosts")
    configured_socks_hosts = config.get("socks_listen_hosts") or config.get("listen_hosts")
    if isinstance(configured_http_hosts, list):
        http_listen_hosts = [str(value).strip() for value in configured_http_hosts if str(value).strip()]
    else:
        http_listen_hosts = [str(config.get("listen_host", "127.0.0.1")).strip()]
    if isinstance(configured_socks_hosts, list):
        socks_listen_hosts = [str(value).strip() for value in configured_socks_hosts if str(value).strip()]
    else:
        socks_listen_hosts = [str(config.get("listen_host", "127.0.0.1")).strip()]
    if not http_listen_hosts:
        http_listen_hosts = ["127.0.0.1"]
    if not socks_listen_hosts:
        socks_listen_hosts = ["127.0.0.1"]
    http_port = int(config.get("http_listen_port", 8080))
    socks_port = int(config.get("socks_listen_port", 1080))
    http2_config = config.get("http2_enabled", {})
    if isinstance(http2_config, dict):
        http2_ctl_enabled = bool(http2_config.get("ctl", False))
        http2_data_enabled = bool(http2_config.get("data", False))
    else:
        http2_ctl_enabled = bool(http2_config)
        http2_data_enabled = bool(http2_config)
    LOGGER.info(
        "helper starting transport=%s http_hosts=%s socks_hosts=%s http_port=%s socks_port=%s trace=%s http2_ctl=%s http2_data=%s peer_id=%s transport_session=%s",
        config.get("transport", "http"),
        ",".join(http_listen_hosts),
        ",".join(socks_listen_hosts),
        http_port,
        socks_port,
        TRACE_ENABLED,
        http2_ctl_enabled,
        http2_data_enabled,
        runtime.peer_id,
        runtime.transport.peer_session_id,
    )
    record_event(
        "helper_starting",
        peer_id=runtime.peer_id,
        transport_session_id=runtime.transport.peer_session_id,
        transport=config.get("transport", "http"),
        http_hosts=http_listen_hosts,
        socks_hosts=socks_listen_hosts,
        http_port=http_port,
        socks_port=socks_port,
    )
    http_servers = []
    socks_servers = []
    serve_tasks = []
    try:
        await runtime.start()
        http_servers, active_http_port = await start_bound_servers(
            http_listen_hosts,
            http_port,
            lambda r, w: handle_client_connection(runtime, handle_http, r, w),
        )
        socks_servers, active_socks_port = await start_bound_servers(
            socks_listen_hosts,
            socks_port,
            lambda r, w: handle_client_connection(runtime, handle_socks, r, w),
        )
        write_listen_state(
            {
                "http_hosts": http_listen_hosts,
                "socks_hosts": socks_listen_hosts,
                "http_port": active_http_port,
                "socks_port": active_socks_port,
                "transport_session_id": runtime.transport.peer_session_id,
            }
        )
        LOGGER.info(
            "helper started log_path=%s event_log_path=%s transport_session=%s http_port=%s socks_port=%s",
            RUNTIME_LOG_PATH or "stderr-only",
            EVENT_LOG_PATH or "disabled",
            runtime.transport.peer_session_id,
            active_http_port,
            active_socks_port,
        )
        record_event(
            "helper_started",
            transport_session_id=runtime.transport.peer_session_id,
            http_hosts=http_listen_hosts,
            socks_hosts=socks_listen_hosts,
            http_port=active_http_port,
            socks_port=active_socks_port,
        )
        for server in http_servers + socks_servers:
            serve_tasks.append(asyncio.create_task(server.serve_forever()))
        stop_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            serve_tasks + [stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            LOGGER.info("helper stop requested")
            record_event("helper_stop_requested", transport_session_id=runtime.transport.peer_session_id)
        else:
            for task in done:
                task.result()
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        clear_runtime_control(loop)
        LOGGER.info("helper stopping")
        record_event("helper_stopping", transport_session_id=runtime.transport.peer_session_id)
        for http_server in http_servers:
            http_server.close()
            with contextlib.suppress(Exception):
                await http_server.wait_closed()
        for socks_server in socks_servers:
            socks_server.close()
            with contextlib.suppress(Exception):
                await socks_server.wait_closed()
        await runtime.stop()
        remove_listen_state_file()
        LOGGER.info("helper stopped")
        record_event("helper_stopped", transport_session_id=runtime.transport.peer_session_id)


def main():
    parser = argparse.ArgumentParser(description="Twoman local helper")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    configure_runtime_logging(args.config, config)
    configure_event_logging(args.config, config)
    configure_pid_file(args.config, config)
    configure_listen_state_path(args.config, config)
    for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if signum is None:
            continue
        with contextlib.suppress(ValueError):
            signal.signal(signum, handle_shutdown_signal)
    try:
        write_pid_file()
        remove_listen_state_file()
        asyncio.run(main_async(config))
    except KeyboardInterrupt:
        LOGGER.info("helper interrupted by user")
        record_event("helper_interrupted")
        raise SystemExit(0)
    except Exception:
        LOGGER.exception("helper crashed")
        record_event("helper_crashed")
        raise SystemExit(1)
    finally:
        remove_pid_file()


if __name__ == "__main__":
    main()
