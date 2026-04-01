#!/usr/bin/env python3

import argparse
import asyncio
from collections import deque
import contextlib
import json
import logging
import os
import sys
import time
import urllib.parse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from twoman_http import (
    expected_binary_media_type,
    extract_connection_identity,
    is_health_path,
    normalize_request_path,
    parse_lane_path,
    validate_binary_media_type,
)
from twoman_protocol import (
    Frame,
    FrameDecoder,
    FRAME_DATA,
    FRAME_OPEN,
    FRAME_OPEN_FAIL,
    FRAME_PING,
    FRAME_RST,
    FLAG_DATA_BULK,
    LANES,
    LANE_CTL,
    LANE_DATA,
    encode_frame,
    make_error_payload,
)
from runtime_diagnostics import (
    DurableEventRecorder,
    configure_component_logger,
    event_log_path,
    event_log_settings,
    runtime_log_path,
    runtime_log_settings,
)

TRACE_ENABLED = os.environ.get("TWOMAN_TRACE", "").strip().lower() in ("1", "true", "yes", "on", "debug", "verbose")
LOGGER = logging.getLogger("twoman.http_broker")
RUNTIME_LOG_PATH = ""
EVENT_LOG_PATH = ""
EVENT_RECORDER = None


def now_ms():
    return int(time.time() * 1000)


def trace(message):
    if not TRACE_ENABLED:
        return
    if LOGGER.handlers:
        LOGGER.debug(message)
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    sys.stderr.write("[%s] [broker] %s\n" % (timestamp, message))
    sys.stderr.flush()


def record_event(kind, **fields):
    if EVENT_RECORDER is None:
        return
    try:
        EVENT_RECORDER.record(kind, component="http-broker", **fields)
    except Exception:
        LOGGER.exception("http broker event log write failed kind=%s", kind)


