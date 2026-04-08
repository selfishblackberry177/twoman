#!/usr/bin/env python3

import argparse
import asyncio
import contextlib
import json
import logging
import os
import socket
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
VENDOR_DIR = os.path.join(CURRENT_DIR, "vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from twoman_protocol import (
    Frame,
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
    parse_open_payload,
)
from runtime_diagnostics import (
    DurableEventRecorder,
    configure_component_logger,
    event_log_path,
    event_log_settings,
    runtime_log_path,
    runtime_log_settings,
)
from twoman_transport import create_transport


INITIAL_WINDOW = 256 * 1024
READ_CHUNK = 16 * 1024
PRI_LIMIT = 64 * 1024
WINDOW_FLUSH_BYTES = 16 * 1024
WINDOW_FLUSH_DELAY = 0.005
SMALL_WRITE_BYTES = 8 * 1024
MAX_RECV_REORDER_BYTES = 1024 * 1024

TRACE_ENABLED = os.environ.get("TWOMAN_TRACE", "").strip().lower() in ("1", "true", "yes", "on", "debug", "verbose")
DEFAULT_OPEN_CONNECT_TIMEOUT_SECONDS = 12.0
DEFAULT_HAPPY_EYEBALLS_DELAY_SECONDS = 0.25
LOGGER = logging.getLogger("twoman.agent")
RUNTIME_LOG_PATH = ""
EVENT_LOG_PATH = ""
EVENT_RECORDER = None


def trace(message):
    if not TRACE_ENABLED:
        return
    if LOGGER.handlers:
        LOGGER.debug(message)
        return
    sys.stderr.write("[agent] %s\n" % message)
    sys.stderr.flush()


def record_event(kind, **fields):
    if EVENT_RECORDER is None:
        return
    try:
        EVENT_RECORDER.record(kind, component="agent", **fields)
    except Exception:
        LOGGER.exception("agent event log write failed kind=%s", kind)


