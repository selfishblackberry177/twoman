#!/usr/bin/env python3

import json
import logging
import os
import queue
import sys
import threading
import time
import urllib.parse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
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
CONFIG_PATH = os.environ.get("TWOMAN_CONFIG_PATH", os.path.join(CURRENT_DIR, "config.json"))
DOWN_POLL_TIMEOUT_SECONDS = max(0.01, float(os.environ.get("TWOMAN_DOWN_POLL_TIMEOUT_SECONDS", "0.25")))
LOGGER = logging.getLogger("twoman.passenger_broker")
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
    sys.stderr.write("[%s] [passenger-broker] %s\n" % (timestamp, message))
    sys.stderr.flush()


def record_event(kind, **fields):
    if EVENT_RECORDER is None:
        return
    try:
        EVENT_RECORDER.record(kind, component="passenger-broker", **fields)
    except Exception:
        LOGGER.exception("passenger broker event log write failed kind=%s", kind)


def configure_runtime_logging(config_path, config):
    global RUNTIME_LOG_PATH
    if LOGGER.handlers:
        return
    RUNTIME_LOG_PATH = runtime_log_path(config_path, config, "passenger-broker.log")
    settings = runtime_log_settings(config)
    configure_component_logger(
        LOGGER,
        log_path=RUNTIME_LOG_PATH,
        trace_enabled=TRACE_ENABLED,
        runtime_log_max_bytes=settings["max_bytes"],
        runtime_log_backup_count=settings["backup_count"],
        console_prefix="passenger-broker",
    )
    LOGGER.info(
        "passenger broker logging initialized log_path=%s max_bytes=%s backup_count=%s",
        RUNTIME_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


def configure_event_logging(config_path, config):
    global EVENT_RECORDER, EVENT_LOG_PATH
    if EVENT_RECORDER is not None:
        return
    EVENT_LOG_PATH = event_log_path(config_path, config, "passenger-broker-events.ndjson")
    settings = event_log_settings(config)
    EVENT_RECORDER = DurableEventRecorder(
        EVENT_LOG_PATH,
        max_bytes=settings["max_bytes"],
        backup_count=settings["backup_count"],
        recent_limit=settings["recent_limit"],
    )
    LOGGER.info(
        "passenger broker event logging initialized event_log_path=%s max_bytes=%s backup_count=%s",
        EVENT_LOG_PATH,
        settings["max_bytes"],
        settings["backup_count"],
    )


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
        self.queue = queue.Queue()
        self.buffered_bytes = 0
        self.lock = threading.Lock()

    def put(self, payload):
        with self.lock:
            self.buffered_bytes += len(payload)
        self.queue.put(payload)

    def get(self, timeout=None):
        payload = self.queue.get(timeout=timeout)
        with self.lock:
            self.buffered_bytes = max(0, self.buffered_bytes - len(payload))
        return payload

    def get_nowait(self):
        payload = self.queue.get_nowait()
        with self.lock:
            self.buffered_bytes = max(0, self.buffered_bytes - len(payload))
        return payload


class PeerState(object):
    def __init__(self, role, peer_label, peer_session_id):
        self.role = role
        self.peer_label = peer_label
        self.peer_session_id = peer_session_id
        self.last_seen_ms = now_ms()
        self.queues = dict((lane, LaneQueue()) for lane in LANES)
        self.active_streams = 0
        self.open_events_ms = []
        self.data_condition = threading.Condition()

    def touch(self):
        self.last_seen_ms = now_ms()

    def buffered_bytes_total(self):
        return sum(queue_obj.buffered_bytes for queue_obj in self.queues.values())


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
        self.lock = threading.Lock()
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

    def auth(self, role, token):
        if role == "helper":
            return token in self.client_tokens
        if role == "agent":
            return token in self.agent_tokens
        return False

    def ensure_peer(self, role, peer_label, peer_session_id):
        with self.lock:
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

    def queue_frame(self, role, peer_session_id, lane, frame):
        payload = encode_frame(frame)
        with self.lock:
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
            queue_obj = peer.queues[lane]
            if queue_obj.buffered_bytes >= self.max_lane_bytes:
                trace("drop frame type=%s stream=%s target=%s/%s lane=%s reason=queue-full buffered=%s" % (frame.type_id, frame.stream_id, role, peer_session_id, lane, queue_obj.buffered_bytes))
                record_event(
                    "queue_drop",
                    reason="queue-full",
                    role=role,
                    peer_session_id=peer_session_id,
                    lane=lane,
                    type_id=frame.type_id,
                    stream_id=frame.stream_id,
                    buffered_bytes=queue_obj.buffered_bytes,
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
        queue_obj.put(payload)
        if lane in ("pri", "bulk"):
            with peer.data_condition:
                peer.data_condition.notify_all()
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

    def handle_frame(self, sender_role, sender_peer_session_id, lane, frame):
        if frame.type_id == FRAME_PING:
            return
        if frame.type_id == FRAME_OPEN and sender_role == "helper":
            self._handle_open(sender_peer_session_id, frame)
            return
        with self.lock:
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
        queued = self.queue_frame(target_role, target_peer_session_id, target_lane, outbound_frame)
        if not queued and sender_role == "helper":
            self.queue_frame(
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
            with self.lock:
                self._drop_stream_locked(stream)

    def _handle_open(self, helper_session_id, frame):
        open_error = ""
        with self.lock:
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
                trace("open helper=%s/%s helper_stream=%s agent_session=%s agent_stream=%s" % (helper_peer_label, helper_session_id, frame.stream_id, agent_session_id, agent_stream_id))
                record_event(
                    "open_map",
                    helper_session_id=helper_session_id,
                    helper_peer_label=helper_peer_label,
                    helper_stream_id=frame.stream_id,
                    agent_session_id=agent_session_id,
                    agent_stream_id=agent_stream_id,
                )
        if open_error:
            record_event(
                "open_fail",
                helper_session_id=helper_session_id,
                helper_stream_id=frame.stream_id,
                reason=open_error,
            )
            self.queue_frame("helper", helper_session_id, LANE_CTL, Frame(FRAME_OPEN_FAIL, stream_id=frame.stream_id, payload=make_error_payload(open_error)))
            return
        if not agent_session_id:
            record_event(
                "open_fail",
                helper_session_id=helper_session_id,
                helper_stream_id=frame.stream_id,
                reason="no-agent",
            )
            self.queue_frame("helper", helper_session_id, LANE_CTL, Frame(FRAME_OPEN_FAIL, stream_id=frame.stream_id, payload=make_error_payload("hidden agent unavailable")))
            return
        self.queue_frame("agent", agent_session_id, LANE_CTL, Frame(FRAME_OPEN, stream_id=agent_stream_id, offset=frame.offset, payload=frame.payload, flags=frame.flags))

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
        helper_peer.open_events_ms = [value for value in helper_peer.open_events_ms if value >= window_start]
        if helper_peer.active_streams >= self.max_streams_per_peer_session:
            return "too many concurrent streams"
        if len(helper_peer.open_events_ms) >= self.max_open_rate_per_peer_session:
            return "too many new streams"
        helper_peer.open_events_ms.append(current_ms)
        return ""

    def cleanup(self):
        peer_cutoff = now_ms() - self.peer_ttl_ms
        stream_cutoff = now_ms() - self.stream_ttl_ms
        resets = []
        with self.lock:
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
            self.queue_frame(role, peer_session_id, lane, frame)

    def _reset_frames_for_stale_peer_locked(self, stale_role, stale_peer_session_id, stream):
        payload = make_error_payload("peer expired")
        resets = []
        if stale_role == "helper" and stream.agent_session_id and ("agent", stream.agent_session_id) in self.peers:
            resets.append(("agent", stream.agent_session_id, LANE_CTL, Frame(FRAME_RST, stream_id=stream.agent_stream_id, payload=payload)))
        if stale_role == "agent" and stream.helper_session_id and ("helper", stream.helper_session_id) in self.peers:
            resets.append(("helper", stream.helper_session_id, LANE_CTL, Frame(FRAME_RST, stream_id=stream.helper_stream_id, payload=payload)))
        return resets

    def _reset_frames_for_stale_stream_locked(self, stream):
        payload = make_error_payload("stream expired")
        resets = []
        if stream.helper_session_id and ("helper", stream.helper_session_id) in self.peers:
            resets.append(("helper", stream.helper_session_id, LANE_CTL, Frame(FRAME_RST, stream_id=stream.helper_stream_id, payload=payload)))
        if stream.agent_session_id and ("agent", stream.agent_session_id) in self.peers:
            resets.append(("agent", stream.agent_session_id, LANE_CTL, Frame(FRAME_RST, stream_id=stream.agent_stream_id, payload=payload)))
        return resets

    def stats(self):
        with self.lock:
            buffered = {}
            for lane in LANES:
                buffered[lane] = sum(peer.queues[lane].buffered_bytes for peer in self.peers.values())
            return {
                "ok": True,
                "pid": os.getpid(),
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

    def next_data_payload(self, peer, wait_timeout_seconds=DOWN_POLL_TIMEOUT_SECONDS):
        pri_queue = peer.queues["pri"]
        bulk_queue = peer.queues["bulk"]
        while True:
            try:
                first = pri_queue.get_nowait()
                source_lane = "pri"
                break
            except queue.Empty:
                try:
                    first = bulk_queue.get_nowait()
                    source_lane = "bulk"
                    break
                except queue.Empty:
                    with peer.data_condition:
                        notified = peer.data_condition.wait(timeout=wait_timeout_seconds)
                    if not notified:
                        return None, None, 0, 0, 0
        peer.touch()
        profile = self.lane_profile(source_lane)
        payloads = [first]
        total = len(first)
        frames = 1
        hold_started = time.time()
        deadline = time.time() + (float(profile["hold_ms"]) / 1000.0)
        queue_obj = peer.queues[source_lane]
        while total < int(profile["max_bytes"]) and frames < int(profile["max_frames"]):
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                payload = queue_obj.get(timeout=remaining)
            except queue.Empty:
                break
            payloads.append(payload)
            total += len(payload)
            frames += 1
        body = b"".join(payloads)
        padded = padded_payload(body, minimum_size=int(profile["pad_min"])) if int(profile["pad_min"]) > 0 else body
        pad_bytes = max(0, len(padded) - len(body))
        hold_ms = max(0, int((time.time() - hold_started) * 1000))
        return padded, source_lane, frames, hold_ms, pad_bytes


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


_CONFIG = load_config()
if not TRACE_ENABLED and _CONFIG.get("trace_enabled"):
    TRACE_ENABLED = True
configure_runtime_logging(CONFIG_PATH, _CONFIG)
configure_event_logging(CONFIG_PATH, _CONFIG)
LOGGER.info(
    "passenger broker loaded config_path=%s log_path=%s event_log_path=%s",
    CONFIG_PATH,
    RUNTIME_LOG_PATH or "stderr-only",
    EVENT_LOG_PATH or "disabled",
)
record_event("broker_loaded", config_path=CONFIG_PATH)
_STATE = BrokerState(_CONFIG)
_BINARY_MEDIA_TYPE = expected_binary_media_type(_CONFIG)
_CLEANUP_STARTED = False
_CLEANUP_LOCK = threading.Lock()


def ensure_cleanup_thread():
    global _CLEANUP_STARTED
    with _CLEANUP_LOCK:
        if _CLEANUP_STARTED:
            return

        def loop():
            while True:
                time.sleep(10.0)
                try:
                    _STATE.cleanup()
                except Exception as error:
                    trace("cleanup error=%r" % (error,))
                    record_event("cleanup_error", error=str(error))

        thread = threading.Thread(target=loop, name="twoman-cleanup", daemon=True)
        thread.start()
        _CLEANUP_STARTED = True


def json_response(start_response, status_code, payload):
    body = json.dumps(payload).encode("utf-8")
    status_text = "%d OK" % int(status_code)
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    start_response(status_text, headers)
    return [body]


def parse_request(environ):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "") or "/"
    headers = {
        "authorization": environ.get("HTTP_AUTHORIZATION", ""),
        "cookie": environ.get("HTTP_COOKIE", ""),
        "content-type": environ.get("CONTENT_TYPE", ""),
        "x-relay-token": environ.get("HTTP_X_RELAY_TOKEN", ""),
        "x-twoman-role": environ.get("HTTP_X_TWOMAN_ROLE", ""),
        "x-twoman-peer": environ.get("HTTP_X_TWOMAN_PEER", ""),
        "x-twoman-session": environ.get("HTTP_X_TWOMAN_SESSION", ""),
    }
    body = b""
    if method == "POST":
        length = int(environ.get("CONTENT_LENGTH", "0") or "0")
        body = environ["wsgi.input"].read(length) if length > 0 else b""
    return method, path, headers, body


def normalize_path(path):
    return normalize_request_path(
        path,
        _CONFIG.get("base_uri") or _CONFIG.get("route_base_path"),
    )


def application(environ, start_response):
    ensure_cleanup_thread()
    method, raw_path, headers, body = parse_request(environ)
    path = normalize_path(raw_path)

    if is_health_path(path, _CONFIG.get("health_template")):
        identity = extract_connection_identity(headers, _CONFIG)
        token = identity["token"]
        health_public = bool(_CONFIG.get("health_public", False))
        if not health_public and not (_STATE.auth("helper", token) or _STATE.auth("agent", token)):
            return json_response(start_response, 403, {"error": "forbidden"})
        return json_response(start_response, 200, _STATE.stats())

    route = parse_lane_path(path, _CONFIG.get("route_template"))
    if route is None:
        return json_response(start_response, 404, {"error": "not found", "path": raw_path})

    lane = route.get("lane", "")
    direction = route.get("direction", "")
    if lane not in (list(LANES) + [LANE_DATA]) or direction not in ("up", "down"):
        return json_response(start_response, 404, {"error": "not found", "path": raw_path})
    identity = extract_connection_identity(headers, _CONFIG)
    role = identity["role"]
    peer_label = identity["peer_label"]
    peer_session_id = identity["peer_session_id"]
    token = identity["token"]
    if not role or not peer_label or not peer_session_id or not _STATE.auth(role, token):
        return json_response(start_response, 403, {"error": "invalid role or token"})

    peer = _STATE.ensure_peer(role, peer_label, peer_session_id)

    if method == "POST" and direction == "up":
        validate_binary_media_type(headers.get("content-type", ""), _CONFIG)
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
            _STATE.handle_frame(role, peer_session_id, frame_lane, frame)
            _STATE.metrics["up_frames"][frame_lane] += 1
            _STATE.metrics["up_bytes"][frame_lane] += len(encode_frame(frame))
        _STATE.metrics["up_batches"][batch_lane] += 1
        record_event(
            "up_batch",
            role=role,
            peer_session_id=peer_session_id,
            lane=batch_lane,
            frame_count=frame_count,
            body_bytes=len(body),
        )
        return json_response(start_response, 200, {"ok": True, "frames": frame_count})

    if method != "GET" or direction != "down":
        return json_response(start_response, 405, {"error": "method not allowed"})

    if lane == LANE_DATA:
        payload, source_lane, frames, hold_ms, pad_bytes = _STATE.next_data_payload(peer, wait_timeout_seconds=DOWN_POLL_TIMEOUT_SECONDS)
        if payload is None:
            payload = padded_payload(encode_frame(Frame(FRAME_PING, offset=now_ms())), minimum_size=1024)
            source_lane = "pri"
            frames = 1
            hold_ms = 0
            pad_bytes = 0
        _STATE.metrics["down_responses"][source_lane] += 1
        _STATE.metrics["down_frames"][source_lane] += frames
        _STATE.metrics["down_bytes"][source_lane] += len(payload)
        _STATE.metrics["down_hold_ms"][source_lane] += hold_ms
        _STATE.metrics["down_pad_bytes"][source_lane] += pad_bytes
        start_response("200 OK", [("Content-Type", _BINARY_MEDIA_TYPE), ("Content-Length", str(len(payload))), ("Cache-Control", "no-store")])
        return [payload]

    queue_obj = peer.queues[lane]
    profile = _STATE.lane_profile(lane)
    payloads = []
    hold_started = time.time()
    try:
        payloads.append(queue_obj.get(timeout=DOWN_POLL_TIMEOUT_SECONDS))
    except queue.Empty:
        ping = padded_payload(encode_frame(Frame(FRAME_PING, offset=now_ms())), minimum_size=int(profile["pad_min"]))
        start_response("200 OK", [("Content-Type", _BINARY_MEDIA_TYPE), ("Content-Length", str(len(ping))), ("Cache-Control", "no-store")])
        return [ping]
    peer.touch()
    total = len(payloads[0])
    frames = 1
    deadline = time.time() + (float(profile["hold_ms"]) / 1000.0)
    while total < int(profile["max_bytes"]) and frames < int(profile["max_frames"]):
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            payload = queue_obj.get(timeout=remaining)
        except queue.Empty:
            break
        payloads.append(payload)
        total += len(payload)
        frames += 1
    body = b"".join(payloads)
    padded = padded_payload(body, minimum_size=int(profile["pad_min"])) if int(profile["pad_min"]) > 0 else body
    pad_bytes = max(0, len(padded) - len(body))
    hold_ms = max(0, int((time.time() - hold_started) * 1000))
    _STATE.metrics["down_responses"][lane] += 1
    _STATE.metrics["down_frames"][lane] += frames
    _STATE.metrics["down_bytes"][lane] += len(padded)
    _STATE.metrics["down_hold_ms"][lane] += hold_ms
    _STATE.metrics["down_pad_bytes"][lane] += pad_bytes
    start_response("200 OK", [("Content-Type", _BINARY_MEDIA_TYPE), ("Content-Length", str(len(padded))), ("Cache-Control", "no-store")])
    return [padded]
