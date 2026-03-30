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
import urllib.parse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from twoman_protocol import (
    Frame,
    FLAG_DATA_BULK,
    FRAME_DATA,
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
from twoman_transport import create_transport


INITIAL_WINDOW = 256 * 1024
PRI_LIMIT = 64 * 1024
READ_CHUNK = 16 * 1024
WINDOW_FLUSH_BYTES = 16 * 1024
WINDOW_FLUSH_DELAY = 0.005
SMALL_WRITE_BYTES = 8 * 1024
MAX_RECV_REORDER_BYTES = 1024 * 1024

TRACE_ENABLED = os.environ.get("TWOMAN_TRACE", "").strip().lower() in ("1", "true", "yes", "on", "debug", "verbose")
LOGGER = logging.getLogger("twoman.helper")
RUNTIME_LOG_PATH = ""
FAULT_LOG_HANDLE = None
PID_FILE_PATH = ""


def trace(message):
    if not TRACE_ENABLED:
        return
    if LOGGER.handlers:
        LOGGER.debug(message)
        return
    sys.stderr.write("[helper] %s\n" % message)
    sys.stderr.flush()


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
    log_dir = os.path.dirname(RUNTIME_LOG_PATH)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    logger_level = logging.DEBUG if TRACE_ENABLED else logging.INFO
    LOGGER.setLevel(logger_level)
    LOGGER.propagate = False

    file_handler = RotatingFileHandler(
        RUNTIME_LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
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
    LOGGER.info("helper logging initialized log_path=%s", RUNTIME_LOG_PATH)


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
        frame = Frame(
            FRAME_OPEN,
            stream_id=self.stream_id,
            payload=make_open_payload(self.target_host, self.target_port, MODE_TCP),
        )
        await self.helper.transport.send_frame(LANE_CTL, frame)
        await asyncio.wait_for(self.open_event.wait(), timeout=30)
        if self.open_failed:
            trace("open failed stream=%s error=%s" % (self.stream_id, self.open_failed))
            raise RuntimeError(self.open_failed)
        trace("open ok stream=%s" % self.stream_id)

    async def on_frame(self, frame):
        if frame.type_id == FRAME_OPEN_OK:
            trace("recv OPEN_OK stream=%s" % self.stream_id)
            self.open_event.set()
            return
        if frame.type_id == FRAME_OPEN_FAIL:
            self.open_failed = parse_error_payload(frame.payload)
            trace("recv OPEN_FAIL stream=%s error=%s" % (self.stream_id, self.open_failed))
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
                await self.recv_queue.put(None)
                return
            self.fin_offset = int(frame.offset)
            trace("recv FIN stream=%s fin_offset=%s recv_offset=%s" % (self.stream_id, self.fin_offset, self.recv_offset))
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
        del chunk_len
        if self.send_offset < PRI_LIMIT:
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

    async def start(self):
        await self.transport.start()

    async def stop(self):
        await self.transport.stop()

    async def on_frame(self, frame, _lane):
        stream = self.streams.get(frame.stream_id)
        if stream is None:
            return
        await stream.on_frame(frame)

    def new_stream(self, target_host, target_port):
        stream_id = self.next_stream_id
        self.next_stream_id += 2
        stream = ProxyStream(self, stream_id, target_host, target_port)
        self.streams[stream_id] = stream
        return stream

    async def release_stream(self, stream_id):
        self.streams.pop(stream_id, None)


async def relay_stream(runtime, stream, reader, writer, initial_payload=b"", connected_response=None, open_stream=True):
    failure = None
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
                drain_task = asyncio.create_task(writer.drain())
                while True:
                    try:
                        await asyncio.wait_for(asyncio.shield(drain_task), timeout=5.0)
                        break
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

        tasks = [asyncio.create_task(local_to_remote()), asyncio.create_task(remote_to_local())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(asyncio.CancelledError):
                error = task.exception()
                if error:
                    raise error
    except Exception as error:
        failure = error
        raise
    finally:
        if failure is not None:
            with contextlib.suppress(Exception):
                await stream.reset(str(failure))
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
            initial_payload = b""
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
    try:
        version, method_count = (await reader.readexactly(2))
        if version != 5:
            raise RuntimeError("unsupported socks version")
        methods = await reader.readexactly(method_count)
        if b"\x00" not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(b"\x05\x00")
        await writer.drain()
        header = await reader.readexactly(4)
        version, command, _reserved, address_type = header
        if version != 5 or command != 1:
            raise RuntimeError("only socks connect is supported")
        if address_type == 1:
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif address_type == 3:
            host_length = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(host_length)).decode("utf-8")
        elif address_type == 4:
            host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
        else:
            raise RuntimeError("unsupported socks address type")
        port = struct.unpack("!H", await reader.readexactly(2))[0]
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


async def main_async(config):
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(log_asyncio_exception)
    runtime = HelperRuntime(config)
    listen_host = config.get("listen_host", "127.0.0.1")
    http_port = int(config.get("http_listen_port", 8080))
    socks_port = int(config.get("socks_listen_port", 1080))
    LOGGER.info(
        "helper starting transport=%s listen_host=%s http_port=%s socks_port=%s trace=%s http2_ctl=%s http2_data=%s",
        config.get("transport", "http"),
        listen_host,
        http_port,
        socks_port,
        TRACE_ENABLED,
        bool(config.get("http2_enabled", {}).get("ctl", False)),
        bool(config.get("http2_enabled", {}).get("data", False)),
    )
    await runtime.start()
    http_server = None
    socks_server = None
    try:
        http_server = await asyncio.start_server(
            lambda r, w: handle_http(runtime, r, w),
            listen_host,
            http_port,
        )
        socks_server = await asyncio.start_server(
            lambda r, w: handle_socks(runtime, r, w),
            listen_host,
            socks_port,
        )
        LOGGER.info("helper started log_path=%s", RUNTIME_LOG_PATH or "stderr-only")
        async with http_server, socks_server:
            await asyncio.gather(http_server.serve_forever(), socks_server.serve_forever())
    finally:
        LOGGER.info("helper stopping")
        if http_server is not None:
            http_server.close()
            with contextlib.suppress(Exception):
                await http_server.wait_closed()
        if socks_server is not None:
            socks_server.close()
            with contextlib.suppress(Exception):
                await socks_server.wait_closed()
        await runtime.stop()
        LOGGER.info("helper stopped")


def main():
    parser = argparse.ArgumentParser(description="Twoman local helper")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    configure_runtime_logging(args.config, config)
    configure_pid_file(args.config, config)
    for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if signum is None:
            continue
        with contextlib.suppress(ValueError):
            signal.signal(signum, handle_shutdown_signal)
    try:
        write_pid_file()
        asyncio.run(main_async(config))
    except KeyboardInterrupt:
        LOGGER.info("helper interrupted by user")
        raise SystemExit(0)
    except Exception:
        LOGGER.exception("helper crashed")
        raise SystemExit(1)
    finally:
        remove_pid_file()


if __name__ == "__main__":
    main()