def configure_runtime_logging(config_path, config):
    global RUNTIME_LOG_PATH
    if LOGGER.handlers:
        return
    RUNTIME_LOG_PATH = runtime_log_path(config_path, config, "agent.log")
    settings = runtime_log_settings(config)
    configure_component_logger(
        LOGGER,
        log_path=RUNTIME_LOG_PATH,
        trace_enabled=TRACE_ENABLED,
        runtime_log_max_bytes=settings["max_bytes"],
        runtime_log_backup_count=settings["backup_count"],
        console_prefix="agent",
    )
    LOGGER.info(
        "agent logging initialized log_path=%s max_bytes=%s backup_count=%s",
        RUNTIME_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


def configure_event_logging(config_path, config):
    global EVENT_RECORDER, EVENT_LOG_PATH
    if EVENT_RECORDER is not None:
        return
    EVENT_LOG_PATH = event_log_path(config_path, config, "agent-events.ndjson")
    settings = event_log_settings(config)
    EVENT_RECORDER = DurableEventRecorder(
        EVENT_LOG_PATH,
        max_bytes=settings["max_bytes"],
        backup_count=settings["backup_count"],
        recent_limit=settings["recent_limit"],
    )
    LOGGER.info(
        "agent event logging initialized event_log_path=%s max_bytes=%s backup_count=%s",
        EVENT_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


def log_unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    LOGGER.critical("unhandled agent exception", exc_info=(exc_type, exc_value, exc_traceback))


def log_asyncio_exception(loop, context):
    del loop
    exception = context.get("exception")
    message = context.get("message", "asyncio loop exception")
    if exception is None:
        LOGGER.error("asyncio loop exception message=%s", message)
        return
    LOGGER.error("asyncio loop exception message=%s", message, exc_info=(type(exception), exception, exception.__traceback__))


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if "broker_base_url" not in config and "broker_v2_base_url" in config:
        config["broker_base_url"] = config["broker_v2_base_url"]
    return config


class RemoteStream(object):
    def __init__(self, agent, stream_id):
        self.agent = agent
        self.stream_id = int(stream_id)
        self.reader = None
        self.writer = None
        self.remote_task = None
        self.send_credit = INITIAL_WINDOW
        self.send_credit_event = asyncio.Event()
        self.send_credit_event.set()
        self.send_offset = 0
        self.recv_offset = 0
        self.fin_offset = None
        self.closed = False
        self.remote_eof_sent = False
        self.remote_read_eof = False
        self.pending_window = 0
        self.window_flush_task = None
        self.open_task = None
        self.recv_pending = {}
        self.recv_pending_bytes = 0

    async def open(self, host, port, mode):
        if mode != MODE_TCP:
            raise RuntimeError("unsupported open mode")
        trace("open stream=%s target=%s:%s" % (self.stream_id, host, port))
        record_event(
            "stream_open_requested",
            stream_id=self.stream_id,
            target_host=host,
            target_port=port,
            mode=mode,
        )
        self.reader, self.writer = await self.agent.open_origin_connection(host, port)
        transport = self.writer.transport
        if transport is not None:
            sock = transport.get_extra_info("socket")
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        await self.agent.transport.send_frame(LANE_CTL, Frame(FRAME_OPEN_OK, stream_id=self.stream_id))
        trace("open ok stream=%s" % self.stream_id)
        record_event(
            "stream_open_ok",
            stream_id=self.stream_id,
            target_host=host,
            target_port=port,
        )
        self.remote_task = asyncio.create_task(self.remote_to_helper())

    async def on_frame(self, frame):
        if frame.type_id == FRAME_WINDOW:
            self.send_credit += int(frame.offset)
            self.send_credit_event.set()
            trace("recv WINDOW stream=%s bytes=%s credit=%s" % (self.stream_id, int(frame.offset), self.send_credit))
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
        if frame.type_id == FRAME_FIN:
            self.fin_offset = int(frame.offset)
            trace("recv FIN stream=%s fin_offset=%s recv_offset=%s" % (self.stream_id, self.fin_offset, self.recv_offset))
            record_event(
                "stream_fin_received",
                stream_id=self.stream_id,
                fin_offset=self.fin_offset,
                recv_offset=self.recv_offset,
            )
            if self.recv_offset >= self.fin_offset:
                await self._finish_remote_write()
            return
        if frame.type_id == FRAME_RST:
            trace("recv RST stream=%s" % self.stream_id)
            record_event("stream_reset_received", stream_id=self.stream_id)
            await self.close()

    async def _accept_in_order_data(self, raw_offset, payload):
        self.recv_offset += len(payload)
        trace(
            "recv DATA stream=%s offset=%s bytes=%s recv_offset=%s pending=%s/%s" % (
                self.stream_id,
                raw_offset,
                len(payload),
                self.recv_offset,
                len(self.recv_pending),
                self.recv_pending_bytes,
            )
        )
        self.writer.write(payload)
        await self.writer.drain()
        await self.grant_window(len(payload))
        if self.fin_offset is not None and self.recv_offset >= self.fin_offset:
            await self._finish_remote_write()

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

    async def remote_to_helper(self):
        try:
            while not self.closed:
                if self.send_credit <= 0:
                    self.send_credit_event.clear()
                    await self.send_credit_event.wait()
                data = await self.reader.read(min(READ_CHUNK, self.send_credit))
                if not data:
                    self.remote_read_eof = True
                    trace("remote EOF stream=%s send_offset=%s" % (self.stream_id, self.send_offset))
                    record_event("remote_eof", stream_id=self.stream_id, send_offset=self.send_offset)
                    await self.flush_window()
                    await self.agent.transport.send_frame(
                        LANE_CTL,
                        Frame(FRAME_FIN, stream_id=self.stream_id, offset=self.send_offset),
                    )
                    trace("send FIN stream=%s offset=%s" % (self.stream_id, self.send_offset))
                    if self.remote_eof_sent:
                        await self.close()
                    return
                lane = self._data_lane(len(data))
                trace("send DATA stream=%s lane=%s offset=%s bytes=%s" % (self.stream_id, lane, self.send_offset, len(data)))
                await self.agent.transport.send_frame(
                    lane,
                    Frame(FRAME_DATA, stream_id=self.stream_id, offset=self.send_offset, payload=data),
                )
                self.send_offset += len(data)
                self.send_credit -= len(data)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.reset(str(error))

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
        await self.agent.transport.send_frame(
            LANE_CTL,
            Frame(FRAME_WINDOW, stream_id=self.stream_id, offset=value),
        )

    async def _flush_window_later(self):
        await asyncio.sleep(WINDOW_FLUSH_DELAY)
        await self.flush_window()

    async def reset(self, message):
        if self.closed:
            return
        if self.window_flush_task is not None and not self.window_flush_task.done():
            self.window_flush_task.cancel()
        record_event("stream_reset_sent", stream_id=self.stream_id, error=message)
        await self.agent.transport.send_frame(
            LANE_CTL,
            Frame(FRAME_RST, stream_id=self.stream_id, payload=make_error_payload(message)),
        )
        await self.close()

    async def _finish_remote_write(self):
        if self.writer is None or self.remote_eof_sent:
            return
        if self.writer.can_write_eof():
            self.writer.write_eof()
            await self.writer.drain()
        self.remote_eof_sent = True
        if self.remote_read_eof:
            await self.close()

    async def close(self):
        current_task = asyncio.current_task()
        already_closed = self.closed
        self.closed = True
        if self.open_task is not None and self.open_task is not current_task and not self.open_task.done():
            self.open_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.open_task
        self.open_task = None
        if self.remote_task is not None and self.remote_task is not current_task and not self.remote_task.done():
            self.remote_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.remote_task
        self.remote_task = None
        if self.writer is not None:
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
        self.writer = None
        self.reader = None
        if self.window_flush_task is not None and not self.window_flush_task.done():
            self.window_flush_task.cancel()
        self.window_flush_task = None
        if not already_closed:
            self.agent.release_stream(self.stream_id, self)

    def _data_lane(self, chunk_len):
        if self.send_offset < PRI_LIMIT and (self.send_offset + int(chunk_len)) <= PRI_LIMIT:
            return LANE_PRI
        return LANE_BULK


class AgentRuntime(object):
    def __init__(self, config):
        self.config = config
        self.transport = create_transport(config, "agent", config.get("peer_id", "agent"), self.on_frame)
        self.transport.event_handler = self._record_transport_event
        self.streams = {}
        self.open_connect_timeout_seconds = max(
            1.0, float(config.get("open_connect_timeout_seconds", DEFAULT_OPEN_CONNECT_TIMEOUT_SECONDS))
        )
        self.happy_eyeballs_delay_seconds = max(
            0.0,
            float(config.get("happy_eyeballs_delay_seconds", DEFAULT_HAPPY_EYEBALLS_DELAY_SECONDS)),
        )
        self.prefer_ipv4 = bool(config.get("prefer_ipv4", True))

    async def start(self):
        await self.transport.start()
        LOGGER.info(
            "agent started log_path=%s event_log_path=%s peer_id=%s transport_session=%s",
            RUNTIME_LOG_PATH or "stderr-only",
            EVENT_LOG_PATH or "disabled",
            self.config.get("peer_id", "agent"),
            self.transport.peer_session_id,
        )
        record_event(
            "agent_started",
            peer_id=self.config.get("peer_id", "agent"),
            transport_session_id=self.transport.peer_session_id,
        )
        try:
            await asyncio.Event().wait()
        finally:
            LOGGER.info("agent stopping transport_session=%s", self.transport.peer_session_id)
            record_event("agent_stopping", transport_session_id=self.transport.peer_session_id)
            await self.stop()
            LOGGER.info("agent stopped transport_session=%s", self.transport.peer_session_id)
            record_event("agent_stopped", transport_session_id=self.transport.peer_session_id)

    async def stop(self):
        for stream in list(self.streams.values()):
            with contextlib.suppress(Exception):
                await stream.close()
        await self.transport.stop()

    async def on_frame(self, frame, _lane):
        if frame.type_id == FRAME_OPEN:
            details = parse_open_payload(frame.payload)
            trace("recv OPEN stream=%s host=%s port=%s" % (frame.stream_id, details['host'], details['port']))
            record_event(
                "stream_open_received",
                stream_id=frame.stream_id,
                target_host=details["host"],
                target_port=details["port"],
                mode=details["mode"],
            )
            stream = RemoteStream(self, frame.stream_id)
            self.streams[frame.stream_id] = stream
            stream.open_task = asyncio.create_task(
                self._open_stream(stream, details["host"], details["port"], details["mode"])
            )
            return

        stream = self.streams.get(frame.stream_id)
        if stream is None:
            return
        await stream.on_frame(frame)
        if frame.type_id == FRAME_RST:
            self.streams.pop(frame.stream_id, None)

    def release_stream(self, stream_id, stream):
        if self.streams.get(stream_id) is stream:
            self.streams.pop(stream_id, None)
            record_event("stream_released", stream_id=stream_id)

    def _record_transport_event(self, event):
        record_event(**event)

    async def open_origin_connection(self, host, port):
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        has_ipv4 = any(info[0] == socket.AF_INET for info in infos)
        has_ipv6 = any(info[0] == socket.AF_INET6 for info in infos)

        async def attempt(label, **kwargs):
            trace(
                "origin connect start host=%s port=%s strategy=%s timeout=%s"
                % (host, port, label, self.open_connect_timeout_seconds)
            )
            record_event(
                "origin_connect_attempt",
                target_host=host,
                target_port=port,
                strategy=label,
                timeout_seconds=self.open_connect_timeout_seconds,
            )
            started_at = loop.time()
            try:
                result = await asyncio.wait_for(
                    asyncio.open_connection(host, port, **kwargs),
                    timeout=self.open_connect_timeout_seconds,
                )
            except Exception as error:
                trace(
                    "origin connect fail host=%s port=%s strategy=%s elapsed=%0.3f error=%r"
                    % (host, port, label, loop.time() - started_at, error)
                )
                record_event(
                    "origin_connect_failed",
                    target_host=host,
                    target_port=port,
                    strategy=label,
                    elapsed_ms=int((loop.time() - started_at) * 1000),
                    error=str(error),
                )
                raise
            trace(
                "origin connect ok host=%s port=%s strategy=%s elapsed=%0.3f"
                % (host, port, label, loop.time() - started_at)
            )
            record_event(
                "origin_connect_ok",
                target_host=host,
                target_port=port,
                strategy=label,
                elapsed_ms=int((loop.time() - started_at) * 1000),
            )
            return result

        last_error = None
        attempts = []
        if self.prefer_ipv4 and has_ipv4:
            attempts.append(("ipv4", {"family": socket.AF_INET}))
        attempts.append(
            (
                "happy",
                {
                    "happy_eyeballs_delay": self.happy_eyeballs_delay_seconds,
                    "interleave": 1,
                },
            )
        )
        if has_ipv6:
            attempts.append(("ipv6", {"family": socket.AF_INET6}))

        seen = set()
        for label, kwargs in attempts:
            key = tuple(sorted(kwargs.items()))
            if key in seen:
                continue
            seen.add(key)
            try:
                return await attempt(label, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error

        if last_error is not None:
            raise last_error
        raise RuntimeError("no origin connection strategy available")

    async def _open_stream(self, stream, host, port, mode):
        try:
            await stream.open(host, port, mode)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            trace("open fail stream=%s error=%s" % (stream.stream_id, error))
            record_event("stream_open_failed", stream_id=stream.stream_id, error=str(error))
            if self.streams.get(stream.stream_id) is stream:
                await self.transport.send_frame(
                    LANE_CTL,
                    Frame(FRAME_OPEN_FAIL, stream_id=stream.stream_id, payload=make_error_payload(str(error))),
                )
                self.streams.pop(stream.stream_id, None)


def main():
    parser = argparse.ArgumentParser(description="Twoman hidden agent")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    configure_runtime_logging(args.config, config)
    configure_event_logging(args.config, config)
    sys.excepthook = log_unhandled_exception
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(log_asyncio_exception)
        record_event(
            "agent_starting",
            peer_id=config.get("peer_id", "agent"),
            transport=config.get("transport", "http"),
        )
        loop.run_until_complete(AgentRuntime(config).start())
    except KeyboardInterrupt:
        LOGGER.info("agent interrupted by user")
        record_event("agent_interrupted")
        raise SystemExit(0)
    except Exception:
        LOGGER.exception("agent crashed")
        record_event("agent_crashed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