def configure_runtime_logging(config_path, config):
    global RUNTIME_LOG_PATH
    if LOGGER.handlers:
        return
    RUNTIME_LOG_PATH = runtime_log_path(config_path, config, "http-broker.log")
    settings = runtime_log_settings(config)
    configure_component_logger(
        LOGGER,
        log_path=RUNTIME_LOG_PATH,
        trace_enabled=TRACE_ENABLED,
        runtime_log_max_bytes=settings["max_bytes"],
        runtime_log_backup_count=settings["backup_count"],
        console_prefix="http-broker",
    )
    LOGGER.info(
        "http broker logging initialized log_path=%s max_bytes=%s backup_count=%s",
        RUNTIME_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


def configure_event_logging(config_path, config):
    global EVENT_RECORDER, EVENT_LOG_PATH
    if EVENT_RECORDER is not None:
        return
    EVENT_LOG_PATH = event_log_path(config_path, config, "http-broker-events.ndjson")
    settings = event_log_settings(config)
    EVENT_RECORDER = DurableEventRecorder(
        EVENT_LOG_PATH,
        max_bytes=settings["max_bytes"],
        backup_count=settings["backup_count"],
        recent_limit=settings["recent_limit"],
    )
    LOGGER.info(
        "http broker event logging initialized event_log_path=%s max_bytes=%s backup_count=%s",
        EVENT_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


def log_unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    LOGGER.critical("unhandled http broker exception", exc_info=(exc_type, exc_value, exc_traceback))


def log_asyncio_exception(loop, context):
    del loop
    exception = context.get("exception")
    message = context.get("message", "asyncio loop exception")
    if exception is None:
        LOGGER.error("asyncio loop exception message=%s", message)
        return
    LOGGER.error("asyncio loop exception message=%s", message, exc_info=(type(exception), exception, exception.__traceback__))


def padded_payload(payload, minimum_size=1024):
    if len(payload) >= minimum_size:
        return payload
    parts = [payload]
    total = len(payload)
    while total < minimum_size:
        filler = encode_frame(Frame(FRAME_PING, offset=now_ms()))
        parts.append(filler)
        total += len(filler)
    return b"".join(parts)


class LaneQueue(object):
    def __init__(self):
        self.queue = asyncio.Queue()
        self.buffered_bytes = 0

    async def put(self, payload):
        self.buffered_bytes += len(payload)
        await self.queue.put(payload)

    async def get(self):
        payload = await self.queue.get()
        self.buffered_bytes = max(0, self.buffered_bytes - len(payload))
        return payload


class PeerState(object):
    def __init__(self, role, peer_label, peer_session_id):
        self.role = role
        self.peer_label = peer_label
        self.peer_session_id = peer_session_id
        self.last_seen_ms = now_ms()
        self.queues = dict((lane, LaneQueue()) for lane in LANES)
        self.data_pri_streak = 0
        self.data_bulk_wait_since_ms = 0
        self.active_streams = 0
        self.open_events_ms = deque()

    def touch(self):
        self.last_seen_ms = now_ms()

    def buffered_bytes_total(self):
        return sum(queue.buffered_bytes for queue in self.queues.values())


class StreamState(object):
    def __init__(self, helper_session_id, helper_peer_label, helper_stream_id, agent_session_id, agent_stream_id):
        self.helper_session_id = helper_session_id
        self.helper_peer_label = helper_peer_label
        self.helper_stream_id = int(helper_stream_id)
        self.agent_session_id = agent_session_id
        self.agent_stream_id = int(agent_stream_id)
        self.created_at_ms = now_ms()
        self.last_seen_ms = self.created_at_ms

    def touch(self):
        self.last_seen_ms = now_ms()


class BrokerState(object):
    def __init__(self, config):
        self.config = config
        self.client_tokens = set(config.get("client_tokens", []))
        self.agent_tokens = set(config.get("agent_tokens", []))
        self.peer_ttl_ms = int(config.get("peer_ttl_seconds", 90)) * 1000
        self.stream_ttl_ms = int(config.get("stream_ttl_seconds", 300)) * 1000
        self.max_lane_bytes = int(config.get("max_lane_bytes", 16 * 1024 * 1024))
        self.max_streams_per_peer_session = max(1, int(config.get("max_streams_per_peer_session", 256)))
        self.max_open_rate_per_peer_session = max(1, int(config.get("max_open_rate_per_peer_session", 120)))
        self.open_rate_window_ms = max(1000, int(config.get("open_rate_window_seconds", 10)) * 1000)
        self.max_peer_buffered_bytes = max(
            self.max_lane_bytes,
            int(config.get("max_peer_buffered_bytes", min(self.max_lane_bytes * 2, 32 * 1024 * 1024))),
        )
        self.peers = {}
        self.streams_by_helper = {}
        self.streams_by_agent = {}
        self.agent_session_id = ""
        self.agent_peer_label = ""
        self.next_agent_stream_id = 1
        self.metrics = {
            "down_responses": dict((lane, 0) for lane in LANES),
            "down_frames": dict((lane, 0) for lane in LANES),
            "down_bytes": dict((lane, 0) for lane in LANES),
            "down_hold_ms": dict((lane, 0) for lane in LANES),
            "down_pad_bytes": dict((lane, 0) for lane in LANES),
            "up_batches": dict((lane, 0) for lane in LANES),
            "up_frames": dict((lane, 0) for lane in LANES),
            "up_bytes": dict((lane, 0) for lane in LANES),
        }
        self.lock = asyncio.Lock()

    async def auth(self, role, token):
        if role == "helper":
            return token in self.client_tokens
        if role == "agent":
            return token in self.agent_tokens
        return False

    async def ensure_peer(self, role, peer_label, peer_session_id):
        async with self.lock:
            key = (role, peer_session_id)
            peer = self.peers.get(key)
            if peer is None:
                peer = PeerState(role, peer_label, peer_session_id)
                self.peers[key] = peer
                trace("peer online role=%s label=%s session=%s" % (role, peer_label, peer_session_id))
                record_event("peer_online", role=role, peer_label=peer_label, peer_session_id=peer_session_id)
            peer.touch()
            peer.peer_label = peer_label
            if role == "agent":
                self.agent_session_id = peer_session_id
                self.agent_peer_label = peer_label
            return peer

    async def queue_frame(self, role, peer_session_id, lane, frame):
        payload = encode_frame(frame)
        async with self.lock:
            peer = self.peers.get((role, peer_session_id))
            if peer is None:
                trace("drop frame type=%s stream=%s target=%s/%s lane=%s reason=no-peer" % (frame.type_id, frame.stream_id, role, peer_session_id, lane))
                record_event(
                    "queue_drop",
                    reason="no-peer",
                    role=role,
                    peer_session_id=peer_session_id,
                    lane=lane,
                    type_id=frame.type_id,
                    stream_id=frame.stream_id,
                )
                return False
            queue = peer.queues[lane]
            if queue.buffered_bytes >= self.max_lane_bytes:
                trace("drop frame type=%s stream=%s target=%s/%s lane=%s reason=queue-full buffered=%s" % (frame.type_id, frame.stream_id, role, peer_session_id, lane, queue.buffered_bytes))
                record_event(
                    "queue_drop",
                    reason="queue-full",
                    role=role,
                    peer_session_id=peer_session_id,
                    lane=lane,
                    type_id=frame.type_id,
                    stream_id=frame.stream_id,
                    buffered_bytes=queue.buffered_bytes,
                )
                return False
            total_buffered = peer.buffered_bytes_total()
            if total_buffered >= self.max_peer_buffered_bytes:
                trace("drop frame type=%s stream=%s target=%s/%s lane=%s reason=peer-buffer-full buffered=%s" % (frame.type_id, frame.stream_id, role, peer_session_id, lane, total_buffered))
                record_event(
                    "queue_drop",
                    reason="peer-buffer-full",
                    role=role,
                    peer_session_id=peer_session_id,
                    lane=lane,
                    type_id=frame.type_id,
                    stream_id=frame.stream_id,
                    buffered_bytes=total_buffered,
                )
                return False
        await peer.queues[lane].put(payload)
        if frame.type_id != FRAME_DATA:
            trace("queue frame type=%s stream=%s target=%s/%s lane=%s bytes=%s" % (frame.type_id, frame.stream_id, role, peer_session_id, lane, len(payload)))
            record_event(
                "queue_ctl",
                role=role,
                peer_session_id=peer_session_id,
                lane=lane,
                type_id=frame.type_id,
                stream_id=frame.stream_id,
                payload_bytes=len(frame.payload),
            )
        return True

    async def handle_frame(self, sender_role, sender_peer_session_id, lane, frame):
        if frame.type_id == FRAME_PING:
            return

        if frame.type_id == FRAME_OPEN and sender_role == "helper":
            await self._handle_open(sender_peer_session_id, frame)
            return

        async with self.lock:
            if sender_role == "helper":
                stream = self.streams_by_helper.get((sender_peer_session_id, frame.stream_id))
            else:
                stream = self.streams_by_agent.get(frame.stream_id)
            if stream is None:
                trace("drop frame type=%s stream=%s from=%s/%s lane=%s reason=unknown-stream" % (frame.type_id, frame.stream_id, sender_role, sender_peer_session_id, lane))
                record_event(
                    "frame_drop",
                    reason="unknown-stream",
                    sender_role=sender_role,
                    sender_peer_session_id=sender_peer_session_id,
                    lane=lane,
                    type_id=frame.type_id,
                    stream_id=frame.stream_id,
                )
                return
            stream.touch()
            if sender_role == "helper":
                target_role = "agent"
                target_peer_session_id = stream.agent_session_id
                outbound_stream_id = stream.agent_stream_id
            else:
                target_role = "helper"
                target_peer_session_id = stream.helper_session_id
                outbound_stream_id = stream.helper_stream_id

        if not target_peer_session_id:
            return

        outbound_frame = Frame(
            frame.type_id,
            stream_id=outbound_stream_id,
            offset=frame.offset,
            payload=frame.payload,
            flags=frame.flags,
        )
        target_lane = lane if frame.type_id == FRAME_DATA else LANE_CTL
        queued = await self.queue_frame(target_role, target_peer_session_id, target_lane, outbound_frame)
        if not queued and sender_role == "helper":
            await self.queue_frame(
                "helper",
                sender_peer_session_id,
                LANE_CTL,
                Frame(FRAME_RST, stream_id=frame.stream_id, payload=make_error_payload("broker queue full")),
            )
        elif frame.type_id != FRAME_DATA:
            record_event(
                "frame_forward",
                sender_role=sender_role,
                sender_peer_session_id=sender_peer_session_id,
                target_role=target_role,
                target_peer_session_id=target_peer_session_id,
                source_lane=lane,
                type_id=frame.type_id,
                source_stream_id=frame.stream_id,
                target_stream_id=outbound_stream_id,
            )

        if frame.type_id == FRAME_RST:
            async with self.lock:
                self._drop_stream_locked(stream)

    async def _handle_open(self, helper_session_id, frame):
        open_error = ""
        async with self.lock:
            agent_session_id = self.agent_session_id
            helper_peer = self.peers.get(("helper", helper_session_id))
            helper_peer_label = helper_peer.peer_label if helper_peer is not None else helper_session_id
            if helper_peer is None:
                open_error = "helper session unavailable"
            else:
                open_error = self._reserve_helper_open_locked(helper_peer)
            if not agent_session_id or ("agent", agent_session_id) not in self.peers:
                agent_session_id = ""
            if agent_session_id and not open_error:
                agent_stream_id = self.next_agent_stream_id
                self.next_agent_stream_id += 1
                stream = StreamState(helper_session_id, helper_peer_label, frame.stream_id, agent_session_id, agent_stream_id)
                self.streams_by_helper[(helper_session_id, frame.stream_id)] = stream
                self.streams_by_agent[agent_stream_id] = stream
                helper_peer.active_streams += 1
                agent_peer = self.peers.get(("agent", agent_session_id))
                if agent_peer is not None:
                    agent_peer.active_streams += 1
                trace("open helper=%s/%s helper_stream=%s agent_session=%s agent_stream=%s" % (
                    helper_peer_label,
                    helper_session_id,
                    frame.stream_id,
                    agent_session_id,
                    agent_stream_id,
                ))
                record_event(
                    "open_map",
                    helper_session_id=helper_session_id,
                    helper_peer_label=helper_peer_label,
                    helper_stream_id=frame.stream_id,
                    agent_session_id=agent_session_id,
                    agent_stream_id=agent_stream_id,
                )
        if open_error:
            trace("open fail stream=%s helper=%s reason=%s" % (frame.stream_id, helper_session_id, open_error))
            record_event(
                "open_fail",
                helper_session_id=helper_session_id,
                helper_stream_id=frame.stream_id,
                reason=open_error,
            )
            await self.queue_frame(
                "helper",
                helper_session_id,
                LANE_CTL,
                Frame(FRAME_OPEN_FAIL, stream_id=frame.stream_id, payload=make_error_payload(open_error)),
            )
            return
        if not agent_session_id:
            trace("open fail stream=%s helper=%s reason=no-agent" % (frame.stream_id, helper_session_id))
            record_event(
                "open_fail",
                helper_session_id=helper_session_id,
                helper_stream_id=frame.stream_id,
                reason="no-agent",
            )
            await self.queue_frame(
                "helper",
                helper_session_id,
                LANE_CTL,
                Frame(FRAME_OPEN_FAIL, stream_id=frame.stream_id, payload=make_error_payload("hidden agent unavailable")),
            )
            return
        await self.queue_frame("agent", agent_session_id, LANE_CTL, Frame(FRAME_OPEN, stream_id=agent_stream_id, offset=frame.offset, payload=frame.payload, flags=frame.flags))

    def _drop_stream_locked(self, stream):
        self.streams_by_helper.pop((stream.helper_session_id, stream.helper_stream_id), None)
        self.streams_by_agent.pop(stream.agent_stream_id, None)
        record_event(
            "drop_stream",
            helper_session_id=stream.helper_session_id,
            helper_peer_label=stream.helper_peer_label,
            helper_stream_id=stream.helper_stream_id,
            agent_session_id=stream.agent_session_id,
            agent_stream_id=stream.agent_stream_id,
        )
        helper_peer = self.peers.get(("helper", stream.helper_session_id))
        if helper_peer is not None and helper_peer.active_streams > 0:
            helper_peer.active_streams -= 1
        agent_peer = self.peers.get(("agent", stream.agent_session_id))
        if agent_peer is not None and agent_peer.active_streams > 0:
            agent_peer.active_streams -= 1

    def _reserve_helper_open_locked(self, helper_peer):
        current_ms = now_ms()
        window_start = current_ms - self.open_rate_window_ms
        while helper_peer.open_events_ms and helper_peer.open_events_ms[0] < window_start:
            helper_peer.open_events_ms.popleft()
        if helper_peer.active_streams >= self.max_streams_per_peer_session:
            return "too many concurrent streams"
        if len(helper_peer.open_events_ms) >= self.max_open_rate_per_peer_session:
            return "too many new streams"
        helper_peer.open_events_ms.append(current_ms)
        return ""

    async def cleanup(self):
        peer_cutoff = now_ms() - self.peer_ttl_ms
        stream_cutoff = now_ms() - self.stream_ttl_ms
        resets = []
        async with self.lock:
            stale_peers = [key for key, peer in self.peers.items() if peer.last_seen_ms < peer_cutoff]
            for key in stale_peers:
                role, peer_session_id = key
                record_event("cleanup_peer_expired", role=role, peer_session_id=peer_session_id)
                stale_streams_for_peer = [
                    stream
                    for stream in self.streams_by_agent.values()
                    if stream.helper_session_id == peer_session_id or stream.agent_session_id == peer_session_id
                ]
                for stream in stale_streams_for_peer:
                    resets.extend(self._reset_frames_for_stale_peer_locked(role, peer_session_id, stream))
                    self._drop_stream_locked(stream)
                del self.peers[key]
                if role == "agent" and self.agent_session_id == peer_session_id:
                    self.agent_session_id = ""
                    self.agent_peer_label = ""

            stale_streams = [stream for stream in self.streams_by_agent.values() if stream.last_seen_ms < stream_cutoff]
            for stream in stale_streams:
                record_event(
                    "cleanup_stream_expired",
                    helper_session_id=stream.helper_session_id,
                    helper_stream_id=stream.helper_stream_id,
                    agent_session_id=stream.agent_session_id,
                    agent_stream_id=stream.agent_stream_id,
                )
                resets.extend(self._reset_frames_for_stale_stream_locked(stream))
                self._drop_stream_locked(stream)

        for role, peer_session_id, lane, frame in resets:
            await self.queue_frame(role, peer_session_id, lane, frame)

    def _reset_frames_for_stale_peer_locked(self, stale_role, stale_peer_session_id, stream):
        payload = make_error_payload("peer expired")
        resets = []
        if stale_role == "helper" and stream.agent_session_id and ("agent", stream.agent_session_id) in self.peers:
            resets.append((
                "agent",
                stream.agent_session_id,
                LANE_CTL,
                Frame(FRAME_RST, stream_id=stream.agent_stream_id, payload=payload),
            ))
        if stale_role == "agent" and stream.helper_session_id and ("helper", stream.helper_session_id) in self.peers:
            resets.append((
                "helper",
                stream.helper_session_id,
                LANE_CTL,
                Frame(FRAME_RST, stream_id=stream.helper_stream_id, payload=payload),
            ))
        return resets

    def _reset_frames_for_stale_stream_locked(self, stream):
        payload = make_error_payload("stream expired")
        resets = []
        if stream.helper_session_id and ("helper", stream.helper_session_id) in self.peers:
            resets.append((
                "helper",
                stream.helper_session_id,
                LANE_CTL,
                Frame(FRAME_RST, stream_id=stream.helper_stream_id, payload=payload),
            ))
        if stream.agent_session_id and ("agent", stream.agent_session_id) in self.peers:
            resets.append((
                "agent",
                stream.agent_session_id,
                LANE_CTL,
                Frame(FRAME_RST, stream_id=stream.agent_stream_id, payload=payload),
            ))
        return resets

    async def stats(self):
        async with self.lock:
            buffered = {}
            for lane in LANES:
                buffered[lane] = sum(peer.queues[lane].buffered_bytes for peer in self.peers.values())
            return {
                "peers": len(self.peers),
                "streams": len(self.streams_by_agent),
                "agent_peer_label": self.agent_peer_label,
                "agent_session_id": self.agent_session_id,
                "log_paths": {
                    "runtime": RUNTIME_LOG_PATH,
                    "events": EVENT_LOG_PATH,
                },
                "max_streams_per_peer_session": self.max_streams_per_peer_session,
                "max_open_rate_per_peer_session": self.max_open_rate_per_peer_session,
                "open_rate_window_seconds": int(self.open_rate_window_ms / 1000),
                "max_peer_buffered_bytes": self.max_peer_buffered_bytes,
                "buffered_ctl_bytes": buffered[LANE_CTL],
                "buffered_pri_bytes": buffered["pri"],
                "buffered_bulk_bytes": buffered["bulk"],
                "metrics": self.metrics,
                "recent_events": EVENT_RECORDER.snapshot(64) if EVENT_RECORDER is not None else [],
            }

    def lane_profile(self, lane):
        if lane == LANE_CTL:
            return {"max_bytes": 4096, "max_frames": 8, "hold_ms": 1, "pad_min": 1024}
        if lane == "pri":
            return {"max_bytes": 16384, "max_frames": 8, "hold_ms": 3, "pad_min": 1024}
        return {"max_bytes": 65536, "max_frames": 16, "hold_ms": 8, "pad_min": 0}


class AsyncBrokerServer(object):
    def __init__(self, host, port, config, unix_socket_path=None):
        self.host = host
        self.port = int(port) if port is not None else 0
        self.unix_socket_path = unix_socket_path
        self.config = config
        self.binary_media_type = expected_binary_media_type(config)
        self.state = BrokerState(config)
        self.down_wait_ms = self._normalize_down_wait_ms(config.get("down_wait_ms"))
        self.streaming_ctl_down_helper = bool(config.get("streaming_ctl_down_helper", True))
        self.streaming_data_down_helper = bool(config.get("streaming_data_down_helper", True))
        self.server = None
        self.cleanup_task = None

    async def start(self):
        if self.unix_socket_path:
            with contextlib.suppress(FileNotFoundError):
                os.remove(self.unix_socket_path)
            self.server = await asyncio.start_unix_server(self.handle_connection, path=self.unix_socket_path)
        else:
            self.server = await asyncio.start_server(self.handle_connection, self.host, self.port)
        loop = asyncio.get_event_loop()
        self.cleanup_task = loop.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(10.0)
            await self.state.cleanup()

    async def handle_connection(self, reader, writer):
        try:
            request_line, headers, body = await self._read_request(reader)
            method, raw_path, _version = request_line.split(" ", 2)
            parsed = urllib.parse.urlsplit(raw_path)
            request_path = normalize_request_path(
                parsed.path,
                self.config.get("base_uri") or self.config.get("route_base_path"),
            )
            if is_health_path(request_path, self.config.get("health_template")):
                await self._send_json(writer, 200, {"ok": True, "stats": await self.state.stats()})
                return

            route = parse_lane_path(request_path, self.config.get("route_template"))
            if route is None:
                await self._send_json(writer, 404, {"error": "not found"})
                return

            lane = route.get("lane", "")
            direction = route.get("direction", "")
            if lane not in (list(LANES) + [LANE_DATA]) or direction not in ("up", "down"):
                await self._send_json(writer, 404, {"error": "not found"})
                return
            identity = extract_connection_identity(headers, self.config)
            role = identity["role"]
            peer_label = identity["peer_label"]
            peer_session_id = identity["peer_session_id"]
            token = identity["token"]
            if not role or not peer_label or not peer_session_id or not await self.state.auth(role, token):
                await self._send_json(writer, 403, {"error": "invalid role or token"})
                return
            peer = await self.state.ensure_peer(role, peer_label, peer_session_id)
            if method == "GET" and direction == "down":
                if lane == LANE_DATA:
                    if role == "helper" and self.streaming_data_down_helper:
                        await self._handle_streaming_data_down(writer, peer)
                    else:
                        await self._handle_data_down(writer, peer)
                elif lane == LANE_CTL and role == "helper" and self.streaming_ctl_down_helper:
                    await self._handle_helper_ctl_down(writer, peer)
                elif lane == LANE_CTL:
                    await self._handle_ctl_down(writer, peer)
                else:
                    await self._handle_down(writer, peer, lane)
                return
            if method == "POST" and direction == "up":
                validate_binary_media_type(headers.get("content-type", ""), self.config)
                await self._handle_up(writer, role, peer_session_id, lane, body)
                return
            await self._send_json(writer, 405, {"error": "method not allowed"})
        except asyncio.IncompleteReadError:
            pass
        except Exception as error:
            try:
                await self._send_json(writer, 500, {"error": str(error)})
            except Exception:
                pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    def _normalize_down_wait_ms(self, configured):
        values = {"ctl": 1000, "data": 1000}
        if isinstance(configured, dict):
            for lane in ("ctl", "data"):
                try:
                    if lane in configured:
                        values[lane] = max(0, int(configured[lane]))
                except (TypeError, ValueError):
                    continue
        return values

    async def _next_ctl_payload(self, peer, wait_timeout_ms):
        queue = peer.queues[LANE_CTL]
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=max(0.0, float(wait_timeout_ms) / 1000.0))
        except asyncio.TimeoutError:
            return padded_payload(encode_frame(Frame(FRAME_PING, offset=now_ms())), minimum_size=1024)
        peer.touch()
        profile = self.state.lane_profile(LANE_CTL)
        payloads = [payload]
        total = len(payload)
        frames = 1
        deadline = asyncio.get_event_loop().time() + (float(profile["hold_ms"]) / 1000.0)
        while total < int(profile["max_bytes"]) and frames < int(profile["max_frames"]):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            payloads.append(payload)
            total += len(payload)
            frames += 1
        body = b"".join(payloads)
        return padded_payload(body, minimum_size=int(profile["pad_min"]))

    async def _handle_down(self, writer, peer, lane):
        trace("down open role=%s peer=%s session=%s lane=%s" % (peer.role, peer.peer_label, peer.peer_session_id, lane))
        queue = peer.queues[lane]
        profile = self.state.lane_profile(lane)
        hold_started = asyncio.get_event_loop().time()
        payloads = [await queue.get()]
        peer.touch()
        total = len(payloads[0])
        frames = 1
        deadline = asyncio.get_event_loop().time() + (float(profile["hold_ms"]) / 1000.0)
        while total < int(profile["max_bytes"]) and frames < int(profile["max_frames"]):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            payloads.append(payload)
            total += len(payload)
            frames += 1
        body = b"".join(payloads)
        padded = padded_payload(body, minimum_size=int(profile["pad_min"])) if int(profile["pad_min"]) > 0 else body
        pad_bytes = max(0, len(padded) - len(body))
        if pad_bytes:
            trace("pad lane=%s role=%s peer=%s session=%s from=%s to=%s" % (lane, peer.role, peer.peer_label, peer.peer_session_id, len(body), len(padded)))
        body = padded
        hold_ms = max(0, int((asyncio.get_event_loop().time() - hold_started) * 1000))
        self.state.metrics["down_responses"][lane] += 1
        self.state.metrics["down_frames"][lane] += frames
        self.state.metrics["down_bytes"][lane] += len(body)
        self.state.metrics["down_hold_ms"][lane] += hold_ms
        self.state.metrics["down_pad_bytes"][lane] += pad_bytes
        trace("down send role=%s peer=%s session=%s lane=%s frames=%s bytes=%s hold_ms=%s" % (peer.role, peer.peer_label, peer.peer_session_id, lane, frames, len(body), hold_ms))
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + ("Content-Type: %s\r\n" % self.binary_media_type).encode("ascii")
                + b"Cache-Control: no-store\r\n"
                + b"Connection: close\r\n"
                + ("Content-Length: %d\r\n\r\n" % len(body)).encode("ascii")
            )
            writer.write(body)
            await writer.drain()
        except Exception as error:
            trace("down error role=%s peer=%s session=%s lane=%s error=%r" % (peer.role, peer.peer_label, peer.peer_session_id, lane, error))
            record_event(
                "down_error",
                role=peer.role,
                peer_label=peer.peer_label,
                peer_session_id=peer.peer_session_id,
                lane=lane,
                error=str(error),
            )
            raise

    async def _handle_streaming_data_down(self, writer, peer):
        trace("down stream open role=%s peer=%s session=%s lane=data" % (peer.role, peer.peer_label, peer.peer_session_id))
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + ("Content-Type: %s\r\n" % self.binary_media_type).encode("ascii")
                + b"Cache-Control: no-store\r\n"
                + b"Transfer-Encoding: chunked\r\n"
                + b"Connection: close\r\n\r\n"
            )
            await writer.drain()
            last_send = asyncio.get_event_loop().time()
            while True:
                payload, source_lane, frames, hold_ms, pad_bytes = await self._next_data_payload(
                    peer,
                    wait_timeout_ms=self.down_wait_ms["data"],
                )
                if payload is None:
                    heartbeat = padded_payload(encode_frame(Frame(FRAME_PING, offset=now_ms())), minimum_size=1024)
                    await self._write_chunk(writer, heartbeat)
                    last_send = asyncio.get_event_loop().time()
                    continue
                self.state.metrics["down_responses"][source_lane] += 1
                self.state.metrics["down_frames"][source_lane] += frames
                self.state.metrics["down_bytes"][source_lane] += len(payload)
                self.state.metrics["down_hold_ms"][source_lane] += hold_ms
                self.state.metrics["down_pad_bytes"][source_lane] += pad_bytes
                trace("down stream send role=%s peer=%s session=%s source=%s frames=%s bytes=%s hold_ms=%s" % (
                    peer.role,
                    peer.peer_label,
                    peer.peer_session_id,
                    source_lane,
                    frames,
                    len(payload),
                    hold_ms,
                ))
                await self._write_chunk(writer, payload)
                last_send = asyncio.get_event_loop().time()
        except Exception as error:
            trace("down stream error role=%s peer=%s session=%s lane=data error=%r" % (peer.role, peer.peer_label, peer.peer_session_id, error))
            record_event(
                "down_error",
                role=peer.role,
                peer_label=peer.peer_label,
                peer_session_id=peer.peer_session_id,
                lane="data",
                error=str(error),
            )
            raise

    async def _handle_data_down(self, writer, peer):
        trace("down open role=%s peer=%s session=%s lane=data" % (peer.role, peer.peer_label, peer.peer_session_id))
        payload, source_lane, frames, hold_ms, pad_bytes = await self._next_data_payload(
            peer,
            wait_timeout_ms=self.down_wait_ms["data"],
        )
        if payload is None:
            payload = padded_payload(encode_frame(Frame(FRAME_PING, offset=now_ms())), minimum_size=1024)
            source_lane = "pri"
            frames = 1
            hold_ms = 0
            pad_bytes = max(0, len(payload) - len(encode_frame(Frame(FRAME_PING, offset=0))))
        self.state.metrics["down_responses"][source_lane] += 1
        self.state.metrics["down_frames"][source_lane] += frames
        self.state.metrics["down_bytes"][source_lane] += len(payload)
        self.state.metrics["down_hold_ms"][source_lane] += hold_ms
        self.state.metrics["down_pad_bytes"][source_lane] += pad_bytes
        trace("down send role=%s peer=%s session=%s lane=data source=%s frames=%s bytes=%s hold_ms=%s" % (
            peer.role,
            peer.peer_label,
            peer.peer_session_id,
            source_lane,
            frames,
            len(payload),
            hold_ms,
        ))
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + ("Content-Type: %s\r\n" % self.binary_media_type).encode("ascii")
            + b"Cache-Control: no-store\r\n"
            + ("Content-Length: %d\r\n\r\n" % len(payload)).encode("ascii")
        )
        writer.write(payload)
        await writer.drain()

    async def _handle_ctl_down(self, writer, peer):
        trace("down open role=%s peer=%s session=%s lane=ctl" % (peer.role, peer.peer_label, peer.peer_session_id))
        payload = await self._next_ctl_payload(peer, self.down_wait_ms["ctl"])
        self.state.metrics["down_responses"][LANE_CTL] += 1
        self.state.metrics["down_frames"][LANE_CTL] += 1
        self.state.metrics["down_bytes"][LANE_CTL] += len(payload)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + ("Content-Type: %s\r\n" % self.binary_media_type).encode("ascii")
            + b"Cache-Control: no-store\r\n"
            + ("Content-Length: %d\r\n\r\n" % len(payload)).encode("ascii")
        )
        writer.write(payload)
        await writer.drain()

    async def _handle_helper_ctl_down(self, writer, peer):
        trace("down stream open role=%s peer=%s session=%s lane=ctl" % (peer.role, peer.peer_label, peer.peer_session_id))
        queue = peer.queues[LANE_CTL]
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + ("Content-Type: %s\r\n" % self.binary_media_type).encode("ascii")
                + b"Cache-Control: no-store\r\n"
                + b"Transfer-Encoding: chunked\r\n"
                + b"Connection: close\r\n\r\n"
            )
            await writer.drain()
            while True:
                try:
                    payload = await self._next_ctl_payload(peer, self.down_wait_ms["ctl"])
                    self.state.metrics["down_responses"][LANE_CTL] += 1
                    self.state.metrics["down_frames"][LANE_CTL] += 1
                    self.state.metrics["down_bytes"][LANE_CTL] += len(payload)
                    trace("down stream send role=%s peer=%s session=%s source=ctl frames=1 bytes=%s hold_ms=0" % (
                        peer.role,
                        peer.peer_label,
                        peer.peer_session_id,
                        len(payload),
                    ))
                    await self._write_chunk(writer, payload)
                except asyncio.TimeoutError:
                    heartbeat = padded_payload(encode_frame(Frame(FRAME_PING, offset=now_ms())), minimum_size=1024)
                    await self._write_chunk(writer, heartbeat)
        except Exception as error:
            trace("down stream error role=%s peer=%s session=%s lane=ctl error=%r" % (peer.role, peer.peer_label, peer.peer_session_id, error))
            record_event(
                "down_error",
                role=peer.role,
                peer_label=peer.peer_label,
                peer_session_id=peer.peer_session_id,
                lane="ctl",
                error=str(error),
            )
            raise

    async def _next_data_payload(self, peer, wait_timeout_ms=10000):
        pri_queue = peer.queues["pri"]
        bulk_queue = peer.queues["bulk"]
        profile = None
        source_lane = None
        try:
            first = pri_queue.queue.get_nowait()
            pri_queue.buffered_bytes = max(0, pri_queue.buffered_bytes - len(first))
            source_lane = "pri"
        except asyncio.QueueEmpty:
            try:
                first = bulk_queue.queue.get_nowait()
                bulk_queue.buffered_bytes = max(0, bulk_queue.buffered_bytes - len(first))
                source_lane = "bulk"
            except asyncio.QueueEmpty:
                loop = asyncio.get_event_loop()
                pri_task = loop.create_task(pri_queue.get())
                bulk_task = loop.create_task(bulk_queue.get())
                done, pending = await asyncio.wait(
                    [pri_task, bulk_task],
                    timeout=max(0.0, float(wait_timeout_ms) / 1000.0),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if not done:
                    return None, None, 0, 0, 0
                completed = done.pop()
                first = completed.result()
                source_lane = "pri" if completed is pri_task else "bulk"
        peer.touch()
        profile = self.state.lane_profile(source_lane)
        payloads = [first]
        total = len(first)
        frames = 1
        hold_started = asyncio.get_event_loop().time()
        deadline = asyncio.get_event_loop().time() + (float(profile["hold_ms"]) / 1000.0)
        queue = peer.queues[source_lane]
        while total < int(profile["max_bytes"]) and frames < int(profile["max_frames"]):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            payloads.append(payload)
            total += len(payload)
            frames += 1
        body = b"".join(payloads)
        padded = padded_payload(body, minimum_size=int(profile["pad_min"])) if int(profile["pad_min"]) > 0 else body
        pad_bytes = max(0, len(padded) - len(body))
        hold_ms = max(0, int((asyncio.get_event_loop().time() - hold_started) * 1000))
        return padded, source_lane, frames, hold_ms, pad_bytes

    async def _write_chunk(self, writer, body):
        if not body:
            return
        writer.write(("%x\r\n" % len(body)).encode("ascii"))
        writer.write(body)
        writer.write(b"\r\n")
        await writer.drain()

    async def _handle_up(self, writer, role, peer_session_id, lane, body):
        decoder = FrameDecoder()
        frame_count = 0
        batch_lane = lane if lane != LANE_DATA else "pri"
        for frame in decoder.feed(body):
            frame_count += 1
            frame_lane = lane
            if lane == LANE_DATA and frame.type_id == FRAME_DATA:
                frame_lane = "bulk" if (frame.flags & FLAG_DATA_BULK) else "pri"
                if frame_lane == "bulk":
                    batch_lane = "bulk"
            elif lane == LANE_DATA:
                frame_lane = LANE_CTL
            if frame.type_id != FRAME_DATA:
                trace("up recv role=%s session=%s lane=%s type=%s stream=%s bytes=%s" % (role, peer_session_id, frame_lane, frame.type_id, frame.stream_id, len(frame.payload)))
            await self.state.handle_frame(role, peer_session_id, frame_lane, frame)
            self.state.metrics["up_frames"][frame_lane] += 1
            self.state.metrics["up_bytes"][frame_lane] += len(encode_frame(frame))
        self.state.metrics["up_batches"][batch_lane] += 1
        record_event(
            "up_batch",
            role=role,
            peer_session_id=peer_session_id,
            lane=batch_lane,
            frame_count=frame_count,
            body_bytes=len(body),
        )
        await self._send_json(writer, 200, {"ok": True})

    async def _send_json(self, writer, status, payload):
        body = json.dumps(payload).encode("utf-8")
        writer.write(
            ("HTTP/1.1 %d OK\r\n" % int(status)).encode("ascii")
            + b"Content-Type: application/json\r\n"
            + ("Content-Length: %d\r\n" % len(body)).encode("ascii")
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()

    async def _read_request(self, reader):
        head = await reader.readuntil(b"\r\n\r\n")
        header_blob = head.decode("iso-8859-1")
        lines = header_blob.split("\r\n")
        request_line = lines[0]
        headers = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0") or "0")
        body = b""
        if length > 0:
            body = await reader.readexactly(length)
        return request_line, headers, body


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    parser = argparse.ArgumentParser(description="Twoman localhost broker")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int)
    parser.add_argument("--unix-socket")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    if not args.unix_socket and args.listen_port is None:
        parser.error("--listen-port is required unless --unix-socket is provided")
    config = load_config(args.config)
    global TRACE_ENABLED
    if not TRACE_ENABLED and config.get("trace_enabled"):
        TRACE_ENABLED = True
    configure_runtime_logging(args.config, config)
    configure_event_logging(args.config, config)
    sys.excepthook = log_unhandled_exception
    server = AsyncBrokerServer(args.listen_host, args.listen_port, config, unix_socket_path=args.unix_socket)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(log_asyncio_exception)
    LOGGER.info(
        "http broker starting listen_host=%s listen_port=%s unix_socket=%s log_path=%s event_log_path=%s",
        args.listen_host,
        args.listen_port,
        args.unix_socket or "",
        RUNTIME_LOG_PATH or "stderr-only",
        EVENT_LOG_PATH or "disabled",
    )
    record_event(
        "broker_starting",
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        unix_socket=args.unix_socket or "",
    )
    loop.run_until_complete(server.start())
    LOGGER.info("http broker started listen_host=%s listen_port=%s unix_socket=%s", args.listen_host, args.listen_port, args.unix_socket or "")
    record_event("broker_started", listen_host=args.listen_host, listen_port=args.listen_port, unix_socket=args.unix_socket or "")
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        LOGGER.info("http broker interrupted by user")
        record_event("broker_interrupted")
    finally:
        LOGGER.info("http broker stopping")
        record_event("broker_stopping")
        if server.cleanup_task is not None:
            server.cleanup_task.cancel()
            with contextlib.suppress(Exception):
                loop.run_until_complete(server.cleanup_task)
        if server.server is not None:
            server.server.close()
            with contextlib.suppress(Exception):
                loop.run_until_complete(server.server.wait_closed())
        if args.unix_socket:
            with contextlib.suppress(FileNotFoundError):
                os.remove(args.unix_socket)
        LOGGER.info("http broker stopped")
        record_event("broker_stopped")
        loop.close()


if __name__ == "__main__":
    main()
