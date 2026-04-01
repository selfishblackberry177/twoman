#!/usr/bin/env python3

import asyncio
import collections
import contextlib
import os
import random
import ssl
import sys
import urllib.parse

import httpx
try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - compatibility for older distro packages
    from websockets import connect as ws_connect

from twoman_http import (
    RouteProvider,
    build_connection_headers,
    expected_binary_media_type,
    is_json_media_type,
    jittered_backoff_seconds,
    jittered_interval_seconds,
    validate_binary_media_type,
    validate_json_media_type,
)
from twoman_protocol import (
    FLAG_DATA_BULK,
    FRAME_DATA,
    FRAME_PING,
    Frame,
    FrameDecoder,
    LANES,
    LANE_CTL,
    LANE_DATA,
    LANE_PRI,
    encode_frame,
)

TRACE_ENABLED = os.environ.get("TWOMAN_TRACE", "").strip().lower() in ("1", "true", "yes", "on", "debug", "verbose")


def trace(message):
    if not TRACE_ENABLED:
        return
    sys.stderr.write("[transport] %s\n" % message)
    sys.stderr.flush()


class LaneTransport(object):
    def __init__(
        self,
        base_url,
        token,
        role,
        peer_id,
        on_frame,
        http_timeout_seconds=60,
        flush_delay_seconds=0.01,
        max_batch_bytes=65536,
        verify_tls=True,
        http2_enabled=True,
        collapse_data_lanes=False,
        upload_profiles=None,
        streaming_up_lanes=None,
        idle_repoll_delay_seconds=None,
        protocol_config=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.role = role
        self.peer_label = peer_id
        self.peer_session_id = os.urandom(8).hex()
        self.on_frame = on_frame
        self.http_timeout_seconds = float(http_timeout_seconds)
        self.flush_delay_seconds = float(flush_delay_seconds)
        self.max_batch_bytes = int(max_batch_bytes)
        self.verify_tls = verify_tls
        self.collapse_data_lanes = bool(collapse_data_lanes)
        self.http2_enabled_default = False if isinstance(http2_enabled, dict) else bool(http2_enabled)
        self.http2_enabled_lanes = self._normalize_http2_enabled(http2_enabled)
        default_upload_profiles = {
            "ctl": {"max_batch_bytes": 4096, "flush_delay_seconds": 0.0},
            "pri": {"max_batch_bytes": 4096, "flush_delay_seconds": min(self.flush_delay_seconds, 0.001)},
            "bulk": {"max_batch_bytes": min(self.max_batch_bytes, 32768), "flush_delay_seconds": max(self.flush_delay_seconds, 0.008)},
            "data": self._default_data_upload_profile(),
        }
        self.upload_profiles = self._merge_upload_profiles(default_upload_profiles, upload_profiles or {})
        self.streaming_up_lanes = self._normalize_streaming_up_lanes(streaming_up_lanes)
        self.idle_repoll_delay_seconds = self._normalize_idle_repoll_delay_seconds(idle_repoll_delay_seconds)
        self.protocol_config = dict(protocol_config or {})
        self.route_provider = RouteProvider.from_config(self.base_url, self.protocol_config)
        self.random = random.Random()
        self.queues = dict((lane, asyncio.Queue()) for lane in LANES)
        self.data_queue = asyncio.Queue() if self.collapse_data_lanes else None
        self.replay_queues = dict((lane, collections.deque()) for lane in self._external_lanes())
        self.stop_event = asyncio.Event()
        self.clients = {}
        self.tasks = []
        self.failure_counts = {}
        self.event_handler = None

    async def start(self):
        if self.clients:
            return
        for lane in self._external_lanes():
            self.clients[(lane, "up")] = self._build_client(lane)
            self.clients[(lane, "down")] = self._build_client(lane)
        for lane in self._external_lanes():
            if lane in self.streaming_up_lanes:
                self.tasks.append(asyncio.create_task(self._streaming_up_loop(lane)))
            else:
                self.tasks.append(asyncio.create_task(self._up_loop(lane)))
            self.tasks.append(asyncio.create_task(self._down_loop(lane)))
        self.tasks.append(asyncio.create_task(self._ping_loop()))
        self._report_event(
            "transport_start",
            base_url=self.base_url,
            collapse_data_lanes=self.collapse_data_lanes,
            http2_enabled=self.http2_enabled_lanes,
            streaming_up_lanes=self.streaming_up_lanes,
        )

    async def stop(self):
        self.stop_event.set()
        self._report_event("transport_stop")
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks = []
        for client in self.clients.values():
            with contextlib.suppress(Exception):
                await client.aclose()
        self.clients = {}

    async def send_frame(self, lane, frame):
        if self.collapse_data_lanes and lane in ("pri", "bulk"):
            if frame.type_id == FRAME_DATA:
                flags = int(frame.flags)
                if lane == "bulk":
                    flags |= FLAG_DATA_BULK
                else:
                    flags &= ~FLAG_DATA_BULK
                frame = Frame(
                    frame.type_id,
                    stream_id=frame.stream_id,
                    offset=frame.offset,
                    payload=frame.payload,
                    flags=flags,
                )
            await self.data_queue.put(frame)
            return
        if lane not in self.queues:
            raise ValueError("unknown lane")
        await self.queues[lane].put(frame)

    async def _up_loop(self, lane):
        while not self.stop_event.is_set():
            batch_frames = []
            try:
                first = await self._next_outbound_frame(lane)
                logical_lane = lane
                profile = self.upload_profiles.get(
                    logical_lane,
                    {"max_batch_bytes": self.max_batch_bytes, "flush_delay_seconds": self.flush_delay_seconds},
                )
                batch_frames = [first]
                batch = [encode_frame(first)]
                total = len(batch[0])
                max_batch_bytes = int(profile["max_batch_bytes"])
                flush_delay_seconds = float(profile["flush_delay_seconds"])
                deadline = asyncio.get_running_loop().time() + flush_delay_seconds
                while total < max_batch_bytes:
                    if flush_delay_seconds <= 0:
                        try:
                            frame = self._next_outbound_frame_nowait(lane)
                        except asyncio.QueueEmpty:
                            break
                        encoded = encode_frame(frame)
                        if total + len(encoded) > max_batch_bytes:
                            await self._requeue_frame(lane, frame)
                            break
                        batch_frames.append(frame)
                        batch.append(encoded)
                        total += len(encoded)
                        continue
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        frame = await asyncio.wait_for(self._next_outbound_frame(lane), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    encoded = encode_frame(frame)
                    if total + len(encoded) > max_batch_bytes:
                        await self._requeue_frame(lane, frame)
                        break
                    batch_frames.append(frame)
                    batch.append(encoded)
                    total += len(encoded)
                response = await self.clients[(lane, "up")].post(
                    self._lane_url(lane, "up"),
                    headers=self._headers(binary_request=True),
                    content=b"".join(batch),
                )
                response.raise_for_status()
                self._validate_ack_response(response)
                self._mark_success("up", lane)
                trace("%s/%s@%s up ok lane=%s batch_bytes=%s status=%s" % (self.role, self.peer_label, self.peer_session_id, lane, total, response.status_code))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._requeue_frames_front(lane, batch_frames)
                await self._reset_client(lane, "up")
                delay = self._backoff_after_error("up", lane)
                self._report_event(
                    "transport_up_error",
                    lane=lane,
                    delay_seconds=delay,
                    error=repr(error),
                    queued_frames=len(batch_frames),
                )
                trace("%s/%s@%s up error lane=%s delay=%0.3f error=%r" % (self.role, self.peer_label, self.peer_session_id, lane, delay, error))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _down_loop(self, lane):
        while not self.stop_event.is_set():
            decoder = FrameDecoder()
            saw_non_ping = False
            try:
                async with self.clients[(lane, "down")].stream("GET", self._lane_url(lane, "down"), headers=self._headers()) as response:
                    response.raise_for_status()
                    self._validate_binary_response(response)
                    self._mark_success("down", lane)
                    trace("%s/%s@%s down open lane=%s status=%s" % (self.role, self.peer_label, self.peer_session_id, lane, response.status_code))
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        trace("%s/%s@%s down chunk lane=%s bytes=%s" % (self.role, self.peer_label, self.peer_session_id, lane, len(chunk)))
                        for frame in decoder.feed(chunk):
                            if frame.type_id != FRAME_PING:
                                saw_non_ping = True
                                trace("%s/%s@%s down frame lane=%s type=%s stream=%s payload=%s" % (self.role, self.peer_label, self.peer_session_id, lane, frame.type_id, frame.stream_id, len(frame.payload)))
                            await self.on_frame(frame, lane)
                if not saw_non_ping:
                    delay = self.idle_repoll_delay_seconds.get(lane, 0.0)
                    if delay > 0:
                        await asyncio.sleep(self._jittered_interval(delay))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                delay = self._backoff_after_error("down", lane)
                self._report_event(
                    "transport_down_error",
                    lane=lane,
                    delay_seconds=delay,
                    error=repr(error),
                )
                trace("%s/%s@%s down error lane=%s delay=%0.3f error=%r" % (self.role, self.peer_label, self.peer_session_id, lane, delay, error))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _streaming_up_loop(self, lane):
        while not self.stop_event.is_set():
            try:
                response = await self.clients[(lane, "up")].post(
                    self._lane_url(lane, "up"),
                    headers=self._headers(binary_request=True),
                    content=self._streaming_upload_chunks(lane),
                )
                response.raise_for_status()
                self._validate_ack_response(response)
                self._mark_success("up_stream", lane)
                trace("%s/%s@%s up stream closed lane=%s status=%s" % (self.role, self.peer_label, self.peer_session_id, lane, response.status_code))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                delay = self._backoff_after_error("up_stream", lane)
                self._report_event(
                    "transport_up_stream_error",
                    lane=lane,
                    delay_seconds=delay,
                    error=repr(error),
                )
                trace("%s/%s@%s up stream error lane=%s delay=%0.3f error=%r" % (self.role, self.peer_label, self.peer_session_id, lane, delay, error))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _ping_loop(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(self._jittered_interval(self.protocol_config.get("heartbeat_interval_seconds", 15.0)))
            await self.send_frame(LANE_CTL, Frame(FRAME_PING, offset=int(asyncio.get_running_loop().time() * 1000)))

    def _headers(self, binary_request=False):
        headers = build_connection_headers(
            self.token,
            self.role,
            self.peer_label,
            self.peer_session_id,
            self.protocol_config,
        )
        if binary_request:
            headers["Content-Type"] = expected_binary_media_type(self.protocol_config)
        return headers

    def _lane_url(self, lane, direction):
        return self.route_provider.lane_url(lane, direction)

    def _external_lanes(self):
        if self.collapse_data_lanes:
            return (LANE_CTL, LANE_DATA)
        return LANES

    def _normalize_http2_enabled(self, http2_enabled):
        external_lanes = self._external_lanes()
        if isinstance(http2_enabled, dict):
            values = dict((str(key), bool(value)) for key, value in http2_enabled.items())
            lanes = dict((lane, False) for lane in external_lanes)
            for lane in external_lanes:
                if lane in values:
                    lanes[lane] = values[lane]
            if self.collapse_data_lanes and LANE_DATA not in values:
                lanes[LANE_DATA] = bool(values.get("pri", False) or values.get("bulk", False))
            return lanes
        default_value = bool(http2_enabled)
        return dict((lane, default_value) for lane in external_lanes)

    def _normalize_streaming_up_lanes(self, streaming_up_lanes):
        if not streaming_up_lanes:
            return set()
        return set(str(value) for value in streaming_up_lanes if str(value) in self._external_lanes())

    def _normalize_idle_repoll_delay_seconds(self, idle_repoll_delay_seconds):
        external_lanes = self._external_lanes()
        if isinstance(idle_repoll_delay_seconds, dict):
            values = dict((str(key), float(value)) for key, value in idle_repoll_delay_seconds.items())
            delays = dict((lane, 0.0) for lane in external_lanes)
            for lane in external_lanes:
                if lane in values:
                    delays[lane] = max(0.0, values[lane])
            if self.collapse_data_lanes and LANE_DATA not in values:
                delays[LANE_DATA] = max(0.0, float(values.get("pri", 0.0) or values.get("bulk", 0.0)))
            return delays
        if idle_repoll_delay_seconds is None:
            default_value = 0.0
        else:
            default_value = max(0.0, float(idle_repoll_delay_seconds))
        return dict((lane, default_value) for lane in external_lanes)

    def _default_data_upload_profile(self):
        if self.role == "agent":
            return {
                "max_batch_bytes": min(max(self.max_batch_bytes, 65536), 131072),
                "flush_delay_seconds": max(self.flush_delay_seconds, 0.005),
            }
        return {
            "max_batch_bytes": min(max(self.max_batch_bytes // 2, 16384), 32768),
            "flush_delay_seconds": max(min(self.flush_delay_seconds, 0.002), 0.001),
        }

    def _merge_upload_profiles(self, defaults, overrides):
        merged = dict((lane, dict(profile)) for lane, profile in defaults.items())
        for lane, profile in (overrides or {}).items():
            lane_key = str(lane)
            if lane_key not in merged or not isinstance(profile, dict):
                continue
            if "max_batch_bytes" in profile:
                merged[lane_key]["max_batch_bytes"] = int(profile["max_batch_bytes"])
            if "flush_delay_seconds" in profile:
                merged[lane_key]["flush_delay_seconds"] = float(profile["flush_delay_seconds"])
        return merged

    def _mark_success(self, direction, lane):
        self.failure_counts[(direction, lane)] = 0

    def _backoff_after_error(self, direction, lane):
        key = (direction, lane)
        failures = self.failure_counts.get(key, 0) + 1
        self.failure_counts[key] = failures
        return jittered_backoff_seconds(
            failures,
            initial_delay=float(self.protocol_config.get("backoff_initial_delay_seconds", 0.1)),
            maximum_delay=float(self.protocol_config.get("backoff_max_delay_seconds", 5.0)),
            multiplier=float(self.protocol_config.get("backoff_multiplier", 2.0)),
            free_failures=int(self.protocol_config.get("backoff_free_failures", 1)),
            rng=self.random,
        )

    async def _next_outbound_frame(self, lane):
        replay = self.replay_queues.get(lane)
        if replay:
            return replay.popleft()
        if lane != LANE_DATA:
            return await self.queues[lane].get()
        return await self.data_queue.get()

    def _next_outbound_frame_nowait(self, lane):
        replay = self.replay_queues.get(lane)
        if replay:
            return replay.popleft()
        if lane != LANE_DATA:
            return self.queues[lane].get_nowait()
        return self.data_queue.get_nowait()

    async def _requeue_frame(self, lane, frame):
        if lane != LANE_DATA:
            await self.queues[lane].put(frame)
            return
        await self.data_queue.put(frame)

    async def _requeue_frames_front(self, lane, frames):
        if not frames:
            return
        replay = self.replay_queues.setdefault(lane, collections.deque())
        for frame in reversed(frames):
            replay.appendleft(frame)

    async def _reset_client(self, lane, direction):
        key = (lane, direction)
        client = self.clients.get(key)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()
        self.clients[key] = self._build_client(lane)

    async def _streaming_upload_chunks(self, lane):
        logical_lane = lane
        profile = self.upload_profiles.get(
            logical_lane,
            {"max_batch_bytes": self.max_batch_bytes, "flush_delay_seconds": self.flush_delay_seconds},
        )
        max_batch_bytes = int(profile["max_batch_bytes"])
        flush_delay_seconds = float(profile["flush_delay_seconds"])
        while not self.stop_event.is_set():
            first = await self._next_outbound_frame(lane)
            batch = [encode_frame(first)]
            total = len(batch[0])
            deadline = asyncio.get_running_loop().time() + flush_delay_seconds
            while total < max_batch_bytes:
                if flush_delay_seconds <= 0:
                    try:
                        frame = self._next_outbound_frame_nowait(lane)
                    except asyncio.QueueEmpty:
                        break
                    encoded = encode_frame(frame)
                    if total + len(encoded) > max_batch_bytes:
                        await self._requeue_frame(lane, frame)
                        break
                    batch.append(encoded)
                    total += len(encoded)
                    continue
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    frame = await asyncio.wait_for(self._next_outbound_frame(lane), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                encoded = encode_frame(frame)
                if total + len(encoded) > max_batch_bytes:
                    await self._requeue_frame(lane, frame)
                    break
                batch.append(encoded)
                total += len(encoded)
            yield b"".join(batch)

    def _build_client(self, lane):
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=120)
        timeout = httpx.Timeout(
            None,
            connect=self.http_timeout_seconds,
            read=None,
            write=self.http_timeout_seconds,
            pool=self.http_timeout_seconds,
        )
        return httpx.AsyncClient(
            http2=self.http2_enabled_lanes.get(lane, self.http2_enabled_default),
            timeout=timeout,
            limits=limits,
            verify=self.verify_tls,
        )

    def _validate_ack_response(self, response):
        validate_json_media_type(response.headers.get("content-type", ""))
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ValueError("unexpected transport ack payload")
        return payload

    def _validate_binary_response(self, response):
        content_type = response.headers.get("content-type", "")
        if is_json_media_type(content_type):
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else payload
            raise ValueError("binary response returned JSON: %s" % error)
        validate_binary_media_type(content_type, self.protocol_config)

    def _jittered_interval(self, base_delay):
        return jittered_interval_seconds(
            base_delay,
            jitter_ratio=float(self.protocol_config.get("interval_jitter_ratio", 0.2)),
            rng=self.random,
        )

    def _report_event(self, kind, **fields):
        if self.event_handler is None:
            return
        payload = {
            "kind": kind,
            "role": self.role,
            "peer_label": self.peer_label,
            "peer_session_id": self.peer_session_id,
        }
        payload.update(fields)
        try:
            self.event_handler(payload)
        except Exception:
            pass


class AsyncFrameQueue(object):
    def __init__(self):
        self._items = collections.deque()
        self._condition = asyncio.Condition()

    async def put(self, item):
        async with self._condition:
            self._items.append(item)
            self._condition.notify()

    async def putleft(self, item):
        async with self._condition:
            self._items.appendleft(item)
            self._condition.notify()

    async def get(self):
        async with self._condition:
            while not self._items:
                await self._condition.wait()
            return self._items.popleft()


class WebSocketLaneTransport(object):
    def __init__(
        self,
        base_url,
        token,
        role,
        peer_id,
        on_frame,
        http_timeout_seconds=60,
        flush_delay_seconds=0.01,
        max_batch_bytes=65536,
        verify_tls=True,
        http2_enabled=True,
        collapse_data_lanes=False,
        upload_profiles=None,
        streaming_up_lanes=None,
        idle_repoll_delay_seconds=None,
        protocol_config=None,
    ):
        del http2_enabled
        del upload_profiles
        del streaming_up_lanes
        del idle_repoll_delay_seconds
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.role = role
        self.peer_label = peer_id
        self.peer_session_id = os.urandom(8).hex()
        self.on_frame = on_frame
        self.http_timeout_seconds = float(http_timeout_seconds)
        self.flush_delay_seconds = float(flush_delay_seconds)
        self.max_batch_bytes = int(max_batch_bytes)
        self.verify_tls = bool(verify_tls)
        self.collapse_data_lanes = bool(collapse_data_lanes)
        self.protocol_config = dict(protocol_config or {})
        self.route_provider = RouteProvider.from_config(self.base_url, self.protocol_config)
        self.random = random.Random()
        self.queues = dict((lane, AsyncFrameQueue()) for lane in LANES)
        self.data_queue = AsyncFrameQueue() if self.collapse_data_lanes else None
        self.stop_event = asyncio.Event()
        self.tasks = []
        self.failure_counts = {}
        self.event_handler = None

    async def start(self):
        if self.tasks:
            return
        for lane in self._external_lanes():
            self.tasks.append(asyncio.create_task(self._lane_loop(lane)))
        self.tasks.append(asyncio.create_task(self._ping_loop()))
        self._report_event(
            "transport_start",
            base_url=self.base_url,
            collapse_data_lanes=self.collapse_data_lanes,
        )

    async def stop(self):
        self.stop_event.set()
        self._report_event("transport_stop")
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks = []

    async def send_frame(self, lane, frame):
        if self.collapse_data_lanes and lane in ("pri", "bulk"):
            if frame.type_id == FRAME_DATA:
                flags = int(frame.flags)
                if lane == "bulk":
                    flags |= FLAG_DATA_BULK
                else:
                    flags &= ~FLAG_DATA_BULK
                frame = Frame(
                    frame.type_id,
                    stream_id=frame.stream_id,
                    offset=frame.offset,
                    payload=frame.payload,
                    flags=flags,
                )
            await self.data_queue.put(frame)
            return
        if lane not in self.queues:
            raise ValueError("unknown lane")
        await self.queues[lane].put(frame)

    async def _lane_loop(self, lane):
        while not self.stop_event.is_set():
            try:
                ssl_context = self._ssl_context_for_lane()
                async with ws_connect(
                    self._lane_url(lane),
                    additional_headers=self._headers(),
                    open_timeout=self.http_timeout_seconds,
                    ping_interval=float(self.protocol_config.get("ws_ping_interval_seconds", 20)),
                    ping_timeout=float(self.protocol_config.get("ws_ping_timeout_seconds", 20)),
                    close_timeout=5,
                    max_size=None,
                    max_queue=16,
                    write_limit=32768,
                    ssl=ssl_context,
                ) as websocket:
                    self._mark_success("ws", lane)
                    trace("%s/%s@%s ws open lane=%s" % (self.role, self.peer_label, self.peer_session_id, lane))
                    recv_task = asyncio.create_task(self._recv_loop(websocket, lane))
                    send_task = asyncio.create_task(self._send_loop(websocket, lane))
                    done, pending = await asyncio.wait(
                        [recv_task, send_task],
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()
                    for task in pending:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                    for task in done:
                        task.result()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                delay = self._backoff_after_error("ws", lane)
                trace("%s/%s@%s ws error lane=%s delay=%0.3f error=%r" % (self.role, self.peer_label, self.peer_session_id, lane, delay, error))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _send_loop(self, websocket, lane):
        while not self.stop_event.is_set():
            frame = await self._next_outbound_frame(lane)
            payload = encode_frame(frame)
            try:
                await websocket.send(payload)
                if frame.type_id != FRAME_PING:
                    trace("%s/%s@%s ws send lane=%s type=%s stream=%s payload=%s" % (self.role, self.peer_label, self.peer_session_id, lane, frame.type_id, frame.stream_id, len(frame.payload)))
            except Exception:
                await self._requeue_frame_front(lane, frame)
                raise

    async def _recv_loop(self, websocket, lane):
        decoder = FrameDecoder()
        async for message in websocket:
            if isinstance(message, str):
                message = message.encode("utf-8")
            if not message:
                continue
            for frame in decoder.feed(message):
                logical_lane = self._logical_lane(lane, frame)
                if frame.type_id != FRAME_PING:
                    trace("%s/%s@%s ws recv lane=%s type=%s stream=%s payload=%s" % (self.role, self.peer_label, self.peer_session_id, logical_lane, frame.type_id, frame.stream_id, len(frame.payload)))
                await self.on_frame(frame, logical_lane)

    async def _ping_loop(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(self._jittered_interval(self.protocol_config.get("heartbeat_interval_seconds", 15.0)))
            await self.send_frame(LANE_CTL, Frame(FRAME_PING, offset=int(asyncio.get_running_loop().time() * 1000)))

    def _headers(self):
        return build_connection_headers(
            self.token,
            self.role,
            self.peer_label,
            self.peer_session_id,
            self.protocol_config,
        )

    def _lane_url(self, lane):
        url = self.route_provider.ws_lane_url(lane)
        parsed = urllib.parse.urlsplit(url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urllib.parse.urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))

    def _external_lanes(self):
        if self.collapse_data_lanes:
            return (LANE_CTL, LANE_DATA)
        return LANES

    def _mark_success(self, direction, lane):
        self.failure_counts[(direction, lane)] = 0

    def _backoff_after_error(self, direction, lane):
        key = (direction, lane)
        failures = self.failure_counts.get(key, 0) + 1
        self.failure_counts[key] = failures
        return jittered_backoff_seconds(
            failures,
            initial_delay=float(self.protocol_config.get("backoff_initial_delay_seconds", 0.1)),
            maximum_delay=float(self.protocol_config.get("backoff_max_delay_seconds", 5.0)),
            multiplier=float(self.protocol_config.get("backoff_multiplier", 2.0)),
            free_failures=int(self.protocol_config.get("backoff_free_failures", 1)),
            rng=self.random,
        )

    async def _next_outbound_frame(self, lane):
        if lane != LANE_DATA:
            return await self.queues[lane].get()
        return await self.data_queue.get()

    async def _requeue_frame_front(self, lane, frame):
        if lane != LANE_DATA:
            await self.queues[lane].putleft(frame)
            return
        await self.data_queue.putleft(frame)

    def _ssl_context_for_lane(self):
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "https":
            return None
        if self.verify_tls:
            return None
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _jittered_interval(self, base_delay):
        return jittered_interval_seconds(
            base_delay,
            jitter_ratio=float(self.protocol_config.get("interval_jitter_ratio", 0.2)),
            rng=self.random,
        )

    def _logical_lane(self, lane, frame):
        if lane != LANE_DATA:
            return lane
        if frame.type_id != FRAME_DATA:
            return LANE_CTL
        return "bulk" if (frame.flags & FLAG_DATA_BULK) else LANE_PRI

    def _report_event(self, kind, **fields):
        if self.event_handler is None:
            return
        payload = {
            "kind": kind,
            "role": self.role,
            "peer_label": self.peer_label,
            "peer_session_id": self.peer_session_id,
        }
        payload.update(fields)
        try:
            self.event_handler(payload)
        except Exception:
            pass


def create_transport(config, role, peer_id, on_frame):
    transport_kind = str(config.get("transport", "http")).strip().lower()
    common_args = {
        "base_url": config["broker_base_url"],
        "token": config["client_token"] if role == "helper" else config["agent_token"],
        "role": role,
        "peer_id": peer_id,
        "on_frame": on_frame,
        "http_timeout_seconds": config.get("http_timeout_seconds", 60),
        "max_batch_bytes": config.get("max_batch_bytes", 65536),
        "flush_delay_seconds": config.get("flush_delay_seconds", 0.01),
        "verify_tls": config.get("verify_tls", True),
        "http2_enabled": config.get("http2_enabled", True),
        "collapse_data_lanes": True,
        "upload_profiles": config.get("upload_profiles", {}),
        "streaming_up_lanes": config.get("streaming_up_lanes", []),
        "idle_repoll_delay_seconds": config.get("idle_repoll_delay_seconds", {}),
        "protocol_config": {
            "auth_mode": config.get("auth_mode", "bearer"),
            "legacy_custom_headers_enabled": config.get("legacy_custom_headers_enabled", True),
            "binary_media_type": config.get("binary_media_type", "image/webp"),
            "binary_media_types": config.get("binary_media_types", []),
            "route_template": config.get("route_template", "/{lane}/{direction}"),
            "ws_route_template": config.get("ws_route_template", "/{lane}"),
            "health_template": config.get("health_template", "/health"),
            "route_context": config.get("route_context", {}),
            "version": config.get("version", config.get("api_version", "")),
            "tenant": config.get("tenant", config.get("tenant_id", "")),
            "endpoint": config.get("endpoint", config.get("endpoint_id", "")),
            "identity_cookie_names": config.get("identity_cookie_names", {}),
            "backoff_initial_delay_seconds": config.get("backoff_initial_delay_seconds", 0.1),
            "backoff_max_delay_seconds": config.get("backoff_max_delay_seconds", 5.0),
            "backoff_multiplier": config.get("backoff_multiplier", 2.0),
            "backoff_free_failures": config.get("backoff_free_failures", 1),
            "heartbeat_interval_seconds": config.get("heartbeat_interval_seconds", 15.0),
            "interval_jitter_ratio": config.get("interval_jitter_ratio", 0.2),
            "ws_ping_interval_seconds": config.get("ws_ping_interval_seconds", 20.0),
            "ws_ping_timeout_seconds": config.get("ws_ping_timeout_seconds", 20.0),
        },
    }
    if transport_kind == "ws":
        return WebSocketLaneTransport(**common_args)
    return LaneTransport(**common_args)
